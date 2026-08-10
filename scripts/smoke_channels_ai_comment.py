# -*- coding: utf-8 -*-
"""
视频号 AI 评论冒烟 — Vision 识图 + OCR 文案 → LLM 生成评论 → 发送。

用法:
  python scripts/smoke_channels_ai_comment.py
  python scripts/smoke_channels_ai_comment.py <serial>

需配置 Vision（HUNYUAN_VISION_MODEL 或 LLM_VISION_MODEL）；
未配置时回退 OCR+文本 LLM；再失败则用短评兜底池。
"""

from __future__ import annotations

import os
import random
import sys
import time

# 运行脚本时补齐项目根目录到 sys.path，确保能 import config/core/storage 等包
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from config.settings import settings
from content.llm_client import LLMClient
from content.personas import random_persona
from core.channels_browser import ChannelsBrowser
from core.device import DeviceManager
from core.wechat_nav import goto_tab, ocr_find_and_click, start_wechat, click_ratio
from storage.db import Database
from utils.adb_utils import ADBUtils
from utils.logger import setup_logger

setup_logger(level="INFO")

DEFAULT_SERIAL = "aabcd8ab"
FALLBACK_COMMENTS = ["不错", "学到了", "哈哈哈", "支持", "有意思", "太真实了"]


def _llm_configured() -> bool:
    provider = str(getattr(settings, "LLM_PROVIDER", "deepseek") or "deepseek").strip().lower()
    if provider == "hunyuan" and getattr(settings, "HUNYUAN_API_KEY", ""):
        return True
    return bool(settings.LLM_API_KEY)


def make_channel_comment_fn(persona: dict):
    """与 BaseScript._channel_comment_fn 一致：Vision 优先 → OCR+LLM → 兜底短评。"""

    def _gen(video_context: str, image_jpeg: bytes | None = None) -> str:
        try:
            client = LLMClient()
            if image_jpeg and client.vision_available:
                text = client.generate_channel_comment_from_image(
                    persona, image_jpeg, video_context or ""
                )
                if text:
                    return text[:40]
            text = client.generate_channel_comment(persona, video_context or "")
            if text:
                return text[:40]
        except Exception:
            pass
        return random.choice(FALLBACK_COMMENTS)

    return _gen


def _warm_enter(browser: ChannelsBrowser) -> None:
    d = browser.d
    start_wechat(d, wait=3.0, cold=False)
    goto_tab(d, "discover")
    time.sleep(1.0)
    clicked = ocr_find_and_click(
        d,
        browser._get_ocr(),
        ["视频号"],
        y_min_ratio=0.08,
        y_max_ratio=0.55,
        conf_min=0.3,
        enhance=browser._enhance,
        click_row_center=True,
    )
    if not clicked:
        click_ratio(d, *browser.CHANNELS_ENTRY)
    time.sleep(2.5)


def _reset_ui(browser: ChannelsBrowser) -> None:
    d = browser.d
    for _ in range(5):
        d.press("back")
        time.sleep(0.4)
    try:
        d.set_input_ime(False)
    except Exception:
        pass


def main() -> int:
    print("=" * 55)
    print("  冒烟: Vision 识图 + OCR → LLM 评论 → 发送")
    print("=" * 55)

    serial_arg = sys.argv[1] if len(sys.argv) >= 2 else None
    if serial_arg:
        serial = serial_arg
    elif ADBUtils.is_device_online(DEFAULT_SERIAL):
        serial = DEFAULT_SERIAL
    else:
        online = ADBUtils.list_devices()
        if not online:
            print("[FAIL] 无设备")
            return 1
        serial = online[0]

    if not _llm_configured():
        print("[WARN] 未配置 LLM（LLM_API_KEY 或 HUNYUAN_API_KEY），将使用兜底短评")

    llm = LLMClient()
    if llm.vision_available:
        ok, detail = llm.probe_vision()
        print(f"[INFO] Vision: {'可用' if ok else '不可用'} — {detail}")
    else:
        print("[WARN] 未配置 Vision（HUNYUAN_VISION_MODEL 或 LLM_VISION_MODEL），将仅用 OCR+文本")

    db = Database(settings.DB_PATH)
    db.init_db()
    dm = DeviceManager()
    devices = dm.discover_and_connect_all()
    if serial not in devices:
        print("[FAIL] u2 连接失败")
        return 1

    dm.ensure_wechat_foreground(serial)
    account_id = dm.get_bound_account(serial) or f"smoke_{serial[:6]}"
    if not db.get_account(account_id):
        db.insert_account(id=account_id, device_serial=serial, stage="trust_building")
        db.bind_device(serial=serial, account_id=account_id)

    persona = random_persona()
    d = dm.get_device(serial)
    browser = ChannelsBrowser(d, account_id=account_id)
    comment_fn = make_channel_comment_fn(persona)

    print("[0] 重置界面 ...")
    _reset_ui(browser)

    print("[1] 进入视频号并切一条 ...")
    _warm_enter(browser)
    browser._swipe_next(dwell_after=False)
    time.sleep(1.2)
    rail = browser._right_rail_counts()
    print(f"  右侧栏计数: {rail}")

    print("[2] OCR 视频文案 + 截取画面 ...")
    video_context = browser.extract_video_context()
    image_jpeg = browser.capture_video_frame_jpeg()
    preview = video_context[:120] if video_context else "(未识别到文案)"
    print(f"  文案: {preview}")
    print(f"  画面 JPEG: {len(image_jpeg)} bytes")

    print("[3] LLM 生成评论 ...")
    text = comment_fn(video_context, image_jpeg)
    print(f"  评论: {text}")

    print("[4] 发表评论 ...")
    ok = browser._comment_current(text)
    print(f"  result={ok}")

    print("[5] 复查 ...")
    time.sleep(0.8)
    opened = browser._open_comment_panel()
    send_vis = browser._keyboard_with_send_visible() if opened else False
    # 点输入框看键盘
    if opened:
        browser._focus_comment_input()
        time.sleep(0.8)
        send_vis = browser._keyboard_with_send_visible()
        blob = browser._input_bar_blob(0.45)
        print(f"  半屏={opened} 发送可见={send_vis}")
        print(f"  OCR={blob[:100]}")
        stuck = text in blob and "发表评论" not in blob
        browser._go_back_to_video()
    else:
        stuck = False
        print("  半屏未打开")

    if ok and not stuck:
        print("\n[PASS] 评论发送成功")
        return 0
    print("\n[FAIL] 评论未真正发出")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
