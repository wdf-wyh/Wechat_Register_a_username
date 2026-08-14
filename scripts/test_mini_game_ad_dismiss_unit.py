# -*- coding: utf-8 -*-
"""
无设备单测：小游戏「关广告」误关整局。

复现日志链（cold-start-burst Day5）：
  可玩判定前关闭广告层 → 通用小游戏拟人游玩中
  → 检测到广告层，尝试关闭 → 广告恢复 … 找游戏列表（小游戏已被关掉）
"""

from __future__ import annotations

import os
import sys
import time

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.social_actions import SocialActions, _MINI_GAME_AD_MARKERS


# 小程序胶囊关闭点（_close_miniprogram_capsule）
_CAPSULE_CLOSE_RATIOS = ((0.94, 0.052), (0.97, 0.055))
# 关广告盲点右上角（_dismiss_mini_game_ad fallback）
_AD_DISMISS_FALLBACK_RATIOS = ((0.93, 0.06), (0.95, 0.05), (0.88, 0.08))


def _near(a: float, b: float, eps: float = 0.025) -> bool:
    return abs(a - b) <= eps


def _hits_capsule_zone(rx: float, ry: float) -> bool:
    """与胶囊 X 足够近，在真机上会关掉小程序/小游戏。"""
    for cx, cy in _CAPSULE_CLOSE_RATIOS:
        if _near(rx, cx, 0.04) and _near(ry, cy, 0.03):
            return True
    # 经验阈值：右上角胶囊带
    return rx >= 0.90 and ry <= 0.09


def _make_sa(w: int = 1080, h: int = 2400) -> SocialActions:
    sa = SocialActions.__new__(SocialActions)
    sa.d = None
    sa.account_id = "unit"
    sa.w, sa.h = w, h
    sa._ocr_cache = None
    sa._ocr_cache_ts = 0.0
    sa._clahe = None
    return sa


def test_ad_fallback_coords_overlap_capsule():
    """H1：关广告盲点坐标与关胶囊坐标重叠。"""
    hits = [
        (rx, ry)
        for rx, ry in _AD_DISMISS_FALLBACK_RATIOS
        if _hits_capsule_zone(rx, ry)
    ]
    assert hits, (
        "预期至少有一个关广告 fallback 点落在胶囊关闭区；"
        f"fallback={_AD_DISMISS_FALLBACK_RATIOS} capsule={_CAPSULE_CLOSE_RATIOS}"
    )
    # 钉死具体危险点，避免以后只剩安全点却仍绿
    assert _hits_capsule_zone(0.93, 0.06)
    assert _hits_capsule_zone(0.95, 0.05)


def test_dismiss_ad_blind_tap_reports_success_after_leaving_game():
    """
    H1 因果链：OCR 找不到「跳过/关闭」→ 盲点右上角 → 离开小游戏后
    `_is_mini_game_ad_overlay` 变 False → 误报关广告成功。
    """
    sa = _make_sa()
    taps: list[tuple[float, float]] = []

    sa._ocr_click_any_boxed = lambda *a, **k: False  # type: ignore[method-assign]

    # 点之前仍在「广告层」；盲点后屏幕变成找游戏列表（无广告词）→ 判定广告已消失
    overlay_checks = {"n": 0}

    def fake_overlay(blob: str = "") -> bool:
        overlay_checks["n"] += 1
        # 第一次检查在 tap 后：已离开小游戏
        return False

    sa._is_mini_game_ad_overlay = fake_overlay  # type: ignore[method-assign]

    def fake_tap(x: int, y: int) -> None:
        taps.append((x / sa.w, y / sa.h))

    sa._adb_tap = fake_tap  # type: ignore[method-assign]

    # 加速 sleep
    real_sleep = time.sleep
    time.sleep = lambda _s: None
    try:
        ok = sa._dismiss_mini_game_ad()
    finally:
        time.sleep = real_sleep

    assert ok is True, "盲点关掉小游戏后仍会返回 True（误报成功）"
    assert taps, "应发生右上角盲点"
    assert _hits_capsule_zone(*taps[0]), (
        f"首个盲点应落在胶囊区，实际={taps[0]}"
    )


def test_false_positive_ad_marker_广告_alone():
    """H2：仅含「广告」二字的可玩界面也会被判为广告层。"""
    sa = _make_sa()
    # 模拟小游戏内角标/Banner 文案，并非全屏广告
    playing_blob = "得分 128 本局最佳 排行 广告"
    assert sa._is_mini_game_ad_overlay(playing_blob) is True
    assert "广告" in _MINI_GAME_AD_MARKERS


def test_left_mini_game_after_capsule_close_ocr():
    """日志同款：关广告后 OCR 已是找游戏列表 → `_is_left_mini_game`。"""
    sa = _make_sa()
    blob = (
        "朋友 圈子 找游戏 在玩 小游戏 分类 排行榜 福利 新: "
        "解压整个活 立即玩 躺平无限金币版 立即玩"
    )
    sa._ocr_screen_blob = lambda force=False: blob  # type: ignore[method-assign]
    sa.d = type("D", (), {"app_current": staticmethod(lambda: {"package": "com.tencent.mm", "activity": ".plugin.appbrand.ui.AppBrandUI1"})})()
    assert sa._is_left_mini_game(blob) is True
    assert sa._looks_like_game_opened("") is False


def test_symptom_guard_closes_then_recovers_to_find_games():
    """
    用户症状端到端（mock）：游玩中误判广告 → dismiss 盲点关局 →
    下一轮 guard 走恢复（离开可玩界面）。
    """
    sa = _make_sa()
    find_games = (
        "朋友 圈子 找游戏 在玩 小游戏 分类 排行榜 福利 "
        "狗狗救救 立即玩 躺平无限金币版 立即玩"
    )
    state = {"phase": "playing_false_ad"}

    def fake_blob(force: bool = False) -> str:
        if state["phase"] == "playing_false_ad":
            return "得分 66 广告 继续"
        return find_games

    sa._ocr_screen_blob = fake_blob  # type: ignore[method-assign]
    sa.d = type(
        "D",
        (),
        {
            "app_current": staticmethod(
                lambda: {
                    "package": "com.tencent.mm",
                    "activity": ".plugin.appbrand.ui.AppBrandUI1",
                }
            )
        },
    )()

    taps: list[tuple[float, float]] = []
    sa._ocr_click_any_boxed = lambda *a, **k: False  # type: ignore[method-assign]

    def fake_tap(x: int, y: int) -> None:
        taps.append((x / sa.w, y / sa.h))
        # 盲点胶囊 X 后立刻退回找游戏列表（真机症状）
        state["phase"] = "find_games"

    sa._adb_tap = fake_tap  # type: ignore[method-assign]

    real_sleep = time.sleep
    time.sleep = lambda _s: None
    try:
        # 第一拍：误判广告并「关闭」
        assert sa._is_mini_game_ad_overlay(fake_blob()) is True
        assert sa._is_left_mini_game(fake_blob()) is False
        ok = sa._dismiss_mini_game_ad()
        assert ok is True
        assert taps and _hits_capsule_zone(*taps[0])
        assert state["phase"] == "find_games"
        assert sa._is_left_mini_game() is True
    finally:
        time.sleep = real_sleep


def main() -> int:
    test_ad_fallback_coords_overlap_capsule()
    test_dismiss_ad_blind_tap_reports_success_after_leaving_game()
    test_false_positive_ad_marker_广告_alone()
    test_left_mini_game_after_capsule_close_ocr()
    test_symptom_guard_closes_then_recovers_to_find_games()
    print("[PASS] mini-game ad-dismiss closes game unit checks (bug characterized)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
