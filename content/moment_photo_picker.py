"""
朋友圈智能选图 — Vision 识图筛选 + 图文配文。

流程:
    1. 相册页截图 → Canny 检测缩略图网格
    2. 逐张裁剪缩略图 → Vision 判断是否适合发朋友圈
    3. 按主题/人设偏好加权（美食/旅行/日常优先，自拍降权）
    4. 避开近期已选索引，从合格候选中随机选 1~3 张
    5. 根据选中图片的画面描述生成配文
"""

from __future__ import annotations

import random
import re
from collections import defaultdict, deque
from dataclasses import dataclass

import cv2
import numpy as np

from utils.logger import get_logger

logger = get_logger("moment_photo_picker")

# 无 Vision 时的启发式阈值
_MIN_THUMB_STD = 12.0
_MIN_THUMB_MEAN = 18.0
_MAX_THUMB_MEAN = 245.0

# 主题/兴趣 → 优先类别
_TOPIC_PREFERRED: dict[str, list[str]] = {
    "生活": ["日常", "美食", "旅行", "风景", "宠物"],
    "日常": ["日常", "美食", "旅行", "风景", "宠物"],
    "美食": ["美食", "日常"],
    "旅行": ["旅行", "风景", "日常"],
    "风景": ["风景", "旅行"],
    "宠物": ["宠物", "日常"],
    "工作": ["日常", "风景", "其他"],
    "自拍": ["自拍", "日常"],
}

# 兴趣爱好关键词 → 类别
_HOBBY_CATEGORY: dict[str, str] = {
    "美食": "美食",
    "探店": "美食",
    "咖啡": "美食",
    "旅行": "旅行",
    "旅游": "旅行",
    "徒步": "旅行",
    "摄影": "风景",
    "看展": "日常",
    "猫": "宠物",
    "狗": "宠物",
    "宠物": "宠物",
}

# 生活向主题下，自拍/人像降权（仍可选，但尽量让出）
_SELFIE_LIKE = {"自拍", "人像", "妆造"}

# 账号维度：最近选过的网格索引，避免连发同一张
_RECENT_PICKS: dict[str, deque[int]] = defaultdict(lambda: deque(maxlen=8))

