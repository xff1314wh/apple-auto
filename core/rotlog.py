# -*- coding: utf-8 -*-
"""轻量文件日志：追加一行，文件超上限时截断保留尾部，防止挂机无限增长。

解耦约定：
- 只做"追加 + 大小保护"，不绑定任何任务 / GUI；
- 读写异常一律静默跳过，日志不能反过来打断主流程；
- 阈值按字节给默认值，调用方可覆盖。
"""
import os


def append_line(path, line, max_bytes=1_000_000, keep_bytes=200_000):
    """向 path 追加一行；超过 max_bytes 则保留尾部 keep_bytes 后再写。

    :param path: 日志文件绝对路径
    :param line: 单行文本（不含换行）
    :param max_bytes: 触发截断的文件大小上限（默认约 1 MB）
    :param keep_bytes: 截断后保留的尾部字节数（默认约 200 KB）
    """
    try:
        folder = os.path.dirname(path)
        if folder:
            os.makedirs(folder, exist_ok=True)
        size = os.path.getsize(path) if os.path.isfile(path) else 0
        if size > max_bytes:
            with open(path, "rb") as f:
                f.seek(max(0, size - keep_bytes))
                tail = f.read()
            with open(path, "wb") as f:
                f.write(tail)
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass
