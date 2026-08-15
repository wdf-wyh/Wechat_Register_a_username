# -*- coding: utf-8 -*-
"""
微信基础操作封装 — 基于 uiautomator2 的微信常用操作。

所有元素定位使用控件属性（text、resourceId、className），不使用图像识别。

微信核心页面结构:
    - 聊天列表 (微信 Tab) — 消息列表
    - 通讯录 — 联系人、公众号、群聊
    - 发现 — 朋友圈、视频号、小程序、游戏入口
    - 我 — 个人信息、服务、收藏、设置

使用方式:
    from core import WeChatControl, Humanizer
    h = Humanizer(seed=42)
    wc = WeChatControl(d, h)
    wc.open_moments()
    wc.scroll_moments(times=5)
"""

import time
import random
from typing import Optional

import uiautomator2 as u2

from core.humanizer import Humanizer
from core.element_locator import ElementLocator
from utils.logger import get_logger
from utils.image_utils import save_debug_screenshot

logger = get_logger("wechat_control")


class WeChatControl:
    """
    微信自动化控制器。

    封装了微信各功能的操作（朋友圈、聊天、视频号、公众号、支付等），
    所有定位通过 ElementLocator 完成，不依赖图像识别。
    """

    def __init__(
        self,
        d: u2.Device,
        humanizer: Humanizer,
        account_id: str = "unknown",
    ):
        self.d = d
        self.h = humanizer
        self.account_id = account_id
        self.locator = ElementLocator(d)

    # ================================================================
    # 基础导航
    # ================================================================

    def restart_wechat(self) -> bool:
        """
        强制杀掉微信后台并冷启动。

        解决微信已在后台运行导致某些操作异常的问题。
        """
        logger.info(f"[{self.account_id}] 正在冷重启微信...")
        from core.wechat_nav import start_wechat
        ok = start_wechat(self.d, wait=4.0, cold=True)
        logger.info(f"[{self.account_id}] 微信冷重启完成")
        return ok

    def go_to_tab(self, tab_name: str) -> bool:
        """
        切换到微信底部 Tab。

        Args:
            tab_name: "微信" | "通讯录" | "发现" | "我"

        Returns:
            是否切换成功
        """
        element_map = {
            "微信":   "tab_wechat",
            "通讯录": "tab_contacts",
            "发现":   "tab_discover",
            "我":     "tab_me",
        }
        if tab_name not in element_map:
            logger.error(f"未知 Tab: {tab_name}")
            return False

        element_name = element_map[tab_name]
        success = self.locator.wait_and_click(element_name)
        if success:
            self.h.random_sleep(0.4, 1.0)
        return success

    def ensure_wechat_home(self) -> bool:
        """确保回到微信首页（聊天列表）。

        微信屏蔽无障碍树时 ``tab_wechat`` 文本定位几乎总是失败，
        不能因此连按 back（会退出微信落到桌面，再点顶栏会误触系统搜索框）。
        """
        from core.wechat_nav import WECHAT_PKG, goto_tab, start_wechat

        try:
            if self.d.app_current().get("package") != WECHAT_PKG:
                start_wechat(self.d, wait=3.0, cold=False)
        except Exception:
            start_wechat(self.d, wait=3.0, cold=False)

        # 优先机型坐标切 Tab，避免无障碍超时拖慢首页
        try:
            goto_tab(self.d, "wechat")
            return True
        except Exception:
            return self.go_to_tab("微信")

    # ================================================================
    # 朋友圈操作
    # ================================================================

    def open_moments(self) -> bool:
        """
        打开朋友圈（自动确保微信在前台+解锁屏幕）。

        路径: 发现 Tab → 朋友圈入口
        """
        # 确保微信在前台
        self.d.screen_on()
        time.sleep(0.3)
        w, h = self.d.info['displayWidth'], self.d.info['displayHeight']
        self.d.swipe(w // 2, int(h * 0.85), w // 2, int(h * 0.2), duration=0.3)
        time.sleep(1)
        current = self.d.app_current()
        if current.get("package") != "com.tencent.mm":
            logger.info(f"[{self.account_id}] 启动微信...")
            self.d.app_start("com.tencent.mm")
            time.sleep(5)

        if not self.go_to_tab("发现"):
            return False
        self.h.random_sleep(0.3, 0.8)

        if self.locator.wait_and_click("moments_entry"):
            self.h.random_sleep(0.8, 1.5)
            return True
        return False

    def scroll_moments(self, times: int = None) -> int:
        """
        刷朋友圈。

        Args:
            times: 滑动次数，None 则随机 6~20 次

        Returns:
            实际滑动次数
        """
        if times is None:
            times = self.h.randint(6, 20)

        for i in range(times):
            self.h.human_swipe(self.d, "up", distance=0.55)
            # 模拟阅读停留
            stay = self.h.uniform(1.5, 5.0)
            if i < times - 1:  # 最后一次不停留
                time.sleep(stay)
        logger.debug(f"[{self.account_id}] 刷朋友圈 {times} 次")
        return times

    def like_moment(self, post_index: int = 0) -> bool:
        """
        点赞朋友圈帖子（OCR 时间戳定位方案）。

        Args:
            post_index: 帖子序号 (0=第一条)
        """
        from core.moments_interact import MomentsInteract
        mi = MomentsInteract(self.d, account_id=self.account_id)
        return mi.like(post_index)

    def comment_moment(self, text: str, post_index: int = 0) -> bool:
        """
        评论朋友圈帖子（OCR 时间戳定位 + IME 注入）。

        Args:
            text:       评论内容
            post_index: 帖子序号 (0=第一条)
        """
        from core.moments_interact import MomentsInteract
        mi = MomentsInteract(self.d, account_id=self.account_id)
        return mi.comment(post_index, text)

    def browse_moments_interact(
        self,
        duration_seconds: int = 300,
        comment_text: str = "",
        like_rate: float = 0.35,
    ) -> dict:
        """
        浏览朋友圈, 随机点赞+评论 (OCR 方案)。

        Args:
            duration_seconds: 浏览时长(秒)
            comment_text:    评论内容, 空字符串则只点赞不评论
            like_rate:       每条帖子互动概率

        Returns:
            {"liked": N, "commented": N, "recovered": N, "elapsed": S}
        """
        from core.moments_interact import MomentsInteract
        mi = MomentsInteract(self.d, account_id=self.account_id)
        return mi.browse_and_interact(
            duration_seconds=duration_seconds,
            comment_text=comment_text,
            like_rate=like_rate,
        )

    def moments_daily_interact(
        self,
        target_count: int = 20,
        big_v_accounts: list[str] | None = None,
        comment_fn=None,
        fresh_minutes: int = 30,
        max_duration: int = 900,
    ) -> dict:
        """
        每日朋友圈互动：完成指定次数点赞/评论，优先秒评大 V 新帖。

        Args:
            target_count:   目标互动次数（点赞+评论合计）
            big_v_accounts: 大 V 昵称/公众号名列表，空则仅按新鲜度排序
            comment_fn:     (post_content, author, image_jpeg) -> 评论文案；Vision 识图优先
            fresh_minutes:  视为「新帖」的时间窗（分钟）
            max_duration:   最长执行秒数

        Returns:
            {"interactions", "liked", "commented", "big_v_commented", "elapsed", "success"}
        """
        from core.moments_interact import MomentsInteract
        mi = MomentsInteract(self.d, account_id=self.account_id)
        return mi.daily_interact(
            target_count=target_count,
            big_v_accounts=big_v_accounts or [],
            comment_fn=comment_fn,
            fresh_minutes=fresh_minutes,
            max_duration=max_duration,
        )

    def post_moment(
        self,
        text: str = "",
        image_count: int = None,
        photo_index: int = 0,
        persona: dict = None,
        smart_select: bool = True,
        topic: str = "日常",
    ) -> bool:
        """
        发朋友圈（配图+文字）。

        默认开启智能选图：Vision 过滤截图/二维码等，并按选中图片生成配文。
        """
        from core.moment_poster import MomentPoster

        if image_count is None:
            import random
            image_count = random.randint(1, 3)

        poster = MomentPoster(self.d, account_id=self.account_id)
        return poster.post(
            text=text,
            photo_index=photo_index,
            photo_count=image_count,
            persona=persona,
            smart_select=smart_select,
            topic=topic,
        )

    # ================================================================
    # 聊天操作
    # ================================================================

    def go_to_chat_list(self) -> bool:
        """回到聊天列表"""
        return self.go_to_tab("微信")

    def open_chat(self, contact_name: str) -> bool:
        """
        打开与指定联系人的聊天窗口。

        先尝试在聊天列表中直接找到，找不到则通过全局搜索。

        Args:
            contact_name: 联系人昵称/备注

        Returns:
            是否成功打开
        """
        self.go_to_chat_list()
        self.h.random_sleep(0.5, 1.0)

        # 在聊天列表中找
        try:
            chat = self.d(text=contact_name)
            if chat.exists(timeout=3.0):
                chat.click()
                self.h.random_sleep(0.5, 1.0)
                return True
        except Exception:
            pass

        # 通过搜索找
        logger.debug(f"[{self.account_id}] 聊天列表未找到 '{contact_name}'，通过搜索...")
        return self._search_and_open(contact_name, target_type="contact")

    def send_message(self, text: str, contact: str = None) -> bool:
        """
        发送文字消息（OCR + IME 方案）。

        Args:
            text:    消息内容
            contact: 联系人名称，None 则表示已在聊天窗口
        """
        from core.message_sender import MessageSender

        sender = MessageSender(self.d, account_id=self.account_id)

        if contact:
            return sender.send(contact=contact, message=text)
        else:
            # 已在聊天窗口，仅输入+发送
            try:
                sender._input_message(text)
                sender._click_send()
                return True
            except Exception as e:
                logger.error(f"[{self.account_id}] 发送消息失败: {e}")
                return False

    def send_image_from_album(self) -> bool:
        """
        在当前聊天窗口发送一张图片（从相册选取）。
        """
        # 点 "+" 更多
        if not self.locator.click("chat_more_btn", timeout=3.0):
            return False
        self.h.random_sleep(0.3, 0.6)

        # 点"相册"
        if not self.locator.click("chat_album_btn", timeout=2.0):
            # fallback
            try:
                self.d(text="相册").click()
            except Exception:
                return False
        self.h.random_sleep(0.5, 1.0)

        # 随机选一张图片（点击第一个 ImageView）
        try:
            images = self.d(className="android.widget.ImageView")
            if images.count > 0:
                # 随机选一个
                idx = self.h.randint(0, min(images.count - 1, 20))
                images[idx].click()
                self.h.random_sleep(0.3, 0.5)

                # 点发送
                self.locator.click("chat_send_btn", timeout=2.0)
                logger.debug(f"[{self.account_id}] 发送图片")
                return True
        except Exception as e:
            logger.warning(f"[{self.account_id}] 发送图片失败: {e}")

        return False

    def send_voice(self, duration_seconds: float = None) -> bool:
        """
        发送语音消息。

        Args:
            duration_seconds: 录音时长，None 随机 2~6 秒
        """
        if duration_seconds is None:
            duration_seconds = self.h.uniform(2.0, 6.0)

        # 切换到语音模式（点击语音按钮）
        if not self.locator.click("chat_voice_btn", timeout=3.0):
            # fallback: 找"按住 说话"
            try:
                self.d(text="按住 说话").click()
            except Exception:
                return False
        self.h.random_sleep(0.3, 0.5)

        # 长按录音
        try:
            voice_btn = self.d(text="按住 说话")
            if voice_btn.exists:
                voice_btn.long_click(duration=duration_seconds)
                logger.debug(f"[{self.account_id}] 发送语音 {duration_seconds:.1f}s")
                return True
        except Exception as e:
            logger.warning(f"[{self.account_id}] 发送语音失败: {e}")

        return False

    def send_emoji(self) -> bool:
        """在当前聊天窗口发送一个随机表情"""
        if not self.locator.click("chat_emoji_btn", timeout=3.0):
            return False
        self.h.random_sleep(0.3, 0.5)

        # 随机点击一个表情
        try:
            images = self.d(className="android.widget.ImageView")
            if images.count > 5:
                idx = self.h.randint(0, min(images.count - 1, 30))
                images[idx].click()
                logger.debug(f"[{self.account_id}] 发送表情")
                return True
        except Exception as e:
            logger.warning(f"[{self.account_id}] 发送表情失败: {e}")

        return False

    # ================================================================
    # 视频号操作
    # ================================================================

    def open_channels(self) -> bool:
        """
        打开视频号。

        路径: 发现 Tab → 视频号
        """
        if not self.go_to_tab("发现"):
            return False
        self.h.random_sleep(0.3, 0.6)
        if self.locator.wait_and_click("channels_entry", timeout=5.0):
            self.h.random_sleep(0.8, 1.5)
            return True
        return False

    def scroll_channels(
        self,
        times: int = None,
        like_rate: float = 0.2,
        duration_seconds: int = None,
        finish_watch: bool = True,
        comment_rate: float = 0.18,
        comment_texts: list = None,
        comment_fn=None,
    ) -> dict:
        """
        刷视频号（默认约 10 分钟完播 + 概率点赞/评论）。

        Args:
            times:             刷几条；与 duration_seconds 二选一
            like_rate:         点赞概率
            duration_seconds:  总观看秒数；都未指定时默认 600
            finish_watch:      尽量完播再切下一条
            comment_rate:      评论概率
            comment_texts:     评论文案池（有 comment_fn 时作兜底）
            comment_fn:        (video_context, image_jpeg) -> comment，Vision 识图优先

        Returns:
            {"liked", "commented", "watched", "switched", "elapsed"}
        """
        from core.channels_browser import ChannelsBrowser
        browser = ChannelsBrowser(self.d, account_id=self.account_id)
        return browser.browse(
            scroll_count=times,
            like_rate=like_rate,
            duration_seconds=duration_seconds,
            finish_watch=finish_watch,
            comment_rate=comment_rate,
            comment_texts=comment_texts,
            comment_fn=comment_fn,
        )

    def like_channel_video(self) -> bool:
        """点赞当前视频号视频（OCR 定位图标）"""
        from core.channels_browser import ChannelsBrowser
        browser = ChannelsBrowser(self.d, account_id=self.account_id)
        return browser._like_current()

    # ================================================================
    # 公众号操作
    # ================================================================

    def open_public_account(self, name: str = None) -> bool:
        """
        打开公众号页面（OCR + 全局搜索）。

        Args:
            name: 公众号名称，None 则浏览公众号列表
        """
        from core.public_account_browser import PublicAccountBrowser
        browser = PublicAccountBrowser(self.d, account_id=self.account_id)
        browser._open_public_accounts()
        return True

    def read_article(self, scroll_times: int = 3) -> int:
        """
        阅读当前文章，模拟滚动。
        """
        from core.public_account_browser import PublicAccountBrowser
        browser = PublicAccountBrowser(self.d, account_id=self.account_id)
        for i in range(scroll_times):
            self.h.human_swipe(self.d, "up", distance=0.25)
            read_time = self.h.uniform(2.0, 6.0)
            time.sleep(read_time)
        logger.debug(f"[{self.account_id}] 阅读文章 {scroll_times} 次滚动")
        return scroll_times

    def favorite_article(self) -> bool:
        """
        收藏当前文章。

        路径: 文章页 → 右上角"..." → 收藏
        """
        # 点右上角菜单
        if not self.locator.click("generic_more", timeout=3.0):
            return False
        self.h.random_sleep(0.3, 0.6)

        # 点收藏
        if self.locator.click("me_favorites", timeout=2.0):
            logger.debug(f"[{self.account_id}] 收藏文章")
            return True

        # fallback
        try:
            self.d(text="收藏").click()
            return True
        except Exception:
            pass

        return False

    # ================================================================
    # 搜索操作
    # ================================================================

    def global_search(self, keyword: str) -> bool:
        """
        微信全局搜索（OCR + OpenCV + ADBKeyboard IME）。

        Args:
            keyword: 搜索关键词
        """
        from core.search_helper import SearchHelper
        helper = SearchHelper(self.d, account_id=self.account_id)
        return helper.search(keyword)

    # ================================================================
    # 支付相关
    # ================================================================

    def open_payment_page(self) -> bool:
        """
        打开发送付款页面。

        路径: 我 → 服务 → 收付款
        """
        if not self.go_to_tab("我"):
            return False
        self.h.random_sleep(0.3, 0.5)

        if not self.locator.click("me_services", timeout=3.0):
            return False
        self.h.random_sleep(0.5, 1.0)

        if self.locator.click("services_receipt", timeout=3.0):
            self.h.random_sleep(0.5, 1.0)
            logger.debug(f"[{self.account_id}] 打开收付款页面")
            return True
        return False

    # ================================================================
    # 官方小游戏
    # ================================================================

    def play_mini_game(
        self,
        game_name: str = "",
        duration_seconds: int = 180,
    ) -> bool:
        """
        打开并游玩官方小游戏。

        路径: 微信首页 → 发现 → 游戏 → 找游戏 → 立即玩（失败可按 game_name 搜索）
        """
        from core.social_actions import SocialActions

        return SocialActions(self.d, self.account_id).play_mini_game(
            game_name=game_name,
            duration_seconds=duration_seconds,
        )

    # ================================================================
    # 收藏夹
    # ================================================================

    def open_favorites(self) -> bool:
        """
        打开收藏夹。

        路径: 我 → 收藏
        """
        if not self.go_to_tab("我"):
            return False
        self.h.random_sleep(0.3, 0.5)

        if self.locator.click("me_favorites", timeout=3.0):
            self.h.random_sleep(0.5, 1.0)
            return True
        return False

    def browse_favorites(self, duration_seconds: int = 120) -> int:
        """
        浏览收藏夹（OCR + CLAHE 方案）。

        进入收藏夹后滚动列表，随机点击条目查看详情并返回，
        循环直到指定时长。

        Args:
            duration_seconds: 浏览总时长（秒）

        Returns:
            查看的收藏条目数
        """
        from core.favorites_browser import FavoritesBrowser
        browser = FavoritesBrowser(self.d, account_id=self.account_id)
        return browser.browse(duration_seconds=duration_seconds)

    # ================================================================
    # 微信运动
    # ================================================================

    def open_wechat_sport(self) -> bool:
        """
        打开微信运动（如已启用）。

        路径: 搜索 "微信运动" → 进入公众号
        """
        return self.global_search("微信运动")

    # ================================================================
    # 内部辅助方法
    # ================================================================

    def _type_text(self, text: str):
        """
        拟人化输入文字。

        行为特征:
          - 逐字输入，非粘贴（粘贴是机器人特征）
          - 大部分字符快速，偶尔停顿（模拟思考）
          - 极小概率打错一个字符并删除重打
        """
        for i, char in enumerate(text):
            try:
                self.d.send_keys(char)
            except Exception:
                # fallback: 如果 send_keys 失败，尝试通过输入框 set_text
                pass

            # 5% 概率停顿（模拟思考或分心）
            if self.h.random.random() < 0.05:
                time.sleep(self.h.uniform(0.3, 0.8))
            else:
                time.sleep(self.h.uniform(0.03, 0.15))

            # 0.5% 概率打错一个字并删除重打
            if i > 0 and self.h.random.random() < 0.005:
                try:
                    self.d.press("delete")
                except Exception:
                    pass
                time.sleep(self.h.uniform(0.1, 0.3))
                try:
                    self.d.send_keys(char)
                except Exception:
                    pass

    def _search_and_open(self, name: str, target_type: str = "contact") -> bool:
        """
        通过全局搜索找到并打开目标（使用截图定位搜索结果）。

        Args:
            name: 搜索关键词
            target_type: 目标类型（contact / public_account / mini_program）
        """
        if not self.global_search(name):
            return False
        self.h.random_sleep(0.5, 1.0)

        # 使用截图定位搜索结果
        from core.screen_locator import ScreenLocator
        sl = ScreenLocator(self.d)
        results = sl.find_search_results(max_results=6)

        if not results:
            logger.warning(f"[{self.account_id}] 搜索结果为空: {name}")
            return False

        # 点击匹配的搜索结果（逐个尝试前几个）
        for r in results[:4]:
            self.d.click(r["x"], r["y"])
            self.h.random_sleep(1.0, 2.0)

            # 检查是否离开了搜索页面（顶部不再有搜索框）
            img = sl.screenshot()
            w, h = img.size
            # 采样顶部区域：搜索页面顶部有输入框（偏暗），离开后变亮
            top_pixel = img.getpixel((w // 2, int(h * 0.05)))[:3]
            if sum(top_pixel) > 400:  # 亮了，说明跳出了搜索页
                logger.info(f"[{self.account_id}] 成功打开: {name}")
                return True

            # 仍在搜索页，返回重试下一个
            self.d.press("back")
            self.h.random_sleep(0.5, 1.0)

        logger.warning(f"[{self.account_id}] 未找到匹配结果: {name}")
        return False

    def _debug_screenshot(self, tag: str):
        """保存调试截图"""
        save_debug_screenshot(self.d, self.account_id, tag)
