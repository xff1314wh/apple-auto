"""WDA 自愈工具 v2：确保设备在线、端口转发活着、runner 在跑。

相比 v1 的改进：
  - 检测设备时重试 3 次（接触不良时 USB 会短暂消失，重试可躲过抖动）
  - 自动清理失效的转发进程（进程还在但 8100 不监听，是最常见的"连不上"元凶）
  - 报错时说明具体卡在哪一步

用法：
  python wda_up.py           检查并按需拉起
  python wda_up.py --force   强制重启 runner
"""
import os
import subprocess
import sys
import time
import urllib.request

UDID = "00008030-001E44DE02D0802E"
XCTRUNNER = "com.facebook.WebDriverAgentRunner.xctrunner.G8Y52ZZLMM"
BASE = "http://127.0.0.1:8100"
PY = sys.executable


def _run(cmd, timeout=30):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                              encoding="utf-8", errors="replace").stdout or ""
    except Exception as e:
        return f"__ERR__ {type(e).__name__}: {e}"


def _opener():
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def wda_ok(timeout=4):
    try:
        with _opener().open(BASE + "/status", timeout=timeout) as r:
            body = r.read().decode("utf-8", "replace")
            return '"state" : "success"' in body or '"state":"success"' in body
    except Exception:
        return False


def usbmux_has_device():
    out = _run([PY, "-m", "pymobiledevice3", "usbmux", "list"], timeout=25)
    if "__ERR__" in out:
        return None, out
    return (UDID in out), out


def device_online(retries=3, delay=1.0, verbose=True):
    """接触不良时 USB 会短暂消失，重试几次再下结论"""
    last = ""
    for i in range(retries):
        found, raw = usbmux_has_device()
        if found:
            return True, "OK"
        last = raw
        if i < retries - 1:
            if verbose:
                print(f"[wda] 未发现设备，重试 {i + 1}/{retries - 1} ...", flush=True)
            time.sleep(delay)
    return False, last


def port_pid():
    out = _run(["netstat", "-ano"], timeout=15)
    for line in out.splitlines():
        if "127.0.0.1:8100" in line and "LISTENING" in line:
            parts = line.split()
            if parts:
                try:
                    return int(parts[-1])
                except ValueError:
                    return None
    return None


def kill_pid(pid):
    _run(["taskkill", "/PID", str(pid), "/F"], timeout=10)


def ensure_forward():
    """保证 8100 有人监听；僵死的转发进程先杀掉再重起"""
    pid = port_pid()
    if pid:
        # 端口在监听，但可能是失效的旧进程：交给调用方用 wda_ok 判定，这里先不清
        return True, f"已监听 (pid {pid})"
    # 清理所有残留的 forward 进程
    out = _run([PY, "-c",
                "import subprocess;"
                "print(subprocess.run(['wmic','process','where',\"name='python.exe'\","
                "'get','ProcessId,CommandLine'],capture_output=True,text=True).stdout)"], timeout=30)
    for line in out.splitlines():
        if "usbmux" in line and "forward" in line:
            for token in line.split():
                if token.isdigit():
                    kill_pid(token)
                    break
    subprocess.Popen(
        [PY, "-m", "pymobiledevice3", "usbmux", "forward",
         "8100", "8100", f"--serial={UDID}"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    for _ in range(24):
        time.sleep(0.5)
        if port_pid():
            return True, "新建转发成功"
    return False, "转发建不起来（端口被占或设备不通）"


def launch_runner():
    _run([PY, "-m", "pymobiledevice3", "developer", "dvt",
          "launch", XCTRUNNER, "--udid", UDID], timeout=60)


def ensure(force=False, verbose=True):
    def say(m):
        if verbose:
            print(m, flush=True)

    ok, detail = device_online(verbose=verbose)
    if not ok:
        say("[wda] 设备不在线：插着线但 usbmux 看不到，通常就是线/口接触问题")
        say("      建议：重新插一下，或换一个 USB 口 / 换根线")
        return False

    if wda_ok() and not force:
        say("[wda] 已就绪")
        return True

    # 端口不通或 WDA 无响应：先清掉可能僵死的转发
    pid = port_pid()
    if pid:
        say(f"[wda] 8100 由 pid {pid} 占用但没响应，清理后重起")
        kill_pid(pid)
        time.sleep(0.8)

    ok, detail = ensure_forward()
    say(f"[wda] 转发：{detail}")
    if not ok:
        return False

    say("[wda] 启动 runner ...")
    launch_runner()
    for i in range(14):
        time.sleep(1)
        if wda_ok(timeout=3):
            say(f"[wda] 就绪（等待 {i + 1}s）")
            return True
    say("[wda] runner 起来了但 WDA 没响应，可能是 WDA 卡住，再试一次 --force")
    return False


if __name__ == "__main__":
    force = "--force" in sys.argv
    ok = ensure(force=force)
    print("OK" if ok else "FAILED")
    sys.exit(0 if ok else 1)
