#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
iosctl —— 从 Windows 直接接管 iPhone 的控制台。
Phase 1（已可用，无需签任何东西）：设备信息 / 截图 / 读界面元素 / App 列表 / 启动 App / 日志
Phase 2（需一次性签名安装 WebDriverAgent）：点击 / 滑动 / 输入 / 全局截图 / 元素树
所有输出尽量是 JSON，方便上层（AI）解析后再决定下一步动作。
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PY = sys.executable
PMD = [PY, "-m", "pymobiledevice3", "--no-color"]
WDA_URL = os.environ.get("IOSCTL_WDA_URL", "http://127.0.0.1:8100")
OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")
_SESSION = {"id": None}


# ---------------------------------------------------------------- 基础执行层
def pmd(args, timeout=300):
    """调用 pymobiledevice3 CLI，返回 (rc, stdout, stderr)。"""
    cp = subprocess.run(PMD + args, capture_output=True, text=True,
                        encoding="utf-8", errors="replace", timeout=timeout)
    return cp.returncode, (cp.stdout or "").strip(), (cp.stderr or "").strip()


def devices():
    """列出 usbmux 可见设备，返回 [{udid, name, ios, conn}] 形态。"""
    rc, out, err = pmd(["usbmux", "list"])
    try:
        raw = json.loads(out) if out.startswith("[") else []
    except Exception:
        raw = []
    items = []
    for d in raw:
        props = d.get("Properties", {}) if isinstance(d, dict) else {}
        udid = (d.get("Identifier") or d.get("UniqueDeviceID")
                or props.get("SerialNumber") or props.get("UDID"))
        items.append({
            "udid": udid,
            "name": props.get("DeviceName") or d.get("DeviceName"),
            "connection": props.get("ConnectionType"),
            "usb_product_id": props.get("ProductID"),
        })
    return items, err


def require_device():
    items, err = devices()
    if not items:
        print(json.dumps({
            "ok": False,
            "error": "NO_DEVICE",
            "message": "没有检测到 iPhone。请用数据线连接电脑，并在手机上点『信任』。"
                       + (f" (usbmuxd: {err})" if err else ""),
        }, ensure_ascii=False, indent=2))
        sys.exit(2)
    return items[0]["udid"]


def udid_flag(udid):
    return ["--udid", udid] if udid else []


def ensure_dir():
    os.makedirs(OUT_DIR, exist_ok=True)
    return OUT_DIR


def dump(obj):
    print(json.dumps(obj, ensure_ascii=False, indent=2))


# ---------------------------------------------------------------- Phase 1
def cmd_devices(args):
    items, err = devices()
    dump({"ok": bool(items), "count": len(items), "devices": items,
          **({"usbmux_error": err} if err and not items else {})})


def cmd_info(args):
    udid = require_device()
    rc, out, err = pmd(["developer", "dvt", "device-information"] + udid_flag(udid))
    if rc != 0 or not out:
        rc2, out2, err2 = pmd(["lockdown", "info"] + udid_flag(udid))
        dump({"ok": rc2 == 0, "raw": out2 if out2 else None,
              "error": err if err else err2})
        return
    try:
        data = json.loads(out)
    except Exception:
        data = {"raw": out}
    dump({"ok": True, "udid": udid, "device": data})


def cmd_shot(args):
    udid = require_device()
    path = args.out or os.path.join(ensure_dir(), "shot.png")
    rc, out, err = pmd(["developer", "dvt", "screenshot", os.path.abspath(path)]
                       + udid_flag(udid))
    if rc != 0 or not os.path.exists(path):
        dump({"ok": False, "error": err or out or "screenshot failed"})
        return
    dump({"ok": True, "path": os.path.abspath(path),
          "bytes": os.path.getsize(path)})


def cmd_ui(args):
    """前台 App 的可访问性元素列表 —— 这是让 AI『看见界面结构』的关键。"""
    udid = require_device()
    rc, out, err = pmd(["developer", "accessibility", "list-items"] + udid_flag(udid))
    if rc != 0:
        dump({"ok": False, "error": err or out})
        return
    try:
        data = json.loads(out)
    except Exception:
        data = {"raw": out}
    dump({"ok": True, "elements": data})


def cmd_elem(args):
    udid = require_device()
    cmd = ["developer", "accessibility", "element-attributes"]
    if args.focus is not None:
        cmd += ["--focus", str(args.focus)]
    rc, out, err = pmd(cmd + udid_flag(udid))
    dump({"ok": rc == 0, "data": _maybe_json(out), "error": err if rc else None})


