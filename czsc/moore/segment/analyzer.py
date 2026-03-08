# -*- coding: utf-8 -*-
"""
30分钟线段级分析器（SegmentAnalyzer）

包含：
  - SegmentState：三个子引擎共享的状态容器（数据总线）
  - SegmentAnalyzer：协调 FractalEngine / CenterEngine / TrendEngine 的主入口

外部通过 SegmentAnalyzer 的属性直接访问结果：
    analyzer.turning_ks   → List[TurningK]
    analyzer.segments     → List[MooreSegment]
    analyzer.all_centers  → List[MooreCenter]
    analyzer.ghost_forks  → List[tuple]
    analyzer.trend_state  → Optional[Direction]
    ...

【架构说明：宏观滞后审判与同向跃迁】
  原有的微观价格穿透机制（check_penetration / consume_imperfect_chain）已被彻底废弃。
  新架构采用"放弃微观实时生长，改由宏观滞后审判驱动回溯"的策略：

  - FractalEngine：纯粹顶底记录仪，只负责确立原始顶底，不参与任何线段合并。
  - TrendEngine：仅维护趋势状态（极值 / 方向 / 翻转），不处理任何穿透逻辑。
  - SegmentAnalyzer（宏观审判层）：在每根 K 线的 fractal_engine.update() 之后，
    执行 _macro_audit_and_replay() —— 检查被审判线段 N（n2→n3）是否"不完美"，
    若通过审计成熟度检验，则尝试三级同向跃迁回溯，将中间的噪音幻影塌陷为幽灵，
    重建正确的线段结构。

【四点模型索引定义】（以上涨趋势为例：1=底, 2=顶, 3=底, 4=顶）
  n4 = turning_ks[-1]：最新确立的顶/底（N+1 段的终点）
  n3 = turning_ks[-2]：N+1 段起点 / N 段终点
                       n3.is_perfect 代表 N 段（n2→n3）的结构完整性
  n2 = turning_ks[-3]：N 段起点（疑假端点打标位置）
  n1 = turning_ks[-4]：P1 跃迁锚点
  n0 = turning_ks[-5]：P2 跃迁锚点
  nm1= turning_ks[-6]：P3 跃迁锚点
"""
import collections
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional

from czsc.py.objects import RawBar
from czsc.py.enum import Mark, Direction

from ..objects import TurningK, MooreCenter, MooreSegment
from .micro_engine import MicroStructureEngine
from .center import CenterEngine
from .trend import TrendEngine
from .macro_engine import MacroAuditEngine


