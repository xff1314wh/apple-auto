"""苹果手机自动化 - 主文件（控制台界面）

左侧按任务文件夹分组列出自动化操作，右侧填参数，下方实时日志。
每一项都对应 tasks\\ 或 core\\ 下的真实脚本。

运行：python gui.py
"""
import http.client
import io
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import messagebox, scrolledtext, simpledialog, ttk

ROOT = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
sys.path.insert(0, os.path.join(ROOT, "core"))
import quick  # noqa: E402
import task_registry  # noqa: E402
import handoff  # noqa: E402
import combo  # noqa: E402

TICKET = "tasks/通用/fasttap.py"
DM = "tasks/大麦抢票/dm_ticket.py"
KUAI = "tasks/快手宝箱/chest_flow.py"
SHOT = "tasks/通用/shot.py"
WDAUP = "tasks/通用/wda_up.py"
CLEAR_BG = "tasks/通用/clear_bg.py"
UNLOCK_PHONE = "tasks/通用/unlock_phone.py"
MIRROR = "tasks/通用/mirror.py"
MATERIAL = "tasks/通用/material.py"

RECORD_FILE = os.path.join(ROOT, "data", "record_script.txt")
PARAM_FILE = os.path.join(ROOT, "data", "task_params.json")

# ---- MJPEG 屏幕流（只读预览，复用 mirror 的抓帧管线） ----
MJPEG_HOST = "127.0.0.1"
MJPEG_PORT = quick.MJPEG_PORT
MJPEG_BOUNDARY = b"--BoundaryString"
RE_JPEG_LEN = re.compile(r"Content-Length:\s*(\d+)", re.I)
PHONE_W, PHONE_H = 414, 896


def _parse_boundary(content_type):
    if not content_type:
        return None
    for part in content_type.split(";"):
        part = part.strip()
        if part.lower().startswith("boundary="):
            return part.split("=", 1)[1].strip().encode("latin1")
    return None


def _pop_jpeg_frame(buf, boundary):
    bi = buf.find(boundary)
    if bi < 0:
        return None, buf
    head_end = buf.find(b"\r\n\r\n", bi)
    if head_end < 0:
        return None, buf
    head = buf[bi:head_end].decode("latin1", "ignore")
    m = RE_JPEG_LEN.search(head)
    if not m:
        return None, buf[bi + len(boundary):]
    n = int(m.group(1))
    jpeg_start = head_end + 4
    jpeg_end = jpeg_start + n
    if len(buf) < jpeg_end:
        return None, buf
    return buf[jpeg_start:jpeg_end], buf[jpeg_end:]

# ---------------- 任务定义 ----------------
# group 就是 tasks\\ 下的文件夹名；"通用" 对应 core\\ 里的底层能力。

OPERATIONS = [
    {
        "group": "通用",
        "name": "检查手机连接",
        "desc": "确认数据线、WDA 服务是否正常。跑任何任务前先点这个。",
        "fields": [],
        "cmd": lambda v: [PY, WDAUP],
        "hint": "手机要插着线、保持解锁。提示设备不在线就重新插一下或换个 USB 口。",
    },
    {
        "group": "通用",
        "name": "截图看当前画面",
        "desc": "截一张手机当前屏幕，存到 data\\shots\\now.png，用来量坐标。",
        "fields": [],
        "cmd": lambda v: [PY, SHOT, "data/shots/now.png"],
        "hint": "截图是 828x1792 像素，像素除以 2 就是点击坐标。",
    },
    {
        "group": "通用",
        "name": "动作录制（镜像点选）",
        "desc": "左边弹出手机实时画面，你对着它点，点哪就点到手机上哪，同时把动作记成脚本。",
        "fields": [],
        "cmd": lambda v: [PY, MIRROR],
        "hint": "录完在录制窗口点「生成脚本」，存到 data\\record_script.txt；"
                 "再回到「连点流水线」点「载入上次录制」就能原样回放。",
    },
    {
        "group": "通用",
        "name": "素材打标 / 素材库",
        "desc": "导入录屏视频按帧率抽帧，逐步打标（点击/长按/滑动/返回/主屏/键入/等待），"
                "坐标直接在画面上点取，每步可填参数、键入文字和备注；存成素材库，生成可运行的自动化代码和 AI 素材文档。",
        "fields": [],
        "cmd": lambda v: [PY, MATERIAL],
        "hint": "素材存在 data\\materials\\：<名字>.py 是可直接运行的自动化代码，<名字>.md 是帧图对照的 AI 参考素材，frames/ 是抽出来的帧图。",
    },
    {
        "group": "通用",
        "name": "连点流水线",
        "desc": "按顺序点多个位置，段与段之间按你设的毫秒数等待。回放、抢票都用它。",
        "fields": [
            {"key": "script", "label": "脚本串",
             "default": "207,800,1,1; wait:1500; 207,700,1,1",
             "width": 48, "btn": ("载入上次录制", "_load_record")},
        ],
        "cmd": lambda v: [PY, TICKET, "--script", v["script"]],
        "hint": "格式：x,y,间隔毫秒,次数，分段用分号隔开；wait:1500 表示停 1.5 秒等页面加载。"
                 "次数填 0 就一直点这一段。\n"
                 "另外支持：text:文字（键入）· hold:x,y,ms（长按）· swipe:x1,y1,x2,y2,ms（滑动）。",
    },
    {
        "group": "快手宝箱",
        "name": "自动领取金币",
        "desc": "点左上角金币宝箱 → 弹窗点「领取奖励」 → 等倒计时 → 下一轮。",
        "fields": [
            {"key": "rounds", "label": "跑多少轮后停 (0=一直跑)", "default": "10", "width": 8},
        ],
        "cmd": lambda v: [PY, KUAI, v["rounds"]],
        "hint": "按素材坐标顺序回放，每步后 OCR 检测异常弹窗并自动处置；识别不到不打断主流程。",
    },
    {
        "group": "大麦抢票",
        "name": "单点连击",
        "desc": "对着一个坐标高频点击，比如「立即购买」按钮。",
        "fields": [
            {"key": "x", "label": "横坐标 X", "default": "207", "width": 8},
            {"key": "y", "label": "纵坐标 Y", "default": "800", "width": 8},
            {"key": "delay", "label": "额外间隔 (毫秒)", "default": "0", "width": 8},
            {"key": "rounds", "label": "点击次数 (0=一直点)", "default": "20", "width": 8},
            {"key": "at", "label": "定时开始 (HH:MM:SS，空=立刻)", "default": "", "width": 12},
        ],
        "cmd": lambda v: [PY, TICKET, v["x"], v["y"],
                          "--delay", v["delay"], "--rounds", v["rounds"]]
        + (["--at", v["at"]] if v.get("at", "").strip() else []),
        "hint": "定时填 12:00:00 就是中午十二点整开始点。进程会提前起来挂着，到点才动手。",
    },
    {
        "group": "大麦抢票",
        "name": "一键抢票（定时）",
        "desc": "预约选票页 → 详情页立即预订 → 确认页立即提交，一条流水跑完，停在支付页。",
        "fields": [
            {"key": "at", "label": "开售时间", "default": "", "width": 12, "kind": "time"},
            {"key": "lead", "label": "提前毫秒（补网络延迟，200 左右）", "default": "0", "width": 6},
            {"key": "pay", "label": "连付款按钮也点（仍会弹密码）填 1", "default": "", "width": 4},
            {"key": "jitter", "label": "坐标抖动 ±pt", "default": "2", "width": 6},
            {"key": "hold", "label": "按住毫秒（最短 20）", "default": "20", "width": 6},
            {"key": "gap", "label": "两下间隔毫秒（最快 0）", "default": "0", "width": 6},
        ],
        "cmd": lambda v: [PY, DM]
        + (["--at", v["at"].strip()] if v.get("at", "").strip() else [])
        + (["--lead", v["lead"].strip()] if v.get("lead", "").strip() not in ("", "0") else [])
        + (["--pay"] if v.get("pay", "").strip() else [])
        + ["--jitter", v["jitter"].strip(), "--hold", v["hold"].strip(), "--gap", v["gap"].strip()],
        "hint": "开抢前先把手机停在大麦演出详情页（或预约选票页），屏幕别锁。"
                 "付款那一下要人脸/密码，脚本停在支付页等你。\n"
                 "参数尽量短：抖动 0~2，按住 20，间隔 0；偶尔丢点再把间隔加到 30。"
                 "按住低于 15ms 可能识别不到。",
    },
]


