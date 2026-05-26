# -*- coding: utf-8 -*-
"""Macro synchronization helpers for SegmentAnalyzer."""

from copy import deepcopy
from dataclasses import dataclass

from czsc.py.enum import Direction, Mark

from ....objects import MooreSegment


@dataclass(frozen=True)
class StableCutoffResult:
    cutoff: int
    stable_cutoff_micro_id: int | None
    pending_leftmost_turning_idx: int


def clone_micro_tk_to_macro(tk):
    """Clone a micro turning K into the macro world and preserve source mapping."""
    ctk = deepcopy(tk)
    ctk.cache = dict(getattr(tk, "cache", {}))
    ctk.cache["source_micro_id"] = tk.cache.get("micro_id")
    return ctk


def compute_stable_cutoff(micro: list, pending_judgements, judgement_nodes: dict) -> StableCutoffResult:
    """Compute the macro-consumable stable frontier."""
    if len(micro) <= 1:
        return StableCutoffResult(-1, None, -1)

    base_cutoff = len(micro) - 2
    id_to_idx = {
        tk.cache.get("micro_id"): i
        for i, tk in enumerate(micro)
        if tk.cache.get("micro_id") is not None
    }

    pending_left = None
    unresolved = {"wait_anchor_start", "wait_anchor_real", "ready_resolve"}
    for node_id in list(pending_judgements):
        node = judgement_nodes.get(node_id)
        if not node or node.stage not in unresolved:
            continue
        idxs = [idx for mid in (node.base_id, node.candidate_id) if (idx := id_to_idx.get(mid)) is not None]
        if node.candidate_id not in id_to_idx:
            return StableCutoffResult(-1, None, -1)
        if idxs:
            cur = min(idxs)
            pending_left = cur if pending_left is None else min(pending_left, cur)

    if pending_left is not None:
        cutoff = min(base_cutoff, pending_left - 1)
        pending_leftmost_turning_idx = pending_left
    else:
        cutoff = base_cutoff
        pending_leftmost_turning_idx = -1

    if cutoff < 0:
        return StableCutoffResult(-1, None, pending_leftmost_turning_idx)

    return StableCutoffResult(
        cutoff=cutoff,
        stable_cutoff_micro_id=micro[cutoff].cache.get("micro_id"),
        pending_leftmost_turning_idx=pending_leftmost_turning_idx,
    )


def build_macro_segments(
    *,
    macro_turning_ks: list,
    micro_turning_ks: list,
    macro_centers: list,
    macro_excluded_micro_ids: set,
    macro_swallow_map: dict,
) -> list:
    """Rebuild macro segments from macro turning Ks and current macro centers."""
    if len(macro_turning_ks) < 2:
        return []

    micro_id_seq = [tk.cache.get("micro_id") for tk in micro_turning_ks if tk.cache.get("micro_id") is not None]
    micro_id_to_pos = {mid: i for i, mid in enumerate(micro_id_seq)}

    macro_segments = []
    for i in range(len(macro_turning_ks) - 1):
        tk1 = macro_turning_ks[i]
        tk2 = macro_turning_ks[i + 1]
        direction = Direction.Up if tk2.mark == Mark.G else Direction.Down
        seg = MooreSegment(symbol=tk1.symbol, start_k=tk1, end_k=tk2, direction=direction)
        seg.centers = []
        for center in macro_centers:
            c_confirm_dt = center.confirm_k.dt if center.confirm_k else center.start_dt
            if not c_confirm_dt:
                continue
            if tk1.dt <= c_confirm_dt <= tk2.dt:
                seg.centers.append(center)

        tk2.is_perfect = bool(seg.centers)
        tk2.maybe_is_fake = not tk2.is_perfect
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
            swallow_ids = [mid for mid in between_ids if mid in macro_excluded_micro_ids]
            if not swallow_ids:
                swallow_ids = macro_swallow_map.get((start_src, end_src), [])
        seg.cache["is_macro_swallow"] = bool(swallow_ids)
        seg.cache["swallow_internal_micro_ids"] = swallow_ids
        macro_segments.append(seg)

    return macro_segments
