# -*- coding: utf-8 -*-
"""定向修复验证：视频号 fling + 加好友手机号。"""

from __future__ import annotations

import time

from config.settings import settings
from core.device import DeviceManager
from core.humanizer import Humanizer
from core.wechat_control import WeChatControl
from core.channels_browser import ChannelsBrowser
from core.social_actions import SocialActions
from storage.db import Database
from utils.logger import setup_logger, get_logger

setup_logger(level="INFO")
logger = get_logger("fix_channels_friend")

SERIAL = "aabcd8ab"
PHONE = "13346396313"


def main():
    print("=" * 55)
    print("  定向验证: 视频号滑动 + 加好友")
    print("=" * 55)

    db = Database(settings.DB_PATH)
    db.init_db()
    dm = DeviceManager()
    devices = dm.discover_and_connect_all()
    if SERIAL not in devices and devices:
        serial = list(devices.keys())[0]
    else:
        serial = SERIAL
    if not devices:
        print("[FAIL] 无设备")
        return 1

    print(f"设备: {serial}")
    dm.ensure_wechat_foreground(serial)
    account_id = dm.get_bound_account(serial) or f"smoke_{serial[:6]}"
    if not db.get_account(account_id):
        db.insert_account(id=account_id, device_serial=serial, stage="trust_building")
        db.bind_device(serial=serial, account_id=account_id)

    d = dm.get_device(serial)
    wc = WeChatControl(d, Humanizer(), account_id=account_id)
    results = []

    # ---- 1. 视频号：进入后连续切，并用帧差验证是否真切成功 ----
    print("\n[1] 视频号上滑切换 x3 (帧差校验) ...")
    t0 = time.time()
    try:
        browser = ChannelsBrowser(d, account_id=account_id)
        browser.DWELL_MIN = 1.0
        browser.DWELL_MAX = 1.5
        browser._enter_channels()
        time.sleep(1.0)
        prev = browser._frame_signature()
        switched = 0
        for _ in range(3):
            if browser._swipe_next(prev_sig=prev):
                switched += 1
                prev = browser._frame_signature()
            else:
                browser._fling_up(
                    int(browser.w * 0.5),
                    int(browser.h * 0.70),
                    int(browser.h * 0.18),
                    140,
                )
                time.sleep(1.0)
                now = browser._frame_signature()
                if now != prev:
                    switched += 1
                    prev = now
        ok = switched >= 2
        detail = f"switched={switched}/3 {time.time()-t0:.1f}s"
        print(f"  [{'PASS' if ok else 'FAIL'}] scroll_channels_fling — {detail}")
        results.append(("scroll_channels_fling", ok, detail))
    except Exception as e:
        print(f"  [FAIL] scroll_channels_fling — {e}")
        results.append(("scroll_channels_fling", False, str(e)))

    time.sleep(1.5)

    # ---- 2. 加好友 ----
    print(f"\n[2] 加好友 {PHONE} ...")
    t0 = time.time()
    try:
        social = SocialActions(d, account_id=account_id)
        ok = social.add_friend(PHONE, remark_source="manual_test")
        detail = f"{time.time()-t0:.1f}s"
        if ok:
            try:
                db.add_friend(account_id, PHONE, source="active_add")
            except Exception:
                pass
            print(f"  [PASS] add_friend — {detail}")
        else:
            print(f"  [FAIL] add_friend — {detail}")
        results.append(("add_friend", ok, detail))
    except Exception as e:
        print(f"  [FAIL] add_friend — {e}")
        results.append(("add_friend", False, str(e)))

    passed = sum(1 for _, ok, _ in results if ok)
    failed = sum(1 for _, ok, _ in results if not ok)
    print("\n" + "=" * 55)
    print(f"  结果: PASS {passed} / FAIL {failed}")
    print("=" * 55)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
