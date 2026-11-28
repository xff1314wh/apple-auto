# -*- coding: utf-8 -*-
"""自动化流程：快手极速版「看广告得金币」咨询任务

流程（一轮 = 看完一条广告并完成一次咨询互动）：
  任务中心 → 领福利 → 广告播放页（等倒计时）→ 点咨询按钮进落地页
  → 按落地页类型分支处置（电话/空结果/留资/在线聊天/聚合流）→ 容器返回回播放页
  → 胶囊 × → 领取奖励 → 下一条。

坐标均按 iPhone 11 逻辑分辨率 414×896 标定；换机型需重新标定。
分支判定靠 OCR 关键词；不可由 WDA source 定位的 WebView 悬浮按钮用 OCR 中心兜底坐标。
"""
import os
import re
import sys
import time
import random

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "core"))

import quick
import ocr as ocr_mod
import issue_checker as checker_mod
import handoff
from guard import IssueGuard

# ============ 可调配置 ============
# 在线聊天发送的咨询话术（1 句）
CONSULT_TEXT = "你好，课程怎么收费？想了解一下。"

# 通用留资表单：随机手机号号段（后 8 位随机，凑满 11 位）
PHONE_PREFIXES = ["138", "139", "135", "136", "137", "150", "151", "158", "159", "188"]
# 单选卡片常见选项文案关键词
OPTION_WORDS = ["本人", "孩子", "岁", "度", "颗", "以内", "以上", "不确定", "男性", "女性", "男", "女", "是", "否"]
# 隐私 / 用户协议勾选文案
AGREEMENT_WORDS = ["已阅读并同意", "阅读并同意", "阅读并接受", "隐私协议", "用户协议", "隐私保护协议", "保护协议"]
# 表单字段语义分类（跨行业通用，按标签 / 占位词判断填什么）
FIELD_PHONE = ["手机", "电话", "联系方式"]
FIELD_NAME = ["姓名", "名字", "称呼"]
FIELD_SKIP = ["验证码", "短信", "城市", "地区", "区县", "所在城市", "城市选择"]
# 随机中文名用字
SURNAMES = ["王", "李", "张", "刘", "陈", "杨", "赵", "黄", "周", "吴", "徐", "孙"]
GIVEN_NAMES = ["伟", "芳", "娜", "敏", "静", "磊", "军", "洋", "勇", "艳", "杰", "涛", "明", "超", "雪", "晨"]
# 提交 / 发送验证码按钮文案
SUBMIT_WORDS = [
    # 提交 / 确认
    "提交", "立即提交", "确认提交", "确认", "确定", "下一步",
    # 验证码
    "发送验证码", "获取验证码",
    # 预约 / 报名
    "立即预约", "免费预约", "在线预约", "马上预约", "预约试听", "预约体验", "预约报名", "立即报名",
    # 领取 / 福利 / 优惠
    "立即领取", "免费领取", "一键领取", "去领取", "领取福利", "领取优惠", "领福利", "领券", "预约好礼",
    # 方案 / 报价 / 名额留资
    "获取方案", "定制方案", "定制报价", "获取报价", "方案报价", "一键获取", "免费获取", "获取1v1", "获取名额", "预留名额",
    # 一键系列
    "一键预约", "一键咨询", "一键获取", "一键报名", "一键提交",
    # 试听 / 体验
    "免费试听", "申请试听", "免费体验", "立即体验",
]

DEFAULT_ROUNDS = 10  # 0 = 一直跑
TASK_NAME = "快手广告咨询"
APP_BUNDLES = {"kuaishou": "com.jiangjia.gif", "nebula": "com.kuaishou.nebula"}
TASK_BUNDLE = APP_BUNDLES["nebula"]   # 默认极速版，--app 可覆盖
MINUTES = 0.0         # >0 时按时间跑（轮次不参与），到点跑完当前一轮退出

# unknown 页面是否走人在环介入（上报 handoff 服务等标注）；False 则直接兜底退出
HANDOFF_ENABLED = True

# 节奏（秒）——动作后仅留最小起稳时间，间隔尽量短
TAP_GAP = 0.08      # 单次点击 / 输入后最小停顿
POST_OPEN = 0.15    # 跳转 / 进落地页后的起稳等待（底线，再短易误判白屏）
ROUND_GAP = 0.1     # 两轮之间间隔
VIDEO_WAIT_MAX = 22.0  # 播放页倒计时最长等待（业务必须看完，不可压）
POLL_GAP = 0.2      # 状态轮询间隔
COUNTDOWN_GAP = 2.0   # 倒计时 OCR 轮询间隔（秒）
ROW_TOL = 60        # 任务标题与同行按钮允许的 y 坐标差（逻辑像素）
BROWSE_SWIPES = 1   # 无咨询入口时，落地页上下滑动浏览次数
BROWSE_HOLD = (0.4, 0.8)  # 每次滑动后停留浏览的随机时长区间（秒）
LEAD_GAP = 0.06      # 表单内点击 / 输入后的最小间隔（秒，尽量快）
LEAD_SCROLL_WAIT = 0.15  # 表单上滑到下一屏后的起稳等待（秒）
# 播放页底部转化 / 咨询按钮文案变体
CONSULT_BTNS = ["立即咨询", "在线咨询", "免费咨询", "免费预约", "立即预约", "预约咨询",
                "一键购买", "立即购买", "立即下单", "去购买", "立即抢购",
                "点击并浏览", "浏览15秒", "浏览 15 秒"]
# 直播页识别特征：source 节点词可直接命中；OCR 走 _ocr_live 加严判定。
# 「说点什么」只是通用评论框占位，普通广告页也有，单凭它会误判成直播页并整轮空转。
# 注意：「说点什么」是通用评论框占位，普通广告播放页也有，不能算直播特征
# （算进去的话每条带评论框的广告都会被判成直播页直接退掉）。
LIVE_SOURCE_WORDS = ["退出直播", "在线观众榜", "关注了主播", "当前直播通用", "快关注我吧"]
LIVE_OCR_STRONG = ["万观看", "主播新人券", "讲解中", "观众榜", "退出直播"]
LIVE_OCR_WEAK = ["说点什么", "在线观众", "进入直播间"]

# ============ 标定坐标（414×896） ============
# L0 任务中心
C_WELFARE = (362, 372)          # 「看广告得金币」行 · 领福利
C_RED_BALL = (47, 160)          # 发现页左上红包悬浮球 → 任务中心（H5，实测约 4 秒加载）
# L1 播放页咨询按钮（收起态 / 展开态）
C_CONSULT_FOLDED = (207, 732)
C_CONSULT_OPEN = (207, 834)
# H5 顶部三按钮（source 实测中心）
C_BACK = (31, 70)
# 第三方 App 顶部「返回快手 / 快手极速版」小字条（左上角固定点，多帧 OCR 实测中心 ≈(46,38)）
C_BACK_KS = (46, 38)
# 播放页胶囊关闭 × 与激励弹窗「领取奖励」
C_CAPSULE_X = (186, 78)
C_REWARD = (207, 514)
# 在线聊天
C_INPUT = (107, 826)
C_RETURN = (361, 794)           # 键盘换行/Return = 发送
# 空结果页
C_REFRESH = (207, 594)
# 快手系前台 App bundleId（普通快手 + 极速版）；不在此集合即视为跳到第三方 App
KUAISHOU_BUNDLES = {"com.kuaishou.nebula", "com.jiangjia.gif"}

