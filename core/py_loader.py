# -*- coding: utf-8 -*-
"""录制产物反向解析：把 flow_template 渲染出的独立 py 解析回编辑态数据。

与 flow_template 对称：模板负责 数据 → py 文本，本模块负责 py 文本 → 数据。
纯 AST / 文本解析，不 import 产物、不连 WDA、不落盘。

返回结构：
    {
      "name": str,
      "rounds": int,
      "issues": [ {name, keywords, region, points, max_rounds, note} ],
      "events": [ (kind, args_tuple, gap_ms) ],
    }

events 的 args 形态与 mirror 内部一致：
    tap:(x,y)  hold:(x,y,ms)  swipe:(x1,y1,x2,y2,ms)
    home:()  back:(x,y)  backswipe:()  text:(text,)
"""
import ast


def _const(node):
    """取 AST 常量字面量，非常量返回 None。"""
    if isinstance(node, ast.Constant):
        return node.value
    return None


def _call_parts(node):
    """调用节点拆成 (对象名, 方法名)，如 quick.tap2 -> ("quick", "tap2")。"""
    f = node.func if isinstance(node, ast.Call) else None
    if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name):
        return f.value.id, f.attr
    return None, None


def _norm_issue(raw):
    """规范化一条异常分支，缺字段补默认；非法返回 None。"""
    if not isinstance(raw, dict):
        return None
    name = str(raw.get("name") or "问题界面")

    kws = raw.get("keywords") or []
    keywords = [str(k).strip() for k in kws if str(k).strip()] \
        if isinstance(kws, list) else []

    region = raw.get("region")
    if not (isinstance(region, list) and len(region) == 4):
        region = None

    pts = raw.get("points") or []
    points = []
    for p in pts if isinstance(pts, list) else []:
        if not isinstance(p, dict):
            continue
        act = p.get("action") or "tap"
        pt = {"action": act, "label": str(p.get("label") or act)}
        if act == "tap":
            pt["x"] = int(p.get("x", 0))
            pt["y"] = int(p.get("y", 0))
        elif act == "wait":
            pt["ms"] = int(p.get("ms", 800))
        points.append(pt)

    try:
        rounds = max(1, int(float(raw.get("max_rounds", 3))))
    except Exception:
        rounds = 3
    return {"name": name, "keywords": keywords, "region": region,
            "points": points, "max_rounds": rounds, "note": str(raw.get("note") or "")}


def _parse_run_once(func, lines):
    """按语句顺序解析 run_once 里的主流程动作。"""
    events = []
    gap_ms = 0
    for node in func.body:
        if not isinstance(node, ast.Expr) or not isinstance(node.value, ast.Call):
            continue
        owner, attr = _call_parts(node.value)
        args = node.value.args

        if owner == "time" and attr == "sleep":
            v = _const(args[0]) if args else None
            if isinstance(v, (int, float)):
                gap_ms += int(round(v * 1000))
            continue
        if owner == "GUARD":          # 兜底检测钩子，不是主流程动作
            continue
        if owner != "quick":
            continue

        ev = None
        if attr == "tap2":
            x = _const(args[0]) if args else None
            y = _const(args[1]) if len(args) > 1 else None
            if x is None or y is None:
                continue
            # 模板里 back 分支行尾带「# 返回箭头」注释，AST 读不到，回源码行判断
            line = lines[node.lineno - 1] if 0 < node.lineno <= len(lines) else ""
            kind = "back" if "返回箭头" in line else "tap"
            ev = (kind, (int(x), int(y)), gap_ms)
        elif attr == "long_press":
            if len(args) < 3:
                continue
            x, y, ms = (_const(a) for a in args[:3])
            if None in (x, y, ms):
                continue
            ev = ("hold", (int(x), int(y), int(ms)), gap_ms)
        elif attr == "swipe2":
            if len(args) < 5:
                continue
            v = [_const(a) for a in args[:5]]
            if any(t is None for t in v):
                continue
            ev = ("swipe", (int(v[0]), int(v[1]), int(v[2]), int(v[3]), int(v[4])),
                  gap_ms)
        elif attr == "press_home":
            ev = ("home", (), gap_ms)
        elif attr == "go_back":
            ev = ("backswipe", (), gap_ms)
        elif attr == "keys":
            txt = _const(args[0]) if args else None
            ev = ("text", (str(txt),), gap_ms)
        elif attr == "open_app":
            bundle = _const(args[0]) if args else None
            if bundle is None:
                continue
            clean = False
            for kw in node.value.keywords:
                if kw.arg == "clean":
                    clean = bool(_const(kw.value))
            # 名称写在上一行「# [n] 打开App · 名称」注释里，向上找最近一条
            name = ""
            for back in range(node.lineno - 2, -1, -1):
                if 0 <= back < len(lines) and "·" in lines[back]:
                    name = lines[back].split("·", 1)[1].strip()
                    break
            ev = ("openapp", (str(bundle), name, clean), gap_ms)

        if ev:
            events.append(ev)
            gap_ms = 0
    return events


def parse_py_source(text):
    """解析录制产物 py 源码，返回编辑态数据结构。解析失败返回空骨架。"""
    result = {"name": "", "rounds": 10, "issues": [], "events": []}
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return result

    lines = text.splitlines()
    issues_raw = None
    func = None

    for node in tree.body:
        targets = []
        if isinstance(node, ast.Assign):
            targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            targets = [node.target.id]

        for t in targets:
            if t == "TASK_NAME":
                v = _const(node.value)
                if isinstance(v, str):
                    result["name"] = v
            elif t == "DEFAULT_ROUNDS":
                v = _const(node.value)
                if isinstance(v, (int, float)):
                    result["rounds"] = int(v)
            elif t == "ISSUES":
                try:
                    issues_raw = ast.literal_eval(node.value)
                except Exception:
                    issues_raw = None

        if isinstance(node, ast.FunctionDef) and node.name == "run_once":
            func = node

    if isinstance(issues_raw, list):
        result["issues"] = [i for i in (_norm_issue(x) for x in issues_raw) if i]
    if func is not None:
        result["events"] = _parse_run_once(func, lines)
    return result
