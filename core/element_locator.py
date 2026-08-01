"""
元素定位辅助模块 — 在 wechat_elements 基础上提供便捷的定位方法。

功能:
    - 等待元素出现
    - 安全点击（带重试）
    - 批量存在性检查
    - 当前页面判断

使用方式:
    locator = ElementLocator(d)
    locator.wait_and_click("tab_discover")
    locator.wait_and_click("moments_entry")
"""

import time
from typing import Optional

import uiautomator2 as u2

from config.device_profiles import get_coord
from config.wechat_elements import locate_element, WECHAT_ELEMENTS, COORDINATE_FALLBACK
from utils.logger import get_logger

logger = get_logger("element_locator")


class ElementLocator:
    """
    微信界面元素定位器。

    封装了等待、重试、异常处理等常用模式，
    让上层脚本代码更简洁。
    坐标 fallback 按当前设备机型配置解析，多机型互不覆盖。
    """

    def __init__(self, d: u2.Device, default_timeout: float = 10.0):
        self.d = d
        self.default_timeout = default_timeout
        self._coords_used: set[str] = set()  # Track which elements use coordinates

    def _fallback_coord(self, element_name: str) -> Optional[tuple[float, float]]:
        """按机型取百分比坐标，找不到再查默认表。"""
        c = get_coord(self.d, element_name)
        if c is not None:
            return c
        return COORDINATE_FALLBACK.get(element_name)

    # ================================================================
    # 基础定位
    # ================================================================

    def find(self, element_name: str, timeout: float = None) -> Optional[u2.UiObject]:
        """
        查找指定元素。有坐标 fallback 的元素使用更短超时。

        Args:
            element_name: WECHAT_ELEMENTS 中的键名
            timeout: 超时秒数，None 使用默认值

        Returns:
            找到的 UiObject，未找到返回 None
        """
        if timeout is None:
            # 有坐标 fallback 时用短超时，快速切换到坐标模式
            if self._fallback_coord(element_name) is not None:
                timeout = 2.0
            else:
                timeout = self.default_timeout
        try:
            return locate_element(self.d, element_name, timeout=timeout)
        except (KeyError, TimeoutError) as e:
            logger.debug(f"元素 '{element_name}' 未找到: {e}")
            return None

    def exists(self, element_name: str, timeout: float = 3.0) -> bool:
        """检查元素是否存在"""
        el = self.find(element_name, timeout=timeout)
        return el is not None and el.exists

    # ================================================================
    # 点击操作
    # ================================================================

    def click(self, element_name: str, timeout: float = None) -> bool:
        """
        点击指定元素。元素定位失败时自动使用坐标 fallback。

        Args:
            element_name: WECHAT_ELEMENTS 中的键名
            timeout: 超时秒数

        Returns:
            点击是否成功
        """
        el = self.find(element_name, timeout=timeout)
        if el is not None:
            try:
                el.click()
                logger.debug(f"已点击 (element): '{element_name}'")
                return True
            except Exception as e:
                logger.warning(f"元素点击 '{element_name}' 异常: {e}")

        # === Fallback: 按机型百分比坐标点击 ===
        fb = self._fallback_coord(element_name)
        if fb is not None:
            x_ratio, y_ratio = fb
            w = self.d.info['displayWidth']
            h = self.d.info['displayHeight']
            x, y = int(w * x_ratio), int(h * y_ratio)
            try:
                self.d.click(x, y)
                logger.debug(f"已点击 (coords): '{element_name}' at ({x}, {y})")
                time.sleep(0.3)
                return True
            except Exception as e:
                logger.warning(f"坐标点击 '{element_name}' 异常: {e}")
                return False

        logger.warning(f"点击失败: 元素 '{element_name}' 未找到且无坐标 fallback")
        return False

    def wait_and_click(
        self,
        element_name: str,
        timeout: float = None,
        retries: int = 1,
    ) -> bool:
        """
        等待元素出现后点击，支持重试和坐标 fallback。

        Args:
            element_name: 元素名称
            timeout: 单次等待超时
            retries: 失败重试次数

        Returns:
            是否点击成功
        """
        for attempt in range(retries + 1):
            if attempt > 0:
                logger.debug(f"重试点击 '{element_name}' ({attempt}/{retries})")
            el = self.find(element_name, timeout=timeout)
            if el is not None:
                try:
                    el.click()
                    logger.debug(f"已点击: '{element_name}'")
                    return True
                except Exception as e:
                    logger.warning(f"点击 '{element_name}' 异常: {e}")

        # === Fallback: 按机型百分比坐标点击 ===
        fb = self._fallback_coord(element_name)
        if fb is not None:
            x_ratio, y_ratio = fb
            w = self.d.info['displayWidth']
            h = self.d.info['displayHeight']
            x, y = int(w * x_ratio), int(h * y_ratio)
            try:
                self.d.click(x, y)
                logger.debug(f"已点击 (coords): '{element_name}' at ({x}, {y})")
                time.sleep(0.3)
                return True
            except Exception as e:
                logger.warning(f"坐标点击 '{element_name}' 异常: {e}")

        logger.error(f"点击 '{element_name}' 失败（{retries} 次重试后）")
        return False

    # ================================================================
    # 文本输入
    # ================================================================

    def find_input_box(self, timeout: float = None) -> Optional[u2.UiObject]:
        """查找当前页面的输入框"""
        timeout = timeout or self.default_timeout
        try:
            el = self.d(className="android.widget.EditText")
            if el.wait(timeout=timeout):
                return el
        except Exception:
            pass
        return None

    def type_text(self, text: str):
        """
        向当前焦点的输入框输入文字。

        Args:
            text: 要输入的文字
        """
        input_box = self.find_input_box()
        if input_box:
            input_box.click()
            import time
            time.sleep(0.2)
            self.d.send_keys(text)
        else:
            logger.warning("未找到输入框，无法输入文字")

    # ================================================================
    # 页面判断
    # ================================================================

    def is_on_page(self, *element_names: str) -> bool:
        """
        判断是否在某个页面（任一元素存在即认为在）。

        Args:
            element_names: 该页面的标志性元素名称列表

        Returns:
            是否在该页面

        Example:
            locator.is_on_page("moments_entry", "channels_entry")
        """
        for name in element_names:
            if self.exists(name, timeout=2.0):
                return True
        return False

    # ================================================================
    # 批量检查
    # ================================================================

    def check_elements(self, elements: list[str]) -> dict[str, bool]:
        """
        批量检查元素是否存在。

        用于初始化时验证微信元素定位字典的准确性。

        Args:
            elements: 元素名称列表

        Returns:
            {element_name: exists}
        """
        results = {}
        for name in elements:
            results[name] = self.exists(name, timeout=2.0)
        return results

    def print_element_tree(self, max_depth: int = 3):
        """
        打印当前页面的控件树（调试用）。

        Args:
            max_depth: 最大递归深度
        """
        root = self.d.dump_hierarchy()
        logger.debug(f"控件树:\n{root}")
