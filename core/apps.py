# -*- coding: utf-8 -*-
"""已装 App 列举：走 USB 通道 pymobiledevice3，不依赖 WDA 8100 端口。

返回 [{"bundle": bundleId, "name": 显示名}]；显示名优先 CFBundleDisplayName
（中文），回退 CFBundleName，再回退 bundleId。结果进程内缓存一次，refresh 强制重扫。
"""
import json
import subprocess
import sys

UDID = "00008030-001E44DE02D0802E"
_PY = sys.executable
_CACHE = None


def list_installed_apps(udid=UDID, app_type="User", refresh=False):
    """列举真机用户 App。

    :return: (apps, err)；成功 err 为 None，失败 apps 为空列表。
    """
    global _CACHE
    if _CACHE is not None and not refresh:
        return list(_CACHE), None

    cmd = [_PY, "-m", "pymobiledevice3", "apps", "list",
           "--udid", udid, "--type", app_type, "--no-color"]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=40,
                           encoding="utf-8", errors="replace")
    except Exception as e:
        return [], f"{type(e).__name__}: {e}"

    out = (p.stdout or "").strip()
    if not out:
        return [], (p.stderr or "无输出").strip()[:200]
    try:
        data = json.loads(out)
    except Exception as e:
        return [], f"解析 App 列表失败：{e}"

    apps = []
    for bundle, info in data.items():
        if not isinstance(info, dict):
            continue
        bid = info.get("CFBundleIdentifier") or bundle
        name = (info.get("CFBundleDisplayName")
                or info.get("CFBundleName") or bid)
        apps.append({"bundle": str(bid), "name": str(name)})
    apps.sort(key=lambda a: a["name"].lower())
    _CACHE = apps
    return apps, None
