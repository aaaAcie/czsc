# -*- coding: utf-8 -*-
from datetime import datetime, timedelta

import pytest

from czsc.connectors import research
from czsc.moore.analyze import MooreCZSC
from czsc.moore.daily_segment import DailySegment, DailySegmentAnalyzer, DailySegmentCenter
from czsc.moore.daily_segment.center_algo import find_b_point, find_center, find_d_point
from czsc.moore.daily_segment.utils import slice_segments_from_anchor
from czsc.moore.objects import MooreSegment, TurningK
from czsc.py.enum import Direction, Freq, Mark
from czsc.py.objects import RawBar


def make_bar(idx: int, close_: float) -> RawBar:
    base_dt = datetime(2023, 1, 1, 9, 30)
    return RawBar(
        symbol="TEST.DAILY",
        id=idx,
        dt=base_dt + timedelta(minutes=30 * idx),
        freq=Freq.F30,
        open=close_,
        close=close_,
        high=close_,
        low=close_,
        vol=100,
        amount=1000,
    )


def make_seg(
    start_idx: int,
    end_idx: int,
    direction: Direction,
    start_price: float,
    end_price: float,
    start_turning_idx: int | None = None,
    end_turning_idx: int | None = None,
) -> MooreSegment:
    start_bar = make_bar(start_idx, start_price)
    end_bar = make_bar(end_idx, end_price)
    start_turning_bar = make_bar(start_turning_idx, start_price) if start_turning_idx is not None else start_bar
    end_turning_bar = make_bar(end_turning_idx, end_price) if end_turning_idx is not None else end_bar
    start_mark = Mark.D if direction == Direction.Up else Mark.G
    end_mark = Mark.G if direction == Direction.Up else Mark.D
    start_tk = TurningK(
        symbol="TEST.DAILY",
        dt=start_bar.dt,
        raw_bar=start_bar,
        mark=start_mark,
        price=start_price,
        k_index=start_idx,
        trigger_k=start_turning_bar,
        trigger_k_index=start_turning_idx if start_turning_idx is not None else start_idx,
    )
    end_tk = TurningK(
        symbol="TEST.DAILY",
        dt=end_bar.dt,
        raw_bar=end_bar,
        mark=end_mark,
        price=end_price,
        k_index=end_idx,
        trigger_k=end_turning_bar,
        trigger_k_index=end_turning_idx if end_turning_idx is not None else end_idx,
    )
    bars = [make_bar(i, start_price + (end_price - start_price) * (i - start_idx) / max(end_idx - start_idx, 1)) for i in range(start_idx, end_idx + 1)]
    return MooreSegment(
        symbol="TEST.DAILY",
        start_k=start_tk,
        end_k=end_tk,
        direction=direction,
        bars=bars,
    )


def make_ma_arrays(states: dict[int, int], size: int = 240) -> tuple[list[float | None], list[float | None]]:
    ma34 = [None] * size
    ma170 = [None] * size
    for idx, state in states.items():
        ma170[idx] = 10
        ma34[idx] = 11 if state > 0 else 9 if state < 0 else 10
    return ma34, ma170


def make_analyzer_with_ma(states: dict[int, int], size: int = 240) -> DailySegmentAnalyzer:
    analyzer = DailySegmentAnalyzer()
    analyzer.state.ma34, analyzer.state.ma170 = make_ma_arrays(states, size=size)
    return analyzer


def set_ma34(analyzer: DailySegmentAnalyzer, values: dict[int, float], size: int = 240):
    analyzer.state.ma34 = [None] * size
    for idx, value in values.items():
        analyzer.state.ma34[idx] = value


def test_slice_segments_from_anchor_dual_key():
    seg1 = make_seg(0, 2, Direction.Up, 10, 12)
    seg2 = make_seg(3, 5, Direction.Down, 12, 11)
    seg3 = make_seg(6, 8, Direction.Up, 11, 13)

    sliced, fallback = slice_segments_from_anchor([seg1, seg2, seg3], seg2.start_k.k_index, seg2.start_k.dt)
    assert fallback is False
    assert sliced == [seg2, seg3]

    sliced_bad, fallback_bad = slice_segments_from_anchor(
        [seg1, seg2, seg3],
        seg2.start_k.k_index,
        seg1.start_k.dt,
    )
    assert sliced_bad is None
    assert fallback_bad is True


