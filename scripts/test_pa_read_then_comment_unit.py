# -*- coding: utf-8 -*-
"""无设备单测：公众号文章须先读完再评论。"""

from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock, patch

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.public_account_browser import PublicAccountBrowser


def test_comment_only_after_full_read():
    """_read_article：分段阅读完成（scroll_to_bottom）后才进入评论。"""
    browser = PublicAccountBrowser.__new__(PublicAccountBrowser)
    browser.account_id = "ut"
    browser.persona = {"style": "casual"}
    browser.w, browser.h = 1080, 2400
    browser.d = MagicMock()
    browser._read_title_keys = set()

    order: list[str] = []

    def mark_read(**kwargs):
        order.append("read")
        assert kwargs.get("scroll_to_bottom") is True
        assert "fast_bottom" not in kwargs or kwargs.get("fast_bottom") is False

    def mark_comment(**kwargs):
        order.append("comment")
        return True

    with (
        patch.object(browser, "_ensure_on_feed_list", return_value=True),
        patch.object(
            browser,
            "_scan_feed_article_candidates",
            return_value=[(100, 200, "测试文章标题足够长", 20)],
        ),
        patch.object(browser, "_title_already_read", return_value=False),
        patch.object(browser, "_try_open_article", return_value=True),
        patch.object(browser, "_simulate_article_reading", side_effect=mark_read),
        patch.object(browser, "_like_article"),
        patch.object(browser, "_capture_article_context", return_value="正文摘要"),
        patch.object(browser, "_gen_article_comment", return_value="写得不错"),
        patch.object(browser, "_dismiss_image_viewer_if_any"),
        patch.object(browser, "_is_comment_entry_visible", return_value=True),
        patch.object(browser, "_comment_article", side_effect=mark_comment),
        patch.object(browser, "_mark_title_read"),
        patch("core.public_account_browser.random.random", return_value=0.0),
    ):
        # comment_rate=1.0 + random=0.0 → will_comment=True
        ok = browser._read_article(comment_rate=1.0)

    assert ok is True
    assert order == ["read", "comment"], f"期望先读后评，实际: {order}"


def test_article_bottom_requires_both_comment_markers():
    """文末判定：须同时 OCR 到「留言」与「写留言」。"""
    browser = PublicAccountBrowser.__new__(PublicAccountBrowser)
    browser.account_id = "ut"

    with patch.object(browser, "_ocr_region_blob", return_value="收藏分享留言写留言在看"):
        assert browser._is_article_bottom_visible() is True

    with patch.object(browser, "_ocr_region_blob", return_value="写留言说点什么"):
        assert browser._is_article_bottom_visible() is False

    with patch.object(browser, "_ocr_region_blob", return_value="留言区精选留言"):
        assert browser._is_article_bottom_visible() is False


def test_summary_feed_detection():
    """简略混合订阅流 vs 完整文章列表 vs 公众号主页。"""
    browser = PublicAccountBrowser.__new__(PublicAccountBrowser)
    browser.account_id = "ut"

    with patch.object(
        browser,
        "_ocr_screen_blob",
        side_effect=["公众号 搜索", "人民日报 更多消息 订阅精选"],
    ):
        assert browser._is_pa_aggregated_summary_feed() is True

    with patch.object(
        browser,
        "_ocr_screen_blob",
        side_effect=["人民日报 历史消息", "标题一 昨天 标题二 3天前 标题三 1周前"],
    ):
        assert browser._is_pa_aggregated_summary_feed() is False

    with patch.object(browser, "_ocr_screen_blob", return_value="人民日报 发消息 已关注"):
        assert browser._is_account_profile_home() is True

    with (
        patch.object(browser, "_is_account_profile_home", return_value=False),
        patch.object(browser, "_is_pa_aggregated_summary_feed", return_value=False),
        patch.object(
            browser,
            "_ocr_screen_blob",
            side_effect=["人民日报 历史消息", "标题一 昨天 标题二 3天前 标题三 1周前"],
        ),
    ):
        assert browser._is_account_article_list() is True


def test_enter_full_list_skips_when_already_on_history():
    browser = PublicAccountBrowser.__new__(PublicAccountBrowser)
    browser.account_id = "ut"

    with patch.object(browser, "_is_account_article_list", return_value=True):
        assert browser._enter_full_article_list_from_summary() is True