class Runner:
    def __init__(self, on_line):
        self.proc = None
        self.on_line = on_line

    def running(self):
        return self.proc is not None and self.proc.poll() is None

    def start(self, cmd):
        if self.running():
            return False
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
        creation = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            self.proc = subprocess.Popen(
                cmd, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                encoding="utf-8", errors="replace", env=env,
                creationflags=creation, bufsize=1)
        except Exception as e:
            self.on_line(f"启动失败：{e}")
            return False
        threading.Thread(target=self._pump, daemon=True).start()
        return True

    def _pump(self):
        try:
            for line in self.proc.stdout:
                self.on_line(line.rstrip())
        except Exception as e:
            self.on_line(f"读取输出出错：{e}")
        self.on_line(f"—— 进程结束（退出码 {self.proc.wait()}）——")

    def stop(self):
        if not self.running():
            return
        try:
            subprocess.run(["taskkill", "/PID", str(self.proc.pid), "/T", "/F"],
                           capture_output=True, timeout=10)
        except Exception:
            try:
                self.proc.terminate()
            except Exception:
                pass


class PhonePreview(ttk.Frame):
    """手机 MJPEG 实时画面预览（只读，不注入操作）。三段管线：读流 → 解码缩放 → 贴图。"""

    FRAME_MS = 40

    def __init__(self, master, view_w=180):
        super().__init__(master)
        self.view_w = view_w
        self.view_h = round(view_w * PHONE_H / PHONE_W)
        self.raw = None
        self.raw_new = False
        self.raw_ts = 0.0
        self.pil = None
        self.pil_new = False
        self.lock = threading.Lock()
        self.stop_flag = False
        self.photo = None
        self.item = None
        self.grabbed = self.used = self.shown = 0
        self.fps_t = time.time()

        ttk.Label(self, text="手机画面", font=("Microsoft YaHei UI", 9, "bold")).pack(anchor="w")
        self.canvas = tk.Canvas(self, width=self.view_w, height=self.view_h, bg="#111",
                                highlightthickness=1, highlightbackground="#666")
        self.canvas.pack()
        self.state = ttk.Label(self, text="等待画面", foreground="#888",
                               font=("Microsoft YaHei UI", 8))
        self.state.pack(anchor="w", pady=(3, 0))

        threading.Thread(target=self._mjpeg_worker, daemon=True).start()
        threading.Thread(target=self._decode_worker, daemon=True).start()
        self.after(self.FRAME_MS, self._tick)

    def _mjpeg_worker(self):
        conn = resp = None
        buf = b""
        boundary = MJPEG_BOUNDARY
        while not self.stop_flag:
            try:
                if conn is None:
                    if not quick.ensure_mjpeg_forward():
                        time.sleep(0.5)
                        continue
                    conn = http.client.HTTPConnection(MJPEG_HOST, MJPEG_PORT, timeout=10)
                    conn.request("GET", "/")
                    resp = conn.getresponse()
                    if resp.status != 200:
                        resp.read()
                        conn.close()
                        conn = None
                        time.sleep(0.5)
                        continue
                    boundary = _parse_boundary(resp.headers.get("Content-Type", "")) or MJPEG_BOUNDARY
                    buf = b""
                chunk = resp.read(65536)
                if not chunk:
                    raise EOFError("stream closed")
                buf += chunk
                jpeg, buf = _pop_jpeg_frame(buf, boundary)
                if jpeg is None:
                    continue
                if not jpeg.startswith(b"\xff\xd8"):
                    continue
                t_req = time.time()
                with self.lock:
                    self.grabbed += 1
                    if t_req >= self.raw_ts:
                        self.raw = jpeg
                        self.raw_ts = t_req
                        self.raw_new = True
                        self.used += 1
            except Exception:
                try:
                    if conn:
                        conn.close()
                except Exception:
                    pass
                conn = resp = None
                buf = b""
                time.sleep(0.4)

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
                im = im.resize((self.view_w, self.view_h), Image.BILINEAR)
                with self.lock:
                    self.pil = im
                    self.pil_new = True
            except Exception:
                pass
            del raw

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
                self.shown += 1
            except Exception:
                self.photo = None
            del im
        if time.time() - self.fps_t > 2:
            span = time.time() - self.fps_t
            if self.shown:
                self.state.configure(text=f"已连接 {self.shown / span:.0f} 帧/秒",
                                     foreground="#0a7a52")
            else:
                self.state.configure(text="无响应（检查数据线）", foreground="#c0392b")
            self.shown = self.grabbed = self.used = 0
            self.fps_t = time.time()
        if not self.stop_flag:
            self.after(self.FRAME_MS, self._tick)

    def close(self):
        self.stop_flag = True
        with self.lock:
            self.raw = None
            self.pil = None


