"""苹果手机自动化 - 主文件（控制台界面）

左侧按任务文件夹分组列出自动化操作，右侧填参数，下方实时日志。
每一项都对应 tasks\\ 或 core\\ 下的真实脚本。

运行：python gui.py
"""
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import messagebox, scrolledtext, simpledialog, ttk

ROOT = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
sys.path.insert(0, os.path.join(ROOT, "core"))
import task_registry  # noqa: E402

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


class App:
    def __init__(self, root):
        self.root = root
        self.queue = queue.Queue()
        self.runner = Runner(self._log)
        self.entries = {}
        self.current = 0
        self.row_of_op = {}
        self.op_of_row = {}
        self.all_ops = []

        root.title("苹果手机自动化")
        root.geometry("1020x720")
        root.minsize(920, 640)

        self._load_dynamic()
        self._build()
        self._select(0)
        self.root.after(80, self._drain)

    def _build(self):
        top = ttk.Frame(self.root, padding=(14, 10, 14, 4))
        top.pack(fill="x")
        ttk.Label(top, text="苹果手机自动化", font=("Microsoft YaHei UI", 15, "bold")).pack(side="left")
        self.status = ttk.Label(top, text="设备：未检查", foreground="#888")
        self.status.pack(side="right", padx=(0, 10))
        ttk.Button(top, text="检查连接", command=self._check).pack(side="right", padx=6)
        ttk.Button(top, text="清空后台", command=self._clear_bg).pack(side="right", padx=6)
        ttk.Button(top, text="解锁手机", command=self._unlock_phone).pack(side="right", padx=6)

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

        bottom = ttk.Frame(self.root, padding=(14, 0, 14, 12))
        bottom.pack(fill="both", expand=True)

        bar = ttk.Frame(bottom)
        bar.pack(fill="x", pady=(4, 8))
        self.btn_start = ttk.Button(bar, text="开始执行", command=self._start, width=14)
        self.btn_start.pack(side="left")
        self.btn_stop = ttk.Button(bar, text="停止", command=self._stop, width=10, state="disabled")
        self.btn_stop.pack(side="left", padx=8)
        ttk.Button(bar, text="清空日志", command=self._clear, width=10).pack(side="left")

        self.log = scrolledtext.ScrolledText(bottom, height=13, font=("Consolas", 9),
                                             state="disabled", wrap="word")
        self.log.pack(fill="both", expand=True)

    def _load_dynamic(self):
        """合并内置任务与注册表登记的录制任务。"""
        ops = []
        for t in task_registry.load(ROOT):
            script = t["script"]

            def _cmd(v, s=script):
                cmd = [PY, s]
                if v.get("rounds"):
                    cmd.append(v["rounds"])
                return cmd

            ops.append({
                "group": t.get("group", "录制任务"),
                "name": t.get("name", script),
                "desc": t.get("hint", ""),
                "fields": t.get("fields", []),
                "hint": t.get("hint", ""),
                "cmd": _cmd,
            })
        self.all_ops = list(OPERATIONS) + ops

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
        if not op["fields"]:
            ttk.Label(self.form, text="（这个任务不需要参数）", foreground="#999").grid(row=0, column=0, sticky="w")
            return
        for r, f in enumerate(op["fields"]):
            ttk.Label(self.form, text=f["label"]).grid(row=r, column=0, sticky="nw", pady=5)
            if f.get("kind") == "time":
                e = TimeWheel(self.form, f.get("default", ""))
                e.grid(row=r, column=1, sticky="w", padx=10, pady=6)
            else:
                e = ttk.Entry(self.form, width=f.get("width", 10))
                e.insert(0, f["default"])
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

    def _values(self):
        op = self.all_ops[self.current]
        return {f["key"]: self.entries[f["key"]].get().strip() for f in op["fields"]}

    def _start(self):
        if self.runner.running():
            messagebox.showinfo("提示", "还有任务在跑，先停止。")
            return
        op = self.all_ops[self.current]
        vals = self._values()
        for f in op["fields"]:
            if not vals.get(f["key"]):
                messagebox.showwarning("参数不全", f"「{f['label']}」不能为空")
                return
        try:
            cmd = op["cmd"](vals)
        except Exception as e:
            messagebox.showerror("参数错误", str(e))
            return
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
