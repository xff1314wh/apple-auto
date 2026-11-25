# -*- coding: utf-8 -*-
"""素材打标 / 素材库（通用工具）

流程：
  1. 导入一段录屏视频（mp4/mov）按设定帧率抽帧；也可以点「录制操作」连 WDA 高速抓帧，
     你操作手机，停止后自动把帧序列编码成 mp4 存到素材目录。
  2. 在打标界面里逐步标注：点击 / 长按 / 滑动 / 返回 / 主屏 / 手势返回 / 键入文字 / 等待。
     坐标直接在画面上点取；每一步可填参数（长按、时长、次数、间隔）、键入文字和备注。
  3. 素材存成 data\\materials\\<名字>.json，帧图存 data\\materials\\frames\\<stem>\\。
  4. 按打标结果生成两份产物：<名字>.py（可直接运行的自动化代码，每步带注释）和
     <名字>.md（帧图对照 + 参数 + 备注 + 代码片段，作为 AI 参考素材）。
     界面里也能直接连手机测试回放。

运行：python tasks/通用/material.py
"""
import base64
import io
import json
import os
import re
import sys
import threading
import time
import traceback
import tkinter as tk
from datetime import datetime
from tkinter import filedialog, messagebox, ttk

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
CORE = os.path.join(ROOT, "core")
sys.path.insert(0, CORE)
sys.path.insert(0, HERE)

import quick  # noqa: E402
try:
    import fasttap  # noqa: E402
except Exception:
    fasttap = None

DATA = os.path.join(ROOT, "data")
MAT_DIR = os.path.join(DATA, "materials")
FRAME_DIR = os.path.join(MAT_DIR, "frames")
os.makedirs(FRAME_DIR, exist_ok=True)

VIEW_W = 300
PHONE_W, PHONE_H = 414, 896  # 默认机型（iPhone 11 / XR）
DEVICE_DEFAULT = "iPhone 11 / XR · 414×896"
DEVICES = {
    "iPhone SE / 8 · 375×667": (375, 667),
    "iPhone X / 11Pro · 375×812": (375, 812),
    "iPhone 11 / XR · 414×896": (414, 896),
    "iPhone 12/13/14Pro · 390×844": (390, 844),
    "iPhone 12/13/14ProMax · 428×926": (428, 926),
    "iPhone 15/15Pro · 393×852": (393, 852),
    "iPhone 15ProMax · 430×932": (430, 932),
}

TYPES = ["tap", "hold", "swipe", "back", "backswipe", "home", "text", "wait"]
TYPE_LABEL = {
    "tap": "点击", "hold": "长按", "swipe": "滑动", "back": "返回(点箭头)",
    "backswipe": "手势返回", "home": "主屏", "text": "键入文字", "wait": "等待",
}
# 各类型需要的字段，用于表单显隐
NEEDS = {
    "tap": ["x", "y", "rounds", "tap_gap"],
    "hold": ["x", "y", "ms"],
    "swipe": ["x1", "y1", "x2", "y2", "ms"],
    "back": ["x", "y"],
    "backswipe": [],
    "home": [],
    "text": ["text"],
    "wait": ["ms"],
}

BAD_NAME = re.compile(r'[\\/:*?"<>|\s]+')


def clean_name(name):
    name = BAD_NAME.sub("_", (name or "").strip()).strip("_")
    return name or "未命名素材"


def new_step(t="tap", frame=0):
    return {
        "type": t, "frame": frame,
        "x": 0, "y": 0, "x1": 0, "y1": 0, "x2": 0, "y2": 0,
        "ms": 700, "rounds": 1, "tap_gap": 0, "gap": 0,
        "text": "", "note": "",
    }


def new_issue():
    return {
        "name": "问题界面",
        "frame": -1,
        "points": [],          # [{x, y, label}] 命中后处置点击点
        "keywords": [],        # OCR 匹配关键词，任一命中即判别为该界面
        "region": None,        # 触发文字范围 [x1,y1,x2,y2] points，None=全屏
        "max_rounds": 3,
        "note": "",
    }


def empty_mat(name="未命名素材"):
    return {
        "name": name,
        "stem": clean_name(name),
        "source": "",
        "video": "",
        "fps": 2.0,
        "created": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "description": "",
        "device": DEVICE_DEFAULT,
        "frames": [],
        "steps": [],
        "issues": [],
    }


