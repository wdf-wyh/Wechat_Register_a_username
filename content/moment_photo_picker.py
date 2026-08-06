"""
朋友圈智能选图 — Vision 识图筛选 + 图文配文。

流程:
    1. 相册页截图 → Canny 检测缩略图网格
    2. 逐张裁剪缩略图 → Vision 判断是否适合发朋友圈
    3. 从合格候选中随机选 1~3 张
    4. 根据选中图片的画面描述生成配文
"""

from __future__ import annotations

import base64
import random
from dataclasses import dataclass

import cv2
import numpy as np

from utils.logger import get_logger

logger = get_logger("moment_photo_picker")

# 无 Vision 时的启发式阈值
_MIN_THUMB_STD = 12.0
_MIN_THUMB_MEAN = 18.0
_MAX_THUMB_MEAN = 245.0


@dataclass
class ThumbnailCell:
    """相册网格中的一个缩略图单元。"""

    index: int
    cx: int
    cy: int
    x: int
    y: int
    w: int
    h: int


@dataclass
class PhotoCandidate:
    """通过筛选的候选照片。"""

    index: int
    cx: int
    cy: int
    category: str = "日常"
    description: str = ""
    suitable: bool = True
    reject_reason: str = ""


def detect_album_thumbnails(gray_img: np.ndarray, screen_h: int) -> list[ThumbnailCell]:
    """
    用 Canny 边缘检测相册缩略图网格，按从上到下、从左到右排序。

    与 moment_poster / image_sender 共用同一套几何检测逻辑。
    """
    album = gray_img[180:int(screen_h * 0.78), :]
    edges = cv2.Canny(cv2.GaussianBlur(album, (5, 5), 0), 25, 80)
    edges = cv2.dilate(edges, np.ones((4, 4), np.uint8), iterations=1)
    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    cells: list[ThumbnailCell] = []
    for cnt in contours:
        x, y, cw, ch = cv2.boundingRect(cnt)
        ar = cw / ch if ch > 0 else 0
        if 60 < cw < 500 and 60 < ch < 500 and 0.5 < ar < 2.0:
            if 500 < cw * ch < 150000:
                cells.append(
                    ThumbnailCell(
                        index=0,
                        cx=x + cw // 2,
                        cy=y + 180 + ch // 2,
                        x=x,
                        y=y + 180,
                        w=cw,
                        h=ch,
                    )
                )

    cells.sort(key=lambda c: (c.cy, c.cx))
    for i, cell in enumerate(cells):
        cell.index = i
    return cells


def _crop_thumbnail(img_bgr: np.ndarray, cell: ThumbnailCell) -> np.ndarray:
    h_img, w_img = img_bgr.shape[:2]
    pad = 2
    x0 = max(0, cell.x - pad)
    y0 = max(0, cell.y - pad)
    x1 = min(w_img, cell.x + cell.w + pad)
    y1 = min(h_img, cell.y + cell.h + pad)
    return img_bgr[y0:y1, x0:x1]


def _encode_jpeg(crop_bgr: np.ndarray, max_width: int = 480) -> bytes:
    h, w = crop_bgr.shape[:2]
    if w > max_width:
        scale = max_width / w
        crop_bgr = cv2.resize(crop_bgr, (max_width, int(h * scale)))
    ok, buf = cv2.imencode(".jpg", crop_bgr, [cv2.IMWRITE_JPEG_QUALITY, 78])
    return buf.tobytes() if ok else b""


def _heuristic_suitable(crop_bgr: np.ndarray) -> tuple[bool, str]:
    """无 Vision 时用亮度/方差做粗筛，过滤纯色黑屏。"""
    gray = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2GRAY)
    mean = float(np.mean(gray))
    std = float(np.std(gray))
    if mean < _MIN_THUMB_MEAN:
        return False, "画面过暗"
    if mean > _MAX_THUMB_MEAN:
        return False, "画面过亮/接近纯白"
    if std < _MIN_THUMB_STD:
        return False, "画面过于单调"
    return True, ""