# ============ 异常弹窗兜底配置（GUARD） ============
ISSUES = [
    {"name": "城市切换询问", "keywords": ["是否切换"],
     "points": [{"action": "tap", "x": 127, "y": 509, "label": "取消"}],
     "max_rounds": 2, "note": "定位城市弹窗，点取消保持原城市"},
    {"name": "离开挽留弹窗", "keywords": ["继续浏览", "查看课程详情"],
     "points": [{"action": "tap", "x": 137, "y": 612, "label": "继续浏览"}],
     "max_rounds": 2, "note": "广告挽留卡，点继续浏览关闭卡片"},
    {"name": "放弃二次确认", "keywords": ["确定放弃"],
     "points": [{"action": "tap", "x": 137, "y": 612, "label": "继续浏览"}],
     "max_rounds": 2, "note": "二次确认框，继续浏览关闭"},
    {"name": "定位授权弹窗", "keywords": ["想使用你当前的位置", "精确位置"],
     "points": [{"action": "tap", "x": 140, "y": 517, "label": "不允许"}],
     "max_rounds": 2, "note": "WebView 定位授权，点不允许拒绝"},
    {"name": "再看1个金币弹窗", "keywords": ["再看1个", "再看一个"],
     "points": [{"action": "tap", "x": 327, "y": 413, "label": "关闭弹窗"}],
     "max_rounds": 2, "note": "看完广告后的激励挽留弹窗，点右上 × 关闭，不领奖励"},
]
GUARD = IssueGuard(ISSUES, quick, ocr_mod=ocr_mod, checker_mod=checker_mod,
                   post_action_sleep=0.1, recheck_gap=0.1)


# ============ 辅助 ============
SHOT_DIR = os.path.join(ROOT, "data", "shobs")
_reported_fp = set()  # 本轮已上报待办的页面指纹（去重）


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


def save_shot():
    """落盘一张待办截图，返回路径；失败返回空串。"""
    data, err = quick.shot_bytes()
    if err or not data:
        return ""
    os.makedirs(SHOT_DIR, exist_ok=True)
    p = os.path.join(SHOT_DIR, f"handoff_{int(time.time() * 1000)}.png")
    try:
        open(p, "wb").write(data)
        return p
    except Exception:
        return ""


def shot_items():
    """截图并识别，返回 OcrItem 列表；失败返回空列表。"""
    data, err = quick.shot_bytes()
    if err or not data:
        return []
    try:
        return ocr_mod.recognize(data)
    except Exception:
        return []


def hit(items, keywords):
    """返回第一个关键词命中的 OcrItem；未命中 None。"""
    for it in items:
        t = (it.text or "").lower()
        if any(kw and kw.lower() in t for kw in keywords):
            return it
    return None


def tap_item(item, fallback):
    """命中点其中心，未命中点兜底坐标。"""
    if item is not None:
        quick.tap2(*[int(v) for v in item.center])
    else:
        quick.tap2(*fallback)


def _source_xml():
    """拉取当前页面 WDA source XML 文本；失败返回空串。"""
    try:
        r = quick._req("GET", f"/session/{quick.session()}/source", timeout=15)
        return r.get("value", "") if isinstance(r, dict) else ""
    except Exception:
        return ""


def _xml_attr(tag, key):
    m = re.search(rf'{key}="([^"]*)"', tag)
    return m.group(1) if m else None


def _xml_nodes():
    """解析 source XML，返回可见元素字典列表（type/name/value/placeholder/center/矩形）。

    WebView 内输入框 / 按钮在 source 里带矩形坐标，用于分类与兜底；填值优先走
    element id click + set_value（坐标 tap 常聚焦不上 WebView 输入框）。
    """
    nodes = []
    xml = _source_xml()
    if not xml:
        return nodes
    for tag in re.findall(r"<XCUIElementType\w+\b[^>]*>", xml):
        def num(k):
            v = _xml_attr(tag, k)
            try:
                return float(v)
            except (TypeError, ValueError):
                return None
        x, y, w, h = num("x"), num("y"), num("width"), num("height")
        if None in (x, y, w, h):
            continue
        nodes.append({
            "type": _xml_attr(tag, "type") or "",
            "name": _xml_attr(tag, "name") or "",
            "value": _xml_attr(tag, "value") or "",
            "placeholder": _xml_attr(tag, "placeholderValue") or "",
            "center": (int(x + w / 2), int(y + h / 2)),
            "x": x, "y": y, "w": w, "h": h,
        })
    return nodes


# 导航栏广告标识 / 反馈 / 更多图标（不是返回或关闭按钮），名字带这些词直接忽略
_AD_ICON_WORDS = ("negative", "feedback", "report", "warn", "alert", "info", "more", "share")


def _nav_buttons(nodes=None):
    """解析导航栏 back / close 按钮中心坐标；找不到对应项为 None。

    优先按可访问性 name 含 back / close 识别；广告负反馈、信息、更多等图标既不当返回也不当关闭。
    位置兜底：导航栏（y<120）按钮按 x 排序，最左当返回；关闭只取标题左侧候选，最右侧
    图标视为广告标识排除。每帧动态读真实坐标，不依赖死坐标。
    """
    if nodes is None:
        nodes = _xml_nodes()
    back = close = None
    nav_btns = []
    for n in nodes:
        if n["type"] != "XCUIElementTypeButton" or n["center"][1] > 120:
            continue
        low = (n["name"] or "").lower()
        if any(w in low for w in _AD_ICON_WORDS):
            continue
        if "back" in low:
            back = n["center"]
        elif "close" in low or "dismiss" in low or "×" in low or "退出直播" in low:
            close = n["center"]
        else:
            nav_btns.append(n)
    if back is None or close is None:
        nav_btns.sort(key=lambda b: b["center"][0])
        if back is None and nav_btns:
            back = nav_btns.pop(0)["center"]
        if close is None:
            cands = [b for b in nav_btns if b["center"][0] <= 310]
            if cands:
                close = cands[0]["center"]
    return back, close


# 占位特征词：旁挂文字只有含这些才视为输入框占位，避免误关联到标题 / 评分
_PLACEHOLDER_HINTS = ["请输入", "手机", "电话", "姓名", "名字", "称呼", "联系方式", "验证码", "城市", "地区"]


def _is_placeholder_text(t):
    t = t or ""
    return any(k in t for k in _PLACEHOLDER_HINTS)


def _field_hint(n, nodes):
    """取输入框的占位 / 标签提示词：优先自身属性，其次同行旁挂占位文字。"""
    h = (n["placeholder"] + n["name"] + n["value"]).strip()
    if h:
        return h.lower()
    sib = next((s["name"] or s["value"] for s in nodes
                if s["type"] == "XCUIElementTypeStaticText"
                and abs(s["y"] - n["y"]) < 12 and _is_placeholder_text(s["name"] or s["value"])), "")
    return sib.lower()


def _field_eid(placeholder):
    """按占位词用 xpath 定位 TextField element id；查不到返回 None。

    依次匹配 value / name / label 属性；placeholderValue 属性 xpath 查不到，不查。
    """
    if not placeholder:
        return None
    xp = (f'//XCUIElementTypeTextField[@value="{placeholder}" '
          f'or @name="{placeholder}" or @label="{placeholder}"]')
    return quick.find_by_xpath(xp)


def _fill_field(n, text, eid=None):
    """聚焦并写入一个输入框：占位词 xpath 优先，其次顺序配对 eid，回退坐标 tap + keys。

    定位顺序：占位符语义属性 → 批量类型配对 eid → 坐标兜底；写入用 set_value 覆盖，
    端点失败再退回 keys 逐字注入。
    """
    target = _field_eid(n["placeholder"] or n["value"]) or eid
    if target:
        quick.click(target)
        time.sleep(LEAD_GAP)
        resp = quick.set_value(target, text)
        if isinstance(resp, dict) and resp.get("error"):
            quick.keys(text)
    else:
        quick.tap2(*n["center"])
        time.sleep(LEAD_GAP)
        quick.keys(text)
    time.sleep(0.06)


# 登录墙 / 验证墙：第三方账号登录、滑块/身份核实。这类页面绝不填资料、不提交，直接退出
LOGIN_WORDS = ["手机号快捷登录", "快捷登录", "请输入手机号码", "请输入手机号", "发送验证码",
               "请输入验证码", "第三方账户登录", "我已阅读并同意", "滑块验证", "身份核实",
               "向右滑动滑块", "滑动验证"]
