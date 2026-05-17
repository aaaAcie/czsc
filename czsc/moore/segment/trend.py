# -*- coding: utf-8 -*-
"""
趋势状态维护层（TrendEngine）

职责：
  - 维护趋势状态（direction / high / low）与触发翻转（_flip_trend）

【架构变更说明】
  原有的 check_penetration / consume_imperfect_chain（价格穿透吞噬）机制已被彻底废弃。
  所有"线段合并/延伸"操作现在统一由 SegmentAnalyzer.macro_audit_and_replay()
  在宏观审判层以"三级同向跃迁"的方式处理。

  FractalEngine 退化为"纯粹的顶底记录仪"，仅负责确立原始顶底，
  不再参与任何线段合并操作。

不直接引用 SegmentAnalyzer，全部通过 SegmentState 共享状态容器操作。
"""
from czsc.py.enum import Mark, Direction
from ..objects import TurningK


class TrendEngine:
    """趋势状态维护层

    消费 SegmentState，不持有独立数据：通过构造函数拿到状态引用后直接操作。
    """

    def __init__(self, state):
        # state: SegmentState（避免循环 import，类型注解用字符串）
        self.s = state

    # =========================================================================
    # 公开接口（供 FractalEngine 调用）
    # =========================================================================

    def update_trend_state(self, new_tk: TurningK):
        """在新 TurningK 确立后，更新趋势状态、全局极值与翻转判断"""
        s = self.s
        # 趋势初始化：第一根有效线段生成时赋值
        if s.trend_state is None:
            if len(s.turning_ks) >= 2:
                s.trend_state = Direction.Up if s.turning_ks[0].mark == Mark.D else Direction.Down
                g_tks = [tk for tk in s.turning_ks if tk.mark == Mark.G]
                d_tks = [tk for tk in s.turning_ks if tk.mark == Mark.D]
                s.trend_high = max(tk.price for tk in g_tks) if g_tks else None
                s.trend_low  = min(tk.price for tk in d_tks) if d_tks else None
                if s.trend_state == Direction.Up:
                    s.trend_extreme_k = max(g_tks, key=lambda x: x.price) if g_tks else None
                else:
                    s.trend_extreme_k = min(d_tks, key=lambda x: x.price) if d_tks else None
            return

        # 更新全局极值
        if new_tk.mark == Mark.G:
            if s.trend_high is None or new_tk.price > s.trend_high:
                s.trend_high = new_tk.price
                if s.trend_state == Direction.Up:
                    s.trend_extreme_k = new_tk
        if new_tk.mark == Mark.D:
            if s.trend_low is None or new_tk.price < s.trend_low:
                s.trend_low = new_tk.price
                if s.trend_state == Direction.Down:
                    s.trend_extreme_k = new_tk

        # 趋势翻转双重锁（满足其一即翻转）
        if s.trend_state == Direction.Up:
            # V 型反转：新底直接打穿全局最低
            if new_tk.mark == Mark.D and s.trend_low is not None and new_tk.price < s.trend_low:
                self._flip_trend(Direction.Down, new_tk)
            # 结构翻转：完美反向线段突破最近关键节点
            elif (new_tk.mark == Mark.D and new_tk.is_perfect
                  and len(s.segments) >= 2):
                key_node = s.segments[-2].start_k.price   # 最近上涨段的起点
                if new_tk.price < key_node:
                    self._flip_trend(Direction.Down, new_tk)
        else:  # Direction.Down
            if new_tk.mark == Mark.G and s.trend_high is not None and new_tk.price > s.trend_high:
                self._flip_trend(Direction.Up, new_tk)
            elif (new_tk.mark == Mark.G and new_tk.is_perfect
                  and len(s.segments) >= 2):
                key_node = s.segments[-2].start_k.price
                if new_tk.price > key_node:
                    self._flip_trend(Direction.Up, new_tk)

    # =========================================================================
    # 私有方法
    # =========================================================================

    def _flip_trend(self, new_direction: Direction, trigger_tk: TurningK):
        """执行趋势翻转：重置方向与全局极值"""
        s = self.s
        s.trend_state = new_direction
        # 翻转后以触发点为新趋势的起始极值
        if new_direction == Direction.Up:
            s.trend_low  = trigger_tk.price
            s.trend_high = None
        else:
            s.trend_high = trigger_tk.price
            s.trend_low  = None
        s.trend_extreme_k = trigger_tk
