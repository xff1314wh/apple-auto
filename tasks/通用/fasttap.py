"""快速连点器：不读界面，直接按坐标高频点击。

原理：常驻进程 + 长连接复用，省掉"每次启动 Python 进程 + 建连"的开销。

用法：

  单点连击
    python fasttap.py 200 800 --delay 120 --rounds 40

  定时开抢（到点自动开始，进程提前起来不浪费时间）
    python fasttap.py 200 800 --delay 120 --rounds 40 --at 12:00:00

  多段流水线
    python fasttap.py --script "200,800,120,20; wait:1500; 210,760,120,10"

坐标是屏幕点（iPhone 11 为 414 x 896）。可用 python quick.py shot data/shots/a.png 截图后目测。
"""
import base64
import http.client
import json
import os
import sys
import time
from datetime import datetime, timedelta

HOST = "127.0.0.1"
PORT = 8100
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SID_FILE = os.path.join(ROOT, "data", "wda_sid.txt")

# 点击 = 一组完整 down/up 周期：move→down→pause→up。
# 一次请求里可以重复多组，每组就是一次独立触摸（实测 10 下一个请求稳定生效）。
# 注意：第一下起点继承上次触摸位置，冷启动先锚定，避免从原点划到底部形成轨迹。
TAP_TEMPLATE = {"actions": [{"type": "pointer", "id": "finger1",
                             "parameters": {"pointerType": "touch"},
                             "actions": [
                                 {"type": "pointerMove", "duration": 0, "x": 0, "y": 0},
                                 {"type": "pointerDown", "button": 0},
                                 {"type": "pause", "duration": 30},
                                 {"type": "pointerUp", "button": 0}]}]}


class Tapper:
    def __init__(self):
        self.sid = open(SID_FILE, encoding="utf-8").read().strip()
        self.conn = None
        self.count = 0
        self.t_total = 0.0

    def _connect(self):
        if self.conn:
            try:
                self.conn.close()
            except Exception:
                pass
        self.conn = http.client.HTTPConnection(HOST, PORT, timeout=5)

    def tap(self, x, y):
        acts = json.loads(json.dumps(TAP_TEMPLATE))
        acts["actions"][0]["actions"][0]["x"] = int(x)
        acts["actions"][0]["actions"][0]["y"] = int(y)
        body = json.dumps(acts)
        headers = {"Content-Type": "application/json"}
        for attempt in range(2):
            try:
                if self.conn is None:
                    self._connect()
                t0 = time.perf_counter()
                self.conn.request("POST", f"/session/{self.sid}/actions",
                                  body=body, headers=headers)
                resp = self.conn.getresponse()
                resp.read()
                dt = (time.perf_counter() - t0) * 1000
                self.count += 1
                self.t_total += dt
                return dt
            except Exception:
                self.conn = None
                if attempt == 1:
                    return -1
                time.sleep(0.05)
        return -1

    def tap_move(self, x, y):
        """点一下（单条 pointerMove，独立请求）。"""
        return self.tap(x, y)

    def long(self, x, y, hold_ms):
        return self._send([{"type": "pointer", "id": "finger1",
                            "parameters": {"pointerType": "touch"},
                            "actions": [{"type": "pointerMove", "duration": 0, "x": int(x), "y": int(y)},
                                        {"type": "pointerDown", "button": 0},
                                        {"type": "pause", "duration": int(hold_ms)},
                                        {"type": "pointerUp", "button": 0}]}])

    def swipe(self, x1, y1, x2, y2, dur_ms):
        return self._send([{"type": "pointer", "id": "finger1",
                            "parameters": {"pointerType": "touch"},
                            "actions": [{"type": "pointerMove", "duration": 0, "x": int(x1), "y": int(y1)},
                                        {"type": "pointerDown", "button": 0},
                                        {"type": "pointerMove", "duration": int(dur_ms), "x": int(x2), "y": int(y2)},
                                        {"type": "pointerUp", "button": 0}]}])

    def back(self, y=448):
        """返回上一页：左边缘分步右滑。"""
        return self._send([{"type": "pointer", "id": "finger1",
                            "parameters": {"pointerType": "touch"},
                            "actions": [
                                {"type": "pointerMove", "duration": 0, "x": 1, "y": int(y)},
                                {"type": "pointerDown", "button": 0},
                                {"type": "pointerMove", "duration": 20, "x": 40, "y": int(y)},
                                {"type": "pointerMove", "duration": 40, "x": 120, "y": int(y)},
                                {"type": "pointerMove", "duration": 60, "x": 260, "y": int(y)},
                                {"type": "pointerUp", "button": 0}]}])

    def home(self):
        """按 Home 键回主屏。"""
        body = json.dumps({"name": "home"})
        headers = {"Content-Type": "application/json"}
        for _ in range(2):
            try:
                if self.conn is None:
                    self._connect()
                self.conn.request("POST", f"/session/{self.sid}/wda/pressButton",
                                  body=body, headers=headers)
                self.conn.getresponse().read()
                self.count += 1
                return 0
            except Exception:
                self.conn = None
                time.sleep(0.05)
        return -1

    def text(self, s):
        """把一段文字打进当前焦点输入框。"""
        body = json.dumps({"value": [s]})
        headers = {"Content-Type": "application/json"}
        for _ in range(2):
            try:
                if self.conn is None:
                    self._connect()
                self.conn.request("POST", f"/session/{self.sid}/wda/keys",
                                  body=body, headers=headers)
                self.conn.getresponse().read()
                self.count += 1
                return 0
            except Exception:
                self.conn = None
                time.sleep(0.05)
        return -1

    def _send(self, acts):
        body = json.dumps({"actions": acts})
        headers = {"Content-Type": "application/json"}
        for _ in range(2):
            try:
                if self.conn is None:
                    self._connect()
                self.conn.request("POST", f"/session/{self.sid}/actions", body=body, headers=headers)
                self.conn.getresponse().read()
                self.count += 1
                return 0
            except Exception:
                self.conn = None
                time.sleep(0.05)
        return -1


