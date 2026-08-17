# -*- coding: utf-8 -*-
"""
14 天冷启动剧本 — 连续缩时验证。

走 TrustBuildingScript / BaseScript 同一套 handler，确保与生产路径一致。
默认缩时（跳过 SLEEP、压缩 duration），--full 使用模板原始参数。

用法:
  python main.py cold-start-burst
  python main.py cold-start-burst --full
  python main.py cold-start-burst --day 4
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import replace
from datetime import date, timedelta

from scripts.base_script import Action, ActionType
from scripts.cold_start_templates import build_cold_start_actions, cold_start_phase
from scripts.trust_building import TrustBuildingScript
from utils.logger import get_logger

logger = get_logger("cold_start_burst")

PHASE_CN = {
    "day1_3": "身份塑造·社交种子",
    "day4_7": "内容生态",
    "day8_10": "深度互动",
    "day11_14": "场景渗透",
}

ACTION_CN = {
    ActionType.OPEN_WECHAT: "打开微信",
    ActionType.SCROLL_MOMENTS: "刷朋友圈",
    ActionType.ADD_FRIEND: "加好友",
    ActionType.FOLLOW_PUBLIC_ACCOUNT: "关注公众号",
    ActionType.READ_ARTICLE: "读公众号文章",
    ActionType.FAVORITE_ARTICLE: "收藏文章",
    ActionType.SCROLL_CHANNELS: "刷视频号",
    ActionType.GLOBAL_SEARCH: "全局搜索",
    ActionType.BROWSE_MINI_PROGRAM: "浏览小程序",
    ActionType.MAKE_PAYMENT: "打开支付页",
    ActionType.POST_MOMENT: "发朋友圈",
    ActionType.GROUP_CHAT: "群聊发言",
    ActionType.PLAY_MINI_GAME: "玩官方小游戏",
    ActionType.LIKE_MOMENT: "朋友圈点赞",
    ActionType.BROWSE_MOMENTS_INTERACT: "朋友圈互动浏览",
    ActionType.DEEP_CHAT: "深聊",
    ActionType.SEND_MESSAGE: "发消息",
    ActionType.COMMENT_MOMENT: "朋友圈评论",
    ActionType.SLEEP: "休眠",
}


class ColdStartBurstScript(TrustBuildingScript):
    """连续跑 14 天模板；模拟注册天数，避免同日 DB 计数串扰。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._simulated_day_index = 1
        self._burst_mode = True

    def _registration_day_index(self) -> int:
        if self._burst_mode:
            return self._simulated_day_index
        return super()._registration_day_index()

    def _today_success_count(self, action_type: str) -> int:
        if self._burst_mode:
            return self._daily_counts.get(action_type, 0)
        return super()._today_success_count(action_type)


def _compress_action(action: Action) -> Action | None:
    """缩时：跳过 SLEEP，压缩耗时类参数。"""
    if action.action_type == ActionType.SLEEP:
        return None

    params = dict(action.params)
    at = action.action_type

    if at == ActionType.SCROLL_MOMENTS:
        params.setdefault("times", 2)
    elif at == ActionType.SCROLL_CHANNELS:
        params["duration"] = min(int(params.get("duration", 600)), 90)
    elif at == ActionType.READ_ARTICLE:
        # Day1 读文约 10 分钟：保留模板 duration（≥600 不压缩）
        # 较短读文+留言：压到 180–240，禁止再压成 45s
        dur = int(params.get("duration", 180) or 180)
        comment_rate = float(params.get("comment_rate", 0) or 0)
        if dur >= 600:
            params["duration"] = dur
            if comment_rate > 0 or params.get("require_comment"):
                params["comment_rate"] = max(comment_rate, 1.0)
                params["require_comment"] = True
        elif comment_rate > 0 or params.get("require_comment"):
            params["duration"] = max(min(dur, 240), 180)
            params["comment_rate"] = max(comment_rate, 1.0)
            params["require_comment"] = True
        else:
            params["duration"] = min(dur, 45)
    elif at == ActionType.BROWSE_MINI_PROGRAM:
        params["duration"] = min(int(params.get("duration", 120)), 45)
    elif at == ActionType.BROWSE_MOMENTS_INTERACT:
        params["duration"] = min(int(params.get("duration", 300)), 90)
    elif at == ActionType.DEEP_CHAT:
        params["duration"] = min(int(params.get("duration", 300)), 90)
        params["rounds"] = min(int(params.get("rounds", 3)), 2)
    elif at == ActionType.PLAY_MINI_GAME:
        params["duration"] = min(int(params.get("duration", 180)), 60)
    elif at == ActionType.GROUP_CHAT:
        count = params.get("count", 1)
        if isinstance(count, tuple):
            params["count"] = 1
        else:
            params["count"] = min(int(count), 1)
    elif at == ActionType.LIKE_MOMENT:
        count = params.get("count", 1)
        if isinstance(count, tuple):
            params["count"] = 1
        else:
            params["count"] = min(int(count), 1)
    elif at == ActionType.FOLLOW_PUBLIC_ACCOUNT:
        # 保留模板 count（Day1=2）；仅把过大值压到 2 以节省时间
        count = params.get("count", 1)
        if isinstance(count, tuple):
            params["count"] = min(count[1], 2)
        else:
            params["count"] = min(max(int(count), 1), 2)

    return replace(action, params=params)


