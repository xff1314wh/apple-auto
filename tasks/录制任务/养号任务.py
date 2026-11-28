# -*- coding: utf-8 -*-
"""自动化流程：快手极速版「定向垂直养号」

方向：不泛刷娱乐流，而是定向搜索教育招生 / B2B 采购货源 / 本地生活服务三类行业词，
进同类视频做浏览完播，并按概率点赞、概率评论，把账号兴趣标签压向目标行业。
关注 / 收藏 / 留资默认关闭（预留开关，不在本脚本内执行）。

流程（一轮）：
  发现页 → 随机抽行业词搜索 → 结果页逐条进播放页 → 完播浏览 → 概率点赞 → 概率评论
  → 返回结果页 → 下一张；一轮结束回到发现页。

坐标按 iPhone 11 逻辑分辨率 414×896 标定；换机型需重新标定。
本脚本不接 handoff 待办。
"""
import os
import random
import re
import sys
import threading
import time

import http.client
import io
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "core"))

import quick
import ocr as ocr_mod
import issue_checker as checker_mod
from guard import IssueGuard

# ============ 参数设定（顶部直接改，也可由 GUI 命令行覆盖） ============
DEFAULT_ROUNDS = 10          # 跑多少个搜索词后停（0=一直跑）
TASK_NAME = "定向养号"
# 普通快手 com.jiangjia.gif / 极速版 com.kuaishou.nebula 界面一致、坐标通用
APP_BUNDLES = {"kuaishou": "com.jiangjia.gif", "nebula": "com.kuaishou.nebula"}
KUAISHOU_BUNDLE = APP_BUNDLES["nebula"]   # 默认极速版，--app 可覆盖

# —— 互动开关 ——
ENABLE_LIKE = True           # 是否参与点赞
ENABLE_COMMENT = True        # 是否参与评论

# —— 概率事件（0~1，越大越容易触发）——
LIKE_PROB = 0.6              # 每条视频点赞概率
COMMENT_PROB = 0.25          # 每条视频评论概率

# —— 节奏（秒）——
POST_OPEN = 0.30             # 进页面后的起稳等待
TAP_GAP = 0.10               # 单次点击后最小停顿
ROUND_GAP = 0.5              # 两个搜索词之间间隔
FALLBACK_HOLD = 30.0        # 读不到时间码（字幕遮挡/无进度条）时统一停留秒数
MIN_HOLD = 8.0               # 读到时长但已接近尾声时的最小停留秒数
SWIPE_VIDEOS = 3             # 每个搜索词在播放页上滑看几条
MINUTES = 0.0                # >0 时按时间跑（轮次不参与），到点跑完当前一条退出
# —— 完播：进视频 3 秒后按住进度条读时间码 ——
READ_DELAY = 3.0            # 切条/进页后等待几秒再读时间码
DRAG_Y_NORMAL = 832         # 普通视频进度条纵坐标（备用；普通流长按易弹操作菜单，暂不启用读码）
DRAG_Y_AD = 826             # 广告视频进度条纵坐标（底部“相关搜索”占位，进度条略低）
TIMECODE_ROI = (125, 710, 290, 735)  # 时间码浮层区（逻辑坐标，中心 y≈725；广告/普通一致）

# —— 搜索词：GUI 一行输入（逗号/空格分隔）覆盖；留空用默认词表 ——
KEYWORDS = []
DEFAULT_KEYWORDS = [
    "知了爱学", "教育培训", "学历提升", "高考志愿填报", "培训机构", "课程推荐",
    "爱采购", "厂家货源", "批发采购", "源头工厂", "供应链批发", "一件代发",
    "装修公司", "口腔门诊", "加盟代理", "创业项目", "招商加盟", "本地服务",
]

