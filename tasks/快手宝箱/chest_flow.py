# -*- coding: utf-8 -*-
"""快手金币宝箱 · 按素材 MD 回放的线性流程（带 OCR 异常兜底）

依据素材包：data/materials/7dd1ed83603baad0df2ee2e1401be3fb.md
参考帧目录：data/materials/frames/7dd1ed83603baad0df2ee2e1401be3fb/

分层：
- 主流程：素材坐标顺序回放（core/quick.py 注入触摸）；
- 兜底：每步后由 core/guard.IssueGuard 截图 → core/ocr 识别 →
  core/issue_checker 判定，命中问题分支才按配置点处置点，未命中继续主流程。

用法：
  python chest_flow.py        跑 10 轮
  python chest_flow.py 20     跑 20 轮
  python chest_flow.py 0      一直跑（Ctrl+C 停）
"""
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "core"))
import quick  # noqa: E402
import ocr as ocr_mod  # noqa: E402
import issue_checker as checker_mod  # noqa: E402
import branch_tap  # noqa: E402
import rotlog  # noqa: E402
from guard import IssueGuard  # noqa: E402

# 素材 stem，问题分支配置从对应 json 读取
MATERIAL_STEM = "7dd1ed83603baad0df2ee2e1401be3fb"

# 停顿时长（场景自定，可按真机速度调）
POST_TAP = 0.8      # 每次点击后停顿
POST_SWIPE = 1.0    # 每次滑动后停顿
WAIT = 1.5          # 素材里的等待步
SWIPE_MS = 700      # 滑动时长
ROUND_GAP = 2.0     # 两轮之间停顿

# 弹窗推进按钮候选：同一节点两版弹窗（「领取奖励」/「去看内容」），OCR 命中哪个点哪个
ADVANCE_BUTTONS = [
    {"keywords": ["领取奖励"], "x": 207, "y": 514, "label": "领取奖励"},
    {"keywords": ["去看内容"], "x": 206, "y": 643, "label": "去看内容"},
]
ADVANCE_FALLBACK = (206, 643)

LOG = os.path.join(ROOT, "data", "logs", "ks_flow.log")
os.makedirs(os.path.dirname(LOG), exist_ok=True)


def log(msg):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    rotlog.append_line(LOG, line)


def _load_issues():
    """从素材 json 读取问题分支配置；缺失或异常时返回空列表。"""
    path = os.path.join(ROOT, "data", "materials", MATERIAL_STEM + ".json")
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f).get("issues", [])
    except Exception:
        return []


GUARD = IssueGuard(_load_issues(), quick, ocr_mod=ocr_mod, checker_mod=checker_mod)


def run_once(round_no):
    log(f"--- 第 {round_no} 轮开始 ---")

    # [1] 点击 · 参考帧 f0003.png · 关闭顶部横幅
    quick.tap2(183, 73)
    time.sleep(POST_TAP)
    GUARD.check_and_handle()

    # [2] 等待 · 参考帧 f0003.png
    time.sleep(WAIT)

    # [3] 点击 · 参考帧 f0002.png
    quick.tap2(206, 733)
    time.sleep(POST_TAP)
    GUARD.check_and_handle()

    # [4] 等待 · 参考帧 f0003.png
    time.sleep(WAIT)

    # [5] 弹窗推进 · 参考帧 f0016.png（两版按钮 OCR 定位：领取奖励 / 去看内容）
    res = branch_tap.tap_candidates(quick, ocr_mod, ADVANCE_BUTTONS,
                                    fallback=ADVANCE_FALLBACK, post_sleep=POST_TAP)
    log(f"    推进按钮：{res['label']}")
    GUARD.check_and_handle()

    # [6] 滑动 · 参考帧 f0022.png
    quick.swipe2(195, 389, 195, 632, SWIPE_MS)
    time.sleep(POST_SWIPE)
    GUARD.check_and_handle()

    # [7] 滑动 · 参考帧 f0034.png
    quick.swipe2(149, 715, 144, 378, SWIPE_MS)
    time.sleep(POST_SWIPE)
    GUARD.check_and_handle()

    # [8] 滑动 · 参考帧 f0034.png
    quick.swipe2(273, 727, 267, 427, SWIPE_MS)
    time.sleep(POST_SWIPE)
    GUARD.check_and_handle()

    # [9] 等待 · 参考帧 f0034.png
    time.sleep(WAIT)

    # [10] 点击 · 参考帧 f0044.png · 返回
    quick.tap2(26, 67)
    time.sleep(POST_TAP)
    GUARD.check_and_handle()
    log(f"--- 第 {round_no} 轮结束 ---")


def main():
    max_rounds = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    rounds = 0
    log("=== 快手宝箱流程回放启动（Ctrl+C 停止）===")
    conn = quick.ensure_session()
    if conn.get("error"):
        log(f"连接 WDA 失败：{conn['error']}")
        return
    if conn.get("reconnected"):
        log("WDA 会话已重建")
    try:
        while True:
            if max_rounds and rounds >= max_rounds:
                log(f"跑满 {rounds} 轮，退出")
                break
            rounds += 1
            try:
                run_once(rounds)
            except Exception as e:
                log(f"第 {rounds} 轮异常：{type(e).__name__}: {str(e)[:90]}")
            if max_rounds and rounds >= max_rounds:
                break
            time.sleep(ROUND_GAP)
    except KeyboardInterrupt:
        log("手动停止")


if __name__ == "__main__":
    main()
