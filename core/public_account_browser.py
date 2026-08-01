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
import cv2
import numpy as np

from utils.logger import get_logger

logger = get_logger("public_account_browser")


class PublicAccountBrowser:
    """公众号浏览器 — 搜索进入 → 浏览文章 → 阅读。"""

    def __init__(self, d, account_id: str = ""):
        self.d = d
        self.account_id = account_id
        self.w, self.h = d.info['displayWidth'], d.info['displayHeight']
        self._ocr = None
        self._clahe = None

    # ================================================================
    # 公共接口
    # ================================================================

    def browse(self, duration_seconds: int = 180) -> int:
        """
        浏览公众号文章，持续指定时长。

        Args:
            duration_seconds: 浏览总时长（秒）

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
                    if self._read_article():
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

        if not open_search(d):
            d.click(int(w * 0.831), int(h * 0.054))
            time.sleep(2)

        d.click(int(w * 0.5), int(h * 0.045))
        time.sleep(0.8)

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

        clicked = ocr_find_and_click(
            d,
            self._get_ocr(),
            ["公众号"],
            y_min_ratio=0.12,
            y_max_ratio=0.85,
            conf_min=0.4,
            enhance=self._enhance,
            exact=False,
        )
        if not clicked:
            d.click(int(w * 0.40), int(h * 0.22))
        time.sleep(3)

    # ================================================================
    # 阅读文章
    # ================================================================

    def _read_article(self) -> bool:
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
                if int(h * 0.15) < y0 < int(h * 0.85) and len(text.strip()) > 2:
                    articles.append((cx, cy, text))

        if not articles:
            self._scroll_list()
            return False

        cx, cy, title = random.choice(articles[:min(10, len(articles))])
        logger.debug(f"[{self.account_id}] 阅读: '{title[:20]}'")
        d.click(cx, cy)
        time.sleep(2.5)

        # 模拟阅读滚动
        for _ in range(random.randint(2, 5)):
            d.swipe(w // 2, int(h * 0.75), w // 2, int(h * 0.35),
                    duration=0.4)
            time.sleep(random.uniform(1.5, 4.0))

        # 9% 概率点赞
        if random.random() < 0.09:
            self._like_article()

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

    def _scroll_list(self):
        """随机上下滚动文章列表。"""
        if random.random() < 0.5:
            self.d.swipe(self.w // 2, int(self.h * 0.6),
                          self.w // 2, int(self.h * 0.3), duration=0.3)
        else:
            self.d.swipe(self.w // 2, int(self.h * 0.3),
                          self.w // 2, int(self.h * 0.6), duration=0.3)
        time.sleep(random.uniform(0.5, 2.0))

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
