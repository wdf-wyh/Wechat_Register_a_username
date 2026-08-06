"""
朋友圈点赞/评论模块 — OCR 时间戳定位 + 窄带菜单识别
===================================================

## 概述

通过 OCR 识别朋友圈时间戳定位帖子，偏移 0.69wpx 点击 "..." 展开菜单，
再通过 OCR 窄带扫描识别 "赞"/"评论" 按钮，实现点赞和评论操作。
全程使用 EasyOCR，不依赖像素扫描或头像检测。

## 工作流

::

    冷启动 → 发现 Tab → 朋友圈 → 下滑3次(露出标题)
      │
      ├─ OCR 全屏扫描 → 匹配时间戳正则 → N 条帖子
      │     └─ 模式: 刚刚 | N分钟前 | N小时前 | N天前 | 昨天 | N月N日
      │
      ├─ 对每条帖子 (随机概率):
      │     │
      │     ├─ 点击 "..." (timestamp.x + 0.69w, timestamp.y)
      │     │
      │     ├─ OCR 窄带 (y±75px, 右半屏) 识别菜单按钮:
      │     │   ├─ "赞"    → 点击 → 点赞完成
      │     │   ├─ "取消"  → 已赞, 跳过
      │     │   └─ "评论"  → 点击 → IME 输入 → 发送
      │     │
      │     └─ 点空白处关闭菜单
      │
      ├─ 页面检测: OCR 顶部找 "朋友圈" 标题
      │     └─ 未找到 → press("back") 恢复 (误触链接/文章)
      │
      └─ 滑动 → 循环直到指定时长

## 快速开始

.. code-block:: python

    from core.moments_interact import MomentsInteract

    mi = MomentsInteract(device)
    mi.like(0)                          # 点赞第1条帖子
    mi.comment(1, "说得好")              # 评论第2条帖子
    mi.browse_and_interact(300, "哈哈")  # 浏览5分钟, 随机互动

WeChatControl 入口:
    wc.like_moment(0)                           # 点赞
    wc.comment_moment("raregas", post_index=1)  # 评论
    wc.browse_moments_interact(600, "喵")        # 浏览10分钟

剧本动作:
    Action(ActionType.LIKE_MOMENT, "10:00", "11:00", (60, 180))
    Action(ActionType.COMMENT_MOMENT, "14:00", "15:00", (60, 180))
    Action(ActionType.BROWSE_MOMENTS_INTERACT, "18:00", "20:00", (300, 600))

## 定位策略

    EasyOCR(ch_sim+en) → CLAHE 增强 → 时间戳正则匹配
    → offset 0.69w → 窄带 OCR → "赞"/"取消"/"评论"

## 适配

基于 Moto X70 Air Pro (1264x2780, Android 14) 校准。
换设备需更新:
  - ``DOTS_X_OFFSET`` — "..." 相对时间戳的 X 偏移 (实测 0.69wpx)
  - ``TIMESTAMP_PATTERNS`` — 微信时间戳格式变化时补充
"""

import time
import random
import re
import cv2
import numpy as np

from utils.logger import get_logger

logger = get_logger("moments_interact")


