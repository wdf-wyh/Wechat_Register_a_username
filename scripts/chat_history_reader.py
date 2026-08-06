"""
聊天页 OCR 读取 — 从当前屏幕提取对话记录。

微信屏蔽无障碍树，因此用 OCR + 气泡左右位置推断发言方：
- 右侧 (x > 52% 屏宽) → 自己
- 左侧 → 好友
"""

from __future__ import annotations

import re
import time
from typing import Optional

import cv2
import numpy as np

from utils.logger import get_logger

logger = get_logger("chat_history_reader")

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
            if not t or len(t) < 2:
                continue
            if self._is_noise(t):
                continue
            xs = [p[0] for p in bbox]
            ys = [p[1] for p in bbox]
            cx = int(sum(xs) / len(xs))
            cy = int(sum(ys) / len(ys)) + y_top
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
            if not t or len(t) < 2:
                continue
            if self._is_noise(t):
                continue
            xs = [p[0] for p in bbox]
            ys = [p[1] for p in bbox]
            cx = int(sum(xs) / len(xs))
            cy = int(sum(ys) / len(ys)) + y_top
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
                    placed = True
                    break
            if not placed:
                merged.append(
                    {
                        "role": item["role"],
                        "text": text,
                        "y": item["y"],
                    }
                )
        return merged

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
        if re.fullmatch(r"[\d:：\-\s]+", t):
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