# 评论模板池：模拟普通网友随口留言，短句口语、轻共鸣或轻提问；不留电话 / 微信，不带销售腔
COMMENT_POOL = [
    "哈哈确实是这样", "说的没毛病", "有点东西", "看完感觉懂了", "终于说明白了",
    "这点我深有体会", "可不嘛，就是这样", "说得挺实在", "有道理啊", "被你说中了",
    "我之前也是这样", "看完踏实多了", "这话在理", "确实如此", "算是听明白了",
    "讲得挺通透", "同感同感", "这个说到点子上了", "不像别的讲得云里雾里", "看完心里有数了",
    "这个还能报名吗", "大概得花多少啊", "新手能做吗", "具体在哪儿啊", "怎么联系上的",
    "有线下的点吗", "这个需要啥基础", "多少钱一节课呀", "外地能弄不", "能先看看样品吗",
    "拿货怎么拿", "有报价没", "多久能见效", "这个靠谱吗", "包教包会吗",
    "现在入手晚不晚", "有没有体验课", "适合多大年纪", "一个人能做不", "后期还要收费吗",
    "我之前踩过这个坑", "跟我情况差不多", "我也在了解这块", "看完想去试试", "家里正好在找这个",
    "身边有人做过，说还行", "观望好久了", "我就是零基础来的", "前阵子刚了解过同类", "本来犹豫，看完有点心动",
    "收藏慢慢研究", "先码住，回头细看", "这个对我正好有用", "之前一直没搞明白", "记下来了",
    "我朋友也在问这个", "正好刷到了", "最近正愁找不到路子", "看完决定去了解下", "这种确实省心",
    "前排围观", "哈哈哈哈", "懂的都懂", "蹲一个后续", "有懂行的来说说吗",
    "这波我先关注了", "确实卷啊", "真香", "听完一整个醍醐灌顶", "学到学到",
    "好家伙", "属实没想到", "涨知识了", "评论区有没有同路人", "先赞为敬",
    "这思路可以", "听完不慌了", "有点意思", "原来还能这样", "受教了受教了",
    "学历这块确实得抓紧", "找机构最怕踩坑", "货源直供确实省心", "工厂价拿样才划算", "批发还是得找源头",
    "加盟水挺深的，得多看", "创业不容易啊", "本地服务方便多了", "口腔这种还是得线下看", "装修确实要多对比",
    "课程适不适合还得试听", "资质这块得查清楚", "售后跟不上最麻烦", "一件代发压力小", "起步阶段投入别太大",
    "多问几家不吃亏", "能实地看看最好", "口碑比宣传重要", "这种模式看着还行", "有做过的出来说说吗",
    "主播早上好", "支持", "不错不错", "不错", "给力", "太好了", "专业", "项目多",
    "我以为是游戏视频", "喜欢这个视频，能了解很多内容", "看起来真不错", "方便实用的平台",
    "没有中间商赚差价", "收到谢谢老师", "好物好平台",
]

# ============ 标定坐标（414×896） ============
S_ICON = (380, 55)            # 发现页右上搜索图标
S_BOX = (250, 60)             # 搜索框
S_BTN = (380, 60)             # 键盘上方「搜索」按钮
TAB_VIDEO = (106, 114)        # 搜索结果「视频」tab（纯封面网格）
FIRST_CARD = (110, 330)       # 视频tab首条封面，点进播放页
# 播放页互动栏
C_BACK = (27, 60)             # 左上返回
C_LIKE = (382, 442)           # 点赞爱心
C_COMMENT = (382, 500)        # 评论气泡
C_COMMENT_INPUT = (207, 840)  # 评论面板底部输入框
C_COMMENT_CLOSE = (382, 280)  # 评论面板右上 ×
C_COMMENT_SEND = (355, 830)   # 评论发送按钮（键盘右下，红色发送）

# ============ 异常弹窗兜底配置（GUARD） ============
ISSUES = [
    {"name": "定位授权弹窗", "keywords": ["想使用你当前的位置", "精确位置", "允许定位"],
     "points": [{"action": "tap", "x": 140, "y": 517, "label": "不允许"}],
     "max_rounds": 2, "note": "定位授权，点不允许拒绝"},
    {"name": "离开挽留弹窗", "keywords": ["继续浏览", "查看课程详情"],
     "points": [{"action": "tap", "x": 137, "y": 612, "label": "继续浏览"}],
     "max_rounds": 2, "note": "挽留卡，点继续浏览关闭"},
    {"name": "二次确认框", "keywords": ["确定放弃", "确认退出"],
     "points": [{"action": "tap", "x": 137, "y": 612, "label": "继续浏览"}],
     "max_rounds": 2, "note": "二次确认，继续浏览关闭"},
    {"name": "面容登录推荐", "keywords": ["面容登录", "登录成功"],
     "points": [{"action": "tap", "x": 380, "y": 62, "label": "关闭"}],
     "max_rounds": 2, "note": "登录后推荐开通面容，点右上关闭"},
    {"name": "红包福利弹窗", "keywords": ["爆红包", "红包可使用", "去使用", "限时免费领取"],
     "points": [{"action": "tap", "x": 207, "y": 685, "label": "关闭"}],
     "max_rounds": 2, "note": "第三方红包福利卡，点底部 × 关闭"},
]
GUARD = IssueGuard(ISSUES, quick, ocr_mod=ocr_mod, checker_mod=checker_mod,
                   post_action_sleep=0.1, recheck_gap=0.1)

