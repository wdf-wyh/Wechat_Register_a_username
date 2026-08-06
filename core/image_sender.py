"""
聊天图片发送模块 — OCR + OpenCV + ADBKeyboard IME 混合方案
============================================================

## 概述

通过全局搜索找到指定联系人，进入聊天窗口后，
点击"+"按钮打开附件菜单，选择"相册"进入系统相册，
使用 Canny 边缘检测选中照片后发送。

与朋友圈发图 (moment_poster) 共享相同的相册选图逻辑。

## 工作流

::

    搜索联系人 → 进入聊天
      │
      ├─[1] 点击右下角 "+" 按钮 (1189, 2620)
      │     └─ OCR 扫描底部弹窗菜单
      │
      ├─[2] OCR 识别 "相册" → 点击
      │     └─ 相册菜单在第一行第一列 (约 194, 2082)
      │
      ├─[3] OpenCV Canny 边缘检测 → 点击照片中心选中
      │     └─ 与 moment_poster._select_photos() 同款逻辑
      │     └─ 照片网格: y≈438/756, x≈158/475/792/1107
      │     └─ 发送按钮显示 "发送(N)" 确认选中数
      │
      └─[4] OCR 识别 "发送" → 点击

## 快速开始

.. code-block:: python

    from core.image_sender import ImageSender
    sender = ImageSender(device)
    sender.send(contact="gas", photo_count=2)

## CLI 测试

.. code-block:: bash

    python test_send_image.py --contact gas --count 1
    python test_send_image.py --contact "稀有气体" --count 2

## 依赖

- EasyOCR: "+"菜单识别 + 发送按钮验证
- OpenCV Canny: 照片缩略图检测
- ADBKeyboard IME: (本模块当前不涉及文字输入)

## 适配

坐标按 ``config.device_profiles`` 机型配置加载。
新机型请新增 profile 文件，勿直接改本模块硬编码覆盖旧机型。
"""

import time
import cv2
import numpy as np

from config.device_profiles import get_extra
from utils.logger import get_logger

logger = get_logger("image_sender")


