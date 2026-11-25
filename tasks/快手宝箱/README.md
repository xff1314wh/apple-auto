# 快手宝箱自动领取

按素材坐标顺序回放一轮操作，每步之后用 OCR 检测异常弹窗并自动处置；识别不到不打断主流程。

## 跑法

```bash
python tasks\快手宝箱\chest_flow.py        # 跑 10 轮，Ctrl+C 可随时停
python tasks\快手宝箱\chest_flow.py 20     # 跑 20 轮自动退出
python tasks\快手宝箱\chest_flow.py 0      # 一直跑
```

也可以在 `gui.py` 里选「快手宝箱 · 自动领取金币」，填轮数后点开始。

## 结构

- **主流程**：素材里标注的坐标顺序回放（`core/quick.py` 注入触摸）。
- **兜底**：每步后 `core/guard.py` 截图 → `core/ocr.py` 识别 → `core/issue_checker.py` 判定，命中问题分支才按配置点关闭点；同一分支有轮次上限，防死循环。
- **问题分支配置**：写在素材 json 的 `issues` 字段——触发关键词、触发范围、关闭点、轮次上限。

## 前提

- 手机停在快手的视频/广告页
- WDA 已就绪（先跑 `python tasks\通用\wda_up.py`）

## 日志

写到 `data\logs\ks_flow.log`。
