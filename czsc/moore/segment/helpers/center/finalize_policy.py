# -*- coding: utf-8 -*-
"""Finalize-time center construction and spacing policy."""

from dataclasses import dataclass

from czsc.py.enum import Direction

from ....objects import MooreCenter


@dataclass(frozen=True)
class FinalizeDecision:
    action: str
    last_center: MooreCenter | None = None


def build_center_candidate(
    *,
    center_id: int,
    center_direction: Direction,
    center_is_visible: bool,
    current_k0,
    center_line_k,
    center_line_k_index: int,
    center_method_found,
    center_upper_rail: float,
    center_lower_rail: float,
    final_start_dt,
    final_start_idx: int,
    center_end_dt,
    center_end_k_index: int,
) -> MooreCenter:
    """Build the immutable candidate object for final spacing policy."""
    type_str = "VISIBLE" if center_is_visible else "INVISIBLE"
    center_line = center_lower_rail if center_direction == Direction.Up else center_upper_rail
    return MooreCenter(
        center_id=center_id,
        source_layer="micro",
        confirm_k_index=center_line_k_index,
        type_name=type_str,
        direction=center_direction,
        anchor_k0=current_k0,
        confirm_k=center_line_k,
        method=center_method_found,
        center_line=center_line,
        upper_rail=center_upper_rail,
        lower_rail=center_lower_rail,
        start_dt=final_start_dt,
        end_dt=center_end_dt,
        start_k_index=final_start_idx,
        end_k_index=center_end_k_index,
    )


def find_last_same_direction_center(potential_centers: list, direction: Direction, center_anchor_idx: int):
    """Find the last non-ghost same-direction center born in the active segment."""
    return next(
        (
            c for c in reversed(potential_centers)
            if c.direction == direction
            and not getattr(c, "is_ghost", False)
            and c.end_k_index >= center_anchor_idx
        ),
        None,
    )


def decide_finalize_policy(candidate: MooreCenter, potential_centers: list, center_anchor_idx: int) -> FinalizeDecision:
    """Decide whether a finalized center should append or extend an existing center."""
    last_center = find_last_same_direction_center(potential_centers, candidate.direction, center_anchor_idx)
    if last_center is None:
        return FinalizeDecision("append")

    if candidate.direction == Direction.Up:
        is_separated = candidate.lower_rail > last_center.upper_rail
    else:
        is_separated = candidate.upper_rail < last_center.lower_rail

    if is_separated:
        return FinalizeDecision("append")
    return FinalizeDecision("extend_existing", last_center)