def wait_until(at_str):
    """等到 HH:MM:SS（若已过则视为明天）。"""
    parts = (at_str.split(":") + ["0", "0"])[:3]
    hh, mm = int(parts[0]), int(parts[1])
    ss = float(parts[2])
    now = datetime.now()
    target = (now.replace(hour=hh, minute=mm, second=0, microsecond=0)
              + timedelta(seconds=ss))
    if target <= now:
        target += timedelta(days=1)
    left = (target - now).total_seconds()
    print(f"定时启动：等到 {target.strftime('%H:%M:%S')}（还有 {left:.0f} 秒）")
    while (target - datetime.now()).total_seconds() > 2:
        time.sleep(0.5)
    while datetime.now() < target:
        time.sleep(0.005)
    print("到点，开始点击")


def burst(tapper, x, y, delay_ms, rounds):
    n = 0
    while True:
        dt = tapper.tap(x, y)
        n += 1
        if dt < 0:
            print(f"  第{n}次失败（连接问题）")
        elif n % 10 == 0 or n == 1:
            print(f"  ({x},{y}) 第{n}次 {dt:.0f}ms")
        if rounds and n >= rounds:
            return n
        time.sleep(delay_ms / 1000.0)


def parse_script(script):
    """支持四种段：
        普通点击  x,y,间隔ms,次数
        等待      wait:毫秒
        长按      hold:x,y,按住毫秒
        滑动      swipe:x1,y1,x2,y2,滑动毫秒
        主屏      home:（按 Home 键）
        返回      back:x,y（点左上箭头，可省坐标默认 34,70）
        手势返回  backswipe:（左边缘分步右滑）
    """
    steps = []
    for seg in script.split(";"):
        seg = seg.strip()
        if not seg:
            continue
        if seg.startswith("wait:"):
            steps.append(("wait", float(seg.split(":", 1)[1]) / 1000.0))
            continue
        if seg == "backswipe:" or seg == "backswipe":
            steps.append(("backswipe",))
            continue
        if seg.startswith("back:"):
            body = seg[5:].strip()
            if body:
                px, py = [int(v) for v in body.split(",")]
            else:
                px, py = 34, 70
            steps.append(("back", px, py))
            continue
        if seg == "home:" or seg == "home":
            steps.append(("home",))
            continue
        if seg.startswith("text64:"):
            try:
                steps.append(("text", base64.b64decode(seg[7:]).decode("utf-8")))
            except Exception:
                raise ValueError(f"text64 段解码失败：{seg}")
            continue
        if seg.startswith("text:"):
            steps.append(("text", seg[5:]))
            continue
        if seg.startswith("hold:"):
            parts = [p.strip() for p in seg[5:].split(",")]
            if len(parts) != 3:
                raise ValueError(f"长按段格式不对：{seg}（应为 hold:x,y,按住毫秒）")
            steps.append(("hold", int(parts[0]), int(parts[1]), int(parts[2])))
            continue
        if seg.startswith("swipe:"):
            parts = [p.strip() for p in seg[6:].split(",")]
            if len(parts) != 5:
                raise ValueError(f"滑动段格式不对：{seg}（应为 swipe:x1,y1,x2,y2,滑动毫秒）")
            steps.append(("swipe", int(parts[0]), int(parts[1]),
                          int(parts[2]), int(parts[3]), int(parts[4])))
            continue
        parts = [p.strip() for p in seg.split(",")]
        if len(parts) != 4:
            raise ValueError(f"段格式不对：{seg}（应为 x,y,间隔ms,次数）")
        steps.append(("tap", int(parts[0]), int(parts[1]), int(parts[2]), int(parts[3])))
    return steps


