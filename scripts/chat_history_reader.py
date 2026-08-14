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

# 语音气泡时长：2''、12″、5秒 等（秒数范围 1-60）
_VOICE_DURATION_RE = re.compile(
    r"^(?:[1-9]|[1-5]\d|60)(['″\"″秒]+)?$"
    r"|^(?:[1-9]|[1-5]\d|60)\s*['″\"″]+$"
)
_VOICE_DURATION_IN_TEXT_RE = re.compile(
    r"(?:^|\D)([1-9]|[1-5]\d|60)\s*['″\"″秒]*"
)
_VOICE_PLACEHOLDER = "[语音消息]"
_VOICE_UNTRANSCRIBED_LLM = "（发来语音，未转写，不知道内容）"
_VOICE_MENU_KEYWORDS = ("转文字", "转文宇", "转成文字", "转为文字")
_CHAT_TIMESTAMP_RE = re.compile(
    r"^(凌晨|早上|上午|中午|下午|晚上)?\d{1,2}[:：;.\uFF1a\uFF1b\uFF0e]\d{2}$"
)

# 聊天页 UI 噪音（非消息内容）
_UI_NOISE = frozenset(
    {
        "发送",
        "按住 说话",
        "松开发送",
        "松开 发送",
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
        "请稍等",
        "稍等",
    }
)
_UI_NOISE_PATTERNS = (
    re.compile(r".*撤回了一条消息.*"),
    re.compile(r".*重新编辑.*"),
)
_CHAT_HISTORY_SKIP_TEXTS = frozenset(
    {
        "请稍等",
        "稍等",
        "等等",
    }
)


def _normalize_voice_text_for_llm(text: str) -> str:
    t = text.strip()
    if not t:
        return t
    if t in ("[语音]", "[语音消息]", "语音") or t.startswith("[语音"):
        return _VOICE_UNTRANSCRIBED_LLM
    if _VOICE_PLACEHOLDER in t:
        m = re.search(r"(\d+)\s*秒", t)
        if m:
            return f"{_VOICE_UNTRANSCRIBED_LLM} {m.group(1)}秒"
        return _VOICE_UNTRANSCRIBED_LLM
    return t