# 商户聚合流：列表页（多张商户卡片 + 去看看/立即领取），不是单页留资表单
AGGREGATE_WORDS = ["去看看", "立即领取", "倾情推荐", "条评价", "免费试听", "到店礼",
                   "商户", "免费体验"]


def _source_hit(nodes, words):
    """source 节点 name/value/placeholder 命中关键词；nodes 为空返回 None（交 OCR 兜底）。"""
    if not nodes:
        return None
    for n in nodes:
        blob = f"{n['name'] or ''} {n['value'] or ''} {n['placeholder'] or ''}".lower()
        for kw in words:
            if kw and kw.lower() in blob:
                return n
    return None


def _is_aggregate_list(nodes=None):
    """source 层识别商户聚合流：多条评价 / 多个评分即视为商户列表，不当单页表单填底部框。"""
    if nodes is None:
        nodes = _xml_nodes()
    if not nodes:
        return False
    review = sum(1 for n in nodes if "条评价" in (n["name"] or ""))
    score = sum(1 for n in nodes if re.search(r"\d\.\d\s*分", n["name"] or ""))
    return review >= 2 or score >= 2


def _is_lead_form(nodes, items):
    """结构判定留资表单：有输入框，且带姓名/电话类字段标签或提交咨询钮。

    词表覆盖不到的行业表单（汽车 / 房产 / 家装等，字段为姓名、电话、车系、门店）靠结构识别，
    避免被判 unknown 走空滑。login 墙、聚合流已在 classify 前面截走，不会落到这里。
    """
    if not nodes:
        return False
    if not any(n["type"] == "XCUIElementTypeTextField" for n in nodes):
        return False
    blob = " ".join((n["name"] or "") + " " + (n["value"] or "") for n in nodes).lower()
    has_field = any(k in blob for k in FIELD_PHONE + FIELD_NAME)
    has_submit = _source_hit(nodes, SUBMIT_WORDS) is not None or hit(items, SUBMIT_WORDS) is not None
    return has_field or has_submit


_LEAD_WORDS = ["立即免费领取", "预约好礼", "请填写联系方式", "请输入您的手机号", "诊疗城市",
               "定制方案", "获取1v1", "隐私保护协议", "意向城市",
               "期望的诊疗城市", "预算范围"]
_CHAT_WORDS = ["你已进入服务咨询会话", "可详细描述您的问题", "在线中", "输入消息",
               "请输入消息", "请输入您想咨询", "请输入您的问题", "咨询客服", "在线客服"]
_PHONE_WORDS = ["打电话", "拨打电话", "电话咨询"]
_EMPTY_WORDS = ["未能匹配", "没有找到", "暂无匹配", "暂无结果", "无匹配结果"]


def _ocr_live(items):
    """OCR 侧直播判定：强特征命中任一，或弱特征同时命中两个以上。"""
    text = " ".join((getattr(it, "text", "") or "") for it in items)
    if any(w in text for w in LIVE_OCR_STRONG):
        return True
    return sum(1 for w in LIVE_OCR_WEAK if w in text) >= 2


def classify(items, nodes=None):
    """落地页 / 播放页类型判定。返回类型字符串。

    判定顺序：真登录墙（无可填字段）→ 直播 → 在线咨询 → 留资表单 → 聚合列表 → 其他。
    登录墙与留资表单共用「手机号 / 已阅读并同意」文案，靠有没有可填字段 + 提交按钮区分；
    聚合列表页底部常挂手机号表单，这种要填表，不能按列表页跳过。
    词表同时匹配 OCR 文本与 source 节点；source 抓不到（封闭 WebView）时只靠 OCR。
    """
    if nodes is None:
        nodes = _xml_nodes()
    # 收银台 / 下单浮层优先级最高：任何交互都可能触发付款
    if hit(items, PAY_WORDS) or _source_hit(nodes, PAY_WORDS):
        return "pay"
    # 留资表单页也常挂「请输入手机号码 / 我已阅读并同意」，有可填字段 + 提交按钮时
    # 不算登录墙，否则表单会被判成 login，只退不填。
    if (hit(items, LOGIN_WORDS) or _source_hit(nodes, LOGIN_WORDS)) and not _is_lead_form(nodes, items):
        return "login"
    if _source_hit(nodes, LIVE_SOURCE_WORDS) or _ocr_live(items):
        return "live"
    if hit(items, _CHAT_WORDS) or _source_hit(nodes, _CHAT_WORDS):
        return "chat"
    # 普通广告播放页 / 广告底页：底部只有通用评论框「说点什么」，不是直播也不是咨询，
    # 按播放页处理（继续等倒计时），不要空滑上报。
    if hit(items, ["说点什么"]) or _source_hit(nodes, ["说点什么"]):
        return "player"
    # 先看是不是能填的单页留资表单：商户聚合列表页底部常挂「手机号 + 提交」，这种要填表，
    # 不能再按 aggregate 跳过（否则页面明明有输入框却什么都不做）。
    if _is_lead_form(nodes, items) or hit(items, _LEAD_WORDS) or _source_hit(nodes, _LEAD_WORDS):
        return "lead"
    if _is_aggregate_list(nodes) or _source_hit(nodes, AGGREGATE_WORDS) or hit(items, AGGREGATE_WORDS):
        return "aggregate"
    # 任务中心（常叠一层翻倍/奖励弹层）：别落进 unknown 去空滑上报
    if hit(items, ["看广告得金币"]) and hit(items, ["领福利"]):
        return "center"
    if hit(items, _PHONE_WORDS) or _source_hit(nodes, _PHONE_WORDS):
        return "phone"
    if hit(items, _EMPTY_WORDS) or _source_hit(nodes, _EMPTY_WORDS):
        return "empty"
    if hit(items, ["立即咨询"]):
        return "player"
    if hit(items, ["查看详情", "了解更多"]):
        return "aggregate"
    return "unknown"


# ============ 分支处置 ============
def _countdown_done(items):
    """倒计时是否结束：顶部胶囊变「已成功领取」。

    金币到账提示也是「已成功领取195金币」，从左上角弹出并带「金币」字样，
    不能当成倒计时结束，否则广告没看完就切条。
    """
    for it in items:
        t = (it.text or "").replace(" ", "")
        if "已成功领取" not in t:
            continue
        cx, cy = it.center
        if "金币" in t and cx < 150:
            continue
        if cy < 130:
            return True
    return False


def wait_countdown(max_wait=None):
    """等播放页倒计时结束。

    走完胶囊变「已成功领取」才往下走；每 COUNTDOWN_GAP 秒 OCR 一次，等待期不做
    任何操作。不做漏识提前结束：广告没看完不切下一条，等到上限超时兜底。
    """
    deadline = time.time() + (max_wait or VIDEO_WAIT_MAX)
    items = shot_items()
    while time.time() < deadline:
        if _countdown_done(items):
            log("  倒计时已结束")
            return
        time.sleep(COUNTDOWN_GAP)
        items = shot_items()
    log("  倒计时等待超时，继续往下走")


def open_consult():
    """点播放页底部转化 / 咨询按钮；首点常只展开卡片，二次点才跳落地页。"""
    items = shot_items()
    btn = hit(items, CONSULT_BTNS)
    # 先按 OCR 命中点；兜底展开态坐标
    tap_item(btn, C_CONSULT_OPEN)
    time.sleep(POST_OPEN)
    # 二次点：若仍是收起卡片，再点一次
    items = shot_items()
    if hit(items, CONSULT_BTNS):
        quick.tap2(*C_CONSULT_FOLDED)
        time.sleep(POST_OPEN)


def handle_chat():
    """在线聊天：source 定位输入框（取屏幕下半部可编辑节点）→ 注入话术 → 回车发送。

    会话页输入框横跨整条底栏，固定坐标可能点在文字提示上，所以优先按节点定位，
    抓不到再退回固定坐标。
    """
    nodes = _xml_nodes() or []
    box = next((n for n in nodes
                if n["type"] in ("XCUIElementTypeTextView", "XCUIElementTypeTextField")
                and n["center"][1] > 700), None)
    pos = box["center"] if box else C_INPUT
    quick.tap2(*pos)
    time.sleep(LEAD_GAP)
    quick.keys(CONSULT_TEXT)
    time.sleep(0.15)
    quick.tap2(*C_RETURN)  # 回车发送
    log(f"  在线咨询已发送（输入框 {pos}）：{CONSULT_TEXT}")
    time.sleep(POST_OPEN)


