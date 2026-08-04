"""
视频号浏览 & 互动模块 — OCR + OpenCV 混合方案
================================================

## 概述

进入微信"发现"→"视频号"，按目标时长刷视频；默认**完播**停留，
并按概率点赞 / 评论。点赞按钮通过 OCR 识别底部计数定位。

评论默认：OCR 截取当前视频作者/标题/简介 → 交给 ``comment_fn``
（通常接 LLM）生成贴合内容的短评；失败则回退内置短评池。

## 工作流

::

    冷启动 → 发现Tab → OCR/相对坐标点「视频号」
      │
      ├─ 完播停留 (约 20~75s，或 OCR 检测到「重播」)
      │
      ├─ 按概率点赞
      │
      ├─ 按概率评论：OCR 视频文案 → comment_fn/LLM → 发送
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

- EasyOCR: 底部计数 / 「重播」/ 评论入口 / 视频文案区
- OpenCV CLAHE: 低对比度文字增强
"""

from __future__ import annotations

import re
import time
import random
from collections.abc import Callable

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

# 视频文案 OCR 时需剔除的 UI/噪声词
_CONTEXT_NOISE = (
    "关注", "已关注", "点赞", "评论", "推荐", "转发", "分享", "重播",
    "说点什么", "写评论", "发送", "直播", "合集", "展开", "收起",
    "广告", "赞助", "再看一遍", "重新播放", "收藏", "私信", "主页",
    "视频号", "发现", "微信", "搜索",
)
_COUNT_RE = re.compile(r"^[\d\.]+万?$|^[\d,]+$|^\d+\.\d+[wW万]?$")


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
        comment_fn: Callable[[str], str] | None = None,
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
            comment_texts:     评论文案池；有 ``comment_fn`` 时作兜底
            comment_fn:        ``(video_context) -> comment``；优先用于按内容评论

        Returns:
            {"liked", "commented", "watched", "switched", "elapsed"}
        """
        if duration_seconds is None and scroll_count is None:
            duration_seconds = DEFAULT_DAILY_DURATION

        use_duration = duration_seconds is not None and int(duration_seconds) > 0
        target = int(duration_seconds) if use_duration else 0
        max_videos = int(scroll_count) if scroll_count is not None else 10_000
        texts = [t for t in (comment_texts or list(_DEFAULT_COMMENTS)) if t]
        can_comment = comment_rate > 0 and (comment_fn is not None or bool(texts))

        logger.info(
            f"[{self.account_id}] 视频号: "
            f"{'时长'+str(target)+'s' if use_duration else '条数'+str(max_videos)}, "
            f"完播={finish_watch}, like={like_rate:.0%}, comment={comment_rate:.0%}, "
            f"ai_comment={'on' if comment_fn else 'off'}"
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

                if can_comment and random.random() < comment_rate:
                    text = self._compose_comment(comment_fn, texts)
                    if text and self._comment_current(text):
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

    def extract_video_context(self) -> str:
        """
        OCR 当前视频页左下角文案区，提取作者/标题/简介。

        视频号常见布局：左侧偏下为昵称+简介，右侧为互动栏；
        字幕偶发叠在画面中部，一并扫入后过滤 UI 噪声。
        """
        try:
            img = np.array(self.d.screenshot(format="pillow"))
            gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
            h, w = gray.shape[:2]
            # 左下信息区（避开右侧互动栏与底部计数条）
            regions = [
                gray[int(h * 0.55):int(h * 0.88), int(w * 0.02):int(w * 0.58)],
                # 中部偏下：可能有字幕/展开简介
                gray[int(h * 0.42):int(h * 0.62), int(w * 0.08):int(w * 0.70)],
            ]
            lines: list[str] = []
            seen: set[str] = set()
            for region in regions:
                enhanced = self._enhance(region)
                results = self._get_ocr().readtext(
                    cv2.cvtColor(enhanced, cv2.COLOR_GRAY2BGR)
                )
                # 按 y 再 x 排序，尽量保持阅读顺序
                rows = []
                for bbox, text, conf in results:
                    if conf < 0.35:
                        continue
                    t = str(text).strip()
                    if not t or not self._is_useful_context_text(t):
                        continue
                    cy = (bbox[0][1] + bbox[2][1]) / 2
                    cx = (bbox[0][0] + bbox[2][0]) / 2
                    rows.append((cy, cx, t))
                rows.sort(key=lambda r: (round(r[0] / 12), r[1]))
                for _, _, t in rows:
                    key = t.lower()
                    if key in seen:
                        continue
                    seen.add(key)
                    lines.append(t)
            context = " ".join(lines).strip()
            if len(context) > 320:
                context = context[:320].rstrip()
            if context:
                logger.debug(
                    f"[{self.account_id}] 视频文案OCR: {context[:80]}"
                    f"{'...' if len(context) > 80 else ''}"
                )
            return context
        except Exception as e:
            logger.debug(f"[{self.account_id}] 视频文案OCR失败: {e}")
            return ""

    def _compose_comment(
        self,
        comment_fn: Callable[[str], str] | None,
        fallback_texts: list[str],
    ) -> str:
        """OCR 上下文 → comment_fn；失败则回退文案池。"""
        context = self.extract_video_context()
        text = ""
        if comment_fn is not None:
            try:
                text = (comment_fn(context) or "").strip()
            except Exception as e:
                logger.debug(f"[{self.account_id}] comment_fn 失败: {e}")
                text = ""
        if not text and fallback_texts:
            text = random.choice(fallback_texts)
        return text[:40] if text else ""

    @staticmethod
    def _is_useful_context_text(text: str) -> bool:
        t = text.strip()
        if len(t) < 2:
            return False
        if _COUNT_RE.match(t):
            return False
        if t in _CONTEXT_NOISE or t in ("关注+", "+关注"):
            return False
        # 「评论」「关注」等极短 UI 变体
        for n in _CONTEXT_NOISE:
            if len(n) >= 2 and n in t and len(t) <= len(n) + 2:
                return False
        if re.fullmatch(r"[\W_]+", t, flags=re.UNICODE):
            return False
        return True

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
        """
        OCR 底部互动计数，按 x 从左到右。

        当前视频号底栏常见顺序：
          赞(拇指) → 转发 → 收藏(心) → 评论(气泡)
        因此：点赞用最左，开评论用最右。
        """
        d, w, h = self.d, self.w, self.h
        img = np.array(d.screenshot(format="pillow"))
        gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
        x0 = int(w * 0.45)
        bottom = self._enhance(gray[int(h * 0.86):int(h * 0.96), x0:w])
        results = self._get_ocr().readtext(cv2.cvtColor(bottom, cv2.COLOR_GRAY2BGR))

        counts = []
        for bbox, text, conf in results:
            t = str(text).strip().replace(",", "")
            if conf < 0.3 or not any(c.isdigit() for c in t):
                continue
            if ":" in t and t.replace(":", "").replace(".", "").isdigit():
                continue
            cx = int((bbox[0][0] + bbox[2][0]) / 2) + x0
            cy = int((bbox[0][1] + bbox[2][1]) / 2) + int(h * 0.86)
            counts.append((cx, cy, t))
        counts.sort(key=lambda c: c[0])
        return counts

    def _right_rail_counts(self) -> list[tuple[int, int, str]]:
        """
        OCR 右侧中部计数（旧竖栏布局兼容）。
        过滤掉过低的底栏数字，避免把底栏评论数当成竖栏。
        """
        d, w, h = self.d, self.w, self.h
        img = np.array(d.screenshot(format="pillow"))
        gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
        x0 = int(w * 0.78)
        y0 = int(h * 0.40)
        y1 = int(h * 0.82)
        rail = self._enhance(gray[y0:y1, x0:w])
        results = self._get_ocr().readtext(cv2.cvtColor(rail, cv2.COLOR_GRAY2BGR))

        counts = []
        for bbox, text, conf in results:
            t = str(text).strip().replace(",", "")
            if conf < 0.3 or not any(c.isdigit() for c in t):
                continue
            if ":" in t and t.replace(":", "").isdigit():
                continue
            # 「弹 0」弹幕开关忽略
            if "弹" in t:
                continue
            cx = int((bbox[0][0] + bbox[2][0]) / 2) + x0
            cy = int((bbox[0][1] + bbox[2][1]) / 2) + y0
            if cy > h * 0.84:
                continue
            counts.append((cx, cy, t))
        counts.sort(key=lambda c: c[1])
        return counts

    def _like_current(self) -> bool:
        """点赞：底栏最左侧拇指；兼容旧右侧竖栏第 1 个。"""
        counts = self._bottom_counts()
        if counts:
            cx, cy, label = counts[0]
            like_x = cx
            like_y = max(cy - int(self.h * 0.028), int(self.h * 0.82))
            logger.debug(f"[{self.account_id}] 点赞(底栏左={label}): ({like_x},{like_y})")
            self.d.click(like_x, like_y)
            time.sleep(0.8)
            return True

        rail = self._right_rail_counts()
        if rail:
            cx, cy, _ = rail[0]
            like_x = min(cx + int(self.w * 0.01), self.w - 8)
            like_y = max(cy - int(self.h * 0.035), int(self.h * 0.40))
            logger.debug(f"[{self.account_id}] 点赞(右侧栏): ({like_x},{like_y})")
            self.d.click(like_x, like_y)
            time.sleep(0.8)
            return True

        logger.debug(f"[{self.account_id}] OCR未找到点赞计数")
        return False

    def _is_comment_panel_open(self) -> bool:
        """评论半屏是否已打开（发表评论 / 评论 N / 都在搜）。"""
        try:
            img = np.array(self.d.screenshot(format="pillow"))
            gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
            h, w = gray.shape[:2]
            region = self._enhance(gray[int(h * 0.28):h, 0:w])
            results = self._get_ocr().readtext(cv2.cvtColor(region, cv2.COLOR_GRAY2BGR))
            blob = " ".join(str(t) for _, t, c in results if c >= 0.3)
            markers = ("发表评论", "都在搜", "条回复", "发送")
            if any(m in blob for m in markers):
                # 「发送」 alone 也可能是别的页；需伴随评论相关词
                if "发送" in blob and not any(
                    m in blob for m in ("发表评论", "都在搜", "条回复", "评论")
                ):
                    return False
                return True
            if "评论" in blob and any(ch.isdigit() for ch in blob):
                return True
        except Exception:
            return False
        return False

    def _open_comment_panel(self) -> bool:
        """
        打开评论区。

        新版底栏最右侧是评论气泡；不要点左侧赞/心形。
        """
        if self._is_comment_panel_open():
            return True

        # 1) 底栏最右侧计数 = 评论
        counts = self._bottom_counts()
        if counts:
            cx, cy, label = counts[-1]
            click_x = cx
            click_y = max(cy - int(self.h * 0.030), int(self.h * 0.82))
            logger.info(
                f"[{self.account_id}] 开评论(底栏最右={label}): ({click_x},{click_y}) "
                f"counts={[c[2] for c in counts]}"
            )
            self.d.click(click_x, click_y)
            time.sleep(1.3)
            if self._is_comment_panel_open():
                return True

        # 2) 机型坐标
        try:
            from config.device_profiles import get_extra, get_coord
            pt = get_extra(self.d, "channels_comment_icon") or get_coord(
                self.d, "channels_comment_icon"
            )
            if pt:
                logger.info(f"[{self.account_id}] 开评论(机型坐标): {pt}")
                click_ratio(self.d, float(pt[0]), float(pt[1]))
                time.sleep(1.3)
                if self._is_comment_panel_open():
                    return True
        except Exception:
            pass

        # 3) 底栏右侧比例兜底（评论气泡常见位）
        for rx, ry in ((0.93, 0.88), (0.92, 0.90), (0.95, 0.88), (0.90, 0.89)):
            click_ratio(self.d, rx, ry)
            time.sleep(1.0)
            if self._is_comment_panel_open():
                logger.info(f"[{self.account_id}] 开评论(比例 {rx},{ry})")
                return True

        # 4) 旧竖栏第 2 个（兼容）
        rail = self._right_rail_counts()
        if len(rail) >= 2:
            cx, cy, label = rail[1]
            click_x = min(cx + int(self.w * 0.01), self.w - 8)
            click_y = max(cy - int(self.h * 0.035), int(self.h * 0.45))
            logger.info(f"[{self.account_id}] 开评论(竖栏第2={label}): ({click_x},{click_y})")
            self.d.click(click_x, click_y)
            time.sleep(1.2)
            if self._is_comment_panel_open():
                return True

        logger.warning(f"[{self.account_id}] 未能打开评论半屏")
        return False

    def _focus_comment_input(self) -> bool:
        """点击「发表评论：」输入条，唤起系统键盘。"""
        for y0, y1 in ((0.55, 0.95), (0.70, 0.99), (0.45, 0.80)):
            focused = ocr_find_and_click(
                self.d,
                self._get_ocr(),
                ["发表评论", "说点什么", "写评论"],
                y_min_ratio=y0,
                y_max_ratio=y1,
                conf_min=0.3,
                enhance=self._enhance,
            )
            if focused:
                time.sleep(0.6)
                return True
        try:
            from config.device_profiles import get_extra
            pt = get_extra(self.d, "channels_comment_input")
            if pt:
                click_ratio(self.d, float(pt[0]), float(pt[1]))
                time.sleep(0.6)
                return True
        except Exception:
            pass
        click_ratio(self.d, 0.42, 0.88)
        time.sleep(0.6)
        return True

    def _keyboard_with_send_visible(self) -> bool:
        """系统键盘已弹出：能 OCR 到「发送」。"""
        return "发送" in self._input_bar_blob(y_min=0.45)

    def _wait_send_button(self, timeout: float = 4.0) -> bool:
        """等待键盘弹出后出现「发送」（空内容时可能灰色，但仍可见）。"""
        t0 = time.time()
        while time.time() - t0 < timeout:
            if self._keyboard_with_send_visible():
                return True
            time.sleep(0.35)
        return False

    def _type_comment_text(self, text: str) -> bool:
        """
        在系统键盘已弹出时写入评论。

        不要 set_input_ime(True)：ADBKeyboard 会收起系统键盘，
        视频号的「发送」按钮会一起消失。
        """
        try:
            focused = self.d(focused=True)
            if focused.exists(timeout=0.6):
                focused.set_text(text)
                time.sleep(0.45)
                if self._text_in_input(text):
                    return True
        except Exception as e:
            logger.debug(f"[{self.account_id}] set_text 失败: {e}")

        try:
            self.d.set_clipboard(text)
            time.sleep(0.15)
            self.d.shell("input keyevent 279")  # PASTE
            time.sleep(0.45)
            if self._text_in_input(text):
                return True
        except Exception as e:
            logger.debug(f"[{self.account_id}] paste 失败: {e}")

        try:
            self.d.send_keys(text)
            time.sleep(0.45)
            if self._text_in_input(text):
                return True
        except Exception as e:
            logger.debug(f"[{self.account_id}] send_keys 失败: {e}")
        return self._text_in_input(text)

    def _text_in_input(self, text: str) -> bool:
        """输入区是否已出现待发文案。"""
        t = (text or "").strip()
        if not t:
            return False
        blob = self._input_bar_blob(y_min=0.48)
        if t in blob:
            return True
        return len(t) >= 2 and t[:2] in blob

    def _input_bar_blob(self, y_min: float = 0.50) -> str:
        """OCR 下半屏文字（含键盘/输入条，便于找「发送」）。"""
        try:
            img = np.array(self.d.screenshot(format="pillow"))
            gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
            h, w = gray.shape[:2]
            region = self._enhance(gray[int(h * y_min):h, 0:w])
            results = self._get_ocr().readtext(cv2.cvtColor(region, cv2.COLOR_GRAY2BGR))
            return " ".join(str(t) for _, t, c in results if c >= 0.3)
        except Exception:
            return ""

    def _strict_input_blob(self) -> str:
        """键盘弹起扫中下部；否则扫底部输入条。"""
        if self._keyboard_with_send_visible():
            return self._input_bar_blob(y_min=0.48)
        return self._input_bar_blob(y_min=0.82)

    def _find_channels_send_green(self):
        """键盘上方右侧微信绿「发送」（有内容后常由灰变绿）。"""
        try:
            shot = self.d.screenshot(format="opencv")
            if shot is None:
                return None
            h, w = shot.shape[:2]
            hsv = cv2.cvtColor(shot, cv2.COLOR_BGR2HSV)
            mask = cv2.inRange(hsv, np.array([35, 60, 60]), np.array([95, 255, 255]))
            y0, y1 = int(h * 0.45), int(h * 0.78)
            x0 = int(w * 0.70)
            mask[:y0, :] = 0
            mask[y1:, :] = 0
            mask[:, :x0] = 0
            contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
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
            logger.debug(f"[{self.account_id}] 评论绿钮检测失败: {e}")
            return None

    def _click_send_by_uiautomator(self) -> bool:
        """控件树若暴露「发送」则直接点。"""
        try:
            node = self.d(text="发送")
            if node.exists(timeout=0.8):
                node.click()
                return True
        except Exception:
            pass
        try:
            node = self.d(description="发送")
            if node.exists(timeout=0.5):
                node.click()
                return True
        except Exception:
            pass
        return False

    def _send_comment_text(self) -> bool:
        """键盘仍在时点击「发送」（表情行右侧）。"""
        pt = self._find_channels_send_green()
        if pt:
            logger.info(f"[{self.account_id}] 绿钮发送 @({pt[0]:.3f},{pt[1]:.3f})")
            click_ratio(self.d, float(pt[0]), float(pt[1]))
            return True

        if self._click_send_by_uiautomator():
            logger.info(f"[{self.account_id}] u2 点发送")
            return True

        for y0, y1 in ((0.48, 0.78), (0.42, 0.72), (0.50, 0.85)):
            if ocr_find_and_click(
                self.d,
                self._get_ocr(),
                ["发送"],
                y_min_ratio=y0,
                y_max_ratio=y1,
                conf_min=0.28,
                enhance=self._enhance,
            ):
                logger.info(f"[{self.account_id}] OCR 点发送 y={y0}-{y1}")
                return True

        try:
            from config.device_profiles import get_extra
            pts = list(get_extra(self.d, "channels_comment_send_candidates") or [])
            pt2 = get_extra(self.d, "channels_comment_send")
            if pt2:
                pts.append(pt2)
            for cand in pts:
                if not cand:
                    continue
                click_ratio(self.d, float(cand[0]), float(cand[1]))
                time.sleep(0.45)
                if self._comment_published_quick():
                    return True
        except Exception:
            pass

        for rx, ry in ((0.90, 0.62), (0.92, 0.60), (0.88, 0.64), (0.90, 0.58)):
            click_ratio(self.d, rx, ry)
            time.sleep(0.4)
            if self._comment_published_quick():
                return True
        return False

    def _comment_published_quick(self) -> bool:
        """发送后通常键盘收起，底部回到「发表评论」。"""
        time.sleep(0.25)
        if self._keyboard_with_send_visible():
            return False
        blob = self._input_bar_blob(y_min=0.70)
        return "发表评论" in blob or "说点什么" in blob

    def _comment_published(self, text: str) -> bool:
        """发送成功：键盘收起 + 占位恢复，输入条不残留原文。"""
        t = (text or "").strip()
        if self._keyboard_with_send_visible():
            return False
        blob = self._input_bar_blob(y_min=0.72)
        if "发表评论" in blob or "说点什么" in blob:
            if t and t in self._input_bar_blob(y_min=0.82):
                return False
            return True
        return False

    def _comment_current(self, text: str) -> bool:
        """
        开评论半屏 → 点输入框弹系统键盘 → 出现「发送」
        → 写入文字（不切 ADBKeyboard）→ 点发送。
        """
        if not text:
            return False
        text = text[:40]
        logger.info(f"[{self.account_id}] 视频号发表评论: {text}")

        if not self._open_comment_panel():
            self._go_back_to_video()
            return False

        # 禁用 ADBKeyboard，保留系统键盘路径
        try:
            self.d.set_input_ime(False)
        except Exception:
            pass

        if not self._focus_comment_input():
            self._go_back_to_video()
            return False

        if not self._wait_send_button(timeout=4.5):
            self._focus_comment_input()
            if not self._wait_send_button(timeout=3.0):
                logger.warning(f"[{self.account_id}] 键盘/发送按钮未出现")
                self._go_back_to_video()
                return False

        logger.info(f"[{self.account_id}] 已检测到发送按钮（键盘已弹出）")

        if not self._type_comment_text(text):
            logger.warning(f"[{self.account_id}] 评论文字未写入，重试")
            self._focus_comment_input()
            self._wait_send_button(timeout=2.5)
            if not self._type_comment_text(text):
                self._go_back_to_video()
                return False

        time.sleep(0.5)
        if not self._keyboard_with_send_visible():
            self._focus_comment_input()
            self._wait_send_button(timeout=2.0)

        self._send_comment_text()
        time.sleep(1.0)
        ok = self._comment_published(text)
        if not ok:
            logger.warning(f"[{self.account_id}] 评论未发出，重试点发送")
            if self._keyboard_with_send_visible() or self._wait_send_button(2.0):
                if not self._text_in_input(text):
                    self._type_comment_text(text)
                    time.sleep(0.4)
                self._send_comment_text()
                time.sleep(1.0)
                ok = self._comment_published(text)

        self._go_back_to_video()
        if ok:
            logger.info(f"[{self.account_id}] 视频号评论已发送")
        else:
            logger.warning(f"[{self.account_id}] 视频号评论发送失败")
        return ok

    # ================================================================
    # 页面检测 + 恢复
    # ================================================================

    def _is_on_video_page(self) -> bool:
        """是否在视频播放页（右侧栏或底栏有互动计数，且非评论半屏）。"""
        if self._is_comment_panel_open():
            return False
        if len(self._right_rail_counts()) >= 1:
            return True
        return len(self._bottom_counts()) >= 2

    def _go_back_to_video(self):
        """从评论区等页面退回视频播放页。"""
        for _ in range(4):
            if self._is_comment_panel_open():
                self.d.press("back")
                time.sleep(0.55)
                continue
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
