"""
视频号浏览 & 点赞模块 — OCR + OpenCV 混合方案
================================================

## 概述

进入微信"发现"→"视频号"，上滑刷视频，按概率点赞。
点赞按钮通过 OCR 识别底部计数来定位——图标在计数文字左侧约 50px。

## 工作流

::

    冷启动 → 发现Tab → OCR/相对坐标点「视频号」
      │
      ├─ 上滑切换视频 (随机停留 2~180s)
      │
      ├─ 按概率决定是否点赞 (默认 20%)
      │     └─ OCR 扫描底部栏 4 个计数 → 取最左边(点赞)
      │     └─ 点赞图标 = 计数左侧 50px，同高度
      │
      ├─ 点赞后验证: 是否还在视频页?
      │     └─ is_on_video_page(): 底部计数 >= 2 → 仍在视频页
      │     └─ 计数消失 → 误入评论区 → press("back") 退回
      │
      └─ 底部 4 按钮 (从左到右): [点赞] [转发] [推荐] [聊天]

## 快速开始

.. code-block:: python

    from core.channels_browser import ChannelsBrowser
    browser = ChannelsBrowser(device)
    browser.browse(scroll_count=10, like_rate=0.2)

## CLI 测试

.. code-block:: bash

    python test_channels.py --scroll 10 --like-rate 0.3

## 依赖

- EasyOCR: 底部计数识别 + 视频页检测
- OpenCV CLAHE: 低对比度文字增强

## 适配

基于相对坐标 + OCR，适配不同分辨率。
点赞图标相对计数的偏移用屏幕宽度比例。
"""

import time
import random
import cv2
import numpy as np

from core.wechat_nav import (
    channels_entry_for,
    click_ratio,
    goto_tab,
    ocr_find_and_click,
    start_wechat,
)
from utils.logger import get_logger

logger = get_logger("channels_browser")


