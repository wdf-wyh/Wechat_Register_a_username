"""
公众号浏览模块 — OCR + 拟人化操作
==================================

## 概述

从会话列表（或通讯录）点「公众号」进入文章流，
随机点击文章阅读、滚动浏览，模拟真人阅读行为。

## 工作流

::

    会话列表点「公众号」→ 失败则通讯录「公众号」→ 再失败则全局搜索
      │
      ├─ 浏览文章列表 (随机上下滚动)
      │
      ├─ OCR 找文章标题 → 随机选一篇 → 点击进入
      │     └─ 模拟阅读: 分段慢速滚动，读完后如需评论再滚到文末
      │     └─ （可选）写评论 / 读后发圈
      │     └─ press("back") 返回列表
      │
      └─ 循环直到指定时长

## 快速开始

.. code-block:: python

    from core.public_account_browser import PublicAccountBrowser
    browser = PublicAccountBrowser(device)
    browser.browse(duration_seconds=600)  # 浏览10分钟

## CLI 测试

.. code-block:: bash

    python test_public_account.py --duration 600

## 适配

基于 Moto X70 Air Pro (1264x2780, Android 14) 校准。
"""

import time
import random
import re
import cv2
import numpy as np

from utils.logger import get_logger

logger = get_logger("public_account_browser")

# 文章页底部强特征：留言区标题 + 输入框占位符须同时出现（OCR 子串匹配）
_ARTICLE_BOTTOM_TITLE = "留言"
_ARTICLE_BOTTOM_INPUT = "写留言"


