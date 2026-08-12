# -*- coding: utf-8 -*-
"""
社交扩展动作 — 关注公众号 / 加好友 / 群聊 / 深聊 / 小程序 / 官方小游戏。

定位策略与现有模块一致：OCR + 百分比坐标 fallback。
人工专属动作（绑卡、发红包、真实支付、改资料）不在此实现。
"""

from __future__ import annotations

import random
import time
from typing import Optional

import cv2
import numpy as np

from core.wechat_nav import click_ratio, goto_tab, ocr_find_and_click, start_wechat
from config.device_profiles import get_coord
from utils.logger import get_logger

logger = get_logger("social_actions")

# 官方小游戏默认池（发现→游戏→小游戏；也可经小程序搜索打开）
DEFAULT_OFFICIAL_MINI_GAMES = (
    "跳一跳",
    "欢乐斗地主",
    "天天象棋",
    "羊了个羊",
)


class SocialActions:
    """可自动化的社交基建动作。"""

    def __init__(self, d, account_id: str = ""):
        self.d = d
        self.account_id = account_id
        self.w, self.h = d.info["displayWidth"], d.info["displayHeight"]
        self._ocr = None
        self._clahe = None
        # 短时 OCR 缓存：同一页多次判定只扫一次屏
        self._ocr_blob = ""
        self._ocr_blob_ts = 0.0
        self._ocr_blob_ttl = 1.5

    # ================================================================
    # 关注公众号
    # ================================================================

    def follow_public_account(self, name: str) -> bool:
        """
        关注公众号标准流程（可重复多次）：

        1. 保证在微信首页（会话 Tab）
        2. 点右上角放大镜进入搜索页
        3. 在顶部搜索框输入公众号名
        4. 点键盘右下角绿色「搜索」
        5. 在结果里点对应公众号，进入公众号主页
        6. 点绿色「关注」；变为灰色「已关注」即成功
        """
        if not name:
            return False
        name = str(name).strip()
        logger.info(f"[{self.account_id}] 关注公众号: {name}")
        try:
            from core.search_helper import SearchHelper
            from core.wechat_nav import lock_portrait
            from utils.image_utils import save_debug_screenshot

            lock_portrait(self.d)
            try:
                self.w, self.h = self.d.window_size()
            except Exception:
                pass

            # 1) 微信首页
            self._ensure_wechat_home(close_capsule=True)

            # 2~4) 放大镜 → 输入 → 键盘绿钮搜索
            helper = SearchHelper(self.d, account_id=self.account_id)
            if not helper.search(name):
                save_debug_screenshot(self.d, self.account_id, "follow_pa_search_fail")
                return False
            time.sleep(1.2)

            # 5) 点对应公众号结果
            if not self._click_public_account_search_result(name):
                save_debug_screenshot(self.d, self.account_id, "follow_pa_no_result")
                logger.warning(f"[{self.account_id}] 未点到公众号结果: {name}")
                self._ensure_wechat_home(close_capsule=False)
                return False
            time.sleep(1.5)

            # 6) 绿钮关注 → 已关注
            ok = self._follow_on_public_account_home(name)
            if ok:
                logger.info(f"[{self.account_id}] 关注成功(已关注): {name}")
            else:
                save_debug_screenshot(self.d, self.account_id, "follow_pa_btn_fail")
                logger.warning(f"[{self.account_id}] 关注未确认成功: {name}")

            # 短暂浏览后回首页，便于连续关注下一个
            if ok:
                for _ in range(random.randint(1, 3)):
                    self.d.swipe(
                        int(self.w * 0.5),
                        int(self.h * 0.68),
                        int(self.w * 0.5),
                        int(self.h * 0.38),
                        duration=0.35,
                    )
                    time.sleep(random.uniform(1.0, 2.2))

            self._ensure_wechat_home(close_capsule=False)
            return ok
        except Exception as e:
            logger.error(f"[{self.account_id}] 关注公众号失败: {e}")
            try:
                self._ensure_wechat_home(close_capsule=False)
            except Exception:
                pass
            return False

    def _click_public_account_search_result(self, name: str) -> bool:
        """搜索结果页：优先切到「公众号」分区，再点同名条目。"""
        # 顶部筛选里点「公众号」（避免点到文章/聊天记录）
        self._ocr_click_any(["公众号"], y_min=0.06, y_max=0.32)
        time.sleep(0.7)
        self._invalidate_ocr_cache()

        # 精确点名称
        if self._ocr_click_public_account_name(name):
            time.sleep(1.2)
            if self._looks_like_public_account_home(name):
                return True

        # 名称 + 「公众号」行附近再试
        if self._ocr_click_any([name], y_min=0.12, y_max=0.88):
            time.sleep(1.2)
            if self._looks_like_public_account_home(name):
                return True

        # 兜底：点靠上的第一条结果区（搜索后首条常为公众号）
        for ry in (0.22, 0.28, 0.34):
            click_ratio(self.d, 0.45, ry)
            time.sleep(1.2)
            if self._looks_like_public_account_home(name):
                return True
            self.d.press("back")
            time.sleep(0.5)
        return self._looks_like_public_account_home(name)

    def _ocr_click_public_account_name(self, name: str) -> bool:
        """OCR 点与目标名最匹配的结果行（优先精确相等）。"""
        reader = self._ensure_ocr()
        if reader is None:
            return False
        try:
            img = np.array(self.d.screenshot(format="pillow"))
            gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
            enhanced = self._clahe.apply(gray) if self._clahe is not None else gray
            results = reader.readtext(cv2.cvtColor(enhanced, cv2.COLOR_GRAY2BGR))
            y0, y1 = int(self.h * 0.12), int(self.h * 0.88)
            best = None  # (priority, y, cx, cy)
            name_n = name.replace(" ", "")
            for bbox, text, conf in results:
                if conf < 0.30:
                    continue
                t = str(text or "").strip().replace(" ", "")
                if not t:
                    continue
                cy = int((bbox[0][1] + bbox[2][1]) / 2)
                cx = int((bbox[0][0] + bbox[2][0]) / 2)
                if cy < y0 or cy > y1:
                    continue
                if t == name_n:
                    pri = 0
                elif name_n in t or t in name_n:
                    pri = 1
                else:
                    continue
                cand = (pri, cy, cx, cy)
                if best is None or cand < best:
                    best = cand
            if best is None:
                return False
            self.d.click(best[2], best[3])
            self._invalidate_ocr_cache()
            return True
        except Exception as e:
            logger.debug(f"[{self.account_id}] 点公众号名失败: {e}")
            return False

    def _looks_like_public_account_home(self, name: str = "") -> bool:
        """是否已进入公众号主页（可关注/已关注）。"""
        blob = self._ocr_screen_blob(force=True)
        if any(k in blob for k in ("搜索指定内容", "搜索发现")):
            return False
        markers = ("已关注", "关注公众号", "历史消息", "发消息", "视频号")
        hit = sum(1 for k in markers if k in blob)
        if "关注" in blob and "已关注" not in blob:
            hit += 1
        if name and name.replace(" ", "") in blob.replace(" ", ""):
            hit += 1
        return hit >= 1 and (
            "已关注" in blob
            or ("关注" in blob and "搜索指定内容" not in blob)
            or "历史消息" in blob
            or "发消息" in blob
        )

    def _is_already_followed(self) -> bool:
        blob = self._ocr_screen_blob(force=True)
        return "已关注" in blob

    def _follow_on_public_account_home(self, name: str) -> bool:
        """公众号主页：点绿色关注，确认变为「已关注」。"""
        self._scroll_public_account_home_to_top()

        if self._is_already_followed():
            logger.info(f"[{self.account_id}] 已是关注状态: {name}")
            return True

        # 1) OCR 精确点顶栏「关注」（避免颜色误点下方文章封面/链接）
        if self._ocr_click_exact_follow():
            time.sleep(1.2)
            if self._recover_from_follow_misclick():
                if self._is_already_followed():
                    return True

        # 2) 颜色找顶栏绿色关注钮（仅 y<0.38）
        if self._click_green_follow_button():
            time.sleep(1.2)
            if self._recover_from_follow_misclick():
                if self._is_already_followed():
                    return True

        # 3) 机型坐标兜底（主页顶栏/profile 区）
        for rx, ry in ((0.50, 0.30), (0.50, 0.34), (0.50, 0.38), (0.72, 0.18)):
            if self._is_already_followed():
                return True
            click_ratio(self.d, rx, ry)
            time.sleep(1.0)
            if self._recover_from_follow_misclick():
                if self._is_already_followed():
                    return True

        return self._is_already_followed()

    def _scroll_public_account_home_to_top(self) -> None:
        """公众号主页滚到顶部，避免关注钮被顶栏/文章列表遮挡。"""
        try:
            for _ in range(2):
                self.d.swipe(
                    int(self.w * 0.5),
                    int(self.h * 0.28),
                    int(self.w * 0.5),
                    int(self.h * 0.72),
                    duration=0.35,
                )
                time.sleep(0.35)
        except Exception:
            pass
        self._invalidate_ocr_cache()

    def _recover_from_follow_misclick(self) -> bool:
        """
        关注点击后校验：若误入文章正文则返回主页再判。
        Returns: 当前是否可继续判定关注结果（True=在主页或已成功）
        """
        if self._is_already_followed():
            return True
        blob = self._ocr_screen_blob(force=True)
        # 误入文章：有留言/分享底栏，且顶栏不是主页态
        in_article = any(
            k in blob for k in ("说点什么", "写留言", "写评论", "阅读原文", "收藏/评论")
        ) and not any(k in blob for k in ("历史消息", "发消息"))
        if in_article:
            logger.warning(f"[{self.account_id}] 关注误点进文章，返回主页重试")
            self.d.press("back")
            time.sleep(0.9)
            self._invalidate_ocr_cache()
            self._scroll_public_account_home_to_top()
        return True

    def _find_green_follow_button(
        self, y_min: float = 0.08, y_max: float = 0.38
    ):
        """公众号主页顶栏/profile 区绿色「关注」按钮（勿扫下方文章区）。"""
        try:
            shot = self.d.screenshot(format="opencv")
            if shot is None:
                return None
            h, w = shot.shape[:2]
            hsv = cv2.cvtColor(shot, cv2.COLOR_BGR2HSV)
            mask = cv2.inRange(
                hsv, np.array([35, 60, 60]), np.array([95, 255, 255])
            )
            y0, y1 = int(h * y_min), int(h * y_max)
            mask[:y0, :] = 0
            mask[y1:, :] = 0
            mask[:, : int(w * 0.05)] = 0
            mask[:, int(w * 0.95) :] = 0
            contours, _ = cv2.findContours(
                mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
            )
            best = None  # (cy, cx, cy) — 取最靠上的绿钮（关注在 profile 区）
            min_area = w * h * 0.0015
            max_area = w * h * 0.06
            for c in contours:
                x, y, bw, bh = cv2.boundingRect(c)
                area = bw * bh
                if area < min_area or area > max_area:
                    continue
                # 关注钮：顶栏横向圆角条，较宽
                if bw < w * 0.18 or bh < h * 0.018 or bh > h * 0.10:
                    continue
                cx, cy = x + bw // 2, y + bh // 2
                cand = (cy, cx, cy)
                if best is None or cand < best:
                    best = cand
            if best is None:
                return None
            return best[1] / w, best[2] / h
        except Exception as e:
            logger.debug(f"[{self.account_id}] 关注绿钮检测失败: {e}")
            return None

    def _click_green_follow_button(self) -> bool:
        pt = self._find_green_follow_button()
        if not pt:
            return False
        click_ratio(self.d, float(pt[0]), float(pt[1]))
        logger.info(
            f"[{self.account_id}] 绿钮点「关注」 @({pt[0]:.3f},{pt[1]:.3f})"
        )
        self._invalidate_ocr_cache()
        return True

    def _ocr_click_exact_follow(self) -> bool:
        """OCR 只点文案恰好为「关注」的绿钮文字，跳过「已关注」。"""
        reader = self._ensure_ocr()
        if reader is None:
            return False
        try:
            img = np.array(self.d.screenshot(format="pillow"))
            gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
            enhanced = self._clahe.apply(gray) if self._clahe is not None else gray
            results = reader.readtext(cv2.cvtColor(enhanced, cv2.COLOR_GRAY2BGR))
            y0, y1 = int(self.h * 0.08), int(self.h * 0.38)
            best = None
            for bbox, text, conf in results:
                if conf < 0.30:
                    continue
                t = str(text or "").strip().replace(" ", "")
                if t not in ("关注", "关注公众号"):
                    continue
                if "已关注" in t:
                    continue
                cy = int((bbox[0][1] + bbox[2][1]) / 2)
                cx = int((bbox[0][0] + bbox[2][0]) / 2)
                if cy < y0 or cy > y1:
                    continue
                cand = (cy, cx, cy)
                if best is None or cand < best:
                    best = cand
            if best is None:
                return False
            self.d.click(best[1], best[2])
            self._invalidate_ocr_cache()
            logger.info(f"[{self.account_id}] OCR 精确点「关注」")
            return True
        except Exception as e:
            logger.debug(f"[{self.account_id}] OCR 点关注失败: {e}")
            return False

    # ================================================================
    # 加好友（严格限流，由剧本/AI 控制次数）
    # ================================================================

    def add_friend(self, keyword: str, remark_source: str = "") -> bool:
        """
        添加好友（手机号/微信号）。

        优先走「+ → 添加朋友」官方入口（全局搜索对手机号不可靠）；
        失败再尝试通讯录「新的朋友」。
        申请页会把 remark_source 写入微信「设置备注」（详细来源）。
        """
        if not keyword:
            return False
        keyword = str(keyword).strip()
        remark = self._build_friend_remark(keyword, remark_source)
        logger.info(
            f"[{self.account_id}] 加好友: {keyword} "
            f"(source={remark_source or 'unspecified'}, remark={remark})"
        )
        try:
            from utils.image_utils import save_debug_screenshot
            from core.wechat_nav import lock_portrait

            lock_portrait(self.d)
            try:
                self.w, self.h = self.d.window_size()
            except Exception:
                pass

            if not self._open_add_friend_page():
                save_debug_screenshot(self.d, self.account_id, "add_friend_no_entry")
                logger.warning(f"[{self.account_id}] 无法打开添加朋友页")
                return False

            if not self._input_add_friend_keyword(keyword):
                save_debug_screenshot(self.d, self.account_id, "add_friend_input_fail")
                return False
            time.sleep(1.0)

            # 点搜索结果：查找手机/微信号
            if not self._click_search_user_result(keyword):
                save_debug_screenshot(self.d, self.account_id, "add_friend_no_result")
                logger.warning(f"[{self.account_id}] 未点到搜索用户结果: {keyword}")
                self.d.press("back")
                return False
            time.sleep(1.2)

            # 资料页状态：一次 OCR 同时判「不存在 / 已是好友」
            blob = self._ocr_screen_blob(force=True)
            if any(
                k in blob
                for k in ("不存在", "无法找到", "找不到", "没有找到", "未找到")
            ):
                save_debug_screenshot(self.d, self.account_id, "add_friend_not_found")
                logger.warning(f"[{self.account_id}] 用户不存在或无法搜索: {keyword}")
                self.d.press("back")
                return False

            has_add = any(
                k in blob for k in ("添加到通讯录", "加好友", "添加到通讯")
            )
            if ("发消息" in blob) and not has_add:
                logger.info(f"[{self.account_id}] 已是好友: {keyword}")
                self.d.press("back")
                return True

            # 严格确认已在用户资料页再点「添加到通讯录」；视频号等页面有相似绿色元素会误点
            if not self._is_user_profile_page():
                save_debug_screenshot(self.d, self.account_id, "add_friend_not_profile")
                logger.warning(f"[{self.account_id}] 点击搜索结果后未进入资料页，跳过添加: {keyword}")
                self.d.press("back")
                return False

            # 关键两步：① 添加到通讯录  ② 申请页填验证+备注并发送
            if not self._click_add_to_contacts():
                save_debug_screenshot(self.d, self.account_id, "add_friend_no_btn")
                logger.warning(f"[{self.account_id}] 未点到「添加到通讯录」: {keyword}")
                self.d.press("back")
                return False

            verify = random.choice(["你好，我是朋友介绍的", "你好呀", "Hi，交个朋友"])
            if not self._submit_friend_request(verify, remark=remark):
                save_debug_screenshot(self.d, self.account_id, "add_friend_no_send")
                logger.warning(f"[{self.account_id}] 未点到「发送」: {keyword}")
                for _ in range(3):
                    self.d.press("back")
                    time.sleep(0.25)
                return False

            for _ in range(2):
                self.d.press("back")
                time.sleep(0.25)
            logger.info(
                f"[{self.account_id}] 加好友申请已发送: {keyword} remark={remark}"
            )
            return True
        except Exception as e:
            logger.error(f"[{self.account_id}] 加好友失败: {e}")
            return False

    @staticmethod
    def _build_friend_remark(keyword: str, remark_source: str = "") -> str:
        """
        生成写入微信「设置备注」的详细来源文案。

        例: 手机号搜索|来源:手动添加|20260803
        """
        src = (remark_source or "unspecified").strip() or "unspecified"
        if src.startswith("来源:") or src.startswith("来源："):
            src = src.split(":", 1)[-1].split("：", 1)[-1].strip() or "unspecified"
        kw = str(keyword).strip()
        if kw.isdigit() and len(kw) == 11:
            channel = "手机号搜索"
        elif kw.isdigit():
            channel = "号码搜索"
        else:
            channel = "微信号搜索"
        date = time.strftime("%Y%m%d")
        return f"{channel}|来源:{src}|{date}"[:40]

    def _is_friend_request_page(self) -> bool:
        """是否在申请添加朋友页（可点底部发送）。排除「选择标签」子页。"""
        blob = self._ocr_screen_blob()
        if self._blob_is_tag_picker(blob):
            return False
        return self._blob_is_friend_request(blob)

    def _is_tag_picker_page(self) -> bool:
        """是否误入申请页里的「选择标签」子页面。"""
        return self._blob_is_tag_picker(self._ocr_screen_blob())

    @staticmethod
    def _blob_is_tag_picker(blob: str) -> bool:
        if not blob:
            return False
        return any(k in blob for k in ("选择标签", "新建标签", "我的标签")) and (
            any(k in blob for k in ("完成", "管理", "取消"))
        )

    @staticmethod
    def _blob_is_friend_request(blob: str) -> bool:
        if not blob:
            return False
        return any(
            k in blob
            for k in (
                "打招呼内容",
                "添加备注",
                "申请添加朋友",
                "朋友验证",
                "你需要发送验证",
            )
        ) and any(k in blob for k in ("发送", "备注", "标签", "添加图片"))

    def _looks_like_friend_request_fast(self) -> bool:
        """优先用底部绿钮判断申请页，避免每次全屏 OCR。"""
        if self._find_green_add_button(y_min=0.78, y_max=0.98):
            return True
        return self._is_friend_request_page()

    def _leave_tag_picker_if_needed(self) -> None:
        """从标签页返回申请页（点取消，避免点完成改动标签）。"""
        if not self._is_tag_picker_page():
            return
        logger.warning(f"[{self.account_id}] 误入选择标签页，返回申请页")
        if not self._ocr_click_any(["取消"], y_min=0.20, y_max=0.40):
            try:
                self.d.press("back")
            except Exception:
                pass
        self._invalidate_ocr_cache()
        time.sleep(0.35)

    def _is_phone_misclick_ui(self) -> bool:
        """是否误点了资料页「电话」行（系统拨号或微信内呼叫面板）。"""
        if self._looks_like_friend_request_fast():
            return False
        try:
            pkg = (self.d.app_current() or {}).get("package", "") or ""
            if pkg and pkg != "com.tencent.mm":
                return True
        except Exception:
            pass
        # 微信内底部面板：呼叫 / 复制号码（勿用单独「复制」，误伤太多）
        return self._ocr_has_any(
            [
                "拨号",
                "呼叫",
                "通话",
                "复制号码",
                "新建联系人",
                "添加联系人",
                "电话本",
                "添加到手机通讯录",
            ]
        )

    def _dismiss_phone_action_sheet(self) -> bool:
        """关闭资料页误点电话后的「呼叫/复制/取消」面板。"""
        if not self._is_phone_misclick_ui():
            return False
        logger.warning(f"[{self.account_id}] 关闭电话操作面板")
        if self._ocr_click_any(["取消"], y_min=0.70, y_max=0.99):
            self._invalidate_ocr_cache()
            time.sleep(0.35)
            return True
        try:
            self.d.press("back")
        except Exception:
            pass
        self._invalidate_ocr_cache()
        time.sleep(0.3)
        return not self._is_phone_misclick_ui()

    def _recover_from_phone_misclick(self) -> None:
        """从误点电话/拨号界面回到微信资料页。"""
        logger.warning(f"[{self.account_id}] 检测到误点电话，尝试返回资料页")
        if self._dismiss_phone_action_sheet():
            return
        try:
            if (self.d.app_current() or {}).get("package") != "com.tencent.mm":
                start_wechat(self.d, wait=2.0, cold=False)
        except Exception:
            pass
        for _ in range(3):
            try:
                self.d.press("back")
            except Exception:
                pass
            self._invalidate_ocr_cache()
            time.sleep(0.3)
            if not self._is_phone_misclick_ui():
                break

    def _is_user_profile_page(self) -> bool:
        """是否已在陌生人/好友资料页（可点添加到通讯录或发消息）。"""
        blob = self._ocr_screen_blob()
        if self._blob_is_friend_request(blob) and not self._blob_is_tag_picker(blob):
            return False
        return any(
            k in blob for k in ("添加到通讯录", "朋友资料", "发消息", "音视频通话")
        ) or (
            any(k in blob for k in ("来源", "来自"))
            and any(k in blob for k in ("电话", "签名", "地区"))
        )

    def _find_green_add_button(self, y_min: float = 0.36, y_max: float = 0.65):
        """
        用颜色找资料页绿色「添加到通讯录」按钮中心（比例坐标）。

        红米实测按钮约 y=0.43；电话行约 y=0.29。
        资料页下半区会展示用户发布的视频号视频缩略图，封面中可能有大块绿色，
        须用宽高比过滤：「添加到通讯录」是横条按钮（宽/高 >> 1），
        视频缩略图的绿色块宽高比通常接近正方形，不满足横条条件。
        """
        try:
            shot = self.d.screenshot(format="opencv")
            if shot is None:
                return None
            h, w = shot.shape[:2]
            hsv = cv2.cvtColor(shot, cv2.COLOR_BGR2HSV)
            mask = cv2.inRange(
                hsv, np.array([35, 60, 60]), np.array([95, 255, 255])
            )
            y0, y1 = int(h * y_min), int(h * y_max)
            mask[:y0, :] = 0
            mask[y1:, :] = 0
            mask[:, : int(w * 0.08)] = 0
            mask[:, int(w * 0.92) :] = 0
            contours, _ = cv2.findContours(
                mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
            )
            best = None  # (area, cx, cy)
            min_area = w * h * 0.006
            for c in contours:
                x, y, bw, bh = cv2.boundingRect(c)
                area = bw * bh
                if area < min_area:
                    continue
                # 高度范围：按钮条（不能太窄也不能太高）
                if bh < h * 0.022 or bh > h * 0.09:
                    continue
                # 宽度：必须横跨屏幕主体（>= 40%）
                if bw < w * 0.40:
                    continue
                # 宽高比：横条按钮宽高比至少 5:1；视频封面绿色块宽高比通常 < 3
                if bw / max(bh, 1) < 5:
                    continue
                cx, cy = x + bw // 2, y + bh // 2
                if best is None or area > best[0]:
                    best = (area, cx, cy)
            if best is None:
                return None
            return best[1] / w, best[2] / h
        except Exception as e:
            logger.debug(f"[{self.account_id}] 绿钮检测失败: {e}")
            return None

    def _click_add_to_contacts(self) -> bool:
        """
        资料页点击「添加到通讯录」，并确认进入申请页。

        速度优先：绿钮 → 机型坐标 → OCR；进页用底部绿钮快判。
        """
        from config.device_profiles import get_extra
        from core.wechat_nav import lock_portrait

        lock_portrait(self.d)

        if self._looks_like_friend_request_fast():
            logger.info(f"[{self.account_id}] 已在好友申请页，跳过添加按钮")
            return True

        phrases = [
            "添加到通讯录",
            "添加到通讯",
            "加为好友",
            "添加好友",
        ]

        def _after_click_ok() -> bool:
            self._invalidate_ocr_cache()
            time.sleep(0.7)
            if self._find_green_add_button(y_min=0.36, y_max=0.65):
                return False
            if self._looks_like_friend_request_fast():
                return True
            if self._is_phone_misclick_ui():
                self._recover_from_phone_misclick()
            return False

        def _try_green() -> bool:
            pt = self._find_green_add_button(y_min=0.36, y_max=0.65)
            if not pt:
                return False
            rx, ry = float(pt[0]), float(pt[1])
            if ry < 0.36 or ry > 0.65:
                return False
            click_ratio(self.d, rx, ry)
            if _after_click_ok():
                logger.info(
                    f"[{self.account_id}] 绿钮点「添加到通讯录」 @({rx:.3f},{ry:.3f})"
                )
                return True
            return False

        # 1) 绿色按钮（无 OCR）
        if _try_green():
            return True

        # 2) 机型坐标（无 OCR）
        raw = get_extra(
            self.d,
            "add_to_contacts_candidates",
            [
                (0.50, 0.43),
                (0.50, 0.48),
                (0.50, 0.40),
                (0.50, 0.52),
            ],
        )
        candidates = []
        for pt in list(raw or []):
            try:
                rx, ry = float(pt[0]), float(pt[1])
            except Exception:
                continue
            if ry < 0.36 or ry > 0.72:
                continue
            candidates.append((rx, ry))
        if not candidates:
            candidates = [(0.50, 0.43), (0.50, 0.48)]

        for rx, ry in candidates[:3]:
            click_ratio(self.d, rx, ry)
            if _after_click_ok():
                logger.info(
                    f"[{self.account_id}] 坐标点「添加到通讯录」 @({rx},{ry})"
                )
                return True

        # 3) OCR 兜底：「添加到通讯录」文字在屏幕上半区，y_max 收到 0.65 避开视频卡片
        if self._ocr_click_phrase(
            phrases,
            y_min=0.36,
            y_max=0.65,
            strict=True,
            avoid_keywords=("电话", "呼叫", "复制", "取消"),
        ):
            if _after_click_ok():
                logger.info(f"[{self.account_id}] OCR 已点「添加到通讯录」")
                return True

        try:
            self.d.swipe(
                int(self.w * 0.5),
                int(self.h * 0.65),
                int(self.w * 0.5),
                int(self.h * 0.48),
                duration=0.28,
            )
            self._invalidate_ocr_cache()
            time.sleep(0.4)
        except Exception:
            pass

        if _try_green():
            return True
        if self._ocr_click_phrase(
            phrases,
            y_min=0.34,
            y_max=0.65,
            strict=True,
            avoid_keywords=("电话", "呼叫", "复制", "取消"),
        ):
            if _after_click_ok():
                logger.info(f"[{self.account_id}] OCR(滑动后) 已点「添加到通讯录」")
                return True

        return self._looks_like_friend_request_fast()

    def _submit_friend_request(self, verify_msg: str, remark: str = "") -> bool:
        """申请页：写备注后点底部绿色「发送」（新版已无右上角发送）。"""
        from core.wechat_nav import lock_portrait

        lock_portrait(self.d)
        time.sleep(0.35)

        if not self._looks_like_friend_request_fast():
            self._leave_tag_picker_if_needed()
            if not self._looks_like_friend_request_fast():
                logger.warning(f"[{self.account_id}] 未进入好友申请页，无法发送")
                return False

        if remark:
            ok_remark = self._fill_friend_remark_field(remark)
            if ok_remark:
                logger.info(f"[{self.account_id}] 已写入备注来源: {remark}")
            else:
                logger.warning(
                    f"[{self.account_id}] 备注来源写入失败，继续发送: {remark}"
                )

        if verify_msg:
            self._fill_verify_edittext_only(verify_msg)

        self._dismiss_keyboard()
        time.sleep(0.25)

        if self._click_friend_request_send():
            return True

        logger.warning(f"[{self.account_id}] 未能点击「发送」")
        return False

    def _click_friend_request_send(self) -> bool:
        """点击申请页发送：优先底部绿钮/坐标，OCR 兜底。"""
        from config.device_profiles import get_extra

        def _sent_ok() -> bool:
            self._invalidate_ocr_cache()
            time.sleep(0.55)
            if not self._find_green_add_button(y_min=0.78, y_max=0.98):
                blob = self._ocr_screen_blob(force=True)
                if any(k in blob for k in ("确定", "我知道了", "知道了")):
                    self._ocr_click_any(
                        ["确定", "我知道了", "知道了"], y_min=0.4, y_max=0.9
                    )
                return True
            blob = self._ocr_screen_blob(force=True)
            if any(k in blob for k in ("确定", "我知道了", "知道了")):
                self._ocr_click_any(
                    ["确定", "我知道了", "知道了"], y_min=0.4, y_max=0.9
                )
                return True
            return not self._blob_is_friend_request(blob)

        pt = self._find_green_add_button(y_min=0.78, y_max=0.98)
        if pt and float(pt[1]) >= 0.78:
            click_ratio(self.d, float(pt[0]), float(pt[1]))
            if _sent_ok():
                logger.info(
                    f"[{self.account_id}] 绿钮底部「发送」 "
                    f"@({pt[0]:.3f},{pt[1]:.3f})"
                )
                return True

        send_pts = get_extra(
            self.d,
            "friend_request_send_candidates",
            [(0.50, 0.90), (0.50, 0.86), (0.50, 0.93), (0.50, 0.82)],
        )
        for rx, ry in list(send_pts or [])[:3]:
            try:
                rx_f, ry_f = float(rx), float(ry)
            except Exception:
                continue
            if ry_f < 0.78:
                continue
            click_ratio(self.d, rx_f, ry_f)
            if _sent_ok():
                logger.info(f"[{self.account_id}] 坐标底部「发送」 @({rx_f},{ry_f})")
                return True

        if self._ocr_click_any(["发送"], y_min=0.75, y_max=0.98):
            if _sent_ok():
                logger.info(f"[{self.account_id}] OCR 底部点「发送」")
                return True

        if self._ocr_click_right(["发送"], y_min=0.01, y_max=0.14, x_min=0.62):
            if _sent_ok():
                logger.info(f"[{self.account_id}] OCR 右上角「发送」")
                return True

        for rx, ry in ((0.92, 0.055), (0.88, 0.055)):
            click_ratio(self.d, rx, ry)
            if _sent_ok():
                logger.info(f"[{self.account_id}] 坐标点「发送」 @({rx},{ry})")
                return True
        return False

    def _dismiss_keyboard(self) -> None:
        """收起键盘，不按系统返回（避免退出好友申请页）。"""
        try:
            self.d.set_input_ime(False)
        except Exception:
            pass
        try:
            if hasattr(self.d, "hide_keyboard"):
                self.d.hide_keyboard()
                time.sleep(0.2)
                return
        except Exception:
            pass
        # 点标题栏左侧空白，避开右上角「发送」
        try:
            click_ratio(self.d, 0.28, 0.055)
            time.sleep(0.25)
        except Exception:
            pass

    def _fill_verify_edittext_only(self, verify_msg: str) -> bool:
        """只通过 EditText[0] 写验证语，避免坐标误点备注框。"""
        if not verify_msg:
            return False
        try:
            edits = self.d(className="android.widget.EditText")
            if edits.exists and edits.count >= 2:
                edits[0].set_text(verify_msg)
                time.sleep(0.2)
                logger.info(f"[{self.account_id}] EditText[0] 写入验证信息")
                return True
        except Exception as e:
            logger.debug(f"[{self.account_id}] 验证信息跳过: {e}")
        return False

    def _fill_request_field(
        self,
        text: str,
        y_candidates: tuple[float, ...],
        label: str = "输入框",
        single_click: bool = False,
        do_clear: bool = True,
    ) -> bool:
        """点击候选 Y 后输入；默认只点一次。备注场景可关闭 clear。"""
        if not text:
            return False
        try:
            self.d.set_input_ime(True)
            time.sleep(0.15)
            clicked = False
            for ry in y_candidates:
                click_ratio(self.d, 0.55, float(ry))
                time.sleep(0.25)
                clicked = True
                if single_click:
                    break
            if not clicked:
                return False
            # 优先 set_text，避免 clear+send_keys 二次清空
            try:
                focused = self.d(focused=True)
                if focused.exists:
                    focused.set_text(text)
                    self.d.set_input_ime(False)
                    time.sleep(0.25)
                    return True
            except Exception:
                pass
            if do_clear:
                try:
                    self.d.clear_text()
                except Exception:
                    pass
            self.d.send_keys(text)
            self.d.set_input_ime(False)
            time.sleep(0.3)
            return True
        except Exception as e:
            logger.debug(f"[{self.account_id}] {label}输入跳过: {e}")
            return False

    def _fill_friend_remark_field(self, remark: str) -> bool:
        """
        新版申请页：点「添加备注」输入行写入详细来源；避开「添加标签」。
        速度优先：EditText → 坐标 → OCR。
        """
        if not remark:
            return False
        from config.device_profiles import get_extra

        # 1) EditText[1]（快，无 OCR）
        try:
            edits = self.d(className="android.widget.EditText")
            if edits.exists and edits.count >= 2:
                edits[1].click()
                time.sleep(0.15)
                if self._is_tag_picker_page():
                    self._leave_tag_picker_if_needed()
                else:
                    edits[1].set_text(remark)
                    time.sleep(0.2)
                    self._dismiss_keyboard()
                    logger.info(f"[{self.account_id}] EditText[1] 写入备注")
                    return True
        except Exception as e:
            logger.debug(f"[{self.account_id}] EditText 备注失败: {e}")

        # 2) 坐标：红米「添加备注」约 y=0.36；严禁 ≥0.45（标签区）
        raw = get_extra(
            self.d,
            "friend_request_remark_candidates",
            [(0.50, 0.36), (0.50, 0.34), (0.50, 0.38)],
        )
        for pnt in list(raw or [])[:3]:
            try:
                ry = float(pnt[1])
            except Exception:
                continue
            if ry < 0.28 or ry > 0.42:
                continue
            click_ratio(self.d, 0.50, ry)
            time.sleep(0.2)
            if self._is_tag_picker_page():
                self._leave_tag_picker_if_needed()
                continue
            if self._set_text_once(remark, allow_clear=True):
                self._dismiss_keyboard()
                if not self._is_tag_picker_page():
                    logger.info(f"[{self.account_id}] 坐标写入备注 @y={ry}")
                    return True

        # 3) OCR 兜底
        if self._ocr_click_remark_input():
            if self._is_tag_picker_page():
                self._leave_tag_picker_if_needed()
            elif self._set_text_once(remark, allow_clear=True):
                self._dismiss_keyboard()
                if not self._is_tag_picker_page():
                    logger.info(f"[{self.account_id}] OCR路径写入备注")
                    return True
        return False

    def _ocr_click_remark_input(self) -> bool:
        """OCR 定位「添加备注/设置备注」，点击输入行（避开标签/备忘）。"""
        try:
            reader = self._ensure_ocr()
            if reader is None:
                return False
            shot = self.d.screenshot(format="opencv")
            if shot is None:
                return False
            h, w = shot.shape[:2]
            results = reader.readtext(shot)
            best = None  # (score, tap_x, tap_y, t)
            for item in results:
                box, text, conf = item[0], str(item[1]), float(item[2])
                if conf < 0.35:
                    continue
                t = text.strip().replace(" ", "")
                # 明确排除
                if any(
                    bad in t
                    for bad in (
                        "标签",
                        "备忘",
                        "照片",
                        "权限",
                        "打招呼",
                        "图片",
                        "发送",
                    )
                ):
                    continue
                score = 0
                if "添加备注" in t or "设置备注" in t:
                    score = 4
                elif t == "备注" or t.endswith("备注"):
                    score = 2
                elif "备注名" in t:
                    score = 3
                else:
                    continue
                xs = [p[0] for p in box]
                ys = [p[1] for p in box]
                cy = (min(ys) + max(ys)) / 2.0
                ry = cy / h
                # 备注行约 0.30~0.40；标签约 0.45+
                if ry < 0.24 or ry > 0.44:
                    continue
                # 「添加备注」点在文字中部；短「备注」点右侧输入区
                if "添加" in t or "设置" in t:
                    tap_x = int((min(xs) + max(xs)) / 2)
                else:
                    tap_x = min(int(max(xs) + w * 0.25), int(w * 0.85))
                tap_y = int(cy)
                if best is None or score > best[0]:
                    best = (score, tap_x, tap_y, t)
            if best is None:
                return False
            _, tap_x, tap_y, t = best
            self.d.click(tap_x, tap_y)
            time.sleep(0.35)
            logger.info(
                f"[{self.account_id}] OCR 点备注输入 "
                f"'{t}' @({tap_x},{tap_y})"
            )
            return True
        except Exception as e:
            logger.debug(f"[{self.account_id}] OCR 备注定位失败: {e}")
        return False

    def _set_text_once(self, text: str, allow_clear: bool = True) -> bool:
        """对当前焦点框写入一次，优先 set_text。"""
        try:
            focused = self.d(focused=True)
            if focused.exists:
                try:
                    focused.set_text(text)
                    time.sleep(0.25)
                    return True
                except Exception:
                    pass
            self.d.set_input_ime(True)
            time.sleep(0.1)
            if allow_clear:
                try:
                    self.d.clear_text()
                except Exception:
                    pass
            self.d.send_keys(text)
            self.d.set_input_ime(False)
            time.sleep(0.25)
            return True
        except Exception as e:
            logger.debug(f"[{self.account_id}] 单次写入失败: {e}")
            return False

    def _type_into_focused(self, text: str) -> bool:
        """兼容旧调用：转为单次写入。"""
        return self._set_text_once(text)

    def _ocr_click_phrase(
        self,
        phrases: list[str],
        y_min: float = 0.05,
        y_max: float = 0.95,
        strict: bool = False,
        avoid_keywords: tuple[str, ...] = (),
    ) -> bool:
        """
        OCR 点击长文案：单框匹配失败时，把同一行相邻文字拼起来再匹配。

        strict=True 时禁止短字串反向包含（t in p），降低误点「电话」等控件。
        """
        try:
            reader = self._ensure_ocr()
            img = np.array(self.d.screenshot(format="pillow"))
            gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
            enhanced = self._clahe.apply(gray) if self._clahe is not None else gray
            results = reader.readtext(cv2.cvtColor(enhanced, cv2.COLOR_GRAY2BGR))
            y0, y1 = int(self.h * y_min), int(self.h * y_max)

            items = []
            for bbox, text, conf in results:
                if conf < 0.30:
                    continue
                t = self._normalize_friend_add_ocr_text(str(text or ""))
                if not t:
                    continue
                cx = int((bbox[0][0] + bbox[2][0]) / 2)
                cy = int((bbox[0][1] + bbox[2][1]) / 2)
                if cy < y0 or cy > y1:
                    continue
                if avoid_keywords and any(k in t for k in avoid_keywords):
                    continue
                # 纯数字行多半是电话，跳过
                if strict and sum(ch.isdigit() for ch in t) >= 7:
                    continue
                items.append((cy, cx, t))

            if not items:
                return False

            items.sort(key=lambda x: (x[0], x[1]))
            best = None  # (score, cx, cy)
            for cy, cx, t in items:
                for p in phrases:
                    if not p:
                        continue
                    if p in t:
                        score = len(p) + 2
                    elif (not strict) and t in p and len(t) >= max(2, len(p) // 2):
                        score = len(t)
                    else:
                        continue
                    if best is None or score > best[0]:
                        best = (score, cx, cy)
            # 同行拼接（cy 差 < 2.2% 屏高）
            row_tol = int(self.h * 0.022)
            for i, (cy, cx, t) in enumerate(items):
                joined = t
                jx, jy = cx, cy
                for cy2, cx2, t2 in items[i + 1 :]:
                    if abs(cy2 - cy) > row_tol:
                        break
                    joined += t2
                    jx = (jx + cx2) // 2
                if avoid_keywords and any(k in joined for k in avoid_keywords):
                    continue
                for p in phrases:
                    if p and p in joined:
                        score = len(p) + 3
                        if best is None or score > best[0]:
                            best = (score, jx, jy)

            if best is None:
                return False
            self.d.click(best[1], best[2])
            self._invalidate_ocr_cache()
            time.sleep(0.45)
            return True
        except Exception as e:
            logger.debug(f"[{self.account_id}] _ocr_click_phrase 失败: {e}")
            return False

    def _is_plus_menu_open(self) -> bool:
        """右上角 + 弹出菜单是否仍打开（发起群聊+收付款同时出现）。"""
        return self._ocr_has_any(["发起群聊"]) and self._ocr_has_any(["收付款"])

    def _is_add_friend_page(self) -> bool:
        """
        是否已在「添加朋友」页。

        新版: 「搜索 账号/手机号」「手机联系人」「企业微信联系人」「雷达」
        旧版: 「微信号/手机号」「雷达加朋友」
        """
        if self._is_plus_menu_open():
            return False
        markers_a = [
            "账号/手机号",
            "微信号/手机号",
            "手机联系人",
            "企业微信联系人",
            "雷达加朋友",
            "面对面建群",
        ]
        markers_b = [
            "添加朋友",
            "账号",
            "手机号",
            "微信号",
            "雷达",
            "面对面",
            "手机联系人",
        ]
        return self._ocr_has_any(markers_a) and self._ocr_has_any(markers_b)

    def _ensure_wechat_foreground(self) -> bool:
        """确认微信仍在前台；否则重新拉起。"""
        try:
            if self.d.app_current().get("package") == "com.tencent.mm":
                return True
        except Exception:
            pass
        logger.warning(f"[{self.account_id}] 微信不在前台，重新拉起")
        start_wechat(self.d, wait=3.0, cold=False)
        try:
            return self.d.app_current().get("package") == "com.tencent.mm"
        except Exception:
            return False

    def _open_add_friend_page(self) -> bool:
        """打开「添加朋友」页：右上角 + → 添加朋友；或 通讯录 → 新的朋友。"""
        from core.wechat_nav import goto_tab, dismiss_app_chooser

        # 先预热 OCR，避免打开 + 菜单后才加载模型
        self._ensure_ocr()

        start_wechat(self.d, wait=3.0, cold=True)
        dismiss_app_chooser(self.d)
        self._dismiss_leave_wechat_dialog()
        if not self._ensure_wechat_foreground():
            return False
        goto_tab(self.d, "wechat")
        time.sleep(1.0)

        if self._open_add_friend_via_plus_menu():
            return True
        if not self._ensure_wechat_foreground():
            return False
        if self._open_add_friend_via_contacts():
            return True
        return self._is_add_friend_page()

    def _open_add_friend_via_plus_menu(self) -> bool:
        """微信 Tab → 右上角 + → 添加朋友（坐标优先，OCR 兜底）。"""
        from config.device_profiles import get_extra
        from core.wechat_nav import goto_tab

        top_plus = get_extra(self.d, "top_plus_btn", (0.96, 0.054))
        plus_candidates = [tuple(top_plus), (0.96, 0.054)]
        menu_item = get_extra(self.d, "add_friend_menu_item", (0.78, 0.197))
        raw = get_extra(
            self.d,
            "add_friend_menu_candidates",
            [tuple(menu_item), (0.84, 0.197), (0.72, 0.20)],
        )
        menu_candidates = list(raw) if raw else [tuple(menu_item)]

        for px, py in plus_candidates:
            if not self._ensure_wechat_foreground():
                return False
            goto_tab(self.d, "wechat")
            time.sleep(0.5)
            click_ratio(self.d, px, py)
            time.sleep(0.8)

            # 1) 坐标优先（红米实测 添加朋友 @0.78,0.197）
            for mx, my in menu_candidates:
                click_ratio(self.d, float(mx), float(my))
                time.sleep(1.2)
                if self._is_add_friend_page():
                    logger.info(
                        f"[{self.account_id}] 坐标进入添加朋友页 "
                        f"plus=({px:.2f},{py:.2f}) item=({mx},{my})"
                    )
                    return True
                if self._is_plus_menu_open():
                    continue
                self.d.press("back")
                time.sleep(0.4)
                if not self._ensure_wechat_foreground():
                    return False
                click_ratio(self.d, px, py)
                time.sleep(0.7)

            # 2) OCR 点右侧「添加朋友」
            if self._ocr_click_right(
                ["添加朋友"],
                y_min=0.08,
                y_max=0.40,
                x_min=0.55,
            ):
                time.sleep(1.2)
                if self._is_add_friend_page():
                    logger.info(f"[{self.account_id}] OCR 进入添加朋友页")
                    return True

            self.d.press("back")
            time.sleep(0.3)

        return False

    def _open_add_friend_via_contacts(self) -> bool:
        """通讯录 → 新的朋友 → 添加朋友。"""
        from core.wechat_nav import goto_tab
        from config.device_profiles import get_extra

        goto_tab(self.d, "contacts")
        time.sleep(1.0)

        entry = get_extra(self.d, "new_friends_entry", (0.40, 0.14))
        opened = self._ocr_click_any(["新的朋友"], y_min=0.06, y_max=0.35)
        if not opened:
            click_ratio(self.d, float(entry[0]), float(entry[1]))
            time.sleep(1.0)

        # 新的朋友页右上角「添加朋友」或页面内入口
        click_ratio(self.d, 0.92, 0.055)
        time.sleep(1.0)
        if self._is_add_friend_page():
            return True
        if self._ocr_click_any(["添加朋友"], y_min=0.02, y_max=0.50):
            time.sleep(1.2)
            return self._is_add_friend_page()
        # 有的版本顶部就是搜索框
        return self._is_add_friend_page()

    def _ocr_click_right(
        self,
        keywords: list[str],
        y_min: float = 0.05,
        y_max: float = 0.95,
        x_min: float = 0.50,
    ) -> bool:
        """OCR 点击：要求关键词出现在文字中，且中心点在右半屏。"""
        try:
            reader = self._ensure_ocr()
            img = np.array(self.d.screenshot(format="pillow"))
            gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
            enhanced = self._clahe.apply(gray) if self._clahe is not None else gray
            results = reader.readtext(cv2.cvtColor(enhanced, cv2.COLOR_GRAY2BGR))
            y0, y1 = int(self.h * y_min), int(self.h * y_max)
            x0 = int(self.w * x_min)
            best = None  # (score, cx, cy)
            for bbox, text, conf in results:
                if conf < 0.35:
                    continue
                t = str(text or "").strip()
                if not t:
                    continue
                cy = int((bbox[0][1] + bbox[2][1]) / 2)
                cx = int((bbox[0][0] + bbox[2][0]) / 2)
                if cy < y0 or cy > y1 or cx < x0:
                    continue
                score = 0
                for k in keywords:
                    if k and k in t:
                        score = max(score, len(k))
                if not score:
                    continue
                if best is None or score > best[0] or (score == best[0] and cy < best[2]):
                    best = (score, cx, cy)
            if best is None:
                return False
            self.d.click(best[1], best[2])
            time.sleep(1.0)
            return True
        except Exception as e:
            logger.debug(f"[{self.account_id}] _ocr_click_right 失败: {e}")
            return False

    def _dismiss_leave_wechat_dialog(self):
        """关闭「即将离开微信，打开其它应用」弹窗。"""
        try:
            if self._ocr_has_any(["即将离开微信", "打开其它应用", "打开其他应用"]):
                if not self._ocr_click_any(["取消"], y_min=0.45, y_max=0.75):
                    click_ratio(self.d, 0.30, 0.54)  # 取消在左
                time.sleep(0.5)
        except Exception:
            pass

    def _input_add_friend_keyword(self, keyword: str) -> bool:
        """在添加朋友页输入手机号/微信号并触发查找。"""
        # 新版占位「搜索 账号/手机号」；旧版「微信号/手机号」
        clicked_box = self._ocr_click_any(
            ["账号/手机号", "微信号/手机号", "搜索", "手机号", "微信号"],
            y_min=0.06,
            y_max=0.28,
        )
        if not clicked_box:
            for ry in (0.12, 0.145, 0.17, 0.20):
                click_ratio(self.d, 0.50, ry)
                time.sleep(0.45)

        time.sleep(0.6)
        try:
            self.d.set_input_ime(True)
            time.sleep(0.3)
            try:
                self.d.clear_text()
            except Exception:
                pass
            self.d.send_keys(keyword)
            time.sleep(0.8)
            self.d.set_input_ime(False)
        except Exception as e:
            logger.warning(f"[{self.account_id}] 添加朋友输入失败: {e}")
            try:
                self.d.shell(f"input text {keyword}")
            except Exception:
                return False

        time.sleep(0.5)
        # 触发搜索：回车或点「搜索/查找」
        try:
            self.d.press("enter")
        except Exception:
            pass
        time.sleep(1.0)
        self._ocr_click_any(["搜索", "查找", "匹配"], y_min=0.10, y_max=0.40)
        time.sleep(1.5)
        return True

    def _click_search_user_result(self, keyword: str) -> bool:
        """在添加朋友搜索结果页点进用户资料；禁止点资料页「电话」号码。"""
        # 输入后「查找」可能已直接进入资料页
        time.sleep(0.6)
        if self._is_user_profile_page():
            self._dismiss_phone_action_sheet()
            logger.info(f"[{self.account_id}] 已在用户资料页，跳过搜索结果点击")
            return True
        if self._is_friend_request_page():
            return True

        # 只点「查找xxx」类文案，绝不点纯手机号（资料页电话行会误呼）
        find_keys = [
            "查找手机/微信号",
            "查找手机/微信",
            "查找账号",
            "网络查找手机",
            "查找手机",
            "查找微信号",
        ]
        if self._ocr_click_any(find_keys, y_min=0.10, y_max=0.55):
            time.sleep(1.5)
            self._dismiss_phone_action_sheet()
            if self._is_user_profile_page() or self._is_friend_request_page():
                return True

        # OCR：优先含「查找」的行；裸号码仅当同行有「查找」时才点
        try:
            reader = self._ensure_ocr()
            img = np.array(self.d.screenshot(format="pillow"))
            gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
            enhanced = self._clahe.apply(gray) if self._clahe is not None else gray
            results = reader.readtext(cv2.cvtColor(enhanced, cv2.COLOR_GRAY2BGR))
            needle = keyword.replace(" ", "")
            best = None
            for bbox, text, conf in results:
                if conf < 0.25:
                    continue
                t = str(text).replace(" ", "")
                cy = (bbox[0][1] + bbox[2][1]) / 2
                if cy < self.h * 0.08 or cy > self.h * 0.60:
                    continue
                # 跳过资料页电话标签附近的纯号码
                if t.replace("+", "").isdigit() and "查找" not in t:
                    continue
                if "电话" in t or "呼叫" in t or "签名" in t or "来源" in t:
                    continue
                score = 0
                if "查找" in t:
                    score += 6
                if needle and needle in t and "查找" in t:
                    score += 5
                if "微信号" in t or "账号" in t:
                    score += 2
                if score and (best is None or score > best[0]):
                    cx = (bbox[0][0] + bbox[2][0]) / 2
                    best = (score, cx, cy, t)
            if best:
                logger.info(
                    f"[{self.account_id}] 搜索结果命中: '{best[3]}' "
                    f"@({int(best[1])},{int(best[2])}) score={best[0]}"
                )
                self.d.click(int(best[1]), int(best[2]))
                time.sleep(1.5)
                self._dismiss_phone_action_sheet()
                return self._is_user_profile_page() or self._is_friend_request_page()
        except Exception as e:
            logger.debug(f"[{self.account_id}] OCR扫结果失败: {e}")

        # 常见结果行（偏上，避开资料页电话 y≈0.29）
        for ry in (0.16, 0.20, 0.24, 0.28):
            if self._is_user_profile_page():
                self._dismiss_phone_action_sheet()
                return True
            click_ratio(self.d, 0.40, ry)
            time.sleep(1.2)
            self._dismiss_phone_action_sheet()
            if self._ocr_has_any(
                ["添加到通讯录", "朋友资料", "发消息", "设置备注"]
            ):
                return True
            if self._is_user_profile_page():
                return True
            self.d.press("back")
            time.sleep(0.6)
        return self._is_user_profile_page()

    def _ocr_has_any(self, keywords: list[str]) -> bool:
        try:
            blob = self._ocr_screen_blob()
            return any(k in blob for k in keywords)
        except Exception:
            return False

    # ================================================================
    # 小程序浏览
    # ================================================================

    def browse_mini_program(self, duration_seconds: int = 120, keyword: str = "") -> bool:
        """
        发现 → 小程序，浏览近期使用；或搜索指定小程序后停留。
        """
        logger.info(
            f"[{self.account_id}] 浏览小程序: {duration_seconds}s "
            f"kw={keyword or '(recent)'}"
        )
        try:
            start_wechat(self.d, wait=3.0, cold=False)
            goto_tab(self.d, "discover")
            time.sleep(1.0)

            if not self._open_discover_list_entry(
                keywords=["小程序"],
                coord_key="mini_program_entry",
                fallback=(0.32, 0.85),
                success_markers=("最近使用", "我的小程序", "搜索", "常用"),
            ):
                logger.warning(f"[{self.account_id}] 未找到小程序入口")
                return False
            time.sleep(2.0)

            if keyword:
                # 小程序页搜索
                if self._ocr_click_any(["搜索"], y_min=0.02, y_max=0.25):
                    time.sleep(0.8)
                    try:
                        self.d.set_input_ime(True)
                        self.d.send_keys(keyword)
                        self.d.set_input_ime(False)
                        self.d.press("enter")
                        time.sleep(2.0)
                        if not self._ocr_click_any(
                            [keyword, "小程序"], y_min=0.12, y_max=0.7
                        ):
                            click_ratio(self.d, 0.4, 0.25)
                        time.sleep(2.0)
                    except Exception:
                        pass
            else:
                # 点一个近期小程序
                click_ratio(self.d, 0.25, 0.35)
                time.sleep(2.0)

            end = time.time() + max(30, duration_seconds)
            while time.time() < end:
                self.d.swipe(
                    int(self.w * 0.5),
                    int(self.h * 0.65),
                    int(self.w * 0.5),
                    int(self.h * 0.4),
                    duration=0.35,
                )
                time.sleep(random.uniform(2.0, 5.0))

            self._exit_nested(times=3)
            return True
        except Exception as e:
            logger.error(f"[{self.account_id}] 小程序浏览失败: {e}")
            return False

    # ================================================================
    # 官方小游戏（跳一跳等）
    # ================================================================

    def play_mini_game(
        self,
        game_name: str = "",
        duration_seconds: int = 180,
    ) -> bool:
        """
        打开微信官方小游戏并拟人游玩一段时间。

        主路径（贴近真人）:
          微信首页 → 发现 → 游戏 → 顶栏「找游戏」→ 点任一「立即玩」→ 停留后退出
        兜底: 按 game_name 在游戏中心/小程序/全局搜索打开

        若画面识别到「跳一跳」则长按蓄力，否则随机轻点/短滑。
        """
        name = (game_name or "").strip()
        duration = max(45, int(duration_seconds))
        logger.info(
            f"[{self.account_id}] 玩小游戏"
            f"{f': {name}' if name else '（找游戏·立即玩）'} ({duration}s)"
        )
        try:
            # 进发现前禁止点右上角：主界面该位置是「+」不是小程序关闭
            self._prepare_wechat_home_no_capsule()
            opened = self._open_mini_game(name)
            if not opened:
                logger.warning(
                    f"[{self.account_id}] 未能打开小游戏"
                    f"{f': {name}' if name else ''}"
                )
                self._return_to_wechat_home()
                return False

            time.sleep(random.uniform(2.0, 3.5))
            if not self._ensure_game_playable():
                logger.warning(
                    f"[{self.account_id}] 打开后仍在隐私/引导页，未进入可玩界面"
                )
                self._return_to_wechat_home()
                return False

            end = time.time() + duration
            blob = self._ocr_screen_blob(force=True)
            if "跳一跳" in blob or (name and "跳一跳" in name):
                self._play_tiaoyitiao(end)
            else:
                self._play_generic_mini_game(end)

            self._return_to_wechat_home()
            return True
        except Exception as e:
            logger.error(f"[{self.account_id}] 小游戏失败: {e}")
            try:
                self._return_to_wechat_home()
            except Exception:
                pass
            return False

    def _prepare_wechat_home_no_capsule(self) -> None:
        """回到微信会话 Tab，绝不点右上角（避免打开「+」菜单）。"""
        start_wechat(self.d, wait=2.0, cold=False)
        self._escape_nested_to_main(max_backs=8)
        self._dismiss_plus_menu_if_open()
        try:
            goto_tab(self.d, "wechat")
        except Exception:
            pass
        time.sleep(0.5)
        self._dismiss_plus_menu_if_open()

    def _escape_nested_to_main(self, max_backs: int = 8) -> bool:
        """从游戏人生/小程序/隐私等嵌套页退到微信主 Tab，不点右上角「+」。"""
        for i in range(max_backs):
            blob = self._ocr_screen_blob(force=True)
            if self._is_wechat_main_tabs(blob) or self._looks_like_chat_list(blob):
                return True
            if "发起群聊" in blob or "面对面建群" in blob:
                self.d.press("back")
                time.sleep(0.4)
                continue
            try:
                pkg = (self.d.app_current() or {}).get("package", "")
            except Exception:
                pkg = ""
            if pkg and pkg != "com.tencent.mm":
                start_wechat(self.d, wait=2.0, cold=False)
                continue
            if self._is_game_life_auth_page(blob):
                self._ocr_click_any(["暂不展示"], y_min=0.55, y_max=0.95)
                time.sleep(0.4)
                continue
            if self._is_game_life_page(blob) or self._is_privacy_or_policy_page(blob):
                # 优先点左上角返回，再系统 back
                try:
                    click_ratio(self.d, 0.06, 0.055)
                    time.sleep(0.45)
                except Exception:
                    pass
                self.d.press("back")
                time.sleep(0.55)
                continue
            if self._looks_like_games_center(blob):
                self.d.press("back")
                time.sleep(0.5)
                continue
            # 其它嵌套：系统返回；偶发关胶囊
            if i >= 4:
                self._close_miniprogram_capsule()
                time.sleep(0.4)
            else:
                self.d.press("back")
                time.sleep(0.5)
        start_wechat(self.d, wait=2.0, cold=False)
        blob = self._ocr_screen_blob(force=True)
        return self._is_wechat_main_tabs(blob) or self._looks_like_chat_list(blob)

    def _is_game_life_page(self, blob: str = "") -> bool:
        """微信「游戏人生」记录/名片页（非找游戏列表）。"""
        text = blob or self._ocr_screen_blob()
        if "找游戏" in text and ("朋友" in text or "圈子" in text):
            return False
        return "游戏人生" in text and any(
            k in text
            for k in (
                "已玩游戏",
                "游戏时长",
                "游戏成分",
                "微信游戏名片",
                "暂无游戏记录",
                "课程小助手",
            )
        )

    def _looks_like_games_center(self, blob: str = "") -> bool:
        """发现→游戏后的游戏中心（顶栏含找游戏）。"""
        text = blob or self._ocr_screen_blob()
        if self._is_game_life_page(text):
            return False
        return "找游戏" in text and ("朋友" in text or "圈子" in text)

    def _open_mini_game(self, game_name: str) -> bool:
        """优先：发现→游戏→找游戏→立即玩；失败再按名称搜索。"""
        if self._open_via_games_center(game_name):
            return True
        if not game_name:
            logger.info(f"[{self.account_id}] 找游戏·立即玩失败，无指定游戏名可搜索")
            return False
        logger.info(f"[{self.account_id}] 立即玩未成功，改走游戏名搜索: {game_name}")
        self._prepare_wechat_home_no_capsule()
        if self._open_via_mini_program_search(game_name):
            return True
        logger.info(f"[{self.account_id}] 小程序搜索未打开，改走全局搜索")
        self._prepare_wechat_home_no_capsule()
        return self._open_via_global_search(game_name)

    def _open_via_games_center(self, game_name: str) -> bool:
        """
        微信首页 → 发现 → 游戏 → 顶栏找游戏 → 点「立即玩」。
        若立即玩失败且传入 game_name，再尝试搜索该游戏。
        """
        self._prepare_wechat_home_no_capsule()
        goto_tab(self.d, "discover")
        time.sleep(1.0)
        self._dismiss_plus_menu_if_open()

        if not self._open_discover_list_entry(
            keywords=["游戏"],
            coord_key="games_entry",
            fallback=(0.32, 0.78),
            # 勿用「在玩/小游戏」：会误匹配「已玩游戏」等游戏人生文案
            success_markers=("找游戏",),
        ):
            return False
        time.sleep(2.0)
        if not self._looks_like_games_center():
            # 可能误进游戏人生：退后重试一次发现→游戏
            logger.info(f"[{self.account_id}] 未进入游戏中心顶栏，尝试退出后重进")
            self.d.press("back")
            time.sleep(0.6)
            goto_tab(self.d, "discover")
            time.sleep(0.8)
            if not self._open_discover_list_entry(
                keywords=["游戏"],
                coord_key="games_entry",
                fallback=(0.32, 0.78),
                success_markers=("找游戏",),
            ):
                return False
            time.sleep(1.5)
            if not self._looks_like_games_center():
                return False

        # 必须进顶栏「找游戏」，不能只靠圈子页右下角悬浮「立即玩」
        if not self._switch_to_find_games_tab():
            logger.warning(f"[{self.account_id}] 未能切换到「找游戏」Tab")
            return False
        time.sleep(1.0)

        if self._click_play_now_any():
            return True

        if game_name:
            if self._search_and_launch_game(game_name):
                return True
            return self._browse_list_for_game(game_name)
        return False

    def _switch_to_find_games_tab(self) -> bool:
        """游戏中心顶栏：朋友 / 圈子 / 找游戏 → 切到找游戏。"""
        for attempt in range(4):
            if attempt < 3:
                self._ocr_click_any(["找游戏"], y_min=0.03, y_max=0.20)
            else:
                # 顶栏右侧「找游戏」坐标兜底
                click_ratio(self.d, 0.72, 0.08)
            time.sleep(1.1)
            blob = self._ocr_screen_blob(force=True)
            if self._on_find_games_list(blob):
                return True
        return False

    def _on_find_games_list(self, blob: str = "") -> bool:
        """是否已在「找游戏」列表（而非圈子动态）。"""
        text = blob or self._ocr_screen_blob()
        if "立即玩" not in text:
            return False
        # 圈子动态流
        if "更多圈子" in text:
            return False
        if "关注" in text and any(
            k in text for k in ("条新内容", "点赞", "评论", "分享", "我家孩子")
        ):
            return False
        if any(k in text for k in ("今日精选", "热门", "必玩", "排行榜", "分类", "精选")):
            return True
        # 顶栏有找游戏 + 列表立即玩，且不像动态流
        return "找游戏" in text and "条新内容" not in text

    def _click_play_now_any(self) -> bool:
        """在找游戏列表里点「立即玩」（避开右下角悬浮条），必要时下滑翻页。"""
        for _ in range(6):
            # 列表行内按钮偏右；悬浮条多在 y>0.85，故限制 y_max
            if self._ocr_click_any_boxed(
                ["立即玩"],
                y_min=0.14,
                y_max=0.82,
                x_min=0.40,
                x_max=0.96,
            ):
                time.sleep(2.8)
                if self._ensure_game_playable():
                    return True
                self._ocr_click_any_boxed(
                    ["立即玩", "开始游戏", "进入游戏"],
                    y_min=0.55,
                    y_max=0.96,
                    x_min=0.20,
                    x_max=0.95,
                )
                time.sleep(2.0)
                if self._ensure_game_playable():
                    return True
                if self._is_privacy_or_policy_page():
                    self.d.press("back")
                    time.sleep(0.6)
                self._exit_nested(times=2)
                time.sleep(0.6)
                self._switch_to_find_games_tab()
            self.d.swipe(
                int(self.w * 0.55),
                int(self.h * 0.75),
                int(self.w * 0.55),
                int(self.h * 0.35),
                duration=0.35,
            )
            time.sleep(0.8)
        return False

    def _open_via_mini_program_search(self, game_name: str) -> bool:
        start_wechat(self.d, wait=3.0, cold=False)
        goto_tab(self.d, "discover")
        time.sleep(1.0)
        if not self._open_discover_list_entry(
            keywords=["小程序"],
            coord_key="mini_program_entry",
            fallback=(0.32, 0.85),
            success_markers=("最近使用", "我的小程序", "搜索", "常用"),
        ):
            return False
        time.sleep(2.0)
        return self._search_and_launch_game(
            game_name,
            launch_keywords=[game_name, "进入", "打开"],
        )

    def _open_via_global_search(self, game_name: str) -> bool:
        """顶部搜索 / SearchHelper 打开小游戏。"""
        try:
            from core.search_helper import SearchHelper

            helper = SearchHelper(self.d, account_id=self.account_id)
            if not helper.search(game_name):
                # 发现页右上角搜索图标兜底
                start_wechat(self.d, wait=2.0, cold=False)
                goto_tab(self.d, "discover")
                time.sleep(0.8)
                if not self._ocr_click_any(["搜索"], y_min=0.02, y_max=0.12):
                    click_ratio(self.d, 0.82, 0.055)
                time.sleep(0.8)
                try:
                    self.d.set_input_ime(True)
                    self.d.send_keys(game_name)
                    self.d.set_input_ime(False)
                    self.d.press("enter")
                    time.sleep(2.0)
                except Exception:
                    return False

            time.sleep(1.5)
            # 优先点小游戏/小程序分区结果
            if self._ocr_click_any(
                [game_name, "小游戏", "小程序"],
                y_min=0.12,
                y_max=0.85,
            ):
                time.sleep(2.5)
                self._ocr_click_any(
                    ["立即玩", "开始游戏", "进入", "打开"],
                    y_min=0.35,
                    y_max=0.95,
                )
                time.sleep(2.0)
                return self._ensure_game_playable()
            return False
        except Exception as e:
            logger.debug(f"[{self.account_id}] 全局搜索打开小游戏失败: {e}")
            return False

    def _search_and_launch_game(
        self,
        game_name: str,
        launch_keywords: Optional[list[str]] = None,
    ) -> bool:
        launch_keywords = launch_keywords or [
            "立即玩",
            "开始游戏",
            "进入",
            game_name,
        ]
        searched = False
        if self._ocr_click_any(["搜索", "搜一搜"], y_min=0.02, y_max=0.28):
            time.sleep(0.7)
            try:
                self.d.set_input_ime(True)
                self.d.send_keys(game_name)
                self.d.set_input_ime(False)
                self.d.press("enter")
                searched = True
                time.sleep(2.0)
            except Exception as e:
                logger.debug(f"[{self.account_id}] 小游戏搜索输入失败: {e}")

        if not searched:
            return False

        if self._ocr_click_any(launch_keywords, y_min=0.12, y_max=0.85):
            time.sleep(2.5)
            return self._ensure_game_playable()

        # 点第一条结果后再找「立即玩」
        click_ratio(self.d, 0.42, 0.28)
        time.sleep(2.0)
        self._ocr_click_any(["立即玩", "开始游戏", "进入", "打开"], y_min=0.35, y_max=0.95)
        time.sleep(2.0)
        return self._ensure_game_playable()

    def _browse_list_for_game(self, game_name: str) -> bool:
        for _ in range(5):
            if self._ocr_click_any(
                [game_name, "立即玩"],
                y_min=0.18,
                y_max=0.92,
            ):
                time.sleep(2.0)
                self._ocr_click_any(["立即玩", "开始游戏"], y_min=0.35, y_max=0.95)
                time.sleep(1.5)
                if self._ensure_game_playable():
                    return True
            self.d.swipe(
                int(self.w * 0.55),
                int(self.h * 0.75),
                int(self.w * 0.55),
                int(self.h * 0.35),
                duration=0.35,
            )
            time.sleep(0.8)
        return False

    def _open_discover_list_entry(
        self,
        keywords: list[str],
        coord_key: str,
        fallback: tuple[float, float],
        success_markers: tuple[str, ...] = (),
    ) -> bool:
        """
        发现页列表入口：OCR 优先；找不到则上滑再试；最后百分比坐标。
        新版发现页项较多，「游戏/小程序」常在中下部。
        若提供 success_markers，点后必须命中其一，避免旧坐标误进其它页。
        """
        for attempt in range(4):
            if self._ocr_click_any(keywords, y_min=0.12, y_max=0.92):
                time.sleep(1.2)
                if not success_markers or self._ocr_has_any(list(success_markers)):
                    return True
                # 误点，退回发现页再试
                self.d.press("back")
                time.sleep(0.6)
                goto_tab(self.d, "discover")
                time.sleep(0.6)
            if attempt < 3:
                self.d.swipe(
                    int(self.w * 0.5),
                    int(self.h * 0.72),
                    int(self.w * 0.5),
                    int(self.h * 0.35),
                    duration=0.35,
                )
                time.sleep(0.7)

        coord = get_coord(self.d, coord_key) or fallback
        click_ratio(self.d, coord[0], coord[1])
        time.sleep(1.5)
        if success_markers:
            if self._ocr_has_any(list(success_markers)):
                return True
            # 坐标可能过时：仍在发现页则再 OCR 一次；否则回退
            blob_ok = self._ocr_has_any(["朋友圈", "视频号", "扫一扫"])
            if blob_ok and self._ocr_click_any(keywords, y_min=0.12, y_max=0.92):
                time.sleep(1.2)
                return (not success_markers) or self._ocr_has_any(list(success_markers))
            self.d.press("back")
            time.sleep(0.4)
            return False
        return True

    def _looks_like_game_opened(self, game_name: str) -> bool:
        """粗判已离开发现/游戏中心列表，进入可玩小游戏界面。"""
        time.sleep(0.4)
        blob = self._ocr_screen_blob(force=True)
        if self._is_privacy_or_policy_page(blob):
            return False
        if self._is_privacy_consent_dialog(blob):
            return False
        if self._is_game_life_auth_page(blob):
            return False
        if self._is_game_life_page(blob):
            return False
        if self._is_wechat_main_tabs(blob) or self._looks_like_chat_list(blob):
            return False
        # 仍在发现页
        if "朋友圈" in blob and "视频号" in blob:
            return False
        # 仍在游戏中心列表/圈子
        if any(k in blob for k in ("找游戏", "今日精选", "在玩", "更多圈子")) and (
            "立即玩" in blob or "关注" in blob
        ):
            return False
        positive = (
            game_name,
            "开始游戏",
            "再玩一次",
            "重新开始",
            "再来一局",
            "得分",
            "本局",
            "排行",
            "跳一跳",
        )
        if any(p and p in blob for p in positive):
            return True
        # 负向：明显非游戏运行页
        negative = (
            "你的权益",
            "隐私保护",
            "隐私政策",
            "发起群聊",
            "通讯录",
            "收付款",
            "朋友圈",
            "视频号",
        )
        if any(k in blob for k in negative):
            return False
        # 已离开游戏中心导航，且无明显非游戏文案 → 视为已进入
        if not any(k in blob for k in ("找游戏", "在玩", "今日精选", "更多圈子")):
            return True
        return False

    def _is_privacy_or_policy_page(self, blob: str = "") -> bool:
        """小程序隐私保护全文页（非可玩界面；同意弹层不算）。"""
        text = blob or self._ocr_screen_blob()
        # 全文页特有章节（弹层通常只有「隐私保护指引」链接）
        if any(
            k in text
            for k in (
                "你的权益",
                "收集的信息",
                "如何管理你授权",
                "信息的用途",
                "开发者处理的信息",
            )
        ):
            return True
        # 标题全文：有「小程序隐私保护」但没有同意/拒绝按钮 → 全文页
        if "小程序隐私保护" in text:
            if any(k in text for k in ("同意并继续", "允许", "拒绝", "不同意")):
                # 弹层链接文案也含「小程序隐私保护指引」
                return False
            return True
        if "隐私政策" in text and "同意并继续" not in text and "允许" not in text:
            if any(k in text for k in ("个人信息", "收集", "存储")):
                return True
        return False

    def _is_privacy_consent_dialog(self, blob: str = "") -> bool:
        """开局隐私/权限同意弹层（有同意/允许按钮，非全文页）。"""
        text = blob or self._ocr_screen_blob()
        if self._is_privacy_or_policy_page(text):
            return False
        has_btn = any(k in text for k in ("同意并继续", "允许", "同意", "拒绝"))
        has_hint = any(
            k in text for k in ("隐私", "权限", "获取你的", "使用你的", "用户协议")
        )
        return has_btn and has_hint

    def _is_game_life_auth_page(self, blob: str = "") -> bool:
        """游戏人生「展示游戏实力」授权弹窗。"""
        text = blob or self._ocr_screen_blob()
        return any(
            k in text
            for k in (
                "开启授权",
                "暂不展示",
                "游戏实力",
                "游戏成分",
            )
        )

    def _looks_like_chat_list(self, blob: str = "") -> bool:
        """微信会话列表（有标题「微信」且底部 Tab）。"""
        text = blob or self._ocr_screen_blob()
        if "发起群聊" in text or "面对面建群" in text:
            return False
        title = "微信" in text
        tabs = ("通讯录" in text and "发现" in text)
        return bool(title and tabs)

    def _is_wechat_main_tabs(self, blob: str = "") -> bool:
        """底部主 Tab 可见（会话/通讯录/发现/我）。"""
        text = blob or self._ocr_screen_blob()
        tab_hits = sum(
            1 for k in ("微信", "通讯录", "发现", "我") if k in text
        )
        return tab_hits >= 2 or ("通讯录" in text and "发现" in text)

    def _dismiss_plus_menu_if_open(self) -> None:
        """若误开首页右上角「+」菜单则关掉（勿用「扫一扫」：发现页列表也有）。"""
        blob = self._ocr_screen_blob(force=True)
        # 「发起群聊/面对面建群」基本只出现在「+」弹层
        if "发起群聊" in blob or "面对面建群" in blob:
            logger.info(f"[{self.account_id}] 检测到「+」菜单，按返回关闭")
            self.d.press("back")
            time.sleep(0.4)
            return
        if "添加朋友" in blob and "收付款" in blob:
            logger.info(f"[{self.account_id}] 检测到「+」菜单，按返回关闭")
            self.d.press("back")
            time.sleep(0.4)

    def _click_consent_agree(self) -> bool:
        """点同意/允许，避开「隐私保护指引」链接文案。"""
        # 优先长文案按钮，且限制在下半屏中部偏右（按钮区）
        for keys in (
            ["同意并继续"],
            ["允许"],
            ["同意"],
            ["我知道了"],
        ):
            if self._ocr_click_any_boxed(
                keys,
                y_min=0.62,
                y_max=0.98,
                x_min=0.35,
                x_max=0.95,
                avoid_substrings=("隐私", "指引", "政策", "协议"),
            ):
                return True
        return False

    def _ensure_game_playable(self) -> bool:
        """
        处理开局弹层与隐私页，直到进入可玩界面。
        隐私全文页只按返回；同意弹层只点下半区按钮，避免点进政策链接。
        """
        for _ in range(8):
            blob = self._ocr_screen_blob(force=True)

            if self._is_privacy_or_policy_page(blob):
                logger.info(f"[{self.account_id}] 检测到隐私全文页，返回")
                self.d.press("back")
                time.sleep(0.8)
                continue

            if self._is_privacy_consent_dialog(blob):
                logger.info(f"[{self.account_id}] 检测到隐私同意弹层，点击同意")
                if not self._click_consent_agree():
                    # 点不到则不要乱点链接，返回重试下一个游戏
                    return False
                time.sleep(1.2)
                continue

            if self._is_game_life_auth_page(blob):
                self._ocr_click_any(["暂不展示"], y_min=0.55, y_max=0.95)
                time.sleep(0.6)
                continue

            self._dismiss_game_overlays()
            blob2 = self._ocr_screen_blob(force=True)
            if (
                self._is_privacy_or_policy_page(blob2)
                or self._is_privacy_consent_dialog(blob2)
                or self._is_game_life_auth_page(blob2)
            ):
                continue
            if self._looks_like_game_opened(""):
                return True
            time.sleep(0.5)
        return False

    def _ensure_wechat_home(self, close_capsule: bool = True) -> None:
        """从嵌套小程序/游戏页退回微信主界面（默认落到会话 Tab）。"""
        blob = self._ocr_screen_blob(force=True)
        already_main = self._is_wechat_main_tabs(blob) or self._looks_like_chat_list(
            blob
        )
        # 已在主界面时禁止点右上角：那是「+」不是小程序关闭
        if close_capsule and not already_main:
            self._close_miniprogram_capsule()
        if not already_main:
            for _ in range(4):
                blob = self._ocr_screen_blob(force=True)
                if self._is_wechat_main_tabs(blob) or self._looks_like_chat_list(blob):
                    break
                if self._is_game_life_auth_page(blob):
                    self._ocr_click_any(["暂不展示"], y_min=0.55, y_max=0.95)
                    time.sleep(0.4)
                    continue
                if self._is_privacy_or_policy_page(blob):
                    self.d.press("back")
                    time.sleep(0.4)
                    continue
                self.d.press("back")
                time.sleep(0.45)
        self._dismiss_plus_menu_if_open()
        try:
            start_wechat(self.d, wait=1.5, cold=False)
            goto_tab(self.d, "wechat")
        except Exception:
            pass
        self._dismiss_plus_menu_if_open()

    def _return_to_wechat_home(self) -> None:
        """小游戏结束后强制回到微信会话首页。"""
        for _ in range(3):
            blob = self._ocr_screen_blob(force=True)
            if self._is_game_life_auth_page(blob):
                if not self._ocr_click_any(["暂不展示"], y_min=0.55, y_max=0.95):
                    self.d.press("back")
                time.sleep(0.5)
                continue
            if self._is_privacy_or_policy_page(blob):
                self.d.press("back")
                time.sleep(0.5)
                continue
            break

        self._escape_nested_to_main(max_backs=8)
        self._dismiss_plus_menu_if_open()
        try:
            start_wechat(self.d, wait=2.0, cold=False)
            goto_tab(self.d, "wechat")
            time.sleep(0.6)
        except Exception:
            try:
                goto_tab(self.d, "wechat")
            except Exception:
                pass
        self._dismiss_plus_menu_if_open()
        # 仍在游戏人生则再退一轮
        blob = self._ocr_screen_blob(force=True)
        if self._is_game_life_page(blob) or not (
            self._is_wechat_main_tabs(blob) or self._looks_like_chat_list(blob)
        ):
            self._escape_nested_to_main(max_backs=6)
            try:
                start_wechat(self.d, wait=1.5, cold=False)
                goto_tab(self.d, "wechat")
            except Exception:
                pass
            self._dismiss_plus_menu_if_open()

    def _close_miniprogram_capsule(self) -> None:
        """点小程序右上角关闭（胶囊 X）。主界面/会话列表/游戏人生禁止乱点（会点到「+」或设置）。"""
        blob = self._ocr_screen_blob(force=True)
        if self._is_wechat_main_tabs(blob) or self._looks_like_chat_list(blob):
            logger.debug(f"[{self.account_id}] 主界面，跳过关胶囊（防误点+）")
            return
        if "发起群聊" in blob or "面对面建群" in blob:
            self.d.press("back")
            time.sleep(0.3)
            return
        # 游戏人生有返回箭头，优先 back，避免点到右上角设置
        if self._is_game_life_page(blob):
            self.d.press("back")
            time.sleep(0.45)
            return
        for rx, ry in ((0.94, 0.052), (0.97, 0.055)):
            try:
                click_ratio(self.d, rx, ry)
                time.sleep(0.45)
            except Exception:
                pass
            self._dismiss_plus_menu_if_open()
            blob2 = self._ocr_screen_blob(force=True)
            if self._is_wechat_main_tabs(blob2) or self._looks_like_chat_list(blob2):
                return
            if self._is_privacy_or_policy_page(blob2) or self._is_game_life_page(blob2):
                self.d.press("back")
                time.sleep(0.4)
                return
            break

    def _dismiss_game_overlays(self) -> None:
        """关闭开局弹层 / 权限 / 引导。同意类只点下半屏按钮，避免点进隐私链接。"""
        for _ in range(3):
            blob = self._ocr_screen_blob(force=True)
            if self._is_privacy_or_policy_page(blob):
                self.d.press("back")
                time.sleep(0.6)
                break
            if self._is_privacy_consent_dialog(blob):
                clicked = self._click_consent_agree()
            else:
                # 广告「进入小游戏」不要点，优先关引导/点本局开始
                clicked = self._ocr_click_any_boxed(
                    [
                        "开始游戏",
                        "我知道了",
                        "跳过",
                        "关闭",
                        "确认",
                        "暂不展示",
                    ],
                    y_min=0.40,
                    y_max=0.95,
                    x_min=0.10,
                    x_max=0.90,
                    avoid_substrings=("隐私", "指引", "政策", "广告"),
                )
            if not clicked:
                clicked = self._ocr_click_any_boxed(
                    ["开始"],
                    y_min=0.58,
                    y_max=0.95,
                    x_min=0.25,
                    x_max=0.85,
                    avoid_substrings=("隐私",),
                )
            if not clicked:
                break
            time.sleep(0.8)

    def _play_tiaoyitiao(self, end_ts: float) -> None:
        """跳一跳：屏幕下半区长按蓄力跳跃。"""
        logger.info(f"[{self.account_id}] 跳一跳拟人游玩中…")
        jumps = 0
        while time.time() < end_ts:
            if jumps == 0 or jumps % 3 == 0:
                blob = self._ocr_screen_blob(force=True)
                if self._is_privacy_or_policy_page(blob) or self._is_privacy_consent_dialog(
                    blob
                ):
                    if not self._ensure_game_playable():
                        logger.warning(
                            f"[{self.account_id}] 跳一跳中误入隐私页，提前结束"
                        )
                        return
            # 结束后重新开局
            if jumps > 0 and jumps % random.randint(6, 12) == 0:
                self._ocr_click_any(
                    ["再玩一次", "重新开始", "再来一局", "开始游戏"],
                    y_min=0.40,
                    y_max=0.95,
                )
                time.sleep(random.uniform(0.8, 1.5))

            x = int(self.w * random.uniform(0.42, 0.58))
            y = int(self.h * random.uniform(0.58, 0.78))
            # 蓄力时长：短跳~中跳，偶发长跳
            if random.random() < 0.12:
                hold_ms = int(random.uniform(900, 1500))
            else:
                hold_ms = int(random.uniform(280, 950))
            self._hold_tap(x, y, hold_ms)
            jumps += 1
            time.sleep(random.uniform(1.1, 2.6))
            # 偶发停顿看成绩
            if random.random() < 0.08:
                time.sleep(random.uniform(1.5, 3.5))
        logger.info(f"[{self.account_id}] 跳一跳结束，约 {jumps} 次跳跃")

    def _play_generic_mini_game(self, end_ts: float) -> None:
        """通用小游戏：随机轻点 + 短滑；禁止点隐私链接。"""
        logger.info(f"[{self.account_id}] 通用小游戏拟人游玩中…")
        tick = 0
        while time.time() < end_ts:
            tick += 1
            if tick == 1 or tick % 3 == 0:
                blob = self._ocr_screen_blob(force=True)
                if (
                    self._is_privacy_or_policy_page(blob)
                    or self._is_privacy_consent_dialog(blob)
                    or self._is_game_life_auth_page(blob)
                ):
                    if not self._ensure_game_playable():
                        logger.warning(
                            f"[{self.account_id}] 游玩中无法回到游戏界面，提前结束"
                        )
                        return
            action = random.random()
            if action < 0.55:
                click_ratio(
                    self.d,
                    random.uniform(0.25, 0.75),
                    random.uniform(0.40, 0.78),
                )
            elif action < 0.85:
                self.d.swipe(
                    int(self.w * random.uniform(0.3, 0.7)),
                    int(self.h * random.uniform(0.45, 0.7)),
                    int(self.w * random.uniform(0.3, 0.7)),
                    int(self.h * random.uniform(0.3, 0.55)),
                    duration=random.uniform(0.15, 0.4),
                )
            else:
                self._ocr_click_any_boxed(
                    ["继续", "再来一局", "再玩一次", "确定"],
                    y_min=0.50,
                    y_max=0.95,
                    x_min=0.20,
                    x_max=0.85,
                    avoid_substrings=("隐私", "同意", "指引"),
                )
            time.sleep(random.uniform(1.2, 3.5))

    def _hold_tap(self, x: int, y: int, hold_ms: int) -> None:
        """定长按压（跳一跳蓄力）；优先 adb swipe 同点。"""
        hold_ms = max(120, min(int(hold_ms), 2500))
        try:
            self.d.shell(f"input swipe {x} {y} {x} {y} {hold_ms}")
            return
        except Exception:
            pass
        try:
            self.d.swipe(x, y, x, y, duration=hold_ms / 1000.0)
        except Exception:
            try:
                self.d.long_click(x, y, duration=hold_ms / 1000.0)
            except Exception as e:
                logger.debug(f"[{self.account_id}] 长按失败: {e}")

    def _exit_nested(self, times: int = 3) -> None:
        for _ in range(times):
            try:
                self.d.press("back")
            except Exception:
                break
            time.sleep(0.4)

    def _ocr_click_any_boxed(
        self,
        keywords: list[str],
        y_min: float = 0.05,
        y_max: float = 0.95,
        x_min: float = 0.0,
        x_max: float = 1.0,
        avoid_substrings: tuple[str, ...] = (),
    ) -> bool:
        """OCR 点击，限制矩形区域，并可避开含敏感子串的匹配（如隐私链接）。"""
        reader = self._ensure_ocr()
        if reader is None:
            return False
        try:
            img = np.array(self.d.screenshot(format="pillow"))
            gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
            if self._clahe is not None:
                gray = self._clahe.apply(gray)
            results = reader.readtext(cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR))
            y0, y1 = int(self.h * y_min), int(self.h * y_max)
            x0, x1 = int(self.w * x_min), int(self.w * x_max)
            best = None  # (score, cx, cy)
            for bbox, text, conf in results:
                if conf < 0.35:
                    continue
                t = (text or "").strip()
                if not t:
                    continue
                if avoid_substrings and any(a in t for a in avoid_substrings):
                    continue
                cy = int((bbox[0][1] + bbox[2][1]) / 2)
                cx = int((bbox[0][0] + bbox[2][0]) / 2)
                if cy < y0 or cy > y1 or cx < x0 or cx > x1:
                    continue
                hit_score = 0
                for k in keywords:
                    if not k:
                        continue
                    if t == k:
                        hit_score = len(k) + 5
                        break
                    if k in t:
                        hit_score = len(k)
                        break
                    if t in k and len(t) >= max(2, len(k) // 2):
                        hit_score = len(t)
                        break
                if hit_score <= 0:
                    continue
                if best is None or hit_score > best[0] or (
                    hit_score == best[0] and cy < best[2]
                ):
                    best = (hit_score, cx, cy)
            if best is None:
                return False
            self.d.click(best[1], best[2])
            self._invalidate_ocr_cache()
            time.sleep(0.5)
            return True
        except Exception as e:
            logger.debug(f"[{self.account_id}] _ocr_click_any_boxed 失败: {e}")
            return False

    # ================================================================
    # 多轮深聊
    # ================================================================

    def deep_chat(
        self,
        contact: str,
        messages: list[str],
        total_seconds: int = 300,
    ) -> bool:
        """
        打开会话后分多轮发送，总停留接近 total_seconds。
        """
        if not contact or not messages:
            return False
        logger.info(
            f"[{self.account_id}] 深聊: {contact} "
            f"rounds={len(messages)} ~{total_seconds}s"
        )
        from core.message_sender import MessageSender

        sender = MessageSender(self.d, account_id=self.account_id)
        # 先发第一条打开会话
        if not sender.send(contact=contact, message=messages[0]):
            return False

        remaining = messages[1:]
        if not remaining:
            time.sleep(min(30, total_seconds))
            return True

        interval = max(20.0, (total_seconds - 30) / max(1, len(remaining)))
        for msg in remaining:
            jitter = random.uniform(0.7, 1.3) * interval
            time.sleep(jitter)
            try:
                # 已在聊天页则直接输入
                self._send_in_current_chat(msg)
            except Exception:
                sender.send(contact=contact, message=msg)
        return True

    def _send_in_current_chat(self, message: str):
        """假定已在聊天页，输入并发送。"""
        from config.device_profiles import get_extra

        input_fb = tuple(get_extra(self.d, "msg_input", (0.50, 0.965)))
        send_fb = tuple(get_extra(self.d, "msg_send", (0.90, 0.965)))
        click_ratio(self.d, *input_fb)
        time.sleep(0.4)
        self.d.set_input_ime(True)
        time.sleep(0.2)
        self.d.send_keys(message)
        self.d.set_input_ime(False)
        time.sleep(0.4)
        if not self._ocr_click_any(["发送"], y_min=0.88, y_max=0.99):
            click_ratio(self.d, *send_fb)

    # ================================================================
    # 视频号评论（尽力而为）
    # ================================================================

    def comment_channel(self, text: str = "", comment_fn=None) -> bool:
        """
        在当前/进入视频号后对当前视频发评论。

        Args:
            text: 指定评论文案；为空时 OCR 视频文案后经 comment_fn 生成
            comment_fn: ``(video_context, image_jpeg) -> comment``，text 为空时使用
        """
        try:
            from core.channels_browser import ChannelsBrowser

            browser = ChannelsBrowser(self.d, account_id=self.account_id)
            browser._enter_channels()
            time.sleep(2.0)
            body = (text or "").strip()
            if not body:
                body = browser._compose_comment(
                    comment_fn,
                    ["不错", "学到了", "哈哈哈", "支持", "有意思", "太真实了"],
                )
            logger.info(f"[{self.account_id}] 视频号评论: {body[:20]}")
            return browser._comment_current(body)
        except Exception as e:
            logger.error(f"[{self.account_id}] 视频号评论失败: {e}")
            return False

    # ================================================================
    # OCR 辅助
    # ================================================================

    def _invalidate_ocr_cache(self) -> None:
        self._ocr_blob = ""
        self._ocr_blob_ts = 0.0

    def _normalize_friend_add_ocr_text(self, text: str) -> str:
        """
        把“添加到通讯录”相关 OCR 识别结果做容错规范化：
        - 繁体：通訊/通訊錄/錄 -> 通讯/通讯录/录
        - 常见同形字符：訊 -> 讯、電話/電話號等（本函数只做与加好友相关的轻量映射）
        """
        t = (text or "").strip()
        # 空格会影响子串匹配
        t = re.sub(r"\s+", "", t)
        # 繁体/异体映射（只针对和“添加到通讯录”相关的字形差异）
        t = t.replace("通訊", "通讯")
        t = t.replace("通訊錄", "通讯录")
        t = t.replace("通訊錄", "通讯录")
        t = t.replace("通訊录", "通讯录")  # 兜底：混合大小写/简繁
        t = t.replace("錄", "录")
        t = t.replace("訊", "讯")
        return t

    def _ocr_screen_blob(self, force: bool = False) -> str:
        """全屏 OCR 一次，短时复用，避免同页反复扫屏。"""
        now = time.time()
        if (
            (not force)
            and self._ocr_blob
            and (now - self._ocr_blob_ts) < self._ocr_blob_ttl
        ):
            return self._ocr_blob
        try:
            reader = self._ensure_ocr()
            if reader is None:
                return ""
            img = np.array(self.d.screenshot(format="pillow"))
            gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
            enhanced = self._clahe.apply(gray) if self._clahe is not None else gray
            results = reader.readtext(cv2.cvtColor(enhanced, cv2.COLOR_GRAY2BGR))
            blob = " ".join(
                self._normalize_friend_add_ocr_text(str(t))
                for _, t, c in results
                if c > 0.25
            )
            self._ocr_blob = blob
            self._ocr_blob_ts = now
            return blob
        except Exception:
            return self._ocr_blob or ""

    def _ensure_ocr(self):
        if self._ocr is None:
            from utils.ocr_utils import create_easyocr_reader

            self._ocr = create_easyocr_reader()
            self._clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        return self._ocr

    def _ocr_click_any(
        self,
        keywords: list[str],
        y_min: float = 0.05,
        y_max: float = 0.95,
    ) -> bool:
        reader = self._ensure_ocr()

        def enhance(gray):
            return self._clahe.apply(gray)

        ok = ocr_find_and_click(
            self.d,
            reader,
            keywords,
            y_min_ratio=y_min,
            y_max_ratio=y_max,
            enhance=enhance,
        )
        if ok:
            self._invalidate_ocr_cache()
        return ok
