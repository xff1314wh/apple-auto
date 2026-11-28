# -*- coding: utf-8 -*-
"""人在环介入信号服务（unknown 兜底交接板）。

任务脚本撞到无法识别的页面时，POST /pending 落一条待办并挂起；
Agent（或人工）GET /queue 拉取、观察真机处置后，POST /cases/<id>
回写标注；脚本轮询到 done 后按标注动作回放，样本同时入台账供同类回放。

仅用 Python 标准库，零第三方依赖。启动：
    python core/handoff_server.py            # 127.0.0.1:8210

端点：
    GET  /health          探活
    POST /pending         落待办，body: {round, depth, fingerprint, ocr, shot, note}
    GET  /queue           列所有 open 待办（脚本之外的介入方拉取）
    GET  /pending/<id>    单条详情（脚本轮询 done）
    POST /cases/<id>      回写标注，body: {type, actions, note}；同时入台账
    GET  /cases           台账全量：指纹 → {type, actions, hits, updated_at}
"""
import json
import os
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data", "handoff")
PENDING_DIR = os.path.join(DATA, "pending")
CASES_FILE = os.path.join(DATA, "cases.json")
HOST = "127.0.0.1"
PORT = 8210

os.makedirs(PENDING_DIR, exist_ok=True)

_lock = threading.Lock()


def _load_cases():
    try:
        with open(CASES_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_cases(cases):
    tmp = CASES_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cases, f, ensure_ascii=False, indent=2)
    os.replace(tmp, CASES_FILE)


def _pending_path(cid):
    cid = os.path.basename(str(cid))  # 防路径穿越
    return os.path.join(PENDING_DIR, cid + ".json")


def _read_pending(cid):
    p = _pending_path(cid)
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def _write_pending(obj):
    p = _pending_path(obj["id"])
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, p)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # 静默，不刷控制台

    def _send(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0:
            return {}
        raw = self.rfile.read(n)
        try:
            return json.loads(raw.decode("utf-8", "replace"))
        except Exception:
            return {}

    def do_GET(self):
        if self.path == "/health":
            return self._send(200, {"ok": True, "ts": time.time()})
        if self.path == "/queue":
            items = []
            with _lock:
                for fn in os.listdir(PENDING_DIR):
                    if not fn.endswith(".json"):
                        continue
                    try:
                        with open(os.path.join(PENDING_DIR, fn), encoding="utf-8") as f:
                            obj = json.load(f)
                    except Exception:
                        continue
                    if obj.get("status") == "open":
                        items.append(obj)
            items.sort(key=lambda o: o.get("ts", 0))
            return self._send(200, {"queue": items})
        if self.path == "/cases":
            with _lock:
                cases = _load_cases()
            return self._send(200, {"cases": cases})
        if self.path.startswith("/pending/"):
            cid = self.path[len("/pending/"):]
            with _lock:
                obj = _read_pending(cid)
            if obj is None:
                return self._send(404, {"error": "not found"})
            return self._send(200, obj)
        self._send(404, {"error": "no route"})

    def do_POST(self):
        if self.path == "/pending":
            body = self._read_body()
            cid = time.strftime("%Y%m%d%H%M%S") + "-" + uuid.uuid4().hex[:6]
            obj = {
                "id": cid,
                "ts": time.time(),
                "round": body.get("round"),
                "depth": body.get("depth"),
                "fingerprint": body.get("fingerprint", ""),
                "ocr": body.get("ocr", []),
                "shot": body.get("shot", ""),
                "note": body.get("note", ""),
                "status": "open",
                "annotation": None,
            }
            with _lock:
                _write_pending(obj)
            return self._send(200, {"id": cid, "status": "open"})
        if self.path.startswith("/cases/"):
            cid = self.path[len("/cases/"):]
            body = self._read_body()
            annotation = {
                "type": body.get("type", "unknown"),
                "actions": body.get("actions", []),
                "note": body.get("note", ""),
            }
            with _lock:
                obj = _read_pending(cid)
                if obj is None:
                    return self._send(404, {"error": "pending not found"})
                obj["status"] = "done"
                obj["annotation"] = annotation
                _write_pending(obj)
                fp = obj.get("fingerprint") or ""
                if fp:
                    cases = _load_cases()
                    prev = cases.get(fp)
                    cases[fp] = {
                        "type": annotation["type"],
                        "actions": annotation["actions"],
                        "note": annotation["note"],
                        "hits": (prev or {}).get("hits", 0) + 1,
                        "updated_at": time.time(),
                    }
                    _save_cases(cases)
            return self._send(200, {"id": cid, "status": "done"})
        self._send(404, {"error": "no route"})


def main():
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"handoff 服务已启动 http://{HOST}:{PORT}  目录 {DATA}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("handoff 服务停止")


if __name__ == "__main__":
    main()