@dataclass
class SegmentState:
    """三个子引擎共享的状态容器（数据总线）

    引擎之间通过持有同一个 SegmentState 实例来共享和修改状态，
    而不是直接互相调用对方的方法（降低耦合）。
    """

    # 可配置参数
    max_segments: int = 500
    use_left_3k_locator: bool = True  # True: 3K向左寻找优先, False: 区间绝对极值优先
    ma34_cross_as_valid_gate: bool = True  # True: 交叉是顶底成立门槛; False: 仅影响线段虚实

    # -------------------------------------------------------------------------
    # 基础数据容器
    # -------------------------------------------------------------------------
    bars_raw: List[RawBar] = field(default_factory=list)

    # -------------------------------------------------------------------------
    # 结果输出容器
    # -------------------------------------------------------------------------
    # 微观世界（原始顶底与线段，供宏观世界消费）
    turning_ks: List[TurningK]        = field(default_factory=list)
    segments:   List[MooreSegment]    = field(default_factory=list)
    # 宏观世界（吞噬/审计后的顶底与线段，默认对外展示）
    macro_turning_ks: List[TurningK]  = field(default_factory=list)
    macro_segments: List[MooreSegment] = field(default_factory=list)

    # 【三仓中枢架构】
    micro_centers: List[MooreCenter]  = field(default_factory=list) # 微观事实仓（同步前重播产出）
    macro_centers: List[MooreCenter]  = field(default_factory=list) # 宏观结果仓（仅审计命中后更新）
    ghost_centers: List[MooreCenter]  = field(default_factory=list) # 幽灵仓（覆盖区迁出的中枢）

    all_centers: List[MooreCenter]    = field(default_factory=list) # 兼容接口：映射到 macro_centers
    refreshed_segments: list          = field(default_factory=list)
    ghost_forks: List[tuple]          = field(default_factory=list)   # 微观幽灵（兼容）
    macro_ghost_forks: List[tuple]    = field(default_factory=list)
    macro_excluded_micro_ids: set     = field(default_factory=set)
    macro_swallow_map: dict           = field(default_factory=dict)   # {(start_id, end_id): [internal_micro_ids]}
    macro_last_synced_micro_id: Optional[int] = None

    # -------------------------------------------------------------------------
    # 顶底引擎游标
    # -------------------------------------------------------------------------
    last_ma5: Optional[float]         = None
    candidate_tk: Optional[TurningK]  = None
    potential_centers: List[MooreCenter] = field(default_factory=list)
    cache: dict = field(default_factory=dict)

    # 特殊法则状态机（转折K无效则后移一根）
    waiting_special_rule: bool          = False
    special_waiting_mark: Optional[Mark] = None
    special_ext_idx_cache: Optional[int] = None
    micro_id_seed: int                  = 0
    center_id_seed: int                 = 0
    # 异向候选 MA5 刷新基线（运行态，失败候选同样推进）
    reversal_ma5_gate_mark: Optional[Mark] = None
    reversal_ma5_gate_start_k_index: int = -1
    reversal_ma5_gate_extreme: Optional[float] = None
    # 异向候选价格刷新基线（运行态，失败候选同样推进）
    reversal_price_gate_mark: Optional[Mark] = None
    reversal_price_gate_start_k_index: int = -1
    reversal_price_gate_extreme: Optional[float] = None

    # -------------------------------------------------------------------------
    # 中枢引擎游标
    # -------------------------------------------------------------------------
    center_anchor_idx: int            = -1     # 记录当前中枢发源的宏观锚点索引
    center_trigger_k_index: int       = -1     # 记录当前观测中枢对应的转折K索引（时间左边界裁决用）
    center_state: int                 = 0
    current_k0: Optional[RawBar]      = None
    latest_k0: Optional[RawBar]       = None   # 追踪最近的合规 K0，用于在旧中枢夭折时原地重建

    # 观测病房（Pending Center）状态（State 2 使用）
    center_line_k: Optional[RawBar]         = None   # 确认K（中枢线K）
    center_line_k_index: int                = -1     # 确认K 的绝对索引
    center_direction: Optional[Direction]   = None   # 当前中枢方向
    center_upper_rail: float                = 0.0    # 结界上轨（实时可更新）
    center_lower_rail: float                = 0.0    # 结界下轨（实时可更新）
    center_start_dt: Optional[datetime]     = None   # 结界起始时间
    center_start_k_index: int               = -1     # 结界起始时间对应的绝对索引
    center_end_dt: Optional[datetime]       = None   # 结界当前右端（实时居新）
    center_end_k_index: int                 = -1     # 当前窗口最后一根在结界内K的绝对索引
    center_is_double_gap: bool              = False  # confirm_k 与 k0 是否双跳空（式一自动成立）
    center_method_found: Optional[str]      = None   # 记录该中枢第一个被触发的确立名分（起手三式）
    center_black_k_pass: bool               = False  # 记录黑K质检是否通过
    # is_visible 定性严格规定需在正向一笔完整形成后才可判断，State 2 期间实时更新此状态
    center_is_visible: bool                 = False  # 当前观测病房中枢是否探测到了“肉眼可见”基因
    center_price_confirmed: bool            = False  # 中枢结界轨道是否已最终确定
    last_center_end_idx: int                = -1     # 记录上一个固化中枢的破窗 K 线索引

    # 【破窗闭库机制】：破窗后进入"预备闭库"状态，等待线段结束才正式固化。
    # 若破窗后有K线回到中枢区域，则清空预备状态，边界延伸，等下次再破窗。
    pending_close: bool                     = False  # 是否处于"预备闭库"状态（已破窗，等待线段结束）
    pending_close_end_dt: Optional[datetime] = None  # 预备闭库时冻结的中枢右边界时间（第一根脱轨K之前一根）
    pending_close_end_k_index: int          = -1     # 预备闭库时冻结的中枢右边界索引
    # 【沙盒机制】：新肉眼中枢与旧隐位中枢的博弈。
    # 当新皇候选面临旧隐位重叠时，开启沙盒试算。
    sandbox_active: bool                    = False  # 是否处于沙盒回溯状态
    pending_overwrite_center: Optional[MooreCenter] = None # 被挂起受威胁的旧隐位中枢

    # -------------------------------------------------------------------------
    # 趋势穿透层状态
    # -------------------------------------------------------------------------
    trend_state: Optional[Direction]        = None
    trend_high: Optional[float]             = None
    trend_low: Optional[float]              = None
    trend_extreme_k: Optional[TurningK]    = None
    segment_start_extreme: Optional[float]  = None

    # -------------------------------------------------------------------------
    # 宏观审计引擎配置
    # -------------------------------------------------------------------------
    enable_macro_audit: bool            = True  # False 时关闭吞噬/跃迁
    audit_link_rounds: int              = 5     # 左右连接机会统一：右侧成熟度 + 左侧回溯轮数

    # -------------------------------------------------------------------------
    # 调试计数器
    # -------------------------------------------------------------------------
    debug_rule_fail: dict  = field(default_factory=lambda: {1: 0, 1.1: 0, 2: 0, 3: 0})
    debug_trigger_count: int = 0
    debug_body_filter: int   = 0


