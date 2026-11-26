"""WDA HTTP 控制台。用法: python quick.py <action> [args]"""
import base64
import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8100"
UDID = "00008030-001E44DE02D0802E"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
SID_FILE = os.path.join(DATA, "wda_sid.txt")

os.makedirs(os.path.join(DATA, "shots"), exist_ok=True)


def _session_dead(resp):
    if not isinstance(resp, dict):
        return False
    s = json.dumps(resp, ensure_ascii=False)
    return ("invalid session id" in s) or ("Session does not exist" in s)


def _send_once(method, path, payload, timeout):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    if data:
        req.add_header("Content-Type", "application/json")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=timeout) as r:
            body = r.read().decode("utf-8", "replace")
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        return {"error": f"HTTP {e.code}: {body[:400]}"}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def _req(method, path, payload=None, timeout=40, _retried=False):
    resp = _send_once(method, path, payload, timeout)
    if _retried or not _session_dead(resp):
        return resp
    old_sid = session()
    nr = start()
    if not nr.get("session"):
        return resp
    time.sleep(0.4)  # 新建 session 需要一点时间才在 WDA 注册好，否则紧接着的请求仍 404
    path = path.replace("/session/" + old_sid, "/session/" + nr["session"])
    return _req(method, path, payload, timeout, _retried=True)


def session():
    return open(SID_FILE, encoding="utf-8-sig").read().strip()


def start(bundle=""):
    payload = {"capabilities": {"alwaysMatch": ({"bundleId": bundle} if bundle else {})}}
    r = _req("POST", "/session", payload)
    sid = None
    if isinstance(r.get("value"), dict):
        sid = r["value"].get("sessionId")
    sid = sid or r.get("sessionId")
    if not sid:
        return {"error": r}
    open(SID_FILE, "w", encoding="utf-8").write(sid)
    return {"session": sid}


def ensure_session():
    """任务开始前统一入口：校验当前会话是否还活着，失效才重建。

    判活顺序：读本地 sid → 探活一次；有效就保留，无 sid / 失效 / 连不上则重建。
    重连判断只收敛在连接层，GUI、渲染模板、手工脚本都调这里，不各自实现。

    返回 {"session": sid, "reconnected": bool, "error": None | 详情}。
    """
    sid = ""
    try:
        sid = session()
    except Exception:
        sid = ""

    if sid:
        probe = _send_once("GET", f"/session/{sid}/screenshot", None, 10)
        alive = not (isinstance(probe, dict)
                     and (probe.get("error") or _session_dead(probe)))
        if alive:
            return {"session": sid, "reconnected": False, "error": None}

    nr = start("")
    if nr.get("session"):
        return {"session": nr["session"], "reconnected": True, "error": None}
    return {"session": sid, "reconnected": False, "error": nr}


def go_back(y=448):
    """返回上一页：左边缘分步右滑（单跳不行，必须分几步走，实测微信有效）"""
    acts = [{"type": "pointer", "id": "finger1",
             "parameters": {"pointerType": "touch"},
             "actions": [
                 {"type": "pointerMove", "duration": 0, "x": 1, "y": int(y)},
                 {"type": "pointerDown", "button": 0},
                 {"type": "pointerMove", "duration": 20, "x": 40, "y": int(y)},
                 {"type": "pointerMove", "duration": 40, "x": 120, "y": int(y)},
                 {"type": "pointerMove", "duration": 60, "x": 260, "y": int(y)},
                 {"type": "pointerUp", "button": 0}]}]
    return _actions(acts)


def press_home():
    """按 Home 键回主屏（/wda/pressButton 实测可用）"""
    return _req("POST", "/session/" + session() + "/wda/pressButton", {"name": "home"})


def tap(x, y):
    return _req("POST", f"/session/{session()}/wda/tap/0", {"x": int(x), "y": int(y)})


def keys(text):
    return _req("POST", f"/session/{session()}/wda/keys", {"value": [text]})


def shot(path):
    r = _req("GET", f"/session/{session()}/screenshot", timeout=20)
    b64 = (r.get("value") or "")
    if isinstance(b64, dict):
        b64 = b64.get("data", "")
    if not b64:
        return {"error": r}
    open(path, "wb").write(base64.b64decode(b64))
    return {"saved": path}


def shot_bytes():
    """取一张截图原始字节，不落盘（镜像窗口用）。"""
    r = _req("GET", f"/session/{session()}/screenshot", timeout=20)
    b64 = (r.get("value") or "")
    if isinstance(b64, dict):
        b64 = b64.get("data", "")
    if not b64:
        return None, r
    return base64.b64decode(b64), None


def source(path):
    r = _req("GET", f"/session/{session()}/source", timeout=25)
    open(path, "w", encoding="utf-8").write(r.get("value", ""))
    return {"saved": path, "len": len(r.get("value", ""))}