def test_enter_full_list_scrolls_then_finds_more_messages():
    browser = PublicAccountBrowser.__new__(PublicAccountBrowser)
    browser.account_id = "ut"
    browser.w, browser.h = 1080, 2400
    browser.d = MagicMock()

    with (
        patch.object(browser, "_is_pa_aggregated_summary_feed", side_effect=[True, False]),
        patch.object(browser, "_is_account_article_list", side_effect=[False, True]),
        patch.object(browser, "_is_wechat_chat_list", return_value=False),
        patch.object(browser, "_is_service_account_chat", return_value=False),
        patch.object(browser, "_looks_like_article_page", return_value=False),
        patch.object(browser, "_is_account_profile_home", return_value=False),
        patch.object(browser, "_is_on_article_feed", return_value=True),
        patch.object(browser, "_ocr_click_more_messages", return_value=True) as m_click_more,
        patch("core.public_account_browser.time.sleep"),
    ):
        ok = browser._enter_full_article_list_from_summary()

    assert ok is True
    m_click_more.assert_called()


def test_try_open_allows_account_article_list():
    """完整文章列表上点标题不应被当成误入主页而返回。"""
    browser = PublicAccountBrowser.__new__(PublicAccountBrowser)
    browser.account_id = "ut"
    browser.w, browser.h = 1080, 2400
    browser.d = MagicMock()
    browser._read_title_keys = set()

    with (
        patch.object(
            browser,
            "_feed_content_signature",
            side_effect=["sig_a", "sig_b"],
        ),
        patch.object(browser, "_is_unexpected_overlay", return_value=False),
        patch.object(browser, "_is_account_profile_home", return_value=False),
        patch.object(browser, "_is_on_article_feed", return_value=False),
        patch.object(browser, "_page_matches_title", return_value=True),
        patch("core.public_account_browser.time.sleep"),
    ):
        ok = browser._try_open_article(540, 800, "测试文章标题足够长可以打开")

    assert ok is True
    browser.d.press.assert_not_called()


def test_simulate_reading_does_segment_before_bottom():
    """scroll_to_bottom 时先分段阅读，再补滚到底（非 fast_bottom）。"""
    browser = PublicAccountBrowser.__new__(PublicAccountBrowser)
    browser.account_id = "ut"
    browser.w, browser.h = 1080, 2400
    browser.d = MagicMock()

    scrolls: list[str] = []

    def segment(*_a, **_k):
        scrolls.append("segment")

    def to_bottom(**_k):
        scrolls.append("bottom")
        return True

    with (
        patch("core.public_account_browser.time.sleep"),
        patch("core.public_account_browser.random.randint", return_value=4),
        patch("core.public_account_browser.random.uniform", return_value=0.5),
        patch("core.public_account_browser.random.random", return_value=1.0),
        patch.object(browser, "_article_read_scroll_once", side_effect=segment),
        patch.object(browser, "_is_comment_entry_visible", return_value=False),
        patch.object(browser, "_is_article_bottom_visible", return_value=False),
        patch.object(browser, "_scroll_to_article_bottom", side_effect=to_bottom),
    ):
        browser._simulate_article_reading(scroll_to_bottom=True, fast_bottom=False)

    assert scrolls[:4] == ["segment"] * 4
    assert scrolls[-1] == "bottom"
    assert "bottom" not in scrolls[:-1]


def main() -> int:
    test_comment_only_after_full_read()
    test_article_bottom_requires_both_comment_markers()
    test_summary_feed_detection()
    test_enter_full_list_skips_when_already_on_history()
    test_enter_full_list_scrolls_then_finds_more_messages()
    test_try_open_allows_account_article_list()
    test_simulate_reading_does_segment_before_bottom()
    # 保留原有标题校验
    from scripts.test_pa_article_open_unit import (
        test_title_already_read_prefix,
        test_title_match_truncation,
        test_title_mismatch_other_article,
    )

    test_title_match_truncation()
    test_title_mismatch_other_article()
    test_title_already_read_prefix()
    print("[PASS] pa read-then-comment order checks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
