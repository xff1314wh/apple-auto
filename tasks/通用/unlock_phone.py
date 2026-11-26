# -*- coding: utf-8 -*-
"""解锁手机：唤醒并输入密码。密码从环境变量 UNLOCK_PIN 读取，不写进脚本/日志。

用法（由 GUI 调用）：
  UNLOCK_PIN=密码 python unlock_phone.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
CORE = os.path.join(ROOT, "core")
sys.path.insert(0, CORE)
import quick  # noqa: E402


def main():
    pin = os.environ.get("UNLOCK_PIN", "").strip()
    if not pin:
        print("未提供锁屏密码", flush=True)
        print("FAILED", flush=True)
        sys.exit(1)

    r = quick.unlock(pin)
    if r.get("already"):
        print("设备已处于解锁状态", flush=True)
        print("OK", flush=True)
        sys.exit(0)
    if r.get("unlocked"):
        print("解锁成功", flush=True)
        print("OK", flush=True)
        sys.exit(0)
    print("解锁失败：屏幕仍处于锁定状态（密码错误或坐标偏差）", flush=True)
    print("FAILED", flush=True)
    sys.exit(1)


if __name__ == "__main__":
    main()
