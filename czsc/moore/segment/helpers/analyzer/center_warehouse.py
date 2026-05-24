# -*- coding: utf-8 -*-
"""Pure-ish center warehouse helpers for SegmentAnalyzer."""

from dataclasses import dataclass, field
from typing import Set


@dataclass
class CenterReplayContext:
    """Micro replay bookkeeping for centers invalidated by the replay window."""

    start_idx: int
    end_idx: int
    removed_ids: Set[int] = field(default_factory=set)


@dataclass
class CenterReplayPlan:
    context: CenterReplayContext
    micro_centers: list
    potential_centers: list
    macro_centers: list


def get_center_confirm_dt(center):
    return center.confirm_k.dt if center.confirm_k else center.start_dt


def center_in_replay_window(center, start_idx: int, end_idx: int) -> bool:
    return start_idx <= center.start_k_index <= end_idx


def filter_centers_by_ids(centers: list, removed_ids: set) -> list:
    return [c for c in centers if getattr(c, "center_id", None) not in removed_ids]


def build_micro_center_replay_plan(
    *,
    micro_centers: list,
    potential_centers: list,
    macro_centers: list,
    start_idx: int,
    end_idx: int,
) -> CenterReplayPlan:
    """Remove stale centers born inside a micro replay window."""
    ctx = CenterReplayContext(start_idx=start_idx, end_idx=end_idx)
    keep_micro = []
    for center in micro_centers:
        if center_in_replay_window(center, start_idx, end_idx):
            ctx.removed_ids.add(center.center_id)
            continue
        keep_micro.append(center)

    if not ctx.removed_ids:
        # Fast path: no centers are invalidated, so callers can keep the original warehouse references.
        return CenterReplayPlan(ctx, micro_centers, potential_centers, macro_centers)

    keep_potential = filter_centers_by_ids(potential_centers, ctx.removed_ids)
    keep_macro = [
        c for c in macro_centers
        if not (
            getattr(c, "center_id", None) in ctx.removed_ids
            and getattr(c, "source_layer", "") != "macro"
        )
    ]
    return CenterReplayPlan(ctx, keep_micro, keep_potential, keep_macro)


def build_valid_owner_keys(segments: list) -> set:
    return {
        (seg.start_k.cache.get("micro_id"), seg.end_k.cache.get("micro_id"))
        for seg in segments
        if seg.start_k.cache.get("micro_id") is not None and seg.end_k.cache.get("micro_id") is not None
    }


def collect_pending_owner_ids(pending_judgements, judgement_nodes: dict) -> set:
    unresolved = {"wait_anchor_start", "wait_anchor_real", "ready_resolve"}
    pending_owner_ids = set()
    for node_id in list(pending_judgements):
        node = judgement_nodes.get(node_id)
        if not node or node.stage not in unresolved:
            continue
        for mid in (node.base_id, node.candidate_id, getattr(node, "c_candidate_id", None)):
            if mid is not None:
                pending_owner_ids.add(mid)
    return pending_owner_ids


def find_owner_key_for_center(center, segments: list, valid_owner_keys: set) -> tuple | None:
    c_confirm_dt = get_center_confirm_dt(center)
    if not c_confirm_dt:
        return None

    for seg in segments:
        if not (seg.start_k.dt <= c_confirm_dt <= seg.end_k.dt):
            continue
        owner_key = (seg.start_k.cache.get("micro_id"), seg.end_k.cache.get("micro_id"))
        if owner_key in valid_owner_keys:
            return owner_key
        return None
    return None