def gen_phone():
    """随机生成 11 位假手机号：号段前缀 + 后 8 位随机数字。"""
    pre = random.choice(PHONE_PREFIXES)
    return pre + "".join(random.choice("0123456789") for _ in range(8))


def gen_name():
    """随机生成中文名。"""
    return random.choice(SURNAMES) + random.choice(GIVEN_NAMES)


def _first_option(items, used):
    """取第一个还没点过的单选卡片选项；返回 OcrItem，无则 None。"""
    for it in items:
        t = (it.text or "").strip()
        if not t or len(t) > 10:
            continue
        if any(w in t for w in OPTION_WORDS):
            key = tuple(int(v) for v in it.center)
            if key not in used:
                return it
    return None


# 必填标签（标题）排除词，避免把字段标题当成选项点
_OPTION_TITLE_WORDS = ["您", "意向", "目前", "请选择", "选择", "城市", "地区", "度数", "年龄"]


def _is_option_text(t):
    t = (t or "").strip()
    if not t or len(t) > 12:
        return False
    if any(k in t for k in _OPTION_TITLE_WORDS):
        return False
    return any(w in t for w in OPTION_WORDS)


# 字段标题词：用于把下方输入框 / 选项归属到对应字段
FIELD_TITLE_WORDS = (FIELD_PHONE + FIELD_NAME + FIELD_SKIP
                      + ["度数", "年龄", "性别", "意向", "预算", "方案"])


def _is_title_text(t):
    t = (t or "").strip()
    if not t or len(t) > 12:
        return False
    return any(w in t for w in FIELD_TITLE_WORDS)


def _field_blocks(nodes):
    """按纵向把 source 节点切成字段块：标题下方最近的输入框 / 单选选项归属到该标题。

    返回 [{"title", "y", "fields": [TextField], "options": [StaticText]}]；
    归属规则：标题 y <= 控件 y，取最靠上的最近标题。
    """
    titles = [n for n in nodes
              if n["type"] == "XCUIElementTypeStaticText" and _is_title_text(n["name"])]
    titles.sort(key=lambda n: n["center"][1])
    blocks = [{"title": t["name"], "y": t["center"][1], "fields": [], "options": []}
              for t in titles]

    def owner(y):
        # 只归属到距离 <=100px 的最近标题；超距（如下方无标题的独立输入框）留作兜底
        cur = None
        best = 101
        for b in blocks:
            if b["y"] <= y + 10 and y - b["y"] <= 100 and y - b["y"] < best:
                cur, best = b, y - b["y"]
        return cur

    for n in nodes:
        if n["type"] == "XCUIElementTypeTextField":
            b = owner(n["center"][1])
            if b:
                b["fields"].append(n)
        elif n["type"] == "XCUIElementTypeStaticText" and _is_option_text(n["name"]):
            b = owner(n["center"][1])
            if b:
                b["options"].append(n)
    return blocks


def _classify_field(hint):
    """输入框语义分类：phone / name / skip / text。

    skip 框不填（验证码、城市选择器等）；text 为其余自由文本框，填短文本。
    """
    h = (hint or "").lower()
    if any(k in h for k in FIELD_PHONE):
        return "phone"
    if any(k in h for k in FIELD_NAME):
        return "name"
    if any(k in h for k in FIELD_SKIP):
        return "skip"
    return "text"


def _already_filled(n):
    """输入框已有真实值（非占位提示、非脱敏号）视为已填，不再覆盖。"""
    v = (n["value"] or "").strip()
    if not v or "*" in v:
        return False
    return not any(k in v for k in ("请输入", "请选择", "请填写", "请完善"))


def _write_textfield(f, fkind, eid, filled_fields, filled_kinds):
    """填入一个 source 输入框并登记去重集合；电话填随机号，其余填随机中文名。"""
    text = gen_phone() if fkind == "phone" else gen_name()
    _fill_field(f, text, eid)
    filled_fields.add(f["center"])
    filled_kinds.add(fkind)


def handle_lead():
    """通用留资表单：source 优先定位输入框 / 提交按钮，OCR 占位词兜底。

    字段不固定，按输入框占位 / 标签语义分类：手机电话框填随机手机号，姓名框填随机
    中文名，其余文字框填短文本；协议勾选左侧圆点；提交点到即退，不等结果。
    source 抓不到输入框时（封闭 WebView），退回 OCR 占位词定位。
    """
    used_opts = set()
    used_opt_names = set()   # 已点过的单选选项文字，二次点会取消选中
    filled_fields = set()    # 已填输入框坐标（同屏兜底去重）
    filled_keys = set()      # 已填输入框语义 key（跨滚动去重）
    filled_kinds = set()     # 已填字段类别：phone / name / text
    agreed = False           # 隐私协议本轮只勾一次
    for screen in range(3):
        items = shot_items()
        GUARD.check_and_handle(items)
        nodes = _xml_nodes()

        if nodes:
            # —— source 轨：按字段块归属匹配，标题下的输入框 / 选项各归各字段 ——
            textfields = [x for x in nodes if x["type"] == "XCUIElementTypeTextField"]
            eids = quick.find_elements("//XCUIElementTypeTextField")
            tf_idx = {id(f): i for i, f in enumerate(textfields)}
            blocks = _field_blocks(nodes)

            for b in blocks:
                kind = _classify_field(b["title"])
                if kind == "skip":
                    continue  # 城市 / 地区 / 验证码选择器，不填不点
                for f in b["fields"]:
                    hint = _field_hint(f, nodes)
                    fkind = _classify_field(hint)
                    fkind = kind if fkind == "text" else fkind
                    if fkind == "skip":
                        continue
                    fkey = (fkind, b["title"], hint)
                    if f["center"] in filled_fields or fkey in filled_keys:
                        continue
                    eid = eids[tf_idx[id(f)]] if tf_idx[id(f)] < len(eids) else None
                    if _already_filled(f):
                        filled_fields.add(f["center"])
                        continue
                    _write_textfield(f, fkind, eid, filled_fields, filled_kinds)
                    filled_keys.add(fkey)
                # 块内单选选项（度数 / 年龄 / 性别等）：点第一个，已点过不再点（二次点会取消选中）
                if b["options"]:
                    opt = sorted(b["options"], key=lambda n: (n["center"][1], n["center"][0]))[0]
                    if opt["name"] in used_opt_names:
                        continue
                    quick.tap2(*opt["center"])
                    used_opt_names.add(opt["name"])
                    time.sleep(LEAD_GAP)
                    log(f"  source 点选选项 {opt['name']}（{b['title']}）")

            # 未归属到标题的输入框：兜底按占位词分类填
            for f in textfields:
                hint = _field_hint(f, nodes)
                kind = _classify_field(hint)
                if kind == "skip" or kind in filled_kinds:
                    continue
                if f["center"] in filled_fields:
                    continue
                eid = eids[tf_idx[id(f)]] if tf_idx[id(f)] < len(eids) else None
                if _already_filled(f):
                    filled_fields.add(f["center"])
                    continue
                _write_textfield(f, kind, eid, filled_fields, filled_kinds)
                filled_keys.add((kind, "", hint))
                log(f"  source 兜底填入（{kind}）于 {f['center']}")

            # 收键盘，避免遮挡协议行
            quick.tap2(207, 150)
            time.sleep(LEAD_GAP)

            # 勾选隐私协议：优先取协议文字左侧的小尺寸圆点节点，否则按文字左缘偏移
            if not agreed:
                agree = next((n for n in nodes if any(k in n["name"] for k in AGREEMENT_WORDS)), None)
                if agree is not None:
                    dot = next((x for x in nodes if x["type"] == "XCUIElementTypeOther"
                                and abs(x["y"] - agree["y"]) < 12 and x["x"] < agree["x"]
                                and 8 <= x["w"] <= 24 and 8 <= x["h"] <= 24), None)
                    cx, cy = dot["center"] if dot else (max(24, int(agree["x"] - 12)), agree["center"][1])
                    quick.tap2(cx, cy)
                    agreed = True
                    time.sleep(LEAD_GAP)
                    log("  已勾选隐私协议")

            # 提交按钮：source 定位优先；封闭 WebView 按钮 source 抓不到，退回 OCR 文字定位
            if not filled_kinds:
                # 一个字段都没填上还点提交，只会跳到别处或提交空表，直接退
                log("  没填到任何字段，放弃提交，直接返回")
                return False
            sub = next((n for n in nodes if n["type"] in ("XCUIElementTypeButton", "XCUIElementTypeStaticText", "XCUIElementTypeOther")
                        and any(k in (n["name"] + n["value"]) for k in SUBMIT_WORDS)), None)
            if sub is not None:
                quick.tap2(*sub["center"])
                log("  已点提交（source 定位），立即返回")
                time.sleep(TAP_GAP)
                return
            btn = hit(items, SUBMIT_WORDS)
            if btn:
                quick.tap2(*[int(v) for v in btn.center])
                log("  已点提交（OCR 兜底定位），立即返回")
                time.sleep(TAP_GAP)
                return
        else:
            # —— OCR 兜底轨：按占位词分类填写 ——
            for it in items:
                kind = _classify_field(it.text or "")
                if kind in ("skip", "text") or kind in filled_kinds:
                    continue
                if kind == "phone":
                    quick.tap2(*[int(v) for v in it.center])
                    time.sleep(LEAD_GAP)
                    quick.keys(gen_phone())
                    filled_kinds.add("phone")
                    time.sleep(0.1)
                    log("  OCR 填入随机手机号")
                elif kind == "name":
                    quick.tap2(*[int(v) for v in it.center])
                    time.sleep(LEAD_GAP)
                    quick.keys(gen_name())
                    filled_kinds.add("name")
                    time.sleep(0.1)
                    log("  OCR 填入随机姓名")
            if not agreed:
                agree = hit(items, AGREEMENT_WORDS)
                if agree:
                    quick.tap2(max(24, int(agree.box[0][0] - 24)), int(agree.center[1]))
                    agreed = True
                    time.sleep(LEAD_GAP)
                    log("  OCR 勾选隐私协议")

            opt = _first_option(items, used_opts)
            if opt:
                quick.tap2(*[int(v) for v in opt.center])
                used_opts.add(tuple(int(v) for v in opt.center))
                time.sleep(LEAD_GAP)

            if not filled_kinds:
                log("  没填到任何字段，放弃提交，直接返回")
                return False
            btn = hit(items, SUBMIT_WORDS)
            if btn:
                quick.tap2(*[int(v) for v in btn.center])
                log("  已点提交 / 发送验证码，立即返回")
                time.sleep(TAP_GAP)
                return

        # 本屏未到底，上滑看下一屏字段与按钮
        quick.swipe2(207, 700, 207, 350, 260)
        time.sleep(LEAD_SCROLL_WAIT)
    log("  表单未找到提交按钮，按兜底返回")


