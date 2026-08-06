"""
EasyOCR 工厂 — 统一 GPU/CPU 配置。

使用方式:
    from utils.ocr_utils import create_easyocr_reader
    reader = create_easyocr_reader()
"""

from __future__ import annotations

from config.settings import settings
from utils.logger import get_logger

logger = get_logger("ocr_utils")

_DEFAULT_LANGS = ("ch_sim", "en")


def resolve_ocr_gpu() -> bool:
    """根据配置与 PyTorch CUDA 可用性决定是否启用 GPU。"""
    if not settings.OCR_USE_GPU:
        return False

    try:
        import torch
    except ImportError:
        logger.warning("OCR_USE_GPU=1 但未安装 torch，EasyOCR 将使用 CPU")
        return False

    if torch.cuda.is_available():
        device_name = torch.cuda.get_device_name(0)
        logger.info("EasyOCR 使用 GPU: {} (torch {})", device_name, torch.__version__)
        return True

    logger.warning(
        "OCR_USE_GPU=1 但 PyTorch 无 CUDA 支持（当前 {}），EasyOCR 将使用 CPU。"
        " 可安装 GPU 版 PyTorch 后自动加速。",
        torch.__version__,
    )
    return False


def create_easyocr_reader(languages: tuple[str, ...] | list[str] | None = None):
    """创建 EasyOCR Reader，GPU 开关由 settings.OCR_USE_GPU 控制。"""
    import easyocr

    langs = list(languages) if languages else list(_DEFAULT_LANGS)
    return easyocr.Reader(langs, gpu=resolve_ocr_gpu())
