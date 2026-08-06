"""
聊天消息发送模块 — OCR + ADBKeyboard IME 混合方案
==================================================

## 概述

通过全局搜索找到指定联系人，打开聊天窗口，输入消息并发送。
全程使用 OCR 定位界面元素，IME 静默注入文字。

## 工作流

::

    冷启动微信 → 微信Tab
      │
      ├─[1] 点击搜索图标 (1050, 150)
      │     └─ 页面差异验证搜索页打开
      │
      ├─[2] IME 输入联系人名称 → Enter 搜索
      │
      ├─[3] OCR 识别搜索结果页
      │     ├─ 找到 "联系人" 区块标题
      │     └─ 找到目标联系人 → 点击进入聊天
      │
      ├─[4] OCR 识别聊天页底部输入框 → 点击激活
      │     └─ Fallback: 固定坐标 (w*0.35, h*0.955)
      │
      ├─[5] ADBKeyboard IME 注入消息文字
      │
      └─[6] OCR 识别 "发送" 按钮 → 点击
            └─ Fallback: 固定坐标 (w*0.88, h*0.955)

## 快速开始

.. code-block:: python

    from core.message_sender import MessageSender
    sender = MessageSender(device)
    sender.send(contact="张三", message="你好")

## CLI 测试

.. code-block:: bash

    python test_send_message.py --contact gas --msg "你好"

## 依赖

- EasyOCR: 搜索结果页 + 聊天页元素识别
- ADBKeyboard IME: 文字静默注入
- core.search_helper: 搜索联系人
"""

import time
import cv2
import numpy as np

from config.device_profiles import get_extra
from utils.logger import get_logger

logger = get_logger("message_sender")


