"""
机型配置注册与解析。

用法:
    from config.device_profiles import resolve_profile, get_coord, get_nav

    profile = resolve_profile(d)          # 按分辨率/型号自动匹配
    x, y = get_coord(d, "moments_entry")  # 百分比坐标
    rx, ry = get_nav(d, "channels_entry")

规则:
  - 每台机型独立文件，新增机型只加文件 + 注册，不改旧机型
  - 匹配不到时回退到 default_profile（Moto），并打 warning
"""

from __future__ import annotations

from typing import Any, Optional

from config.device_profiles.base import DeviceProfile
from config.device_profiles.moto_x70_air_pro import PROFILE as MOTO
from config.device_profiles.redmi_k30_pro import PROFILE as REDMI_K30
from utils.logger import get_logger

logger = get_logger("device_profiles")

# 已注册机型（顺序无关，按 score 选最高分）
PROFILES: list[DeviceProfile] = [
    MOTO,
    REDMI_K30,
]

DEFAULT_PROFILE: DeviceProfile = MOTO

# 按设备序列号缓存，避免每次 dump 型号信息
_cache: dict[str, DeviceProfile] = {}


def _device_key(d) -> str:
    try:
        info = d.info or {}
        serial = str(getattr(d, "serial", "") or info.get("serial", "") or "")
        w = int(info.get("displayWidth", 0))
        h = int(info.get("displayHeight", 0))
        return f"{serial}:{w}x{h}"
    except Exception:
        return "unknown"


def _model_blob(d) -> str:
    parts: list[str] = []
    try:
        info = d.info or {}
        for k in ("productName", "product", "model", "brand", "device"):
            v = info.get(k)
            if v:
                parts.append(str(v))
    except Exception:
        pass
    try:
        # uiautomator2 有时提供 shell getprop
        for prop in (
            "ro.product.model",
            "ro.product.device",
            "ro.product.name",
            "ro.product.brand",
        ):
            try:
                out = d.shell(f"getprop {prop}").output
                if out:
                    parts.append(str(out).strip())
            except Exception:
                continue
    except Exception:
        pass
    return " ".join(parts).lower()


def resolve_profile(d=None, width: int = 0, height: int = 0,
                    model_blob: str = "") -> DeviceProfile:
    """
    解析当前设备应对应的机型配置。

    优先用 d 的分辨率 + 型号；也可直接传 width/height。
    """
    if d is not None:
        key = _device_key(d)
        if key in _cache:
            return _cache[key]
        try:
            info = d.info or {}
            width = int(info.get("displayWidth", width) or 0)
            height = int(info.get("displayHeight", height) or 0)
        except Exception:
            pass
        if not model_blob:
            model_blob = _model_blob(d)

    best: Optional[DeviceProfile] = None
    best_score = 0
    for p in PROFILES:
        s = p.score(width, height, model_blob)
        if s > best_score:
            best_score = s
            best = p

    if best is None or best_score == 0:
        logger.warning(
            f"未匹配到机型配置 ({width}x{height}, model={model_blob!r})，"
            f"回退默认: {DEFAULT_PROFILE.display_name}。"
            f"请新增 config/device_profiles/ 下的机型文件并注册。"
        )
        best = DEFAULT_PROFILE
    else:
        logger.info(
            f"机型适配: {best.display_name} "
            f"({width}x{height}, score={best_score})"
        )

    if d is not None:
        _cache[_device_key(d)] = best
    return best


def get_coord_map(d=None) -> dict[str, tuple[float, float]]:
    """返回当前机型完整坐标表（默认机型 + 本机覆盖）。"""
    base = dict(DEFAULT_PROFILE.coords)
    profile = resolve_profile(d) if d is not None else DEFAULT_PROFILE
    if profile is not DEFAULT_PROFILE:
        base.update(profile.coords)
    else:
        base.update(DEFAULT_PROFILE.coords)
    return base


def get_coord(d, name: str) -> Optional[tuple[float, float]]:
    """取元素百分比坐标；不存在返回 None。"""
    profile = resolve_profile(d)
    if name in profile.coords:
        return profile.coords[name]
    return DEFAULT_PROFILE.coords.get(name)


def get_nav(d, key: str, default: Any = None) -> Any:
    """取导航配置项。"""
    profile = resolve_profile(d)
    if key in profile.nav:
        return profile.nav[key]
    return DEFAULT_PROFILE.nav.get(key, default)


def get_extra(d, key: str, default: Any = None) -> Any:
    """取模块专用配置。"""
    profile = resolve_profile(d)
    if key in profile.extras:
        return profile.extras[key]
    return DEFAULT_PROFILE.extras.get(key, default)


def clear_profile_cache():
    """测试或热插拔设备后清空缓存。"""
    _cache.clear()


def list_profiles() -> list[str]:
    return [f"{p.profile_id} ({p.display_name}) "
            f"{p.match.width}x{p.match.height}" for p in PROFILES]
