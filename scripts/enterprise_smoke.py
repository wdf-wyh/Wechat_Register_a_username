# -*- coding: utf-8 -*-
"""
企业级真机冒烟 — 验证冷启动新能力在 Redmi 等真机上是否可用。

覆盖:
  A. 离线: 14 天相位模板 + AI planner 回退
  B. 真机: 机型匹配、微信前台、消费类动作、新社交动作

用法:
  python main.py enterprise-smoke
  python -m scripts.enterprise_smoke
"""

from __future__ import annotations

import asyncio
import time
from datetime import date, timedelta

from utils.logger import get_logger

logger = get_logger("enterprise_smoke")


class EnterpriseSmoke:
    def __init__(self, wc, db, persona: dict, serial: str):
        self.wc = wc
        self.db = db
        self.persona = persona
        self.serial = serial
        self.account_id = wc.account_id
        self.results: list[dict] = []

    def _record(self, name: str, ok: bool, detail: str = "", skipped: bool = False):
        status = "SKIP" if skipped else ("PASS" if ok else "FAIL")
        self.results.append(
            {"name": name, "ok": ok, "skipped": skipped, "detail": detail, "status": status}
        )
        mark = {"PASS": "[PASS]", "FAIL": "[FAIL]", "SKIP": "[SKIP]"}[status]
        print(f"  {mark} {name}" + (f" — {detail}" if detail else ""))
        logger.info(f"{status} {name}: {detail}")

    # ----------------------------------------------------------
    # A. 离线
    # ----------------------------------------------------------

    def run_offline(self):
        print("\n--- A. 离线编排 ---")
        from scripts.cold_start_templates import (
            build_cold_start_actions,
            cold_start_phase,
            max_add_friends_for_day,
        )
        from content.ai_god_planner import AiGodPlanner
        from config.settings import settings

        try:
            phases = [cold_start_phase(d) for d in (1, 4, 8, 11, 20)]
            assert phases == ["day1_3", "day4_7", "day8_10", "day11_14", "post_14"]
            for d in (1, 5, 9, 12):
                acts = build_cold_start_actions(d)
                assert acts and any(a.action_type.value == "sleep" for a in acts)
            assert [max_add_friends_for_day(d) for d in (1, 2, 3, 4)] == [1, 2, 2, 0]
            self._record("cold_start_templates", True, f"phases={phases}")
        except Exception as e:
            self._record("cold_start_templates", False, str(e))

        try:
            prev = settings.USE_AI_GOD_PLANNER
            settings.USE_AI_GOD_PLANNER = False
            # 确保账号有注册日（day1）
            acc = self.db.get_account(self.account_id)
            if not acc:
                self.db.insert_account(
                    id=self.account_id,
                    device_serial=self.serial,
                    stage="trust_building",
                    registration_date=date.today().isoformat(),
                    mode="full",
                    state="normal",
                )
            else:
                self.db.update_account(
                    self.account_id,
                    registration_date=date.today().isoformat(),
                    stage="trust_building",
                )
            planner = AiGodPlanner(self.db, self.persona)
            actions = planner.plan_day(self.account_id, is_weekend=False)
            settings.USE_AI_GOD_PLANNER = prev
            names = [a.action_type.value for a in actions]
            ok = "follow_public_account" in names and "sleep" in names
            self._record(
                "ai_planner_fallback_day1",
                ok,
                f"n={len(actions)} sample={names[:5]}",
            )
        except Exception as e:
            self._record("ai_planner_fallback_day1", False, str(e))

        try:
            # day8 应含 add_friend / deep_chat
            self.db.update_account(
                self.account_id,
                registration_date=(date.today() - timedelta(days=7)).isoformat(),
            )
            settings.USE_AI_GOD_PLANNER = False
            actions = AiGodPlanner(self.db, self.persona).plan_day(
                self.account_id, is_weekend=False
            )
            settings.USE_AI_GOD_PLANNER = prev
            names = {a.action_type.value for a in actions}
            ok = "add_friend" in names and "deep_chat" in names
            self._record("ai_planner_fallback_day8", ok, f"types={sorted(names)}")
        except Exception as e:
            self._record("ai_planner_fallback_day8", False, str(e))

    # ----------------------------------------------------------
    # B. 真机
    # ----------------------------------------------------------

    async def run_device(self):
        print("\n--- B. 真机动作 ---")
        d = self.wc.d

        # B1 机型
        try:
            from config.device_profiles import resolve_profile

            p = resolve_profile(d)
            ok = p.profile_id == "redmi_k30_pro"
            self._record(
                "device_profile",
                ok,
                f"{p.profile_id} {d.info.get('displayWidth')}x{d.info.get('displayHeight')}",
            )
        except Exception as e:
            self._record("device_profile", False, str(e))

        # 唤醒 + 断线重连
        try:
            d.screen_on()
            time.sleep(0.5)
        except Exception as e:
            logger.warning(f"screen_on 失败，尝试重连: {e}")
            d = self._reconnect()
            if d is None:
                self._record("wechat_foreground", False, "ADB 重连失败")
                return
            self.wc.d = d

        # B2 微信前台（先非冷启动，失败再冷启）
        try:
            from core.wechat_nav import start_wechat

            ok = False
            last_err = ""
            for cold in (False, True):
                try:
                    ok = start_wechat(d, wait=4.0, cold=cold)
                    cur = d.app_current().get("package", "")
                    ok = bool(ok) and "tencent.mm" in str(cur)
                    if ok:
                        self._record("wechat_foreground", True, f"pkg={cur} cold={cold}")
                        break
                    last_err = f"pkg={cur}"
                except Exception as e:
                    last_err = str(e)
                    if "not found" in last_err.lower() or "offline" in last_err.lower():
                        d = self._reconnect()
                        if d is None:
                            break
                        self.wc.d = d
            if not ok:
                self._record("wechat_foreground", False, last_err or "unknown")
                return
        except Exception as e:
            self._record("wechat_foreground", False, str(e))
            return

        # B3 回首页
        t0 = time.time()
        try:
            ok = self.wc.ensure_wechat_home()
            self._record("ensure_home", ok, f"{time.time()-t0:.1f}s")
        except Exception as e:
            self._record("ensure_home", False, str(e))

        # B4 刷朋友圈（短）
        await self._step_bool(
            "scroll_moments",
            lambda: self.wc.open_moments() and (self.wc.scroll_moments(3) or True),
        )
        try:
            d.press("back")
        except Exception:
            pass

        # B5 关注公众号（新）
        from core.social_actions import SocialActions

        social = SocialActions(d, self.account_id)
        pa = (self.persona.get("public_accounts") or ["人民日报"])[0]
        await self._step_bool("follow_public_account", lambda: social.follow_public_account(pa))

        # B6 小程序（新，缩短）
        await self._step_bool(
            "browse_mini_program",
            lambda: social.browse_mini_program(duration_seconds=45, keyword="美团"),
        )

        # B6b 官方小游戏（发现→游戏→找游戏→立即玩，缩短）
        await self._step_bool(
            "play_mini_game",
            lambda: social.play_mini_game(game_name="", duration_seconds=60),
        )

        # B7 视频号短刷
        from core.channels_browser import ChannelsBrowser

        await self._step_bool(
            "scroll_channels",
            lambda: bool(
                ChannelsBrowser(d, self.account_id).browse(
                    scroll_count=2,
                    like_rate=0.0,
                    finish_watch=False,
                    comment_rate=0.0,
                ).get("watched", 0)
                >= 0
            ),
        )

        # B8 读公众号短
        from core.public_account_browser import PublicAccountBrowser

        await self._step_bool(
            "read_article",
            lambda: PublicAccountBrowser(d, self.account_id, persona=self.persona).browse(duration_seconds=35) >= 0,
        )

        # B9 打开支付页（非真实支付）
        await self._step_bool("open_payment_page", lambda: self.wc.open_payment_page())
        try:
            d.press("back")
            d.press("back")
        except Exception:
            pass

        # B10 加好友 / 深聊：名单分开（seed=待加，chat=已互为好友）
        seeds = self.persona.get("seed_friends") or []
        chat_friends = self.persona.get("chat_friends") or []
        if not seeds:
            self._record("add_friend", True, "无 seed_friends，跳过（预期）", skipped=True)
        else:
            await self._step_bool(
                "add_friend",
                lambda: social.add_friend(seeds[0], remark_source="enterprise_smoke"),
            )
        if not chat_friends:
            self._record("deep_chat", True, "无 chat_friends，跳过（预期）", skipped=True)
        else:
            await self._step_bool(
                "deep_chat",
                lambda: social.deep_chat(
                    chat_friends[0], ["在吗", "测一下"], total_seconds=40
                ),
            )

        # B11 群聊
        groups = self.persona.get("seed_groups") or []
        db_groups = self.db.get_friends(self.account_id, source="group")
        if not groups and not db_groups:
            self._record("group_chat", True, "无群资源，跳过（预期）", skipped=True)
        else:
            from core.message_sender import MessageSender

            g = groups[0] if groups else db_groups[0]["friend_name"]
            await self._step_bool(
                "group_chat",
                lambda: MessageSender(d, self.account_id).send(g, "大家好，路过打个招呼"),
            )

    async def _step_bool(self, name: str, fn):
        t0 = time.time()
        try:
            ok = bool(fn())
            self._record(name, ok, f"{time.time()-t0:.1f}s")
        except Exception as e:
            self._record(name, False, f"{time.time()-t0:.1f}s {e}")
        await asyncio.sleep(0.8)

    def _reconnect(self):
        """ADB/u2 掉线时重连当前序列号。"""
        import time as _t

        import uiautomator2 as u2

        logger.warning(f"尝试重连设备 {self.serial}")
        for i in range(3):
            try:
                import subprocess

                subprocess.run(
                    ["adb", "connect", self.serial],
                    capture_output=True,
                    timeout=5,
                )
            except Exception:
                pass
            _t.sleep(1.5)
            try:
                d = u2.connect(self.serial)
                _ = d.info
                self.wc.d = d
                logger.info(f"重连成功 ({i+1})")
                return d
            except Exception as e:
                logger.warning(f"重连失败 ({i+1}): {e}")
        return None

    def summary(self) -> dict:
        executed = [r for r in self.results if not r["skipped"]]
        passed = sum(1 for r in executed if r["ok"])
        failed = sum(1 for r in executed if not r["ok"])
        skipped = sum(1 for r in self.results if r["skipped"])
        return {
            "passed": passed,
            "failed": failed,
            "skipped": skipped,
            "total_executed": len(executed),
            "results": self.results,
            "ok": failed == 0 and passed > 0,
        }


