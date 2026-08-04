# -*- coding: utf-8 -*-
"""真机冒烟：系统键盘弹出后才有发送 → 写入 → 发送。"""

from __future__ import annotations

import time

from config.settings import settings
from core.channels_browser import ChannelsBrowser
from core.device import DeviceManager
from core.wechat_nav import goto_tab, ocr_find_and_click, start_wechat, click_ratio
from storage.db import Database
from utils.adb_utils import ADBUtils
from utils.logger import setup_logger

setup_logger(level="INFO")

SERIAL = "aabcd8ab"


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
    print("  冒烟: 点输入框弹键盘 → 发送")
    print("=" * 55)

    if not ADBUtils.is_device_online(SERIAL):
        online = ADBUtils.list_devices()
        if not online:
            print("[FAIL] 无设备")
            return 1
        serial = online[0]
    else:
        serial = SERIAL

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

    d = dm.get_device(serial)
    browser = ChannelsBrowser(d, account_id=account_id)

    print("[0] 重置界面 ...")
    _reset_ui(browser)

    print("[1] 进入视频号并切一条 ...")
    _warm_enter(browser)
    browser._swipe_next(dwell_after=False)
    time.sleep(1.2)
    rail = browser._right_rail_counts()
    print(f"  右侧栏计数: {rail}")

    text = "学到了"
    print(f"[2] 发表评论: {text}")
    ok = browser._comment_current(text)
    print(f"  result={ok}")

    print("[3] 复查 ...")
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