def handle_empty():
    """空结果页：刷新一次，仍空则退出。"""
    quick.tap2(*C_REFRESH)
    time.sleep(POST_OPEN)
    items = shot_items()
    if hit(items, ["未能匹配", "没有找到"]):
        log("  刷新后仍为空结果")


def browse_in_place(swipes=None, quiet=False):
    """无咨询入口的兜底拟人：上滑一下、下滑回一点，不拨号 / 不留资 / 不互动。

    :param swipes: 滑几轮（一轮=上滑+下滑），默认 BROWSE_SWIPES
    :param quiet: 不打印「无咨询入口」那句（聚合页自己有说法）
    """
    for _ in range(swipes if swipes is not None else BROWSE_SWIPES):
        quick.swipe2(207, 640, 207, 300, 280)
        time.sleep(random.uniform(*BROWSE_HOLD))
        quick.swipe2(207, 300, 207, 560, 240)
        time.sleep(random.uniform(*BROWSE_HOLD))
    if not quiet:
        log("  无咨询入口：已滑动浏览，准备返回")


# 收银台 / 下单浮层特征（命中即高危，禁止点内容区，只退不点）
PAY_WORDS = ["立即支付", "提交订单", "确认支付", "去支付", "收银台",
             "微信支付", "支付宝支付", "实付款", "立即下单"]


def _is_pay(items=None):
    """是否停在收银台 / 下单浮层。"""
    return hit(items or shot_items(), PAY_WORDS) is not None


def close_pay(max_tries=4):
    """退出收银台 / 下单浮层：只点关闭 × 或返回手势，绝不点任何支付按钮。

    × 位置：先按 source 关闭语义节点，再取浮层容器右上角内缩，最后右上角区域；
    所有点击都限制在 y<700（支付条在底部），点完用支付词是否消失来验证。
    """
    log("  收银台页：只退出，不点支付")
    for i in range(max_tries):
        items = shot_items()
        nodes = _xml_nodes()
        pos = _close_by_name(nodes) or _close_in_zone(items, nodes)
        if pos is not None and pos[1] < 700 and 30 < pos[0] < 396:
            quick.tap2(*pos)
            time.sleep(POST_OPEN)
            if not _is_pay(shot_items()):
                log(f"  已退出收银台（点关闭 {pos}）")
                return True
            log(f"  {pos} 点了没退出（第 {i + 1}/{max_tries} 次）")
            continue
        # 找不到安全的关闭点就只用手势，绝不盲点页面内容
        quick.go_back(448)
        time.sleep(POST_OPEN)
        if not _is_pay(shot_items()):
            log("  已退出收银台（边缘返回手势）")
            return True
        log(f"  手势没退出（第 {i + 1}/{max_tries} 次）")
    return False


def _on_player(items):
    """是否已回到播放页（咨询按钮变体 / 结束胶囊 / 进行胶囊任一命中）。

    在线咨询会话页顶部也挂「已成功领取xx金币」，先按会话特征排除，否则会被当成播放页。
    """
    if _is_pay(items):
        return False
    if hit(items, _CHAT_WORDS) is not None:
        return False
    return hit(items, CONSULT_BTNS + ["已成功领取", "后可领取", "后可领"]) is not None


def _on_center(items):
    """是否退回了任务中心（退过头的标志）。"""
    return hit(items, ["领福利", "任务中心"]) is not None


def handle_unknown(items, round_no, depth):
    """unknown 页面：台账命中先回放；未命中上报待办记录后直接继续兜底，不挂起等待。

    待办留给人工 / Agent 事后处置并回写台账；脚本不阻塞，随后 close_ad 退出本轮。
    :return: True 已接管回放；False 直接走降级兜底退出。
    """
    if not HANDOFF_ENABLED:
        return False
    fp = handoff.fingerprint(items)
    cases = handoff.load_cases()
    hit_case = handoff.match_case(cases, fp)
    if hit_case:
        log(f"  台账命中指纹 {fp}：类型 {hit_case.get('type')}，回放已录动作")
        handoff.replay(hit_case, quick)
        return True
    if fp in _reported_fp:
        log("  该类页面本轮已记录过，不再重复上报")
        return False
    if not handoff.available():
        log("  handoff 服务不可达，未知页面记录跳过，降级兜底退出")
        return False
    shot_path = save_shot()
    try:
        resp = handoff.post_pending(fp, items, shot_path, round_no, depth,
                                    note="落地页类型未识别，待人工处置")
        cid = resp.get("id")
        _reported_fp.add(fp)
        log(f"  未知页面已记录为待办 {cid}（不等待，继续兜底退出），待人工处理后回写台账")
    except Exception as e:
        log(f"  待办记录失败：{type(e).__name__}，降级退出")
    return False