def test_find_b_and_d_respect_structure_boundaries():
    seg_23 = make_seg(10, 12, Direction.Up, 10, 12)
    seg_34 = make_seg(13, 15, Direction.Down, 12, 9)
    seg_56 = make_seg(19, 21, Direction.Down, 11, 8)

    ma34 = [None] * 30
    # B 点只能在 seg_34 的右边界内找到；15 后面再高也不能被命中
    ma34[12] = 1
    ma34[13] = 2
    ma34[14] = 5
    ma34[15] = 3
    ma34[16] = 9
    b_idx, b_val = find_b_point(seg_23, seg_34, ma34, sign=1)
    assert b_idx == 14
    assert b_val == 5

    ma34[17] = 1
    ma34[18] = 2
    ma34[19] = 4
    ma34[20] = 6
    ma34[21] = 5
    ma34[22] = 9
    d_idx, d_val = find_d_point(18, seg_56.end_k.k_index, ma34, sign=1)
    assert d_idx == 20
    assert d_val == 6


def test_find_b_chooses_strongest_local_extreme_in_turning_window():
    seg_23 = make_seg(10, 20, Direction.Up, 10, 12, start_turning_idx=11, end_turning_idx=20)
    seg_34 = make_seg(21, 30, Direction.Down, 12, 9, start_turning_idx=21, end_turning_idx=28)

    ma34 = [None] * 40
    ma34[13] = 3
    ma34[14] = 7
    ma34[15] = 4
    ma34[23] = 5
    ma34[24] = 11
    ma34[25] = 6
    ma34[29] = 30

    b_idx, b_val = find_b_point(seg_23, seg_34, ma34, sign=1)
    assert b_idx == 24
    assert b_val == 11


def test_center_algo_legacy_import_path_stays_compatible():
    assert callable(find_center)


def test_swallow_segment_direct_commit_and_moore_aliases():
    prev = make_seg(20, 30, Direction.Down, 12, 10)
    swallow = make_seg(30, 33, Direction.Up, 10, 15)
    swallow.cache["is_macro_swallow"] = True

    analyzer = DailySegmentAnalyzer()
    analyzer.state.completed_segments = [
        DailySegment(
            symbol="TEST.DAILY",
            direction=Direction.Down,
            start_seg=prev,
            end_seg=prev,
            segments=[prev],
        )
    ]
    analyzer._process_new_segment(swallow)
    assert len(analyzer.daily_segments) == 2
    assert analyzer.daily_segments[-1].segments == [swallow]
    assert analyzer.daily_segments[-1].cache["from_macro_swallow"] is True
    assert analyzer.state.current_segments == []

    engine = MooreCZSC([])
    engine.daily_segment_analyzer.update([swallow])
    assert engine.daily_segments == engine.higher_segments
    assert engine.daily_active_center == engine.higher_active_center


def test_trend_relationship_requires_unique_start_extreme_and_allows_equal_end_extreme():
    analyzer = DailySegmentAnalyzer()
    up_ok = [
        make_seg(0, 1, Direction.Up, 10, 13),
        make_seg(1, 2, Direction.Down, 13, 11),
        make_seg(2, 3, Direction.Up, 11, 13),
    ]
    assert analyzer._check_global_trend_relationship(up_ok)

    up_duplicate_start_low = [
        make_seg(0, 1, Direction.Up, 10, 13),
        make_seg(1, 2, Direction.Down, 13, 10),
        make_seg(2, 3, Direction.Up, 10, 13),
    ]
    assert not analyzer._check_global_trend_relationship(up_duplicate_start_low)

    down_ok = [
        make_seg(0, 1, Direction.Down, 20, 17),
        make_seg(1, 2, Direction.Up, 17, 19),
        make_seg(2, 3, Direction.Down, 19, 17),
    ]
    assert analyzer._check_global_trend_relationship(down_ok)

    down_duplicate_start_high = [
        make_seg(0, 1, Direction.Down, 20, 17),
        make_seg(1, 2, Direction.Up, 17, 20),
        make_seg(2, 3, Direction.Down, 20, 17),
    ]
    assert not analyzer._check_global_trend_relationship(down_duplicate_start_high)