class Wheel(tk.Canvas):
    """一个数字滚筒。鼠标滚轮 / 按住拖 / 点哪就是哪，中间那行是选中的值。"""

    ROW_H = 28
    ROWS = 5

    def __init__(self, master, maximum, value=0, width=62, **kw):
        super().__init__(master, width=width, height=self.ROW_H * self.ROWS,
                         bg="white", highlightthickness=1,
                         highlightbackground="#c8c8c8", **kw)
        self.maximum = maximum
        self.idx = value % (maximum + 1)
        self.w = width
        self._drag = None
        self._draw()
        self.bind("<MouseWheel>", self._on_wheel)
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<B1-Motion>", self._on_drag)
        self.bind("<ButtonRelease-1>", self._on_release)

    def _wrap(self, v):
        return v % (self.maximum + 1)

    def value(self):
        return self.idx

    def set(self, v):
        self.idx = self._wrap(int(v))
        self._draw()

    def step(self, d):
        self.idx = self._wrap(self.idx + d)
        self._draw()

    def _on_wheel(self, ev):
        self.step(-1 if ev.delta > 0 else 1)

    def _on_press(self, ev):
        row = ev.y // self.ROW_H
        mid = self.ROWS // 2
        if row != mid:                       # 点上面的行就往上转，点下面往下转
            self.step(row - mid)
        self._drag = (ev.y, self.idx)

    def _on_drag(self, ev):
        if not self._drag:
            return
        y0, i0 = self._drag
        moved = (y0 - ev.y) // self.ROW_H
        if moved:
            self.idx = self._wrap(i0 + moved)
            self._drag = (ev.y, self.idx)
            self._draw()

    def _on_release(self, _ev):
        self._drag = None

    def _draw(self):
        self.delete("all")
        mid = self.ROWS // 2
        self.create_rectangle(3, mid * self.ROW_H + 2, self.w - 3,
                              (mid + 1) * self.ROW_H - 2, fill="#e9f2ff", outline="")
        for i in range(self.ROWS):
            v = self._wrap(self.idx + (i - mid))
            if i == mid:
                font, color = ("Consolas", 15, "bold"), "#111"
            elif abs(i - mid) == 1:
                font, color = ("Consolas", 11), "#888"
            else:
                font, color = ("Consolas", 10), "#ccc"
            self.create_text(self.w // 2, i * self.ROW_H + self.ROW_H // 2,
                             text="%02d" % v, font=font, fill=color)


class TimeWheel(ttk.Frame):
    """时 / 分 / 秒 三个滚筒。不勾「按点开抢」就等于不用定时，立刻开跑。"""

    def __init__(self, master, default=""):
        super().__init__(master)
        hh = mm = ss = 0
        if default and ":" in default:
            parts = (default.split(":") + ["0", "0", "0"])[:3]
            try:
                hh, mm, ss = [int(float(x)) for x in parts]
            except Exception:
                hh = mm = ss = 0
        self.h = Wheel(self, 23, hh)
        self.m = Wheel(self, 59, mm)
        self.s = Wheel(self, 59, ss)
        self.h.pack(side="left")
        ttk.Label(self, text=":", font=("Consolas", 16, "bold")).pack(side="left", padx=3)
        self.m.pack(side="left")
        ttk.Label(self, text=":", font=("Consolas", 16, "bold")).pack(side="left", padx=3)
        self.s.pack(side="left")

        self.on = tk.BooleanVar(value=bool(default))
        ttk.Checkbutton(self, text="到点自动开抢", variable=self.on).pack(side="left", padx=(14, 0))

    def get(self):
        if not self.on.get():
            return ""
        return "%02d:%02d:%02d" % (self.h.value(), self.m.value(), self.s.value())

    def delete(self, *_a):
        pass

    def insert(self, *_a):
        pass