class MaterialApp:
    def __init__(self, root):
        self.root = root
        self.mat = empty_mat()
        self.cache = {}            # 帧序号 -> PIL Image
        self.frames_dir = FRAME_DIR
        self.selected_frame = -1
        self.selected_step = -1
        self.selected_issue = -1
        self.play_busy = False
        self.grabbing = False
        self.grab_stop = threading.Event()
        self.grab_times = []
        self.drag_start = None
        self.phone_w, self.phone_h = PHONE_W, PHONE_H
        self.view_h = round(VIEW_W * self.phone_h / self.phone_w)

        root.title("素材打标 / 素材库")
        root.geometry("1260x800")
        root.minsize(1100, 680)

        self._build()
        self._refresh_frames()
        self._refresh_steps()
        self._refresh_issues()
        self.root.protocol("WM_DELETE_WINDOW", self._close)

    # ---------------- UI ----------------
    def _build(self):
        bar = ttk.Frame(self.root, padding=(10, 8))
        bar.pack(fill="x")
        ttk.Label(bar, text="素材名：").pack(side="left")
        self.name_var = tk.StringVar(value=self.mat["name"])
        ttk.Entry(bar, textvariable=self.name_var, width=18).pack(side="left", padx=(0, 8))
        ttk.Label(bar, text="机型").pack(side="left", padx=(8, 0))
        self.device_var = tk.StringVar(value=self.mat.get("device") or DEVICE_DEFAULT)
        ttk.Combobox(bar, textvariable=self.device_var, width=22, state="readonly",
                     values=list(DEVICES.keys())).pack(side="left", padx=4)
        self.device_var.trace_add("write", lambda *a: self._on_device_change())
        ttk.Button(bar, text="导入视频抽帧", command=self._import_video).pack(side="left", padx=4)
        self.btn_grab = ttk.Button(bar, text="录制操作：开始", command=self._grab_toggle)
        self.btn_grab.pack(side="left", padx=4)
        ttk.Button(bar, text="打开素材", command=self._open_mat).pack(side="left", padx=4)
        ttk.Button(bar, text="保存素材", command=self._save_mat).pack(side="left", padx=4)
        ttk.Button(bar, text="生成代码", command=self._export_code).pack(side="left", padx=4)
        self.btn_play = ttk.Button(bar, text="测试回放", command=self._test_play)
        self.btn_play.pack(side="left", padx=4)

        desc_frame = ttk.LabelFrame(self.root, text="任务总描述（写给 AI：这个流程要做什么、目标、注意事项）",
                                    padding=(10, 6))
        desc_frame.pack(fill="x", padx=10, pady=(6, 0))
        self.desc_box = tk.Text(desc_frame, height=3, font=("Microsoft YaHei UI", 9), wrap="word")
        self.desc_box.pack(fill="x")

        body = ttk.Frame(self.root, padding=(10, 4))
        body.pack(fill="both", expand=True)

        # 左栏：帧列表
        left = ttk.Frame(body, width=190)
        left.pack(side="left", fill="y")
        left.pack_propagate(False)
        ttk.Label(left, text="帧时间轴", font=("Microsoft YaHei UI", 10, "bold")).pack(anchor="w")
        row = ttk.Frame(left)
        row.pack(fill="x", pady=(4, 4))
        ttk.Label(row, text="帧率").pack(side="left")
        self.fps_var = tk.StringVar(value="2")
        ttk.Combobox(row, textvariable=self.fps_var, width=5,
                     values=["1", "2", "5", "10"]).pack(side="left", padx=4)
        self.frame_lb = tk.Listbox(left, width=26, height=26, activestyle="none",
                                    font=("Consolas", 9), exportselection=False)
        self.frame_lb.pack(fill="both", expand=True)
        self.frame_lb.bind("<<ListboxSelect>>", self._on_frame_pick)

        # 中间：画面
        mid = ttk.Frame(body)
        mid.pack(side="left", padx=(10, 0), fill="y")
        self.canvas = tk.Canvas(mid, width=VIEW_W, height=self.view_h, bg="#111",
                                highlightthickness=1, highlightbackground="#666")
        self.canvas.pack()
        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_motion)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        ttk.Label(mid, text="直接在画面上点一下就取到坐标（没选步骤会自动新建一步）；滑动按下拖到终点",
                  foreground="#666").pack(pady=(4, 0))

        # 右栏：主流程 / 异常分支 两个页签
        right = ttk.Frame(body, padding=(12, 0, 0, 0))
        right.pack(side="left", fill="both", expand=True)
        self.nb = ttk.Notebook(right)
        self.nb.pack(fill="both", expand=True)

        tab_main = ttk.Frame(self.nb)
        self.nb.add(tab_main, text="主流程步骤")

        top = ttk.Frame(tab_main)
        top.pack(fill="x")
        ttk.Label(top, text="步骤列表", font=("Microsoft YaHei UI", 10, "bold")).pack(side="left")
        ttk.Button(top, text="新增", command=self._add_step).pack(side="right", padx=2)
        ttk.Button(top, text="复制", command=self._dup_step).pack(side="right", padx=2)
        ttk.Button(top, text="删除", command=self._del_step).pack(side="right", padx=2)
        ttk.Button(top, text="上移", command=lambda: self._move_step(-1)).pack(side="right", padx=2)
        ttk.Button(top, text="下移", command=lambda: self._move_step(1)).pack(side="right", padx=2)

        self.step_lb = tk.Listbox(tab_main, height=9, activestyle="none",
                                  font=("Consolas", 9), exportselection=False)
        self.step_lb.pack(fill="x", pady=(4, 6))
        self.step_lb.bind("<<ListboxSelect>>", self._on_step_pick)

        form = ttk.LabelFrame(tab_main, text="当前步骤参数", padding=(10, 8))
        form.pack(fill="both", expand=True)

        self.var = {}
        self.entries = {}
        specs = [
            ("type", "操作类型", "combo", list(TYPE_LABEL.values())),
            ("x", "X（画面点取）", "entry", None), ("y", "Y（画面点取）", "entry", None),
            ("x1", "起点 X", "entry", None), ("y1", "起点 Y", "entry", None),
            ("x2", "终点 X", "entry", None), ("y2", "终点 Y", "entry", None),
            ("ms", "长按/滑动/等待 毫秒", "entry", None),
            ("rounds", "点击次数", "entry", None),
            ("tap_gap", "段内两下间隔 ms", "entry", None),
            ("gap", "与上一步间隔 ms", "entry", None),
            ("text", "键入文字", "entry", None),
            ("note", "备注（指导文字）", "entry", None),
        ]
        for r, (key, label, kind, vals) in enumerate(specs):
            ttk.Label(form, text=label).grid(row=r, column=0, sticky="w", pady=3)
            if kind == "combo":
                v = tk.StringVar()
                c = ttk.Combobox(form, textvariable=v, width=22, state="readonly", values=vals)
                c.grid(row=r, column=1, sticky="w", padx=10)
                c.bind("<<ComboboxSelected>>", lambda e: self._apply_form())
                self.var[key], self.entries[key] = v, c
            else:
                v = tk.StringVar()
                e = ttk.Entry(form, textvariable=v, width=24)
                e.grid(row=r, column=1, sticky="w", padx=10)
                e.bind("<FocusOut>", lambda e: self._apply_form())
                self.var[key], self.entries[key] = v, e
        ttk.Button(form, text="应用到当前步骤", command=self._apply_form).grid(
            row=len(specs), column=0, columnspan=2, pady=(8, 0))

        # 异常分支页签
        tab_issue = ttk.Frame(self.nb)
        self.tab_issue = tab_issue
        self.nb.add(tab_issue, text="异常分支（问题界面）")
        self.nb.bind("<<NotebookTabChanged>>", self._on_tab_change)
        self._pane = "main"
        itop = ttk.Frame(tab_issue)
        itop.pack(fill="x")
        ttk.Label(itop, text="问题界面列表", font=("Microsoft YaHei UI", 10, "bold")).pack(side="left")
        ttk.Button(itop, text="删除问题", command=self._del_issue).pack(side="right", padx=2)
        ttk.Button(itop, text="新增问题", command=self._add_issue).pack(side="right", padx=2)

        self.issue_lb = tk.Listbox(tab_issue, height=6, activestyle="none",
                                   font=("Consolas", 9), exportselection=False)
        self.issue_lb.pack(fill="x", pady=(4, 6))
        self.issue_lb.bind("<<ListboxSelect>>", self._on_issue_pick)

        iform = ttk.LabelFrame(tab_issue, text="当前问题界面（参考当前选中帧）", padding=(10, 8))
        iform.pack(fill="both", expand=True)
        ttk.Label(iform, text="名称").grid(row=0, column=0, sticky="w", pady=3)
        self.iss_name = tk.StringVar()
        self.iss_name.trace_add("write", lambda *a: self._apply_issue_form())
        ttk.Entry(iform, textvariable=self.iss_name, width=24).grid(row=0, column=1, sticky="w", padx=10)

        ttk.Label(iform, text="最多处理轮次").grid(row=1, column=0, sticky="w", pady=3)
        self.iss_rounds = tk.StringVar(value="3")
        self.iss_rounds.trace_add("write", lambda *a: self._apply_issue_form())
        ttk.Entry(iform, textvariable=self.iss_rounds, width=24).grid(row=1, column=1, sticky="w", padx=10)

        ttk.Label(iform, text="触发关键词").grid(row=2, column=0, sticky="w", pady=3)
        self.iss_keywords = tk.StringVar()
        self.iss_keywords.trace_add("write", lambda *a: self._apply_issue_form())
        ttk.Entry(iform, textvariable=self.iss_keywords, width=24).grid(row=2, column=1, sticky="w", padx=10)

        ttk.Label(iform, text="说明（给 AI）").grid(row=3, column=0, sticky="nw", pady=3)
        self.iss_note = tk.Text(iform, width=24, height=3, wrap="word")
        self.iss_note.grid(row=3, column=1, sticky="w", padx=10)
        self.iss_note.bind("<FocusOut>", lambda e: self._apply_issue_form())

        ttk.Label(iform, text="点击像素（画布点取）").grid(row=4, column=0, sticky="nw", pady=3)
        ipoint = ttk.Frame(iform)
        ipoint.grid(row=4, column=1, sticky="w", padx=10)
        self.point_lb = tk.Listbox(ipoint, width=22, height=4, activestyle="none",
                                    font=("Consolas", 9), exportselection=False)
        self.point_lb.pack(side="left")
        pbtns = ttk.Frame(ipoint)
        pbtns.pack(side="left", padx=6)
        ttk.Button(pbtns, text="删除选中点", command=self._del_issue_point).pack()

        # 底部：生成的自动化代码 + 日志
        bottom = ttk.Frame(self.root, padding=(10, 0, 10, 8))
        bottom.pack(fill="both", expand=True)
        ttk.Label(bottom, text="生成的自动化代码（可直接运行，也作为 AI 参考素材）：").pack(anchor="w")
        self.code_box = tk.Text(bottom, height=7, font=("Consolas", 9), wrap="word")
        self.code_box.pack(fill="x", pady=(2, 4))
        log_row = ttk.Frame(bottom)
        log_row.pack(fill="both", expand=True)
        sb = ttk.Scrollbar(log_row)
        sb.pack(side="right", fill="y")
        self.log_box = tk.Text(log_row, height=6, font=("Consolas", 9), wrap="word",
                               yscrollcommand=sb.set)
        self.log_box.pack(side="left", fill="both", expand=True)
        sb.configure(command=self.log_box.yview)

    # ---------------- 素材 / 帧 ----------------
    def _mat_file(self):
        return os.path.join(MAT_DIR, self.mat["stem"] + ".json")

    def _frame_path(self, fn):
        return os.path.join(FRAME_DIR, self.mat["stem"], fn)

    def _import_video(self):
        path = filedialog.askopenfilename(
            title="选择录屏视频",
            filetypes=[("视频", "*.mp4 *.mov *.m4v *.webm"), ("所有文件", "*.*")])
        if not path:
            return
        try:
            fps_target = float(self.fps_var.get())
        except Exception:
            fps_target = 2.0
        name = clean_name(os.path.splitext(os.path.basename(path))[0])
        fdir = os.path.join(FRAME_DIR, name)
        os.makedirs(fdir, exist_ok=True)

        self._log("正在抽帧：" + os.path.basename(path))
        try:
            import av
            container = av.open(path)
            stream = container.streams.video[0]
            src_fps = float(stream.average_rate or 25)
            every = max(1, round(src_fps / fps_target))
            frames, saved, idx = [], 0, 0
            for frame in container.decode(video=0):
                if idx % every == 0:
                    im = frame.to_image().convert("RGB")
                    fn = f"f{saved:04d}.png"
                    im.save(os.path.join(fdir, fn))
                    frames.append(fn)
                    saved += 1
                idx += 1
            container.close()
        except Exception as e:
            messagebox.showerror("抽帧失败", f"{type(e).__name__}: {e}")
            return

        self.mat = empty_mat(name)
        self.mat["source"] = os.path.basename(path)
        self.mat["fps"] = fps_target
        self.mat["frames"] = frames
        self.name_var.set(name)
        self.desc_box.delete("1.0", "end")
        self.cache.clear()
        self.selected_frame = 0 if frames else -1
        self.selected_step = -1
        self.selected_issue = -1
        self._refresh_frames()
        self._refresh_steps()
        self._refresh_issues()
        self._show_frame()
        self._log(f"抽帧完成：{len(frames)} 帧 -> {fdir}")

    def _grab_toggle(self):
        if self.grabbing:
            self.grab_stop.set()
            self.grabbing = False
            self.btn_grab.configure(text="录制操作：开始")
            threading.Thread(target=self._encode_video, daemon=True).start()
            return
        if not os.path.isfile(os.path.join(DATA, "wda_sid.txt")):
            messagebox.showinfo("未连接", "先在主界面「检查手机连接」。")
            return
        stem = clean_name(self.name_var.get() or "live")
        if self.mat.get("source") != "live" or not self.mat["frames"]:
            self.mat = empty_mat(stem)
            self.mat["source"] = "live"
            self.desc_box.delete("1.0", "end")
            self.selected_issue = -1
            self._refresh_issues()
        self.name_var.set(self.mat["name"])
        self.grab_times = []
        self.cache.clear()
        self.grabbing = True
        self.grab_stop.clear()
        self.btn_grab.configure(text="录制操作：停止")
        self._log("开始录制手机操作，操作完点「停止」。")
        threading.Thread(target=self._grab_loop, daemon=True).start()

    def _grab_loop(self):
        fdir = os.path.join(FRAME_DIR, self.mat["stem"])
        os.makedirs(fdir, exist_ok=True)
        n = len(self.mat["frames"])
        while self.grabbing and not self.grab_stop.is_set():
            raw, err = quick.shot_bytes()
            if raw:
                fn = f"f{n:04d}.png"
                with open(os.path.join(fdir, fn), "wb") as f:
                    f.write(raw)
                self.mat["frames"].append(fn)
                self.grab_times.append(time.time())
                n += 1
                self.root.after(0, self._refresh_frames)
            else:
                self.root.after(0, lambda: self._log("抓帧失败：" + str(err)[:60]))
            self.grab_stop.wait(0.25)
        if len(self.grab_times) >= 2 and self.grab_times[-1] > self.grab_times[0]:
            self.mat["fps"] = max(1.0, min(15.0,
                                          (len(self.grab_times) - 1) /
                                          (self.grab_times[-1] - self.grab_times[0])))
        self.root.after(0, self._refresh_frames)

    def _encode_video(self):
        """把刚抓的帧序列封装成 mp4；编码失败不影响打标（帧还在）。"""
        frames = self.mat.get("frames", [])
        if len(frames) < 2:
            self._log("帧数不足，不生成视频，帧可直接用于打标。")
            return
        try:
            import av
            from PIL import Image
            mp4 = os.path.join(MAT_DIR, self.mat["stem"] + ".mp4")
            im0 = Image.open(self._frame_path(frames[0])).convert("RGB")
            w, h = im0.size
            w -= w % 2
            h -= h % 2
            fps = max(1.0, min(15.0, self.mat.get("fps", 4.0)))
            container = av.open(mp4, "w")
            stream = container.add_stream("h264", rate=fps)
            stream.width, stream.height = w, h
            stream.pix_fmt = "yuv420p"
            try:
                stream.options = {"crf": "23"}
            except Exception:
                pass
            for fn in frames:
                im = Image.open(self._frame_path(fn)).convert("RGB").resize((w, h))
                frame = av.VideoFrame.from_image(im)
                for packet in stream.encode(frame):
                    container.mux(packet)
            for packet in stream.encode():
                container.mux(packet)
            container.close()
            self.mat["video"] = os.path.basename(mp4)
            self._log(f"视频已生成：{mp4}（{len(frames)} 帧，{fps:.1f} fps）")
        except Exception as e:
            self._log(f"视频编码失败（不影响打标）：{type(e).__name__}: {e}")

    def _open_mat(self):
        path = filedialog.askopenfilename(
            title="打开素材", initialdir=MAT_DIR,
            filetypes=[("素材 JSON", "*.json"), ("所有文件", "*.*")])
        if not path:
            return
        try:
            with open(path, encoding="utf-8") as f:
                self.mat = json.load(f)
        except Exception as e:
            messagebox.showerror("打开失败", str(e))
            return
        self.mat.setdefault("description", "")
        self.mat.setdefault("issues", [])
        if self.mat.get("device") not in DEVICES:
            self.mat["device"] = DEVICE_DEFAULT
        self.device_var.set(self.mat["device"])
        self._apply_device()
        for _iss in self.mat["issues"]:
            _iss.setdefault("keywords", [])
            _iss.setdefault("region", None)
        self.name_var.set(self.mat.get("name", ""))
        self.desc_box.delete("1.0", "end")
        self.desc_box.insert("end", self.mat["description"])
        self.cache.clear()
        self.selected_frame = 0 if self.mat["frames"] else -1
        self.selected_step = -1
        self.selected_issue = -1
        self._refresh_frames()
        self._refresh_steps()
        self._refresh_issues()
        self._show_frame()
        self._log("已载入素材：" + path)

    def _prune_unused_frames(self):
        """只保留被步骤引用的帧图，其余从磁盘和帧列表里删掉；重映射帧序号。"""
        frames = self.mat["frames"]
        refs = [int(st.get("frame", -1)) for st in self.mat["steps"]]
        refs += [int(iss.get("frame", -1)) for iss in self.mat["issues"]]
        used_old = sorted({r for r in refs if 0 <= r < len(frames)})
        keep_files = [frames[i] for i in used_old]
        remap = {old: new for new, old in enumerate(used_old)}
        for st in self.mat["steps"]:
            old = int(st.get("frame", -1))
            st["frame"] = remap.get(old, -1)
        for iss in self.mat["issues"]:
            old = int(iss.get("frame", -1))
            iss["frame"] = remap.get(old, -1)
        fdir = os.path.join(FRAME_DIR, self.mat["stem"])
        used_set = set(keep_files)
        if os.path.isdir(fdir):
            for fn in os.listdir(fdir):
                if fn not in used_set:
                    try:
                        os.remove(os.path.join(fdir, fn))
                    except OSError:
                        pass
        self.mat["frames"] = keep_files
        self.cache.clear()
        if self.selected_frame >= len(keep_files):
            self.selected_frame = len(keep_files) - 1

    def _save_mat(self):
        name = clean_name(self.name_var.get())
        self.mat["name"] = name
        self.mat["description"] = self.desc_box.get("1.0", "end").strip()
        self.mat["device"] = self.device_var.get()
        if not self.mat.get("stem"):
            self.mat["stem"] = name
        self._prune_unused_frames()
        os.makedirs(MAT_DIR, exist_ok=True)
        path = self._mat_file()
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.mat, f, ensure_ascii=False, indent=2)
        self._refresh_frames()
        self._log("已保存：" + path)
        messagebox.showinfo("已保存", path)

    def _refresh_frames(self):
        self.frame_lb.delete(0, "end")
        fps = self.mat.get("fps", 2.0) or 2.0
        for i, fn in enumerate(self.mat["frames"]):
            t = i / fps
            self.frame_lb.insert("end", f"f{i:03d}  {t:5.1f}s  {fn}")
        if 0 <= self.selected_frame < len(self.mat["frames"]):
            self.frame_lb.selection_clear(0, "end")
            self.frame_lb.selection_set(self.selected_frame)

    def _on_frame_pick(self, _e):
        sel = self.frame_lb.curselection()
        if not sel:
            return
        self.selected_frame = sel[0]
        self._show_frame()

    def _load_pil(self, i):
        if i in self.cache:
            return self.cache[i]
        from PIL import Image
        fn = self.mat["frames"][i]
        im = Image.open(self._frame_path(fn)).convert("RGB")
        im = im.resize((VIEW_W, self.view_h), Image.BILINEAR)
        self.cache[i] = im
        return im

    def _show_frame(self):
        self.canvas.delete("all")
        if 0 <= self.selected_frame < len(self.mat["frames"]):
            try:
                from PIL import ImageTk
                im = self._load_pil(self.selected_frame)
                self.photo = ImageTk.PhotoImage(im)
                self.canvas.create_image(0, 0, anchor="nw", image=self.photo)
            except Exception as e:
                self.canvas.create_text(VIEW_W // 2, VIEW_H // 2,
                                         text="帧读取失败", fill="#fff")
                self._log(f"帧 {self.selected_frame}：{e}")
        else:
            self.canvas.create_text(VIEW_W // 2, VIEW_H // 2,
                                     text="（还没有帧：导入视频或开始抓帧）", fill="#888")
        self._draw_marks()

    # ---------------- 步骤 ----------------
    def _refresh_steps(self):
        self.step_lb.delete(0, "end")
        for i, st in enumerate(self.mat["steps"]):
            self.step_lb.insert("end", f"{i + 1:>2}. {self._summary(st)}")
        if 0 <= self.selected_step < len(self.mat["steps"]):
            self.step_lb.selection_clear(0, "end")
            self.step_lb.selection_set(self.selected_step)

    @staticmethod
    def _summary(st):
        t = st["type"]
        if t == "tap":
            return f"点击 ({st['x']},{st['y']}) ×{st['rounds']}  {st.get('note','')}"
        if t == "hold":
            return f"长按 ({st['x']},{st['y']}) {st['ms']}ms  {st.get('note','')}"
        if t == "swipe":
            return f"滑动 ({st['x1']},{st['y1']})→({st['x2']},{st['y2']}) {st['ms']}ms"
        if t == "back":
            return f"返回 ({st['x']},{st['y']})  {st.get('note','')}"
        if t == "backswipe":
            return "手势返回"
        if t == "home":
            return "主屏"
        if t == "text":
            return f'键入 "{st["text"]}"  {st.get("note","")}'
        if t == "wait":
            return f"等待 {st['ms']}ms  {st.get('note','')}"
        return t

    def _on_step_pick(self, _e):
        sel = self.step_lb.curselection()
        if not sel:
            return
        self.selected_step = sel[0]
        st = self.mat["steps"][self.selected_step]
        fr = int(st.get("frame", -1))
        if 0 <= fr < len(self.mat["frames"]):
            self.selected_frame = fr
            self._show_frame()
        self._step_to_form()
        self._draw_marks()

    def _add_step(self):
        st = new_step("tap", self.selected_frame)
        self.mat["steps"].append(st)
        self.selected_step = len(self.mat["steps"]) - 1
        self._refresh_steps()
        self._step_to_form()
        self._draw_marks()

    def _dup_step(self):
        if self.selected_step < 0:
            return
        import copy
        st = copy.deepcopy(self.mat["steps"][self.selected_step])
        self.mat["steps"].insert(self.selected_step + 1, st)
        self.selected_step += 1
        self._refresh_steps()
        self._step_to_form()

    def _del_step(self):
        if self.selected_step < 0:
            return
        del self.mat["steps"][self.selected_step]
        self.selected_step = max(-1, self.selected_step - 1)
        self._refresh_steps()
        self._step_to_form()
        self._draw_marks()

    def _move_step(self, d):
        i = self.selected_step
        if i < 0:
            return
        j = i + d
        if not (0 <= j < len(self.mat["steps"])):
            return
        self.mat["steps"][i], self.mat["steps"][j] = self.mat["steps"][j], self.mat["steps"][i]
        self.selected_step = j
        self._refresh_steps()
        self._draw_marks()

    def _step_to_form(self):
        st = (self.mat["steps"][self.selected_step]
              if 0 <= self.selected_step < len(self.mat["steps"]) else None)
        values = {
            "type": TYPE_LABEL.get(st["type"], "tap") if st else TYPE_LABEL["tap"],
            "x": st["x"] if st else "", "y": st["y"] if st else "",
            "x1": st["x1"] if st else "", "y1": st["y1"] if st else "",
            "x2": st["x2"] if st else "", "y2": st["y2"] if st else "",
            "ms": st["ms"] if st else "", "rounds": st["rounds"] if st else "",
            "tap_gap": st["tap_gap"] if st else "", "gap": st["gap"] if st else "",
            "text": st["text"] if st else "", "note": st["note"] if st else "",
        }
        for k, v in values.items():
            self.var[k].set(str(v))
        self._refresh_form_visibility()

    def _refresh_form_visibility(self):
        label_to_type = {v: k for k, v in TYPE_LABEL.items()}
        t = label_to_type.get(self.var["type"].get(), "tap")
        need = NEEDS.get(t, [])
        for key, ent in self.entries.items():
            if key == "type":
                continue
            state = "normal" if key in need else "disabled"
            ent.configure(state=state)

    def _apply_form(self):
        if self.selected_step < 0:
            return
        label_to_type = {v: k for k, v in TYPE_LABEL.items()}
        st = self.mat["steps"][self.selected_step]
        st["type"] = label_to_type.get(self.var["type"].get(), st["type"])

        def num(key, default=0):
            try:
                return int(float(self.var[key].get()))
            except Exception:
                return default

        st["x"], st["y"] = num("x"), num("y")
        st["x1"], st["y1"] = num("x1"), num("y1")
        st["x2"], st["y2"] = num("x2"), num("y2")
        st["ms"] = num("ms", st["ms"])
        st["rounds"] = num("rounds", 1)
        st["tap_gap"] = num("tap_gap")
        st["gap"] = num("gap")
        st["text"] = self.var["text"].get()
        st["note"] = self.var["note"].get()
        self._refresh_form_visibility()
        self._refresh_steps()
        self._draw_marks()

    # ---------------- 异常分支（问题界面） ----------------
    def _on_tab_change(self, _e):
        self._pane = "issue" if self.nb.select() == str(self.tab_issue) else "main"

    def _refresh_issues(self):
        self.issue_lb.delete(0, "end")
        for i, iss in enumerate(self.mat["issues"]):
            self.issue_lb.insert("end", f"{i + 1}. {self._issue_summary(iss)}")
        if 0 <= self.selected_issue < len(self.mat["issues"]):
            self.issue_lb.selection_clear(0, "end")
            self.issue_lb.selection_set(self.selected_issue)
        self._refresh_points()

    @staticmethod
    def _issue_summary(iss):
        pts = len(iss.get("points", []))
        return f"{iss.get('name', '问题界面')} · {pts} 个点击点 · 上限 {iss.get('max_rounds', 3)} 轮"

    def _on_issue_pick(self, _e):
        sel = self.issue_lb.curselection()
        if not sel:
            return
        self.selected_issue = sel[0]
        iss = self.mat["issues"][self.selected_issue]
        fr = int(iss.get("frame", -1))
        if 0 <= fr < len(self.mat["frames"]):
            self.selected_frame = fr
            self._show_frame()
        self._issue_to_form()
        self._draw_marks()

    def _add_issue(self):
        iss = new_issue()
        iss["frame"] = self.selected_frame
        self.mat["issues"].append(iss)
        self.selected_issue = len(self.mat["issues"]) - 1
        self._refresh_issues()
        self._issue_to_form()
        self._draw_marks()

    def _del_issue(self):
        if self.selected_issue < 0:
            return
        del self.mat["issues"][self.selected_issue]
        self.selected_issue = max(-1, self.selected_issue - 1)
        self._refresh_issues()
        self._issue_to_form()
        self._draw_marks()

    def _issue_to_form(self):
        iss = (self.mat["issues"][self.selected_issue]
               if 0 <= self.selected_issue < len(self.mat["issues"]) else None)
        self.iss_name.set(iss.get("name", "") if iss else "")
        self.iss_rounds.set(str(iss.get("max_rounds", 3)) if iss else "3")
        self.iss_keywords.set("，".join(iss.get("keywords", [])) if iss else "")
        self.iss_note.delete("1.0", "end")
        if iss:
            self.iss_note.insert("end", iss.get("note", ""))
        self._refresh_points()

    def _apply_issue_form(self):
        if self.selected_issue < 0:
            return
        iss = self.mat["issues"][self.selected_issue]
        iss["name"] = self.iss_name.get().strip() or "问题界面"
        try:
            iss["max_rounds"] = max(1, int(float(self.iss_rounds.get())))
        except Exception:
            iss["max_rounds"] = 3
        raw_kw = self.iss_keywords.get().strip()
        iss["keywords"] = [k.strip() for k in raw_kw.replace("，", ",").split(",") if k.strip()]
        iss["note"] = self.iss_note.get("1.0", "end").strip()
        self._refresh_issues()

    def _refresh_points(self):
        self.point_lb.delete(0, "end")
        if self.selected_issue < 0:
            return
        iss = self.mat["issues"][self.selected_issue]
        for i, p in enumerate(iss.get("points", [])):
            label = p.get("label") or f"点 {i + 1}"
            self.point_lb.insert("end", f"{label} ({p['x']},{p['y']})")

    def _add_issue_point(self, x, y):
        if self.selected_issue < 0:
            return
        iss = self.mat["issues"][self.selected_issue]
        idx = len(iss.get("points", [])) + 1
        iss.setdefault("points", []).append({"x": x, "y": y, "label": f"点击点 {idx}"})
        self._refresh_points()
        self._refresh_issues()
        self._draw_marks()

    def _del_issue_point(self):
        if self.selected_issue < 0:
            return
        sel = self.point_lb.curselection()
        if not sel:
            return
        del self.mat["issues"][self.selected_issue]["points"][sel[0]]
        self._refresh_points()
        self._refresh_issues()
        self._draw_marks()

    # ---------------- 画布取坐标 ----------------
    def _apply_device(self):
        name = self.device_var.get()
        self.phone_w, self.phone_h = DEVICES.get(name, (PHONE_W, PHONE_H))
        self.view_h = round(VIEW_W * self.phone_h / self.phone_w)
        self.mat["device"] = name

    def _on_device_change(self):
        self._apply_device()
        if hasattr(self, "canvas"):
            self.canvas.config(height=self.view_h)
            self.cache.clear()
            self._show_frame()

    def _to_phone(self, cx, cy):
        x = int(cx / VIEW_W * self.phone_w)
        y = int(cy / self.view_h * self.phone_h)
        return max(0, min(self.phone_w - 1, x)), max(0, min(self.phone_h - 1, y))

    def _on_press(self, ev):
        if self._pane == "issue":
            return
        if self.selected_step < 0:
            return
        st = self.mat["steps"][self.selected_step]
        if st["type"] == "swipe":
            self.drag_start = (ev.x, ev.y)
            self.canvas.delete("preview")

    def _on_motion(self, ev):
        if self._pane == "issue":
            return
        if self.drag_start and self.selected_step >= 0:
            x0, y0 = self.drag_start
            self.canvas.delete("preview")
            self.canvas.create_line(x0, y0, ev.x, ev.y, fill="#ff3b30",
                                    width=2, tags="preview")

    def _on_release(self, ev):
        px, py = self._to_phone(ev.x, ev.y)
        if self._pane == "issue":
            if self.selected_issue < 0:
                self._add_issue()
            self._add_issue_point(px, py)
            return
        if self.selected_step < 0:
            # 没选步骤：直接在画面点一下就新建一步，坐标取这次点击，不用手填
            self._add_step()
        st = self.mat["steps"][self.selected_step]
        if st["type"] == "swipe":
            if self.drag_start:
                x0, y0 = self._to_phone(self.drag_start[0], self.drag_start[1])
                st["x1"], st["y1"] = x0, y0
                st["x2"], st["y2"] = px, py
                self.drag_start = None
        elif st["type"] in ("tap", "hold", "back"):
            st["x"], st["y"] = px, py
        self._step_to_form()
        self._refresh_steps()
        self._draw_marks()

    def _draw_marks(self):
        self.canvas.delete("mark")
        for i, st in enumerate(self.mat["steps"]):
            sel = i == self.selected_step
            width = 3 if sel else 1
            if st["type"] in ("tap", "hold", "back"):
                cx, cy = self._view_of(st["x"], st["y"])
                color = "#ff3b30" if st["type"] == "tap" else "#ff9500"
                r = 8 if sel else 5
                self.canvas.create_oval(cx - r, cy - r, cx + r, cy + r,
                                        outline=color, width=width, tags="mark")
            elif st["type"] == "swipe":
                a = self._view_of(st["x1"], st["y1"])
                b = self._view_of(st["x2"], st["y2"])
                self.canvas.create_line(*a, *b, fill="#0a7a52", width=width, tags="mark")
                self.canvas.create_oval(b[0] - 4, b[1] - 4, b[0] + 4, b[1] + 4,
                                        fill="#0a7a52", tags="mark")
            elif st["type"] == "text":
                self.canvas.create_text(VIEW_W - 12, 18 + i * 16, anchor="e",
                                        text=f"T{i+1}", fill="#af52de", tags="mark")
        for iss in self.mat["issues"]:
            if int(iss.get("frame", -1)) != self.selected_frame:
                continue
            for p in iss.get("points", []):
                cx, cy = self._view_of(p["x"], p["y"])
                self.canvas.create_oval(cx - 7, cy - 7, cx + 7, cy + 7,
                                        outline="#af52de", width=2, tags="mark")

    def _view_of(self, x, y):
        return int(x / self.phone_w * VIEW_W), int(y / self.phone_h * self.view_h)

    # ---------------- 导出 / 回放 ----------------
    def build_script(self):
        parts = []
        for st in self.mat["steps"]:
            gap = int(st.get("gap") or 0)
            if gap > 0:
                parts.append(f"wait:{gap}")
            t = st["type"]
            if t == "tap":
                parts.append(f"{st['x']},{st['y']},{int(st.get('tap_gap') or 0)},{st['rounds']}")
            elif t == "hold":
                parts.append(f"hold:{st['x']},{st['y']},{st['ms']}")
            elif t == "swipe":
                parts.append(f"swipe:{st['x1']},{st['y1']},{st['x2']},{st['y2']},{st['ms']}")
            elif t == "back":
                parts.append(f"back:{st['x']},{st['y']}")
            elif t == "backswipe":
                parts.append("backswipe:")
            elif t == "home":
                parts.append("home:")
            elif t == "wait":
                parts.append(f"wait:{st['ms']}")
            elif t == "text":
                txt = st["text"]
                if ";" in txt or "\n" in txt or ":" in txt:
                    parts.append("text64:" + base64.b64encode(txt.encode("utf-8")).decode())
                else:
                    parts.append("text:" + txt)
        return "; ".join(parts)

    # ---------------- 代码 / 素材文档生成 ----------------
    def _frame_ref(self, st):
        idx = st.get("frame", -1)
        if 0 <= idx < len(self.mat["frames"]):
            return self.mat["frames"][idx]
        return ""

    @staticmethod
    def _py_literal(s):
        return json.dumps(s, ensure_ascii=False)

    def _step_code(self, st, i):
        """返回 (注释行, 代码行列表)；代码行不含外层 run() 缩进，循环体自带缩进。"""
        t = st["type"]
        note = st.get("note", "")
        frame = self._frame_ref(st)
        tag = f"[{i + 1}] {TYPE_LABEL.get(t, t)}"
        if note:
            tag += f" · {note}"
        if frame:
            tag += f" · 参考帧 {frame}"
        comment = "# " + tag
        code = []
        gap = int(st.get("gap") or 0)
        if gap > 0:
            code.append(f"time.sleep({gap / 1000:.3f})  # 与上一步间隔 {gap}ms")
        if t == "tap":
            rounds = int(st.get("rounds") or 1)
            if rounds <= 1:
                code.append(f"quick.tap2({st['x']}, {st['y']})")
            else:
                code.append(f"for _ in range({rounds}):")
                code.append(f"    quick.tap2({st['x']}, {st['y']})")
                tg = int(st.get("tap_gap") or 0)
                if tg > 0:
                    code.append(f"    time.sleep({tg / 1000:.3f})")
        elif t == "hold":
            code.append(f"quick.long_press({st['x']}, {st['y']}, {st['ms']})")
        elif t == "swipe":
            code.append(f"quick.swipe2({st['x1']}, {st['y1']}, {st['x2']}, {st['y2']}, {st['ms']})")
        elif t == "back":
            code.append(f"quick.tap2({st['x']}, {st['y']})  # 返回箭头")
        elif t == "backswipe":
            code.append("quick.go_back()")
        elif t == "home":
            code.append("quick.press_home()")
        elif t == "text":
            code.append(f"quick.keys({self._py_literal(st['text'])})")
        elif t == "wait":
            code.append(f"time.sleep({st['ms'] / 1000:.3f})")
        return comment, code

    def _issue_frame_ref(self, iss):
        idx = iss.get("frame", -1)
        if 0 <= idx < len(self.mat["frames"]):
            return self.mat["frames"][idx]
        return ""

    def _md_step_line(self, st):
        t = st["type"]
        if t == "tap":
            return f"坐标 ({st['x']}, {st['y']}) · 点击次数 {st['rounds']}"
        if t == "hold":
            return f"坐标 ({st['x']}, {st['y']}) · 长按（按住时长由你判断）"
        if t == "swipe":
            return f"起点 ({st['x1']}, {st['y1']}) → 终点 ({st['x2']}, {st['y2']}) · 滑动（时长由你判断）"
        if t == "back":
            return f"返回箭头坐标 ({st['x']}, {st['y']})"
        if t == "backswipe":
            return "边缘手势返回"
        if t == "home":
            return "回到主屏"
        if t == "text":
            return f"键入文字：{st['text']}"
        if t == "wait":
            return "等待（时长由你判断）"
        return TYPE_LABEL.get(t, t)

    def generate_py(self):
        m = self.mat
        lines = [
            "# -*- coding: utf-8 -*-",
            f'"""自动化流程：{m["name"]}',
            f"素材来源：{m.get('source', '')}",
            f"生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"参考帧目录：frames/{m['stem']}/",
            "说明：由素材打标导出，可直接 python 运行；也作为 AI 生成自动化流程的参考素材。",
            '"""',
            "import os",
            "import sys",
            "import time",
            "",
            'HERE = os.path.dirname(os.path.abspath(__file__))',
            'ROOT = os.path.dirname(os.path.dirname(HERE))',
            'sys.path.insert(0, os.path.join(ROOT, "core"))',
            "",
            "import quick",
            "",
            "",
            "def run():",
        ]
        if not m["steps"]:
            lines.append("    pass")
        for i, st in enumerate(m["steps"]):
            comment, code = self._step_code(st, i)
            lines.append("    " + comment)
            for c in code:
                lines.append("    " + c)
        if m["issues"]:
            lines.append("")
            lines.append("    # ---- 异常分支（问题界面）：素材占位，处置逻辑由 AI 依据 .md 素材补全 ----")
            for iss in m["issues"]:
                lines.append(f"    # 【{iss.get('name', '问题界面')}】轮次上限 {iss.get('max_rounds', 3)}")
                frame = self._issue_frame_ref(iss)
                if frame:
                    lines.append(f"    #   参考帧 frames/{m['stem']}/{frame}")
                for p in iss.get("points", []):
                    lines.append(f"    #   {p.get('label', '点击点')} -> quick.tap2({p['x']}, {p['y']})")
                if iss.get("note"):
                    lines.append(f"    #   说明：{iss['note']}")
        lines += ["", "", 'if __name__ == "__main__":', "    run()", ""]
        return "\n".join(lines)

    def generate_md(self):
        m = self.mat
        desc = (m.get("description") or "").strip()
        L = [
            "# 自动化任务生成指令（Skill 素材包）",
            "",
            "## 0. 任务总描述",
            "",
            desc if desc else "（未填写）",
            "",
            "## 1. 角色与任务",
            "",
            "你是 iOS WebDriverAgent 自动化工程师。依据下面「主流程素材」和「异常分支素材」，"
            "生成一份可直接 `python` 运行的自动化脚本。只按素材给的坐标注入触摸，"
            "不要臆造素材里没出现的按钮位置。",
            "",
            "## 2. 环境与硬性规则",
            "",
            f"- 设备：{self.device_var.get()}，屏幕坐标 {self.phone_w} × {self.phone_h} points；素材坐标已是 points，直接使用，不要再换算。",
            "- 只能调用 `core/quick.py` 提供的接口，禁止读取控件树 / 按元素名查找：",
            "  - `quick.tap2(x, y)` 点击",
            "  - `quick.long_press(x, y, ms)` 长按",
            "  - `quick.swipe2(x1, y1, x2, y2, ms)` 滑动",
            "  - `quick.go_back()` 边缘手势返回",
            "  - `quick.press_home()` 回主屏",
            "  - `quick.keys(text)` 键入文字",
            "- 各步停顿、长按时长、滑动速度由你根据界面场景自行判断合理值，素材不强制固定间隔。",
            "- 异常分支：只有运行中真实出现该页面才进入处置，主流程里不要无条件执行；处置要带轮次上限，防止死循环。",
            "",
            "## 3. 主流程素材",
            "",
        ]
        if not m["steps"]:
            L.append("（主流程无标注步骤）")
            L.append("")
        for i, st in enumerate(m["steps"]):
            L.append(f"### 步骤 {i + 1} · {TYPE_LABEL.get(st['type'], st['type'])}")
            L.append("")
            frame = self._frame_ref(st)
            if frame:
                L.append(f"- 参考帧：![{frame}](frames/{m['stem']}/{frame})")
            L.append("- " + self._md_step_line(st))
            if st.get("note"):
                L.append(f"- 备注：{st['note']}")
            L.append("")
        L += ["## 4. 异常分支素材（问题界面）", ""]
        if not m["issues"]:
            L.append("（无异常分支）")
            L.append("")
        for j, iss in enumerate(m["issues"]):
            L.append(f"### 分支 {chr(65 + j)} · {iss.get('name', '问题界面')}")
            L.append("")
            frame = self._issue_frame_ref(iss)
            if frame:
                L.append(f"- 参考帧：![{frame}](frames/{m['stem']}/{frame})")
            pts = iss.get("points", [])
            if pts:
                L.append("- 点击像素：")
                for p in pts:
                    label = p.get("label") or "点击点"
                    L.append(f"  - {label} ({p['x']}, {p['y']})")
            if iss.get("keywords"):
                L.append("- 触发关键词：" + "、".join(iss["keywords"]))
            if iss.get("region"):
                L.append(f"- 触发范围：{iss['region']}")
            if iss.get("note"):
                L.append(f"- 处置说明：{iss['note']}")
            L.append(f"- 轮次上限：{iss.get('max_rounds', 3)}")
            L.append("")
        L += [
            "## 5. 输出要求",
            "",
            "- 结构：`import quick` → `def run():` 包裹全部步骤 → `if __name__ == \"__main__\": run()`",
            "- 每步加注释：步骤号 + 备注 + 参考帧文件名",
            "- 异常分支写成独立处理函数，命中才调用；处理完返回主流程",
            "- 不要硬编码截图路径，坐标直接使用素材数值",
            "",
        ]
        return "\n".join(L)

    def _export_code(self):
        if not self.mat["steps"] and not self.mat.get("issues"):
            messagebox.showinfo("空素材", "先标注主流程步骤或问题界面，再生成代码。")
            return
        try:
            self.mat["description"] = self.desc_box.get("1.0", "end").strip()
            self._prune_unused_frames()
            py = self.generate_py()
            md = self.generate_md()
            self.code_box.delete("1.0", "end")
            self.code_box.insert("end", py)
            os.makedirs(MAT_DIR, exist_ok=True)
            py_path = os.path.join(MAT_DIR, self.mat["stem"] + ".py")
            md_path = os.path.join(MAT_DIR, self.mat["stem"] + ".md")
            with open(py_path, "w", encoding="utf-8") as f:
                f.write(py)
            with open(md_path, "w", encoding="utf-8") as f:
                f.write(md)
            self._log("自动化代码：" + py_path)
            self._log("素材文档：" + md_path)
            messagebox.showinfo("已生成", f"代码：{py_path}\n素材文档：{md_path}")
        except Exception as e:
            self._log("生成失败：\n" + traceback.format_exc())
            messagebox.showerror("生成失败", f"{type(e).__name__}: {e}")

    def _test_play(self):
        if self.play_busy:
            return
        if fasttap is None:
            messagebox.showinfo("缺依赖", "fasttap 模块不可用，无法回放。")
            return
        script = self.build_script()
        if not script:
            messagebox.showinfo("空素材", "先标注步骤再回放。")
            return
        self.play_busy = True
        self.btn_play.configure(state="disabled")
        threading.Thread(target=self._run_play, args=(script,), daemon=True).start()

    def _run_play(self, script):
        try:
            steps = fasttap.parse_script(script)
            t = fasttap.Tapper()
            self._log(f"开始回放 {len(steps)} 步…")
            for st in steps:
                if st[0] == "wait":
                    self._log(f"等待 {st[1] * 1000:.0f}ms")
                    time.sleep(st[1])
                elif st[0] == "text":
                    self._log(f'键入 "{st[1]}"')
                    t.text(st[1])
                elif st[0] == "hold":
                    _, x, y, ms = st
                    self._log(f"长按 ({x},{y}) {ms}ms")
                    t.long(x, y, ms)
                elif st[0] == "back":
                    _, px, py = st
                    self._log(f"返回 ({px},{py})")
                    t.tap(px, py)
                elif st[0] == "backswipe":
                    self._log("返回（边缘手势）")
                    t.back()
                elif st[0] == "home":
                    self._log("回到主屏")
                    t.home()
                elif st[0] == "swipe":
                    _, x1, y1, x2, y2, ms = st
                    self._log(f"滑动 ({x1},{y1})→({x2},{y2}) {ms}ms")
                    t.swipe(x1, y1, x2, y2, ms)
                else:
                    _, x, y, d, r = st
                    self._log(f"点击 ({x},{y}) ×{r} 间隔 {d}ms")
                    fasttap.burst(t, x, y, d, r)
            self._log("回放结束。")
        except Exception as e:
            self._log(f"回放出错：{type(e).__name__}: {e}")
        finally:
            self.play_busy = False
            self.root.after(0, lambda: self.btn_play.configure(state="normal"))

    def _log(self, text):
        self.log_box.insert("end", text + "\n")
        self.log_box.see("end")

    def _close(self):
        self.grab_stop.set()
        self.grabbing = False
        try:
            self.root.destroy()
        except Exception:
            pass


if __name__ == "__main__":
    root = tk.Tk()
    MaterialApp(root)
    root.mainloop()