class MomentPhotoPicker:
    """朋友圈智能选图器。"""

    MAX_SCAN = 12

    def __init__(self, d, account_id: str = ""):
        self.d = d
        self.account_id = account_id
        self.w = d.info["displayWidth"]
        self.h = d.info["displayHeight"]

    def scan_and_pick(
        self,
        persona: dict,
        photo_count: int | None = None,
        topic: str = "日常",
    ) -> tuple[list[int], str, list[str]]:
        """
        扫描当前相册页，挑选适合发朋友圈的照片并生成配文。

        Returns:
            (photo_indices, caption, descriptions)
        """
        import time

        time.sleep(2.5)

        img = np.array(self.d.screenshot(format="pillow"))
        img_bgr = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
        gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
        cells = detect_album_thumbnails(gray, self.h)
        logger.info(f"[{self.account_id}] 相册检测到 {len(cells)} 个缩略图")

        if not cells:
            return self._fallback_pick(persona, photo_count, topic)

        from content.llm_client import LLMClient

        llm = LLMClient()
        use_vision = llm.vision_available
        suitable: list[PhotoCandidate] = []

        for cell in cells[: self.MAX_SCAN]:
            crop = _crop_thumbnail(img_bgr, cell)
            if crop.size == 0:
                continue

            ok, reject = _heuristic_suitable(crop)
            if not ok:
                logger.debug(
                    f"[{self.account_id}] 缩略图#{cell.index + 1} 启发式拒绝: {reject}"
                )
                continue

            category = "日常"
            description = ""
            reject_reason = ""

            if use_vision:
                jpeg = _encode_jpeg(crop)
                if jpeg:
                    result = llm.classify_moment_thumbnail(jpeg)
                    ok = bool(result.get("suitable"))
                    category = str(result.get("category") or "日常")
                    description = str(result.get("description") or "").strip()
                    reject_reason = str(result.get("reject_reason") or "").strip()
                    if not ok:
                        logger.debug(
                            f"[{self.account_id}] 缩略图#{cell.index + 1} "
                            f"Vision拒绝: {reject_reason or category}"
                        )
                        continue
            else:
                description = f"一张{category}照片"

            suitable.append(
                PhotoCandidate(
                    index=cell.index,
                    cx=cell.cx,
                    cy=cell.cy,
                    category=category,
                    description=description,
                    suitable=True,
                )
            )
            logger.debug(
                f"[{self.account_id}] 缩略图#{cell.index + 1} 通过: "
                f"{category} | {description[:30]}"
            )

        if not suitable:
            logger.warning(f"[{self.account_id}] 无合格照片，回退到网格前几张")
            return self._fallback_pick(persona, photo_count, topic, cells)

        target_count = photo_count
        if target_count is None:
            target_count = random.randint(1, min(3, len(suitable)))

        if len(suitable) > target_count:
            # 真人会略随机，不总选最靠前几张
            weights = [1.0 / (i + 1.2) for i in range(len(suitable))]
            picked = []
            pool = suitable.copy()
            for _ in range(target_count):
                if not pool:
                    break
                choice = random.choices(pool, weights=weights[: len(pool)], k=1)[0]
                picked.append(choice)
                pool.remove(choice)
            picked.sort(key=lambda p: p.index)
        else:
            picked = suitable[:target_count]

        indices = [p.index for p in picked]
        descriptions = [p.description for p in picked if p.description]

        caption = self._build_caption(llm, persona, descriptions, topic)
        logger.info(
            f"[{self.account_id}] 选中照片索引={indices}, 配文='{caption[:24]}...'"
        )
        return indices, caption, descriptions

    def _build_caption(
        self,
        llm,
        persona: dict,
        descriptions: list[str],
        topic: str,
    ) -> str:
        if descriptions and llm.available:
            text = llm.generate_post_from_photos(persona, descriptions, topic=topic)
            if text:
                return text

        if llm.available:
            text = llm.generate_post_text(persona, topic=topic)
            if text:
                return text

        from content.post_templates import PostTemplateManager

        return PostTemplateManager().get_random_post(persona, topic=topic)

    def _fallback_pick(
        self,
        persona: dict,
        photo_count: int | None,
        topic: str,
        cells: list[ThumbnailCell] | None = None,
    ) -> tuple[list[int], str, list[str]]:
        count = photo_count or random.randint(1, 3)
        if cells:
            indices = list(range(min(count, len(cells))))
        else:
            indices = list(range(count))

        from content.llm_client import LLMClient

        llm = LLMClient()
        caption = self._build_caption(llm, persona, [], topic)
        return indices, caption, []
