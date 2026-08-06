# -*- coding: utf-8 -*-
"""
公众号评论定向冒烟测试 — 只验证「阅读公众号文章 -> 写评论 -> 点绿色发送」链路。

用法:
  python scripts/public_account_comment_smoke.py
  python scripts/public_account_comment_smoke.py <serial>
"""

from __future__ import annotations

import os
import sys
from datetime import date, timedelta

# 运行脚本时补齐项目根目录到 sys.path，确保能 import config/core/storage 等包
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from config.settings import settings
from content.personas import random_persona
from core.device import DeviceManager
from core.humanizer import Humanizer
from core.public_account_browser import PublicAccountBrowser
from core.wechat_control import WeChatControl
from storage.db import Database
from utils.logger import get_logger, setup_logger

logger = get_logger("public_account_comment_smoke")


def main():
    # 调到 DEBUG，确保能看到“评论结果(ok/still_input)”等关键判断日志
    setup_logger(level="DEBUG")
    serial = sys.argv[1] if len(sys.argv) >= 2 else None
    duration_seconds = 180
    comment_rate = 1.0  # 目标是尽量触发评论发送

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

    account_id = dm.get_bound_account(serial) or f"pa_comment_smoke_{serial[:6]}"
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
    else:
        # 避免使用到很早的 registration_date 影响 stage 逻辑（仅冒烟）
        db.update_account(
            account_id,
            registration_date=(date.today() - timedelta(days=1)).isoformat(),
        )

    persona = random_persona()
    d = dm.get_device(serial)
    wc = WeChatControl(d, Humanizer(), account_id=account_id)

    # 快速回到微信 Tab（不再连按 back，避免退出到桌面误点系统搜索）
    wc.ensure_wechat_home()

    browser = PublicAccountBrowser(d, account_id=account_id, persona=persona)
    read = browser.browse(duration_seconds=duration_seconds, comment_rate=comment_rate)

    print(f"[PASS?] 公众号阅读完成: {read} 篇(评论尝试以日志为准)")
    logger.info(f"[{account_id}] smoke done read={read} comment_rate={comment_rate}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

