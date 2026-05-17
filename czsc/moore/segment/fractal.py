# -*- coding: utf-8 -*-
"""兼容层：旧名称 FractalEngine，现由 MicroStructureEngine 承载实现。"""

from .micro_engine import MicroStructureEngine


class FractalEngine(MicroStructureEngine):
    """顶底识别引擎兼容别名（向后兼容旧导入路径）。"""