def tap2(x, y):
    acts = {"actions": [{"type": "pointer", "id": "finger1",
                         "parameters": {"pointerType": "touch"},
                         "actions": [
                             {"type": "pointerMove", "duration": 0, "x": int(x), "y": int(y)},
                             {"type": "pointerDown", "button": 0},
                             {"type": "pause", "duration": 80},
                             {"type": "pointerUp", "button": 0}]}]}
    return _req("POST", f"/session/{session()}/actions", acts)


def swipe(x1, y1, x2, y2, dur=0.3):
    return _req("POST", f"/session/{session()}/wda/dragfromtoforduration",
                {"fromX": x1, "fromY": y1, "toX": x2, "toY": y2, "duration": dur})


def _actions(acts, timeout=40):
    return _req("POST", f"/session/{session()}/actions", {"actions": acts}, timeout=timeout)


def long_press(x, y, hold_ms=1200):
    """按住 hold_ms 毫秒再松手。"""
    acts = [{"type": "pointer", "id": "finger1",
             "parameters": {"pointerType": "touch"},
             "actions": [{"type": "pointerMove", "duration": 0, "x": int(x), "y": int(y)},
                         {"type": "pointerDown", "button": 0},
                         {"type": "pause", "duration": int(hold_ms)},
                         {"type": "pointerUp", "button": 0}]}]
    return _actions(acts)


def swipe2(x1, y1, x2, y2, dur_ms=300):
    """从 (x1,y1) 滑到 (x2,y2)，全程 dur_ms 毫秒。"""
    acts = [{"type": "pointer", "id": "finger1",
             "parameters": {"pointerType": "touch"},
             "actions": [{"type": "pointerMove", "duration": 0, "x": int(x1), "y": int(y1)},
                         {"type": "pointerDown", "button": 0},
                         {"type": "pointerMove", "duration": int(dur_ms), "x": int(x2), "y": int(y2)},
                         {"type": "pointerUp", "button": 0}]}]
    return _actions(acts)


def find_el(using, value):
    """按 using(label/name/accessibility id) 找元素，返回 element id"""
    r = _req("POST", f"/session/{session()}/element",
             {"using": using, "value": value}, timeout=20)
    v = r.get("value") or {}
    if isinstance(v, dict):
        for k in ("ELEMENT", "element-6066-11e4-a52e-4f7302a00e33", "elementId"):
            if v.get(k):
                return {"eid": v[k]}
    return {"error": r}


def click(eid):
    return _req("POST", f"/session/{session()}/element/{eid}/click", {"id": eid}, timeout=20)


def findclick(using, value):
    e = find_el(using, value)
    if e.get("error"):
        return e
    return click(e["eid"])


def _dvt(*args, timeout=30):
    """经 pymobiledevice3 DVT 设备层下发命令（依赖本地 tunneld 常驻）。

    返回 {ok, out, err}；隧道不通或设备锁屏时 ok=False。
    """
    cmd = [sys.executable, "-m", "pymobiledevice3", "developer", "dvt",
           *args, "--udid", UDID]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           encoding="utf-8", errors="replace")
        return {"ok": p.returncode == 0,
                "out": (p.stdout or "").strip(),
                "err": (p.stderr or "").strip()[-160:]}
    except Exception as e:
        return {"ok": False, "err": f"{type(e).__name__}: {e}"}


def activate_app(bundle):
    """拉起/激活 App。

    WDA 未注册 App 启停 HTTP 路由（appium / wda 端点全 404），
    可用通道是：用 bundleId 新建一个 WDA session，由 WDA 附着并拉起该 App。
    """
    r = start(bundle)
    return r if r.get("session") else {"error": r}


def terminate_app(bundle):
    """杀后台进程（DVT pkill，按 bundleId 匹配，需 tunneld 在跑）。

    成功返回 {ok: True}；隧道不通时返回 {ok: False, error}，调用方据此降级。
    """
    r = _dvt("pkill", bundle, "--bundle")
    if r["ok"]:
        return {"ok": True}
    return {"ok": False, "error": (r.get("err") or "pkill 失败") + "（需管理员 tunneld 隧道在运行）"}


WDA_BUNDLE = "com.facebook.WebDriverAgentRunner"


def _proclist():
    r = _dvt("proclist", timeout=30)
    if not r["ok"]:
        return None, (r.get("err") or "proclist 失败") + "（需管理员 tunneld 隧道在运行）"
    try:
        return json.loads(r["out"]), None
    except Exception as e:
        return None, f"解析进程列表失败：{e}"


