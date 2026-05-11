# -*- coding: utf-8 -*-
"""日线级别线段候选选择与延迟提交决策。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence

from czsc.py.enum import Direction

from ..centers.algo import find_center
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


@dataclass(frozen=True)
class CommitDecision:
    start_offset: int
    end_offset: int
    segments: List
    pending_segments: List
    tail_offset: Optional[int] = None
    independence: Optional["IndependenceDecision"] = None
    extra_segments: tuple = ()

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
    reason: str = ""


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


def _candidate_has_swallow(candidate: WindowCandidate) -> bool:
    return candidate.kind == "swallow" or any(
        seg.cache.get("is_macro_swallow") and seg.direction == candidate.direction
        for seg in candidate.segments
    )


def _is_strong_reverse_candidate(candidate: WindowCandidate, segments: Sequence, ma34, ma170) -> bool:
    if len(candidate.segments) >= 5:
        return True
    if candidate.kind == "non_same":
        return True
    if _candidate_has_swallow(candidate):
        lag_segment = segments[candidate.end_offset] if candidate.end_offset < len(segments) else None
        return check_ma_cross_correlation(candidate.segments, ma34, ma170, lag_segment)
    return False


def _candidate_extends_primary(primary: WindowCandidate, confirmer: WindowCandidate) -> bool:
    primary_end = seg_end_price(primary.segments[-1])
    confirm_end = seg_end_price(confirmer.segments[-1])
    if primary.direction == Direction.Up:
        return confirm_end > primary_end
    if primary.direction == Direction.Down:
        return confirm_end < primary_end
    return False


def _reverse_strictly_breaks_primary_start(primary: WindowCandidate, reverse: WindowCandidate) -> bool:
    pivot = seg_start_price(primary.segments[0])
    reverse_end = seg_end_price(reverse.segments[-1])
    if primary.direction == Direction.Down:
        return reverse_end > pivot
    if primary.direction == Direction.Up:
        return reverse_end < pivot
    return False


def _find_candidate_center(candidate: WindowCandidate, ma34) -> Optional[dict]:
    return find_center(candidate.segments, ma34, trend_direction=candidate.direction)


def _find_boundary_turning_center(primary: WindowCandidate, reverse: WindowCandidate, ma34) -> Optional[dict]:
    if not primary.segments or not reverse.segments:
        return None
    boundary_segments = [*primary.segments, reverse.segments[0]]
    result = find_center(boundary_segments, ma34, trend_direction=primary.direction)
    if result and result.get("center_kind") == "turning":
        return result
    return None


def _decision_from_center(kind: str, center: dict, **kwargs) -> IndependenceDecision:
    return IndependenceDecision(
        ok=True,
        kind=kind,
        center_kind=center.get("center_kind", ""),
        center_low=center.get("low"),
        center_high=center.get("high"),
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

    boundary_turning = _find_boundary_turning_center(primary, reverse, ma34)
    if boundary_turning:
        return _decision_from_center(
            "third_buy_sell",
            boundary_turning,
            requires_new_extreme=False,
            new_extreme_ok=None,
            reason="boundary turning center third buy/sell confirms independence",
        )

    candidate_center = _find_candidate_center(reverse, ma34)
    new_extreme_ok = _reverse_strictly_breaks_primary_start(primary, reverse)

    if candidate_center is None:
        return IndependenceDecision(
            ok=True,
            kind="no_daily_center",
            reason="candidate itself is a valid daily segment and has no daily center",
        )

    center_kind = candidate_center.get("center_kind", "")
    if center_kind == "turning":
        return _decision_from_center(
            "third_buy_sell",
            candidate_center,
            requires_new_extreme=False,
            new_extreme_ok=None,
            reason="candidate turning center third buy/sell confirms independence",
        )

    if new_extreme_ok:
        return _decision_from_center(
            "strict_new_extreme",
            candidate_center,
            requires_new_extreme=True,
            new_extreme_ok=True,
            reason="reverse candidate strictly breaks primary start extreme",
        )

    if center_kind == "trend_class":
        return IndependenceDecision(
            ok=False,
            kind="third_buy_sell",
            center_kind=center_kind,
            center_low=candidate_center.get("low"),
            center_high=candidate_center.get("high"),
            requires_new_extreme=True,
            new_extreme_ok=False,
            reason="trend-class center needs third buy/sell plus strict new extreme",
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


def find_delayed_commit_decision(
    segments: Sequence,
    completed_segments: Sequence,
    ma34,
    ma170,
    continuity_broken: bool = False,
    allow_cold_start: bool = False,
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
            require_ma=False,
        )
        if not primary_candidates:
            continue

        if not best_pending:
            best_pending = primary_candidates[-1].segments

        swallow_fallback: Optional[CommitDecision] = None
        chosen_decision: Optional[CommitDecision] = None
        for primary in _iter_terminal_candidates(primary_candidates, segments):
            if primary.kind == "swallow":
                best_pending = primary.segments
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
                best_pending = primary.segments
                continue

            committed = False
            for reverse in reverse_candidates:
                if reverse.kind == "swallow" and len(primary.segments) <= 3:
                    best_pending = primary.segments
                    continue
                if not _is_strong_reverse_candidate(reverse, segments, ma34, ma170):
                    best_pending = primary.segments
                    continue
                independence = check_daily_segment_independence(
                    primary,
                    reverse,
                    segments,
                    ma34,
                    ma170,
                    completed_segments,
                )
                if not independence.ok:
                    is_cold_start = len(completed_segments) == 0
                    can_relax_cold_start = (
                        ENABLE_COLD_START_RELAX_PIVOT_BREAK
                        and is_cold_start
                        and primary.start_offset == 0
                        and primary.segments[0].cache.get("is_macro_swallow")
                    )
                    if not can_relax_cold_start:
                        best_pending = primary.segments
                        continue
                    independence = IndependenceDecision(
                        ok=True,
                        kind="cold_start_swallow_primary_relax",
                        reason="cold start primary swallow keeps legacy relax behavior",
                    )

                possible_decision = CommitDecision(
                    start_offset=primary.start_offset,
                    end_offset=primary.end_offset,
                    segments=primary.segments,
                    pending_segments=reverse.segments,
                    tail_offset=primary.end_offset,
                    independence=independence,
                )
                if allow_cold_start and primary.start_offset == 0 and primary.segments[0].cache.get("is_macro_swallow"):
                    return possible_decision

                confirmers = find_reverse_candidates(
                    segments,
                    reverse.end_offset,
                    reverse.direction,
                    ma34,
                    ma170,
                    completed_segments,
                )
                if not confirmers:
                    if reverse.kind == "swallow" and len(primary.segments) > 3:
                        swallow_fallback = possible_decision
                    chosen_decision = possible_decision
                    best_pending = reverse.segments
                    continue

                confirmer = None
                for candidate in confirmers:
                    if _candidate_extends_primary(primary, candidate):
                        confirmer = candidate
                        break
                if confirmer is None:
                    chosen_decision = possible_decision
                    best_pending = reverse.segments
                    continue

                committed = True
                chosen_decision = possible_decision
                break
            if committed:
                continue

        selected = None
        if swallow_fallback and (
            chosen_decision is None or swallow_fallback.end_offset > chosen_decision.end_offset
        ):
            selected = swallow_fallback
        elif chosen_decision:
            selected = chosen_decision
        if selected:
            if allow_cold_start and start_offset == 0:
                cold_start_fallback = selected
            else:
                return selected

        if allow_cold_start and start_offset > 0:
            break

    if best_pending:
        return CommitDecision(start_offset=-1, end_offset=-1, segments=[], pending_segments=best_pending)
    if cold_start_fallback:
        return cold_start_fallback
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
