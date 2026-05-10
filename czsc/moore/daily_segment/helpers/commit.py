# -*- coding: utf-8 -*-
"""日线级别线段候选选择与延迟提交决策。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence

from czsc.py.enum import Direction

from .continuity import check_daily_segment_continuity
from .extension import is_extension_same_trend, is_opposite_direction
from .ma_cross import check_ma_cross_correlation
from .non_same import try_non_same_candidate
from .trend import check_global_trend_relationship
from ..utils import seg_end_price, seg_start_price


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

    @property
    def next_tail_offset(self) -> int:
        return self.end_offset if self.tail_offset is None else self.tail_offset


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
    if candidate.kind != "regular" or _candidate_has_swallow(candidate):
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


def _reverse_breaks_primary_last_pivot(primary: WindowCandidate, reverse: WindowCandidate) -> bool:
    pivot = seg_start_price(primary.segments[-1])
    reverse_end = seg_end_price(reverse.segments[-1])
    if primary.direction == Direction.Down:
        return reverse_end > pivot
    if primary.direction == Direction.Up:
        return reverse_end < pivot
    return False


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
            reverse_candidates = find_reverse_candidates(
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
                if not _reverse_breaks_primary_last_pivot(primary, reverse):
                    best_pending = primary.segments
                    continue

                possible_decision = CommitDecision(
                    start_offset=primary.start_offset,
                    end_offset=primary.end_offset,
                    segments=primary.segments,
                    pending_segments=reverse.segments,
                    tail_offset=primary.end_offset,
                )

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