class StepDialog(tk.Toplevel):
    """编辑组合里的一个步骤：任务 / 跑几分钟 / 用哪个 App / 该任务自己的参数。"""

    def __init__(self, master, tasks, step=None):
        super().__init__(master)
        self.title("编辑步骤")
        self.transient(master)
        self.result = None
        self.tasks = tasks or []
        self.step = dict(step or {})
        self.param_fields = {}

        f = ttk.Frame(self, padding=14)
        f.pack(fill="both", expand=True)

        ttk.Label(f, text="跑哪个任务").grid(row=0, column=0, sticky="w", pady=5)
        self.task = ttk.Combobox(f, values=[t.get("name", "") for t in self.tasks],
                                 width=26, state="readonly")
        self.task.set(self.step.get("task") or (self.tasks[0]["name"] if self.tasks else ""))
        self.task.grid(row=0, column=1, sticky="w", padx=10)
        self.task.bind("<<ComboboxSelected>>", lambda e: self._render_params())

        ttk.Label(f, text="跑多少分钟").grid(row=1, column=0, sticky="w", pady=5)
        self.minutes = ttk.Entry(f, width=10)
        if self.step.get("minutes") not in (None, ""):
            self.minutes.insert(0, str(self.step.get("minutes")))
        self.minutes.grid(row=1, column=1, sticky="w", padx=10)

        ttk.Label(f, text="用哪个 App").grid(row=2, column=0, sticky="w", pady=5)
        self.app = ttk.Combobox(f, values=["nebula", "kuaishou"], width=14, state="readonly")
        self.app.set(self.step.get("app") or "nebula")
        self.app.grid(row=2, column=1, sticky="w", padx=10)

        self.box = ttk.LabelFrame(f, text="这个任务的其它参数（留空＝用脚本自带默认）")
        self.box.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        self._render_params()

        bar = ttk.Frame(f)
        bar.grid(row=4, column=0, columnspan=2, sticky="e", pady=(12, 0))
        ttk.Button(bar, text="确定", width=8, command=self._ok).pack(side="left", padx=4)
        ttk.Button(bar, text="取消", width=8, command=self.destroy).pack(side="left")

        self.lift()
        self.focus_force()
        self.grab_set()
        self.wait_window(self)

    def _task_def(self, name):
        return next((t for t in self.tasks if t.get("name") == name), {})

    def _render_params(self):
        """按当前任务重画参数区。轮次不进组合（按时间跑），所以跳过 rounds。"""
        for w in self.box.winfo_children():
            w.destroy()
        self.param_fields = {}
        fields = [fd for fd in (self._task_def(self.task.get()).get("fields") or [])
                  if fd.get("key") not in ("rounds", "app")]
        if not fields:
            ttk.Label(self.box, text="（这个任务没有可填的参数）",
                      foreground="#999").grid(row=0, column=0, padx=8, pady=6, sticky="w")
            return
        params = self.step.get("params") or {}
        for r, fd in enumerate(fields):
            ttk.Label(self.box, text=fd.get("label", fd["key"])).grid(
                row=r, column=0, sticky="w", padx=(8, 0), pady=4)
            e = ttk.Entry(self.box, width=max(12, int(fd.get("width", 10)) + 2))
            e.insert(0, str(params.get(fd["key"], "") or ""))
            e.grid(row=r, column=1, sticky="w", padx=8, pady=4)
            self.param_fields[fd["key"]] = e

    def _ok(self):
        raw = self.minutes.get().strip()
        if not raw:
            messagebox.showwarning("分钟没填", "这一步要跑多少分钟？至少 1 分钟。", parent=self)
            return
        try:
            n = float(raw)
        except ValueError:
            messagebox.showwarning("分钟不对", "分钟要填数字。", parent=self)
            return
        if n < 1:
            messagebox.showwarning("分钟太小", "至少 1 分钟；填 0 会永远进不到下一步。", parent=self)
            return
        params = {}
        for k, w in self.param_fields.items():
            v = w.get().strip()
            if v:
                params[k] = v
        self.result = {"task": self.task.get(), "minutes": n,
                       "app": self.app.get(), "params": params}
        self.destroy()


