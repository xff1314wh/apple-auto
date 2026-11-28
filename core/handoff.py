# -*- coding: utf-8 -*-
"""handoff 客户端：任务脚本与本地介入信号服务交接。

流程：unknown 页面 → post_pending 落待办 → poll_done 挂起等标注 → replay 回放动作；
台账命中（match_case）则直接回放，不再叫人。服务不可达 / 超时由调用方降级兜底。

动作模型（annotation.actions 元素）：
    {"action": "tap",   "x": int, "y": int}
    {"action": "back"}
    {"action": "home"}
    {"action": "keys",  "text": str}
    {"action": "swipe", "x1": int, "y1": int, "x2": int, "y2": int}
"""
import hashlib
import json
import os
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8210"
POLL_GAP = 2.0        # 脚本轮询 done 间隔（秒）
WAIT_TIMEOUT = 300.0  # 等标注超时（秒，5 分钟）
HEARTBEAT = 10.0      # 等待期保活心跳间隔（秒）

_HANDOFF_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "handoff")
os.makedirs(_HANDOFF_DIR, exist_ok=True)


def _req(method, path, payload=None, timeout=5):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    if data:
        req.add_header("Content-Type", "application/json")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=timeout) as r:
        body = r.read().decode("utf-8", "replace")
        return json.loads(body) if body else {}


def available():
    """服务是否在跑；不可达返回 False，调用方据此降级。"""
    try:
        _req("GET", "/health", timeout=2)
        return True
    except Exception:
        return False


def fingerprint(items):
    """由 OCR 文本集合算页面指纹（排序去重拼接后取 md5 前 12 位）。"""
    texts = sorted({(it.text or "").strip() for it in items if (it.text or "").strip()})
    return hashlib.md5("|".join(texts).encode("utf-8")).hexdigest()[:12]


def load_cases():
    """拉台账；服务不可达返回空 dict。"""
    try:
        return _req("GET", "/cases", timeout=5).get("cases", {})
    except Exception:
        return {}


def match_case(cases, fp):
    """台账指纹命中返回条目，否则 None。"""
    return cases.get(fp) if cases else None


def post_pending(fp, items, shot_path, round_no, depth, note=""):
    """落一条 open 待办，返回服务响应（含 id）。服务异常向上抛，由调用方捕获。"""
    ocr = [{"text": it.text, "center": list(it.center)} for it in items]
    body = {"round": round_no, "depth": depth, "fingerprint": fp,
            "ocr": ocr, "shot": shot_path, "note": note}
    return _req("POST", "/pending", body, timeout=5)


def poll_done(cid, on_wait=None):
    """挂起等待标注。

    :param on_wait: 每次心跳间隔回调（用于保活 WDA session）。
    :return: annotation dict；超时或服务断连返回 None，交调用方降级。
    """
    deadline = time.time() + WAIT_TIMEOUT
    last_hb = 0.0
    while time.time() < deadline:
        try:
            obj = _req("GET", f"/pending/{cid}", timeout=5)
            if obj.get("status") == "done":
                return obj.get("annotation")
        except Exception:
            return None  # 服务断连，交调用方降级
        now = time.time()
        if on_wait and now - last_hb >= HEARTBEAT:
            try:
                on_wait()
            except Exception:
                pass
            last_hb = now
        time.sleep(POLL_GAP)
    return None


def replay(annotation, quick):
    """按标注动作序列回放。返回是否执行过动作。"""
    if not annotation:
        return False
    for act in annotation.get("actions", []):
        a = act.get("action")
        if a == "tap":
            quick.tap2(act["x"], act["y"])
        elif a == "back":
            quick.go_back()
        elif a == "home":
            quick.press_home()
        elif a == "keys":
            quick.keys(act.get("text", ""))
        elif a == "swipe":
            quick.swipe2(act.get("x1"), act.get("y1"), act.get("x2"), act.get("y2"))
        time.sleep(0.25)
    return True
