# patch helper — rewrite comment send section
from pathlib import Path

NEW = '''
    def _focus_comment_input(self) -> bool:
        """点击「发表评论：」输入条，唤起系统键盘。"""
        for y0, y1 in ((0.55, 0.95), (0.70, 0.99), (0.45, 0.80)):
            focused = ocr_find_and_click(
                self.d,
                self._get_ocr(),
                ["发表评论", "说点什么", "写评论"],
                y_min_ratio=y0,
                y_max_ratio=y1,
                conf_min=0.3,
                enhance=self._enhance,
            )
            if focused:
                time.sleep(0.6)
                return True
        try:
            from config.device_profiles import get_extra
            pt = get_extra(self.d, "channels_comment_input")
            if pt:
                click_ratio(self.d, float(pt[0]), float(pt[1]))
                time.sleep(0.6)
                return True
        except Exception:
            pass
        click_ratio(self.d, 0.42, 0.88)
        time.sleep(0.6)
        return True

    def _keyboard_with_send_visible(self) -> bool:
        """系统键盘已弹出：能 OCR 到「发送」。"""
        return "发送" in self._input_bar_blob(y_min=0.45)

    def _wait_send_button(self, timeout: float = 4.0) -> bool:
        """等待键盘弹出后出现「发送」（空内容时可能灰色，但仍可见）。"""
        t0 = time.time()
        while time.time() - t0 < timeout:
            if self._keyboard_with_send_visible():
                return True
            time.sleep(0.35)
        return False

    def _type_comment_text(self, text: str) -> bool:
        """
        在系统键盘已弹出时写入评论。

        不要 set_input_ime(True)：ADBKeyboard 会收起系统键盘，
        视频号的「发送」按钮会一起消失。
        """
        try:
            focused = self.d(focused=True)
            if focused.exists(timeout=0.6):
                focused.set_text(text)
                time.sleep(0.45)
                if self._text_in_input(text):
                    return True
        except Exception as e:
            logger.debug(f"[{self.account_id}] set_text 失败: {e}")

        try:
            self.d.set_clipboard(text)
            time.sleep(0.15)
            self.d.shell("input keyevent 279")  # PASTE
            time.sleep(0.45)
            if self._text_in_input(text):
                return True
        except Exception as e:
            logger.debug(f"[{self.account_id}] paste 失败: {e}")

        try:
            self.d.send_keys(text)
            time.sleep(0.45)
            if self._text_in_input(text):
                return True
        except Exception as e:
            logger.debug(f"[{self.account_id}] send_keys 失败: {e}")
        return self._text_in_input(text)

    def _text_in_input(self, text: str) -> bool:
        """输入区是否已出现待发文案。"""
        t = (text or "").strip()
        if not t:
            return False
        blob = self._input_bar_blob(y_min=0.48)
        if t in blob:
            return True
        return len(t) >= 2 and t[:2] in blob

    def _input_bar_blob(self, y_min: float = 0.50) -> str:
        """OCR 下半屏文字（含键盘/输入条，便于找「发送」）。"""
        try:
            img = np.array(self.d.screenshot(format="pillow"))
            gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
            h, w = gray.shape[:2]
            region = self._enhance(gray[int(h * y_min):h, 0:w])
            results = self._get_ocr().readtext(cv2.cvtColor(region, cv2.COLOR_GRAY2BGR))
            return " ".join(str(t) for _, t, c in results if c >= 0.3)
        except Exception:
            return ""

    def _strict_input_blob(self) -> str:
        """键盘弹起扫中下部；否则扫底部输入条。"""
        if self._keyboard_with_send_visible():
            return self._input_bar_blob(y_min=0.48)
        return self._input_bar_blob(y_min=0.82)

    def _find_channels_send_green(self):
        """键盘上方右侧微信绿「发送」（有内容后常由灰变绿）。"""
        try:
            shot = self.d.screenshot(format="opencv")
            if shot is None:
                return None
            h, w = shot.shape[:2]
            hsv = cv2.cvtColor(shot, cv2.COLOR_BGR2HSV)
            mask = cv2.inRange(hsv, np.array([35, 60, 60]), np.array([95, 255, 255]))
            y0, y1 = int(h * 0.45), int(h * 0.78)
            x0 = int(w * 0.70)
            mask[:y0, :] = 0
            mask[y1:, :] = 0
            mask[:, :x0] = 0
            contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            best = None
            min_area = w * h * 0.0005
            max_area = w * h * 0.05
            for c in contours:
                x, y, bw, bh = cv2.boundingRect(c)
                area = bw * bh
                if area < min_area or area > max_area:
                    continue
                if bw < 18 or bh < 16:
                    continue
                if bw > w * 0.40 or bh > h * 0.12:
                    continue
                cx, cy = x + bw // 2, y + bh // 2
                if best is None or area > best[0]:
                    best = (area, cx, cy)
            if best is None:
                return None
            return best[1] / w, best[2] / h
        except Exception as e:
            logger.debug(f"[{self.account_id}] 评论绿钮检测失败: {e}")
            return None

    def _click_send_by_uiautomator(self) -> bool:
        """控件树若暴露「发送」则直接点。"""
        try:
            node = self.d(text="发送")
            if node.exists(timeout=0.8):
                node.click()
                return True
        except Exception:
            pass
        try:
            node = self.d(description="发送")
            if node.exists(timeout=0.5):
                node.click()
                return True
        except Exception:
            pass
        return False

    def _send_comment_text(self) -> bool:
        """键盘仍在时点击「发送」（表情行右侧）。"""
        pt = self._find_channels_send_green()
        if pt:
            logger.info(f"[{self.account_id}] 绿钮发送 @({pt[0]:.3f},{pt[1]:.3f})")
            click_ratio(self.d, float(pt[0]), float(pt[1]))
            return True

        if self._click_send_by_uiautomator():
            logger.info(f"[{self.account_id}] u2 点发送")
            return True

        for y0, y1 in ((0.48, 0.78), (0.42, 0.72), (0.50, 0.85)):
            if ocr_find_and_click(
                self.d,
                self._get_ocr(),
                ["发送"],
                y_min_ratio=y0,
                y_max_ratio=y1,
                conf_min=0.28,
                enhance=self._enhance,
            ):
                logger.info(f"[{self.account_id}] OCR 点发送 y={y0}-{y1}")
                return True

        try:
            from config.device_profiles import get_extra
            pts = list(get_extra(self.d, "channels_comment_send_candidates") or [])
            pt2 = get_extra(self.d, "channels_comment_send")
            if pt2:
                pts.append(pt2)
            for cand in pts:
                if not cand:
                    continue
                click_ratio(self.d, float(cand[0]), float(cand[1]))
                time.sleep(0.45)
                if self._comment_published_quick():
                    return True
        except Exception:
            pass

        for rx, ry in ((0.90, 0.62), (0.92, 0.60), (0.88, 0.64), (0.90, 0.58)):
            click_ratio(self.d, rx, ry)
            time.sleep(0.4)
            if self._comment_published_quick():
                return True
        return False

    def _comment_published_quick(self) -> bool:
        """发送后通常键盘收起，底部回到「发表评论」。"""
        time.sleep(0.25)
        if self._keyboard_with_send_visible():
            return False
        blob = self._input_bar_blob(y_min=0.70)
        return "发表评论" in blob or "说点什么" in blob

    def _comment_published(self, text: str) -> bool:
        """发送成功：键盘收起 + 占位恢复，输入条不残留原文。"""
        t = (text or "").strip()
        if self._keyboard_with_send_visible():
            return False
        blob = self._input_bar_blob(y_min=0.72)
        if "发表评论" in blob or "说点什么" in blob:
            if t and t in self._input_bar_blob(y_min=0.82):
                return False
            return True
        return False

    def _comment_current(self, text: str) -> bool:
        """
        开评论半屏 → 点输入框弹系统键盘 → 出现「发送」
        → 写入文字（不切 ADBKeyboard）→ 点发送。
        """
        if not text:
            return False
        text = text[:40]
        logger.info(f"[{self.account_id}] 视频号发表评论: {text}")

        if not self._open_comment_panel():
            self._go_back_to_video()
            return False

        # 禁用 ADBKeyboard，保留系统键盘路径
        try:
            self.d.set_input_ime(False)
        except Exception:
            pass

        if not self._focus_comment_input():
            self._go_back_to_video()
            return False

        if not self._wait_send_button(timeout=4.5):
            self._focus_comment_input()
            if not self._wait_send_button(timeout=3.0):
                logger.warning(f"[{self.account_id}] 键盘/发送按钮未出现")
                self._go_back_to_video()
                return False

        logger.info(f"[{self.account_id}] 已检测到发送按钮（键盘已弹出）")

        if not self._type_comment_text(text):
            logger.warning(f"[{self.account_id}] 评论文字未写入，重试")
            self._focus_comment_input()
            self._wait_send_button(timeout=2.5)
            if not self._type_comment_text(text):
                self._go_back_to_video()
                return False

        time.sleep(0.5)
        if not self._keyboard_with_send_visible():
            self._focus_comment_input()
            self._wait_send_button(timeout=2.0)

        self._send_comment_text()
        time.sleep(1.0)
        ok = self._comment_published(text)
        if not ok:
            logger.warning(f"[{self.account_id}] 评论未发出，重试点发送")
            if self._keyboard_with_send_visible() or self._wait_send_button(2.0):
                if not self._text_in_input(text):
                    self._type_comment_text(text)
                    time.sleep(0.4)
                self._send_comment_text()
                time.sleep(1.0)
                ok = self._comment_published(text)

        self._go_back_to_video()
        if ok:
            logger.info(f"[{self.account_id}] 视频号评论已发送")
        else:
            logger.warning(f"[{self.account_id}] 视频号评论发送失败")
        return ok

'''

path = Path('core/channels_browser.py')
text = path.read_text(encoding='utf-8')
start = text.index('    def _focus_comment_input(self) -> bool:')
end = text.index('    # ================================================================\n    # 页面检测 + 恢复')
path.write_text(text[:start] + NEW.lstrip('\n') + text[end:], encoding='utf-8')
print('OK', start, end)
