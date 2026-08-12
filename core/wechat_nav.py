"""
微信导航公共工具 — 分辨率无关点击 + 小米/MIUI 双开处理。

所有模块冷启动微信时都应走 ``start_wechat()``，避免：
  1. 绝对像素坐标在不同分辨率下点偏
  2. MIUI 双开弹窗卡住自动化

导航坐标按 ``config.device_profiles`` 机型配置解析，
禁止在本文件写死某一机型坐标覆盖其他机型。
"""

from __future__ import annotations

import time
from typing import Callable, Iterable, Optional

from config.device_profiles import get_nav
from config.device_profiles.moto_x70_air_pro import PROFILE as _DEFAULT_NAV
from utils.logger import get_logger

logger = get_logger("wechat_nav")

WECHAT_PKG = "com.tencent.mm"

# 底部 Tab X 比例（各机型通用，Y 由机型配置决定）
TAB_X = {
    "wechat": 0.125,
    "contacts": 0.375,
    "discover": 0.625,
    "me": 0.875,
}

# 模块级默认值 = Moto 基线（仅兼容旧引用；运行时请用 *_for(d)）
TAB_Y = _DEFAULT_NAV.nav["tab_y"]
TAB_Y_CANDIDATES = _DEFAULT_NAV.nav["tab_y_candidates"]
SEARCH_ICON_CANDIDATES = list(_DEFAULT_NAV.nav["search_icon_candidates"])
MOMENTS_ENTRY = _DEFAULT_NAV.nav["moments_entry"]
CHANNELS_ENTRY = _DEFAULT_NAV.nav["channels_entry"]


def moments_entry_for(d) -> tuple[float, float]:
    return tuple(get_nav(d, "moments_entry", MOMENTS_ENTRY))


def channels_entry_for(d) -> tuple[float, float]:
    return tuple(get_nav(d, "channels_entry", CHANNELS_ENTRY))


def tab_y_candidates_for(d) -> tuple:
    return tuple(get_nav(d, "tab_y_candidates", TAB_Y_CANDIDATES))


def search_icon_candidates_for(d) -> list:
    return list(get_nav(d, "search_icon_candidates", SEARCH_ICON_CANDIDATES))


def window_size(d) -> tuple[int, int]:
    try:
        return d.window_size()
    except Exception:
        info = d.info
        return int(info["displayWidth"]), int(info["displayHeight"])


def lock_portrait(d) -> None:
    """
    强制竖屏，抑制自动化过程中微信短暂横屏闪切。

    即使系统「自动旋转」已关，ATX/部分 Activity 仍可能改方向；
    这里同时写 settings + u2 freeze + 设为 natural。
    """
    try:
        d.shell("settings put system accelerometer_rotation 0")
    except Exception:
        pass
    try:
        d.shell("settings put system user_rotation 0")
    except Exception:
        pass
    try:
        d.freeze_rotation(True)
    except Exception:
        pass
    try:
        # natural / n = 竖屏正向
        d.set_orientation("natural")
    except Exception:
        try:
            d.set_orientation("n")
        except Exception:
            pass
    # 若当前已是横屏（宽>高），再推一次竖屏
    try:
        w, h = window_size(d)
        if w > h:
            logger.warning(f"检测到横屏 {w}x{h}，强制恢复竖屏")
            try:
                d.set_orientation("natural")
            except Exception:
                pass
            time.sleep(0.3)
    except Exception:
        pass


def click_ratio(d, rx: float, ry: float):
    w, h = window_size(d)
    d.click(int(w * rx), int(h * ry))


def dismiss_app_chooser(d, prefer_left: bool = True) -> bool:
    """
    处理 MIUI「请选择要使用的应用」双开/多开弹窗。
    prefer_left=True 选择左侧主应用。
    """
    try:
        cur = d.app_current()
        activity = str(cur.get("activity", ""))
        need = "Resolver" in activity or "Chooser" in activity
        if not need:
            try:
                xml = d.dump_hierarchy()
                need = ("请选择要使用的应用" in xml) or ("下次默认选择" in xml)
            except Exception:
                need = False
        if not need:
            return False

        w, h = window_size(d)
        logger.info("检测到应用选择弹窗，选择主微信")
        # 勾选「下次默认」
        d.click(int(w * 0.10), int(h * 0.815))
        time.sleep(0.35)
        rx = 0.28 if prefer_left else 0.72
        d.click(int(w * rx), int(h * 0.72))
        time.sleep(2.0)
        return True
    except Exception as e:
        logger.debug(f"dismiss_app_chooser 失败: {e}")
        return False


