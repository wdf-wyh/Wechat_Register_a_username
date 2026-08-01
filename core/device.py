"""
设备管理模块 — ADB 连接、设备发现、连接池维护、健康检查。

核心职责:
    - 自动发现 USB 连接的设备
    - 维持 uiautomator2 连接池
    - 定期健康检查（设备在线、ATX agent 存活、微信前台状态）
    - 设备断开自动重连

使用方式:
    dm = DeviceManager()
    dm.discover_and_connect_all()
    d = dm.get_device("ABCD1234")
"""

from typing import Optional

import uiautomator2 as u2

from config.settings import settings
from utils.adb_utils import ADBUtils
from utils.logger import get_logger

logger = get_logger("device")


class DeviceManager:
    """
    设备管理器 — 维护多台手机 uiautomator2 连接。

    连接池结构: { serial: u2.Device }
    """

    def __init__(self):
        self.devices: dict[str, u2.Device] = {}
        self._device_serial_to_account: dict[str, str] = {}

    # ================================================================
    # 设备发现与连接
    # ================================================================

    def discover_devices(self) -> list[str]:
        """
        通过 adb devices 发现所有连接的设备。

        Returns:
            在线设备序列号列表
        """
        devices = ADBUtils.list_devices()
        logger.info(f"发现 {len(devices)} 台在线设备: {devices}")
        return devices

    def connect_device(self, serial: str) -> u2.Device:
        """
        连接指定设备。

        Args:
            serial: 设备序列号（adb devices 中的标识）

        Returns:
            uiautomator2 设备对象

        Raises:
            ConnectionError: 连接失败
        """
        logger.info(f"正在连接设备 {serial} ...")
        last_err: Exception | None = None
        for attempt in range(1, 4):
            try:
                d = u2.connect(serial)
                # 验证 ATX agent 是否正常运行（偶发 RemoteDisconnected，重试）
                info = d.info
                if not info:
                    raise ConnectionError(f"设备 {serial} 返回空 info")
                logger.info(
                    f"设备 {serial} 连接成功: "
                    f"{info.get('productName', 'Unknown')} "
                    f"({info.get('displayWidth', '?')}x{info.get('displayHeight', '?')})"
                )
                self.devices[serial] = d
                return d
            except Exception as e:
                last_err = e
                logger.warning(f"连接设备 {serial} 第{attempt}次失败: {e}")
                import time
                time.sleep(1.5 * attempt)
        logger.error(f"连接设备 {serial} 失败: {last_err}")
        raise ConnectionError(f"无法连接设备 {serial}: {last_err}")

    def discover_and_connect_all(self) -> dict[str, u2.Device]:
        """
        自动发现并连接所有 USB 设备。

        Returns:
            {serial: u2.Device} 连接池
        """
        serials = self.discover_devices()
        for serial in serials:
            if serial not in self.devices:
                try:
                    self.connect_device(serial)
                except ConnectionError as e:
                    logger.warning(f"设备 {serial} 连接失败，跳过: {e}")
        return self.devices

    def get_device(self, serial: str) -> Optional[u2.Device]:
        """获取已连接的设备"""
        return self.devices.get(serial)

    def disconnect_device(self, serial: str):
        """主动断开设备连接"""
        if serial in self.devices:
            del self.devices[serial]
            logger.info(f"设备 {serial} 已从连接池移除")

    # ================================================================
    # 健康检查
    # ================================================================

    def health_check(self, serial: str) -> bool:
        """
        检查设备是否正常在线。

        Args:
            serial: 设备序列号

        Returns:
            True 表示设备健康
        """
        d = self.devices.get(serial)
        if not d:
            logger.debug(f"设备 {serial} 不在连接池中")
            return False

        # 先检查 ADB 连接
        if not ADBUtils.is_device_online(serial):
            logger.warning(f"设备 {serial} ADB 连接断开")
            return False

        # 再检查 ATX agent
        try:
            info = d.info
            if not info:
                logger.warning(f"设备 {serial} ATX agent 无响应")
                return False
            return True
        except Exception as e:
            logger.warning(f"设备 {serial} 健康检查异常: {e}")
            return False

    def health_check_all(self) -> dict[str, bool]:
        """
        对所有已连接设备执行健康检查。

        Returns:
            {serial: is_healthy}
        """
        results = {}
        for serial in list(self.devices.keys()):
            results[serial] = self.health_check(serial)
        unhealthy = [s for s, ok in results.items() if not ok]
        if unhealthy:
            logger.warning(f"不健康的设备: {unhealthy}")
        return results

    def reconnect_device(self, serial: str) -> bool:
        """
        尝试重连设备。

        Args:
            serial: 设备序列号

        Returns:
            重连是否成功
        """
        logger.info(f"尝试重连设备 {serial} ...")
        # 先移除旧连接
        if serial in self.devices:
            del self.devices[serial]
        try:
            self.connect_device(serial)
            return True
        except ConnectionError:
            return False

    # ================================================================
    # 微信前后台管理
    # ================================================================

    def ensure_wechat_foreground(self, serial: str) -> bool:
        """
        确保微信在前台运行（自动唤醒屏幕+解锁）。

        Args:
            serial: 设备序列号

        Returns:
            True 表示微信已在前台
        """
        d = self.devices.get(serial)
        if not d:
            logger.error(f"设备 {serial} 未连接，无法启动微信")
            return False

        try:
            from core.wechat_nav import start_wechat

            current = d.app_current()
            if current.get("package") == settings.WECHAT_PACKAGE:
                return True
            logger.info(f"设备 {serial}: 微信未在前台，正在启动...")
            return start_wechat(
                d, wait=settings.WECHAT_LAUNCH_WAIT, cold=False
            )
        except Exception as e:
            logger.error(f"设备 {serial} 启动微信失败: {e}")
            return False

    def close_wechat(self, serial: str):
        """停止微信"""
        d = self.devices.get(serial)
        if d:
            d.app_stop(settings.WECHAT_PACKAGE)
            logger.info(f"设备 {serial}: 微信已关闭")

    # ================================================================
    # 账号-设备绑定
    # ================================================================

    def bind_account(self, serial: str, account_id: str):
        """将设备序列号与账号 ID 绑定"""
        self._device_serial_to_account[serial] = account_id
        logger.info(f"设备 {serial} 已绑定账号 {account_id}")

    def get_bound_account(self, serial: str) -> Optional[str]:
        """获取设备绑定的账号 ID"""
        return self._device_serial_to_account.get(serial)

    # ================================================================
    # 状态查询
    # ================================================================

    @property
    def device_count(self) -> int:
        """当前已连接设备数"""
        return len(self.devices)

    def get_all_serials(self) -> list[str]:
        """获取所有已连接设备的序列号"""
        return list(self.devices.keys())

    def get_device_info(self, serial: str) -> Optional[dict]:
        """获取设备详细信息"""
        d = self.devices.get(serial)
        if not d:
            return None
        try:
            return {
                "serial": serial,
                "info": d.info,
                "current_app": d.app_current(),
                "account_id": self.get_bound_account(serial),
            }
        except Exception as e:
            logger.warning(f"获取设备 {serial} 信息失败: {e}")
            return {"serial": serial, "error": str(e)}