def test_ma_cross_scans_between_turning_k_confirmations():
    segs = [
        make_seg(0, 1, Direction.Up, 10, 12, start_turning_idx=10, end_turning_idx=20),
        make_seg(1, 2, Direction.Down, 12, 11, start_turning_idx=20, end_turning_idx=30),
        make_seg(2, 3, Direction.Up, 11, 14, start_turning_idx=30, end_turning_idx=40),
    ]

    analyzer = make_analyzer_with_ma({10: 1, 25: -1, 40: 1})
    assert analyzer._check_ma_cross_correlation(segs, analyzer.state.ma34, analyzer.state.ma170)

    analyzer = make_analyzer_with_ma({10: 1, 25: 1, 40: -1})
    assert analyzer._check_ma_cross_correlation(segs, analyzer.state.ma34, analyzer.state.ma170)

    analyzer = make_analyzer_with_ma({10: 1, 40: 0})
    assert not analyzer._check_ma_cross_correlation(segs, analyzer.state.ma34, analyzer.state.ma170)


def test_daily_segment_exposes_turning_confirmation_time_separately_from_price_endpoint():
    segs = [
        make_seg(0, 1, Direction.Up, 10, 12, start_turning_idx=10, end_turning_idx=20),
        make_seg(1, 2, Direction.Down, 12, 11, start_turning_idx=20, end_turning_idx=30),
        make_seg(2, 3, Direction.Up, 11, 14, start_turning_idx=30, end_turning_idx=40),
    ]

    daily = DailySegment(
        symbol="TEST.DAILY",
        direction=Direction.Up,
        start_seg=segs[0],
        end_seg=segs[-1],
        segments=segs,
    )

    assert daily.price_end_dt == make_bar(3, 14).dt
    assert daily.confirm_end_index == 40
    assert daily.edt == make_bar(40, 14).dt


def test_ma_cross_allows_exactly_one_segment_lag():
    segs = [
        make_seg(0, 1, Direction.Up, 10, 12, start_turning_idx=10, end_turning_idx=20),
        make_seg(1, 2, Direction.Down, 12, 11, start_turning_idx=20, end_turning_idx=30),
        make_seg(2, 3, Direction.Up, 11, 14, start_turning_idx=30, end_turning_idx=40),
    ]
    lag = make_seg(3, 4, Direction.Down, 14, 12, start_turning_idx=40, end_turning_idx=50)
    too_late = make_seg(4, 5, Direction.Up, 12, 15, start_turning_idx=50, end_turning_idx=60)

    analyzer = make_analyzer_with_ma({10: 1, 40: 1, 50: -1, 60: -1})
    assert analyzer._check_ma_cross_correlation(segs, analyzer.state.ma34, analyzer.state.ma170, lag)

    analyzer = make_analyzer_with_ma({10: 1, 40: 1, 50: 1, 60: -1})
    assert not analyzer._check_ma_cross_correlation(segs, analyzer.state.ma34, analyzer.state.ma170, lag)
    assert analyzer._check_ma_cross_correlation(segs, analyzer.state.ma34, analyzer.state.ma170, too_late)


def test_commit_selects_odd_window_from_continuous_start():
    segs = [
        make_seg(0, 1, Direction.Up, 10, 12, start_turning_idx=10, end_turning_idx=20),
        make_seg(1, 2, Direction.Down, 12, 11, start_turning_idx=20, end_turning_idx=30),
        make_seg(2, 3, Direction.Up, 11, 13, start_turning_idx=30, end_turning_idx=40),
        make_seg(3, 4, Direction.Down, 13, 12, start_turning_idx=40, end_turning_idx=50),
        make_seg(4, 5, Direction.Up, 12, 15, start_turning_idx=50, end_turning_idx=60),
    ]
    analyzer = make_analyzer_with_ma({10: 1, 40: 1, 60: -1})

    assert analyzer._commit_segments_if_valid(segs)
    assert len(analyzer.daily_segments) == 1
    assert analyzer.daily_segments[0].segments == segs[:5]


def test_live_processing_locks_ready_segment_and_restarts_from_its_endpoint():
    segs = [
        make_seg(0, 1, Direction.Down, 20, 16, start_turning_idx=10, end_turning_idx=20),
        make_seg(1, 2, Direction.Up, 16, 18, start_turning_idx=20, end_turning_idx=30),
        make_seg(2, 3, Direction.Down, 18, 10, start_turning_idx=30, end_turning_idx=40),
        make_seg(3, 4, Direction.Up, 10, 13, start_turning_idx=40, end_turning_idx=50),
        make_seg(4, 5, Direction.Down, 13, 11, start_turning_idx=50, end_turning_idx=60),
        make_seg(5, 6, Direction.Up, 11, 15, start_turning_idx=60, end_turning_idx=70),
    ]
    analyzer = make_analyzer_with_ma({10: 1, 40: -1, 70: 1})

    for seg in segs[:3]:
        analyzer._process_new_segment(seg)

    assert analyzer.daily_segments == []
    assert analyzer.state.pending_daily_segments == segs[:3]
    assert analyzer.state.current_segments == segs[:3]

    for seg in segs[3:]:
        analyzer._process_new_segment(seg)

    assert analyzer.daily_segments == []
    assert analyzer.state.current_segments == segs
    assert analyzer.state.pending_daily_segments == segs[:3]


