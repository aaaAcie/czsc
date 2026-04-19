# -*- coding: utf-8 -*-
"""
摩尔缠论统一审计引擎 (Unified Audit Engine)
------------------------------------------
沉淀调试能力，涵盖：
1. 物理分水岭审计 (TurningK & TriggerK)
2. 线段完美性审计 (法则三：独立脱离测试详情)
3. 中枢物理边界审计 (右边界截断、左边界防火墙)
4. 趋势穿透与异常审计 (Ghost Forks)
5. 技术指标动力学审计 (K 线实时 MA 详情)
"""
import argparse
import collections
import os
import sys
from datetime import datetime
from typing import List, Dict, Any

from czsc.moore.analyze import MooreCZSC
from czsc.connectors import research
from czsc.py.enum import Direction, Mark
from czsc.py.objects import RawBar
import czsc.moore.segment.center as mod_center
import czsc.moore.segment.fractal as mod_fractal

def audit_300371_special():
    """汇中股份 300371 全量深度审计模板"""
    symbol = '300371'
    sdt, edt = '20190122', '20200828'
    # 包含了最近讨论的 03-04, 03-23, 05-11, 05-12, 05-15 等关键节点
    target_dates = ['2020-02-26', '2020-03-04', '2020-03-23', '2020-05-11', '2020-05-12', '2020-05-15']
    
    flags = {
        'audit_center': True,
        'audit_fractal': True,
        'show_ghosts': True,
        'detail_seg': True,
    }
    unified_audit(symbol, sdt, edt, target_dates, flags)

def _fmt_dt(dt):
    return dt.strftime('%Y-%m-%d') if dt else 'None'


def _audit_target_segment(engine: MooreCZSC, seg_start: str, seg_end: str):
    """定点审计：解释某条线段为何实/虚，以及是否被 ghost 化。"""
    segs = getattr(engine, 'micro_segments', engine.segments)
    target = None
    target_i = None
    for i, seg in enumerate(segs):
        if seg.start_k.dt.strftime('%Y-%m-%d') == seg_start and seg.end_k.dt.strftime('%Y-%m-%d') == seg_end:
            target = seg
            target_i = i
            break

    print("\n" + "🔍 SECTION 4: 目标线段 ghost 归因审计")
    print("-" * 120)
    if target is None:
        print(f"未找到目标线段: {seg_start} -> {seg_end}")
        return

    print(
        f"Target: v{target_i}-v{target_i+1} | {seg_start} -> {seg_end} | "
        f"Perfect={target.is_perfect} | seg.centers={len(getattr(target, 'centers', []))} | "
        f"k_idx=({target.start_k.k_index},{target.end_k.k_index})"
    )

    si, ei = target.start_k.k_index, target.end_k.k_index
    micro_hit = [c for c in engine.micro_centers if c.start_k_index <= ei and c.end_k_index >= si]
    ghost_hit = [c for c in engine.ghost_centers if c.start_k_index <= ei and c.end_k_index >= si]

    uniq = {}
    for c in micro_hit + ghost_hit:
        key = (getattr(c, 'center_id', None), c.start_k_index, c.end_k_index)
        uniq[key] = c
    centers = list(uniq.values())

    if not centers:
        print("重叠中枢: 无")
    else:
        print("重叠中枢:")
        for c in sorted(centers, key=lambda x: (x.start_k_index, x.end_k_index)):
            print(
                f"  C#{getattr(c, 'center_id', None)} | {_fmt_dt(c.start_dt)}->{_fmt_dt(c.end_dt)} | "
                f"idx=({c.start_k_index},{c.end_k_index}) | ghost={getattr(c,'is_ghost',None)} | "
                f"visible={getattr(c,'is_visible',None)} | method={getattr(c,'method',None)} | "
                f"layer={getattr(c,'source_layer',None)}"
            )

    forks = getattr(engine, 'ghost_forks', []) or []
    if not forks:
        print("ghost_forks: 无（无法映射吞噬窗口）")
    else:
        print("匹配到的吞噬窗口:")
        matched = 0
        for anchor_tk, consumed in forks:
            points = [anchor_tk] + list(consumed)
            idxs = [p.k_index for p in points]
            left, right = min(idxs), max(idxs)
            if any(c.end_k_index >= left and c.start_k_index <= right for c in centers):
                matched += 1
                consumed_txt = ", ".join([f"{_fmt_dt(t.dt)}({t.mark.name})" for t in consumed])
                print(f"  anchor={_fmt_dt(anchor_tk.dt)} idx={anchor_tk.k_index} | window=({left},{right})")
                print(f"    consumed: {consumed_txt}")
        if matched == 0:
            print("  无直接命中（可能被后续重播覆盖）")

    has_ghost = any(getattr(c, 'is_ghost', False) for c in centers)
    if has_ghost:
        print("结论: 这条线段先因中枢被确权为实线，后有重叠中枢被迁入 ghost 仓；is_perfect 不会自动回退。")
    else:
        print("结论: 这条线段当前重叠中枢均未 ghost 化；实线判定与当前结构一致。")


