"""
全局配置 — 集中管理所有可配置参数。

使用方式:
    from config.settings import settings
    print(settings.DATA_DIR)
"""

import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Settings:
    # ===== 项目路径 =====
    PROJECT_ROOT: Path = field(default_factory=lambda: Path(__file__).parent.parent)
    DATA_DIR: Path = field(default_factory=lambda: Path(__file__).parent.parent / "data")
    LOGS_DIR: Path = field(default_factory=lambda: Path(__file__).parent.parent / "logs")
    SCREENSHOTS_DIR: Path = field(default_factory=lambda: Path(__file__).parent.parent / "screenshots")

    # ===== 数据库 =====
    DB_PATH: str = ""

    def __post_init__(self):
        if not self.DB_PATH:
            self.DB_PATH = str(self.PROJECT_ROOT / "wechat_farm.db")
        # 确保目录存在
        self.DATA_DIR.mkdir(parents=True, exist_ok=True)
        self.LOGS_DIR.mkdir(parents=True, exist_ok=True)
        self.SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)

    # ===== 微信 =====
    WECHAT_PACKAGE: str = "com.tencent.mm"
    WECHAT_LAUNCH_WAIT: float = 3.0       # 启动微信后等待秒数
    WECHAT_PAGE_LOAD_WAIT: float = 2.0     # 页面加载等待秒数
    WECHAT_ELEMENT_TIMEOUT: float = 10.0   # 元素查找超时秒数

    # ===== ADB / 设备 =====
    ADB_PATH: str = r"E:\tools\platform-tools\adb.exe"  # 本机 platform-tools；也可写 "adb" 走 PATH
    USB_CONNECTION_TIMEOUT: float = 30.0   # USB 连接超时
    DEVICE_HEALTH_CHECK_INTERVAL: float = 60.0  # 设备健康检查间隔（秒）
    ATX_AGENT_INIT_RETRY: int = 3          # ATX agent 初始化重试次数

    # ===== 行为控制 =====
    ACTIVE_HOURS_START: int = 7            # 每日活跃开始时间（时）
    ACTIVE_HOURS_END: int = 23             # 每日活跃结束时间（时）
    DEFAULT_RANDOM_OFFSET_MINUTES: int = 30  # 默认时间窗口随机偏移

    # ===== 拟人化 =====
    CLICK_SIGMA: float = 5.0               # 点击坐标正态分布标准差（像素）
    SWIPE_DURATION_RANGE: tuple = (0.3, 0.8)  # 滑动耗时范围（秒）
    HESITATE_PROBABILITY: float = 0.03     # 犹豫概率
    MISTAP_PROBABILITY: float = 0.01       # 误操作概率
    LOGNORMAL_SIGMA: float = 0.5           # 对数正态分布 σ 参数

    # ===== LLM =====
    LLM_PROVIDER: str = "deepseek"         # deepseek / openai / local
    LLM_API_KEY: str = ""
    LLM_BASE_URL: str = "https://api.deepseek.com"
    LLM_MODEL: str = "deepseek-chat"
    LLM_TEMPERATURE: float = 0.9
    LLM_MAX_TOKENS: int = 200

    # ===== AI 上帝视角编排 =====
    USE_AI_GOD_PLANNER: bool = True        # True=LLM 编排当日剧本，失败回退规则模板
    AI_PLANNER_FALLBACK_TO_TEMPLATE: bool = True

    # ===== 监控 =====
    PROMETHEUS_PORT: int = 9090
    HEALTH_CHECK_INTERVAL: float = 3600.0  # 账号健康检查间隔（秒）
    ALERT_DINGTALK_WEBHOOK: str = ""        # 钉钉告警 webhook（可选）

    # ===== 账号限量（全局默认）=====
    NEW_ACCOUNT_DAILY_ADD_FRIENDS: int = 2
    OLD_ACCOUNT_DAILY_ADD_FRIENDS: int = 15
    NEW_ACCOUNT_WEEKLY_POSTS: int = 3
    OLD_ACCOUNT_DAILY_POSTS: int = 3
    OPERATION_MIN_INTERVAL_SEC: float = 600.0  # 同类操作最小间隔 10 分钟

    def load_from_env(self):
        """从环境变量覆盖配置"""
        self.LLM_API_KEY = os.getenv("LLM_API_KEY", self.LLM_API_KEY)
        self.LLM_BASE_URL = os.getenv("LLM_BASE_URL", self.LLM_BASE_URL)
        self.ALERT_DINGTALK_WEBHOOK = os.getenv("DINGTALK_WEBHOOK", self.ALERT_DINGTALK_WEBHOOK)
        flag = os.getenv("USE_AI_GOD_PLANNER")
        if flag is not None:
            self.USE_AI_GOD_PLANNER = flag.strip().lower() in ("1", "true", "yes", "on")


# 全局单例
settings = Settings()
settings.load_from_env()