def test_breaking_lag_segment_can_confirm_previous_window_before_reset():
    segs = [
        make_seg(0, 1, Direction.Up, 10, 12, start_turning_idx=10, end_turning_idx=20),
        make_seg(1, 2, Direction.Down, 12, 11, start_turning_idx=20, end_turning_idx=30),
        make_seg(2, 3, Direction.Up, 11, 14, start_turning_idx=30, end_turning_idx=40),
        make_seg(3, 4, Direction.Down, 14, 9, start_turning_idx=40, end_turning_idx=50),
    ]
    analyzer = make_analyzer_with_ma({10: 1, 40: 1, 50: -1})

    for seg in segs:
        analyzer._process_new_segment(seg)

    assert analyzer.daily_segments == []
    assert analyzer.state.pending_daily_segments == segs[:3]
    assert analyzer.state.current_segments == segs


def test_live_processing_extends_by_two_until_reverse_trend_forms():
    segs = [
        make_seg(0, 1, Direction.Up, 10, 12, start_turning_idx=10, end_turning_idx=20),
        make_seg(1, 2, Direction.Down, 12, 11, start_turning_idx=20, end_turning_idx=30),
        make_seg(2, 3, Direction.Up, 11, 13, start_turning_idx=30, end_turning_idx=40),
        make_seg(3, 4, Direction.Down, 13, 12, start_turning_idx=40, end_turning_idx=50),
        make_seg(4, 5, Direction.Up, 12, 15, start_turning_idx=50, end_turning_idx=60),
        make_seg(5, 6, Direction.Down, 15, 13, start_turning_idx=60, end_turning_idx=70),
        make_seg(6, 7, Direction.Up, 13, 14, start_turning_idx=70, end_turning_idx=80),
        make_seg(7, 8, Direction.Down, 14, 9, start_turning_idx=80, end_turning_idx=90),
    ]
    analyzer = make_analyzer_with_ma({10: 1, 60: -1, 90: 1})
    segs[5].end_k.is_perfect = True

    for seg in segs:
        analyzer._process_new_segment(seg)

    assert analyzer.daily_segments == []
    assert analyzer.state.current_segments == segs
    assert analyzer.state.pending_daily_segments


def test_non_same_processing_marks_reverse_dashed_start_as_pending_candidate():
    prev = make_seg(0, 3, Direction.Up, 10, 15, start_turning_idx=10, end_turning_idx=30)
    reverse_dashed = make_seg(3, 4, Direction.Down, 15, 14, start_turning_idx=30, end_turning_idx=40)
    middle = make_seg(4, 5, Direction.Up, 14, 16, start_turning_idx=40, end_turning_idx=50)
    end = make_seg(5, 6, Direction.Down, 16, 13, start_turning_idx=50, end_turning_idx=60)

    analyzer = DailySegmentAnalyzer()
    analyzer.state.completed_segments = [
        DailySegment(
            symbol="TEST.DAILY",
            direction=Direction.Up,
            start_seg=prev,
            end_seg=prev,
            segments=[prev],
        )
    ]
    set_ma34(analyzer, {30: 10, 40: 9, 50: 11, 60: 8})

    for seg in [reverse_dashed, middle]:
        analyzer._process_new_segment(seg)
    assert len(analyzer.daily_segments) == 1

    analyzer._process_new_segment(end)

    assert len(analyzer.daily_segments) == 1
    assert analyzer.state.pending_daily_segments == [reverse_dashed, middle, end]
    assert analyzer.state.current_segments == [reverse_dashed, middle, end]


