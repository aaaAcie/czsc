#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Moore 审计脚本：线段虚实与 ghost 化原因排查"""

import argparse
from dataclasses import dataclass
from typing import List, Optional, Tuple

from czsc.connectors import research
from czsc.moore.analyze import MooreCZSC


@dataclass
class Case:
    symbol: str
    sdt: str
    edt: str
    seg_start: Optional[str] = None
    seg_end: Optional[str] = None
    desc: str = ""


def _d(dt) -> str:
    return dt.strftime("%Y-%m-%d")


def _find_target_segment(engine: MooreCZSC, seg_start: Optional[str], seg_end: Optional[str]):
    segs = getattr(engine, "micro_segments", engine.segments)
    if not seg_start or not seg_end:
        return None, None
    for i, seg in enumerate(segs):
        if _d(seg.start_k.dt) == seg_start and _d(seg.end_k.dt) == seg_end:
            return i, seg
    return None, None


def _unique_centers(centers) -> List:
    uniq = {}
    for c in centers:
        key = (getattr(c, "center_id", None), c.start_k_index, c.end_k_index)
        uniq[key] = c
    return list(uniq.values())


def _collect_overlap_centers(engine: MooreCZSC, seg) -> Tuple[List, List]:
    si, ei = seg.start_k.k_index, seg.end_k.k_index
    micro = [c for c in getattr(engine, "micro_centers", []) if c.start_k_index <= ei and c.end_k_index >= si]
    ghost = [c for c in getattr(engine, "ghost_centers", []) if c.start_k_index <= ei and c.end_k_index >= si]
    return _unique_centers(micro), _unique_centers(ghost)


def _print_basic(engine: MooreCZSC):
    print("\n=== Basic ===")
    print(f"turning_ks={len(engine.turning_ks)} | micro_segments={len(engine.micro_segments)} | macro_segments={len(engine.segments)}")
    print(
        f"micro_centers={len(engine.micro_centers)} | "
        f"macro_centers={len(engine.macro_centers)} | "
        f"ghost_centers={len(engine.ghost_centers)} | "
        f"ghost_forks={len(engine.ghost_forks)}"
    )
    fail = engine._debug_rule_fail
    total_fail = sum(fail.values())
    print(
        f"rule_fail_total={total_fail} | "
        f"R1={fail.get(1, 0)} | R1.1={fail.get(1.1, 0)} | R2={fail.get(2, 0)} | R3={fail.get(3, 0)}"
    )


def _print_segments(engine: MooreCZSC, limit: int = 40):
    print("\n=== Micro Segments ===")
    segs = getattr(engine, "micro_segments", engine.segments)
    for i, seg in enumerate(segs[:limit]):
        print(
            f"v{i}-v{i+1} | {_d(seg.start_k.dt)} -> {_d(seg.end_k.dt)} | "
            f"perfect={seg.is_perfect} | seg.centers={len(getattr(seg, 'centers', []))}"
        )
    if len(segs) > limit:
        print(f"... ({len(segs) - limit} more)")


def _print_target_details(engine: MooreCZSC, seg_idx: int, seg):
    print("\n=== Target Segment ===")
    print(
        f"idx=v{seg_idx}-v{seg_idx+1} | {_d(seg.start_k.dt)} -> {_d(seg.end_k.dt)} | "
        f"k_idx=({seg.start_k.k_index},{seg.end_k.k_index}) | "
        f"perfect={seg.is_perfect} | seg.centers={len(getattr(seg, 'centers', []))}"
    )

    micro_hit, ghost_hit = _collect_overlap_centers(engine, seg)

    print("\n[Overlap in micro_centers]")
    if not micro_hit:
        print("  none")
    else:
        for c in micro_hit:
            print(
                f"  id={getattr(c,'center_id',None)} | {_d(c.start_dt)}->{_d(c.end_dt)} | "
                f"idx=({c.start_k_index},{c.end_k_index}) | ghost={getattr(c,'is_ghost',None)} | "
                f"visible={getattr(c,'is_visible',None)} | method={getattr(c,'method',None)}"
            )

    print("\n[Overlap in ghost_centers]")
    if not ghost_hit:
        print("  none")
    else:
        for c in ghost_hit:
            print(
                f"  id={getattr(c,'center_id',None)} | {_d(c.start_dt)}->{_d(c.end_dt)} | "
                f"idx=({c.start_k_index},{c.end_k_index}) | ghost={getattr(c,'is_ghost',None)} | "
                f"visible={getattr(c,'is_visible',None)} | method={getattr(c,'method',None)} | "
                f"source_layer={getattr(c,'source_layer',None)}"
            )


