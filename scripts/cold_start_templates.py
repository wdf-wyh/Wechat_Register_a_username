# -*- coding: utf-8 -*-
"""
新号 14 天冷启动剧本模板（规则兜底）。

对应「防封养号指南」中可自动化部分；人工项已剔除：
  - 换绑卡/发红包/线下真实消费/改头像昵称签名
  - 真实大额交易

行为层禁忌（硬限，AI clamp + 运行时双重兜底）:
  - 首周加好友 ≤ 3 人/天（Day1-3 按 1/2/2 种子节奏，Day4-7 = 0）
  - 前两周禁止群发、禁止自动回复（见 FORBIDDEN_BEHAVIOR_ACTIONS）
  - 禁止凌晨频繁操作（活跃窗仅 07:00–23:00，须含 sleep）

阶段切分（注册日起算 day_index，从 1 开始）:
  Day1-3   身份塑造 · 社交种子
  Day4-7   内容生态
  Day8-10  深度互动
  Day11-14 场景渗透
"""

from __future__ import annotations

from scripts.base_script import Action, ActionType, channels_daily_params


# 人工专属动作（AI/规则均不得排入）
MANUAL_ONLY_ACTIONS = frozenset({
    "bind_bank",
    "send_red_packet",
    "offline_pay",
    "edit_profile",
    "real_purchase",
    "create_group",
})

# 行为层禁忌：系统永不编排（群发助手 / 自动回复类）
FORBIDDEN_BEHAVIOR_ACTIONS = frozenset({
    "mass_send",
    "broadcast_message",
    "group_broadcast",
    "auto_reply",
    "auto_reply_message",
})

# 首周加好友绝对天花板（保证通过率；相位硬限可更严）
WEEK1_ADD_FRIEND_CAP = 3
# 前两周禁止群发/自动回复的天数上界（含）
NO_MASS_AUTO_REPLY_DAYS = 14
# Day1-3 社交种子好友节奏：第 1 天 1 位，第 2 天 2 位，第 3 天 2 位
DAY1_3_ADD_FRIEND_SCHEDULE = {
    1: 1,
    2: 2,
    3: 2,
}


def cold_start_phase(day_index: int) -> str:
    """返回 phase 名。day_index 从 1 起；<=0 按 1。"""
    d = max(1, int(day_index))
    if d <= 3:
        return "day1_3"
    if d <= 7:
        return "day4_7"
    if d <= 10:
        return "day8_10"
    if d <= 14:
        return "day11_14"
    return "post_14"


def build_cold_start_actions(day_index: int, is_weekend: bool = False) -> list[Action]:
    """按注册天数生成当日动作列表。"""
    phase = cold_start_phase(day_index)
    builders = {
        "day1_3": _day1_3,
        "day4_7": _day4_7,
        "day8_10": _day8_10,
        "day11_14": _day11_14,
        "post_14": _post_14,
    }
    return builders[phase](day_index, is_weekend)


def _sleep_action(is_weekend: bool) -> Action:
    if is_weekend:
        return Action(ActionType.SLEEP, "00:00", "08:00", (0, 0))
    return Action(ActionType.SLEEP, "23:00", "07:00", (0, 0))


def _day1_3_add_friend_count(day_index: int) -> int:
    """Day1-3 种子好友数；超出相位范围时返回 0。"""
    return int(DAY1_3_ADD_FRIEND_SCHEDULE.get(max(1, int(day_index)), 0))


def _day1_3(day_index: int, is_weekend: bool) -> list[Action]:
    """社交种子：加高粘性好友、关注公众号、读文 10 分钟。"""
    morning = "08:30" if is_weekend else "07:30"
    add_count = _day1_3_add_friend_count(day_index)
    return [
        Action(ActionType.OPEN_WECHAT, morning, "09:30", (180, 480)),
        Action(ActionType.SCROLL_MOMENTS, "08:00", "09:30", (300, 600)),
        Action(ActionType.ADD_FRIEND, "09:00", "10:30", (90, 180),
               params={
                   "count": add_count,
                   "source": f"day{min(max(1, int(day_index)), 3)}_seed_friend",
               }),
        Action(ActionType.FOLLOW_PUBLIC_ACCOUNT, "09:30", "11:00", (60, 180),
               params={"count": 2}),
        Action(ActionType.READ_ARTICLE, "11:30", "13:00", (600, 900),
               params={"duration": 600}),
        Action(ActionType.FAVORITE_ARTICLE, "12:30", "13:30", (30, 120)),
        # 每日视频号约 10 分钟完播（新号前期不评论）
        Action(ActionType.SCROLL_CHANNELS, "13:30", "15:00", (600, 720),
               params=channels_daily_params(600, comment=False)),
        Action(ActionType.GLOBAL_SEARCH, "15:00", "16:30", (60, 180)),
        Action(ActionType.BROWSE_MINI_PROGRAM, "16:30", "17:30", (90, 180),
               params={"duration": 120}),
        Action(ActionType.MAKE_PAYMENT, "17:30", "18:30", (60, 180)),
        Action(ActionType.SCROLL_MOMENTS, "19:00", "20:30", (300, 600)),
        _sleep_action(is_weekend),
    ]


