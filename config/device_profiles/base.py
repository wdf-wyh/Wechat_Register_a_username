"""
机型适配基类 — 每台手机一套配置，互不覆盖。

新增机型步骤:
  1. 复制 moto_x70_air_pro.py 为新文件（如 vivo_xxx.py）
  2. 改 PROFILE_ID / MATCH / 实测坐标
  3. 在 registry.py 的 PROFILES 列表里追加（不要改旧文件）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class DeviceMatch:
    """匹配条件：分辨率精确匹配优先，型号关键字次之。"""

    width: int
    height: int
    # 设备 model / product / brand 中任一包含即可（小写比较）
    model_keywords: tuple[str, ...] = ()


@dataclass
class DeviceProfile:
    """
    单机型适配配置。

    coords: 覆盖 COORDINATE_FALLBACK 的百分比坐标
    nav: 导航相关（发现页入口、Tab Y、搜索图标候选等）
    extras: 各模块专用坐标（发图、发圈等）
    """

    profile_id: str
    display_name: str
    match: DeviceMatch
    coords: dict[str, tuple[float, float]] = field(default_factory=dict)
    nav: dict = field(default_factory=dict)
    extras: dict = field(default_factory=dict)

    def score(self, width: int, height: int, model_blob: str = "") -> int:
        """匹配得分，越高越优先；0 表示不匹配。"""
        score = 0
        if self.match.width == width and self.match.height == height:
            score += 100
        blob = (model_blob or "").lower()
        for kw in self.match.model_keywords:
            if kw.lower() in blob:
                score += 20
                break
        return score
