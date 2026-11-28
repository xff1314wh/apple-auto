# -*- coding: utf-8 -*-
"""组合任务编排：串行跑多个任务，两棒之间清后台，交给下一个任务自己拉起 App。

解耦约定：
- 本层只做调度（起进程 / 等退出 / 清场），不碰任何业务坐标与页面判断；
- 启动哪个 App、归位到哪个起始页，由任务脚本内部的 prepare() 自己负责；
- 配置存 data/combos.json，任务按 tasks.json 里的 name 引用，不写死脚本路径。

步骤结构：
    {"task": "定向养号", "minutes": 30, "app": "nebula",
     "params": {"videos": "3", "like_prob": "0.6"}}

时长语义：minutes 必填且 > 0。带 --minutes 的任务内部按时间跑、不看轮次；
到点后跑完当前这一条再退出，所以实际耗时会比设定值多几十秒。

用法（命令行验收）：
    python core/combo.py --list
    python core/combo.py "组合名"
"""
import json
import os
import subprocess
import sys
import threading
import time

REG_NAME = "combos.json"
APPS = {"kuaishou": "com.jiangjia.gif", "nebula": "com.kuaishou.nebula"}

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import quick        # noqa: E402
import task_registry  # noqa: E402


# ============ 配置读写 ============
def path(root):
    return os.path.join(root, "data", REG_NAME)


def load(root):
    """读组合列表；文件缺失或损坏返回 []。"""
    p = path(root)
    if not os.path.isfile(p):
        return []
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
        combos = data.get("combos", []) if isinstance(data, dict) else []
        return [c for c in combos if isinstance(c, dict) and c.get("steps")]
    except Exception:
        return []


def save(root, combos):
    os.makedirs(os.path.join(root, "data"), exist_ok=True)
    with open(path(root), "w", encoding="utf-8") as f:
        json.dump({"combos": combos}, f, ensure_ascii=False, indent=2)


def upsert(root, combo):
    """按 name 去重：同名覆盖，否则追加。"""
    combos = load(root)
    name = combo.get("name")
    for i, c in enumerate(combos):
        if c.get("name") == name:
            combos[i] = combo
            break
    else:
        combos.append(combo)
    save(root, combos)
    return combos


def remove(root, name):
    combos = [c for c in load(root) if c.get("name") != name]
    save(root, combos)
    return combos


# ============ 校验与命令拼装 ============
def validate(root, combo):
    """返回错误描述；没问题返回 None。"""
    steps = combo.get("steps") or []
    if not steps:
        return "组合里没有步骤"
    known = {t.get("name"): t for t in task_registry.load(root)}
    for i, s in enumerate(steps, 1):
        if not s.get("task"):
            return f"第 {i} 步没写任务名"
        if s["task"] not in known:
            return f"第 {i} 步任务「{s['task']}」不在任务列表里"
        if s.get("app") and s["app"] not in APPS:
            return f"第 {i} 步 app 只能是 {list(APPS)}"
        try:
            mins = float(s.get("minutes") or 0)
        except Exception:
            return f"第 {i} 步分钟数不是数字"
        if mins <= 0:
            return f"第 {i} 步分钟数必须大于 0（填 0 会永远进不到下一步）"
    return None


def resolve_script(root, task_name):
    for t in task_registry.load(root):
        if t.get("name") == task_name:
            return os.path.join(root, t["script"])
    return None


def build_cmd(py, root, step):
    """把一步配置拼成任务脚本的命令行。"""
    script = resolve_script(root, step["task"])
    if not script:
        raise RuntimeError(f"找不到任务「{step['task']}」的脚本")
    cmd = [py, script, "--minutes", str(float(step["minutes"]))]
    if step.get("app"):
        cmd += ["--app", step["app"]]
    for k, v in (step.get("params") or {}).items():
        if v is None or str(v) == "":
            continue
        cmd += ["--" + str(k).replace("_", "-"), str(v)]
    return cmd


# ============ 执行 ============
def _kill_tree(pid):
    try:
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                       capture_output=True, timeout=15)
    except Exception:
        pass


def _pump(proc, log):
    """把子进程输出逐行转发出去。

    子任务日志本身已是「[时:分:秒] [级别] 正文」，这里只加 4 空格缩进表示层级，
    不再套第二层时间戳；没有时间戳的杂行才走 log 补前缀。
    """
    try:
        for line in iter(proc.stdout.readline, ""):
            line = line.rstrip()
            if not line:
                continue
            head = line[:10]
            if head.startswith("[") and head.endswith("]"):
                print("    " + line, flush=True)
            else:
                log("  " + line)
    except Exception:
        pass


