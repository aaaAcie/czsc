# -*- coding: utf-8 -*-
"""Shared center window interpretation for A/B daily-segment paths."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Sequence

from czsc.py.enum import Direction, Mark

from ...objects import MooreSegment
from ..utils import seg_end_price
from .algo import find_center


def _turning_index(tk) -> int:
    return tk.turning_k_index if tk.turning_k_index is not None else tk.k_index


def tk_key(tk) -> tuple:
    return (tk.k_index, tk.dt, tk.price, tk.mark.value)


def seg_key(seg: MooreSegment) -> tuple:
    return (tk_key(seg.start_k), tk_key(seg.end_k))


def segment_span_key(segments: Sequence[MooreSegment]) -> Optional[tuple]:
    if not segments:
        return None
    return (tk_key(segments[0].start_k), tk_key(segments[-1].end_k))


@dataclass(frozen=True)
class RawCenterCandidate:
    center: dict
    source_segments: tuple[MooreSegment, ...]
    trend_direction: Direction
    refined_segments: tuple[MooreSegment, ...] = ()


@dataclass(frozen=True)
class CenterWindowEvent:
    raw: RawCenterCandidate
    center_kind: str = ""
    overlap_type: Optional[int] = None
    status: str = ""
    points: dict = field(default_factory=dict)
    high: Optional[float] = None
    low: Optional[float] = None
    owner_segments: tuple[MooreSegment, ...] = ()
    evidence_segments: tuple[MooreSegment, ...] = ()
    owner_span: Optional[tuple] = None
    evidence_span: Optional[tuple] = None
    maturity_span: Optional[tuple] = None
    point_owners: dict = field(default_factory=dict)
    owner_chain: tuple[tuple, ...] = ()
    owner_chain_valid: bool = False
    refined_segments: tuple[MooreSegment, ...] = ()
    invalid_reasons: tuple[str, ...] = ()

    @property
    def valid(self) -> bool:
        return not self.invalid_reasons

    @property
    def is_turning(self) -> bool:
        return self.center_kind == "turning" or self.overlap_type == 0

    @property
    def is_trend_class(self) -> bool:
        return self.center_kind == "trend_class" and self.overlap_type in {1, 3}


def _turning_range(seg: MooreSegment) -> tuple[int, int]:
    start = _turning_index(seg.start_k)
    end = _turning_index(seg.end_k)
    return (min(start, end), max(start, end))


def _desired_owner_mark(point_name: str, trend_direction: Direction) -> Mark:
    down_marks = {"A": Mark.D, "B": Mark.G, "C": Mark.D, "D": Mark.G}
    up_marks = {"A": Mark.G, "B": Mark.D, "C": Mark.G, "D": Mark.D}
    return (down_marks if trend_direction == Direction.Down else up_marks)[point_name]


def _owner_endpoint_for_point(
    point_name: str,
    point: tuple,
    source_segments: Sequence[MooreSegment],
    trend_direction: Direction,
) -> Optional[dict]:
    idx = point[0]
    desired_mark = _desired_owner_mark(point_name, trend_direction)
    matches = []
    for seg in source_segments:
        left, right = _turning_range(seg)
        if not (left <= idx <= right):
            continue
        for side, tk in (("start", seg.start_k), ("end", seg.end_k)):
            if tk.mark == desired_mark:
                matches.append((seg, side, tk))
    if not matches:
        return None
    seg, side, tk = min(matches, key=lambda item: abs(_turning_index(item[2]) - idx))
    return {
        "point": point_name,
        "owner_endpoint": tk_key(tk),
        "owner_endpoint_index": _turning_index(tk),
        "owner_endpoint_side": side,
        "owner_segment": seg_key(seg),
    }


def _owner_keys_valid_for_source(owner_keys: Sequence[tuple], source_segments: Sequence[MooreSegment]) -> bool:
    if len(owner_keys) < 2 or len(owner_keys) != len(set(owner_keys)):
        return False
    source_pairs = {(tk_key(seg.start_k), tk_key(seg.end_k)) for seg in source_segments}
    for left, right in zip(owner_keys, owner_keys[1:]):
        if (left, right) not in source_pairs:
            return False
    return True


def _owner_segments_for_center(center: dict) -> tuple[MooreSegment, ...]:
    evidence_segments = tuple(center.get("segments") or ())
    if center.get("center_kind") == "turning" or center.get("overlap_type") == 0:
        return evidence_segments[:3]
    return evidence_segments


def build_center_window_event(raw: RawCenterCandidate) -> CenterWindowEvent:
    center = raw.center
    evidence_segments = tuple(center.get("segments") or ())
    owner_segments = _owner_segments_for_center(center)
    points = center.get("points") or {}
    invalid_reasons: list[str] = []

    if not evidence_segments:
        invalid_reasons.append("missing_evidence_segments")
    if not owner_segments:
        invalid_reasons.append("missing_owner_segments")

    point_owners = {}
    owner_chain = []
    required_points = ("A", "B", "C", "D") if (
        center.get("overlap_type") == 3 and center.get("status") == "FINAL"
    ) else tuple(points)

    for point_name in ("A", "B", "C", "D"):
        if point_name not in points:
            continue
        owner = _owner_endpoint_for_point(point_name, points[point_name], owner_segments, raw.trend_direction)
        point_owners[point_name] = owner
        if owner is None:
            invalid_reasons.append(f"missing_owner_{point_name}")
            continue
        owner_chain.append(owner["owner_endpoint"])

    for point_name in required_points:
        if point_name not in point_owners or point_owners.get(point_name) is None:
            invalid_reasons.append(f"missing_required_{point_name}")

    if len(owner_chain) != len(set(owner_chain)):
        invalid_reasons.append("duplicate_owner")
    if "B" in point_owners and "D" in point_owners and point_owners["B"] and point_owners["D"]:
        if point_owners["B"]["owner_endpoint"] == point_owners["D"]["owner_endpoint"]:
            invalid_reasons.append("same_owner_B_D")

    owner_chain_valid = _owner_keys_valid_for_source(owner_chain, owner_segments)
    if owner_chain and not owner_chain_valid:
        invalid_reasons.append("owner_chain_not_continuous")

    return CenterWindowEvent(
        raw=raw,
        center_kind=center.get("center_kind", ""),
        overlap_type=center.get("overlap_type"),
        status=center.get("status", ""),
        points=points,
        high=center.get("high"),
        low=center.get("low"),
        owner_segments=owner_segments,
        evidence_segments=evidence_segments,
        owner_span=segment_span_key(owner_segments),
        evidence_span=segment_span_key(evidence_segments),
        point_owners=point_owners,
        owner_chain=tuple(owner_chain),
        owner_chain_valid=owner_chain_valid and not invalid_reasons,
        refined_segments=raw.refined_segments,
        invalid_reasons=tuple(dict.fromkeys(invalid_reasons)),
    )


def _event_owner_within_candidate(event: CenterWindowEvent, owner_segments: Sequence[MooreSegment]) -> bool:
    owner_keys = {seg_key(seg) for seg in owner_segments}
    return bool(event.owner_segments) and all(seg_key(seg) in owner_keys for seg in event.owner_segments)


def find_center_window_events(
    owner_segments: Sequence[MooreSegment],
    evidence_segments: Sequence[MooreSegment],
    ma34,
    trend_direction: Direction,
    center_kind: Optional[str] = None,
) -> list[CenterWindowEvent]:
    events: list[CenterWindowEvent] = []
    if len(evidence_segments) < 4:
        return events
    for local_start in range(max(1, len(evidence_segments) - 2)):
        if evidence_segments[local_start].direction != trend_direction:
            continue
        center = find_center(evidence_segments[local_start:], ma34, trend_direction=trend_direction)
        if center is None:
            continue
        if center_kind is not None and center.get("center_kind") != center_kind:
            continue
        raw = RawCenterCandidate(
            center=center,
            source_segments=tuple(evidence_segments),
            trend_direction=trend_direction,
        )
        event = build_center_window_event(raw)
        if not _event_owner_within_candidate(event, owner_segments):
            continue
        events.append(event)
    return events


def find_latest_center_window_event(
    owner_segments: Sequence[MooreSegment],
    evidence_segments: Sequence[MooreSegment],
    ma34,
    trend_direction: Direction,
    center_kind: Optional[str] = None,
) -> Optional[CenterWindowEvent]:
    events = find_center_window_events(owner_segments, evidence_segments, ma34, trend_direction, center_kind=center_kind)
    if not events:
        return None
    return max(
        events,
        key=lambda event: (
            _span_end_rank(event.owner_segments),
            _span_end_rank(event.evidence_segments),
            _span_start_rank(event.owner_segments),
        ),
    )


def _span_start_rank(segments: Sequence[MooreSegment]) -> int:
    if not segments:
        return -1
    return min(seg.start_k.k_index for seg in segments)


def _span_end_rank(segments: Sequence[MooreSegment]) -> int:
    if not segments:
        return -1
    return max(seg.end_k.k_index for seg in segments)


def strictly_extends_after_event(owner_segments: Sequence[MooreSegment], event: CenterWindowEvent) -> bool:
    if not owner_segments or not event.evidence_segments:
        return False
    owner_keys = {seg_key(seg) for seg in owner_segments}
    if any(seg_key(seg) not in owner_keys for seg in event.evidence_segments):
        return False

    evidence_prices = []
    for seg in event.evidence_segments:
        evidence_prices.extend([seg.start_k.price, seg.end_k.price])
    if not evidence_prices:
        return False

    candidate_end = seg_end_price(owner_segments[-1])
    if owner_segments[0].direction == Direction.Up:
        return candidate_end > max(evidence_prices)
    if owner_segments[0].direction == Direction.Down:
        return candidate_end < min(evidence_prices)
    return False
