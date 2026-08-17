"""
图像工具模块 — 动作关键帧 / 异常截图存储，不用于视觉识别 / 元素定位。

使用方式:
    from utils.image_utils import save_action_keyframe, save_debug_screenshot
    path = save_action_keyframe(d, account_id, "like_moment", success=True)
"""

import time
from pathlib import Path

import uiautomator2 as u2

from config.settings import settings
from utils.logger import get_logger

logger = get_logger("image_utils")

# 不需要关键帧证据的动作（无屏幕结果可对）
SKIP_KEYFRAME_ACTIONS = frozenset({"sleep", "idle"})

# 动作英文名 → 中文说明（日志「做了什么」）
ACTION_LABELS_CN: dict[str, str] = {
    "open_wechat": "打开微信",
    "scroll_moments": "刷朋友圈",
    "like_moment": "点赞朋友圈",
    "comment_moment": "评论朋友圈",
    "browse_moments_interact": "浏览朋友圈并互动",
    "moments_daily_interact": "朋友圈每日互动",
    "post_moment": "发朋友圈",
    "scroll_channels": "刷视频号",
    "like_channel": "点赞视频号",
    "comment_channel": "评论视频号",
    "read_article": "阅读公众号文章",
    "favorite_article": "收藏文章",
    "follow_public_account": "关注公众号",
    "global_search": "全局搜索",
    "send_message": "发送文字消息",
    "send_image": "发送图片消息",
    "send_voice": "发送语音",
    "send_emoji": "发送表情",
    "deep_chat": "深度聊天",
    "group_chat": "群聊发言",
    "add_friend": "添加好友",
    "browse_mini_program": "浏览小程序",
    "play_mini_game": "玩官方小游戏",
    "make_payment": "打开收付款",
    "open_favorites": "打开收藏夹",
    "browse_favorites": "浏览收藏夹",
    "idle": "空闲等待",
    "sleep": "休眠等待",
}


def action_label_cn(action_type: str) -> str:
    """动作类型转中文；未知类型回退原文。"""
    return ACTION_LABELS_CN.get(action_type, action_type)


def format_evidence_message(
    account_id: str,
    action_type: str,
    success: bool,
    screenshot_path: str | None,
) -> str:
    """中文证据一行：做了什么 + 结果 + 图片路径（便于 Ctrl+点击）。"""
    result = "成功" if success else "失败"
    what = action_label_cn(action_type)
    pic = screenshot_path or "无"
    return (
        f"账号 {account_id} | 做了什么：{what} | 结果：{result} | 图片是：{pic}"
    )


def save_debug_screenshot(
    d: u2.Device,
    account_id: str,
    tag: str = "",
) -> str | None:
    """
    保存调试截图（操作异常时调用）。

    Returns:
        截图绝对路径，失败返回 None
    """
    try:
        account_dir = settings.SCREENSHOTS_DIR / account_id
        account_dir.mkdir(parents=True, exist_ok=True)

        timestamp = time.strftime("%Y%m%d_%H%M%S")
        filename = f"{timestamp}_{tag}.png" if tag else f"{timestamp}.png"
        filepath = (account_dir / filename).resolve()

        d.screenshot(str(filepath))
        logger.debug(f"调试截图已保存: {filepath}")
        return str(filepath)
    except Exception as e:
        logger.warning(f"保存截图失败 ({account_id}/{tag}): {e}")
        return None


def save_action_keyframe(
    d: u2.Device,
    account_id: str,
    action_type: str,
    success: bool = True,
    *,
    jpeg_quality: int = 65,
) -> str | None:
    """
    保存动作结束后的关键帧（成功/失败都拍），JPEG 省磁盘。

    Returns:
        截图绝对路径（便于日志里 Ctrl+点击），失败返回 None
    """
    if action_type in SKIP_KEYFRAME_ACTIONS:
        return None

    try:
        account_dir = settings.SCREENSHOTS_DIR / account_id
        account_dir.mkdir(parents=True, exist_ok=True)

        timestamp = time.strftime("%Y%m%d_%H%M%S")
        status = "ok" if success else "fail"
        filename = f"{timestamp}_{action_type}_{status}.jpg"
        filepath = (account_dir / filename).resolve()

        pil_img = d.screenshot(format="pillow")
        pil_img.convert("RGB").save(
            str(filepath),
            format="JPEG",
            quality=int(jpeg_quality),
            optimize=True,
        )
        logger.debug(f"动作关键帧已保存: {filepath}")
        return str(filepath)
    except Exception as e:
        logger.warning(f"保存关键帧失败 ({account_id}/{action_type}): {e}")
        return None


def append_cron_evidence(
    account_id: str,
    action_type: str,
    success: bool,
    screenshot_path: str | None,
) -> None:
    """
    往 logs/cron.log 追加中文证据行，行尾路径便于 Cursor/VS Code Ctrl+点击打开。
    """
    if action_type in SKIP_KEYFRAME_ACTIONS:
        return

    try:
        cron_log = (settings.LOGS_DIR / "cron.log").resolve()
        cron_log.parent.mkdir(parents=True, exist_ok=True)
        msg = format_evidence_message(
            account_id, action_type, success, screenshot_path
        )
        line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} | [证据] {msg}\n"
        with open(cron_log, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception as e:
        logger.debug(f"写入 cron.log 证据行失败: {e}")


def clean_old_screenshots(days: int = 7):
    """
    清理超过指定天数的旧截图（png / jpg）。

    Args:
        days: 保留最近 N 天的截图
    """
    cutoff = time.time() - days * 86400
    removed = 0
    for pattern in ("*.png", "*.jpg", "*.jpeg"):
        for img in settings.SCREENSHOTS_DIR.rglob(pattern):
            if img.stat().st_mtime < cutoff:
                img.unlink()
                removed += 1
    if removed > 0:
        logger.info(f"已清理 {removed} 张旧截图（保留最近 {days} 天）")
