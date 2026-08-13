"""
朋友圈自动发布模块 — OCR + OpenCV + ADBKeyboard IME 混合识别方案
==================================================================

## 概述

本模块实现微信朋友圈的全自动发布流程。由于微信屏蔽了 UiAutomation 控件树，
所有界面定位均通过 **截图 + OCR 文字识别 + OpenCV 图像匹配** 完成，
文字输入通过 **ADBKeyboard IME 静默注入**（不弹键盘、不乱码）。

## 工作流

::

    导航到朋友圈
      │
      ├─[1] OpenCV 模板匹配 "相机" 图标 ──→ 点击右上角相机
      │     └─ 多尺度模板匹配 (0.7x~1.2x)，失败时多坐标 fallback
      │
      ├─[2] OCR 识别 "从手机相册选择" ──→ 点击
      │     └─ EasyOCR 扫描下半屏菜单区域，关键词匹配
      │
      ├─[3] OpenCV Canny 边缘检测 ──→ 点击指定照片
      │     └─ 检测缩略图网格，按行列排序，支持多选
      │
      ├─[4] OCR 识别 "完成" 按钮 ──→ 点击进入编辑页
      │     └─ 扫描右下角区域，匹配 "完成(N)"
      │
      ├─[5] OCR 识别 "这一刻的想法..." 占位文字 ──→ 点击激活输入框
      │     └─ CLAHE 增强低对比度文字后 OCR，ADBKeyboard IME send_keys
      │
      └─[6] OCR 识别 "发表" 按钮 ──→ 点击发送
            └─ 扫描右上角区域，验证返回朋友圈页面

## 快速开始

.. code-block:: python

    from core.moment_poster import MomentPoster

    poster = MomentPoster(device, account_id="wx_001")
    poster.post(text="今天天气真好", photo_count=2)

## CLI 测试

.. code-block:: bash

    python test_post_moment.py --text "hello" --count 1
    python test_post_moment.py --cmd "前两张照片发送并配文字 raregas"

## 依赖

- **EasyOCR** (ch_sim + en): 文字识别，首次运行需下载模型 (~100MB)
- **OpenCV**: 模板匹配、边缘检测、图像预处理
- **ADBKeyboard IME**: uiautomator2 内置，首次使用自动安装
- **PyTorch**: EasyOCR 后端 (CPU 模式)

## 适配

当前基于 **Moto X70 Air Pro (1264x2780, Android 14)** 校准。
换设备需更新:
  - ``screenshots/template_camera_*.png`` — 相机图标模板截图
  - ``_click_publish()`` / ``_click_done()`` 中的 fallback 坐标
"""

import time
import random
import cv2
import numpy as np
from pathlib import Path

from utils.logger import get_logger

logger = get_logger("moment_poster")


