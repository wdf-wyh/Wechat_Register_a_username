"""
视频号浏览 & 互动模块 — OCR + OpenCV 混合方案
================================================

## 概述

进入微信"发现"→"视频号"，按目标时长刷视频；默认**完播**停留，
并按概率点赞 / 评论。点赞按钮通过 OCR 识别底部计数定位。

## 工作流

::

    冷启动 → 发现Tab → OCR/相对坐标点「视频号」
      │
      ├─ 完播停留 (约 20~75s，或 OCR 检测到「重播」)
      │
      ├─ 按概率点赞 / 评论
      │
      ├─ 上滑切换下一条，直到达到 duration_seconds（默认 600=10分钟）
      │
      └─ 底部栏常见: [点赞] [评论] [推荐] [转发]

## 快速开始

.. code-block:: python

    from core.channels_browser import ChannelsBrowser
    browser = ChannelsBrowser(device)
    # 每日默认: 看满约 10 分钟，尽量完播，并随机评论
    browser.browse(duration_seconds=600, finish_watch=True, comment_rate=0.18)

## 依赖

- EasyOCR: 底部计数 / 「重播」/ 评论入口
- OpenCV CLAHE: 低对比度文字增强
"""

from __future__ import annotations

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

# 每日默认观看时长（秒）
DEFAULT_DAILY_DURATION = 600

# 短评兜底文案
_DEFAULT_COMMENTS = (
    "不错", "学到了", "哈哈哈", "支持", "有意思", "真的假的", "太真实了",
)


