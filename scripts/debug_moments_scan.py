# -*- coding: utf-8 -*-
"""诊断朋友圈 OCR 扫描与菜单点击。"""

from __future__ import annotations

import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.device import DeviceManager
from core.moments_interact import MomentsInteract
from utils.adb_utils import ADBUtils
from utils.logger import setup_logger

setup_logger(level="INFO")

SERIAL = sys.argv[1] if len(sys.argv) > 1 else "aabcd8ab"


def main() -> int:
    if not ADBUtils.is_device_online(SERIAL):
        print("[FAIL] 设备离线")
        return 1

    dm = DeviceManager()
    devices = dm.discover_and_connect_all()
    d = devices.get(SERIAL)
    if not d:
        print("[FAIL] u2 连接失败")
        return 1

    mi = MomentsInteract(d, account_id="debug")
    mi._ensure_on_moments()
    on = mi._is_on_moments()
    print(f"[1] 在朋友圈页: {on}")

    blocks = mi._ocr_blocks()
    print(f"[2] OCR 文本块: {len(blocks)}")
    for b in blocks[:25]:
        print(f"    y={b['y']:4d} x={b['x']:4d} conf={b['conf']:.2f} | {b['text'][:40]}")

    timestamps = mi._blocks_to_timestamps(blocks)
    print(f"[3] 时间戳帖: {len(timestamps)}")
    for i, ts in enumerate(timestamps[:8]):
        print(f"    #{i} y={ts['y']} | {ts['text']}")

    posts = mi._scan_posts_rich(["课程小助手"], fresh_minutes=30)
    print(f"[4] 富扫描帖: {len(posts)}")
    for i, p in enumerate(posts[:8]):
        print(
            f"    #{i} author='{p.get('author')}' big_v={p.get('is_big_v')} "
            f"fresh={p.get('is_fresh')} ts='{p.get('text')}' "
            f"content='{(p.get('content') or '')[:30]}'"
        )

    if posts:
        for i, post in enumerate(posts[:3]):
            opened = mi._open_menu(post)
            print(f"[5.{i}] 打开菜单 post#{i} y={post['y']} author={post.get('author')!r}: {opened}")
            if opened:
                buttons = mi._find_menu_buttons(mi._menu_y(post))
                print(f"      buttons={list(buttons.keys())}")
                break
    else:
        print("[5] 无帖可测菜单")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