class MessageSender:
    """微信消息发送器 — 搜索联系人 → 发消息。"""

    def __init__(self, d, account_id: str = ""):
        self.d = d
        self.account_id = account_id
        self.w, self.h = d.info['displayWidth'], d.info['displayHeight']
        self._ocr = None
        self._clahe = None
        self.SEARCH_ICON = tuple(get_extra(d, "msg_search_icon", (0.831, 0.054)))
        self.SEND_FALLBACK = tuple(get_extra(d, "msg_send", (0.88, 0.93)))
        self.INPUT_FALLBACK = tuple(get_extra(d, "msg_input", (0.35, 0.93)))

    # ================================================================
    # 公共接口
    # ================================================================

    def send(self, contact: str, message: str) -> bool:
        """
        搜索联系人并发送消息。

        Args:
            contact: 联系人名称（昵称或备注）
            message: 消息内容

        Returns:
            是否成功
        """
        logger.info(f"[{self.account_id}] 发送消息: '{contact}' <- '{message[:20]}...'")

        try:
            self._goto_home()
            self._search_contact(contact)
            self._click_contact_in_results(contact)
            self._input_message(message)
            self._click_send()
            logger.info(f"[{self.account_id}] 消息发送成功")
            return True
        except Exception as e:
            logger.error(f"[{self.account_id}] 发送消息失败: {e}")
            return False

    # ================================================================
    # 导航 + 搜索
    # ================================================================

    def _goto_home(self):
        """冷启动微信 → 微信 Tab。"""
        from core.wechat_nav import goto_tab, start_wechat

        start_wechat(self.d, wait=4.0, cold=True)
        goto_tab(self.d, "wechat")

    def _search_contact(self, contact: str):
        """点击搜索图标 → IME 输入联系人 → Enter。"""
        from core.wechat_nav import open_search

        d, w, h = self.d, self.w, self.h

        if not open_search(d):
            # fallback 单点
            d.click(int(w * self.SEARCH_ICON[0]), int(h * self.SEARCH_ICON[1]))
            time.sleep(2)

        # 点击输入框
        d.click(int(w * 0.50), int(h * 0.045))
        time.sleep(0.8)

        # IME 输入（中文必须走 IME，shell input text 会乱码/失败）
        try:
            d.set_input_ime(True)
            time.sleep(0.3)
            d.send_keys(contact)
            time.sleep(0.8)
            d.set_input_ime(False)
        except Exception as e:
            logger.warning(f"[{self.account_id}] IME输入失败: {e}")
            try:
                d(focused=True).set_text(contact)
            except Exception:
                pass

        time.sleep(0.3)
        d.press("enter")
        time.sleep(2.5)
        logger.debug(f"[{self.account_id}] 搜索: '{contact}'")

    # ================================================================
    # 搜索结果中找联系人
    # ================================================================

    def _click_contact_in_results(self, contact: str):
        """
        OCR 分析搜索结果页:
          1. 找到目标联系人（支持子串/模糊）
          2. 点击文字中心进入聊天
        """
        logger.debug(f"[{self.account_id}] OCR找联系人: '{contact}'")
        d, w, h = self.d, self.w, self.h

        img = np.array(d.screenshot(format="pillow"))
        gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
        enhanced = self._enhance(gray)
        enhanced_bgr = cv2.cvtColor(enhanced, cv2.COLOR_GRAY2BGR)

        results = self._ocr_region(enhanced_bgr, 0, 0, w, h)

        contact_x, contact_y = None, None
        contact_lower = contact.lower().replace(" ", "")
        y_min = int(h * 0.10)

        # 别名：文件传输助手常见 OCR 误读
        aliases = {contact_lower}
        if "文件传输" in contact or "传输助手" in contact:
            aliases.update({"文件传输助手", "文件传输", "传输助手", "file helper", "filehelper"})

        for text, cx, cy, conf, y0, _y1 in results:
            if conf < 0.2:
                continue
            t = (text or "").strip().lower().replace(" ", "")
            if y0 <= y_min:
                continue
            hit = any(a in t or t in a for a in aliases if a)
            if not hit:
                continue
            if contact_y is None or y0 < contact_y:
                contact_y = cy
                contact_x = cx

        if contact_x is None:
            # 再试一次不增强的原图
            results2 = self._ocr_region(
                cv2.cvtColor(img, cv2.COLOR_RGB2BGR), 0, 0, w, h)
            for text, cx, cy, conf, y0, _y1 in results2:
                if conf < 0.15 or y0 <= y_min:
                    continue
                t = (text or "").strip().lower().replace(" ", "")
                if any(a in t or t in a for a in aliases if a):
                    contact_x, contact_y = cx, cy
                    break

        if contact_x is None:
            raise RuntimeError(f"未找到联系人 '{contact}'")

        # 点文字中心偏右一点（避开头像）
        click_x = min(contact_x + int(w * 0.08), w - 40)
        click_y = contact_y
        d.click(click_x, click_y)
        time.sleep(3)
        logger.debug(f"[{self.account_id}] 点击联系人: ({click_x},{click_y})")

    # ================================================================
    # 输入消息
    # ================================================================

    def _input_message(self, message: str):
        """点击聊天输入框 → IME 注入消息。"""
        logger.debug(f"[{self.account_id}] 输入消息: '{message[:20]}...'")
        d, w, h = self.d, self.w, self.h

        # 尝试 OCR 找输入框，失败则 fallback
        try:
            img = np.array(d.screenshot(format="pillow"))
            img_bgr = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
            gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
            enhanced_bgr = cv2.cvtColor(self._enhance(gray), cv2.COLOR_GRAY2BGR)
            bottom_results = self._ocr_region(
                enhanced_bgr, 0, int(h * 0.88), w, h - 30)

            input_pos = None
            for text, cx, cy, conf, y0, _y1 in bottom_results:
                if conf < 0.2:
                    continue
                if y0 > h * 0.88 and cx < w * 0.7 and cx > 80:
                    input_pos = (cx, cy)
                    break

            if input_pos:
                d.click(*input_pos)
            else:
                d.click(int(w * self.INPUT_FALLBACK[0]),
                         int(h * self.INPUT_FALLBACK[1]))
        except Exception:
            d.click(int(w * self.INPUT_FALLBACK[0]),
                     int(h * self.INPUT_FALLBACK[1]))

        time.sleep(0.5)

        # IME 注入
        try:
            d.set_input_ime(True)
            time.sleep(0.3)
            d.send_keys(message)
            time.sleep(0.5)
            d.set_input_ime(False)
        except Exception:
            try:
                d.shell(f"input text {message}")
            except Exception:
                pass

    # ================================================================
    # 发送
    # ================================================================

    def _click_send(self):
        """OCR 找"发送"按钮 → 点击，失败则 fallback。"""
        logger.debug(f"[{self.account_id}] 点击发送")
        d, w, h = self.d, self.w, self.h

        try:
            img = np.array(d.screenshot(format="pillow"))
            img_bgr = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
            gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
            enhanced_bgr = cv2.cvtColor(self._enhance(gray), cv2.COLOR_GRAY2BGR)
            results = self._ocr_region(
                enhanced_bgr, int(w * 0.55), int(h * 0.88), w, h - 30)

            for text, cx, cy, conf, _y0, _y1 in results:
                if "发送" in text and conf > 0.3:
                    d.click(cx, cy)
                    time.sleep(2)
                    logger.debug(f"[{self.account_id}] 发送: ({cx},{cy})")
                    return
        except Exception:
            pass

        d.click(int(w * self.SEND_FALLBACK[0]), int(h * self.SEND_FALLBACK[1]))
        time.sleep(2)

    # ================================================================
    # 工具
    # ================================================================

    def _enhance(self, gray):
        if self._clahe is None:
            self._clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
        return self._clahe.apply(gray)

    def _get_ocr(self):
        if self._ocr is None:
            from utils.ocr_utils import create_easyocr_reader
            self._ocr = create_easyocr_reader()
        return self._ocr

    def _ocr_region(self, img_bgr, x0, y0, x1, y1):
        h_img, w_img = img_bgr.shape[:2]
        x0, y0 = max(0, x0), max(0, y0)
        x1, y1 = min(w_img, x1), min(h_img, y1)
        if x0 >= x1 or y0 >= y1:
            return []

        crop = img_bgr[y0:y1, x0:x1]
        reader = self._get_ocr()
        raw = reader.readtext(crop)

        results = []
        for bbox, text, conf in raw:
            cx = int((bbox[0][0] + bbox[2][0]) / 2) + x0
            cy = int((bbox[0][1] + bbox[2][1]) / 2) + y0
            y_start = int(bbox[0][1]) + y0
            y_end = int(bbox[2][1]) + y0
            results.append((text, cx, cy, conf, y_start, y_end))
        return results