def unified_audit(
    symbol: str, sdt: str, edt: str, target_dates: List[str], flags: Dict[str, bool],
    seg_start: str = None, seg_end: str = None,
    audit_link_rounds: int = 5,
    ma34_cross_as_valid_gate: bool = True,
    replay_centers_after_macro_swallow: bool = True,
):
    """
    统一审计逻辑入口
    """
    print(f"\n{'='*120}")
    print(f" 🛡️  MOORE UNIFIED AUDIT ENGINE | {symbol} | {sdt} - {edt}")
    print(f" 🎯 Focus Dates: {target_dates}")
    print(f"{'='*120}\n")

    # 1. 指针备份 (Monkey Patching)
    orig_center = {
        "update": mod_center.CenterEngine.update,
    }

    # --- 审计钩子定义 (SECTION 4 增强) ---
    def center_audit_update(self, bar: RawBar, k_index: int, **kwargs):
        curr_dt = str(bar.dt)
        hit = any(d in curr_dt for d in target_dates)
        if hit:
            ma5 = bar.cache.get('ma5', 0)
            ma34 = bar.cache.get('ma34', 0)
            # 实时动力学计算
            c_dir = self.s.center_direction if self.s.center_state > 0 else Direction.Down
            if c_dir == Direction.Up:
                is_pure = min(bar.open, bar.close) > ma5
                is_break = min(bar.open, bar.close) < ma5
            else:
                is_pure = max(bar.open, bar.close) < ma5
                is_break = max(bar.open, bar.close) > ma5
            
            print(f"  [📈 指标审计👉 {curr_dt}] State:{self.s.center_state} | MA5:{ma5:.2f} | MA34:{ma34:.2f} | "
                  f"Entity:({min(bar.open,bar.close):.2f}-{max(bar.open,bar.close):.2f}) | Pure:{is_pure} | Break:{is_break}")
        
        return orig_center["update"](self, bar, k_index, **kwargs)

    if flags.get('audit_center'):
        mod_center.CenterEngine.update = center_audit_update

    try:
        os.environ['CZSC_USE_PYTHON'] = '1'
        bars = research.get_raw_bars_origin(symbol, sdt=sdt, edt=edt)
        if not bars:
            print(f"❌ 错误: 未能获取到标的 {symbol} 的 K 线数据")
            return

        engine = MooreCZSC(
            bars,
            audit_link_rounds=audit_link_rounds,
            ma34_cross_as_valid_gate=ma34_cross_as_valid_gate,
            replay_centers_after_macro_swallow=replay_centers_after_macro_swallow,
        )
        s = engine.segment_analyzer.state

        # =========================================================================
        # 0. 物理分水岭审计 (TurningK & TriggerK)
        # =========================================================================
        print("\n" + "🔍 SECTION 0: 物理分水岭与转折审计 (TurningK & TriggerK)")
        print("-" * 120)
        print(f"{'编号':<4} | {'极值日期':<10} | {'触发日期 (Trig)':<15} | {'类型':<4} | {'极值价格':<8} | {'状态'}")
        print("-" * 120)
        for i, tk in enumerate(engine.turning_ks):
            status = "🔒 锁定" if tk.is_locked else ("✅ 验证" if tk.is_valid else "⏳ 候选")
            trig_dt = tk.trigger_k.dt.strftime('%m-%d') if tk.trigger_k else "None"
            print(f"TK#{i+1:02d} | {tk.dt.strftime('%m-%d'):<10} | {trig_dt:<15} | {tk.mark.name:<4} | {tk.price:8.2f} | {status}")

        # =========================================================================
        # 1. 线段完美性 (法则三) 深度审计
        # =========================================================================
        print("\n" + "🔍 SECTION 1: 线段结构与完美性审计 (虚实分析)")
        print("-" * 120)
        print(f"{'编号':<4} | {'起始(极值)':<10} | {'结束(极值)':<10} | {'方向':<4} | {'状态':<6} | {'详细原因报告'}")
        print("-" * 120)
        
        for i, seg in enumerate(engine.segments):
            status = "✅ 完美" if seg.is_perfect else "⚠️ 虚线"
            detail = ""
            if not seg.is_perfect:
                if not seg.centers:
                    detail = "线段内无任何确立中枢"
                else:
                    max_upper = max(c.upper_rail for c in seg.centers)
                    min_lower = min(c.lower_rail for c in seg.centers)
                    t_tk = seg.start_k if seg.direction == Direction.Up else seg.end_k
                    b_tk = seg.end_k if seg.direction == Direction.Up else seg.start_k
                    # 检查是否侵入轨道
                    t_hit = any(b.low <= max_upper for b in s.bars_raw[t_tk.k_index:t_tk.k_index+2] if seg.sdt <= b.dt <= seg.edt)
                    b_hit = any(b.high >= min_lower for b in s.bars_raw[max(0, b_tk.k_index-1):b_tk.k_index+1] if seg.sdt <= b.dt <= seg.edt)
                    reasons = []
                    if t_hit: reasons.append(f"顶侵轨(>{max_upper:.2f})")
                    if b_hit: reasons.append(f"底侵轨(<{min_lower:.2f})")
                    detail = " & ".join(reasons) if reasons else "内部中枢确立但不满足脱离深度"
            else:
                 detail = f"包含 {len(seg.centers)} 个活跃中枢"

            print(f"Seg#{i+1:02d} | {seg.sdt.strftime('%m-%d'):<10} | {seg.edt.strftime('%m-%d'):<10} | {seg.direction.name:<4} | {status:<6} | {detail}")

        # =========================================================================
        # 2. 中枢系统审计
        # =========================================================================
        print("\n" + "🔍 SECTION 2: 中枢系统深度审计 (名分与归属)")
        print("-" * 120)
        print(f"{'编号':<4} | {'起始时刻':<10} | {'结束时刻':<10} | {'方向':<4} | {'方式':<10} | {'轨道范围':<15} | {'状态/归属详情'}")
        print("-" * 120)
        
        all_centers = s.all_centers + s.potential_centers
        for i, c in enumerate(all_centers):
            ghost = "👻 幽灵" if getattr(c, 'is_ghost', False) else "✅ 正常"
            belong = "游离"
            for si, seg in enumerate(engine.segments):
                if seg.sdt <= c.start_dt and (c.confirm_k.dt if c.confirm_k else c.start_dt) <= seg.edt:
                    belong = f"Seg#{si+1}"
                    if c.direction != seg.direction: belong += "(❌异类)"
                    break
            print(f"C#{i:02d} | {c.start_dt.strftime('%m-%d'):<10} | {c.end_dt.strftime('%m-%d'):<10} | {c.direction.name:<4} | {c.method:<10} | {c.lower_rail:6.2f}-{c.upper_rail:6.2f} | {ghost} ({belong})")

        # =========================================================================
        # 3. 趋势穿透审计
        # =========================================================================
        if flags.get('show_ghosts') and s.ghost_forks:
            print("\n" + "🔍 SECTION 3: 趋势穿透与锚点吞噬审计 (Ghost Forks)")
            print("-" * 120)
            for fork_tk, consumed in s.ghost_forks:
                print(f"🔱 吞噬锚点: {fork_tk.dt} | 方向: {fork_tk.mark.name}")
                for ck in consumed:
                    print(f"    - 被抹除极值: {ck.dt} | {ck.mark.name} | Price: {ck.price:.2f}")

        if seg_start and seg_end:
            _audit_target_segment(engine, seg_start, seg_end)

    finally:
        mod_center.CenterEngine.update = orig_center["update"]
        print(f"\n{'='*120}")
        print(" ✅ 审计分析报告生成完毕")
        print(f"{'='*120}\n")

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="摩尔缠论统一审计引擎")
    subparsers = parser.add_subparsers(dest='command')
    subparsers.add_parser('scan_300371', help='汇中股份 300371 全量深度诊断')
    gen = subparsers.add_parser('generic', help='通用标的审计')
    gen.add_argument('--symbol', required=True)
    gen.add_argument('--sdt', default='20200101')
    gen.add_argument('--edt', default='20200828')
    gen.add_argument('--target', nargs='+', default=[])
    gen.add_argument('--seg-start', default=None, help='目标线段起点日期，格式 YYYY-MM-DD')
    gen.add_argument('--seg-end', default=None, help='目标线段终点日期，格式 YYYY-MM-DD')
    gen.add_argument('--audit-link-rounds', type=int, default=5, help='宏观审计回放轮数')
    gen.add_argument('--ma34-cross-as-valid-gate', action='store_true', help='开启法则2作为成立门槛（默认关闭）')
    gen.add_argument(
        '--disable-replay-centers-after-macro-swallow',
        action='store_true',
        help='命中宏观吞噬后，不重播吞噬窗口中枢'
    )
    args = parser.parse_args()

    if args.command == 'scan_300371':
        audit_300371_special()
    elif args.command == 'generic':
        unified_audit(
            args.symbol, args.sdt, args.edt, args.target,
            {'audit_center': True, 'show_ghosts': True},
            seg_start=args.seg_start, seg_end=args.seg_end,
            audit_link_rounds=args.audit_link_rounds,
            ma34_cross_as_valid_gate=args.ma34_cross_as_valid_gate,
            replay_centers_after_macro_swallow=not args.disable_replay_centers_after_macro_swallow,
        )
    else:
        parser.print_help()
