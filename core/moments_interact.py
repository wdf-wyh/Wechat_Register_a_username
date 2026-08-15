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
      │     ├─ 定位「…」(同行 OCR/删除右侧/CV 暗色横条，再右侧比例兜底)
      │     │
      │     ├─ OCR 窄带识别菜单按钮:
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

    EasyOCR → 必须先锚定时间戳行（… 永远与时间戳同行、配图之下）
    → 仅在该行最右侧点「…」，点击前做「UI 底栏 vs 配图」纹理校验
    → 无时间戳则跳过互动（禁止按昵称/固定比例往配图区盲点）
    → 窄带 OCR 找「赞/取消」→「评论」点在赞与「…」之间

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
    MENU_RETRY = 24           # 点击 "..." 最大重试次数（坐标已优先近时间戳）
    MENU_EDGE_Y_RATIO = 0.88  # 帖子过靠底则先滑动，避免菜单点不开

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
    # 配图/桌面截图常见 OCR 碎片，不能当作者或正文主句
    _IMAGE_OCR_JUNK = (
        "美团", "抖音", "快手", "微信", "支付宝", "淘宝", "京东",
        "拼多多", "小红书", "微博", "腾讯", "设置", "相机", "相册",
        "时钟", "天气", "电话", "短信", "应用", "CS",
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
                      (0.91, 0.93, 0.89, 0.95))
        )
        self.DOTS_AFTER_DELETE_DX = int(
            get_extra(d, "moments_dots_after_delete_dx", 58) or 58
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

        buttons = self._find_menu_buttons(
            self._menu_y(post), post.get("_dots_xy")
        )
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
                buttons = self._find_menu_buttons(
                    self._menu_y(post), post.get("_dots_xy")
                )

                if buttons.get("already_liked"):
                    logger.debug(f"[{self.account_id}] 已赞跳过: {post['text']}")

                # 先评论再点赞（点赞后菜单会关闭，无法再评）
                # 有赞/取消锚即可点弹层评论（OCR 可能吃不到「评论」二字）
                can_comment = bool(
                    buttons.get("comment")
                    or buttons.get("menu_anchor")
                    or buttons.get("like")
                    or buttons.get("already_liked")
                )
                if can_comment and comment_text:
                    if self._comment_on_post(post, comment_text):
                        commented += 1
                    elif not self._is_on_moments():
                        self._recover_to_moments()
                        recovered += 1

                if "like" in buttons:
                    if not self._open_menu(post):
                        continue
                    buttons = self._find_menu_buttons(
                        self._menu_y(post), post.get("_dots_xy")
                    )
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
        failed_keys: set[str] = set()
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
                and not self._post_already_acted(p, failed_keys)
            ]
            posts.sort(key=lambda p: p["priority"], reverse=True)

            acted_this_round = False
            need_scroll_for_edge = False
            for post in posts:
                if interactions >= target_count:
                    break
                if (time.time() - start_time) >= max_duration:
                    break

                if not self._post_menu_reachable(post):
                    need_scroll_for_edge = True
                    continue

                # 每日互动以评论为主：非大 V 也尽量评，点赞作补充
                if post.get("is_big_v"):
                    do_comment, do_like = True, True
                else:
                    do_comment = True
                    do_like = random.random() < 0.75
                if not do_comment and not do_like:
                    continue

                comment_text = ""
                if do_comment:
                    content = (post.get("content") or "").strip()
                    author = (post.get("author") or "").strip()
                    image_jpeg = self.capture_post_jpeg(post)
                    logger.debug(
                        f"[{self.account_id}] 评论上下文 author={author!r} "
                        f"content={content[:60]!r} jpeg={len(image_jpeg)}B"
                    )
                    if comment_fn:
                        comment_text = self._invoke_comment_fn(
                            comment_fn, content, author, image_jpeg
                        )
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
                    # 菜单点不开时标记失败，避免同一帖反复烧掉时长
                    self._mark_post_acted(post, failed_keys)
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
            if not acted_this_round or scroll_round >= max_scroll_rounds or need_scroll_for_edge:
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
            author_y = ts.get("author_y")
            if author and author_y is None:
                for b in blocks:
                    if b["text"] == author and b["y"] < ts["y"]:
                        author_y = b["y"]
                        break
            content = self._extract_content(
                blocks,
                {**ts, "author_y": author_y} if author_y is not None else ts,
                author,
            )
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
            logger.debug(
                f"[{self.account_id}] 帖子抽取 author={author!r} "
                f"content={content[:40]!r} ts={ts.get('text')!r}"
            )
            post_out = {
                **ts,
                "author": author,
                "content": content,
                "is_fresh": is_fresh,
                "age_minutes": age_min,
                "is_big_v": is_big_v,
                "priority": priority,
            }
            if author_y is not None:
                post_out["author_y"] = int(author_y)
            posts.append(post_out)
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

    def _is_image_ocr_junk(self, text: str) -> bool:
        """配图/桌面截图里的短标签，不能当昵称或正文主句。"""
        t = (text or "").strip()
        if not t:
            return True
        if t in self._IMAGE_OCR_JUNK:
            return True
        # 纯符号/短代号（如 -4-11、V12）
        if re.fullmatch(r"[\W\d_\-]{1,8}", t):
            return True
        if len(t) <= 4 and not any("\u4e00" <= c <= "\u9fff" for c in t):
            return True
        return False

    def _extract_author(self, blocks: list[dict], ts: dict) -> str:
        """取帖子区域最上方的昵称（头像右侧）；向上搜索足够远以越过配图。"""
        y_ts = ts["y"]
        lookback = max(900, int(self.h * 0.38))
        candidates = []
        for block in blocks:
            if block["y"] >= y_ts - 8:
                continue
            if block["y"] < y_ts - lookback:
                continue
            if block["x"] < self.w * 0.12:
                continue
            if block["x"] > self.w * 0.62:
                continue
            text = block["text"]
            if self._is_noise_text(text) or self._is_image_ocr_junk(text):
                continue
            if not self._looks_like_nickname(text):
                continue
            candidates.append(block)
        if not candidates:
            return ""
        # 昵称在帖子顶部：取最高（y 最小）的候选
        candidates.sort(key=lambda b: (b["y"], b["x"]))
        return candidates[0]["text"]

    def _post_region_text(self, blocks: list[dict], ts: dict) -> str:
        """帖子时间戳上方区域的全部 OCR 文本（用于大 V 兜底匹配）。"""
        y_lo = ts["y"] - max(900, int(self.h * 0.38))
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
        """取昵称下方、时间戳上方的正文；过滤配图 OCR 噪声。"""
        y_hi = ts["y"] - 8
        lookback = max(900, int(self.h * 0.38))
        y_lo = ts["y"] - lookback

        author_y = None
        if author:
            author_blocks = [
                b for b in blocks
                if b["text"] == author and b["y"] < ts["y"] - 8
            ]
            if author_blocks:
                # 取最靠上的作者行，正文从其下方开始
                author_y = min(b["y"] for b in author_blocks)
                y_lo = max(y_lo, author_y + 12)
            elif ts.get("author_y"):
                author_y = int(ts["author_y"])
                y_lo = max(y_lo, author_y + 12)

        if y_lo >= y_hi:
            y_lo = max(0, y_hi - 200)

        parts: list[tuple[int, str]] = []
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
            if self._is_timestamp_text(text):
                continue
            if self._is_image_ocr_junk(text):
                continue
            parts.append((block["y"], text))

        parts.sort(key=lambda x: x[0])
        # 优先保留含中文且较长的句子；短碎片垫后
        scored = []
        for y, t in parts:
            cn = sum(1 for c in t if "\u4e00" <= c <= "\u9fff")
            scored.append((0 if cn >= 4 or len(t) >= 8 else 1, y, t))
        scored.sort()
        content = " ".join(t for _, _, t in scored)
        return content[:200]

    def _post_menu_reachable(self, post: dict) -> bool:
        """过靠屏幕底部的帖子先滑动，避免 ... 菜单点不开。"""
        y = int(post.get("y") or 0)
        return 0 < y < int(self.h * self.MENU_EDGE_Y_RATIO)

    def capture_post_jpeg(self, post: dict, quality: int = 78) -> bytes:
        """
        截取单条朋友圈帖子区域 JPEG，供 Vision 识图评论。

        裁剪范围：昵称下方附近 → 时间戳行；左右去掉头像列与边缘。
        """
        try:
            img = np.array(self.d.screenshot(format="pillow"))
            h, w = img.shape[:2]
            y_ts = int(post.get("y") or (h // 2))
            author_y = post.get("author_y")
            if author_y is not None:
                y0 = max(int(h * 0.08), int(author_y) - 10)
            else:
                y0 = max(int(h * 0.08), y_ts - max(720, int(h * 0.32)))
            y1 = min(h - 8, y_ts + 20)
            if y1 - y0 < 120:
                y0 = max(int(h * 0.08), y1 - 320)
            x0 = int(w * 0.10)
            x1 = int(w * 0.96)
            crop = img[y0:y1, x0:x1]
            if crop.size == 0:
                return b""
            # 过宽时缩小，控制 Vision 体积
            ch, cw = crop.shape[:2]
            max_w = 720
            if cw > max_w:
                scale = max_w / cw
                crop = cv2.resize(
                    crop, (max_w, max(1, int(ch * scale))),
                    interpolation=cv2.INTER_AREA,
                )
            bgr = cv2.cvtColor(crop, cv2.COLOR_RGB2BGR)
            ok, buf = cv2.imencode(
                ".jpg", bgr, [cv2.IMWRITE_JPEG_QUALITY, int(quality)]
            )
            return buf.tobytes() if ok else b""
        except Exception as e:
            logger.debug(f"[{self.account_id}] 朋友圈帖子截图失败: {e}")
            return b""

    def _invoke_comment_fn(
        self,
        comment_fn: Callable[..., str],
        content: str,
        author: str,
        image_jpeg: bytes,
    ) -> str:
        """调用 comment_fn；兼容 (content, author) / (content,) 旧签名。"""
        try:
            return (comment_fn(content, author, image_jpeg) or "").strip()
        except TypeError:
            try:
                return (comment_fn(content, author) or "").strip()
            except TypeError:
                return (comment_fn(content) or "").strip()

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
            buttons = self._find_menu_buttons(
                self._menu_y(post), post.get("_dots_xy")
            )
            if "like" in buttons:
                self.d.click(*buttons["like"])
                result["liked"] = 1
                time.sleep(0.3)
            elif buttons.get("already_liked"):
                result["liked"] = 1

        return result

    def _comment_on_post(self, post: dict, text: str) -> bool:
        """打开菜单 → 点弹层「评论」→ 输入 → 发送。"""
        if not text:
            return False
        if not self._open_menu(post):
            logger.warning(f"[{self.account_id}] 评论: 菜单未打开")
            return False

        buttons = self._find_menu_buttons(
            self._menu_y(post), post.get("_dots_xy")
        )
        if not self._click_popup_comment(post, buttons):
            logger.warning(f"[{self.account_id}] 评论: 未点中弹层评论按钮")
            return False

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

    def _click_popup_comment(self, post: dict, buttons: dict) -> bool:
        """
        点击 ... 弹层上的「评论」。

        布局多为 [赞|评论]… ；X 偏右会点到「…」把弹层关掉。
        以赞为左界、「…」为右界，Y 锁同行。
        """
        menu_y = self._menu_y(post)
        dots_xy = post.get("_dots_xy")
        candidates = self._comment_click_candidates(buttons, menu_y, dots_xy)
        if not candidates:
            logger.warning(f"[{self.account_id}] 评论: 无弹层评论坐标")
            return False

        for i, (cx, cy) in enumerate(candidates):
            if i > 0:
                # 误点「…」会关弹层；勿乱按 back，直接重开
                time.sleep(0.25)
                if not self._is_on_moments():
                    self._recover_to_moments()
                if not self._open_menu(post):
                    continue
                buttons = self._find_menu_buttons(
                    self._menu_y(post), post.get("_dots_xy")
                )
                dots_xy = post.get("_dots_xy")
                retry = self._comment_click_candidates(
                    buttons, self._menu_y(post), dots_xy
                )
                if not retry:
                    continue
                cx, cy = retry[min(i, len(retry) - 1)]

            logger.debug(
                f"[{self.account_id}] 点击弹层评论 @({cx},{cy}) try={i + 1} "
                f"dots={post.get('_dots_xy')}"
            )
            self.d.click(cx, cy)
            time.sleep(0.7)
            if self._wait_moment_comment_ready(1.6):
                return True
            # 若仍看到赞/评论，说明没点到「…」但也没进输入，换下一候选前先确认弹层还在
            if not self._check_menu_open(self._menu_y(post)):
                # 已被「…」关掉，下一轮会 _open_menu
                pass
        return False

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

    _DOTS_TEXT_RE = re.compile(r"^[\.·•。…]{2,4}$")

    def _merge_dots_click_points(
        self,
        y_ts: int,
        ts_x: int,
        ocr_hits: list[dict] | None = None,
        cv_hits: list[tuple[int, int, float]] | None = None,
    ) -> list[tuple[int, int]]:
        """
        合并「…」点击点（纯逻辑）。

        所有候选 Y 强制=时间戳行：配图再多也在时间戳上方，不能跟 OCR/CV 的 Y 漂移。
        优先级: OCR「…」→ 删除右侧 → CV → 右侧比例（仍须过纹理校验）。
        """
        out: list[tuple[int, int]] = []
        seen: set[int] = set()

        def add(x: int, _y: int = 0):
            x = int(x)
            y = int(y_ts)  # 锁死时间戳行，杜绝点进上方配图
            if x < int(self.w * 0.72) or x >= self.w - 4:
                return
            if y < int(self.h * 0.12) or y >= int(self.h * self.MENU_EDGE_Y_RATIO):
                return
            if ts_x + 50 <= x <= ts_x + 220:
                return
            key = x // 6
            if key in seen:
                return
            seen.add(key)
            out.append((x, y))

        delete_pt = None
        for h in ocr_hits or ():
            t = (h.get("text") or "").strip().replace(" ", "")
            if self._DOTS_TEXT_RE.match(t) or t in ("...", "..", "···", "•••", "··"):
                add(h["x"])
            if "删除" in t:
                delete_pt = (int(h["x"]), int(h["y"]))

        if delete_pt:
            add(delete_pt[0] + self.DOTS_AFTER_DELETE_DX)
            add(delete_pt[0] + self.DOTS_AFTER_DELETE_DX + 24)

        for cx, _cy, _score in cv_hits or ():
            add(cx)

        for rx in self.DOTS_X_RATIOS:
            add(int(self.w * float(rx)))

        return out

    def _is_ui_chrome_at(self, gray, x: int, y: int, half: int = 16) -> bool:
        """
        判断 (x,y) 是否像朋友圈时间戳行 UI（浅底），而非配图。

        「…」本身是小暗点，允许局部少量暗像素；配图则中位数偏低或暗区占比高。
        """
        h, w = gray.shape[:2]
        x0 = max(0, int(x) - half)
        x1 = min(w, int(x) + half)
        y0 = max(0, int(y) - half)
        y1 = min(h, int(y) + half)
        if x1 - x0 < 8 or y1 - y0 < 8:
            return False
        patch = gray[y0:y1, x0:x1].astype(np.float32)
        median = float(np.median(patch))
        # 时间戳行背景浅；配图中位数通常更低或花
        if median < 185:
            return False
        dark_frac = float(np.mean(patch < 120))
        # 「…」暗点占比很小；大块暗区=配图
        if dark_frac > 0.30:
            return False
        edges = cv2.Canny(patch.astype(np.uint8), 60, 140)
        edge_ratio = float(np.count_nonzero(edges)) / float(edges.size)
        # 照片边缘密；UI+小图标边缘稀疏
        if edge_ratio > 0.18 and dark_frac > 0.12:
            return False
        return True

    def _relocate_to_timestamp_row(self, post: dict) -> bool:
        """
        把互动锚点校正到真实时间戳行。

        微信不变式：… 与时间戳同行，且整行在全部配图之下。
        无时间戳时禁止用昵称估算 Y（多图会落在配图上）。
        """
        if self._is_timestamp_text(post.get("text") or ""):
            return True

        author_y = int(post.get("author_y") or max(0, int(post.get("y") or 0) - 500))
        y_lo = author_y + 30
        y_hi = min(self.h - 30, author_y + max(900, int(self.h * 0.42)))

        try:
            img = np.array(self.d.screenshot(format="pillow"))
            gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
            enhanced = cv2.cvtColor(self._enhance(gray), cv2.COLOR_GRAY2BGR)
            crop = enhanced[y_lo:y_hi, int(self.w * 0.10):int(self.w * 0.70)]
            results = self._get_ocr().readtext(crop)
        except Exception as e:
            logger.debug(f"[{self.account_id}] 重定位时间戳失败: {e}")
            return False

        best = None
        for bbox, text, conf in results:
            t = (text or "").strip()
            if conf < 0.18 or not t:
                continue
            if not self._is_timestamp_text(t):
                continue
            cx = int((bbox[0][0] + bbox[2][0]) / 2) + int(self.w * 0.10)
            cy = int((bbox[0][1] + bbox[2][1]) / 2) + y_lo
            if cx > self.w * 0.70:
                continue
            # 取最靠下的时间戳（更贴近 … 行；上方可能误扫到别的）
            if best is None or cy > best["y"]:
                best = {"x": cx, "y": cy, "text": t, "conf": float(conf)}

        if not best:
            return False

        post["x"] = best["x"]
        post["y"] = best["y"]
        post["text"] = best["text"]
        post["conf"] = best["conf"]
        post["detect_method"] = "timestamp"
        logger.debug(
            f"[{self.account_id}] 时间戳重定位 y={best['y']} text={best['text']!r}"
        )
        return True

    def _dots_click_candidates(self, post: dict) -> list[tuple[int, int]]:
        """
        仅在时间戳行定位「…」。

        流程：OCR/CV/比例出候选 → Y 锁时间戳 → 纹理校验丢掉配图像素点。
        """
        y_ts = int(post.get("y") or 0)
        ts_x = int(post.get("x") or (self.w * 0.20))
        try:
            img = np.array(self.d.screenshot(format="pillow"))
            gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
        except Exception:
            return self._merge_dots_click_points(y_ts, ts_x)

        try:
            ocr_hits = self._ocr_timestamp_row(y_ts)
        except Exception:
            ocr_hits = []
        try:
            cv_hits = self._cv_score_dots_blobs(gray, y_ts, ts_x)
        except Exception:
            cv_hits = []

        raw = self._merge_dots_click_points(y_ts, ts_x, ocr_hits, cv_hits)
        safe = [(x, y_ts) for x, _y in raw if self._is_ui_chrome_at(gray, x, y_ts)]
        if safe:
            return safe

        # 比例点全被纹理否决时：在最右栏沿时间戳行滑探浅底区域
        probed: list[tuple[int, int]] = []
        for rx in (0.93, 0.91, 0.95, 0.89, 0.87):
            x = int(self.w * rx)
            if self._is_ui_chrome_at(gray, x, y_ts):
                probed.append((x, y_ts))
        return probed

    def _ocr_timestamp_row(self, y_ts: int) -> list[dict]:
        """OCR 时间戳所在窄行（含删除/…）。"""
        img = np.array(self.d.screenshot(format="pillow"))
        gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
        enhanced = cv2.cvtColor(self._enhance(gray), cv2.COLOR_GRAY2BGR)
        # 极窄行：只吃时间戳带，避免吃到上方多图底边
        margin = 22
        y0 = max(0, int(y_ts) - margin)
        y1 = min(self.h, int(y_ts) + margin)
        x0 = int(self.w * 0.12)
        crop = enhanced[y0:y1, x0:self.w]
        hits = []
        for bbox, text, conf in self._get_ocr().readtext(crop):
            t = (text or "").strip()
            if not t or conf < 0.15:
                continue
            cx = int((bbox[0][0] + bbox[2][0]) / 2) + x0
            cy = int((bbox[0][1] + bbox[2][1]) / 2) + y0
            hits.append({"text": t, "x": cx, "y": cy, "conf": float(conf)})
        return hits

    def _cv_find_dots_on_row(
        self, y_ts: int, ts_x: int
    ) -> list[tuple[int, int, float]]:
        """在时间戳同行右侧用 CV 找「…」图标，返回 (x,y,score)。"""
        img = np.array(self.d.screenshot(format="pillow"))
        gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
        return self._cv_score_dots_blobs(gray, y_ts, ts_x)

    def _cv_score_dots_blobs(
        self, gray, y_ts: int, ts_x: int
    ) -> list[tuple[int, int, float]]:
        """
        纯图像：时间戳同行最右栏找「…」。
        多图右格暗块会被纹理校验继续过滤。
        """
        h, w = gray.shape[:2]
        y0 = max(0, int(y_ts) - 10)
        y1 = min(h, int(y_ts) + 18)
        x0 = max(int(ts_x) + 230, int(w * 0.84))
        x0 = min(x0, w - 50)
        if y1 <= y0 or w - x0 < 30:
            return []

        band = gray[y0:y1, x0:w]
        _, th = cv2.threshold(band, 155, 255, cv2.THRESH_BINARY_INV)
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (11, 3))
        merged = cv2.morphologyEx(th, cv2.MORPH_CLOSE, kernel)
        contours, _ = cv2.findContours(
            merged, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )

        hits: list[tuple[int, int, float]] = []
        for cnt in contours:
            x, y, cw, ch = cv2.boundingRect(cnt)
            if not (10 <= cw <= 100 and 3 <= ch <= 34):
                continue
            aspect = cw / max(ch, 1)
            if aspect < 1.15:
                continue
            cx = x0 + x + cw // 2
            cy = y0 + y + ch // 2
            if cx < int(w * 0.84):
                continue
            if not self._is_ui_chrome_at(gray, cx, int(y_ts)):
                continue
            y_score = max(0.0, 1.0 - abs(cy - y_ts) / 22.0)
            x_score = cx / float(w)
            score = aspect * 0.30 + y_score * 0.40 + x_score * 0.50
            hits.append((cx, int(y_ts), float(score)))

        hits.sort(key=lambda t: -t[2])
        return hits[:6]

    def _open_menu(self, post: dict) -> bool:
        """
        点击帖子「…」打开赞/评论弹层。

        不依赖配图布局：先锚定时间戳行，再在该行浅底区域点「…」。
        """
        if not self._post_menu_reachable(post):
            logger.debug(
                f"[{self.account_id}] 帖子过靠底，跳过菜单 y={post.get('y')}"
            )
            return False

        if not self._relocate_to_timestamp_row(post):
            logger.warning(
                f"[{self.account_id}] 无时间戳行，跳过「…」"
                f"（避免点进配图） y≈{post.get('y')} "
                f"author={post.get('author')!r}"
            )
            return False

        if not self._post_menu_reachable(post):
            return False

        candidates = self._dots_click_candidates(post)
        if not candidates:
            logger.warning(
                f"[{self.account_id}] 时间戳行未找到可点的「…」"
                f"（疑似落在配图） y={post.get('y')}"
            )
            return False

        # 只允许贴着时间戳行微调，禁止大幅上移进配图
        micro = (0, -3, 3, -6, 6)
        attempt = 0
        for x0, y0 in candidates:
            for dy in micro:
                if attempt >= self.MENU_RETRY:
                    break
                attempt += 1
                x, y = x0, y0 + dy
                if y < int(self.h * 0.12) or y >= int(self.h * self.MENU_EDGE_Y_RATIO):
                    continue
                self.d.click(x, y)
                time.sleep(0.4)
                if self._check_menu_open(y0):
                    post["_menu_y"] = y0
                    post["_dots_xy"] = (x, y)
                    logger.debug(f"[{self.account_id}] 「…」打开菜单 @({x},{y})")
                    return True
                if not self._is_on_moments():
                    self._recover_to_moments()

        logger.warning(f"[{self.account_id}] 菜单未弹出 y={post.get('y')}")
        return False

    def _dots_x_candidates(self, post: dict) -> list[int]:
        """右侧比例兜底 X（无截屏）；完整定位请用 _dots_click_candidates。"""
        y_ts = int(post.get("y") or 0)
        ts_x = int(post.get("x") or (self.w * 0.20))
        return [x for x, _y in self._merge_dots_click_points(y_ts, ts_x)]

    def _menu_ocr_hits(
        self,
        y_ts: int,
        y_margin: int | None = None,
        dots_xy: tuple[int, int] | None = None,
    ) -> list[dict]:
        """
        OCR 弹层窄带。多图时配图底边易污染上沿，Y 少向上、X 靠「…」左侧走廊。
        """
        margin = min(self.BAND_Y_MARGIN, 48) if y_margin is None else y_margin
        img = np.array(self.d.screenshot(format="pillow"))
        gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
        enhanced = cv2.cvtColor(self._enhance(gray), cv2.COLOR_GRAY2BGR)

        # 少吃配图底边：上沿窄、下沿略宽
        y0 = max(0, y_ts - max(16, margin // 2))
        y1 = min(self.h, y_ts + margin)
        if dots_xy:
            dots_x = int(dots_xy[0])
            x0 = max(int(self.w * 0.30), dots_x - int(self.w * 0.42))
            x1 = min(self.w, dots_x + 8)
        else:
            x0 = int(self.w * 0.45)
            x1 = self.w
        crop = enhanced[y0:y1, x0:x1]
        results = self._get_ocr().readtext(crop)

        hits = []
        for bbox, text, conf in results:
            t = (text or "").strip()
            if not t:
                continue
            cx = int((bbox[0][0] + bbox[2][0]) / 2) + x0
            cy = int((bbox[0][1] + bbox[2][1]) / 2) + y0
            hits.append({
                "text": t,
                "x": cx,
                "y": cy,
                "conf": float(conf),
            })
        return hits

    @staticmethod
    def _is_menu_comment_label(text: str) -> bool:
        """菜单上的「评论」短标签；排除正文下评论内容里夹带的「评论」。"""
        t = (text or "").strip()
        if not t or "评论" not in t:
            return False
        # 菜单按钮通常就是「评论」两字，或 OCR 粘连成很短的片段
        if t in ("评论", "写评论", "評論"):
            return True
        return len(t) <= 4 and t.startswith("评")

    def _menu_open_from_hits(self, hits: list[dict], y_ts: int) -> bool:
        """
        赞/评论菜单必须成对出现。

        单独「取消」可能是删除确认框；正文评论里也可能扫到「评论」字样。
        """
        band = [
            h for h in hits
            if h.get("conf", 0) > 0.15
            and abs(int(h["y"]) - int(y_ts)) <= self.BAND_Y_MARGIN * 2
        ]
        has_like_side = False
        has_comment = False
        for h in band:
            t = h["text"]
            if self._is_menu_comment_label(t):
                has_comment = True
            if "取消" in t and "评论" not in t:
                has_like_side = True
            elif "赞" in t and "评论" not in t:
                has_like_side = True
        return has_like_side and has_comment

    def _parse_menu_button_hits(
        self,
        hits: list[dict],
        y_ts: int,
        dots_xy: tuple[int, int] | None = None,
    ) -> dict:
        """
        从 OCR hits 解析菜单按钮。
        keys: "like" | "comment" | "already_liked" | "menu_anchor"

        多图时配图 OCR 会在左/中部冒出「赞/评论」杂字，必须限制在「…」左侧弹层走廊。
        """
        if dots_xy:
            dots_x = int(dots_xy[0])
        else:
            dots_x = int(self.w * min(float(r) for r in self.DOTS_X_RATIOS))
        x_hi = dots_x - 40
        x_lo = max(int(self.w * 0.40), dots_x - int(self.w * 0.40))

        row = [
            h for h in hits
            if h.get("conf", 0) >= 0.2
            and abs(int(h["y"]) - int(y_ts)) <= min(self.BAND_Y_MARGIN, 40)
            and x_lo <= int(h["x"]) <= x_hi
        ]

        like_btns: list[tuple[int, int]] = []
        menu_anchors: list[tuple[int, int]] = []  # 赞或取消
        comment_cands: list[tuple] = []  # (pri, dist_key, x, y)
        already = False

        for h in row:
            t = h["text"]
            cx, cy = int(h["x"]), int(h["y"])
            if "取消" in t and "评论" not in t:
                already = True
                menu_anchors.append((cx, cy))
            elif "赞" in t and "评论" not in t:
                like_btns.append((cx, cy))
                menu_anchors.append((cx, cy))
            if "评论" not in t:
                continue
            if not self._is_menu_comment_label(t):
                continue
            comment_cands.append((0 if len(t) <= 2 else 1, -cx, cx, cy))

        buttons: dict = {}
        if already:
            buttons["already_liked"] = True
        if like_btns:
            like_btns.sort(key=lambda p: p[0], reverse=True)
            buttons["like"] = like_btns[0]
        if menu_anchors:
            menu_anchors.sort(key=lambda p: p[0], reverse=True)
            buttons["menu_anchor"] = menu_anchors[0]

        if comment_cands and menu_anchors:
            lx, ly = menu_anchors[0]
            near = [
                (abs(cy - ly), abs(cx - lx), cx, cy)
                for _, _, cx, cy in comment_cands
                if abs(cy - ly) <= 35
                and 30 <= abs(cx - lx) <= int(self.w * 0.35)
                and cx < x_hi
            ]
            if near:
                near.sort()
                buttons["comment"] = (near[0][2], near[0][3])
        elif comment_cands and not menu_anchors:
            same_row = [
                (pri, nx, cx, cy)
                for pri, nx, cx, cy in comment_cands
                if abs(cy - int(y_ts)) <= 35 and cx < x_hi
            ]
            if same_row:
                same_row.sort()
                buttons["comment"] = (same_row[0][2], same_row[0][3])

        return buttons

    def _comment_offset_dx_list(self) -> list[int]:
        """弹层里「评论」相对「赞/取消」的水平偏移候选（像素）。偏大易点到「…」。"""
        from config.device_profiles import get_extra
        raw = get_extra(self.d, "moments_menu_comment_dx_from_like", None)
        if isinstance(raw, (list, tuple)) and raw:
            return [int(x) for x in raw]
        if isinstance(raw, (int, float)):
            dx = int(raw)
            return [dx, dx - 20, dx + 20, -dx]
        # 保守偏小：评论紧挨赞右侧；过大的正偏移会点到「…」关弹层
        return [85, 65, 105, 125, -85, -110]

    def _comment_click_point(
        self,
        buttons: dict,
        menu_y: int,
        dots_xy: tuple[int, int] | None = None,
    ) -> tuple[int, int] | None:
        """解析弹层「评论」点击点：Y 锁在赞行，X 避开「…」。"""
        cands = self._comment_click_candidates(buttons, menu_y, dots_xy)
        return cands[0] if cands else None

    def _comment_click_candidates(
        self,
        buttons: dict,
        menu_y: int,
        dots_xy: tuple[int, int] | None = None,
    ) -> list[tuple[int, int]]:
        """
        弹层「评论」点击候选。

        微信布局: [赞|评论]…  —— 「评论」在赞与「…」之间；
        点到「…」会关闭弹层。
        """
        out: list[tuple[int, int]] = []
        seen: set[tuple[int, int]] = set()

        # 「…」右界：优先实测点击点，否则用机型右侧比例最左值作软上界
        if dots_xy:
            dots_x = int(dots_xy[0])
        else:
            dots_x = int(self.w * min(float(r) for r in self.DOTS_X_RATIOS))
        # 必须离「…」足够远，否则等同再点一次两点
        dots_limit = dots_x - 52

        def add(x: int, y: int):
            x = max(8, min(self.w - 8, int(x)))
            y = max(8, min(self.h - 8, int(y)))
            if x >= dots_limit:
                return
            key = (x // 4 * 4, y // 4 * 4)
            if key in seen:
                return
            seen.add(key)
            out.append((x, y))

        anchor = buttons.get("menu_anchor") or buttons.get("like")
        ocr_c = buttons.get("comment")

        if anchor:
            ax, ay = int(anchor[0]), int(anchor[1])
            # 1) 有「…」与赞：优先取两者之间的几何位置（最稳）
            span = dots_x - ax
            if span > 90:
                for ratio in (0.55, 0.42, 0.68, 0.35):
                    add(ax + int(span * ratio), ay)

            # 2) OCR「评论」：必须在赞与「…」之间
            if ocr_c:
                cx = int(ocr_c[0])
                if ax + 25 <= cx <= dots_limit and abs(cx - ax) <= int(self.w * 0.42):
                    # OCR 插入最前
                    key = (cx // 4 * 4, ay // 4 * 4)
                    if key not in seen:
                        seen.add(key)
                        out.insert(0, (cx, ay))

            # 3) 小幅水平偏移兜底（过滤会碰到「…」的）
            for dx in self._comment_offset_dx_list():
                add(ax + dx, ay)
        elif ocr_c:
            cx, cy = int(ocr_c[0]), int(ocr_c[1])
            if cx < dots_limit:
                add(cx, int(menu_y) if abs(cy - menu_y) > 25 else cy)

        return out

    def _check_menu_open(self, y_ts: int) -> bool:
        """OCR 窄带检查赞/评论菜单是否展开（须成对，防误判删除框）。"""
        hits = self._menu_ocr_hits(y_ts, y_margin=self.BAND_Y_MARGIN)
        return self._menu_open_from_hits(hits, y_ts)

    def _menu_y(self, post: dict) -> int:
        return int(post.get("_menu_y") or post["y"])

    def _find_menu_buttons(
        self,
        y_ts: int,
        dots_xy: tuple[int, int] | None = None,
    ) -> dict:
        """
        OCR 窄带扫描菜单, 返回按钮坐标字典。
        keys: "like" | "comment" | "already_liked" | "menu_anchor"
        """
        hits = self._menu_ocr_hits(
            y_ts, y_margin=min(self.BAND_Y_MARGIN, 48), dots_xy=dots_xy
        )
        return self._parse_menu_button_hits(hits, y_ts, dots_xy)

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