def wake_and_unlock(d):
    """唤醒屏幕；仅在锁屏界面尝试上滑解锁，避免误滑进设置。"""
    d.screen_on()
    time.sleep(0.3)
    try:
        cur = d.app_current()
        pkg = str(cur.get("package", ""))
        act = str(cur.get("activity", ""))
        # 仅在锁屏相关界面滑动
        if ("keyguard" in pkg.lower() or "keyguard" in act.lower()
                or "LockScreen" in act or "launcher" not in act.lower()):
            # 桌面/微信前台不滑；只有明确锁屏才滑
            if "keyguard" in pkg.lower() or "keyguard" in act.lower() or "LockScreen" in act:
                w, h = window_size(d)
                d.swipe(w // 2, int(h * 0.85), w // 2, int(h * 0.2), duration=0.3)
                time.sleep(0.5)
    except Exception:
        pass


def start_wechat(d, wait: float = 4.0, cold: bool = True) -> bool:
    """
    启动微信并处理双开弹窗。

    Returns:
        True 表示微信包已在前台
    """
    import subprocess

    wake_and_unlock(d)
    lock_portrait(d)

    if cold:
        try:
            d.app_stop(WECHAT_PKG)
            time.sleep(1)
        except Exception:
            pass

    # 显式 Activity 比 app_start 更稳（避免落到设置等无关页）
    from utils.adb_utils import resolve_adb_path

    serial = getattr(d, "serial", None) or ""
    try:
        adb_bin = resolve_adb_path()
        cmd = [adb_bin]
        if serial:
            cmd += ["-s", serial]
        cmd += ["shell", "am", "start", "-n", f"{WECHAT_PKG}/.ui.LauncherUI"]
        subprocess.run(cmd, check=False, capture_output=True)
    except Exception:
        d.app_start(WECHAT_PKG)
    time.sleep(1.8)

    for _ in range(5):
        if dismiss_app_chooser(d):
            continue
        cur = d.app_current()
        if cur.get("package") == WECHAT_PKG:
            time.sleep(max(0.5, wait - 1.5))
            return True
        # 再拉一次
        try:
            adb_bin = resolve_adb_path()
            cmd = [adb_bin]
            if serial:
                cmd += ["-s", serial]
            cmd += ["shell", "am", "start", "-n", f"{WECHAT_PKG}/.ui.LauncherUI"]
            subprocess.run(cmd, check=False, capture_output=True)
        except Exception:
            d.app_start(WECHAT_PKG)
        time.sleep(1.5)

    ok = d.app_current().get("package") == WECHAT_PKG
    if not ok:
        logger.warning("start_wechat 未能把微信带到前台")
    return ok


def goto_tab(d, tab: str = "wechat"):
    """切换底部 Tab: wechat / contacts / discover / me"""
    import cv2
    import numpy as np

    rx = TAB_X.get(tab, TAB_X["wechat"])
    w, h = window_size(d)

    # 期望标题关键词（用于验证是否切成功）
    expect = {
        "wechat": ["微信"],
        "contacts": ["通讯录"],
        "discover": ["发现", "朋友圈", "视频号"],
        "me": ["我", "服务", "收藏", "设置"],
    }.get(tab, [])

    img_before = np.array(d.screenshot(format="pillow"))
    gray_before = cv2.cvtColor(img_before, cv2.COLOR_RGB2GRAY)

    for ry in tab_y_candidates_for(d):
        d.click(int(w * rx), int(h * ry))
        time.sleep(1.2)
        dismiss_app_chooser(d)
        img_after = np.array(d.screenshot(format="pillow"))
        gray_after = cv2.cvtColor(img_after, cv2.COLOR_RGB2GRAY)
        diff = float(np.mean(cv2.absdiff(
            gray_after.astype(np.int16), gray_before.astype(np.int16))))
        if diff < 5:
            continue
        # 粗验证：切到发现后顶部不应再是「微信(N)」会话列表主态
        # 用底部选中态 diff 足够大即可
        logger.debug(f"goto_tab {tab} @({rx:.3f},{ry:.3f}) diff={diff:.0f}")
        return
    # 最后兜底
    click_ratio(d, rx, get_nav(d, "tab_y", TAB_Y))
    time.sleep(1.5)


def locate_home_search_icon(d) -> Optional[tuple[float, float]]:
    """
    在微信首页顶栏视觉定位放大镜（两个深色图标中偏左的那个）。

    Returns:
        (rx, ry) 比例坐标；找不到则 None
    """
    import cv2
    import numpy as np

    try:
        shot = d.screenshot(format="opencv")
        if shot is None:
            return None
        h, w = shot.shape[:2]
        y0, y1 = int(h * 0.040), int(h * 0.100)
        x0, x1 = int(w * 0.68), int(w * 0.99)
        roi = shot[y0:y1, x0:x1]
        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        mask = (gray < 100).astype(np.uint8) * 255
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
        contours, _ = cv2.findContours(
            mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )
        icons = []
        for c in contours:
            x, y, bw, bh = cv2.boundingRect(c)
            area = bw * bh
            if area < 80 or area > 6000:
                continue
            if bw < 12 or bh < 12 or bw > 90 or bh > 90:
                continue
            # 近似方形图标
            if abs(bw - bh) > 25:
                continue
            cx = x0 + x + bw // 2
            cy = y0 + y + bh // 2
            icons.append((cx, cy, area))
        if len(icons) < 1:
            return None
        icons.sort(key=lambda t: t[0])
        # 右侧通常是「+」；取最右两个里偏左的，或仅一个时用它
        if len(icons) >= 2:
            # 过滤：最右若明显是加号，取它左边那个
            left, right = icons[-2], icons[-1]
            # 加号一般更靠右 (rx>~0.90)
            if right[0] / w >= 0.90 and left[0] / w < 0.90:
                cx, cy = left[0], left[1]
            else:
                # 取最左的合理候选（排除过左的噪声）
                cx, cy = left[0], left[1]
        else:
            cx, cy = icons[0][0], icons[0][1]
        rx, ry = cx / w, cy / h
        # 放大镜不应贴右边（那是 +）
        if rx >= 0.92:
            return None
        return rx, ry
    except Exception as e:
        logger.debug(f"locate_home_search_icon 失败: {e}")
        return None


def _ocr_region_blob(d, y_max_ratio: float = 0.48) -> str:
    """截屏 OCR 上半屏，用于判定搜索页 / 加号菜单（微信无障碍不可靠）。"""
    import cv2
    import numpy as np

    try:
        from utils.ocr_utils import create_easyocr_reader

        reader = create_easyocr_reader()
        img = np.array(d.screenshot(format="pillow"))
        h = img.shape[0]
        crop = img[0 : int(h * y_max_ratio), :]
        gray = cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY)
        results = reader.readtext(cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR))
        return " ".join(str(t) for _, t, c in results if c > 0.28)
    except Exception:
        return ""