def test_non_same_processing_can_confirm_previous_long_trend_as_reverse_candidate():
    up = [
        make_seg(0, 1, Direction.Up, 10, 12, start_turning_idx=10, end_turning_idx=20),
        make_seg(1, 2, Direction.Down, 12, 11, start_turning_idx=20, end_turning_idx=30),
        make_seg(2, 3, Direction.Up, 11, 15, start_turning_idx=30, end_turning_idx=40),
    ]
    reverse_dashed = make_seg(3, 4, Direction.Down, 15, 14, start_turning_idx=40, end_turning_idx=50)
    middle = make_seg(4, 5, Direction.Up, 14, 16, start_turning_idx=50, end_turning_idx=60)
    end = make_seg(5, 6, Direction.Down, 16, 13, start_turning_idx=60, end_turning_idx=70)

    analyzer = make_analyzer_with_ma({10: 1, 40: -1})
    set_ma34(analyzer, {10: 11, 40: 9, 50: 10, 60: 12, 70: 8})
    analyzer.state.ma170 = [10] * 240

    for seg in [*up, reverse_dashed, middle, end]:
        analyzer._process_new_segment(seg)

    assert analyzer.daily_segments == []
    assert analyzer.state.current_segments == [*up, reverse_dashed, middle, end]
    assert analyzer.state.pending_daily_segments[:3] == up


def test_non_same_processing_requires_dashed_reverse_start():
    prev = make_seg(0, 3, Direction.Up, 10, 15, start_turning_idx=10, end_turning_idx=30)
    reverse_perfect = make_seg(3, 4, Direction.Down, 15, 14, start_turning_idx=30, end_turning_idx=40)
    reverse_perfect.end_k.is_perfect = True
    middle = make_seg(4, 5, Direction.Up, 14, 16, start_turning_idx=40, end_turning_idx=50)
    end = make_seg(5, 6, Direction.Down, 16, 13, start_turning_idx=50, end_turning_idx=60)

    analyzer = DailySegmentAnalyzer()
    analyzer.state.completed_segments = [
        DailySegment(
            symbol="TEST.DAILY",
            direction=Direction.Up,
            start_seg=prev,
            end_seg=prev,
            segments=[prev],
        )
    ]
    set_ma34(analyzer, {30: 10, 40: 9, 50: 11, 60: 8})

    for seg in [reverse_perfect, middle, end]:
        analyzer._process_new_segment(seg)

    assert len(analyzer.daily_segments) == 1
    assert analyzer.state.current_segments == [reverse_perfect, middle, end]


def test_old_trend_break_no_longer_resets_running_candidate():
    seg1 = make_seg(0, 1, Direction.Up, 10, 12, start_turning_idx=10, end_turning_idx=20)
    seg2 = make_seg(1, 2, Direction.Down, 12, 9, start_turning_idx=20, end_turning_idx=30)
    analyzer = make_analyzer_with_ma({10: 1, 30: -1})

    analyzer._process_new_segment(seg1)
    analyzer._process_new_segment(seg2)

    assert analyzer.daily_segments == []
    assert analyzer.state.current_segments == [seg1, seg2]
    assert analyzer.state.continuity_broken is False


def test_swallow_segment_can_also_participate_in_ordinary_window():
    swallow = make_seg(0, 1, Direction.Up, 10, 12, start_turning_idx=10, end_turning_idx=20)
    swallow.cache["is_macro_swallow"] = True
    seg2 = make_seg(1, 2, Direction.Down, 12, 11, start_turning_idx=20, end_turning_idx=30)
    seg3 = make_seg(2, 3, Direction.Up, 11, 14, start_turning_idx=30, end_turning_idx=40)
    break_seg = make_seg(3, 4, Direction.Down, 14, 9, start_turning_idx=40, end_turning_idx=50)

    analyzer = DailySegmentAnalyzer()
    analyzer.state.ma34, analyzer.state.ma170 = make_ma_arrays({10: 1, 40: -1})
    for seg in [swallow, seg2, seg3, break_seg]:
        analyzer._process_new_segment(seg)

    assert analyzer.daily_segments == []
    assert analyzer.state.pending_daily_segments == [swallow, seg2, seg3]
    assert analyzer.state.current_segments == [swallow, seg2, seg3, break_seg]


