# -*- coding: utf-8 -*-
"""日线级别线段候选选择与延迟提交决策。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, List, Optional, Sequence

from czsc.py.enum import Direction

from ..centers.algo import find_center
from ..centers.window_event import (
    CenterWindowEvent,
    find_center_window_events,
    find_latest_center_window_event,
    seg_key,
    segment_span_key,
    strictly_extends_after_event,
)
from .continuity import check_daily_segment_continuity
from .extension import is_extension_same_trend, is_opposite_direction
from .ma_cross import check_ma_cross_correlation
from .non_same import try_non_same_candidate
from .trend import check_global_trend_relationship
from ..utils import seg_end_price, seg_start_price

# 冷启动提交放宽开关：
# 仅在 completed_segments 为空（首条日线段尚未提交）时，允许跳过“反向破前枢轴”硬门槛。
ENABLE_COLD_START_RELAX_PIVOT_BREAK = True


@dataclass(frozen=True)
class WindowCandidate:
    start_offset: int
    end_offset: int
    segments: List
    kind: str = "regular"

    @property
    def direction(self):
        return self.segments[0].direction


CenterEvidenceBuilder = Callable[["WindowCandidate", Sequence], Sequence]


@dataclass(frozen=True)
class CommitDecision:
    start_offset: int
    end_offset: int
    segments: List
    pending_segments: List
    tail_offset: Optional[int] = None
    independence: Optional["IndependenceDecision"] = None
    extra_segments: tuple = ()
    candidate_kind: str = ""

    @property
    def next_tail_offset(self) -> int:
        return self.end_offset if self.tail_offset is None else self.tail_offset


@dataclass(frozen=True)
class IndependenceDecision:
    ok: bool
    kind: str = ""
    center_kind: str = ""
    center_low: Optional[float] = None
    center_high: Optional[float] = None
    requires_new_extreme: bool = False
    new_extreme_ok: Optional[bool] = None
    third_point_index: Optional[int] = None
    third_point_price: Optional[float] = None
    new_extreme_index: Optional[int] = None
    new_extreme_price: Optional[float] = None
    chain_confirmed_by: Optional[tuple] = None
    chain_confirm_kind: str = ""
    owner_span: Optional[tuple] = None
    evidence_span: Optional[tuple] = None
    maturity_span: Optional[tuple] = None
    owner_chain_valid: Optional[bool] = None
    invalid_reasons: tuple[str, ...] = ()
    reason: str = ""


@dataclass(frozen=True)
class SplitPathPlan:
    primary: WindowCandidate
    primary_self: IndependenceDecision
    reverse: WindowCandidate
    reverse_self: IndependenceDecision
    next_reverse: WindowCandidate
    score: tuple


def is_valid_regular_window(
    window: Sequence,
    ma34,
    ma170,
    lag_segment=None,
    completed_segments: Sequence = (),
    previous_end_k=None,
    enforce_continuity: bool = True,
    require_ma: bool = True,
) -> bool:
    if len(window) < 3:
        return False
    if enforce_continuity and not check_daily_segment_continuity(window, completed_segments, previous_end_k=previous_end_k):
        return False
    if not check_global_trend_relationship(window):
        return False
    if require_ma and not check_ma_cross_correlation(window, ma34, ma170, lag_segment):
        return False
    return True


def regular_candidates_from_start(
    segments: Sequence,
    start_offset: int,
    ma34,
    ma170,
    completed_segments: Sequence = (),
    enforce_continuity: bool = True,
    require_ma: bool = True,
) -> List[WindowCandidate]:
    candidates: List[WindowCandidate] = []
    max_len = len(segments) - start_offset
    if max_len < 3:
        return candidates
    max_len = max_len if max_len % 2 == 1 else max_len - 1
    for window_len in range(3, max_len + 1, 2):
        end_offset = start_offset + window_len
        window = list(segments[start_offset:end_offset])
        lag_segment = segments[end_offset] if end_offset < len(segments) else None
        if is_valid_regular_window(
            window,
            ma34,
            ma170,
            lag_segment=lag_segment,
            completed_segments=completed_segments,
            enforce_continuity=enforce_continuity,
            require_ma=require_ma,
        ):
            candidates.append(WindowCandidate(start_offset, end_offset, window))
    return candidates


def candidates_from_start(
    segments: Sequence,
    start_offset: int,
    ma34,
    ma170,
    completed_segments: Sequence = (),
    enforce_continuity: bool = True,
    previous_direction=None,
    include_swallow_candidate: bool = True,
    require_ma: bool = True,
) -> List[WindowCandidate]:
    candidates: List[WindowCandidate] = []
    if start_offset < len(segments):
        first = segments[start_offset]
        if include_swallow_candidate and first.cache.get("is_macro_swallow"):
            candidates.append(WindowCandidate(start_offset, start_offset + 1, [first], kind="swallow"))

    candidates.extend(regular_candidates_from_start(
        segments,
        start_offset,
        ma34,
        ma170,
        completed_segments=completed_segments,
        enforce_continuity=enforce_continuity,
        require_ma=require_ma,
    ))
    if previous_direction is not None:
        non_same = try_non_same_candidate(segments[start_offset:], previous_direction, ma34)
        if non_same:
            candidates.append(WindowCandidate(start_offset, start_offset + len(non_same), list(non_same), kind="non_same"))
    candidates.sort(key=lambda c: (c.end_offset, 1 if c.kind == "regular" else 0))
    return candidates


def _is_terminal_candidate(candidate: WindowCandidate, segments: Sequence) -> bool:
    next_two = segments[candidate.end_offset:candidate.end_offset + 2]
    if len(next_two) < 2:
        return False
    return not is_extension_same_trend(candidate.segments, next_two)


def _select_pending_and_terminal(
    candidates: Sequence[WindowCandidate],
    segments: Sequence,
) -> tuple[Optional[WindowCandidate], Optional[WindowCandidate]]:
    pending: Optional[WindowCandidate] = None
    for candidate in sorted(candidates, key=lambda c: (c.end_offset, 1 if c.kind == "regular" else 0)):
        pending = candidate
        if candidate.kind != "regular" or _is_terminal_candidate(candidate, segments):
            return pending, candidate
    return pending, None


def _iter_terminal_candidates(
    candidates: Sequence[WindowCandidate],
    segments: Sequence,
) -> list[WindowCandidate]:
    terminals: list[WindowCandidate] = []
    for candidate in sorted(candidates, key=lambda c: (c.end_offset, 1 if c.kind == "regular" else 0)):
        if candidate.kind != "regular" or _is_terminal_candidate(candidate, segments):
            terminals.append(candidate)
    return terminals


def find_terminal_candidate_from_start(
    segments: Sequence,
    start_offset: int,
    ma34,
    ma170,
    completed_segments: Sequence = (),
    enforce_continuity: bool = True,
    previous_direction=None,
    include_swallow_candidate: bool = True,
    require_ma: bool = True,
) -> tuple[Optional[WindowCandidate], Optional[WindowCandidate]]:
    candidates = candidates_from_start(
        segments,
        start_offset,
        ma34,
        ma170,
        completed_segments=completed_segments,
        enforce_continuity=enforce_continuity,
        previous_direction=previous_direction,
        include_swallow_candidate=include_swallow_candidate,
        require_ma=require_ma,
    )
    return _select_pending_and_terminal(candidates, segments)


def _reverse_strictly_breaks_primary_start(primary: WindowCandidate, reverse: WindowCandidate) -> bool:
    pivot = seg_start_price(primary.segments[0])
    reverse_end = seg_end_price(reverse.segments[-1])
    if primary.direction == Direction.Down:
        return reverse_end > pivot
    if primary.direction == Direction.Up:
        return reverse_end < pivot
    return False


def _candidate_evidence_segments(
    candidate: WindowCandidate,
    all_segments: Optional[Sequence] = None,
    evidence_segments: Optional[Sequence] = None,
) -> list:
    if evidence_segments is not None:
        return list(evidence_segments)
    if all_segments is None or candidate.start_offset < 0:
        return list(candidate.segments)
    return list(all_segments[candidate.start_offset:min(len(all_segments), candidate.end_offset + 1)])


def _find_candidate_center_event(
    candidate: WindowCandidate,
    ma34,
    all_segments: Optional[Sequence] = None,
) -> Optional[CenterWindowEvent]:
    return _find_latest_candidate_center_event(candidate, ma34, all_segments=all_segments, center_kind="trend_class")


def _find_latest_turning_center_event(
    candidate: WindowCandidate,
    ma34,
    all_segments: Optional[Sequence] = None,
    evidence_segments: Optional[Sequence] = None,
) -> Optional[CenterWindowEvent]:
    return _find_latest_candidate_center_event(
        candidate,
        ma34,
        all_segments=all_segments,
        evidence_segments=evidence_segments,
        center_kind="turning",
    )


def _find_latest_candidate_center_event(
    candidate: WindowCandidate,
    ma34,
    all_segments: Optional[Sequence] = None,
    evidence_segments: Optional[Sequence] = None,
    center_kind: Optional[str] = None,
) -> Optional[CenterWindowEvent]:
    return find_latest_center_window_event(
        candidate.segments,
        _candidate_evidence_segments(candidate, all_segments, evidence_segments=evidence_segments),
        ma34,
        candidate.direction,
        center_kind=center_kind,
    )


def _find_candidate_center(candidate: WindowCandidate, ma34) -> Optional[dict]:
    event = _find_candidate_center_event(candidate, ma34)
    return event.raw.center if event else None


def _find_latest_turning_center(candidate: WindowCandidate, ma34) -> Optional[dict]:
    event = _find_latest_turning_center_event(candidate, ma34)
    return event.raw.center if event else None


def _tk_key(tk) -> tuple:
    return (tk.k_index, tk.dt, tk.price, tk.mark.value)


def _candidate_strictly_extends_after_center(candidate: WindowCandidate, center: dict) -> bool:
    center_segments = center.get("segments") or []
    if not center_segments or len(center_segments) >= len(candidate.segments):
        return False

    center_prices = []
    for seg in center_segments:
        center_prices.extend([seg.start_k.price, seg.end_k.price])
    if not center_prices:
        return False

    candidate_end = seg_end_price(candidate.segments[-1])
    if candidate.direction == Direction.Up:
        return candidate_end > max(center_prices)
    if candidate.direction == Direction.Down:
        return candidate_end < min(center_prices)
    return False


def _candidate_strictly_extends_after_center_event(candidate: WindowCandidate, event: CenterWindowEvent) -> bool:
    return strictly_extends_after_event(candidate.segments, event)


def _turning_center_has_third_buy_sell(center: dict) -> bool:
    return (
        center.get("center_kind") == "turning"
        and center.get("overlap_type") == 0
        and center.get("status") == "FINAL"
        and {"A", "B"} <= set(center.get("points") or {})
        and len(center.get("segments") or []) >= 4
    )


def check_turning_center_independence(
    center_event: CenterWindowEvent,
    *,
    new_extreme_ok: bool,
    reason_prefix: str,
) -> IndependenceDecision:
    """Judge Type0 / turning-center independence.

    Turning centers are not trend-class centers, so they do not require a later
    strict new extreme.  A strict new high / low is still recorded as a stronger
    turning-specific independence reason when it is present.
    """
    center = center_event.raw.center
    if new_extreme_ok:
        return _decision_from_center_event(
            "turning_new_extreme",
            center_event,
            requires_new_extreme=False,
            new_extreme_ok=True,
            reason=f"{reason_prefix}; turning center is followed by a strict new extreme",
        )
    if _turning_center_has_third_buy_sell(center):
        return _decision_from_center_event(
            "turning_third_buy_sell",
            center_event,
            requires_new_extreme=False,
            new_extreme_ok=False,
            reason=f"{reason_prefix}; turning center confirms independence without requiring a later strict new extreme",
        )

    return IndependenceDecision(
        ok=False,
        kind="turning_unconfirmed",
        center_kind=center.get("center_kind", ""),
        center_low=center.get("low"),
        center_high=center.get("high"),
        requires_new_extreme=False,
        new_extreme_ok=False,
        owner_span=center_event.owner_span,
        evidence_span=center_event.evidence_span,
        owner_chain_valid=center_event.owner_chain_valid,
        invalid_reasons=center_event.invalid_reasons,
        reason=f"{reason_prefix}; turning center has neither strict new extreme nor confirmed third buy/sell",
    )


def check_candidate_self_independence(
    candidate: WindowCandidate,
    ma34,
    all_segments: Optional[Sequence] = None,
    boundary_evidence_segments: Optional[Sequence] = None,
) -> IndependenceDecision:
    """Judge whether a candidate already carries its own independence evidence."""
    center_event = _find_candidate_center_event(candidate, ma34, all_segments=all_segments)
    if center_event is None or not center_event.valid:
        turning_event = _find_latest_turning_center_event(
            candidate,
            ma34,
            all_segments=all_segments,
            evidence_segments=boundary_evidence_segments,
        )
        if turning_event is not None and turning_event.valid:
            return check_turning_center_independence(
                turning_event,
                new_extreme_ok=_candidate_strictly_extends_after_center_event(candidate, turning_event),
                reason_prefix="candidate has a turning center and no trend-class daily center",
            )
        return IndependenceDecision(
            ok=True,
            kind="no_daily_center",
            reason="candidate itself is a valid daily segment and has no trend-class daily center",
        )

    center = center_event.raw.center
    center_kind = center_event.center_kind
    new_extreme_ok = _candidate_strictly_extends_after_center_event(candidate, center_event)
    if center_kind == "trend_class" and new_extreme_ok:
        return _decision_from_center_event(
            "strict_new_extreme",
            center_event,
            requires_new_extreme=True,
            new_extreme_ok=True,
            maturity_span=segment_span_key(candidate.segments),
            reason="candidate trend-class center is followed by a strict new extreme",
        )

    if center_kind == "trend_class":
        return _decision_from_center_event(
            "trend_class_center",
            center_event,
            requires_new_extreme=True,
            new_extreme_ok=False,
            reason="candidate has a valid trend-class center; strict new extreme is not required by current independence mode",
        )

    return IndependenceDecision(
        ok=False,
        kind="unknown",
        center_kind=center_kind,
        center_low=center.get("low"),
        center_high=center.get("high"),
        reason="candidate center did not satisfy any self-independence rule",
    )


def _find_boundary_turning_center(primary: WindowCandidate, reverse: WindowCandidate, ma34) -> Optional[dict]:
    if not primary.segments or not reverse.segments:
        return None
    boundary_segments = [*primary.segments, reverse.segments[0]]
    result = find_center(boundary_segments, ma34, trend_direction=primary.direction)
    if result and result.get("center_kind") == "turning":
        return result
    return None


def _find_boundary_turning_event(primary: WindowCandidate, reverse: WindowCandidate, ma34) -> Optional[CenterWindowEvent]:
    if not primary.segments or not reverse.segments:
        return None
    return find_latest_center_window_event(
        primary.segments,
        [*primary.segments, reverse.segments[0]],
        ma34,
        primary.direction,
        center_kind="turning",
    )


def _decision_from_center(kind: str, center: dict, **kwargs) -> IndependenceDecision:
    return IndependenceDecision(
        ok=True,
        kind=kind,
        center_kind=center.get("center_kind", ""),
        center_low=center.get("low"),
        center_high=center.get("high"),
        **kwargs,
    )


def _decision_from_center_event(kind: str, event: CenterWindowEvent, **kwargs) -> IndependenceDecision:
    return IndependenceDecision(
        ok=True,
        kind=kind,
        center_kind=event.center_kind,
        center_low=event.low,
        center_high=event.high,
        owner_span=event.owner_span,
        evidence_span=event.evidence_span,
        owner_chain_valid=event.owner_chain_valid,
        invalid_reasons=event.invalid_reasons,
        **kwargs,
    )


def check_daily_segment_independence(
    primary: WindowCandidate,
    reverse: WindowCandidate,
    segments: Sequence,
    ma34,
    ma170,
    completed_segments: Sequence,
) -> IndependenceDecision:
    """Judge whether a reverse daily candidate is independent enough to be born.

    The reverse candidate is already responsible for "违背趋势" in the delayed
    confirmation chain.  This helper only decides the independent birth reason.
    """
    if reverse.kind == "swallow" or (
        len(reverse.segments) == 1 and reverse.segments[0].cache.get("is_macro_swallow")
    ):
        if check_daily_segment_continuity(reverse.segments, completed_segments):
            return IndependenceDecision(
                ok=True,
                kind="swallow_one_segment",
                reason="macro swallow segment can be promoted as one daily segment",
            )

    candidate_center_event = _find_candidate_center_event(reverse, ma34, all_segments=segments)
    new_extreme_ok = _reverse_strictly_breaks_primary_start(primary, reverse)

    if candidate_center_event is None or not candidate_center_event.valid:
        turning_event = (
            _find_latest_turning_center_event(reverse, ma34, all_segments=segments)
            or _find_boundary_turning_event(primary, reverse, ma34)
        )
        if turning_event is not None and turning_event.valid:
            turning_new_extreme_ok = (
                _candidate_strictly_extends_after_center_event(reverse, turning_event)
                or _reverse_strictly_breaks_primary_start(primary, reverse)
            )
            return check_turning_center_independence(
                turning_event,
                new_extreme_ok=turning_new_extreme_ok,
                reason_prefix="reverse candidate has a turning center and no trend-class daily center",
            )
        return IndependenceDecision(
            ok=True,
            kind="no_daily_center",
            reason="candidate itself is a valid daily segment and has no trend-class daily center",
        )

    candidate_center = candidate_center_event.raw.center
    center_kind = candidate_center_event.center_kind
    if new_extreme_ok:
        return _decision_from_center_event(
            "strict_new_extreme",
            candidate_center_event,
            requires_new_extreme=True,
            new_extreme_ok=True,
            maturity_span=segment_span_key(reverse.segments),
            reason="reverse candidate strictly breaks primary start extreme",
        )

    if center_kind == "trend_class":
        return _decision_from_center_event(
            "trend_class_center",
            candidate_center_event,
            requires_new_extreme=True,
            new_extreme_ok=False,
            reason="reverse candidate has a valid trend-class center; strict new extreme is not required by current independence mode",
        )

    return IndependenceDecision(
        ok=False,
        kind="unknown",
        center_kind=center_kind,
        center_low=candidate_center.get("low"),
        center_high=candidate_center.get("high"),
        reason="candidate center did not satisfy any independence rule",
    )


def find_reverse_candidate(
    segments: Sequence,
    start_offset: int,
    previous_direction,
    ma34,
    ma170,
    completed_segments: Sequence,
) -> Optional[WindowCandidate]:
    candidates = candidates_from_start(
        segments,
        start_offset,
        ma34,
        ma170,
        completed_segments=completed_segments,
        enforce_continuity=False,
        previous_direction=previous_direction,
    )
    valid = [c for c in candidates if is_opposite_direction(c.direction, previous_direction)]
    if not valid:
        return None
    _, terminal = _select_pending_and_terminal(valid, segments)
    return terminal


def find_reverse_candidates(
    segments: Sequence,
    start_offset: int,
    previous_direction,
    ma34,
    ma170,
    completed_segments: Sequence,
) -> list[WindowCandidate]:
    candidates = candidates_from_start(
        segments,
        start_offset,
        ma34,
        ma170,
        completed_segments=completed_segments,
        enforce_continuity=False,
        previous_direction=previous_direction,
    )
    valid = [c for c in candidates if is_opposite_direction(c.direction, previous_direction)]
    return _iter_terminal_candidates(valid, segments)


def find_reverse_confirmation_candidates(
    segments: Sequence,
    start_offset: int,
    previous_direction,
    ma34,
    ma170,
    completed_segments: Sequence,
) -> list[WindowCandidate]:
    terminals = find_reverse_candidates(
        segments,
        start_offset,
        previous_direction,
        ma34,
        ma170,
        completed_segments,
    )
    candidates = candidates_from_start(
        segments,
        start_offset,
        ma34,
        ma170,
        completed_segments=completed_segments,
        enforce_continuity=False,
        previous_direction=previous_direction,
    )
    valid = [c for c in candidates if is_opposite_direction(c.direction, previous_direction)]
    if not valid:
        return terminals
    pending = valid[-1]
    seen = {(c.start_offset, c.end_offset, c.kind) for c in terminals}
    if (pending.start_offset, pending.end_offset, pending.kind) not in seen:
        return [*terminals, pending]
    return terminals


def should_commit_leading_swallow(
    segments: Sequence,
    completed_segments: Sequence,
    ma34,
    ma170,
) -> bool:
    if not segments or not completed_segments or not segments[0].cache.get("is_macro_swallow"):
        return False
    if not check_daily_segment_continuity([segments[0]], completed_segments):
        return False
    if len(segments) < 5:
        return False
    swallow_end = seg_end_price(segments[0])
    for candidate in regular_candidates_from_start(
        segments,
        0,
        ma34,
        ma170,
        completed_segments=completed_segments,
        enforce_continuity=True,
        require_ma=False,
    ):
        if candidate.direction != segments[0].direction:
            continue
        candidate_end = seg_end_price(candidate.segments[-1])
        if candidate.direction == Direction.Up and candidate_end > swallow_end:
            return False
        if candidate.direction == Direction.Down and candidate_end < swallow_end:
            return False
    return True


def _has_maturity_barrier(independence: IndependenceDecision) -> bool:
    return independence.ok and independence.center_kind == "trend_class"


def _chain_independence(
    primary_self: IndependenceDecision,
    reverse: WindowCandidate,
    reverse_self: IndependenceDecision,
) -> IndependenceDecision:
    return IndependenceDecision(
        ok=True,
        kind="same_trend_chain",
        center_kind=primary_self.center_kind,
        center_low=primary_self.center_low,
        center_high=primary_self.center_high,
        requires_new_extreme=primary_self.requires_new_extreme,
        new_extreme_ok=primary_self.new_extreme_ok,
        chain_confirmed_by=_tk_key(reverse.segments[-1].end_k),
        chain_confirm_kind=reverse_self.kind,
        reason="confirmed by following independent segment",
    )


def _can_chain_confirm(
    primary_self: IndependenceDecision,
    reverse: WindowCandidate,
    reverse_self: IndependenceDecision,
) -> bool:
    return (
        not primary_self.ok
        and primary_self.center_kind == "trend_class"
        and reverse_self.ok
    )


def _reverse_selection_key(reverse: WindowCandidate, reverse_self: IndependenceDecision) -> tuple:
    """Prefer the most structural independent reverse before short special cases."""
    return (
        1 if reverse.kind == "regular" else 0,
        1 if reverse_self.center_kind == "trend_class" else 0,
        1 if reverse_self.kind in {"strict_new_extreme", "turning_new_extreme"} else 0,
        reverse.end_offset,
    )


def _is_structural_independent(independence: IndependenceDecision) -> bool:
    return independence.ok and (
        independence.center_kind in {"trend_class", "turning"}
        or independence.kind in {
            "same_trend_chain",
            "strict_new_extreme",
            "trend_class_center",
            "turning_new_extreme",
            "turning_third_buy_sell",
            "swallow_one_segment",
        }
    )


def _candidate_self_with_one_step_chain(
    candidate: WindowCandidate,
    candidate_self: IndependenceDecision,
    segments: Sequence,
    ma34,
    ma170,
    completed_segments: Sequence,
    center_evidence_builder: Optional[CenterEvidenceBuilder] = None,
) -> IndependenceDecision:
    if candidate_self.ok or candidate_self.center_kind != "trend_class":
        return candidate_self

    next_reverses = find_reverse_confirmation_candidates(
        segments,
        candidate.end_offset,
        candidate.direction,
        ma34,
        ma170,
        completed_segments,
    )
    if not next_reverses:
        return candidate_self

    pairs = []
    for next_reverse in next_reverses:
        next_self = check_candidate_self_independence(
            next_reverse,
            ma34,
            all_segments=segments,
            boundary_evidence_segments=(
                center_evidence_builder(next_reverse, segments)
                if center_evidence_builder
                else None
            ),
        )
        if next_self.ok:
            pairs.append((next_reverse, next_self))
    if not pairs:
        return candidate_self

    next_reverse, next_self = sorted(pairs, key=lambda item: _reverse_selection_key(item[0], item[1]), reverse=True)[0]
    return _chain_independence(candidate_self, next_reverse, next_self)


def _iter_reverse_independence_candidates(
    reverse_candidates: Sequence[WindowCandidate],
    ma34,
    all_segments: Optional[Sequence] = None,
    ma170=None,
    completed_segments: Sequence = (),
    center_evidence_builder: Optional[CenterEvidenceBuilder] = None,
) -> list[tuple[WindowCandidate, IndependenceDecision]]:
    pairs = []
    for reverse in reverse_candidates:
        reverse_self = check_candidate_self_independence(
            reverse,
            ma34,
            all_segments=all_segments,
            boundary_evidence_segments=(
                center_evidence_builder(reverse, all_segments)
                if center_evidence_builder and all_segments is not None
                else None
            ),
        )
        if all_segments is not None and ma170 is not None:
            reverse_self = _candidate_self_with_one_step_chain(
                reverse,
                reverse_self,
                all_segments,
                ma34,
                ma170,
                completed_segments,
                center_evidence_builder=center_evidence_builder,
            )
        pairs.append((reverse, reverse_self))
    independent = [(reverse, reverse_self) for reverse, reverse_self in pairs if reverse_self.ok]
    if not independent:
        return pairs
    return sorted(independent, key=lambda item: _reverse_selection_key(item[0], item[1]), reverse=True)


def _candidate_independence(
    candidate: WindowCandidate,
    segments: Sequence,
    ma34,
    center_evidence_builder: Optional[CenterEvidenceBuilder] = None,
) -> IndependenceDecision:
    return check_candidate_self_independence(
        candidate,
        ma34,
        all_segments=segments,
        boundary_evidence_segments=(
            center_evidence_builder(candidate, segments)
            if center_evidence_builder
            else None
        ),
    )


def _candidate_independence_with_one_step_chain(
    candidate: WindowCandidate,
    segments: Sequence,
    ma34,
    ma170,
    completed_segments: Sequence,
    center_evidence_builder: Optional[CenterEvidenceBuilder] = None,
) -> IndependenceDecision:
    candidate_self = _candidate_independence(candidate, segments, ma34, center_evidence_builder)
    return _candidate_self_with_one_step_chain(
        candidate,
        candidate_self,
        segments,
        ma34,
        ma170,
        completed_segments,
        center_evidence_builder=center_evidence_builder,
    )


def _has_later_maturity_barrier_for_short_reverse(
    primary_candidates: Sequence[WindowCandidate],
    reverse: WindowCandidate,
    reverse_self: IndependenceDecision,
    segments: Sequence,
    ma34,
    center_evidence_builder: Optional[CenterEvidenceBuilder] = None,
) -> bool:
    if reverse_self.kind != "no_daily_center":
        return False
    for candidate in primary_candidates:
        if candidate.end_offset <= reverse.end_offset:
            continue
        candidate_self = _candidate_independence(candidate, segments, ma34, center_evidence_builder)
        if candidate_self.center_kind == "trend_class":
            return True
    return False


def _first_legal_next_reverse(
    reverse: WindowCandidate,
    segments: Sequence,
    ma34,
    ma170,
    completed_segments: Sequence,
) -> Optional[WindowCandidate]:
    next_reverses = find_reverse_confirmation_candidates(
        segments,
        reverse.end_offset,
        reverse.direction,
        ma34,
        ma170,
        completed_segments,
    )
    return next_reverses[0] if next_reverses else None


def _open_tail_reverse_candidate(
    segments: Sequence,
    start_offset: int,
    previous_direction,
    ma34,
    ma170,
    completed_segments: Sequence,
) -> Optional[WindowCandidate]:
    candidates = candidates_from_start(
        segments,
        start_offset,
        ma34,
        ma170,
        completed_segments=completed_segments,
        enforce_continuity=False,
        previous_direction=previous_direction,
        include_swallow_candidate=True,
    )
    valid = [candidate for candidate in candidates if is_opposite_direction(candidate.direction, previous_direction)]
    if not valid:
        return None
    return valid[-1]


def _segments_fit_range(event_segments: Sequence, offset_by_key: dict, start_offset: int, end_offset: int) -> bool:
    if not event_segments:
        return False
    offsets = []
    for seg in event_segments:
        offset = offset_by_key.get(seg_key(seg))
        if offset is None:
            return False
        offsets.append(offset)
    return min(offsets) >= start_offset and max(offsets) < end_offset


def _event_fits_one_split_part(event: CenterWindowEvent, split_ranges: Sequence[tuple[int, int]], offset_by_key: dict) -> bool:
    for start_offset, end_offset in split_ranges:
        owner_ok = _segments_fit_range(event.owner_segments, offset_by_key, start_offset, end_offset)
        evidence_ok = True
        if event.evidence_segments:
            evidence_offsets = [offset_by_key.get(seg_key(seg)) for seg in event.evidence_segments]
            if all(offset is not None for offset in evidence_offsets):
                evidence_ok = _segments_fit_range(event.evidence_segments, offset_by_key, start_offset, end_offset)
        if owner_ok and evidence_ok:
            return True
    return False


def _split_path_preserves_valid_trend_centers(
    primary_candidates: Sequence[WindowCandidate],
    split_ranges: Sequence[tuple[int, int]],
    segments: Sequence,
    ma34,
    center_evidence_builder: Optional[CenterEvidenceBuilder] = None,
) -> bool:
    if not split_ranges:
        return True
    split_start = split_ranges[0][0]
    split_end = split_ranges[-1][1]
    offset_by_key = {seg_key(seg): offset for offset, seg in enumerate(segments)}
    for candidate in primary_candidates:
        if candidate.start_offset != split_start or candidate.end_offset < split_end:
            continue
        evidence_segments = _candidate_evidence_segments(
            candidate,
            segments,
            evidence_segments=(
                center_evidence_builder(candidate, segments)
                if center_evidence_builder
                else None
            ),
        )
        events = find_center_window_events(
            candidate.segments,
            evidence_segments,
            ma34,
            candidate.direction,
            center_kind="trend_class",
        )
        for event in events:
            if not event.valid:
                continue
            if not _event_fits_one_split_part(event, split_ranges, offset_by_key):
                return False
    return True


def _split_path_score(
    primary: WindowCandidate,
    primary_self: IndependenceDecision,
    reverse: WindowCandidate,
    reverse_self: IndependenceDecision,
    next_reverse: WindowCandidate,
) -> tuple:
    trend_center_count = sum(
        1
        for independence in (primary_self, reverse_self)
        if independence.center_kind == "trend_class"
    )
    segment_count = 3 if next_reverse else 2
    return (
        trend_center_count,
        segment_count,
    )


def _build_internal_split_plans(
    primary_candidates: Sequence[WindowCandidate],
    segments: Sequence,
    ma34,
    ma170,
    completed_segments: Sequence,
    center_evidence_builder: Optional[CenterEvidenceBuilder] = None,
) -> list[SplitPathPlan]:
    plans: list[SplitPathPlan] = []
    for primary in primary_candidates:
        if primary.kind == "swallow":
            continue
        primary_self = _candidate_independence(primary, segments, ma34, center_evidence_builder)
        if not primary_self.ok:
            continue
        if primary_self.center_kind == "turning":
            continue
        reverse_candidates = find_reverse_confirmation_candidates(
            segments,
            primary.end_offset,
            primary.direction,
            ma34,
            ma170,
            completed_segments,
        )
        if not reverse_candidates:
            continue
        for reverse, reverse_self in _iter_reverse_independence_candidates(
            reverse_candidates,
            ma34,
            all_segments=segments,
            ma170=ma170,
            completed_segments=completed_segments,
            center_evidence_builder=center_evidence_builder,
        ):
            if not reverse_self.ok:
                continue
            if reverse_self.kind == "no_daily_center":
                continue
            next_reverse = _first_legal_next_reverse(reverse, segments, ma34, ma170, completed_segments)
            if next_reverse is None:
                continue
            if _has_later_maturity_barrier_for_short_reverse(
                primary_candidates,
                reverse,
                reverse_self,
                segments,
                ma34,
                center_evidence_builder,
            ):
                continue
            split_ranges = (
                (primary.start_offset, primary.end_offset),
                (reverse.start_offset, reverse.end_offset),
                (next_reverse.start_offset, next_reverse.end_offset),
            )
            if not _split_path_preserves_valid_trend_centers(
                primary_candidates,
                split_ranges,
                segments,
                ma34,
                center_evidence_builder=center_evidence_builder,
            ):
                continue
            plans.append(
                SplitPathPlan(
                    primary=primary,
                    primary_self=primary_self,
                    reverse=reverse,
                    reverse_self=reverse_self,
                    next_reverse=next_reverse,
                    score=_split_path_score(primary, primary_self, reverse, reverse_self, next_reverse),
                )
            )
    return sorted(plans, key=lambda plan: plan.score, reverse=True)


def _find_internal_split_decision(
    primary_candidates: Sequence[WindowCandidate],
    segments: Sequence,
    ma34,
    ma170,
    completed_segments: Sequence,
    center_evidence_builder: Optional[CenterEvidenceBuilder] = None,
) -> Optional[CommitDecision]:
    plans = _build_internal_split_plans(
        primary_candidates,
        segments,
        ma34,
        ma170,
        completed_segments,
        center_evidence_builder=center_evidence_builder,
    )
    if plans:
        trend_plans = [plan for plan in plans if plan.score[0] > 0]
        if not trend_plans:
            return None
        plan = trend_plans[0]
        return CommitDecision(
            start_offset=plan.primary.start_offset,
            end_offset=plan.primary.end_offset,
            segments=plan.primary.segments,
            pending_segments=plan.next_reverse.segments,
            tail_offset=plan.reverse.end_offset,
            independence=plan.primary_self,
            extra_segments=((plan.reverse.segments, plan.reverse_self),),
            candidate_kind=plan.primary.kind,
        )
    return None


def _find_open_tail_direct_decision(
    primary_candidates: Sequence[WindowCandidate],
    segments: Sequence,
    ma34,
    ma170,
    completed_segments: Sequence,
    center_evidence_builder: Optional[CenterEvidenceBuilder] = None,
) -> Optional[CommitDecision]:
    for primary in sorted(primary_candidates, key=lambda c: (c.end_offset, 1 if c.kind == "regular" else 0), reverse=True):
        if primary.kind == "swallow":
            continue
        primary_self = _candidate_independence(primary, segments, ma34, center_evidence_builder)
        if not primary_self.ok:
            continue
        if primary_self.center_kind == "turning":
            continue
        reverse_candidates = find_reverse_confirmation_candidates(
            segments,
            primary.end_offset,
            primary.direction,
            ma34,
            ma170,
            completed_segments,
        )
        if not reverse_candidates:
            open_tail_reverse = _open_tail_reverse_candidate(
                segments,
                primary.end_offset,
                primary.direction,
                ma34,
                ma170,
                completed_segments,
            )
            if open_tail_reverse is None:
                continue
            return CommitDecision(
                start_offset=primary.start_offset,
                end_offset=primary.end_offset,
                segments=primary.segments,
                pending_segments=open_tail_reverse.segments,
                tail_offset=primary.end_offset,
                independence=primary_self,
                candidate_kind=primary.kind,
            )
        reverse = reverse_candidates[0]
        if _first_legal_next_reverse(reverse, segments, ma34, ma170, completed_segments) is not None:
            continue
        return CommitDecision(
            start_offset=primary.start_offset,
            end_offset=primary.end_offset,
            segments=primary.segments,
            pending_segments=reverse.segments,
            tail_offset=primary.end_offset,
            independence=primary_self,
            candidate_kind=primary.kind,
        )
    return None


def _select_mature_commit_decision(decisions: Sequence[CommitDecision]) -> Optional[CommitDecision]:
    chain_decisions = [
        decision
        for decision in decisions
        if decision.independence is not None and decision.independence.kind == "same_trend_chain"
    ]
    if chain_decisions:
        return max(chain_decisions, key=lambda decision: (decision.end_offset, decision.next_tail_offset))
    return decisions[0] if decisions else None


def _cold_start_relax_allowed(primary: WindowCandidate, completed_segments: Sequence) -> bool:
    return (
        ENABLE_COLD_START_RELAX_PIVOT_BREAK
        and len(completed_segments) == 0
        and primary.start_offset == 0
        and primary.segments[0].cache.get("is_macro_swallow")
    )


def find_delayed_commit_decision(
    segments: Sequence,
    completed_segments: Sequence,
    ma34,
    ma170,
    continuity_broken: bool = False,
    allow_cold_start: bool = False,
    center_evidence_builder: Optional[CenterEvidenceBuilder] = None,
) -> Optional[CommitDecision]:
    if continuity_broken or len(segments) < 3:
        return None

    start_offsets = range(0, len(segments) - 2) if allow_cold_start else range(0, 1)
    best_pending: List = []
    cold_start_fallback: Optional[CommitDecision] = None

    for start_offset in start_offsets:
        previous_direction = completed_segments[-1].direction if completed_segments else None
        primary_candidates = candidates_from_start(
            segments,
            start_offset,
            ma34,
            ma170,
            completed_segments=completed_segments,
            enforce_continuity=not allow_cold_start,
            previous_direction=previous_direction,
            include_swallow_candidate=not allow_cold_start,
        )
        if not primary_candidates:
            continue

        if not best_pending:
            best_pending = primary_candidates[-1].segments

        internal_split_decision = _find_internal_split_decision(
            primary_candidates,
            segments,
            ma34,
            ma170,
            completed_segments,
            center_evidence_builder=center_evidence_builder,
        )
        if internal_split_decision:
            return internal_split_decision

        open_tail_decision = (
            None
            if allow_cold_start
            else _find_open_tail_direct_decision(
                primary_candidates,
                segments,
                ma34,
                ma170,
                completed_segments,
                center_evidence_builder=center_evidence_builder,
            )
        )

        swallow_fallback: Optional[CommitDecision] = None
        mature_decisions: list[CommitDecision] = []
        reverse_freeze_decisions: list[CommitDecision] = []
        fallback_decision: Optional[CommitDecision] = None
        for primary in _iter_terminal_candidates(primary_candidates, segments):
            if primary.kind == "swallow":
                best_pending = primary.segments
                continue
            primary_self = check_candidate_self_independence(
                primary,
                ma34,
                all_segments=segments,
                boundary_evidence_segments=(
                    center_evidence_builder(primary, segments)
                    if center_evidence_builder
                    else None
                ),
            )
            reverse_candidates = find_reverse_confirmation_candidates(
                segments,
                primary.end_offset,
                primary.direction,
                ma34,
                ma170,
                completed_segments,
            )
            if not reverse_candidates:
                best_pending = primary.segments
                continue

            for reverse, reverse_self in _iter_reverse_independence_candidates(
                reverse_candidates,
                ma34,
                all_segments=segments,
                ma170=ma170,
                completed_segments=completed_segments,
                center_evidence_builder=center_evidence_builder,
            ):
                if reverse.kind == "swallow" and len(primary.segments) <= 3:
                    best_pending = primary.segments
                    continue
                extra_segments = ()
                tail_offset = primary.end_offset
                if primary_self.ok:
                    commit_independence = primary_self
                elif _can_chain_confirm(primary_self, reverse, reverse_self):
                    commit_independence = _chain_independence(
                        primary_self,
                        reverse,
                        reverse_self,
                    )
                    extra_segments = ((reverse.segments, reverse_self),)
                    tail_offset = reverse.end_offset
                elif primary_self.center_kind == "trend_class":
                    best_pending = primary.segments
                    continue
                else:
                    independence = check_daily_segment_independence(
                        primary,
                        reverse,
                        segments,
                        ma34,
                        ma170,
                        completed_segments,
                    )
                    if not independence.ok:
                        if not _cold_start_relax_allowed(primary, completed_segments):
                            best_pending = primary.segments
                            continue
                        independence = IndependenceDecision(
                            ok=True,
                            kind="cold_start_swallow_primary_relax",
                            reason="cold start primary swallow keeps legacy relax behavior",
                        )
                    commit_independence = independence
                if (
                    allow_cold_start
                    and not _has_maturity_barrier(commit_independence)
                    and commit_independence.kind != "same_trend_chain"
                    and not _reverse_strictly_breaks_primary_start(primary, reverse)
                ):
                    best_pending = reverse.segments
                    continue
                if commit_independence.center_kind == "turning" and not _is_structural_independent(reverse_self):
                    best_pending = primary.segments
                    continue
                possible_decision = CommitDecision(
                    start_offset=primary.start_offset,
                    end_offset=primary.end_offset,
                    segments=primary.segments,
                    pending_segments=reverse.segments,
                    tail_offset=tail_offset,
                    independence=commit_independence,
                    extra_segments=extra_segments,
                    candidate_kind=primary.kind,
                )
                if allow_cold_start and primary.start_offset == 0 and primary.segments[0].cache.get("is_macro_swallow"):
                    return possible_decision

                if _has_maturity_barrier(commit_independence):
                    mature_decisions.append(possible_decision)
                elif primary_self.ok and _is_structural_independent(reverse_self):
                    reverse_freeze_decisions.append(possible_decision)
                fallback_decision = possible_decision
                best_pending = reverse.segments

        selected = None
        if swallow_fallback and (
            fallback_decision is None or swallow_fallback.end_offset > fallback_decision.end_offset
        ):
            selected = swallow_fallback
        elif reverse_freeze_decisions:
            selected = reverse_freeze_decisions[0]
        elif mature_decisions:
            selected = _select_mature_commit_decision(mature_decisions)
        elif fallback_decision:
            selected = fallback_decision
        elif open_tail_decision:
            selected = open_tail_decision
        if selected:
            if allow_cold_start and start_offset == 0 and _has_maturity_barrier(selected.independence):
                return selected
            if allow_cold_start and start_offset == 0:
                cold_start_fallback = selected
            else:
                return selected

        if allow_cold_start and start_offset > 0:
            break

    if cold_start_fallback:
        return cold_start_fallback
    if best_pending:
        return CommitDecision(start_offset=-1, end_offset=-1, segments=[], pending_segments=best_pending)
    return None


def select_valid_daily_window(
    segments: Sequence,
    completed_segments: Sequence,
    ma34,
    ma170,
    continuity_broken: bool = False,
    previous_end_k=None,
) -> List:
    if continuity_broken or len(segments) < 3:
        return []

    chosen: List = []
    max_len = len(segments) if len(segments) % 2 == 1 else len(segments) - 1
    for window_len in range(3, max_len + 1, 2):
        window = list(segments[:window_len])
        lag_segment = segments[window_len] if window_len < len(segments) else None
        if is_valid_regular_window(
            window,
            ma34,
            ma170,
            lag_segment=lag_segment,
            completed_segments=completed_segments,
            previous_end_k=previous_end_k,
        ):
            chosen = window
    return chosen