def clear_background(exclude=None):
    """清空后台用户 App，保留 WDA 与系统进程。

    只杀 bundleId 属于已装用户 App 列表的运行中进程；WDA runner 显式排除。
    :param exclude: 额外排除的 bundle 列表。
    :return: {ok, killed:[{name,bundle,pid}], skipped:[...], errors:[...]}
    """
    exclude = set(exclude or [])
    exclude.add(WDA_BUNDLE)

    procs, err = _proclist()
    if procs is None:
        return {"ok": False, "error": err, "killed": [], "skipped": [], "errors": [err]}

    try:
        from apps import list_installed_apps
        user_apps, e2 = list_installed_apps()
    except Exception as e:
        return {"ok": False, "error": f"列举用户 App 失败：{e}", "killed": [], "skipped": [], "errors": [str(e)]}
    if e2:
        return {"ok": False, "error": f"列举用户 App 失败：{e2}", "killed": [], "skipped": [], "errors": [e2]}
    user_bundles = {a["bundle"] for a in user_apps}

    killed, skipped, errors = [], [], []
    for proc in procs:
        bundle = proc.get("bundleIdentifier") or ""
        if not bundle:
            continue
        if bundle in exclude or "WebDriverAgent" in bundle:
            skipped.append({"name": proc.get("name"), "bundle": bundle, "pid": proc.get("pid")})
            continue
        if bundle not in user_bundles:
            continue  # 系统/自带 App 不动
        pid = proc.get("pid")
        if not pid:
            continue
        r = _dvt("kill", str(pid), timeout=15)
        if r["ok"]:
            killed.append({"name": proc.get("name"), "bundle": bundle, "pid": pid})
        else:
            errors.append(f"{bundle}(pid {pid}): {r.get('err')}")

    return {"ok": not errors, "killed": killed, "skipped": skipped, "errors": errors}


def open_app(bundle, clean=False):
    """打开 App。

    clean=True：先 DVT pkill 清掉后台进程，再由 WDA session 重新附着拉起，
    实现冷启动；杀进程失败不阻断，降级为直接激活。
    clean=False：直接激活（后台拉前台，不在则启动）。
    """
    killed = None
    if clean:
        killed = terminate_app(bundle)
        if killed.get("ok"):
            time.sleep(0.6)
    r = activate_app(bundle)
    if killed and not killed.get("ok"):
        r["kill_fallback"] = killed
    return r


def is_locked():
    """设备是否处于锁屏状态。查不到时返回 None。"""
    sid = session()
    r = _req("GET", f"/session/{sid}/wda/locked")
    v = r.get("value")
    return v if isinstance(v, bool) else None


def wake():
    """唤醒屏幕（亮到密码页）。仅点亮，不能代替输入密码解锁。"""
    return _req("POST", f"/session/{session()}/wda/unlock", {}, timeout=8)


# 密码盘数字键中心（逻辑坐标 414x896，iPhone 11 标定；换机型需重新标定）
_PIN_KEYS = {
    "1": (90, 375), "2": (207, 375), "3": (324, 375),
    "4": (90, 482), "5": (207, 482), "6": (324, 482),
    "7": (90, 590), "8": (207, 590), "9": (324, 590),
    "0": (207, 697),
}


def unlock(pin, interval=0.35, settle=2.0):
    """唤醒并输入密码解锁（库内工具，主流程不自动调用）。

    :param pin: 数字锁屏密码字符串，由调用方传入；本模块不持久化、不写死密码。
    :param interval: 按键间隔秒。
    :param settle: 输完等待解锁完成的秒数。
    :return: {"unlocked": bool, "already": bool, "locked_after": bool|None}
    """
    if is_locked() is False:
        return {"unlocked": True, "already": True, "locked_after": False}

    # wake 端点会阻塞等待解锁，丢到线程里跑，主线程继续等密码页出现
    threading.Thread(target=wake, daemon=True).start()
    time.sleep(1.3)

    pin = str(pin)
    for ch in pin:
        if ch not in _PIN_KEYS:
            return {"unlocked": False, "already": False, "error": f"非法密码位: {ch}"}
        tap2(*_PIN_KEYS[ch])
        time.sleep(interval)

    time.sleep(settle)
    locked = is_locked()
    return {"unlocked": locked is False, "already": False, "locked_after": locked}


if __name__ == "__main__":
    act = sys.argv[1]
    if act == "start":
        print(start(sys.argv[2] if len(sys.argv) > 2 else ""))
    elif act == "tap":
        print(tap(sys.argv[2], sys.argv[3]))
    elif act == "source":
        print(source(sys.argv[2]))
    elif act == "tap2":
        print(tap2(sys.argv[2], sys.argv[3]))
    elif act == "keys":
        print(keys(sys.argv[2]))
    elif act == "shot":
        print(shot(sys.argv[2]))
    elif act == "swipe":
        print(swipe(*[float(v) for v in sys.argv[2:6]]))
    elif act == "find":
        print(find_el(sys.argv[2], sys.argv[3]))
    elif act == "findclick":
        print(findclick(sys.argv[2], sys.argv[3]))
