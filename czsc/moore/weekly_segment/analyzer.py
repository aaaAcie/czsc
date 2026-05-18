# -*- coding: utf-8 -*-
"""周线级别线段分析器。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence

from czsc.py.enum import Direction

from ..daily_segment.centers.algo import find_a_point, find_b_point, find_center
from ..daily_segment.helpers.trend import check_global_trend_relationship
from ..daily_segment.objects import DailySegment, DailySegmentCenter
from ..objects import MooreSegment
from .objects import WeeklySegment, WeeklySegmentCenter
from .state import WeeklySegmentState


@dataclass
class _DailySegmentAdapter:
    """把 DailySegment 暂时投影成 center 算法可消费的线段形状。"""

    source: DailySegment

    @property
    def symbol(self):
        return self.source.symbol

    @property
    def start_k(self):
        return self.source.start_seg.start_k

    @property
    def end_k(self):
        return self.source.end_seg.end_k

    @property
    def direction(self):
        return self.source.direction

    @property
    def bars(self):
        bars = []
        for seg in self.source.segments:
            bars.extend(seg.bars)
        return bars

    @property
    def cache(self):
        return self.source.cache


def _daily_start_key(segment: DailySegment) -> tuple:
    tk = segment.start_seg.start_k
    return (tk.k_index, tk.dt, tk.price, tk.mark.value)


def _daily_end_key(segment: DailySegment) -> tuple:
    tk = segment.end_seg.end_k
    return (tk.k_index, tk.dt, tk.price, tk.mark.value)


def _center_start_index(center: DailySegmentCenter) -> int:
    return center.start_index


def _center_end_index(center: DailySegmentCenter) -> int:
    return center.end_index


class WeeklySegmentAnalyzer:
    """消费日线层最终产物，构造周线级别线段与中枢。"""

    def __init__(
        self,
        daily_segments: Optional[Sequence[DailySegment]] = None,
        daily_centers: Optional[Sequence[DailySegmentCenter]] = None,
        daily_refined_segments: Optional[Sequence[MooreSegment]] = None,
        ma170: Optional[Sequence[Optional[float]]] = None,
    ):
        self.state = WeeklySegmentState()
        if daily_segments is not None:
            self.update(
                daily_segments,
                daily_centers=daily_centers,
                daily_refined_segments=daily_refined_segments,
                ma170=ma170,
            )

    def update(
        self,
        daily_segments: Sequence[DailySegment],
        daily_centers: Optional[Sequence[DailySegmentCenter]] = None,
        daily_refined_segments: Optional[Sequence[MooreSegment]] = None,
        ma170: Optional[Sequence[Optional[float]]] = None,
    ):
        sig = (
            tuple((_daily_start_key(seg), _daily_end_key(seg), seg.direction.value, seg.cache.get("candidate_kind", "")) for seg in daily_segments),
            tuple((c.start_index, c.end_index, round(c.low, 8), round(c.high, 8), c.overlap_type, c.cache.get("identity_key")) for c in (daily_centers or ())),
            tuple((seg.start_k.k_index, seg.end_k.k_index, seg.cache.get("repair_reason", "")) for seg in (daily_refined_segments or ())),
        )
        if sig == self.state.last_sig and ma170 is None:
            return
        self.state.last_sig = sig
        self.state.daily_segments = list(daily_segments)
        self.state.daily_centers = list(daily_centers or [])
        self.state.daily_refined_segments = list(daily_refined_segments or [])
        if ma170 is not None:
            self.state.ma170 = list(ma170)
        self._rebuild()

    @property
    def weekly_segments(self) -> List[WeeklySegment]:
        return self.state.completed_segments

    @property
    def weekly_pending_segments(self) -> List[WeeklySegment]:
        return self.state.pending_segments

    @property
    def weekly_non_same_segments(self) -> List[WeeklySegment]:
        return self.state.non_same_segments

    @property
    def weekly_centers(self) -> List[WeeklySegmentCenter]:
        return self.state.weekly_centers

    @property
    def weekly_pending_centers(self) -> List[WeeklySegmentCenter]:
        return self.state.pending_centers

    @property
    def weekly_center_source_segments(self) -> List[DailySegment]:
        return self.state.weekly_center_source_segments

    def _rebuild(self):
        s = self.state
        s.completed_segments = []
        s.pending_segments = []
        s.non_same_segments = []
        s.weekly_centers = []
        s.pending_centers = []
        s.weekly_center_source_segments = []

        offset = 0
        daily_segments = s.daily_segments
        while offset < len(daily_segments):
            candidate = self._select_regular_window(offset)
            if candidate:
                weekly_segment = self._make_weekly_segment(candidate, candidate_kind="regular")
                s.completed_segments.append(weekly_segment)
                s.weekly_centers.extend(weekly_segment.centers)
                s.weekly_center_source_segments.extend(candidate)
                offset += len(candidate)
                continue

            non_same = self._select_non_same_window(offset)
            if non_same:
                weekly_segment = self._make_non_same_weekly_segment(non_same)
                s.completed_segments.append(weekly_segment)
                s.non_same_segments.append(weekly_segment)
                s.weekly_centers.extend(weekly_segment.centers)
                s.weekly_center_source_segments.extend(weekly_segment.segments)
                offset = self._offset_after_segment(weekly_segment.end_seg, offset + 1)
                continue

            if len(daily_segments) - offset >= 3:
                offset += 1
                continue

            if len(daily_segments) - offset >= 3:
                pending = self._make_weekly_segment(daily_segments[offset:], candidate_kind="pending")
                pending.cache["status"] = "PENDING"
                s.pending_segments.append(pending)
                s.pending_centers.extend(pending.centers)
            break

    def _select_regular_window(self, start_offset: int) -> List[DailySegment]:
        segments = self.state.daily_segments
        max_len = len(segments) - start_offset
        if max_len < 3:
            return []
        max_len = max_len if max_len % 2 == 1 else max_len - 1
        for window_len in range(3, max_len + 1, 2):
            window = list(segments[start_offset:start_offset + window_len])
            if self._is_valid_regular_window(window):
                return window
        return []

    def _is_valid_regular_window(self, window: Sequence[DailySegment]) -> bool:
        if len(window) < 3:
            return False
        if not self._is_continuous(window):
            return False
        adapters = [_DailySegmentAdapter(seg) for seg in window]
        return check_global_trend_relationship(adapters)

    @staticmethod
    def _is_continuous(window: Sequence[DailySegment]) -> bool:
        for left, right in zip(window, window[1:]):
            if _daily_end_key(left) != _daily_start_key(right):
                return False
        return True

    def _make_weekly_segment(self, segments: Sequence[DailySegment], candidate_kind: str) -> WeeklySegment:
        segment_list = list(segments)
        direction = segment_list[0].direction if segment_list else Direction.Up
        weekly_segment = WeeklySegment(
            symbol=segment_list[0].symbol if segment_list else "",
            direction=direction,
            start_seg=segment_list[0],
            end_seg=segment_list[-1],
            segments=segment_list,
            cache={"candidate_kind": candidate_kind},
        )
        if candidate_kind in {"regular", "pending"}:
            center = self._build_regular_center(segment_list)
            if center is not None:
                if candidate_kind == "pending":
                    center.status = "PENDING"
                    center.cache["source"] = "weekly_segment_pending"
                    center.cache["status_layer"] = "PENDING"
                weekly_segment.centers.append(center)
        return weekly_segment

    def _build_regular_center(self, segments: Sequence[DailySegment]) -> Optional[WeeklySegmentCenter]:
        adapters = [_DailySegmentAdapter(seg) for seg in segments]
        result = find_center(adapters, self.state.ma170, trend_direction=segments[0].direction)
        if result is None and len(adapters) >= 3:
            result = self._build_three_segment_ba_center(adapters, segments[0].direction)
        if result is None:
            return None
        center_segments = [adapter.source for adapter in result.get("segments", adapters)]
        return WeeklySegmentCenter(
            segments=center_segments,
            high=result["high"],
            low=result["low"],
            overlap_type=result["overlap_type"],
            status=result.get("status", "FINAL"),
            points=result.get("points", {}),
            cache={
                "source": "weekly_segment_regular",
                "center_kind": result.get("center_kind", "trend_class"),
                "source_algorithm": result.get("source_algorithm", "daily_badc_on_ma170"),
            },
        )

    def _build_three_segment_ba_center(
        self,
        adapters: Sequence[_DailySegmentAdapter],
        trend_direction: Direction,
    ) -> Optional[dict]:
        if len(adapters) < 3:
            return None
        sign = 1 if trend_direction == Direction.Down else -1
        b_idx, b_val = find_b_point(adapters[1], adapters[2], self.state.ma170, sign)
        if b_val is None or b_idx is None:
            return None
        a_idx, a_val = find_a_point(adapters[0], b_idx, self.state.ma170, sign)
        if a_val is None or a_idx is None:
            return None
        return {
            "high": max(a_val, b_val),
            "low": min(a_val, b_val),
            "overlap_type": 1,
            "center_kind": "trend_class",
            "status": "FINAL",
            "segments": list(adapters[:3]),
            "points": {"A": (a_idx, a_val), "B": (b_idx, b_val)},
            "source_algorithm": "weekly_three_segment_ba_on_ma170",
        }

    def _select_non_same_window(self, start_offset: int) -> List[DailySegmentCenter]:
        if self._select_regular_window(start_offset):
            return []
        if start_offset >= len(self.state.daily_segments):
            return []
        for window_len in (1, 2):
            window = list(self.state.daily_segments[start_offset:start_offset + window_len])
            if len(window) != window_len:
                continue
            if len(window) > 1 and not self._is_continuous(window):
                continue
            centers = self._trend_centers_inside_daily_window(window)
            selected = self._dedupe_centers(centers)
            if len(selected) >= 3:
                return selected[:3]
        return []

    def _trend_centers_inside_daily_window(self, window: Sequence[DailySegment]) -> List[DailySegmentCenter]:
        left = window[0].start_seg.start_k.k_index
        right = window[-1].end_seg.end_k.k_index
        centers = [
            center
            for center in self.state.daily_centers
            if center.overlap_type in {1, 3}
            and center.cache.get("center_kind", "trend_class") == "trend_class"
            and center.status == "FINAL"
            and _center_start_index(center) >= left
            and _center_end_index(center) <= right
        ]
        centers.sort(key=lambda center: (_center_start_index(center), _center_end_index(center), center.low, center.high))
        return centers

    @staticmethod
    def _dedupe_centers(centers: Sequence[DailySegmentCenter]) -> List[DailySegmentCenter]:
        seen = set()
        selected: List[DailySegmentCenter] = []
        for center in centers:
            key = center.cache.get("identity_key") or (
                center.start_index,
                center.end_index,
                round(center.low, 8),
                round(center.high, 8),
                center.overlap_type,
            )
            if key in seen:
                continue
            seen.add(key)
            selected.append(center)
        return selected

    def _make_non_same_weekly_segment(self, source_centers: Sequence[DailySegmentCenter]) -> WeeklySegment:
        source_daily_segments = self._daily_segments_covering_centers(source_centers)
        if not source_daily_segments:
            source_daily_segments = self.state.daily_segments[:1]
        first_center = source_centers[0]
        direction = source_daily_segments[0].direction
        weekly_center = WeeklySegmentCenter(
            segments=source_daily_segments,
            high=first_center.high,
            low=first_center.low,
            overlap_type=first_center.overlap_type,
            status="FINAL",
            points=dict(first_center.points),
            cache={
                "source": "weekly_segment_non_same",
                "center_kind": "trend_class",
                "candidate_kind": "non_same",
                "source_daily_center_identity": first_center.cache.get("identity_key"),
                "source_daily_centers": [center.cache.get("identity_key") for center in source_centers],
                "source_rails": "first_daily_trend_center",
            },
        )
        return WeeklySegment(
            symbol=source_daily_segments[0].symbol,
            direction=direction,
            start_seg=source_daily_segments[0],
            end_seg=source_daily_segments[-1],
            segments=source_daily_segments,
            centers=[weekly_center],
            cache={
                "candidate_kind": "non_same",
                "source_daily_centers": [center.cache.get("identity_key") for center in source_centers],
            },
        )

    def _daily_segments_covering_centers(self, centers: Sequence[DailySegmentCenter]) -> List[DailySegment]:
        if not centers:
            return []
        left = min(center.start_index for center in centers)
        right = max(center.end_index for center in centers)
        return [
            segment
            for segment in self.state.daily_segments
            if segment.confirm_end_index >= left and segment.confirm_start_index <= right
        ]

    def _offset_after_segment(self, segment: DailySegment, fallback: int) -> int:
        for idx, item in enumerate(self.state.daily_segments):
            if item is segment or (_daily_start_key(item), _daily_end_key(item)) == (_daily_start_key(segment), _daily_end_key(segment)):
                return idx + 1
        return fallback
