#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 Appium 的 WebDriverAgentRunner-Runner.zip 打包成可签名的 WebDriverAgent.ipa。

要点：
1. ipa 根目录必须是 Payload/WebDriverAgentRunner-Runner.app/...
2. 保留 Unix 可执行权限位（否则装到手机里起不来）
3. 剔除 *.xctest.dSYM —— Sideloadly 遇到过带它签名失败（社区报告）
"""
import os
import shutil
import sys
import zipfile

BASE = os.path.dirname(os.path.abspath(__file__))
WDA = os.path.join(BASE, "wda")
SRC = os.path.join(WDA, "WebDriverAgentRunner-Runner.zip")
WORK = os.path.join(WDA, "_build")
APP = "WebDriverAgentRunner-Runner.app"


def filenames():
    return ["WebDriverAgentRunner.ipa", "WebDriverAgent.ipa"]


def main():
    if not os.path.exists(SRC):
        sys.exit(f"缺少源包: {SRC}")

    if os.path.isdir(WORK):
        shutil.rmtree(WORK)
    os.makedirs(WORK)
    with zipfile.ZipFile(SRC) as z:
        z.extractall(WORK)

    # 剔除 dSYM
    removed = []
    for root, dirs, _ in os.walk(WORK, topdown=True):
        for d in list(dirs):
            if d.endswith(".dSYM"):
                removed.append(os.path.join(root, d))
                shutil.rmtree(os.path.join(root, d), ignore_errors=True)
                dirs.remove(d)

    payload = os.path.join(WORK, "Payload")
    os.makedirs(payload, exist_ok=True)
    src_app = os.path.join(WORK, APP)
    if os.path.isdir(src_app):
        shutil.move(src_app, os.path.join(payload, APP))
    else:
        sys.exit("源包结构不对：找不到 WebDriverAgentRunner-Runner.app")

    out = os.path.join(WDA, "WebDriverAgent.ipa")
    if os.path.exists(out):
        os.remove(out)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for root, dirs, files in os.walk(payload):
            for d in dirs:
                full = os.path.join(root, d)
                arc = os.path.relpath(full, WORK).replace("\\", "/") + "/"
                zi = zipfile.ZipInfo(arc)
                zi.external_attr = (0o40755 << 16) | 0x10
                z.writestr(zi, b"")
            for f in files:
                full = os.path.join(root, f)
                arc = os.path.relpath(full, WORK).replace("\\", "/")
                zi = zipfile.ZipInfo(arc)
                exec_bit = os.access(full, os.X_OK)
                zi.external_attr = ((0o100755 if exec_bit else 0o100644) << 16)
                zi.compress_type = zipfile.ZIP_DEFLATED
                with open(full, "rb") as fh:
                    z.writestr(zi, fh.read())

    # 校验根结构
    with zipfile.ZipFile(out) as z:
        names = z.namelist()
        root_ok = f"Payload/{APP}/Info.plist" in names
        xctest_ok = any(n.endswith("PlugIns/WebDriverAgentRunner.xctest/Info.plist")
                        for n in names)
    print(f"ipa: {out}  {os.path.getsize(out)} bytes")
    print(f"根结构正确: {root_ok}  嵌套xctest存在: {xctest_ok}")
    print(f"剔除 dSYM: {len(removed)}")
    if not (root_ok and xctest_ok):
        sys.exit("打包校验失败")


if __name__ == "__main__":
    main()