def _print_ghost_reason(engine: MooreCZSC, seg):
    print("\n=== Ghost Reason Chain ===")
    seg_start = seg.start_k.k_index
    seg_end = seg.end_k.k_index

    ghost_hits = [c for c in getattr(engine, "ghost_centers", []) if c.start_k_index <= seg_end and c.end_k_index >= seg_start]
    ghost_hits = _unique_centers(ghost_hits)
    if not ghost_hits:
        print("target segment has no overlap with ghost centers")
        return

    print("ghost overlap centers:")
    for c in ghost_hits:
        print(
            f"  center_id={getattr(c,'center_id',None)} | "
            f"range={_d(c.start_dt)}->{_d(c.end_dt)} | idx=({c.start_k_index},{c.end_k_index})"
        )

    # 查找可能触发 ghost 迁移的宏观吞噬区间：
    # replay_centers_for_macro_audit 的条件是 center 区间与 [start_ext_idx, swallow_end_idx] 有交集。
    macro_forks = getattr(engine, "ghost_forks", []) or []
    if not macro_forks:
        print("no macro ghost forks found; cannot map to a swallow chain")
        return

    print("matched macro swallow windows:")
    matched = 0
    for anchor_tk, consumed in macro_forks:
        points = [anchor_tk] + list(consumed)
        idxs = [p.k_index for p in points]
        left, right = min(idxs), max(idxs)

        hit_any = False
        for c in ghost_hits:
            if c.end_k_index >= left and c.start_k_index <= right:
                hit_any = True
                break
        if hit_any:
            matched += 1
            print(
                f"  anchor={_d(anchor_tk.dt)} idx={anchor_tk.k_index} | "
                f"swallow_window=({left},{right}) | consumed={len(consumed)}"
            )
            print("    consumed_points:", ", ".join([f"{_d(t.dt)}({t.mark.name})" for t in consumed]))

    if matched == 0:
        print("  none directly matched by current ghost_forks window (possible due to later replay overwrite)")

    print(
        "\ninterpretation: center was first used to make the segment perfect, then moved to ghost warehouse "
        "during macro audit replay. current visualization still reads seg.is_perfect, so line stays solid."
    )


def run_case(case: Case, rounds: int, valid_gate: bool, list_only: bool):
    print(f"\n>>> {case.symbol} {case.sdt}-{case.edt} {case.desc}".strip())
    bars = research.get_raw_bars_origin(case.symbol, sdt=case.sdt, edt=case.edt)
    engine = MooreCZSC(
        bars,
        ma34_cross_as_valid_gate=valid_gate,
        audit_link_rounds=rounds,
    )

    _print_basic(engine)
    _print_segments(engine)
    if list_only:
        return

    seg_idx, seg = _find_target_segment(engine, case.seg_start, case.seg_end)
    if seg is None:
        print(f"\nTarget segment not found: {case.seg_start} -> {case.seg_end}")
        return

    _print_target_details(engine, seg_idx, seg)
    _print_ghost_reason(engine, seg)


def main():
    parser = argparse.ArgumentParser(description="Moore 线段/中枢/ghost 审计脚本")
    parser.add_argument("--symbol", default="002346")
    parser.add_argument("--sdt", default="20180901")
    parser.add_argument("--edt", default="20200928")
    parser.add_argument("--seg-start", default="2018-11-19")
    parser.add_argument("--seg-end", default="2018-11-30")
    parser.add_argument("--audit-link-rounds", type=int, default=3)
    parser.add_argument("--valid-gate", action="store_true", help="开启 ma34_cross_as_valid_gate（默认关闭）")
    parser.add_argument("--list-only", action="store_true", help="仅列出微观线段，不做目标段分析")
    args = parser.parse_args()

    case = Case(
        symbol=args.symbol,
        sdt=args.sdt,
        edt=args.edt,
        seg_start=args.seg_start,
        seg_end=args.seg_end,
    )
    run_case(case, rounds=args.audit_link_rounds, valid_gate=args.valid_gate, list_only=args.list_only)


if __name__ == "__main__":
    main()