class PublicAccountBrowser:
    """公众号浏览器 — 会话列表/通讯录进入 → 浏览文章 → 阅读。"""

    # 会话列表/通讯录入口文案：本机显示「公众号」（非旧版「订阅号」）
    _PA_ENTRY_KEYWORDS = ("公众号",)

    def __init__(self, d, account_id: str = "", persona: dict | None = None):
        self.d = d
        self.account_id = account_id
        self.persona = persona or {}
        self.w, self.h = d.info['displayWidth'], d.info['displayHeight']
        self._ocr = None
        self._clahe = None
        # 本轮 browse 内已读标题指纹，避免重复点开同一篇
        self._read_title_keys: set[str] = set()

    # ================================================================
    # 公共接口
    # ================================================================

    def browse(
        self,
        duration_seconds: int = 180,
        comment_rate: float = 0.15,
        post_after_read: bool = False,
        post_rate: float = 0.0,
    ) -> int:
        """
        浏览公众号文章，持续指定时长。

        Args:
            duration_seconds: 浏览总时长（秒）
            comment_rate: 写评论概率（每篇文章进入后尝试一次）

        Returns:
            阅读文章篇数
        """
        logger.info(f"[{self.account_id}] 公众号浏览: {duration_seconds}s")
        articles_read = 0
        self._read_title_keys.clear()

        try:
            self._open_public_accounts()

            start = time.time()
            while time.time() - start < duration_seconds:
                if random.random() < 0.4:
                    self._scroll_list()
                else:
                    if self._read_article(
                        comment_rate=comment_rate,
                        post_after_read=post_after_read,
                        post_rate=post_rate,
                    ):
                        articles_read += 1

            logger.info(f"[{self.account_id}] 公众号完成: {articles_read}篇")
        except Exception as e:
            logger.error(f"[{self.account_id}] 公众号异常: {e}")

        return articles_read

    # ================================================================
    # 进入公众号
    # ================================================================

    def _open_public_accounts(self):
        """优先会话列表点「公众号」；失败则通讯录；再失败全局搜索。"""
        from core.wechat_nav import WECHAT_PKG, goto_tab, start_wechat

        d = self.d
        # 已在前台则暖启动，避免「先开再杀」落到桌面后误点系统搜索框
        already = False
        try:
            already = d.app_current().get("package") == WECHAT_PKG
        except Exception:
            already = False
        start_wechat(d, wait=2.5 if already else 4.0, cold=not already)
        goto_tab(d, "wechat")
        time.sleep(0.6)

        # 预热 OCR，避免首轮点击前静默加载显得像卡在会话列表
        self._get_ocr()

        if self._open_from_chat_list():
            return
        if self._open_from_contacts():
            return
        logger.info(f"[{self.account_id}] 会话/通讯录未进入，改走全局搜索公众号")
        self._open_via_global_search()

    def _open_from_chat_list(self) -> bool:
        """微信 Tab 会话列表里点击「公众号」文件夹。"""
        from core.wechat_nav import goto_tab, ocr_find_and_click

        d, w, h = self.d, self.w, self.h
        goto_tab(d, "wechat")
        time.sleep(0.4)

        for attempt in range(1, 3):
            clicked = ocr_find_and_click(
                d,
                self._get_ocr(),
                list(self._PA_ENTRY_KEYWORDS),
                y_min_ratio=0.08,
                y_max_ratio=0.42,
                x_min_ratio=0.12,
                x_max_ratio=0.70,
                conf_min=0.40,
                enhance=self._enhance,
                exact=True,
                click_row_center=True,
                post_click_sleep=1.5,
            )
            if clicked and self._looks_like_pa_feed():
                logger.debug(f"[{self.account_id}] 已从会话列表进入公众号")
                return True
            if clicked:
                # 点到了但可能误入其它页，退回再试
                d.press("back")
                time.sleep(0.6)
                goto_tab(d, "wechat")
            else:
                logger.debug(
                    f"[{self.account_id}] 会话列表未找到公众号入口，重试 {attempt}/2"
                )
                # 轻微下拉刷新列表，避免未读角标挡住识别
                try:
                    d.swipe(w // 2, int(h * 0.35), w // 2, int(h * 0.55), 0.25)
                except Exception:
                    pass
                time.sleep(0.5)

        # 坐标兜底：红米等机型「公众号」常在会话列表第一行
        d.click(int(w * 0.40), int(h * 0.125))
        time.sleep(1.8)
        if self._looks_like_pa_feed():
            logger.debug(f"[{self.account_id}] 坐标点击会话列表公众号成功")
            return True
        d.press("back")
        time.sleep(0.5)
        logger.info(f"[{self.account_id}] 会话列表无公众号，改走通讯录→公众号")
        return False

    def _open_from_contacts(self) -> bool:
        """通讯录 → 点击「公众号」。"""
        from core.wechat_nav import goto_tab, ocr_find_and_click

        d, w, h = self.d, self.w, self.h
        goto_tab(d, "contacts")
        time.sleep(0.8)

        # 通讯录列表可能偏下，先轻微上滑露出「公众号」
        try:
            d.swipe(w // 2, int(h * 0.55), w // 2, int(h * 0.30), 0.3)
            time.sleep(0.5)
        except Exception:
            pass

        clicked = ocr_find_and_click(
            d,
            self._get_ocr(),
            list(self._PA_ENTRY_KEYWORDS),
            y_min_ratio=0.12,
            y_max_ratio=0.85,
            x_min_ratio=0.10,
            x_max_ratio=0.75,
            conf_min=0.40,
            enhance=self._enhance,
            exact=True,
            click_row_center=True,
            post_click_sleep=1.5,
        )
        if clicked and self._looks_like_pa_feed():
            logger.debug(f"[{self.account_id}] 已从通讯录进入公众号")
            return True
        return False

    def _looks_like_pa_feed(self) -> bool:
        """粗判已进入公众号订阅流 / 账号列表（已离开会话主列表）。"""
        blob = self._ocr_screen_blob(0.0, 0.35)
        # 仍在会话主列表：常见系统会话
        chat_markers = ("服务通知", "腾讯新闻", "微信支付", "微信团队")
        if sum(1 for k in chat_markers if k in blob) >= 2:
            return False
        if "通讯录" in blob and "发现" in blob and "微信(" in blob:
            return False
        # 订阅流 / 公众号相关顶栏或列表特征
        if any(k in blob for k in ("公众号", "已关注", "历史消息", "消息列表")):
            return True
        # 离开会话主列表后顶栏通常不再是「微信(N)」
        if "微信(" not in blob and "服务通知" not in blob:
            return True
        return False

    def _open_via_global_search(self):
        """全局搜索「公众号」→ 点击进入（兜底）。"""
        from core.wechat_nav import (
            goto_tab,
            open_search,
            ocr_find_and_click,
        )

        d, w, h = self.d, self.w, self.h
        goto_tab(d, "wechat")
        time.sleep(0.5)

        if not self._ensure_search_page(open_search):
            raise RuntimeError("未能进入微信搜索页")

        self._focus_search_box()

        try:
            d.set_input_ime(True)
            time.sleep(0.3)
            d.send_keys("公众号")
            time.sleep(0.5)
            d.set_input_ime(False)
        except Exception:
            try:
                d(focused=True).set_text("公众号")
            except Exception:
                pass

        d.press("enter")
        time.sleep(2)
        self._dismiss_unexpected_overlay()

        clicked = ocr_find_and_click(
            d,
            self._get_ocr(),
            list(self._PA_ENTRY_KEYWORDS),
            y_min_ratio=0.10,
            y_max_ratio=0.45,
            conf_min=0.45,
            enhance=self._enhance,
            exact=True,
            click_row_center=True,
        )
        if not clicked:
            if self._is_unexpected_overlay():
                d.press("back")
                time.sleep(0.8)
            d.click(int(w * 0.45), int(h * 0.22))
        time.sleep(3)
        self._dismiss_unexpected_overlay()

    # ================================================================
    # 阅读文章
    # ================================================================

    @staticmethod
    def _title_fingerprint(title: str) -> str:
        """去空白/标点后的标题指纹，用于已读去重与模糊匹配。"""
        text = re.sub(r"[^\w\u4e00-\u9fff]+", "", (title or "").strip())
        return text

    @staticmethod
    def _title_match_score(expected: str, haystack: str) -> float:
        """
        列表 OCR 标题 vs 文章页 OCR 的匹配分（0~1）。
        OCR 常截断/漏字，用最长连续子串占比。
        """
        a = PublicAccountBrowser._title_fingerprint(expected)
        b = PublicAccountBrowser._title_fingerprint(haystack)
        if not a or not b:
            return 0.0
        if a in b or b in a:
            return 1.0
        min_chunk = min(6, len(a))
        if len(a) < min_chunk:
            return 1.0 if a in b else 0.0
        best = 0
        # 从长到短找连续命中，避免短碎片误匹配
        for n in range(len(a), min_chunk - 1, -1):
            for i in range(0, len(a) - n + 1):
                if a[i : i + n] in b:
                    best = max(best, n)
                    break
            if best:
                break
        return best / len(a)

    def _title_already_read(self, title: str) -> bool:
        key = self._title_fingerprint(title)
        if not key:
            return False
        if key in self._read_title_keys:
            return True
        # 前缀碰撞：同一篇 OCR 长短不一
        for seen in self._read_title_keys:
            if key in seen or seen in key:
                return True
            if self._title_match_score(key, seen) >= 0.7:
                return True
        return False

    def _mark_title_read(self, title: str):
        key = self._title_fingerprint(title)
        if key:
            self._read_title_keys.add(key[:48])

    def _scan_feed_article_candidates(self) -> list[tuple[int, int, str, int]]:
        """OCR 订阅流，返回 (cx, cy, title, len) 候选，已读标题已过滤。"""
        d, w, h = self.d, self.w, self.h
        img = np.array(d.screenshot(format="pillow"))
        gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
        results = self._get_ocr().readtext(
            cv2.cvtColor(self._enhance(gray), cv2.COLOR_GRAY2BGR)
        )

        articles = []
        for bbox, text, conf in results:
            if conf <= 0.4:
                continue
            y0 = int(bbox[0][1])
            x0 = int(bbox[0][0])
            x1 = int(bbox[2][0])
            cy = int((bbox[0][1] + bbox[2][1]) / 2)
            cx = int((bbox[0][0] + bbox[2][0]) / 2)
            cleaned = self._normalize_ocr_text(text)
            # 公众号卡片：左侧常是头像/账号名（点进去进主页），中间才是标题
            if x0 < int(w * 0.12) and (x1 - x0) < int(w * 0.35):
                continue
            if not (
                int(h * 0.16) < y0 < int(h * 0.82)
                and int(w * 0.10) < cx < int(w * 0.92)
                and self._is_plausible_article_title(cleaned)
            ):
                continue
            if self._title_already_read(cleaned):
                continue
            articles.append((cx, cy, cleaned, len(cleaned)))

        articles.sort(key=lambda item: item[3], reverse=True)
        return articles

    def _looks_like_article_page(self) -> bool:
        """强特征：底栏留言/分享等（用于确认仍在正文、需要返回）。"""
        if self._is_public_account_home_or_history():
            return False
        bottom = self._ocr_screen_blob(0.72, 1.0)
        if any(
            k in bottom
            for k in ("说点什么", "写留言", "写评论", "发表评论", "阅读原文")
        ):
            return True
        return sum(1 for k in ("收藏", "分享", "在看", "赞") if k in bottom) >= 2

    def _is_on_article_feed(self) -> bool:
        """是否仍在订阅流/历史列表（未进入单篇正文）。"""
        if self._is_public_account_home_or_history():
            return True
        if self._looks_like_article_page():
            return False
        mid = self._ocr_screen_blob(0.12, 0.85)
        time_hits = 0
        for token in re.split(r"\s+", mid):
            if self._is_feed_timestamp(self._normalize_ocr_text(token)):
                time_hits += 1
        # 订阅流卡片几乎总有相对时间；正文页顶通常没有
        return time_hits >= 1

    def _feed_content_signature(self) -> str:
        """列表中部内容指纹，用于判断点击后页面是否变化。"""
        return self._title_fingerprint(self._ocr_screen_blob(0.14, 0.78))[:96]

    def _page_matches_title(self, title: str) -> bool:
        """打开后核对文章页是否出现目标标题（允许 OCR 截断）。"""
        top = self._ocr_screen_blob(0.04, 0.42)
        score = self._title_match_score(title, top)
        if score >= 0.42:
            return True
        wider = self._ocr_screen_blob(0.04, 0.65)
        return self._title_match_score(title, wider) >= 0.55

    def _ensure_on_feed_list(self, max_backs: int = 4) -> bool:
        """确保回到可点选文章的列表页，避免在正文里 OCR 到「相关阅读」当新文章。"""
        d = self.d
        for _ in range(max_backs):
            if self._is_unexpected_overlay():
                d.press("back")
                time.sleep(0.6)
                continue
            if self._is_image_viewer():
                d.press("back")
                time.sleep(0.6)
                continue
            if self._is_on_article_feed():
                return True
            if self._looks_like_article_page():
                d.press("back")
                time.sleep(1.0)
                continue
            # 无列表时间戳、也无文末底栏：多半还在正文页顶/中部
            mid = self._ocr_screen_blob(0.12, 0.75)
            if len(self._title_fingerprint(mid)) > 36:
                d.press("back")
                time.sleep(1.0)
                continue
            return True
        ok = self._is_on_article_feed() or not self._looks_like_article_page()
        if not ok:
            logger.warning(f"[{self.account_id}] 未能回到公众号文章列表")
        return ok

    def _try_open_article(self, cx: int, cy: int, title: str) -> bool:
        """
        点击标题并校验：页面已变化、已离开列表、标题匹配。
        失败时尽量回到列表，返回 False 供上层换一篇重试。
        """
        d, h = self.d, self.h
        before_sig = self._feed_content_signature()
        logger.debug(f"[{self.account_id}] 尝试打开: '{title[:24]}'")
        d.click(cx, min(int(h * 0.88), cy + int(h * 0.012)))
        time.sleep(1.35)

        if self._is_unexpected_overlay():
            logger.warning(f"[{self.account_id}] 打开文章后出现异常浮层")
            d.press("back")
            time.sleep(0.8)
            return False

        if self._is_public_account_home_or_history():
            logger.warning(
                f"[{self.account_id}] 误入公众号主页/历史列表(非文章正文)，返回重试"
            )
            d.press("back")
            time.sleep(1.0)
            return False

        if self._is_on_article_feed():
            logger.warning(
                f"[{self.account_id}] 点击后仍在文章列表，未打开: '{title[:20]}'"
            )
            return False

        after_sig = self._feed_content_signature()
        if before_sig and after_sig and before_sig == after_sig:
            logger.warning(
                f"[{self.account_id}] 点击后页面无变化，未打开: '{title[:20]}'"
            )
            return False

        if not self._page_matches_title(title):
            logger.warning(
                f"[{self.account_id}] 打开后标题不匹配(仍可能是上一篇): '{title[:20]}'"
            )
            d.press("back")
            time.sleep(1.0)
            self._ensure_on_feed_list(max_backs=2)
            return False

        logger.debug(f"[{self.account_id}] 已确认打开文章: '{title[:20]}'")
        return True

    def _read_article(
        self,
        comment_rate: float = 0.0,
        post_after_read: bool = False,
        post_rate: float = 0.0,
    ) -> bool:
        """随机选一篇未读文章 → 点击并校验打开 → 滚动阅读 → 返回列表。"""
        if not self._ensure_on_feed_list():
            return False

        articles = self._scan_feed_article_candidates()
        if not articles:
            self._scroll_list()
            return False

        # 优先长标题；最多尝试 3 个不同候选，避免点空/标题串文还当成功
        pool = articles[: min(8, len(articles))]
        pool.sort(key=lambda item: item[3], reverse=True)
        attempts = pool[: min(3, len(pool))]

        title = ""
        opened = False
        for cx, cy, cand_title, _ in attempts:
            if self._title_already_read(cand_title):
                continue
            if not self._ensure_on_feed_list(max_backs=2):
                return False
            if self._try_open_article(cx, cy, cand_title):
                title = cand_title
                opened = True
                break
            logger.debug(f"[{self.account_id}] 打开失败，换一篇重试")

        if not opened:
            self._scroll_list()
            return False

        logger.debug(f"[{self.account_id}] 阅读: '{title[:20]}'")

        # 先完整模拟阅读；需要评论/发圈时读完后再滚到文末。
        # 禁止 fast_bottom：评论前不得跳过阅读直接冲留言区。
        will_comment = random.random() < float(comment_rate)
        need_full_read = will_comment or post_after_read
        self._simulate_article_reading(scroll_to_bottom=need_full_read)

        # 非评论场景才随机点赞，避免点赞区误触配图拖慢评论
        if (not will_comment) and random.random() < 0.09:
            self._like_article()

        article_context = ""
        # 评论仅在阅读结束后进行
        # - 失败不影响返回列表/继续浏览
        # - comment_rate 控制触发频率，避免过度互动
        try:
            if will_comment:
                article_context = self._capture_article_context(title)
                pending_comment = self._gen_article_comment(
                    title=title, article_context=article_context
                )
                self._dismiss_image_viewer_if_any()
                if not self._is_comment_entry_visible():
                    self._scroll_to_article_bottom(
                        max_scrolls=6,
                        min_scrolls=2,
                        pause_range=(1.2, 2.8),
                    )
                commented = self._comment_article(
                    title=title,
                    article_context=article_context,
                    prepared_text=pending_comment,
                )
                if not commented:
                    time.sleep(0.4)
                    self._dismiss_image_viewer_if_any()
                    commented = self._comment_article(
                        title=title,
                        article_context=article_context,
                        prepared_text=pending_comment,
                    )
                if commented:
                    logger.info(f"[{self.account_id}] 公众号评论发送成功")
                else:
                    logger.warning(f"[{self.account_id}] 公众号评论发送失败")
        except Exception as e:
            logger.exception(f"[{self.account_id}] 公众号评论链路异常: {e}")

        if post_after_read and random.random() < float(post_rate):
            try:
                if not article_context:
                    article_context = self._capture_article_context(title)
                posted = self._post_from_article(title=title, article_context=article_context)
                if posted:
                    logger.info(f"[{self.account_id}] 文章读后发圈成功")
                else:
                    logger.warning(f"[{self.account_id}] 文章读后发圈失败")
            except Exception as e:
                logger.exception(f"[{self.account_id}] 文章读后发圈链路异常: {e}")

        self._mark_title_read(title)
        self._ensure_on_feed_list(max_backs=3)
        return True

    def _like_article(self):
        """
        点赞当前文章。
        底部按钮(从左到右): [点赞] [转发] [收藏/评论]
        点赞图标在最左边计数的左侧约50px。
        """
        d, w, h = self.d, self.w, self.h

        img = np.array(d.screenshot(format="pillow"))
        gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
        bottom = self._enhance(gray[int(h * 0.90):h, int(w * 0.4):w])
        results = self._get_ocr().readtext(cv2.cvtColor(bottom, cv2.COLOR_GRAY2BGR))

        counts = []
        for bbox, text, conf in results:
            if conf > 0.3 and any(c.isdigit() for c in text):
                cx = int((bbox[0][0]+bbox[2][0])/2) + int(w*0.4)
                cy = int((bbox[0][1]+bbox[2][1])/2) + int(h*0.90)
                counts.append((cx, cy))

        if counts:
            counts.sort(key=lambda c: c[0])
            lx = counts[0][0] - 50  # 点赞=最左边，图标在计数左侧
            ly = counts[0][1]
            logger.debug(f"[{self.account_id}] 点赞文章: ({lx},{ly})")
            d.click(lx, ly)
            time.sleep(0.8)

    def _gen_article_comment(self, title: str, article_context: str = "") -> str:
        """
        生成公众号文章评论文案。
        优先使用 LLM（若未配置 API Key 则回退到短模板）。
        """
        fallback = [
            "受益匪浅",
            "写得很清楚",
            "学到了，谢谢分享",
            "这个角度不错",
            "讲得挺到位",
            "确实有用",
            "太真实了",
        ]
        try:
            if not self.persona:
                return random.choice(fallback)
            from content.llm_client import LLMClient

            text = LLMClient().generate_article_comment(
                self.persona,
                title=title,
                article_context=article_context,
            )
            text = self._sanitize_comment_text(text)
            if text:
                return text[:40]
        except Exception:
            pass
        return random.choice(fallback)[:40]

    def _sanitize_comment_text(self, text: str) -> str:
        """清洗评论文案：去引号/前导误触符号/换行。"""
        t = (text or "").strip().strip('"\'「」『』')
        # 误触键盘常见前导：%% / ，， / 。。 等
        t = re.sub(r"^[%％,，.。、;；!！?？\s]+", "", t)
        t = re.sub(r"[\r\n\t]+", " ", t)
        t = re.sub(r"\s+", " ", t).strip()
        return t

    def _is_soft_keyboard_open(self) -> bool:
        """系统软键盘是否已弹出（弹出后再点底部坐标会误触「，」等键）。"""
        try:
            out = str(getattr(self.d.shell("dumpsys input_method"), "output", "") or "")
            if re.search(r"mInputShown\s*=\s*true", out, re.I):
                return True
        except Exception:
            pass
        return False

    def _arm_comment_ime(self) -> None:
        """切入 ADBKeyboard，收起系统键盘，避免后续误点逗号键。"""
        try:
            self.d.set_input_ime(True)
            time.sleep(0.15)
        except Exception:
            pass

    def _is_comment_compose_open(self) -> bool:
        """留言输入态是否已打开（EditText / 发送键 / 软键盘）。"""
        if self._is_soft_keyboard_open():
            return True
        d, h = self.d, self.h
        try:
            edits = d(className="android.widget.EditText")
            if edits.exists and edits.count >= 1:
                for e in edits:
                    try:
                        info = getattr(e, "info", {}) or {}
                        bounds = info.get("bounds") or ""
                        nums = list(map(int, re.findall(r"\d+", str(bounds))))
                        if len(nums) >= 4 and nums[3] >= int(h * 0.40):
                            return True
                        cy = int(e.center()[1])
                        if cy >= int(h * 0.40):
                            return True
                    except Exception:
                        continue
        except Exception:
            pass
        try:
            blob = self._ocr_region_blob(0.45, 1.0)
            if any(k in blob for k in ("发送", "发表", "提交")):
                return True
        except Exception:
            pass
        return False

    def _clear_comment_input(self) -> None:
        """清空当前评论草稿（去掉误触的，，等）。WebView 上 clear_text 常无效，需 DEL 兜底。"""
        d = self.d
        cleared = False
        try:
            focused = d(focused=True)
            if focused.exists:
                try:
                    focused.set_text("")
                    cleared = True
                except Exception:
                    pass
                try:
                    focused.clear_text()
                    cleared = True
                except Exception:
                    pass
        except Exception:
            pass
        if not cleared:
            try:
                edits = d(className="android.widget.EditText")
                if edits.exists and edits.count >= 1:
                    best = None
                    best_bottom = -1
                    h = self.h
                    for e in edits:
                        try:
                            info = getattr(e, "info", {}) or {}
                            bounds = info.get("bounds") or ""
                            nums = list(map(int, re.findall(r"\d+", str(bounds))))
                            bottom = nums[3] if len(nums) >= 4 else int(e.center()[1])
                            if bottom < int(h * 0.40):
                                continue
                            if bottom > best_bottom:
                                best_bottom = bottom
                                best = e
                        except Exception:
                            continue
                    if best is not None:
                        try:
                            best.set_text("")
                            cleared = True
                        except Exception:
                            pass
                        try:
                            best.clear_text()
                            cleared = True
                        except Exception:
                            pass
            except Exception:
                pass
        # WebView 无障碍清空失败时：光标移到末尾连删（覆盖误触的短前缀）
        try:
            d.shell("input keyevent 123 67 67 67 67 67 67 67 67 67 67 67 67")
            time.sleep(0.08)
        except Exception:
            try:
                for _ in range(12):
                    d.shell("input keyevent 67")  # DEL
            except Exception:
                pass
        time.sleep(0.08)

    def _find_pa_comment_send_green(
        self, y_min: float = 0.68, y_max: float = 0.96, x_min: float = 0.55
    ):
        """留言区输入栏右侧微信绿「发送」（有正文后由灰变绿）。"""
        try:
            shot = self.d.screenshot(format="opencv")
            if shot is None:
                return None
            h, w = shot.shape[:2]
            hsv = cv2.cvtColor(shot, cv2.COLOR_BGR2HSV)
            mask = cv2.inRange(hsv, np.array([35, 60, 60]), np.array([95, 255, 255]))
            y0, y1 = int(h * y_min), int(h * y_max)
            x0 = int(w * x_min)
            mask[:y0, :] = 0
            mask[y1:, :] = 0
            mask[:, :x0] = 0
            contours, _ = cv2.findContours(
                mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
            )
            best = None
            min_area = w * h * 0.0005
            max_area = w * h * 0.05
            for c in contours:
                x, y, bw, bh = cv2.boundingRect(c)
                area = bw * bh
                if area < min_area or area > max_area:
                    continue
                if bw < 18 or bh < 16:
                    continue
                if bw > w * 0.40 or bh > h * 0.12:
                    continue
                cx, cy = x + bw // 2, y + bh // 2
                if best is None or area > best[0]:
                    best = (area, cx, cy)
            if best is None:
                return None
            return best[1] / w, best[2] / h
        except Exception as e:
            logger.debug(f"[{self.account_id}] 公众号评论绿钮检测失败: {e}")
            return None

    def _click_pa_comment_send(
        self,
        draft_text: str,
        best_right: int | None = None,
        best_center_y: int | None = None,
    ) -> tuple[bool, bool]:
        """评论写入输入框后点击绿色「发送」，再 OCR/u2/坐标兜底。"""
        from core.wechat_nav import click_ratio, ocr_find_and_click

        d, w, h = self.d, self.w, self.h
        send_triggered = False
        sent = False
        keyboard_up = self._is_soft_keyboard_open()

        y_ranges: list[tuple[float, float]] = []
        if best_center_y is not None:
            cy = best_center_y / h
            y_ranges.append((max(0.35, cy - 0.08), min(0.98, cy + 0.08)))
        if keyboard_up:
            y_ranges.extend([(0.45, 0.78), (0.40, 0.82)])
        else:
            y_ranges.extend([(0.68, 0.96), (0.72, 0.92)])

        for y0, y1 in y_ranges:
            pt = self._find_pa_comment_send_green(y0, y1)
            if not pt:
                continue
            logger.info(
                f"[{self.account_id}] 公众号评论绿钮发送 @({pt[0]:.3f},{pt[1]:.3f})"
            )
            click_ratio(d, float(pt[0]), float(pt[1]))
            time.sleep(0.8)
            send_triggered = True
            if not self._is_comment_draft_still_present(draft_text):
                sent = True
            break

        if sent:
            return send_triggered, sent

        for exact in (True, False):
            ok = ocr_find_and_click(
                d,
                self._get_ocr(),
                ["发送", "发表", "提交"],
                y_min_ratio=0.45,
                y_max_ratio=0.99,
                x_min_ratio=0.55,
                x_max_ratio=0.99,
                conf_min=0.28 if exact else 0.25,
                enhance=self._enhance,
                exact=exact,
                post_click_sleep=0.8,
            )
            if ok:
                sent = True
                send_triggered = True
                break

        if not sent:
            for label in ("发送", "发表", "提交"):
                try:
                    node = d(text=label)
                    if node.exists(timeout=0.5):
                        node.click()
                        time.sleep(0.8)
                        sent = True
                        send_triggered = True
                        break
                except Exception:
                    pass

        if not sent:
            try:
                send_x = int(w * 0.90)
                send_y = int(h * 0.88)
                if best_right is not None and best_center_y is not None:
                    send_x = min(w - 1, int(best_right) + 28)
                    send_y = int(best_center_y)
                else:
                    try:
                        from config.device_profiles import get_extra

                        pt = get_extra(d, "pa_comment_send")
                        if pt:
                            send_x, send_y = int(w * pt[0]), int(h * pt[1])
                    except Exception:
                        pass
                d.click(send_x, send_y)
                time.sleep(0.7)
                send_triggered = True
                for label in ("发送", "发表", "提交"):
                    try:
                        node = d(text=label)
                        if node.exists(timeout=0.35):
                            node.click()
                            time.sleep(0.7)
                            sent = True
                            break
                    except Exception:
                        pass
                if not sent:
                    pt = self._find_pa_comment_send_green(0.45, 0.99)
                    if pt:
                        click_ratio(d, float(pt[0]), float(pt[1]))
                        time.sleep(0.7)
                        send_triggered = True
                        if not self._is_comment_draft_still_present(draft_text):
                            sent = True
                if not sent:
                    ok_retry = ocr_find_and_click(
                        d,
                        self._get_ocr(),
                        ["发送", "发表", "提交"],
                        y_min_ratio=0.45,
                        y_max_ratio=0.99,
                        x_min_ratio=0.55,
                        x_max_ratio=0.99,
                        conf_min=0.22,
                        enhance=self._enhance,
                        exact=False,
                        post_click_sleep=0.7,
                    )
                    if ok_retry:
                        sent = True
            except Exception:
                pass

        return send_triggered, sent

    def _comment_article(
        self,
        title: str,
        article_context: str = "",
        prepared_text: str = "",
    ) -> bool:
        """
        在当前文章页尝试点击「写评论」并发送一条评论。
        不保证所有机型/版本都能 100% 命中，失败则返回 False。
        """
        d, w, h = self.d, self.w, self.h

        text = (prepared_text or "").strip() or self._gen_article_comment(
            title=title, article_context=article_context
        )
        text = self._sanitize_comment_text(text)
        if not text:
            return False
        logger.info(f"[{self.account_id}] 公众号评论尝试: {text[:18]}")

        self._dismiss_image_viewer_if_any()

        # 0) 评论前确保已滚到文末留言区（调用方应已先完成阅读，此处仅兜底）
        if not self._is_comment_entry_visible():
            self._scroll_to_article_bottom(
                max_scrolls=5, min_scrolls=2, pause_range=(1.0, 2.2)
            )

        # 1) 只点明确入口，禁止裸匹配「评论/留言」（易点到评论区配图）
        inline_clicked = False
        try:
            from core.wechat_nav import ocr_find_and_click

            # 键盘未开时可点到底栏；已开则收窄，避免点到「，」
            y_max = 0.78 if self._is_soft_keyboard_open() else 0.92
            inline_clicked = ocr_find_and_click(
                d,
                self._get_ocr(),
                ["写评论", "写留言", "说点什么", "发表评论"],
                y_min_ratio=0.55,
                y_max_ratio=y_max,
                x_min_ratio=0.05,
                x_max_ratio=0.72,
                conf_min=0.30,
                enhance=self._enhance,
                exact=False,
                click_x_bias=-0.25,
                post_click_sleep=0.7,
            )
            if inline_clicked:
                time.sleep(0.35)
                self._dismiss_image_viewer_if_any()
                self._arm_comment_ime()
        except Exception:
            pass

        clicked_panel = inline_clicked
        if not inline_clicked:
            try:
                from core.wechat_nav import ocr_find_and_click

                y_max = 0.78 if self._is_soft_keyboard_open() else 0.92
                # 仅「收藏/评论」整词，避免单独「收藏」「评论」点到图片/赞区
                clicked_panel = ocr_find_and_click(
                    d,
                    self._get_ocr(),
                    ["收藏/评论", "写评论", "写留言", "说点什么", "发表评论"],
                    y_min_ratio=0.72,
                    y_max_ratio=y_max,
                    x_min_ratio=0.35,
                    x_max_ratio=0.98,
                    conf_min=0.30,
                    enhance=self._enhance,
                    exact=False,
                    click_x_bias=-0.15,
                    post_click_sleep=0.7,
                )
                time.sleep(0.35)
                self._dismiss_image_viewer_if_any()
                if clicked_panel:
                    self._arm_comment_ime()
            except Exception:
                pass

            if not clicked_panel and not self._is_comment_compose_open():
                logger.warning(
                    f"[{self.account_id}] 公众号评论入口未找到(写评论/说点什么)"
                )
                # 禁止在软键盘弹出时点底部；未弹出时只点一次偏上输入条
                if not self._is_soft_keyboard_open():
                    try:
                        d.click(int(w * 0.30), int(h * 0.86))
                        time.sleep(0.45)
                        self._dismiss_image_viewer_if_any()
                        self._arm_comment_ime()
                    except Exception:
                        pass

        # 2) 写入输入框（公众号留言多为 WebView：EditText 可读性差，需 OCR 校验）
        text_written = False
        best_right = None
        best_center_y = None
        try:
            edits = d(className="android.widget.EditText")
            usable_edit = False
            best_edit = None
            if edits.exists and edits.count >= 1:
                best_bottom = -1
                for e in edits:
                    try:
                        info = getattr(e, "info", {}) or {}
                        bounds = info.get("bounds")
                        bottom = None
                        right = None
                        center_y = None
                        if bounds:
                            nums = list(map(int, re.findall(r"\d+", bounds)))
                            if len(nums) >= 4:
                                # 只要下半屏输入框，避开顶栏搜索 EditText
                                if nums[3] < int(h * 0.40):
                                    continue
                                bottom = nums[3]
                                right = nums[2]
                                center_y = int((nums[1] + nums[3]) / 2)
                        if bottom is None:
                            center = e.center()
                            if int(center[1]) < int(h * 0.40):
                                continue
                            bottom = int(center[1])
                            center_y = int(center[1])
                        if bottom > best_bottom:
                            best_bottom = bottom
                            best_edit = e
                            best_right = right
                            best_center_y = center_y
                    except Exception:
                        continue

                if best_edit is not None:
                    usable_edit = True
                    try:
                        best_edit.click()
                    except Exception:
                        pass
                    self._arm_comment_ime()
                    time.sleep(0.15)
                    # 先清空误触的「，，」等，再写正文
                    self._clear_comment_input()
                    try:
                        best_edit.set_text(text)
                    except Exception:
                        pass
                    time.sleep(0.25)
                    # WebView 常读不到 info.text：结合 OCR /「发送」再判断，避免重复写入
                    if self._comment_input_looks_filled(text, edit=best_edit):
                        text_written = True

            if not text_written:
                from core.wechat_nav import ocr_find_and_click

                # 无可用 EditText 或 set_text 未确认成功：再点入口后走 IME（只写一次）
                # 入口已点开 / 已在输入态：禁止再点底部坐标（会打到键盘「，」）
                compose_ready = (
                    usable_edit
                    or inline_clicked
                    or clicked_panel
                    or self._is_comment_compose_open()
                )
                if not compose_ready:
                    clicked_hint = ocr_find_and_click(
                        d,
                        self._get_ocr(),
                        ["说点什么", "写评论", "发表评论", "写留言"],
                        y_min_ratio=0.45,
                        # 避开系统键盘区域，防止误触逗号键
                        y_max_ratio=0.78,
                        x_min_ratio=0.05,
                        x_max_ratio=0.72,
                        conf_min=0.28,
                        enhance=self._enhance,
                        exact=False,
                        click_x_bias=-0.25,
                        post_click_sleep=0.55,
                    )
                    self._dismiss_image_viewer_if_any()
                    if clicked_hint:
                        self._arm_comment_ime()
                    elif (
                        not self._is_comment_compose_open()
                        and not self._is_soft_keyboard_open()
                    ):
                        # 只点一次偏上位置；键盘已开绝不点
                        try:
                            d.click(int(w * 0.32), int(h * 0.72))
                            time.sleep(0.4)
                            self._dismiss_image_viewer_if_any()
                            self._arm_comment_ime()
                        except Exception:
                            pass
                    if (
                        not clicked_hint
                        and not self._is_comment_compose_open()
                        and not inline_clicked
                        and not clicked_panel
                    ):
                        logger.warning(
                            f"[{self.account_id}] 公众号评论输入框入口未找到(说点什么/写评论)"
                        )
                        return False
                else:
                    self._arm_comment_ime()
                # 进 IME 前清空：去掉误触标点，并避免 set_text 漏检叠写
                self._clear_comment_input()
                text_written = self._type_comment_via_ime(text)
        except Exception:
            return False

        if not text_written:
            logger.warning(f"[{self.account_id}] 评论文本未写入成功，放弃发送")
            return False

        # 3) 写入成功后优先点绿色「发送」，再 OCR/u2/坐标兜底
        try:
            send_triggered, sent = self._click_pa_comment_send(
                text, best_right=best_right, best_center_y=best_center_y
            )
        except Exception:
            send_triggered, sent = False, False

        draft_remains = self._is_comment_draft_still_present(text)

        still_input = False
        try:
            blob = self._ocr_screen_blob(0.35, 1.0)
            if any(k in blob for k in ("发表评论", "说点什么", "写评论", "输入评论")):
                # 若草稿已清空且出现占位「写评论」，更像已发送回到入口态
                if draft_remains or (not sent):
                    still_input = True
                try:
                    retry_triggered, retry_sent = self._click_pa_comment_send(
                        text, best_right=best_right, best_center_y=best_center_y
                    )
                    if retry_triggered:
                        send_triggered = True
                    if retry_sent:
                        sent = True
                        time.sleep(0.4)
                        draft_remains = self._is_comment_draft_still_present(text)
                        still_input = bool(draft_remains)
                    elif draft_remains:
                        d.press("back")
                        time.sleep(0.5)
                        self._dismiss_image_viewer_if_any()
                except Exception:
                    pass
        except Exception:
            pass

        draft_remains = self._is_comment_draft_still_present(text)
        # 严格：必须确认发送成功（OCR/u2/坐标+草稿消失），且草稿不再残留
        ok = bool(sent and (not draft_remains))
        logger.debug(
            f"[{self.account_id}] 公众号评论结果: {ok} "
            f"(send_triggered={send_triggered}, sent={sent}, "
            f"still_input={still_input}, draft_remains={draft_remains})"
        )
        return ok

    def _type_comment_via_ime(self, text: str) -> bool:
        """
        通过剪贴板粘贴 / send_keys 写入评论，并用 OCR 校验是否出现在输入区。
        WebView 留言框常无可靠 EditText.info.text。
        """
        t = self._sanitize_comment_text(text)
        if not t:
            return False
        d = self.d
        # 写入前清空，避免残留误触「，，」再叠正文
        self._clear_comment_input()
        # 1) 剪贴板粘贴（少改输入法状态）
        try:
            d.set_clipboard(t)
            time.sleep(0.1)
            d.shell("input keyevent 279")  # PASTE
            time.sleep(0.45)
            if self._comment_input_looks_filled(t):
                return True
        except Exception:
            pass
        # 2) send_keys（仅粘贴未确认成功时才写入；先清空，避免粘贴已生效时叠写）
        try:
            self._clear_comment_input()
            try:
                d.set_input_ime(True)
            except Exception:
                pass
            time.sleep(0.15)
            d.send_keys(t)
            time.sleep(0.35)
            try:
                d.set_input_ime(False)
            except Exception:
                pass
        except Exception:
            return False
        return self._comment_input_looks_filled(t)

    def _comment_input_looks_filled(self, draft_text: str, edit=None) -> bool:
        """
        判定评论是否已写入：必须有正文证据。
        - info.text 匹配目标文案（忽略空白/换行）
        - 或 OCR 下半屏出现草稿片段
        「发送」键单独不算成功：空输入框聚焦也会出现发送。
        """
        text = re.sub(r"[\r\n\t]+", " ", (draft_text or "")).strip()
        text = re.sub(r"\s+", " ", text)
        if not text:
            return False
        probe = text[: min(6, len(text))]

        # 1) info.text：实质内容且匹配目标（纯换行/空白不算）
        try:
            nodes = [edit] if edit is not None else []
            if not nodes:
                edits = self.d(className="android.widget.EditText")
                if edits.exists and edits.count >= 1:
                    nodes = list(edits)
            for e in nodes:
                if e is None:
                    continue
                try:
                    info = getattr(e, "info", {}) or {}
                    bounds = info.get("bounds")
                    if bounds:
                        nums = list(map(int, re.findall(r"\d+", str(bounds))))
                        if len(nums) >= 4 and nums[3] < int(self.h * 0.40):
                            continue
                    raw = str(info.get("text", "") or "")
                    val = re.sub(r"[\r\n\t]+", " ", raw).strip()
                    val = re.sub(r"\s+", " ", val)
                except Exception:
                    val = ""
                if not val:
                    continue
                if val == text or text in val:
                    return True
                # 短 probe 易误匹配，至少 2 字才用片段命中
                if len(probe) >= 2 and probe in val:
                    return True
        except Exception:
            pass

        # 2) OCR 草稿片段（正文证据）
        return self._ocr_comment_draft_visible(text)

    def _ocr_comment_draft_visible(self, draft_text: str) -> bool:
        """OCR 下半屏是否出现待发评论片段。"""
        text = re.sub(r"[\r\n\t]+", " ", (draft_text or "")).strip()
        text = re.sub(r"\s+", " ", text)
        if not text:
            return False
        blob = self._ocr_region_blob(0.42, 1.0)
        if text in blob:
            return True
        # 至少取 2 字，避免单字/标点误命中
        probe_len = min(6, len(text))
        if probe_len < 2:
            return False
        probe = text[:probe_len]
        return probe in blob

    def _is_comment_draft_still_present(self, draft_text: str) -> bool:
        """判断发送后输入框是否仍残留评论草稿，避免“误判已发送”。

        注意：已发出的评论会出现在留言列表，不能全屏 OCR 搜原文。
        仅在仍有「发送/发表」输入态时，才用 OCR 查输入区草稿。
        """
        text = (draft_text or "").strip()
        if not text:
            return False
        probe = text[: min(8, len(text))]
        try:
            edits = self.d(className="android.widget.EditText")
            if edits.exists and edits.count >= 1:
                for e in edits:
                    try:
                        info = getattr(e, "info", {}) or {}
                        bounds = info.get("bounds")
                        if bounds:
                            nums = list(map(int, re.findall(r"\d+", str(bounds))))
                            if len(nums) >= 4 and nums[3] < int(self.h * 0.40):
                                continue
                        val = str(info.get("text", "") or "").strip()
                    except Exception:
                        val = ""
                    if not val:
                        continue
                    if val == text or probe in val:
                        return True
        except Exception:
            pass

        blob = self._ocr_region_blob(0.55, 1.0)
        composing = any(k in blob for k in ("发送", "发表", "提交"))
        if not composing:
            return False
        return self._ocr_comment_draft_visible(text)

    def _scroll_list(self):
        """随机上下滚动文章列表。"""
        if random.random() < 0.5:
            self.d.swipe(self.w // 2, int(self.h * 0.6),
                          self.w // 2, int(self.h * 0.34), duration=0.45)
        else:
            self.d.swipe(self.w // 2, int(self.h * 0.3),
                          self.w // 2, int(self.h * 0.62), duration=0.50)
        time.sleep(random.uniform(1.2, 2.8))

    def _ensure_search_page(self, open_search_fn) -> bool:
        """进入微信搜索页，并规避误触顶栏 + 菜单。"""
        from core.wechat_nav import WECHAT_PKG

        try:
            if self.d.app_current().get("package") != WECHAT_PKG:
                return False
        except Exception:
            return False

        if self._is_search_page():
            return True
        for _ in range(2):
            opened = False
            try:
                opened = bool(open_search_fn(self.d))
            except Exception:
                opened = False
            time.sleep(0.5)
            self._dismiss_unexpected_overlay()
            if self._is_search_page():
                return True
            # open_search 用像素差判定成功时，OCR 可能读不到「搜索指定内容」
            if opened and not self._is_unexpected_overlay():
                # 二次确认仍在微信，避免桌面误点后继续输入
                try:
                    if self.d.app_current().get("package") == WECHAT_PKG:
                        return True
                except Exception:
                    pass
            try:
                self.d.press("back")
                time.sleep(0.4)
            except Exception:
                pass
        return self._is_search_page()

    def _focus_search_box(self):
        """只在已进入搜索页后聚焦搜索框，避免顶栏误点到系统搜索。"""
        from core.wechat_nav import WECHAT_PKG

        try:
            if self.d.app_current().get("package") != WECHAT_PKG:
                raise RuntimeError("微信不在前台，拒绝点击顶部搜索框")
        except RuntimeError:
            raise
        except Exception:
            pass
        if self._is_unexpected_overlay():
            self._dismiss_unexpected_overlay()
        if self._is_unexpected_overlay():
            raise RuntimeError("仍在异常浮层，拒绝聚焦搜索框")
        self.d.click(int(self.w * 0.5), int(self.h * 0.065))
        time.sleep(0.5)

    def _ocr_screen_blob(self, y_min_ratio: float = 0.0, y_max_ratio: float = 1.0) -> str:
        img = np.array(self.d.screenshot(format="pillow"))
        gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
        y0 = int(self.h * y_min_ratio)
        y1 = int(self.h * y_max_ratio)
        crop = gray[y0:y1, :]
        results = self._get_ocr().readtext(cv2.cvtColor(self._enhance(crop), cv2.COLOR_GRAY2BGR))
        texts = []
        for _, text, conf in results:
            if conf > 0.35:
                texts.append(self._normalize_ocr_text(text))
        return " ".join(t for t in texts if t)

    def _is_search_page(self) -> bool:
        blob = self._ocr_screen_blob(0.0, 0.28)
        # 不能单靠「公众号」一字判定：订阅流顶栏也会出现
        if "搜索指定内容" in blob:
            return True
        tabs = ("聊天记录", "朋友圈", "文章", "公众号", "小程序", "联系人", "全部")
        tab_hits = sum(1 for k in tabs if k in blob)
        if tab_hits >= 2:
            return True
        if "搜索" in blob and tab_hits >= 1:
            return True
        return False

    def _is_unexpected_overlay(self) -> bool:
        blob = self._ocr_screen_blob(0.0, 0.40)
        bad_markers = (
            "发起群聊",
            "添加朋友",
            "扫一扫",
            "收付款",
            "应用推荐",
            "热搜榜",
            "搜索发现",
        )
        return any(k in blob for k in bad_markers)

    def _dismiss_unexpected_overlay(self):
        if self._is_unexpected_overlay():
            self.d.press("back")
            time.sleep(0.6)

    def _is_image_viewer(self) -> bool:
        """文末点到配图后常见全屏大图：无留言入口、无正文操作栏。"""
        top = self._ocr_screen_blob(0.0, 0.20)
        mid = self._ocr_screen_blob(0.20, 0.78)
        bottom = self._ocr_screen_blob(0.78, 1.0)
        all_blob = f"{top} {mid} {bottom}"
        if any(
            k in all_blob
            for k in ("写评论", "写留言", "说点什么", "收藏/评论", "搜索指定内容")
        ):
            return False
        if sum(1 for k in ("收藏", "分享", "在看", "阅读原文") if k in bottom) >= 2:
            return False
        viewer_markers = ("保存到手机", "识别图中二维码", "用其他应用打开")
        if any(k in all_blob for k in viewer_markers):
            return True
        tokens = [t for t in re.split(r"\s+", mid) if t]
        # 几乎无文字 + 底栏也无文章操作 → 大概率全屏图
        return len(tokens) <= 1 and not any(
            k in bottom for k in ("收藏", "分享", "赞", "在看", "留言")
        )

    def _dismiss_image_viewer_if_any(self) -> bool:
        """若误开文章配图预览，按返回关闭后继续评论。"""
        try:
            if not self._is_image_viewer():
                return False
            logger.warning(f"[{self.account_id}] 检测到文章配图全屏，按返回关闭")
            self.d.press("back")
            time.sleep(0.7)
            # 再确认一次
            if self._is_image_viewer():
                self.d.press("back")
                time.sleep(0.5)
            return True
        except Exception:
            return False

    def _is_comment_entry_visible(self) -> bool:
        """是否看到可点的留言入口（比泛化底栏更严）。"""
        blob = self._ocr_region_blob(0.55, 1.0)
        return any(
            k in blob for k in ("写评论", "写留言", "说点什么", "发表评论", "收藏/评论")
        )

    def _normalize_ocr_text(self, text: str) -> str:
        text = (text or "").strip()
        text = re.sub(r"\s+", "", text)
        return text

    def _is_plausible_article_title(self, text: str) -> bool:
        if not text or len(text) < 6 or len(text) > 40:
            return False
        # 整段等于导航/账号页短词
        banned_exact = {
            "公众号", "文章", "搜索", "聊天记录", "朋友圈", "小程序",
            "发起群聊", "添加朋友", "扫一扫", "收付款", "应用推荐",
            "热搜榜", "推荐", "热搜", "生活", "娱乐", "话题", "搜索发现",
            "已关注", "关注", "发消息", "取消关注", "订阅", "历史消息",
            "精选", "视频号", "服务",
        }
        if text in banned_exact:
            return False
        # 仅对明确 UI 短语做子串拒绝，避免误杀含“关注/服务”的正常标题
        banned_substr = (
            "聊天记录", "发起群聊", "添加朋友", "应用推荐", "搜索发现",
            "已关注", "取消关注", "历史消息", "视频号", "发消息",
        )
        if any(k in text for k in banned_substr):
            return False
        # 列表时间戳常被 OCR 当成“标题”，点它容易进公众号主页
        if self._is_feed_timestamp(text):
            return False
        if text.isdigit():
            return False
        if sum(ch.isdigit() for ch in text) >= max(3, len(text) // 2):
            return False
        return True

    def _is_feed_timestamp(self, text: str) -> bool:
        """公众号订阅流里的相对时间 / 日期，不是文章标题。"""
        t = (text or "").strip()
        if not t:
            return False
        exact = {
            "刚刚", "昨天", "前天", "今天", "明天", "星期日", "星期一",
            "星期二", "星期三", "星期四", "星期五", "星期六",
        }
        if t in exact:
            return True
        patterns = (
            r"^\d+\s*(秒|分钟|分|小时|小時|天|周|週|个月|月)前$",
            r"^\d{1,2}:\d{2}$",
            r"^\d{1,2}月\d{1,2}日$",
            r"^\d{4}[-/年]\d{1,2}[-/月]\d{1,2}日?$",
        )
        return any(re.match(p, t) for p in patterns)

    def _is_public_account_home_or_history(self) -> bool:
        """
        判断当前是否误入公众号主页 / 账号历史消息列表（而非单篇正文）。

        注意：文章页顶栏也可能出现「已关注」，不能单凭它判定。
        """
        top = self._ocr_screen_blob(0.0, 0.30)
        mid = self._ocr_screen_blob(0.18, 0.75)
        # 主页强特征（文章页一般没有）
        if any(k in top for k in ("发消息", "音视频通话")):
            return True
        if "视频号" in top and "服务" in top:
            return True
        if "历史消息" in top or "历史消息" in mid:
            return True

        # 历史/订阅列表：多条相对时间并存，且看不到正文留言特征
        time_hits = 0
        for token in re.split(r"\s+", mid):
            if self._is_feed_timestamp(self._normalize_ocr_text(token)):
                time_hits += 1
        if time_hits >= 3 and not any(
            k in mid for k in ("说点什么", "写留言", "写评论", "阅读原文", "在看")
        ):
            return True
        return False

    def _capture_article_context(self, title: str) -> str:
        """抓取文章页可见正文摘要，供评论/发圈生成使用。"""
        img = np.array(self.d.screenshot(format="pillow"))
        gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
        y0, y1 = int(self.h * 0.16), int(self.h * 0.80)
        crop = gray[y0:y1, :]
        results = self._get_ocr().readtext(cv2.cvtColor(self._enhance(crop), cv2.COLOR_GRAY2BGR))
        lines = []
        for _, text, conf in results:
            clean = self._normalize_ocr_text(text)
            if conf <= 0.38 or not clean or clean == title:
                continue
            if len(clean) <= 1:
                continue
            if any(k in clean for k in ("广告", "分享", "收藏", "点赞", "写留言", "说点什么")):
                continue
            lines.append(clean)
        if not lines:
            return title[:120]
        return " ".join(lines[:8])[:320]

    def _simulate_article_reading(
        self,
        article_context: str = "",
        scroll_to_bottom: bool = False,
        fast_bottom: bool = False,
    ):
        """
        分段慢速阅读；需要评论/发圈时，读完后再滚到文末。

        fast_bottom 仅作兼容保留：为 True 时跳过分段阅读、快滑到底（调用方
        不应在评论链路启用，否则等于未读完就去留言）。
        """
        w, h = self.w, self.h
        if fast_bottom and scroll_to_bottom:
            time.sleep(random.uniform(0.4, 0.8))
            reached = self._scroll_to_article_bottom(
                max_scrolls=14,
                min_scrolls=1,
                pause_range=(0.45, 0.95),
            )
            logger.debug(
                f"[{self.account_id}] 文章快滑至底部: {reached}"
            )
            return

        time.sleep(random.uniform(0.8, 1.6))

        # 先按真人节奏分段阅读正文
        total_scrolls = random.randint(4, 7)
        if article_context and len(article_context) > 120:
            total_scrolls += 1

        for idx in range(total_scrolls):
            self._article_read_scroll_once(w, h)
            time.sleep(random.uniform(2.4, 5.5))
            if idx < total_scrolls - 1 and random.random() < 0.18:
                self.d.swipe(
                    int(w * random.uniform(0.48, 0.54)),
                    int(h * random.uniform(0.40, 0.48)),
                    int(w * random.uniform(0.48, 0.54)),
                    int(h * random.uniform(0.52, 0.62)),
                    duration=random.uniform(0.35, 0.65),
                )
                time.sleep(random.uniform(1.0, 2.5))

        # 评论/发圈：阅读完成后再补滚到留言区
        if scroll_to_bottom:
            if self._is_article_bottom_visible():
                logger.debug(f"[{self.account_id}] 文章阅读至底部: True (分段阅读后已可见)")
                return
            reached = self._scroll_to_article_bottom(
                max_scrolls=12,
                min_scrolls=2,
                pause_range=(2.0, 5.0),
            )
            logger.debug(
                f"[{self.account_id}] 文章阅读至底部: {reached}"
            )

    def _article_read_scroll_once(self, w: int, h: int):
        """单次向下阅读滑动。"""
        start_x = int(w * random.uniform(0.47, 0.56))
        end_x = int(w * random.uniform(0.45, 0.54))
        start_y = int(h * random.uniform(0.73, 0.82))
        end_y = int(h * random.uniform(0.40, 0.56))
        duration = random.uniform(0.75, 1.25)
        self.d.swipe(start_x, start_y, end_x, end_y, duration=duration)

    def _ocr_region_blob(self, y_min_ratio: float, y_max_ratio: float) -> str:
        return self._ocr_screen_blob(y_min_ratio, y_max_ratio)

    def _is_article_bottom_visible(self) -> bool:
        """OCR 判断当前是否已滚到文章底部（「留言」与「写留言」须同时可见）。"""
        blob = self._ocr_region_blob(0.52, 1.0)
        if _ARTICLE_BOTTOM_INPUT not in blob:
            return False
        # 「留言」须作为区块标题独立出现，不能仅靠「写留言」内的子串
        remainder = blob.replace(_ARTICLE_BOTTOM_INPUT, "")
        return _ARTICLE_BOTTOM_TITLE in remainder

    def _scroll_to_article_bottom(
        self,
        max_scrolls: int = 16,
        min_scrolls: int = 2,
        pause_range: tuple[float, float] = (1.2, 3.5),
    ) -> bool:
        """
        持续向下滚动直到文末特征出现，或达到上限/内容不再变化。

        Returns:
            是否检测到文末特征
        """
        w, h = self.w, self.h
        prev_tail = ""
        stagnant = 0

        for idx in range(max_scrolls):
            if idx >= min_scrolls and self._is_article_bottom_visible():
                return True

            self._article_read_scroll_once(w, h)
            time.sleep(random.uniform(*pause_range))

            tail = self._ocr_region_blob(0.68, 1.0)[:120]
            if tail and tail == prev_tail:
                stagnant += 1
                if stagnant >= 2 and idx + 1 >= min_scrolls:
                    logger.debug(
                        f"[{self.account_id}] 文章滚动停滞，视为已到底 (scrolls={idx + 1})"
                    )
                    return self._is_article_bottom_visible()
            else:
                stagnant = 0
            prev_tail = tail

        return self._is_article_bottom_visible()

    def _post_from_article(self, title: str, article_context: str) -> bool:
        if not self.persona:
            return False
        from content.llm_client import LLMClient
        from core.humanizer import Humanizer
        from core.wechat_control import WeChatControl

        text = LLMClient().generate_post_from_article(
            self.persona,
            title=title,
            article_context=article_context,
        )
        text = (text or "").strip().strip('"\'「」')
        if not text:
            return False
        self.d.press("back")
        time.sleep(1.2)
        wc = WeChatControl(self.d, Humanizer(), account_id=self.account_id)
        return wc.post_moment(text)

    # ================================================================
    # 工具
    # ================================================================

    def _enhance(self, gray):
        if self._clahe is None:
            self._clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
        return self._clahe.apply(gray)

    def _get_ocr(self):
        if self._ocr is None:
            from utils.ocr_utils import create_easyocr_reader
            self._ocr = create_easyocr_reader()
        return self._ocr
