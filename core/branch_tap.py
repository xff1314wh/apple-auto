# -*- coding: utf-8 -*-
"""候选按钮条件点击：截图 OCR，在多个候选按钮里识别当前出现的那个并点击。

适用场景：同一个流程节点会弹出多种形态的弹窗，每种形态主按钮文字 / 位置不同，
不能写死单一坐标。这里按候选顺序匹配，命中哪个点哪个；都没命中再走兜底坐标。

分层职责：
- 只做「识别 → 选候选 → 触摸」编排，不含业务坐标；
- 依赖注入 quick（截图 / 触摸）、ocr_mod（识别），便于离线测试替换；
- OCR 不可用或识别异常时，按兜底坐标处理，不抛异常中断主流程。
"""
from typing import List, Optional, Tuple


def _hit_item(items, keywords, region=None):
    """返回第一个关键词命中（可选区域内）的 OcrItem；未命中返回 None。"""
    for it in items:
        text = getattr(it, "text", "") or ""
        text_low = text.lower()
        if any(kw and kw.lower() in text_low for kw in keywords):
            if region:
                x, y = it.center
                x1, y1, x2, y2 = region
                if not (x1 <= x <= x2 and y1 <= y <= y2):
                    continue
            return it
    return None


def tap_candidates(quick, ocr_mod, candidates, fallback=None, post_sleep=0.8):
    """依次匹配候选按钮并点击。

    :param candidates: [{keywords:[str], x:int, y:int, label:str, region:None}]
    :param fallback: (x, y)，全部未命中时点击；None 表示未命中不点。
    :param post_sleep: 点击后停顿秒数。
    :return: {"hit": bool, "label": str, "center": (x, y)|None, "fallback": bool}
    """
    items: List = []
    if ocr_mod is not None:
        try:
            data, err = quick.shot_bytes()
            if not err and data:
                items = ocr_mod.recognize(data)
        except Exception:
            items = []

    for cand in candidates:
        hit = _hit_item(items, cand.get("keywords") or [], cand.get("region"))
        if hit is None:
            continue
        x, y = int(cand["x"]), int(cand["y"])
        quick.tap2(x, y)
        try:
            import time
            time.sleep(post_sleep)
        except Exception:
            pass
        return {"hit": True, "label": cand.get("label", "候选按钮"),
                "center": tuple(hit.center), "fallback": False}

    # 全部未命中
    if fallback:
        quick.tap2(int(fallback[0]), int(fallback[1]))
        try:
            import time
            time.sleep(post_sleep)
        except Exception:
            pass
        return {"hit": False, "label": "兜底坐标", "center": None, "fallback": True}

    return {"hit": False, "label": "未命中", "center": None, "fallback": False}
