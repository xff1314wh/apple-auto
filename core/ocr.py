# -*- coding: utf-8 -*-
"""OCR 抽象层：后端可插拔，对外返回标准化识别结果。

解耦约定：
- 本层不认识业务关键词、不做异常判定、不调用 WDA；
- 新后端只要实现 ``recognize(image) -> raw`` 并注册进 ``_BACKENDS`` 即可；
- 对外只暴露 :class:`OcrItem`，坐标统一换算到手机逻辑坐标 414×896 points。

raw 结果约定（各后端自行适配成这个结构）：
    [ [四点框, 文字, 置信度], ... ]
"""
import io
from typing import List, Optional, Tuple

from PIL import Image

PHONE_W, PHONE_H = 414, 896


class OcrItem:
    """一条识别结果。center/box 均为 points 坐标。"""

    def __init__(self, text: str, center: Tuple[float, float],
                 box: List[List[float]], score: float):
        self.text = text
        self.center = center
        self.box = box
        self.score = score

    def __repr__(self):
        return f"OcrItem({self.text!r}, center={self.center}, score={self.score:.2f})"


class OcrBackend:
    """后端接口基类，子类实现 recognize。"""

    name = "base"

    def recognize(self, image):
        raise NotImplementedError


class RapidOcrBackend(OcrBackend):
    """rapidocr-onnxruntime（ONNX Runtime CPU）后端。引擎懒加载、单例复用。"""

    name = "rapid"

    def __init__(self):
        self._engine = None

    def _lazy(self):
        if self._engine is None:
            try:
                from rapidocr_onnxruntime import RapidOCR
            except ImportError as e:
                raise RuntimeError(
                    "未安装 OCR 后端 rapidocr-onnxruntime，"
                    "请执行 pip install rapidocr-onnxruntime==1.3.9"
                ) from e
            self._engine = RapidOCR()
        return self._engine

    def recognize(self, image):
        result, _ = self._lazy()(image)
        return result or []


_BACKENDS = {"rapid": RapidOcrBackend}
_backend_cache = {}


def _load_pil(image) -> Image.Image:
    if isinstance(image, str):
        return Image.open(image)
    if isinstance(image, Image.Image):
        return image
    if isinstance(image, (bytes, bytearray)):
        return Image.open(io.BytesIO(bytes(image)))
    # numpy ndarray：交给 PIL 兜底
    return Image.fromarray(image)


def _image_size(image) -> Tuple[int, int]:
    pil = _load_pil(image)
    return pil.size  # (w, h)


def _to_ndarray(image):
    import numpy as np
    return np.array(_load_pil(image))


def recognize(image, backend: str = "rapid") -> List[OcrItem]:
    """识别图片，返回标准化 OcrItem 列表。

    :param image: 截图字节 / PIL.Image / ndarray
    :param backend: 后端名，默认 "rapid"
    """
    if backend not in _BACKENDS:
        raise ValueError(f"未知 OCR 后端: {backend}，可选 {list(_BACKENDS)}")

    if backend not in _backend_cache:
        _backend_cache[backend] = _BACKENDS[backend]()
    raw = _backend_cache[backend].recognize(_to_ndarray(image))

    img_w, img_h = _image_size(image)
    if img_w == 0 or img_h == 0:
        return []

    items: List[OcrItem] = []
    for entry in raw:
        if not entry or len(entry) < 2:
            continue
        box, text, score = entry[0], entry[1], entry[2] if len(entry) > 2 else 1.0
        xs = [p[0] for p in box]
        ys = [p[1] for p in box]
        cx = sum(xs) / len(xs) / img_w * PHONE_W
        cy = sum(ys) / len(ys) / img_h * PHONE_H
        pt_box = [[x / img_w * PHONE_W, y / img_h * PHONE_H] for x, y in box]
        items.append(OcrItem(str(text).strip(), (cx, cy), pt_box, float(score)))
    return items


def available(backend: str = "rapid") -> bool:
    """后端是否可用（不触发引擎加载，仅探测依赖是否安装）。"""
    if backend == "rapid":
        try:
            import rapidocr_onnxruntime  # noqa: F401
            return True
        except ImportError:
            return False
    return False
