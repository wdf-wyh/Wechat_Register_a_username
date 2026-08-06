# -*- coding: utf-8 -*-
"""
对比测试：EasyOCR vs LLM 多模态识图 读取聊天页消息。

同一张聊天区域截图，分别走 OCR 与 Vision，输出条数、耗时与内容 diff。

用法:
  python -m scripts.chat_read_compare_smoke
  python -m scripts.chat_read_compare_smoke "30金色"
  python -m scripts.chat_read_compare_smoke "30金色" aabcd8ab
"""

from __future__ import annotations

import os
import sys
import time
from datetime import date
from pathlib import Path

import cv2

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from config.settings import settings
from content.llm_client import LLMClient
from core.device import DeviceManager
from core.message_sender import MessageSender
from scripts.chat_history_reader import ChatHistoryReader
from scripts.manual_deep_chat import open_chat
from storage.db import Database
from utils.logger import get_logger, setup_logger

logger = get_logger("chat_read_compare")
DEFAULT_CONTACT = "30金色"


def _ensure_account(db: Database, dm: DeviceManager, serial: str) -> str:
    account_id = dm.get_bound_account(serial) or f"compare_{serial[:6]}"
    if not db.get_account(account_id):
        db.insert_account(
            id=account_id,
            device_serial=serial,
            stage="trust_building",
            registration_date=date.today().isoformat(),
        )
        db.bind_device(serial=serial, account_id=account_id)
    return account_id


def _norm(text: str) -> str:
    return "".join(str(text or "").split())


def _format_messages(items: list[dict]) -> list[str]:
    role_map = {"self": "我", "friend": "友"}
    lines = []
    for i, item in enumerate(items, 1):
        role = role_map.get(item.get("role", ""), "?")
        text = str(item.get("text", "")).strip()
        lines.append(f"  {i:2d}. [{role}] {text}")
    return lines


def _compare_text_sets(ocr_items: list[dict], vision_items: list[dict]) -> dict:
    ocr_texts = [_norm(x.get("text", "")) for x in ocr_items if _norm(x.get("text", ""))]
    vis_texts = [_norm(x.get("text", "")) for x in vision_items if _norm(x.get("text", ""))]

    ocr_set = set(ocr_texts)
    vis_set = set(vis_texts)
    overlap = ocr_set & vis_set

    only_ocr = [t for t in ocr_texts if t not in vis_set]
    only_vision = [t for t in vis_texts if t not in ocr_set]

    union = ocr_set | vis_set
    jaccard = (len(overlap) / len(union)) if union else 1.0

    return {
        "ocr_count": len(ocr_items),
        "vision_count": len(vision_items),
        "overlap": len(overlap),
        "only_ocr": only_ocr,
        "only_vision": only_vision,
        "jaccard": jaccard,
    }


def _resize_for_vision(crop_bgr, max_width: int = 720) -> bytes:
    h, w = crop_bgr.shape[:2]
    if w > max_width:
        scale = max_width / w
        crop_bgr = cv2.resize(crop_bgr, (max_width, int(h * scale)))
    ok, buf = cv2.imencode(".jpg", crop_bgr, [cv2.IMWRITE_JPEG_QUALITY, 75])
    if not ok:
        return b""
    return buf.tobytes()


