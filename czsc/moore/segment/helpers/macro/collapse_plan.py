# -*- coding: utf-8 -*-
"""Pure planning for macro leap collapse state updates."""

from dataclasses import dataclass
from typing import Optional

from czsc.py.enum import Direction, Mark


@dataclass(frozen=True)
class LeapCollapsePlan:
    tk_anchor: object
    tk_new_end: object
    ghost_nodes: list
    excluded_micro_ids: set
    swallow_key: Optional[tuple]
    swallow_internal_ids: list
    macro_ghost_fork: tuple
    new_turning_ks: list
    replay_marks: dict


def build_leap_collapse_plan(macro_turning_ks: list, anchor_idx: int, new_end_idx: int) -> LeapCollapsePlan | None:
    """Compute all macro state changes needed for a leap collapse."""
    tk_anchor = macro_turning_ks[anchor_idx]
    tk_new_end = macro_turning_ks[new_end_idx]
    ghost_nodes = macro_turning_ks[anchor_idx + 1 : new_end_idx]
    if not ghost_nodes:
        return None

    excluded_micro_ids = {
        gtk.cache.get("source_micro_id")
        for gtk in ghost_nodes
        if gtk.cache.get("source_micro_id") is not None
    }

    start_src = tk_anchor.cache.get("source_micro_id")
    end_src = tk_new_end.cache.get("source_micro_id")
    swallow_key = None
    swallow_internal_ids = []
    if start_src is not None and end_src is not None:
        swallow_key = (start_src, end_src)
        swallow_internal_ids = [
            gtk.cache.get("source_micro_id")
            for gtk in ghost_nodes
            if gtk.cache.get("source_micro_id") is not None
        ]

    new_turning_ks = list(macro_turning_ks[: anchor_idx + 1])
    new_turning_ks.append(tk_new_end)
    for i in range(new_end_idx + 1, len(macro_turning_ks)):
        new_turning_ks.append(macro_turning_ks[i])

    correct_dir = Direction.Up if tk_new_end.mark == Mark.G else Direction.Down
    replay_marks = {
        "start_ext_idx": tk_anchor.k_index,
        "swallow_end_idx": tk_new_end.k_index,
        "correct_direction": correct_dir,
        "start_trig_idx": tk_anchor.turning_k_index if tk_anchor.turning_k_index is not None else tk_anchor.k_index,
    }

    return LeapCollapsePlan(
        tk_anchor=tk_anchor,
        tk_new_end=tk_new_end,
        ghost_nodes=ghost_nodes,
        excluded_micro_ids=excluded_micro_ids,
        swallow_key=swallow_key,
        swallow_internal_ids=swallow_internal_ids,
        macro_ghost_fork=(tk_anchor, sorted(ghost_nodes, key=lambda t: t.k_index)),
        new_turning_ks=new_turning_ks,
        replay_marks=replay_marks,
    )
