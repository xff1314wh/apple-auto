"""大麦抢票：预约之后的一键流程

页面顺序（真机校准，414x896 坐标）：

  ① 预约选票页  底部橙色「已预约」                          (250,872)
  ② 演出详情页  底部橙色「已预约 / 立即预订」                (336,836)
  ③ 确认购买页  右下橙色「立即提交」                        (336,836)
  ④ 支付面板    蓝色「付款」                                (210,805)  ← 人脸/密码留给人

【点击机制（实测定稿，iPhone 11 / iOS 18.7.8）】

  一个请求里排 N 组完整 down/up 周期，每组就是一次独立触摸：

      move(按钮) → pointerDown → pause(hold) → pointerUp
      move(按钮) → pointerDown → pause(hold) → pointerUp
      ... 重复 N 组，组间可选 pause(gap)

  实测：5 下 5/5 生效，10 下 ≥10/10 生效，约 230ms/次，一个请求发完。
  对照实验排除：坐标抖动、hold 随机、gap 随机都不是跑飞原因（五组全部稳定）。

  唯一要防的是冷启动第一下：指针起点在屏幕原点，划到底部会形成长垂直轨迹。
  所以第一段发之前先做一次 warm 锚定，把指针落到按钮邻近位置。

【参数建议（尽量短为优，实测基准 ~230ms/次，下面的时长叠加在其上）】

  --jitter N   段首坐标抖动 ±pt。最快 0；稳妥 2。默认 2。抖动只在段首做一次，段内固定。
  --hold MS     按住时长。最快 20ms；稳妥 30ms。低于 15ms 可能没被识别。默认 20。
  --gap MS      两下之间间隔。最快 0；偶尔丢点再加到 30。默认 0。
  --hold / --gap 可以给区间 "20-60" 表示随机取值；--fixed 关闭随机（取低值）。

用法：
  python dm_ticket.py --at 20:00:00
  python dm_ticket.py --at 20:00:00 --lead 200 --pay
  python dm_ticket.py --at 20:00:00 --jitter 2 --hold 20 --gap 0
  python dm_ticket.py --stepwise                 只打日志不发请求，看流程和预计耗时
"""
import os
import random
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "tasks", "通用"))
from fasttap import Tapper, wait_until          # noqa: E402

BTN_BOOKED = (250, 872)     # 预约选票页底部「已预约」
BTN_BUY = (336, 836)        # 详情页「已预约/立即预订」＝确认页「立即提交」
BTN_PAY = (210, 805)        # 支付面板「付款」

WAIT_LOAD = 900             # 详情页 → 确认页 的加载时间
WAIT_SUBMIT = 1200          # 提交 → 支付面板

# 默认参数（短为优）
DEF_JITTER = 2
DEF_HOLD = "20"
DEF_GAP = "0"
WARM_OFFSET = 40            # 预热锚定：按钮上方多少 pt
SEG_MAX = 10                # 单段上限（20 下会 10s 超时）

# 每下 XCTest 固有处理开销（实测 230~270ms），用于 stepwise 估时
BASE_PER_TAP = 230


def _rng(s, fixed):
    """把 "20" 或 "20-60" 变成一个取数函数。fixed 时取定值/区间低值。"""
    if "-" in s:
        a, b = (int(v) for v in s.split("-", 1))
        return (lambda: a) if fixed else (lambda: random.randint(a, b))
    v = int(s)
    return lambda: v


def _source(acts):
    return {"type": "pointer", "id": "finger1",
            "parameters": {"pointerType": "touch"}, "actions": acts}


def _warm(t, x, y):
    """冷启动锚定：先把指针落到按钮上方邻近位置，避免第一下从原点起飞。"""
    y0 = max(1, y - WARM_OFFSET)
    t._send([_source([{"type": "pointerMove", "duration": 0, "x": x, "y": y0}])])


def _seg(t, x, y, n, hold_fn, gap_fn, jitter):
    """一个请求内排 n 组 down/up 周期，段首坐标固定抖动一次。"""
    if jitter:
        x += random.randint(-jitter, jitter)
        y += random.randint(-jitter, jitter)
    acts = []
    for i in range(n):
        h = hold_fn()
        g = gap_fn()
        acts += [{"type": "pointerMove", "duration": 0, "x": x, "y": y},
                 {"type": "pointerDown", "button": 0},
                 {"type": "pause", "duration": h},
                 {"type": "pointerUp", "button": 0}]
        if g and i != n - 1:
            acts.append({"type": "pause", "duration": g})
    t._send([_source(acts)])


