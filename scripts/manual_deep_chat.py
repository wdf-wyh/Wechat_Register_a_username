"""
手动任务：针对指定好友执行多次 1v1 深聊。

默认使用 AI：每轮 OCR 读取聊天记录 → LLM 结合上下文生成下一条 → 发送，循环至时长结束。

用法:
    ./wechat_env/Scripts/python.exe -m scripts.manual_deep_chat \
        --contact "30金色" --times 5 --duration 330

    # 关闭 AI，使用静态模板（旧行为）
    ./wechat_env/Scripts/python.exe -m scripts.manual_deep_chat \
        --contact "30金色" --static
"""

from __future__ import annotations

import argparse
import random
import time

from config.settings import settings
from content.chat_templates import ChatTemplateManager
from content.llm_client import LLMClient
from content.personas import get_persona, random_persona
from core.device import DeviceManager
from core.message_sender import MessageSender
from core.social_actions import SocialActions
from scripts.chat_history_reader import (
    ChatHistoryReader,
    merge_sent_with_ocr,
    sanitize_chat_history_for_llm,
)
from storage.db import Database
from utils.logger import get_logger

logger = get_logger("manual_deep_chat")

LLM_UNAVAILABLE_REPLY = "请稍等"


def build_static_messages(rounds: int = 7) -> list[str]:
    """构造一轮静态深聊文本（模板兜底）。"""
    mgr = ChatTemplateManager()
    persona = {"name": "manual_deep_chat"}
    topics = ["greeting", "small_talk", "share"]
    messages: list[str] = []
    for idx in range(max(1, rounds)):
        topic = topics[idx % len(topics)]
        text = mgr.get_random_chat(topic, persona)
        if text:
            messages.append(str(text))
    return messages or ["在吗，最近怎么样？", "我这边在做点测试，顺便和你聊聊近况。"]


def open_chat(sender: MessageSender, contact: str) -> bool:
    """打开与联系人的聊天页（不发送消息）。"""
    try:
        sender._goto_home()
        sender._search_contact(contact)
        sender._click_contact_in_results(contact)
        return True
    except Exception as e:
        logger.error(f"打开聊天失败: {e}")
        return False


def _read_chat_for_llm(
    reader: ChatHistoryReader,
    llm: LLMClient,
    contact: str,
    scroll_up: int,
    max_voice_transcribe: int = 4,
) -> list[dict]:
    """优先 Vision 多页截图；不可用则回退 OCR。"""
    if llm.vision_available:
        history = reader.read_messages_via_vision(
            contact_name=contact,
            scroll_up=scroll_up,
            max_voice_transcribe=max_voice_transcribe,
        )
    else:
        history = reader.read_messages_with_voice(
            contact_name=contact,
            scroll_up=0,
            max_voice_transcribe=max_voice_transcribe,
        )
    return sanitize_chat_history_for_llm(history)


def send_contextual_reply(
    d,
    contact: str,
    persona: dict,
    account_id: str,
    text: str = "",
    scroll_up: int = 1,
    max_voice_transcribe: int = 4,
) -> bool:
    """
    读聊天历史（语音先转文字 → Vision 多页截图）后生成并发送一条回复。
    text 非空时直接发送，不读历史。
    LLM 不可用或生成失败时发送「请稍等」。
    """
    sender = MessageSender(d, account_id=account_id)
    if text:
        return sender.send(contact=contact, message=text)

    llm = LLMClient()
    if not llm.available:
        logger.warning(f"[{account_id}] LLM 未配置，发送: {LLM_UNAVAILABLE_REPLY}")
        return sender.send(contact=contact, message=LLM_UNAVAILABLE_REPLY)

    reader = ChatHistoryReader(d, account_id=account_id)
    if open_chat(sender, contact):
        try:
            history = _read_chat_for_llm(
                reader,
                llm,
                contact,
                scroll_up=scroll_up,
                max_voice_transcribe=max_voice_transcribe,
            )
            reply = llm.generate_chat_reply_from_history(
                persona=persona,
                contact=contact,
                history=history,
            )
            if reply:
                try:
                    sender._input_message(reply)
                    sender._click_send()
                    logger.info(
                        f"[{account_id}] 上下文回复已发送: {reply[:40]}"
                    )
                    return True
                except Exception as e:
                    logger.warning(
                        f"[{account_id}] 发送失败，重试打开会话: {e}"
                    )
                    if open_chat(sender, contact):
                        sender._input_message(reply)
                        sender._click_send()
                        return True
        except Exception as e:
            logger.warning(f"[{account_id}] 读历史/生成回复失败: {e}")

    logger.warning(f"[{account_id}] 无法生成 AI 回复，跳过发送")
    return False