class MomentsInteract:
    """朋友圈互动器 — OCR 定位 + 点赞 + 评论 + 页面恢复。"""

    BAND_Y_MARGIN = 90        # 菜单窄带 OCR 的 Y 范围
    MENU_RETRY = 12           # 点击 "..." 最大重试次数

    # 时间戳匹配正则
    TIMESTAMP_PATTERNS = [
        r"刚刚",
        r"\d+分钟前",
        r"\d+小时前",
        r"\d+天前",
        r"昨天\s*\d{1,2}:\d{2}",   # "昨天 13:45"
        r"昨天",
        r"\d+月\d+日",
        r"\d+年\d+月\d+日",
    ]

    def __init__(self, d, account_id: str = ""):
        self.d = d
        self.account_id = account_id
        self.w, self.h = d.info['displayWidth'], d.info['displayHeight']
        self._ocr = None
        self._clahe = None
        from config.device_profiles import get_extra
        self.DOTS_X_RATIOS = tuple(
            get_extra(d, "moments_dots_x_ratios",
                      (0.92, 0.90, 0.88, 0.94, 0.86))
        )

    # ================================================================
    # 公共接口
    # ================================================================

    def like(self, post_index: int = 0) -> bool:
        """点赞第 N 条帖子 (0-based)。"""
        self._ensure_on_moments()
        posts = self._find_timestamps()
        if post_index >= len(posts):
            logger.warning(f"[{self.account_id}] like: index {post_index} "
                           f">= {len(posts)} posts")
            return False

        post = posts[post_index]
        logger.info(f"[{self.account_id}] like: #{post_index} '{post['text']}'")

        if not self._open_menu(post):
            return False

        buttons = self._find_menu_buttons(post["y"])
        if "like" in buttons:
            self.d.click(*buttons["like"])
            time.sleep(0.3)
            logger.info(f"[{self.account_id}] 点赞成功")
            return True

        if buttons.get("already_liked"):
            logger.debug(f"[{self.account_id}] 已赞, 跳过")
            return True

        logger.warning(f"[{self.account_id}] 未找到赞按钮")
        return False

    def comment(self, post_index: int = 0, text: str = "") -> bool:
        """评论第 N 条帖子 (0-based)。"""
        self._ensure_on_moments()
        posts = self._find_timestamps()
        if post_index >= len(posts):
            logger.warning(f"[{self.account_id}] comment: index {post_index} "
                           f">= {len(posts)} posts")
            return False

        post = posts[post_index]
        logger.info(f"[{self.account_id}] comment: #{post_index} '{text[:20]}'")

        if not self._open_menu(post):
            return False

        buttons = self._find_menu_buttons(post["y"])
        if "comment" not in buttons:
            logger.warning(f"[{self.account_id}] 未找到评论按钮")
            return False

        self.d.click(*buttons["comment"])
        time.sleep(0.7)
        self._ime_input(text)
        self.d.click(int(self.w * 0.90), int(self.h * 0.96))
        time.sleep(0.8)
        logger.info(f"[{self.account_id}] 评论成功")
        return True

    def browse_and_interact(
        self,
        duration_seconds: int = 300,
        comment_text: str = "",
        like_rate: float = 0.35,
        scroll_rounds: int = 8,
    ) -> dict:
        """
        浏览朋友圈, 随机点赞+评论, 持续指定时长。

        Args:
            duration_seconds: 浏览总时长(秒), 0 则用 scroll_rounds 控制
            comment_text:    评论内容
            like_rate:       每条帖子互动概率
            scroll_rounds:   滑动轮次 (duration_seconds>0 时忽略)

        Returns:
            {"liked": N, "commented": N, "recovered": N, "elapsed": S}
        """
        logger.info(f"[{self.account_id}] 朋友圈互动浏览: "
                     f"rate={like_rate:.0%} comment='{comment_text[:20]}'")

        self._ensure_on_moments()
        if not self._is_on_moments():
            logger.error(f"[{self.account_id}] 无法进入朋友圈, 退出")
            return {"liked": 0, "commented": 0, "recovered": 0,
                    "elapsed": 0, "success": False}

        liked = commented = recovered = 0
        rd = 0
        start_time = time.time()

        while True:
            # 时长控制
            if duration_seconds > 0:
                if time.time() - start_time >= duration_seconds:
                    break
            elif rd >= scroll_rounds:
                break

            # 页面恢复
            if not self._is_on_moments():
                if self._recover_to_moments():
                    recovered += 1
                else:
                    logger.error(f"[{self.account_id}] 无法返回朋友圈, 退出")
                    break

            # 本轮截图 + OCR
            posts = self._find_timestamps()

            for post in posts:
                if random.random() > like_rate:
                    continue

                # 页面检查
                if not self._is_on_moments():
                    self._recover_to_moments()
                    recovered += 1

                # 打开菜单
                if not self._open_menu(post):
                    continue

                # 识别按钮
                buttons = self._find_menu_buttons(post["y"])

                if buttons.get("already_liked"):
                    logger.debug(f"[{self.account_id}] 已赞跳过: {post['text']}")

                # 点赞
                if "like" in buttons:
                    self.d.click(*buttons["like"])
                    liked += 1
                    time.sleep(0.3)

                # 评论
                if "comment" in buttons and comment_text:
                    self.d.click(*buttons["comment"])
                    time.sleep(0.7)
                    self._ime_input(comment_text)
                    self.d.click(int(self.w * 0.90), int(self.h * 0.96))
                    commented += 1
                    time.sleep(0.8)

                # 微信点赞/评论后均会自动关闭菜单，无需手动关闭

            rd += 1

            # 滑动下一轮
            if duration_seconds > 0:
                if time.time() - start_time < duration_seconds:
                    self._scroll()
            elif rd < scroll_rounds:
                self._scroll()

        elapsed = time.time() - start_time
        logger.info(f"[{self.account_id}] 互动完成: "
                     f"liked={liked} commented={commented} recovered={recovered} "
                     f"{elapsed:.0f}s")

        return {
            "liked": liked,
            "commented": commented,
            "recovered": recovered,
            "elapsed": elapsed,
            "success": True,
        }

    # ================================================================
    # 页面检测与恢复
    # ================================================================

    def _is_on_moments(self) -> bool:
        """OCR 检测是否在朋友圈页（勿用「昨天」等会话列表常见词）。"""
        img = np.array(self.d.screenshot(format="pillow"))
        gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
        enhanced = cv2.cvtColor(self._enhance(gray), cv2.COLOR_GRAY2BGR)
        top = enhanced[int(self.h * 0.02):int(self.h * 0.18),
                       int(self.w * 0.05):int(self.w * 0.95)]
        mid = enhanced[int(self.h * 0.15):int(self.h * 0.75),
                       int(self.w * 0.05):int(self.w * 0.95)]
        texts = []
        for region in (top, mid):
            for _, t, c in self._get_ocr().readtext(region):
                if c > 0.25 and t:
                    texts.append(t.strip())
        blob = " ".join(texts)
        # 负向：仍在会话列表 / 发现页
        if any(k in blob for k in ("通讯录", "腾讯新闻", "服务通知", "视频号", "扫一扫")):
            # 发现页有视频号；朋友圈内一般没有这些
            if "朋友圈" not in blob and "更换封面" not in blob and "轻触更换" not in blob:
                if "视频号" in blob or "扫一扫" in blob or "通讯录" in blob:
                    return False
        markers = (
            "朋友圈", "轻触更换封面", "更换封面", "还没有朋友", "拍一张",
            "这一刻的想法",
        )
        if any(k in blob for k in markers):
            return True
        # 有时间戳 + 不在发现/会话：弱判定
        if "分钟前" in blob or "小时前" in blob:
            if not any(k in blob for k in ("微信(", "腾讯新闻", "服务通知")):
                return True
        return False

    def _recover_to_moments(self) -> bool:
        """按 back 直到返回朋友圈, 最多 3 次。"""
        for _ in range(3):
            if self._is_on_moments():
                return True
            logger.debug(f"[{self.account_id}] 误触恢复: back")
            self.d.press("back")
            time.sleep(1.5)
        return self._is_on_moments()

    def _ensure_on_moments(self):
        """进入朋友圈并确保标题可见。"""
        from core.wechat_nav import (
            moments_entry_for,
            click_ratio,
            goto_tab,
            ocr_find_and_click,
            start_wechat,
        )

        start_wechat(self.d, wait=4.0, cold=True)

        if self._is_on_moments():
            return

        goto_tab(self.d, "discover")
        time.sleep(1.0)

        clicked = ocr_find_and_click(
            self.d,
            self._get_ocr(),
            ["朋友圈"],
            y_min_ratio=0.08,
            y_max_ratio=0.45,
            conf_min=0.3,
            enhance=self._enhance,
            click_row_center=True,
        )
        if not clicked:
            logger.warning(f"[{self.account_id}] OCR未找到朋友圈入口，使用相对坐标")
            click_ratio(self.d, *moments_entry_for(self.d))
        time.sleep(2)

        # 轻微上滑露出时间戳（空页/封面页不必猛滑）
        self.d.swipe(self.w // 2, int(self.h * 0.55),
                     self.w // 2, int(self.h * 0.40), duration=0.2)
        time.sleep(0.8)

        if not self._is_on_moments():
            logger.error(f"[{self.account_id}] 进入朋友圈后仍未检测到标题")

    # ================================================================
    # 时间戳定位
    # ================================================================

    def _find_timestamps(self) -> list[dict]:
        """OCR 全屏扫描, 匹配时间戳, 返回帖子列表按 Y 排序。"""
        img = np.array(self.d.screenshot(format="pillow"))
        gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
        enhanced = cv2.cvtColor(self._enhance(gray), cv2.COLOR_GRAY2BGR)

        results = self._get_ocr().readtext(
            enhanced, text_threshold=0.3, low_text=0.2)

        posts = []
        for bbox, text, conf in results:
            if conf < 0.18:
                continue
            text = text.strip()
            # 宽松匹配：正则或含「分钟前/小时前/天前/刚刚」
            hit = any(re.search(p, text) for p in self.TIMESTAMP_PATTERNS)
            if not hit:
                hit = any(k in text for k in ("分钟前", "小时前", "天前", "刚刚"))
            if not hit:
                continue
            cx = int((bbox[0][0] + bbox[2][0]) / 2)
            cy = int((bbox[0][1] + bbox[2][1]) / 2)
            # 会话列表时间在右侧；朋友圈时间戳偏左
            if cx > self.w * 0.70:
                continue
            if cy < self.h * 0.15 or cy > self.h * 0.95:
                continue
            posts.append({"x": cx, "y": cy, "text": text, "conf": float(conf)})

        posts.sort(key=lambda p: p["y"])
        # 去重：相近 Y 只留一条
        dedup = []
        for p in posts:
            if not dedup or abs(p["y"] - dedup[-1]["y"]) > 40:
                dedup.append(p)
        return dedup

    # ================================================================
    # 菜单操作
    # ================================================================

    def _open_menu(self, post: dict) -> bool:
        """点击帖子的 '...' 并验证菜单弹出。支持多 X 比例 + 偏移重试。"""
        y_base = post["y"]
        y_offsets = (0, 4, -4, 8, -8, 12, -12)
        attempt = 0

        for rx in self.DOTS_X_RATIOS:
            x_base = int(self.w * rx)
            for dy in y_offsets:
                if attempt >= self.MENU_RETRY:
                    break
                attempt += 1
                x, y = x_base, y_base + dy
                if y < 0 or y >= self.h:
                    continue
                self.d.click(x, y)
                time.sleep(0.55)
                if self._check_menu_open(post["y"]):
                    logger.debug(f"[{self.account_id}] 菜单打开 @({x},{y})")
                    return True

        logger.warning(f"[{self.account_id}] 菜单未弹出")
        return False

    def _check_menu_open(self, y_ts: int) -> bool:
        """OCR 窄带检查菜单是否展开。"""
        img = np.array(self.d.screenshot(format="pillow"))
        gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
        enhanced = cv2.cvtColor(self._enhance(gray), cv2.COLOR_GRAY2BGR)

        y0 = max(0, y_ts - self.BAND_Y_MARGIN * 3)
        y1 = min(self.h, y_ts + self.BAND_Y_MARGIN * 3)
        crop = enhanced[y0:y1, int(self.w * 0.3):self.w]
        results = self._get_ocr().readtext(crop)

        for _, text, conf in results:
            if conf > 0.2 and ("赞" in text or "评论" in text or "取消" in text):
                return True
        return False

    def _find_menu_buttons(self, y_ts: int) -> dict:
        """
        OCR 窄带扫描菜单, 返回按钮坐标字典。
        keys: "like" | "comment" | "already_liked"
        """
        img = np.array(self.d.screenshot(format="pillow"))
        gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
        enhanced = cv2.cvtColor(self._enhance(gray), cv2.COLOR_GRAY2BGR)

        y0 = max(0, y_ts - self.BAND_Y_MARGIN)
        y1 = min(self.h, y_ts + self.BAND_Y_MARGIN)
        crop = enhanced[y0:y1, int(self.w * 0.3):self.w]
        results = self._get_ocr().readtext(crop)

        buttons = {}
        for bbox, text, conf in results:
            if conf < 0.2:
                continue
            t = text.strip()
            cx = int((bbox[0][0] + bbox[2][0]) / 2) + int(self.w * 0.3)
            cy = int((bbox[0][1] + bbox[2][1]) / 2) + y0

            if "取消" in t and "评论" not in t:
                buttons["already_liked"] = True
            elif "赞" in t and "评论" not in t:
                buttons["like"] = (cx, cy)
            if "评论" in t:
                buttons["comment"] = (cx, cy)

        return buttons

    # ================================================================
    # 输入 & 滑动
    # ================================================================

    def _ime_input(self, text: str):
        """ADBKeyboard IME 静默输入文字。"""
        try:
            self.d.set_input_ime(True)
            time.sleep(0.2)
            self.d.send_keys(text)
            time.sleep(0.3)
            self.d.set_input_ime(False)
        except Exception:
            self.d.shell(f"input text {text}")

    def _scroll(self):
        """向下滑动朋友圈, 随机停留。"""
        self.d.swipe(
            self.w // 2, int(self.h * 0.72),
            self.w // 2, int(self.h * 0.28),
            duration=0.3,
        )
        time.sleep(random.uniform(1.5, 4.0))

    # ================================================================
    # 工具
    # ================================================================

    def _enhance(self, gray):
        if self._clahe is None:
            self._clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(4, 4))
        return self._clahe.apply(gray)

    def _get_ocr(self):
        if self._ocr is None:
            from utils.ocr_utils import create_easyocr_reader
            self._ocr = create_easyocr_reader()
        return self._ocr
