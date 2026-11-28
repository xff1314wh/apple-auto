# -*- coding: utf-8 -*-
"""OCR / source 耗时测算：真机实测单次成本 + 一轮路径建模，对比各优化方案收益。

不改主流程，只连 WDA 采样计时并输出对比表。
跑：D:\\python\\python.exe bench_ocr_cost.py

帧次模型按「典型 lead 留资路径」一轮估算（数值在 MODEL 区可调）：
  ensure_chain 1 OCR；open_consult 2 OCR；depth 分类 1 OCR + 1 source；
  handle_lead 1 屏 1 OCR + 1 source；close_ad 3 层 3 OCR + 3 source；
  wait_countdown 等待期轮询 12 OCR；next_ad 1 OCR。
  现状合计：21 OCR 帧、5 source 次。
注意：倒计时 22s 是业务必须看完的等待，不可压；方案 C 省的是等待期内的轮询 OCR，
不是等待本身。
"""
import io
import os
import re
import sys
import time
import statistics

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "core"))
import quick  # noqa: E402
import ocr as ocr_mod  # noqa: E402
from PIL import Image  # noqa: E402

# ---------------- 真机实测采样 ----------------

def _timeit(fn, n, warm=0):
    for _ in range(warm):
        fn()
    ts = []
    for _ in range(n):
        t0 = time.perf_counter()
        fn()
        ts.append(time.perf_counter() - t0)
    return ts


def _avg(ts):
    return statistics.mean(ts) if ts else 0.0


def _parse_source(xml):
    nodes = []
    for tag in re.findall(r"<XCUIElementType\w+\b[^>]*>", xml):
        def num(k):
            m = re.search(rf'{k}="([^"]*)"', tag)
            try:
                return float(m.group(1))
            except (TypeError, ValueError, AttributeError):
                return None
        x, y, w, h = num("x"), num("y"), num("width"), num("height")
        if None in (x, y, w, h):
            continue
        nodes.append((x, y, w, h))
    return nodes


def sample_baseline():
    """返回单次耗时基线（秒）。真机连不上时退回历史兜底值。"""
    fallback = dict(shot=0.29, ocr_full=1.70, source=2.70,
                    ocr_roi_top=0.90, ocr_roi_bot=0.90, cold=6.18, online=False)
    try:
        conn = quick.ensure_session()
        if conn.get("error"):
            print("[!] WDA 连不上，使用历史兜底基线值\n")
            return fallback

        shot_ts = _timeit(lambda: quick.shot_bytes(), 5)

        # 冷启 1 次单独记（引擎加载），再取热帧均值
        t0 = time.perf_counter()
        data, err = quick.shot_bytes()
        ocr_mod.recognize(data)
        cold = time.perf_counter() - t0
        full_ts = _timeit(lambda: ocr_mod.recognize(quick.shot_bytes()[0]), 5)

        def pull_source():
            r = quick._req("GET", f"/session/{quick.session()}/source", timeout=15)
            xml = r.get("value", "") if isinstance(r, dict) else ""
            return _parse_source(xml)

        src_ts = _timeit(pull_source, 3)

        def crop(top=True):
            d, _ = quick.shot_bytes()
            im = Image.open(io.BytesIO(d))
            W, H = im.size
            box = (0, 0, W, 240) if top else (0, H - 320, W, H)
            return im.crop(box)

        top_ts = _timeit(lambda: ocr_mod.recognize(crop(True)), 5)
        bot_ts = _timeit(lambda: ocr_mod.recognize(crop(False)), 5)

        return dict(shot=_avg(shot_ts), ocr_full=_avg(full_ts),
                    source=_avg(src_ts), ocr_roi_top=_avg(top_ts),
                    ocr_roi_bot=_avg(bot_ts), cold=cold, online=True)
    except Exception as e:
        print(f"[!] 采样异常 {type(e).__name__}: {e}，使用历史兜底基线值\n")
        return fallback


