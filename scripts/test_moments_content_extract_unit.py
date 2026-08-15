# -*- coding: utf-8 -*-
"""无设备单测：朋友圈正文/作者抽取须贴合帖子，不能被配图 OCR 噪声带偏。"""

from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock, patch

import numpy as np
import cv2

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.moments_interact import MomentsInteract


def _mi() -> MomentsInteract:
    d = MagicMock()
    d.info = {"displayWidth": 1264, "displayHeight": 2780}
    return MomentsInteract(d, "ut")


def _long_post_blocks() -> list[dict]:
    """昵称在上、正文在中、配图 OCR 噪声靠近时间戳（用户反馈的真实形态）。"""
    return [
        {"text": "课程小助手", "x": 200, "y": 900, "conf": 0.9},
        {
            "text": "今晚随手拍了两张，一盘刚做的红烧肉油亮亮的",
            "x": 220,
            "y": 980,
            "conf": 0.9,
        },
        {"text": "还有张朋友戴项链的侧脸", "x": 220, "y": 1040, "conf": 0.9},
        {"text": "美团", "x": 300, "y": 1450, "conf": 0.8},
        {"text": "-4-11", "x": 400, "y": 1500, "conf": 0.7},
        {"text": "3小时前", "x": 200, "y": 1600, "conf": 0.9},
    ]


def test_extract_author_prefers_nickname_not_image_ocr():
    mi = _mi()
    ts = {"x": 200, "y": 1600, "text": "3小时前"}
    author = mi._extract_author(_long_post_blocks(), ts)
    assert author == "课程小助手", f"期望昵称，实际: {author!r}"


def test_extract_content_keeps_post_text_not_empty_or_junk():
    mi = _mi()
    ts = {"x": 200, "y": 1600, "text": "3小时前"}
    blocks = _long_post_blocks()
    author = mi._extract_author(blocks, ts)
    content = mi._extract_content(blocks, ts, author)
    assert "红烧肉" in content, f"正文应含红烧肉，实际: {content!r}"
    assert "美团" not in content, f"不应含配图噪声美团: {content!r}"
    assert "-4-11" not in content, f"不应含配图噪声-4-11: {content!r}"


def test_extract_content_with_known_author_not_empty():
    """作者已由昵称锚点给出时，正文区间也不能被掏空。"""
    mi = _mi()
    ts = {"x": 200, "y": 1600, "text": "3小时前", "author_y": 900}
    content = mi._extract_content(_long_post_blocks(), ts, "课程小助手")
    assert "红烧肉" in content, f"已知作者时正文仍应保留: {content!r}"


def test_bottom_posts_skipped_for_menu():
    mi = _mi()
    assert mi._post_menu_reachable({"y": 2000}) is True
    assert mi._post_menu_reachable({"y": 2500}) is False


def test_moments_comment_fn_prefers_vision():
    """朋友圈评论：有截图时必须走 Vision，不得先被 OCR 文本 LLM 带偏。"""
    from scripts.base_script import BaseScript
    from core.humanizer import Humanizer

    class _S(BaseScript):
        STAGE_NAME = "ut"

        def _build_weekday_script(self):
            raise NotImplementedError

        def _build_weekend_script(self):
            raise NotImplementedError

    script = _S(MagicMock(), {"age": "28", "comment_style": "随意"}, MagicMock())
    script.h = Humanizer()
    gen = script._moments_comment_fn()

    calls: list[str] = []

    class _FakeLLM:
        vision_available = True

        def generate_moment_comment_from_image(self, persona, image_jpeg, post_context=""):
            calls.append("vision")
            assert image_jpeg == b"jpeg-bytes"
            return "红烧肉看着真香"

        def generate_comment(self, persona, post_text=""):
            calls.append("text")
            return "这又是啥课？"

    with patch("content.llm_client.LLMClient", _FakeLLM):
        text = gen("美团 -4-11", "课程小助手", b"jpeg-bytes")

    assert text == "红烧肉看着真香"
    assert calls == ["vision"], f"应只走 Vision，实际: {calls}"


def test_menu_buttons_prefer_popup_comment_on_like_row():
    """弹层「评论」与「赞」同行；下方评论区坐标即使也被 OCR 到也不能用。"""
    mi = _mi()
    y_ts = 1600
    dots = (1140, 1600)
    hits = [
        {"text": "赞", "x": 820, "y": 1600, "conf": 0.9},
        {"text": "评论", "x": 980, "y": 1600, "conf": 0.9},
        # 误检：Y 落在时间戳下方的评论灰区
        {"text": "评论", "x": 500, "y": 1720, "conf": 0.85},
    ]
    buttons = mi._parse_menu_button_hits(hits, y_ts, dots)
    assert buttons.get("menu_anchor") == (820, 1600)
    pt = mi._comment_click_point(buttons, y_ts, dots_xy=dots)
    assert pt is not None
    cx, cy = pt
    assert abs(cx - 980) < 30, f"应点弹层评论 X，实际: {pt}"
    assert abs(cy - 1600) < 5, f"Y 必须锁在赞同一行，实际: {pt}"


