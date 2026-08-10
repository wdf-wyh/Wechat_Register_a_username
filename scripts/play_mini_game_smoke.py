# -*- coding: utf-8 -*-
"""
官方小游戏冒烟 — 发现→游戏→找游戏→立即玩（可按游戏名搜索）。

用法:
  python -m scripts.play_mini_game_smoke
  python -m scripts.play_mini_game_smoke <serial>
  python -m scripts.play_mini_game_smoke <serial> 跳一跳
  python -m scripts.play_mini_game_smoke <serial> 跳一跳 180
"""

from __future__ import annotations

import os
import sys
from datetime import date

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from config.settings import settings
from core.device import DeviceManager
from core.humanizer import Humanizer
from core.wechat_control import WeChatControl
from storage.db import Database
from utils.logger import get_logger, setup_logger

logger = get_logger("play_mini_game_smoke")

DEFAULT_DURATION = 180


def main() -> int:
    setup_logger(level="INFO")

    serial = sys.argv[1] if len(sys.argv) >= 2 else None
    game_name = sys.argv[2] if len(sys.argv) >= 3 else ""
    duration = max(45, int(sys.argv[3])) if len(sys.argv) >= 4 else DEFAULT_DURATION

    db = Database(settings.DB_PATH)
    db.init_db()

    dm = DeviceManager()
    devices = dm.discover_and_connect_all()
    if not devices:
        print("[FAIL] 无设备")
        return 1

    serials = list(devices.keys())
    if not serial:
        serial = serials[0]
    if serial not in devices:
        print(f"[FAIL] 指定设备不在线: {serial}")
        return 1

    dm.ensure_wechat_foreground(serial)

    account_id = dm.get_bound_account(serial) or f"game_smoke_{serial[:6]}"
    if not db.get_account(account_id):
        db.insert_account(
            id=account_id,
            device_serial=serial,
            stage="trust_building",
            registration_date=date.today().isoformat(),
            mode="full",
            state="normal",
        )
        db.bind_device(serial=serial, account_id=account_id)

    d = dm.get_device(serial)
    wc = WeChatControl(d, Humanizer(), account_id=account_id)
    wc.ensure_wechat_home()

    label = game_name or "找游戏·立即玩"
    print(f"[INFO] 设备={serial} 账号={account_id} 游戏={label} 时长={duration}s")
    print("[RUN] 玩官方小游戏 ...")

    ok = wc.play_mini_game(game_name=game_name, duration_seconds=duration)

    if ok:
        print("[PASS] 小游戏游玩完成")
        logger.info(f"[{account_id}] play_mini_game_smoke pass game={label}")
        return 0

    print("[FAIL] 小游戏游玩失败")
    logger.error(f"[{account_id}] play_mini_game_smoke fail game={label}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