def test_continuity_break_blocks_later_daily_segment_from_skipping_gap():
    first = [
        make_seg(0, 1, Direction.Up, 10, 12, start_turning_idx=10, end_turning_idx=20),
        make_seg(1, 2, Direction.Down, 12, 11, start_turning_idx=20, end_turning_idx=30),
        make_seg(2, 3, Direction.Up, 11, 14, start_turning_idx=30, end_turning_idx=40),
    ]
    failed = [
        make_seg(3, 4, Direction.Down, 14, 13, start_turning_idx=40, end_turning_idx=50),
        make_seg(4, 5, Direction.Up, 13, 14, start_turning_idx=50, end_turning_idx=60),
        make_seg(5, 6, Direction.Down, 14, 12, start_turning_idx=60, end_turning_idx=70),
    ]
    later = [
        make_seg(6, 7, Direction.Up, 12, 15, start_turning_idx=70, end_turning_idx=80),
        make_seg(7, 8, Direction.Down, 15, 13, start_turning_idx=80, end_turning_idx=90),
        make_seg(8, 9, Direction.Up, 13, 16, start_turning_idx=90, end_turning_idx=100),
    ]
    analyzer = make_analyzer_with_ma({10: 1, 40: -1, 70: -1, 100: 1})

    assert analyzer._commit_segments_if_valid(first)
    assert not analyzer._commit_segments_if_valid(failed)
    analyzer.state.continuity_broken = True
    assert not analyzer._commit_segments_if_valid(later)


def test_cold_start_skips_warmup_segments_and_waits_for_reverse_confirmation():
    warmup = make_seg(0, 1, Direction.Up, 9, 11, start_turning_idx=10, end_turning_idx=20)
    down = [
        make_seg(1, 2, Direction.Down, 11, 8, start_turning_idx=20, end_turning_idx=30),
        make_seg(2, 3, Direction.Up, 8, 9, start_turning_idx=30, end_turning_idx=40),
        make_seg(3, 4, Direction.Down, 9, 6, start_turning_idx=40, end_turning_idx=50),
    ]
    up = [
        make_seg(4, 5, Direction.Up, 6, 7, start_turning_idx=50, end_turning_idx=60),
        make_seg(5, 6, Direction.Down, 7, 6.5, start_turning_idx=60, end_turning_idx=70),
        make_seg(6, 7, Direction.Up, 6.5, 12, start_turning_idx=70, end_turning_idx=80),
    ]
    analyzer = make_analyzer_with_ma({20: 1, 50: -1, 60: -1, 80: 1})

    for seg in [warmup, *down]:
        analyzer._process_new_segment(seg)
    assert analyzer.daily_segments == []

    for seg in up:
        analyzer._process_new_segment(seg)

    assert analyzer.daily_segments == []
    assert analyzer.state.current_segments == [warmup, *down, *up]
    assert analyzer.state.pending_daily_segments == down


def test_delayed_confirmation_keeps_best_endpoint_until_reverse_daily_candidate_forms():
    prev = make_seg(0, 1, Direction.Up, 5, 10, start_turning_idx=10, end_turning_idx=20)
    down = [
        make_seg(1, 2, Direction.Down, 10, 7, start_turning_idx=20, end_turning_idx=30),
        make_seg(2, 3, Direction.Up, 7, 8, start_turning_idx=30, end_turning_idx=40),
        make_seg(3, 4, Direction.Down, 8, 6, start_turning_idx=40, end_turning_idx=50),
        make_seg(4, 5, Direction.Up, 6, 7, start_turning_idx=50, end_turning_idx=60),
        make_seg(5, 6, Direction.Down, 7, 4, start_turning_idx=60, end_turning_idx=70),
    ]
    up = [
        make_seg(6, 7, Direction.Up, 4, 5, start_turning_idx=70, end_turning_idx=80),
        make_seg(7, 8, Direction.Down, 5, 4.5, start_turning_idx=80, end_turning_idx=90),
        make_seg(8, 9, Direction.Up, 4.5, 11, start_turning_idx=90, end_turning_idx=100),
    ]
    analyzer = make_analyzer_with_ma({20: 1, 50: -1, 70: -1, 100: 1})
    analyzer.state.completed_segments = [
        DailySegment(
            symbol="TEST.DAILY",
            direction=Direction.Up,
            start_seg=prev,
            end_seg=prev,
            segments=[prev],
        )
    ]

    for seg in down:
        analyzer._process_new_segment(seg)
    assert len(analyzer.daily_segments) == 1
    assert analyzer.state.pending_daily_segments == down

    for seg in up:
        analyzer._process_new_segment(seg)

    assert len(analyzer.daily_segments) == 1
    assert analyzer.state.current_segments == down + up
    assert analyzer.state.pending_daily_segments == down