class ChannelsBrowser:
    """视频号浏览器 — 刷视频 + 概率点赞。"""

    # 点赞图标在计数文字左侧的偏移（相对屏宽）
    LIKE_ICON_X_OFFSET_RATIO = -0.04

    # 默认点赞概率
    DEFAULT_LIKE_RATE = 0.2

    # 观看停留时间范围 (秒) — 上限下调，避免单次卡死过久
    DWELL_MIN = 2.0
    DWELL_MAX = 25.0

    def __init__(self, d, account_id: str = ""):
        self.d = d
        self.account_id = account_id
        self.w, self.h = d.info['displayWidth'], d.info['displayHeight']
        self._ocr = None
        self._clahe = None
        # 按当前机型取视频号入口，不写死某一分辨率
        self.CHANNELS_ENTRY = channels_entry_for(d)

    # ================================================================
    # 公共接口
    # ================================================================

    def browse(self, scroll_count: int = 5,
               like_rate: float = DEFAULT_LIKE_RATE) -> int:
        """
        刷视频号，按概率点赞。

        Args:
            scroll_count: 刷几条视频
            like_rate:    点赞概率 (0.0 ~ 1.0)

        Returns:
            实际点赞次数
        """
        logger.info(f"[{self.account_id}] 视频号: 刷{scroll_count}条, "
                     f"like_rate={like_rate:.0%}")
        liked = 0

        try:
            self._enter_channels()

            for i in range(scroll_count):
                if random.random() < like_rate:
                    if self._like_current():
                        liked += 1

                if i < scroll_count - 1:
                    self._swipe_next()

            logger.info(f"[{self.account_id}] 视频号完成: 点赞{liked}/{scroll_count}")
        except Exception as e:
            logger.error(f"[{self.account_id}] 视频号异常: {e}")

        return liked

    # ================================================================
    # 导航
    # ================================================================

    def _enter_channels(self):
        """冷启动 → 发现 → 视频号（OCR 优先，相对坐标 fallback）。"""
        d = self.d
        start_wechat(d, wait=4.0, cold=True)
        goto_tab(d, "discover")
        time.sleep(1.0)

        # OCR 找「视频号」
        clicked = ocr_find_and_click(
            d,
            self._get_ocr(),
            ["视频号"],
            y_min_ratio=0.08,
            y_max_ratio=0.55,
            conf_min=0.3,
            enhance=self._enhance,
            click_row_center=True,
        )
        if not clicked:
            logger.warning(f"[{self.account_id}] OCR未找到视频号，使用相对坐标")
            click_ratio(d, *self.CHANNELS_ENTRY)
        time.sleep(3)

    def _swipe_next(self):
        """上滑切换视频，随机观看停留。"""
        self.d.swipe(self.w // 2, int(self.h * 0.8),
                      self.w // 2, int(self.h * 0.2), duration=0.3)
        stay = random.uniform(self.DWELL_MIN, self.DWELL_MAX)
        logger.debug(f"[{self.account_id}] 停留 {stay:.0f}s")
        time.sleep(stay)

    # ================================================================
    # 点赞
    # ================================================================

    def _like_current(self) -> bool:
        """
        OCR 找底部计数 → 点击左侧图标 → 验证。
        底部 4 按钮: [点赞] [转发] [推荐] [聊天]，点赞是最左边。

        Returns:
            是否成功点赞
        """
        d, w, h = self.d, self.w, self.h

        # OCR 底部计数
        img = np.array(d.screenshot(format="pillow"))
        gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
        bottom = self._enhance(gray[int(h * 0.88):int(h * 0.97),
                                     int(w * 0.55):w])

        reader = self._get_ocr()
        results = reader.readtext(cv2.cvtColor(bottom, cv2.COLOR_GRAY2BGR))

        # 收集所有数字计数，按 x 排序
        counts = []
        for bbox, text, conf in results:
            if conf > 0.3 and any(c.isdigit() for c in text):
                cx = int((bbox[0][0] + bbox[2][0]) / 2) + int(w * 0.55)
                cy = int((bbox[0][1] + bbox[2][1]) / 2) + int(h * 0.88)
                counts.append((cx, cy, text))

        if not counts:
            logger.debug(f"[{self.account_id}] OCR未找到计数")
            return False

        counts.sort(key=lambda c: c[0])  # 按 x 排序
        like_x = counts[0][0] + int(w * self.LIKE_ICON_X_OFFSET_RATIO)
        like_y = counts[0][1]

        logger.debug(f"[{self.account_id}] 点赞: ({like_x},{like_y})")
        d.click(like_x, like_y)
        time.sleep(0.8)

        # 验证: 还在视频页 = 成功; 误入评论区 = 退回
        if self._is_on_video_page():
            return True
        else:
            logger.debug(f"[{self.account_id}] 误入其他页面，退回")
            self._go_back_to_video()
            return False

    # ================================================================
    # 页面检测 + 恢复
    # ================================================================

    def _is_on_video_page(self) -> bool:
        """检测是否在视频播放页（至少2个计数 = 底部栏完整）。"""
        img = np.array(self.d.screenshot(format="pillow"))
        gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
        bottom = self._enhance(gray[int(self.h * 0.88):int(self.h * 0.97),
                                     int(self.w * 0.55):self.w])
        results = self._get_ocr().readtext(cv2.cvtColor(bottom, cv2.COLOR_GRAY2BGR))
        num_count = sum(1 for _, t, c in results
                         if c > 0.3 and any(d.isdigit() for d in t))
        return num_count >= 2

    def _go_back_to_video(self):
        """从评论区等页面退回视频播放页。"""
        for _ in range(3):
            if self._is_on_video_page():
                return
            self.d.press("back")
            time.sleep(0.5)

    # ================================================================
    # 工具
    # ================================================================

    def _enhance(self, gray):
        if self._clahe is None:
            self._clahe = cv2.createCLAHE(clipLimit=4.0, tileGridSize=(6, 6))
        return self._clahe.apply(gray)

    def _get_ocr(self):
        if self._ocr is None:
            import easyocr
            self._ocr = easyocr.Reader(['ch_sim', 'en'], gpu=False)
        return self._ocr