def cmd_apps(args):
    udid = require_device()
    rc, out, err = pmd(["apps", "list"] + udid_flag(udid))
    if rc != 0:
        dump({"ok": False, "error": err or out})
        return
    data = _maybe_json(out)
    if args.filter and isinstance(data, (list, dict)):
        kw = args.filter.lower()
        if isinstance(data, list):
            data = [app for app in data
                    if kw in json.dumps(app, ensure_ascii=False).lower()]
    dump({"ok": True, "apps": data})


def cmd_launch(args):
    udid = require_device()
    rc, out, err = pmd(["developer", "dvt", "launch", args.bundle] + udid_flag(udid))
    dump({"ok": rc == 0, "pid": out or None, "error": err if rc else None})


def cmd_kill(args):
    udid = require_device()
    rc, out, err = pmd(["developer", "dvt", "pkill", args.bundle,
                        "--bundle"] + udid_flag(udid))
    dump({"ok": rc == 0, "output": out or None, "error": err if rc else None})


def cmd_log(args):
    udid = require_device()
    cp = subprocess.run(PMD + ["syslog"] + udid_flag(udid),
                        capture_output=True, text=True, encoding="utf-8",
                        errors="replace", timeout=args.seconds + 15)
    lines = (cp.stdout or "").splitlines()[-args.lines:]
    dump({"ok": True, "lines": lines})


def cmd_procs(args):
    udid = require_device()
    rc, out, err = pmd(["developer", "dvt", "proclist"] + udid_flag(udid))
    dump({"ok": rc == 0, "processes": _maybe_json(out), "error": err if rc else None})


def _maybe_json(text):
    try:
        return json.loads(text)
    except Exception:
        return text


# ---------------------------------------------------------------- Phase 2: WDA
def wda(method, path, payload=None, timeout=30):
    url = WDA_URL + path
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:600]
        return {"_error": f"HTTP {e.code}", "_body": body}
    except Exception as e:
        return {"_error": f"{type(e).__name__}: {e}"}


def wda_session():
    if _SESSION["id"]:
        return _SESSION["id"]
    st = wda("GET", "/status")
    if "_error" in st:
        return None
    r = wda("POST", "/session", {"capabilities": {"alwaysMatch": {}}})
    sid = (r.get("sessionId") or (r.get("value") or {}).get("sessionId"))
    if sid:
        _SESSION["id"] = sid
    return sid


def need_wda():
    sid = wda_session()
    if not sid:
        dump({"ok": False, "error": "WDA_NOT_RUNNING",
              "message": "WebDriverAgent 没在跑。先执行 `python iosctl.py wda up`，"
                         "若提示未安装，则需先签装 WDA（见 README）。"})
        sys.exit(3)
    return sid