def close_ad(max_layers=8):
    """退出落地页 / 聊天窗 / 第三方页：纯 App 内逐层返回，退到播放页或任务中心为止。

    不回主屏、不重拉 App（重拉影响账号权重）。返回手段优先读 WDA source 导航栏
    back / close 按钮的真实坐标，其次 OCR 容器条 / 文字按钮，固定坐标与边缘手势兜底。
    某手段连续两层无进展再换下一种。

    :return: True 已回到广告链（播放页 / 任务中心）；False App 内手段穷尽仍未回链。
    """
    method = 0  # 0 source close, 1 source back, 2 容器条, 3 胶囊×, 4 箭头, 5/6/7 手势, 8 OCR 文字
    prev_fp = None
    stuck = 0
    method_uses = {}   # 同一返回手段累计次数，超过 3 次仍没退出去就换下一种
    for _ in range(max_layers):
        method_uses[method] = method_uses.get(method, 0) + 1
        if method_uses[method] > 3 and method < 8:
            log(f"  第 {method} 种返回手段已用 {method_uses[method]} 次无进展，换下一种")
            method += 1
        cur = quick.active_bundle()
        if cur and cur not in KUAISHOU_BUNDLES:
            # 跳到第三方 App：不走它的导航栏解析，直接点顶部返回快手小字条 + 边缘手势退回快手系
            log(f"  前台为第三方 App {cur}，点左上角返回快手条")
            quick.tap2(*C_BACK_KS)
            time.sleep(POST_OPEN)
            quick.go_back(448)
            time.sleep(POST_OPEN)
            continue
        items = shot_items()
        if _on_player(items):
            return True
        if _on_center(items):
            log("  已退到任务中心（过头），停止返回，交下一轮回链")
            return True
        fp = handoff.fingerprint(items)
        if fp == prev_fp:
            stuck += 1
            if stuck >= 2:
                log("  该返回手段连续两层无进展，换下一种")
                method += 1
                stuck = 0
        else:
            stuck = 0
        prev_fp = fp

        nodes = _xml_nodes()
        back, close = _nav_buttons(nodes)
        acted = True
        if method == 0:
            if close is not None:
                quick.tap2(*close)
                log(f"  source 关闭按钮退出 {close}")
            else:
                method = 1
                acted = False
        elif method == 1:
            if back is not None:
                quick.tap2(*back)
                log(f"  source 返回箭头退出 {back}")
            else:
                method = 2
                acted = False
        elif method == 2:
            bar = next((it for it in items
                        if "快手极速版" in (it.text or "") and it.center[1] < 120), None)
            if bar is not None:
                quick.tap2(max(24, int(bar.box[0][0] + 22)), int(bar.center[1]))
                log("  容器返回退出（快手极速版导航条）")
            else:
                method = 3
                acted = False
        elif method == 3:
            quick.tap2(*C_CAPSULE_X)
            log("  固定坐标胶囊 × 兜底")
        elif method == 4:
            quick.tap2(*C_BACK)
            log("  固定坐标左上箭头兜底")
        elif method == 5:
            quick.go_back(448)
            log("  边缘右滑手势返回（中部）")
        elif method == 6:
            quick.go_back(200)
            log("  边缘右滑手势返回（上部）")
        elif method == 7:
            quick.go_back(600)
            log("  边缘右滑手势返回（下部）")
        else:
            close_btn = hit(items, ["返回", "关闭", "完成", "跳过"])
            if close_btn is not None:
                quick.tap2(*[int(v) for v in close_btn.center])
                log("  点 WebView 返回/关闭文字按钮")
            else:
                log("  App 内已无可用返回手段，停止空点")
                break

        if acted:
            time.sleep(POST_OPEN)
            GUARD.check_and_handle(items)

    # 手段穷尽后复检一次；不回主屏、不重拉 App，留待下一轮 App 内再退
    items = shot_items()
    return _on_player(items) or _on_center(items)


# 聚合页随机点击要绕开的按钮 / 表单 / 导航类文案
_AGG_AVOID = ["去看看", "立即领取", "领福利", "已领取", "返回", "关闭", "跳过", "下载",
              "立即下载", "打开", "立即打开", "查看更多", "查看详情", "同意并提交", "提交",
              "获取验证码", "免费试听", "到店礼", "去购买", "立即抢购", "领券", "预约",
              # 商品类一律不点：点进去就是商品详情 / 下单收银台，有付款风险
              "￥", "¥", "包邮", "现货", "发货", "购买", "下单", "支付", "团购", "套餐",
              "人均", "销量", "评价", "抢购", "秒杀", "优惠券", "已售"]
_AGG_SHAPE = ["学校", "店", "馆", "中心", "教育", "医院", "门诊", "培训", "画室", "基地",
              "机构", "咨询"]


def tap_in_aggregate(items):
    """聚合列表页随机点一个商户 / 商品卡片，进去看看再交给返回流程。

    只在内容区取候选，绕开顶部导航、底部表单和各类行动按钮；优先点标题形似的条目
    （含店名 / 人均 / 团购等字样），没有就随机挑一个中等长度的文本块。

    :return: True 已点击；False 没有可用候选（页面只有按钮 / 导航）。
    """
    cands = []
    for it in items:
        t = (it.text or "").strip()
        if not (5 <= len(t) <= 30):
            continue
        if any(w in t for w in _AGG_AVOID):
            continue
        x, y = it.center
        if not (40 <= x <= 380 and 200 <= y <= 1150):
            continue
        cands.append(it)
    if not cands:
        log("  聚合页没有可点的卡片，直接返回")
        return False
    prefer = [c for c in cands if any(k in (c.text or "") for k in _AGG_SHAPE)]
    it = random.choice(prefer or cands)
    log(f"  聚合页随机点「{it.text[:18]}」{it.center}")
    quick.tap2(int(it.center[0]), int(it.center[1]))
    time.sleep(random.uniform(0.4, 0.8))
    return True


def handle_opened(kind):
    """聚合页点开的页面处置：留资表单照填，在线咨询发话术（发完即退，不等结果）。

    :return: True 已按表单 / 咨询处置；False 判是咨询类但抓不到输入框，直接退出。
    """
    if kind == "lead":
        log("  点开的是留资表单，按表单流程填")
        handle_lead()
        return True
    if kind == "chat":
        nodes = _xml_nodes() or []
        editable = [n for n in nodes
                    if n["type"] in ("XCUIElementTypeTextField", "XCUIElementTypeTextView")]
        if not editable:
            log("  判为在线咨询但抓不到输入框，不空点，直接退出")
            return False
        log("  点开的是在线咨询，按咨询流程发话术")
        handle_chat()
        return True
    return False


def back_to_player(tries=3):
    """从任意落地状态退回广告播放页，退到任务中心就点「领福利」重进广告链。

    与 close_ad 的区别：close_ad 退到任务中心就算完成，这里继续往下走，直到真的
    停在播放页；中途跳到第三方 App 的点左上返回快手条 + 边缘手势拉回。

    :return: True 已停在广告播放页；False 多轮尝试仍未回链。
    """
    for i in range(tries):
        items = shot_items()
        if _on_player(items):
            return True
        log(f"  返回播放页（第 {i + 1}/{tries} 次尝试）")
        close_ad()                       # 内部逐层退，退到播放页 / 任务中心即停
        items = shot_items()
        if _on_player(items):
            log("  已回到广告播放页")
            return True
        if _on_center(items):
            log("  停在任务中心，点「领福利」重回广告链")
            handle_center()
            time.sleep(POST_OPEN)
            if _on_player(shot_items()):
                log("  已回到广告播放页")
                return True
            continue
        cur = quick.active_bundle()
        if cur and cur not in KUAISHOU_BUNDLES:
            log(f"  前台第三方 App {cur}，拉回快手")
            quick.tap2(*C_BACK_KS)
            time.sleep(POST_OPEN)
            quick.go_back(448)
            time.sleep(POST_OPEN)
    ok = _on_player(shot_items())
    if not ok:
        log(f"  {tries} 次尝试仍没回到播放页")
    return ok


