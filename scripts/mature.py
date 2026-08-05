"""
成熟期维护脚本（3个月后）— 维持自然使用节奏，保持账号活跃。

行为特征:
    - 全面放开社交限制
    - 自然的使用频率（不过度操作）
    - 适合在账号投入 benchmark 测试期间维持热度
"""

from scripts.base_script import BaseScript, ActionType, Action, DailyScript, channels_daily_params


class MatureScript(BaseScript):
    """成熟期 — 自然维持"""

    STAGE_NAME = "mature"

    def _build_weekday_script(self) -> DailyScript:
        return DailyScript(
            stage=self.STAGE_NAME,
            is_weekend=False,
            actions=[
                # 07:30 - 查看消息
                Action(ActionType.OPEN_WECHAT, "07:00", "08:30", (120, 300)),
                # 08:00 - 刷朋友圈 + 点赞
                Action(ActionType.SCROLL_MOMENTS, "07:30", "09:00", (180, 480)),
                Action(ActionType.LIKE_MOMENT, "08:00", "09:00", (30, 120),
                       params={"count": (1, 4)}),
                # 09:00 - 搜索 / 聊天
                Action(ActionType.GLOBAL_SEARCH, "08:30", "10:00", (30, 120)),
                Action(ActionType.SEND_MESSAGE, "09:00", "11:00", (120, 300),
                       params={"contact_count": (1, 3)}),
                Action(ActionType.SEND_EMOJI, "09:30", "11:00", (30, 60)),
                # 12:00 - 视频号约 10 分钟（完播 + 评论）+ 阅读
                Action(ActionType.SCROLL_CHANNELS, "11:30", "13:00", (600, 720),
                       params=channels_daily_params(600)),
                Action(ActionType.READ_ARTICLE, "12:00", "13:30", (180, 480)),
                Action(ActionType.FAVORITE_ARTICLE, "12:30", "13:30", (30, 60)),
                # 14:00 - 聊天 (图片/语音)
                Action(ActionType.SEND_IMAGE, "13:30", "15:00", (60, 180)),
                # 15:30 - 支付
                Action(ActionType.MAKE_PAYMENT, "15:00", "17:00", (30, 120)),
                # 17:00 - 发朋友圈
                Action(ActionType.POST_MOMENT, "16:30", "18:30", (120, 300)),
                # 18:00 - 收藏浏览
                Action(ActionType.BROWSE_FAVORITES, "17:30", "19:00", (60, 180)),
                # 19:00 - 聊天 + 评论
                Action(ActionType.SEND_MESSAGE, "18:30", "21:00", (120, 360)),
                Action(ActionType.COMMENT_MOMENT, "19:00", "21:00", (30, 120)),
                # 20:00 - 朋友圈 + 点赞
                Action(ActionType.SCROLL_MOMENTS, "19:30", "21:00", (180, 480)),
                Action(ActionType.LIKE_MOMENT, "20:00", "21:00", (30, 120),
                       params={"count": (1, 3)}),
                # 22:30 - 睡前
                Action(ActionType.SCROLL_MOMENTS, "21:30", "23:00", (120, 300)),
                # 23:00-07:00 - 不活跃
                Action(ActionType.SLEEP, "23:00", "07:00", (0, 0)),
            ],
        )

    def _build_weekend_script(self) -> DailyScript:
        """成熟期周末：自然悠闲，但不刻意增加操作"""
        return DailyScript(
            stage=self.STAGE_NAME,
            is_weekend=True,
            actions=[
                # 09:00 - 晚起
                Action(ActionType.OPEN_WECHAT, "08:00", "10:00", (120, 300)),
                # 10:00 - 朋友圈
                Action(ActionType.SCROLL_MOMENTS, "09:30", "11:30", (300, 600)),
                Action(ActionType.LIKE_MOMENT, "10:00", "11:30", (30, 120),
                       params={"count": (2, 5)}),
                Action(ActionType.POST_MOMENT, "10:30", "12:30", (180, 300)),
                # 12:00 - 聊天
                Action(ActionType.SEND_MESSAGE, "11:00", "13:00", (120, 360)),
                Action(ActionType.SEND_EMOJI, "11:30", "13:00", (30, 60)),
                Action(ActionType.SEND_IMAGE, "12:00", "13:30", (60, 180)),
                # 14:00 - 视频号约 10 分钟（完播 + 评论）
                Action(ActionType.SCROLL_CHANNELS, "13:00", "16:00", (600, 720),
                       params=channels_daily_params(600)),
                Action(ActionType.PLAY_MINI_GAME, "14:30", "16:00", (120, 240),
                       params={"game": "跳一跳", "duration": 180}),
                # 15:30 - 阅读 + 收藏
                Action(ActionType.READ_ARTICLE, "15:00", "17:00", (180, 480)),
                Action(ActionType.FAVORITE_ARTICLE, "15:30", "17:00", (30, 60)),
                # 17:00 - 支付 + 搜索
                Action(ActionType.MAKE_PAYMENT, "16:00", "18:30", (30, 120)),
                Action(ActionType.GLOBAL_SEARCH, "16:30", "18:30", (30, 120)),
                # 18:00 - 聊天
                Action(ActionType.SEND_MESSAGE, "17:00", "20:00", (180, 480)),
                # 19:00 - 朋友圈 + 评论
                Action(ActionType.SCROLL_MOMENTS, "18:30", "21:00", (180, 480)),
                Action(ActionType.COMMENT_MOMENT, "19:00", "21:00", (30, 120)),
                # 22:30 - 睡前
                Action(ActionType.SCROLL_MOMENTS, "21:30", "23:30", (120, 300)),
                # 00:00-08:00 - 不活跃
                Action(ActionType.SLEEP, "00:00", "08:00", (0, 0)),
            ],
        )