def test_600707_daily_segments_match_expected_long_trend_and_swallow():
    bars = research.get_raw_bars_origin("600707", sdt="20140601", edt="20210820")
    if not bars:
        pytest.skip("no bars for 600707")

    engine = MooreCZSC(
        bars,
        ma34_cross_as_valid_gate=True,
        ma34_cross_expand_one_k=False,
        audit_link_rounds=3,
        enable_pre_round=True,
        replay_centers_after_macro_swallow=False,
    )
    display_tks = getattr(engine, "micro_turning_ks", engine.turning_ks)
    label_by_key = {
        (tk.k_index, tk.dt, tk.price): f"mV{i}{'T' if tk.mark == Mark.G else 'B'}"
        for i, tk in enumerate(display_tks)
    }

    def label(tk):
        return label_by_key[(tk.k_index, tk.dt, tk.price)]

    daily_pairs = [(label(ds.start_seg.start_k), label(ds.end_seg.end_k)) for ds in engine.daily_segments]

    assert daily_pairs[:2] == [("mV1T", "mV36B"), ("mV36B", "mV43T")]
    assert ("mV4B", "mV6T") not in daily_pairs
    assert engine.daily_segments[1].segments[0].cache.get("is_macro_swallow") is True


def test_600707_daily_centers_use_filtered_30f_segments():
    bars = research.get_raw_bars_origin("600707", sdt="20140601", edt="20210820")
    if not bars:
        pytest.skip("no bars for 600707")

    engine = MooreCZSC(
        bars,
        ma34_cross_as_valid_gate=True,
        ma34_cross_expand_one_k=False,
        audit_link_rounds=3,
        enable_pre_round=True,
        replay_centers_after_macro_swallow=False,
    )
    display_tks = getattr(engine, "micro_turning_ks", engine.turning_ks)
    label_by_key = {
        (tk.k_index, tk.dt, tk.price): f"mV{i}{'T' if tk.mark == Mark.G else 'B'}"
        for i, tk in enumerate(display_tks)
    }

    def label(tk):
        return label_by_key[(tk.k_index, tk.dt, tk.price)]

    centers = engine.daily_centers
    assert centers
    first = centers[0]

    assert (label(first.segments[0].start_k), label(first.segments[-1].end_k)) == ("mV1T", "mV9T")
    assert first.overlap_type == 3
    assert first.status == "FINAL"
    assert round(first.low, 3) == 11.431
    assert round(first.high, 3) == 11.875
    assert round(first.points["A"][1], 3) == 11.431
    assert round(first.points["B"][1], 3) == 11.875
    assert all(not seg.cache.get("is_macro_swallow") for seg in first.segments)


def test_600707_daily_centers_follow_parent_daily_segment_direction():
    bars = research.get_raw_bars_origin("600707", sdt="20140601", edt="20210820")
    if not bars:
        pytest.skip("no bars for 600707")

    engine = MooreCZSC(
        bars,
        ma34_cross_as_valid_gate=True,
        ma34_cross_expand_one_k=False,
        audit_link_rounds=3,
        enable_pre_round=True,
        replay_centers_after_macro_swallow=False,
    )
    display_tks = getattr(engine, "micro_turning_ks", engine.turning_ks)
    label_by_key = {
        (tk.k_index, tk.dt, tk.price): f"mV{i}{'T' if tk.mark == Mark.G else 'B'}"
        for i, tk in enumerate(display_tks)
    }

    def label(tk):
        return label_by_key[(tk.k_index, tk.dt, tk.price)]

    center = next(
        c
        for c in engine.daily_centers
        if (label(c.segments[0].start_k), label(c.segments[-1].end_k)) == ("mV1T", "mV9T")
    )

    assert center.cache["daily_segment_direction"] == Direction.Down.value
    assert round(center.points["B"][1], 3) == 11.875


