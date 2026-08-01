"""
全局搜索模块 — OpenCV + ADBKeyboard IME。

工作流:
  1. 冷启动微信 → 确保在"微信"Tab
  2. 点击右上角搜索图标（放大镜，紧挨"+"按钮）
  3. 验证搜索页打开（页面差异对比）
  4. ADBKeyboard IME 注入关键词 → Enter 搜索

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
        """按 Enter 键触发搜索。"""
        time.sleep(0.3)
        try:
            self.d.press("enter")
            time.sleep(2)
        except Exception:
            pass
