"""
全局配置 — 集中管理所有可配置参数。

使用方式:
    from config.settings import settings
    print(settings.DATA_DIR)
"""

import os
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path


def _iter_env_candidates(project_root: Path) -> Iterable[Path]:
    """按优先级返回可加载的 .env 文件列表。"""
    yield project_root / ".env"
    env_name = os.getenv("WECHAT_FARM_ENV")
    if env_name:
        yield project_root / f".env.{env_name.strip()}"


def _load_dotenv_file(project_root: Path) -> None:
    """
    从项目根目录加载 .env 文件。

    仅填充当前进程尚未设置的环境变量，保持系统环境变量优先级更高。
    支持最常见的 KEY=VALUE 格式和可选的引号。
    """
    for env_path in _iter_env_candidates(project_root):
        if not env_path.exists() or not env_path.is_file():
            continue

        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue

            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()
            if not key or key in os.environ:
                continue

            if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
                value = value[1:-1]

            os.environ[key] = value


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
        _load_dotenv_file(self.PROJECT_ROOT)
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
    LLM_PROVIDER: str = "deepseek"         # deepseek / openai / hunyuan / local
    LLM_API_KEY: str = ""
    LLM_BASE_URL: str = "https://api.deepseek.com"
    LLM_MODEL: str = "deepseek-chat"
    LLM_TEMPERATURE: float = 0.9
    LLM_MAX_TOKENS: int = 200
    LLM_TIMEOUT: int = 60                  # 单次 API 超时（秒）
    LLM_RETRY_TIMES: int = 2               # 超时后重试次数
    LLM_VISION_MODEL: str = ""             # 多模态 endpoint（如 ep-xxx 或 doubao-1.5-vision-pro-32k-250115）
    LLM_VISION_TIMEOUT: int = 90           # Vision 专用超时（秒，通常比文本更长）

    # ===== 混元 / TokenHub（可选，优先用于 Vision 识图）=====
    HUNYUAN_API_KEY: str = ""
    HUNYUAN_BASE_URL: str = "https://tokenhub.tencentmaas.com/v1"
    HUNYUAN_MODEL: str = "hy3"             # 文本模型
    HUNYUAN_VISION_MODEL: str = "hy-vision-2.0-instruct"  # 图生文

    # ===== AI 上帝视角编排 =====
    USE_AI_GOD_PLANNER: bool = True        # True=LLM 编排当日剧本，失败回退规则模板
    AI_PLANNER_FALLBACK_TO_TEMPLATE: bool = True

    # ===== OCR =====
    OCR_USE_GPU: bool = True                 # EasyOCR 是否尝试使用 GPU（需 CUDA 版 PyTorch）

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
        self.LLM_PROVIDER = os.getenv("LLM_PROVIDER", self.LLM_PROVIDER)
        self.LLM_API_KEY = os.getenv("LLM_API_KEY", self.LLM_API_KEY)
        self.LLM_BASE_URL = os.getenv("LLM_BASE_URL", self.LLM_BASE_URL)
        self.LLM_MODEL = os.getenv("LLM_MODEL", self.LLM_MODEL)
        _timeout = os.getenv("LLM_TIMEOUT")
        if _timeout and _timeout.strip().isdigit():
            self.LLM_TIMEOUT = int(_timeout)
        _retry = os.getenv("LLM_RETRY_TIMES")
        if _retry and _retry.strip().isdigit():
            self.LLM_RETRY_TIMES = int(_retry)
        self.LLM_VISION_MODEL = os.getenv("LLM_VISION_MODEL", self.LLM_VISION_MODEL)
        _vtimeout = os.getenv("LLM_VISION_TIMEOUT")
        if _vtimeout and _vtimeout.strip().isdigit():
            self.LLM_VISION_TIMEOUT = int(_vtimeout)
        self.HUNYUAN_API_KEY = os.getenv("HUNYUAN_API_KEY", self.HUNYUAN_API_KEY)
        self.HUNYUAN_BASE_URL = os.getenv("HUNYUAN_BASE_URL", self.HUNYUAN_BASE_URL)
        self.HUNYUAN_MODEL = os.getenv("HUNYUAN_MODEL", self.HUNYUAN_MODEL)
        self.HUNYUAN_VISION_MODEL = os.getenv(
            "HUNYUAN_VISION_MODEL", self.HUNYUAN_VISION_MODEL
        )
        self.ALERT_DINGTALK_WEBHOOK = os.getenv("DINGTALK_WEBHOOK", self.ALERT_DINGTALK_WEBHOOK)
        flag = os.getenv("USE_AI_GOD_PLANNER")
        if flag is not None:
            self.USE_AI_GOD_PLANNER = flag.strip().lower() in ("1", "true", "yes", "on")
        ocr_gpu = os.getenv("OCR_USE_GPU")
        if ocr_gpu is not None:
            self.OCR_USE_GPU = ocr_gpu.strip().lower() in ("1", "true", "yes", "on")


# 全局单例
settings = Settings()
settings.load_from_env()