# ---------------- 一轮路径帧次模型 ----------------
# 现状一轮：OCR 全帧次数、source 次数
MODEL = {
    "base": {"ocr": 21, "src": 5},
    # A 首帧复用：同屏外层识别完内层不再重拍，省重复 OCR 2 帧、source 1 次
    "A_reuse": {"ocr": -2, "src": -1},
    # B 判定下沉 source：分类/弹窗/按钮走节点树，OCR 只兜底封闭 WebView，省 OCR 2 帧
    "B_source": {"ocr": -2, "src": 0},
    # C 倒计时定时 + 末尾确认：等待期不再逐帧 OCR，省 OCR 10 帧（留 2 帧确认）
    "C_countdown": {"ocr": -10, "src": 0},
}

# 方案叠加链：现状 → A → A+B → A+B+C → A+B+C+D(ROI 裁剪)
PLANS = [
    ("现状基线", ["base"], False),
    ("A 首帧复用", ["base", "A_reuse"], False),
    ("A+B source 下沉判定", ["base", "A_reuse", "B_source"], False),
    ("A+B+C 倒计时定时确认", ["base", "A_reuse", "B_source", "C_countdown"], False),
    ("A+B+C+D ROI 裁剪 OCR", ["base", "A_reuse", "B_source", "C_countdown"], True),
]


def plan_frames(keys):
    ocr = src = 0
    for k in keys:
        ocr += MODEL[k]["ocr"]
        src += MODEL[k]["src"]
    return max(ocr, 0), max(src, 0)


def main():
    base = sample_baseline()
    roi = (base["ocr_roi_top"] + base["ocr_roi_bot"]) / 2

    print("=== 单次耗时基线（秒/次）===")
    print(f"截图 shot_bytes        : {base['shot']:.3f}")
    print(f"全帧 OCR（热帧均值）   : {base['ocr_full']:.3f}  （冷启首帧 {base['cold']:.2f}）")
    print(f"source 拉取+解析       : {base['source']:.3f}")
    print(f"ROI 顶部条 OCR         : {base['ocr_roi_top']:.3f}")
    print(f"ROI 底部条 OCR         : {base['ocr_roi_bot']:.3f}")
    print(f"ROI 均值               : {roi:.3f}")
    print(f"在线采样: {base['online']}\n")

    # 现状成本
    b_ocr, b_src = plan_frames(["base"])
    b_ocr_cost = b_ocr * base["ocr_full"]
    b_src_cost = b_src * base["source"]
    b_total = b_ocr_cost + b_src_cost

    rows = []
    for name, keys, use_roi in PLANS:
        ocr_n, src_n = plan_frames(keys)
        ocr_unit = roi if use_roi else base["ocr_full"]
        ocr_cost = ocr_n * ocr_unit
        src_cost = src_n * base["source"]
        total = ocr_cost + src_cost
        saved = b_total - total
        pct = (saved / b_total * 100) if b_total > 0 else 0
        rows.append((name, ocr_n, src_n, ocr_cost, src_cost, total, saved, pct))

    print("=== 一轮成本对比（典型 lead 路径，不含 22s 业务等待）===")
    hdr = f"{'方案':<24}{'OCR帧':>6}{'src次':>7}{'OCR耗时':>10}{'src耗时':>10}{'合计':>9}{'省秒':>9}{'降幅':>8}"
    print(hdr)
    print("-" * len(hdr.encode("gbk", errors="ignore")))
    for name, ocr_n, src_n, oc, sc, total, saved, pct in rows:
        print(f"{name:<24}{ocr_n:>6}{src_n:>7}{oc:>10.2f}{sc:>10.2f}{total:>9.2f}{saved:>9.2f}{pct:>7.1f}%")

    print("\n=== 结论要点 ===")
    print("1. 最大头是倒计时等待期的逐帧 OCR（方案 C）：把轮询改成定时长 sleep + 末尾 1~2 帧确认，")
    print("   省的是等待期重复识别，22s 业务看完时间不变。")
    print("2. source 单次（~2.7s）比 OCR 全帧（~1.7s）还贵，能复用同一份 nodes 就别重复拉。")
    print("3. ROI 裁剪只对必须 OCR 的帧有效；source 能判定的页面不应再送全帧 OCR。")
    print("4. 颜色/像素规则（按钮高亮、勾选态）属定性优化，未计入上表。")


if __name__ == "__main__":
    main()
