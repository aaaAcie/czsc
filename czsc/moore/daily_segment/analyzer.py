# -*- coding: utf-8 -*-
"""日线级别线段分析器。"""
from __future__ import annotations

from typing import List, Optional, Sequence

from czsc.py.enum import Direction
from czsc.py.objects import RawBar

from ..objects import MooreSegment
from .center_algo import find_center
from .objects import DailySegment, DailySegmentCenter
from .state import DailySegmentState
from .utils import (
    build_sma_array,
    clone_completed_segments_snapshot,
    collect_bars_by_index,
    seg_end_index,
    seg_end_price,
    seg_start_index,
    seg_start_price,
    slice_segments_from_anchor,
)


class DailySegmentAnalyzer:
    """消费 30F 宏观线段构造日线级别线段与中枢。"""

    def __init__(self, segments: Optional[Sequence[MooreSegment]] = None, bars: Optional[Sequence[RawBar]] = None):
        self.state = DailySegmentState()
        if bars:
            self.state.bars_raw = list(bars)
        if segments:
            self.update(list(segments))

    def update(self, segments: Sequence[MooreSegment], bars: Optional[Sequence[RawBar]] = None):
        s = self.state
        sig = tuple(
            (
                seg.start_k.k_index,
                seg.end_k.k_index,
                self._turning_index(seg.start_k),
                self._turning_index(seg.end_k),
                bool(seg.cache.get("is_macro_swallow")),
            )
            for seg in segments
        )
        if bars is not None:
            s.bars_raw = list(bars)
        if sig == s.last_sig and bars is None:
            return
        s.last_sig = sig
        s.base_segments = list(segments)
        self._rebuild()

    @property
    def daily_segments(self) -> List[DailySegment]:
        return self.state.completed_segments

    @property
    def active_center(self) -> Optional[DailySegmentCenter]:
        return self.state.active_center

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
            return

        self._restore_from_anchor_snapshot()
        for seg in danger:
            self._process_new_segment(seg)

    def _full_rebuild_from_scratch(self):
        s = self.state
        s.current_segments = []
        s.pending_daily_segments = []
        s.completed_segments = []
        s.active_center = None
        s.archived_centers = []
        s.candidates = []
        s.anchor_k_index = None
        s.anchor_dt = None
        s.anchor_completed_segments = []
        s.pending_anchor_snapshot = False
        s.continuity_broken = False

        for seg in s.base_segments:
            self._process_new_segment(seg)

    def _restore_from_anchor_snapshot(self):
        s = self.state
        s.completed_segments = clone_completed_segments_snapshot(s.anchor_completed_segments)
        s.current_segments = []
        s.pending_daily_segments = []
        s.active_center = None
        s.archived_centers = []
        s.candidates = []
        s.continuity_broken = False

    def _reset_runtime_state(self):
        s = self.state
        s.current_segments = []
        s.pending_daily_segments = []
        s.active_center = None
        s.archived_centers = []
        s.candidates = []

    def _process_new_segment(self, new_seg: MooreSegment):
        s = self.state

        if new_seg.cache.get("is_macro_swallow"):
            if self._check_daily_segment_continuity([new_seg]):
                self._reset_runtime_state()
                self._commit_swallow_segment_directly(new_seg)
                return

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
                if c_b.overlap_type == 3 and c_a.overlap_type == 1 and c_a.check_segment_overlap(c_b):
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
        )

        for c in s.candidates:
            if c.cache.get("identity_key") == key:
                if c.status == "TEMPORARY" and status == "FINAL":
                    c.status = status
                    c.high = high
                    c.low = low
                    c.overlap_type = overlap_type
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
                cache={"identity_key": key},
            )
        )

    def _append_completed_segment(self, segment: DailySegment):
        s = self.state
        s.completed_segments.append(segment)
        s.anchor_completed_segments = clone_completed_segments_snapshot(s.completed_segments)

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

    def _append_daily_segment(self, segments: Sequence[MooreSegment]):
        centers = [
            c
            for c in self.state.candidates
            if c.is_active
            and c.start_index >= seg_start_index(segments[0])
            and c.end_index <= seg_end_index(segments[-1])
        ]
        self._append_completed_segment(
            DailySegment(
                symbol=segments[0].symbol,
                direction=segments[0].direction,
                start_seg=segments[0],
                end_seg=segments[-1],
                segments=list(segments),
                centers=centers,
            )
        )
        self.state.continuity_broken = False

    def _select_valid_daily_window(
        self,
        segments: Sequence[MooreSegment],
        previous_end_k=None,
    ) -> List[MooreSegment]:
        if self.state.continuity_broken or len(segments) < 3:
            return []

        chosen: List[MooreSegment] = []
        max_len = len(segments) if len(segments) % 2 == 1 else len(segments) - 1
        for window_len in range(3, max_len + 1, 2):
            window = list(segments[:window_len])
            next_seg = segments[window_len] if window_len < len(segments) else None
            if self._is_valid_daily_window(window, next_seg=next_seg, previous_end_k=previous_end_k):
                chosen = window
        return chosen

    def _is_valid_daily_window(
        self,
        window: Sequence[MooreSegment],
        next_seg: Optional[MooreSegment] = None,
        previous_end_k=None,
    ) -> bool:
        return (
            self._check_daily_segment_continuity(window, previous_end_k=previous_end_k)
            and self._check_global_trend_relationship(window)
            and self._check_ma_cross_correlation(window, self.state.ma34, self.state.ma170, next_seg)
        )

    def _commit_ready_daily_segments(self):
        s = self.state
        while True:
            commit_segments = self._find_confirmed_daily_window(s.current_segments)
            if not commit_segments:
                return

            tail = list(s.current_segments[len(commit_segments):])
            self._append_daily_segment(commit_segments)
            s.current_segments = tail
            s.pending_daily_segments = []
            self._reset_daily_center_state()
            if s.current_segments:
                self._advance_anchor_snapshot(s.current_segments[0])
                self._rebuild_candidates_for_current_segments()
            else:
                s.anchor_k_index = None
                s.anchor_dt = None
                s.pending_anchor_snapshot = True

    def _find_confirmed_daily_window(self, segments: Sequence[MooreSegment]) -> List[MooreSegment]:
        self.state.pending_daily_segments = []
        if self.state.continuity_broken or len(segments) < 3:
            return []

        non_same_window = self._try_non_same_processing(segments)
        if non_same_window:
            return non_same_window

        max_len = len(segments) if len(segments) % 2 == 1 else len(segments) - 1
        for window_len in range(3, max_len + 1, 2):
            window = list(segments[:window_len])
            next_seg = segments[window_len] if window_len < len(segments) else None
            if not self._is_valid_daily_window(window, next_seg=next_seg):
                continue

            self.state.pending_daily_segments = window
            next_two = list(segments[window_len : window_len + 2])
            if len(next_two) < 2:
                return []

            if not self._is_extension_same_trend(window, next_two):
                return window
        return []

    def _try_non_same_processing(self, segments: Sequence[MooreSegment]) -> List[MooreSegment]:
        """反向趋势以虚线 30F 线段开头时，尝试 idx-1 到 idx+2 直连。"""
        if len(segments) < 3 or not self.state.completed_segments:
            return []

        first = segments[0]
        prev_daily = self.state.completed_segments[-1]
        if first.direction == prev_daily.direction:
            return []
        if first.is_perfect:
            return []

        window = list(segments[:3])
        if not self._check_daily_segment_continuity(window):
            return []
        if not self._ma34_reverses_against_window(window):
            return []
        return window

    def _ma34_reverses_against_window(self, window: Sequence[MooreSegment]) -> bool:
        if not window or not self.state.ma34:
            return False

        start_price = seg_start_price(window[0])
        end_price = seg_end_price(window[-1])
        if end_price == start_price:
            return False

        start_idx = self._turning_index(window[0].start_k)
        end_idx = self._turning_index(window[-1].end_k)
        if start_idx > end_idx:
            start_idx, end_idx = end_idx, start_idx

        vals = [
            self.state.ma34[idx]
            for idx in range(start_idx, end_idx + 1)
            if 0 <= idx < len(self.state.ma34) and self.state.ma34[idx] is not None
        ]
        if len(vals) < 2:
            return False

        if end_price > start_price:
            return any(curr < prev for prev, curr in zip(vals, vals[1:]))
        return any(curr > prev for prev, curr in zip(vals, vals[1:]))

    @staticmethod
    def _is_extension_same_trend(window: Sequence[MooreSegment], next_two: Sequence[MooreSegment]) -> bool:
        if not window or len(next_two) < 2:
            return False
        direction = window[0].direction
        current_end = seg_end_price(window[-1])
        extended_end = seg_end_price(next_two[-1])
        if direction == Direction.Up:
            return extended_end >= current_end
        if direction == Direction.Down:
            return extended_end <= current_end
        return False

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
        if not segments:
            return False
        if previous_end_k is not None:
            curr_start = segments[0].start_k
            return previous_end_k.k_index == curr_start.k_index and previous_end_k.dt == curr_start.dt
        if not self.state.completed_segments:
            return True
        prev_daily = self.state.completed_segments[-1]
        if prev_daily.cache.get("from_macro_swallow") and prev_daily.segments[0] is segments[0]:
            return True
        prev_end = self.state.completed_segments[-1].end_seg.end_k
        curr_start = segments[0].start_k
        return prev_end.k_index == curr_start.k_index and prev_end.dt == curr_start.dt

    def _check_global_trend_relationship(self, segments: Sequence[MooreSegment]) -> bool:
        if not segments:
            return False
        daily_dir = segments[0].direction
        start_p = seg_start_price(segments[0])
        end_p = seg_end_price(segments[-1])
        endpoints = []
        for seg in segments:
            endpoints.extend([seg_start_price(seg), seg_end_price(seg)])
        global_max = max(endpoints)
        global_min = min(endpoints)
        if daily_dir == Direction.Up:
            return start_p == global_min and endpoints.count(global_min) == 1 and end_p >= global_max
        if daily_dir == Direction.Down:
            return start_p == global_max and endpoints.count(global_max) == 1 and end_p <= global_min
        return False

    def _check_ma_cross_correlation(
        self,
        segments: Sequence[MooreSegment],
        ma_fast,
        ma_slow,
        lag_segment: Optional[MooreSegment] = None,
    ) -> bool:
        if not segments:
            return False

        start_idx = self._turning_index(segments[0].start_k)
        end_tk = lag_segment.end_k if lag_segment is not None else segments[-1].end_k
        end_idx = self._turning_index(end_tk)
        return self._has_ma_cross_between(start_idx, end_idx, ma_fast, ma_slow)

    @classmethod
    def _has_ma_cross_between(cls, start_idx: int, end_idx: int, ma_fast, ma_slow) -> bool:
        if start_idx > end_idx:
            start_idx, end_idx = end_idx, start_idx
        prev_state = 0
        for idx in range(start_idx, end_idx + 1):
            state = cls._ma_relation_state_at(idx, ma_fast, ma_slow)
            if state == 0:
                continue
            if prev_state != 0 and state != prev_state:
                return True
            prev_state = state
        return False

    @staticmethod
    def _turning_index(tk) -> int:
        return tk.turning_k_index if tk.turning_k_index is not None else tk.k_index

    @classmethod
    def _ma_relation_state_for_tk(cls, tk, ma_fast, ma_slow) -> int:
        for bar in (getattr(tk, "turning_k", None), getattr(tk, "trigger_k", None), getattr(tk, "raw_bar", None)):
            if bar is None:
                continue
            fast = bar.cache.get("ma34")
            slow = bar.cache.get("ma170")
            state = cls._ma_relation_state_from_values(fast, slow)
            if state != 0:
                return state
        return cls._ma_relation_state_at(cls._turning_index(tk), ma_fast, ma_slow)

    @staticmethod
    def _ma_relation_state_at(idx: int, ma_fast, ma_slow) -> int:
        if idx < 0 or idx >= len(ma_fast) or idx >= len(ma_slow):
            return 0
        fast = ma_fast[idx]
        slow = ma_slow[idx]
        return DailySegmentAnalyzer._ma_relation_state_from_values(fast, slow)

    @staticmethod
    def _ma_relation_state_from_values(fast, slow) -> int:
        if fast is None or slow is None:
            return 0
        if fast > slow:
            return 1
        if fast < slow:
            return -1
        return 0


# 兼容旧命名
HigherAnalyzer = DailySegmentAnalyzer
HigherCenter = DailySegmentCenter
HigherSegment = DailySegment
HigherState = DailySegmentState