# ============ 辅助 ============
_seen = set()  # 本轮已互动过的视频指纹（去重，避免重复赞 / 评同一条）


# 日志级别归类：ERR 失败 > WARN 异常但继续 > PAGE 页面判定 > ACT 动作 > OK 完成
_LOG_RULES = (
    ("ERR", ("失败", "异常", "错误", "不可达", "抓不到", "没生效", "不可用", "超时")),
    ("WARN", ("未找到", "没有", "为空", "跳过", "放弃", "无进展", "仍是", "过头",
              "未确认", "不够", "超过", "降级", "未回")),
    ("PAGE", ("判为", "类型", "页面", "落地", "层级", "判成")),
    ("ACT", ("已点", "已填", "已勾", "已滑", "已清理", "已回", "点击", "随机点",
             "滑动", "填入", "退回", "发送", "点开", "点选")),
    ("OK", ("完成", "结束", "成功", "跑满")),
)


def log(msg, tag=None):
    """统一日志行：``[时:分:秒] [级别] 正文``，缩进按 2 空格层级对齐。

    级别按内容自动归类，也可显式传 ERR / WARN / PAGE / ACT / OK / SEP / INFO。
    """
    txt = str(msg)
    if tag is None:
        if txt.strip().startswith(("---", "===", ">>>")):
            tag = "SEP"
        else:
            tag = next((t for t, words in _LOG_RULES if any(w in txt for w in words)), "INFO")
    depth = max(0, (len(txt) - len(txt.lstrip(" "))) // 2)
    print(f"[{time.strftime('%H:%M:%S')}] [{tag:<4}] {'  ' * depth}{txt.strip()}", flush=True)


def shot_items():
    """截图并识别，返回 OcrItem 列表；失败返回空列表。"""
    data, err = quick.shot_bytes()
    if err or not data:
        return []
    try:
        return ocr_mod.recognize(data)
    except Exception as e:
        log(f"OCR 异常：{type(e).__name__}: {str(e)[:60]}")
        return []


# ---- MJPEG 后台抓帧：时间浮层只在按住期间短暂存在，必须从屏幕流里抓帧，松手后单张截图抓不到 ----
_MJ_HOST = "127.0.0.1"
_MJ_FRAMES = []
_MJ_LOCK = threading.Lock()
_MJ_STOP = False
_MJ_BOUNDARY = b"--BoundaryString"
_MJ_LEN_RE = re.compile(r"Content-Length:\s*(\d+)", re.I)


def _mj_pop(buf, boundary):
    bi = buf.find(boundary)
    if bi < 0:
        return None, buf
    he = buf.find(b"\r\n\r\n", bi)
    if he < 0:
        return None, buf
    head = buf[bi:he].decode("latin1", "ignore")
    m = _MJ_LEN_RE.search(head)
    if not m:
        return None, buf[bi + len(boundary):]
    n = int(m.group(1))
    js, je = he + 4, he + 4 + n
    if len(buf) < je:
        return None, buf
    return buf[js:je], buf[je:]


def _mj_pump():
    conn = resp = None
    buf = b""
    boundary = _MJ_BOUNDARY
    while not _MJ_STOP:
        try:
            if conn is None:
                quick.ensure_mjpeg_forward()
                conn = http.client.HTTPConnection(_MJ_HOST, quick.MJPEG_PORT, timeout=10)
                conn.request("GET", "/")
                resp = conn.getresponse()
                if resp.status != 200:
                    resp.read(); conn.close(); conn = None; time.sleep(0.5); continue
                ct = resp.headers.get("Content-Type", "")
                boundary = ct.split("boundary=")[-1].strip().encode("latin1") if "boundary=" in ct else _MJ_BOUNDARY
                buf = b""
            chunk = resp.read(65536)
            if not chunk:
                raise EOFError("closed")
            buf += chunk
            jpeg, buf = _mj_pop(buf, boundary)
            if jpeg and jpeg.startswith(b"\xff\xd8"):
                with _MJ_LOCK:
                    _MJ_FRAMES.append((time.time(), jpeg))
                    if len(_MJ_FRAMES) > 30:
                        _MJ_FRAMES.pop(0)
        except Exception:
            try:
                if conn: conn.close()
            except Exception:
                pass
            conn = resp = None; time.sleep(0.3)


def _mj_start():
    threading.Thread(target=_mj_pump, daemon=True).start()


def _is_ad(items):
    """广告页判据：底部带「相关搜索 · xxx」行，或画面出现独立的「广告」小标签。"""
    text = " ".join(getattr(it, "text", "") or "" for it in items)
    if "相关搜索" in text:
        return True
    return any((getattr(it, "text", "") or "").strip() in ("广告", "赞助", "推广")
               for it in items)


def _seek_drag(drag_y):
    # 单请求完整手势：压住进度条→短停→极小右移→停留调出浮层→松开。
    acts = [{"type": "pointer", "id": "finger1", "parameters": {"pointerType": "touch"},
             "actions": [
                 {"type": "pointerMove", "duration": 0, "x": 207, "y": drag_y},
                 {"type": "pointerDown", "button": 0},
                 {"type": "pause", "duration": 200},
                 {"type": "pointerMove", "duration": 120, "x": 227, "y": drag_y},
                 {"type": "pause", "duration": 600},
                 {"type": "pointerUp", "button": 0}]}]
    quick._actions(acts)


def _parse_timecode(jpeg):
    # 整帧 OCR，只取浮层纵向区间内的 mm:ss；返回 (当前秒, 总时长秒)，读不到 None。
    try:
        items = ocr_mod.recognize(jpeg)
        y0, y1 = TIMECODE_ROI[1], TIMECODE_ROI[3]
        texts = [it.text or "" for it in items if y0 - 14 <= it.center[1] <= y1 + 14]
        times = re.findall(r"\d{1,2}:\d{2}", " ".join(texts))
        if not times:
            return None

        def _sec(t):
            m, sec = t.split(":")
            return int(m) * 60 + int(sec)

        if len(times) >= 2:
            cur, total = _sec(times[0]), _sec(times[1])
            if total > 0 and cur <= total:
                return cur, total
        else:
            total = _sec(times[0])
            if total > 0:
                return 0, total
        return None
    except Exception:
        return None


def _read_timecode(since_ts):
    # 浮层只在按住期间存在、松手约 0.4s 消失；只取手势中段 0.8~1.7s 的帧。
    with _MJ_LOCK:
        snap = [(ts, j) for ts, j in _MJ_FRAMES if ts >= since_ts]
    cand = [(ts, j) for ts, j in snap if 0.8 <= ts - since_ts <= 1.7]
    if not cand:
        cand = snap[-3:]
    for _, jpeg in reversed(cand[-3:]):
        tc = _parse_timecode(jpeg)
        if tc:
            return tc
    return None


def _fingerprint(items):
    """当前页简单指纹，用于返回时判断页面是否有变化。"""
    return "|".join(sorted(getattr(it, "text", "") for it in items))[:200]


# 直播页识别特征词（OCR 命中即判直播：不看完播、不互动，直接切走）
LIVE_OCR_WORDS = ["正在直播", "人在线", "在线人数", "观众榜", "讲解中"]

# 跳转第三方 App / H5 的强特征词（命中即视为离开了快手）
THIRD_PARTY_WORDS = ["淘宝", "闪购", "支付宝", "余额宝", "市民中心", "美团",
                     "京东", "拼多多", "高德", "蚂蚁保", "外卖", "打车"]


def _is_third_party(items):
    text = " ".join(getattr(it, "text", "") or "" for it in items).lower()
    return any(w.lower() in text for w in THIRD_PARTY_WORDS)


def _is_live(items):
    text = " ".join(getattr(it, "text", "") or "" for it in items).lower()
    return any(w.lower() in text for w in LIVE_OCR_WORDS)


def escape_to_kuaishou():
    """检测到误触跳第三方时，多级返回；退不出就回主屏重新拉起快手极速版。"""
    if not _is_third_party(shot_items()):
        return
    log("检测到跳转第三方，退回快手")
    for _ in range(5):
        _tap_back_arrow()
        if not _is_third_party(shot_items()):
            return
    quick.press_home()
    time.sleep(0.8)
    quick.open_app(KUAISHOU_BUNDLE)
    time.sleep(POST_OPEN + 0.5)


# ============ 子流程 ============
def _fill_search_box(kw):
    """覆盖式填入搜索词：优先元素 value 直接覆盖（避免旧词残留拼接）；找不到输入框元素时回退 keys。"""
    eid = quick.find_by_xpath("//XCUIElementTypeTextField")
    if eid:
        quick.set_value(eid, kw)
    else:
        quick.keys(kw)


def prepare():
    """冷启动到主界面就够：首页顶部同样有搜索入口，不再强制点「发现」tab。"""
    log(f"准备：冷启动 {KUAISHOU_BUNDLE}")
    quick.ensure_session()
    quick.open_app(KUAISHOU_BUNDLE, clean=True)
    time.sleep(2.5)                  # 冷启动起稳
    quick.ensure_session()           # App 被杀后旧会话失效，必须重建
    time.sleep(1.0)
    GUARD.check_and_handle(shot_items())   # 关开屏 / 运营弹窗
    return True


def do_search(kw):
    """发现页 → 搜索关键词 → 进入结果页。"""
    quick.tap2(*S_ICON)
    time.sleep(POST_OPEN)
    GUARD.check_and_handle()

    quick.tap2(*S_BOX)
    time.sleep(TAP_GAP)
    _fill_search_box(kw)
    time.sleep(POST_OPEN)  # 等下拉联想加载

    quick.tap2(*S_BTN)
    time.sleep(POST_OPEN + 0.4)
    GUARD.check_and_handle()
    log(f"已搜索「{kw}」")


def watch_one():
    # 广告页按住进度条读时间码，按剩余看完；普通流长按会弹操作菜单，不发起手势，走兜底时长。
    time.sleep(READ_DELAY)
    items = shot_items()
    if _is_ad(items):
        t_mark = time.time()
        _seek_drag(DRAG_Y_AD)
        tc = _read_timecode(t_mark)
        if tc is not None:
            cur, total = tc
            remain = total - cur
            hold = max(remain, MIN_HOLD)
            log(f"广告读到总时长 {total:.0f}s，剩余 {remain:.0f}s，停留 {hold:.0f}s")
            time.sleep(hold)
            return
        log(f"未读到时长（字幕遮挡或无进度条），按 {FALLBACK_HOLD:.0f}s 停留")
    else:
        log(f"普通视频不读时长，按 {FALLBACK_HOLD:.0f}s 停留")
    time.sleep(FALLBACK_HOLD)


def do_like():
    """概率点赞；已赞过的不重复点。"""
    if not ENABLE_LIKE or random.random() > LIKE_PROB:
        return
    quick.tap2(*C_LIKE)
    time.sleep(TAP_GAP)
    log("已点赞")


def do_comment():
    """概率评论：打开评论面板 → 输入随机模板 → 发送 → 关闭面板。"""
    if not ENABLE_COMMENT or random.random() > COMMENT_PROB:
        return
    log("打开评论面板")

    quick.tap2(*C_COMMENT)
    time.sleep(POST_OPEN)

    quick.tap2(*C_COMMENT_INPUT)
    time.sleep(0.5)

    text = random.choice(COMMENT_POOL)
    quick.keys(text)
    time.sleep(TAP_GAP)

    # 发送按钮位置固定，直接点右下红色发送（不再整图 OCR，避免识别卡顿）
    quick.tap2(*C_COMMENT_SEND)
    time.sleep(0.5)
    log(f"已评论：{text}")

    # 关闭评论面板
    quick.tap2(*C_COMMENT_CLOSE)
    time.sleep(TAP_GAP)


def _tap_back_arrow():
    """点左上返回箭头（快手播放页 / 结果页边缘右滑手势退不出，必须点箭头）。"""
    quick.tap2(*C_BACK)
    time.sleep(POST_OPEN)


def back_home():
    """结果页 → 发现页。"""
    for _ in range(2):
        _tap_back_arrow()


def _swipe_next():
    """播放页上滑切换到下一条视频。"""
    quick.swipe2(207, 700, 207, 250, 300)
    time.sleep(POST_OPEN + 0.3)


def run_once(kw):
    """一个搜索词：进结果 → 首条视频 → 上滑切条看 SWIPE_VIDEOS 条，不退回结果页。"""
    log(f"本轮关键词「{kw}」")
    do_search(kw)
    escape_to_kuaishou()

    quick.tap2(*TAB_VIDEO)    # 先切「视频」tab，纯封面网格
    time.sleep(POST_OPEN + 0.4)
    GUARD.check_and_handle()
    quick.tap2(*FIRST_CARD)   # 点首条封面进播放页
    time.sleep(POST_OPEN + 0.3)
    GUARD.check_and_handle()
    escape_to_kuaishou()

    for i in range(SWIPE_VIDEOS):
        log(f"看第 {i + 1}/{SWIPE_VIDEOS} 条")
        items = shot_items()
        fp = _fingerprint(items)
        if fp in _seen:
            log("重复视频，滑过")
        elif _is_live(items):
            log("直播页，跳过不互动")
        else:
            _seen.add(fp)
            watch_one()
            log("浏览完，准备互动")
            do_like()
            do_comment()
        if i < SWIPE_VIDEOS - 1:
            _swipe_next()
            GUARD.check_and_handle()
            escape_to_kuaishou()

    back_home()
    GUARD.check_and_handle()


def _parse_keywords(s):
    if not s:
        return []
    return [p for p in (x.strip() for x in re.split(r"[,，、\s]+", s)) if p]


def _apply_cli():
    """解析命令行可选参数，覆盖顶部默认值（GUI / 组合编排透传用）。"""
    global DEFAULT_ROUNDS, LIKE_PROB, COMMENT_PROB, SWIPE_VIDEOS, KEYWORDS, MINUTES, KUAISHOU_BUNDLE
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("rounds", nargs="?", default=None)
    p.add_argument("--like-prob", dest="like_prob", default=None)
    p.add_argument("--comment-prob", dest="comment_prob", default=None)
    p.add_argument("--keywords", dest="keywords", default=None)
    p.add_argument("--videos", dest="videos", default=None)
    p.add_argument("--minutes", dest="minutes", default=None)
    p.add_argument("--app", dest="app", default=None, choices=["kuaishou", "nebula"])
    a = p.parse_args()
    if a.rounds:
        DEFAULT_ROUNDS = int(a.rounds)
    if a.like_prob:
        LIKE_PROB = float(a.like_prob)
    if a.comment_prob:
        COMMENT_PROB = float(a.comment_prob)
    if a.keywords is not None:
        KEYWORDS = _parse_keywords(a.keywords)
    if a.videos:
        SWIPE_VIDEOS = int(a.videos)
    if a.app:
        KUAISHOU_BUNDLE = APP_BUNDLES[a.app]
    if a.minutes:
        MINUTES = float(a.minutes)
        DEFAULT_ROUNDS = 0        # 按时间跑时，轮次不参与


def _keyword_pool():
    """全量词表洗牌：每个词都跑到，只是顺序随机。"""
    kws = list(KEYWORDS) if KEYWORDS else list(DEFAULT_KEYWORDS)
    random.shuffle(kws)
    return kws


def main():
    _apply_cli()
    log(f"=== {TASK_NAME} 启动（Ctrl+C 停止）===")
    log(f"点赞概率 {LIKE_PROB}｜评论概率 {COMMENT_PROB}｜每词看 {SWIPE_VIDEOS} 条｜词数 {len(KEYWORDS or DEFAULT_KEYWORDS)}")

    conn = quick.ensure_session()
    if conn.get("error"):
        log(f"连接 WDA 失败：{conn['error']}")
        return
    if conn.get("reconnected"):
        log("WDA 会话已重建")
    _mj_start()

    # 单跑时人已经在快手/极速版里，就别再杀一遍拉起来（会把手机踢回桌面）；
    # 只有组合按时间跑（步间清过后台）或当前前台不是目标平台时才归位。
    if MINUTES > 0 or quick.active_bundle() != KUAISHOU_BUNDLE:
        prepare()
    deadline = 0.0
    if MINUTES > 0:
        deadline = time.time() + MINUTES * 60
        log(f"按时间跑 {MINUTES:g} 分钟，到点跑完当前一条退出")

    pool = _keyword_pool()
    idx = 0
    rounds = 0
    try:
        while True:
            if deadline and time.time() >= deadline:
                log(f"跑满 {MINUTES:g} 分钟，退出")
                break
            if DEFAULT_ROUNDS and rounds >= DEFAULT_ROUNDS:
                log(f"跑满 {rounds} 个词，退出")
                break
            if idx >= len(pool):
                pool = _keyword_pool()
                idx = 0
            kw = pool[idx]
            idx += 1
            rounds += 1
            try:
                run_once(kw)
            except Exception as e:
                log(f"第 {rounds} 个词异常：{type(e).__name__}: {str(e)[:90]}")
            if DEFAULT_ROUNDS and rounds >= DEFAULT_ROUNDS:
                break
            time.sleep(ROUND_GAP)
    except KeyboardInterrupt:
        log("手动停止")


if __name__ == "__main__":
    main()