# 相册缩略图左下角时长，如 0:06 / 1:23
_DURATION_RE = re.compile(r"^\d{1,2}:\d{2}$")


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
    检测微信「图片和视频」相册缩略图网格。

    微信相册为固定 4 列近似正方形格子。用右上角勾选圆定位首行，
    避免网格整体上偏（上偏时下一格会裁进上一张下沿，Vision 易把截图误判成美食）。
    """
    h_img, w_img = gray_img.shape[:2]
    y_top = 160
    y_bottom = int(min(screen_h, h_img) * 0.78)
    cols = 4
    cell_w = max(120, w_img // cols)

    start_y, cell_h = _detect_album_grid_by_checkboxes(
        gray_img, y_top=y_top, y_bottom=y_bottom, cell_w=cell_w
    )
    if start_y is None:
        start_y, cell_h = _detect_album_grid_by_edges(
            gray_img, y_top=y_top, y_bottom=y_bottom, cell_w=cell_w
        )
    if start_y is None:
        start_y, cell_h = y_top + 45, cell_w

    cell_h = int(cell_h) if cell_h else cell_w
    if abs(cell_h - cell_w) > cell_w * 0.2:
        cell_h = cell_w

    cells: list[ThumbnailCell] = []
    for row in range(14):
        y = int(start_y + row * cell_h)
        if y + int(cell_h * 0.55) > y_bottom:
            break
        for col in range(cols):
            x = col * cell_w
            x0, y0 = x + 1, y + 1
            cw, ch = cell_w - 2, cell_h - 2
            if x0 + cw > w_img or y0 + ch > h_img or cw < 80 or ch < 80:
                continue
            patch = gray_img[y0:y0 + ch, x0:x0 + cw]
            if patch.size == 0:
                continue
            mean = float(patch.mean())
            std = float(patch.std())
            if std < 5.5 and mean > 242:
                continue
            cells.append(
                ThumbnailCell(
                    index=0,
                    cx=x0 + cw // 2,
                    cy=y0 + ch // 2,
                    x=x0,
                    y=y0,
                    w=cw,
                    h=ch,
                )
            )

    if len(cells) < 2:
        cells = _detect_album_thumbnails_canny(gray_img, screen_h)

    for i, cell_item in enumerate(cells):
        cell_item.index = i
    return cells


def _detect_album_grid_by_checkboxes(
    gray_img: np.ndarray,
    *,
    y_top: int,
    y_bottom: int,
    cell_w: int,
) -> tuple[int | None, int | None]:
    """用相册勾选圆（Hough）定位首行与行距。"""
    album = gray_img[y_top:y_bottom, :]
    if album.size == 0:
        return None, None
    blur = cv2.GaussianBlur(album, (5, 5), 0)
    try:
        circles = cv2.HoughCircles(
            blur,
            cv2.HOUGH_GRADIENT,
            dp=1.2,
            minDist=max(40, int(cell_w * 0.65)),
            param1=100,
            param2=20,
            minRadius=16,
            maxRadius=44,
        )
    except Exception:
        return None, None
    if circles is None:
        return None, None

    checks: list[int] = []
    for x, y, _r in np.round(circles[0]).astype(int):
        abs_x = int(x)
        abs_y = int(y) + y_top
        col = abs_x // cell_w
        local_x = abs_x - col * cell_w
        if local_x < int(cell_w * 0.55):
            continue
        if abs_y < y_top + 10 or abs_y > y_bottom - 20:
            continue
        checks.append(abs_y)

    if len(checks) < 3:
        return None, None

    checks.sort()
    rows_y: list[float] = []
    bucket: list[int] = []
    for cy in checks:
        if not bucket or abs(cy - int(np.mean(bucket))) < cell_w * 0.35:
            bucket.append(cy)
        else:
            rows_y.append(float(np.median(bucket)))
            bucket = [cy]
    if bucket:
        rows_y.append(float(np.median(bucket)))
    if not rows_y:
        return None, None

    cell_h = cell_w
    if len(rows_y) >= 2:
        gaps = np.diff(rows_y)
        gaps = gaps[(gaps > cell_w * 0.7) & (gaps < cell_w * 1.35)]
        if len(gaps) > 0:
            cell_h = int(np.median(gaps))

    start_y = int(rows_y[0] - 0.17 * cell_h)
    start_y = max(y_top, min(start_y, y_top + cell_w))
    return start_y, cell_h


def _detect_album_grid_by_edges(
    gray_img: np.ndarray,
    *,
    y_top: int,
    y_bottom: int,
    cell_w: int,
) -> tuple[int | None, int | None]:
    """无勾选圆时：用行方差找内容带，并下移避免裁进上一行。"""
    probe = gray_img[y_top:min(y_top + cell_w * 2, y_bottom), :]
    if probe.size == 0:
        return None, None
    row_std = probe.astype(np.float32).std(axis=1)
    thresh = max(10.0, float(np.median(row_std)) * 1.25)
    hits = np.where(row_std > thresh)[0]
    if len(hits) < 12:
        return None, None
    best = int(hits[0])
    run = 1
    for i in range(1, len(hits)):
        if hits[i] == hits[i - 1] + 1:
            run += 1
            if run >= int(cell_w * 0.35):
                best = int(hits[i] - run + 1)
                break
        else:
            run = 1
    start_y = y_top + best + int(cell_w * 0.08)
    return start_y, cell_w


def _detect_album_thumbnails_canny(
    gray_img: np.ndarray, screen_h: int
) -> list[ThumbnailCell]:
    """Canny 兜底：强制最小边长，过滤勾选圆。"""
    album = gray_img[180:int(screen_h * 0.78), :]
    edges = cv2.Canny(cv2.GaussianBlur(album, (5, 5), 0), 25, 80)
    edges = cv2.dilate(edges, np.ones((4, 4), np.uint8), iterations=1)
    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    cells: list[ThumbnailCell] = []
    for cnt in contours:
        x, y, cw, ch = cv2.boundingRect(cnt)
        ar = cw / ch if ch > 0 else 0
        if 140 < cw < 520 and 140 < ch < 520 and 0.65 < ar < 1.55:
            if cw * ch > 22000:
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
    return cells


def _crop_thumbnail(
    img_bgr: np.ndarray,
    cell: ThumbnailCell,
    *,
    for_vision: bool = False,
) -> np.ndarray:
    h_img, w_img = img_bgr.shape[:2]
    pad = 2
    x0 = max(0, cell.x - pad)
    y0 = max(0, cell.y - pad)
    x1 = min(w_img, cell.x + cell.w + pad)
    y1 = min(h_img, cell.y + cell.h + pad)
    crop = img_bgr[y0:y1, x0:x1]
    if not for_vision or crop.size == 0:
        return crop
    # Vision：去掉顶部 12%（防串图）+ 右侧勾选圆
    ch, cw = crop.shape[:2]
    top_cut = max(6, int(ch * 0.12))
    trim_x = max(10, int(cw * 0.14))
    return crop[top_cut:ch, 0:max(cw - trim_x, cw // 2)]


def _encode_jpeg(crop_bgr: np.ndarray, max_width: int = 480) -> bytes:
    h, w = crop_bgr.shape[:2]
    if w > max_width:
        scale = max_width / w
        crop_bgr = cv2.resize(crop_bgr, (max_width, int(h * scale)))
    ok, buf = cv2.imencode(".jpg", crop_bgr, [cv2.IMWRITE_JPEG_QUALITY, 78])
    return buf.tobytes() if ok else b""


def is_video_thumbnail(
    img_bgr: np.ndarray,
    cell: ThumbnailCell,
    reader=None,
) -> bool:
    """
    检测相册缩略图是否为视频（左下角时长 + 可选 OCR）。

    混选图片与视频时微信会进入「制作视频」流程，发朋友圈应跳过视频。
    """
    crop = _crop_thumbnail(img_bgr, cell)
    if crop.size == 0:
        return False

    ch, cw = crop.shape[:2]
    # 时长标签在左下角；播放图标在中部（白/灰三角，与照片区分）
    corner = crop[int(ch * 0.72): ch, 0: int(cw * 0.48)]
    if corner.size == 0:
        return False

    if reader is not None:
        try:
            results = reader.readtext(corner)
            for _bbox, text, conf in results:
                if conf < 0.35:
                    continue
                t = str(text or "").strip().replace(" ", "")
                if _DURATION_RE.match(t):
                    return True
                if ":" in t and re.search(r"\d:\d{2}", t):
                    return True
        except Exception:
            pass
        return False

    # 无 OCR 时用启发式（仅兜底）
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    white_ratio = float(np.mean(binary > 200))
    # 时长文字：小区域高对比白字
    if white_ratio > 0.04 and float(np.std(gray)) > 28:
        center = crop[int(ch * 0.28): int(ch * 0.72), int(cw * 0.28): int(cw * 0.72)]
        if center.size > 0:
            cg = cv2.cvtColor(center, cv2.COLOR_BGR2GRAY)
            if float(np.std(cg)) > 22 and 40 < float(np.mean(cg)) < 210:
                return True
    return False


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


def resolve_preferred_categories(persona: dict, topic: str) -> list[str]:
    """根据发圈主题 + 人设兴趣，得到优先选图类别（保序去重）。"""
    preferred: list[str] = []
    topic_key = (topic or "日常").strip()
    preferred.extend(_TOPIC_PREFERRED.get(topic_key, ["日常", "美食", "旅行", "风景"]))

    for hobby in persona.get("hobbies") or []:
        hobby_s = str(hobby)
        for key, cat in _HOBBY_CATEGORY.items():
            if key in hobby_s:
                preferred.append(cat)

    for t in persona.get("topics") or []:
        t_s = str(t)
        for key, cat in _HOBBY_CATEGORY.items():
            if key in t_s:
                preferred.append(cat)

    # 生活向默认再补一层
    if topic_key in ("生活", "日常", ""):
        preferred.extend(["美食", "旅行", "日常", "风景"])

    seen: set[str] = set()
    out: list[str] = []
    for c in preferred:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out


def _normalize_category(category: str) -> str:
    c = (category or "日常").strip()
    aliases = {
        "生活": "日常",
        "人像": "自拍",
        "妆造": "自拍",
        "街拍": "日常",
        "打卡": "日常",
        "景点": "旅行",
        "旅游": "旅行",
        "菜品": "美食",
        "餐厅": "美食",
        "咖啡": "美食",
    }
    return aliases.get(c, c)


def _score_candidate(
    cand: PhotoCandidate,
    preferred: list[str],
    recent: set[int],
) -> float:
    """
    选图权重：偏好类别高分，自拍在生活向下降权，近期已选大幅降权。
    不再用「网格越靠前越高」——那会固定选同一张。
    """
    cat = _normalize_category(cand.category)
    if preferred and cat in preferred:
        # 越靠前的偏好分越高
        rank = preferred.index(cat)
        weight = 4.0 - min(rank, 3) * 0.6
    elif cat in _SELFIE_LIKE and preferred and "自拍" not in preferred:
        weight = 0.25
    elif cat == "其他":
        weight = 0.7
    else:
        weight = 1.0

    # 轻微位置抖动，避免同分时总定在某一张；幅度远小于类别分差
    weight *= 0.85 + 0.3 * random.random()

    if cand.index in recent:
        weight *= 0.08

    return max(weight, 0.02)


def _weighted_sample(
    suitable: list[PhotoCandidate],
    target_count: int,
    preferred: list[str],
    recent: set[int],
) -> list[PhotoCandidate]:
    pool = suitable.copy()
    picked: list[PhotoCandidate] = []

    # 若存在偏好类别候选，优先从偏好池抽；抽不够再从全体补
    preferred_pool = [
        c for c in pool if _normalize_category(c.category) in preferred
    ] if preferred else []

    def _draw(from_pool: list[PhotoCandidate], n: int) -> None:
        nonlocal pool
        for _ in range(n):
            if not from_pool:
                break
            weights = [_score_candidate(c, preferred, recent) for c in from_pool]
            choice = random.choices(from_pool, weights=weights, k=1)[0]
            picked.append(choice)
            from_pool.remove(choice)
            if choice in pool:
                pool.remove(choice)

    need = target_count
    if preferred_pool:
        take = min(need, len(preferred_pool))
        _draw(preferred_pool, take)
        need = target_count - len(picked)

    if need > 0 and pool:
        _draw(pool, need)

    picked.sort(key=lambda p: p.index)
    return picked


class MomentPhotoPicker:
    """朋友圈智能选图器。"""

    MAX_SCAN = 12

    def __init__(self, d, account_id: str = ""):
        self.d = d
        self.account_id = account_id
        self.w = d.info["displayWidth"]
        self.h = d.info["displayHeight"]
        self._ocr = None

    def _get_ocr(self):
        if self._ocr is None:
            from utils.ocr_utils import create_easyocr_reader
            self._ocr = create_easyocr_reader()
        return self._ocr

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
        preferred = resolve_preferred_categories(persona or {}, topic)
        logger.info(
            f"[{self.account_id}] 选图偏好类别={preferred}, topic={topic!r}"
        )

        suitable: list[PhotoCandidate] = []
        reader = self._get_ocr()

        for cell in cells[: self.MAX_SCAN]:
            if is_video_thumbnail(img_bgr, cell, reader):
                logger.info(f"[{self.account_id}] 缩略图#{cell.index + 1} 跳过视频")
                continue

            crop = _crop_thumbnail(img_bgr, cell)
            if crop.size == 0:
                continue

            ok, reject = _heuristic_suitable(crop)
            if not ok:
                logger.info(
                    f"[{self.account_id}] 缩略图#{cell.index + 1} 启发式拒绝: {reject}"
                )
                continue

            category = "日常"
            description = ""
            reject_reason = ""

            if use_vision:
                vision_crop = _crop_thumbnail(img_bgr, cell, for_vision=True)
                jpeg = _encode_jpeg(vision_crop if vision_crop.size else crop)
                if jpeg:
                    result = llm.classify_moment_thumbnail(
                        jpeg,
                        preferred_categories=preferred,
                        topic=topic,
                    )
                    ok = bool(result.get("suitable"))
                    category = str(result.get("category") or "日常")
                    description = str(result.get("description") or "").strip()
                    reject_reason = str(result.get("reject_reason") or "").strip()
                    if not ok:
                        logger.info(
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
            logger.info(
                f"[{self.account_id}] 缩略图#{cell.index + 1} 通过: "
                f"{category} | {description[:40]}"
            )

            # 生活向：已凑够偏好类照片就提前结束，缩短相册停留
            need = photo_count or 1
            preferred_hits = [
                c
                for c in suitable
                if _normalize_category(c.category) in preferred
                and _normalize_category(c.category) not in _SELFIE_LIKE
            ]
            if len(preferred_hits) >= max(need, 2):
                logger.info(
                    f"[{self.account_id}] 已有 {len(preferred_hits)} 张偏好图，提前结束扫描"
                )
                break

        if not suitable:
            logger.warning(f"[{self.account_id}] 无合格照片，回退到网格前几张")
            return self._fallback_pick(persona, photo_count, topic, cells)

        target_count = photo_count
        if target_count is None:
            target_count = random.randint(1, min(3, len(suitable)))
        target_count = max(1, min(target_count, len(suitable)))

        recent = set(_RECENT_PICKS[self.account_id or "_"])
        picked = _weighted_sample(suitable, target_count, preferred, recent)

        # 若偏好池为空导致全是自拍，再尝试强制避开近期索引重抽一次
        if (
            preferred
            and all(_normalize_category(p.category) in _SELFIE_LIKE for p in picked)
            and any(
                _normalize_category(c.category) not in _SELFIE_LIKE for c in suitable
            )
        ):
            non_selfie = [
                c
                for c in suitable
                if _normalize_category(c.category) not in _SELFIE_LIKE
            ]
            picked = _weighted_sample(non_selfie, target_count, preferred, recent)

        indices = [p.index for p in picked]
        descriptions = [p.description for p in picked if p.description]
        cats = [p.category for p in picked]

        for idx in indices:
            _RECENT_PICKS[self.account_id or "_"].append(idx)

        caption = self._build_caption(llm, persona, descriptions, topic)
        logger.info(
            f"[{self.account_id}] 选中照片索引={indices}, 类别={cats}, "
            f"配文='{caption[:24]}...'"
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
        recent = set(_RECENT_PICKS[self.account_id or "_"])
        if cells:
            img = np.array(self.d.screenshot(format="pillow"))
            img_bgr = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
            reader = self._get_ocr()
            pool = [
                i
                for i, cell in enumerate(cells)
                if not is_video_thumbnail(img_bgr, cell, reader)
            ]
            if not pool:
                pool = list(range(len(cells)))
            # 回退时也尽量避开最近选过的
            fresh = [i for i in pool if i not in recent] or pool
            random.shuffle(fresh)
            indices = sorted(fresh[: min(count, len(fresh))])
        else:
            indices = list(range(count))

        for idx in indices:
            _RECENT_PICKS[self.account_id or "_"].append(idx)

        from content.llm_client import LLMClient

        llm = LLMClient()
        caption = self._build_caption(llm, persona, [], topic)
        return indices, caption, []