def _is_plus_menu_blob(blob: str) -> bool:
    markers = ("发起群聊", "添加朋友", "扫一扫", "收付款", "面对面建群")
    return sum(1 for m in markers if m in blob) >= 2 or (
        "发起群聊" in blob and "添加朋友" in blob
    )


def _is_wechat_search_blob(blob: str) -> bool:
    if _is_plus_menu_blob(blob):
        return False
    # 新版微信全局搜索 FTSMainUI（含 AI/最近在搜）
    if any(
        k in blob
        for k in (
            "最近在搜",
            "搜索本地或网络结果",
            "搜索指定内容",
            "AI搜索",
            "A1搜索",
            "深度思考",
        )
    ):
        return True
    tabs = ("聊天记录", "朋友圈", "文章", "公众号", "小程序", "联系人", "全部")
    if sum(1 for t in tabs if t in blob) >= 2:
        return True
    if "搜索" in blob and sum(1 for t in tabs if t in blob) >= 1:
        return True
    return False


def _is_wechat_search_activity(d) -> bool:
    try:
        act = str(d.app_current().get("activity") or "")
    except Exception:
        return False
    act_l = act.lower()
    return ("fts" in act_l) or ("search" in act_l and "launcher" not in act_l)


def open_search(d) -> bool:
    """在微信首页点击搜索图标（视觉定位 + 机型候选）。

    必须已在微信前台。不能仅靠像素差：点到「+」菜单 diff 也很大。
    以 Activity/OCR 确认微信搜索页为准；误开加号菜单则 back 换下一候选。
    """
    import cv2
    import numpy as np

    if d.app_current().get("package") != WECHAT_PKG:
        logger.warning("open_search: 微信不在前台，拒绝点击顶栏")
        return False

    w, h = window_size(d)
    img_before = np.array(d.screenshot(format="pillow"))
    gray_before = cv2.cvtColor(img_before, cv2.COLOR_RGB2GRAY)

    candidates: list[tuple[float, float]] = []
    located = locate_home_search_icon(d)
    if located:
        candidates.append((float(located[0]), float(located[1])))
        logger.info(
            f"open_search 视觉定位放大镜 @({located[0]:.3f},{located[1]:.3f})"
        )

    # 避开最右侧「+」：rx>=0.92 几乎必中加号
    for rx, ry in search_icon_candidates_for(d):
        if float(rx) >= 0.92:
            continue
        pt = (float(rx), float(ry))
        if not any(
            abs(pt[0] - c[0]) < 0.012 and abs(pt[1] - c[1]) < 0.012
            for c in candidates
        ):
            candidates.append(pt)

    if not candidates:
        candidates = [
            (float(rx), float(ry))
            for rx, ry in search_icon_candidates_for(d)
            if float(rx) < 0.92
        ]

    for rx, ry in candidates:
        if d.app_current().get("package") != WECHAT_PKG:
            logger.warning("open_search: 微信不在前台，中止")
            return False
        cx, cy = int(w * rx), int(h * ry)
        d.click(cx, cy)
        time.sleep(1.2)
        if dismiss_app_chooser(d):
            time.sleep(0.5)
        cur = d.app_current()
        if cur.get("package") != WECHAT_PKG:
            logger.warning("open_search: 点击后离开微信，返回重试")
            try:
                d.app_start(WECHAT_PKG)
                time.sleep(2.0)
            except Exception:
                pass
            continue

        # Activity 最快最准：FTSMainUI
        if _is_wechat_search_activity(d):
            logger.info(
                f"搜索页已打开 @({rx:.3f},{ry:.3f}) activity={cur.get('activity')}"
            )
            return True

        img_after = np.array(d.screenshot(format="pillow"))
        gray_after = cv2.cvtColor(img_after, cv2.COLOR_RGB2GRAY)
        diff = float(
            np.mean(
                cv2.absdiff(
                    gray_after.astype(np.int16), gray_before.astype(np.int16)
                )
            )
        )

        blob = _ocr_region_blob(d, y_max_ratio=0.50)
        if _is_plus_menu_blob(blob):
            logger.info(f"open_search 误触加号菜单 @({rx:.3f},{ry:.3f})，返回重试")
            d.press("back")
            time.sleep(0.5)
            continue

        if _is_wechat_search_blob(blob):
            logger.info(f"搜索页已打开 @({rx:.3f},{ry:.3f}) diff={diff:.0f}")
            return True

        # 像素变了但不是搜索页：多半点偏，退回再试
        if diff > 8:
            logger.info(
                f"open_search 点击后非搜索页 @({rx:.3f},{ry:.3f}) "
                f"diff={diff:.0f} act={cur.get('activity')} blob={blob[:60]!r}"
            )
            d.press("back")
            time.sleep(0.45)
            continue

        d.press("back")
        time.sleep(0.35)
    return False


