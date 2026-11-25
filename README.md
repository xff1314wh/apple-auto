# 苹果手机自动化

Windows 上通过 WDA 直接控制 iPhone：界面点一下、脚本跑一遍，动作落在真机上。

## 目录

```
苹果手机自动化/
├── gui.py                    主文件 / 唯一入口
├── core/
│   ├── quick.py              共享底层：WDA 会话、点击、滑动、长按、键入、截图
│   ├── ocr.py                OCR 抽象层（后端可插拔，结果标准化）
│   ├── issue_checker.py      异常分支纯判定（关键词 + 区域）
│   └── guard.py              步后兜底编排：检测 → 处置 → 轮次上限
├── tasks/
│   ├── 通用/
│   │   ├── wda_up.py         检查手机连接（设备 / 转发 / runner 自愈）
│   │   ├── shot.py           截图看当前画面
│   │   ├── mirror.py         动作录制（镜像点选）
│   │   └── fasttap.py        连点流水线（单点连击也调它）
│   ├── 快手宝箱/
│   │   └── chest_flow.py     坐标回放 + OCR 兜底
│   └── 大麦抢票/
│       └── README.md         抢票流程说明（脚本复用 tasks/通用/fasttap.py）
├── data/                     运行产物：截图、日志、WDA session、录制的脚本
├── assets/                   重装用的 ipa / runner / ios.exe
└── setup/                    安装包与安装脚本
```

一个任务 = `tasks\` 下的一行 + `gui.py` 的 `OPERATIONS` 里一条。加新任务就建文件夹、放脚本和 README，再加一条配置。

## 界面上的任务

| 分组 | 任务 | 实际跑的脚本 |
|---|---|---|
| 通用 | 检查手机连接 | `tasks\通用\wda_up.py` |
| 通用 | 截图看当前画面 | `tasks\通用\shot.py` |
| 通用 | 动作录制（镜像点选） | `tasks\通用\mirror.py` |
| 通用 | 连点流水线 | `tasks\通用\fasttap.py --script` |
| 快手宝箱 | 自动领取金币 | `tasks\快手宝箱\chest_flow.py` |
| 大麦抢票 | 单点连击 | `tasks\通用\fasttap.py` |

## 脚本串格式

连点流水线、单点连击、录制回放都用同一套，段与段之间用分号 `;`。

| 段 | 含义 |
|---|---|
| `x,y,间隔ms,次数` | 点 (x,y)，每次隔"间隔"，点"次数"下（次数 0 = 一直点） |
| `wait:ms` | 干等，比如 `wait:1500` 停 1.5 秒等页面加载 |
| `hold:x,y,ms` | 长按 (x,y) 持续 ms 毫秒 |
| `swipe:x1,y1,x2,y2,ms` | 从 (x1,y1) 滑到 (x2,y2)，用时 ms |
| `text:文字` | 键入文字（手机焦点要已在输入框）；含分号/换行会自动转 `text64:` |
| `back:x,y` | 返回上一页：点左上角返回箭头，坐标可省（默认 34,70） |
| `backswipe:` | 返回上一页：左边缘分步右滑手势（部分 App 不吃，如淘宝登录页） |
| `home:` | 回主屏（按 Home 键） |

例子：预约页 → 票档 → 确认，三次点击串起来

```
207,800,120,3; wait:2000; 207,600,120,5; wait:1500; 207,700,120,3
```

## 坐标怎么量

`python tasks\通用\shot.py` 截图存在 `data\shots\now.png`，是 828 × 1792 像素，**像素 ÷ 2 = 点击坐标**。

## 常见问题

- **设备不在线**：先插拔线 / 换 USB 口 / 换线，再跑「检查手机连接」，还不行加 `--force`。
- **所有操作突然全失败**：八成是 WDA 连接断了（数据线接触不良），不是功能坏了，先查连接。
- **录制画面抖**：多线程抓帧返回乱序，已按时间戳单调过滤 + 固定节拍贴图解决，状态栏「丢」为 0 表示顺序正常。

## 卸载清单

本项目所有东西都能卸干净：

1. `pip uninstall pymobiledevice3`
2. 卸载 Sideloadly
3. 删 `C:\Program Files\Common Files\Apple\Apple Application Support`
4. 清环境变量 `HTTP_PROXY` / `HTTPS_PROXY`，关掉系统代理
5. 手机上删掉 WDA app
6. 删掉 `E:\苹果手机自动化` 整个目录