def test_multi_image_ignores_ocr_noise_left_of_popup():
    """多图配图 OCR 常在左/中部冒出「赞/评论」，不能当弹层按钮。"""
    mi = _mi()
    dots = (1140, 1600)
    hits = [
        # 配图杂字（多图右格附近）
        {"text": "赞", "x": 520, "y": 1595, "conf": 0.7},
        {"text": "评论", "x": 600, "y": 1598, "conf": 0.7},
        # 真弹层
        {"text": "赞", "x": 860, "y": 1600, "conf": 0.9},
        {"text": "评论", "x": 1000, "y": 1600, "conf": 0.9},
    ]
    buttons = mi._parse_menu_button_hits(hits, 1600, dots)
    assert buttons.get("like") == (860, 1600)
    assert buttons.get("comment") == (1000, 1600)
    pt = mi._comment_click_point(buttons, 1600, dots)
    assert pt is not None and pt[0] >= 860 and pt[0] < dots[0] - 50


def test_cv_ignores_mid_image_dark_blob():
    """多图右格暗块（x≈0.7w）不能当成「…」。"""
    mi = _mi()
    gray = np.full((2780, 1264), 240, dtype=np.uint8)
    # 假：配图区暗条
    cv2.rectangle(gray, (880, 1596), (920, 1604), 40, -1)
    # 真：最右栏「…」
    cv2.rectangle(gray, (1120, 1596), (1155, 1604), 40, -1)
    hits = mi._cv_score_dots_blobs(gray, y_ts=1600, ts_x=200)
    assert hits, "应找到最右「…」"
    assert hits[0][0] >= int(mi.w * 0.84), f"应只要最右栏: {hits}"
    assert all(cx >= int(mi.w * 0.84) for cx, _, _ in hits)


def test_comment_click_falls_back_to_offset_when_ocr_misses_popup():
    """OCR 没吃到弹层「评论」时，按赞与「…」之间几何位置点。"""
    mi = _mi()
    buttons = mi._parse_menu_button_hits(
        [{"text": "赞", "x": 820, "y": 1600, "conf": 0.9}],
        1600,
    )
    pt = mi._comment_click_point(buttons, 1600, dots_xy=(1140, 1600))
    assert pt is not None
    cx, cy = pt
    assert cy == 1600
    assert 820 < cx < 1140 - 50, f"评论应在赞与…之间，实际: {pt}"


def test_comment_click_never_hits_dots():
    """候选点必须离开「…」，否则会关掉弹层。"""
    mi = _mi()
    buttons = {
        "like": (1000, 1600),
        "menu_anchor": (1000, 1600),
    }
    dots = (1140, 1600)
    pts = mi._comment_click_candidates(buttons, 1600, dots_xy=dots)
    assert pts, "应有候选"
    for x, y in pts:
        assert x < dots[0] - 50, f"不可点到…附近: {(x, y)} dots={dots}"
        assert y == 1600


def test_already_liked_does_not_expose_like_click():
    """已赞菜单是「取消」+「评论」，不能把取消坐标当成赞去点。"""
    mi = _mi()
    buttons = mi._parse_menu_button_hits(
        [
            {"text": "取消", "x": 820, "y": 1600, "conf": 0.9},
            {"text": "评论", "x": 980, "y": 1600, "conf": 0.9},
        ],
        1600,
        (1140, 1600),
    )
    assert buttons.get("already_liked") is True
    assert "like" not in buttons
    pt = mi._comment_click_point(buttons, 1600, dots_xy=(1140, 1600))
    assert pt == (980, 1600)


def test_menu_open_requires_like_and_comment_pair():
    """删除确认框只有「取消」，不能当成赞/评论菜单已打开。"""
    mi = _mi()
    assert mi._menu_open_from_hits(
        [{"text": "取消", "x": 400, "y": 1400, "conf": 0.9},
         {"text": "删除", "x": 700, "y": 1400, "conf": 0.9}],
        y_ts=1600,
    ) is False
    assert mi._menu_open_from_hits(
        [{"text": "赞", "x": 820, "y": 1600, "conf": 0.9},
         {"text": "评论", "x": 980, "y": 1600, "conf": 0.9}],
        y_ts=1600,
    ) is True
    assert mi._menu_open_from_hits(
        [{"text": "取消", "x": 820, "y": 1600, "conf": 0.9},
         {"text": "评论", "x": 980, "y": 1600, "conf": 0.9}],
        y_ts=1600,
    ) is True