class ComboDialog(tk.Toplevel):
    """组合任务编辑器：左边挑组合，右边排步骤。"""

    def __init__(self, master, log, on_done=None):
        super().__init__(master)
        self.title("组合任务设置")
        self.geometry("880x540")
        self.minsize(820, 480)
        self.transient(master)
        self.log = log
        self.on_done = on_done
        self.tasks = task_registry.load(ROOT)
        self.task_names = [t.get("name", "") for t in self.tasks]
        self.combos = combo.load(ROOT)
        self.cur = -1
        self.steps = []
        self._build()
        self._refresh()
        self.lift()
        self.focus_force()
        if self.combos:
            self.list.selection_set(0)
            self._pick()
        else:
            self._new()

    def _build(self):
        body = ttk.Frame(self, padding=12)
        body.pack(fill="both", expand=True)

        left = ttk.Frame(body)
        left.pack(side="left", fill="y")
        ttk.Label(left, text="组合", font=("Microsoft YaHei UI", 10, "bold")).pack(anchor="w")
        self.list = tk.Listbox(left, width=22, height=18, activestyle="none",
                               font=("Microsoft YaHei UI", 10), exportselection=False)
        self.list.pack(fill="y", pady=(6, 6))
        self.list.bind("<<ListboxSelect>>", lambda e: self._pick())
        bar = ttk.Frame(left)
        bar.pack(fill="x")
        ttk.Button(bar, text="新建", width=7, command=self._new).pack(side="left")
        ttk.Button(bar, text="删除", width=7, command=self._del).pack(side="left", padx=6)

        right = ttk.LabelFrame(body, text="步骤", padding=(12, 8))
        right.pack(side="left", fill="both", expand=True, padx=(14, 0))

        row = ttk.Frame(right)
        row.pack(fill="x")
        ttk.Label(row, text="组合名").pack(side="left")
        self.name = ttk.Entry(row, width=28)
        self.name.pack(side="left", padx=8)

        cols = ("task", "minutes", "app")
        self.tree = ttk.Treeview(right, columns=cols, show="headings", height=9)
        for c, t, w in (("task", "任务", 210), ("minutes", "分钟", 80), ("app", "App", 100)):
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w, anchor="w")
        self.tree.pack(fill="both", expand=True, pady=(10, 6))
        self.tree.bind("<Double-1>", lambda e: self._edit_step())

        bar2 = ttk.Frame(right)
        bar2.pack(fill="x")
        ttk.Button(bar2, text="加一步", width=9, command=self._add_step).pack(side="left")
        ttk.Button(bar2, text="改这步", width=9, command=self._edit_step).pack(side="left", padx=6)
        ttk.Button(bar2, text="删这步", width=9, command=self._del_step).pack(side="left", padx=6)
        ttk.Button(bar2, text="上移", width=6, command=lambda: self._move(-1)).pack(side="left")
        ttk.Button(bar2, text="下移", width=6, command=lambda: self._move(1)).pack(side="left", padx=4)
        ttk.Button(bar2, text="保存组合", width=11, command=self._save).pack(side="right")

        ttk.Label(right, wraplength=560, foreground="#0a7a52",
                  text="每步按分钟跑，到点跑完当前一条或一轮自己退出；步与步之间清后台，"
                       "下一个任务自己拉起 App 并回到自己的起始页。").pack(anchor="w", pady=(8, 0))

    # ---- 列表 ----
    def _refresh(self):
        self.list.delete(0, "end")
        for c in self.combos:
            self.list.insert("end", c.get("name", "未命名"))
        if 0 <= self.cur < len(self.combos):
            self.list.selection_set(self.cur)
            self.list.activate(self.cur)

    def _pick(self):
        sel = self.list.curselection()
        if not sel:
            return
        i = sel[0]
        if i >= len(self.combos):
            return
        self.cur = i
        c = self.combos[i]
        self.name.delete(0, "end")
        self.name.insert(0, c.get("name", ""))
        self.steps = [dict(s) for s in c.get("steps", [])]
        self._draw_steps()

    def _draw_steps(self):
        for it in self.tree.get_children():
            self.tree.delete(it)
        for k, s in enumerate(self.steps):
            self.tree.insert("", "end", iid=str(k),
                             values=(s.get("task", ""), s.get("minutes", ""), s.get("app", "")))

    # ---- 按钮 ----
    def _new(self):
        self.cur = -1
        self.list.selection_clear(0, "end")
        self.name.delete(0, "end")
        self.name.insert(0, "新组合")
        self.steps = []
        self._draw_steps()

    def _del(self):
        if self.cur < 0:
            return
        c = self.combos[self.cur]
        if not messagebox.askyesno("删除组合", f"删掉「{c.get('name')}」？", parent=self):
            return
        combo.remove(ROOT, c.get("name"))
        self.combos = combo.load(ROOT)
        self.cur = -1
        self._refresh()
        if self.combos:
            self.list.selection_set(0)
            self._pick()
        else:
            self._new()

    def _add_step(self):
        if not self.task_names:
            messagebox.showwarning("没有任务", "任务列表是空的，先回主界面刷新一下。", parent=self)
            return
        # 默认照抄上一步，连跑同一个任务时只改分钟就行
        seed = dict(self.steps[-1]) if self.steps else None
        d = StepDialog(self, self.tasks, seed)
        if d.result:
            self.steps.append(d.result)
            self._draw_steps()

    def _edit_step(self):
        sel = self.tree.selection()
        if not sel:
            return
        i = int(sel[0])
        old = self.steps[i]
        d = StepDialog(self, self.tasks, old)
        if d.result:
            merged = dict(old)
            merged.update(d.result)
            self.steps[i] = merged
            self._draw_steps()

    def _del_step(self):
        sel = self.tree.selection()
        if not sel:
            return
        del self.steps[int(sel[0])]
        self._draw_steps()

    def _move(self, delta):
        """调整步骤顺序：选中一行上移或下移。"""
        sel = self.tree.selection()
        if not sel:
            return
        i = int(sel[0])
        j = i + delta
        if not (0 <= j < len(self.steps)):
            return
        self.steps[i], self.steps[j] = self.steps[j], self.steps[i]
        self._draw_steps()
        self.tree.selection_set(str(j))

    def _save(self):
        nm = self.name.get().strip()
        if not nm:
            messagebox.showwarning("没名字", "组合名不能为空", parent=self)
            return
        cfg = {"name": nm, "steps": [dict(s) for s in self.steps]}
        err = combo.validate(ROOT, cfg)
        if err:
            messagebox.showwarning("配置有问题", err, parent=self)
            return
        self.combos = combo.upsert(ROOT, cfg)
        self.cur = next((i for i, c in enumerate(self.combos) if c.get("name") == nm), 0)
        self._refresh()
        self._pick()
        if self.on_done:
            self.on_done()
        self.log(f"组合已保存：{nm}（{len(cfg['steps'])} 步）")