def _day4_7(day_index: int, is_weekend: bool) -> list[Action]:
    """内容生态：开始发圈、群发言、小程序。"""
    morning = "08:30" if is_weekend else "07:30"
    return [
        Action(ActionType.OPEN_WECHAT, morning, "09:30", (180, 480)),
        Action(ActionType.SCROLL_MOMENTS, "08:00", "09:30", (300, 600)),
        Action(ActionType.POST_MOMENT, "10:00", "12:00", (180, 300),
               params={"topic": "生活"}),
        Action(ActionType.GROUP_CHAT, "11:00", "13:00", (120, 300),
               params={"count": 3}),
        Action(ActionType.READ_ARTICLE, "12:30", "14:00", (300, 600),
               params={"duration": 480}),
        Action(ActionType.SCROLL_CHANNELS, "14:00", "16:00", (600, 720),
               params=channels_daily_params(600, comment=True, comment_rate=0.12)),
        Action(ActionType.BROWSE_MINI_PROGRAM, "16:00", "17:30", (120, 240),
               params={"duration": 180}),
        Action(ActionType.GROUP_CHAT, "18:00", "19:30", (60, 180),
               params={"count": 2}),
        Action(ActionType.SCROLL_MOMENTS, "19:30", "21:00", (300, 600)),
        Action(ActionType.LIKE_MOMENT, "20:00", "21:00", (60, 120),
               params={"count": (2, 4)}),
        _sleep_action(is_weekend),
    ]


def _day8_10(day_index: int, is_weekend: bool) -> list[Action]:
    """深度互动：限量加好友、深聊、朋友圈高频互动。"""
    morning = "08:30" if is_weekend else "07:30"
    return [
        Action(ActionType.OPEN_WECHAT, morning, "09:30", (180, 480)),
        Action(ActionType.SCROLL_MOMENTS, "08:00", "09:30", (300, 600)),
        Action(ActionType.BROWSE_MOMENTS_INTERACT, "09:00", "11:00", (600, 900),
               params={"duration": 600, "like_rate": 0.45}),
        Action(ActionType.ADD_FRIEND, "10:30", "12:00", (60, 180),
               params={"count": 2}),
        Action(ActionType.DEEP_CHAT, "11:30", "13:30", (300, 420),
               params={"rounds": 5, "duration": 320}),
        Action(ActionType.SEND_MESSAGE, "14:00", "15:30", (60, 180)),
        Action(ActionType.SCROLL_CHANNELS, "15:00", "16:30", (600, 720),
               params=channels_daily_params(600, comment=True, comment_rate=0.15)),
        Action(ActionType.DEEP_CHAT, "17:00", "19:00", (300, 420),
               params={"rounds": 4, "duration": 300}),
        Action(ActionType.LIKE_MOMENT, "19:00", "20:30", (60, 180),
               params={"count": (4, 8)}),
        Action(ActionType.COMMENT_MOMENT, "19:30", "21:00", (30, 120)),
        Action(ActionType.GROUP_CHAT, "20:00", "21:30", (60, 180),
               params={"count": 2}),
        Action(ActionType.SCROLL_MOMENTS, "21:30", "22:30", (180, 360)),
        _sleep_action(is_weekend),
    ]


