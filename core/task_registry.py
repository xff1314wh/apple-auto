# -*- coding: utf-8 -*-
"""动态任务注册表：录制生成的 py 任务在这里登记，gui 启动时合并展示。

解耦约定：
- 只负责 tasks.json 的读写与去重，不碰 GUI / WDA / OCR；
- root（仓库根目录）由调用方传入，模块不硬编码绝对路径，便于离线测试；
- 文件缺失或损坏时按空列表处理，不抛异常中断主界面。

任务条目结构：
    {
        "name": "任务名",
        "group": "录制任务",
        "script": "tasks/录制任务/xxx.py",   # 相对仓库根
        "fields": [{"key": "rounds", "label": "...", "default": "10", "width": 8}],
        "hint": "提示文案",
        "source": "record"
    }
"""
import json
import os

REG_NAME = "tasks.json"


def _path(root):
    return os.path.join(root, "data", REG_NAME)


def load(root):
    """读取动态任务列表；文件缺失 / 损坏返回 []。"""
    p = _path(root)
    if not os.path.isfile(p):
        return []
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
        tasks = data.get("tasks", []) if isinstance(data, dict) else []
        return [t for t in tasks if isinstance(t, dict) and t.get("script")]
    except Exception:
        return []


def save(root, tasks):
    """整体写回注册表。"""
    os.makedirs(os.path.join(root, "data"), exist_ok=True)
    with open(_path(root), "w", encoding="utf-8") as f:
        json.dump({"tasks": tasks}, f, ensure_ascii=False, indent=2)


def upsert(root, task):
    """按 name 去重：同名覆盖，否则追加。返回更新后的列表。"""
    tasks = load(root)
    name = task.get("name") or task.get("script")
    replaced = False
    for i, t in enumerate(tasks):
        if t.get("name") == name:
            tasks[i] = task
            replaced = True
            break
    if not replaced:
        tasks.append(task)
    save(root, tasks)
    return tasks


def remove(root, name):
    """按 name 删除，返回剩余列表。"""
    tasks = [t for t in load(root) if t.get("name") != name]
    save(root, tasks)
    return tasks
