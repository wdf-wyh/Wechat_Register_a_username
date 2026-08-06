"""
设备 & 账号健康检查模块
========================

两层检查:
  1. DeviceHealthChecker — 设备基础设施 (ADB/ATX/截图/Tab/核心页面)
  2. HealthChecker — 账号风险检测 (朋友圈可见/消息延迟/验证频率)

## DeviceHealthChecker (设备层)

检查项 (12项):
  - ADB 连接、ATX Agent、设备信息、微信安装、冷启动
  - 微信前台、Tab切换、截图、朋友圈/搜索/视频号页面、存储

.. code-block:: python

    from monitor.health_check import DeviceHealthChecker
    checker = DeviceHealthChecker()
    report = checker.run_all()  # → dict + JSON报告

## HealthChecker (账号层)

检查项:
  - 朋友圈是否可见（被降权则"仅自己可见"）
  - 消息发送是否延迟（> 5s = 一级预警）
  - 是否频繁出现滑块验证

.. code-block:: python

    from monitor.health_check import HealthChecker, AccountState
    checker = HealthChecker(wc, db)
    result = checker.check_all()
"""

import time
from enum import Enum
from typing import Optional

from core.wechat_control import WeChatControl
from storage.db import Database
from utils.logger import get_logger

logger = get_logger("health_check")


class AccountState(Enum):
    NORMAL = "normal"           # 正常养号
    WARNING = "warning"         # 出现预警信号，减少操作
    COOLDOWN = "cooldown"       # 冷却期，纯消费模式
    SUSPENDED = "suspended"     # 暂停自动化，需人工介入
    MATURE = "mature"           # 成熟，可用于测试


class HealthChecker:
    """
    账号健康检查器。

    对单个账号执行多项功能检测，综合评估风险等级。
    """

    def __init__(self, wechat: WeChatControl, db: Database):
        self.wc = wechat
        self.db = db

    # ================================================================
    # 单项检查
    # ================================================================

    def _check_moments_visibility(self) -> Optional[bool]:
        """
        检查朋友圈可见性。

        Returns:
            True=正常, False=可能被降权, None=无法检测
        """
        try:
            if not self.wc.open_moments():
                return None
            # 检查是否有内容（如果有"暂无朋友圈"或正常内容=正常）
            # 简化逻辑：只要能打开朋友圈页面且没有异常提示就认为正常
            return True
        except Exception as e:
            logger.warning(f"朋友圈可见性检查异常: {e}")
            return None

    def _check_message_delay(self) -> Optional[float]:
        """
        检测消息发送延迟。

        发送一条测试消息并计时。

        Returns:
            延迟秒数，None 表示无法检测
        """
        try:
            start = time.time()
            # 尝试在当前聊天窗口发送一条短消息
            self.wc.send_message("test")
            delay = time.time() - start
            return delay
        except Exception as e:
            logger.warning(f"消息延迟检查异常: {e}")
            return None

    def _check_captcha_frequency(self) -> int:
        """
        检查近24小时内滑块验证出现次数。

        通过检查最近的截图和日志记录来判断。

        Returns:
            估计的验证次数
        """
        # 从日志中查找滑块验证记录
        logs = self.db.get_action_logs(
            self.wc.account_id, limit=200,
            date=time.strftime("%Y-%m-%d"),
        )
        captcha_count = sum(
            1 for log in logs
            if "captcha" in str(log.get("error_msg", "")).lower()
            or "滑块" in str(log.get("error_msg", ""))
        )
        return captcha_count

    # ================================================================
    # 综合检查
    # ================================================================

    def check_all(self) -> dict:
        """
        执行全部健康检查。

        Returns:
            {
                "moments_visible": True/False/None,
                "message_delay_ms": float/None,
                "captcha_count": int,
                "risk_score": 0.0-1.0,
                "state": "normal"/"warning"/"cooldown"/"suspended",
                "suggestions": ["..."]
            }
        """
        account_id = self.wc.account_id
        logger.debug(f"开始健康检查: {account_id}")

        # 各项检查
        moments_ok = self._check_moments_visibility()
        message_delay = self._check_message_delay()
        captcha_count = self._check_captcha_frequency()

        # 风险评估
        risk_score = 0.0
        issues = []

        if moments_ok is False:
            risk_score += 0.3
            issues.append("朋友圈可能被降权（仅自己可见）")

        if message_delay is not None and message_delay > 5.0:
            risk_score += 0.3
            issues.append(f"消息发送延迟过高 ({message_delay:.1f}s > 5s)")

        if captcha_count >= 3:
            risk_score += 0.2
            issues.append(f"滑块验证频繁 ({captcha_count} 次/天)")

        # 状态判定
        if risk_score >= 0.6:
            state = AccountState.SUSPENDED
        elif risk_score >= 0.4:
            state = AccountState.COOLDOWN
        elif risk_score >= 0.2:
            state = AccountState.WARNING
        else:
            state = AccountState.NORMAL

        result = {
            "moments_visible": moments_ok,
            "message_delay_ms": message_delay * 1000 if message_delay else None,
            "captcha_count": captcha_count,
            "risk_score": risk_score,
            "state": state.value,
            "suggestions": issues,
        }

        # 持久化
        self.db.record_health_check(
            account_id=account_id,
            moments_visible=1 if moments_ok else (0 if moments_ok is False else None),
            message_delay_ms=message_delay * 1000 if message_delay else None,
            captcha_count=captcha_count,
            risk_score=risk_score,
            state=state.value,
            notes="; ".join(issues) if issues else None,
        )

        # 更新账号状态
        if state != AccountState.NORMAL:
            self.db.set_account_state(account_id, state.value)

        logger.info(
            f"健康检查完成: {account_id} → {state.value} "
            f"(risk={risk_score:.2f})"
        )
        return result

    # ================================================================
    # 快速自检（轻量级）
    # ================================================================

    def quick_check(self) -> bool:
        """
        快速自检 — 仅检查微信是否在前台且能正常响应。

        用于在每次操作前做前置检查，开销很低。

        Returns:
            True 表示基本正常
        """
        try:
            current = self.wc.d.app_current()
            if current.get("package") != "com.tencent.mm":
                logger.warning(f"[{self.wc.account_id}] 微信不在前台")
                return False
            return True
        except Exception:
            return False