def run_compare(contact: str, serial: str | None = None) -> dict:
    print("=" * 60)
    print("  对比: EasyOCR vs LLM 多模态识图（聊天页）")
    print("=" * 60)

    llm = LLMClient()
    if not llm.available:
        print("[FAIL] LLM 未配置")
        return {"ok": False}

    db = Database(settings.DB_PATH)
    db.init_db()
    dm = DeviceManager()
    devices = dm.discover_and_connect_all()
    if not devices:
        print("[FAIL] 无在线设备")
        return {"ok": False}

    serial = serial or list(devices.keys())[0]
    if serial not in devices:
        print(f"[FAIL] 设备不在线: {serial}")
        return {"ok": False}

    print(f"[INFO] 设备: {serial}")
    dm.ensure_wechat_foreground(serial)
    account_id = _ensure_account(db, dm, serial)
    d = dm.get_device(serial)
    if d is None:
        print("[FAIL] u2 连接失败")
        return {"ok": False}

    sender = MessageSender(d, account_id=account_id)
    reader = ChatHistoryReader(d, account_id=account_id)

    print(f"[INFO] 打开会话: {contact}")
    if not open_chat(sender, contact):
        print("[FAIL] 无法打开聊天页")
        return {"ok": False}

    time.sleep(1.0)
    crop = reader.capture_chat_region()

    out_dir = Path("logs") / "chat_read_compare"
    out_dir.mkdir(parents=True, exist_ok=True)
    shot_path = out_dir / f"chat_{serial[:6]}_{int(time.time())}.jpg"
    cv2.imwrite(str(shot_path), crop)
    print(f"[INFO] 截图已保存: {shot_path}")

    ok, buf = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, 85])
    if not ok:
        print("[FAIL] JPEG 编码失败")
        return {"ok": False}
    jpeg_bytes = _resize_for_vision(crop)

    print("\n--- EasyOCR ---")
    t0 = time.time()
    ocr_items = reader.ocr_from_crop(crop, contact_name=contact)
    ocr_elapsed = time.time() - t0
    print(f"  耗时: {ocr_elapsed:.1f}s | 条数: {len(ocr_items)}")
    for line in _format_messages(ocr_items):
        print(line)

    print("\n--- LLM Vision ---")
    vision_ok, vision_detail = llm.probe_vision()
    if not vision_ok:
        print(f"  [SKIP] {vision_detail}")
        vision_items = []
        vision_elapsed = 0.0
    else:
        print(f"  模型: {vision_detail}")
        print(f"  图片: {len(jpeg_bytes)//1024} KB (已压缩)")
        t0 = time.time()
        vision_items = llm.read_chat_messages_from_image(jpeg_bytes, contact_name=contact)
        vision_elapsed = time.time() - t0
        print(f"  耗时: {vision_elapsed:.1f}s | 条数: {len(vision_items)}")
        for line in _format_messages(vision_items):
            print(line)

    diff = _compare_text_sets(ocr_items, vision_items)
    print("\n--- 对比摘要 ---")
    print(f"  OCR 条数     : {diff['ocr_count']}")
    print(f"  Vision 条数  : {diff['vision_count']}")
    print(f"  文本重合     : {diff['overlap']} 条")
    print(f"  Jaccard 相似 : {diff['jaccard']:.2%}")
    print(f"  OCR 耗时     : {ocr_elapsed:.1f}s")
    print(f"  Vision 耗时  : {vision_elapsed:.1f}s")

    if diff["only_ocr"]:
        print("\n  仅 OCR 识别到:")
        for t in diff["only_ocr"][:8]:
            print(f"    - {t}")
    if diff["only_vision"]:
        print("\n  仅 Vision 识别到:")
        for t in diff["only_vision"][:8]:
            print(f"    - {t}")

    ok = len(ocr_items) > 0
    winner = "ocr"
    if vision_ok and vision_items:
        if diff["vision_count"] > diff["ocr_count"] and diff["jaccard"] >= 0.5:
            winner = "vision"
        elif diff["ocr_count"] > diff["vision_count"] and diff["jaccard"] >= 0.5:
            winner = "ocr"
        elif vision_elapsed < ocr_elapsed * 0.7 and diff["jaccard"] >= 0.6:
            winner = "vision (更快且相近)"
        elif ocr_elapsed < vision_elapsed * 0.7:
            winner = "ocr (更快)"
        else:
            winner = "tie"
    elif not vision_ok:
        winner = "ocr (vision 未配置)"

    print("-" * 60)
    print(f"  结论: {'PASS' if ok else 'FAIL'} | 综合: {winner}")
    logger.info(
        f"[{account_id}] compare ocr={diff['ocr_count']} vision={diff['vision_count']} "
        f"jaccard={diff['jaccard']:.2f} ocr_t={ocr_elapsed:.1f}s vis_t={vision_elapsed:.1f}s"
    )
    return {
        "ok": ok,
        "ocr_items": ocr_items,
        "vision_items": vision_items,
        "diff": diff,
        "ocr_elapsed": ocr_elapsed,
        "vision_elapsed": vision_elapsed,
        "screenshot": str(shot_path),
    }


def main() -> int:
    setup_logger(level="INFO")
    contact = sys.argv[1] if len(sys.argv) >= 2 else DEFAULT_CONTACT
    serial = sys.argv[2] if len(sys.argv) >= 3 else None
    result = run_compare(contact=contact, serial=serial)
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