def run(t, plan, hold_fn, gap_fn, jitter):
    total = sum(p[2] for p in plan)
    t_start = time.perf_counter()
    warmed = False
    for i, (x, y, n, rest) in enumerate(plan, 1):
        if not warmed:
            _warm(t, x, y)
            warmed = True
        t0 = time.perf_counter()
        left = n
        while left > 0:
            k = min(left, SEG_MAX)
            _seg(t, x, y, k, hold_fn, gap_fn, jitter)
            left -= k
        print("  第%d段 (%d,%d) ×%d  %.0fms" % (i, x, y, n, (time.perf_counter() - t0) * 1000))
        if rest:
            print("  等待 %dms" % rest)
            time.sleep(rest / 1000.0)
    print("总耗时 %.1f 秒（%d 下，%d 段）"
          % (time.perf_counter() - t_start, total, len(plan)))


def stepwise(plan, hold_fn, gap_fn, jitter):
    for i, (x, y, n, rest) in enumerate(plan, 1):
        print("  第%d段 (%d,%d) ×%d，之后等 %dms" % (i, x, y, n, rest))
    hold_m = (hold_fn() + hold_fn()) // 2
    gap_m = (gap_fn() + gap_fn()) // 2
    est = sum(p[2] for p in plan) * (BASE_PER_TAP + hold_m + gap_m) / 1000.0
    est += sum(p[3] for p in plan) / 1000.0
    print("  参数：抖动 ±%dpt，按住 %dms，间隔 %dms" % (jitter, hold_m, gap_m))
    print("  预计 %.1f 秒" % est)


def main():
    args = sys.argv[1:]
    at, do_pay, lead, fixed = None, False, 0, False
    mode = "run"
    jitter_s, hold_s, gap_s = str(DEF_JITTER), DEF_HOLD, DEF_GAP

    i = 0
    while i < len(args):
        a = args[i]
        if a == "--at" and i + 1 < len(args):
            at = args[i + 1]; i += 1
        elif a == "--pay":
            do_pay = True
        elif a == "--fixed":
            fixed = True
        elif a == "--stepwise":
            mode = "stepwise"
        elif a == "--lead" and i + 1 < len(args):
            lead = int(args[i + 1]); i += 1
        elif a == "--jitter" and i + 1 < len(args):
            jitter_s = args[i + 1]; i += 1
        elif a == "--hold" and i + 1 < len(args):
            hold_s = args[i + 1]; i += 1
        elif a == "--gap" and i + 1 < len(args):
            gap_s = args[i + 1]; i += 1
        i += 1

    jitter = int(jitter_s)
    hold_fn = _rng(hold_s, fixed)
    gap_fn = _rng(gap_s, fixed)

    if at:
        if lead > 0:
            h, m, s = [float(v) for v in (at.split(":") + ["0", "0"])[:3]]
            total = h * 3600 + m * 60 + s - lead / 1000.0
            at = "%02d:%02d:%06.3f" % (total // 3600, (total % 3600) // 60, total % 60)
        wait_until(at)

    plan = [
        (BTN_BOOKED[0], BTN_BOOKED[1], 2, 700),      # 停在预约页时才有效
        (BTN_BUY[0], BTN_BUY[1], 4, WAIT_LOAD),      # 详情页 → 进确认页
        (BTN_BUY[0], BTN_BUY[1], 4, 200),             # 确认页 → 提交
        (BTN_BUY[0], BTN_BUY[1], 4, 0),               # 继续顶着提交（穿排队）
    ]
    if do_pay:
        plan.append((BTN_PAY[0], BTN_PAY[1], 3, 0))

    t = Tapper()
    print("开始抢票（一个请求多组 down/up，抖动 ±%dpt）" % jitter)
    if mode == "stepwise":
        stepwise(plan, hold_fn, gap_fn, jitter)
    else:
        run(t, plan, hold_fn, gap_fn, jitter)

    print("结束。停在支付页的话，付款那一下要人脸或密码，得你自己按。")


if __name__ == "__main__":
    main()
