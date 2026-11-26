# -*- coding: utf-8 -*-
"""流程脚本渲染模板：把录制的主流程动作 + 异常分支配置渲染成独立可运行的 py。

复用约定：
- 本模块只做纯文本渲染，不碰 GUI / WDA / 文件落盘；
- 产物骨架对齐 core 分层：quick 触摸、ocr 识别、issue_checker 判定、guard 兜底；
- 异常分支配置内联进产物（单文件拷走可跑），不依赖素材目录；
- steps / issues 的字段结构与 core.issue_checker、core.guard 保持一致。

steps 元素：
    {"type": "tap|hold|swipe|home|back|backswipe|text|wait",
     "x","y","x1","y1","x2","y2","ms","text","gap","note"}

issues 元素：
    {"name","keywords":[...],"region":[x1,y1,x2,y2]|None,
     "points":[{"x","y","label"}],"max_rounds","note"}
"""
import json

TYPE_LABEL = {
    "tap": "点击", "hold": "长按", "swipe": "滑动", "home": "主屏",
    "back": "返回", "backswipe": "手势返回", "text": "键入", "wait": "等待",
    "openapp": "打开App",
}

# 动作后是否接一次 OCR 兜底检测；等待步不检测
_GUARD_AFTER = {"tap", "hold", "swipe", "home", "back", "backswipe", "text",
                "openapp"}


def _py_literal(v):
    """把配置值转成合法 Python 字面量（None/布尔/中文安全）。"""
    if v is None:
        return "None"
    if isinstance(v, bool):
        return "True" if v else "False"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, str):
        return json.dumps(v, ensure_ascii=False)
    if isinstance(v, dict):
        return "{" + ", ".join(f"{_py_literal(k)}: {_py_literal(val)}"
                               for k, val in v.items()) + "}"
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(_py_literal(x) for x in v) + "]"
    return json.dumps(str(v), ensure_ascii=False)


def _step_lines(st, i):
    """返回 (注释行, 代码行列表)；代码行不含 run_once 外层缩进。"""
    t = st["type"]
    note = st.get("note", "")
    tag = f"[{i + 1}] {TYPE_LABEL.get(t, t)}"
    if note:
        tag += f" · {note}"
    comment = "# " + tag
    code = []

    gap = int(st.get("gap") or 0)
    if gap > 0:
        code.append(f"time.sleep({gap / 1000:.3f})  # 与上一步间隔 {gap}ms")

    def i2(v):
        return int(round(float(v)))

    if t == "tap":
        code.append(f"quick.tap2({i2(st['x'])}, {i2(st['y'])})")
    elif t == "hold":
        code.append(f"quick.long_press({i2(st['x'])}, {i2(st['y'])}, {i2(st['ms'])})")
    elif t == "swipe":
        code.append(
            f"quick.swipe2({i2(st['x1'])}, {i2(st['y1'])}, "
            f"{i2(st['x2'])}, {i2(st['y2'])}, {i2(st['ms'])})")
    elif t == "home":
        code.append("quick.press_home()")
    elif t == "back":
        code.append(f"quick.tap2({i2(st['x'])}, {i2(st['y'])})  # 返回箭头")
    elif t == "backswipe":
        code.append("quick.go_back()")
    elif t == "text":
        code.append(f"quick.keys({_py_literal(st.get('text', ''))})")
    elif t == "openapp":
        clean = bool(st.get("clean", False))
        code.append(f"quick.open_app({_py_literal(st.get('bundle', ''))}, "
                    f"clean={clean})")
    elif t == "wait":
        code.append(f"time.sleep({int(st.get('ms', 1000)) / 1000:.3f})")

    if t in _GUARD_AFTER:
        code.append("GUARD.check_and_handle()")
    return comment, code


def render_py(name, steps, issues=None, default_rounds=10, description=""):
    """渲染完整 py 源码文本。

    :param name: 任务名（写进 docstring）
    :param steps: 主流程动作列表
    :param issues: 异常分支配置列表
    :param default_rounds: 默认跑多少轮（0=一直跑），可被命令行参数覆盖
    :param description: 任务总描述
    """
    issues = issues or []
    L = []
    L += [
        "# -*- coding: utf-8 -*-",
        f'"""自动化流程：{name}',
    ]
    if description:
        L += [description]
    L += [
        "由动作录制导出；主流程坐标回放，每步后 OCR 检测异常弹窗并按分支处置。",
        '"""',
        "import os",
        "import sys",
        "import time",
        "",
        "HERE = os.path.dirname(os.path.abspath(__file__))",
        "ROOT = os.path.dirname(os.path.dirname(HERE))",
        'sys.path.insert(0, os.path.join(ROOT, "core"))',
        "",
        "import quick",
        "import ocr as ocr_mod",
        "import issue_checker as checker_mod",
        "from guard import IssueGuard",
        "",
        "# 停顿时长（可按真机速度调）",
        "POST_TAP = 0.8",
        "POST_SWIPE = 1.0",
        "ROUND_GAP = 2.0",
        f"DEFAULT_ROUNDS = {int(default_rounds)}  # 0 = 一直跑",
        f"TASK_NAME = {_py_literal(name)}",
        "",
        f"ISSUES = {_py_literal(issues)}",
        "GUARD = IssueGuard(ISSUES, quick, ocr_mod=ocr_mod, checker_mod=checker_mod)",
        "",
        "",
        "def run_once():",
    ]

    if not steps:
        L.append("    pass")
    for i, st in enumerate(steps):
        comment, code = _step_lines(st, i)
        L.append("    " + comment)
        for c in code:
            L.append("    " + c)

    L += [
        "",
        "",
        "def main():",
        "    max_rounds = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_ROUNDS",
        "    rounds = 0",
        '    print(f"=== {TASK_NAME} 启动（Ctrl+C 停止）===")',
        "    conn = quick.ensure_session()",
        '    if conn.get("error"):',
        "        print(f\"连接 WDA 失败：{conn['error']}\")",
        "        return",
        '    if conn.get("reconnected"):',
        '        print("WDA 会话已重建")',
        "    try:",
        "        while True:",
        "            if max_rounds and rounds >= max_rounds:",
        '                print(f"跑满 {rounds} 轮，退出")',
        "                break",
        "            rounds += 1",
        "            try:",
        "                run_once()",
        "            except Exception as e:",
        '                print(f"第 {rounds} 轮异常：{type(e).__name__}: {str(e)[:90]}")',
        "            if max_rounds and rounds >= max_rounds:",
        "                break",
        "            time.sleep(ROUND_GAP)",
        "    except KeyboardInterrupt:",
        '        print("手动停止")',
        "",
        "",
        'if __name__ == "__main__":',
        "    main()",
        "",
    ]
    return "\n".join(L)
