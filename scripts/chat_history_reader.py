"""
聊天页 OCR 读取 — 从当前屏幕提取对话记录。

微信屏蔽无障碍树，因此用 OCR + 气泡左右位置推断发言方：
- 右侧 (x > 52% 屏宽) → 自己
- 左侧 → 好友
"""

from __future__ import annotations

import random
import re
import time
from typing import Optional

import cv2
import numpy as np

from utils.logger import get_logger

logger = get_logger("chat_history_reader")

# 语音气泡时长：3''、12″、5秒 等
_VOICE_DURATION_RE = re.compile(
    r"^\d{1,3}(['″\"″秒]+)?$|^\d{1,3}\s*['″\"″]+$"
)
_VOICE_PLACEHOLDER = "[语音消息]"
_VOICE_MENU_KEYWORDS = ("转文字", "转文宇", "转成文字", "转为文字")
_CHAT_TIMESTAMP_RE = re.compile(
    r"^(凌晨|早上|上午|中午|下午|晚上)?\d{1,2}[:：;.\uFF1a\uFF1b\uFF0e]\d{2}$"
)

# 聊天页 UI 噪音（非消息内容）
_UI_NOISE = frozenset(
    {
        "发送",
        "按住",
        "说话",
        "表情",
        "更多",
        "微信",
        "相册",
        "拍摄",
        "视频通话",
        "语音通话",
        "[视频]",
        "[图片]",
        "[文件]",
        "[链接]",
        "文件",
        "红包",
        "转账",
        "名片",
        "位置",
        "收藏",
        "输入",
        "返回",
        "聊天信息",
        "免打扰",
        "置顶",
        "查找聊天内容",
    }
)