class ImageSender:
    """聊天图片发送器 — 搜索联系人 → 选图 → 发送。"""

    def __init__(self, d, account_id: str = ""):
        self.d = d
        self.account_id = account_id
        self.w, self.h = d.info['displayWidth'], d.info['displayHeight']
        self._ocr = None
        self._clahe = None
        # 按机型加载，百分比坐标
        self.PLUS_BTN = tuple(get_extra(d, "plus_btn", (0.941, 0.942)))
        grid = get_extra(d, "photo_grid") or []
        # photo_grid 存百分比；内部 fallback 转像素
        self.PHOTO_GRID = [
            (int(self.w * rx), int(self.h * ry)) for rx, ry in grid
        ]

    # ================================================================
    # 公共接口
    # ================================================================

    def send(self, contact: str, photo_count: int = 1) -> bool:
        """
        给指定联系人发送图片。

        Args:
            contact:     联系人名称
            photo_count: 发送几张照片 (选最新的 N 张)

        Returns:
            是否成功
        """
        logger.info(f"[{self.account_id}] 发送图片: '{contact}' x{photo_count}")

        try:
            self._goto_chat(contact)
            self._open_album()
            self._select_photos(photo_count)
            self._click_send()
            logger.info(f"[{self.account_id}] 图片发送成功")
            return True
        except Exception as e:
            logger.error(f"[{self.account_id}] 发送图片失败: {e}")
            return False

    # ================================================================
    # 导航到聊天
    # ================================================================

    def _goto_chat(self, contact: str):
        """搜索联系人 → 进入聊天窗口。"""
        from core.message_sender import MessageSender

        logger.debug(f"[{self.account_id}] 进入聊天: '{contact}'")
        # 复用已修复的消息发送导航/搜索逻辑
        ms = MessageSender(self.d, account_id=self.account_id)
        ms._goto_home()
        ms._search_contact(contact)
        ms._click_contact_in_results(contact)
        # 同步 OCR 实例，避免重复加载
        self._ocr = ms._ocr
        self._clahe = ms._clahe
        logger.debug(f"[{self.account_id}] 已进入聊天")

    # ================================================================
    # "+" → "相册"
    # ================================================================

    def _open_album(self):
        """点击"+" → OCR 找"相册" → 点击进入系统相册。"""
        logger.debug(f"[{self.account_id}] 打开相册")
        d, w, h = self.d, self.w, self.h

        # 点击 "+"
        d.click(int(w * self.PLUS_BTN[0]), int(h * self.PLUS_BTN[1]))
        time.sleep(2)

        # OCR 找"相册"
        img = np.array(d.screenshot(format="pillow"))
        gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
        enhanced_bgr = cv2.cvtColor(self._enhance(gray), cv2.COLOR_GRAY2BGR)
        results = self._ocr_region(enhanced_bgr, 0, int(h * 0.4), int(w * 0.85), h)

        album_pos = None
        for text, cx, cy, conf, _y0, _y1 in results:
            if "相册" in text and conf > 0.3:
                album_pos = (cx, cy)
                break

        if album_pos:
            d.click(*album_pos)
        else:
            ax, ay = get_extra(self.d, "album_menu", (0.25, 0.55))
            d.click(int(w * ax), int(h * ay))

        time.sleep(3)

    # ================================================================
    # 选照片 (与 moment_poster._select_photos 同款逻辑)
    # ================================================================

    def _select_photos(self, count: int):
        """Canny 边缘检测 → 点击照片中心选中。"""
        logger.debug(f"[{self.account_id}] 选择 {count} 张照片")
        d, w, h = self.d, self.w, self.h

        time.sleep(2)

        img = np.array(d.screenshot(format="pillow"))
        g = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)

        album = g[180:int(h * 0.78), :]
        edges = cv2.Canny(cv2.GaussianBlur(album, (5, 5), 0), 25, 80)
        edges = cv2.dilate(edges, np.ones((4, 4), np.uint8), iterations=1)
        contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        photos = []
        for cnt in contours:
            x, y, cw, ch = cv2.boundingRect(cnt)
            ar = cw / ch if ch > 0 else 0
            if 60 < cw < 500 and 60 < ch < 500 and 0.5 < ar < 2.0:
                if 500 < cw * ch < 150000:
                    photos.append({"cx": x + cw // 2, "cy": y + 180 + ch // 2})

        photos.sort(key=lambda p: (p["cy"], p["cx"]))
        logger.debug(f"[{self.account_id}] Canny: {len(photos)} 个缩略图")

        if len(photos) < count:
            photos = [{"cx": g[0], "cy": g[1]} for g in self.PHOTO_GRID[:count]]

        for i, p in enumerate(photos[:count]):
            d.click(p["cx"], p["cy"])
            time.sleep(0.5)

    # ================================================================
    # 发送
    # ================================================================

    def _click_send(self):
        """OCR 找"发送"按钮 → 点击。"""
        logger.debug(f"[{self.account_id}] 点击发送")
        d, w, h = self.d, self.w, self.h

        img = np.array(d.screenshot(format="pillow"))
        gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
        enhanced_bgr = cv2.cvtColor(self._enhance(gray), cv2.COLOR_GRAY2BGR)
        results = self._ocr_region(enhanced_bgr, int(w * 0.55), int(h * 0.88), w, h - 30)

        for text, cx, cy, conf, _y0, _y1 in results:
            if "发送" in text and conf > 0.3:
                d.click(cx, cy)
                time.sleep(2)
                return

        d.click(int(w * 0.88), int(h * 0.955))  # fallback
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
        h_i, w_i = img_bgr.shape[:2]
        x0, y0 = max(0, x0), max(0, y0)
        x1, y1 = min(w_i, x1), min(h_i, y1)
        if x0 >= x1 or y0 >= y1:
            return []
        crop = img_bgr[y0:y1, x0:x1]
        raw = self._get_ocr().readtext(crop)
        return [(t, int((b[0][0]+b[2][0])/2)+x0, int((b[0][1]+b[2][1])/2)+y0, c,
                 int(b[0][1])+y0, int(b[2][1])+y0) for b, t, c in raw]