def test_dots_x_candidates_prefer_right_edge_not_delete():
    """「...」兜底优先靠右；时间戳近旁小偏移会点到「删除」，不得排在前面。"""
    mi = _mi()
    post = {"x": 200, "y": 1600, "detect_method": "timestamp"}
    xs = mi._dots_x_candidates(post)
    assert xs, "应有点击候选"
    for x in xs[:3]:
        assert x >= int(mi.w * 0.72), f"靠前候选应在右侧 ... 区，实际 x={x} xs={xs}"
    for x in xs:
        assert x < mi.w
        assert not (200 + 50 <= x <= 200 + 200), (
            f"候选不应落在删除区 x={x}"
        )


def test_dots_prefer_ocr_ellipsis_then_after_delete():
    """有 OCR「…」时优先；删除右侧仅在越过最右栏门槛后入选。"""
    mi = _mi()
    pts = mi._merge_dots_click_points(
        y_ts=1600,
        ts_x=200,
        ocr_hits=[
            {"text": "删除", "x": 880, "y": 1600, "conf": 0.9},
            {"text": "..", "x": 980, "y": 1600, "conf": 0.85},
        ],
        cv_hits=[(1100, 1600, 0.9)],
    )
    assert pts[0][0] == 980 and pts[0][1] == 1600, f"OCR「…」应最优先: {pts[:3]}"
    after_del = 880 + mi.DOTS_AFTER_DELETE_DX
    assert any(abs(x - after_del) < 5 and y == 1600 for x, y in pts[:5]), (
        f"删除右侧应入选: expect≈{after_del} pts={pts[:5]}"
    )


def test_ui_chrome_rejects_photo_texture():
    """浅底低纹理=时间戳行；高方差=配图，禁止点击。"""
    mi = _mi()
    gray = np.full((2780, 1264), 245, dtype=np.uint8)
    assert mi._is_ui_chrome_at(gray, 1100, 1600) is True
    # 模拟配图噪点
    rng = np.random.default_rng(0)
    gray[1585:1615, 700:760] = rng.integers(40, 220, size=(30, 60), dtype=np.uint8)
    assert mi._is_ui_chrome_at(gray, 730, 1600) is False


def test_merge_dots_y_always_timestamp_row():
    """即便 OCR 给出偏上的 Y（配图底边），合并后也必须锁回时间戳行。"""
    mi = _mi()
    pts = mi._merge_dots_click_points(
        y_ts=1600,
        ts_x=200,
        ocr_hits=[{"text": "..", "x": 1100, "y": 1480, "conf": 0.9}],
        cv_hits=[],
    )
    assert pts and all(y == 1600 for _, y in pts), f"Y 必须锁时间戳: {pts}"


def test_cv_finds_horizontal_dots_blob():
    """同行右侧横向暗色小块应被识别为「…」。"""
    mi = _mi()
    gray = np.full((2780, 1264), 240, dtype=np.uint8)
    # 在 y=1600 右侧画两个小点连成的横条
    y, x = 1600, 1080
    cv2.rectangle(gray, (x, y - 4), (x + 36, y + 4), 40, -1)
    hits = mi._cv_score_dots_blobs(gray, y_ts=1600, ts_x=200)
    assert hits, "应找到暗色横条"
    cx, cy, _ = hits[0]
    assert abs(cy - 1600) <= 10
    assert cx > 1000


def main() -> int:
    test_extract_author_prefers_nickname_not_image_ocr()
    test_extract_content_keeps_post_text_not_empty_or_junk()
    test_extract_content_with_known_author_not_empty()
    test_bottom_posts_skipped_for_menu()
    test_moments_comment_fn_prefers_vision()
    test_menu_buttons_prefer_popup_comment_on_like_row()
    test_multi_image_ignores_ocr_noise_left_of_popup()
    test_cv_ignores_mid_image_dark_blob()
    test_comment_click_falls_back_to_offset_when_ocr_misses_popup()
    test_comment_click_never_hits_dots()
    test_already_liked_does_not_expose_like_click()
    test_menu_open_requires_like_and_comment_pair()
    test_dots_x_candidates_prefer_right_edge_not_delete()
    test_dots_prefer_ocr_ellipsis_then_after_delete()
    test_ui_chrome_rejects_photo_texture()
    test_merge_dots_y_always_timestamp_row()
    test_cv_finds_horizontal_dots_blob()
    print("[PASS] moments content extract / vision comment / menu reachability")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
