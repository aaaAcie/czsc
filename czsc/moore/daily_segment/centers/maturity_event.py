# -*- coding: utf-8 -*-
"""Shadow-B daily maturity events.

This module is intentionally side-effect free.  It turns existing daily-center
results into explicit maturity events for audit/plot overlays without changing
the production daily-segment commit path.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Optional, Sequence

from czsc.py.enum import Direction, Mark

from ..objects import DailySegment
from ..utils import seg_end_price, seg_start_price
from .algo import find_center
from ..helpers.commit import (
    WindowCandidate,
    candidates_from_start,
    check_daily_segment_independence,
    find_reverse_confirmation_candidates,
)


@dataclass(frozen=True)
class CenterOwnerEvidence:
    point_owners: dict = field(default_factory=dict)
    owner_chain: list[tuple] = field(default_factory=list)
    raw_owner_chain: list[tuple] = field(default_factory=list)
    owner_chain_valid: bool = False
    owner_chain_duplicate: bool = False
    invalid_reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class CenterMaturityEvent:
    candidate: WindowCandidate
    center: Optional[dict] = None
    center_kind: str = "none"
    overlap_type: Optional[int] = None
    status: str = ""
    high: Optional[float] = None
    low: Optional[float] = None
    points: dict = field(default_factory=dict)
    owner_evidence: CenterOwnerEvidence = field(default_factory=CenterOwnerEvidence)
    independence_kind: str = ""
    has_maturity_barrier: bool = False
    requires_new_extreme: bool = False
    new_extreme_ok: Optional[bool] = None
    maturity_end_offset: Optional[int] = None
    maturity_end_segment: object = None
    third_entry_index: Optional[int] = None
    invalid_reasons: tuple[str, ...] = ()

    @property
    def independent(self) -> bool:
        if self.center_kind == "none":
            return True
        return self.has_maturity_barrier


@dataclass(frozen=True)
class CandidateEventTrace:
    primary: WindowCandidate
    event: CenterMaturityEvent
    reverse_candidates: tuple[WindowCandidate, ...] = ()
    ignored_reverse_candidates: tuple[WindowCandidate, ...] = ()
    reverse_events: tuple[CenterMaturityEvent, ...] = ()
    selected: bool = False
    selection_reason: str = ""
    chain_confirmed_by: Optional[tuple] = None
    chain_confirm_kind: str = ""


@dataclass(frozen=True)
class ShadowBDailyPlan:
    daily_segments: tuple[DailySegment, ...] = ()
    traces: tuple[CandidateEventTrace, ...] = ()
    center_events: tuple[CenterMaturityEvent, ...] = ()
    invalid_center_events: tuple[CenterMaturityEvent, ...] = ()
    maturity_events: tuple[CenterMaturityEvent, ...] = ()
    invalid_events: tuple[CenterMaturityEvent, ...] = ()


def _turning_index(tk) -> int:
    return tk.turning_k_index if tk.turning_k_index is not None else tk.k_index


def _tk_key(tk) -> tuple:
    return (tk.k_index, tk.dt, tk.price, tk.mark.value)


def _seg_key(seg) -> tuple:
    return (_tk_key(seg.start_k), _tk_key(seg.end_k))


def _turning_range(seg) -> tuple[int, int]:
    start = _turning_index(seg.start_k)
    end = _turning_index(seg.end_k)
    return (min(start, end), max(start, end))


def _desired_owner_mark(point_name: str, trend_direction: Direction) -> Mark:
    down_marks = {"A": Mark.D, "B": Mark.G, "C": Mark.D, "D": Mark.G}
    up_marks = {"A": Mark.G, "B": Mark.D, "C": Mark.G, "D": Mark.D}
    return (down_marks if trend_direction == Direction.Down else up_marks)[point_name]


def _owner_endpoint_for_point(point_name: str, point: tuple, source_segments: Sequence, trend_direction: Direction) -> Optional[dict]:
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
        "owner_endpoint": _tk_key(tk),
        "owner_endpoint_index": _turning_index(tk),
        "owner_endpoint_side": side,
        "owner_segment": _seg_key(seg),
    }


def _owner_keys_valid_for_source(owner_keys: Sequence[tuple], source_segments: Sequence) -> bool:
    if len(owner_keys) < 2 or len(owner_keys) != len(set(owner_keys)):
        return False
    source_pairs = {(_tk_key(seg.start_k), _tk_key(seg.end_k)) for seg in source_segments}
    for left, right in zip(owner_keys, owner_keys[1:]):
        if (left, right) not in source_pairs:
            return False
    return True


def _owner_evidence_for_center(center: dict, source_segments: Sequence, trend_direction: Direction) -> CenterOwnerEvidence:
    points = center.get("points") or {}
    point_owners = {}
    owner_chain = []
    invalid_reasons = []
    required_points = ("A", "B", "C", "D") if center.get("overlap_type") == 3 and center.get("status") == "FINAL" else tuple(points)

    for point_name in ("A", "B", "C", "D"):
        if point_name not in points:
            continue
        owner = _owner_endpoint_for_point(point_name, points[point_name], source_segments, trend_direction)
        point_owners[point_name] = owner
        if owner is None:
            invalid_reasons.append(f"missing_owner_{point_name}")
            continue
        owner_chain.append(owner["owner_endpoint"])

    for point_name in required_points:
        if point_name not in point_owners or point_owners.get(point_name) is None:
            invalid_reasons.append(f"missing_required_{point_name}")

    duplicate = len(owner_chain) != len(set(owner_chain))
    if duplicate:
        invalid_reasons.append("duplicate_owner")
    if "B" in point_owners and "D" in point_owners and point_owners["B"] and point_owners["D"]:
        if point_owners["B"]["owner_endpoint"] == point_owners["D"]["owner_endpoint"]:
            invalid_reasons.append("same_owner_B_D")

    valid = _owner_keys_valid_for_source(owner_chain, source_segments)
    if not valid:
        invalid_reasons.append("owner_chain_not_continuous")

    return CenterOwnerEvidence(
        point_owners=point_owners,
        owner_chain=owner_chain,
        raw_owner_chain=list(owner_chain),
        owner_chain_valid=valid and not invalid_reasons,
        owner_chain_duplicate=duplicate,
        invalid_reasons=tuple(dict.fromkeys(invalid_reasons)),
    )


def _third_segment_entry_index(center: dict, ma34) -> Optional[int]:
    if center.get("overlap_type") != 3:
        return None
    points = center.get("points") or {}
    if not {"A", "B", "C", "D"} <= set(points):
        return None
    a_val = points["A"][1]
    b_val = points["B"][1]
    c_idx = points["C"][0]
    d_idx = points["D"][0]
    low = min(a_val, b_val)
    high = max(a_val, b_val)
    for idx in range(c_idx, d_idx + 1):
        if idx >= len(ma34):
            break
        ma_val = ma34[idx]
        if ma_val is not None and low < ma_val < high:
            return idx
    return d_idx


def _center_source_threshold(center: dict, candidate: WindowCandidate) -> tuple[float, float]:
    center_segments = center.get("segments") or candidate.segments
    prices = []
    for seg in center_segments:
        prices.extend([seg.start_k.price, seg.end_k.price])
    return min(prices), max(prices)


def _find_new_extreme_after_center(center: dict, candidate: WindowCandidate) -> tuple[Optional[int], object]:
    center_segments = center.get("segments") or []
    local_start = len(center_segments)
    if local_start >= len(candidate.segments):
        return None, None
    low, high = _center_source_threshold(center, candidate)
    for local_idx, seg in enumerate(candidate.segments[local_start:], start=local_start):
        price = seg_end_price(seg)
        if candidate.direction == Direction.Up and price > high:
            return candidate.start_offset + local_idx + 1, seg
        if candidate.direction == Direction.Down and price < low:
            return candidate.start_offset + local_idx + 1, seg
    return None, None


def _event_from_center(
    candidate: WindowCandidate,
    center: dict,
    ma34,
    source_segments: Sequence,
    trend_direction: Direction,
) -> CenterMaturityEvent:
    owner_evidence = _owner_evidence_for_center(center, source_segments, trend_direction)
    center_kind = center.get("center_kind", "")
    overlap_type = center.get("overlap_type")
    invalid_reasons = list(owner_evidence.invalid_reasons)
    has_maturity_barrier = False
    requires_new_extreme = False
    new_extreme_ok = None
    maturity_end_offset = None
    maturity_end_segment = None
    independence_kind = ""

    if overlap_type == 0 or center_kind == "turning":
        has_maturity_barrier = True
        requires_new_extreme = False
        new_extreme_ok = None
        maturity_end_offset = candidate.end_offset
        maturity_end_segment = candidate.segments[-1]
        independence_kind = "third_buy_sell"
    elif center_kind == "trend_class" and overlap_type in {1, 3}:
        requires_new_extreme = True
        if owner_evidence.owner_chain_valid:
            maturity_end_offset, maturity_end_segment = _find_new_extreme_after_center(center, candidate)
            new_extreme_ok = maturity_end_offset is not None
            if new_extreme_ok:
                has_maturity_barrier = True
                independence_kind = "strict_new_extreme"
            else:
                independence_kind = "third_buy_sell"
                invalid_reasons.append("missing_strict_new_extreme")
        else:
            new_extreme_ok = False
            independence_kind = "third_buy_sell"
    else:
        invalid_reasons.append("unknown_center_kind")

    return CenterMaturityEvent(
        candidate=candidate,
        center=center,
        center_kind=center_kind,
        overlap_type=overlap_type,
        status=center.get("status", ""),
        high=center.get("high"),
        low=center.get("low"),
        points=center.get("points") or {},
        owner_evidence=owner_evidence,
        independence_kind=independence_kind,
        has_maturity_barrier=has_maturity_barrier,
        requires_new_extreme=requires_new_extreme,
        new_extreme_ok=new_extreme_ok,
        maturity_end_offset=maturity_end_offset,
        maturity_end_segment=maturity_end_segment,
        third_entry_index=_third_segment_entry_index(center, ma34),
        invalid_reasons=tuple(dict.fromkeys(invalid_reasons)),
    )


def build_candidate_event_trace(
    candidate: WindowCandidate,
    ma34,
    source_segments: Optional[Sequence] = None,
    trend_direction: Optional[Direction] = None,
    reverse_candidates: Sequence[WindowCandidate] = (),
) -> CandidateEventTrace:
    source_segments = list(source_segments or candidate.segments)
    trend_direction = trend_direction or candidate.direction
    center = _find_latest_candidate_center(candidate, ma34, trend_direction)

    if center is None:
        event = CenterMaturityEvent(
            candidate=candidate,
            center=None,
            center_kind="none",
            independence_kind="no_daily_center",
            has_maturity_barrier=False,
        )
        reverse_events = tuple(
            build_candidate_event_trace(reverse, ma34, source_segments=source_segments, trend_direction=reverse.direction).event
            for reverse in reverse_candidates
        )
        return CandidateEventTrace(candidate, event, tuple(reverse_candidates), (), reverse_events)

    event = _event_from_center(candidate, center, ma34, source_segments, trend_direction)
    ignored_reverses = tuple(
        reverse
        for reverse in reverse_candidates
        if event.maturity_end_offset is not None and reverse.end_offset <= event.maturity_end_offset
    )
    reverse_events = tuple(
        build_candidate_event_trace(reverse, ma34, source_segments=source_segments, trend_direction=reverse.direction).event
        for reverse in reverse_candidates
    )
    return CandidateEventTrace(candidate, event, tuple(reverse_candidates), ignored_reverses, reverse_events)


def _find_latest_candidate_center(
    candidate: WindowCandidate,
    ma34,
    trend_direction: Direction,
) -> Optional[dict]:
    centers = []
    for local_start in range(max(1, len(candidate.segments) - 2)):
        if candidate.segments[local_start].direction != trend_direction:
            continue
        center = find_center(candidate.segments[local_start:], ma34, trend_direction=trend_direction)
        if center is None:
            continue
        center_segments = center.get("segments") or []
        centers.append((local_start + len(center_segments), local_start, center))
    if not centers:
        return None
    return max(centers, key=lambda item: (item[0], item[1]))[2]


def _reverse_strictly_breaks_primary_start(primary: WindowCandidate, reverse: WindowCandidate) -> bool:
    pivot = seg_start_price(primary.segments[0])
    reverse_end = seg_end_price(reverse.segments[-1])
    if primary.direction == Direction.Down:
        return reverse_end > pivot
    if primary.direction == Direction.Up:
        return reverse_end < pivot
    return False


def _candidate_is_independent(event: CenterMaturityEvent) -> bool:
    return event.center_kind == "none" or event.has_maturity_barrier


def _daily_segment_from_candidate(candidate: WindowCandidate, event: CenterMaturityEvent, cache_extra: Optional[dict] = None) -> DailySegment:
    cache = {
        "source": "daily_shadow_b",
        "shadow_start_offset": candidate.start_offset,
        "shadow_end_offset": candidate.end_offset,
        "independence_kind": event.independence_kind,
        "center_kind": event.center_kind,
        "owner_chain_valid": event.owner_evidence.owner_chain_valid,
        "new_extreme_ok": event.new_extreme_ok,
    }
    if cache_extra:
        cache.update(cache_extra)
    return DailySegment(
        symbol=candidate.segments[0].symbol,
        direction=candidate.direction,
        start_seg=candidate.segments[0],
        end_seg=candidate.segments[-1],
        segments=list(candidate.segments),
        cache={k: v for k, v in cache.items() if v is not None and v != ""},
    )


def _event_key(event: CenterMaturityEvent) -> tuple:
    center_segments = (event.center or {}).get("segments") or event.candidate.segments
    return (
        _seg_key(center_segments[0]) if center_segments else None,
        _seg_key(center_segments[-1]) if center_segments else None,
        event.overlap_type,
        round(float(event.low), 8) if event.low is not None else None,
        round(float(event.high), 8) if event.high is not None else None,
    )


def _point_key(point: tuple) -> Optional[tuple]:
    if not point:
        return None
    return (point[0], round(float(point[1]), 8))


def _center_identity_key(event: CenterMaturityEvent) -> tuple:
    points = event.points or {}
    return (
        event.overlap_type,
        event.center_kind,
        round(float(event.low), 8) if event.low is not None else None,
        round(float(event.high), 8) if event.high is not None else None,
        tuple((name, _point_key(points.get(name))) for name in ("A", "B", "C", "D") if name in points),
        tuple(event.owner_evidence.owner_chain),
    )


def _dedupe_events(events: Sequence[CenterMaturityEvent]) -> tuple[CenterMaturityEvent, ...]:
    result = []
    seen = set()
    for event in events:
        key = _event_key(event)
        if key in seen:
            continue
        seen.add(key)
        result.append(event)
    return tuple(result)


def _canonicalize_center_windows(
    events: Sequence[CenterMaturityEvent],
    source_segments: Sequence,
) -> tuple[CenterMaturityEvent, ...]:
    grouped: dict[tuple, list[CenterMaturityEvent]] = {}
    for event in events:
        grouped.setdefault(_center_identity_key(event), []).append(event)

    source_offsets = {_seg_key(seg): idx for idx, seg in enumerate(source_segments)}
    canonical_events: list[CenterMaturityEvent] = []
    for group in grouped.values():
        start_offsets = []
        end_offsets = []
        for event in group:
            center_segments = (event.center or {}).get("segments") or event.candidate.segments
            if not center_segments:
                continue
            start_offset = source_offsets.get(_seg_key(center_segments[0]))
            end_offset = source_offsets.get(_seg_key(center_segments[-1]))
            if start_offset is None or end_offset is None:
                continue
            start_offsets.append(start_offset)
            end_offsets.append(end_offset)
        if not start_offsets:
            canonical_events.append(group[0])
            continue

        base = min(group, key=lambda event: (_event_key(event), event.candidate.start_offset))
        center = dict(base.center or {})
        center["segments"] = list(source_segments[min(start_offsets) : max(end_offsets) + 1])
        canonical_events.append(replace(base, center=center))

    return tuple(sorted(canonical_events, key=_event_key))


def _collect_shadow_b_center_events(
    daily_segments: Sequence[DailySegment],
    ma34,
) -> tuple[tuple[CenterMaturityEvent, ...], tuple[CenterMaturityEvent, ...]]:
    """Build the Shadow-B center warehouse by sliding inside selected B segments."""
    events: list[CenterMaturityEvent] = []
    invalid_events: list[CenterMaturityEvent] = []
    seen = set()
    for daily_segment in daily_segments:
        source_segments = list(daily_segment.segments)
        segment_events: list[CenterMaturityEvent] = []
        segment_invalid_events: list[CenterMaturityEvent] = []
        global_start = daily_segment.cache.get("shadow_start_offset", 0)
        global_end = daily_segment.cache.get("shadow_end_offset", global_start + len(source_segments))
        for local_start in range(max(0, len(source_segments) - 3)):
            if source_segments[local_start].direction != daily_segment.direction:
                continue
            candidate = WindowCandidate(
                global_start + local_start,
                global_end,
                source_segments[local_start:],
            )
            center = find_center(candidate.segments, ma34, trend_direction=daily_segment.direction)
            if center is None:
                continue
            event = _event_from_center(candidate, center, ma34, source_segments, daily_segment.direction)
            key = _event_key(event)
            if key in seen:
                continue
            seen.add(key)
            if event.owner_evidence.invalid_reasons:
                segment_invalid_events.append(event)
            else:
                segment_events.append(event)
        events.extend(_canonicalize_center_windows(segment_events, source_segments))
        invalid_events.extend(_canonicalize_center_windows(segment_invalid_events, source_segments))
    return tuple(events), tuple(invalid_events)


def build_shadow_b_daily_plan(
    segments: Sequence,
    ma34,
    ma170,
    completed_segments: Sequence = (),
    max_segments: int = 20,
) -> ShadowBDailyPlan:
    daily_segments = []
    traces = []
    maturity_events = []
    invalid_events = []
    offset = 0
    completed = list(completed_segments)

    while offset <= len(segments) - 3 and len(daily_segments) < max_segments:
        allow_cold_start = not completed and offset == 0
        start_offsets = range(offset, len(segments) - 2) if allow_cold_start else range(offset, offset + 1)
        selected_trace = None
        selected_extra = None
        cold_start_fallback_trace = None
        cold_start_fallback_extra = None
        fallback_trace = None

        for start_offset in start_offsets:
            previous_direction = completed[-1].direction if completed else None
            pre_maturity_reverses = []
            primary_candidates = candidates_from_start(
                segments,
                start_offset,
                ma34,
                ma170,
                completed_segments=completed,
                enforce_continuity=not allow_cold_start,
                previous_direction=previous_direction,
                include_swallow_candidate=not allow_cold_start,
            )
            primary_candidates = [
                candidate
                for candidate in primary_candidates
                if candidate.kind != "regular" or _is_terminal_shadow_candidate(candidate, segments)
            ]
            for primary in primary_candidates:
                reverse_candidates = find_reverse_confirmation_candidates(
                    segments,
                    primary.end_offset,
                    primary.direction,
                    ma34,
                    ma170,
                    completed,
                )
                trace = build_candidate_event_trace(
                    primary,
                    ma34,
                    source_segments=segments,
                    trend_direction=primary.direction,
                    reverse_candidates=reverse_candidates,
                )
                traces.append(trace)
                maturity_events.append(trace.event)
                if trace.event.invalid_reasons:
                    invalid_events.append(trace.event)
                if not reverse_candidates:
                    continue
                if not trace.event.has_maturity_barrier:
                    pre_maturity_reverses.extend(reverse_candidates)

                for reverse, reverse_event in zip(reverse_candidates, trace.reverse_events):
                    confirmation = check_daily_segment_independence(primary, reverse, segments, ma34, ma170, completed)
                    reverse_independent = _candidate_is_independent(reverse_event) or confirmation.ok
                    if not reverse_independent:
                        continue
                    if trace.event.has_maturity_barrier:
                        ignored = tuple(
                            reverse_candidate
                            for reverse_candidate in [*pre_maturity_reverses, *trace.ignored_reverse_candidates]
                            if trace.event.maturity_end_offset is not None
                            and reverse_candidate.end_offset <= trace.event.maturity_end_offset
                        )
                        selected_trace = CandidateEventTrace(
                            primary=trace.primary,
                            event=trace.event,
                            reverse_candidates=trace.reverse_candidates,
                            ignored_reverse_candidates=ignored,
                            reverse_events=trace.reverse_events,
                            selected=True,
                            selection_reason="maturity_barrier_confirmed_by_reverse",
                        )
                    elif not _candidate_is_independent(trace.event) and _candidate_is_independent(reverse_event):
                        selected_trace = CandidateEventTrace(
                            primary=trace.primary,
                            event=trace.event,
                            reverse_candidates=trace.reverse_candidates,
                            ignored_reverse_candidates=trace.ignored_reverse_candidates,
                            reverse_events=trace.reverse_events,
                            selected=True,
                            selection_reason="same_trend_chain",
                            chain_confirmed_by=_tk_key(reverse.segments[-1].end_k),
                            chain_confirm_kind=reverse_event.independence_kind,
                        )
                        selected_extra = (reverse, reverse_event)
                    else:
                        if allow_cold_start and not _reverse_strictly_breaks_primary_start(primary, reverse):
                            continue
                        fallback_trace = CandidateEventTrace(
                            primary=trace.primary,
                            event=trace.event,
                            reverse_candidates=trace.reverse_candidates,
                            ignored_reverse_candidates=trace.ignored_reverse_candidates,
                            reverse_events=trace.reverse_events,
                            selected=True,
                            selection_reason="fallback_reverse_confirmation",
                        )
                    break
                if selected_trace:
                    break
            if selected_trace:
                if allow_cold_start and start_offset == 0:
                    cold_start_fallback_trace = selected_trace
                    cold_start_fallback_extra = selected_extra
                    selected_trace = None
                    selected_extra = None
                else:
                    break
            if not allow_cold_start:
                break

        selected_trace = selected_trace or fallback_trace or cold_start_fallback_trace
        selected_extra = selected_extra or cold_start_fallback_extra
        if selected_trace is None:
            break
        for trace_idx in range(len(traces) - 1, -1, -1):
            if traces[trace_idx].primary == selected_trace.primary:
                traces[trace_idx] = selected_trace
                break
        else:
            traces.append(selected_trace)
        if selected_trace.selection_reason == "same_trend_chain":
            cache_extra = {
                "independence_kind": "same_trend_chain",
                "chain_confirmed_by": selected_trace.chain_confirmed_by,
                "chain_confirm_kind": selected_trace.chain_confirm_kind,
            }
        else:
            cache_extra = {"selection_reason": selected_trace.selection_reason}
        ds = _daily_segment_from_candidate(selected_trace.primary, selected_trace.event, cache_extra)
        daily_segments.append(ds)
        completed.append(ds)
        if selected_extra:
            reverse, reverse_event = selected_extra
            extra_ds = _daily_segment_from_candidate(reverse, reverse_event)
            daily_segments.append(extra_ds)
            completed.append(extra_ds)
            offset = reverse.end_offset
            selected_extra = None
        else:
            offset = selected_trace.primary.end_offset

    center_events, invalid_center_events = _collect_shadow_b_center_events(daily_segments, ma34)

    return ShadowBDailyPlan(
        daily_segments=tuple(daily_segments),
        traces=tuple(traces),
        center_events=center_events,
        invalid_center_events=invalid_center_events,
        maturity_events=tuple(maturity_events),
        invalid_events=_dedupe_events([*invalid_events, *invalid_center_events]),
    )


def _is_terminal_shadow_candidate(candidate: WindowCandidate, segments: Sequence) -> bool:
    next_two = segments[candidate.end_offset:candidate.end_offset + 2]
    if len(next_two) < 2:
        return False
    from ..helpers.extension import is_extension_same_trend

    return not is_extension_same_trend(candidate.segments, next_two)
