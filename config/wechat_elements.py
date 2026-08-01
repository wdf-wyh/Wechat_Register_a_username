"""
微信 UI 元素定位映射表。

定位策略优先级（ElementLocator 自动按此顺序尝试）：
    1. text 匹配 — 最稳定，跨版本兼容性好
    2. description (content-desc) 匹配
    3. resourceId 匹配 — 可能随微信版本变化
    4. className + 层级关系 — 兜底方案

⚠️ 重要：微信的 resourceId 可能因版本不同而变化。
   实际使用前请通过 `python -m weditor` 连接手机，确认当前版本的准确 ID，
   以 text/description 为首选定位策略。

使用方式:
    from config.wechat_elements import WECHAT_ELEMENTS, locate_element
    d = u2.connect()
    locate_element(d, "tab_wechat").click()
"""

import uiautomator2 as u2

from config.device_profiles.moto_x70_air_pro import PROFILE as _MOTO_PROFILE

# ============================================================
# 微信包名 & Activity
# ============================================================
WECHAT_PACKAGE = "com.tencent.mm"
WECHAT_MAIN_ACTIVITY = "com.tencent.mm.ui.LauncherUI"

# ============================================================
# UI 元素定位字典
# ============================================================
WECHAT_ELEMENTS: dict[str, dict] = {
    # ===== 底部导航 Tab =====
    "tab_wechat":    {"text": "微信",     "description": "微信"},
    "tab_contacts":  {"text": "通讯录",   "description": "通讯录"},
    "tab_discover":  {"text": "发现",     "description": "发现"},
    "tab_me":        {"text": "我",       "description": "我"},

    # ===== 发现页 =====
    "moments_entry":       {"text": "朋友圈"},
    "channels_entry":      {"text": "视频号"},
    "scan_entry":          {"text": "扫一扫"},
    "mini_program_entry":  {"text": "小程序"},
    "search_entry":        {"text": "搜一搜"},

    # ===== 聊天列表（微信首页） =====
    "search_btn":        {"description": "搜索"},
    "add_contact_btn":   {"description": "添加朋友"},
    "chat_list_item":    {"className": "android.widget.LinearLayout"},

    # ===== 聊天窗口内部 =====
    "chat_input_box":   {"className": "android.widget.EditText"},
    "chat_send_btn":    {"text": "发送"},
    "chat_emoji_btn":   {"description": "表情"},
    "chat_more_btn":    {"description": "更多功能按钮"},
    "chat_voice_btn":   {"text": "按住 说话"},
    "chat_album_btn":   {"text": "相册"},
    "chat_back_btn":    {"description": "返回"},

    # ===== 朋友圈 =====
    "moments_camera_btn":   (0.83, 0.054),   # 右上角相机 (x=1048, y=150)
    "moments_publish_btn":  (0.88, 0.056),   # 发表按钮
    "moments_dots_btn":     (0.90, 0.49),    # "..." 按钮(动态Y, 基于分割线)
    "moments_menu_like":    (0.92, 0.444),   # 菜单-赞 (x=1160, y=1235)
    "moments_menu_comment": (0.35, 0.446),   # 菜单-评论 (x=445, y=1240)

    # ===== 通讯录页 =====
    "contacts_public_acct":  {"text": "公众号"},
    "contacts_group":        {"text": "群聊"},
    "contacts_new_friend":   {"text": "新的朋友"},
    "contacts_tag":          {"text": "标签"},

    # ===== "我" 页面 =====
    "me_services":     {"text": "服务"},
    "me_favorites":    {"text": "收藏"},
    "me_settings":     {"text": "设置"},
    "me_emojis":       {"text": "表情"},
    "me_moments":      {"text": "朋友圈"},

    # ===== 服务/支付页 =====
    "services_wallet":     {"text": "钱包"},
    "services_receipt":    {"text": "收付款"},

    # ===== 收藏页 =====
    "favorites_list":      {"className": "android.widget.ListView"},

    # ===== 视频号 =====
    "channels_like_btn":   {"description": "点赞"},
    "channels_comment_btn":{"description": "评论"},
    "channels_share_btn":  {"description": "分享"},

    # ===== 通用 =====
    "generic_back":         {"description": "返回"},
    "generic_more":         {"description": "更多"},
    "generic_close":        {"description": "关闭"},
    "generic_confirm":      {"text": "确定"},
    "generic_cancel":       {"text": "取消"},
}


# ============================================================
# 坐标 Fallback 映射表（微信屏蔽 UiAutomation 时使用）
# ============================================================
# 坐标为屏幕百分比 (x_ratio, y_ratio)，例如 (0.5, 0.85) 表示屏幕正中间偏下。
#
# ⚠️ 多机型：本表仅保留「默认 / Moto」基线，供未接设备时的静态引用。
# 运行时请用 config.device_profiles.get_coord(d, name)，按分辨率自动选机型。
# 新机型请加 config/device_profiles/<机型>.py，禁止直接改本表覆盖旧机型。

COORDINATE_FALLBACK: dict[str, tuple[float, float]] = dict(_MOTO_PROFILE.coords)


def locate_element(
    d: u2.Device,
    element_name: str,
    timeout: float = 10.0,
) -> u2.UiObject:
    """
    按优先级策略定位微信界面元素。

    尝试顺序: text → description → resourceId → className

    Args:
        d: uiautomator2 设备连接
        element_name: WECHAT_ELEMENTS 中的键名
        timeout: 元素等待超时（秒）

    Returns:
        uiautomator2 UiObject

    Raises:
        KeyError: element_name 不在字典中
        TimeoutError: 所有策略均未在超时内找到元素
    """
    if element_name not in WECHAT_ELEMENTS:
        raise KeyError(f"未知元素: '{element_name}'，请在 wechat_elements.py 中定义")

    attrs = WECHAT_ELEMENTS[element_name]

    # 策略 1: text
    if "text" in attrs:
        el = d(text=attrs["text"])
        if el.wait(timeout=timeout):
            return el

    # 策略 2: description (content-desc)
    if "description" in attrs:
        el = d(description=attrs["description"])
        if el.wait(timeout=timeout):
            return el

    # 策略 3: resourceId
    if "resourceId" in attrs:
        el = d(resourceId=attrs["resourceId"])
        if el.wait(timeout=timeout):
            return el

    # 策略 4: className
    if "className" in attrs:
        el = d(className=attrs["className"])
        if el.wait(timeout=timeout):
            return el

    raise TimeoutError(
        f"元素 '{element_name}' 在 {timeout}s 内未能定位，"
        f"尝试了: {list(attrs.keys())}"
    )


def locate_by_fallback(
    d: u2.Device,
    element_name: str,
    *fallback_names: str,
    timeout: float = 10.0,
) -> u2.UiObject:
    """
    依次尝试多个元素名称，返回第一个找到的。

    用于处理微信版本差异导致的元素名称变化。

    Args:
        d: uiautomator2 设备连接
        element_name: 首选元素名称
        fallback_names: 备选元素名称列表
        timeout: 每次尝试的超时时间

    Returns:
        找到的 uiautomator2 UiObject
    """
    for name in (element_name,) + fallback_names:
        try:
            return locate_element(d, name, timeout=timeout)
        except (KeyError, TimeoutError):
            continue
    raise TimeoutError(f"所有备选元素均未找到: {(element_name,) + fallback_names}")