class ChannelsBrowser:
    """视频号浏览器 — 按时长完播观看 + 概率点赞/评论。"""

    LIKE_ICON_X_OFFSET_RATIO = -0.04
    COMMENT_ICON_X_OFFSET_RATIO = -0.04

    DEFAULT_LIKE_RATE = 0.2
    DEFAULT_COMMENT_RATE = 0.18

    # 快刷停留
    DWELL_MIN = 2.0
    DWELL_MAX = 25.0

    # 完播停留（短视频常见时长区间）
    FINISH_DWELL_MIN = 18.0
    FINISH_DWELL_MAX = 75.0

    def __init__(self, d, account_id: str = ""):
        self.d = d
        self.account_id = account_id
        self.w, self.h = d.info['displayWidth'], d.info['displayHeight']
        self._ocr = None
        self._clahe = None
        self.CHANNELS_ENTRY = channels_entry_for(d)

    # ================================================================
    # 公共接口
    # ================================================================

    def browse(
        self,
        scroll_count: int | None = None,
        like_rate: float = DEFAULT_LIKE_RATE,
        duration_seconds: int | None = None,
        finish_watch: bool = True,
        comment_rate: float = 0.0,
        comment_texts: list[str] | None = None,
    ) -> dict:
        """
        刷视频号。

        优先按时长 ``duration_seconds`` 控制；未指定时长时用 ``scroll_count``。
        两者都未指定时默认看满 ``DEFAULT_DAILY_DURATION``（10 分钟）。

        Args:
            scroll_count:      刷几条（与 duration 二选一；duration 优先）
            like_rate:         点赞概率
            duration_seconds:  总观看秒数；默认 600
            finish_watch:      True=尽量完播再滑下一条
            comment_rate:      评论概率（0 关闭）
            comment_texts:     评论文案池；空则用内置短评

        Returns:
            {"liked", "commented", "watched", "switched", "elapsed"}
        """
        if duration_seconds is None and scroll_count is None:
            duration_seconds = DEFAULT_DAILY_DURATION

        use_duration = duration_seconds is not None and int(duration_seconds) > 0
        target = int(duration_seconds) if use_duration else 0
        max_videos = int(scroll_count) if scroll_count is not None else 10_000
        texts = [t for t in (comment_texts or list(_DEFAULT_COMMENTS)) if t]

        logger.info(
            f"[{self.account_id}] 视频号: "
            f"{'时长'+str(target)+'s' if use_duration else '条数'+str(max_videos)}, "
            f"完播={finish_watch}, like={like_rate:.0%}, comment={comment_rate:.0%}"
        )

        result = {
            "liked": 0,
            "commented": 0,
            "watched": 0,
            "switched": 0,
            "elapsed": 0.0,
        }
        start = time.time()

        try:
            self._enter_channels()
            time.sleep(1.0)
            prev = self._frame_signature()

            for i in range(max_videos):
                if use_duration and (time.time() - start) >= target:
                    break

                # 先完播/停留，再互动（更像真人）
                stayed = self._dwell_current(finish_watch=finish_watch)
                result["watched"] += 1
                logger.debug(
                    f"[{self.account_id}] 第{result['watched']}条停留 {stayed:.0f}s"
                )

                if random.random() < like_rate:
                    if self._like_current():
                        result["liked"] += 1

                if comment_rate > 0 and texts and random.random() < comment_rate:
                    text = random.choice(texts)
                    if self._comment_current(text):
                        result["commented"] += 1

                if use_duration and (time.time() - start) >= target:
                    break
                if not use_duration and i >= max_videos - 1:
                    break

                if self._swipe_next(prev_sig=prev, dwell_after=False):
                    result["switched"] += 1
                    prev = self._frame_signature()
                else:
                    logger.warning(f"[{self.account_id}] 切视频未生效，重试强甩")
                    self._fling_up(
                        int(self.w * 0.5),
                        int(self.h * 0.70),
                        int(self.h * 0.18),
                        150,
                    )
                    time.sleep(1.0)
                    now = self._frame_signature()
                    if now != prev:
                        result["switched"] += 1
                        prev = now

            result["elapsed"] = round(time.time() - start, 1)
            logger.info(
                f"[{self.account_id}] 视频号完成: "
                f"看{result['watched']}条/{result['elapsed']}s, "
                f"赞{result['liked']}, 评{result['commented']}, "
                f"切换{result['switched']}"
            )
        except Exception as e:
            result["elapsed"] = round(time.time() - start, 1)
            logger.error(f"[{self.account_id}] 视频号异常: {e}")

        return result

    # ================================================================
    # 导航
    # ================================================================

    def _enter_channels(self):
        """冷启动 → 发现 → 视频号（OCR 优先，相对坐标 fallback）。"""
        d = self.d
        start_wechat(d, wait=4.0, cold=True)
        goto_tab(d, "discover")
        time.sleep(1.0)

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

    def _dwell_current(self, finish_watch: bool = True) -> float:
        """在当前视频停留；完播模式尽量看到结束（或「重播」）。"""
        if finish_watch:
            budget = random.uniform(self.FINISH_DWELL_MIN, self.FINISH_DWELL_MAX)
            return self._wait_until_finished(budget)
        stay = random.uniform(self.DWELL_MIN, self.DWELL_MAX)
        time.sleep(stay)
        return stay

    def _wait_until_finished(self, max_seconds: float) -> float:
        """分段等待；OCR 到「重播」视为已完播可提前结束。"""
        start = time.time()
        while True:
            elapsed = time.time() - start
            remaining = max_seconds - elapsed
            if remaining <= 0:
                break
            # 前半段少检测，后半段更勤快找「重播」
            chunk = min(4.0 if elapsed > max_seconds * 0.45 else 6.0, remaining)
            time.sleep(chunk)
            if elapsed + chunk >= max_seconds * 0.4 and self._has_replay_hint():
                logger.debug(f"[{self.account_id}] 检测到重播提示，视为完播")
                break
        return time.time() - start

    def _has_replay_hint(self) -> bool:
        """屏幕中部/偏下是否出现「重播」等完播提示。"""
        try:
            img = np.array(self.d.screenshot(format="pillow"))
            gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
            h, w = gray.shape[:2]
            region = self._enhance(gray[int(h * 0.35):int(h * 0.75), int(w * 0.25):int(w * 0.75)])
            results = self._get_ocr().readtext(cv2.cvtColor(region, cv2.COLOR_GRAY2BGR))
            for _, text, conf in results:
                if conf < 0.35:
                    continue
                t = text.strip()
                if any(k in t for k in ("重播", "再看一遍", "重新播放")):
                    return True
        except Exception:
            return False
        return False

    def _swipe_next(
        self,
        prev_sig: tuple | None = None,
        dwell_after: bool = True,
    ) -> bool:
        """
        上滑切换下一条视频。

        视频号是全屏竖滑 feed，必须用「快速甩动」(fling)：
        - 过慢 / Bézier 分段滑 → 橡皮筋抖动、切不了片
        - 起点过低 → 撞全面屏手势条
        """
        w, h = self.w, self.h
        x = int(w * random.uniform(0.42, 0.58))
        y1 = int(h * random.uniform(0.62, 0.72))
        y2 = int(h * random.uniform(0.18, 0.28))
        duration_ms = random.randint(100, 180)

        before = prev_sig or self._frame_signature()
        ok = self._fling_up(x, y1, y2, duration_ms)
        if not ok:
            try:
                self.d.swipe(x, y1, x, y2, duration=0.05)
            except Exception as e:
                logger.warning(f"[{self.account_id}] 视频号滑动失败: {e}")

        time.sleep(random.uniform(0.7, 1.2))
        after = self._frame_signature()
        switched = after != before
        if dwell_after:
            stay = random.uniform(self.DWELL_MIN, self.DWELL_MAX)
            logger.debug(
                f"[{self.account_id}] 切视频 fling {y1}->{y2} {duration_ms}ms "
                f"switched={switched}, 停留 {stay:.0f}s"
            )
            time.sleep(stay)
        else:
            logger.debug(
                f"[{self.account_id}] 切视频 fling {y1}->{y2} {duration_ms}ms "
                f"switched={switched}"
            )
        return switched

    def _fling_up(self, x: int, y1: int, y2: int, duration_ms: int) -> bool:
        """通过 adb shell input swipe 做快速上滑。"""
        try:
            self.d.shell(f"input swipe {x} {y1} {x} {y2} {duration_ms}")
            return True
        except Exception as e:
            logger.debug(f"[{self.account_id}] adb fling 失败: {e}")
            return False

    def _frame_signature(self) -> tuple:
        """缩略帧签名，用于判断是否切到下一条。"""
        try:
            img = np.array(self.d.screenshot(format="pillow"))
            h, w = img.shape[:2]
            crop = img[int(h * 0.25):int(h * 0.75), int(w * 0.15):int(w * 0.85)]
            small = cv2.resize(crop, (32, 32))
            return tuple(small.mean(axis=2).astype(np.uint8).flatten().tolist())
        except Exception:
            return (random.random(),)

    # ================================================================
    # 点赞 / 评论
    # ================================================================

    def _bottom_counts(self) -> list[tuple[int, int, str]]:
        """OCR 底部计数，按 x 从左到右排序。"""
        d, w, h = self.d, self.w, self.h
        img = np.array(d.screenshot(format="pillow"))
        gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
        bottom = self._enhance(gray[int(h * 0.88):int(h * 0.97), int(w * 0.55):w])
        results = self._get_ocr().readtext(cv2.cvtColor(bottom, cv2.COLOR_GRAY2BGR))

        counts = []
        for bbox, text, conf in results:
            if conf > 0.3 and any(c.isdigit() for c in text):
                cx = int((bbox[0][0] + bbox[2][0]) / 2) + int(w * 0.55)
                cy = int((bbox[0][1] + bbox[2][1]) / 2) + int(h * 0.88)
                counts.append((cx, cy, text))
        counts.sort(key=lambda c: c[0])
        return counts

    def _like_current(self) -> bool:
        """OCR 找底部计数 → 点击最左侧图标 → 验证仍在视频页。"""
        counts = self._bottom_counts()
        if not counts:
            logger.debug(f"[{self.account_id}] OCR未找到计数")
            return False

        like_x = counts[0][0] + int(self.w * self.LIKE_ICON_X_OFFSET_RATIO)
        like_y = counts[0][1]
        logger.debug(f"[{self.account_id}] 点赞: ({like_x},{like_y})")
        self.d.click(like_x, like_y)
        time.sleep(0.8)

        if self._is_on_video_page():
            return True
        logger.debug(f"[{self.account_id}] 误入其他页面，退回")
        self._go_back_to_video()
        return False

    def _comment_current(self, text: str) -> bool:
        """对当前视频发评论（不重新进视频号）。"""
        if not text:
            return False
        text = text[:40]
        logger.debug(f"[{self.account_id}] 评论: {text}")

        opened = ocr_find_and_click(
            self.d,
            self._get_ocr(),
            ["评论", "说点什么", "写评论"],
            y_min_ratio=0.72,
            y_max_ratio=0.98,
            conf_min=0.3,
            enhance=self._enhance,
        )
        if not opened:
            counts = self._bottom_counts()
            if len(counts) >= 2:
                cx = counts[1][0] + int(self.w * self.COMMENT_ICON_X_OFFSET_RATIO)
                cy = counts[1][1]
                self.d.click(cx, cy)
            else:
                click_ratio(self.d, 0.62, 0.92)
            time.sleep(1.0)

        try:
            self.d.set_input_ime(True)
            time.sleep(0.2)
            self.d.send_keys(text)
            self.d.set_input_ime(False)
        except Exception as e:
            logger.debug(f"[{self.account_id}] 评论输入失败: {e}")
            self._go_back_to_video()
            return False

        time.sleep(0.4)
        sent = ocr_find_and_click(
            self.d,
            self._get_ocr(),
            ["发送"],
            y_min_ratio=0.85,
            y_max_ratio=0.99,
            conf_min=0.3,
            enhance=self._enhance,
        )
        if not sent:
            click_ratio(self.d, 0.90, 0.94)
        time.sleep(0.8)
        self._go_back_to_video()
        return True

    # ================================================================
    # 页面检测 + 恢复
    # ================================================================

    def _is_on_video_page(self) -> bool:
        """检测是否在视频播放页（至少2个计数 = 底部栏完整）。"""
        return len(self._bottom_counts()) >= 2

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
