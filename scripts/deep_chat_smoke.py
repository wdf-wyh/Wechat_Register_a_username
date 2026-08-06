# -*- coding: utf-8 -*-
"""
1v1 深度聊天冒烟测试 — 验证「打开会话 → OCR 读记录 → AI 生成 → 发送」链路。

用法:
  python -m scripts.deep_chat_smoke
  python -m scripts.deep_chat_smoke "30金色"
  python -m scripts.deep_chat_smoke "30金色" aabcd8ab
"""

from __future__ import annotations

import os
import sys
import time
from datetime import date

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from config.settings import settings
from content.llm_client import LLMClient
from content.personas import random_persona
from core.device import DeviceManager
from core.message_sender import MessageSender
from scripts.chat_history_reader import ChatHistoryReader, merge_sent_with_ocr
from scripts.manual_deep_chat import open_chat
from storage.db import Database
from utils.logger import get_logger, setup_logger

logger = get_logger("deep_chat_smoke")

DEFAULT_CONTACT = "30金色"
SMOKE_DURATION = 90
MIN_SEND_COUNT = 2


def _ensure_account(db: Database, dm: DeviceManager, serial: str) -> str:
    account_id = dm.get_bound_account(serial) or f"deep_chat_smoke_{serial[:6]}"
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
    return account_id


def run_smoke(contact: str, serial: str | None = None) -> dict:
    results: list[dict] = []

    def record(name: str, ok: bool, detail: str = "") -> None:
        status = "PASS" if ok else "FAIL"
        print(f"  [{status}] {name}" + (f" — {detail}" if detail else ""))
        results.append({"name": name, "ok": ok, "detail": detail})

    print("=" * 55)
    print("  冒烟: 1v1 AI 深度聊天")
    print("=" * 55)

    llm = LLMClient()
    record("llm_available", llm.available, "LLM_API_KEY 已配置" if llm.available else "未配置")

    db = Database(settings.DB_PATH)
    db.init_db()
    dm = DeviceManager()
    devices = dm.discover_and_connect_all()
    if not devices:
        record("device_online", False, "无设备")
        return _summary(results)

    serials = list(devices.keys())
    serial = serial or serials[0]
    if serial not in devices:
        record("device_online", False, f"设备不在线: {serial}")
        return _summary(results)

    record("device_online", True, serial)
    dm.ensure_wechat_foreground(serial)

    account_id = _ensure_account(db, dm, serial)
    persona = random_persona(seed=hash(account_id) % 10000)
    d = dm.get_device(serial)
    if d is None:
        record("u2_connect", False)
        return _summary(results)
    record("u2_connect", True)

    sender = MessageSender(d, account_id=account_id)
    reader = ChatHistoryReader(d, account_id=account_id)

    opened = open_chat(sender, contact)
    record("open_chat", opened, contact)
    if not opened:
        return _summary(results)

    ocr_history = reader.read_messages_with_voice(contact_name=contact, scroll_up=1)
    record("ocr_read_history", True, f"读到 {len(ocr_history)} 条可见消息")

    reply = llm.generate_chat_reply_from_history(
        persona=persona,
        contact=contact,
        history=ocr_history,
    )
    record(
        "llm_generate_reply",
        bool(reply),
        (reply[:36] + "...") if reply and len(reply) > 36 else (reply or "空"),
    )
    if not reply:
        return _summary(results)

    try:
        sender._input_message(reply)
        sender._click_send()
        record("send_message", True, reply[:40])
    except Exception as e:
        record("send_message", False, str(e))
        return _summary(results)

    sent_log = [{"role": "self", "text": reply}]
    sent_count = 1
    end_at = time.time() + SMOKE_DURATION

    while time.time() < end_at and sent_count < MIN_SEND_COUNT:
        time.sleep(20)
        ocr_history = reader.read_messages_with_voice(contact_name=contact, scroll_up=1)
        history = merge_sent_with_ocr(ocr_history, sent_log)
        recent = history[-6:]
        friend_msgs = [h for h in recent if h.get("role") == "friend"]
        last = history[-1] if history else None
        if last and last.get("role") == "self" and not friend_msgs:
            print("  [INFO] 等对方回复，跳过本轮")
            continue
        next_reply = llm.generate_chat_reply_from_history(
            persona=persona,
            contact=contact,
            history=history,
        )
        if not next_reply:
            continue
        try:
            sender._input_message(next_reply)
            sender._click_send()
            sent_log.append({"role": "self", "text": next_reply})
            sent_count += 1
            print(f"  [INFO] 追加发送 #{sent_count}: {next_reply[:40]}")
        except Exception as e:
            logger.warning(f"追加发送失败: {e}")
            break

    record(
        "multi_round_chat",
        sent_count >= MIN_SEND_COUNT,
        f"共发送 {sent_count} 条（目标 >= {MIN_SEND_COUNT}）",
    )
    logger.info(
        f"[{account_id}] deep_chat smoke done contact={contact} sent={sent_count}"
    )
    return _summary(results)


def _summary(results: list[dict]) -> dict:
    executed = [r for r in results if r["name"] != "llm_available" or not r["ok"]]
    failed = [r for r in results if not r["ok"]]
    ok = len(failed) == 0 and any(r["ok"] for r in results)
    print("-" * 55)
    print(
        f"  结论: {'PASS' if ok else 'FAIL'} "
        f"({sum(1 for r in results if r['ok'])}/{len(results)} 项通过)"
    )
    if failed:
        print("  失败项:", ", ".join(r["name"] for r in failed))
    return {"ok": ok, "results": results}


def main() -> int:
    setup_logger(level="INFO")
    contact = sys.argv[1] if len(sys.argv) >= 2 else DEFAULT_CONTACT
    serial = sys.argv[2] if len(sys.argv) >= 3 else None
    summary = run_smoke(contact=contact, serial=serial)
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
