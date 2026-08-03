# -*- coding: utf-8 -*-
"""仅测加好友手机号。"""
import time
from config.settings import settings
from core.device import DeviceManager
from core.social_actions import SocialActions
from storage.db import Database
from utils.logger import setup_logger

setup_logger(level="INFO")
PHONE = "13346396313"

def main():
    db = Database(settings.DB_PATH)
    db.init_db()
    dm = DeviceManager()
    devices = dm.discover_and_connect_all()
    if not devices:
        print("[FAIL] no device")
        return 1
    serial = "aabcd8ab" if "aabcd8ab" in devices else list(devices.keys())[0]
    print(f"device={serial} add={PHONE}")
    dm.ensure_wechat_foreground(serial)
    account_id = dm.get_bound_account(serial) or f"smoke_{serial[:6]}"
    if not db.get_account(account_id):
        db.insert_account(id=account_id, device_serial=serial, stage="trust_building")
    d = dm.get_device(serial)
    t0 = time.time()
    remark_source = "手动添加"
    ok = SocialActions(d, account_id).add_friend(PHONE, remark_source=remark_source)
    print(f"[{'PASS' if ok else 'FAIL'}] add_friend {time.time()-t0:.1f}s")
    print(f"  remark_source={remark_source}")
    if ok:
        try:
            db.add_friend(account_id, PHONE, source=f"active_add:{remark_source}")
        except Exception:
            pass
    return 0 if ok else 1

if __name__ == "__main__":
    raise SystemExit(main())
