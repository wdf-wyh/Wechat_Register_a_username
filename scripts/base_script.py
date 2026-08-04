"""
行为脚本基类 — 定义 DailyScript 数据结构、ActionType 枚举和脚本执行接口。

所有养号阶段脚本都继承此基类，统一行为调度和执行逻辑。

使用方式:
    script = TrustBuildingScript(wc, persona, db)
    await script.run_daily()
"""

import asyncio
import random
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Callable, Optional

from core.wechat_control import WeChatControl
from core.humanizer import Humanizer
from storage.db import Database
from utils.logger import get_logger
from utils.image_utils import save_debug_screenshot
from monitor.metrics import MetricsReporter

logger = get_logger("scripts")


class ActionType(Enum):
    """动作类型枚举"""
    OPEN_WECHAT = "open_wechat"
    SCROLL_MOMENTS = "scroll_moments"
    LIKE_MOMENT = "like_moment"
    COMMENT_MOMENT = "comment_moment"
    BROWSE_MOMENTS_INTERACT = "browse_moments_interact"
    POST_MOMENT = "post_moment"
    SCROLL_CHANNELS = "scroll_channels"
    LIKE_CHANNEL = "like_channel"
    COMMENT_CHANNEL = "comment_channel"
    READ_ARTICLE = "read_article"
    FAVORITE_ARTICLE = "favorite_article"
    FOLLOW_PUBLIC_ACCOUNT = "follow_public_account"
    GLOBAL_SEARCH = "global_search"
    SEND_MESSAGE = "send_message"
    SEND_IMAGE = "send_image"
    SEND_VOICE = "send_voice"
    SEND_EMOJI = "send_emoji"
    DEEP_CHAT = "deep_chat"
    GROUP_CHAT = "group_chat"
    ADD_FRIEND = "add_friend"
    BROWSE_MINI_PROGRAM = "browse_mini_program"
    MAKE_PAYMENT = "make_payment"
    OPEN_FAVORITES = "open_favorites"
    BROWSE_FAVORITES = "browse_favorites"
    IDLE = "idle"
    SLEEP = "sleep"


@dataclass
class Action:
    """单个行为动作"""
    action_type: ActionType
    time_window_start: str          # "08:00"
    time_window_end: str            # "09:00"
    duration_seconds: tuple         # (min, max)
    params: dict = field(default_factory=dict)
    random_offset_minutes: int = 30


def channels_daily_params(
    duration: int = 600,
    *,
    comment: bool = True,
    like_rate: float = 0.2,
    comment_rate: float = 0.18,
) -> dict:
    """每日视频号默认参数：约 10 分钟完播 + 可选评论。"""
    return {
        "duration": int(duration),
        "finish_watch": True,
        "like_rate": like_rate,
        "comment_rate": comment_rate if comment else 0.0,
    }


@dataclass
class DailyScript:
    """单日行为剧本"""
    stage: str
    is_weekend: bool
    actions: list[Action]


