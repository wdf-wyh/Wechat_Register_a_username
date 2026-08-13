"""
信任积累期脚本（第1-2周 / 冷启动 14 天）— 按注册天数分相位。

人工项（资料/绑卡/红包/真实支付）不自动化。
可自动化项走 cold_start_templates；启用 AI 上帝视角时由 AiGodPlanner 覆盖。

相位:
  Day1     加 1 好友、关注 2 个行业公众号、读文 10 分钟并留言
  Day2-3   加 2 好友、关注 2 个行业公众号、读文 10 分钟并留言
  Day4-7   发 1 条生活类朋友圈（相册原创配图）+ 官方小游戏
  Day8-10  加 3 好友、5 次 1v1 深聊（>5 分钟/次）、朋友圈互动 20 次
  Day11    视频号 10 分钟（完播 + 评论）
  Day12-14 日常活跃：每周 4-6 条朋友圈（生活:工作≈3:2）、小程序 3 次/周
"""

from scripts.base_script import BaseScript, DailyScript
from scripts.cold_start_templates import build_cold_start_actions


class TrustBuildingScript(BaseScript):
    """信任积累期 — 14 天冷启动节奏"""

    STAGE_NAME = "trust_building"

    def _day_index(self) -> int:
        from datetime import date, datetime

        account = self.db.get_account(self.account_id) or {}
        reg = account.get("registration_date") or date.today().isoformat()
        try:
            reg_date = datetime.strptime(str(reg)[:10], "%Y-%m-%d").date()
            return max(1, (date.today() - reg_date).days + 1)
        except Exception:
            return 1

    def _build_weekday_script(self) -> DailyScript:
        return DailyScript(
            stage=self.STAGE_NAME,
            is_weekend=False,
            actions=build_cold_start_actions(self._day_index(), is_weekend=False),
        )

    def _build_weekend_script(self) -> DailyScript:
        return DailyScript(
            stage=self.STAGE_NAME,
            is_weekend=True,
            actions=build_cold_start_actions(self._day_index(), is_weekend=True),
        )
