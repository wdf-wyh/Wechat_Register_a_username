"""
Moto X70 Air Pro (1264×2780) — 项目默认校准机型。

坐标来自原 COORDINATE_FALLBACK / 各模块实测，新增机型时勿改本文件。
"""

from config.device_profiles.base import DeviceMatch, DeviceProfile

PROFILE = DeviceProfile(
    profile_id="moto_x70_air_pro",
    display_name="Moto X70 Air Pro",
    match=DeviceMatch(
        width=1264,
        height=2780,
        model_keywords=("moto", "x70", "air"),
    ),
    coords={
        # 底部 Tab（全面屏，Y 偏下）
        "tab_wechat": (0.125, 0.955),
        "tab_contacts": (0.375, 0.955),
        "tab_discover": (0.625, 0.955),
        "tab_me": (0.875, 0.955),
        # 发现页
        "moments_entry": (0.32, 0.131),
        "channels_entry": (0.32, 0.207),
        "scan_entry": (0.32, 0.277),
        "search_entry": (0.32, 0.347),
        # 新版发现页「游戏/小程序」在列表底部（OCR 优先，坐标仅兜底）
        "games_entry": (0.32, 0.78),
        "mini_program_entry": (0.32, 0.85),
        # 聊天
        "search_btn": (0.736, 0.058),
        "chat_input_box": (0.50, 0.965),
        "chat_send_btn": (0.90, 0.965),
        "chat_more_btn": (0.92, 0.965),
        "chat_voice_btn": (0.08, 0.965),
        # 通讯录 / 我
        "contacts_public_acct": (0.50, 0.180),
        "contacts_group": (0.50, 0.230),
        "me_services": (0.50, 0.310),
        "me_favorites": (0.50, 0.352),
        "me_settings": (0.50, 0.525),
        "services_wallet": (0.50, 0.216),
        "services_receipt": (0.50, 0.140),
        "generic_back": (0.05, 0.06),
        "generic_more": (0.95, 0.06),
        "generic_close": (0.05, 0.06),
    },
    nav={
        "tab_y": 0.955,
        "tab_y_candidates": (0.955, 0.945, 0.93, 0.915),
        "moments_entry": (0.32, 0.131),
        "channels_entry": (0.32, 0.207),
        "search_icon_candidates": [
            (0.831, 0.054),
            (0.820, 0.054),
            (0.845, 0.054),
            (0.736, 0.058),
        ],
        "favorites_entry": (0.50, 0.352),
    },
    extras={
        # 发图：+ 按钮、相册菜单、选图网格（百分比）
        "plus_btn": (0.941, 0.942),
        # 无视频号时按钮偏上；有视频号时由 social_actions 改走偏低 OCR/坐标，勿把中部缩略图当按钮
        "add_to_contacts_candidates": [
            (0.50, 0.45),
            (0.50, 0.50),
            (0.50, 0.42),
            (0.50, 0.55),
            (0.50, 0.66),
            (0.50, 0.70),
            (0.50, 0.74),
        ],
        "friend_request_send_candidates": [
            (0.50, 0.90),
            (0.50, 0.86),
            (0.50, 0.93),
            (0.90, 0.055),
        ],
        # 新版「添加备注」行；避开标签
        "friend_request_remark_candidates": [
            (0.50, 0.36),
            (0.50, 0.34),
            (0.50, 0.38),
            (0.50, 0.32),
        ],
        "album_menu": (0.25, 0.55),
        "photo_grid": [
            (0.125, 0.157), (0.376, 0.157), (0.627, 0.157), (0.876, 0.157),
            (0.125, 0.272), (0.376, 0.272), (0.627, 0.272), (0.876, 0.272),
        ],
        "img_send_btn": (0.88, 0.955),
        "msg_search_icon": (0.831, 0.054),
        "msg_input": (0.50, 0.965),
        "msg_send": (0.90, 0.965),
        "moments_camera": (0.83, 0.054),
        "moments_publish": (0.88, 0.056),
        "moments_album_done": (0.88, 0.93),
        "moments_dots_x_ratios": (0.92, 0.90, 0.88, 0.94, 0.86),
        "moments_comment_send": (0.90, 0.965),
        "moments_comment_send_candidates": [
            (0.90, 0.965),
            (0.92, 0.96),
            (0.88, 0.97),
            (0.90, 0.95),
        ],
        # 视频号右侧栏评论 / 半屏输入（新增键，不覆盖旧坐标）
        "channels_comment_icon": (0.93, 0.68),
        "channels_comment_input": (0.42, 0.95),
        "channels_comment_send": (0.90, 0.60),
        "channels_comment_send_candidates": [
            (0.90, 0.60),
            (0.92, 0.56),
            (0.88, 0.66),
            (0.90, 0.70),
        ],
        # 公众号文章留言区「发送」
        "pa_comment_send": (0.92, 0.90),
        "pa_comment_send_candidates": [
            (0.92, 0.90),
            (0.90, 0.88),
            (0.94, 0.91),
        ],
    },
)
