# -*- coding: utf-8 -*-
"""无设备单测：视频号「未成年人模式」遮罩识别与关闭优先级。"""

from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock, patch

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.channels_browser import ChannelsBrowser, youth_mode_overlay_hit


def test_youth_mode_overlay_hit():
    sample = (
        "为呵护未成年人健康成长，微信推出未成年人模式。"
        "该模式下部分功能将受限制使用，请监护人主动设置。"
        "设置未成年人模式 > 我知道了 不再提醒"
    )
    assert youth_mode_overlay_hit(sample)
    assert youth_mode_overlay_hit("不再提醒")
    assert not youth_mode_overlay_hit("关注 点赞 评论 推荐")
    assert not youth_mode_overlay_hit("")


def _browser() -> ChannelsBrowser:
    d = MagicMock()
    d.info = {"displayWidth": 1264, "displayHeight": 2780}
    node = MagicMock()
    node.exists.return_value = False
    d.return_value = node  # d(text=...) → node
    return ChannelsBrowser(d, account_id="ut")


def test_dismiss_prefers_never_remind_via_ocr():
    browser = _browser()
    blob = "呵护未成年人 未成年人模式 我知道了 不再提醒"

    with patch.object(browser, "_screen_blob_quick", return_value=blob), patch(
        "core.channels_browser.ocr_find_and_click"
    ) as ocr_click, patch("core.channels_browser.click_ratio") as click_ratio:
        # 第一次点「不再提醒」成功
        ocr_click.side_effect = [True]

        ok = browser._dismiss_channels_overlays(deep=True)
        assert ok is True
        assert ocr_click.call_count == 1
        keys = list(ocr_click.call_args.args[2])
        assert "不再提醒" in keys
        click_ratio.assert_not_called()


def test_dismiss_fallback_coordinate_when_ocr_misses_buttons():
    browser = _browser()
    blob = "为呵护未成年人健康成长，微信推出未成年人模式。"

    with patch.object(browser, "_screen_blob_quick", return_value=blob), patch(
        "core.channels_browser.ocr_find_and_click", return_value=False
    ), patch("core.channels_browser.click_ratio") as click_ratio:
        ok = browser._dismiss_channels_overlays(deep=True)
        assert ok is True
        click_ratio.assert_called_once()
        args = click_ratio.call_args.args
        assert abs(args[1] - 0.68) < 1e-6
        assert abs(args[2] - 0.66) < 1e-6


def test_dismiss_deep_false_skips_ocr():
    browser = _browser()
    with patch.object(browser, "_screen_blob_quick") as quick, patch(
        "core.channels_browser.ocr_find_and_click"
    ) as ocr_click:
        ok = browser._dismiss_channels_overlays(deep=False)
        assert ok is False
        quick.assert_not_called()
        ocr_click.assert_not_called()


if __name__ == "__main__":
    test_youth_mode_overlay_hit()
    test_dismiss_prefers_never_remind_via_ocr()
    test_dismiss_fallback_coordinate_when_ocr_misses_buttons()
    test_dismiss_deep_false_skips_ocr()
    print("OK")