def _prepare_actions(day_index: int, *, full: bool) -> list[Action]:
    actions = build_cold_start_actions(day_index, is_weekend=False)
    if full:
        return [a for a in actions if a.action_type != ActionType.SLEEP]
    compressed: list[Action] = []
    for action in actions:
        item = _compress_action(action)
        if item is not None:
            compressed.append(item)
    return compressed


def _sync_registration_day(db, account_id: str, day_index: int) -> None:
    """让 DB 注册日与模拟天数一致（handler 限流逻辑会读取）。"""
    reg_date = date.today() - timedelta(days=max(0, int(day_index) - 1))
    db.update_account(account_id, registration_date=reg_date.isoformat())


async def _execute_action(script: ColdStartBurstScript, action: Action) -> bool:
    from utils.image_utils import (
        append_cron_evidence,
        format_evidence_message,
        save_action_keyframe,
    )

    handler = script._action_handlers.get(action.action_type)
    if not handler:
        logger.warning(f"无 handler: {action.action_type.value}")
        return False

    start = time.time()
    try:
        result = bool(handler(action.params))
        elapsed = time.time() - start
        shot = save_action_keyframe(
            script.wc.d,
            script.account_id,
            action.action_type.value,
            success=result,
        )
        script.db.log_action(
            account_id=script.account_id,
            action_type=action.action_type.value,
            success=result,
            action_params={
                **action.params,
                "_burst_day": script._simulated_day_index,
            },
            screenshot_path=shot or "",
        )
        append_cron_evidence(
            script.account_id, action.action_type.value, result, shot
        )
        logger.info(
            "[证据] "
            + format_evidence_message(
                script.account_id, action.action_type.value, result, shot
            )
        )
        script.metrics.record_action(
            script.account_id,
            action.action_type.value,
            success=result,
            duration=elapsed,
        )
        return result
    except Exception as e:
        logger.error(
            f"[{script.account_id}] Day{script._simulated_day_index} "
            f"{action.action_type.value} 异常: {e}"
        )
        shot = save_action_keyframe(
            script.wc.d,
            script.account_id,
            action.action_type.value,
            success=False,
        )
        script.db.log_action(
            account_id=script.account_id,
            action_type=action.action_type.value,
            success=False,
            error_msg=str(e),
            action_params={
                **action.params,
                "_burst_day": script._simulated_day_index,
            },
            screenshot_path=shot or "",
        )
        append_cron_evidence(
            script.account_id, action.action_type.value, False, shot
        )
        logger.info(
            "[证据] "
            + format_evidence_message(
                script.account_id, action.action_type.value, False, shot
            )
        )
        return False


async def run_cold_start_burst(
    script: ColdStartBurstScript,
    *,
    full: bool = False,
    day_from: int = 1,
    day_to: int = 14,
) -> dict:
    """
    连续执行 Day day_from..day_to 的冷启动模板。

    返回: {success, fail, days, elapsed, day_results}
    """
    day_from = max(1, int(day_from))
    day_to = min(14, int(day_to))
    if day_from > day_to:
        day_from, day_to = day_to, day_from

    mode = "完整时长" if full else "缩时"
    total_days = day_to - day_from + 1
    print(f"\n{'=' * 55}")
    print(f"  14 天冷启动验证 — {mode}")
    print(f"  账号: {script.account_id}")
    print(f"  范围: Day {day_from} ~ Day {day_to}（共 {total_days} 天）")
    print(f"  路径: 模板 → BaseScript handler → WeChatControl")
    print(f"{'=' * 55}\n")

    overall_start = time.time()
    total_success = 0
    total_fail = 0
    day_results: list[dict] = []

    for day_index in range(day_from, day_to + 1):
        phase = cold_start_phase(day_index)
        phase_label = PHASE_CN.get(phase, phase)
        actions = _prepare_actions(day_index, full=full)

        script._simulated_day_index = day_index
        script._daily_counts = {}
        _sync_registration_day(script.db, script.account_id, day_index)

        day_success = 0
        day_fail = 0
        print(f"\n--- Day {day_index} | {phase_label} | {len(actions)} 个动作 ---")

        for i, action in enumerate(actions, 1):
            label = ACTION_CN.get(action.action_type, action.action_type.value)
            params_hint = ""
            if action.params:
                params_hint = f" {action.params}"
            print(f"  [{i}/{len(actions)}] {label}{params_hint} ...", end=" ", flush=True)

            ok = await _execute_action(script, action)
            if ok:
                day_success += 1
                total_success += 1
                print("[OK]")
            else:
                day_fail += 1
                total_fail += 1
                print("[FAIL]")

            await asyncio.sleep(script.h.uniform(0.3, 0.8))

        day_results.append(
            {
                "day": day_index,
                "phase": phase,
                "success": day_success,
                "fail": day_fail,
                "total": len(actions),
            }
        )
        print(f"  Day {day_index} 小结: {day_success}/{len(actions)} 成功")

    elapsed = time.time() - overall_start
    summary = (
        f"\n{'=' * 55}\n"
        f"  完成: {total_success} 成功 / {total_fail} 失败\n"
        f"  天数: Day {day_from}~{day_to}\n"
        f"  耗时: {elapsed:.0f}s ({elapsed / 60:.1f} min)\n"
        f"{'=' * 55}\n"
    )
    print(summary)
    logger.info(
        f"[{script.account_id}] cold_start_burst done "
        f"ok={total_success} fail={total_fail} elapsed={elapsed:.0f}s"
    )

    return {
        "success": total_success,
        "fail": total_fail,
        "days": day_results,
        "elapsed": elapsed,
    }
