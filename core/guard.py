# -*- coding: utf-8 -*-
"""异常兜底编排：截图 → OCR 识别 → 判定命中 → 按配置处置 → 复核。

分层职责：
- :mod:`core.ocr` 只负责把图片识别成标准化 OcrItem；
- :mod:`core.issue_checker` 只负责纯逻辑判定；
- 本层只负责编排"何时检测、命中后怎么处置、处置后是否消失"，不写主流程坐标。

依赖注入：构造时传入 quick 模块、ocr 模块、checker 模块，便于离线测试替换。
OCR 不可用或识别异常时，一律按"无异常"处理，不中断主流程。

处置点 point 结构：
    {"action": "tap|back|home|wait", "x", "y", "ms", "label"}
    - tap（默认）：点击 x,y
    - back：边缘手势返回，不需要坐标
    - home：回主屏，不需要坐标
    - wait：等待 ms 毫秒
"""
import time


class IssueGuard:
    def __init__(self, issues, quick, ocr_mod=None, checker_mod=None,
                 post_action_sleep=0.8, recheck_gap=0.6, log=None):
        self.issues = issues or []
        self.quick = quick
        self.ocr = ocr_mod
        self.checker = checker_mod
        self.post_action_sleep = post_action_sleep
        self.recheck_gap = recheck_gap
        self._counts = {}
        self._log = log or (lambda m: print(m, flush=True))

    def _recognize(self):
        if self.ocr is None:
            return []
        try:
            data, err = self.quick.shot_bytes()
            if err or not data:
                return []
            return self.ocr.recognize(data)
        except Exception:
            return []

    def _dispose_one(self, p):
        """按处置点动作类型分派到 quick 接口。"""
        act = (p.get("action") or "tap").lower()
        if act == "back":
            self.quick.go_back()
        elif act == "home":
            self.quick.press_home()
        elif act == "wait":
            time.sleep(int(p.get("ms", 800)) / 1000)
        else:
            self.quick.tap2(int(p["x"]), int(p["y"]))
        time.sleep(self.post_action_sleep)

    def _dispose(self, iss):
        for p in iss.get("points", []):
            label = p.get("label", "点击点")
            act = (p.get("action") or "tap").lower()
            self._log(f"  处置[{iss.get('name', '问题界面')}] {label}（{act}）")
            self._dispose_one(p)

    def _match(self, items):
        if self.checker is None:
            return None, None
        return self.checker.match_first(self.issues, items)

    def check_and_handle(self):
        """检测一次：命中则处置并复核；返回本轮是否命中过分支。"""
        if not self.issues:
            return False
        items = self._recognize()
        iss, hit = self._match(items)
        if not iss:
            return False

        name = iss.get("name", "问题界面")
        max_rounds = int(iss.get("max_rounds", 3))
        self._counts[name] = self._counts.get(name, 0) + 1

        if self._counts[name] > max_rounds:
            self._log(f"  异常[{name}]已达上限 {max_rounds} 轮，不再自动处置")
            return True

        hit_text = getattr(hit, "text", "")
        self._log(f"  命中异常[{name}] 文字={hit_text!r}，开始处置")
        self._dispose(iss)

        # 复核闭环：仍命中同一分支且未超上限，就再处置一轮；消失则返回主流程
        while self._counts[name] < max_rounds:
            time.sleep(self.recheck_gap)
            iss2, hit2 = self._match(self._recognize())
            if not iss2 or iss2.get("name") != name:
                self._log(f"  异常[{name}]已消失，返回主流程")
                break
            self._counts[name] += 1
            if self._counts[name] > max_rounds:
                self._log(f"  异常[{name}]已达上限 {max_rounds} 轮")
                break
            self._log(f"  异常[{name}]仍在，第 {self._counts[name]} 轮处置")
            self._dispose(iss2)
        return True
