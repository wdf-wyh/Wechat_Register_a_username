# -*- coding: utf-8 -*-
"""
语音转文字冒烟测试 — 验证「打开会话 → 检测语音气泡 → 微信转文字 → OCR 读结果」。

用法:
  python -m scripts.voice_transcribe_smoke
  python -m scripts.voice_transcribe_smoke "30金色"
  python -m scripts.voice_transcribe_smoke "30金色" aabcd8ab

前置：聊天页中至少有一条对方语音消息（或已转写文字）。
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
from core.device import DeviceManager
from core.message_sender import MessageSender
from scripts.chat_history_reader import (
    ChatHistoryReader,
    _VOICE_PLACEHOLDER,
    _VOICE_UNTRANSCRIBED_LLM,
)


def _voice_is_transcribed(text: str) -> bool:
    t = str(text or "").strip()
    if not t:
        return False
    if _VOICE_PLACEHOLDER in t or _VOICE_UNTRANSCRIBED_LLM in t:
        return False
    return True
from scripts.manual_deep_chat import open_chat
from storage.db import Database
from utils.logger import get_logger, setup_logger

logger = get_logger("voice_transcribe_smoke")

DEFAULT_CONTACT = "30金色"


def _ensure_account(db: Database, dm: DeviceManager, serial: str) -> str:
    account_id = dm.get_bound_account(serial) or f"voice_smoke_{serial[:6]}"
    if not db.get_account(account_id):
        db.insert_account(
            id=account_id,
            device_serial=serial,
            stage="trust_building",
            registration_date=date.today().isoformat(),
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
    print("  冒烟: 1v1 语音转文字")
    print("=" * 55)

    db = Database(settings.DB_PATH)
    db.init_db()
    dm = DeviceManager()
    devices = dm.discover_and_connect_all()
    if not devices:
        record("device_online", False, "无设备")
        return _summary(results)

    serial = serial or list(devices.keys())[0]
    if serial not in devices:
        record("device_online", False, f"设备不在线: {serial}")
        return _summary(results)

    record("device_online", True, serial)
    dm.ensure_wechat_foreground(serial)

    account_id = _ensure_account(db, dm, serial)
    d = dm.get_device(serial)
    if d is None:
        record("u2_connect", False)
        return _summary(results)
    record("u2_connect", True)

    sender = MessageSender(d, account_id=account_id)
    reader = ChatHistoryReader(d, account_id=account_id)

    if not open_chat(sender, contact):
        record("open_chat", False, contact)
        return _summary(results)
    record("open_chat", True, contact)

    time.sleep(1.0)

    crop_path = reader.save_ocr_debug_crop("smoke")
    if crop_path:
        print(f"  [INFO] OCR 裁剪截图: {crop_path}")

    text_only = reader.read_messages(contact_name=contact, scroll_up=0)
    record("ocr_text_read", True, f"文字消息 {len(text_only)} 条")

    voice_history = reader.read_messages_with_voice(
        contact_name=contact,
        scroll_up=0,
        max_voice_transcribe=4,
    )
    voice_items = [h for h in voice_history if h.get("type") == "voice"]
    friend_voice = [h for h in voice_items if h.get("role") == "friend"]
    self_voice = [h for h in voice_items if h.get("role") == "self"]
    record(
        "voice_detect_or_transcribe",
        len(voice_history) > 0,
        f"共 {len(voice_history)} 条，语音 {len(voice_items)} 条"
        f"（友 {len(friend_voice)} / 我 {len(self_voice)}）",
    )

    print("\n--- 消息列表 ---")
    role_map = {"self": "我", "friend": "友"}
    for i, item in enumerate(voice_history, 1):
        role = role_map.get(item.get("role", ""), "?")
        kind = item.get("type", "text")
        text = item.get("text", "")
        dur = item.get("duration")
        extra = f" ({dur}s)" if dur and kind == "voice" else ""
        print(f"  {i:2d}. [{role}] [{kind}]{extra} {text[:60]}")

    transcribed = [
        h
        for h in voice_items
        if _voice_is_transcribed(h.get("text", ""))
    ]
    if voice_items and not friend_voice:
        print(
            "  [WARN] 未检测到好友语音。"
            "请让好友在聊天页发送一条语音（保持在当前可见区域）后重试。"
        )
    if voice_items:
        friend_ok = all(
            _voice_is_transcribed(h.get("text", ""))
            for h in friend_voice
        ) if friend_voice else True
        self_ok = all(
            _voice_is_transcribed(h.get("text", ""))
            for h in self_voice
        ) if self_voice else True
        record(
            "voice_transcript",
            len(transcribed) > 0 and friend_ok and self_ok,
            f"成功转写 {len(transcribed)}/{len(voice_items)} 条"
            f"（友 {len([h for h in friend_voice if _voice_is_transcribed(h.get('text',''))])}/{len(friend_voice)}"
            f" 我 {len([h for h in self_voice if _voice_is_transcribed(h.get('text',''))])}/{len(self_voice)}）",
        )
    else:
        record(
            "voice_transcript",
            True,
            "当前屏无语音气泡（跳过转写验证）",
        )

    logger.info(
        f"[{account_id}] voice_transcribe smoke contact={contact} "
        f"voice={len(voice_items)} transcribed={len(transcribed)}"
    )
    return _summary(results)


def _summary(results: list[dict]) -> dict:
    failed = [r for r in results if not r["ok"]]
    ok = len(failed) == 0
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
    return 0 if summary.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