def ocr_find_and_click(
    d,
    reader,
    keywords: Iterable[str],
    *,
    y_min_ratio: float = 0.08,
    y_max_ratio: float = 0.92,
    x_min_ratio: float = 0.0,
    x_max_ratio: float = 1.0,
    conf_min: float = 0.35,
    enhance: Optional[Callable] = None,
    exact: bool = False,
    click_row_center: bool = False,
    click_x_bias: float = 0.0,
    post_click_sleep: float = 1.2,
) -> bool:
    """
    OCR 全屏找关键词并点击文字中心。
    keywords 任一命中即可（子串或 exact）。
    click_row_center=True 时点该行中部（适合发现页列表项）。
    click_x_bias: 相对文字框宽度的水平偏移（负=偏左，适合点「写评论」避开右侧配图）。
    """
    import cv2
    import numpy as np

    w, h = window_size(d)
    img = np.array(d.screenshot(format="pillow"))
    gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
    if enhance is not None:
        gray = enhance(gray)
    bgr = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    results = reader.readtext(bgr)

    y_min, y_max = int(h * y_min_ratio), int(h * y_max_ratio)
    x_min, x_max = int(w * x_min_ratio), int(w * x_max_ratio)
    keys = list(keywords)
    best = None  # (priority, y, cx, cy)  priority: exact match first

    for bbox, text, conf in results:
        if conf < conf_min:
            continue
        t = (text or "").strip()
        if not t:
            continue
        cy = int((bbox[0][1] + bbox[2][1]) / 2)
        cx = int((bbox[0][0] + bbox[2][0]) / 2)
        if cy < y_min or cy > y_max:
            continue
        if cx < x_min or cx > x_max:
            continue
        hit = False
        is_exact = False
        for k in keys:
            if t == k:
                hit = True
                is_exact = True
                break
            if not exact and (k in t or t in k):
                hit = True
                break
        if not hit:
            continue
        # 偏左点击：写评论输入条文字中心右侧常有配图/表情
        box_w = max(8, int(bbox[2][0] - bbox[0][0]))
        adj_cx = int(cx + click_x_bias * box_w)
        adj_cx = max(0, min(w - 1, adj_cx))
        priority = 0 if is_exact else 1
        cand = (priority, cy, adj_cx, cy)
        if best is None or cand < best:
            best = cand

    if best is None:
        return False
    cx, cy = best[2], best[3]
    if click_row_center:
        cx = int(w * 0.45)
    d.click(cx, cy)
    time.sleep(max(0.2, float(post_click_sleep)))
    return True