def run_ai_session(
    d,
    contact: str,
    persona: dict,
    account_id: str,
    duration_seconds: int,
    scroll_up: int = 2,
) -> bool:
    """
    单次 AI 深聊：读聊天记录 → 生成 → 发送，直到达到 duration_seconds。
    """
    llm = LLMClient()
    if not llm.available:
        logger.warning(f"[{account_id}] LLM 未配置，发送: {LLM_UNAVAILABLE_REPLY}")
        sender = MessageSender(d, account_id=account_id)
        return sender.send(contact=contact, message=LLM_UNAVAILABLE_REPLY)

    sender = MessageSender(d, account_id=account_id)
    reader = ChatHistoryReader(d, account_id=account_id)
    if not open_chat(sender, contact):
        return False

    end_at = time.time() + max(301, duration_seconds)
    sent_log: list[dict] = []
    sent_count = 0
    min_gap = 25.0
    max_gap = 55.0

    print(f"[INFO] AI 深聊开始: contact={contact}, duration={duration_seconds}s")
    mode = "Vision多页" if llm.vision_available else "OCR当前屏"
    print(f"[INFO] 读记录模式: {mode}")

    while time.time() < end_at:
        remaining = end_at - time.time()
        if remaining < min_gap:
            break

        ocr_history = _read_chat_for_llm(
            reader,
            llm,
            contact,
            scroll_up=scroll_up,
            max_voice_transcribe=4,
        )
        history = sanitize_chat_history_for_llm(
            merge_sent_with_ocr(ocr_history, sent_log)
        )

        recent = history[-6:]
        friend_msgs = [h for h in recent if h.get("role") == "friend"]
        last = history[-1] if history else None
        if (
            last
            and last.get("role") == "self"
            and not friend_msgs
            and sent_count > 0
        ):
            wait = min(
                random.uniform(min_gap, max_gap),
                max(5.0, remaining - 10),
            )
            print(f"[INFO] 等对方回复，跳过本轮（{wait:.0f}s 后再读）")
            time.sleep(wait)
            continue

        reply = llm.generate_chat_reply_from_history(
            persona=persona,
            contact=contact,
            history=history,
        )
        if not reply:
            print("[WARN] LLM 未返回内容，等待后重试")
            time.sleep(10)
            continue

        print(f"[AI] 生成回复: {reply[:40]}{'...' if len(reply) > 40 else ''}")
        try:
            sender._input_message(reply)
            sender._click_send()
        except Exception as e:
            logger.error(f"发送失败: {e}")
            if not open_chat(sender, contact):
                return sent_count > 0
            continue

        sent_log.append({"role": "self", "text": reply})
        sent_count += 1
        print(f"[PASS] 已发送第 {sent_count} 条")

        wait = min(
            random.uniform(min_gap, max_gap),
            max(5.0, remaining - 10),
        )
        print(f"[INFO] 等待 {wait:.0f}s 后继续读聊天记录...")
        time.sleep(wait)

    print(f"[DONE] 本次 AI 深聊结束，共发送 {sent_count} 条")
    return sent_count > 0


def run_static_session(
    d,
    contact: str,
    account_id: str,
    duration_seconds: int,
) -> bool:
    """旧版：预生成多条消息按间隔发送。"""
    messages = build_static_messages(rounds=7)
    return SocialActions(d, account_id=account_id).deep_chat(
        contact=contact,
        messages=messages,
        total_seconds=max(301, duration_seconds),
    )


def resolve_persona(db: Database, account_id: str) -> dict:
    account = db.get_account(account_id) or {}
    persona_id = account.get("persona_id") or ""
    return get_persona(persona_id) or random_persona(seed=hash(account_id) % 10000)


def main() -> int:
    parser = argparse.ArgumentParser(description="手动执行 1v1 深聊任务")
    parser.add_argument("--contact", required=True, help="好友昵称")
    parser.add_argument("--times", type=int, default=5, help="深聊次数")
    parser.add_argument(
        "--duration",
        type=int,
        default=330,
        help="每次总时长（秒，建议 > 300）",
    )
    parser.add_argument(
        "--account-id",
        default="acc_001",
        help="执行账号 ID（默认 acc_001）",
    )
    parser.add_argument(
        "--static",
        action="store_true",
        help="使用静态模板，不用 AI 读聊天记录",
    )
    parser.add_argument(
        "--scroll-up",
        type=int,
        default=2,
        help="每次读记录前上滑次数（加载更早消息）",
    )
    args = parser.parse_args()

    dm = DeviceManager()
    devices = dm.discover_and_connect_all()
    if not devices:
        print("[FAIL] 未发现在线设备")
        return 1

    serial = list(devices.keys())[0]
    db = Database(settings.DB_PATH)
    account_id = args.account_id
    account = db.get_account(account_id)
    if not account:
        db.insert_account(id=account_id, device_serial=serial, stage="trust_building")
    db.bind_device(serial=serial, account_id=account_id)
    dm._device_serial_to_account[serial] = account_id
    dm.ensure_wechat_foreground(serial)

    d = dm.get_device(serial)
    if d is None:
        print(f"[FAIL] 设备未连接: {serial}")
        return 1

    persona = resolve_persona(db, account_id)
    ok_count = 0
    total = max(1, args.times)
    each_duration = max(301, int(args.duration))
    use_ai = not args.static

    mode = "AI上下文深聊" if use_ai else "静态模板深聊"
    print(
        f"[INFO] 开始执行深聊任务: mode={mode}, serial={serial}, "
        f"account={account_id}, contact={args.contact}, "
        f"times={total}, duration={each_duration}s"
    )

    for i in range(1, total + 1):
        print(f"[INFO] 第 {i}/{total} 次深聊开始")
        if use_ai:
            ok = run_ai_session(
                d,
                contact=str(args.contact),
                persona=persona,
                account_id=account_id,
                duration_seconds=each_duration,
                scroll_up=max(0, args.scroll_up),
            )
        else:
            ok = run_static_session(
                d,
                contact=str(args.contact),
                account_id=account_id,
                duration_seconds=each_duration,
            )
        if ok:
            ok_count += 1
            print(f"[PASS] 第 {i}/{total} 次深聊完成")
        else:
            print(f"[FAIL] 第 {i}/{total} 次深聊失败")
        if i < total:
            time.sleep(20)

    print(f"[DONE] 深聊任务结束: success={ok_count}/{total}")
    return 0 if ok_count == total else 2


if __name__ == "__main__":
    raise SystemExit(main())
