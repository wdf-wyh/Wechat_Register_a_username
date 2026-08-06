# -*- coding: utf-8 -*-
"""
发朋友圈定向冒烟 — Vision 智能选图 + 图文配文。

用法:
  python -m scripts.post_moment_smoke
  python -m scripts.post_moment_smoke <serial>
"""

from __future__ import annotations

import os
import sys
from datetime import date

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from config.settings import settings
from content.llm_client import LLMClient
from content.personas import random_persona
from core.device import DeviceManager
from core.humanizer import Humanizer
from core.wechat_control import WeChatControl
from storage.db import Database
from utils.logger import get_logger, setup_logger

logger = get_logger("post_moment_smoke")


def main() -> int:
    setup_logger(level="INFO")
    serial = sys.argv[1] if len(sys.argv) >= 2 else None

    llm = LLMClient()
    vision_ok, vision_detail = llm.probe_vision()
    print(f"[INFO] Vision: {vision_detail}")

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

    account_id = dm.get_bound_account(serial) or f"post_smoke_{serial[:6]}"
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

    persona = random_persona()
    d = dm.get_device(serial)
    wc = WeChatControl(d, Humanizer(), account_id=account_id)
    wc.ensure_wechat_home()

    print(f"[INFO] 设备={serial} 账号={account_id} 人设={persona.get('name', '')}")
    print("[RUN] 智能选图发朋友圈 ...")

    ok = wc.post_moment(
        text="",
        image_count=1,
        persona=persona,
        smart_select=True,
        topic="日常",
    )

    if ok:
        print("[PASS] 朋友圈发送成功")
        logger.info(f"[{account_id}] post_moment_smoke pass vision={vision_ok}")
        return 0

    print("[FAIL] 朋友圈发送失败")
    logger.error(f"[{account_id}] post_moment_smoke fail")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