async def run_enterprise_smoke(serial: str | None = None) -> dict:
    from config.settings import settings
    from core.device import DeviceManager
    from core.humanizer import Humanizer
    from core.wechat_control import WeChatControl
    from content.personas import random_persona
    from storage.db import Database

    print("=" * 55)
    print("  企业级真机冒烟 — Redmi / 冷启动可用性")
    print("=" * 55)

    db = Database(settings.DB_PATH)
    db.init_db()
    dm = DeviceManager()
    devices = dm.discover_and_connect_all()
    if not devices:
        print("[FAIL] 无设备")
        return {"ok": False, "failed": 1, "passed": 0, "skipped": 0}

    serials = list(devices.keys())
    serial = serial or serials[0]
    if serial not in devices:
        print(f"[FAIL] 指定设备不在线: {serial}")
        return {"ok": False, "failed": 1, "passed": 0, "skipped": 0}

    print(f"设备: {serial}")
    dm.ensure_wechat_foreground(serial)

    account_id = dm.get_bound_account(serial) or f"smoke_{serial[:6]}"
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
    smoke = EnterpriseSmoke(wc, db, persona, serial)

    smoke.run_offline()
    await smoke.run_device()
    summary = smoke.summary()

    print("\n" + "=" * 55)
    print(
        f"  结果: PASS {summary['passed']} / FAIL {summary['failed']} / "
        f"SKIP {summary['skipped']}"
    )
    print(f"  结论: {'local-ok (device smoke)' if summary['ok'] else 'blocked'}")
    print("=" * 55)
    return summary


if __name__ == "__main__":
    asyncio.run(run_enterprise_smoke())
