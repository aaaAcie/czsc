# -*- coding: utf-8 -*-
"""日线级别线段分析器。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence

from czsc.py.enum import Direction, Mark
from czsc.py.objects import RawBar

from ..objects import MooreSegment
from .centers.algo import find_center
from .helpers.commit import (
    CommitDecision,
    IndependenceDecision,
    WindowCandidate,
    candidates_from_start,
    find_delayed_commit_decision,
    is_valid_regular_window,
    select_valid_daily_window,
    should_commit_leading_swallow,
)
from .helpers.continuity import check_daily_segment_continuity
from .helpers.cold_start import find_cold_start_decision
from .helpers.extension import is_extension_same_trend, is_opposite_direction
from .helpers.ma_cross import (
    check_ma_cross_correlation,
    has_ma_cross_between,
    ma_relation_state_at,
    ma_relation_state_for_tk,
    ma_relation_state_from_values,
    ma_reverses_against_window,
    turning_index,
)
from .helpers.non_same import try_non_same_candidate
from .helpers.trend import check_global_trend_relationship
from .objects import DailySegment, DailySegmentCenter
from .state import DailySegmentState
from .utils import (
    build_sma_array,
    clone_completed_segments_snapshot,
    collect_bars_by_index,
    seg_end_index,
    seg_end_price,
    seg_start_index,
    slice_segments_from_anchor,
)


@dataclass(frozen=True)
class RepairProposal:
    """日线中枢 owner-chain 修正提案。"""

    seed_result: dict
    refined_segment: MooreSegment
    source_segments: List[MooreSegment]
    promoted_result: dict


class DailySegmentAnalyzer:
    """消费 30F 宏观线段构造日线级别线段与中枢。"""

    def __init__(
        self,
        segments: Optional[Sequence[MooreSegment]] = None,
        bars: Optional[Sequence[RawBar]] = None,
        micro_segments: Optional[Sequence[MooreSegment]] = None,
        rebuild_centers_after_segment_change: bool = False,
    ):
        self.state = DailySegmentState()
        self.state.rebuild_centers_after_segment_change = rebuild_centers_after_segment_change
        if bars:
            self.state.bars_raw = list(bars)
        if micro_segments:
            self.state.micro_segments = list(micro_segments)
        if segments:
            self.update(list(segments), micro_segments=micro_segments)

    def update(
        self,
        segments: Sequence[MooreSegment],
        bars: Optional[Sequence[RawBar]] = None,
        micro_segments: Optional[Sequence[MooreSegment]] = None,
    ):
        s = self.state
        sig = tuple(
            (
                seg.start_k.k_index,
                seg.end_k.k_index,
                turning_index(seg.start_k),
                turning_index(seg.end_k),
                bool(seg.cache.get("is_macro_swallow")),
            )
            for seg in segments
        )
        if bars is not None:
            s.bars_raw = list(bars)
        if micro_segments is not None:
            s.micro_segments = list(micro_segments)
        if sig == s.last_sig and bars is None and micro_segments is None:
            return
        s.last_sig = sig
        s.base_segments = list(segments)
        self._rebuild()

    @property
    def daily_segments(self) -> List[DailySegment]:
        return self.state.completed_segments

    @property
    def daily_pending_segments(self) -> List[DailySegment]:
        return [
            segment
            for segment in self.state.pending_display_segments
            if segment.cache.get("candidate_kind") != "non_same"
        ]

    @property
    def daily_non_same_segments(self) -> List[DailySegment]:
        return [
            segment
            for segment in self.state.pending_display_segments
            if segment.cache.get("candidate_kind") == "non_same"
        ]

    @property
    def daily_pending_centers(self) -> List[DailySegmentCenter]:
        return self.state.pending_centers

    @property
    def active_center(self) -> Optional[DailySegmentCenter]:
        return self.state.active_center

    @property
    def daily_centers(self) -> List[DailySegmentCenter]:
        return self.state.daily_centers

    @property
    def daily_center_source_segments(self) -> List[MooreSegment]:
        return self.state.daily_center_source_segments

    @property
    def refined_segments(self) -> List[MooreSegment]:
        return self.state.refined_segments

    @property
    def pending_refined_segments(self) -> List[MooreSegment]:
        return self.state.pending_refined_segments

    @property
    def archived_centers(self) -> List[DailySegmentCenter]:
        return self.state.archived_centers

    @property
    def candidates(self) -> List[DailySegmentCenter]:
        return self.state.candidates

    # 兼容旧命名
    @property
    def higher_segments(self) -> List[DailySegment]:
        return self.daily_segments

    def _rebuild(self):
        s = self.state
        bars_by_index = {i: bar for i, bar in enumerate(s.bars_raw)} if s.bars_raw else collect_bars_by_index(s.base_segments)
        s.ma34 = build_sma_array(bars_by_index, window=34)
        s.ma170 = build_sma_array(bars_by_index, window=170)

        danger, should_fallback = slice_segments_from_anchor(
            s.base_segments, s.anchor_k_index, s.anchor_dt
        )
        if s.anchor_k_index is None or should_fallback or danger is None:
            self._full_rebuild_from_scratch()
            self._rebuild_daily_centers()
            return

        self._restore_from_anchor_snapshot()
        for seg in danger:
            self._process_new_segment(seg)
        self._rebuild_daily_centers()

    def _full_rebuild_from_scratch(self):
        s = self.state
        s.current_segments = []
        s.pending_daily_segments = []
        s.pending_display_segments = []
        s.completed_segments = []
        s.daily_centers = []
        s.pending_centers = []
        s.active_center = None
        s.archived_centers = []
        s.candidates = []
        s.refined_segments = []
        s.pending_refined_segments = []
        s.anchor_k_index = None
        s.anchor_dt = None
        s.anchor_completed_segments = []
        s.pending_anchor_snapshot = False
        s.continuity_broken = False
        s.pending_after_tail_extension = False

        for seg in s.base_segments:
            self._process_new_segment(seg)
        self._finalize_terminal_swallow_pending()

    def _is_excluded_from_daily_center(self, seg: MooreSegment) -> bool:
        """Hook for daily-center source filtering.

        Callers can extend the mouth by setting ``exclude_from_daily_center`` in
        segment cache.  Macro swallow segments are not excluded here by default:
        only swallow segments promoted to a daily segment are expanded at the
        daily-segment source layer.
        """
        return bool(seg.cache.get("exclude_from_daily_center"))

    def _daily_center_source_segments(self) -> List[MooreSegment]:
        return [seg for seg in self.state.base_segments if not self._is_excluded_from_daily_center(seg)]

    @staticmethod
    def _turning_id(tk) -> Optional[int]:
        return tk.cache.get("source_micro_id", tk.cache.get("micro_id"))

    @staticmethod
    def _clone_source_segment(seg: MooreSegment, cache_updates: Optional[dict] = None) -> MooreSegment:
        cache = dict(seg.cache)
        if cache_updates:
            cache.update(cache_updates)
        return MooreSegment(
            symbol=seg.symbol,
            start_k=seg.start_k,
            end_k=seg.end_k,
            direction=seg.direction,
            bars=list(seg.bars),
            centers=list(seg.centers),
            cache=cache,
        )

    def _micro_segments_between(self, start_id, end_id) -> List[MooreSegment]:
        if start_id is None or end_id is None:
            return []
        segments = []
        for seg in self.state.micro_segments:
            sid = self._turning_id(seg.start_k)
            eid = self._turning_id(seg.end_k)
            if sid is None or eid is None:
                continue
            lo = min(start_id, end_id)
            hi = max(start_id, end_id)
            if lo <= sid and eid <= hi:
                segments.append(seg)
        return sorted(segments, key=lambda seg: (seg_start_index(seg), seg_end_index(seg)))

    def _expand_daily_swallow_segment(self, seg: MooreSegment) -> List[MooreSegment]:
        source_segments = self._micro_segments_between(self._turning_id(seg.start_k), self._turning_id(seg.end_k))
        if not source_segments:
            return [self._clone_source_segment(seg)]
        owner = (seg_start_index(seg), seg_end_index(seg))
        return [
            self._clone_source_segment(
                source,
                {
                    "source_for_daily_center": "expanded_from_swallow",
                    "expanded_from_macro_segment": owner,
                },
            )
            for source in source_segments
        ]

    def _daily_segment_center_source_segments(self, daily_segment: DailySegment) -> List[MooreSegment]:
        source_segments: List[MooreSegment] = []
        expand_daily_swallow = bool(daily_segment.cache.get("from_macro_swallow"))
        for seg in daily_segment.segments:
            if self._is_excluded_from_daily_center(seg):
                continue
            if expand_daily_swallow and seg.cache.get("is_macro_swallow"):
                source_segments.extend(self._expand_daily_swallow_segment(seg))
            else:
                source_segments.append(seg)
        return sorted(source_segments, key=lambda seg: (seg_start_index(seg), seg_end_index(seg)))

    @staticmethod
    def _center_segment_keys(center: DailySegmentCenter) -> set:
        return {(seg_start_index(seg), seg_end_index(seg)) for seg in center.segments}

    @classmethod
    def _center_segment_overlap_count(cls, left: DailySegmentCenter, right: DailySegmentCenter) -> int:
        return left.shared_endpoint_count(right)

    @classmethod
    def _center_can_squeeze(cls, weaker: DailySegmentCenter, stronger: DailySegmentCenter) -> bool:
        if cls._center_segment_overlap_count(weaker, stronger) < 3:
            return False
        if stronger.overlap_type == 3 and weaker.overlap_type == 1:
            return True
        if stronger.overlap_type == 3 and weaker.overlap_type == 3:
            return cls._center_generation_rank(stronger) < cls._center_generation_rank(weaker)
        return False

    @staticmethod
    def _tk_key(tk) -> tuple:
        return (tk.k_index, tk.dt, tk.price, tk.mark.value)

    @classmethod
    def _seg_key(cls, seg: MooreSegment) -> tuple:
        return (cls._tk_key(seg.start_k), cls._tk_key(seg.end_k))

    @classmethod
    def _endpoint_axis(cls, source_segments: Sequence[MooreSegment]) -> tuple:
        if not source_segments:
            return ()
        axis = [cls._tk_key(source_segments[0].start_k)]
        axis.extend(cls._tk_key(seg.end_k) for seg in source_segments)
        return tuple(axis)

    @classmethod
    def _center_endpoint_span(
        cls,
        center_segments: Sequence[MooreSegment],
        source_segments: Sequence[MooreSegment],
    ) -> Optional[tuple[int, int]]:
        axis = cls._endpoint_axis(source_segments)
        pos = {key: idx for idx, key in enumerate(axis)}
        indexes = []
        for seg in center_segments:
            for tk in (seg.start_k, seg.end_k):
                idx = pos.get(cls._tk_key(tk))
                if idx is not None:
                    indexes.append(idx)
        if not indexes:
            return None
        return (min(indexes), max(indexes))

    @staticmethod
    def _turning_range(seg: MooreSegment) -> tuple:
        start = turning_index(seg.start_k)
        end = turning_index(seg.end_k)
        return (min(start, end), max(start, end))

    @staticmethod
    def _desired_owner_mark(point_name: str, trend_direction: Direction) -> Mark:
        down_marks = {"A": Mark.D, "B": Mark.G, "C": Mark.D, "D": Mark.G}
        up_marks = {"A": Mark.G, "B": Mark.D, "C": Mark.G, "D": Mark.D}
        return (down_marks if trend_direction == Direction.Down else up_marks)[point_name]

    def _owner_endpoint_for_point(
        self,
        point_name: str,
        point: tuple,
        source_segments: Sequence[MooreSegment],
        trend_direction: Direction,
    ) -> Optional[dict]:
        idx = point[0]
        desired_mark = self._desired_owner_mark(point_name, trend_direction)
        matches = []
        for seg in source_segments:
            left, right = self._turning_range(seg)
            if not (left <= idx <= right):
                continue
            for side, tk in (("start", seg.start_k), ("end", seg.end_k)):
                if tk.mark == desired_mark:
                    matches.append((seg, side, tk))
        if not matches:
            return None
        seg, side, tk = min(matches, key=lambda item: abs(turning_index(item[2]) - idx))
        return {
            "point": point_name,
            "owner_endpoint": self._tk_key(tk),
            "owner_endpoint_index": turning_index(tk),
            "owner_endpoint_side": side,
            "owner_segment": self._seg_key(seg),
        }

    def _segment_between_endpoints(self, start_tk, end_tk, source_segments: Sequence[MooreSegment]) -> Optional[MooreSegment]:
        start_key = self._tk_key(start_tk)
        end_key = self._tk_key(end_tk)
        for seg in source_segments:
            if self._tk_key(seg.start_k) == start_key and self._tk_key(seg.end_k) == end_key:
                return seg
        return None

    def _owner_chain_evidence(
        self,
        result: dict,
        source_segments: Sequence[MooreSegment],
        trend_direction: Direction,
    ) -> dict:
        points = result.get("points") or {}
        point_owners = {}
        owner_chain = []
        for point_name in ("A", "B", "C", "D"):
            point = points.get(point_name)
            if point is None:
                continue
            owner = self._owner_endpoint_for_point(point_name, point, source_segments, trend_direction)
            if owner is None:
                point_owners[point_name] = None
                continue
            point_owners[point_name] = owner
            owner_chain.append(owner["owner_endpoint"])

        owner_keys = [owner.get("owner_endpoint") for owner in point_owners.values() if owner]
        continuous = len(owner_keys) == len(point_owners) and len(owner_keys) >= 2
        if continuous:
            for left, right in zip(owner_keys, owner_keys[1:]):
                if not any(self._tk_key(seg.start_k) == left and self._tk_key(seg.end_k) == right for seg in source_segments):
                    continuous = False
                    break

        return {
            "point_owners": point_owners,
            "owner_chain": owner_chain,
            "owner_chain_valid": continuous,
        }

    def _owner_keys_valid_for_source(self, owner_keys: Sequence[tuple], source_segments: Sequence[MooreSegment]) -> bool:
        if len(owner_keys) < 2 or len(owner_keys) != len(set(owner_keys)):
            return False
        for left, right in zip(owner_keys, owner_keys[1:]):
            if not any(self._tk_key(seg.start_k) == left and self._tk_key(seg.end_k) == right for seg in source_segments):
                return False
        return True

    def _bars_between_tks(self, start_tk, end_tk) -> List[RawBar]:
        left = min(start_tk.k_index, end_tk.k_index)
        right = max(start_tk.k_index, end_tk.k_index)
        return [
            bar
            for idx, bar in enumerate(self.state.bars_raw)
            if left <= getattr(bar, "id", idx) <= right
        ]

    def _make_refined_segment(self, start_tk, end_tk, source_result: dict) -> MooreSegment:
        direction = Direction.Up if end_tk.price >= start_tk.price else Direction.Down
        return MooreSegment(
            symbol=start_tk.symbol,
            start_k=start_tk,
            end_k=end_tk,
            direction=direction,
            bars=self._bars_between_tks(start_tk, end_tk),
            cache={
                "source": "daily_segment_owner_chain_repair",
                "repair_reason": "missing_continuous_owner_segment_for_badc",
                "source_center_key": (
                    seg_start_index(source_result["segments"][0]),
                    seg_end_index(source_result["segments"][-1]),
                    round(source_result["high"], 8),
                    round(source_result["low"], 8),
                ),
            },
        )

    def _find_source_segment_offset(self, source_segments: Sequence[MooreSegment], target: MooreSegment) -> Optional[int]:
        target_key = self._seg_key(target)
        for idx, seg in enumerate(source_segments):
            if self._seg_key(seg) == target_key:
                return idx
        return None

    def _variant_contains_segment(self, result: dict, refined: MooreSegment) -> bool:
        refined_key = self._seg_key(refined)
        return any(self._seg_key(seg) == refined_key for seg in result.get("segments", []))

    def _replace_source_span_with_refined(
        self,
        source_segments: Sequence[MooreSegment],
        refined: MooreSegment,
    ) -> Optional[List[MooreSegment]]:
        start_offset = None
        end_offset = None
        for idx, seg in enumerate(source_segments):
            if self._tk_key(seg.start_k) == self._tk_key(refined.start_k):
                start_offset = idx
            if start_offset is not None and self._tk_key(seg.end_k) == self._tk_key(refined.end_k):
                end_offset = idx
                break
        if start_offset is None or end_offset is None or start_offset > end_offset:
            return None
        return list(source_segments[:start_offset]) + [refined] + list(source_segments[end_offset + 1 :])

    def _has_intermediate_direct_type3(
        self,
        source_segments: Sequence[MooreSegment],
        repair_start_offset: int,
        scan_end_offset: int,
        trend_direction: Direction,
        before_index: Optional[int] = None,
    ) -> bool:
        """A type0 seed cannot skip over a direct type3 formed inside its repair path."""
        scan_end_offset = min(scan_end_offset, len(source_segments) - 1)
        for start_offset in range(repair_start_offset + 1, scan_end_offset - 2):
            result = find_center(
                source_segments[start_offset : scan_end_offset + 1],
                self.state.ma34,
                trend_direction=trend_direction,
            )
            if result and result.get("overlap_type") == 3 and result.get("status") == "FINAL":
                entry_index = self._third_segment_entry_index(result, self.state.ma34)
                if before_index is None or entry_index is None or entry_index <= before_index:
                    return True
        return False

    def _find_promoted_type3_with_refined(
        self,
        source_segments: Sequence[MooreSegment],
        start_offset: int,
        refined: MooreSegment,
        trend_direction: Direction,
    ) -> Optional[dict]:
        for offset in range(start_offset, max(0, len(source_segments) - 3)):
            result = find_center(source_segments[offset:], self.state.ma34, trend_direction=trend_direction)
            if (
                result
                and result.get("overlap_type") == 3
                and result.get("status") == "FINAL"
                and self._variant_contains_segment(result, refined)
            ):
                return result
        return None

    def _build_owner_chain_repair_proposals(
        self,
        source_segments: Sequence[MooreSegment],
        candidate_result: dict,
        trend_direction: Direction,
    ) -> List[RepairProposal]:
        if candidate_result.get("overlap_type") >= 3:
            return []
        segs = candidate_result.get("segments") or []
        if len(segs) < 4 or "A" not in candidate_result.get("points", {}) or "B" not in candidate_result.get("points", {}):
            return []

        proposals: List[RepairProposal] = []
        seen = set()
        seed_offset = self._find_source_segment_offset(source_segments, segs[0])
        if seed_offset is None:
            return []

        repair_seed_segments = [segs[3]]
        if candidate_result.get("overlap_type") == 1:
            repair_seed_segments.insert(0, segs[2])

        repair_start_offsets = []
        for repair_seed in repair_seed_segments:
            repair_start_offset = self._find_source_segment_offset(source_segments, repair_seed)
            if repair_start_offset is not None and repair_start_offset not in repair_start_offsets:
                repair_start_offsets.append(repair_start_offset)

        for repair_start_offset in repair_start_offsets:
            repair_start_seg = source_segments[repair_start_offset]
            start_tk = repair_start_seg.start_k
            for end_offset in range(repair_start_offset + 2, len(source_segments) - 1):
                end_tk = source_segments[end_offset].end_k
                if start_tk.mark == end_tk.mark:
                    continue
                if self._segment_between_endpoints(start_tk, end_tk, source_segments) is not None:
                    continue

                refined = self._make_refined_segment(start_tk, end_tk, candidate_result)
                if refined.direction != repair_start_seg.direction:
                    continue
                replaced_span = list(source_segments[repair_start_offset : end_offset + 1])
                if not ma_reverses_against_window(replaced_span, self.state.ma34):
                    continue

                refined.cache.update(
                    {
                        "repair_context": "owner_chain_proposal",
                        "repair_reason": "daily_center_source_non_same",
                        "promoted_overlap_type": candidate_result.get("overlap_type"),
                    }
                )
                refined_key = self._seg_key(refined)
                if refined_key in seen:
                    continue
                repaired_source_segments = self._replace_source_span_with_refined(source_segments, refined)
                if repaired_source_segments is None:
                    continue

                if candidate_result.get("overlap_type") == 0:
                    promoted = self._find_promoted_type3_with_refined(
                        repaired_source_segments,
                        seed_offset,
                        refined,
                        trend_direction,
                    )
                else:
                    promoted = find_center(repaired_source_segments[seed_offset:], self.state.ma34, trend_direction=trend_direction)
                    if not (
                        promoted
                        and promoted.get("overlap_type") == 3
                        and promoted.get("status") == "FINAL"
                        and self._variant_contains_segment(promoted, refined)
                    ):
                        promoted = None
                if promoted:
                    promoted_entry_index = self._third_segment_entry_index(promoted, self.state.ma34)
                    if candidate_result.get("overlap_type") == 0 and self._has_intermediate_direct_type3(
                        source_segments,
                        repair_start_offset,
                        end_offset + 1,
                        trend_direction,
                        before_index=promoted_entry_index,
                    ):
                        return []
                    seen.add(refined_key)
                    proposals.append(
                        RepairProposal(
                            seed_result=candidate_result,
                            refined_segment=refined,
                            source_segments=repaired_source_segments,
                            promoted_result=promoted,
                        )
                    )
                    break
            if proposals:
                break
        return proposals

    def _candidate_owner_chain_repair(
        self,
        result: dict,
        source_segments: Sequence[MooreSegment],
        trend_direction: Direction,
    ) -> Optional[dict]:
        if result.get("overlap_type") != 3 or not {"A", "B", "C", "D"} <= set(result.get("points", {})):
            return None
        segs = result.get("segments") or []
        if len(segs) < 5:
            return None

        a_tk = segs[0].end_k
        b_tk = segs[1].end_k
        d_owner = self._owner_endpoint_for_point("D", result["points"]["D"], source_segments, trend_direction)
        d_tk = None
        if d_owner is not None:
            d_key = d_owner["owner_endpoint"]
            for seg in source_segments:
                if self._tk_key(seg.start_k) == d_key:
                    d_tk = seg.start_k
                    break
                if self._tk_key(seg.end_k) == d_key:
                    d_tk = seg.end_k
                    break
        if d_tk is None:
            d_tk = segs[-2].end_k

        c_mark = self._desired_owner_mark("C", trend_direction)
        b_idx = turning_index(b_tk)
        d_idx = turning_index(d_tk)
        lo, hi = sorted((b_idx, d_idx))
        candidates = []
        for seg in source_segments:
            for tk in (seg.start_k, seg.end_k):
                idx = turning_index(tk)
                if lo < idx < hi and tk.mark == c_mark:
                    candidates.append(tk)
        if not candidates:
            return None
        c_tk = min(candidates, key=turning_index)

        refined_segment = None
        if self._segment_between_endpoints(c_tk, d_tk, source_segments) is None:
            refined_segment = self._make_refined_segment(c_tk, d_tk, result)

        owner_chain = [self._tk_key(tk) for tk in (a_tk, b_tk, c_tk, d_tk)]
        owner_chain_valid = self._owner_keys_valid_for_source(owner_chain, source_segments)
        return {
            "source": "daily_segment_owner_chain_repair",
            "source_segments_kind": "owner_chain_repair",
            "repair_reason": "missing_continuous_owner_segment_for_badc" if refined_segment else "",
            "point_owners": {
                "A": {"owner_endpoint": self._tk_key(a_tk)},
                "B": {"owner_endpoint": self._tk_key(b_tk)},
                "C": {"owner_endpoint": self._tk_key(c_tk), "inferred": True},
                "D": {"owner_endpoint": self._tk_key(d_tk)},
            },
            "owner_chain": owner_chain,
            "owner_chain_repaired": refined_segment is not None,
            "owner_chain_valid": owner_chain_valid,
            "refined_segments": [refined_segment] if refined_segment else [],
        }

    @staticmethod
    def _compact_repair_source_segments(source_segments: Sequence[MooreSegment]) -> List[MooreSegment]:
        """Build a compact diagnostic source for candidates hidden by swallows.

        This is intentionally separate from the official daily-center source
        sequence.  It lets the center layer explain an older/failed compact
        candidate and derive refined segments without mutating 30F facts.
        """
        return [
            seg
            for seg in source_segments
            if not seg.cache.get("is_macro_swallow") and not seg.cache.get("exclude_from_daily_center")
        ]

    @staticmethod
    def _center_generation_rank(center: DailySegmentCenter) -> tuple:
        if center.overlap_type == 3 and center.status == "FINAL":
            center_type_rank = 0
        elif center.overlap_type == 3:
            center_type_rank = 1
        elif center.overlap_type == 1:
            center_type_rank = 2
        else:
            center_type_rank = 3
        point_indices = [point[0] for point in center.points.values()]
        generation_index = center.cache.get("third_entry_index")
        if generation_index is None:
            generation_index = max(point_indices) if point_indices else center.end_index
        c_index = center.points.get("C", center.points.get("B", (center.start_index, None)))[0]
        source_kind_rank = 1 if center.cache.get("source_segments_kind") == "owner_chain_repair" else 0
        return (center_type_rank, generation_index, source_kind_rank, c_index, center.start_index, center.low, center.high)

    @staticmethod
    def _third_segment_entry_index(result: dict, ma_array) -> Optional[int]:
        if result.get("overlap_type") != 3:
            return None
        points = result.get("points") or {}
        if not {"A", "B", "C", "D"} <= set(points):
            return None
        a_val = points["A"][1]
        b_val = points["B"][1]
        c_idx = points["C"][0]
        d_idx = points["D"][0]
        low = min(a_val, b_val)
        high = max(a_val, b_val)
        for idx in range(c_idx, d_idx + 1):
            if idx >= len(ma_array):
                break
            ma_val = ma_array[idx]
            if ma_val is not None and low < ma_val < high:
                return idx
        return d_idx

    def _dedupe_overlapping_daily_centers(self, centers: Sequence[DailySegmentCenter]) -> List[DailySegmentCenter]:
        selected: List[DailySegmentCenter] = []
        for center in sorted(centers, key=self._center_generation_rank):
            if any(self._center_can_squeeze(center, kept) for kept in selected):
                continue
            selected.append(center)
        return selected

    def _record_center_result(
        self,
        centers: List[DailySegmentCenter],
        seen: set,
        refined_seen: set,
        refined_segments: List[MooreSegment],
        result: dict,
        source_segments: Sequence[MooreSegment],
        daily_segment: DailySegment,
        source: str,
        status_layer: str,
        collect_refined: bool,
        source_segments_kind: str,
        endpoint_source_segments: Optional[Sequence[MooreSegment]] = None,
        forced_refined_segments: Optional[Sequence[MooreSegment]] = None,
    ) -> None:
        seg_slice = result["segments"]
        endpoint_source_segments = endpoint_source_segments or source_segments
        owner_evidence = self._owner_chain_evidence(result, source_segments, daily_segment.direction)
        repair_evidence = self._candidate_owner_chain_repair(result, source_segments, daily_segment.direction)
        if forced_refined_segments:
            forced_refined_segments = list(forced_refined_segments)
            forced_repair_reason = next(
                (seg.cache.get("repair_reason") for seg in forced_refined_segments if seg.cache.get("repair_reason")),
                "missing_continuous_owner_segment_for_badc",
            )
            if repair_evidence is None:
                repair_evidence = {
                    "source": "daily_segment_owner_chain_repair",
                    "source_segments_kind": source_segments_kind,
                    "repair_reason": forced_repair_reason,
                    "point_owners": owner_evidence.get("point_owners", {}),
                    "owner_chain": owner_evidence.get("owner_chain", []),
                    "owner_chain_valid": False,
                    "refined_segments": [],
                }
            existing = {self._seg_key(seg) for seg in repair_evidence.get("refined_segments", [])}
            for refined in forced_refined_segments:
                if self._seg_key(refined) not in existing:
                    repair_evidence.setdefault("refined_segments", []).append(refined)
                    existing.add(self._seg_key(refined))
            repair_evidence["repair_reason"] = repair_evidence.get("repair_reason") or forced_repair_reason
            if repair_evidence.get("refined_segments"):
                repair_keys = [
                    owner.get("owner_endpoint")
                    for owner in repair_evidence.get("point_owners", {}).values()
                    if owner
                ]
                repair_valid = self._owner_keys_valid_for_source(repair_keys, source_segments)
                repair_evidence["owner_chain_repaired"] = repair_valid
                repair_evidence["owner_chain_valid"] = repair_valid

        effective_evidence = repair_evidence if repair_evidence else owner_evidence
        owner_keys = [
            owner.get("owner_endpoint")
            for owner in effective_evidence.get("point_owners", {}).values()
            if owner
        ]
        if (
            result.get("overlap_type") == 3
            and result.get("status") == "FINAL"
            and not self._owner_keys_valid_for_source(owner_keys, source_segments)
        ):
            return

        if repair_evidence:
            for refined in repair_evidence.get("refined_segments", []):
                refined_key = self._seg_key(refined)
                if refined_key in refined_seen:
                    continue
                refined_seen.add(refined_key)
                if collect_refined:
                    refined_segments.append(refined)
        repaired_owner_evidence = repair_evidence if (repair_evidence or {}).get("refined_segments") else None
        owner_cache = {
            "point_owners": (
                repaired_owner_evidence.get("point_owners")
                if repaired_owner_evidence
                else owner_evidence.get("point_owners", {})
            ),
            "owner_chain": (
                repaired_owner_evidence.get("owner_chain")
                if repaired_owner_evidence
                else owner_evidence.get("owner_chain", [])
            ),
            "owner_chain_valid": (
                repaired_owner_evidence.get("owner_chain_valid")
                if repaired_owner_evidence
                else owner_evidence.get("owner_chain_valid", False)
            ),
            "raw_point_owners": owner_evidence.get("point_owners", {}),
            "raw_owner_chain": owner_evidence.get("owner_chain", []),
            "raw_owner_chain_valid": owner_evidence.get("owner_chain_valid", False),
        }
        key = (
            seg_start_index(seg_slice[0]),
            seg_end_index(seg_slice[-1]),
            round(result["high"], 8),
            round(result["low"], 8),
            result["overlap_type"],
            result.get("center_kind", "trend_class"),
            result["status"],
        )
        if key in seen:
            return
        seen.add(key)
        endpoint_axis = self._endpoint_axis(endpoint_source_segments)
        endpoint_span = self._center_endpoint_span(seg_slice, endpoint_source_segments)
        centers.append(
            DailySegmentCenter(
                segments=list(seg_slice),
                high=result["high"],
                low=result["low"],
                overlap_type=result["overlap_type"],
                status=result["status"],
                points=result["points"],
                cache={
                    "identity_key": key,
                    "source": source,
                    "status_layer": status_layer,
                    "source_segments_kind": source_segments_kind,
                    "daily_segment_direction": daily_segment.direction.value,
                    "construction_direction": daily_segment.cache.get("construction_direction", daily_segment.direction.value),
                    "center_kind": result.get("center_kind", "trend_class"),
                    "generation_index": max(point[0] for point in result["points"].values()),
                    "third_entry_index": self._third_segment_entry_index(result, self.state.ma34),
                    "expanded_segments": [
                        self._seg_key(seg)
                        for seg in source_segments
                        if seg.cache.get("source_for_daily_center") == "expanded_from_swallow"
                    ],
                    **owner_cache,
                    "repair": repair_evidence,
                    "refined_segments": [
                        self._seg_key(seg)
                        for seg in (repair_evidence or {}).get("refined_segments", [])
                    ],
                    "endpoint_axis": endpoint_axis,
                    "endpoint_span": endpoint_span,
                    "repair_reason": (repair_evidence or {}).get("repair_reason", ""),
                    "excluded_reasons": ("exclude_from_daily_center",),
                },
            )
        )

    def _collect_centers_for_daily_segments(
        self,
        daily_segments: Sequence[DailySegment],
        source: str,
        status_layer: str,
        collect_refined: bool,
    ) -> tuple[List[DailySegmentCenter], List[MooreSegment], List[MooreSegment]]:
        centers: List[DailySegmentCenter] = []
        source_accumulator: List[MooreSegment] = []
        refined_segments: List[MooreSegment] = []
        seen = set()
        refined_seen = set()
        for daily_segment in daily_segments:
            source_segments = self._daily_segment_center_source_segments(daily_segment)
            source_accumulator.extend(source_segments)
            candidate_results = []
            for start in range(max(0, len(source_segments) - 3)):
                if source_segments[start].direction != daily_segment.direction:
                    continue
                result = find_center(source_segments[start:], self.state.ma34, trend_direction=daily_segment.direction)
                if not result:
                    continue
                candidate_results.append(result)
                self._record_center_result(
                    centers,
                    seen,
                    refined_seen,
                    refined_segments,
                    result,
                    source_segments,
                    daily_segment,
                    source,
                    status_layer,
                    collect_refined,
                    "expanded_continuous_30f",
                    endpoint_source_segments=source_segments,
                )

            for candidate_result in candidate_results:
                for proposal in self._build_owner_chain_repair_proposals(source_segments, candidate_result, daily_segment.direction):
                    self._record_center_result(
                        centers,
                        seen,
                        refined_seen,
                        refined_segments,
                        proposal.promoted_result,
                        proposal.source_segments,
                        daily_segment,
                        source,
                        status_layer,
                        collect_refined,
                        "owner_chain_repair",
                        endpoint_source_segments=source_segments,
                        forced_refined_segments=[proposal.refined_segment],
                    )

        active_centers = [c for c in centers if c.is_active]
        for c in centers:
            if any(
                other is not c
                and other.is_active
                and self._center_can_squeeze(c, other)
                for other in active_centers
            ):
                c.is_active = False
        selected_centers = self._dedupe_overlapping_daily_centers([c for c in centers if c.is_active])
        selected_refined_keys = {
            key
            for center in selected_centers
            for key in center.cache.get("refined_segments", [])
        }
        refined_segments = [
            seg
            for seg in refined_segments
            if self._seg_key(seg) in selected_refined_keys or seg.cache.get("repair_context") == "compact_diagnostic"
        ]
        return selected_centers, source_accumulator, refined_segments

    def _collect_construction_centers_for_daily_segment(
        self,
        daily_segment: DailySegment,
    ) -> tuple[List[DailySegmentCenter], List[MooreSegment], List[MooreSegment]]:
        return self._collect_centers_for_daily_segments(
            [daily_segment],
            source="daily_segment_construction",
            status_layer="COMPLETED",
            collect_refined=True,
        )

    def _publish_construction_time_centers(self):
        s = self.state
        centers: List[DailySegmentCenter] = []
        source_segments: List[MooreSegment] = []
        refined_segments: List[MooreSegment] = []
        refined_seen = set()

        for daily_segment in s.completed_segments:
            centers.extend(daily_segment.centers)
            source_segments.extend(daily_segment.cache.get("_construction_source_segments", []))
            for refined in daily_segment.cache.get("_construction_refined_segments", []):
                refined_key = self._seg_key(refined)
                if refined_key in refined_seen:
                    continue
                refined_seen.add(refined_key)
                refined_segments.append(refined)

        selected_centers = self._dedupe_overlapping_daily_centers([c for c in centers if c.is_active])
        selected_refined_keys = {
            key
            for center in selected_centers
            for key in center.cache.get("refined_segments", [])
        }
        s.daily_centers = selected_centers
        s.daily_center_source_segments = source_segments
        s.refined_segments = [
            seg
            for seg in refined_segments
            if self._seg_key(seg) in selected_refined_keys or seg.cache.get("repair_context") == "compact_diagnostic"
        ]

    def _rebuild_daily_centers(self):
        s = self.state
        if not s.rebuild_centers_after_segment_change:
            self._publish_construction_time_centers()
            self._rebuild_pending_centers()
            return
        s.daily_centers, s.daily_center_source_segments, s.refined_segments = self._collect_centers_for_daily_segments(
            s.completed_segments,
            source="daily_segment_internal",
            status_layer="COMPLETED",
            collect_refined=True,
        )
        self._rebuild_pending_centers()

    def _rebuild_pending_centers(self):
        s = self.state
        if not s.rebuild_centers_after_segment_change:
            s.pending_centers, _, s.pending_refined_segments = self._collect_centers_for_daily_segments(
                s.pending_display_segments,
                source="daily_segment_pending_construction",
                status_layer="PENDING",
                collect_refined=True,
            )
            return
        s.pending_centers, _, s.pending_refined_segments = self._collect_centers_for_daily_segments(
            s.pending_display_segments,
            source="daily_segment_pending_internal",
            status_layer="PENDING",
            collect_refined=True,
        )

    def _restore_from_anchor_snapshot(self):
        s = self.state
        s.completed_segments = clone_completed_segments_snapshot(s.anchor_completed_segments)
        s.current_segments = []
        s.pending_daily_segments = []
        s.pending_display_segments = []
        s.pending_centers = []
        s.active_center = None
        s.archived_centers = []
        s.candidates = []
        s.continuity_broken = False

    def _reset_runtime_state(self):
        s = self.state
        s.current_segments = []
        s.pending_daily_segments = []
        s.pending_display_segments = []
        s.pending_centers = []
        s.active_center = None
        s.archived_centers = []
        s.candidates = []

    def _process_new_segment(self, new_seg: MooreSegment):
        s = self.state

        if not s.current_segments and s.pending_anchor_snapshot:
            self._advance_anchor_snapshot(new_seg)

        s.current_segments.append(new_seg)

        if len(s.current_segments) == 1 and s.anchor_k_index is None:
            self._advance_anchor_snapshot(new_seg)

        self._try_spawn_new_candidate()
        self._update_candidates_and_resolve_conflicts()
        self._commit_ready_daily_segments()

    def _update_candidates_and_resolve_conflicts(self):
        s = self.state
        actives = [c for c in s.candidates if c.is_active]
        for c_a in actives:
            for c_b in actives:
                if c_a is c_b:
                    continue
                if self._center_can_squeeze(c_a, c_b):
                    c_a.is_active = False

        s.candidates = [c for c in s.candidates if c.is_active]
        type3 = [c for c in s.candidates if c.overlap_type == 3]
        if not type3:
            return

        type3.sort(key=lambda x: (x.start_index, x.end_index))
        chosen = type3[-1]
        if s.active_center and s.active_center is not chosen:
            if s.active_center not in s.archived_centers:
                s.archived_centers.append(s.active_center)
        s.active_center = chosen

    def _try_spawn_new_candidate(self):
        s = self.state
        if len(s.current_segments) < 4:
            return

        result = find_center(s.current_segments, s.ma34)
        if not result:
            return
        self._upsert_center_candidate(result)

    def _upsert_center_candidate(self, result: dict):
        s = self.state
        high = result["high"]
        low = result["low"]
        overlap_type = result["overlap_type"]
        status = result["status"]
        seg_slice = result["segments"]
        key = (
            seg_start_index(seg_slice[0]),
            seg_end_index(seg_slice[-1]),
            round(high, 8),
            round(low, 8),
            overlap_type,
            result.get("center_kind", "trend_class"),
        )

        for c in s.candidates:
            if c.cache.get("identity_key") == key:
                if c.status == "TEMPORARY" and status == "FINAL":
                    c.status = status
                    c.high = high
                    c.low = low
                    c.overlap_type = overlap_type
                    c.cache["center_kind"] = result.get("center_kind", "trend_class")
                    c.points = result["points"]
                    c.segments = list(seg_slice)
                return

        s.candidates.append(
            DailySegmentCenter(
                segments=list(seg_slice),
                high=high,
                low=low,
                overlap_type=overlap_type,
                status=status,
                points=result["points"],
                cache={"identity_key": key, "center_kind": result.get("center_kind", "trend_class")},
            )
        )

    def _append_completed_segment(self, segment: DailySegment):
        s = self.state
        if s.completed_segments:
            s.completed_segments[-1].cache["end_state"] = "frozen_end"
        segment.cache.setdefault("end_state", "extendable_end")
        s.completed_segments.append(segment)
        s.anchor_completed_segments = clone_completed_segments_snapshot(s.completed_segments)
        s.pending_after_tail_extension = False

    def _advance_anchor_snapshot(self, next_start_seg: MooreSegment):
        s = self.state
        s.anchor_k_index = next_start_seg.start_k.k_index
        s.anchor_dt = next_start_seg.start_k.dt
        s.anchor_completed_segments = clone_completed_segments_snapshot(s.completed_segments)
        s.pending_anchor_snapshot = False

    def _commit_segments_if_valid(self, segments: Sequence[MooreSegment]) -> bool:
        valid_segments = self._select_valid_daily_window(segments)
        if valid_segments:
            self._append_daily_segment(valid_segments)
            return True
        return False

    @staticmethod
    def _independence_cache(independence: Optional[IndependenceDecision]) -> dict:
        if independence is None:
            return {}
        cache = {
            "independence_kind": independence.kind,
            "independence_reason": independence.reason,
            "center_kind": independence.center_kind,
            "center_low": independence.center_low,
            "center_high": independence.center_high,
            "requires_new_extreme": independence.requires_new_extreme,
            "new_extreme_ok": independence.new_extreme_ok,
        }
        if independence.third_point_index is not None:
            cache["third_point_index"] = independence.third_point_index
            cache["third_point_price"] = independence.third_point_price
        if independence.new_extreme_index is not None:
            cache["new_extreme_index"] = independence.new_extreme_index
            cache["new_extreme_price"] = independence.new_extreme_price
        if independence.chain_confirmed_by is not None:
            cache["chain_confirmed_by"] = independence.chain_confirmed_by
            cache["chain_confirm_kind"] = independence.chain_confirm_kind
        return {k: v for k, v in cache.items() if v is not None and v != ""}

    def _append_daily_segment(
        self,
        segments: Sequence[MooreSegment],
        independence: Optional[IndependenceDecision] = None,
        candidate_kind: str = "",
    ):
        cache = {"from_macro_swallow": True} if len(segments) == 1 and segments[0].cache.get("is_macro_swallow") else {}
        cache.update(self._independence_cache(independence))
        if independence and independence.center_kind in {"trend_class", "turning"}:
            cache["end_state"] = "frozen_end"
        if candidate_kind:
            cache["candidate_kind"] = candidate_kind
        daily_segment = DailySegment(
            symbol=segments[0].symbol,
            direction=segments[0].direction,
            start_seg=segments[0],
            end_seg=segments[-1],
            segments=list(segments),
            cache=cache,
        )
        centers, source_segments, refined_segments = self._collect_construction_centers_for_daily_segment(daily_segment)
        daily_segment.centers = centers
        daily_segment.cache["_construction_source_segments"] = source_segments
        daily_segment.cache["_construction_refined_segments"] = refined_segments
        self._append_completed_segment(daily_segment)
        self.state.continuity_broken = False

    def _select_valid_daily_window(
        self,
        segments: Sequence[MooreSegment],
        previous_end_k=None,
    ) -> List[MooreSegment]:
        return select_valid_daily_window(
            segments,
            self.state.completed_segments,
            self.state.ma34,
            self.state.ma170,
            continuity_broken=self.state.continuity_broken,
            previous_end_k=previous_end_k,
        )

    def _is_valid_daily_window(
        self,
        window: Sequence[MooreSegment],
        next_seg: Optional[MooreSegment] = None,
        previous_end_k=None,
    ) -> bool:
        return is_valid_regular_window(
            window,
            self.state.ma34,
            self.state.ma170,
            lag_segment=next_seg,
            completed_segments=self.state.completed_segments,
            previous_end_k=previous_end_k,
        )

    def _commit_ready_daily_segments(self):
        s = self.state
        while True:
            decision = self._find_commit_decision(s.current_segments)
            if not decision:
                if self._extend_unfrozen_completed_tail_if_needed():
                    continue
                self._rebuild_pending_display_segments()
                return
            if not decision.segments:
                if self._extend_unfrozen_completed_tail_if_needed():
                    continue
                s.pending_daily_segments = decision.pending_segments
                self._rebuild_pending_display_segments()
                return

            tail = list(s.current_segments[decision.next_tail_offset:])
            self._append_daily_segment(decision.segments, decision.independence, decision.candidate_kind)
            for extra_segments, extra_independence in decision.extra_segments:
                self._append_daily_segment(extra_segments, extra_independence)
            s.current_segments = tail
            s.pending_daily_segments = []
            s.pending_display_segments = []
            s.pending_centers = []
            self._reset_daily_center_state()
            if s.current_segments:
                self._advance_anchor_snapshot(s.current_segments[0])
                self._rebuild_candidates_for_current_segments()
            else:
                s.anchor_k_index = None
                s.anchor_dt = None
                s.pending_anchor_snapshot = True

    @staticmethod
    def _strictly_extends_direction(direction: Direction, base_price: float, price: float) -> bool:
        if direction == Direction.Up:
            return price > base_price
        if direction == Direction.Down:
            return price < base_price
        return False

    def _tail_extension_offset(self, completed: DailySegment, segments: Sequence[MooreSegment]) -> Optional[int]:
        if not segments:
            return None
        if self._tk_key(segments[0].start_k) != self._tk_key(completed.end_seg.end_k):
            return None
        if segments[0].cache.get("is_macro_swallow"):
            return None

        best_offset = None
        best_price = completed.end_price
        for idx, seg in enumerate(segments):
            price = seg.end_k.price
            if not self._strictly_extends_direction(completed.direction, completed.end_price, price):
                continue
            if best_offset is None or self._strictly_extends_direction(completed.direction, best_price, price):
                best_offset = idx
                best_price = price
        return best_offset

    def _extend_unfrozen_completed_tail_if_needed(self) -> bool:
        s = self.state
        if not s.completed_segments or not s.current_segments:
            return False

        completed = s.completed_segments[-1]
        if completed.cache.get("end_state") != "extendable_end":
            return False
        reverse_decision = find_delayed_commit_decision(
            s.current_segments,
            s.completed_segments,
            s.ma34,
            s.ma170,
            continuity_broken=s.continuity_broken,
            allow_cold_start=False,
        )
        if reverse_decision and reverse_decision.segments and reverse_decision.independence:
            return False

        extension_offset = self._tail_extension_offset(completed, s.current_segments)
        if extension_offset is None:
            return False

        extension_segments = list(s.current_segments[: extension_offset + 1])
        extended_cache = dict(completed.cache)
        extended_cache.update(
            {
                "end_state": "extendable_end",
                "extended_from_unfrozen_end": True,
                "extension_reason": "unfrozen_boundary_continuation_new_extreme",
                "original_end_segment": self._seg_key(completed.end_seg),
                "extension_end_segment": self._seg_key(extension_segments[-1]),
            }
        )
        s.completed_segments[-1] = DailySegment(
            symbol=completed.symbol,
            direction=completed.direction,
            start_seg=completed.start_seg,
            end_seg=extension_segments[-1],
            segments=[*completed.segments, *extension_segments],
            centers=list(completed.centers),
            cache=extended_cache,
        )
        centers, source_segments, refined_segments = self._collect_construction_centers_for_daily_segment(s.completed_segments[-1])
        s.completed_segments[-1].centers = centers
        s.completed_segments[-1].cache["_construction_source_segments"] = source_segments
        s.completed_segments[-1].cache["_construction_refined_segments"] = refined_segments
        s.anchor_completed_segments = clone_completed_segments_snapshot(s.completed_segments)
        s.current_segments = list(s.current_segments[extension_offset + 1 :])
        s.pending_daily_segments = []
        s.pending_display_segments = []
        s.pending_centers = []
        s.pending_after_tail_extension = True
        self._reset_daily_center_state()
        if s.current_segments:
            self._advance_anchor_snapshot(s.current_segments[0])
            self._rebuild_candidates_for_current_segments()
        else:
            s.anchor_k_index = None
            s.anchor_dt = None
            s.pending_anchor_snapshot = True
        return True

    def _finalize_terminal_swallow_pending(self):
        s = self.state
        segments = list(s.current_segments)
        if not segments or not s.completed_segments:
            return
        first = segments[0]
        if not first.cache.get("is_macro_swallow"):
            return
        if not check_daily_segment_continuity([first], s.completed_segments):
            return
        expandable = select_valid_daily_window(
            segments,
            s.completed_segments,
            s.ma34,
            s.ma170,
            continuity_broken=s.continuity_broken,
        )
        if expandable and len(expandable) > 1:
            return
        self._append_daily_segment([first])
        s.current_segments = list(segments[1:])
        s.pending_daily_segments = []
        s.pending_display_segments = []
        s.pending_centers = []
        self._reset_daily_center_state()
        if s.current_segments:
            self._advance_anchor_snapshot(s.current_segments[0])
            self._rebuild_candidates_for_current_segments()
            self._commit_ready_daily_segments()
        else:
            s.anchor_k_index = None
            s.anchor_dt = None
            s.pending_anchor_snapshot = True

    def _tail_extension_independence_decision(
        self,
        segments: Sequence[MooreSegment],
    ) -> Optional[CommitDecision]:
        if not self.state.pending_after_tail_extension or not self.state.completed_segments:
            return None
        decision = find_delayed_commit_decision(
            segments,
            self.state.completed_segments,
            self.state.ma34,
            self.state.ma170,
            continuity_broken=self.state.continuity_broken,
            allow_cold_start=False,
        )
        if not decision or not decision.segments or not decision.independence:
            return None
        completed = self.state.completed_segments[-1]
        if not is_opposite_direction(decision.segments[0].direction, completed.direction):
            return None
        if decision.independence.kind == "no_daily_center":
            return decision
        return None

    def _rebuild_pending_display_segments(self):
        s = self.state
        s.pending_display_segments = []
        s.pending_centers = []
        segments = list(s.current_segments)
        if len(segments) < 3:
            return

        offset = 0
        previous_direction = s.completed_segments[-1].direction if s.completed_segments else None
        while offset < len(segments):
            candidates = candidates_from_start(
                segments,
                offset,
                s.ma34,
                s.ma170,
                completed_segments=s.completed_segments if offset == 0 else (),
                enforce_continuity=offset == 0,
                previous_direction=previous_direction,
                include_swallow_candidate=True,
                require_ma=False,
            )
            if not candidates:
                if offset == 0 and len(segments) >= 3:
                    s.pending_display_segments.append(
                        DailySegment(
                            symbol=segments[0].symbol,
                            direction=segments[0].direction,
                            start_seg=segments[0],
                            end_seg=segments[-1],
                            segments=list(segments),
                            cache={
                                "status": "PENDING",
                                "source": "daily_segment_open_tail",
                                "open_ended": True,
                                "source_end_segment": self._seg_key(segments[-1]),
                            },
                        )
                    )
                break
            selected = candidates[-1]
            if selected.end_offset <= offset:
                break
            open_ended = selected.kind == "regular" and offset == 0 and selected.end_offset < len(segments)
            display_segments = list(segments[offset:]) if open_ended else list(selected.segments)
            display_end_seg = selected.segments[-1]
            cache = {
                "status": "PENDING",
                "source": "daily_segment_cold_end",
                "candidate_kind": selected.kind,
                "construction_direction": selected.direction.value,
            }
            if open_ended:
                cache.update(
                    {
                        "open_ended": True,
                        "candidate_end_segment": self._seg_key(selected.segments[-1]),
                        "evidence_end_segment": self._seg_key(selected.segments[-1]),
                        "source_end_segment": self._seg_key(display_segments[-1]),
                    }
                )
            s.pending_display_segments.append(
                DailySegment(
                    symbol=selected.segments[0].symbol,
                    direction=selected.direction,
                    start_seg=selected.segments[0],
                    end_seg=display_end_seg,
                    segments=display_segments,
                    cache=cache,
                )
            )
            if open_ended:
                break
            previous_direction = selected.direction
            offset = selected.end_offset
        self._rebuild_pending_centers()

    def _find_commit_decision(self, segments: Sequence[MooreSegment]) -> Optional[CommitDecision]:
        if self.state.continuity_broken:
            return None
        if self.state.pending_after_tail_extension:
            return self._tail_extension_independence_decision(segments)
        if not self.state.completed_segments and len(segments) >= 3:
            return find_cold_start_decision(segments, self.state.ma34, self.state.ma170)
        if self.state.completed_segments and should_commit_leading_swallow(
            segments,
            self.state.completed_segments,
            self.state.ma34,
            self.state.ma170,
        ):
            return CommitDecision(0, 1, [segments[0]], [])
        if len(segments) < 3:
            return None
        return find_delayed_commit_decision(
            segments,
            self.state.completed_segments,
            self.state.ma34,
            self.state.ma170,
            continuity_broken=self.state.continuity_broken,
            allow_cold_start=False,
        )

    def _find_confirmed_daily_window(self, segments: Sequence[MooreSegment]) -> List[MooreSegment]:
        decision = self._find_commit_decision(segments)
        if not decision:
            self.state.pending_daily_segments = []
            return []
        self.state.pending_daily_segments = decision.pending_segments
        return decision.segments

    def _try_non_same_processing(self, segments: Sequence[MooreSegment]) -> List[MooreSegment]:
        """反向趋势以虚线 30F 线段开头时，尝试 idx-1 到 idx+2 直连。"""
        if len(segments) < 3 or not self.state.completed_segments:
            return []
        if not self._check_daily_segment_continuity(segments[:3]):
            return []
        return try_non_same_candidate(segments, self.state.completed_segments[-1].direction, self.state.ma34)

    def _ma34_reverses_against_window(self, window: Sequence[MooreSegment]) -> bool:
        return ma_reverses_against_window(window, self.state.ma34)

    @staticmethod
    def _is_extension_same_trend(window: Sequence[MooreSegment], next_two: Sequence[MooreSegment]) -> bool:
        return is_extension_same_trend(window, next_two)

    def _reset_daily_center_state(self):
        s = self.state
        s.active_center = None
        s.archived_centers = []
        s.candidates = []

    def _rebuild_candidates_for_current_segments(self):
        self.state.candidates = []
        for i in range(4, len(self.state.current_segments) + 1):
            result = find_center(self.state.current_segments[:i], self.state.ma34)
            if not result:
                continue
            self._upsert_center_candidate(result)
        self._update_candidates_and_resolve_conflicts()

    def _commit_current_running_epoch_if_needed(self) -> bool:
        s = self.state
        segments = list(s.current_segments)
        committed = False
        if segments:
            committed = self._commit_segments_if_valid(segments)
        self._reset_runtime_state()
        s.anchor_k_index = None
        s.anchor_dt = None
        s.pending_anchor_snapshot = True
        if segments and not committed and s.completed_segments:
            s.continuity_broken = True
        return committed

    def _commit_swallow_segment_directly(self, new_seg: MooreSegment):
        if not self._check_daily_segment_continuity([new_seg]):
            if self.state.completed_segments:
                self.state.continuity_broken = True
            self._reset_runtime_state()
            return
        direct_seg = DailySegment(
            symbol=new_seg.symbol,
            direction=new_seg.direction,
            start_seg=new_seg,
            end_seg=new_seg,
            segments=[new_seg],
            centers=[],
            cache={"from_macro_swallow": True},
        )
        self._append_completed_segment(direct_seg)
        self.state.continuity_broken = False
        self._reset_runtime_state()
        self.state.anchor_k_index = None
        self.state.anchor_dt = None
        self.state.pending_anchor_snapshot = True

    def _check_daily_segment_continuity(self, segments: Sequence[MooreSegment], previous_end_k=None) -> bool:
        return check_daily_segment_continuity(
            segments,
            self.state.completed_segments,
            previous_end_k=previous_end_k,
        )

    def _check_global_trend_relationship(self, segments: Sequence[MooreSegment]) -> bool:
        return check_global_trend_relationship(segments)

    def _check_ma_cross_correlation(
        self,
        segments: Sequence[MooreSegment],
        ma_fast,
        ma_slow,
        lag_segment: Optional[MooreSegment] = None,
    ) -> bool:
        return check_ma_cross_correlation(segments, ma_fast, ma_slow, lag_segment)

    @classmethod
    def _has_ma_cross_between(cls, start_idx: int, end_idx: int, ma_fast, ma_slow) -> bool:
        return has_ma_cross_between(start_idx, end_idx, ma_fast, ma_slow)

    @staticmethod
    def _turning_index(tk) -> int:
        return turning_index(tk)

    @classmethod
    def _ma_relation_state_for_tk(cls, tk, ma_fast, ma_slow) -> int:
        return ma_relation_state_for_tk(tk, ma_fast, ma_slow)

    @staticmethod
    def _ma_relation_state_at(idx: int, ma_fast, ma_slow) -> int:
        return ma_relation_state_at(idx, ma_fast, ma_slow)

    @staticmethod
    def _ma_relation_state_from_values(fast, slow) -> int:
        return ma_relation_state_from_values(fast, slow)


# 兼容旧命名
HigherAnalyzer = DailySegmentAnalyzer
HigherCenter = DailySegmentCenter
HigherSegment = DailySegment
HigherState = DailySegmentState
