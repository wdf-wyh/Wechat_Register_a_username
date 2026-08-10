# -*- coding: utf-8 -*-
"""
AI 上帝视角编排器 — 根据账号状态自动生成当日养号剧本。

职责（上帝视角）:
  1. 汇总账号上下文：注册天数、阶段、健康状态、近期失败、好友/群资源
  2. 调用 LLM 生成当日动作编排（时间窗 + 动作 + 参数）
  3. 用硬性安全规则 clamp（夜间禁操作、人工动作剔除、阶段上限）
  4. LLM 不可用或解析失败时，回退到 14 天规则模板

使用方式:
    from content.ai_god_planner import AiGodPlanner
    planner = AiGodPlanner(db, persona)
    actions = planner.plan_day(account_id)
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime
from typing import Any, Optional

from config.settings import settings
from content.llm_client import LLMClient
from content.personas import get_public_account_candidates
from scripts.base_script import Action, ActionType
from scripts.cold_start_templates import (
    FORBIDDEN_BEHAVIOR_ACTIONS,
    MANUAL_ONLY_ACTIONS,
    NO_MASS_AUTO_REPLY_DAYS,
    PHASE_HARD_LIMITS,
    WEEK1_ADD_FRIEND_CAP,
    build_cold_start_actions,
    cold_start_phase,
    max_add_friends_for_day,
)
from utils.logger import get_logger

logger = get_logger("ai_god_planner")

# 允许 AI 编排的动作白名单
ALLOWED_ACTION_TYPES = {a.value for a in ActionType}


class AiGodPlanner:
    """账号级当日剧本上帝编排。"""

    def __init__(self, db, persona: dict):
        self.db = db
        self.persona = persona or {}
        self.llm = LLMClient()

    def plan_day(
        self,
        account_id: str,
        is_weekend: Optional[bool] = None,
    ) -> list[Action]:
        """
        生成当日 Action 列表。

        优先 AI；失败或关闭时用规则模板。
        """
        if is_weekend is None:
            is_weekend = datetime.now().weekday() >= 5

        ctx = self._build_context(account_id)
        phase = ctx["phase"]
        day_index = ctx["day_index"]

        logger.info(
            f"[{account_id}] 上帝视角编排: day={day_index} phase={phase} "
            f"state={ctx.get('state')} mode={ctx.get('mode')} "
            f"ai={'on' if settings.USE_AI_GOD_PLANNER and self.llm.available else 'off'}"
        )

        actions: list[Action] = []
        if settings.USE_AI_GOD_PLANNER and self.llm.available:
            raw = self.llm.plan_daily_nurture(ctx, self.persona)
            actions = self._parse_actions(raw)

        if not actions:
            # 仅冷启动 14 天内用规则模板；之后交给各阶段静态剧本
            if day_index <= 14 or ctx.get("stage") == "trust_building":
                logger.info(f"[{account_id}] 使用规则模板兜底 phase={phase}")
                actions = build_cold_start_actions(day_index, is_weekend=is_weekend)
            else:
                logger.info(
                    f"[{account_id}] AI 未产出且已过冷启动，交回阶段静态剧本"
                )
                return []
        else:
            logger.info(f"[{account_id}] AI 编排通过，原始动作 {len(actions)} 个")

        # consume_only：剔除互动类
        if ctx.get("mode") == "consume_only":
            actions = self._filter_consume_only(actions)

        actions = self._clamp_actions(actions, phase, day_index=day_index)
        actions = self._ensure_sleep(actions, is_weekend)

        logger.info(
            f"[{account_id}] 最终剧本 {len(actions)} 个动作: "
            + ", ".join(a.action_type.value for a in actions if a.action_type != ActionType.SLEEP)
        )
        return actions

    # ================================================================
    # 上下文
    # ================================================================

    def _build_context(self, account_id: str) -> dict[str, Any]:
        account = self.db.get_account(account_id) or {}
        reg = account.get("registration_date") or date.today().isoformat()
        try:
            reg_date = datetime.strptime(reg[:10], "%Y-%m-%d").date()
            day_index = (date.today() - reg_date).days + 1
        except Exception:
            day_index = 1

        day_index = max(1, day_index)
        phase = cold_start_phase(day_index)

        friends = self.db.get_friends(account_id)
        groups = [f for f in friends if (f.get("source") or "") == "group"]
        people = [f for f in friends if (f.get("source") or "") != "group"]

        today_stats = {}
        try:
            today_stats = self.db.get_today_stats(account_id) or {}
        except Exception:
            pass

        health = None
        try:
            health = self.db.get_latest_health_check(account_id)
        except Exception:
            pass

        recent_fails = []
        try:
            recent_fails = self.db.get_recent_failed_actions(account_id, limit=8)
        except Exception:
            pass

        return {
            "account_id": account_id,
            "day_index": day_index,
            "phase": phase,
            "stage": account.get("stage", "trust_building"),
            "state": account.get("state", "normal"),
            "mode": account.get("mode", "full"),
            "friend_count": len(people),
            "group_count": len(groups),
            "industry": self.persona.get("industry", ""),
            "seed_friends": self.persona.get("seed_friends", [])[:8],
            "seed_groups": self.persona.get("seed_groups", [])[:5],
            "public_accounts": get_public_account_candidates(self.persona, count=8),
            "today_stats": today_stats,
            "health": health or {},
            "recent_fails": recent_fails,
            "hard_limits": PHASE_HARD_LIMITS.get(phase, {}),
            "add_friend_cap_today": max_add_friends_for_day(day_index),
            "manual_forbidden": sorted(MANUAL_ONLY_ACTIONS),
            "behavior_forbidden": sorted(FORBIDDEN_BEHAVIOR_ACTIONS),
            "behavior_taboos": [
                f"首周加好友≤{WEEK1_ADD_FRIEND_CAP}人/天（今日相位上限见 add_friend_cap_today）",
                f"前{NO_MASS_AUTO_REPLY_DAYS}天禁止群发、禁止自动回复",
                "禁止凌晨频繁操作（仅 07:00-23:00）",
            ],
            "allowed_actions": sorted(ALLOWED_ACTION_TYPES),
        }

    # ================================================================
    # 解析 / 校验
    # ================================================================

    def _parse_actions(self, raw: str) -> list[Action]:
        if not raw:
            return []
        data = self._extract_json(raw)
        if not data:
            return []

        items = data.get("actions") if isinstance(data, dict) else data
        if not isinstance(items, list):
            return []

        actions: list[Action] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            type_name = str(item.get("type") or item.get("action_type") or "").strip()
            if type_name in MANUAL_ONLY_ACTIONS or type_name in FORBIDDEN_BEHAVIOR_ACTIONS:
                continue
            if type_name not in ALLOWED_ACTION_TYPES:
                logger.debug(f"忽略未知动作: {type_name}")
                continue
            try:
                at = ActionType(type_name)
            except ValueError:
                continue

            window = item.get("window") or item.get("time_window") or ["09:00", "10:00"]
            if not isinstance(window, (list, tuple)) or len(window) < 2:
                window = ["09:00", "10:00"]
            start, end = str(window[0]), str(window[1])

            duration = item.get("duration") or item.get("duration_seconds") or [60, 180]
            if isinstance(duration, (int, float)):
                duration = (int(duration), int(duration))
            elif isinstance(duration, (list, tuple)) and len(duration) >= 2:
                duration = (int(duration[0]), int(duration[1]))
            else:
                duration = (60, 180)

            params = item.get("params") or {}
            if not isinstance(params, dict):
                params = {}

            actions.append(Action(at, start, end, duration, params=params))
        return actions

    def _extract_json(self, text: str) -> Any:
        text = text.strip()
        # 直接解析
        try:
            return json.loads(text)
        except Exception:
            pass
        # 代码块
        m = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
        if m:
            try:
                return json.loads(m.group(1).strip())
            except Exception:
                pass
        # 截取首尾大括号
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except Exception:
                pass
        start = text.find("[")
        end = text.rfind("]")
        if start >= 0 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except Exception:
                pass
        logger.warning("无法解析 AI 剧本 JSON")
        return None

    def _clamp_actions(
        self,
        actions: list[Action],
        phase: str,
        day_index: int = 1,
    ) -> list[Action]:
        limits = dict(PHASE_HARD_LIMITS.get(phase, {}))
        # 首周加好友绝对天花板（即使相位被误配也不超过）
        add_cap = max_add_friends_for_day(day_index)
        if "add_friend" in limits:
            limits["add_friend"] = min(int(limits["add_friend"]), add_cap)
        # 前两周强制禁群发/自动回复
        if day_index <= NO_MASS_AUTO_REPLY_DAYS:
            limits["mass_send"] = 0
            limits["auto_reply"] = 0

        counters: dict[str, int] = {}
        kept: list[Action] = []

        # 动作类型 → 限额键
        type_to_limit = {
            ActionType.ADD_FRIEND: "add_friend",
            ActionType.POST_MOMENT: "post_moment",
            ActionType.DEEP_CHAT: "deep_chat",
            ActionType.GROUP_CHAT: "group_chat",
            ActionType.LIKE_MOMENT: "like_moment",
            ActionType.COMMENT_MOMENT: "comment_moment",
            ActionType.BROWSE_MOMENTS_INTERACT: "like_moment",
            ActionType.MOMENTS_DAILY_INTERACT: "like_moment",
        }

        for action in actions:
            if action.action_type == ActionType.SLEEP:
                kept.append(action)
                continue

            type_name = action.action_type.value
            if type_name in FORBIDDEN_BEHAVIOR_ACTIONS:
                logger.debug(f"剔除行为禁忌动作: {type_name}")
                continue

            # 禁凌晨窗口（避免凌晨频繁操作）
            try:
                sh = int(action.time_window_start.split(":")[0])
                eh = int(action.time_window_end.split(":")[0])
            except Exception:
                sh, eh = 9, 10
            if sh >= 23 or eh < 7 or (sh < 7 and eh <= 7):
                # 允许跨夜的 SLEEP；其它丢掉
                if action.action_type != ActionType.SLEEP:
                    logger.debug(f"剔除夜间动作: {type_name} {action.time_window_start}")
                    continue

            # 群聊 ≠ 群发：SEND_MESSAGE 若带多目标视为群发，前两周剔除
            if (
                day_index <= NO_MASS_AUTO_REPLY_DAYS
                and action.action_type == ActionType.SEND_MESSAGE
            ):
                targets = action.params.get("targets") or action.params.get("contacts")
                if isinstance(targets, (list, tuple)) and len(targets) > 1:
                    logger.debug("前两周禁止一对多群发，丢弃 send_message(multi)")
                    continue
                if action.params.get("mass") or action.params.get("broadcast"):
                    logger.debug("前两周禁止群发标记，丢弃 send_message")
                    continue

            limit_key = type_to_limit.get(action.action_type)
            if limit_key is not None and limit_key in limits:
                used = counters.get(limit_key, 0)
                # ADD_FRIEND / GROUP_CHAT 按 params.count 计
                cost = 1
                if action.action_type in (ActionType.ADD_FRIEND, ActionType.GROUP_CHAT):
                    c = action.params.get("count", 1)
                    cost = int(c) if not isinstance(c, tuple) else int(c[0])
                elif action.action_type == ActionType.LIKE_MOMENT:
                    c = action.params.get("count", 1)
                    if isinstance(c, tuple):
                        cost = int(c[1])
                    else:
                        cost = int(c)
                elif action.action_type == ActionType.MOMENTS_DAILY_INTERACT:
                    cost = int(action.params.get("target_count", 20))
                if used + cost > limits[limit_key]:
                    logger.debug(
                        f"超阶段上限，丢弃 {action.action_type.value} "
                        f"(used={used}+{cost}>{limits[limit_key]})"
                    )
                    continue
                counters[limit_key] = used + cost

            kept.append(action)
        return kept

    def _filter_consume_only(self, actions: list[Action]) -> list[Action]:
        blocked = {
            ActionType.LIKE_MOMENT,
            ActionType.COMMENT_MOMENT,
            ActionType.BROWSE_MOMENTS_INTERACT,
            ActionType.MOMENTS_DAILY_INTERACT,
            ActionType.POST_MOMENT,
            ActionType.SEND_MESSAGE,
            ActionType.SEND_IMAGE,
            ActionType.SEND_VOICE,
            ActionType.SEND_EMOJI,
            ActionType.ADD_FRIEND,
            ActionType.GROUP_CHAT,
            ActionType.DEEP_CHAT,
            ActionType.COMMENT_CHANNEL,
            ActionType.LIKE_CHANNEL,
            ActionType.FOLLOW_PUBLIC_ACCOUNT,
        }
        kept = []
        for a in actions:
            if a.action_type in blocked:
                continue
            if a.action_type == ActionType.SCROLL_CHANNELS:
                # 仅消费：可看完播，但不赞不评
                params = dict(a.params or {})
                params["like_rate"] = 0.0
                params["comment_rate"] = 0.0
                params.setdefault("finish_watch", True)
                params.setdefault("duration", 600)
                a = Action(
                    a.action_type,
                    a.time_window_start,
                    a.time_window_end,
                    a.duration_seconds,
                    params=params,
                    random_offset_minutes=a.random_offset_minutes,
                )
            kept.append(a)
        return kept

    def _ensure_sleep(self, actions: list[Action], is_weekend: bool) -> list[Action]:
        if any(a.action_type == ActionType.SLEEP for a in actions):
            return actions
        if is_weekend:
            actions.append(Action(ActionType.SLEEP, "00:00", "08:00", (0, 0)))
        else:
            actions.append(Action(ActionType.SLEEP, "23:00", "07:00", (0, 0)))
        return actions
