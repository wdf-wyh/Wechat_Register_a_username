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

from __future__ import annotations

import time
import random
import re
import cv2
import numpy as np
from collections.abc import Callable

from utils.logger import get_logger

logger = get_logger("moments_interact")


class MomentsInteract:
    """朋友圈互动器 — OCR 定位 + 点赞 + 评论 + 页面恢复。"""

    BAND_Y_MARGIN = 90        # 菜单窄带 OCR 的 Y 范围
    MENU_RETRY = 36           # 点击 "..." 最大重试次数

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

    # 作者/正文 OCR 噪声词
    _AUTHOR_NOISE = (
        "朋友圈", "轻触更换封面", "更换封面", "还没有朋友", "拍一张",
        "这一刻的想法", "全文", "收起", "展开", "删除", "赞", "评论",
        "转发", "分钟前", "小时前", "天前", "刚刚", "昨天", "广告",
    )
    _MINUTES_AGO_RE = re.compile(r"(\d+)\s*分钟前")
    _HOURS_AGO_RE = re.compile(r"(\d+)\s*小时前")

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

        buttons = self._find_menu_buttons(self._menu_y(post))
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
        return self._comment_on_post(post, text)

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
                buttons = self._find_menu_buttons(self._menu_y(post))

                if buttons.get("already_liked"):
                    logger.debug(f"[{self.account_id}] 已赞跳过: {post['text']}")

                # 先评论再点赞（点赞后菜单会关闭，无法再评）
                if "comment" in buttons and comment_text:
                    if self._comment_on_post(post, comment_text):
                        commented += 1
                    elif not self._is_on_moments():
                        self._recover_to_moments()
                        recovered += 1

                if "like" in buttons:
                    if not self._open_menu(post):
                        continue
                    buttons = self._find_menu_buttons(self._menu_y(post))
                    if "like" in buttons:
                        self.d.click(*buttons["like"])
                        liked += 1
                        time.sleep(0.3)

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

    def daily_interact(
        self,
        target_count: int = 20,
        big_v_accounts: list[str] | None = None,
        comment_fn: Callable[[str, str], str] | None = None,
        fresh_minutes: int = 30,
        max_duration: int = 900,
        max_scroll_rounds: int = 30,
    ) -> dict:
        """
        每日朋友圈互动：完成 target_count 次点赞/评论，优先秒评大 V 新帖。

        排序策略（高→低）:
          1. 大 V + 新帖（刚刚/N分钟内）→ 秒评
          2. 大 V 旧帖 → 点赞/评论
          3. 普通好友 → 补齐剩余互动次数
        """
        big_v_accounts = big_v_accounts or []
        logger.info(
            f"[{self.account_id}] 每日朋友圈互动: target={target_count} "
            f"big_v={len(big_v_accounts)} fresh<={fresh_minutes}min"
        )

        self._ensure_on_moments()
        if not self._is_on_moments():
            logger.error(f"[{self.account_id}] 无法进入朋友圈, 退出")
            return {
                "interactions": 0, "liked": 0, "commented": 0,
                "big_v_commented": 0, "recovered": 0, "elapsed": 0, "success": False,
            }

        liked = commented = big_v_commented = recovered = interactions = 0
        acted_keys: set[str] = set()
        start_time = time.time()
        scroll_round = 0

        while (
            interactions < target_count
            and scroll_round < max_scroll_rounds
            and (time.time() - start_time) < max_duration
        ):
            if not self._is_on_moments():
                if self._recover_to_moments():
                    recovered += 1
                else:
                    logger.error(f"[{self.account_id}] 无法返回朋友圈, 退出")
                    break

            posts = self._scan_posts_rich(big_v_accounts, fresh_minutes)
            posts = [
                p for p in posts
                if not self._post_already_acted(p, acted_keys)
            ]
            posts.sort(key=lambda p: p["priority"], reverse=True)

            acted_this_round = False
            for post in posts:
                if interactions >= target_count:
                    break
                if (time.time() - start_time) >= max_duration:
                    break

                do_comment, do_like = self._decide_interaction(post)
                if not do_comment and not do_like:
                    continue

                comment_text = ""
                if do_comment:
                    if comment_fn:
                        comment_text = (comment_fn(
                            post.get("content", ""),
                            post.get("author", ""),
                        ) or "").strip()
                    if not comment_text:
                        comment_text = random.choice(["不错", "学到了", "哈哈哈", "支持"])

                result = self._interact_on_post(
                    post,
                    comment_text=comment_text,
                    do_like=do_like,
                    do_comment=do_comment,
                )
                delta = result["liked"] + result["commented"]
                if delta <= 0:
                    continue

                self._mark_post_acted(post, acted_keys)
                interactions += delta
                liked += result["liked"]
                commented += result["commented"]
                if result["commented"] and post.get("is_big_v") and post.get("is_fresh"):
                    big_v_commented += 1
                acted_this_round = True
                time.sleep(random.uniform(0.8, 2.0))

            scroll_round += 1
            if interactions >= target_count:
                break
            if not acted_this_round or scroll_round >= max_scroll_rounds:
                if (time.time() - start_time) >= max_duration:
                    break
            self._scroll()

        elapsed = time.time() - start_time
        logger.info(
            f"[{self.account_id}] 每日互动完成: interactions={interactions} "
            f"liked={liked} commented={commented} big_v秒评={big_v_commented} "
            f"recovered={recovered} {elapsed:.0f}s"
        )
        return {
            "interactions": interactions,
            "liked": liked,
            "commented": commented,
            "big_v_commented": big_v_commented,
            "recovered": recovered,
            "elapsed": elapsed,
            "success": interactions > 0,
        }

    # ================================================================
    # 每日互动：帖子扫描与优先级
    # ================================================================

    def _scan_posts_rich(
        self,
        big_v_accounts: list[str],
        fresh_minutes: int,
    ) -> list[dict]:
        """OCR 全屏扫描，提取时间戳/昵称锚点、作者、正文及优先级。"""
        blocks = self._ocr_blocks()
        raw_posts = self._detect_posts(blocks, big_v_accounts)
        raw_posts = self._merge_duplicate_posts(raw_posts)
        if not raw_posts:
            logger.warning(f"[{self.account_id}] 未识别到任何朋友圈帖子")
        else:
            ts_n = sum(1 for p in raw_posts if p.get("detect_method") == "timestamp")
            anchor_n = len(raw_posts) - ts_n
            logger.info(
                f"[{self.account_id}] 识别帖子 {len(raw_posts)} 条 "
                f"(时间戳={ts_n} 昵称锚点={anchor_n})"
            )

        posts = []
        for ts in raw_posts:
            author = ts.get("author") or self._extract_author(blocks, ts)
            content = self._extract_content(blocks, ts, author)
            region_text = self._post_region_text(blocks, ts)
            is_fresh_raw, age_min = self._parse_freshness(ts["text"])
            is_fresh = is_fresh_raw and age_min <= fresh_minutes
            is_big_v = (
                self._match_big_v(author, big_v_accounts)
                or self._match_big_v_in_text(region_text, big_v_accounts)
            )
            if is_big_v and not author:
                author = self._find_big_v_name(region_text, big_v_accounts) or author
            priority = 0
            if is_big_v:
                priority += 10000
            if is_fresh:
                priority += 1000 - min(age_min, 999)
            if is_big_v:
                logger.debug(
                    f"[{self.account_id}] 大V帖: author='{author}' "
                    f"fresh={is_fresh} ts='{ts['text']}'"
                )
            posts.append({
                **ts,
                "author": author,
                "content": content,
                "is_fresh": is_fresh,
                "age_minutes": age_min,
                "is_big_v": is_big_v,
                "priority": priority,
            })
        return posts

    def _detect_posts(
        self,
        blocks: list[dict],
        big_v_accounts: list[str] | None = None,
    ) -> list[dict]:
        """时间戳定位为主；无时间戳时用昵称锚点兜底（新版微信常见）。"""
        big_v_accounts = big_v_accounts or []
        timestamps = self._blocks_to_timestamps(blocks)
        posts: list[dict] = []

        for p in self._blocks_to_author_anchors(blocks, big_v_accounts, timestamps):
            posts.append(p)

        for ts in timestamps:
            if any(abs(ts["y"] - q["y"]) < 60 for q in posts):
                continue
            posts.append({**ts, "detect_method": "timestamp", "author": ""})

        posts.sort(key=lambda p: p["y"])
        return posts

    def _blocks_to_author_anchors(
        self,
        blocks: list[dict],
        big_v_accounts: list[str],
        timestamps: list[dict] | None = None,
    ) -> list[dict]:
        """
        无 OCR 时间戳时，用昵称行定位帖子。

        朋友圈布局：头像左 + 昵称 + 正文 + （时间戳/... 可能在 OCR 外）
        用昵称 y 与下一条昵称之间的正文块估算 ... 按钮行。
        """
        timestamps = timestamps or []
        anchors: list[dict] = []
        for block in blocks:
            text = block["text"]
            if block["y"] < self.h * 0.12 or block["y"] > self.h * 0.92:
                continue
            if block["x"] < self.w * 0.10 or block["x"] > self.w * 0.62:
                continue
            is_big_v = self._match_big_v(text, big_v_accounts)
            if not is_big_v and not self._looks_like_nickname(text):
                continue
            # 点赞区会重复 OCR 出昵称（在时间戳下方），不是新帖
            ts_above = [
                t for t in timestamps
                if 0 < block["y"] - t["y"] < 250
            ]
            if ts_above:
                continue
            anchors.append({**block, "is_big_v_anchor": is_big_v})

        anchors.sort(key=lambda a: a["y"])
        dedup: list[dict] = []
        for anchor in anchors:
            if dedup and abs(anchor["y"] - dedup[-1]["y"]) < 80:
                if anchor.get("is_big_v_anchor") and not dedup[-1].get("is_big_v_anchor"):
                    dedup[-1] = anchor
                continue
            dedup.append(anchor)

        posts: list[dict] = []
        for i, anchor in enumerate(dedup):
            y_next = dedup[i + 1]["y"] - 15 if i + 1 < len(dedup) else anchor["y"] + 900
            cluster_limit = min(y_next, anchor["y"] + 520)
            cluster = [
                b for b in blocks
                if anchor["y"] - 5 <= b["y"] < cluster_limit
            ]
            bottom_y = max((b["y"] for b in cluster), default=anchor["y"] + 100)
            ts_in_range = [
                t for t in timestamps
                if anchor["y"] < t["y"] < y_next
            ]
            if ts_in_range:
                ts_row = ts_in_range[0]
                posts.append({
                    **ts_row,
                    "detect_method": "timestamp",
                    "author": anchor["text"],
                    "author_y": anchor["y"],
                    "bottom_y": bottom_y,
                })
            else:
                span = max(y_next - anchor["y"], 160)
                menu_y = min(int(anchor["y"] + span * 0.88), self.h - 60)
                posts.append({
                    "x": int(self.w * 0.28),
                    "y": menu_y,
                    "text": "昵称锚点",
                    "conf": anchor["conf"],
                    "detect_method": "author_anchor",
                    "author": anchor["text"],
                    "author_y": anchor["y"],
                    "bottom_y": bottom_y,
                })
        return posts

    def _looks_like_nickname(self, text: str) -> bool:
        text = (text or "").strip()
        if len(text) < 2 or len(text) > 16:
            return False
        if any(c in text for c in "，。！？、；：""''（）【】《》"):
            return False
        if self._is_noise_text(text):
            return False
        return True

    def _ocr_blocks(self) -> list[dict]:
        """全屏 OCR，返回文本块列表 {text, x, y, conf}。"""
        img = np.array(self.d.screenshot(format="pillow"))
        gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
        enhanced = cv2.cvtColor(self._enhance(gray), cv2.COLOR_GRAY2BGR)
        results = self._get_ocr().readtext(
            enhanced, text_threshold=0.3, low_text=0.2)

        blocks = []
        for bbox, text, conf in results:
            if conf < 0.12:
                continue
            text = text.strip()
            if not text:
                continue
            cx = int((bbox[0][0] + bbox[2][0]) / 2)
            cy = int((bbox[0][1] + bbox[2][1]) / 2)
            blocks.append({"text": text, "x": cx, "y": cy, "conf": float(conf)})
        return blocks

    def _blocks_to_timestamps(self, blocks: list[dict]) -> list[dict]:
        posts = []
        for block in blocks:
            text = block["text"]
            hit = any(re.search(p, text) for p in self.TIMESTAMP_PATTERNS)
            if not hit:
                hit = any(k in text for k in ("分钟前", "小时前", "天前", "刚刚"))
            if not hit:
                continue
            if block["x"] > self.w * 0.70:
                continue
            if block["y"] < self.h * 0.15 or block["y"] > self.h * 0.95:
                continue
            posts.append({
                "x": block["x"],
                "y": block["y"],
                "text": text,
                "conf": block["conf"],
            })

        posts.sort(key=lambda p: p["y"])
        dedup = []
        for p in posts:
            if not dedup or abs(p["y"] - dedup[-1]["y"]) > 40:
                dedup.append(p)
        return dedup

    def _is_timestamp_text(self, text: str) -> bool:
        if any(re.search(p, text) for p in self.TIMESTAMP_PATTERNS):
            return True
        return any(k in text for k in ("分钟前", "小时前", "天前", "刚刚", "昨天"))

    def _is_noise_text(self, text: str) -> bool:
        if self._is_timestamp_text(text):
            return True
        if len(text) > 80:
            return True
        return any(n in text for n in self._AUTHOR_NOISE)

    def _extract_author(self, blocks: list[dict], ts: dict) -> str:
        """取帖子区域最上方的昵称（头像右侧，非正文末行）。"""
        y_ts = ts["y"]
        candidates = []
        for block in blocks:
            if block["y"] >= y_ts - 8:
                continue
            if block["y"] < y_ts - 400:
                continue
            if block["x"] < self.w * 0.12:
                continue
            if block["x"] > self.w * 0.78:
                continue
            text = block["text"]
            if self._is_noise_text(text):
                continue
            if len(text) < 2 or len(text) > 24:
                continue
            candidates.append(block)
        if not candidates:
            return ""
        candidates.sort(key=lambda b: (b["y"], b["x"]))
        return candidates[0]["text"]

    def _post_region_text(self, blocks: list[dict], ts: dict) -> str:
        """帖子时间戳上方区域的全部 OCR 文本（用于大 V 兜底匹配）。"""
        y_lo = ts["y"] - 400
        y_hi = ts["y"]
        parts = []
        for block in blocks:
            if block["y"] <= y_lo or block["y"] >= y_hi:
                continue
            if block["x"] > self.w * 0.85:
                continue
            parts.append(block["text"])
        return " ".join(parts)

    def _match_big_v_in_text(self, text: str, big_v_accounts: list[str]) -> bool:
        text = (text or "").strip()
        if not text:
            return False
        for name in big_v_accounts:
            name = str(name).strip()
            if name and name in text:
                return True
        return False

    def _find_big_v_name(self, text: str, big_v_accounts: list[str]) -> str:
        for name in big_v_accounts:
            name = str(name).strip()
            if name and name in (text or ""):
                return name
        return ""

    def _extract_content(self, blocks: list[dict], ts: dict, author: str) -> str:
        """取昵称与时间戳之间的正文片段。"""
        y_lo = ts["y"] - 320
        y_hi = ts["y"] - 8
        if author:
            author_blocks = [b for b in blocks if b["text"] == author]
            if author_blocks:
                y_hi = min(y_hi, author_blocks[0]["y"] + 40)

        parts = []
        for block in blocks:
            if block["y"] <= y_lo or block["y"] >= y_hi:
                continue
            if block["x"] > self.w * 0.92:
                continue
            text = block["text"]
            if self._is_noise_text(text):
                continue
            if author and text == author:
                continue
            parts.append((block["y"], text))

        parts.sort(key=lambda x: x[0])
        content = " ".join(t for _, t in parts)
        return content[:200]

    def _parse_freshness(self, timestamp_text: str) -> tuple[bool, int]:
        """解析时间戳新鲜度，返回 (是否新帖, 估计分钟数)。"""
        text = (timestamp_text or "").strip()
        if "刚刚" in text:
            return True, 0
        m = self._MINUTES_AGO_RE.search(text)
        if m:
            minutes = int(m.group(1))
            return minutes <= 60, minutes
        m = self._HOURS_AGO_RE.search(text)
        if m:
            hours = int(m.group(1))
            return hours == 0, hours * 60
        return False, 9999

    def _match_big_v(self, author: str, big_v_accounts: list[str]) -> bool:
        author = (author or "").strip()
        if not author:
            return False
        for name in big_v_accounts:
            name = str(name).strip()
            if not name:
                continue
            if name in author or author in name:
                return True
        return False

    def _post_act_key(self, post: dict) -> str:
        author = (post.get("author") or "").strip()
        author_y = int(post.get("author_y") or post["y"])
        if author:
            return f"{author}|{author_y // 100}"
        return f"|{post['y'] // 60}"

    def _same_post(self, a: dict, b: dict) -> bool:
        if abs(a["y"] - b["y"]) < 80:
            return True
        author_a = (a.get("author") or "").strip()
        author_b = (b.get("author") or "").strip()
        if not author_a or author_a != author_b:
            return False
        ay_a = int(a.get("author_y") or a["y"])
        ay_b = int(b.get("author_y") or b["y"])
        return abs(ay_a - ay_b) < 500

    def _merge_duplicate_posts(self, posts: list[dict]) -> list[dict]:
        """合并同帖重复识别（昵称 + 点赞区昵称回声）。"""
        merged: list[dict] = []
        for post in sorted(posts, key=lambda p: p["y"]):
            dup = next((m for m in merged if self._same_post(post, m)), None)
            if dup is None:
                merged.append(post)
                continue
            # 优先保留带时间戳、author_y 更靠上的条目
            prefer_new = (
                post.get("detect_method") == "timestamp"
                and dup.get("detect_method") != "timestamp"
            ) or int(post.get("author_y") or post["y"]) < int(
                dup.get("author_y") or dup["y"]
            )
            if prefer_new:
                merged[merged.index(dup)] = post
        return merged

    def _mark_post_acted(self, post: dict, acted_keys: set[str]) -> None:
        acted_keys.add(self._post_act_key(post))
        author = (post.get("author") or "").strip()
        author_y = int(post.get("author_y") or post["y"])
        if author:
            for bucket in range(-2, 3):
                acted_keys.add(f"{author}|{(author_y // 100) + bucket}")

    def _post_already_acted(self, post: dict, acted_keys: set[str]) -> bool:
        if self._post_act_key(post) in acted_keys:
            return True
        author = (post.get("author") or "").strip()
        if not author:
            return False
        author_y = int(post.get("author_y") or post["y"])
        for key in acted_keys:
            if not key.startswith(f"{author}|"):
                continue
            try:
                bucket = int(key.split("|", 1)[1])
                if abs(author_y - bucket * 100) < 450:
                    return True
            except (ValueError, IndexError):
                continue
        return False

    def _decide_interaction(self, post: dict) -> tuple[bool, bool]:
        """返回 (是否评论, 是否点赞)。大 V 帖必评必赞。"""
        if post.get("is_big_v"):
            return True, True
        return random.random() < 0.28, random.random() < 0.85

    def _interact_on_post(
        self,
        post: dict,
        *,
        comment_text: str = "",
        do_like: bool = True,
        do_comment: bool = False,
    ) -> dict:
        """对指定帖子点赞/评论，返回 {"liked": 0|1, "commented": 0|1}。"""
        result = {"liked": 0, "commented": 0}

        # 先评论再点赞：微信点赞后菜单自动关闭，无法再评
        if do_comment and comment_text:
            if self._comment_on_post(post, comment_text):
                result["commented"] = 1

        if do_like:
            if not self._open_menu(post):
                return result
            buttons = self._find_menu_buttons(self._menu_y(post))
            if "like" in buttons:
                self.d.click(*buttons["like"])
                result["liked"] = 1
                time.sleep(0.3)
            elif buttons.get("already_liked"):
                result["liked"] = 1

        return result

    def _comment_on_post(self, post: dict, text: str) -> bool:
        """打开菜单 → 点评论 → 输入 → 发送。"""
        if not text:
            return False
        if not self._open_menu(post):
            logger.warning(f"[{self.account_id}] 评论: 菜单未打开")
            return False

        buttons = self._find_menu_buttons(self._menu_y(post))
        if "comment" not in buttons:
            logger.warning(f"[{self.account_id}] 评论: 未找到评论按钮")
            return False

        self.d.click(*buttons["comment"])
        time.sleep(0.9)
        self._wait_moment_comment_ready()
        self._ime_input(text)
        time.sleep(0.4)

        if not self._click_moment_comment_send():
            logger.warning(
                f"[{self.account_id}] 评论: 发送失败 text='{text[:16]}'"
            )
            self._dismiss_comment_panel()
            return False

        logger.info(f"[{self.account_id}] 评论成功: '{text[:20]}'")
        time.sleep(0.8)
        return True

    def _wait_moment_comment_ready(self, timeout: float = 2.0) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            blob = self._ocr_region_text(0.52, 0.99)
            if any(k in blob for k in ("发送", "写评论", "评论")):
                return True
            time.sleep(0.25)
        return False

    def _click_moment_comment_send(self) -> bool:
        from config.device_profiles import get_extra
        from core.wechat_nav import click_ratio, ocr_find_and_click

        if ocr_find_and_click(
            self.d,
            self._get_ocr(),
            ["发送"],
            y_min_ratio=0.50,
            y_max_ratio=0.99,
            conf_min=0.22,
            enhance=self._enhance,
        ):
            time.sleep(0.5)
            if not self._moment_comment_composing():
                return True

        candidates = list(
            get_extra(self.d, "moments_comment_send_candidates", ()) or []
        )
        if not candidates:
            pt = get_extra(self.d, "moments_comment_send", (0.90, 0.93))
            candidates = [pt]

        for rx, ry in candidates:
            click_ratio(self.d, rx, ry)
            time.sleep(0.55)
            if not self._moment_comment_composing():
                return True

        return False

    def _moment_comment_composing(self) -> bool:
        blob = self._ocr_region_text(0.48, 0.99)
        return "发送" in blob

    def _dismiss_comment_panel(self):
        for _ in range(2):
            self.d.press("back")
            time.sleep(0.4)

    def _ocr_region_text(self, y_min_ratio: float, y_max_ratio: float) -> str:
        img = np.array(self.d.screenshot(format="pillow"))
        gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
        enhanced = cv2.cvtColor(self._enhance(gray), cv2.COLOR_GRAY2BGR)
        y0 = int(self.h * y_min_ratio)
        y1 = int(self.h * y_max_ratio)
        crop = enhanced[y0:y1, int(self.w * 0.05):int(self.w * 0.95)]
        parts = []
        for _, text, conf in self._get_ocr().readtext(crop):
            if conf > 0.2 and text:
                parts.append(text.strip())
        return " ".join(parts)

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
        """OCR 全屏扫描, 匹配时间戳/昵称锚点, 返回帖子列表按 Y 排序。"""
        blocks = self._ocr_blocks()
        posts = self._detect_posts(blocks, [])
        return [
            {
                "x": p["x"],
                "y": p["y"],
                "text": p["text"],
                "conf": p.get("conf", 0.0),
            }
            for p in posts
        ]

    # ================================================================
    # 菜单操作
    # ================================================================

    def _open_menu(self, post: dict) -> bool:
        """点击帖子的 '...' 并验证菜单弹出。支持多 X 比例 + 偏移重试。"""
        y_bases: list[int] = []
        if post.get("detect_method") == "author_anchor":
            author_y = int(post.get("author_y") or (post["y"] - 250))
            bottom_y = int(post.get("bottom_y") or (post["y"] - 28))
            y_bases = list(range(bottom_y + 8, bottom_y + 100, 10))
            y_bases.extend(range(author_y + 200, author_y + 360, 12))
        y_bases.append(int(post["y"]))
        # 去重保序
        seen: set[int] = set()
        y_bases = [y for y in y_bases if not (y in seen or seen.add(y))]

        x_bases: list[int] = []
        if post.get("detect_method") == "timestamp" and post.get("x"):
            x_bases.append(int(post["x"]) + int(self.w * 0.62))
        # 朋友圈 ... 通常在左侧时间戳行偏右
        x_bases.append(int(self.w * 0.25) + int(self.w * 0.62))
        for rx in self.DOTS_X_RATIOS:
            x_bases.append(int(self.w * rx))
        seen_x: set[int] = set()
        x_bases = [x for x in x_bases if not (x in seen_x or seen_x.add(x))]

        y_offsets = (0, -6, 6, -12, 12, -18, 18)
        attempt = 0

        for y_base in y_bases:
            for x_base in x_bases:
                for dy in y_offsets:
                    if attempt >= self.MENU_RETRY:
                        break
                    attempt += 1
                    x, y = x_base, y_base + dy
                    if y < 0 or y >= self.h:
                        continue
                    self.d.click(x, y)
                    time.sleep(0.55)
                    if self._check_menu_open(y):
                        post["_menu_y"] = y
                        logger.debug(f"[{self.account_id}] 菜单打开 @({x},{y})")
                        return True

        logger.warning(f"[{self.account_id}] 菜单未弹出 y={post.get('y')}")
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
            if conf > 0.15 and ("赞" in text or "评论" in text or "取消" in text):
                return True
        return False

    def _menu_y(self, post: dict) -> int:
        return int(post.get("_menu_y") or post["y"])

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
