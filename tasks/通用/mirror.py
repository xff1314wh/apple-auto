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
from tkinter import ttk

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
CORE = os.path.join(ROOT, "core")
sys.path.insert(0, CORE)
import quick

DATA = os.path.join(ROOT, "data")
RECORD_FILE = os.path.join(DATA, "record_script.txt")

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
        self.last_t = None
        self.press = None
        self.dragging = False

        self.raw = None
        self.raw_new = False
        self.raw_ts = 0.0        # 已被接受的最新帧的拍摄时间戳
        self.pil = None
        self.pil_new = False
        self.lock = threading.Lock()

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
        ttk.Button(right, text="生成脚本", command=self._make_script).pack(fill="x", pady=2)
        ttk.Button(right, text="清空录制", command=self._clear).pack(fill="x", pady=2)

        ttk.Label(right, text="动作列表", font=("Microsoft YaHei UI", 9, "bold")).pack(anchor="w", pady=(8, 2))
        self.lb = tk.Listbox(right, height=9, font=("Consolas", 9))
        self.lb.pack(fill="both", expand=True)

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
                time.sleep(0.12)

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
        dt = int((time.time() - t0) * 1000)
        p0 = self._to_phone(x0, y0)
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

    def _record(self, kind, *args):
        now = time.time()
        gap = int((now - self.last_t) * 1000) if self.last_t else 0
        self.last_t = now
        self.events.append((kind, args, gap))

        self.count.configure(text=f"已录 {len(self.events)} 个动作")
        self.lb.insert("end", f"{len(self.events):>2}  {self._desc(kind, args)}  +{gap}ms")
        self.lb.see("end")

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

    def _log_inject(self, kind, ok, err):
        try:
            if ok:
                self.lb.insert("end", "      → ✓ " + (err if err else "已注入手机"))
            else:
                self.lb.insert("end", "      → ✗ 注入失败 " + err)
            self.lb.see("end")
        except Exception:
            pass

    # ---------- 脚本 ----------
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
        self.last_t = None
        self.lb.delete(0, "end")
        self.canvas.delete("mark")
        self.canvas.delete("preview")
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
