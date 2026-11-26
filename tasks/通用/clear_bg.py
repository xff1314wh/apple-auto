# -*- coding: utf-8 -*-
"""清空后台用户 App（保留 WDA runner 与系统进程）。

走 DVT 设备层：列出运行中进程，只杀 bundleId 属于已装用户 App 的，
WDA runner 显式排除。依赖本地管理员 tunneld 隧道在运行。

用法：
  python clear_bg.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
CORE = os.path.join(ROOT, "core")
sys.path.insert(0, CORE)
import quick  # noqa: E402


def main():
    print(">>> 清空后台（保留 WDA）", flush=True)
    r = quick.clear_background()

    if r.get("error"):
        print("失败：" + r["error"], flush=True)
        print("FAILED", flush=True)
        sys.exit(1)

    killed = r.get("killed", [])
    print(f"已清理 {len(killed)} 个后台 App：", flush=True)
    for k in killed:
        print(f"  - {k['name']} ({k['bundle']})", flush=True)
    for e in r.get("errors", []):
        print("  ! " + e, flush=True)
    kept = r.get("skipped", [])
    if kept:
        print("保留：" + ", ".join(k["bundle"] for k in kept), flush=True)

    print("OK" if r.get("ok") else "FAILED", flush=True)
    sys.exit(0 if r.get("ok") else 1)


if __name__ == "__main__":
    main()