def main():
    args = sys.argv[1:]
    at_str = None
    if "--at" in args:
        i = args.index("--at")
        at_str = args[i + 1]
        del args[i:i + 2]

    if "--script" in args:
        i = args.index("--script")
        if i + 1 >= len(args):
            print("--script 后面要跟脚本串")
            return
        steps = parse_script(args[i + 1])
        if at_str:
            wait_until(at_str)
        tapper = Tapper()
        total0 = time.time()
        try:
            for st in steps:
                if st[0] == "wait":
                    print(f"等待 {st[1] * 1000:.0f}ms")
                    time.sleep(st[1])
                elif st[0] == "text":
                    print(f'键入文字 "{st[1]}"')
                    tapper.text(st[1])
                elif st[0] == "hold":
                    _, x, y, ms = st
                    print(f"长按 ({x},{y}) {ms}ms")
                    tapper.long(x, y, ms)
                elif st[0] == "back":
                    _, px, py = st
                    print(f"返回（点 {px},{py}）")
                    tapper.tap(px, py)
                elif st[0] == "backswipe":
                    print("返回（边缘手势）")
                    tapper.back()
                elif st[0] == "home":
                    print("回到主屏")
                    tapper.home()
                elif st[0] == "swipe":
                    _, x1, y1, x2, y2, ms = st
                    print(f"滑动 ({x1},{y1}) -> ({x2},{y2}) {ms}ms")
                    tapper.swipe(x1, y1, x2, y2, ms)
                else:
                    _, x, y, d, r = st
                    print(f"点击 ({x},{y})：{r} 次，间隔 {d}ms")
                    burst(tapper, x, y, d, r)
        except KeyboardInterrupt:
            print("中断")
        finally:
            used = time.time() - total0
            avg = (tapper.t_total / tapper.count) if tapper.count else 0
            print(f"共点击 {tapper.count} 次，用时 {used:.1f}s，平均 {avg:.0f}ms/次")
        return

    if len(args) < 2:
        print("用法：python fasttap.py <x> <y> [--delay 120] [--rounds 40] [--at HH:MM:SS]")
        print("      python fasttap.py --script \"200,800,120,30; wait:1500; 210,760,120,10\"")
        return

    x, y = int(args[0]), int(args[1])
    delay = 120
    rounds = 40
    if "--delay" in args:
        delay = int(args[args.index("--delay") + 1])
    if "--rounds" in args:
        rounds = int(args[args.index("--rounds") + 1])

    if at_str:
        wait_until(at_str)

    tapper = Tapper()
    t0 = time.time()
    try:
        burst(tapper, x, y, delay, rounds)
    except KeyboardInterrupt:
        print("中断")
    finally:
        used = time.time() - t0
        avg = (tapper.t_total / tapper.count) if tapper.count else 0
        print(f"共点击 {tapper.count} 次，用时 {used:.1f}s，平均 {avg:.0f}ms/次")


if __name__ == "__main__":
    main()