def _day11_14(day_index: int, is_weekend: bool) -> list[Action]:
    """场景渗透：视频号加长观看+评论、小程序、继续互动。"""
    morning = "08:30" if is_weekend else "07:30"
    return [
        Action(ActionType.OPEN_WECHAT, morning, "09:30", (180, 480)),
        Action(ActionType.SCROLL_MOMENTS, "08:00", "09:30", (300, 600)),
        Action(ActionType.BROWSE_MOMENTS_INTERACT, "09:00", "10:30", (480, 720),
               params={"duration": 480, "like_rate": 0.4}),
        Action(ActionType.ADD_FRIEND, "10:30", "12:00", (60, 120),
               params={"count": 1}),
        Action(ActionType.DEEP_CHAT, "11:30", "13:30", (300, 420),
               params={"rounds": 5, "duration": 320}),
        Action(ActionType.SCROLL_CHANNELS, "13:30", "15:30", (600, 720),
               params=channels_daily_params(600, comment=True, comment_rate=0.22)),
        Action(ActionType.BROWSE_MINI_PROGRAM, "16:00", "17:30", (120, 240),
               params={"duration": 200}),
        Action(ActionType.POST_MOMENT, "17:30", "19:00", (180, 300),
               params={"topic": "日常"}),
        Action(ActionType.GROUP_CHAT, "19:00", "20:30", (90, 180),
               params={"count": 3}),
        Action(ActionType.MAKE_PAYMENT, "18:00", "19:00", (60, 120)),
        _sleep_action(is_weekend),
    ]


def _post_14(day_index: int, is_weekend: bool) -> list[Action]:
    """超过 14 天仍处 trust 阶段时的保守维持。"""
    morning = "08:30" if is_weekend else "07:30"
    return [
        Action(ActionType.OPEN_WECHAT, morning, "09:30", (180, 480)),
        Action(ActionType.SCROLL_MOMENTS, "08:00", "10:00", (300, 600)),
        Action(ActionType.LIKE_MOMENT, "09:00", "10:30", (60, 120),
               params={"count": (1, 3)}),
        Action(ActionType.SEND_MESSAGE, "11:00", "13:00", (120, 240)),
        Action(ActionType.SCROLL_CHANNELS, "13:00", "15:00", (600, 720),
               params=channels_daily_params(600, comment=True)),
        Action(ActionType.READ_ARTICLE, "15:00", "17:00", (300, 600)),
        Action(ActionType.BROWSE_MINI_PROGRAM, "17:00", "18:00", (90, 180)),
        Action(ActionType.SCROLL_MOMENTS, "19:00", "21:00", (300, 600)),
        Action(ActionType.COMMENT_MOMENT, "20:00", "21:00", (30, 90)),
        _sleep_action(is_weekend),
    ]


# 阶段硬限（AI 输出后也会再 clamp）
# add_friend: Day1-3 走 1/2/2 种子节奏；Day4-7 = 0；Day8-10 才到 3
PHASE_HARD_LIMITS = {
    "day1_3": {
        "add_friend": 2,
        "post_moment": 0,
        "deep_chat": 0,
        "group_chat": 0,
        "like_moment": 0,
        "comment_moment": 0,
        "mass_send": 0,
        "auto_reply": 0,
    },
    "day4_7": {
        "add_friend": 0,
        "post_moment": 1,
        "deep_chat": 0,
        "group_chat": 5,
        "like_moment": 5,
        "comment_moment": 1,
        "mass_send": 0,
        "auto_reply": 0,
    },
    "day8_10": {
        "add_friend": 3,  # 首周后上限，仍 ≤ WEEK1_ADD_FRIEND_CAP 语义延伸
        "post_moment": 1,
        "deep_chat": 5,
        "group_chat": 4,
        "like_moment": 20,
        "comment_moment": 8,
        "mass_send": 0,
        "auto_reply": 0,
    },
    "day11_14": {
        "add_friend": 2,
        "post_moment": 1,
        "deep_chat": 4,
        "group_chat": 4,
        "like_moment": 15,
        "comment_moment": 6,
        "mass_send": 0,
        "auto_reply": 0,
    },
    "post_14": {
        "add_friend": 1,
        "post_moment": 1,
        "deep_chat": 2,
        "group_chat": 3,
        "like_moment": 8,
        "comment_moment": 3,
        "mass_send": 0,   # 成熟前仍禁止群发助手
        "auto_reply": 0,
    },
}


def max_add_friends_for_day(day_index: int) -> int:
    """
    加好友当日绝对上限。
    Day1-3 按 1/2/2；其余首周遵守相位硬限且 ≤3；之后取相位硬限。
    """
    d = max(1, int(day_index))
    phase = cold_start_phase(d)
    if phase == "day1_3":
        return _day1_3_add_friend_count(d)
    phase_cap = int(PHASE_HARD_LIMITS.get(phase, {}).get("add_friend", 0))
    if d <= 7:
        return min(phase_cap, WEEK1_ADD_FRIEND_CAP)
    return phase_cap
