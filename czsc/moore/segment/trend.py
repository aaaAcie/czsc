# -*- coding: utf-8 -*-
"""
趋势穿透层（TrendEngine）

职责：
  - 判断新 TurningK 是否触发趋势穿透（_check_penetration）
  - 执行多层回溯吞噬不稳定结构链（_consume_imperfect_chain）
  - 维护趋势状态（direction / high / low）与触发翻转（_flip_trend）

不直接引用 SegmentAnalyzer，全部通过 SegmentState 共享状态容器操作。
"""
from czsc.py.enum import Mark, Direction
from ..objects import TurningK


class TrendEngine:
    """趋势穿透层

    消费 SegmentState，不持有独立数据：通过构造函数拿到状态引用后直接操作。
    """

    def __init__(self, state):
        # state: SegmentState（避免循环 import，类型注解用字符串）
        self.s = state

    # =========================================================================
    # 公开接口（供 FractalEngine 调用）
    # =========================================================================

    def check_penetration(self, new_tk: TurningK) -> bool:
        """根据 penetration_level 判断是否触发趋势穿透（允许跨越回溯）

        穿透判定看的是**待被吞噬的中继结构（turning_ks[-1]）** 是否稳固，
        而不是新 pivot 自身是否完美（新 pivot 是主动方，无需评判其结构）。

        OR 递进关系：高级别天然包含低级别条件。
          Level 1: 仅中继结构不完美 → 允许吞噬
          Level 2: Level1 OR 新 pivot 突破线段起点极值
          Level 3: Level1 OR 新 pivot 突破趋势全局极值（最宽松）
        """
        s = self.s
        if not s.turning_ks:
            return False

        last_opposite = s.turning_ks[-1]  # 待审判的中继结构

        # 条件 A：中继结构不完美（all levels）
        if not last_opposite.is_perfect:
            return True

        # 条件 B：新 pivot 突破线段起点极值（Level 2 / 3）
        if s.penetration_level >= 2 and s.segment_start_extreme is not None:
            if new_tk.mark == Mark.G and new_tk.price > s.segment_start_extreme:
                return True
            if new_tk.mark == Mark.D and new_tk.price < s.segment_start_extreme:
                return True

        # 条件 C：新 pivot 突破趋势全局极值（Level 3）
        if s.penetration_level >= 3:
            if s.trend_high is not None and new_tk.mark == Mark.G and new_tk.price > s.trend_high:
                return True
            if s.trend_low is not None and new_tk.mark == Mark.D and new_tk.price < s.trend_low:
                return True

        return False

    def consume_imperfect_chain(self, new_pivot: TurningK):
        """双重门多层回溯引擎：动态吞噬不稳定结构链

        门1（方向门）：只吞噬正确方向的异向中继
        门2（防御门）：宏观锁定（is_locked）为绝对铁门；结构完美（is_perfect）为弹性门
        门3（价格门）：新 pivot 必须在价格上碾压旧同向点
        """
        s = self.s
        MAX_BACKTRACK = 50
        backtrack_count = 0
        while len(s.turning_ks) >= 2:
            if backtrack_count > MAX_BACKTRACK:
                break
            last_opposite = s.turning_ks[-1]   # 最近的异向点（待吞噬的中继）
            last_same     = s.turning_ks[-2]   # 最近的同向点（待替换的旧极值）

            # 门1：方向必须正确
            if last_opposite.mark == new_pivot.mark:
                break

            # 门2：双重防御（宏观铁门 > 弹性微观门）
            # 绝对防御：宏观锁定，任何情况不可吞噬
            if last_opposite.is_locked:
                break
            # 弹性防御：结构完美 + 保守模式（Level 1），停止
            if last_opposite.is_perfect:
                if s.penetration_level == 1:   # STRUCT_ONLY
                    break
                # Level 2/3：完美但未锁定，继续看价格门

            # 门3：价格替代 — 新 pivot 必须在价格上碾压旧同向点
            if last_same.mark != new_pivot.mark:
                break
            if new_pivot.mark == Mark.G and new_pivot.price < last_same.price:
                break
            if new_pivot.mark == Mark.D and new_pivot.price > last_same.price:
                break

            # 通过三重门 → 先存档一对，再 pop
            consumed_opposite = s.turning_ks.pop()   # 异向中继（时间较晚）
            consumed_same     = s.turning_ks.pop()   # 旧同向点（时间较早）
            # 分叉锚点 = pop 完后 turning_ks 末端存活的锚点
            fork_tk = s.turning_ks[-1] if s.turning_ks else consumed_same
            # 按时间顺序存储：先小后大
            s.ghost_forks.append((
                fork_tk,
                sorted([consumed_same, consumed_opposite], key=lambda t: t.k_index)
            ))
            backtrack_count += 1

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
