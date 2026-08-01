"""
ADB 工具模块 — 封装常用 ADB 命令，用于设备诊断和管理。

使用方式:
    from utils.adb_utils import ADBUtils
    devices = ADBUtils.list_devices()
"""

import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Optional

from utils.logger import get_logger

logger = get_logger("adb_utils")

# 本机常见安装位置（Windows 新开终端偶发未继承 User PATH）
_ADB_CANDIDATES = [
    Path(r"E:\tools\platform-tools\adb.exe"),
    Path(r"C:\platform-tools\adb.exe"),
    Path(r"D:\platform-tools\adb.exe"),
    Path(os.path.expandvars(r"%LOCALAPPDATA%\Android\Sdk\platform-tools\adb.exe")),
    Path(os.path.expandvars(r"%USERPROFILE%\AppData\Local\Android\Sdk\platform-tools\adb.exe")),
]


def resolve_adb_path(configured: str = "") -> str:
    """
    解析可用的 adb 可执行文件路径。

    优先级: 配置路径 → PATH → 常见安装目录。
    """
    candidates: list[str] = []
    if configured:
        candidates.append(configured)
    try:
        from config.settings import settings
        if settings.ADB_PATH:
            candidates.append(settings.ADB_PATH)
    except Exception:
        pass
    candidates.append("adb")
    candidates.append("adb.exe")

    for c in candidates:
        if not c:
            continue
        p = Path(c)
        if p.is_file():
            return str(p)
        found = shutil.which(c)
        if found:
            return found

    for p in _ADB_CANDIDATES:
        if p.is_file():
            return str(p)

    raise FileNotFoundError(
        "找不到 adb。请安装 Android platform-tools，"
        "或在 config/settings.py 中设置 ADB_PATH 为 adb.exe 全路径"
        r"（例如 E:\tools\platform-tools\adb.exe）。"
    )


class ADBUtils:
    """ADB 命令封装（静态方法集合）"""

    _resolved: Optional[str] = None

    @classmethod
    def adb(cls) -> str:
        if not cls._resolved:
            cls._resolved = resolve_adb_path()
            logger.debug(f"使用 ADB: {cls._resolved}")
        return cls._resolved

    @classmethod
    def _run(cls, args: list[str], **kwargs) -> subprocess.CompletedProcess:
        return subprocess.run([cls.adb(), *args], **kwargs)

    @classmethod
    def list_devices(cls) -> list[str]:
        """
        列出所有已连接的 Android 设备。

        Returns:
            设备序列号列表
        """
        result = cls._run(
            ["devices"],
            capture_output=True, text=True,
        )
        lines = result.stdout.strip().split("\n")[1:]
        devices = []
        for line in lines:
            if not line.strip():
                continue
            parts = line.split()
            if len(parts) >= 2 and parts[1] == "device":
                devices.append(parts[0])
        return devices

    @classmethod
    def get_device_state(cls, serial: str) -> str:
        """
        获取设备连接状态。

        Returns:
            "device" | "offline" | "unauthorized" | "unknown"
        """
        result = cls._run(
            ["-s", serial, "get-state"],
            capture_output=True, text=True,
        )
        return result.stdout.strip()

    @classmethod
    def is_device_online(cls, serial: str) -> bool:
        """检查设备是否在线"""
        return cls.get_device_state(serial) == "device"

    @classmethod
    def get_device_info(cls, serial: str) -> dict:
        """
        获取设备基本信息（型号、Android 版本、SDK 级别）。

        Returns:
            {"model": "...", "brand": "...", "android_version": "...", "sdk": "..."}
        """
        info = {}
        props = {
            "model": "ro.product.model",
            "brand": "ro.product.brand",
            "android_version": "ro.build.version.release",
            "sdk": "ro.build.version.sdk",
        }
        for key, prop in props.items():
            result = cls._run(
                ["-s", serial, "shell", "getprop", prop],
                capture_output=True, text=True,
            )
            info[key] = result.stdout.strip()
        return info

    @classmethod
    def reboot_device(cls, serial: str) -> bool:
        """重启设备"""
        try:
            cls._run(
                ["-s", serial, "reboot"],
                capture_output=True, timeout=10,
            )
            logger.info(f"设备 {serial} 正在重启...")
            return True
        except Exception as e:
            logger.error(f"重启设备 {serial} 失败: {e}")
            return False

    @classmethod
    def wait_for_device(cls, serial: str, timeout: float = 120.0) -> bool:
        """等待设备上线（重启后使用）"""
        logger.info(f"等待设备 {serial} 上线...")
        start = time.time()
        while time.time() - start < timeout:
            if cls.is_device_online(serial):
                logger.info(f"设备 {serial} 已上线")
                return True
            time.sleep(2)
        logger.error(f"设备 {serial} 在 {timeout}s 内未上线")
        return False

    @classmethod
    def restart_adb_server(cls) -> bool:
        """重启 ADB 服务（USB 连接异常时使用）"""
        try:
            cls._run(["kill-server"], capture_output=True, timeout=5)
            time.sleep(1)
            cls._run(["start-server"], capture_output=True, timeout=10)
            logger.info("ADB 服务已重启")
            return True
        except Exception as e:
            logger.error(f"重启 ADB 服务失败: {e}")
            return False
