"""
信任积累期脚本（第1-2周 / 冷启动 14 天）— 按注册天数分相位。

人工项（资料/绑卡/红包/真实支付）不自动化。
可自动化项走 cold_start_templates；启用 AI 上帝视角时由 AiGodPlanner 覆盖。

相位:
  Day1-3   关注公众号、读文、搜索、刷视频号、小程序、打开支付页
  Day4-7   开始发圈、群发言、轻点赞、小程序
  Day8-10  限量加好友、深聊、朋友圈互动
  Day11-14 视频号加长+评论、小程序、继续互动
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
