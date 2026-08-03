"""
快速调试剧本 — 一键跑通全部核心功能。

流程:
    1. 发朋友圈 (1张图 + 文案)
    2. 朋友圈浏览 + 点赞 + 评论
    3. 给联系人发文字
    4. 给联系人发图片
    5. 刷视频号 (3min, 随机点赞)
    6. 阅读公众号 (2min)
    7. 浏览收藏夹 (1min)

用法:
    python main.py fast-debug

联系人配置: 修改本文件顶部 CONTACT_NAME
"""

import asyncio
import time

from scripts.base_script import BaseScript, ActionType, Action, DailyScript
from utils.logger import get_logger

logger = get_logger("fast_test")

# ============================================================
# 自行配置区域
# ============================================================
CONTACT_NAME = "微信团队"           # 接收文字/图片的联系人（必有官方号，便于验证）
MOMENT_TEXT = "fast_debug发送朋友圈"  # 发朋友圈文案
COMMENT_TEXT = "deeeeebug"           # 朋友圈评论内容
# ============================================================


class FastTestScript(BaseScript):
    """快速调试剧本 — 跑通全部核心功能"""

    STAGE_NAME = "fast_test"

    def _build_weekday_script(self) -> DailyScript:
        return DailyScript(stage=self.STAGE_NAME, is_weekend=False, actions=[])

    def _build_weekend_script(self) -> DailyScript:
        return DailyScript(stage=self.STAGE_NAME, is_weekend=False, actions=[])

    async def run_daily(self):
        """顺序执行全部动作，动作间短暂间隔。"""
        total = 7
        success = 0
        overall_start = time.time()

        logger.info(f"[{self.account_id}] === 快速调试 共 {total} 步 ===")
        print(f"\n{'='*50}")
        print(f"  快速调试 — 设备: {self.account_id}")
        print(f"  联系人: {CONTACT_NAME}")
        print(f"  共 {total} 步")
        print(f"{'='*50}\n")

        async def step(i: int, label: str, fn, *args):
            nonlocal success
            print(f"  [{i}/{total}] {label} ...", end=" ", flush=True)
            start = time.time()
            try:
                raw = fn(*args) if args else fn()
                elapsed = time.time() - start
                if isinstance(raw, dict):
                    ok = raw.get("success", True) is not False
                else:
                    ok = bool(raw)
                if ok:
                    success += 1
                    print(f"[OK] ({elapsed:.1f}s)")
                else:
                    print(f"[FAIL] ({elapsed:.1f}s)")
                try:
                    self.db.log_action(account_id=self.account_id,
                                       action_type=f"fast_step{i}", success=ok)
                except Exception:
                    pass
            except Exception as e:
                elapsed = time.time() - start
                print(f"[ERR] ({elapsed:.1f}s): {e}")
                logger.error(f"[{self.account_id}] step{i} error: {e}")
            await asyncio.sleep(0.5)

        # Step 1: 发朋友圈
        await step(1, f"发朋友圈: {MOMENT_TEXT}",
                   self.wc.post_moment, MOMENT_TEXT, 1)

        await asyncio.sleep(2)

        # Step 2: 朋友圈点赞+评论
        await step(2, f"朋友圈点赞+评论: {COMMENT_TEXT}",
                   self.wc.browse_moments_interact,
                   40, COMMENT_TEXT, 0.55)

        await asyncio.sleep(1)

        # Step 3: 发文字
        await step(3, f"发文字 'test' → {CONTACT_NAME}",
                   self.wc.send_message, "test", CONTACT_NAME)

        await asyncio.sleep(1)

        # Step 4: 发图片
        from core.image_sender import ImageSender
        sender = ImageSender(self.wc.d, account_id=self.account_id)

        def _send_image():
            return sender.send(contact=CONTACT_NAME, photo_count=1)

        await step(4, f"发1张图片 → {CONTACT_NAME}", _send_image)

        await asyncio.sleep(1)

        # Step 5: 刷视频号 3min
        from core.channels_browser import ChannelsBrowser

        def _browse_channels():
            browser = ChannelsBrowser(self.wc.d, account_id=self.account_id)
            # 冒烟缩短：条数控制，关闭完播与评论
            return browser.browse(
                scroll_count=3,
                like_rate=0.2,
                finish_watch=False,
                comment_rate=0.0,
            )

        await step(5, "刷视频号 (3条)", _browse_channels)

        await asyncio.sleep(1)

        # Step 6: 阅读公众号
        from core.public_account_browser import PublicAccountBrowser

        def _browse_articles():
            browser = PublicAccountBrowser(self.wc.d, account_id=self.account_id)
            return {"read": browser.browse(duration_seconds=45)}

        await step(6, "阅读公众号 (45s)", _browse_articles)

        await asyncio.sleep(1)

        # Step 7: 浏览收藏夹
        from core.favorites_browser import FavoritesBrowser

        def _browse_favorites():
            browser = FavoritesBrowser(self.wc.d, account_id=self.account_id)
            return {"viewed": browser.browse(duration_seconds=30)}

        await step(7, "浏览收藏夹 (30s)", _browse_favorites)

        total_elapsed = time.time() - overall_start
        summary = (
            f"\n{'='*50}\n"
            f"  结果: {success}/{total} 成功\n"
            f"  耗时: {total_elapsed:.0f}s ({total_elapsed/60:.1f}min)\n"
            f"{'='*50}\n"
        )
        print(summary)
        logger.info(f"[{self.account_id}] 快速调试完成: {success}/{total}")

        return {"success": success, "total": total, "elapsed": total_elapsed}
