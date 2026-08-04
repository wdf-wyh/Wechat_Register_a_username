"""
公众号浏览模块 — OCR + 拟人化操作
==================================

## 概述

全局搜索"公众号"进入公众号文章列表，
随机点击文章阅读、滚动浏览，模拟真人阅读行为。

## 工作流

::

    搜索"公众号" → OCR 找结果 → 点击进入
      │
      ├─ 浏览文章列表 (随机上下滚动)
      │
      ├─ OCR 找文章标题 → 随机选一篇 → 点击进入
      │     └─ 模拟阅读: 滚动 2~5 次，每次停留 1.5~4s
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

# 文章页底部/留言区 OCR 特征（用于判断“已读到底”）
_ARTICLE_BOTTOM_MARKERS = (
    "说点什么",
    "写留言",
    "写评论",
    "发表评论",
    "输入评论",
    "留言",
    "在看",
    "阅读原文",
)
_ARTICLE_BOTTOM_BAR_MARKERS = ("收藏", "分享", "赞", "在看")


class PublicAccountBrowser:
    """公众号浏览器 — 搜索进入 → 浏览文章 → 阅读。"""

    def __init__(self, d, account_id: str = "", persona: dict | None = None):
        self.d = d
        self.account_id = account_id
        self.persona = persona or {}
        self.w, self.h = d.info['displayWidth'], d.info['displayHeight']
        self._ocr = None
        self._clahe = None

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
    # 搜索进入
    # ================================================================

    def _open_public_accounts(self):
        """全局搜索'公众号' → 点击进入。"""
        from core.wechat_nav import goto_tab, open_search, ocr_find_and_click, start_wechat

        d, w, h = self.d, self.w, self.h
        start_wechat(d, wait=4.0, cold=True)
        goto_tab(d, "wechat")
        time.sleep(1.0)

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
            ["公众号"],
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

    def _read_article(
        self,
        comment_rate: float = 0.0,
        post_after_read: bool = False,
        post_rate: float = 0.0,
    ) -> bool:
        """随机选一篇文章 → 点击 → 滚动阅读 → 返回。"""
        d, w, h = self.d, self.w, self.h

        img = np.array(d.screenshot(format="pillow"))
        gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
        results = self._get_ocr().readtext(
            cv2.cvtColor(self._enhance(gray), cv2.COLOR_GRAY2BGR))

        articles = []
        for bbox, text, conf in results:
            if conf > 0.4:
                y0 = int(bbox[0][1])
                cy = int((bbox[0][1] + bbox[2][1]) / 2)
                cx = int((bbox[0][0] + bbox[2][0]) / 2)
                cleaned = self._normalize_ocr_text(text)
                if (
                    int(h * 0.16) < y0 < int(h * 0.82)
                    and self._is_plausible_article_title(cleaned)
                ):
                    articles.append((cx, cy, cleaned))

        if not articles:
            self._scroll_list()
            return False

        cx, cy, title = random.choice(articles[:min(10, len(articles))])
        logger.debug(f"[{self.account_id}] 阅读: '{title[:20]}'")
        d.click(cx, cy)
        time.sleep(2.5)
        if self._is_unexpected_overlay():
            d.press("back")
            time.sleep(1.0)
            return False

        article_context = self._capture_article_context(title)
        need_full_read = float(comment_rate) > 0 or post_after_read
        self._simulate_article_reading(
            article_context=article_context,
            scroll_to_bottom=need_full_read,
        )

        # 9% 概率点赞
        if random.random() < 0.09:
            self._like_article()

        # 补上“公众号文章评论”
        # - 失败不影响返回列表/继续浏览
        # - comment_rate 控制触发频率，避免过度互动
        try:
            if random.random() < float(comment_rate):
                if not self._is_article_bottom_visible():
                    self._scroll_to_article_bottom(max_scrolls=8)
                commented = self._comment_article(title=title, article_context=article_context)
                if not commented:
                    # 评论入口在不同机型/页面样式上偶发失效，补一次轻量重试
                    time.sleep(0.8)
                    commented = self._comment_article(title=title, article_context=article_context)
                if commented:
                    logger.info(f"[{self.account_id}] 公众号评论发送成功")
                else:
                    logger.warning(f"[{self.account_id}] 公众号评论发送失败")
        except Exception as e:
            logger.exception(f"[{self.account_id}] 公众号评论链路异常: {e}")

        if post_after_read and random.random() < float(post_rate):
            try:
                posted = self._post_from_article(title=title, article_context=article_context)
                if posted:
                    logger.info(f"[{self.account_id}] 文章读后发圈成功")
                else:
                    logger.warning(f"[{self.account_id}] 文章读后发圈失败")
            except Exception as e:
                logger.exception(f"[{self.account_id}] 文章读后发圈链路异常: {e}")

        d.press("back")
        time.sleep(1.5)
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
            text = (text or "").strip().strip('"\'「」')
            # 防止出现前导符号（例如 "%%"）导致输入到评论框里
            text = text.lstrip("%").strip()
            # 避免把换行直接输入到评论框，导致只出现回车不出正文
            text = re.sub(r"[\r\n\t]+", " ", text)
            text = re.sub(r"\s+", " ", text).strip()
            if text and text.strip():
                return text[:40]
        except Exception:
            pass
        return random.choice(fallback)[:40]

    def _comment_article(self, title: str, article_context: str = "") -> bool:
        """
        在当前文章页尝试点击「收藏/评论」并发送一条评论。
        不保证所有机型/版本都能 100% 命中，失败则返回 False。
        """
        d, w, h = self.d, self.w, self.h

        text = self._gen_article_comment(title=title, article_context=article_context)
        if not text:
            return False
        text = re.sub(r"[\r\n\t]+", " ", str(text))
        text = re.sub(r"\s+", " ", text).strip()
        if not text:
            return False
        logger.info(f"[{self.account_id}] 公众号评论尝试: {text[:18]}")

        # 0) 评论前确保已滚到文末留言区（阅读阶段通常已完成，此处作兜底）
        if not self._is_article_bottom_visible():
            self._scroll_to_article_bottom(max_scrolls=6)

        # 1) 优先点击文章结尾 inline「写评论/写留言」入口（比“收藏/评论”面板更不容易跑偏）
        inline_clicked = False
        try:
            from core.wechat_nav import ocr_find_and_click

            inline_clicked = ocr_find_and_click(
                d,
                self._get_ocr(),
                ["写评论", "写留言"],
                y_min_ratio=0.50,
                y_max_ratio=0.88,
                conf_min=0.25,
                enhance=self._enhance,
                exact=False,
            )
            if inline_clicked:
                time.sleep(0.9)
        except Exception:
            pass

        # 2) 点击底部「收藏/评论/留言」（inline 失败时再走这个老入口）
        clicked_panel = inline_clicked
        if not inline_clicked:
            try:
                from core.wechat_nav import ocr_find_and_click

                clicked_panel = ocr_find_and_click(
                    d,
                    self._get_ocr(),
                    ["收藏/评论", "收藏", "评论", "写评论", "发表评论", "说点什么", "写留言", "留言"],
                    y_min_ratio=0.55,
                    y_max_ratio=0.98,
                    conf_min=0.25,
                    enhance=self._enhance,
                    exact=False,
                )
                time.sleep(0.9)
            except Exception:
                pass

            if not clicked_panel:
                logger.warning(
                    f"[{self.account_id}] 公众号评论面板未找到(收藏/评论/留言按钮OCR未命中)"
                )
                # OCR 偶发没命中：尝试一次底部区域坐标兜底，打开输入框后由后续“输入提示OCR/EditText写入”继续处理
                try:
                    d.click(int(w * 0.52), int(h * 0.85))
                    time.sleep(0.9)
                except Exception:
                    pass

        # 2) 写入输入框
        text_written = False
        try:
            edits = d(className="android.widget.EditText")
            if edits.exists and edits.count >= 1:
                # 选取“最底部”的输入框，减少把输入写到搜索/加好友等其它 EditText 上
                best_edit = None
                best_bottom = -1
                best_right = None
                best_center_y = None
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
                                bottom = nums[3]
                                right = nums[2]
                                center_y = int((nums[1] + nums[3]) / 2)
                        if bottom is None:
                            center = e.center()
                            bottom = int(center[1])
                            center_y = int(center[1])
                        if bottom > best_bottom:
                            best_bottom = bottom
                            best_edit = e
                            best_right = right
                            best_center_y = center_y
                    except Exception:
                        continue

                if best_edit is None:
                    best_edit = edits[0]

                try:
                    best_edit.click()
                    # 确保焦点在输入框，避免 set_text 但未唤起输入法
                    try:
                        d.set_input_ime(True)
                    except Exception:
                        pass
                    time.sleep(0.25)
                except Exception:
                    pass
                best_edit.set_text(text)
                time.sleep(0.35)
                try:
                    cur = str(getattr(best_edit, "info", {}).get("text", "") or "").strip()
                except Exception:
                    cur = ""
                if not cur:
                    # set_text 偶发未生效，再补一次 send_keys
                    try:
                        d.set_input_ime(True)
                    except Exception:
                        pass
                    time.sleep(0.2)
                    d.send_keys(text)
                    time.sleep(0.35)
                    try:
                        d.set_input_ime(False)
                    except Exception:
                        pass
                    try:
                        cur = str(getattr(best_edit, "info", {}).get("text", "") or "").strip()
                    except Exception:
                        cur = ""
                text_written = bool(cur)
            else:
                from core.wechat_nav import ocr_find_and_click

                clicked_hint = ocr_find_and_click(
                    d,
                    self._get_ocr(),
                    ["说点什么", "写评论", "发表评论", "写留言", "留言"],
                    y_min_ratio=0.35,
                    y_max_ratio=0.92,
                    conf_min=0.25,
                    enhance=self._enhance,
                    exact=False,
                )
                if not clicked_hint:
                    # 机型/版式兜底：文章底部留言输入框多在左下，先尝试左侧输入区再尝试中间
                    for rx, ry in ((0.30, 0.84), (0.42, 0.84), (0.30, 0.88), (0.50, 0.85)):
                        try:
                            d.click(int(w * rx), int(h * ry))
                            time.sleep(0.45)
                        except Exception:
                            pass
                        edits2 = d(className="android.widget.EditText")
                        if edits2.exists and edits2.count >= 1:
                            clicked_hint = True
                            break
                if not clicked_hint:
                    logger.warning(f"[{self.account_id}] 公众号评论输入框入口未找到(说点什么/写评论/留言)")
                    return False
                d.set_input_ime(True)
                time.sleep(0.25)
                d.send_keys(text)
                time.sleep(0.35)
                d.set_input_ime(False)
                text_written = True
        except Exception:
            return False

        if not text_written:
            logger.warning(f"[{self.account_id}] 评论文本未写入成功，放弃发送")
            return False

        # 3) 点击「发送/发表」
        sent = False
        send_triggered = False
        try:
            from core.wechat_nav import ocr_find_and_click

            ok = ocr_find_and_click(
                d,
                self._get_ocr(),
                ["发送", "发表"],
                y_min_ratio=0.60,
                y_max_ratio=0.98,
                conf_min=0.25,
                enhance=self._enhance,
                exact=False,
            )
            if ok:
                time.sleep(0.9)
                sent = True
                send_triggered = True

            # fallback：u2 点字
            if not sent:
                try:
                    node = d(text="发送")
                    if node.exists(timeout=0.8):
                        node.click()
                        time.sleep(0.9)
                        sent = True
                        send_triggered = True
                except Exception:
                    pass

            # fallback：部分版本按钮文案为“提交”
            if not sent:
                try:
                    node = d(text="提交")
                    if node.exists(timeout=0.8):
                        node.click()
                        time.sleep(0.9)
                        sent = True
                        send_triggered = True
                except Exception:
                    pass

            # 兜底：发送按钮文字 OCR 不稳定时，尝试输入框右侧发送区域点击
            # 注意：不再按 Enter，避免插入换行导致“只回车无正文”。
            if not sent:
                try:
                    # 受控坐标：只在存在输入框右侧附近时点击发送区域
                    send_x = int(w * 0.90)
                    send_y = int(h * 0.88)
                    try:
                        if 'best_right' in locals() and best_right is not None and 'best_center_y' in locals() and best_center_y is not None:
                            send_x = min(w - 1, best_right + 24)
                            send_y = int(best_center_y)
                    except Exception:
                        pass
                    d.click(send_x, send_y)
                    time.sleep(0.8)
                    # 坐标点击只代表“触发了发送动作尝试”，不代表一定发出
                    send_triggered = True
                    time.sleep(0.9)
                except Exception:
                    pass
        except Exception:
            pass

        # 发送后补充校验：若输入框仍保留原文，通常说明并未真正发送成功
        draft_remains = self._is_comment_draft_still_present(text)

        # 4) 收尾：若仍停留在“发表评论/说点什么”输入态，先后退一次
        #    让 _read_article 的 back 能回到列表页。
        still_input = False
        try:
            img = np.array(d.screenshot(format="pillow"))
            gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
            results = self._get_ocr().readtext(
                cv2.cvtColor(self._enhance(gray), cv2.COLOR_GRAY2BGR)
            )
            blob = " ".join(str(t) for _, t, c in results if c > 0.35)
            if any(k in blob for k in ("发表评论", "说点什么", "写评论", "输入评论")):
                still_input = True
                # 不再按 back 关闭输入框（避免误操作/提前退出页面）
                # 先尝试再提交一次，仍失败则交由调用方 back 返回列表。
                try:
                    from core.wechat_nav import ocr_find_and_click
                    ok2 = ocr_find_and_click(
                        d,
                        self._get_ocr(),
                        ["发送", "发表", "提交"],
                        y_min_ratio=0.60,
                        y_max_ratio=0.98,
                        conf_min=0.25,
                        enhance=self._enhance,
                        exact=False,
                    )
                    if ok2:
                        time.sleep(0.7)
                    else:
                        # 发送失败时，先退出输入态，避免卡在“留言+图片”页
                        d.press("back")
                        time.sleep(0.6)
                except Exception:
                    pass
        except Exception:
            pass

        # 判定：
        # - 触发过发送动作（OCR按钮/u2按钮/坐标发送区）
        # - 不在输入态
        # - 输入框里不再残留原评论草稿
        ok = bool(send_triggered and (not still_input) and (not draft_remains))
        logger.debug(
            f"[{self.account_id}] 公众号评论结果: {ok} "
            f"(send_triggered={send_triggered}, sent={sent}, still_input={still_input}, draft_remains={draft_remains})"
        )
        return ok

    def _is_comment_draft_still_present(self, draft_text: str) -> bool:
        """判断发送后输入框是否仍残留评论草稿，避免“误判已发送”"""
        text = (draft_text or "").strip()
        if not text:
            return False
        probe = text[: min(8, len(text))]
        try:
            edits = self.d(className="android.widget.EditText")
            if not edits.exists or edits.count < 1:
                return False
            for e in edits:
                try:
                    val = str(getattr(e, "info", {}).get("text", "") or "").strip()
                except Exception:
                    val = ""
                if not val:
                    continue
                if val == text or probe in val:
                    return True
        except Exception:
            return False
        return False

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
        if self._is_search_page():
            return True
        for _ in range(2):
            if open_search_fn(self.d):
                time.sleep(0.8)
            self._dismiss_unexpected_overlay()
            if self._is_search_page():
                return True
            try:
                self.d.press("back")
                time.sleep(0.5)
            except Exception:
                pass
        return self._is_search_page()

    def _focus_search_box(self):
        """只在已进入搜索页后聚焦搜索框，避免顶栏误点到其它入口。"""
        if not self._is_search_page():
            raise RuntimeError("当前不在搜索页，拒绝点击顶部搜索框")
        self.d.click(int(self.w * 0.5), int(self.h * 0.065))
        time.sleep(0.8)

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
        blob = self._ocr_screen_blob(0.0, 0.26)
        search_markers = ("搜索", "搜索指定内容", "聊天记录", "朋友圈", "文章", "公众号", "小程序")
        return any(k in blob for k in search_markers)

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
            time.sleep(0.8)

    def _normalize_ocr_text(self, text: str) -> str:
        text = (text or "").strip()
        text = re.sub(r"\s+", "", text)
        return text

    def _is_plausible_article_title(self, text: str) -> bool:
        if not text or len(text) < 4 or len(text) > 36:
            return False
        banned = (
            "公众号", "文章", "搜索", "聊天记录", "朋友圈", "小程序",
            "发起群聊", "添加朋友", "扫一扫", "收付款", "应用推荐",
            "热搜榜", "推荐", "热搜", "生活", "娱乐", "话题", "搜索发现",
        )
        if any(k == text or k in text for k in banned):
            return False
        if text.isdigit():
            return False
        if sum(ch.isdigit() for ch in text) >= max(3, len(text) // 2):
            return False
        return True

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
    ):
        """分段慢速阅读；需要评论/发圈时滚动至文末再结束。"""
        w, h = self.w, self.h
        time.sleep(random.uniform(2.8, 5.5))

        if scroll_to_bottom:
            reached = self._scroll_to_article_bottom(
                max_scrolls=20,
                min_scrolls=3,
                pause_range=(2.0, 5.0),
            )
            logger.debug(
                f"[{self.account_id}] 文章阅读至底部: {reached}"
            )
            return

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
        """OCR 判断当前是否已滚到文章底部（留言区/底部操作栏）。"""
        blob = self._ocr_region_blob(0.52, 1.0)
        if any(k in blob for k in _ARTICLE_BOTTOM_MARKERS):
            return True
        bar_hits = sum(1 for k in _ARTICLE_BOTTOM_BAR_MARKERS if k in blob)
        return bar_hits >= 2

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
            import easyocr
            self._ocr = easyocr.Reader(['ch_sim', 'en'], gpu=False)
        return self._ocr
