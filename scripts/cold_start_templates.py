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
  Day1     加 1 好友 + 关注 2 行业公众号 + 读文 10 分钟并留言
  Day2-3   加 2 好友 + 关注 2 行业公众号 + 读文 10 分钟并留言
  Day4-7   发 1 条生活类朋友圈（相册原创配图）+ 官方小游戏（跳一跳）
  Day8-10  加 3 好友 + 5 次 1v1 深聊（每次 >5 分钟）+ 朋友圈互动 20 次
  Day11    视频号 10 分钟（完播 + 评论）
  Day12-14 日常活跃：每周 4-6 条朋友圈（生活:工作≈3:2）、小程序 3 次/周
"""

from __future__ import annotations

from scripts.base_script import (
    Action,
    ActionType,
    channels_daily_params,
    moments_daily_params,
)


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
    d = max(1, int(day_index))
    if d == 1:
        return _day1(is_weekend)
    if d in (2, 3):
        return _day2_3(d, is_weekend)
    if 4 <= d <= 7:
        return _day4_7(d, is_weekend)
    if 8 <= d <= 10:
        return _day8_10(d, is_weekend)
    if d == 11:
        return _day11(is_weekend)
    if 12 <= d <= 14:
        return _day12_14(d, is_weekend)
    return _post_14(d, is_weekend)


def _sleep_action(is_weekend: bool) -> Action:
    if is_weekend:
        return Action(ActionType.SLEEP, "00:00", "08:00", (0, 0))
    return Action(ActionType.SLEEP, "23:00", "07:00", (0, 0))


def _day1_3_add_friend_count(day_index: int) -> int:
    """Day1-3 种子好友数；超出相位范围时返回 0。"""
    return int(DAY1_3_ADD_FRIEND_SCHEDULE.get(max(1, int(day_index)), 0))


def _day1(is_weekend: bool) -> list[Action]:
    """
    Day1 专项：
      1. 添加 1 个种子好友
      2. 关注 2 个行业相关公众号
      3. 阅读公众号推文约 10 分钟并留言
    """
    morning = "08:30" if is_weekend else "07:30"
    return [
        Action(ActionType.OPEN_WECHAT, morning, "09:30", (180, 480)),
        Action(ActionType.ADD_FRIEND, "09:00", "10:30", (90, 180),
               params={"count": 1, "source": "day1_seed_friend"}),
        Action(ActionType.FOLLOW_PUBLIC_ACCOUNT, "09:30", "11:00", (60, 180),
               params={"count": 2, "industry_only": True}),
        Action(ActionType.READ_ARTICLE, "11:00", "13:00", (600, 900),
               params={
                   "duration": 600,  # 阅读约 10 分钟
                   "comment_rate": 1.0,  # 读完尽量留言（不因成功 1 条而停评）
                   "require_comment": True,
               }),
        _sleep_action(is_weekend),
    ]


def _day2_3(day_index: int, is_weekend: bool) -> list[Action]:
    """
    Day2-3 专项（与 Day1 同结构，加好友 2 位）:
      1. 添加 2 个种子好友
      2. 关注 2 个行业相关公众号
      3. 阅读公众号推文约 10 分钟并留言
    """
    d = max(1, int(day_index))
    morning = "08:30" if is_weekend else "07:30"
    add_count = _day1_3_add_friend_count(d)
    return [
        Action(ActionType.OPEN_WECHAT, morning, "09:30", (180, 480)),
        Action(ActionType.ADD_FRIEND, "09:00", "10:30", (90, 180),
               params={
                   "count": add_count,
                   "source": f"day{min(d, 3)}_seed_friend",
               }),
        Action(ActionType.FOLLOW_PUBLIC_ACCOUNT, "09:30", "11:00", (60, 180),
               params={"count": 2, "industry_only": True}),
        Action(ActionType.READ_ARTICLE, "11:00", "13:00", (600, 900),
               params={
                   "duration": 600,
                   "comment_rate": 1.0,
                   "require_comment": True,
               }),
        _sleep_action(is_weekend),
    ]


def _day4_7(day_index: int, is_weekend: bool) -> list[Action]:
    """内容生态：发 1 条生活类朋友圈（相册原创配图）+ 官方小游戏。"""
    morning = "08:30" if is_weekend else "07:30"
    return [
        Action(ActionType.OPEN_WECHAT, morning, "09:30", (180, 480)),
        Action(ActionType.POST_MOMENT, "10:00", "12:00", (180, 300),
               params={
                   "topic": "生活",
                   "smart_select": True,
               }),
        Action(ActionType.PLAY_MINI_GAME, "14:00", "16:00", (120, 240),
               params={"duration": 180}),
        _sleep_action(is_weekend),
    ]


def _day8_10(day_index: int, is_weekend: bool) -> list[Action]:
    """深度互动：加 3 好友 + 5 次 1v1 深聊（每次 >5 分钟）+ 朋友圈互动 20 次。"""
    morning = "08:30" if is_weekend else "07:30"
    deep_chat_slots = [
        ("10:00", "10:45", (300, 360)),
        ("11:00", "11:45", (300, 360)),
        ("13:30", "14:15", (300, 360)),
        ("15:00", "15:45", (300, 360)),
        ("17:00", "17:45", (300, 360)),
    ]
    actions: list[Action] = [
        Action(ActionType.OPEN_WECHAT, morning, "09:30", (180, 480)),
        Action(ActionType.ADD_FRIEND, "09:00", "10:00", (60, 120),
               params={"count": 3, "source": f"day{int(day_index)}_deep_seed"}),
    ]
    for start, end, duration in deep_chat_slots:
        actions.append(
            Action(ActionType.DEEP_CHAT, start, end, duration,
                   params={"duration": 320}),
        )
    actions.extend([
        Action(ActionType.MOMENTS_DAILY_INTERACT, "18:30", "20:30", (600, 900),
               params=moments_daily_params(20)),
        _sleep_action(is_weekend),
    ])
    return actions


def _day11(is_weekend: bool) -> list[Action]:
    """Day11：视频号 10 分钟（完播 + 评论）。"""
    morning = "08:30" if is_weekend else "07:30"
    return [
        Action(ActionType.OPEN_WECHAT, morning, "09:30", (180, 480)),
        Action(ActionType.SCROLL_CHANNELS, "10:00", "11:30", (600, 720),
               params=channels_daily_params(600, comment=True, comment_rate=0.18)),
        _sleep_action(is_weekend),
    ]


def _day12_14(day_index: int, is_weekend: bool) -> list[Action]:
    """
    Day12-14 日常活跃维持（周指标）:
      - 每周 4-6 条朋友圈，生活:工作 ≈ 3:2（Day4-7 已发 4 条生活，此处补工作向）
      - 每周小程序 3 次（Day12/13/14 各 1 次）
    """
    morning = "08:30" if is_weekend else "07:30"
    d = int(day_index)
    actions: list[Action] = [
        Action(ActionType.OPEN_WECHAT, morning, "09:30", (180, 480)),
        Action(ActionType.BROWSE_MINI_PROGRAM, "10:00", "11:00", (90, 180),
               params={"duration": 120}),
    ]
    if d == 12:
        actions.append(
            Action(ActionType.POST_MOMENT, "11:30", "13:00", (180, 300),
                   params={"topic": "工作", "smart_select": True}),
        )
    elif d == 13:
        actions.append(
            Action(ActionType.POST_MOMENT, "11:30", "13:00", (180, 300),
                   params={"topic": "生活", "smart_select": True}),
        )
    elif d == 14:
        actions.append(
            Action(ActionType.POST_MOMENT, "11:30", "13:00", (180, 300),
                   params={"topic": "工作", "smart_select": True}),
        )
        actions.append(
            Action(ActionType.MOMENTS_DAILY_INTERACT, "15:00", "17:00", (480, 720),
                   params=moments_daily_params(10)),
        )
    actions.append(_sleep_action(is_weekend))
    return actions


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
        "group_chat": 0,
        "like_moment": 0,
        "comment_moment": 0,
        "mass_send": 0,
        "auto_reply": 0,
    },
    "day8_10": {
        "add_friend": 3,
        "post_moment": 0,
        "deep_chat": 5,
        "group_chat": 0,
        "like_moment": 20,
        "comment_moment": 0,
        "mass_send": 0,
        "auto_reply": 0,
    },
    "day11_14": {
        "add_friend": 0,
        "post_moment": 1,
        "deep_chat": 0,
        "group_chat": 0,
        "like_moment": 10,
        "comment_moment": 0,
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
