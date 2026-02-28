# -*- coding: utf-8 -*-
"""
author: moore_czsc
describe: 摩尔缠论核心数据对象定义
"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional
from czsc.py.enum import Mark, Direction
from czsc.py.objects import RawBar


@dataclass
class TurningK:
    """摩尔转折 K 线实体 (包含极值与触发极值的转折K)"""
    # 原始 K 线关联数据
    symbol: str
    dt: datetime
    raw_bar: RawBar         # 对真正的顶底极值K线的引用
    # 极值点属性
    mark: Mark              # Mark.G (顶) 或 Mark.D (底)
    price: float            # 价格：如果是顶则为 high，如果是底则为 low
    k_index: int            # 在原始 K 线序列中的绝对索引位置，用于追溯距离

    # 触发K
    trigger_k: Optional[RawBar] = None     # 发出跨越/跳空触发信号的“转折K”
    trigger_k_index: Optional[int] = None  # 转折信号发生时的绝对索引位置

    # 状态标记
    is_valid: bool = False    # 是否已经通过了基础验证（法则1,2）成立
    is_perfect: bool = False  # 内政：内部是否包含线段中枢（微观几何成立）
    is_locked: bool = False   # 外交：是否被趋势正式锁定为不可更改的历史锚点（宏观锁定，不可被趋势穿透吞噬）
    cache: dict = field(default_factory=dict)

    def __repr__(self):
        return f"TurningK(dt={self.dt}, mark={self.mark.value}, price={self.price}, is_valid={self.is_valid})"


@dataclass
class MooreCenter:
    """摩尔双轨线段中枢"""
    # 中枢类型
    type_name: str          # "VISIBLE" (肉眼可见) 或 "INVISIBLE" (非肉眼可见)
    direction: Direction    # 中枢所在线段的整体延伸方向

    # --- 慢车道（非肉眼中枢）专有状态机锁死属性 ---
    anchor_k0: Optional[RawBar] = None    # 状态机：无暇的 K0
    confirm_k: Optional[RawBar] = None    # 状态机：反穿 MA5 的确认 K
    
    # 轨道属性
    method: str = ""                      # 判定方式：VISIBLE / 2C / 5K / 3BI
    center_line: float = 0.0              # 中枢线价格
    upper_rail: float = 0.0               # 上轨价格
    lower_rail: float = 0.0               # 下轨价格

    start_dt: Optional[datetime] = None   # 中枢确立的起始时间
    end_dt: Optional[datetime] = None     # 中枢确立的终点时间

    start_k_index: int = -1               # 中枢起始 K 线索引
    end_k_index: int = -1                 # 中枢结束 K 线索引
    
    is_ghost: bool = False                # 标记该中枢是否为洗盘期遗留的“逆势幽灵中枢”

    cache: dict = field(default_factory=dict)

    @property
    def is_visible(self) -> bool:
        return self.type_name == "VISIBLE"

    def __repr__(self):
        type_str = "肉眼" if self.is_visible else "非肉眼"
        return f"MooreCenter({type_str}, center={self.center_line:.2f}, rail=[{self.lower_rail:.2f}, {self.upper_rail:.2f}])"


@dataclass
class MooreSegment:
    """摩尔本质线段"""
    symbol: str
    start_k: TurningK                     # 线段起点的转折K（通过唯一性与四法则验证）
    end_k: TurningK                       # 线段终点的转折K
    direction: Direction                  # 线段向上的 Direction.Up 或 向下的 Direction.Down
    
    # 组成元素
    bars: List[RawBar] = field(default_factory=list)        # 线段内部所包含的所有原始 K 线
    centers: List[MooreCenter] = field(default_factory=list)# 线段内部包裹的双轨中枢列表

    cache: dict = field(default_factory=dict)

    @property
    def is_perfect(self) -> bool:
        """结构完美性决定线段虚实（法则三：端点 TurningK 内部是否有中枢）"""
        return self.end_k.is_perfect

    @property
    def sdt(self) -> datetime:
        return self.start_k.dt
        
    @property
    def edt(self) -> datetime:
        return self.end_k.dt
        
    @property
    def power(self) -> float:
        """线段的绝对价格力度"""
        return abs(self.end_k.price - self.start_k.price)

    def __repr__(self):
        return (f"MooreSeg(sdt={self.sdt}, edt={self.edt}, dir={self.direction.value}, "
                f"power={self.power:.2f}, centers={len(self.centers)})")
