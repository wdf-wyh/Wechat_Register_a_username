"""
正常使用期脚本（第2-3个月）— 模拟正常社交用户的频率。

行为特征:
    - 每天与 3-5 个好友聊天
    - 每周发朋友圈 4-7 条
    - 活跃点赞和评论
    - 收藏夹、搜索、支付等日常使用
"""

from scripts.base_script import BaseScript, ActionType, Action, DailyScript, channels_daily_params


class NormalUseScript(BaseScript):
    """正常使用期"""

    STAGE_NAME = "normal_use"

    def _build_weekday_script(self) -> DailyScript:
        return DailyScript(
            stage=self.STAGE_NAME,
            is_weekend=False,
            actions=[
                # 07:30 - 打开微信查看消息
                Action(ActionType.OPEN_WECHAT, "07:00", "08:30", (180, 480)),
                # 08:00 - 刷朋友圈 + 点赞 2-3 条
                Action(ActionType.SCROLL_MOMENTS, "07:30", "09:00", (300, 600)),
                Action(ActionType.LIKE_MOMENT, "08:00", "09:00", (60, 180),
                       params={"count": (2, 3)}),
                # 09:00 - 搜索 (模拟日常工作场景)
                Action(ActionType.GLOBAL_SEARCH, "08:30", "10:00", (60, 180)),
                # 10:00 - 聊天 (文字 + 表情)
                Action(ActionType.SEND_MESSAGE, "09:30", "11:00", (120, 360),
                       params={"contact_count": (1, 3)}),
                Action(ActionType.SEND_EMOJI, "10:00", "11:00", (30, 60)),
                # 12:00 - 视频号约 10 分钟（完播 + 评论）+ 阅读文章
                Action(ActionType.SCROLL_CHANNELS, "11:30", "13:00", (600, 720),
                       params=channels_daily_params(600)),
                Action(ActionType.READ_ARTICLE, "12:00", "13:30", (300, 600)),
                Action(ActionType.FAVORITE_ARTICLE, "12:30", "13:30", (30, 120)),
                # 14:00 - 聊天 (图片 / 语音)
                Action(ActionType.SEND_IMAGE, "13:30", "15:00", (60, 180)),
                # 15:30 - 收藏夹
                Action(ActionType.BROWSE_FAVORITES, "15:00", "16:30", (120, 300)),
                # 17:00 - 支付页面
                Action(ActionType.MAKE_PAYMENT, "16:30", "18:00", (60, 300)),
                # 18:00 - 发朋友圈
                Action(ActionType.POST_MOMENT, "17:30", "19:00", (180, 300)),
                # 19:00 - 聊天（下班闲聊）
                Action(ActionType.SEND_MESSAGE, "18:30", "20:30", (180, 480),
                       params={"contact_count": (1, 2)}),
                # 20:00 - 刷朋友圈 + 评论 2-3 条
                Action(ActionType.SCROLL_MOMENTS, "19:30", "21:00", (300, 600)),
                Action(ActionType.COMMENT_MOMENT, "20:00", "21:00", (60, 180)),
                # 22:30 - 睡前朋友圈
                Action(ActionType.SCROLL_MOMENTS, "21:30", "23:00", (180, 480)),
                # 23:00-07:00 - 不活跃
                Action(ActionType.SLEEP, "23:00", "07:00", (0, 0)),
            ],
        )

    def _build_weekend_script(self) -> DailyScript:
        return DailyScript(
            stage=self.STAGE_NAME,
            is_weekend=True,
            actions=[
                # 09:00 - 晚起看消息
                Action(ActionType.OPEN_WECHAT, "08:00", "10:00", (300, 600)),
                # 10:00 - 刷朋友圈 + 点赞 + 发朋友圈
                Action(ActionType.SCROLL_MOMENTS, "09:30", "11:30", (300, 900)),
                Action(ActionType.LIKE_MOMENT, "10:00", "11:00", (60, 180),
                       params={"count": (2, 4)}),
                Action(ActionType.POST_MOMENT, "10:30", "12:00", (180, 300)),
                # 12:00 - 聊天（多轮）
                Action(ActionType.SEND_MESSAGE, "11:00", "13:00", (180, 480),
                       params={"contact_count": (2, 4)}),
                Action(ActionType.SEND_EMOJI, "11:30", "13:00", (30, 90)),
                Action(ActionType.SEND_IMAGE, "12:00", "13:30", (60, 180)),
                # 14:00 - 视频号约 10 分钟（完播 + 评论）
                Action(ActionType.SCROLL_CHANNELS, "13:00", "16:00", (600, 720),
                       params=channels_daily_params(600)),
                # 15:30 - 阅读文章 + 收藏
                Action(ActionType.READ_ARTICLE, "15:00", "17:00", (300, 600)),
                Action(ActionType.FAVORITE_ARTICLE, "15:30", "17:00", (30, 120)),
                Action(ActionType.BROWSE_FAVORITES, "16:00", "18:00", (120, 300)),
                # 17:00 - 支付 + 搜索
                Action(ActionType.MAKE_PAYMENT, "16:00", "18:00", (60, 300)),
                Action(ActionType.GLOBAL_SEARCH, "17:00", "19:00", (60, 180)),
                # 19:00 - 朋友圈 + 评论
                Action(ActionType.SCROLL_MOMENTS, "18:30", "20:30", (300, 600)),
                Action(ActionType.COMMENT_MOMENT, "19:00", "20:00", (60, 180)),
                # 20:00 - 聊天
                Action(ActionType.SEND_MESSAGE, "19:30", "21:30", (180, 480),
                       params={"contact_count": (1, 3)}),
                # 22:30 - 睡前朋友圈
                Action(ActionType.SCROLL_MOMENTS, "21:30", "23:30", (180, 480)),
                # 00:00-08:00 - 不活跃
                Action(ActionType.SLEEP, "00:00", "08:00", (0, 0)),
            ],
        )
