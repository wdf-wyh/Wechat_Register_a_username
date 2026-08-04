"""
账号阶段定义 — 各养号阶段的参数配置与限量规则。

各阶段说明:
    trust_building  — 信任积累期（第1-2周）：14 天冷启动分相位（见 cold_start_templates）
    light_interact  — 轻度互动期（第3-4周）：开始稳定互动
    normal_use      — 正常使用期（第2-3个月）：正常社交
    mature          — 成熟期（3个月后）：可投入测试
"""

from enum import Enum
from datetime import date, datetime


class AccountStage(Enum):
    TRUST_BUILDING = "trust_building"
    LIGHT_INTERACT = "light_interact"
    NORMAL_USE = "normal_use"
    MATURE = "mature"


# 各阶段时长定义（天）
STAGE_DURATION_DAYS = {
    AccountStage.TRUST_BUILDING: 14,    # 2 周（冷启动黄金期）
    AccountStage.LIGHT_INTERACT: 14,    # 2 周
    AccountStage.NORMAL_USE: 60,        # 约 2 个月
    # 之后自动进入 MATURE
}

# 各阶段每日操作限量（上限；具体日节奏由剧本/AI 再细分）
STAGE_CONFIGS = {
    AccountStage.TRUST_BUILDING: {
        "name": "信任积累期",
        "daily_add_friends": 3,              # 首周绝对天花板；Day1-7 相位硬限为 0
        "weekly_post_moments": 3,            # Day4+ 才发圈
        "daily_chat_contacts": 5,
        "daily_payments": 1,                 # 仅打开支付页，非真实交易
        "daily_scroll_moments_min": 15,
        "voice_call_weekly": 0,
        "description": "14天冷启动：先基建消费，中后期轻互动",
    },
    AccountStage.LIGHT_INTERACT: {
        "name": "轻度互动期",
        "daily_add_friends": 2,
        "weekly_post_moments": 4,
        "daily_chat_contacts": 3,
        "daily_payments": 2,
        "daily_scroll_moments_min": 10,
        "voice_call_weekly": 1,
        "description": "开始轻量社交互动，控制频率",
    },
    AccountStage.NORMAL_USE: {
        "name": "正常使用期",
        "daily_add_friends": 5,
        "weekly_post_moments": 6,
        "daily_chat_contacts": 5,
        "daily_payments": 3,
        "daily_scroll_moments_min": 5,
        "voice_call_weekly": 3,
        "description": "模拟正常用户社交频率（约每周4-6条朋友圈）",
    },
    AccountStage.MATURE: {
        "name": "成熟期",
        "daily_add_friends": 15,
        "weekly_post_moments": 14,
        "daily_chat_contacts": 10,
        "daily_payments": 5,
        "daily_scroll_moments_min": 0,
        "voice_call_weekly": 5,
        "description": "账号已成熟，可用于 benchmark 测试",
    },
}


def get_stage_for_account(registration_date: str) -> AccountStage:
    """
    根据注册日期计算当前应处于哪个阶段。

    Args:
        registration_date: 注册日期字符串 "2026-07-01"

    Returns:
        对应的 AccountStage
    """
    reg_date = datetime.strptime(registration_date, "%Y-%m-%d").date()
    days_since_reg = (date.today() - reg_date).days

    if days_since_reg < 0:
        raise ValueError(f"注册日期 {registration_date} 不能是未来日期")

    cumulative = 0
    for stage in [AccountStage.TRUST_BUILDING, AccountStage.LIGHT_INTERACT, AccountStage.NORMAL_USE]:
        cumulative += STAGE_DURATION_DAYS[stage]
        if days_since_reg < cumulative:
            return stage

    return AccountStage.MATURE


def get_daily_limits(stage: AccountStage) -> dict:
    """获取当前阶段的每日限量"""
    return STAGE_CONFIGS.get(stage, STAGE_CONFIGS[AccountStage.MATURE])
