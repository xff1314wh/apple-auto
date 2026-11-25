"""动作录制 - 镜像点选

左边显示手机实时画面，你在电脑上对着它操作：
  1. 把这一下真实注入到手机
  2. 把动作记下来（单击 / 长按 / 滑动 / 键入文字）

手势识别：
  按下就松、几乎没动   → 单击
  按住超过 700ms 再松   → 长按
  按下后拖动超过 8 像素  → 滑动
  右侧输入框点「输入」    → 键入文字（手机焦点要已在输入框里）

画面管线（三段并行）：
  抓帧线程 ≫ 1 个解码缩放线程 ≫ 主线程按固定节拍贴图

抖动是怎么防的（关键）：
  多线程并发请求时，返回顺序是乱的 —— 后发的可能先回，先发的后回，
  直接把"最后到达的"当成最新帧就会出现画面退回去再前进，看着在抖。
  所以每个请求发出前打一个时间戳，只接受时间戳比上一次更大的帧，
  乱序到达的旧帧直接丢弃。显示节拍固定在 30 帧/秒，节奏均匀。

其他：
  · 抓帧只认真正的 PNG（校验文件头），坏响应丢掉
  · 提取 base64 用字节切片，不跑 json 解析
  · 缩放先 reduce(2) 再 BILINEAR，字清楚且快
  · 贴图复用同一个 canvas item 和 PhotoImage，每帧重新压层级（画面沉底、标记在上）
  · 每次新动作先清旧标记，画面不糊成一团
  · 任何环节只留最新一帧，旧帧立刻丢弃，不落盘 → 内存恒定几 MB

运行：python tasks/通用/mirror.py
"""
import base64
import http.client
import io
import os
import queue
import re
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
CORE = os.path.join(ROOT, "core")
sys.path.insert(0, CORE)
import quick  # noqa: E402
import ocr as ocr_mod  # noqa: E402
import task_registry  # noqa: E402
import flow_template  # noqa: E402

DATA = os.path.join(ROOT, "data")
RECORD_FILE = os.path.join(DATA, "record_script.txt")
TASK_DIR = os.path.join(ROOT, "tasks", "录制任务")
TASK_GROUP = "录制任务"

PHONE_W, PHONE_H = 414, 896
VIEW_W = 356
VIEW_H = round(VIEW_W * PHONE_H / PHONE_W)   # 770
DRAG_PX = 8
LONG_MS = 700
GRAB_THREADS = 8          # 并发数：太少慢，太多乱序多、手机端压力大
FRAME_MS = 20             # 显示节拍固定 50 帧/秒上限，节奏均匀

RE_DATA = re.compile(rb'"data"\s*:\s*"([^"]+)"')
RE_VALUE = re.compile(rb'"value"\s*:\s*"([^"]+)"')


def extract_b64(body):
    m = RE_DATA.search(body) or RE_VALUE.search(body)
    return m.group(1) if m else None