class App:
    def __init__(self, root):
        self.root = root
        self.queue = queue.Queue()
        self.runner = Runner(self._log)
        self.entries = {}
        self.params = self._read_params()
        self.current = 0
        self.row_of_op = {}
        self.op_of_row = {}
        self.all_ops = []
        self.combo_steps = []
        self.preview = None

        root.title("苹果手机自动化")
        root.geometry("1200x720")
        root.minsize(1080, 640)

        self._load_dynamic()
        self._build()
        self._select(0)
        self.root.after(80, self._drain)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _read_params(self):
        """读取各任务上次填写的参数；文件损坏或缺失时返回空。"""
        try:
            if os.path.isfile(PARAM_FILE):
                data = json.load(io.open(PARAM_FILE, encoding="utf-8"))
                return data if isinstance(data, dict) else {}
        except Exception:
            pass
        return {}

    def _save_params(self):
        """把当前各任务参数落盘，供下次打开回填。"""
        try:
            os.makedirs(os.path.dirname(PARAM_FILE), exist_ok=True)
            io.open(PARAM_FILE, "w", encoding="utf-8").write(
                json.dumps(self.params, ensure_ascii=False, indent=1))
        except Exception as e:
            self._log(f"参数保存失败：{e}")

    def _on_close(self):
        try:
            if self.preview is not None:
                self.preview.close()
        except Exception:
            pass
        self.root.destroy()

    def _toggle_preview(self):
        # 手机画面预览开关：默认关闭；需要时打开，关闭即停掉读流/解码线程。
        if self.preview is None:
            self.preview = PhonePreview(self.bottom, view_w=180)
            self.preview.pack(side="right", fill="y", padx=(12, 0))
            self.btn_preview.configure(text="关闭画面")
        else:
            try:
                self.preview.close()
            except Exception:
                pass
            self.preview.pack_forget()
            self.preview = None
            self.btn_preview.configure(text="手机画面")

    def _build(self):
        top = ttk.Frame(self.root, padding=(14, 10, 14, 4))
        top.pack(fill="x")
        ttk.Label(top, text="苹果手机自动化", font=("Microsoft YaHei UI", 15, "bold")).pack(side="left")
        self.status = ttk.Label(top, text="设备：未检查", foreground="#888")
        self.status.pack(side="right", padx=(0, 10))
        ttk.Button(top, text="检查连接", command=self._check).pack(side="right", padx=6)
        ttk.Button(top, text="清空后台", command=self._clear_bg).pack(side="right", padx=6)
        ttk.Button(top, text="组合设置", command=self._edit_combos).pack(side="right", padx=6)
        ttk.Button(top, text="解锁手机", command=self._unlock_phone).pack(side="right", padx=6)
        ttk.Button(top, text="打开AI兜底", command=self._open_handoff).pack(side="right", padx=6)
        self.btn_preview = ttk.Button(top, text="手机画面", command=self._toggle_preview)
        self.btn_preview.pack(side="right", padx=6)

        body = ttk.Frame(self.root, padding=(14, 4, 14, 4))
        body.pack(fill="both", expand=True)

        left = ttk.Frame(body)
        left.pack(side="left", fill="y")
        head = ttk.Frame(left)
        head.pack(fill="x")
        ttk.Label(head, text="任务", font=("Microsoft YaHei UI", 10, "bold")).pack(side="left")
        ttk.Button(head, text="刷新", width=6, command=self._reload).pack(side="right")
        self.listbox = tk.Listbox(left, width=24, height=12, activestyle="none",
                                  font=("Microsoft YaHei UI", 10), exportselection=False)
        self._fill_list()
        self.listbox.pack(fill="y")
        self.listbox.bind("<<ListboxSelect>>", self._on_pick)

        right = ttk.LabelFrame(body, text="参数设置", padding=(14, 10))
        right.pack(side="left", fill="both", expand=True, padx=(16, 0))
        self.desc = ttk.Label(right, text="", wraplength=600, foreground="#555")
        self.desc.pack(anchor="w", pady=(0, 10))
        self.form = ttk.Frame(right)
        self.form.pack(fill="x")
        self.hint = ttk.Label(right, text="", wraplength=600, foreground="#0a7a52")
        self.hint.pack(anchor="w", pady=(12, 0))

        self.bottom = ttk.Frame(self.root, padding=(14, 0, 14, 12))
        self.bottom.pack(fill="both", expand=True)

        console = ttk.Frame(self.bottom)
        console.pack(side="left", fill="both", expand=True)

        bar = ttk.Frame(console)
        bar.pack(fill="x", pady=(4, 8))
        self.btn_start = ttk.Button(bar, text="开始执行", command=self._start, width=14)
        self.btn_start.pack(side="left")
        self.btn_stop = ttk.Button(bar, text="停止", command=self._stop, width=10, state="disabled")
        self.btn_stop.pack(side="left", padx=8)
        ttk.Button(bar, text="清空日志", command=self._clear, width=10).pack(side="left")

        self.log = scrolledtext.ScrolledText(console, height=13, font=("Consolas", 9),
                                             state="disabled", wrap="word")
        self.log.pack(fill="both", expand=True)



    def _load_dynamic(self):
        """合并内置任务与注册表登记的录制任务。"""
        ops = []
        reg = task_registry.load(ROOT)
        self.tasks = reg
        self.task_names = [t.get("name", "") for t in reg]
        for t in reg:
            script = t["script"]

            def _cmd(v, s=script):
                cmd = [PY, s]
                if v.get("rounds"):
                    cmd.append(v["rounds"])
                for key, val in v.items():
                    if key == "rounds" or val == "":
                        continue
                    cmd += ["--" + key.replace("_", "-"), val]
                return cmd

            ops.append({
                "group": t.get("group", "录制任务"),
                "name": t.get("name", script),
                "desc": t.get("hint", ""),
                "fields": t.get("fields", []),
                "hint": t.get("hint", ""),
                "cmd": _cmd,
            })
        for c in combo.load(ROOT):
            nm = c.get("name") or "未命名组合"
            steps = c.get("steps") or []
            brief = " → ".join(
                f"{s.get('task')} {s.get('minutes')}分" for s in steps) or "（还没加步骤）"
            ops.append({
                "group": "组合",
                "kind": "combo",
                "combo": c,
                "name": nm,
                "desc": f"整条链：{brief}",
                "fields": [],
                "hint": "按顺序跑；每步跑满设定分钟、跑完当前一条或一轮自己退出，"
                        "步间清后台，由下一个任务自己拉起 App 并回到自己的起始页。",
                "cmd": (lambda v, n=nm: [PY, os.path.join(ROOT, "core", "combo.py"), n]),
            })
        self.all_ops = list(OPERATIONS) + ops

    def _edit_combos(self):
        ComboDialog(self.root, self._log, on_done=self._reload)

    def _reload(self):
        self._load_dynamic()
        self._fill_list()
        self._select(0)

    def _fill_list(self):
        self.listbox.delete(0, "end")
        self.row_of_op = {}
        self.op_of_row = {}
        last = None
        for i, op in enumerate(self.all_ops):
            if op["group"] != last:
                self.listbox.insert("end", f"  {op['group']}")
                row = self.listbox.size() - 1
                self.listbox.itemconfig(row, foreground="#7a7a7a")
                last = op["group"]
            self.listbox.insert("end", "      " + op["name"])
            row = self.listbox.size() - 1
            self.op_of_row[row] = i
            self.row_of_op[i] = row

    def _select(self, idx):
        self.current = idx
        op = self.all_ops[idx]
        row = self.row_of_op.get(idx)
        self.listbox.selection_clear(0, "end")
        if row is not None:
            self.listbox.selection_set(row)
            self.listbox.activate(row)
        self.desc.configure(text=op["desc"])
        self.hint.configure(text=op["hint"])

        for w in self.form.winfo_children():
            w.destroy()
        self.entries = {}
        if op.get("kind") == "combo":
            self._render_combo(op)
            return
        if not op["fields"]:
            ttk.Label(self.form, text="（这个任务不需要参数）", foreground="#999").grid(row=0, column=0, sticky="w")
            return
        mem = self.params.get(op["name"], {})
        for r, f in enumerate(op["fields"]):
            ttk.Label(self.form, text=f["label"]).grid(row=r, column=0, sticky="nw", pady=5)
            default = str(mem.get(f["key"], f.get("default", "")))
            if f.get("kind") == "time":
                e = TimeWheel(self.form, default)
                e.grid(row=r, column=1, sticky="w", padx=10, pady=6)
            elif f.get("kind") == "choice":
                labels = f.get("value_labels") or {}
                vals = f.get("values") or []
                e = ttk.Combobox(self.form, values=[labels.get(v, v) for v in vals],
                                 width=f.get("width", 12), state="readonly")
                e.set(labels.get(default, default))
                e.grid(row=r, column=1, sticky="w", padx=10, pady=5)
            else:
                e = ttk.Entry(self.form, width=f.get("width", 10))
                e.insert(0, default)
                e.grid(row=r, column=1, sticky="w", padx=10, pady=5)
            self.entries[f["key"]] = e
            btn = f.get("btn")
            if btn:
                ttk.Button(self.form, text=btn[0], width=14,
                           command=getattr(self, btn[1])).grid(row=r, column=2, padx=4)

    def _load_record(self):
        name = "script"
        if name not in self.entries:
            return
        if not os.path.isfile(RECORD_FILE):
            messagebox.showinfo("没有录制", "还没录过动作。先跑一次「动作录制（镜像点选）」。")
            return
        s = open(RECORD_FILE, encoding="utf-8").read().strip()
        if not s:
            messagebox.showinfo("内容为空", "上次录制是空的。")
            return
        self.entries[name].delete(0, "end")
        self.entries[name].insert(0, s)

    def _on_pick(self, _evt):
        sel = self.listbox.curselection()
        if not sel:
            return
        idx = self.op_of_row.get(sel[0])
        if idx is None:
            self.listbox.selection_clear(0, "end")
            row = self.row_of_op.get(self.current)
            if row is not None:
                self.listbox.selection_set(row)
            return
        self._select(idx)

    def _task_fields(self, name):
        """取某个任务的字段定义（组合里跳过轮次和 App，两者另有专位）。"""
        t = next((x for x in getattr(self, "tasks", []) if x.get("name") == name), {})
        return [f for f in (t.get("fields") or [])
                if f.get("key") not in ("rounds", "app")]

    def _grab_params(self, key):
        """把某一步当前控件里的值收成 params，空值代表走脚本默认。"""
        h = self.entries.get(key)
        if not h:
            return {}
        out = dict(h.get("src_params") or {})
        for k, w in (h.get("params") or {}).items():
            v = w.get().strip()
            back = getattr(w, "_back", None)
            if back:
                v = back.get(v, "")
            if v:
                out[k] = v
            else:
                out.pop(k, None)
        return out

    def _render_step_params(self, key):
        """按这一步选的任务重画参数行。"""
        h = self.entries[key]
        h["src_params"] = self._grab_params(key)
        box = h["pbox"]
        for w in box.winfo_children():
            w.destroy()
        h["params"] = {}
        fields = self._task_fields(h["task"].get())
        if not fields:
            ttk.Label(box, text="（这个任务没有其它参数）", foreground="#999").pack(anchor="w")
            return
        for f in fields:
            row = ttk.Frame(box)
            row.pack(fill="x", pady=1)
            ttk.Label(row, text=f.get("label", f.get("key", "")),
                      width=26, anchor="w").pack(side="left")
            cur = str(h["src_params"].get(f.get("key"), "") or "")
            if f.get("kind") == "choice":
                labels = f.get("value_labels") or {}
                vals = f.get("values") or []
                w = ttk.Combobox(row, values=[""] + [labels.get(v, v) for v in vals],
                                 width=14, state="readonly")
                w.set(labels.get(cur, cur) if cur else "")
                w._back = {labels.get(v, v): v for v in vals}
            else:
                w = ttk.Entry(row, width=max(14, int(f.get("width", 10)) + 4))
                w.insert(0, cur)
            w.pack(side="left", padx=(6, 0))
            h["params"][f.get("key")] = w

    def _render_combo(self, op):
        """主界面直接改组合每一步：任务 / 分钟 / App / 该任务的全部参数。"""
        self.entries = {}
        self.combo_steps = [dict(s) for s in (op.get("combo", {}).get("steps") or [])]
        if not self.combo_steps:
            ttk.Label(self.form, text="这条组合还没有步骤，点右上角「组合设置」加。",
                      foreground="#999").grid(row=0, column=0, sticky="w")
            return
        for i, s in enumerate(self.combo_steps, 1):
            box = ttk.LabelFrame(self.form, text=f"第 {i} 步", padding=(8, 6))
            box.grid(row=i - 1, column=0, sticky="ew", pady=3)
            top = ttk.Frame(box)
            top.pack(fill="x")
            ttk.Label(top, text="任务").pack(side="left")
            cb = ttk.Combobox(top, values=self.task_names, width=17, state="readonly")
            cb.set(s.get("task", ""))
            cb.pack(side="left", padx=(4, 10))
            ttk.Label(top, text="分钟").pack(side="left")
            mn = ttk.Entry(top, width=6)
            mn.insert(0, "" if s.get("minutes") in (None, "") else str(s.get("minutes")))
            mn.pack(side="left", padx=(4, 10))
            ttk.Label(top, text="App").pack(side="left")
            ap = ttk.Combobox(top, values=["nebula", "kuaishou"], width=9, state="readonly")
            ap.set(s.get("app") or "nebula")
            ap.pack(side="left", padx=(4, 0))
            pbox = ttk.Frame(box)
            pbox.pack(fill="x", pady=(5, 0))
            self.entries[str(i)] = {"task": cb, "minutes": mn, "app": ap,
                                    "pbox": pbox, "params": {},
                                    "src_params": dict(s.get("params") or {})}
            cb.bind("<<ComboboxSelected>>", lambda e, k=str(i): self._render_step_params(k))
            self._render_step_params(str(i))
        ttk.Label(self.form, foreground="#0a7a52",
                  text="参数留空就走脚本自带默认；顺序、增删步骤点右上角「组合设置」。"
                  ).grid(row=len(self.combo_steps), column=0, sticky="w", pady=(8, 0))

    def _save_combo_from_form(self, op):
        """把主界面上的改动写回 combos.json；有问题弹窗并返回 False。"""
        steps = []
        for i, s in enumerate(self.combo_steps, 1):
            h = self.entries[str(i)]
            raw = h["minutes"].get().strip()
            try:
                mins = float(raw)
            except ValueError:
                messagebox.showwarning("分钟不对", f"第 {i} 步的分钟要填数字。")
                return False
            if mins < 1:
                messagebox.showwarning("分钟太小", f"第 {i} 步至少 1 分钟，填 0 会进不到下一步。")
                return False
            params = self._grab_params(str(i))
            params.pop("app", None)          # App 走专位，别混进参数
            steps.append({"task": h["task"].get(), "minutes": mins,
                          "app": h["app"].get(), "params": params})
        cfg = {"name": op["name"], "steps": steps}
        err = combo.validate(ROOT, cfg)
        if err:
            messagebox.showwarning("组合配置有问题", err)
            return False
        combo.upsert(ROOT, cfg)
        op["combo"] = cfg
        self._log(f"组合已更新：{cfg['name']}（{len(steps)} 步）")
        return True

    def _values(self):
        op = self.all_ops[self.current]
        if op.get("kind") == "combo":
            return {}
        out = {}
        for f in op["fields"]:
            w = self.entries[f["key"]]
            v = w.get().strip()
            if f.get("kind") == "choice":
                labels = f.get("value_labels") or {}
                back = {labels.get(k, k): k for k in (f.get("values") or [])}
                v = back.get(v, v)
            out[f["key"]] = v
        return out

    def _start(self):
        if self.runner.running():
            messagebox.showinfo("提示", "还有任务在跑，先停止。")
            return
        op = self.all_ops[self.current]
        if op.get("kind") == "combo" and not self._save_combo_from_form(op):
            return
        vals = self._values()
        for f in op["fields"]:
            if not vals.get(f["key"]) and not f.get("optional"):
                messagebox.showwarning("参数不全", f"「{f['label']}」不能为空")
                return
        try:
            cmd = op["cmd"](vals)
        except Exception as e:
            messagebox.showerror("参数错误", str(e))
            return
        if op.get("kind") != "combo":
            self.params[op["name"]] = vals
            self._save_params()
        self._log(f"\n>>> {op['group']} / {op['name']}")
        self._log("    " + " ".join(str(c) for c in cmd[1:]))
        if self.runner.start(cmd):
            self.btn_start.configure(state="disabled")
            self.btn_stop.configure(state="normal")
            self.status.configure(text="运行中", foreground="#0a7a52")

    def _stop(self):
        self.runner.stop()
        self._log("—— 已请求停止 ——")
        self.btn_start.configure(state="normal")
        self.btn_stop.configure(state="disabled")
        self.status.configure(text="设备：未检查", foreground="#888")

    def _open_handoff(self):
        """拉起 AI 兜底（handoff 信号服务 :8210）；已在运行则只刷新状态。"""
        if handoff.available():
            self._log("AI 兜底服务已在运行（:8210）")
            self.status.configure(text="AI 兜底：运行中", foreground="#0a7a52")
            return
        self._log("\n>>> 启动 AI 兜底服务（handoff :8210）")
        creation = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            subprocess.Popen(
                [PY, os.path.join(ROOT, "core", "handoff_server.py")],
                cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=creation)
        except Exception as e:
            self._log(f"AI 兜底启动失败：{e}")
            messagebox.showerror("启动失败", str(e))
            return
        for _ in range(15):
            time.sleep(0.2)
            if handoff.available():
                self._log("AI 兜底服务已启动，unknown 页面将上报待办")
                self.status.configure(text="AI 兜底：运行中", foreground="#0a7a52")
                return
        self._log("AI 兜底启动超时；未连上时脚本遇 unknown 会降级兜底退出")
        self.status.configure(text="AI 兜底：未启动", foreground="#c0392b")

    def _check(self):
        if self.runner.running():
            messagebox.showinfo("提示", "任务运行中，先停止再检查。")
            return
        self._log("\n>>> 通用 / 检查手机连接")
        if self.runner.start([PY, WDAUP]):
            self.btn_start.configure(state="disabled")
            self.btn_stop.configure(state="normal")
            self.status.configure(text="检查中", foreground="#888")

    def _clear_bg(self):
        if self.runner.running():
            messagebox.showinfo("提示", "任务运行中，先停止再清理。")
            return
        self._log("\n>>> 通用 / 清空后台（保留 WDA）")
        if self.runner.start([PY, CLEAR_BG]):
            self.btn_start.configure(state="disabled")
            self.btn_stop.configure(state="normal")
            self.status.configure(text="清理中", foreground="#888")

    def _unlock_phone(self):
        if self.runner.running():
            messagebox.showinfo("提示", "任务运行中，先停止。")
            return
        pin = simpledialog.askstring("解锁手机", "输入锁屏密码：", show="*", parent=self.root)
        if pin is None:
            return
        pin = pin.strip()
        if not pin:
            messagebox.showwarning("提示", "密码为空")
            return
        os.environ["UNLOCK_PIN"] = pin
        self._log("\n>>> 通用 / 解锁手机")
        if self.runner.start([PY, UNLOCK_PHONE]):
            self.btn_start.configure(state="disabled")
            self.btn_stop.configure(state="normal")
            self.status.configure(text="解锁中", foreground="#888")
        os.environ.pop("UNLOCK_PIN", None)

    def _clear(self):
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    def _log(self, text):
        self.queue.put(text)

    def _drain(self):
        try:
            while True:
                line = self.queue.get_nowait()
                self.log.configure(state="normal")
                self.log.insert("end", line + "\n")
                self.log.see("end")
                self.log.configure(state="disabled")
                s = line.strip()
                if s == "OK":
                    self.status.configure(text="设备：在线", foreground="#0a7a52")
                elif s == "FAILED":
                    self.status.configure(text="设备：不在线", foreground="#c0392b")
                if "进程结束" in line:
                    self.btn_start.configure(state="normal")
                    self.btn_stop.configure(state="disabled")
        except queue.Empty:
            pass
        self.root.after(80, self._drain)


if __name__ == "__main__":
    root = tk.Tk()
    App(root)
    root.mainloop()