def cmd_wda(args):
    action = args.action
    if action == "status":
        dump({"ok": True, "status": wda("GET", "/status")})
        return
    if action == "up":
        udid = require_device()
        # 1) 转发 8100 端口（等价 iproxy），2) 启动 WDA runner，3) 等 /status
        fwd = subprocess.Popen(PMD + ["usbmux", "forward", "8100", "8100"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(1.5)
        launched = []
        for bundle in ("com.facebook.WebDriverAgentRunner.xctrunner",
                       "com.appium.ios.WebDriverAgentRunner.xctrunner"):
            rc, out, err = pmd(["developer", "dvt", "launch", bundle] + udid_flag(udid))
            launched.append({"bundle": bundle, "ok": rc == 0, "pid": out or None,
                             "error": err if rc else None})
            if rc == 0:
                break
        ok = None
        for _ in range(20):
            st = wda("GET", "/status")
            if "_error" not in st:
                ok = st
                break
            time.sleep(1.5)
        dump({"ok": ok is not None, "forwarder_pid": fwd.pid,
              "launch": launched, "status": ok})
        return
    sid = need_wda()
    if action == "tap":
        dump({"ok": True, "result": wda("POST", f"/session/{sid}/wda/tap/0",
                                        {"x": args.x, "y": args.y})})
    elif action == "doubletap":
        dump({"ok": True, "result": wda("POST", f"/session/{sid}/wda/doubleTap",
                                        {"x": args.x, "y": args.y})})
    elif action == "swipe":
        dump({"ok": True, "result": wda("POST", f"/session/{sid}/wda/dragfromtoforDuration",
                                        {"fromX": args.x1, "fromY": args.y1,
                                         "toX": args.x2, "toY": args.y2,
                                         "duration": args.duration})})
    elif action == "type":
        dump({"ok": True, "result": wda("POST", f"/session/{sid}/wda/keys",
                                        {"value": [args.text]})})
    elif action == "home":
        dump({"ok": True, "result": wda("POST", f"/session/{sid}/wda/pressButton",
                                        {"name": "home"})})
    elif action == "source":
        dump({"ok": True, "source": wda("GET", f"/session/{sid}/source", timeout=60)})
    elif action == "shot":
        path = args.out or os.path.join(ensure_dir(), "wda.png")
        r = wda("GET", f"/session/{sid}/screenshot", timeout=60)
        b64 = r.get("value") or r.get("screenshot")
        if not b64:
            dump({"ok": False, "error": "no screenshot in response", "raw": r})
            return
        with open(path, "wb") as f:
            f.write(base64.b64decode(b64))
        dump({"ok": True, "path": os.path.abspath(path)})
    elif action == "openapp":
        dump({"ok": True, "result": wda("POST", f"/session/{sid}/wda/apps/launch",
                                        {"bundleId": args.bundle})})
    elif action == "find":
        r = wda("POST", f"/session/{sid}/element",
                {"using": args.by, "value": args.value})
        dump({"ok": "_error" not in r, "element": r})
    elif action == "click":
        r = wda("POST", f"/session/{sid}/element",
                {"using": args.by, "value": args.value})
        eid = ((r.get("value") or {}).get("ELEMENT") or r.get("ELEMENT"))
        if not eid:
            dump({"ok": False, "error": "element not found", "raw": r})
            return
        dump({"ok": True, "result": wda("POST", f"/session/{sid}/element/{eid}/click", {})})
    else:
        dump({"ok": False, "error": f"unknown action {action}"})


# ---------------------------------------------------------------- CLI
def build_parser():
    p = argparse.ArgumentParser(prog="iosctl", description="Windows -> iPhone 控制台")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("devices", help="列出已连接设备").set_defaults(fn=cmd_devices)
    sub.add_parser("info", help="设备信息").set_defaults(fn=cmd_info)

    s = sub.add_parser("shot", help="截图（Phase1）")
    s.add_argument("-o", "--out")
    s.set_defaults(fn=cmd_shot)

    sub.add_parser("ui", help="读前台界面元素（Phase1）").set_defaults(fn=cmd_ui)

    e = sub.add_parser("elem", help="读指定焦点元素属性")
    e.add_argument("--focus", type=int)
    e.set_defaults(fn=cmd_elem)

    a = sub.add_parser("apps", help="已安装 App 列表")
    a.add_argument("--filter")
    a.set_defaults(fn=cmd_apps)

    l = sub.add_parser("launch", help="启动 App（bundle id）")
    l.add_argument("bundle")
    l.set_defaults(fn=cmd_launch)

    k = sub.add_parser("kill", help="结束 App")
    k.add_argument("bundle")
    k.set_defaults(fn=cmd_kill)

    lg = sub.add_parser("log", help="抓系统日志")
    lg.add_argument("--seconds", type=int, default=5)
    lg.add_argument("--lines", type=int, default=200)
    lg.set_defaults(fn=cmd_log)

    sub.add_parser("procs", help="进程列表").set_defaults(fn=cmd_procs)

    w = sub.add_parser("wda", help="Phase2：点击/滑动/输入")
    w.add_argument("action", choices=["status", "up", "tap", "doubletap", "swipe",
                                      "type", "home", "source", "shot", "openapp",
                                      "find", "click"])
    w.add_argument("--x", type=int)
    w.add_argument("--y", type=int)
    w.add_argument("--x1", type=int)
    w.add_argument("--y1", type=int)
    w.add_argument("--x2", type=int)
    w.add_argument("--y2", type=int)
    w.add_argument("--duration", type=float, default=0.3)
    w.add_argument("--text")
    w.add_argument("--bundle")
    w.add_argument("--by", default="xpath")
    w.add_argument("--value")
    w.add_argument("-o", "--out")
    w.set_defaults(fn=cmd_wda)

    return p


def main():
    args = build_parser().parse_args()
    try:
        args.fn(args)
    except subprocess.TimeoutExpired:
        dump({"ok": False, "error": "TIMEOUT"})
        sys.exit(4)


if __name__ == "__main__":
    main()
