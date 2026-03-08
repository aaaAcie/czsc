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

    # 转折K（确立K）：默认等于触发信号K；若触发K本身即极值K，按特殊法则后移一根
    trigger_k: Optional[RawBar] = None     # 兼容旧名：转折K
    trigger_k_index: Optional[int] = None  # 兼容旧名：转折K索引

    # 状态标记
    is_valid: bool = False       # 是否已经通过了基础验证（法则1,2）成立
    is_perfect: bool = False     # 内政：内部是否包含线段中枢（微观几何成立）
    is_locked: bool = False      # 外交：是否被趋势正式锁定为不可更改的历史锚点（宏观锁定，不可被趋势穿透吞噬）
    maybe_is_fake: bool = False  # 宏观审判层标记：该点所在的线段结构不完美，疑似虚假端点，等待三级跃迁回溯
    has_visible_center: bool = False # 线段内部是否包含肉眼中枢（高能级结构保障）
    cache: dict = field(default_factory=dict)

    @property
    def turning_k(self) -> Optional[RawBar]:
        """转折K（正式命名，兼容映射到 trigger_k）。"""
        return self.trigger_k

    @turning_k.setter
    def turning_k(self, value: Optional[RawBar]):
        self.trigger_k = value

    @property
    def turning_k_index(self) -> Optional[int]:
        """转折K索引（正式命名，兼容映射到 trigger_k_index）。"""
        return self.trigger_k_index

    @turning_k_index.setter
    def turning_k_index(self, value: Optional[int]):
        self.trigger_k_index = value

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

    # 增强属性
    center_id: int = -1                   # 唯一自增 ID
    confirm_k_index: int = -1             # 确认 K 线的索引，用于时序锁定
    source_layer: str = ""                # "micro" | "macro" | "ghost"
    owner_seg_key: Optional[tuple] = None # (start_micro_id, end_micro_id) 所属线段的微观 ID 键对
    origin_center_id: Optional[int] = None # 追踪来源（宏观复用或幽灵迁移自哪号中枢）

    start_k_index: int = -1               # 中枢起始 K 线索引
    end_k_index: int = -1                 # 中枢结束 K 线索引
    
    is_ghost: bool = False                # 标记该中枢是否为洗盘期遗留的“幽灵中枢”

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
