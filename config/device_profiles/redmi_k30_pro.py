"""
Redmi K30 Pro (1080×2400) — 独立适配，不覆盖 Moto 配置。

坐标来自该机实测；仅本文件维护本机型差异。
"""

from config.device_profiles.base import DeviceMatch, DeviceProfile

PROFILE = DeviceProfile(
    profile_id="redmi_k30_pro",
    display_name="Redmi K30 Pro",
    match=DeviceMatch(
        width=1080,
        height=2400,
        model_keywords=("k30", "redmi", "lmi", "poco f2"),
    ),
    coords={
        # 全面屏手势条会抬高 Tab，Y 比 Moto 略上
        "tab_wechat": (0.125, 0.93),
        "tab_contacts": (0.375, 0.93),
        "tab_discover": (0.625, 0.93),
        "tab_me": (0.875, 0.93),
        # 发现页入口（相对坐标实测）
        "moments_entry": (0.20, 0.127),
        "channels_entry": (0.20, 0.199),
        "scan_entry": (0.20, 0.270),
        "search_entry": (0.20, 0.340),
        "mini_program_entry": (0.20, 0.410),
        # 聊天 / 搜索
        "search_btn": (0.90, 0.054),
        "chat_input_box": (0.50, 0.93),
        "chat_send_btn": (0.90, 0.93),
        "chat_more_btn": (0.94, 0.93),
        "chat_voice_btn": (0.08, 0.93),
        # 我 / 收藏
        "me_services": (0.50, 0.30),
        "me_favorites": (0.50, 0.35),
        "me_settings": (0.50, 0.52),
        "services_wallet": (0.50, 0.22),
        "services_receipt": (0.50, 0.14),
        "generic_back": (0.05, 0.055),
        "generic_more": (0.95, 0.055),
        "generic_close": (0.05, 0.055),
    },
    nav={
        "tab_y": 0.93,
        "tab_y_candidates": (0.93, 0.915, 0.945, 0.955),
        "moments_entry": (0.20, 0.127),
        "channels_entry": (0.20, 0.199),
        "search_icon_candidates": [
            (0.90, 0.054),
            (0.88, 0.054),
            (0.831, 0.054),
            (0.78, 0.054),
        ],
        "favorites_entry": (0.50, 0.35),
    },
    extras={
        "plus_btn": (0.94, 0.93),
        "top_plus_btn": (0.96, 0.054),
        # 微信首页右上角 + 弹出菜单：「添加朋友」实测约 (0.84, 0.20)
        "add_friend_menu_item": (0.78, 0.197),
        "add_friend_menu_candidates": [
            (0.78, 0.197),
            (0.84, 0.197),
            (0.72, 0.197),
            (0.78, 0.21),
            (0.78, 0.18),
        ],
        # 通讯录顶栏「新的朋友」
        "new_friends_entry": (0.40, 0.14),
        # 资料页绿按钮：红米实测约 y=0.43；电话行约 y=0.29
        "add_to_contacts_candidates": [
            (0.50, 0.43),
            (0.50, 0.48),
            (0.50, 0.40),
            (0.50, 0.52),
            (0.50, 0.58),
        ],
        # 新版底部通栏「发送」优先，旧版右上角兜底
        "friend_request_send_candidates": [
            (0.50, 0.90),
            (0.50, 0.86),
            (0.50, 0.93),
            (0.92, 0.055),
        ],
        # 新版「添加备注」行约 y=0.36（标签约 0.45+）
        "friend_request_remark_candidates": [
            (0.50, 0.36),
            (0.50, 0.34),
            (0.50, 0.38),
            (0.50, 0.32),
        ],
        "album_menu": (0.20, 0.72),
        "photo_grid": [
            (0.146, 0.182), (0.440, 0.182), (0.733, 0.182), (0.975, 0.182),
            (0.146, 0.315), (0.440, 0.315), (0.733, 0.315), (0.975, 0.315),
        ],
        "img_send_btn": (0.88, 0.955),
        "msg_search_icon": (0.90, 0.054),
        "msg_input": (0.50, 0.93),
        "msg_send": (0.90, 0.93),
        "moments_camera": (0.90, 0.054),
        "moments_publish": (0.90, 0.055),
        "moments_dots_x_ratios": (0.93, 0.91, 0.89, 0.95, 0.87),
        # 视频号底栏最右侧评论气泡（数字上方图标）
        "channels_comment_icon": (0.93, 0.88),
        "channels_comment_input": (0.42, 0.72),
        "channels_comment_send": (0.90, 0.62),
        # 键盘弹起后「发送」在表情行右侧
        "channels_comment_send_candidates": [
            (0.90, 0.62),
            (0.92, 0.60),
            (0.88, 0.64),
            (0.90, 0.58),
            (0.93, 0.56),
        ],
    },
)
