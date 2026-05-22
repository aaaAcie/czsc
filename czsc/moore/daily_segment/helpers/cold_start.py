# -*- coding: utf-8 -*-
"""冷启动入口。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional, Sequence

from .commit import CommitDecision, candidates_from_start, find_delayed_commit_decision


@dataclass(frozen=True)
class ColdStartPathNode:
    """冷启动路径里的单条候选日线线段。"""

    start_offset: int
    end_offset: int
    segments: tuple
    kind: str
    score: tuple = ()


@dataclass(frozen=True)
class ColdStartPath:
    """冷启动候选路径，用于避免首段陷入局部最优。"""

    nodes: tuple[ColdStartPathNode, ...]
    decision: CommitDecision
    score: tuple


def _shift_decision(decision: CommitDecision, start_offset: int) -> CommitDecision:
    tail_offset = None
    if decision.tail_offset is not None:
        tail_offset = start_offset + decision.tail_offset
    return CommitDecision(
        start_offset=start_offset + decision.start_offset,
        end_offset=start_offset + decision.end_offset,
        segments=decision.segments,
        pending_segments=decision.pending_segments,
        tail_offset=tail_offset,
        independence=decision.independence,
        extra_segments=decision.extra_segments,
        candidate_kind=decision.candidate_kind,
    )


def _decision_path(decision: CommitDecision) -> ColdStartPath:
    explained_offset = decision.next_tail_offset
    pending_len = len(decision.pending_segments or [])
    independence = decision.independence
    has_trend_center = 1 if independence and independence.center_kind == "trend_class" else 0
    has_chain = 1 if independence and independence.kind == "same_trend_chain" else 0
    start_bonus = 1 if decision.start_offset == 0 else 0
    node = ColdStartPathNode(
        start_offset=decision.start_offset,
        end_offset=decision.end_offset,
        segments=tuple(decision.segments),
        kind=decision.candidate_kind or "regular",
        score=(explained_offset, pending_len, has_trend_center, has_chain, start_bonus),
    )
    score = (
        explained_offset,
        pending_len,
        has_trend_center,
        has_chain,
        start_bonus,
        len(decision.segments),
        -decision.start_offset,
    )
    return ColdStartPath(nodes=(node,), decision=decision, score=score)


def _find_offset_decision(
    segments: Sequence,
    start_offset: int,
    ma34,
    ma170,
    center_evidence_builder: Optional[Callable] = None,
) -> Optional[CommitDecision]:
    decision = find_delayed_commit_decision(
        segments[start_offset:],
        completed_segments=(),
        ma34=ma34,
        ma170=ma170,
        continuity_broken=False,
        allow_cold_start=False,
        center_evidence_builder=center_evidence_builder,
    )
    if decision is None or not decision.segments:
        return None
    return _shift_decision(decision, start_offset)


def _leading_candidate_should_wait(
    segments: Sequence,
    baseline: CommitDecision,
    ma34,
    ma170,
) -> Optional[CommitDecision]:
    if baseline.start_offset <= 0:
        return None
    candidates = candidates_from_start(
        segments,
        0,
        ma34,
        ma170,
        completed_segments=(),
        enforce_continuity=False,
        include_swallow_candidate=False,
    )
    if not candidates:
        return None
    leading = candidates[-1]
    local_span = len(baseline.segments) + len(baseline.pending_segments or [])
    if leading.end_offset < baseline.next_tail_offset:
        return None
    if len(leading.segments) < local_span:
        return None
    return CommitDecision(
        start_offset=-1,
        end_offset=-1,
        segments=[],
        pending_segments=list(leading.segments),
    )


def find_cold_start_path_decision(
    segments: Sequence,
    ma34,
    ma170,
    center_evidence_builder: Optional[Callable] = None,
    max_start_offsets: int = 3,
) -> Optional[CommitDecision]:
    paths: list[ColdStartPath] = []
    baseline = find_delayed_commit_decision(
        segments,
        completed_segments=(),
        ma34=ma34,
        ma170=ma170,
        continuity_broken=False,
        allow_cold_start=True,
        center_evidence_builder=center_evidence_builder,
    )
    if baseline is not None and baseline.segments:
        paths.append(_decision_path(baseline))
    else:
        return baseline

    wait_decision = _leading_candidate_should_wait(segments, baseline, ma34, ma170)
    if wait_decision is not None:
        return wait_decision

    max_offset = min(max_start_offsets, max(0, len(segments) - 2))
    for start_offset in range(max_offset):
        decision = _find_offset_decision(
            segments,
            start_offset,
            ma34,
            ma170,
            center_evidence_builder=center_evidence_builder,
        )
        if decision is None:
            continue
        paths.append(_decision_path(decision))

    if paths:
        return max(paths, key=lambda path: path.score).decision
    return baseline


def find_cold_start_decision(
    segments: Sequence,
    ma34,
    ma170,
    center_evidence_builder: Optional[Callable] = None,
) -> Optional[CommitDecision]:
    return find_cold_start_path_decision(
        segments,
        ma34=ma34,
        ma170=ma170,
        center_evidence_builder=center_evidence_builder,
    )