class RecorderApp:
    def __init__(self, root):
        self.root = root
        self.events = []
        self.issues = []
        self.selected_issue = -1
        self.mark_mode = False
        self.region_mode = False
        self.last_t = None
        self.press = None
        self.dragging = False

        self.raw = None
        self.raw_new = False
        self.raw_ts = 0.0        # 已被接受的最新帧的拍摄时间戳
        self.pil = None
        self.pil_new = False
        self.lock = threading.Lock()
        self.reconnect_lock = threading.Lock()

        self.sid = quick.session()
        self.stop_flag = False
        self.act_busy = threading.Event()
        self.grabbed = 0          # 抓帧计数（仅用于展示）
        self.used = 0             # 真正被采纳的帧数
        self.shown = 0
        self.fps_t = time.time()

        self.inject_q = queue.Queue(maxsize=32)
        self.photo = None
        self.item = None

        root.title("动作录制 — 镜像点选")
        root.geometry(f"{VIEW_W + 330}x{VIEW_H + 200}")
        root.minsize(VIEW_W + 290, 620)

        self._build()

        for _ in range(GRAB_THREADS):
            threading.Thread(target=self._grab_worker, daemon=True).start()
        threading.Thread(target=self._decode_worker, daemon=True).start()
        threading.Thread(target=self._inject_loop, daemon=True).start()
        self.root.after(FRAME_MS, self._tick)
        self.root.protocol("WM_DELETE_WINDOW", self._close)

    def _build(self):
        main = ttk.Frame(self.root, padding=8)
        main.pack(fill="both", expand=True)

        left = ttk.Frame(main)
        left.pack(side="left", fill="y")
        stage = ttk.Frame(left)
        stage.pack()

        keys = ttk.Frame(stage)
        keys.pack(side="left", fill="y", padx=(0, 6))
        ttk.Button(keys, text="◀\n返回", width=6, command=self._go_back).pack(fill="x", pady=(0, 6))
        ttk.Button(keys, text="←\n手势", width=6, command=self._go_swipe).pack(fill="x", pady=(0, 6))
        ttk.Button(keys, text="⌂\n主屏", width=6, command=self._go_home).pack(fill="x")
        ttk.Button(keys, text="✕\n关窗", width=6, command=self._close).pack(fill="x", pady=(6, 0))

        self.canvas = tk.Canvas(stage, width=VIEW_W, height=VIEW_H, bg="#111",
                                highlightthickness=1, highlightbackground="#666")
        self.canvas.pack(side="left")
        self.canvas.bind("<Button-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_motion)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        ttk.Label(left, text="单击 = 点 · 拖动 = 滑动 · 按住再松 = 长按 · 左边 ◀ 走返回手势",
                  foreground="#666").pack(pady=(4, 0))

        right = ttk.Frame(main, padding=(10, 0, 0, 0))
        right.pack(side="left", fill="both", expand=True)

        self.state = ttk.Label(right, text="画面：等待中", foreground="#888")
        self.state.pack(anchor="w")
        self.count = ttk.Label(right, text="已录 0 个动作", font=("Microsoft YaHei UI", 10, "bold"))
        self.count.pack(anchor="w", pady=(5, 6))

        self.inject_on = tk.BooleanVar(value=True)
        ttk.Checkbutton(right, text="操作时真实注入到手机", variable=self.inject_on).pack(anchor="w", pady=(0, 5))

        ttk.Label(right, text="键入文字（先点手机上的输入框）",
                  font=("Microsoft YaHei UI", 9, "bold")).pack(anchor="w", pady=(5, 2))
        row = ttk.Frame(right)
        row.pack(fill="x")
        self.text_in = ttk.Entry(row, width=18)
        self.text_in.pack(side="left", fill="x", expand=True)
        self.text_in.bind("<Return>", lambda e: self._send_text())
        ttk.Button(row, text="输入", width=6, command=self._send_text).pack(side="left", padx=(4, 0))

        ttk.Label(right, text="返回箭头坐标（左上角那个 < ）",
                  font=("Microsoft YaHei UI", 9, "bold")).pack(anchor="w", pady=(6, 2))
        rowb = ttk.Frame(right)
        rowb.pack(fill="x")
        self.back_x = ttk.Entry(rowb, width=5)
        self.back_x.insert(0, "34")
        self.back_x.pack(side="left")
        ttk.Label(rowb, text=" X   ").pack(side="left")
        self.back_y = ttk.Entry(rowb, width=5)
        self.back_y.insert(0, "70")
        self.back_y.pack(side="left")
        ttk.Label(rowb, text=" Y").pack(side="left")

        ttk.Separator(right).pack(fill="x", pady=8)

        # ---- 任务导出：生成可运行 py，并登记到 gui 任务列表 ----
        ttk.Label(right, text="任务导出", font=("Microsoft YaHei UI", 9, "bold")).pack(anchor="w", pady=(0, 2))
        row_n = ttk.Frame(right); row_n.pack(fill="x")
        ttk.Label(row_n, text="任务名", width=8).pack(side="left")
        self.task_name = ttk.Entry(row_n, width=14)
        self.task_name.pack(side="left", fill="x", expand=True)
        row_r = ttk.Frame(right); row_r.pack(fill="x", pady=(2, 0))
        ttk.Label(row_r, text="默认轮数", width=8).pack(side="left")
        self.task_rounds = ttk.Entry(row_r, width=6)
        self.task_rounds.insert(0, "10")
        self.task_rounds.pack(side="left")
        ttk.Label(row_r, text="(0=一直跑)", foreground="#888").pack(side="left", padx=4)

        ttk.Label(right, text="异常分支（问题界面）", font=("Microsoft YaHei UI", 9, "bold")).pack(anchor="w", pady=(6, 2))
        self.iss_lb = tk.Listbox(right, height=4, font=("Consolas", 9))
        self.iss_lb.pack(fill="x")
        self.iss_lb.bind("<<ListboxSelect>>", self._on_issue_pick)
        row_i = ttk.Frame(right); row_i.pack(fill="x", pady=(2, 0))
        ttk.Button(row_i, text="新增异常", width=7, command=self._add_issue).pack(side="left")
        ttk.Button(row_i, text="删除异常", width=7, command=self._del_issue).pack(side="left", padx=2)
        ttk.Button(row_i, text="识别文字", width=7, command=self._ocr_pick).pack(side="left")
        row_k = ttk.Frame(right); row_k.pack(fill="x", pady=(2, 0))
        ttk.Label(row_k, text="关键词", width=8).pack(side="left")
        self.iss_keywords = ttk.Entry(row_k, width=14)
        self.iss_keywords.pack(side="left", fill="x", expand=True)
        row_m = ttk.Frame(right); row_m.pack(fill="x", pady=(2, 0))
        ttk.Label(row_m, text="上限轮次", width=8).pack(side="left")
        self.iss_rounds = ttk.Entry(row_m, width=6)
        self.iss_rounds.insert(0, "3")
        self.iss_rounds.pack(side="left")

        row_mode = ttk.Frame(right); row_mode.pack(fill="x", pady=(3, 0))
        ttk.Label(row_mode, text="画布模式", width=8).pack(side="left")
        self.mode_var = tk.StringVar(value="record")
        ttk.Radiobutton(row_mode, text="录制", variable=self.mode_var, value="record",
                        command=self._sync_modes).pack(side="left")
        ttk.Radiobutton(row_mode, text="点处置点", variable=self.mode_var, value="mark",
                        command=self._sync_modes).pack(side="left", padx=2)
        ttk.Radiobutton(row_mode, text="框范围", variable=self.mode_var, value="region",
                        command=self._sync_modes).pack(side="left")
        self.mode_hint = ttk.Label(row_mode, text="", foreground="#0a7a52")
        self.mode_hint.pack(side="left", padx=6)
        self._sync_modes()

        row_a = ttk.Frame(right); row_a.pack(fill="x", pady=(2, 0))
        ttk.Label(row_a, text="处置动作", width=8).pack(side="left")
        self.iss_action = ttk.Combobox(row_a, width=10, state="readonly",
                                        values=["点击", "返回", "主屏", "等待"])
        self.iss_action.current(0)
        self.iss_action.pack(side="left")
        ttk.Label(row_a, text="等待毫秒", foreground="#888").pack(side="left", padx=(8, 2))
        self.iss_wait_ms = ttk.Entry(row_a, width=5)
        self.iss_wait_ms.insert(0, "800")
        self.iss_wait_ms.pack(side="left")

        row_r2 = ttk.Frame(right); row_r2.pack(fill="x", pady=(2, 0))
        ttk.Button(row_r2, text="清除范围", width=7, command=self._clear_region).pack(side="left")
        self.region_label = ttk.Label(row_r2, text="全屏匹配", foreground="#888")
        self.region_label.pack(side="left", padx=2)

        self.pt_lb = tk.Listbox(right, height=3, font=("Consolas", 9))
        self.pt_lb.pack(fill="x", pady=(3, 0))
        row_pt = ttk.Frame(right); row_pt.pack(fill="x")
        ttk.Button(row_pt, text="删除处置点", width=10, command=self._del_point).pack(side="left")

        ttk.Button(right, text="生成任务脚本 (py)", command=self._generate_task).pack(fill="x", pady=(6, 2))

        ttk.Button(right, text="生成脚本", command=self._make_script).pack(fill="x", pady=2)
        ttk.Button(right, text="清空录制", command=self._clear).pack(fill="x", pady=2)

        ttk.Label(right, text="动作列表（仅正常流程）", font=("Microsoft YaHei UI", 9, "bold")).pack(anchor="w", pady=(8, 2))
        row_ev = ttk.Frame(right); row_ev.pack(fill="x")
        self.lb = tk.Listbox(row_ev, height=9, font=("Consolas", 9))
        self.lb.pack(side="left", fill="both", expand=True)
        col_ev = ttk.Frame(row_ev); col_ev.pack(side="left", fill="y", padx=(4, 0))
        ttk.Button(col_ev, text="删除\n动作", width=6, command=self._del_event).pack(fill="x")

        ttk.Label(right, text="运行日志", font=("Microsoft YaHei UI", 9, "bold")).pack(anchor="w", pady=(4, 2))
        self.log_lb = tk.Listbox(right, height=3, font=("Consolas", 9))
        self.log_lb.pack(fill="x")

        bottom = ttk.Frame(self.root, padding=(8, 0, 8, 8))
        bottom.pack(fill="x")
        ttk.Label(bottom, text="脚本串（自动存到 data\\record_script.txt）：").pack(anchor="w")
        self.text = tk.Text(bottom, height=3, font=("Consolas", 9), wrap="word")
        self.text.pack(fill="x")

    # ---------- 第一段：抓帧（时间戳单调过滤） ----------
    def _grab_worker(self):
        conn = None
        while not self.stop_flag:
            if self.act_busy.is_set():      # 正在往手机注入动作，先别抢 WDA
                time.sleep(0.02)
                continue
            try:
                if conn is None:
                    conn = http.client.HTTPConnection("127.0.0.1", 8100, timeout=10)
                t_req = time.time()          # 这一帧的拍摄时刻
                conn.request("GET", f"/session/{self.sid}/screenshot")
                r = conn.getresponse()
                body = r.read()
                r.close()
                if b"invalid session id" in body or b"Session does not exist" in body:
                    self._reconnect()
                    time.sleep(0.1)
                    continue
                if r.status != 200 or len(body) < 1000:
                    time.sleep(0.05)
                    continue
                b64 = extract_b64(body)
                if not b64:
                    time.sleep(0.05)
                    continue
                raw = base64.b64decode(b64)
                del body, b64
                if not raw.startswith(b"\x89PNG") or len(raw) < 50000:
                    time.sleep(0.05)
                    continue
                with self.lock:
                    self.grabbed += 1
                    if t_req > self.raw_ts:      # 只认比上一帧更晚的，乱序旧帧丢掉
                        self.raw = raw
                        self.raw_ts = t_req
                        self.raw_new = True
                        self.used += 1
                    # 否则：这是迟到的一帧旧画面，直接丢，避免画面倒退
            except Exception:
                try:
                    if conn:
                        conn.close()
                except Exception:
                    pass
                conn = None
                self._reconnect()
                time.sleep(0.12)

    def _reconnect(self):
        """session 失效 / 连不上时，复用 core 的统一重连入口刷新 sid。"""
        with self.reconnect_lock:
            try:
                conn = quick.ensure_session()
                if conn.get("session"):
                    self.sid = conn["session"]
                if conn.get("error"):
                    self._log(f"重连 WDA：{str(conn['error'])[:80]}")
            except Exception as e:
                self._log(f"重连异常：{type(e).__name__}: {e}")

    # ---------- 第二段：解码缩放 ----------
    def _decode_worker(self):
        from PIL import Image
        while not self.stop_flag:
            raw = None
            with self.lock:
                if self.raw_new and self.raw:
                    raw = self.raw
                    self.raw = None
                    self.raw_new = False
            if raw is None:
                time.sleep(0.008)
                continue
            try:
                im = Image.open(io.BytesIO(raw)).convert("RGB")
                if im.width >= 600:
                    im = im.reduce(2)
                im = im.resize((VIEW_W, VIEW_H), Image.BILINEAR)
                with self.lock:
                    self.pil = im
                    self.pil_new = True
            except Exception:
                pass
            del raw

    # ---------- 第三段：贴图（固定节拍） ----------
    def _tick(self):
        im = None
        with self.lock:
            if self.pil_new and self.pil:
                im = self.pil
                self.pil = None
                self.pil_new = False
        if im:
            try:
                from PIL import ImageTk
                if self.photo is None:
                    self.photo = ImageTk.PhotoImage(im)
                    self.item = self.canvas.create_image(0, 0, anchor="nw", image=self.photo)
                else:
                    self.photo.paste(im)
                self.canvas.tag_lower(self.item)
                self.canvas.tag_raise("mark")
                self.canvas.tag_raise("region")
                self.canvas.tag_raise("preview")
                self.shown += 1
            except Exception as e:
                self.state.configure(text=f"贴图失败 {e}", foreground="#c0392b")
                self.photo = None
            del im

        if time.time() - self.fps_t > 2:
            span = time.time() - self.fps_t
            fps = self.shown / span
            if self.shown:
                self.state.configure(
                    text=f"画面：已连接  {fps:.0f} 帧/秒（抓 {self.grabbed / span:.0f}，丢 {self.grabbed - self.used}）",
                    foreground="#0a7a52")
            else:
                self.state.configure(text="画面：无响应（检查数据线）", foreground="#c0392b")
            self.shown = 0
            self.grabbed = 0
            self.used = 0
            self.fps_t = time.time()
        self.root.after(FRAME_MS, self._tick)

    # ---------- 手势 ----------
    def _go_back(self):
        """返回上一页：点左上角返回箭头（比边缘手势可靠，坐标可改）"""
        try:
            x = int(self.back_x.get())
            y = int(self.back_y.get())
        except Exception:
            x, y = 34, 70
        self._record("back", x, y)

    def _go_swipe(self):
        """返回上一页：只走左边缘手势（不再挂按钮，留作备用）"""
        self._record("backswipe")

    def _go_home(self):
        """回主屏：按 Home 键"""
        self._record("home")

    def _to_phone(self, cx, cy):
        x = int(cx / VIEW_W * PHONE_W)
        y = int(cy / VIEW_H * PHONE_H)
        return max(0, min(PHONE_W - 1, x)), max(0, min(PHONE_H - 1, y))

    def _on_press(self, ev):
        self.press = (ev.x, ev.y, time.time())
        self.dragging = False
        self.canvas.delete("preview")

    def _on_motion(self, ev):
        if not self.press:
            return
        x0, y0, _ = self.press
        if self.region_mode:
            self.canvas.delete("preview")
            self.canvas.create_rectangle(x0, y0, ev.x, ev.y, outline="#2f9cff",
                                         width=2, tags="preview")
            self.canvas.tag_raise("preview")
            return
        if abs(ev.x - x0) + abs(ev.y - y0) > DRAG_PX:
            self.dragging = True
            self.canvas.delete("preview")
            self.canvas.create_line(x0, y0, ev.x, ev.y, fill="#ff3b30", width=2, tags="preview")
            self.canvas.tag_raise("preview")

    def _on_release(self, ev):
        if not self.press:
            return
        x0, y0, t0 = self.press
        self.press = None
        p0 = self._to_phone(x0, y0)

        # 范围模式：拖框存触发区域，不记主流程、不注入手机
        if self.region_mode:
            iss = self._current_issue()
            if iss is None:
                self._log("先新增异常页，再框选范围")
                return
            pb = self._to_phone(ev.x, ev.y)
            region = [min(p0[0], pb[0]), min(p0[1], pb[1]),
                      max(p0[0], pb[0]), max(p0[1], pb[1])]
            if region[2] - region[0] < 12 or region[3] - region[1] < 12:
                self._log("范围太小，请在画面上拖出一个矩形框")
                return
            iss["region"] = region
            self.canvas.delete("mark")
            self.canvas.delete("preview")
            self._draw_region()
            self._refresh_issues()
            self._refresh_region_label()
            self._log(f"触发范围 {region} 已存入「{iss.get('name', '问题界面')}」")
            return

        # 标记模式：画布点取的是异常分支处置点，不记主流程、不注入手机
        if self.mark_mode:
            iss = self._current_issue()
            if iss is None:
                self._log("先新增异常页，再点取处置点")
                return
            idx = len(iss.setdefault("points", [])) + 1
            act_map = {"点击": "tap", "返回": "back", "主屏": "home", "等待": "wait"}
            act = act_map.get(self.iss_action.get(), "tap")
            if act == "tap":
                point = {"action": "tap", "x": p0[0], "y": p0[1], "label": f"点击点 {idx}"}
                self._dot(x0, y0, "#9b59b6")
            elif act == "back":
                point = {"action": "back", "label": f"返回 {idx}"}
            elif act == "home":
                point = {"action": "home", "label": f"主屏 {idx}"}
            else:
                try:
                    ms = int(float(self.iss_wait_ms.get() or 800))
                except Exception:
                    ms = 800
                point = {"action": "wait", "ms": ms, "label": f"等待 {ms}ms"}
            iss["points"].append(point)
            self._refresh_issues()
            self._refresh_points()
            self._log(f"处置点[{act}]已加入「{iss.get('name', '问题界面')}」")
            return

        dt = int((time.time() - t0) * 1000)
        p1 = self._to_phone(ev.x, ev.y)

        self.canvas.delete("mark")
        self.canvas.delete("preview")

        if self.dragging:
            self._record("swipe", p0[0], p0[1], p1[0], p1[1], max(dt, 120))
            self.canvas.create_line(x0, y0, ev.x, ev.y, fill="#0a7a52", width=2, tags="mark")
            self._dot(ev.x, ev.y, "#0a7a52")
        elif dt >= LONG_MS:
            self._record("hold", p0[0], p0[1], dt)
            self._dot(ev.x, ev.y, "#ff9500")
        else:
            self._record("tap", p0[0], p0[1])
            self._dot(ev.x, ev.y, "#ff3b30")
        self.canvas.tag_raise("mark")

    def _dot(self, cx, cy, color):
        r = 6
        self.canvas.create_oval(cx - r, cy - r, cx + r, cy + r,
                                outline=color, width=2, tags="mark")

    def _send_text(self):
        s = self.text_in.get()
        if not s:
            return
        self._record("text", s)
        self.text_in.delete(0, "end")

    def _refresh_events(self):
        self.lb.delete(0, "end")
        for i, (kind, args, gap) in enumerate(self.events):
            self.lb.insert("end", f"{i + 1:>2}  {self._desc(kind, args)}  +{gap}ms")
        self.count.configure(text=f"已录 {len(self.events)} 个动作")

    def _del_event(self):
        sel = self.lb.curselection()
        if not sel:
            self._log("先在动作列表选中要删除的动作")
            return
        idx = sel[0]
        if idx < 0 or idx >= len(self.events):
            return
        del self.events[idx]
        self._refresh_events()
        self._log(f"已删除动作 {idx + 1}")

    def _record(self, kind, *args):
        now = time.time()
        gap = int((now - self.last_t) * 1000) if self.last_t else 0
        self.last_t = now
        self.events.append((kind, args, gap))
        self._refresh_events()

        if self.inject_on.get():
            try:
                self.inject_q.put_nowait((kind, args))
            except queue.Full:
                pass

    @staticmethod
    def _desc(kind, a):
        if kind == "tap":
            return f"点击 ({a[0]},{a[1]})"
        if kind == "hold":
            return f"长按 ({a[0]},{a[1]}) {a[2]}ms"
        if kind == "swipe":
            return f"滑动 ({a[0]},{a[1]})→({a[2]},{a[3]}) {a[4]}ms"
        if kind == "home":
            return "回到主屏"
        if kind == "back":
            return f"返回（点 {a[0]},{a[1]}）"
        if kind == "backswipe":
            return "返回（边缘手势）"
        return f'键入 "{a[0]}"'

    # ---------- 注入 ----------
    def _inject_loop(self):
        while not self.stop_flag:
            try:
                kind, args = self.inject_q.get(timeout=0.3)
            except queue.Empty:
                continue

            # 注入期间禁止抓帧，免得截图请求把 WDA 队列占满、点击排不上
            self.act_busy.set()
            time.sleep(0.35)          # 等在飞的截图请求先落地，点击别排在它们后面
            ok, err = True, ""
            try:
                if kind == "tap":
                    quick.tap2(*args)
                elif kind == "hold":
                    quick.long_press(args[0], args[1], args[2])
                elif kind == "swipe":
                    quick.swipe2(args[0], args[1], args[2], args[3], args[4])
                elif kind == "text":
                    quick.keys(args[0])
                elif kind == "home":
                    r = quick.press_home()
                    ok = not (isinstance(r, dict) and r.get("error"))
                    err = str(r)[:80] if not ok else ""
                elif kind == "back":
                    r = quick.tap2(args[0], args[1])
                    ok = not (isinstance(r, dict) and r.get("error"))
                    err = str(r)[:80] if not ok else ""
                elif kind == "backswipe":
                    r = quick.go_back()
                    ok = not (isinstance(r, dict) and r.get("error"))
                    err = str(r)[:80] if not ok else ""
            except Exception as e:
                ok, err = False, f"{type(e).__name__}: {e}"
            finally:
                time.sleep(0.12)
                self.act_busy.clear()

            self.root.after(0, self._log_inject, kind, ok, err)

    def _append_log(self, msg):
        try:
            self.log_lb.insert("end", msg)
            self.log_lb.see("end")
        except Exception:
            pass

    def _log_inject(self, kind, ok, err):
        if ok:
            self._append_log("✓ " + (err if err else "已注入手机"))
        else:
            self._append_log("✗ 注入失败 " + err)

    # ---------- 脚本 ----------
    # ---------------- 异常分支 / 任务导出 ----------------
    def _log(self, msg):
        print(msg, flush=True)
        try:
            self.root.after(0, lambda: self._append_log(msg))
        except Exception:
            pass

    def _refresh_issues(self):
        self.iss_lb.delete(0, "end")
        for iss in self.issues:
            pts = len(iss.get("points", []))
            kw = "、".join(iss.get("keywords", []))
            box = " · 含范围" if iss.get("region") else ""
            self.iss_lb.insert("end", f"{iss.get('name', '问题界面')} · {pts}点{box} · {kw}")
        if 0 <= self.selected_issue < len(self.issues):
            self.iss_lb.selection_set(self.selected_issue)

    def _fill_action_controls(self, iss):
        acts = [p.get("action", "tap") for p in iss.get("points", [])] if iss else []
        last = acts[-1] if acts else "tap"
        am = {"tap": "点击", "back": "返回", "home": "主屏", "wait": "等待"}
        self.iss_action.set(am.get(last, "点击"))
        waits = [p.get("ms", 800) for p in iss.get("points", []) if p.get("action") == "wait"] if iss else []
        self._set_entry(self.iss_wait_ms, str(waits[-1] if waits else 800))

    def _refresh_points(self):
        self.pt_lb.delete(0, "end")
        iss = self._current_issue()
        if not iss:
            return
        for i, p in enumerate(iss.get("points", [])):
            act = p.get("action", "tap")
            if act == "tap":
                tail = f"({p.get('x')},{p.get('y')})"
            elif act == "back":
                tail = "返回"
            elif act == "home":
                tail = "主屏"
            else:
                tail = f"等待 {p.get('ms', 0)}ms"
            self.pt_lb.insert("end", f"{i + 1}. {p.get('label', act)} {tail}")

    def _del_point(self):
        iss = self._current_issue()
        if iss is None:
            self._log("先新增并选中一个异常页")
            return
        sel = self.pt_lb.curselection()
        if not sel:
            self._log("先选中要删除的处置点")
            return
        del iss["points"][sel[0]]
        self._refresh_points()
        self._refresh_issues()

    def _current_issue(self):
        if 0 <= self.selected_issue < len(self.issues):
            return self.issues[self.selected_issue]
        return None

    def _set_entry(self, entry, value):
        entry.delete(0, "end")
        entry.insert(0, str(value))

    def _sync_form_to_issue(self):
        iss = self._current_issue()
        if not iss:
            return
        raw = self.iss_keywords.get().strip()
        iss["keywords"] = [k.strip() for k in raw.replace("，", ",").split(",") if k.strip()]
        try:
            iss["max_rounds"] = max(1, int(float(self.iss_rounds.get())))
        except Exception:
            iss["max_rounds"] = 3

    def _on_issue_pick(self, _e):
        sel = self.iss_lb.curselection()
        if not sel:
            return
        self._sync_form_to_issue()
        self.selected_issue = sel[0]
        iss = self.issues[self.selected_issue]
        self._set_entry(self.iss_keywords, "，".join(iss.get("keywords", [])))
        self._set_entry(self.iss_rounds, str(iss.get("max_rounds", 3)))
        self._fill_action_controls(iss)
        self._refresh_region_label()
        self._draw_region()
        self._refresh_points()

    def _add_issue(self):
        self._sync_form_to_issue()
        iss = {"name": f"问题界面 {len(self.issues) + 1}", "keywords": [],
               "region": None, "points": [], "max_rounds": 3, "note": ""}
        self.issues.append(iss)
        self.selected_issue = len(self.issues) - 1
        self._refresh_issues()
        self._refresh_points()
        self._draw_region()
        self._fill_action_controls(None)
        self._set_entry(self.iss_keywords, "")
        self._set_entry(self.iss_rounds, "3")

    def _del_issue(self):
        if self._current_issue() is None:
            self._log("没有可删除的异常页")
            return
        del self.issues[self.selected_issue]
        self.selected_issue = max(-1, self.selected_issue - 1)
        self._refresh_issues()
        if self.selected_issue >= 0:
            iss = self.issues[self.selected_issue]
            self._set_entry(self.iss_keywords, "，".join(iss.get("keywords", [])))
            self._set_entry(self.iss_rounds, str(iss.get("max_rounds", 3)))
        else:
            iss = None
            self._set_entry(self.iss_keywords, "")
            self._set_entry(self.iss_rounds, "3")
        self._fill_action_controls(iss)
        self._refresh_region_label()
        self._draw_region()
        self._refresh_points()

    def _sync_modes(self):
        m = self.mode_var.get()
        self.mark_mode = m == "mark"
        self.region_mode = m == "region"
        self.canvas.delete("preview")
        hints = {"record": "正常录制，点画布即操作手机",
                 "mark": "点取异常处置点，不录主流程",
                 "region": "拖框选触发范围，不录主流程"}
        self.mode_hint.configure(text=hints[m],
                                 foreground="#0a7a52" if m == "record" else "#b8860b")

    def _to_view(self, px, py):
        return px / PHONE_W * VIEW_W, py / PHONE_H * VIEW_H

    def _draw_region(self):
        self.canvas.delete("region")
        iss = self._current_issue()
        if not iss or not iss.get("region"):
            return
        x1, y1, x2, y2 = iss["region"]
        vx1, vy1 = self._to_view(x1, y1)
        vx2, vy2 = self._to_view(x2, y2)
        self.canvas.create_rectangle(vx1, vy1, vx2, vy2, outline="#2f9cff",
                                     width=2, dash=(4, 3), tags="region")

    def _clear_region(self):
        iss = self._current_issue()
        if iss is None:
            return
        iss["region"] = None
        self._refresh_issues()
        self._refresh_region_label()

    def _refresh_region_label(self):
        iss = self._current_issue()
        r = iss.get("region") if iss else None
        if r:
            self.region_label.configure(
                text=f"范围 {r[0]},{r[1]}-{r[2]},{r[3]}", foreground="#2f9cff")
        else:
            self.region_label.configure(text="全屏匹配", foreground="#888")

    def _ocr_pick(self):
        try:
            if self.pil is not None:
                items = ocr_mod.recognize(self.pil)
            else:
                data, err = quick.shot_bytes()
                items = ocr_mod.recognize(data) if not err and data else []
        except Exception as e:
            self._log(f"OCR 失败：{e}")
            return
        if not items:
            self._log("当前帧未识别到文字")
            return
        win = tk.Toplevel(self.root)
        win.title("选择触发文字")
        lb = tk.Listbox(win, width=34, font=("Consolas", 10))
        lb.pack(fill="both", expand=True)
        for it in items:
            lb.insert("end", f"{it.text}  ({it.center[0]:.0f},{it.center[1]:.0f})")

        def pick(_e):
            sel = lb.curselection()
            if not sel:
                return
            it = items[sel[0]]
            kw = [k for k in self.iss_keywords.get().strip()
                  .replace("，", ",").split(",") if k.strip()]
            if it.text not in kw:
                kw.append(it.text)
            self._set_entry(self.iss_keywords, "，".join(kw))
            self._sync_form_to_issue()
            win.destroy()

        lb.bind("<Double-Button-1>", pick)

    def _events_to_steps(self):
        steps = []
        for kind, a, gap in self.events:
            st = {"gap": gap}
            if kind == "tap":
                st.update({"type": "tap", "x": a[0], "y": a[1]})
            elif kind == "hold":
                st.update({"type": "hold", "x": a[0], "y": a[1], "ms": a[2]})
            elif kind == "swipe":
                st.update({"type": "swipe", "x1": a[0], "y1": a[1],
                           "x2": a[2], "y2": a[3], "ms": a[4]})
            elif kind == "home":
                st.update({"type": "home"})
            elif kind == "back":
                st.update({"type": "back", "x": a[0], "y": a[1]})
            elif kind == "backswipe":
                st.update({"type": "backswipe"})
            elif kind == "text":
                st.update({"type": "text", "text": a[0]})
            else:
                continue
            steps.append(st)
        return steps

    def _generate_task(self):
        self._sync_form_to_issue()
        name = self.task_name.get().strip() or "未命名任务"
        try:
            rounds = int(float(self.task_rounds.get() or "10"))
        except Exception:
            rounds = 10
        steps = self._events_to_steps()
        if not steps:
            messagebox.showinfo("空录制", "先在画面上录主流程动作，再生成任务脚本。")
            return
        clean_issues = [i for i in self.issues if i.get("keywords") or i.get("points")]
        py = flow_template.render_py(name, steps, clean_issues or None,
                                     default_rounds=rounds, description="")
        safe = re.sub(r'[\\/:*?"<>|\s]+', "_", name).strip("_") or "task"
        os.makedirs(TASK_DIR, exist_ok=True)
        py_path = os.path.join(TASK_DIR, safe + ".py")
        with open(py_path, "w", encoding="utf-8") as f:
            f.write(py)
        rel = os.path.relpath(py_path, ROOT).replace(os.sep, "/")
        task = {
            "name": name,
            "group": TASK_GROUP,
            "script": rel,
            "fields": [{"key": "rounds", "label": "跑多少轮后停 (0=一直跑)",
                        "default": str(rounds), "width": 8}],
            "hint": "由动作录制导出；主流程坐标回放，每步后 OCR 检测异常弹窗并处置。",
            "source": "record",
        }
        task_registry.upsert(ROOT, task)
        self._log(f"任务脚本已生成：{rel}")
        messagebox.showinfo("已生成", f"脚本：{rel}\n已登记到 gui，刷新任务即可看到。")

    def _make_script(self):
        if not self.events:
            self.text.delete("1.0", "end")
            self.text.insert("end", "还没有录制动作")
            return
        parts = []
        for kind, a, gap in self.events:
            if gap > 0:
                parts.append(f"wait:{gap}")
            if kind == "tap":
                parts.append(f"{a[0]},{a[1]},1,1")
            elif kind == "hold":
                parts.append(f"hold:{a[0]},{a[1]},{a[2]}")
            elif kind == "swipe":
                parts.append(f"swipe:{a[0]},{a[1]},{a[2]},{a[3]},{a[4]}")
            elif kind == "home":
                parts.append("home:")
            elif kind == "back":
                parts.append(f"back:{a[0]},{a[1]}")
            elif kind == "backswipe":
                parts.append("backswipe:")
            else:
                txt = a[0]
                if ";" in txt or "\n" in txt:
                    parts.append("text64:" + base64.b64encode(txt.encode("utf-8")).decode())
                else:
                    parts.append("text:" + txt)
        s = "; ".join(parts)
        self.text.delete("1.0", "end")
        self.text.insert("end", s)
        os.makedirs(DATA, exist_ok=True)
        with open(RECORD_FILE, "w", encoding="utf-8") as f:
            f.write(s)
        print(s)
        print(f"已保存到 {RECORD_FILE}")

    def _clear(self):
        self.events = []
        self.issues = []
        self.selected_issue = -1
        self.last_t = None
        self.mode_var.set("record")
        self._sync_modes()
        self.lb.delete(0, "end")
        self.log_lb.delete(0, "end")
        self._refresh_issues()
        self._refresh_points()
        self.canvas.delete("mark")
        self.canvas.delete("region")
        self.canvas.delete("preview")
        self._set_entry(self.iss_keywords, "")
        self._set_entry(self.iss_rounds, "3")
        self._refresh_region_label()
        self.count.configure(text="已录 0 个动作")
        self.text.delete("1.0", "end")
        self.text_in.delete(0, "end")

    def _close(self):
        self.stop_flag = True
        with self.lock:
            self.raw = None
            self.pil = None
        try:
            self.root.destroy()
        except Exception:
            pass


if __name__ == "__main__":
    root = tk.Tk()
    RecorderApp(root)
    root.mainloop()