def sanitize_chat_history_for_llm(history: list[dict]) -> list[dict]:
    """过滤不应进入 LLM 上下文的 OCR 噪音与脚本占位回复。"""
    out: list[dict] = []
    for item in history:
        text = _normalize_voice_text_for_llm(str(item.get("text", "")))
        if not text:
            continue
        if text in _CHAT_HISTORY_SKIP_TEXTS:
            continue
        if any(p.search(text) for p in _UI_NOISE_PATTERNS):
            continue
        if re.fullmatch(r"\d{1,4}", text):
            continue
        out.append(
            {
                "role": item.get("role", "friend"),
                "text": text,
                **({"type": item["type"]} if item.get("type") else {}),
            }
        )
    return out


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

    def _chat_y_top(self) -> int:
        return int(self.h * 0.10)

    def _chat_y_bottom(self) -> int:
        """聊天消息区下边界（输入栏在 ~0.93，留一点余量）。"""
        return int(self.h * 0.905)

    def capture_chat_region(self) -> np.ndarray:
        """截取聊天消息区域（灰度增强 BGR，供 EasyOCR）。"""
        y_top = self._chat_y_top()
        y_bottom = self._chat_y_bottom()

        img = np.array(self.d.screenshot(format="pillow"))
        gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
        if self._clahe is None:
            self._clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        enhanced = self._clahe.apply(gray)
        crop = enhanced[y_top:y_bottom, :]
        return cv2.cvtColor(crop, cv2.COLOR_GRAY2BGR)

    def capture_chat_region_color(self) -> np.ndarray:
        """截取聊天消息区域（彩色 BGR，供 Vision 识左右气泡颜色）。"""
        y_top = self._chat_y_top()
        y_bottom = self._chat_y_bottom()
        img = np.array(self.d.screenshot(format="pillow"))
        crop = img[y_top:y_bottom, :]
        return cv2.cvtColor(crop, cv2.COLOR_RGB2BGR)

    def capture_chat_jpeg(self, max_width: int = 720, quality: int = 78) -> bytes:
        """当前聊天区彩色 JPEG（压缩后给 Vision）。"""
        crop = self.capture_chat_region_color()
        if crop is None or crop.size == 0:
            return b""
        h, w = crop.shape[:2]
        if w > max_width:
            scale = max_width / float(w)
            crop = cv2.resize(crop, (max_width, int(h * scale)))
        ok, buf = cv2.imencode(
            ".jpg", crop, [int(cv2.IMWRITE_JPEG_QUALITY), int(quality)]
        )
        return buf.tobytes() if ok else b""

    def save_ocr_debug_crop(self, tag: str = "crop") -> str:
        """保存当前 OCR 裁剪区，便于核对是否截到最新消息。"""
        try:
            from pathlib import Path

            out_dir = Path("logs") / "voice_transcribe"
            out_dir.mkdir(parents=True, exist_ok=True)
            path = out_dir / f"ocr_{tag}_{int(time.time())}.png"
            crop = self.capture_chat_region()
            cv2.imwrite(str(path), crop)
            logger.info(
                f"[{self.account_id}] OCR 裁剪区截图: {path} "
                f"(y {self._chat_y_top()}-{self._chat_y_bottom()})"
            )
            return str(path)
        except Exception as e:
            logger.debug(f"保存 OCR 裁剪截图失败: {e}")
            return ""

    def ocr_from_crop(
        self,
        crop_bgr: np.ndarray,
        contact_name: str = "",
    ) -> list[dict]:
        """对给定聊天区域截图做 OCR（不滚动、不重新截屏）。"""
        x_mid = int(self.w * 0.52)
        y_top = self._chat_y_top()
        x_self_edge = int(self.w * 0.85)
        x_friend_edge = int(self.w * 0.20)

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
            x_left, x_right = int(min(xs)), int(max(xs))
            if x_right >= x_self_edge:
                role = "self"
            elif x_left <= x_friend_edge:
                role = "friend"
            else:
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
        """
        OCR 当前屏；若 scroll_up>0 再上滑补扫历史后回到底部。

        注意：多页结果禁止直接拼成一袋再按 y 合并——屏幕 y 在不同滚动位置
        指向不同消息，会导致「旧外卖 + 新名片」拼成一句。请用
        `_collect_ocr_pages` + `_stitch_ocr_pages`。
        """
        pages = self._collect_ocr_pages(scroll_up)
        out: list[dict] = []
        for page_idx, page in enumerate(pages):
            for item in page:
                row = dict(item)
                row["page"] = page_idx
                out.append(row)
        return out

    def _collect_ocr_pages(self, scroll_up: int) -> list[list[dict]]:
        """
        按页 OCR，返回从旧到新的页列表。

        流程：先上滑若干次收集历史页，再回底部扫当前页。
        """
        older: list[list[dict]] = []
        if scroll_up > 0:
            for _ in range(int(scroll_up)):
                self.scroll_up_for_history(1)
                older.append(self._ocr_chat_region())
            # older[0] 是刚上滑一屏（较新），最后一页最旧
            older.reverse()
            self.scroll_to_bottom(times=int(scroll_up) + 1)
            time.sleep(0.45)
        bottom = self._ocr_chat_region()
        return older + [bottom]

    def _stitch_ocr_pages(
        self,
        pages: list[list[dict]],
        contact_name: str = "",
    ) -> list[dict]:
        """
        每页独立 merge_lines，再按文本去重拼成时间序（旧→新）。

        同一句话在相邻两屏重复出现时只保留一次；禁止跨页按 y 拼接。
        """
        if not pages:
            return []

        merged_pages: list[list[dict]] = []
        for page in pages:
            merged = self._merge_lines(page, contact_name=contact_name)
            merged.sort(key=lambda x: x.get("y", 0))
            merged_pages.append(merged)

        if len(merged_pages) == 1:
            return merged_pages[0]

        out: list[dict] = []
        # role -> list of normalized texts already kept
        kept_norms: dict[str, list[str]] = {"self": [], "friend": []}

        for page in merged_pages:
            for item in page:
                role = str(item.get("role", "friend"))
                text = str(item.get("text", "")).strip()
                if not text:
                    continue
                nt = self._norm_transcript(text)
                if not nt:
                    continue
                norms = kept_norms.setdefault(role, [])
                skip = False
                replace_idx = -1
                for i, prev in enumerate(norms):
                    if nt == prev:
                        skip = True
                        break
                    # 短片段被长句覆盖 / 长句替换短片段（滚动重叠）
                    if nt in prev and len(nt) < len(prev):
                        skip = True
                        break
                    if prev in nt and len(prev) < len(nt):
                        replace_idx = i
                        break
                if skip:
                    continue
                if replace_idx >= 0:
                    # 用更长文本替换已有短句
                    old_norm = norms[replace_idx]
                    for j, existing in enumerate(out):
                        if (
                            existing.get("role") == role
                            and self._norm_transcript(existing.get("text", ""))
                            == old_norm
                        ):
                            out[j] = item
                            break
                    norms[replace_idx] = nt
                    continue
                norms.append(nt)
                out.append(item)
        return out

    @staticmethod
    def _dedupe_visible(items: list[dict]) -> list[dict]:
        seen: set[tuple[str, int, str, int]] = set()
        out: list[dict] = []
        for item in items:
            role = item.get("role", "")
            y_bucket = int(item.get("y", 0)) // 24
            text = str(item.get("text", "")).strip()
            page = int(item.get("page", 0))
            key = (role, y_bucket, text, page)
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
        self.scroll_to_bottom(times=1)
        time.sleep(0.4)
        # 多页上滑曾把「旧外卖」和「新名片」按屏幕 y 错拼进同一段上下文。
        # 深聊优先保证当前屏正确；需要更长历史时再做锚点拼接。
        if scroll_up > 0:
            logger.info(
                f"[{self.account_id}] 聊天上下文仅用当前屏"
                f"（忽略 scroll_up={scroll_up}，避免跨页串话）"
            )
        pages = self._collect_ocr_pages(0)
        visible = pages[-1] if pages else []

        merged_all = self._stitch_ocr_pages(pages, contact_name=contact_name)
        merged_all = self._dedupe_scroll_ghosts(merged_all)
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
        transcript_map = self._filter_invalid_transcripts(
            transcript_map, voice_bubbles, merged
        )

        transcribe_budget = max(0, max_voice_transcribe)
        live_transcripts: dict[int, str] = {}
        used_transcript_texts = {
            self._norm_transcript(t) for t in transcript_map.values() if t
        }

        for vb in sorted(
            voice_bubbles,
            key=lambda v: (
                not v.get("bbox"),
                not v.get("confirmed", False),
                v.get("role") != "friend",
                -v.get("y", 0),
            ),
        ):
            y_key = vb.get("y", 0)
            role = vb.get("role", "friend")
            bbox = vb.get("bbox")
            live = ""
            if bbox and vb.get("confirmed"):
                live = self._ocr_transcript_below_bubble(
                    vb.get("x", int(self.w * 0.25)),
                    y_key,
                    role=role,
                    bbox=bbox,
                )
            if live and not self._is_invalid_transcript(
                live, y_key, merged, role, bbox=bbox
            ):
                live_transcripts[y_key] = live
                used_transcript_texts.add(self._norm_transcript(live))
                continue
            if live:
                logger.info(
                    f"[{self.account_id}] 气泡下方 OCR 无效，尝试重新转写 "
                    f"y={y_key}: {live[:30]}"
                )
            cached = transcript_map.get(y_key, "")
            if cached and self._is_invalid_transcript(
                cached, y_key, merged, role, bbox=bbox
            ):
                logger.info(
                    f"[{self.account_id}] 丢弃无效缓存转写 y={y_key}: {cached[:30]}"
                )
            live_transcripts.pop(y_key, None)
            if transcribe_budget <= 0:
                break
            if transcribe_friend_only and role != "friend":
                continue
            if not vb.get("confirmed", False):
                logger.debug(
                    f"[{self.account_id}] 跳过未确认语音气泡 "
                    f"role={role} y={y_key}"
                )
                continue
            if not bbox or vb.get("source") != "cv":
                logger.debug(
                    f"[{self.account_id}] 无 CV 定位，跳过转写 "
                    f"role={role} y={y_key}"
                )
                continue
            if self._has_text_message_near(
                merged, role, y_key, visible
            ):
                logger.info(
                    f"[{self.account_id}] 同行已有文字气泡，跳过转写 "
                    f"role={role} y={y_key}"
                )
                continue
            if not self._voice_bubble_position_valid(
                role,
                vb.get("x", 0),
                y_key,
                bbox=bbox,
            ):
                logger.debug(
                    f"[{self.account_id}] 跳过无效语音坐标 "
                    f"role={role} y={y_key} bbox={bbox}"
                )
                continue
            px, py = self._voice_press_point(
                vb.get("x", int(self.w * 0.25)),
                y_key,
                role,
                bbox=bbox,
            )
            transcript = self.transcribe_voice_at(
                px, py, role=role, bbox=bbox, merged=merged
            )
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

            if voice_match or self._is_strict_voice_duration(item.get("text", "")):
                vb = voice_match or {"y": y_key, "role": item.get("role", "friend")}
                vy = vb.get("y", y_key)
                matched_voice_y.add(vy)
                duration = self._parse_voice_duration(item.get("text", "")) or vb.get(
                    "duration"
                )
                role = vb.get("role", item.get("role", "friend"))
                transcript = live_transcripts.get(vy, "")
                if not transcript:
                    transcript = _VOICE_UNTRANSCRIBED_LLM
                    if duration:
                        transcript = f"{_VOICE_UNTRANSCRIBED_LLM} {duration}秒"
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
                transcript = live_transcripts.get(vy, "")
                if not transcript:
                    transcript = _VOICE_UNTRANSCRIBED_LLM
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
            if not transcript:
                transcript = _VOICE_UNTRANSCRIBED_LLM
                if duration:
                    transcript = f"{_VOICE_UNTRANSCRIBED_LLM} {duration}秒"
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

    def _transcribe_voices_on_current_screen(
        self,
        contact_name: str = "",
        max_voice_transcribe: int = 4,
        transcribe_friend_only: bool = False,
    ) -> int:
        """
        仅处理当前屏可见语音：转文字后灰字会出现在气泡下方，便于随后截图给 Vision。
        返回实际发起转写的次数。
        """
        visible = self._ocr_chat_region()
        merged_all = self._merge_lines(visible, contact_name=contact_name)
        voice_bubbles = self._find_voice_bubbles(merged_all, visible, contact_name)
        merged = [
            item
            for item in merged_all
            if not self._is_chat_timestamp(item.get("text", ""), item.get("x", 0))
        ]
        transcript_map, _ = self._map_existing_transcripts(voice_bubbles, merged)
        transcript_map = self._filter_invalid_transcripts(
            transcript_map, voice_bubbles, merged
        )

        budget = max(0, int(max_voice_transcribe))
        used = 0
        for vb in sorted(
            voice_bubbles,
            key=lambda v: (
                not v.get("bbox"),
                not v.get("confirmed", False),
                v.get("role") != "friend",
                -v.get("y", 0),
            ),
        ):
            if budget <= 0:
                break
            y_key = vb.get("y", 0)
            role = vb.get("role", "friend")
            bbox = vb.get("bbox")
            if transcript_map.get(y_key):
                continue
            if bbox and vb.get("confirmed"):
                live = self._ocr_transcript_below_bubble(
                    vb.get("x", int(self.w * 0.25)),
                    y_key,
                    role=role,
                    bbox=bbox,
                )
                if live and not self._is_invalid_transcript(
                    live, y_key, merged, role, bbox=bbox
                ):
                    continue
            if transcribe_friend_only and role != "friend":
                continue
            if not vb.get("confirmed", False):
                continue
            if not bbox or vb.get("source") != "cv":
                continue
            if self._has_text_message_near(merged, role, y_key, visible):
                continue
            if not self._voice_bubble_position_valid(
                role, vb.get("x", 0), y_key, bbox=bbox
            ):
                continue
            px, py = self._voice_press_point(
                vb.get("x", int(self.w * 0.25)),
                y_key,
                role,
                bbox=bbox,
            )
            transcript = self.transcribe_voice_at(
                px, py, role=role, bbox=bbox, merged=merged
            )
            budget -= 1
            used += 1
            if transcript:
                logger.info(
                    f"[{self.account_id}] 当前屏语音已转写 role={role}: "
                    f"{str(transcript)[:36]}"
                )
            time.sleep(random.uniform(0.4, 1.0))
        return used

    def read_messages_via_vision(
        self,
        contact_name: str = "",
        scroll_up: int = 2,
        max_voice_transcribe: int = 4,
        transcribe_friend_only: bool = False,
    ) -> list[dict]:
        """
        深聊推荐路径：当前屏/多页先转语音 → 彩色截图 → Vision 识图拼上下文。

        流程：
          1. 滑到较旧位置
          2. 从旧到新：每页先转写可见语音，再截图
          3. 多图交给 Vision 输出按时间排序的消息列表
          4. Vision 不可用时回退 EasyOCR 路径
        """
        from content.llm_client import LLMClient

        llm = LLMClient()
        if not llm.vision_available:
            logger.warning(
                f"[{self.account_id}] Vision 不可用，回退 OCR 读聊天记录"
            )
            return self.read_messages_with_voice(
                contact_name=contact_name,
                scroll_up=0,
                max_voice_transcribe=max_voice_transcribe,
                transcribe_friend_only=transcribe_friend_only,
            )

        scroll_up = max(0, int(scroll_up))
        self.scroll_to_bottom(times=1)
        time.sleep(0.4)
        if scroll_up > 0:
            for _ in range(scroll_up):
                self.scroll_up_for_history(1)
            time.sleep(0.35)

        total_pages = scroll_up + 1
        budget = max(0, int(max_voice_transcribe))
        page_jpegs: list[bytes] = []

        for i in range(total_pages):
            used = self._transcribe_voices_on_current_screen(
                contact_name=contact_name,
                max_voice_transcribe=budget,
                transcribe_friend_only=transcribe_friend_only,
            )
            budget = max(0, budget - used)
            # 等转写灰字渲染出来再截
            time.sleep(0.45 if used else 0.2)
            jpeg = self.capture_chat_jpeg()
            if jpeg:
                page_jpegs.append(jpeg)
                logger.info(
                    f"[{self.account_id}] Vision 截图页 {i + 1}/{total_pages} "
                    f"({len(jpeg) // 1024}KB, 本页转写 {used})"
                )
            if i < total_pages - 1:
                self.scroll_to_bottom(times=1)
                time.sleep(0.45)

        if not page_jpegs:
            logger.warning(f"[{self.account_id}] Vision 截图失败，回退 OCR")
            return self.read_messages_with_voice(
                contact_name=contact_name,
                scroll_up=0,
                max_voice_transcribe=0,
            )

        history = llm.read_chat_messages_from_images(
            page_jpegs, contact_name=contact_name
        )
        if not history:
            logger.warning(
                f"[{self.account_id}] Vision 未解析出消息，回退当前屏 OCR"
            )
            return self.read_messages_with_voice(
                contact_name=contact_name,
                scroll_up=0,
                max_voice_transcribe=0,
            )

        logger.info(
            f"[{self.account_id}] Vision 读聊天完成: "
            f"{len(page_jpegs)} 页 → {len(history)} 条"
        )
        return history

    def _voice_bubble_position_valid(
        self,
        role: str,
        x: int,
        y: int,
        bbox: Optional[tuple[int, int, int, int]] = None,
    ) -> bool:
        """语音气泡坐标是否在聊天区且左右归属合理。"""
        if bbox and len(bbox) >= 4:
            x1, y1, x2, y2 = (int(v) for v in bbox[:4])
            x = (x1 + x2) // 2
            y = (y1 + y2) // 2
        y_min = self._chat_y_top() + int(self.h * 0.05)
        y_max = self._chat_y_bottom() - int(self.h * 0.06)
        if not (y_min <= y <= y_max):
            return False
        if role == "friend":
            return x <= int(self.w * 0.54)
        return x >= int(self.w * 0.46)

    def _estimate_voice_bbox(
        self,
        x: int,
        y: int,
        role: str,
        duration: Optional[int] = None,
    ) -> tuple[int, int, int, int]:
        """根据时长 OCR 点估算语音气泡外接框（用于长按中心）。"""
        dur = max(1, min(int(duration or 3), 60))
        bubble_w = int(self.w * (0.10 + dur * 0.012))
        bubble_w = max(int(self.w * 0.14), min(bubble_w, int(self.w * 0.42)))
        bubble_h = max(int(self.h * 0.028), int(self.h * 0.036))
        y1 = max(self._chat_y_top(), y - bubble_h // 2)
        y2 = min(self._chat_y_bottom(), y + bubble_h // 2)
        if role == "friend":
            # 好友时长在气泡右侧，框往左扩
            x2 = min(int(self.w * 0.56), x + int(self.w * 0.03))
            x1 = max(int(self.w * 0.12), x2 - bubble_w)
        else:
            # 己方时长在气泡偏左，框往右扩
            x1 = max(int(self.w * 0.44), x - int(self.w * 0.03))
            x2 = min(int(self.w * 0.92), x1 + bubble_w)
        return x1, y1, x2, y2

    def _voice_press_point(
        self,
        x: int,
        y: int,
        role: str,
        bbox: Optional[tuple[int, int, int, int]] = None,
    ) -> tuple[int, int]:
        """将 OCR/CV 坐标校正到语音气泡可长按中心。"""
        from config.device_profiles import get_extra

        if bbox and len(bbox) >= 4:
            x1, y1, x2, y2 = (int(v) for v in bbox[:4])
            px = (x1 + x2) // 2
            py = (y1 + y2) // 2
        else:
            py = y
            friend_off = float(get_extra(self.d, "voice_press_friend_x_offset", 0.07))
            self_off = float(get_extra(self.d, "voice_press_self_x_offset", 0.09))
            if role == "friend":
                px = x - int(self.w * friend_off)
                px = max(int(self.w * 0.20), min(px, int(self.w * 0.46)))
            else:
                px = x + int(self.w * self_off)
                px = max(int(self.w * 0.58), min(px, int(self.w * 0.84)))
        py = max(
            self._chat_y_top() + int(self.h * 0.02),
            min(py, self._chat_y_bottom() - int(self.h * 0.06)),
        )
        return px, py

    def transcribe_voice_at(
        self,
        x: int,
        y: int,
        retries: int = 2,
        role: str = "friend",
        bbox: Optional[tuple[int, int, int, int]] = None,
        merged: Optional[list[dict]] = None,
    ) -> str:
        """长按语音气泡 → 点「转文字」→ OCR 转写结果。"""
        voice_y = y
        if bbox and len(bbox) >= 4:
            voice_y = (int(bbox[1]) + int(bbox[3])) // 2
        candidates = self._voice_press_candidates(x, y, role, bbox=bbox)
        logger.info(
            f"[{self.account_id}] 开始转写语音 role={role} "
            f"候选点={candidates[:3]}"
        )
        for attempt in range(retries + 1):
            px, py = candidates[min(attempt, len(candidates) - 1)]
            try:
                self._long_press(px, py, hold_ms=1200)
                time.sleep(0.8)
                if self._long_press_menu_is_text_only():
                    logger.info(
                        f"[{self.account_id}] 按到文字气泡（复制/转发菜单），"
                        f"放弃转写 @({px},{py})"
                    )
                    self._save_debug_screenshot(f"text_menu_{attempt}")
                    self._dismiss_popup()
                    return ""
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

                text = self._poll_transcript(
                    px, py, timeout=8.0, role=role, bbox=bbox
                )
                if text and merged and self._is_invalid_transcript(
                    text, voice_y, merged, role, bbox=bbox
                ):
                    logger.info(
                        f"[{self.account_id}] 转写结果无效（污染/与历史重复），丢弃: "
                        f"{text[:30]}"
                    )
                    text = ""
                if text and len(text) >= 2:
                    logger.info(
                        f"[{self.account_id}] 语音转写成功: {text[:40]}"
                    )
                    return text

                self._dismiss_popup()
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
        self,
        x: int,
        y: int,
        role: str,
        bbox: Optional[tuple[int, int, int, int]] = None,
    ) -> list[tuple[int, int]]:
        """生成多个候选长按点（气泡中心 + 左右/上下微调）。"""
        primary = self._voice_press_point(x, y, role, bbox=bbox)
        px0, py0 = primary
        if role == "self":
            xs = [px0, int(self.w * 0.68), int(self.w * 0.72), int(self.w * 0.76), int(self.w * 0.80)]
        else:
            xs = [px0, int(self.w * 0.26), int(self.w * 0.30), int(self.w * 0.34), int(self.w * 0.38)]
        ys = [py0, py0 - 12, py0 + 12, py0 - 24, py0 + 24]
        out: list[tuple[int, int]] = []
        seen: set[tuple[int, int]] = set()
        for py in ys:
            for px in xs:
                px = max(int(self.w * 0.10), min(px, int(self.w * 0.90)))
                py = max(
                    self._chat_y_top() + int(self.h * 0.02),
                    min(py, self._chat_y_bottom() - int(self.h * 0.06)),
                )
                key = (px // 6, py // 6)
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

    def _poll_transcript(
        self,
        x: int,
        y: int,
        timeout: float = 8.0,
        role: str = "friend",
        bbox: Optional[tuple[int, int, int, int]] = None,
    ) -> str:
        """轮询等待微信转写完成并 OCR 读取（仅气泡下方，不读上方历史文字）。"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            text = self._ocr_transcript_below_bubble(x, y, role=role, bbox=bbox)
            if text and len(text) >= 2:
                return text
            time.sleep(0.7)
        return ""

    def _bubble_bottom_y(
        self,
        y: int,
        bbox: Optional[tuple[int, int, int, int]] = None,
    ) -> int:
        if bbox and len(bbox) >= 4:
            return int(bbox[3])
        return y + int(self.h * 0.016)

    def _ocr_transcript_below_bubble(
        self,
        x: int,
        y: int,
        role: str = "friend",
        bbox: Optional[tuple[int, int, int, int]] = None,
    ) -> str:
        """OCR 语音气泡正下方的转写灰字（禁止向上扫到历史聊天气泡）。"""
        bottom = self._bubble_bottom_y(y, bbox)
        y0 = max(self._chat_y_top(), bottom + 4)
        y1 = min(self._chat_y_bottom(), bottom + int(self.h * 0.10))
        x0, x1 = self._transcript_x_bounds(role, x)
        return self._ocr_best_text_in_region(
            x0, y0, x1, y1, role=role, anchor_y=bottom, below_only=True
        )

    def _ocr_transcript_below(self, x: int, y: int, role: str = "friend") -> str:
        """兼容旧调用：仅读气泡下方。"""
        return self._ocr_transcript_below_bubble(x, y, role=role)

    def _transcript_x_bounds(self, role: str, x: int) -> tuple[int, int]:
        """按发言方限制 OCR 横向范围，避免读到对侧气泡的转写文字。"""
        if role == "self":
            x0 = max(int(self.w * 0.46), x - int(self.w * 0.38))
            x1 = min(self.w, x + int(self.w * 0.38))
        else:
            x0 = max(0, x - int(self.w * 0.38))
            x1 = min(int(self.w * 0.54), x + int(self.w * 0.38))
        return x0, x1

    def _ocr_transcript_near_bubble(self, x: int, y: int, role: str = "friend") -> str:
        """仅读气泡下方转写（保留兼容入口）。"""
        return self._ocr_transcript_below_bubble(x, y, role=role)

    def _ocr_best_text_in_region(
        self,
        x0: int,
        y0: int,
        x1: int,
        y1: int,
        role: str = "",
        anchor_y: int = 0,
        below_only: bool = False,
    ) -> str:
        img = np.array(self.d.screenshot(format="pillow"))
        gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
        if self._clahe is None:
            self._clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        enhanced = cv2.cvtColor(self._clahe.apply(gray), cv2.COLOR_GRAY2BGR)

        x_mid = int(self.w * 0.52)
        lines: list[tuple[int, int, float, str]] = []
        for text, cx, cy, conf in self._ocr_region(enhanced, x0, y0, x1, y1):
            t = str(text or "").strip()
            if not t or self._is_noise(t):
                continue
            if self._is_voice_duration(t) or self._is_voice_marker(t):
                continue
            if self._is_chat_timestamp(t, cx):
                continue
            if not self._looks_like_transcript(t):
                continue
            if role == "friend" and cx >= x_mid:
                continue
            if role == "self" and cx < x_mid:
                continue
            lines.append((cy, cx, conf, t))

        if not lines:
            return ""

        if anchor_y > 0:
            min_cy = anchor_y + (int(self.h * 0.012) if below_only else -int(self.h * 0.01))
            below = [
                (cy - anchor_y, -conf, t)
                for cy, _cx, conf, t in lines
                if cy >= min_cy
            ]
            if below:
                below.sort()
                parts = [t for _, _, t in below]
                return self._join_text_parts(parts)

        lines.sort(key=lambda item: (item[0], -item[2]))
        row_tol = int(self.h * 0.025)
        merged: list[str] = []
        bucket_y = -1
        bucket_parts: list[str] = []
        for cy, _cx, _conf, t in lines:
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
        if not merged:
            return ""
        if anchor_y > 0:
            scored = [
                (abs(self._estimate_text_y(merged, lines, m) - anchor_y), len(m), m)
                for m in merged
            ]
            scored.sort()
            return scored[0][2]
        return max(merged, key=len)

    def _estimate_text_y(
        self,
        merged_text: str,
        lines: list[tuple[int, int, float, str]],
        target: str,
    ) -> int:
        for cy, _cx, _conf, t in lines:
            if t in target or target in t:
                return cy
        return 0

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

    def _dismiss_popup(self) -> None:
        """关闭长按菜单 / 多选模式（避免误触返回退出聊天）。"""
        try:
            el = self.d(text="取消")
            if el.exists(timeout=0.4):
                el.click()
                time.sleep(0.3)
                return
        except Exception:
            pass
        try:
            safe_y = self._chat_y_top() + int(self.h * 0.06)
            self.d.click(int(self.w * 0.50), safe_y)
            time.sleep(0.25)
        except Exception:
            pass

    def _long_press_menu_is_text_only(self) -> bool:
        """长按后出现文字消息菜单（复制/转发）而非语音转文字菜单。"""
        try:
            if self.d(text="转文字").exists(timeout=0.5):
                return False
        except Exception:
            pass
        try:
            img = np.array(self.d.screenshot(format="pillow"))
            gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
            if self._clahe is None:
                self._clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
            enhanced = cv2.cvtColor(self._clahe.apply(gray), cv2.COLOR_GRAY2BGR)
            blob = " ".join(
                t for t, _cx, _cy, conf in self._ocr_region(
                    enhanced, 0, 0, self.w, self.h
                )
                if conf > 0.2
            )
            if any(kw in blob for kw in _VOICE_MENU_KEYWORDS):
                return False
            if "转" in blob and "文" in blob:
                return False
            # 语音菜单也有「多选」，不能用多选判断；复制/转发仅文字气泡有
            if "复制" in blob or "转发" in blob:
                return True
        except Exception:
            pass
        return False

    def _verify_voice_bubble_visual(
        self,
        bbox: Optional[tuple[int, int, int, int]],
        role: str,
    ) -> bool:
        """转写前视觉校验：排除文字泡、红包、过宽气泡。"""
        if not bbox or len(bbox) < 4:
            return False
        x1, y1, x2, y2 = (int(v) for v in bbox[:4])
        bw, bh = max(1, x2 - x1), max(1, y2 - y1)
        if bw > int(self.w * 0.42):
            return False
        if bh > int(self.h * 0.055):
            return False
        try:
            img = np.array(self.d.screenshot(format="pillow"))
            gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
            if self._clahe is None:
                self._clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
            enhanced = cv2.cvtColor(self._clahe.apply(gray), cv2.COLOR_GRAY2BGR)
            pad = 4
            cx0 = max(0, x1 - pad)
            cy0 = max(0, y1 - pad)
            cx1 = min(self.w, x2 + pad)
            cy1 = min(self.h, y2 + pad)
            crop = enhanced[cy0:cy1, cx0:cx1]
            if crop.size == 0:
                return False
            texts: list[str] = []
            for t, _cx, _cy, conf in self._ocr_region(enhanced, cx0, cy0, cx1, cy1):
                if conf > 0.15:
                    texts.append(t)
            blob = "".join(texts)
            if self._is_special_non_voice_bubble(blob):
                return False
            cjk = re.findall(r"[\u4e00-\u9fff]", blob)
            if len(cjk) >= 4:
                return False
            for t in texts:
                if self._is_strict_voice_duration(t):
                    return True
            duration, _ = self._ocr_duration_from_bubble_crop(crop)
            return duration is not None
        except Exception as e:
            logger.debug(f"[{self.account_id}] 语音视觉校验失败: {e}")
            return False

    @staticmethod
    def _is_special_non_voice_bubble(text: str) -> bool:
        t = str(text or "")
        markers = (
            "红包",
            "恭喜发财",
            "微信红包",
            "转账",
            "[图片]",
            "[视频]",
            "[文件]",
        )
        return any(m in t for m in markers)

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
        return ChatHistoryReader._parse_voice_duration(text) is not None

    @staticmethod
    def _is_strict_voice_duration(text: str) -> bool:
        """
        严格语音时长：必须带引号/秒标记，避免把文字里的孤立数字误判为语音。
        例：3''、5"  ✓    纯 3、123、文字碎片  ✗
        """
        t = re.sub(r"\s+", "", str(text or "").strip())
        if not t:
            return False
        if _VOICE_DURATION_RE.match(t):
            return ChatHistoryReader._parse_voice_duration(t) is not None
        if len(t) > 12:
            return False
        if re.search(r"\d\s*['″\"″秒]", t) or re.search(r"['″\"″]\s*\d", t):
            return ChatHistoryReader._parse_voice_duration(t) is not None
        return False

    @staticmethod
    def _parse_voice_duration(text: str) -> Optional[int]:
        t = re.sub(r"\s+", "", str(text or "").strip())
        if not t:
            return None
        if _VOICE_DURATION_RE.match(t):
            m = re.match(r"(\d+)", t)
            if m:
                val = int(m.group(1))
                return val if 1 <= val <= 60 else None
        if re.fullmatch(r"['″\"″]+", t):
            return None
        # OCR 常把波形括号与秒数粘在一起，如 "))2''"
        m = _VOICE_DURATION_IN_TEXT_RE.search(t)
        if m and len(t) <= 12:
            val = int(m.group(1))
            return val if 1 <= val <= 60 else None
        return None

    def _ocr_duration_from_bubble_crop(
        self, bubble_bgr: np.ndarray
    ) -> tuple[Optional[int], str]:
        """在气泡裁剪区内 OCR 识别 1-60 秒时长（波形图标右侧数字）。"""
        if bubble_bgr is None or bubble_bgr.size == 0:
            return None, ""
        reader = self._ensure_ocr()
        h, w = bubble_bgr.shape[:2]
        regions = [bubble_bgr]
        if w >= 40:
            regions.append(bubble_bgr[:, int(w * 0.30):])
            regions.append(bubble_bgr[:, : int(w * 0.70)])
        if w >= 60:
            regions.append(bubble_bgr[:, int(w * 0.45):])
            regions.append(bubble_bgr[:, : int(w * 0.55)])

        for region in regions:
            if region.size == 0:
                continue
            for _bbox, text, conf in reader.readtext(region):
                if conf < 0.12:
                    continue
                t = str(text or "").strip()
                if not ChatHistoryReader._is_strict_voice_duration(t):
                    continue
                duration = self._parse_voice_duration(t)
                if duration:
                    return duration, t
        return None, ""

    def _is_chat_timestamp(self, text: str, x: int = 0) -> bool:
        t = re.sub(r"\s+", "", str(text or "").strip())
        if not t:
            return False
        if _CHAT_TIMESTAMP_RE.match(t):
            return True
        if len(t) <= 10 and re.search(r"(昨天|星期)", t):
            return True
        # 8月7日中午12:39 / OCR 常把「日」认成乱码
        if re.match(
            r"^\d{1,2}月\d{1,2}.*?(上午|中午|下午|晚上|凌晨)?\d{1,2}[:：]\d{2}$",
            t,
        ):
            return True
        if re.match(r"^(昨天|今天).{0,4}\d{1,2}[:：]\d{2}$", t):
            return True
        if x and int(self.w * 0.32) <= x <= int(self.w * 0.68):
            if re.search(r"\d{1,2}[:：;]\d{2}", t) and len(t) <= 16:
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

    def _voice_row_tolerance(self) -> int:
        """同一气泡行判定容差（约一条语音条高度，避免上下相邻消息误判）。"""
        return int(self.h * 0.02)

    def _has_text_message_near(
        self,
        merged: list[dict],
        role: str,
        y: int,
        raw_items: Optional[list[dict]] = None,
    ) -> bool:
        """同一物理行若已有正常文字气泡，则不应再判为语音。"""
        tol = self._voice_row_tolerance()
        for item in list(merged) + list(raw_items or []):
            if item.get("role") != role:
                continue
            if abs(int(item.get("y", 0)) - y) > tol:
                continue
            text = str(item.get("text", "")).strip()
            if not text:
                continue
            if self._is_strict_voice_duration(text) or self._is_voice_marker(text):
                continue
            if self._is_voice_duration(text) and not self._is_strict_voice_duration(text):
                continue
            if self._is_probable_text_message(text):
                return True
            if len(text) >= 4:
                return True
        return False

    def _on_bubble_side(self, role: str, x: int) -> bool:
        if role == "self":
            return x >= int(self.w * 0.50)
        return x <= int(self.w * 0.50)

    def _bubble_exists_near(
        self,
        bubbles: list[dict],
        y: int,
        role: str,
    ) -> bool:
        tol = int(self.h * 0.04)
        for vb in bubbles:
            if vb.get("role") != role:
                continue
            if abs(int(vb.get("y", 0)) - y) <= tol:
                return True
        return False

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

        def _add(
            role: str,
            y: int,
            x: int,
            duration_text: str = "",
            confirmed: bool = False,
            bbox: Optional[tuple[int, int, int, int]] = None,
            source: str = "",
        ) -> None:
            bucket = y // max(row_tol, 1)
            key = (role, bucket)
            if key in seen:
                return
            strict_duration = self._is_strict_voice_duration(duration_text)
            is_confirmed = bool(confirmed and source == "cv" and bbox and strict_duration)
            if is_confirmed and self._has_text_message_near(
                merged, role, y, raw_items
            ):
                return
            if is_confirmed and not self._voice_bubble_position_valid(
                role, x, y, bbox=bbox
            ):
                return
            seen.add(key)
            entry = {
                "role": role,
                "y": y,
                "x": x
                if x
                else (
                    int(self.w * 0.75) if role == "self" else int(self.w * 0.25)
                ),
                "duration": self._parse_voice_duration(duration_text),
                "confirmed": is_confirmed,
                "source": source,
            }
            if bbox:
                entry["bbox"] = bbox
            bubbles.append(entry)

        for vb in self._find_voice_bubbles_cv(merged):
            _add(
                vb["role"],
                vb["y"],
                vb["x"],
                vb.get("text", ""),
                confirmed=True,
                bbox=vb.get("bbox"),
                source="cv",
            )
        for vb in self._find_voice_bubbles_bottom_strip():
            _add(
                vb["role"],
                vb["y"],
                vb["x"],
                vb.get("text", ""),
                confirmed=True,
                bbox=vb.get("bbox"),
                source="cv",
            )

        for item in merged:
            text = item.get("text", "")
            if not self._is_strict_voice_duration(text):
                continue
            role = item.get("role", "friend")
            iy = item.get("y", 0)
            ix = item.get("x", 0)
            if self._bubble_exists_near(bubbles, iy, role):
                continue
            refined = self._locate_voice_bbox_near(role, iy, ix)
            if not refined:
                refined = self._try_confirm_estimated_voice_bbox(role, ix, iy, text)
            if refined:
                _add(
                    role,
                    refined["y"],
                    refined["x"],
                    text,
                    confirmed=True,
                    bbox=refined["bbox"],
                    source="cv",
                )
            else:
                _add(
                    role,
                    iy,
                    ix,
                    text,
                    confirmed=False,
                    source="ocr",
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
            if self._is_strict_voice_duration(text):
                if self._bubble_exists_near(bubbles, y, role):
                    continue
                refined = self._locate_voice_bbox_near(role, y, x)
                if not refined:
                    refined = self._try_confirm_estimated_voice_bbox(role, x, y, text)
                if refined:
                    _add(
                        role,
                        refined["y"],
                        refined["x"],
                        text,
                        confirmed=True,
                        bbox=refined["bbox"],
                        source="cv",
                    )
                else:
                    _add(role, y, x, text, confirmed=False, source="ocr")
            elif self._is_voice_marker(text):
                if self._bubble_exists_near(bubbles, y, role):
                    continue
                _add(role, y, x, text, confirmed=False, source="ocr")

        friend_count = sum(1 for b in bubbles if b.get("role") == "friend")
        self_count = sum(1 for b in bubbles if b.get("role") == "self")
        if friend_count == 0 and self_count == 0:
            for vb in self._find_voice_bubbles_vision(
                contact_name,
                need_friend=True,
                need_self=True,
            ):
                if not vb.get("duration"):
                    continue
                dur = int(vb["duration"])
                _add(
                    vb["role"],
                    vb["y"],
                    vb["x"],
                    f"{dur}''",
                    confirmed=False,
                    source="vision",
                )

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

    def _scan_voice_durations_by_side(
        self, raw_items: list[dict]
    ) -> list[dict]:
        """按左右半屏补扫 OCR 漏掉的语音时长（好友白底气泡常见）。"""
        x_mid = int(self.w * 0.52)
        found: list[dict] = []
        seen: set[tuple[str, int]] = set()
        row_tol = int(self.h * 0.035)

        for item in raw_items:
            text = str(item.get("text", "")).strip()
            x = item.get("x", 0)
            y = item.get("y", 0)
            if self._is_chat_timestamp(text, x):
                continue
            if not self._is_strict_voice_duration(text):
                continue
            role = "self" if x >= x_mid else "friend"
            bucket = y // max(row_tol, 1)
            key = (role, bucket)
            if key in seen:
                continue
            seen.add(key)
            found.append({"role": role, "y": y, "x": x, "text": text})

        crop = self.capture_chat_region()
        y_top = self._chat_y_top()
        reader = self._ensure_ocr()
        for role, x0_pct, x1_pct in (
            ("friend", 0.0, 0.56),
            ("self", 0.44, 1.0),
        ):
            x0 = int(self.w * x0_pct)
            x1 = int(self.w * x1_pct)
            side_crop = crop[:, int(self.w * x0_pct): int(self.w * x1_pct)]
            raw = reader.readtext(side_crop)
            for bbox, text, conf in raw:
                if conf < 0.22:
                    continue
                t = str(text or "").strip()
                if not self._is_strict_voice_duration(t):
                    continue
                xs = [p[0] for p in bbox]
                ys = [p[1] for p in bbox]
                cx = int(sum(xs) / len(xs)) + x0
                cy = int(sum(ys) / len(ys)) + y_top
                bucket = cy // max(row_tol, 1)
                key = (role, bucket)
                if key in seen:
                    continue
                seen.add(key)
                found.append({"role": role, "y": cy, "x": cx, "text": t})
        return found

    def _locate_voice_bbox_near(
        self,
        role: str,
        y: int,
        x_hint: int = 0,
    ) -> Optional[dict]:
        """在 OCR 时长点附近做局部 CV，获取可长按的语音气泡 bbox。"""
        y_top = self._chat_y_top()
        crop = self.capture_chat_region()
        h_crop, w_crop = crop.shape[:2]
        pad_y = int(self.h * 0.045)
        y_local = y - y_top
        y1 = max(0, y_local - pad_y)
        y2 = min(h_crop, y_local + pad_y)
        if y2 - y1 < 12:
            return None

        if role == "friend":
            x0_pct, x1_pct, min_w, min_h = 0.0, 0.58, 48, 14
        else:
            x0_pct, x1_pct, min_w, min_h = 0.42, 1.0, 60, 16
        x0 = int(w_crop * x0_pct)
        x1 = int(w_crop * x1_pct)
        strip = crop[y1:y2, x0:x1]
        if strip.size == 0:
            return None

        gray = cv2.cvtColor(strip, cv2.COLOR_BGR2GRAY)
        bg = float(np.median(gray))
        _, mask = cv2.threshold(gray, int(min(bg + 22, 220)), 255, cv2.THRESH_BINARY)
        adapt = cv2.adaptiveThreshold(
            gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 21, 3
        )
        mask = cv2.bitwise_or(mask, adapt)
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (7, 3))
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)

        best: Optional[dict] = None
        best_score = -1.0
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for cnt in contours:
            x, ly, cw, ch = cv2.boundingRect(cnt)
            if cw < min_w or ch < min_h or ch > 52:
                continue
            if cw > int(w_crop * 0.44):
                continue
            aspect = cw / max(ch, 1)
            if aspect < 1.2 or aspect > 7.0:
                continue
            cy_local = ly + ch // 2
            cy = cy_local + y1 + y_top
            cx = x + cw // 2 + x0
            if role == "friend" and cx < int(w_crop * 0.10):
                continue
            if x_hint and abs(cx - x_hint) > int(self.w * 0.22):
                continue
            bubble_crop = strip[
                max(0, ly - 1): min(strip.shape[0], ly + ch + 1),
                max(0, x - 1): min(strip.shape[1], x + cw + 1),
            ]
            duration, duration_text = self._ocr_duration_from_bubble_crop(bubble_crop)
            if not duration:
                continue
            y_dist = abs(cy - y)
            score = 1000.0 - y_dist - abs(cx - x_hint) * 0.5
            if score > best_score:
                best_score = score
                screen_bbox = (
                    x0 + max(0, x - 1),
                    y_top + y1 + max(0, ly - 1),
                    x0 + x + cw + 1,
                    y_top + y1 + ly + ch + 1,
                )
                best = {
                    "role": role,
                    "y": cy,
                    "x": cx,
                    "duration": duration,
                    "text": duration_text or f"{duration}''",
                    "bbox": screen_bbox,
                }
        return best

    def _try_confirm_estimated_voice_bbox(
        self,
        role: str,
        x: int,
        y: int,
        duration_text: str,
    ) -> Optional[dict]:
        """OCR 已识别到时长时，用估算框 + 裁剪区二次确认，避免按到文字气泡。"""
        duration = self._parse_voice_duration(duration_text)
        if not duration:
            return None
        bbox = self._estimate_voice_bbox(x, y, role, duration)
        x1, y1, x2, y2 = (int(v) for v in bbox)
        y_top = self._chat_y_top()
        chat = self.capture_chat_region()
        ly1 = max(0, y1 - y_top)
        ly2 = min(chat.shape[0], y2 - y_top)
        lx1 = max(0, x1)
        lx2 = min(chat.shape[1], x2)
        if ly2 <= ly1 or lx2 <= lx1:
            return None
        bubble_crop = chat[ly1:ly2, lx1:lx2]
        found_dur, found_text = self._ocr_duration_from_bubble_crop(bubble_crop)
        if not found_dur:
            return None
        return {
            "role": role,
            "y": (y1 + y2) // 2,
            "x": (x1 + x2) // 2,
            "duration": found_dur,
            "text": found_text or duration_text,
            "bbox": bbox,
        }

    def _find_voice_bubbles_cv(self, merged: list[dict]) -> list[dict]:
        """用浅色圆角气泡轮廓 + 气泡内 OCR 识别 1-60 秒时长。"""
        y_top = self._chat_y_top()
        crop = self.capture_chat_region()
        h_crop, w_crop = crop.shape[:2]
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        bg = float(np.median(gray))
        thresh_val = int(min(bg + 24, 220))
        _, mask = cv2.threshold(gray, thresh_val, 255, cv2.THRESH_BINARY)
        adapt = cv2.adaptiveThreshold(
            gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 4
        )
        mask = cv2.bitwise_or(mask, adapt)
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (9, 4))
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)

        found: list[dict] = []
        for role, x0_pct, x1_pct, min_w, min_h in (
            ("friend", 0.0, 0.58, 52, 16),
            ("self", 0.42, 1.0, 70, 20),
        ):
            x0 = int(w_crop * x0_pct)
            x1 = int(w_crop * x1_pct)
            region = mask[:, x0:x1]
            color_region = crop[:, x0:x1]
            contours, _ = cv2.findContours(
                region, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
            )
            for cnt in contours:
                x, y, cw, ch = cv2.boundingRect(cnt)
                if cw < min_w or ch < min_h or ch > 52:
                    continue
                if cw > int(w_crop * 0.44):
                    continue
                aspect = cw / max(ch, 1)
                if aspect < 1.35 or aspect > 6.5:
                    continue
                cy = y + ch // 2 + y_top
                cx = x + cw // 2 + x0
                if role == "friend" and cx < int(w_crop * 0.10):
                    continue
                if self._has_text_message_near(merged, role, cy):
                    continue

                pad = 2
                y1 = max(0, y - pad)
                y2 = min(h_crop, y + ch + pad)
                x1c = max(0, x - pad)
                x2c = min(color_region.shape[1], x + cw + pad)
                bubble_crop = color_region[y1:y2, x1c:x2c]
                duration, duration_text = self._ocr_duration_from_bubble_crop(
                    bubble_crop
                )
                if not duration:
                    continue
                screen_bbox = (
                    x0 + x1c,
                    y_top + y1,
                    x0 + x2c,
                    y_top + y2,
                )
                text = duration_text or f"{duration}''"
                found.append(
                    {
                        "role": role,
                        "y": cy,
                        "x": cx,
                        "duration": duration,
                        "text": text,
                        "confirmed": True,
                        "source": "cv",
                        "bbox": screen_bbox,
                    }
                )
        return found

    def _find_voice_bubbles_vision(
        self,
        contact_name: str,
        need_friend: bool = True,
        need_self: bool = True,
        focus_side: str = "",
    ) -> list[dict]:
        """Vision 兜底：OCR/CV 均未发现某侧语音时调用。"""
        if not need_friend and not need_self:
            return []
        try:
            from content.llm_client import LLMClient

            llm = LLMClient()
            if not llm.vision_available:
                logger.warning(
                    f"[{self.account_id}] Vision 未配置，跳过语音气泡识图兜底"
                )
                return []
            crop = self.capture_chat_region()
            ok, buf = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, 80])
            if not ok:
                return []
            y_top = self._chat_y_top()
            crop_h = int(self.h * 0.74)
            raw = llm.detect_voice_bubbles_from_image(
                buf.tobytes(),
                focus_side=focus_side,
            )
            out: list[dict] = []
            for item in raw:
                role = item.get("role", "friend")
                if role not in ("self", "friend"):
                    continue
                if role == "friend" and not need_friend:
                    continue
                if role == "self" and not need_self:
                    continue
                y_pct = float(item.get("y_percent", 0))
                y = int(y_top + y_pct * crop_h)
                x = int(
                    self.w * (0.75 if role == "self" else 0.22)
                )
                duration = item.get("duration")
                dur_text = f"{duration}''" if duration else ""
                out.append(
                    {
                        "role": role,
                        "y": y,
                        "x": x,
                        "duration": duration,
                        "text": dur_text,
                        "confirmed": bool(duration),
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

    def _dedupe_scroll_ghosts(self, items: list[dict]) -> list[dict]:
        """滚动补扫时同一句话会出现在多个 y，只保留最靠下（当前屏）的一条。"""
        if not items:
            return items
        groups: dict[tuple[str, str], list[dict]] = {}
        for item in items:
            text = str(item.get("text", "")).strip()
            if not text:
                continue
            key = (item.get("role", ""), self._norm_transcript(text))
            groups.setdefault(key, []).append(item)

        drop_ids: set[int] = set()
        y_span = int(self.h * 0.08)
        for group in groups.values():
            if len(group) < 2:
                continue
            ys = [g.get("y", 0) for g in group]
            if max(ys) - min(ys) < y_span:
                continue
            keep = max(group, key=lambda g: g.get("y", 0))
            for g in group:
                if g is not keep:
                    drop_ids.add(id(g))

        if not drop_ids:
            return items
        return [item for item in items if id(item) not in drop_ids]

    def _is_invalid_transcript(
        self,
        text: str,
        voice_y: int,
        merged: list[dict],
        role: str,
        bbox: Optional[tuple[int, int, int, int]] = None,
    ) -> bool:
        if not str(text or "").strip():
            return True
        if self._transcript_looks_contaminated(text, voice_y, merged, role):
            return True
        return self._transcript_conflicts_with_chat(
            text, merged, role, voice_y, bbox=bbox
        )

    def _transcript_conflicts_with_chat(
        self,
        text: str,
        merged: list[dict],
        role: str,
        voice_y: int,
        bbox: Optional[tuple[int, int, int, int]] = None,
    ) -> bool:
        """转写内容与同侧其他聊天气泡高度重合（不含该语音正下方的转写灰字行）。"""
        t = str(text or "").strip()
        nt = self._norm_transcript(t)
        bottom = self._bubble_bottom_y(voice_y, bbox)
        transcript_band = int(self.h * 0.075)
        for item in merged:
            if item.get("role") != role:
                continue
            other = str(item.get("text", "")).strip()
            if not other or other == t:
                continue
            if self._is_voice_duration(other) or self._is_voice_marker(other):
                continue
            if len(other) < 3:
                continue
            iy = int(item.get("y", 0))
            if bottom < iy <= bottom + transcript_band:
                continue
            no = self._norm_transcript(other)
            if other in t or no in nt or nt in no:
                return True
            if self._transcript_shares_prefix(nt, no, 4):
                return True
        return False

    def _transcript_looks_contaminated(
        self,
        text: str,
        voice_y: int,
        merged: list[dict],
        role: str,
    ) -> bool:
        """转写文字混入了气泡上方/附近的历史聊天内容。"""
        t = str(text or "").strip()
        if not t:
            return True
        nt = self._norm_transcript(t)
        above_tol = int(self.h * 0.02)
        for item in merged:
            if item.get("role") != role:
                continue
            iy = item.get("y", 0)
            if iy >= voice_y - above_tol:
                continue
            other = str(item.get("text", "")).strip()
            if len(other) < 3:
                continue
            if self._is_voice_duration(other) or self._is_voice_marker(other):
                continue
            no = self._norm_transcript(other)
            if not no:
                continue
            if other in t or no in nt or nt in no:
                return True
            if self._transcript_shares_prefix(nt, no, min_len=4):
                return True
        return False

    @staticmethod
    def _transcript_shares_prefix(a: str, b: str, min_len: int = 4) -> bool:
        if not a or not b:
            return False
        n = min(len(a), len(b))
        for size in range(n, min_len - 1, -1):
            for i in range(len(a) - size + 1):
                sub = a[i: i + size]
                if len(sub) >= min_len and sub in b:
                    return True
        return False

    def _filter_invalid_transcripts(
        self,
        transcript_map: dict[int, str],
        voice_bubbles: list[dict],
        merged: list[dict],
    ) -> dict[int, str]:
        out: dict[int, str] = {}
        bubble_by_y = {vb.get("y", 0): vb for vb in voice_bubbles}
        for vy, text in transcript_map.items():
            vb = bubble_by_y.get(vy, {})
            role = vb.get("role", "friend")
            if self._is_invalid_transcript(
                text, vy, merged, role, bbox=vb.get("bbox")
            ):
                logger.info(
                    f"[{self.account_id}] 丢弃无效转写 y={vy}: {text[:30]}"
                )
                continue
            out[vy] = text
        return out

    def _infer_friend_voice_after_self(
        self,
        bubbles: list[dict],
        add_fn,
    ) -> None:
        """己方语音下方常见好友回复语音（截图底部左侧白底气泡）。"""
        if any(b.get("role") == "friend" for b in bubbles):
            return
        self_ys = [b.get("y", 0) for b in bubbles if b.get("role") == "self"]
        if not self_ys:
            return
        self_y = max(self_ys)
        probe_y = min(int(self.h * 0.86), self_y + int(self.h * 0.05))
        probe_y = max(probe_y, int(self.h * 0.80))
        add_fn("friend", probe_y, int(self.w * 0.28), "")

    def _find_voice_bubbles_bottom_strip(self) -> list[dict]:
        """专门扫描聊天区底部左侧，补检 OCR 漏掉的好友语音条。"""
        y_top = self._chat_y_top()
        strip_h = int(self.h * 0.24)
        crop = self.capture_chat_region()
        h_crop, w_crop = crop.shape[:2]
        strip_start = max(0, h_crop - strip_h)
        strip = crop[strip_start:, : int(w_crop * 0.60)]
        if strip.size == 0:
            return []

        gray = cv2.cvtColor(strip, cv2.COLOR_BGR2GRAY)
        adapt = cv2.adaptiveThreshold(
            gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 25, 4
        )
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (9, 4))
        mask = cv2.morphologyEx(adapt, cv2.MORPH_CLOSE, kernel)

        found: list[dict] = []
        contours, _ = cv2.findContours(
            mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )
        for cnt in contours:
            x, y, cw, ch = cv2.boundingRect(cnt)
            if cw < 52 or ch < 16 or ch > 52:
                continue
            if cw > int(w_crop * 0.44):
                continue
            if cw / max(ch, 1) < 1.35:
                continue
            cy = y + ch // 2 + strip_start + y_top
            cx = x + cw // 2
            if cx < int(w_crop * 0.10):
                continue
            bubble_crop = strip[max(0, y - 1): y + ch + 1, max(0, x - 1): x + cw + 1]
            duration, duration_text = self._ocr_duration_from_bubble_crop(bubble_crop)
            if not duration:
                continue
            screen_bbox = (
                x,
                y_top + strip_start + max(0, y - 1),
                x + cw,
                y_top + strip_start + y + ch + 1,
            )
            found.append(
                {
                    "role": "friend",
                    "y": cy,
                    "x": cx,
                    "duration": duration,
                    "text": duration_text or f"{duration}''",
                    "confirmed": True,
                    "source": "cv",
                    "bbox": screen_bbox,
                }
            )
        return found

    def _map_existing_transcripts(
        self,
        voice_bubbles: list[dict],
        merged: list[dict],
    ) -> tuple[dict[int, str], set[int]]:
        """用气泡正下方实时 OCR 读取已展示的转写灰字（不用全屏 merged 坐标）。"""
        result: dict[int, str] = {}
        consumed: set[int] = set()

        for vb in voice_bubbles:
            if not vb.get("confirmed") or not vb.get("bbox"):
                continue
            vy = vb.get("y", 0)
            role = vb.get("role", "friend")
            bbox = vb.get("bbox")
            text = self._ocr_transcript_below_bubble(
                vb.get("x", int(self.w * 0.25)),
                vy,
                role=role,
                bbox=bbox,
            )
            if not text:
                continue
            if self._is_invalid_transcript(
                text, vy, merged, role, bbox=bbox
            ):
                continue
            result[vy] = text
            bottom = self._bubble_bottom_y(vy, bbox)
            consumed.add(bottom + int(self.h * 0.02))

        return result, consumed

    def _ocr_chat_region(self) -> list[dict]:
        """OCR 聊天区域并标注左右归属。"""
        crop = self.capture_chat_region()
        x_mid = int(self.w * 0.52)
        y_top = self._chat_y_top()
        # 己方绿气泡右缘通常 >85%；好友白气泡左缘通常 <20%
        # 长句会越过中线，必须先看贴边，不能看中心
        x_self_edge = int(self.w * 0.85)
        x_friend_edge = int(self.w * 0.20)

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
            x_left, x_right = int(min(xs)), int(max(xs))
            if x_right >= x_self_edge:
                role = "self"
            elif x_left <= x_friend_edge:
                role = "friend"
            else:
                role = "self" if cx >= x_mid else "friend"
            items.append(
                {
                    "role": role,
                    "text": t,
                    "x": cx,
                    "y": cy,
                    "conf": conf,
                    "x_left": x_left,
                    "x_right": x_right,
                }
            )

        return items

    def _merge_lines(
        self,
        items: list[dict],
        contact_name: str = "",
    ) -> list[dict]:
        """同一气泡/同一行的 OCR 碎片合并（仅限同一 page）。"""
        if not items:
            return []

        items = sorted(
            items,
            key=lambda x: (int(x.get("page", 0)), x["y"], x["x"]),
        )
        row_tol = int(self.h * 0.025)
        merged: list[dict] = []

        for item in items:
            text = item["text"]
            if contact_name and text == contact_name and item["y"] < self.h * 0.12:
                continue
            page = int(item.get("page", 0))
            placed = False
            for bucket in merged:
                if int(bucket.get("page", 0)) != page:
                    continue
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
                        "page": page,
                    }
                )
        return merged

    @staticmethod
    def _is_voice_fragment(text: str) -> bool:
        t = str(text or "").strip()
        if re.fullmatch(r"(?:[1-9]|[1-5]\d|60)", t):
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
        if any(p.search(t) for p in _UI_NOISE_PATTERNS):
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