CLOSE_ZONE = (0.60, 0.05, 0.995, 0.45)   # 右上角候选区（占屏比例，414×896 换算）
CLOSE_WORDS = ("关闭弹窗", "关闭", "close", "Close", "dismiss", "取消")


def _close_by_name(nodes):
    """source 里带关闭语义的节点：位置随页面自动变，不依赖固定坐标。"""
    if not nodes:
        return None
    cands = []
    for n in nodes:
        blob = (n["name"] or "") + (n["value"] or "")
        if any(k in blob for k in CLOSE_WORDS):
            cands.append(n)
    if not cands:
        return None
    # 多个命中时取最靠右上的那个（关闭按钮的常规位置）
    n = min(cands, key=lambda x: (x["center"][1], -x["center"][0]))
    return n["center"]


def _close_in_zone(items=None, nodes=None):
    """右上角区域兜底：① 小方块节点 ② 弹窗容器右上角内缩 ③ OCR 的 ×/关闭 字符。

    只在按名字找不到时用；每次只给一个候选，由 tap_close 决定点不点、成不成功。
    """
    W, H = 414, 896
    x0, y0, x1, y1 = (CLOSE_ZONE[0] * W, CLOSE_ZONE[1] * H,
                      CLOSE_ZONE[2] * W, CLOSE_ZONE[3] * H)
    nodes = nodes or []
    # ① 右上角区域内的近方形小块（关闭按钮一般 24~56pt 见方）
    sq = []
    for n in nodes:
        cx, cy = n["center"]
        if not (x0 <= cx <= x1 and y0 <= cy <= y1):
            continue
        if 24 <= n["w"] <= 56 and 24 <= n["h"] <= 56 and abs(n["w"] - n["h"]) <= 12:
            sq.append(n)
    if sq:
        n = max(sq, key=lambda x: (x["center"][0], -x["center"][1]))
        return (int(n["center"][0]), int(n["center"][1]))
    # ② 大块弹窗容器：取容器右上角内缩 26pt
    big = [n for n in nodes if n["w"] >= 250 and n["h"] >= 100 and n["center"][1] > 90]
    if big:
        b = min(big, key=lambda n: n["y"])
        return (int(b["x"] + b["w"] - 26), int(b["y"] + 26))
    # ③ OCR：右上角区域里的 × / 关闭 文本
    for it in (items or []):
        t = (it.text or "").strip()
        cx, cy = it.center
        if not (x0 <= cx <= x1 and y0 <= cy <= y1):
            continue
        if t in ("×", "✕", "x", "X", "关闭", "关 闭") or "关闭" in t:
            return (int(cx), int(cy))
    return None


def _popup_gone(before, after):
    """点完是否真的关掉了：对比前后文案，消失的占多数才算关成功（防止点错跳页也当成功）。"""
    b = {(it.text or "").strip() for it in before if (it.text or "").strip()}
    a = {(it.text or "").strip() for it in after if (it.text or "").strip()}
    gone = b - a
    return len(gone) >= max(3, int(len(b) * 0.35))


def tap_close(max_tries=3):
    """通用关弹层：按名字定位 → 区域兜底，每次点完验证文案是否消失。

    位置不写死：关闭按钮换到别处也能靠 source 节点 / 容器右上角 / OCR 字符找到。
    :return: True 已关掉一层；False 找不到或点了没效果。
    """
    for i in range(max_tries):
        items = shot_items()
        nodes = _xml_nodes()
        pos = _close_by_name(nodes) or _close_in_zone(items, nodes)
        if pos is None:
            log("  右上角没找到关闭按钮，不盲点")
            return False
        before = items
        quick.tap2(*pos)
        time.sleep(POST_OPEN)
        after = shot_items()
        if _popup_gone(before, after):
            log(f"  已点右上角关闭 {pos}")
            return True
        log(f"  {pos} 点了没关掉（第 {i + 1}/{max_tries} 次），换位置")
    return False


# 任务中心 / 播放链路运营弹窗与奖励到账弹窗的 OCR 特征词
POPUP_WORDS = ["瓜分百亿金币", "百亿金币奖池", "第三日瓜分大额金币",
               "看视频任务奖励", "金币奖励已入账", "继续看视频赚更多"]


def close_popup(items):
    """OCR 命中运营 / 奖励到账弹窗 → 点 source 里「关闭弹窗」节点；找不到回退右上 ×。

    弹窗遮罩盖住任务列表 / 广告页，不关会锚错领福利按钮。关闭钮在 source 里 name
    统一为「关闭弹窗」，高低随弹窗变化，故按节点动态定位，不写死坐标。
    :return: True 已关闭一个弹窗。
    """
    # 有「关闭」语义节点就直接点它：位置随页面变，不依赖固定坐标，也不依赖文案词表
    pos = _close_by_name(_xml_nodes())
    if pos is not None:
        quick.tap2(*pos)
        time.sleep(POST_OPEN)
        log(f"  已关闭弹窗（source 关闭节点 {pos}）")
        return True
    if not hit(items, POPUP_WORDS):
        return False
    nodes = _xml_nodes()
    close = next((n for n in nodes
                  if any(k in (n["name"] or "").lower() for k in ("关闭弹窗", "close", "dismiss"))), None)
    if close is not None:
        quick.tap2(*close["center"])
        log(f"  已关闭弹窗（source 定位 {close['center']}）")
    else:
        quick.tap2(374, 183)
        log("  已关闭弹窗（右上兜底坐标）")
    time.sleep(POST_OPEN)
    return True


def locate_welfare(items):
    """锚定「看广告得金币」任务行，取同行右侧「领福利」按钮；找不到返回 None。

    标题优先完整词「看广告得金币」；兜底「看广告」但须落在日常任务列表区（y>230），
    排除顶部红包 / 现金卡片。按钮必须与标题同一水平带（y 差 <= ROW_TOL）且在标题右侧，
    避免误点去观看 / 去搜索 / 去领取 / 宝箱。
    """
    title = hit(items, ["看广告得金币"])
    if title is None:
        title = next((it for it in items
                      if "看广告" in (it.text or "") and it.center[1] > 230), None)
    if title is None:
        return None
    ty = title.center[1]
    btn = next((it for it in items
                if "领福利" in (it.text or "")
                and abs(it.center[1] - ty) <= ROW_TOL
                and it.center[0] > title.center[0]), None)
    if btn is None:
        return None
    return (int(btn.center[0]), int(btn.center[1]))


def handle_center():
    """任务中心处置：先关弹窗，再锚定「看广告得金币」行点同行「领福利」回广告链。

    锚点是任务标题而非列表顺序；标题行找不到时兜底固定坐标。去观看 / 去瓜分 /
    悬浮宝箱等其他按钮一律不碰。
    """
    items = shot_items()
    for _ in range(3):  # 运营 / 奖励弹窗可能连续弹多个，逐个关掉再定位领福利
        if not close_popup(items):
            break
        items = shot_items()
    pos = locate_welfare(items)
    if pos is None:
        # 标题锚不到时，退回找列表内任意「领福利」按钮（同为广告任务）
        any_btn = next((it for it in items if "领福利" in (it.text or "")), None)
        pos = (int(any_btn.center[0]), int(any_btn.center[1])) if any_btn else None
    if pos is not None:
        quick.tap2(*pos)
        log(f"  任务中心：已点领福利 {pos}")
    else:
        quick.tap2(*C_WELFARE)
        log("  任务中心：未定位到领福利，兜底固定坐标点领福利")
    time.sleep(POST_OPEN)


