"""
全局搜索模块 — OpenCV + ADBKeyboard IME。

工作流:
  1. 冷启动微信 → 确保在"微信"Tab
  2. 点击右上角搜索图标（放大镜，紧挨"+"按钮）
  3. 验证搜索页打开（页面差异对比）
  4. ADBKeyboard IME 注入关键词 → 键盘右下角绿色「搜索」（失败再 Enter）

使用方式:
    from core.search_helper import SearchHelper
    helper = SearchHelper(device)
    helper.search("天气预报")
"""

import time
import cv2
import numpy as np

from utils.logger import get_logger

logger = get_logger("search_helper")


class SearchHelper:
    """微信全局搜索器。"""

    def __init__(self, d, account_id: str = ""):
        self.d = d
        self.account_id = account_id
        self.w, self.h = d.info['displayWidth'], d.info['displayHeight']
        from core.wechat_nav import search_icon_candidates_for
        self.SEARCH_CANDIDATES = search_icon_candidates_for(d)

    # ================================================================
    # 公共接口
    # ================================================================

    def open_search(self) -> bool:
        """
        打开微信全局搜索页面。

        Returns:
            是否成功打开搜索页
        """
        logger.info(f"[{self.account_id}] 打开搜索页")

        try:
            self._goto_wechat_home()
            self._click_search_icon()
            logger.info(f"[{self.account_id}] 搜索页已打开")
            return True
        except Exception as e:
            logger.error(f"[{self.account_id}] 打开搜索页失败: {e}")
            return False

    def search(self, keyword: str) -> bool:
        """
        打开搜索页并搜索关键词。

        Args:
            keyword: 搜索关键词

        Returns:
            是否成功
        """
        logger.info(f"[{self.account_id}] 搜索: '{keyword}'")

        if not self.open_search():
            return False

        try:
            self._input_keyword(keyword)
            self._press_search()
            logger.info(f"[{self.account_id}] 搜索完成: '{keyword}'")
            return True
        except Exception as e:
            logger.error(f"[{self.account_id}] 搜索失败: {e}")
            return False

    # ================================================================
    # 导航
    # ================================================================

    def _goto_wechat_home(self):
        """冷启动微信 → 微信 Tab。"""
        from core.wechat_nav import goto_tab, start_wechat

        logger.debug(f"[{self.account_id}] 导航到微信首页")
        start_wechat(self.d, wait=4.0, cold=True)
        goto_tab(self.d, "wechat")

    # ================================================================
    # 点击搜索图标
    # ================================================================

    def _click_search_icon(self):
        """多位置重试点击搜索图标，用页面差异验证。"""
        from core.wechat_nav import open_search

        logger.debug(f"[{self.account_id}] 点击搜索图标")
        if not open_search(self.d):
            raise RuntimeError("所有位置均未能打开搜索页")

    # ================================================================
    # 输入关键词 + 搜索
    # ================================================================

    def _input_keyword(self, keyword: str):
        """ADBKeyboard IME 注入搜索关键词。"""
        logger.debug(f"[{self.account_id}] 输入关键词: '{keyword}'")
        d, w, h = self.d, self.w, self.h

        # 点击搜索输入框（搜索页顶部居中）
        d.click(int(w * 0.50), int(h * 0.045))
        time.sleep(0.8)

        # IME 注入
        try:
            d.set_input_ime(True)
            time.sleep(0.3)
            d.send_keys(keyword)
            time.sleep(0.8)
            d.set_input_ime(False)
        except Exception as e:
            logger.warning(f"[{self.account_id}] IME失败: {e}，尝试shell")
            try:
                d.shell(f"input text {keyword}")
            except Exception:
                pass

    def _press_search(self):
        """优先点键盘右下角绿色「搜索」，失败再 Enter / OCR。"""
        time.sleep(0.35)
        if self._click_keyboard_search_green():
            time.sleep(2.0)
            return
        try:
            self.d.press("enter")
            time.sleep(2.0)
            return
        except Exception:
            pass
        # OCR 兜底：键盘区「搜索」
        try:
            from core.wechat_nav import ocr_find_and_click
            from utils.ocr_utils import create_easyocr_reader

            reader = create_easyocr_reader()
            if ocr_find_and_click(
                self.d,
                reader,
                ["搜索"],
                y_min_ratio=0.78,
                y_max_ratio=0.99,
                x_min_ratio=0.55,
                x_max_ratio=1.0,
                conf_min=0.30,
                exact=True,
                post_click_sleep=1.5,
            ):
                time.sleep(0.5)
                return
        except Exception as e:
            logger.debug(f"[{self.account_id}] OCR 点键盘搜索失败: {e}")
        # 坐标兜底：键盘右下角
        try:
            self.d.click(int(self.w * 0.90), int(self.h * 0.94))
            time.sleep(2.0)
        except Exception:
            pass

    def _click_keyboard_search_green(self) -> bool:
        """点击软键盘右下角绿色搜索键。"""
        try:
            shot = self.d.screenshot(format="opencv")
            if shot is None:
                return False
            h, w = shot.shape[:2]
            hsv = cv2.cvtColor(shot, cv2.COLOR_BGR2HSV)
            mask = cv2.inRange(
                hsv, np.array([35, 60, 60]), np.array([95, 255, 255])
            )
            # 键盘区右下角
            y0, y1 = int(h * 0.78), int(h * 0.995)
            x0 = int(w * 0.62)
            mask[:y0, :] = 0
            mask[y1:, :] = 0
            mask[:, :x0] = 0
            contours, _ = cv2.findContours(
                mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
            )
            best = None  # (score, cx, cy)  偏右下优先
            min_area = w * h * 0.0003
            max_area = w * h * 0.06
            for c in contours:
                x, y, bw, bh = cv2.boundingRect(c)
                area = bw * bh
                if area < min_area or area > max_area:
                    continue
                if bw < 20 or bh < 16:
                    continue
                if bw > w * 0.45 or bh > h * 0.14:
                    continue
                cx, cy = x + bw // 2, y + bh // 2
                # 越靠右下越好
                score = (cx / w) * 2.0 + (cy / h)
                if best is None or score > best[0]:
                    best = (score, cx, cy)
            if best is None:
                return False
            _, cx, cy = best
            self.d.click(int(cx), int(cy))
            logger.info(
                f"[{self.account_id}] 键盘绿钮搜索 @({cx / w:.3f},{cy / h:.3f})"
            )
            return True
        except Exception as e:
            logger.debug(f"[{self.account_id}] 键盘绿钮检测失败: {e}")
            return False
