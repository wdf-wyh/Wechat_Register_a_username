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


def open_search(d) -> bool:
    """在微信首页点击搜索图标（多候选，按机型）。"""
    import cv2
    import numpy as np

    w, h = window_size(d)
    img_before = np.array(d.screenshot(format="pillow"))
    gray_before = cv2.cvtColor(img_before, cv2.COLOR_RGB2GRAY)

    for rx, ry in search_icon_candidates_for(d):
        cx, cy = int(w * rx), int(h * ry)
        d.click(cx, cy)
        time.sleep(1.6)
        if dismiss_app_chooser(d):
            time.sleep(0.5)
        img_after = np.array(d.screenshot(format="pillow"))
        gray_after = cv2.cvtColor(img_after, cv2.COLOR_RGB2GRAY)
        diff = float(np.mean(cv2.absdiff(
            gray_after.astype(np.int16), gray_before.astype(np.int16))))
        if diff > 8:
            logger.debug(f"搜索页已打开 ({cx},{cy}) diff={diff:.0f}")
            return True
        d.press("back")
        time.sleep(0.4)
    return False


def ocr_find_and_click(
    d,
    reader,
    keywords: Iterable[str],
    *,
    y_min_ratio: float = 0.08,
    y_max_ratio: float = 0.92,
    conf_min: float = 0.35,
    enhance: Optional[Callable] = None,
    exact: bool = False,
    click_row_center: bool = False,
) -> bool:
    """
    OCR 全屏找关键词并点击文字中心。
    keywords 任一命中即可（子串或 exact）。
    click_row_center=True 时点该行中部（适合发现页列表项）。
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
    keys = list(keywords)
    best = None  # (y, cx, cy)

    for bbox, text, conf in results:
        if conf < conf_min:
            continue
        t = (text or "").strip()
        if not t:
            continue
        cy = int((bbox[0][1] + bbox[2][1]) / 2)
        if cy < y_min or cy > y_max:
            continue
        hit = False
        for k in keys:
            if exact:
                if t == k:
                    hit = True
                    break
            else:
                if k in t or t in k:
                    hit = True
                    break
        if not hit:
            continue
        cx = int((bbox[0][0] + bbox[2][0]) / 2)
        if best is None or cy < best[0]:
            best = (cy, cx, cy)

    if best is None:
        return False
    cx, cy = best[1], best[2]
    if click_row_center:
        cx = int(w * 0.45)
    d.click(cx, cy)
    time.sleep(2.0)
    return True