class SegmentAnalyzer:
    """30分钟线段级分析器

    协调 FractalEngine / CenterEngine / TrendEngine，
    维护 MA5/MA34 滑窗队列，驱动每根 K 线的状态机推进。
    """

    def __init__(
        self,
        bars: List[RawBar],
        max_segments: int = 500,
        use_left_3k_locator: bool = True,
        ma34_cross_as_valid_gate: bool = True,
        audit_link_rounds: int = 5,
        enable_macro_audit: bool = True,
    ):
        # 1. 准备共享状态容器（初始物理空间为空）
        s = SegmentState(
            bars_raw=[],
            max_segments=max_segments,
            use_left_3k_locator=use_left_3k_locator,
            ma34_cross_as_valid_gate=ma34_cross_as_valid_gate,
            audit_link_rounds=audit_link_rounds,
            enable_macro_audit=enable_macro_audit,
        )
        self.state = s

        # 2. 准备滑窗缓存
        self._ma5_q  = collections.deque(maxlen=5)
        self._ma34_q = collections.deque(maxlen=34)

        # 3. 实例化子引擎（此时它们持有了空的 state 引用）
        self._trend_engine   = TrendEngine(self.state)
        self._center_engine  = CenterEngine(self.state)
        self._fractal_engine = MicroStructureEngine(self.state, self._trend_engine, self._center_engine)
        self._macro_engine   = MacroAuditEngine(self.state, self._fractal_engine, self._trend_engine)

        # 4. 历史状态推演：执行全量 update，物理化重建所有转折点与中枢
        for bar in bars:
            self.update(bar)

    # =========================================================================
    # 公开接口
    # =========================================================================

    def update(self, bar: RawBar):
        """流式处理引擎入口：每接收一根 K 线，推动一次状态机"""
        s = self.state

        # --- 1. 数据预处理与冷启动拦截 ---
        s.bars_raw.append(bar)
        k_index = len(s.bars_raw) - 1

        self._ma5_q.append(bar.close)
        self._ma34_q.append(bar.close)

        # MA5 不应受 MA34 冷启动门限制：满 5 根后立即写入缓存
        if len(self._ma5_q) == 5:
            bar.cache['ma5'] = sum(self._ma5_q) / 5

        if len(self._ma34_q) < 34:
            return

        current_ma34 = sum(self._ma34_q) / 34

        bar.cache['ma34'] = current_ma34
        current_ma5 = bar.cache.get('ma5')

        # --- 2. 中枢引擎（先于顶底引擎）---
        self._center_engine.update(bar, k_index)

        # --- 3. 顶底确立游标引擎（纯记录，不做线段合并）---
        old_tk_len     = len(s.turning_ks)
        old_last_tk_dt = s.turning_ks[-1].dt if s.turning_ks else None

        self._fractal_engine.update(bar, k_index, current_ma5)

        new_tk_len     = len(s.turning_ks)
        new_last_tk_dt = s.turning_ks[-1].dt if s.turning_ks else None

        # --- 4. 同步前：分层重播阶段 ---
        # 变化分类：
        is_new_tk    = new_tk_len > old_tk_len
        is_refreshed = (not is_new_tk) and (old_tk_len > 0 and old_last_tk_dt != new_last_tk_dt)
        is_changed   = is_new_tk or is_refreshed

        # 4.1 微观层重播 (Stage 1)
        if is_changed and len(s.turning_ks) >= 2:
            tk_replay_start = s.turning_ks[-2]
            real_start_idx  = tk_replay_start.k_index
            real_trig_idx   = tk_replay_start.turning_k_index if tk_replay_start.turning_k_index is not None else tk_replay_start.k_index
            correct_direction = Direction.Up if s.turning_ks[-1].mark == Mark.G else Direction.Down
            
            # 第一阶段重播：打标并生成消费级中心
            self._replay_center_engine_for_segment(real_start_idx, real_trig_idx, k_index, correct_direction)
            self._fractal_engine._update_segments()

        # 4.2 事实仓维护 (ALWAYS 同步 micro_centers)
        self._sync_potential_to_micro_warehouse()

        # --- 5. 宏观同步与审计 (Stage 2) ---
        macro_changed = self._sync_macro_world_from_micro()
        
        if s.enable_macro_audit and macro_changed and len(s.macro_turning_ks) > 4:
            # 这里的 audit_and_replay 只执行顶底塌陷逻辑
            audit_hit = self._macro_engine.audit_and_replay(k_index)
            if audit_hit:
                # 命中审计：触发宏观中枢两步走（幽灵迁移 + 区间重建）
                self._replay_centers_for_macro_audit()
                macro_changed = True

        # 宏观线段基于当前宏观顶底与宏观中枢仓独立重建
        if macro_changed:
            self._update_macro_segments()

        # 游标步进
        s.last_ma5 = current_ma5

    # =========================================================================
    # 宏观审判层：三级同向跃迁回溯
    # =========================================================================

    def _clone_micro_tk_to_macro(self, tk: TurningK) -> TurningK:
        """将微观点克隆到宏观世界，并写入来源映射。"""
        ctk = deepcopy(tk)
        ctk.cache = dict(getattr(tk, "cache", {}))
        ctk.cache["source_micro_id"] = tk.cache.get("micro_id")
        return ctk

    def _get_committed_micro_turning_ks(self) -> List[TurningK]:
        """获取可用于宏观同步的“已提交”微观点（不含最后一个可刷新端点）。"""
        micro = self.state.turning_ks
        if len(micro) <= 1:
            return []
        return micro[:-1]

    def _sync_macro_world_from_micro(self) -> bool:
        """把“已提交微观快照”增量同步到宏观世界（仅追加，不回退不替换）。"""
        s = self.state
        micro = s.turning_ks
        if not micro:
            return False

        changed = False
        micro_id_to_idx = {}
        for i, tk in enumerate(micro):
            mid = tk.cache.get("micro_id")
            if mid is None:
                s.micro_id_seed += 1
                mid = s.micro_id_seed
                tk.cache["micro_id"] = mid
            else:
                s.micro_id_seed = max(s.micro_id_seed, int(mid))
            micro_id_to_idx[mid] = i

        committed = self._get_committed_micro_turning_ks()
        if not committed:
            return False

        committed_ids = [tk.cache.get("micro_id") for tk in committed]
        if not committed_ids:
            return False

        # 首次冷启动：宏观仅导入已提交微观点，天然避开“末端同向刷新回写”。
        if not s.macro_turning_ks:
            for tk in committed:
                if tk.cache.get("micro_id") in s.macro_excluded_micro_ids:
                    continue
                s.macro_turning_ks.append(self._clone_micro_tk_to_macro(tk))
                changed = True
            s.macro_last_synced_micro_id = committed_ids[-1]
            return changed

        # 宏观同步是 append-only：历史端点由宏观自己负责吞噬塌陷，不能被微观回写替换。
        last_synced_id = s.macro_last_synced_micro_id
        if last_synced_id is None and s.macro_turning_ks:
            last_synced_id = s.macro_turning_ks[-1].cache.get("source_micro_id")

        start_pos = -1
        if last_synced_id in micro_id_to_idx:
            start_pos = micro_id_to_idx[last_synced_id]
        elif s.macro_turning_ks:
            # 正常情况下不会出现；若发生，宁可冻结宏观也不做危险回退。
            return False

        for tk in committed[start_pos + 1 :]:
            if tk.cache.get("micro_id") in s.macro_excluded_micro_ids:
                continue
            s.macro_turning_ks.append(self._clone_micro_tk_to_macro(tk))
            changed = True

        s.macro_last_synced_micro_id = committed_ids[-1]

        # 宏观审计未命中时，也尝试增量同步 micro_centers 到 macro_centers (滞后复用)
        for c in s.micro_centers:
            if not getattr(c, 'is_ghost', False) and c not in s.macro_centers:
                # 检查此中枢是否已经在 ghost 仓里（防止重复）
                if any(gc.center_id == c.center_id for gc in s.ghost_centers):
                    continue
                s.macro_centers.append(c)

        return changed

    def _sync_potential_to_micro_warehouse(self):
        """维护微观事实仓：将运行态 potential_centers 同步到 micro_centers，并打上所有权标。"""
        s = self.state
        current_mids = {tk.cache.get("micro_id") for tk in s.turning_ks if tk.cache.get("micro_id") is not None}
        
        # 1. 增量导入
        for c in s.potential_centers:
            if c not in s.micro_centers:
                c.source_layer = "micro"
                s.micro_centers.append(c)

        # 2. 补齐所有权标 (owner_seg_key)
        # 微观仓中枢的所有权始终基于当前微观线段划分。
        for c in s.micro_centers:
            if c.owner_seg_key is not None:
                continue
            
            # 判定中枢归属哪段微观线段
            c_confirm_dt = c.confirm_k.dt if c.confirm_k else c.start_dt
            for seg in s.segments:
                if seg.start_k.dt <= c_confirm_dt <= seg.end_k.dt:
                    ms_id = seg.start_k.cache.get("micro_id")
                    me_id = seg.end_k.cache.get("micro_id")
                    if ms_id is not None and me_id is not None:
                        c.owner_seg_key = (ms_id, me_id)
                    break

    def _update_macro_segments(self):
        """根据宏观顶底重建宏观线段，并挂载现有中枢快照。"""
        s = self.state
        tks = s.macro_turning_ks
        s.macro_segments = []
        if len(tks) < 2:
            return

        micro_id_seq = [tk.cache.get("micro_id") for tk in s.turning_ks if tk.cache.get("micro_id") is not None]
        micro_id_to_pos = {mid: i for i, mid in enumerate(micro_id_seq)}

        all_avail_centers = s.macro_centers
        for i in range(len(tks) - 1):
            tk1 = tks[i]
            tk2 = tks[i + 1]
            direction = Direction.Up if tk2.mark == Mark.G else Direction.Down
            seg = MooreSegment(symbol=tk1.symbol, start_k=tk1, end_k=tk2, direction=direction)
            seg.centers = []
            for c in all_avail_centers:
                c_confirm_dt = c.confirm_k.dt if c.confirm_k else c.start_dt
                if not c_confirm_dt:
                    continue
                if tk1.dt <= c_confirm_dt <= tk2.dt:
                    seg.centers.append(c)
            if seg.centers:
                tk2.is_perfect = True
                tk2.maybe_is_fake = False
            start_src = tk1.cache.get("source_micro_id")
            end_src = tk2.cache.get("source_micro_id")
            swallow_ids = []
            if (
                start_src is not None and end_src is not None
                and start_src in micro_id_to_pos and end_src in micro_id_to_pos
            ):
                a = micro_id_to_pos[start_src]
                b = micro_id_to_pos[end_src]
                lo, hi = sorted((a, b))
                between_ids = micro_id_seq[lo + 1 : hi]
                swallow_ids = [mid for mid in between_ids if mid in s.macro_excluded_micro_ids]
                if not swallow_ids:
                    swallow_ids = s.macro_swallow_map.get((start_src, end_src), [])
            seg.cache["is_macro_swallow"] = bool(swallow_ids)
            seg.cache["swallow_internal_micro_ids"] = swallow_ids
            s.macro_segments.append(seg)

    # 为兼容旧的外部调试脚本，保留同名代理方法
    def _macro_audit_and_replay(self, current_k_idx: int) -> bool:
        return self._macro_engine.audit_and_replay(current_k_idx)

    def _check_leap_physics(self, tk_start: TurningK, tk_end: TurningK,
                           tk_mid_same: TurningK, tk_pullback: TurningK) -> bool:
        return self._macro_engine._check_leap_physics(tk_start, tk_end, tk_mid_same, tk_pullback)

    def _execute_leap_collapse(self, anchor_idx: int, new_end_idx: int):
        return self._macro_engine._execute_leap_collapse(anchor_idx, new_end_idx)

    # =========================================================================
    # 中枢重播引擎
    # =========================================================================

    def _replay_center_engine_for_segment(self, start_ext_idx: int, start_trig_idx: int,
                                           current_end_idx: int, correct_direction: Direction):
        """发生线段结构变化时，回滚并重播正确的方向，找回被遗漏的中枢，冻结逆势幽灵中枢"""
        s = self.state

        # Step 1: 精准清理（拔根与留种）
        # 属于前一个线段且恰好结束于 start_ext_idx 的中枢必须保留（由 start_k_index < start_ext_idx 保证）
        # 凡是起点落在重播区间 [start_ext_idx, current_end_idx] 之后的中枢，若方向不对则转为幽灵，
        # 若方向一致，则应当被清理掉，因为接下来的重播会重新生成它们。
        new_potential = []
        for center in s.potential_centers:
            if center.start_k_index >= start_ext_idx:
                # 微观重播不再产生幽灵，幽灵目前仅由宏观审计产生
                # 同向中枢不进入 new_potential，等待重播生成
                pass
            else:
                new_potential.append(center)
        s.potential_centers = new_potential

        # Stage 1 重播不直接修改 all_centers/macro_centers，保持解耦

        # Step 2: 状态机洗盘重置
        self._center_engine.rollback()

        # 重置叹息之墙
        last_valid_end_idx = -1
        combined_centers = s.all_centers + s.potential_centers
        for center in reversed(combined_centers):
            if center.start_k_index < start_ext_idx and not getattr(center, 'is_ghost', False):
                # 叹息之墙 = 破窗K（end_k_index 是最后一根在轨K，+1 即为破窗K）
                last_valid_end_idx = center.end_k_index + 1
                break
        s.last_center_end_idx = last_valid_end_idx

        # Step 3: 时空重播（找回被忽略的真中枢）
        segment_boundary_idx = s.turning_ks[-1].k_index

        for i in range(start_ext_idx, current_end_idx + 1):
            bar = s.bars_raw[i]
            if i <= segment_boundary_idx:
                self._center_engine.update(bar, i, force_direction=correct_direction,
                                           force_anchor_idx=start_ext_idx,
                                           force_trigger_idx=start_trig_idx)
                if i == segment_boundary_idx:
                    self._center_engine.seal_on_boundary()
            else:
                self._center_engine.update(bar, i)

    def _replay_centers_for_macro_audit(self):
        """第二阶段重播：宏观审计命中后的中枢维护。"""
        s = self.state
        marks = s.cache.pop("macro_replay_marks", None)
        if not marks:
            return

        start_idx = marks['start_ext_idx']
        swallow_end_idx = marks['swallow_end_idx']
        correct_dir = marks['correct_direction']
        
        # 1. 幽灵迁移
        stay_micro = []
        for c in s.micro_centers:
            if c.end_k_index >= start_idx and c.start_k_index <= swallow_end_idx:
                c.is_ghost = True
                c.source_layer = "ghost"
                c.origin_center_id = c.center_id
                if c not in s.ghost_centers:
                    s.ghost_centers.append(c)
            else:
                stay_micro.append(c)
        s.micro_centers = stay_micro

        # 2. 宏观仓清理
        s.macro_centers = [c for c in s.macro_centers if not (c.end_k_index >= start_idx and c.start_k_index <= swallow_end_idx)]

        # 3. 运行态缓冲区临时备份（宏观重播会污染 potential_centers，需还原）
        old_potential = [deepcopy(c) for c in s.potential_centers]
        s.potential_centers = [c for c in s.potential_centers if c.start_k_index < start_idx]

        # 4. 执行宏观区间重建 (仅重建吞噬区间)
        self._center_engine.rollback()
        s.last_center_end_idx = start_idx

        # 仅重播到吞噬终点，找回宏观级别的主干中枢
        for i in range(start_idx, swallow_end_idx + 1):
            bar = s.bars_raw[i]
            self._center_engine.update(bar, i, force_direction=correct_dir, 
                                       force_anchor_idx=start_idx,
                                       force_trigger_idx=marks.get('start_trig_idx', start_idx))
            if i == swallow_end_idx:
                self._center_engine.seal_on_boundary()

        # 5. 重播产物入库 (Macro Warehouse)
        for c in s.potential_centers:
            if c.start_k_index >= start_idx:
                c.source_layer = "macro"
                if c not in s.macro_centers:
                    s.macro_centers.append(c)
        
        # 6. 还原运行态缓冲区（供后续 K 线继续微观探测）
        s.potential_centers = old_potential
            

    # =========================================================================
    # 属性代理
    # =========================================================================

    @property
    def turning_ks(self) -> List[TurningK]:
        return self.state.macro_turning_ks if self.state.macro_turning_ks else self.state.turning_ks

    @property
    def segments(self) -> List[MooreSegment]:
        return self.state.macro_segments if self.state.macro_segments else self.state.segments

    @property
    def micro_turning_ks(self) -> List[TurningK]:
        return self.state.turning_ks

    @property
    def micro_segments(self) -> List[MooreSegment]:
        return self.state.segments

    @property
    def all_centers(self) -> List[MooreCenter]:
        return self.state.all_centers + self.state.potential_centers

    @property
    def micro_centers(self) -> List[MooreCenter]:
        return self.state.micro_centers

    @property
    def macro_centers(self) -> List[MooreCenter]:
        return self.state.macro_centers

    @property
    def all_centers(self) -> List[MooreCenter]:
        """兼容性接口：映射到 macro_centers"""
        return self.state.macro_centers

    @property
    def ghost_centers(self) -> List[MooreCenter]:
        return self.state.ghost_centers

    @property
    def ghost_forks(self) -> List[tuple]:
        return self.state.macro_ghost_forks

    @property
    def micro_ghost_forks(self) -> List[tuple]:
        return self.state.ghost_forks

    @property
    def refreshed_segments(self) -> list:
        return self.state.refreshed_segments

    @property
    def candidate_tk(self) -> Optional[TurningK]:
        return self.state.candidate_tk

    @property
    def trend_state(self) -> Optional[Direction]:
        return self.state.trend_state

    @property
    def trend_high(self) -> Optional[float]:
        return self.state.trend_high

    @property
    def trend_low(self) -> Optional[float]:
        return self.state.trend_low

    # -------------------------------------------------------------------------
    # 调试属性
    # -------------------------------------------------------------------------

    @property
    def _debug_rule_fail(self) -> dict:
        return self.state.debug_rule_fail

    @property
    def _debug_trigger_count(self) -> int:
        return self.state.debug_trigger_count

    @property
    def _debug_body_filter(self) -> int:
        return self.state.debug_body_filter

    def _check_actual_perfection(self, tk_start: Optional[TurningK], tk_end: TurningK) -> bool:
        """执行实时的结构完善性检查（Rule 3 的 Live 版本，供宏观审判层调用）

        判定标准与 FractalEngine._validate_four_rules 中的 Rule 3 完全对齐：
        A. 已固化中枢（all_centers + potential_centers）：起点落在段内即保护。
        B. 活跃中枢（State 2）：须"名分已立 AND 黑K已过"才构成保护。
           -- is_visible 定性不在此处判断，等固化后由 MooreCenter.is_visible 决定。
        """
        if tk_start is None or tk_end is None:
            return False

        s = self.state
        start_idx = tk_start.k_index
        end_idx   = tk_end.k_index

        # A. 扫描已存在的各种中枢
        all_c = s.all_centers + s.potential_centers
        for c in all_c:
            if start_idx <= c.start_k_index <= end_idx and not getattr(c, 'is_ghost', False):
                return True

        # B. 扫描正在孵化的活动中枢（仅限已确权：名分 + 黑K）
        # 时间截止锚：中枢确认K必须严格早于被评估的候选终点，防止跨时序污染
        if s.center_state >= 2 and s.center_line_k:
            is_confirmed = (s.center_method_found is not None and s.center_black_k_pass)
            if is_confirmed and s.center_line_k_index <= end_idx:
                c_start = s.center_start_k_index
                if start_idx <= c_start <= end_idx:
                    return True

        return False