# ================================================================
# AccountHealthChecker — 账号风险检测 (轻量级，不依赖 WeChatControl/DB)
# ================================================================

class AccountHealthChecker:
    """
    账号健康检查器 — 检测账户是否被限制/降权。

    轻量级设计，仅需 uiautomator2 device 实例，
    复用 core.search_helper 和 core.message_sender。

    检查项:
      1. 微信冷启动到首页
      2. 朋友圈可见性 (OCR 检测降权关键词)
      3. 搜索联系人 (复用 SearchHelper)
      4. 发送测试消息 (复用 MessageSender)
      5. 滑块/验证码弹出检测
      6. 功能限制关键词检测
    """

    def __init__(self, d, contact: str = "gas"):
        self.d = d
        self.w, self.h = d.info['displayWidth'], d.info['displayHeight']
        self.contact = contact

    def run_all(self) -> dict:
        """
        执行全部检查，返回报告。

        Returns:
            {"time", "contact", "checks": [...], "passed", "total",
             "risk_score": 0.0-1.0, "state": "NORMAL"|"WARNING"|"COOLDOWN"|"SUSPENDED"}
        """
        import time as _time

        checks = [
            ("微信启动+首页",      self._wechat_home),
            ("朋友圈可见性",        self._moments_visibility),
            ("搜索功能",            self._search),
            ("进入聊天+发消息",     self._send_message),
            ("滑块/验证码检出",     self._captcha_check),
            ("功能限制检出",        self._restriction_check),
        ]

        results = []
        for name, fn in checks:
            try:
                status, detail, risk = fn()
                results.append((name, bool(status), str(detail or ""), risk))
            except Exception as e:
                results.append((name, False, str(e)[:80], 0.0))

        total_risk = sum(r for _, _, _, r in results)
        risk_score = min(total_risk, 1.0)

        if risk_score >= 0.6:   state = "SUSPENDED"
        elif risk_score >= 0.4: state = "COOLDOWN"
        elif risk_score >= 0.2: state = "WARNING"
        else:                   state = "NORMAL"

        passed = sum(1 for _, s, _, _ in results if s)

        report = {
            "time": __import__("datetime").datetime.now().isoformat(),
            "contact": self.contact,
            "checks": [{"name": n, "status": s, "detail": d, "risk": r}
                        for n, s, d, r in results],
            "passed": passed, "total": len(checks),
            "risk_score": risk_score, "state": state,
        }

        logger.info(f"账号健康检查: {passed}/{len(checks)} risk={risk_score:.2f} {state}")
        return report

    # ---- 检查项: (status, detail, risk) ----

    def _wechat_home(self):
        d, w, h = self.d, self.w, self.h
        d.app_stop("com.tencent.mm"); __import__("time").sleep(1)
        d.app_start("com.tencent.mm"); __import__("time").sleep(5)
        d.click(int(w * 0.125), int(h * 0.955)); __import__("time").sleep(1)
        info = d.app_info("com.tencent.mm")
        return info is not None, "OK", 0.0

    def _moments_visibility(self):
        import cv2, numpy as np
        d, w, h = self.d, self.w, self.h
        d.click(int(w * 0.625), int(h * 0.955)); __import__("time").sleep(1)
        d.click(int(w * 0.32), int(h * 0.131)); __import__("time").sleep(2)
        img = np.array(d.screenshot(format="pillow"))
        gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
        clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8,8))
        results = self._ocr().readtext(cv2.cvtColor(clahe.apply(gray), cv2.COLOR_GRAY2BGR))
        for _, text, conf in results:
            if conf > 0.4 and any(kw in text for kw in ["仅自己可见","被限制","违规","封禁"]):
                return False, f"限制:{text}", 0.5
        return True, "可见", 0.0

    def _search(self):
        import time as _time
        from core.search_helper import SearchHelper
        if not SearchHelper(self.d).open_search():
            return False, "搜索页未打开", 0.2
        d = self.d
        d.click(int(self.w * 0.5), int(self.h * 0.045)); _time.sleep(0.5)
        d.set_input_ime(True); _time.sleep(0.3)
        d.send_keys(self.contact); _time.sleep(0.3)
        d.set_input_ime(False)
        d.press("enter"); _time.sleep(2)
        import cv2, numpy as np
        img = np.array(d.screenshot(format="pillow"))
        gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
        clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8,8))
        results = self._ocr().readtext(cv2.cvtColor(clahe.apply(gray), cv2.COLOR_GRAY2BGR))
        found = any(self.contact.lower() in t.lower() and c > 0.5
                     for _, t, c in results)
        return found, "找到" if found else "未找到", 0.0 if found else 0.1

    def _send_message(self):
        from core.message_sender import MessageSender
        ok = MessageSender(self.d).send(contact=self.contact, message="health_test")
        return ok, "发送成功" if ok else "失败", 0.0 if ok else 0.3

    def _captcha_check(self):
        import cv2, numpy as np
        img = np.array(self.d.screenshot(format="pillow"))
        gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
        clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8,8))
        results = self._ocr().readtext(cv2.cvtColor(clahe.apply(gray), cv2.COLOR_GRAY2BGR))
        for _, text, conf in results:
            if conf > 0.4 and any(kw in text for kw in ["滑块","验证","拼图","验证码"]):
                return False, f"{text}", 0.3
        return True, "未检出", 0.0

    def _restriction_check(self):
        import cv2, numpy as np
        img = np.array(self.d.screenshot(format="pillow"))
        gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
        clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8,8))
        results = self._ocr().readtext(cv2.cvtColor(clahe.apply(gray), cv2.COLOR_GRAY2BGR))
        for _, text, conf in results:
            if conf > 0.4 and any(kw in text for kw in ["功能受限","已被限制","违规","封号"]):
                return False, f"{text}", 0.5
        return True, "未检出", 0.0

    def _ocr(self):
        if not hasattr(self, '_ocr_instance'):
            from utils.ocr_utils import create_easyocr_reader
            self._ocr_instance = create_easyocr_reader()
        return self._ocr_instance


