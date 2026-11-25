# -*- coding: utf-8 -*-
"""异常分支判定层：纯逻辑，不碰 IO / WDA / OCR 引擎。

输入标准化的 OCR 结果列表 + 问题分支配置列表，返回命中的分支。
判定规则：
- 分支 ``keywords`` 中任一词被某条识别文字包含（忽略大小写）；
- 若分支配了 ``region``（[x1,y1,x2,y2]，points），命中文字的中心必须落在区域内；
- 配置列表顺序即优先级，返回第一个命中的分支。

issue 配置字段（与素材 json 对齐）：
    name, keywords:[str], region:[x1,y1,x2,y2]|None,
    points:[{x,y,label}], max_rounds, note
"""
from typing import List, Optional, Tuple


def _in_region(center, region) -> bool:
    if not region or len(region) != 4:
        return True
    x, y = center
    x1, y1, x2, y2 = region
    return x1 <= x <= x2 and y1 <= y <= y2


def _keyword_hit(text: str, keywords: List[str]) -> bool:
    text_low = text.lower()
    return any(kw and kw.lower() in text_low for kw in keywords)


def match_first(issues: List[dict], items) -> Tuple[Optional[dict], Optional[object]]:
    """返回 (命中的 issue 配置, 命中的 OcrItem)；未命中返回 (None, None)。"""
    for iss in issues:
        keywords = iss.get("keywords") or []
        if not keywords:
            continue
        region = iss.get("region")
        for it in items:
            if _keyword_hit(it.text, keywords) and _in_region(it.center, region):
                return iss, it
    return None, None


def hit_label(iss: dict, it) -> str:
    """日志用简述。"""
    name = iss.get("name", "问题界面")
    text = getattr(it, "text", "")
    return f"命中[{name}] 文字={text!r}"
