"""快手金币宝箱自动领取机器人 v4

为什么这样设计：
  WDA 的「读界面树 / 按文字找元素」在快手这种重界面 app 上会卡死（source、find 都超时），
  但「截图 / 坐标点击 / 滑动」一直很快。所以改成：
    读状态 -> accessibility 通道（约 5 秒，能拿到所有文字）
    做点击 -> WDA 坐标点击（0.3 秒，坐标已实测验证）

循环：
  1. 读到「领取奖励」          -> 点 (207,514)，进下一条广告，轮数+1
  2. 读到「已成功领取NN金币」  -> 点 (80,78)，弹窗
  3. 读到「NNs后可领取」       -> 等，不点
  4. 连续什么都没读到           -> 上滑换下一条

用法：
  python ks_bot.py          无限循环（Ctrl+C 停）
  python ks_bot.py 20       跑满 20 轮退出
"""
import json
import os
import re
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "core"))
import quick

LOG = os.path.join(ROOT, "data", "logs", "ks.log")
os.makedirs(os.path.dirname(LOG), exist_ok=True)

UDID = "00008030-001E44DE02D0802E"
CHEST_POS = (107, 78)       # 左上角宝箱（已实测可点）
GRAB_POS = (207, 514)      # 弹窗里的「领取奖励」
SWIPE_UP = (207, 720, 207, 260)

TIMER_PAT = re.compile(r"(\d+)\s*(?:s|秒)\s*后可领取")
READY_MARK = "已成功领取"
GRAB_MARK = "领取奖励"
IDLE_SWIPE = 5
FAIL_LIMIT = 2


def log(msg):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def read_captions():
    """读当前界面的所有文字（accessibility 通道）"""
    out = subprocess.run(
        [sys.executable, "-m", "pymobiledevice3", "--no-color",
         "developer", "accessibility", "list-items", f"--udid={UDID}"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=90).stdout
    i = out.find("[")
    if i < 0:
        raise RuntimeError("accessibility 无返回")
    data = json.loads(out[i:])
    return [d.get("caption", "") for d in data]


def tap(x, y, tag):
    log(f"点击 {tag} @({x},{y})")
    r = quick.tap2(x, y)
    if r.get("error"):
        log(f"  失败 {str(r['error'])[:90]}")
        return False
    return True


def swipe_next():
    log("上滑换下一条")
    quick.swipe(*SWIPE_UP, 0.35)


def heal():
    log("连接异常，尝试自愈 ...")
    try:
        import wda_up
        if wda_up.ensure(verbose=False):
            quick.start("")
            log("自愈完成")
            return True
        log("自愈失败（设备可能没插稳）")
    except Exception as e:
        log(f"自愈出错 {type(e).__name__}: {e}")
    return False


def main():
    max_rounds = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    rounds = 0
    fails = 0
    idle = 0
    chest_tries = 0

    log("=== 快手宝箱机器人 v4 启动（Ctrl+C 停止）===")
    try:
        quick.start("")
        log("会话已建立")
    except Exception:
        pass

    while True:
        if max_rounds and rounds >= max_rounds:
            log(f"跑满 {rounds} 轮，退出")
            break
        try:
            caps = read_captions()

            grab = [c for c in caps if GRAB_MARK in c]
            if grab:
                tap(*GRAB_POS, "领取奖励")
                rounds += 1
                fails = 0
                idle = 0
                chest_tries = 0
                log(f"--- 完成第 {rounds} 轮 ---")
                time.sleep(1.5)
                continue

            ready = [c for c in caps if READY_MARK in c and not TIMER_PAT.search(c)]
            if ready:
                chest_tries += 1
                tap(*CHEST_POS, f"开宝箱#{chest_tries}")
                fails = 0
                idle = 0
                time.sleep(1.8)
                if chest_tries >= 3:
                    chest_tries = 0
                continue

            timer = None
            for c in caps:
                m = TIMER_PAT.search(c)
                if m:
                    timer = int(m.group(1))
                    break
            if timer is not None:
                fails = 0
                idle = 0
                chest_tries = 0
                log(f"计时中，剩 {timer}s")
                time.sleep(min(3, max(1, timer)))
                continue

            idle += 1
            chest_tries = 0
            log(f"当前界面没有可领的东西（第 {idle} 次）")
            if idle >= IDLE_SWIPE:
                swipe_next()
                idle = 0
                time.sleep(1.5)
            else:
                time.sleep(1)

        except KeyboardInterrupt:
            log("手动停止")
            break
        except Exception as e:
            fails += 1
            log(f"第 {fails} 次异常：{type(e).__name__}: {str(e)[:90]}")
            if fails >= FAIL_LIMIT:
                if not heal():
                    time.sleep(10)
                fails = 0
            else:
                time.sleep(1.5)


if __name__ == "__main__":
    main()