class ChatHistoryReader:
    """读取当前聊天页可见消息。"""

    def __init__(self, d, account_id: str = ""):
        self.d = d
        self.account_id = account_id
        self.w, self.h = d.info["displayWidth"], d.info["displayHeight"]
        self._ocr = None
        self._clahe = None

    def scroll_up_for_history(self, times: int = 1) -> None:
        """上滑加载更早消息（手指从下往上）。"""
        for _ in range(max(0, times)):
            self.d.swipe(
                int(self.w * 0.5),
                int(self.h * 0.35),
                int(self.w * 0.5),
                int(self.h * 0.72),
                duration=0.35,
            )
            time.sleep(0.8)

    def scroll_to_bottom(self, times: int = 2) -> None:
        """下滑回到最新消息。"""
        for _ in range(max(0, times)):
            self.d.swipe(
                int(self.w * 0.5),
                int(self.h * 0.72),
                int(self.w * 0.5),
                int(self.h * 0.35),
                duration=0.35,
            )
            time.sleep(0.5)

    def capture_chat_region(self) -> np.ndarray:
        """截取聊天消息区域（BGR，与 OCR 使用同一裁剪）。"""
        y_top = int(self.h * 0.10)
        y_bottom = int(self.h * 0.84)

        img = np.array(self.d.screenshot(format="pillow"))
        gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
        if self._clahe is None:
            self._clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        enhanced = self._clahe.apply(gray)
        crop = enhanced[y_top:y_bottom, :]
        return cv2.cvtColor(crop, cv2.COLOR_GRAY2BGR)

    def ocr_from_crop(
        self,
        crop_bgr: np.ndarray,
        contact_name: str = "",
    ) -> list[dict]:
        """对给定聊天区域截图做 OCR（不滚动、不重新截屏）。"""
        x_mid = int(self.w * 0.52)
        y_top = int(self.h * 0.10)

        reader = self._ensure_ocr()
        raw = reader.readtext(crop_bgr)

        items: list[dict] = []
        for bbox, text, conf in raw:
            if conf < 0.28:
                continue
            t = str(text or "").strip()
            if not t:
                continue
            if len(t) < 2 and not self._is_voice_fragment(t):
                continue
            if self._is_noise(t) and not self._is_voice_duration(t):
                continue
            xs = [p[0] for p in bbox]
            ys = [p[1] for p in bbox]
            cx = int(sum(xs) / len(xs))
            cy = int(sum(ys) / len(ys)) + y_top
            if self._is_chat_timestamp(t, cx):
                continue
            role = "self" if cx >= x_mid else "friend"
            items.append({"role": role, "text": t, "x": cx, "y": cy, "conf": conf})

        merged = self._merge_lines(items, contact_name=contact_name)
        merged.sort(key=lambda x: x.get("y", 0))
        return [{"role": item["role"], "text": item["text"]} for item in merged]

    def read_messages_no_scroll(
        self,
        contact_name: str = "",
    ) -> list[dict]:
        """读取当前屏可见消息，不滚动。"""
        crop = self.capture_chat_region()
        return self.ocr_from_crop(crop, contact_name=contact_name)

    def _collect_visible_ocr(self, scroll_up: int) -> list[dict]:
        """OCR 当前屏；若 scroll_up>0 再上滑补扫历史后回到底部。"""
        chunks: list[dict] = list(self._ocr_chat_region())
        if scroll_up > 0:
            for _ in range(scroll_up):
                self.scroll_up_for_history(1)
                chunks.extend(self._ocr_chat_region())
            self.scroll_to_bottom(times=scroll_up + 1)
        return self._dedupe_visible(chunks)

    @staticmethod
    def _dedupe_visible(items: list[dict]) -> list[dict]:
        seen: set[tuple[str, int, str]] = set()
        out: list[dict] = []
        for item in items:
            role = item.get("role", "")
            y_bucket = int(item.get("y", 0)) // 24
            text = str(item.get("text", "")).strip()
            key = (role, y_bucket, text)
            if key in seen:
                continue
            seen.add(key)
            out.append(item)
        return out

    def read_messages(
        self,
        contact_name: str = "",
        scroll_up: int = 1,
    ) -> list[dict]:
        """
        读取聊天页消息列表。

        Returns:
            [{"role": "self"|"friend", "text": "...", "y": int}, ...] 按 y 排序
        """
        if scroll_up > 0:
            self.scroll_up_for_history(scroll_up)
        visible = self._ocr_chat_region()
        self.scroll_to_bottom(times=scroll_up + 1)

        merged = self._merge_lines(visible, contact_name=contact_name)
        merged.sort(key=lambda x: x.get("y", 0))
        out: list[dict] = []
        for item in merged:
            out.append({"role": item["role"], "text": item["text"]})
        return out

    def read_messages_with_voice(
        self,
        contact_name: str = "",
        scroll_up: int = 1,
        max_voice_transcribe: int = 4,
        transcribe_friend_only: bool = False,
    ) -> list[dict]:
        """
        读取聊天消息，并对未转写的语音气泡调用微信「转文字」后 OCR 结果。

        默认双方语音均转写，供 LLM 获取完整对话上下文。

        Returns:
            [{"role": "self"|"friend", "text": "...", "type": "text"|"voice"}, ...]
        """
        visible = self._collect_visible_ocr(scroll_up)

        merged_all = self._merge_lines(visible, contact_name=contact_name)
        merged_all.sort(key=lambda x: x.get("y", 0))

        voice_bubbles = self._find_voice_bubbles(merged_all, visible, contact_name)
        merged = [
            item
            for item in merged_all
            if not self._is_chat_timestamp(
                item.get("text", ""), item.get("x", 0)
            )
        ]
        transcript_map, consumed_transcript_y = self._map_existing_transcripts(
            voice_bubbles, merged
        )

        transcribe_budget = max(0, max_voice_transcribe)
        live_transcripts: dict[int, str] = dict(transcript_map)
        used_transcript_texts = {
            self._norm_transcript(t) for t in live_transcripts.values() if t
        }

        for vb in sorted(
            voice_bubbles,
            key=lambda v: (v.get("role") == "self", -v.get("y", 0)),
        ):
            y_key = vb.get("y", 0)
            if live_transcripts.get(y_key):
                continue
            if transcribe_budget <= 0:
                break
            role = vb.get("role", "friend")
            if transcribe_friend_only and role != "friend":
                continue
            x, y = self._voice_press_point(
                vb.get("x", int(self.w * 0.25)),
                y_key,
                role,
            )
            transcript = self.transcribe_voice_at(x, y, role=role)
            transcribe_budget -= 1
            if transcript:
                live_transcripts[y_key] = transcript
                used_transcript_texts.add(self._norm_transcript(transcript))
            time.sleep(random.uniform(0.5, 1.5))

        out: list[dict] = []
        matched_voice_y: set[int] = set()

        for item in merged:
            y_key = item.get("y", 0)
            if y_key in consumed_transcript_y:
                continue

            text_norm = self._norm_transcript(item.get("text", ""))
            if text_norm and text_norm in used_transcript_texts:
                continue

            voice_match = self._match_voice_bubble(y_key, voice_bubbles, matched_voice_y)
            if voice_match and self._is_probable_text_message(item.get("text", "")):
                voice_match = None

            if voice_match or self._is_voice_duration(item.get("text", "")):
                vb = voice_match or {"y": y_key, "role": item.get("role", "friend")}
                vy = vb.get("y", y_key)
                matched_voice_y.add(vy)
                duration = self._parse_voice_duration(item.get("text", "")) or vb.get(
                    "duration"
                )
                role = vb.get("role", item.get("role", "friend"))
                transcript = live_transcripts.get(vy, "")
                if not transcript:
                    transcript = _VOICE_PLACEHOLDER
                    if duration:
                        transcript = f"{_VOICE_PLACEHOLDER} {duration}秒"
                out.append(
                    {
                        "role": role,
                        "text": transcript,
                        "type": "voice",
                        "duration": duration,
                        "y": vy,
                    }
                )
                continue

            if self._is_voice_marker(item.get("text", "")):
                role = item.get("role", "friend")
                vy = y_key
                matched_voice_y.add(vy)
                transcript = live_transcripts.get(vy, _VOICE_PLACEHOLDER)
                out.append(
                    {
                        "role": role,
                        "text": transcript,
                        "type": "voice",
                        "y": vy,
                    }
                )
                continue

            out.append(
                {
                    "role": item.get("role", "friend"),
                    "text": item.get("text", ""),
                    "type": "text",
                    "y": y_key,
                }
            )

        for vb in voice_bubbles:
            vy = vb.get("y", 0)
            if vy in matched_voice_y:
                continue
            role = vb.get("role", "friend")
            duration = vb.get("duration")
            transcript = live_transcripts.get(vy, "")
            if not transcript or _VOICE_PLACEHOLDER in transcript:
                continue
            matched_voice_y.add(vy)
            out.append(
                {
                    "role": role,
                    "text": transcript,
                    "type": "voice",
                    "duration": duration,
                    "y": vy,
                }
            )

        out.sort(key=lambda item: item.get("y", 0))
        for item in out:
            item.pop("y", None)
        return out

    def _voice_press_point(self, x: int, y: int, role: str) -> tuple[int, int]:
        """将 OCR 时长文字坐标校正到气泡可长按区域。"""
        if role == "self":
            px = max(x, int(self.w * 0.62))
            px = min(px, int(self.w * 0.88))
        else:
            if x < int(self.w * 0.16):
                px = int(self.w * 0.30)
            else:
                px = max(int(self.w * 0.18), min(x, int(self.w * 0.42)))
        py = max(int(self.h * 0.12), min(y, int(self.h * 0.80)))
        return px, py

    def transcribe_voice_at(
        self,
        x: int,
        y: int,
        retries: int = 2,
        role: str = "friend",
    ) -> str:
        """长按语音气泡 → 点「转文字」→ OCR 转写结果。"""
        candidates = self._voice_press_candidates(x, y, role)
        logger.info(
            f"[{self.account_id}] 开始转写语音 role={role} "
            f"候选点={candidates[:3]}"
        )
        for attempt in range(retries + 1):
            px, py = candidates[min(attempt, len(candidates) - 1)]
            try:
                self._long_press(px, py, hold_ms=1000)
                time.sleep(0.8)
                if not self._click_voice_to_text_menu():
                    logger.warning(
                        f"[{self.account_id}] 未找到「转文字」菜单 "
                        f"(attempt={attempt + 1}, @({px},{py}))"
                    )
                    self._save_debug_screenshot(f"no_menu_{attempt}")
                    self._dismiss_popup()
                    if attempt < retries:
                        time.sleep(0.6)
                        continue
                    return ""

                text = self._poll_transcript(px, py, timeout=8.0)
                self._dismiss_popup()
                if text and len(text) >= 2:
                    logger.info(
                        f"[{self.account_id}] 语音转写成功: {text[:40]}"
                    )
                    return text

                logger.warning(
                    f"[{self.account_id}] 转写结果为空 "
                    f"(attempt={attempt + 1}, @({px},{py}))"
                )
                self._save_debug_screenshot(f"no_text_{attempt}")
                if attempt < retries:
                    time.sleep(0.8)
                    continue
            except Exception as e:
                logger.warning(f"[{self.account_id}] 语音转写失败: {e}")
                self._dismiss_popup()

        return ""

    def _voice_press_candidates(
        self, x: int, y: int, role: str
    ) -> list[tuple[int, int]]:
        """生成多个候选长按点，避免点到头像或空白区。"""
        primary = self._voice_press_point(x, y, role)
        py = primary[1]
        if role == "self":
            xs = [primary[0], int(self.w * 0.72), int(self.w * 0.68), int(self.w * 0.78)]
        else:
            xs = [primary[0], int(self.w * 0.30), int(self.w * 0.35), int(self.w * 0.26)]
        out: list[tuple[int, int]] = []
        seen: set[tuple[int, int]] = set()
        for px in xs:
            px = max(int(self.w * 0.12), min(px, int(self.w * 0.88)))
            key = (px // 8, py // 8)
            if key in seen:
                continue
            seen.add(key)
            out.append((px, py))
        return out or [primary]

    def _click_voice_to_text_menu(self) -> bool:
        """点击长按菜单中的「转文字」。"""
        try:
            el = self.d(text="转文字")
            if el.exists(timeout=1.5):
                el.click()
                time.sleep(0.3)
                return True
        except Exception:
            pass

        img = np.array(self.d.screenshot(format="pillow"))
        gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
        if self._clahe is None:
            self._clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        enhanced = cv2.cvtColor(self._clahe.apply(gray), cv2.COLOR_GRAY2BGR)

        matches: list[tuple[float, int, int, str]] = []
        for text, cx, cy, conf in self._ocr_region(enhanced, 0, 0, self.w, self.h):
            t = str(text or "").strip().replace(" ", "")
            if not t or conf < 0.2:
                continue
            matched = False
            for kw in _VOICE_MENU_KEYWORDS:
                if kw in t:
                    matches.append((conf, cx, cy, t))
                    matched = True
                    break
            if not matched and len(t) <= 8 and "转" in t and "文" in t:
                matches.append((conf * 0.9, cx, cy, t))

        if not matches:
            return False

        matches.sort(key=lambda m: m[0], reverse=True)
        _conf, cx, cy, label = matches[0]
        logger.debug(f"[{self.account_id}] OCR 点击菜单: {label} @({cx},{cy})")
        self.d.click(cx, cy)
        time.sleep(0.3)
        return True

    def _poll_transcript(self, x: int, y: int, timeout: float = 8.0) -> str:
        """轮询等待微信转写完成并 OCR 读取。"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            for fn in (self._ocr_transcript_near_bubble, self._ocr_transcript_below):
                text = fn(x, y)
                if text and len(text) >= 2:
                    return text
            time.sleep(0.7)
        return ""

    def _ocr_transcript_near_bubble(self, x: int, y: int) -> str:
        """OCR 气泡本体及紧邻区域（转写可能覆盖气泡或紧贴下方）。"""
        y0 = max(int(self.h * 0.10), y - int(self.h * 0.02))
        y1 = min(int(self.h * 0.84), y + int(self.h * 0.12))
        x0 = max(0, x - int(self.w * 0.42))
        x1 = min(self.w, x + int(self.w * 0.42))
        return self._ocr_best_text_in_region(x0, y0, x1, y1)

    def _ocr_best_text_in_region(
        self,
        x0: int,
        y0: int,
        x1: int,
        y1: int,
    ) -> str:
        img = np.array(self.d.screenshot(format="pillow"))
        gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
        if self._clahe is None:
            self._clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        enhanced = cv2.cvtColor(self._clahe.apply(gray), cv2.COLOR_GRAY2BGR)

        lines: list[tuple[int, float, str]] = []
        for text, _cx, cy, conf in self._ocr_region(enhanced, x0, y0, x1, y1):
            t = str(text or "").strip()
            if not t or self._is_noise(t):
                continue
            if self._is_voice_duration(t) or self._is_voice_marker(t):
                continue
            if self._is_chat_timestamp(t, _cx):
                continue
            if not self._looks_like_transcript(t):
                continue
            lines.append((cy, conf, t))

        if not lines:
            return ""

        lines.sort(key=lambda item: (item[0], -item[1]))
        row_tol = int(self.h * 0.025)
        merged: list[str] = []
        bucket_y = -1
        bucket_parts: list[str] = []
        for cy, _conf, t in lines:
            if bucket_y < 0 or abs(cy - bucket_y) > row_tol:
                if bucket_parts:
                    merged.append(self._join_text_parts(bucket_parts))
                bucket_parts = [t]
                bucket_y = cy
            else:
                bucket_parts.append(t)
        if bucket_parts:
            merged.append(self._join_text_parts(bucket_parts))

        merged = [m for m in merged if len(m) >= 2]
        return max(merged, key=len) if merged else ""

    @staticmethod
    def _join_text_parts(parts: list[str]) -> str:
        if not parts:
            return ""
        out = parts[0]
        for part in parts[1:]:
            out = ChatHistoryReader._join_text(out, part)
        return out

    def _save_debug_screenshot(self, tag: str) -> None:
        try:
            from pathlib import Path

            out_dir = Path("logs") / "voice_transcribe"
            out_dir.mkdir(parents=True, exist_ok=True)
            path = out_dir / f"{tag}_{int(time.time())}.png"
            self.d.screenshot(str(path))
            logger.info(f"[{self.account_id}] 调试截图: {path}")
        except Exception as e:
            logger.debug(f"保存调试截图失败: {e}")

    def _long_press(self, x: int, y: int, hold_ms: int = 800) -> None:
        hold_ms = max(400, min(int(hold_ms), 2000))
        try:
            self.d.shell(f"input swipe {x} {y} {x} {y} {hold_ms}")
            return
        except Exception:
            pass
        try:
            self.d.long_click(x, y, duration=hold_ms / 1000.0)
        except Exception as e:
            logger.debug(f"[{self.account_id}] 长按失败: {e}")

    def _click_ocr_keyword(self, keyword: str) -> bool:
        """全屏 OCR 找关键词并点击。"""
        return self._click_voice_to_text_menu() if keyword == "转文字" else self._click_ocr_keyword_raw(keyword)

    def _click_ocr_keyword_raw(self, keyword: str) -> bool:
        img = np.array(self.d.screenshot(format="pillow"))
        gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
        if self._clahe is None:
            self._clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        enhanced = cv2.cvtColor(self._clahe.apply(gray), cv2.COLOR_GRAY2BGR)
        for text, cx, cy, conf in self._ocr_region(enhanced, 0, 0, self.w, self.h):
            if keyword in text and conf > 0.25:
                self.d.click(cx, cy)
                return True
        return False

    def _ocr_transcript_below(self, x: int, y: int) -> str:
        """OCR 语音气泡下方带状区域的转写文字。"""
        y0 = max(int(self.h * 0.10), y + 15)
        y1 = min(int(self.h * 0.84), y + int(self.h * 0.14))
        x0 = max(0, x - int(self.w * 0.42))
        x1 = min(self.w, x + int(self.w * 0.42))
        return self._ocr_best_text_in_region(x0, y0, x1, y1)

    def _dismiss_popup(self) -> None:
        """关闭长按菜单（避免 back 取消已展示的转写文字）。"""
        try:
            self.d.click(int(self.w * 0.5), int(self.h * 0.06))
            time.sleep(0.25)
        except Exception:
            pass

    def _ocr_region(
        self,
        img_bgr: np.ndarray,
        x0: int,
        y0: int,
        x1: int,
        y1: int,
    ) -> list[tuple[str, int, int, float]]:
        h_i, w_i = img_bgr.shape[:2]
        x0, y0 = max(0, x0), max(0, y0)
        x1, y1 = min(w_i, x1), min(h_i, y1)
        if x0 >= x1 or y0 >= y1:
            return []
        crop = img_bgr[y0:y1, x0:x1]
        raw = self._ensure_ocr().readtext(crop)
        out: list[tuple[str, int, int, float]] = []
        for bbox, text, conf in raw:
            cx = int((bbox[0][0] + bbox[2][0]) / 2) + x0
            cy = int((bbox[0][1] + bbox[2][1]) / 2) + y0
            out.append((str(text or "").strip(), cx, cy, conf))
        return out

    @staticmethod
    def _is_voice_duration(text: str) -> bool:
        t = re.sub(r"\s+", "", str(text or "").strip())
        if not t:
            return False
        if _VOICE_DURATION_RE.match(t):
            return True
        if re.fullmatch(r"['″\"″]+", t):
            return True
        return False

    @staticmethod
    def _parse_voice_duration(text: str) -> Optional[int]:
        t = re.sub(r"\s+", "", str(text or "").strip())
        m = re.match(r"(\d{1,3})", t)
        if m:
            return int(m.group(1))
        return None

    def _is_chat_timestamp(self, text: str, x: int = 0) -> bool:
        t = re.sub(r"\s+", "", str(text or "").strip())
        if not t:
            return False
        if _CHAT_TIMESTAMP_RE.match(t):
            return True
        if len(t) <= 10 and re.search(r"(昨天|星期)", t):
            return True
        if x and int(self.w * 0.32) <= x <= int(self.w * 0.68):
            if re.search(r"\d{1,2}[:：;]\d{2}", t) and len(t) <= 10:
                return True
        return False

    @staticmethod
    def _is_voice_marker(text: str) -> bool:
        t = str(text or "").strip()
        return t in ("[语音]", "[语音消息]", "语音") or t.startswith("[语音")

    @staticmethod
    def _is_probable_text_message(text: str) -> bool:
        """是否为正常文字消息（不应被语音气泡匹配覆盖）。"""
        t = str(text or "").strip()
        if not t:
            return False
        if ChatHistoryReader._is_voice_duration(t):
            return False
        if ChatHistoryReader._is_voice_marker(t):
            return False
        if len(t) >= 3:
            return True
        return False

    @staticmethod
    def _looks_like_transcript(text: str) -> bool:
        t = str(text or "").strip()
        if len(t) < 2:
            return False
        cjk = re.findall(r"[\u4e00-\u9fff]", t)
        return len(cjk) >= 2

    def _match_voice_bubble(
        self,
        y: int,
        voice_bubbles: list[dict],
        matched_voice_y: set[int],
    ) -> Optional[dict]:
        tol = int(self.h * 0.035)
        for vb in voice_bubbles:
            vy = vb.get("y", 0)
            if vy in matched_voice_y:
                continue
            if abs(vy - y) <= tol:
                return vb
        return None

    def _on_bubble_side(self, role: str, x: int) -> bool:
        if role == "self":
            return x >= int(self.w * 0.50)
        return x <= int(self.w * 0.50)

    def _find_voice_bubbles(
        self,
        merged: list[dict],
        raw_items: list[dict],
        contact_name: str = "",
    ) -> list[dict]:
        """从 OCR + 图像轮廓 + 时间戳间隙 + Vision 识别语音气泡。"""
        row_tol = int(self.h * 0.035)
        bubbles: list[dict] = []
        seen: set[tuple[str, int]] = set()

        def _add(role: str, y: int, x: int, duration_text: str = "") -> None:
            bucket = y // max(row_tol, 1)
            key = (role, bucket)
            if key in seen:
                return
            seen.add(key)
            bubbles.append(
                {
                    "role": role,
                    "y": y,
                    "x": x
                    if x
                    else (
                        int(self.w * 0.75) if role == "self" else int(self.w * 0.25)
                    ),
                    "duration": self._parse_voice_duration(duration_text),
                }
            )

        for item in merged:
            text = item.get("text", "")
            if self._is_voice_duration(text) or self._is_voice_marker(text):
                _add(
                    item.get("role", "friend"),
                    item.get("y", 0),
                    item.get("x", 0),
                    text,
                )

        for raw in raw_items:
            text = raw.get("text", "")
            x = raw.get("x", 0)
            y = raw.get("y", 0)
            role = raw.get("role", "friend")
            if self._is_chat_timestamp(text, x):
                continue
            if not self._on_bubble_side(role, x):
                continue
            if self._is_voice_duration(text) or self._is_voice_fragment(text):
                _add(role, y, x, text)
            elif self._is_voice_marker(text):
                _add(role, y, x, text)

        self._infer_voice_near_timestamps(merged, raw_items, _add)
        self._infer_voice_in_message_gaps(merged, _add)
        for vb in self._find_voice_bubbles_cv(merged):
            _add(vb["role"], vb["y"], vb["x"], "")
        for vb in self._find_voice_bubbles_vision(contact_name):
            _add(vb["role"], vb["y"], vb["x"], "")

        bubbles.sort(key=lambda b: b.get("y", 0))
        logger.info(
            f"[{self.account_id}] 语音气泡: 共 {len(bubbles)} "
            f"(友 {sum(1 for b in bubbles if b.get('role')=='friend')}"
            f" / 我 {sum(1 for b in bubbles if b.get('role')=='self')})"
        )
        return bubbles

    def _infer_voice_near_timestamps(
        self,
        merged: list[dict],
        raw_items: list[dict],
        add_fn,
    ) -> None:
        """时间戳附近常有语音气泡；OCR 可能只识别到时间而未识别波形。"""
        band = int(self.h * 0.12)
        min_gap = int(self.h * 0.035)
        timestamps: list[int] = []

        for item in list(merged) + list(raw_items):
            text = item.get("text", "")
            x = item.get("x", 0)
            y = item.get("y", 0)
            if self._is_chat_timestamp(text, x):
                timestamps.append(y)

        for y in sorted(set(timestamps)):
            y_top = y - band
            friend_msgs = [
                m
                for m in merged
                if m.get("role") == "friend"
                and y_top < m.get("y", 0) < y
                and not self._is_chat_timestamp(m.get("text", ""), m.get("x", 0))
                and not self._is_voice_duration(m.get("text", ""))
                and not self._is_voice_marker(m.get("text", ""))
            ]

            probe_points: list[tuple[int, int]] = []
            if friend_msgs:
                last_y = max(m.get("y", 0) for m in friend_msgs)
                gap = y - last_y
                if gap >= min_gap:
                    probe_points.append(((last_y + y) // 2, int(self.w * 0.22)))
            else:
                probe_points.append((y - int(self.h * 0.055), int(self.w * 0.22)))

            raw_friend = [
                r
                for r in raw_items
                if r.get("role") == "friend"
                and y_top < r.get("y", 0) < y
                and self._on_bubble_side("friend", r.get("x", 0))
            ]
            for r in raw_friend:
                rt = r.get("text", "")
                if (
                    self._is_voice_duration(rt)
                    or self._is_voice_fragment(rt)
                    or self._is_voice_marker(rt)
                    or len(rt) <= 3
                ):
                    add_fn("friend", r.get("y", 0), r.get("x", 0), rt)

            for py, px in probe_points:
                add_fn("friend", py, px, "")

    def _infer_voice_in_message_gaps(
        self,
        merged: list[dict],
        add_fn,
    ) -> None:
        """好友文字与己方回复之间间距异常大时，中间可能有语音气泡。"""
        items = sorted(
            [
                m
                for m in merged
                if not self._is_chat_timestamp(m.get("text", ""), m.get("x", 0))
                and not self._is_voice_duration(m.get("text", ""))
                and not self._is_voice_marker(m.get("text", ""))
            ],
            key=lambda x: x.get("y", 0),
        )
        if len(items) < 2:
            return

        min_gap = int(self.h * 0.078)
        for i in range(len(items) - 1):
            a, b = items[i], items[i + 1]
            y1, y2 = a.get("y", 0), b.get("y", 0)
            gap = y2 - y1
            if gap < min_gap:
                continue
            if a.get("role") != "friend" or b.get("role") != "self":
                continue
            mid_y = (y1 + y2) // 2
            add_fn("friend", mid_y, int(self.w * 0.30), "")

    def _find_voice_bubbles_cv(self, merged: list[dict]) -> list[dict]:
        """用浅色气泡轮廓检测 OCR 漏掉的语音条（多为宽扁气泡）。"""
        y_top = int(self.h * 0.10)
        crop = self.capture_chat_region()
        h_crop, w_crop = crop.shape[:2]
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        bg = float(np.median(gray))
        thresh_val = int(min(bg + 28, 225))
        _, mask = cv2.threshold(gray, thresh_val, 255, cv2.THRESH_BINARY)
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (11, 5))
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)

        text_rows: dict[str, set[int]] = {"friend": set(), "self": set()}
        row_tol = int(self.h * 0.03)
        for item in merged:
            role = item.get("role", "friend")
            if self._is_chat_timestamp(item.get("text", ""), item.get("x", 0)):
                continue
            if self._is_voice_duration(item.get("text", "")):
                continue
            bucket = item.get("y", 0) // max(row_tol, 1)
            text_rows.setdefault(role, set()).add(bucket)

        found: list[dict] = []
        for role, x0_pct, x1_pct in (
            ("friend", 0.0, 0.58),
            ("self", 0.42, 1.0),
        ):
            x0 = int(w_crop * x0_pct)
            x1 = int(w_crop * x1_pct)
            region = mask[:, x0:x1]
            contours, _ = cv2.findContours(
                region, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
            )
            for cnt in contours:
                x, y, cw, ch = cv2.boundingRect(cnt)
                if cw < 80 or ch < 22 or ch > 58:
                    continue
                if cw / max(ch, 1) < 1.8:
                    continue
                cy = y + ch // 2 + y_top
                cx = x + cw // 2 + x0
                if role == "friend" and cx < int(w_crop * 0.14):
                    continue
                bucket = cy // max(row_tol, 1)
                if bucket in text_rows.get(role, set()):
                    continue
                found.append({"role": role, "y": cy, "x": cx})
        return found

    def _find_voice_bubbles_vision(self, contact_name: str) -> list[dict]:
        """Vision 兜底：OCR/CV 均未发现好友语音时调用。"""
        try:
            from content.llm_client import LLMClient

            llm = LLMClient()
            if not llm.vision_available:
                return []
            crop = self.capture_chat_region()
            ok, buf = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, 80])
            if not ok:
                return []
            y_top = int(self.h * 0.10)
            crop_h = int(self.h * 0.74)
            raw = llm.detect_voice_bubbles_from_image(buf.tobytes())
            out: list[dict] = []
            for item in raw:
                role = item.get("role", "friend")
                if role not in ("self", "friend"):
                    continue
                y_pct = float(item.get("y_percent", 0))
                y = int(y_top + y_pct * crop_h)
                x = int(
                    self.w * (0.75 if role == "self" else 0.22)
                )
                out.append(
                    {
                        "role": role,
                        "y": y,
                        "x": x,
                        "duration": item.get("duration"),
                    }
                )
            if out:
                logger.info(
                    f"[{self.account_id}] Vision 检测到 {len(out)} 个语音气泡"
                )
            return out
        except Exception as e:
            logger.debug(f"[{self.account_id}] Vision 语音检测失败: {e}")
            return []

    @staticmethod
    def _norm_transcript(text: str) -> str:
        return re.sub(r"[\s。．.!！?？,，;；]+", "", str(text or "")).strip()

    def _map_existing_transcripts(
        self,
        voice_bubbles: list[dict],
        merged: list[dict],
    ) -> tuple[dict[int, str], set[int]]:
        """语音气泡下方若已有转写文字，直接映射 voice_y -> text。"""
        row_tol = int(self.h * 0.10)
        result: dict[int, str] = {}
        consumed: set[int] = set()

        for vb in voice_bubbles:
            vy = vb.get("y", 0)
            role = vb.get("role", "friend")
            best = ""
            best_dist = row_tol + 1
            best_y = 0

            for item in merged:
                if item.get("role") != role:
                    continue
                text = item.get("text", "")
                if self._is_voice_duration(text) or self._is_voice_marker(text):
                    continue
                if not self._looks_like_transcript(text):
                    continue
                if self._is_chat_timestamp(text, item.get("x", 0)):
                    continue
                iy = item.get("y", 0)
                if iy <= vy or iy - vy > row_tol:
                    continue
                dist = iy - vy
                if dist < best_dist:
                    best_dist = dist
                    best = text
                    best_y = iy

            if best:
                result[vy] = best
                consumed.add(best_y)

        return result, consumed

    def _ocr_chat_region(self) -> list[dict]:
        """OCR 聊天区域并标注左右归属。"""
        crop = self.capture_chat_region()
        x_mid = int(self.w * 0.52)
        y_top = int(self.h * 0.10)

        reader = self._ensure_ocr()
        raw = reader.readtext(crop)

        items: list[dict] = []
        for bbox, text, conf in raw:
            if conf < 0.28:
                continue
            t = str(text or "").strip()
            if not t:
                continue
            if len(t) < 2 and not self._is_voice_fragment(t):
                continue
            if self._is_noise(t) and not self._is_voice_duration(t):
                continue
            xs = [p[0] for p in bbox]
            ys = [p[1] for p in bbox]
            cx = int(sum(xs) / len(xs))
            cy = int(sum(ys) / len(ys)) + y_top
            if self._is_chat_timestamp(t, cx):
                continue
            role = "self" if cx >= x_mid else "friend"
            items.append({"role": role, "text": t, "x": cx, "y": cy, "conf": conf})

        return items

    def _merge_lines(
        self,
        items: list[dict],
        contact_name: str = "",
    ) -> list[dict]:
        """同一气泡/同一行的 OCR 碎片合并。"""
        if not items:
            return []

        items = sorted(items, key=lambda x: (x["y"], x["x"]))
        row_tol = int(self.h * 0.025)
        merged: list[dict] = []

        for item in items:
            text = item["text"]
            if contact_name and text == contact_name and item["y"] < self.h * 0.12:
                continue
            placed = False
            for bucket in merged:
                if (
                    bucket["role"] == item["role"]
                    and abs(bucket["y"] - item["y"]) <= row_tol
                ):
                    bucket["text"] = self._join_text(bucket["text"], text)
                    bucket["y"] = min(bucket["y"], item["y"])
                    if item.get("x"):
                        bucket["x"] = item["x"]
                    placed = True
                    break
            if not placed:
                merged.append(
                    {
                        "role": item["role"],
                        "text": text,
                        "y": item["y"],
                        "x": item.get("x", 0),
                    }
                )
        return merged

    @staticmethod
    def _is_voice_fragment(text: str) -> bool:
        t = str(text or "").strip()
        if re.fullmatch(r"\d{1,3}", t):
            return True
        if re.fullmatch(r"['″\"″]+", t):
            return True
        return False

    @staticmethod
    def _join_text(left: str, right: str) -> str:
        if not left:
            return right
        if not right:
            return left
        if left.endswith(right) or right in left:
            return left
        if right.startswith(left):
            return right
        return f"{left}{right}"

    @staticmethod
    def _is_noise(text: str) -> bool:
        t = text.strip()
        if len(t) <= 1:
            return True
        if t in _UI_NOISE:
            return True
        if re.fullmatch(r"\[[^\]]+\]", t):
            if ChatHistoryReader._is_voice_marker(t):
                return False
            return True
        if re.fullmatch(r"[\d:：\-\s]+", t):
            if ChatHistoryReader._is_voice_duration(t):
                return False
            return True
        if "昨天" in t and len(t) < 8:
            return True
        if "星期" in t and len(t) < 10:
            return True
        return False

    def _ensure_ocr(self):
        if self._ocr is None:
            from utils.ocr_utils import create_easyocr_reader

            self._ocr = create_easyocr_reader()
        return self._ocr


def merge_sent_with_ocr(
    ocr_history: list[dict],
    sent_by_script: list[dict],
) -> list[dict]:
    """
    合并 OCR 读到的记录与脚本已知发送内容，去重并保持顺序。

    OCR 可能漏读己方消息，因此把 sent_by_script 补进 history。
    """
    combined = list(ocr_history)
    seen = {(_norm(h.get("text")), h.get("role")) for h in combined}

    for item in sent_by_script:
        key = (_norm(item.get("text")), item.get("role"))
        if key in seen:
            continue
        combined.append({"role": item.get("role", "self"), "text": item.get("text", "")})
        seen.add(key)

    # 去掉空文本
    combined = [h for h in combined if _norm(h.get("text"))]
    return combined


def _norm(text: Optional[str]) -> str:
    return re.sub(r"\s+", "", str(text or "")).strip()