# ================================================================
# DeviceHealthChecker — 设备基础设施检查
# ================================================================

class DeviceHealthChecker:
    """
    设备 + 微信基础设施健康检查器。

    纯设备层面，不涉及账号状态。
    输出 JSON 格式报告供监控系统消费。
    """

    def __init__(self):
        self.d = None
        self.w, self.h = 0, 0
        self.checks = []

    # ================================================================
    # 公共接口
    # ================================================================

    def run_all(self) -> dict:
        """执行全部12项检查，返回报告。"""
        import time as _time
        import json as _json
        from pathlib import Path as _Path

        checks = [
            ("ADB连接",       self._adb_connect),
            ("ATX Agent",     self._atx_agent),
            ("设备信息",       self._device_info),
            ("微信安装",       self._wechat_installed),
            ("微信冷启动",     self._wechat_cold_start),
            ("微信前台状态",   self._wechat_foreground),
            ("Tab切换(4个)",   self._tab_switch),
            ("截图功能",       self._screenshot),
            ("朋友圈页面",     self._moments_page),
            ("搜索功能",       self._search),
            ("视频号页面",     self._channels_page),
            ("存储空间",       self._storage),
        ]

        self.checks = []
        for name, fn in checks:
            try:
                status, detail = fn()
                self.checks.append((name, bool(status), str(detail or "")))
            except Exception as e:
                self.checks.append((name, False, str(e)))

        report = {
            "time": __import__("datetime").datetime.now().isoformat(),
            "checks": [{"name": n, "status": s, "detail": d}
                        for n, s, d in self.checks],
            "passed": sum(1 for _, s, _ in self.checks if s),
            "total": len(self.checks),
        }

        logger.info(f"设备健康检查: {report['passed']}/{report['total']} 通过")
        return report

    # ================================================================
    # 检查项
    # ================================================================

    def _adb_connect(self):
        import uiautomator2 as u2
        d = u2.connect()
        d.screen_on()
        self.d = d
        serial = getattr(d, 'serial', None) or getattr(d, '_serial', "USB")
        return True, str(serial)

    def _atx_agent(self):
        if not self.d: return False, "无连接"
        info = self.d.info
        return bool(info and len(info) > 0), ""

    def _device_info(self):
        if not self.d: return False, ""
        info = self.d.info
        self.w = info.get('displayWidth', 0)
        self.h = info.get('displayHeight', 0)
        return True, f"{self.w}x{self.h} SDK{info.get('sdkInt','?')}"

    def _wechat_installed(self):
        if not self.d: return False, ""
        try:
            info = self.d.app_info("com.tencent.mm")
            return bool(info), f"v{info.get('versionName','?')}" if info else ""
        except Exception:
            return False, ""

    def _wechat_cold_start(self):
        if not self.d: return False, ""
        import time
        self.d.app_stop("com.tencent.mm")
        time.sleep(1)
        self.d.app_start("com.tencent.mm")
        time.sleep(3)
        info = self.d.app_info("com.tencent.mm")
        return info is not None, ""

    def _wechat_foreground(self):
        if not self.d: return False, ""
        import time
        self.d.app_start("com.tencent.mm")
        time.sleep(2)
        pkg = self.d.app_current().get("package", "")
        return pkg == "com.tencent.mm", pkg

    def _tab_switch(self):
        if not self.d or not self.w: return False, ""
        import time
        self.d.app_start("com.tencent.mm")
        time.sleep(2)
        for rx in [0.125, 0.375, 0.625, 0.875]:
            self.d.click(int(self.w * rx), int(self.h * 0.955))
            time.sleep(0.4)
        return True, ""

    def _screenshot(self):
        if not self.d: return False, ""
        try:
            from pathlib import Path
            img = self.d.screenshot(format="pillow")
            if img and img.size[0] > 0:
                Path("screenshots").mkdir(exist_ok=True)
                img.save("screenshots/health_check.png")
                return True, ""
        except Exception as e:
            return False, str(e)
        return False, "空截图"

    def _moments_page(self):
        if not self.d or not self.w: return False, ""
        import time, cv2, numpy as np
        self.d.click(int(self.w * 0.625), int(self.h * 0.955))
        time.sleep(1)
        self.d.click(int(self.w * 0.32), int(self.h * 0.131))
        time.sleep(2)
        img = np.array(self.d.screenshot(format="pillow"))
        gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
        top_right = gray[100:200, int(self.w*0.80):int(self.w*0.95)]
        return np.mean(top_right) < 245, ""

    def _search(self):
        if not self.d or not self.w: return False, ""
        import time, cv2, numpy as np
        self.d.click(int(self.w * 0.125), int(self.h * 0.955))
        time.sleep(0.5)
        self.d.click(1050, 150)
        time.sleep(1.5)
        img = np.array(self.d.screenshot(format="pillow"))
        gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
        return np.mean(gray[60:150, 50:int(self.w*0.85)]) < 230, ""

    def _channels_page(self):
        if not self.d or not self.w: return False, ""
        import time, cv2, numpy as np
        self.d.click(int(self.w * 0.625), int(self.h * 0.955))
        time.sleep(1)
        self.d.click(258, 582)
        time.sleep(2)
        img = np.array(self.d.screenshot(format="pillow"))
        gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
        return np.mean(gray[180:250, :]) < 240, ""

    def _storage(self):
        try:
            from pathlib import Path
            p = Path("screenshots")
            p.mkdir(exist_ok=True)
            t = p / ".health_write_test"
            t.write_text("ok")
            t.unlink()
            return True, ""
        except Exception as e:
            return False, str(e)
