# -*- coding: utf-8 -*-
from pathlib import Path

path = Path("content/moment_photo_picker.py")
text = path.read_text(encoding="utf-8")
start = text.index("def detect_album_thumbnails")
end = text.index("def _encode_jpeg")

new = '''def detect_album_thumbnails(gray_img: np.ndarray, screen_h: int) -> list[ThumbnailCell]:
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


'''

path.write_text(text[:start] + new + text[end:], encoding="utf-8")
print("ok")