def test_600707_daily_centers_drop_overlapping_sliding_derivatives():
    bars = research.get_raw_bars_origin("600707", sdt="20140601", edt="20210820")
    if not bars:
        pytest.skip("no bars for 600707")

    engine = MooreCZSC(
        bars,
        ma34_cross_as_valid_gate=True,
        ma34_cross_expand_one_k=False,
        audit_link_rounds=3,
        enable_pre_round=True,
        replay_centers_after_macro_swallow=False,
    )
    display_tks = getattr(engine, "micro_turning_ks", engine.turning_ks)
    label_by_key = {
        (tk.k_index, tk.dt, tk.price): f"mV{i}{'T' if tk.mark == Mark.G else 'B'}"
        for i, tk in enumerate(display_tks)
    }

    def label(tk):
        return label_by_key[(tk.k_index, tk.dt, tk.price)]

    spans = [(label(c.segments[0].start_k), label(c.segments[-1].end_k)) for c in engine.daily_centers]

    assert ("mV1T", "mV9T") in spans
    assert ("mV10B", "mV24B") in spans
    assert ("mV9T", "mV20B") not in spans
    assert ("mV2B", "mV10B") not in spans
    assert ("mV3T", "mV11T") not in spans


def test_overlapping_daily_centers_keep_first_generated_type3():
    analyzer = DailySegmentAnalyzer()
    shared_1 = make_seg(20, 30, Direction.Down, 12, 9)
    shared_2 = make_seg(30, 40, Direction.Up, 9, 11)
    slower_left = DailySegmentCenter(
        segments=[
            make_seg(0, 10, Direction.Down, 20, 10),
            make_seg(10, 20, Direction.Up, 10, 12),
            shared_1,
            shared_2,
            make_seg(40, 100, Direction.Down, 11, 6),
        ],
        high=12,
        low=10,
        overlap_type=3,
        status="FINAL",
    )
    faster_right = DailySegmentCenter(
        segments=[
            shared_1,
            shared_2,
            make_seg(40, 50, Direction.Down, 11, 8),
            make_seg(50, 60, Direction.Up, 8, 10),
            make_seg(60, 70, Direction.Down, 10, 7),
        ],
        high=11,
        low=9,
        overlap_type=3,
        status="FINAL",
    )

    selected = analyzer._dedupe_overlapping_daily_centers([slower_left, faster_right])

    assert selected == [faster_right]


def test_overlapping_daily_centers_keep_earliest_third_segment_ba_entry():
    analyzer = DailySegmentAnalyzer()
    shared_1 = make_seg(20, 30, Direction.Down, 12, 9)
    shared_2 = make_seg(30, 40, Direction.Up, 9, 11)
    later_entry = DailySegmentCenter(
        segments=[
            make_seg(0, 10, Direction.Down, 20, 10),
            make_seg(10, 20, Direction.Up, 10, 12),
            shared_1,
            shared_2,
            make_seg(40, 100, Direction.Down, 11, 6),
        ],
        high=12,
        low=10,
        overlap_type=3,
        status="FINAL",
        points={"A": (10, 10), "B": (20, 12), "C": (55, 9), "D": (80, 11)},
        cache={"third_entry_index": 70},
    )
    earlier_entry = DailySegmentCenter(
        segments=[
            shared_1,
            shared_2,
            make_seg(40, 50, Direction.Down, 11, 8),
            make_seg(50, 60, Direction.Up, 8, 10),
            make_seg(60, 70, Direction.Down, 10, 7),
        ],
        high=11,
        low=9,
        overlap_type=3,
        status="FINAL",
        points={"A": (10, 10), "B": (20, 12), "C": (65, 9), "D": (80, 11)},
        cache={"third_entry_index": 60},
    )

    selected = analyzer._dedupe_overlapping_daily_centers([later_entry, earlier_entry])

    assert selected == [earlier_entry]


def test_002613_daily_segments_match_expected_blue_split():
    bars = research.get_raw_bars_origin("002613", sdt="20160801", edt="20210820")
    if not bars:
        pytest.skip("no bars for 002613")

    engine = MooreCZSC(
        bars,
        ma34_cross_as_valid_gate=True,
        ma34_cross_expand_one_k=False,
        audit_link_rounds=3,
        enable_pre_round=True,
        replay_centers_after_macro_swallow=False,
    )
    display_tks = getattr(engine, "micro_turning_ks", engine.turning_ks)
    label_by_key = {
        (tk.k_index, tk.dt, tk.price): f"V{i}{'T' if tk.mark == Mark.G else 'B'}"
        for i, tk in enumerate(display_tks)
    }

    def label(tk):
        return label_by_key[(tk.k_index, tk.dt, tk.price)]

    daily_pairs = [(label(ds.start_seg.start_k), label(ds.end_seg.end_k)) for ds in engine.daily_segments]

    assert daily_pairs[:3] == [("V1T", "V18B"), ("V18B", "V23T"), ("V23T", "V30B")]