class BaseScript(ABC):
    """
    行为脚本基类。

    子类需要实现:
      - _build_weekday_script() → DailyScript
      - _build_weekend_script() → DailyScript

    公用能力:
      - 动作到 WeChatControl 方法的映射和执行
      - 时间窗口调度
      - 异常处理和日志记录
      - 每日限量检查
    """

    STAGE_NAME = "base"

    def __init__(
        self,
        wechat: WeChatControl,
        persona: dict,
        db: Database,
        metrics: Optional[MetricsReporter] = None,
    ):
        self.wc = wechat
        self.h = wechat.h
        self.persona = persona
        self.db = db
        self.account_id = wechat.account_id
        self.metrics = metrics or MetricsReporter()

        # 当日操作计数器
        self._daily_counts: dict[str, int] = {}

        # 动作处理器映射
        self._action_handlers: dict[ActionType, Callable] = {
            ActionType.OPEN_WECHAT:      self._handle_open_wechat,
            ActionType.SCROLL_MOMENTS:          self._handle_scroll_moments,
            ActionType.LIKE_MOMENT:             self._handle_like_moment,
            ActionType.COMMENT_MOMENT:          self._handle_comment_moment,
            ActionType.BROWSE_MOMENTS_INTERACT: self._handle_browse_moments_interact,
            ActionType.POST_MOMENT:      self._handle_post_moment,
            ActionType.SCROLL_CHANNELS:  self._handle_scroll_channels,
            ActionType.LIKE_CHANNEL:     self._handle_like_channel,
            ActionType.COMMENT_CHANNEL:  self._handle_comment_channel,
            ActionType.READ_ARTICLE:     self._handle_read_article,
            ActionType.FAVORITE_ARTICLE: self._handle_favorite_article,
            ActionType.FOLLOW_PUBLIC_ACCOUNT: self._handle_follow_public_account,
            ActionType.GLOBAL_SEARCH:    self._handle_global_search,
            ActionType.SEND_MESSAGE:     self._handle_send_message,
            ActionType.SEND_IMAGE:       self._handle_send_image,
            ActionType.SEND_VOICE:       self._handle_send_voice,
            ActionType.SEND_EMOJI:       self._handle_send_emoji,
            ActionType.DEEP_CHAT:        self._handle_deep_chat,
            ActionType.GROUP_CHAT:       self._handle_group_chat,
            ActionType.ADD_FRIEND:       self._handle_add_friend,
            ActionType.BROWSE_MINI_PROGRAM: self._handle_browse_mini_program,
            ActionType.MAKE_PAYMENT:     self._handle_make_payment,
            ActionType.OPEN_FAVORITES:   self._handle_open_favorites,
            ActionType.BROWSE_FAVORITES: self._handle_browse_favorites,
            ActionType.IDLE:             self._handle_idle,
            ActionType.SLEEP:            self._handle_sleep,
        }

    # ================================================================
    # 子类必须实现
    # ================================================================

    @abstractmethod
    def _build_weekday_script(self) -> DailyScript:
        """构建工作日行为剧本"""
        ...

    @abstractmethod
    def _build_weekend_script(self) -> DailyScript:
        """构建周末行为剧本"""
        ...

    # ================================================================
    # 每日执行入口
    # ================================================================

    async def run_daily(self):
        """
        执行一天的养号剧本。

        1. 判断工作日/周末
        2. 优先 AI 上帝视角编排；失败则用子类静态剧本 / 14 天模板
        3. 为每个动作生成精确执行时间并执行
        """
        is_weekend = datetime.now().weekday() >= 5
        actions = self._resolve_daily_actions(is_weekend)

        logger.info(
            f"[{self.account_id}] 开始执行 {self.STAGE_NAME} "
            f"({'周末' if is_weekend else '工作日'}) 剧本, "
            f"共 {len(actions)} 个动作"
        )

        scheduled = self._schedule_actions(actions)
        success_count = 0
        fail_count = 0

        for exec_time, action in scheduled:
            # 等待到预定时间
            now = datetime.now()
            wait = (exec_time - now).total_seconds()
            if wait > 0:
                # 如果等待时间长，分段 sleep 以便响应中断
                while wait > 0:
                    chunk = min(wait, 60)  # 每次最多 sleep 60s
                    await asyncio.sleep(chunk)
                    wait -= chunk

                    # 检查是否在允许的操作时间内
                    current_hour = datetime.now().hour
                    if action.action_type != ActionType.SLEEP:
                        if current_hour < 7 or current_hour >= 23:
                            logger.debug(
                                f"[{self.account_id}] 超出活跃时间({current_hour}h)，"
                                f"跳过动作 {action.action_type.value}"
                            )
                            break
                else:
                    # wait loop 正常结束，执行动作
                    pass
                if current_hour < 7 or current_hour >= 23:
                    continue

            # 执行动作
            handler = self._action_handlers.get(action.action_type)
            if handler:
                start_time = time.time()
                try:
                    result = handler(action.params)
                    elapsed = time.time() - start_time

                    if result:
                        success_count += 1
                    else:
                        fail_count += 1
                        logger.warning(
                            f"[{self.account_id}] 动作未成功: {action.action_type.value}"
                        )

                    # 记录日志
                    self.db.log_action(
                        account_id=self.account_id,
                        action_type=action.action_type.value,
                        success=result,
                        action_params=action.params,
                    )

                    # 上报指标
                    self.metrics.record_action(
                        self.account_id, action.action_type.value,
                        success=result, duration=elapsed,
                    )

                except Exception as e:
                    fail_count += 1
                    logger.error(
                        f"[{self.account_id}] 动作异常 {action.action_type.value}: {e}"
                    )
                    self.db.log_action(
                        account_id=self.account_id,
                        action_type=action.action_type.value,
                        success=False,
                        error_msg=str(e),
                        action_params=action.params,
                        screenshot_path=save_debug_screenshot(
                            self.wc.d, self.account_id, action.action_type.value
                        ) or "",
                    )

                # 动作间随机间隔
                await asyncio.sleep(self.h.action_interval("general"))

        logger.info(
            f"[{self.account_id}] 剧本执行完成: "
            f"{success_count} 成功 / {fail_count} 失败"
        )
        return {"success": success_count, "fail": fail_count}

    def _resolve_daily_actions(self, is_weekend: bool) -> list[Action]:
        """AI 上帝视角优先，否则子类静态剧本。"""
        from config.settings import settings

        if getattr(settings, "USE_AI_GOD_PLANNER", True):
            try:
                from content.ai_god_planner import AiGodPlanner

                planner = AiGodPlanner(self.db, self.persona)
                planned = planner.plan_day(self.account_id, is_weekend=is_weekend)
                if planned:
                    return planned
            except Exception as e:
                logger.warning(f"[{self.account_id}] AI 编排失败，回退静态剧本: {e}")

        script = self._build_weekend_script() if is_weekend else self._build_weekday_script()
        return script.actions

    # ================================================================
    # 时间调度
    # ================================================================

    def _schedule_actions(self, actions: list[Action]) -> list[tuple[datetime, Action]]:
        """
        为每个动作生成精确执行时间。

        在时间窗口内随机选取 + 随机偏移。
        """
        scheduled = []
        today = datetime.now().date()

        for action in actions:
            start_h, start_m = map(int, action.time_window_start.split(":"))
            end_h, end_m = map(int, action.time_window_end.split(":"))

            window_start = datetime(today.year, today.month, today.day, start_h, start_m)
            window_end = datetime(today.year, today.month, today.day, end_h, end_m)

            # 处理跨天窗口（如 SLEEP 23:00-07:00）
            if window_end <= window_start:
                window_end += timedelta(days=1)

            window_seconds = (window_end - window_start).total_seconds()
            if window_seconds > 0:
                random_offset = self.h.uniform(0, window_seconds)
                exec_time = window_start + timedelta(seconds=random_offset)
            else:
                exec_time = window_start

            scheduled.append((exec_time, action))

        scheduled.sort(key=lambda x: x[0])
        return scheduled

    # ================================================================
    # 动作处理函数
    # ================================================================

    def _handle_open_wechat(self, params: dict) -> bool:
        """打开微信，模拟查看消息"""
        self.wc.ensure_wechat_home()
        # 模拟查看消息列表的停留时间
        self.h.random_sleep(1.0, 3.0)
        return True

    def _handle_scroll_moments(self, params: dict) -> bool:
        """刷朋友圈"""
        if not self.wc.open_moments():
            return False
        times = params.get("times") or self.h.randint(6, 16)
        self.wc.scroll_moments(times)
        self.wc.d.press("back")
        return True

    def _handle_like_moment(self, params: dict) -> bool:
        """点赞朋友圈（OCR 时间戳定位方案）"""
        count = params.get("count", 1)
        if isinstance(count, tuple):
            count = self.h.randint(*count)
        success = 0
        for _ in range(count):
            idx = self.h.randint(0, min(5, count)) if count > 1 else 0
            if self.wc.like_moment(post_index=idx):
                success += 1
            if _ < count - 1:
                self.h.random_sleep(0.5, 2.0)
        return success > 0

    def _handle_comment_moment(self, params: dict) -> bool:
        """评论朋友圈（LLM 优先，模板降级）"""
        text = params.get("text", "")
        if not text:
            try:
                from content.llm_client import LLMClient
                text = LLMClient().generate_comment(self.persona)
            except Exception:
                from content.comment_templates import CommentTemplateManager
                text = CommentTemplateManager().get_comment(self.persona)
        return self.wc.comment_moment(text)

    def _handle_browse_moments_interact(self, params: dict) -> bool:
        """浏览朋友圈并随机点赞+评论（OCR 方案）"""
        duration = params.get("duration", 300)
        comment_text = params.get("comment_text", "")
        like_rate = params.get("like_rate", 0.35)
        result = self.wc.browse_moments_interact(
            duration_seconds=duration,
            comment_text=comment_text,
            like_rate=like_rate,
        )
        return result.get("liked", 0) > 0 or result.get("commented", 0) > 0

    def _handle_post_moment(self, params: dict) -> bool:
        """发朋友圈（LLM 优先，模板降级）"""
        text = params.get("text", "")
        topic = params.get("topic", "日常")
        if not text:
            try:
                from content.llm_client import LLMClient
                text = LLMClient().generate_post_text(self.persona, topic=topic)
            except Exception:
                from content.post_templates import PostTemplateManager
                text = PostTemplateManager().get_random_post(self.persona)
        return self.wc.post_moment(text)

    def _handle_scroll_channels(self, params: dict) -> bool:
        """刷视频号：默认约 10 分钟完播观看，可按概率点赞/评论。"""
        from core.channels_browser import ChannelsBrowser, DEFAULT_DAILY_DURATION

        duration = params.get("duration")
        times = params.get("times")
        # 未指定条数时按时长；都未指定则每日默认 10 分钟
        if duration is None and times is None:
            duration = DEFAULT_DAILY_DURATION
        like_rate = params.get("like_rate", 0.2)
        comment_rate = params.get("comment_rate", 0.18)
        finish_watch = params.get("finish_watch", True)
        comment_texts = params.get("comment_texts")
        # 未显式给文案池时：OCR 视频文案 → LLM 按内容评论
        comment_fn = None
        if not comment_texts and comment_rate > 0:
            comment_fn = self._channel_comment_fn()

        browser = ChannelsBrowser(self.wc.d, account_id=self.account_id)
        result = browser.browse(
            scroll_count=times,
            like_rate=like_rate,
            duration_seconds=duration,
            finish_watch=finish_watch,
            comment_rate=comment_rate,
            comment_texts=comment_texts,
            comment_fn=comment_fn,
        )
        return result.get("watched", 0) > 0

    def _channel_comment_fn(self):
        """返回 (video_context) -> comment；LLM 失败则用人设短评兜底。"""
        fallback = ["不错", "学到了", "哈哈哈", "支持", "有意思", "太真实了"]

        def _gen(video_context: str) -> str:
            try:
                from content.llm_client import LLMClient
                text = LLMClient().generate_channel_comment(
                    self.persona, video_context or ""
                )
                if text:
                    return text[:40]
            except Exception:
                pass
            return self.h.choice(fallback)

        return _gen

    def _handle_like_channel(self, params: dict) -> bool:
        """点赞视频号"""
        from core.channels_browser import ChannelsBrowser
        browser = ChannelsBrowser(self.wc.d, account_id=self.account_id)
        return browser._like_current()

    def _handle_comment_channel(self, params: dict) -> bool:
        """视频号评论：优先用 params.text，否则 OCR 文案 + LLM。"""
        from core.channels_browser import ChannelsBrowser

        browser = ChannelsBrowser(self.wc.d, account_id=self.account_id)
        browser._enter_channels()
        time.sleep(2.0)

        text = (params.get("text") or "").strip()
        if not text:
            text = self._channel_comment_fn()(browser.extract_video_context())
        return browser._comment_current(text)

    def _handle_read_article(self, params: dict) -> bool:
        """阅读公众号文章（OCR 方案）"""
        from core.public_account_browser import PublicAccountBrowser
        duration = params.get("duration", 180)
        comment_rate = float(params.get("comment_rate", 0.15))
        post_after_read = bool(params.get("post_after_read", False))
        post_rate = float(params.get("post_rate", 0.0))
        browser = PublicAccountBrowser(
            self.wc.d,
            account_id=self.account_id,
            persona=self.persona,
        )
        browser.browse(
            duration_seconds=duration,
            comment_rate=comment_rate,
            post_after_read=post_after_read,
            post_rate=post_rate,
        )
        return True

    def _handle_favorite_article(self, params: dict) -> bool:
        """收藏文章"""
        return self.wc.favorite_article()

    def _handle_follow_public_account(self, params: dict) -> bool:
        """关注行业公众号（名单来自 persona.public_accounts）"""
        from core.social_actions import SocialActions
        from content.personas import get_public_account_candidates

        names = params.get("names") or get_public_account_candidates(self.persona)
        count = params.get("count", 1)
        if isinstance(count, tuple):
            count = self.h.randint(*count)
        if not names:
            # 无配置时用搜索关键词当公众号名（尽力）
            from content.search_keywords import SearchKeywordManager
            names = SearchKeywordManager().get_keywords_batch(count)

        social = SocialActions(self.wc.d, self.account_id)
        ok = 0
        for name in names[:count]:
            if social.follow_public_account(str(name)):
                ok += 1
            self.h.random_sleep(2.0, 5.0)
        return ok > 0

    def _handle_global_search(self, params: dict) -> bool:
        """全局搜索"""
        keyword = params.get("keyword", "")
        if not keyword:
            from content.search_keywords import SearchKeywordManager
            category = params.get("keyword_category")
            # 兼容模板里的 mini_program 类别名
            cat_map = {"mini_program": "小程序"}
            if category in cat_map:
                category = cat_map[category]
            keyword = SearchKeywordManager().get_random_keyword(
                self.persona, category=category
            )
        return self.wc.global_search(keyword)

    def _handle_send_message(self, params: dict) -> bool:
        """发送聊天消息（OCR+IME 方案）"""
        from core.message_sender import MessageSender
        from scripts.cold_start_templates import NO_MASS_AUTO_REPLY_DAYS

        # 前两周禁止群发（一对多 / mass 标记）
        day_index = self._registration_day_index()
        if day_index <= NO_MASS_AUTO_REPLY_DAYS:
            targets = params.get("targets") or params.get("contacts")
            if isinstance(targets, (list, tuple)) and len(targets) > 1:
                logger.warning(
                    f"[{self.account_id}] 前{NO_MASS_AUTO_REPLY_DAYS}天禁止群发，跳过"
                )
                return True
            if params.get("mass") or params.get("broadcast") or params.get("auto_reply"):
                logger.warning(
                    f"[{self.account_id}] 前{NO_MASS_AUTO_REPLY_DAYS}天禁止群发/自动回复，跳过"
                )
                return True

        contact = params.get("contact", "")
        if not contact:
            friend = self.db.get_random_friend(self.account_id, exclude_groups=True)
            if not friend:
                logger.debug(f"[{self.account_id}] 没有好友可聊天")
                return True
            contact = friend["friend_name"]

        text = params.get("text", "")
        if not text:
            try:
                from content.llm_client import LLMClient
                text = LLMClient().generate_chat_text(
                    self.persona,
                    context=self.h.choice(["small_talk", "greeting", "share"]),
                )
            except Exception:
                from content.chat_templates import ChatTemplateManager
                text = ChatTemplateManager().get_random_chat(
                    self.h.choice(["small_talk", "greeting", "share"]),
                    self.persona,
                )

        sender = MessageSender(self.wc.d, account_id=self.account_id)
        return sender.send(contact=contact, message=text)

    def _handle_send_image(self, params: dict) -> bool:
        """发送图片（OCR+OpenCV 方案）"""
        from core.image_sender import ImageSender

        contact = params.get("contact", "")
        if not contact:
            friend = self.db.get_random_friend(self.account_id, exclude_groups=True)
            if not friend:
                logger.debug(f"[{self.account_id}] 没有好友可发送图片")
                return True
            contact = friend["friend_name"]

        count = params.get("count", 1)
        sender = ImageSender(self.wc.d, account_id=self.account_id)
        return sender.send(contact=contact, photo_count=count)

    def _handle_send_voice(self, params: dict) -> bool:
        """发送语音"""
        contact = params.get("contact", "")
        if contact:
            self.wc.open_chat(contact)
        duration = params.get("duration")
        return self.wc.send_voice(duration)

    def _handle_send_emoji(self, params: dict) -> bool:
        """发送表情"""
        return self.wc.send_emoji()

    def _handle_deep_chat(self, params: dict) -> bool:
        """多轮深度聊天（约 5 分钟）"""
        from core.social_actions import SocialActions

        contact = params.get("contact", "")
        if not contact:
            friend = self.db.get_random_friend(self.account_id, exclude_groups=True)
            if not friend:
                # 回退 persona 种子好友
                seeds = self.persona.get("seed_friends") or []
                if not seeds:
                    logger.debug(f"[{self.account_id}] 无好友可深聊，跳过")
                    return True
                contact = self.h.choice(seeds)
            else:
                contact = friend["friend_name"]

        rounds = params.get("rounds", 5)
        duration = params.get("duration", 300)
        messages = params.get("messages") or []
        if not messages:
            try:
                from content.llm_client import LLMClient
                messages = LLMClient().generate_deep_chat_turns(
                    self.persona, contact=contact, rounds=rounds
                )
            except Exception:
                messages = []
        if not messages:
            from content.chat_templates import ChatTemplateManager
            mgr = ChatTemplateManager()
            messages = [
                mgr.get_random_chat(
                    self.h.choice(["small_talk", "greeting", "share"]),
                    self.persona,
                )
                for _ in range(rounds)
            ]

        return SocialActions(self.wc.d, self.account_id).deep_chat(
            contact=contact,
            messages=messages,
            total_seconds=int(duration),
        )

    def _handle_group_chat(self, params: dict) -> bool:
        """群聊发言（群名来自 DB source=group 或 persona.seed_groups）"""
        from core.message_sender import MessageSender

        count = params.get("count", 1)
        if isinstance(count, tuple):
            count = self.h.randint(*count)

        groups = self.db.get_friends(self.account_id, source="group")
        names = [g["friend_name"] for g in groups] if groups else []
        if not names:
            names = list(self.persona.get("seed_groups") or [])
        if not names:
            logger.debug(f"[{self.account_id}] 无群可发言，跳过")
            return True

        sender = MessageSender(self.wc.d, account_id=self.account_id)
        ok = 0
        for _ in range(count):
            group = self.h.choice(names)
            text = params.get("text", "")
            if not text:
                try:
                    from content.llm_client import LLMClient
                    text = LLMClient().generate_chat_text(
                        self.persona,
                        context=f"在群「{group}」里自然发言",
                        scene="group",
                    )
                except Exception:
                    from content.chat_templates import ChatTemplateManager
                    text = ChatTemplateManager().get_random_chat(
                        "small_talk", self.persona
                    )
            if sender.send(contact=group, message=text):
                ok += 1
            self.h.random_sleep(8.0, 25.0)
        return ok > 0

    def _handle_add_friend(self, params: dict) -> bool:
        """加好友（仅 persona.seed_friends / params.targets，严格限流）"""
        from core.social_actions import SocialActions
        from scripts.cold_start_templates import (
            WEEK1_ADD_FRIEND_CAP,
            max_add_friends_for_day,
        )

        day_index = self._registration_day_index()
        cap = max_add_friends_for_day(day_index)
        if cap <= 0:
            logger.info(
                f"[{self.account_id}] Day{day_index} 禁止自动加好友"
                f"（首周上限≤{WEEK1_ADD_FRIEND_CAP}，相位硬限=0），跳过"
            )
            return True

        already = self._today_success_count("add_friend")
        remain = max(0, cap - already)
        if remain <= 0:
            logger.info(
                f"[{self.account_id}] 加好友已达今日上限 {cap}（Day{day_index}），跳过"
            )
            return True

        count = params.get("count", 1)
        if isinstance(count, tuple):
            count = self.h.randint(*count)
        count = min(int(count), remain)

        targets = params.get("targets") or list(self.persona.get("seed_friends") or [])
        if not targets:
            logger.debug(f"[{self.account_id}] 无 seed_friends，跳过加好友")
            return True

        # 过滤已在 DB 中的
        existing = {f["friend_name"] for f in self.db.get_friends(self.account_id)}
        targets = [t for t in targets if t not in existing]
        if not targets:
            logger.debug(f"[{self.account_id}] seed_friends 均已添加")
            return True

        social = SocialActions(self.wc.d, self.account_id)
        ok = 0
        for target in targets[:count]:
            source = params.get("source", "seed")
            if social.add_friend(str(target), remark_source=str(source)):
                try:
                    self.db.add_friend(
                        self.account_id,
                        str(target),
                        source=f"active_add:{source}",
                    )
                except Exception:
                    pass
                ok += 1
                self._increment_daily_count("add_friend")
            self.h.random_sleep(15.0, 40.0)
        return ok > 0

    def _registration_day_index(self) -> int:
        """注册日起算的天数（从 1 开始）。"""
        from datetime import date, datetime

        account = self.db.get_account(self.account_id) or {}
        reg = account.get("registration_date") or date.today().isoformat()
        try:
            reg_date = datetime.strptime(str(reg)[:10], "%Y-%m-%d").date()
            return max(1, (date.today() - reg_date).days + 1)
        except Exception:
            return 1

    def _today_success_count(self, action_type: str) -> int:
        """今日该动作成功次数（内存计数 + DB 已落库）。"""
        mem = self._daily_counts.get(action_type, 0)
        try:
            from datetime import date

            logs = self.db.get_action_logs(
                self.account_id, limit=200, date=date.today().isoformat()
            )
            db_n = sum(
                1
                for row in logs
                if row.get("action_type") == action_type and row.get("success")
            )
            return max(mem, db_n)
        except Exception:
            return mem

    def _handle_browse_mini_program(self, params: dict) -> bool:
        """浏览小程序"""
        from core.social_actions import SocialActions
        from content.search_keywords import SearchKeywordManager

        duration = params.get("duration", 120)
        keyword = params.get("keyword", "")
        if not keyword and params.get("random_keyword", True):
            keyword = SearchKeywordManager().get_random_keyword(
                self.persona, category="小程序"
            )
        return SocialActions(self.wc.d, self.account_id).browse_mini_program(
            duration_seconds=int(duration),
            keyword=keyword,
        )

    def _handle_make_payment(self, params: dict) -> bool:
        """打开支付页面（模拟支付行为，不完成真实交易）"""
        return self.wc.open_payment_page()

    def _handle_open_favorites(self, params: dict) -> bool:
        """打开收藏夹页面"""
        return self.wc.open_favorites()

    def _handle_browse_favorites(self, params: dict) -> bool:
        """浏览收藏夹（OCR + CLAHE 方案）"""
        from core.favorites_browser import FavoritesBrowser
        duration = params.get("duration", 180)
        browser = FavoritesBrowser(self.wc.d, account_id=self.account_id)
        browser.browse(duration_seconds=duration)
        return True

    def _handle_idle(self, params: dict) -> bool:
        """空闲（保持在线但不操作）"""
        idle_sec = self.h.uniform(
            params.get("min_seconds", 60),
            params.get("max_seconds", 300),
        )
        time.sleep(idle_sec)
        return True

    def _handle_sleep(self, params: dict) -> bool:
        """睡眠时段，不执行任何操作"""
        return True

    # ================================================================
    # 限量检查
    # ================================================================

    def _check_daily_limit(self, action_type: str, limit: int) -> bool:
        """检查当日操作是否超限"""
        count = self._daily_counts.get(action_type, 0)
        if count >= limit:
            logger.debug(
                f"[{self.account_id}] {action_type} 已达当日上限 ({limit})"
            )
            return False
        return True

    def _increment_daily_count(self, action_type: str):
        """增加当日操作计数"""
        self._daily_counts[action_type] = self._daily_counts.get(action_type, 0) + 1