def prepare():
    """冷启动 → 归位到「任务中心」（本任务起始页）；已在位直接返回，可重复调用。"""
    log(f"准备起始页：任务中心（{TASK_BUNDLE}）")
    quick.ensure_session()
    quick.open_app(TASK_BUNDLE, clean=True)
    time.sleep(1.8)                  # 冷启动起稳
    quick.ensure_session()           # App 被杀后旧会话失效，必须重建
    time.sleep(0.3)
    for i in range(3):
        items = shot_items()
        text = " ".join(it.text or "" for it in items)
        if "任务中心" in text and "看广告得金币" in text:
            log("已在任务中心")
            return True
        log(f"不在任务中心（第 {i + 1} 次），点红包球入口")
        quick.tap2(*C_RED_BALL)
        time.sleep(1.8)             # H5 活动页加载，后面紧接 OCR 判位
        GUARD.check_and_handle()
    log("归位未确认，按当前页面继续执行")
    return False


def ensure_chain():
    """每轮开头确认在广告链上：中心→点领福利回链；残留落地层→先退回；播放页→直接继续。"""
    items = shot_items()
    if close_popup(items):
        items = shot_items()
    if _on_center(items):
        handle_center()
    elif _on_player(items):
        return
    else:
        close_ad()


def next_ad():
    """播放页 → 胶囊 × → 领取奖励，进入下一条广告。"""
    quick.tap2(*C_CAPSULE_X)
    time.sleep(POST_OPEN)
    items = shot_items()
    GUARD.check_and_handle(items)
    btn = hit(items, ["领取奖励"])
    if btn is not None:
        quick.tap2(int(btn.center[0]), int(btn.center[1]))
    else:
        # 找不到领取按钮就别按固定坐标乱点：商品/收银台页那一带全是可下单区域
        log("  没找到「领取奖励」按钮，跳过领取，不乱点")
    time.sleep(POST_OPEN)


def run_once(round_no):
    log(f"--- 第 {round_no} 轮开始 ---")
    ensure_chain()
    # 广告刚打开即进咨询 / 落地页填表，不等播放倒计时；落地页操作与播放并行
    open_consult()

    fallback_reset = False  # 本轮 App 内未退回广告链（True 则跳过本条领取）

    # 逐层处置落地页（聚合流可能再进一层，最多 4 层）
    for depth in range(4):
        items = shot_items()
        GUARD.check_and_handle(items)
        nodes = _xml_nodes()
        kind = classify(items, nodes)
        if kind == "unknown":
            # 落地页可能还在白屏加载，短等重判一次再决定退出
            time.sleep(POLL_GAP)
            items = shot_items()
            nodes = _xml_nodes()
            kind = classify(items, nodes)
        log(f"  落地页类型：{kind}（第 {depth + 1} 层）")

        if kind in ("pay", "live"):
            # 商品购买 / 直播页：一律不点击任何内容（不点商品、不下单、不互动），
            # 只等这条广告的倒计时走完，再切下一条。
            log(f"  {kind} 页：不点击，等倒计时走完直接下一条")
            wait_countdown()
            next_ad()
            # 兜底：切条没生效、还停在收银台，才走安全退出（依然不碰支付按钮）
            if _is_pay(shot_items()) and not close_pay():
                fallback_reset = True
            break
        if kind == "login":
            log("  登录/验证墙：不填资料，直接退出")
            if not close_ad():
                fallback_reset = True
            break
        if kind == "phone":
            log("  电话咨询型：不拨号，浏览后返回")
            browse_in_place()
            if not close_ad():
                fallback_reset = True
            break
        if kind == "empty":
            browse_in_place()
            if not close_ad():
                fallback_reset = True
            break
        if kind == "lead":
            handle_lead()
            if not close_ad():
                fallback_reset = True
            break
        if kind == "chat":
            handle_chat()
            if not close_ad():
                fallback_reset = True
            break
        if kind == "aggregate":
            # 聚合列表页：滑一滑、随机点一个卡片进去看看，然后一律退回广告播放页。
            # 点开后可能是商户详情 / 表单 / 第三方 App / 又一个落地页，统一由
            # back_to_player 逐层退，退到任务中心也会点「领福利」重新回链。
            if depth == 0:
                browse_in_place(2, quiet=True)
                after = shot_items()          # 滑动后重新取帧，别用滑动前的旧坐标
                if tap_in_aggregate(after):
                    time.sleep(random.uniform(0.6, 1.2))
                    after = shot_items()
                    kind_after = classify(after, _xml_nodes())
                    log(f"  点开后判为 {kind_after}")
                    if kind_after in ("lead", "chat"):
                        # 点开的正是留资表单 / 在线咨询，按对应流程走完再退
                        handle_opened(kind_after)
                    # 点开还是聚合/详情页就不再点第二个：详情页里再点容易钻进商品和下单页
                    elif kind_after == "unknown":
                        # 详情页里咨询入口常埋在下面：往下滚一屏再判一次
                        browse_in_place(1, quiet=True)
                        after = shot_items()
                        k2 = classify(after, _xml_nodes())
                        log(f"  详情页滚一屏后判为 {k2}")
                        if k2 in ("lead", "chat"):
                            handle_opened(k2)
                if not back_to_player():
                    fallback_reset = True
            else:
                log("  仍是聚合页，直接再退一层")
                if not close_ad():
                    fallback_reset = True
            break
        if kind == "center":
            log("  任务中心（可能叠着弹层）：关弹窗后点「领福利」回广告链")
            handle_center()
            break
        if kind == "player":
            log("  仍在播放页，无需落地处置")
            break
        if kind == "unknown":
            handle_unknown(items, round_no, depth)
            browse_in_place()
            if not close_ad():
                fallback_reset = True
            break
        # 兜底关闭，避免卡死
    else:
        log("  落地页层级超出预期，关闭当前页")
        if not close_ad():
            fallback_reset = True

    if fallback_reset:
        log("  本轮 App 内未退回广告链，跳过本条领取，下一轮继续返回")
    else:
        # 落地页操作完成后回到播放页，补齐倒计时再切下一条
        wait_countdown()
        next_ad()
    log(f"--- 第 {round_no} 轮结束 ---")


def _apply_cli():
    """解析命令行：位置参数 rounds，可选 --minutes / --app（组合编排透传用）。"""
    global DEFAULT_ROUNDS, MINUTES, TASK_BUNDLE
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("rounds", nargs="?", default=None)
    p.add_argument("--minutes", default=None)
    p.add_argument("--app", default=None, choices=["kuaishou", "nebula"])
    a = p.parse_args()
    if a.rounds:
        DEFAULT_ROUNDS = int(a.rounds)
    if a.app:
        TASK_BUNDLE = APP_BUNDLES[a.app]
    if a.minutes:
        MINUTES = float(a.minutes)
        DEFAULT_ROUNDS = 0        # 按时间跑时，轮次不参与


def main():
    _apply_cli()
    max_rounds = DEFAULT_ROUNDS
    rounds = 0
    print(f"=== {TASK_NAME} 启动（Ctrl+C 停止）===")
    conn = quick.ensure_session()
    if conn.get("error"):
        print(f"连接 WDA 失败：{conn['error']}")
        return
    if conn.get("reconnected"):
        print("WDA 会话已重建")

    # 同上：单跑停在快手系里就接着跑，别重开 App 把手机带回桌面。
    if MINUTES > 0 or quick.active_bundle() not in KUAISHOU_BUNDLES:
        prepare()
    deadline = 0.0
    if MINUTES > 0:
        deadline = time.time() + MINUTES * 60
        print(f"按时间跑 {MINUTES:g} 分钟，到点跑完当前一轮退出", flush=True)

    try:
        while True:
            if deadline and time.time() >= deadline:
                print(f"跑满 {MINUTES:g} 分钟，退出", flush=True)
                break
            if max_rounds and rounds >= max_rounds:
                print(f"跑满 {rounds} 轮，退出")
                break
            rounds += 1
            try:
                run_once(rounds)
            except Exception as e:
                log(f"第 {rounds} 轮异常：{type(e).__name__}: {str(e)[:90]}")
            if max_rounds and rounds >= max_rounds:
                break
            time.sleep(ROUND_GAP)
    except KeyboardInterrupt:
        print("手动停止")


if __name__ == "__main__":
    main()
