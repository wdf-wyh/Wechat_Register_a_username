# -*- coding: utf-8 -*-
"""无设备单测：公众号标题指纹/匹配（打开校验用）。"""

from __future__ import annotations

import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.public_account_browser import PublicAccountBrowser


def test_title_match_truncation():
    expected = "明查|摩洛哥小伙家门口游泳,"
    page = "明查摩洛哥小伙家门口游泳 作者 某某"
    assert PublicAccountBrowser._title_match_score(expected, page) >= 0.42


def test_title_mismatch_other_article():
    expected = "明查|摩洛哥小伙家门口游泳,"
    page = "储备银行主席施密德在最新的讲话中警告称"
    assert PublicAccountBrowser._title_match_score(expected, page) < 0.3


def test_title_already_read_prefix():
    browser = PublicAccountBrowser.__new__(PublicAccountBrowser)
    browser._read_title_keys = set()
    browser._mark_title_read("明查|摩洛哥小伙家门口游泳,")
    assert browser._title_already_read("明查|摩洛哥小伙家门口游泳")
    assert not browser._title_already_read("储备银行主席施密德在最新的讲话中警告称,")


def main() -> int:
    test_title_match_truncation()
    test_title_mismatch_other_article()
    test_title_already_read_prefix()
    print("[PASS] pa title match / read-skip unit checks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
