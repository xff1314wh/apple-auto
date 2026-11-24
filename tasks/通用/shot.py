"""截图看当前画面：把手机屏幕存成 PNG，用来量点击坐标。

用法：python tasks/通用/shot.py [输出路径]
默认存 data/shots/now.png
截出来是 828x1792 像素，像素除以 2 就是点击坐标。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "core"))
import quick

out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "data", "shots", "now.png")
os.makedirs(os.path.dirname(out), exist_ok=True)
print(quick.shot(out))
print("saved ->", out)