class MomentPoster:
    """朋友圈自动发布器。"""

    def __init__(self, d, account_id: str = ""):
        self.d = d
        self.account_id = account_id
        self.w, self.h = d.info['displayWidth'], d.info['displayHeight']

        # 延迟加载
        self._ocr = None
        self._clahe = None

    # ================================================================
    # 公共接口
    # ================================================================

    def post(
        self,
        text: str = "",
        photo_index: int = 0,
        photo_count: int = 1,
        photo_indices: list[int] | None = None,
        persona: dict | None = None,
        smart_select: bool = True,
        topic: str = "日常",
    ) -> bool:
        """
        发朋友圈。

        Args:
            text:           朋友圈文案（空且 smart_select 时会按选中图片生成）
            photo_index:    起始照片序号，0=第一张（非智能选图时使用）
            photo_count:    选几张照片
            photo_indices:  指定要选中的网格索引（优先于 photo_index）
            persona:        人设，智能选图/配文时使用
            smart_select:   是否 Vision 智能选图并生成图文配文
            topic:          配文主题（智能选图失败时降级）

        Returns:
            是否成功
        """
        logger.info(
            f"[{self.account_id}] 发朋友圈: text='{(text or '')[:20]}...', "
            f"photos={photo_count}, smart={smart_select}"
        )

        try:
            self._navigate_to_moments()
            self._click_camera()
            self._click_album_option()

            if photo_indices is None and smart_select and persona:
                from content.moment_photo_picker import MomentPhotoPicker

                picker = MomentPhotoPicker(self.d, account_id=self.account_id)
                picked_indices, generated_text, _ = picker.scan_and_pick(
                    persona,
                    photo_count=photo_count,
                    topic=topic,
                )
                if picked_indices:
                    photo_indices = picked_indices
                if not text and generated_text:
                    text = generated_text

            if not text:
                text = "记录一下"

            if photo_indices is not None:
                self._select_photos_by_indices(photo_indices)
            else:
                self._select_photos(photo_index, photo_count)

            if not self._album_has_selection():
                logger.warning(f"[{self.account_id}] 相册未选中照片，重试勾选")
                if photo_indices is not None:
                    self._select_photos_by_indices(photo_indices, prefer_checkbox=True)
                else:
                    self._select_photos(photo_index, photo_count)
                if not self._album_has_selection():
                    logger.error(f"[{self.account_id}] 选图失败，放弃发表")
                    self.d.press("back")
                    return False

            if not self._click_done():
                logger.error(f"[{self.account_id}] 未进入编辑页，放弃发表")
                return False

            # 发表前复核配图：防止网格偏移导致「文案美食、实图截图」
            if not self._compose_photo_looks_ok(topic=topic):
                logger.error(f"[{self.account_id}] 编辑页配图复核失败（疑似截图/错图），放弃发表")
                self.d.press("back")
                time.sleep(0.5)
                self.d.press("back")
                return False

            self._input_text(text)
            if not self._click_publish():
                logger.error(f"[{self.account_id}] 发表按钮点击后仍在编辑页")
                return False

            logger.info(f"[{self.account_id}] 朋友圈发送成功")
            return True
        except Exception as e:
            logger.error(f"[{self.account_id}] 发朋友圈失败: {e}")
            return False

    # ================================================================
    # 导航
    # ================================================================

    def _navigate_to_moments(self):
        """冷启动微信 → 发现 → 朋友圈 → 顶部。"""
        from core.wechat_nav import (
            moments_entry_for,
            click_ratio,
            goto_tab,
            ocr_find_and_click,
            start_wechat,
        )

        logger.debug(f"[{self.account_id}] 导航到朋友圈...")
        d = self.d
        start_wechat(d, wait=4.0, cold=True)
        goto_tab(d, "discover")
        time.sleep(1.0)

        clicked = ocr_find_and_click(
            d,
            self._get_ocr(),
            ["朋友圈"],
            y_min_ratio=0.08,
            y_max_ratio=0.45,
            conf_min=0.3,
            enhance=self._clahe_enhance,
            click_row_center=True,
        )
        if not clicked:
            click_ratio(d, *moments_entry_for(d))
        time.sleep(2.5)
        w, h = self.w, self.h
        d.swipe(w // 2, int(h * 0.3), w // 2, int(h * 0.7), duration=0.3)
        time.sleep(1.5)

    # ================================================================
    # 阶段1: OpenCV 模板匹配相机
    # ================================================================

    def _click_camera(self):
        """OpenCV 模板匹配相机图标 → 点击。"""
        logger.debug(f"[{self.account_id}] 阶段1: 匹配相机图标")
        d, w, h = self.d, self.w, self.h

        img = np.array(d.screenshot(format="pillow"))
        g = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
        search = g[int(h * 0.03):int(h * 0.12), int(w * 0.70):w]

        best_match, best_val = None, 0
        template_dir = Path("screenshots")
        for tpl_name in ["template_camera_icon.png", "template_camera_980.png",
                          "template_camera_1000.png"]:
            tpl_path = template_dir / tpl_name
            if not tpl_path.exists():
                continue
            tpl = cv2.imread(str(tpl_path), cv2.IMREAD_GRAYSCALE)
            if tpl is None:
                continue
            for scale in [0.7, 0.8, 0.9, 1.0, 1.1, 1.2]:
                sw, sh = int(tpl.shape[1] * scale), int(tpl.shape[0] * scale)
                if sw < 10 or sh < 10 or sw > search.shape[1] or sh > search.shape[0]:
                    continue
                resized = cv2.resize(tpl, (sw, sh))
                result = cv2.matchTemplate(search, resized, cv2.TM_CCOEFF_NORMED)
                _, mv, _, ml = cv2.minMaxLoc(result)
                if mv > best_val:
                    best_val = mv
                    best_match = {
                        "x": ml[0] + sw // 2 + int(w * 0.70),
                        "y": ml[1] + sh // 2 + int(h * 0.03),
                        "score": mv,
                    }

        if best_match and best_match["score"] > 0.4:
            cx, cy = best_match["x"], best_match["y"]
            logger.debug(f"[{self.account_id}] 相机匹配: ({cx},{cy}) score={best_match['score']:.2f}")
        else:
            cx, cy = int(w * 0.862), int(h * 0.054)
            logger.debug(f"[{self.account_id}] 相机 fallback: ({cx},{cy})")

        d.click(cx, cy)
        time.sleep(2)

        # 重试验证
        if not self._is_dimmed():
            for rx, ry in [(0.893, 0.055), (0.870, 0.054)]:
                d.click(int(w * rx), int(h * ry))
                time.sleep(2)
                if self._is_dimmed():
                    break
                d.press("back")
                time.sleep(0.3)

    # ================================================================
    # 阶段2: OCR 识别"从相册选择"
    # ================================================================

    def _click_album_option(self):
        """OCR 识别菜单中的'从相册选择' → 点击。"""
        logger.debug(f"[{self.account_id}] 阶段2: OCR识别相册选项")
        d, h = self.d, self.h

        img = np.array(d.screenshot(format="pillow"))
        img_bgr = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)

        results = self._ocr_region(img_bgr, 0, int(h * 0.48), self.w, h)
        album_targets = ["从相册选择", "从手机相册选择", "相册选择", "手机相册"]

        best = None
        for bbox, text, conf in results:
            for tgt in album_targets:
                if tgt in text:
                    cx = int((bbox[0][0] + bbox[2][0]) / 2)
                    cy = int((bbox[0][1] + bbox[2][1]) / 2)
                    if cy > h * 0.5:
                        best = (cx, cy)
                        break
            if best:
                break

        if best:
            logger.debug(f"[{self.account_id}] 相册选项: ({best[0]},{best[1]})")
            d.click(*best)
        else:
            logger.warning(f"[{self.account_id}] OCR未找到相册选项，fallback")
            d.click(int(self.w * 0.5), int(h * 0.87))

        time.sleep(3)

    # ================================================================
    # 阶段3: OpenCV 选照片
    # ================================================================

    def _select_photos(self, photo_index: int = 0, count: int = 1):
        """OpenCV Canny边缘检测 → 选照片。"""
        from content.moment_photo_picker import detect_album_thumbnails

        logger.debug(f"[{self.account_id}] 阶段3: 选{count}张照片(从#{photo_index+1}起)")
        d, w, h = self.d, self.w, self.h

        time.sleep(1.5)

        img = np.array(d.screenshot(format="pillow"))
        g = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
        photos = detect_album_thumbnails(g, h)
        logger.debug(f"[{self.account_id}] 检测到{len(photos)}个缩略图")

        selected = 0
        for i in range(photo_index, min(photo_index + count, len(photos))):
            p = photos[i]
            d.click(p.cx, p.cy)
            time.sleep(0.4)
            selected += 1

        # fallback
        if selected < count:
            fallback = [(0.158, 0.180), (0.158, 0.200), (0.475, 0.180),
                         (0.475, 0.200), (0.120, 0.180)]
            for i in range(selected, min(count, len(fallback))):
                d.click(int(w * fallback[i][0]), int(h * fallback[i][1]))
                time.sleep(0.4)

    def _select_photos_by_indices(
        self,
        indices: list[int],
        prefer_checkbox: bool = True,
    ):
        """按网格索引点击已筛选的照片（优先点右上角勾选圆）。"""
        from content.moment_photo_picker import detect_album_thumbnails

        logger.info(f"[{self.account_id}] 阶段3: 按索引选图 {indices}")
        d, h = self.d, self.h
        time.sleep(0.5)

        img = np.array(d.screenshot(format="pillow"))
        g = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
        photos = detect_album_thumbnails(g, h)

        for idx in indices:
            if not (0 <= idx < len(photos)):
                logger.warning(f"[{self.account_id}] 索引越界: {idx}")
                continue
            p = photos[idx]
            if prefer_checkbox:
                # 微信相册：点缩略图中心可能进预览；点右上勾选圆更稳
                cx = int(p.x + p.w * 0.82)
                cy = int(p.y + p.h * 0.18)
            else:
                cx, cy = p.cx, p.cy
            logger.info(f"[{self.account_id}] 点击缩略图#{idx + 1} @({cx},{cy})")
            d.click(cx, cy)
            time.sleep(0.55)

    def _compose_photo_looks_ok(self, topic: str = "日常") -> bool:
        """编辑页预览图快速 Vision 复核，拦截聊天截图等错图。"""
        try:
            from content.llm_client import LLMClient
            from content.moment_photo_picker import _encode_jpeg

            d, w, h = self.d, self.w, self.h
            img = np.array(d.screenshot(format="pillow"))
            bgr = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
            # 编辑页首张配图大致在中上部偏左
            x0, y0 = int(w * 0.04), int(h * 0.22)
            x1, y1 = int(w * 0.42), int(h * 0.48)
            crop = bgr[y0:y1, x0:x1]
            if crop.size == 0:
                return True
            llm = LLMClient()
            if not llm.vision_available:
                return True
            result = llm.classify_moment_thumbnail(
                _encode_jpeg(crop),
                preferred_categories=["日常", "美食", "旅行", "风景", "宠物"],
                topic=topic or "日常",
            )
            ok = bool(result.get("suitable"))
            cat = str(result.get("category") or "")
            desc = str(result.get("description") or "")
            reason = str(result.get("reject_reason") or "")
            logger.info(
                f"[{self.account_id}] 编辑页配图复核 suitable={ok} "
                f"cat={cat} desc={desc[:40]} reason={reason[:40]}"
            )
            bad_kw = ("截图", "聊天", "列表", "桌面", "图标墙", "系统界面")
            if any(k in desc or k in reason for k in bad_kw):
                return False
            return ok
        except Exception as e:
            logger.warning(f"[{self.account_id}] 编辑页配图复核异常，放行: {e}")
            return True

    def _album_has_selection(self) -> bool:
        """相册底栏是否出现 完成(N) / 预览(N)。"""
        d, w, h = self.d, self.w, self.h
        img = np.array(d.screenshot(format="pillow"))
        img_bgr = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
        results = self._ocr_region(img_bgr, 0, int(h * 0.84), w, h)
        blob = " ".join(str(t) for _, t, c in results if c > 0.25)
        ok = ("完成(" in blob) or ("预览(" in blob) or ("完成（" in blob)
        logger.info(f"[{self.account_id}] 相册选中检测={ok} OCR: {blob[:80]}")
        return ok

    def _on_compose_page(self) -> bool:
        """是否在朋友圈编辑页（取消/发表/这一刻的想法）。"""
        d, w, h = self.d, self.w, self.h
        img = np.array(d.screenshot(format="pillow"))
        img_bgr = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
        top = self._ocr_region(img_bgr, 0, 0, w, int(h * 0.22))
        mid = self._ocr_region(img_bgr, 0, int(h * 0.08), w, int(h * 0.40))
        blob = " ".join(str(t) for _, t, _ in top + mid)
        return ("发表" in blob) and (
            "取消" in blob or "这一刻" in blob or "想法" in blob
        )

    def _on_make_video_page(self) -> bool:
        """混选图片+视频后是否停在「制作视频」中间页。"""
        d, w, h = self.d, self.w, self.h
        img = np.array(d.screenshot(format="pillow"))
        img_bgr = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
        top = self._ocr_region(img_bgr, int(w * 0.35), 0, w, int(h * 0.18))
        bottom = self._ocr_region(img_bgr, 0, int(h * 0.72), w, h)
        top_blob = " ".join(str(t) for _, t, c in top if c > 0.25)
        bottom_blob = " ".join(str(t) for _, t, c in bottom if c > 0.25)
        if "发表" in top_blob:
            return False
        has_top_done = "完成" in top_blob
        make_video_kw = ("制作视频", "调整", "简单", "模板", "一键成片")
        on_make = any(k in bottom_blob for k in make_video_kw)
        return has_top_done and on_make

    def _click_top_done(self) -> bool:
        """点击顶栏「完成」（制作视频页 → 编辑页）。"""
        from config.device_profiles import get_extra

        d, w, h = self.d, self.w, self.h
        img = np.array(d.screenshot(format="pillow"))
        img_bgr = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
        results = self._ocr_region(img_bgr, int(w * 0.45), 0, w, int(h * 0.16))

        for bbox, text, conf in results:
            if conf > 0.2 and "完成" in str(text):
                cx = int((bbox[0][0] + bbox[2][0]) / 2)
                cy = int((bbox[0][1] + bbox[2][1]) / 2)
                logger.info(
                    f"[{self.account_id}] 制作视频页顶栏完成 OCR '{text}' @({cx},{cy})"
                )
                d.click(cx, cy)
                return True

        fb = tuple(get_extra(d, "moments_make_video_done", (0.89, 0.066)))
        logger.warning(f"[{self.account_id}] 顶栏完成 OCR 未命中，fallback {fb}")
        d.click(int(w * fb[0]), int(h * fb[1]))
        return True

    # ================================================================
    # 阶段4: OCR 识别"完成"
    # ================================================================

    def _click_done(self) -> bool:
        """OCR 识别'完成'按钮 → 点击，并确认进入编辑页。"""
        from config.device_profiles import get_extra

        logger.info(f"[{self.account_id}] 阶段4: 识别完成按钮")
        d, w, h = self.d, self.w, self.h
        time.sleep(0.5)

        img = np.array(d.screenshot(format="pillow"))
        img_bgr = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
        results = self._ocr_region(img_bgr, int(w * 0.45), int(h * 0.84), w, h)

        done_pos = None
        for bbox, text, conf in results:
            t = str(text or "")
            if conf > 0.2 and ("完成" in t):
                cx = int((bbox[0][0] + bbox[2][0]) / 2)
                cy = int((bbox[0][1] + bbox[2][1]) / 2)
                done_pos = (cx, cy)
                logger.info(f"[{self.account_id}] 完成按钮 OCR '{t}' @({cx},{cy})")
                break

        if done_pos:
            d.click(*done_pos)
        else:
            # Redmi 底栏「完成」约在右下，避开系统导航条
            fb = tuple(get_extra(d, "moments_album_done", (0.88, 0.93)))
            logger.warning(f"[{self.account_id}] 完成按钮 OCR 未命中，fallback {fb}")
            d.click(int(w * fb[0]), int(h * fb[1]))

        time.sleep(2.5)
        if self._on_compose_page():
            return True

        # 混选含视频时微信会进「制作视频」页，需再点顶栏「完成」
        if self._on_make_video_page():
            logger.info(f"[{self.account_id}] 检测到制作视频中间页，点击顶栏完成")
            self._click_top_done()
            time.sleep(2.5)
            if self._on_compose_page():
                logger.info(f"[{self.account_id}] 制作视频页已进入编辑页")
                return True

        # 相册底栏「完成」再点一次兜底
        fb2 = tuple(get_extra(d, "moments_album_done", (0.88, 0.93)))
        d.click(int(w * fb2[0]), int(h * fb2[1]))
        time.sleep(2.0)
        if self._on_compose_page():
            return True

        # 仍可能在制作视频页（OCR 漏检），再试顶栏完成
        self._click_top_done()
        time.sleep(2.0)
        ok = self._on_compose_page()
        logger.info(f"[{self.account_id}] 进入编辑页={ok}")
        return ok

    # ================================================================
    # 阶段5: IME 注入文字
    # ================================================================

    def _input_text(self, text: str):
        """OCR定位输入区 + ADBKeyboard IME 注入文字。"""
        logger.info(f"[{self.account_id}] 阶段5: 输入文字 '{text[:20]}...'")
        d, w, h = self.d, self.w, self.h

        # 5a. 切换到 ADBKeyboard IME
        try:
            d.set_input_ime(True)
            time.sleep(0.3)
        except Exception as e:
            logger.warning(f"[{self.account_id}] set_input_ime 失败: {e}")

        # 5b. OCR 找占位文字
        img = np.array(d.screenshot(format="pillow"))
        gray_img = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
        enhanced = self._clahe_enhance(gray_img)
        enhanced_bgr = cv2.cvtColor(enhanced, cv2.COLOR_GRAY2BGR)
        results = self._ocr_region(enhanced_bgr, 0, int(h * 0.06), w, int(h * 0.35))

        placeholder_kw = ["这一刻的想法", "这一刻", "说说这一刻", "此刻的想法",
                           "这一刻想说", "这一刻的"]
        input_pos = None
        for bbox, vtext, conf in results:
            for kw in placeholder_kw:
                if kw in vtext:
                    cx = int((bbox[0][0] + bbox[2][0]) / 2)
                    cy = int((bbox[0][1] + bbox[2][1]) / 2)
                    input_pos = (cx, cy)
                    break
            if input_pos:
                break

        if input_pos:
            d.click(*input_pos)
        else:
            d.click(int(w * 0.50), int(h * 0.25))

        time.sleep(0.8)

        # 5c. IME 注入
        try:
            d.clear_text()
            time.sleep(0.2)
        except Exception:
            pass

        try:
            d.send_keys(text)
            time.sleep(0.8)
            logger.debug(f"[{self.account_id}] send_keys 完成")
        except Exception as e:
            logger.warning(f"[{self.account_id}] send_keys 异常: {e}")
            for char in text:
                try:
                    d.send_keys(char)
                    time.sleep(0.05)
                except Exception:
                    pass

        # 5d. 收起 IME，避免挡住「发表」（不要按系统返回，以免退出编辑页）
        try:
            d.set_input_ime(False)
        except Exception:
            pass
        time.sleep(0.6)

    # ================================================================
    # 阶段6: OCR 识别"发表"
    # ================================================================

    def _click_publish(self) -> bool:
        """OCR 识别'发表'按钮 → 点击，并确认离开编辑页。"""
        from config.device_profiles import get_extra

        logger.info(f"[{self.account_id}] 阶段6: 识别发表按钮")
        d, w, h = self.d, self.w, self.h

        # 确保键盘已收起
        if not self._on_compose_page():
            logger.warning(f"[{self.account_id}] 发表前不在编辑页")

        img = np.array(d.screenshot(format="pillow"))
        img_bgr = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
        results = self._ocr_region(img_bgr, int(w * 0.55), 0, w, int(h * 0.18))

        publish_pos = None
        for bbox, text, conf in results:
            if "发表" in str(text) and conf > 0.2:
                cx = int((bbox[0][0] + bbox[2][0]) / 2)
                cy = int((bbox[0][1] + bbox[2][1]) / 2)
                publish_pos = (cx, cy)
                logger.info(f"[{self.account_id}] 发表按钮 @({cx},{cy})")
                break

        if publish_pos:
            d.click(*publish_pos)
        else:
            fb = tuple(get_extra(d, "moments_publish", (0.90, 0.055)))
            logger.warning(f"[{self.account_id}] 发表 OCR 未命中，fallback {fb}")
            d.click(int(w * fb[0]), int(h * fb[1]))

        time.sleep(3.5)
        # 成功：不再停留在「取消/发表/这一刻」编辑页
        if not self._on_compose_page():
            return True
        # 再点一次
        fb2 = tuple(get_extra(d, "moments_publish", (0.90, 0.055)))
        d.click(int(w * fb2[0]), int(h * fb2[1]))
        time.sleep(3.0)
        ok = not self._on_compose_page()
        logger.info(f"[{self.account_id}] 发表后离开编辑页={ok}")
        return ok

    # ================================================================
    # 工具方法
    # ================================================================

    def _is_dimmed(self):
        """检测是否弹出了暗色遮罩（菜单已打开）。"""
        img = np.array(self.d.screenshot(format="pillow"))
        g = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
        center = g[int(self.h * 0.60):int(self.h * 0.80),
                     int(self.w * 0.20):int(self.w * 0.80)]
        return np.mean(center) < 170

    def _is_moments_page(self):
        """检测是否在朋友圈页面。"""
        img = np.array(self.d.screenshot(format="pillow"))
        g = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
        if self._is_dimmed():
            return False
        return np.mean(g[int(self.h * 0.03):int(self.h * 0.12), :]) > 120

    def _clahe_enhance(self, gray_img):
        """CLAHE 增强低对比度文字。"""
        if self._clahe is None:
            self._clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
        return self._clahe.apply(gray_img)

    def _get_ocr(self):
        """获取 EasyOCR 实例（延迟加载）。"""
        if self._ocr is None:
            from utils.ocr_utils import create_easyocr_reader
            self._ocr = create_easyocr_reader()
        return self._ocr

    def _ocr_region(self, img_bgr, x0, y0, x1, y1):
        """对指定区域做 OCR，返回全局坐标结果。"""
        h_img, w_img = img_bgr.shape[:2]
        x0 = max(0, x0)
        y0 = max(0, y0)
        x1 = min(w_img, x1)
        y1 = min(h_img, y1)
        if x0 >= x1 or y0 >= y1:
            return []

        crop = img_bgr[y0:y1, x0:x1]
        reader = self._get_ocr()
        results = reader.readtext(crop)

        global_results = []
        for bbox, text, conf in results:
            global_bbox = [[p[0] + x0, p[1] + y0] for p in bbox]
            global_results.append((global_bbox, text, conf))
        return global_results
