# -*- coding: utf-8 -*-
"""
朋友圈每日互动冒烟 — 20 次互动，优先秒评大 V 新帖。

用法:
  python scripts/smoke_moments_daily_interact.py
  python scripts/smoke_moments_daily_interact.py <serial> [target_count]
"""

from __future__ import annotations

import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from config.settings import settings
from content.personas import get_moments_big_v_candidates, get_persona, random_persona
from core.device import DeviceManager
from core.humanizer import Humanizer
from core.wechat_control import WeChatControl
from scripts.base_script import BaseScript
from storage.db import Database
from utils.adb_utils import ADBUtils
from utils.logger import setup_logger

setup_logger(level="INFO")

DEFAULT_SERIAL = "aabcd8ab"


class _SmokeScript(BaseScript):
    STAGE_NAME = "smoke"

    def _build_weekday_script(self):
        raise NotImplementedError

    def _build_weekend_script(self):
        raise NotImplementedError


def main() -> int:
    print("=" * 55)
    print("  冒烟: 朋友圈每日互动（优先秒评大 V）")
    print("=" * 55)

    serial_arg = sys.argv[1] if len(sys.argv) >= 2 else None
    target = int(sys.argv[2]) if len(sys.argv) >= 3 else 5

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
        db.insert_account(
            id=account_id,
            device_serial=serial,
            stage="normal_use",
            persona_id="p01",
        )
        db.bind_device(serial=serial, account_id=account_id)

    account = db.get_account(account_id) or {}
    persona_id = account.get("persona_id") or "p01"
    persona = get_persona(persona_id) or random_persona()
    big_v = get_moments_big_v_candidates(persona)
    print(f"[INFO] 设备={serial} persona={persona_id} target={target} 大V候选={big_v[:5]}")

    d = dm.get_device(serial)
    wc = WeChatControl(d, Humanizer(), account_id=account_id)
    script = _SmokeScript(wc, persona, db)

    result = wc.moments_daily_interact(
        target_count=target,
        big_v_accounts=big_v,
        comment_fn=script._moments_comment_fn(),  # Vision 识图优先
        fresh_minutes=30,
        max_duration=600,
    )

    print(
        f"[RESULT] interactions={result.get('interactions')} "
        f"liked={result.get('liked')} commented={result.get('commented')} "
        f"big_v秒评={result.get('big_v_commented')} "
        f"elapsed={result.get('elapsed', 0):.0f}s"
    )

    if result.get("success"):
        print("\n[PASS] 朋友圈每日互动完成")
        return 0
    print("\n[FAIL] 未完成任何互动")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