# 日志级别归类：ERR 失败 > WARN 异常但继续 > PAGE 页面判定 > ACT 动作 > OK 完成
_LOG_RULES = (
    ("ERR", ("失败", "异常", "错误", "不可达", "抓不到", "没生效", "不可用", "超时")),
    ("WARN", ("未找到", "没有", "为空", "跳过", "放弃", "无进展", "仍是", "过头",
              "未确认", "不够", "超过", "降级", "未回")),
    ("PAGE", ("判为", "类型", "页面", "落地", "层级", "判成")),
    ("ACT", ("已点", "已填", "已勾", "已滑", "已清理", "已回", "点击", "随机点",
             "滑动", "填入", "退回", "发送", "点开", "点选")),
    ("OK", ("完成", "结束", "成功", "跑满")),
)


def log_fn(msg, tag=None):
    """统一日志行：``[时:分:秒] [级别] 正文``，缩进按 2 空格层级对齐。

    级别按内容自动归类，也可显式传 ERR / WARN / PAGE / ACT / OK / SEP / INFO。
    """
    txt = str(msg)
    if tag is None:
        if txt.strip().startswith(("---", "===", ">>>")):
            tag = "SEP"
        else:
            tag = next((t for t, words in _LOG_RULES if any(w in txt for w in words)), "INFO")
    depth = max(0, (len(txt) - len(txt.lstrip(" "))) // 2)
    print(f"[{time.strftime('%H:%M:%S')}] [{tag:<4}] {'  ' * depth}{txt.strip()}", flush=True)


def run_step(py, root, step, log, grace=90):
    """跑一步：等它自己按时间退出；超时强杀兜底。返回退出码。"""
    cmd = build_cmd(py, root, step)
    log(f"  命令：{' '.join(str(c) for c in cmd[1:])}")
    creation = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        proc = subprocess.Popen(
            cmd, cwd=root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
            creationflags=creation)
    except Exception as e:
        log(f"  启动失败：{e}")
        return -1

    th = threading.Thread(target=_pump, args=(proc, log), daemon=True)
    th.start()

    deadline = time.time() + float(step["minutes"]) * 60 + grace
    killed = False
    while proc.poll() is None:
        if time.time() > deadline:
            log(f"  超过 {step['minutes']} 分钟 + {grace}s 仍未退出，强制结束")
            _kill_tree(proc.pid)
            killed = True
            break
        time.sleep(0.5)
    th.join(timeout=5)
    return -9 if killed else proc.returncode


def run_combo(root, combo, log=None, py=None, settle=0.3, clear_each=True):
    """串行跑完整条组合；每步结束后清后台，下一棒自己拉起 App。

    :return: {"ok": bool, "steps": [{"task", "minutes", "code"}], "error": None}
    """
    py = py or sys.executable
    log = log or log_fn
    err = validate(root, combo)
    if err:
        log(f"组合配置有问题：{err}")
        return {"ok": False, "steps": [], "error": err}

    steps = combo["steps"]
    results = []
    log(f"=== 组合开始：{combo.get('name', '未命名')}（{len(steps)} 步）===")

    for i, step in enumerate(steps, 1):
        log(f"\n>>> 第 {i}/{len(steps)} 步：{step['task']}｜{step['minutes']} 分钟"
            + (f"｜{step['app']}" if step.get("app") else ""))
        code = run_step(py, root, step, log)
        results.append({"task": step["task"], "minutes": step["minutes"], "code": code})
        log(f"第 {i} 步结束（退出码 {code}）")

        if clear_each:
            log("  清空后台…")
            try:
                r = quick.clear_background()
                if r.get("ok"):
                    log(f"  已清理 {len(r.get('killed', []))} 个后台 App")
                else:
                    # 清后台依赖 DVT 隧道；拿不到也不影响衔接，下一步会自己冷启动 App
                    log(f"  清后台没生效（{r.get('error') or r.get('errors')}），"
                        f"下一步自己冷启动 App，照常衔接")
            except Exception as e:
                log(f"  清理异常：{e}")
            time.sleep(settle)

    log("\n=== 组合结束 ===")
    return {"ok": True, "steps": results, "error": None}


def main():
    """命令行入口：--list 查看组合，或直接跑指定组合名。"""
    root = os.path.dirname(_HERE)
    if len(sys.argv) < 2 or sys.argv[1] == "--list":
        combos = load(root)
        if not combos:
            print("还没有组合，先写 data/combos.json")
            return
        for c in combos:
            print(f"- {c.get('name')}")
            for s in c.get("steps", []):
                print(f"    {s.get('task')}  {s.get('minutes')} 分钟  {s.get('app') or ''}")
        return

    name = sys.argv[1]
    target = next((c for c in load(root) if c.get("name") == name), None)
    if not target:
        print(f"找不到组合「{name}」")
        return
    r = run_combo(root, target, log=log_fn)
    sys.exit(0 if r["ok"] else 1)


if __name__ == "__main__":
    main()
