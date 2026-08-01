"""
收藏夹浏览模块 — OCR + CLAHE + 拟人化操作
==========================================

进入微信"我"→"收藏"，OCR 扫描列表条目，随机点击查看详情并模拟滚动
阅读，返回后循环，持续指定时长。

::

    冷启动 → 我 Tab(0.875,0.955) → 收藏(0.50,0.352)
      │
      ├─ 40% 概率: 滚动列表 (70%向下, 30%回滚)
      │
      └─ 60% 概率: OCR 扫描条目 → 随机选一条 (70%偏前部/更新)
            │
            └─ 点击进入 → 模拟滚动 2~4 次 (1.5~4s/次) → back 返回

用法:
    from core.favorites_browser import FavoritesBrowser
    FavoritesBrowser(device).browse(duration_seconds=300)

WeChatControl 入口:
    wc.browse_favorites(duration_seconds=120)

剧本动作:
    Action(ActionType.BROWSE_FAVORITES, "15:00", "16:30", (120, 300))

定位策略:
    EasyOCR(ch_sim+en) → CLAHE 增强 → 过滤 "我的收藏"/"搜索" 标题栏

适配: Moto X70 Air Pro (1264x2780, Android 14)
"""

import time
import random
import cv2
import numpy as np

from utils.logger import get_logger

logger = get_logger("favorites_browser")


class FavoritesBrowser:
    """收藏夹浏览器 — 进入收藏 → 滚动列表 → 随机查看 → 返回循环。"""

    # 扫描区域配置
    ITEM_SCAN_Y_START_RATIO = 0.12   # 列表起始 (避开顶部标题栏)
    ITEM_SCAN_Y_END_RATIO = 0.82     # 列表结束 (避开底部Tab)
    SCROLL_PER_ITEM = (2, 4)         # 每个收藏项内滚动次数
    DWELL_PER_SCROLL = (1.5, 4.0)    # 每次滚动后停留秒数

    def __init__(self, d, account_id: str = ""):
        self.d = d
        self.account_id = account_id
        self.w, self.h = d.info['displayWidth'], d.info['displayHeight']
        self._ocr = None
        self._clahe = None

    # ================================================================
    # 公共接口
    # ================================================================

    def browse(self, duration_seconds: int = 120) -> int:
        """
        浏览收藏夹，持续指定时长。

        Args:
            duration_seconds: 浏览总时长（秒）

        Returns:
            查看的收藏条目数
        """
        logger.info(f"[{self.account_id}] 收藏夹浏览: {duration_seconds}s")
        items_viewed = 0

        try:
            self._navigate_to_favorites()

            start = time.time()
            while time.time() - start < duration_seconds:
                # 40% 概率先滚动列表
                if random.random() < 0.4:
                    self._scroll_list()
                    time.sleep(random.uniform(0.5, 1.5))
                else:
                    if self._view_random_item():
                        items_viewed += 1
                    else:
                        # 没找到条目，滚动再试
                        self._scroll_list()
                        time.sleep(random.uniform(0.8, 1.5))

            logger.info(f"[{self.account_id}] 收藏夹完成: 查看{items_viewed}项")
        except Exception as e:
            logger.error(f"[{self.account_id}] 收藏夹异常: {e}")

        return items_viewed

    # ================================================================
    # 导航
    # ================================================================

    def _navigate_to_favorites(self):
        """冷启动微信 → 我 Tab → 收藏。"""
        from core.wechat_nav import goto_tab, ocr_find_and_click, start_wechat

        logger.debug(f"[{self.account_id}] 导航到收藏夹")
        d, w, h = self.d, self.w, self.h
        start_wechat(d, wait=4.0, cold=True)
        goto_tab(d, "me")

        clicked = ocr_find_and_click(
            d,
            self._get_ocr(),
            ["收藏"],
            y_min_ratio=0.15,
            y_max_ratio=0.70,
            conf_min=0.35,
            enhance=self._enhance,
        )
        if not clicked:
            from config.device_profiles import get_nav
            rx, ry = get_nav(d, "favorites_entry", (0.50, 0.352))
            d.click(int(w * rx), int(h * ry))
        time.sleep(2.5)

    # ================================================================
    # 条目定位与查看
    # ================================================================

    def _find_items(self) -> list[dict]:
        """OCR 扫描收藏列表，返回 [{cx, cy, text}]。"""
        d, w, h = self.d, self.w, self.h

        img = np.array(d.screenshot(format="pillow"))
        gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
        enhanced = self._enhance(gray)
        enhanced_bgr = cv2.cvtColor(enhanced, cv2.COLOR_GRAY2BGR)

        y0 = int(h * self.ITEM_SCAN_Y_START_RATIO)
        y1 = int(h * self.ITEM_SCAN_Y_END_RATIO)
        x0 = int(w * 0.05)
        x1 = int(w * 0.95)

        crop = enhanced_bgr[y0:y1, x0:x1]
        results = self._get_ocr().readtext(crop)

        items = []
        for bbox, text, conf in results:
            if conf > 0.3 and len(text.strip()) >= 1:
                cx = int((bbox[0][0] + bbox[2][0]) / 2) + x0
                cy = int((bbox[0][1] + bbox[2][1]) / 2) + y0
                # 过滤标题栏
                if "我的收藏" in text or "搜索" in text:
                    continue
                items.append({"cx": cx, "cy": cy, "text": text.strip()[:30]})

        return items

    def _view_random_item(self) -> bool:
        """随机选一条 → 点击 → 浏览 → 返回。"""
        items = self._find_items()
        if len(items) < 2:
            logger.debug(f"[{self.account_id}] 收藏列表OCR条目不足: {len(items)}")
            return False

        # 70% 偏向前部（更新的收藏在前面）
        if random.random() < 0.7:
            idx = random.randint(0, len(items) * 2 // 3)
        else:
            idx = random.randint(0, len(items) - 1)

        item = items[idx]
        logger.debug(f"[{self.account_id}] 查看收藏: '{item['text']}'")
        self.d.click(item["cx"], item["cy"])
        time.sleep(2.0)

        # 模拟浏览
        self._browse_content()

        # 返回列表
        self.d.press("back")
        time.sleep(random.uniform(1.0, 2.0))
        return True

    # ================================================================
    # 浏览与滚动
    # ================================================================

    def _browse_content(self):
        """在收藏详情页模拟滚动浏览。"""
        d, w, h = self.d, self.w, self.h

        n_scrolls = random.randint(*self.SCROLL_PER_ITEM)
        for _ in range(n_scrolls):
            sx = w // 2 + random.randint(-30, 30)
            sy = int(h * 0.72) + random.randint(-20, 20)
            ex = w // 2 + random.randint(-30, 30)
            ey = int(h * 0.32) + random.randint(-20, 20)
            d.swipe(sx, sy, ex, ey, duration=random.uniform(0.15, 0.30))
            time.sleep(random.uniform(*self.DWELL_PER_SCROLL))

    def _scroll_list(self):
        """随机滚动收藏列表 (70%向下)。"""
        d, w, h = self.d, self.w, self.h

        if random.random() < 0.7:
            d.swipe(w // 2, int(h * 0.68), w // 2, int(h * 0.32),
                    duration=random.uniform(0.15, 0.30))
        else:
            d.swipe(w // 2, int(h * 0.32), w // 2, int(h * 0.68),
                    duration=random.uniform(0.15, 0.30))
        time.sleep(random.uniform(0.5, 1.5))

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
