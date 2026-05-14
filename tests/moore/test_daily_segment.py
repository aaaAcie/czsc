# -*- coding: utf-8 -*-
from datetime import datetime, timedelta

import pytest

from czsc.connectors import research
from czsc.moore.analyze import MooreCZSC
from czsc.moore.daily_segment import DailySegment, DailySegmentAnalyzer, DailySegmentCenter
from czsc.moore.daily_segment.center_algo import find_b_point, find_center, find_d_point
from czsc.moore.daily_segment.helpers.commit import WindowCandidate, check_daily_segment_independence
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


def make_empty_ma(size: int = 240) -> tuple[list[float | None], list[float | None]]:
    return [None] * size, [10] * size


def make_600707_engine() -> MooreCZSC:
    bars = research.get_raw_bars_origin("600707", sdt="20140601", edt="20210820")
    if not bars:
        pytest.skip("no bars for 600707")
    return MooreCZSC(
        bars,
        ma34_cross_as_valid_gate=True,
        ma34_cross_expand_one_k=False,
        audit_link_rounds=3,
        enable_pre_round=True,
        replay_centers_after_macro_swallow=False,
        rebuild_daily_centers_after_segment_change=True,
    )


def make_603178_engine() -> MooreCZSC:
    bars = research.get_raw_bars_origin("603178", sdt="20171015", edt="20211101")
    if not bars:
        pytest.skip("no bars for 603178")
    return MooreCZSC(
        bars,
        ma34_cross_as_valid_gate=True,
        ma34_cross_expand_one_k=False,
        audit_link_rounds=3,
        enable_pre_round=True,
        replay_centers_after_macro_swallow=False,
        rebuild_daily_centers_after_segment_change=True,
    )


def make_visible_labelers(engine: MooreCZSC, prefix: str = "mV"):
    display_tks = getattr(engine, "micro_turning_ks", engine.turning_ks)
    label_by_key = {
        (tk.k_index, tk.dt, tk.price): f"{prefix}{i}{'T' if tk.mark == Mark.G else 'B'}"
        for i, tk in enumerate(display_tks)
    }
    label_by_owner_key = {
        (tk.k_index, tk.dt, tk.price, tk.mark.value): f"{prefix}{i}{'T' if tk.mark == Mark.G else 'B'}"
        for i, tk in enumerate(display_tks)
    }

    def label(tk):
        return label_by_key[(tk.k_index, tk.dt, tk.price)]

    def owner_label(key):
        return label_by_owner_key[key]

    return label, owner_label


def daily_date_spans(engine: MooreCZSC):
    return [
        (
            ds.start_seg.start_k.dt.strftime("%Y-%m-%d"),
            ds.end_seg.end_k.dt.strftime("%Y-%m-%d"),
            ds.direction.name,
        )
        for ds in engine.daily_segments
    ]


def pending_date_spans(engine: MooreCZSC):
    return [
        (
            ds.start_seg.start_k.dt.strftime("%Y-%m-%d"),
            ds.end_seg.end_k.dt.strftime("%Y-%m-%d"),
            ds.direction.name,
        )
        for ds in engine.daily_pending_segments
    ]


def non_same_date_spans(engine: MooreCZSC):
    return [
        (
            ds.start_seg.start_k.dt.strftime("%Y-%m-%d"),
            ds.end_seg.end_k.dt.strftime("%Y-%m-%d"),
            ds.direction.name,
        )
        for ds in engine.daily_non_same_segments
    ]


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


def test_find_center_returns_turning_center_when_price_never_reenters_ab():
    segs = [
        make_seg(0, 10, Direction.Down, 20, 10),
        make_seg(10, 20, Direction.Up, 10, 12),
        make_seg(20, 30, Direction.Down, 12, 8.5),
        make_seg(30, 40, Direction.Up, 8.5, 8.8),
        make_seg(40, 50, Direction.Down, 8.8, 7),
    ]
    ma34, _ = make_empty_ma(80)
    ma34[7], ma34[8], ma34[9] = 10, 9, 10
    ma34[18], ma34[19], ma34[20] = 11, 12, 11

    result = find_center(segs, ma34, trend_direction=Direction.Down)

    assert result is not None
    assert result["center_kind"] == "turning"
    assert result["overlap_type"] == 0
    assert result["status"] == "FINAL"
    assert result["low"] == 9
    assert result["high"] == 12


def test_find_center_keeps_turning_boundary_from_later_badc_upgrade():
    segs = [
        make_seg(0, 10, Direction.Down, 20, 10),
        make_seg(10, 20, Direction.Up, 10, 12),
        make_seg(20, 30, Direction.Down, 12, 8.5),
        make_seg(30, 40, Direction.Up, 8.5, 8.8),
        make_seg(40, 50, Direction.Down, 8.8, 7),
        make_seg(50, 60, Direction.Up, 7, 10),
    ]
    ma34, _ = make_empty_ma(90)
    ma34[7], ma34[8], ma34[9] = 10, 9, 10
    ma34[18], ma34[19], ma34[20] = 11, 12, 11
    ma34[55], ma34[56], ma34[57] = 8, 10, 8

    result = find_center(segs, ma34, trend_direction=Direction.Down)

    assert result is not None
    assert result["center_kind"] == "turning"
    assert result["segments"] == segs[:4]


def test_independence_accepts_valid_candidate_without_own_daily_center():
    primary = WindowCandidate(0, 3, [
        make_seg(0, 1, Direction.Up, 10, 13),
        make_seg(1, 2, Direction.Down, 13, 11),
        make_seg(2, 3, Direction.Up, 11, 14),
    ])
    reverse = WindowCandidate(3, 6, [
        make_seg(3, 4, Direction.Down, 14, 12),
        make_seg(4, 5, Direction.Up, 12, 13),
        make_seg(5, 6, Direction.Down, 13, 11),
    ])
    ma34, ma170 = make_empty_ma()

    decision = check_daily_segment_independence(primary, reverse, primary.segments + reverse.segments, ma34, ma170, [])

    assert decision.ok
    assert decision.kind == "no_daily_center"


def test_independence_requires_strict_new_extreme_for_trend_class_center():
    primary = WindowCandidate(0, 3, [
        make_seg(0, 1, Direction.Up, 10, 13),
        make_seg(1, 2, Direction.Down, 13, 11),
        make_seg(2, 3, Direction.Up, 11, 14),
    ])
    reverse = WindowCandidate(3, 7, [
        make_seg(3, 13, Direction.Down, 14, 11),
        make_seg(13, 23, Direction.Up, 11, 12),
        make_seg(23, 33, Direction.Down, 12, 8.5),
        make_seg(33, 43, Direction.Up, 8.5, 10),
    ])
    ma34, ma170 = make_empty_ma(80)
    ma34[7], ma34[8], ma34[9] = 10, 9, 10
    ma34[18], ma34[19], ma34[20] = 11, 12, 11

    decision = check_daily_segment_independence(primary, reverse, primary.segments + reverse.segments, ma34, ma170, [])

    assert not decision.ok
    assert decision.center_kind == "trend_class"
    assert decision.requires_new_extreme
    assert decision.new_extreme_ok is False


def test_independence_accepts_trend_class_center_with_strict_new_extreme():
    primary = WindowCandidate(0, 3, [
        make_seg(0, 1, Direction.Up, 10, 13),
        make_seg(1, 2, Direction.Down, 13, 11),
        make_seg(2, 3, Direction.Up, 11, 14),
    ])
    reverse = WindowCandidate(3, 8, [
        make_seg(3, 13, Direction.Down, 14, 11),
        make_seg(13, 23, Direction.Up, 11, 12),
        make_seg(23, 33, Direction.Down, 12, 8.5),
        make_seg(33, 43, Direction.Up, 8.5, 10),
        make_seg(43, 53, Direction.Down, 10, 8),
    ])
    ma34, ma170 = make_empty_ma(90)
    ma34[7], ma34[8], ma34[9] = 10, 9, 10
    ma34[18], ma34[19], ma34[20] = 11, 12, 11

    decision = check_daily_segment_independence(primary, reverse, primary.segments + reverse.segments, ma34, ma170, [])

    assert decision.ok
    assert decision.kind == "strict_new_extreme"
    assert decision.center_kind == "trend_class"
    assert decision.new_extreme_ok is True


def test_independence_accepts_boundary_turning_center_without_new_extreme():
    primary = WindowCandidate(0, 3, [
        make_seg(0, 10, Direction.Down, 20, 10),
        make_seg(10, 20, Direction.Up, 10, 12),
        make_seg(20, 30, Direction.Down, 12, 8.5),
    ])
    reverse = WindowCandidate(3, 6, [
        make_seg(30, 40, Direction.Up, 8.5, 8.8),
        make_seg(40, 50, Direction.Down, 8.8, 7.5),
        make_seg(50, 60, Direction.Up, 7.5, 11),
    ])
    ma34, ma170 = make_empty_ma(90)
    ma34[7], ma34[8], ma34[9] = 10, 9, 10
    ma34[18], ma34[19], ma34[20] = 11, 12, 11

    decision = check_daily_segment_independence(primary, reverse, primary.segments + reverse.segments, ma34, ma170, [])

    assert decision.ok
    assert decision.kind == "third_buy_sell"
    assert decision.center_kind == "turning"
    assert decision.requires_new_extreme is False


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
    assert len(analyzer.daily_segments) == 1
    assert analyzer.state.current_segments == [swallow]
    assert analyzer.daily_pending_segments == []

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

    up_bad_end = [
        make_seg(0, 1, Direction.Up, 10, 13),
        make_seg(1, 2, Direction.Down, 13, 11),
        make_seg(2, 3, Direction.Up, 11, 12),
    ]
    assert not analyzer._check_global_trend_relationship(up_bad_end)

    down_bad_end = [
        make_seg(0, 1, Direction.Down, 20, 17),
        make_seg(1, 2, Direction.Up, 17, 19),
        make_seg(2, 3, Direction.Down, 19, 18),
    ]
    assert not analyzer._check_global_trend_relationship(down_bad_end)


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
    assert analyzer.state.pending_daily_segments == segs[3:]


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

    assert len(analyzer.daily_segments) == 1
    assert analyzer.daily_segments[0].segments == segs[2:5]
    assert analyzer.state.current_segments == segs[5:]
    assert analyzer.state.pending_daily_segments == segs[5:]


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


def test_daily_center_source_expands_qualified_daily_swallow_only():
    swallow = make_seg(0, 30, Direction.Up, 10, 13, start_turning_idx=0, end_turning_idx=30)
    swallow.cache["is_macro_swallow"] = True
    swallow.start_k.cache["micro_id"] = 1
    swallow.end_k.cache["micro_id"] = 4

    micro_12 = make_seg(0, 10, Direction.Up, 10, 11, start_turning_idx=0, end_turning_idx=10)
    micro_23 = make_seg(10, 20, Direction.Down, 11, 10.5, start_turning_idx=10, end_turning_idx=20)
    micro_34 = make_seg(20, 30, Direction.Up, 10.5, 13, start_turning_idx=20, end_turning_idx=30)
    for seg, start_id, end_id in [(micro_12, 1, 2), (micro_23, 2, 3), (micro_34, 3, 4)]:
        seg.start_k.cache["micro_id"] = start_id
        seg.end_k.cache["micro_id"] = end_id

    analyzer = DailySegmentAnalyzer(micro_segments=[micro_12, micro_23, micro_34])
    daily_swallow = DailySegment(
        symbol="TEST.DAILY",
        direction=Direction.Up,
        start_seg=swallow,
        end_seg=swallow,
        segments=[swallow],
        cache={"from_macro_swallow": True},
    )
    ordinary = DailySegment(
        symbol="TEST.DAILY",
        direction=Direction.Up,
        start_seg=swallow,
        end_seg=swallow,
        segments=[swallow],
    )

    expanded = analyzer._daily_segment_center_source_segments(daily_swallow)
    kept = analyzer._daily_segment_center_source_segments(ordinary)

    assert [(seg.start_k.cache["micro_id"], seg.end_k.cache["micro_id"]) for seg in expanded] == [(1, 2), (2, 3), (3, 4)]
    assert all(seg.cache["source_for_daily_center"] == "expanded_from_swallow" for seg in expanded)
    assert kept == [swallow]


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

    assert len(analyzer.daily_segments) == 1
    assert analyzer.daily_segments[0].segments == down[0:3]
    assert analyzer.state.current_segments == up
    assert analyzer.state.pending_daily_segments == up


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
    assert analyzer.daily_segments[0].segments == [prev]
    assert analyzer.state.pending_daily_segments == down

    for seg in up:
        analyzer._process_new_segment(seg)

    assert len(analyzer.daily_segments) == 2
    assert analyzer.daily_segments[1].segments == down
    assert analyzer.state.current_segments == up
    assert analyzer.state.pending_daily_segments == up


def test_600707_daily_segments_match_expected_long_trend_and_swallow():
    engine = make_600707_engine()
    label, _ = make_visible_labelers(engine)

    daily_pairs = [(label(ds.start_seg.start_k), label(ds.end_seg.end_k)) for ds in engine.daily_segments]

    assert daily_pairs[:2] == [("mV1T", "mV36B"), ("mV36B", "mV43T")]
    assert ("mV4B", "mV6T") not in daily_pairs
    assert engine.daily_segments[1].segments[0].cache.get("is_macro_swallow") is True


def test_daily_center_rebuild_after_segment_change_defaults_to_construction_time_centers():
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

    assert engine.daily_segments
    assert engine.daily_centers
    assert all(c.cache.get("source") == "daily_segment_construction" for c in engine.daily_centers)
    assert all(c.cache.get("source") != "daily_segment_internal" for c in engine.daily_centers)
    direct = next(c for c in engine.daily_centers if round(c.low, 3) == 8.046 and round(c.high, 3) == 8.160)
    assert direct.cache["construction_direction"] == Direction.Down.value
    assert engine.daily_pending_centers == []
    assert engine.daily_refined_segments == []


def test_regression_002346_pending_reverse_confirms_previous_daily_segments():
    bars = research.get_raw_bars_origin("002346", sdt="20161201", edt="20211001")
    if not bars:
        pytest.skip("no bars for 002346")

    engine = MooreCZSC(
        bars,
        ma34_cross_as_valid_gate=True,
        ma34_cross_expand_one_k=False,
        audit_link_rounds=3,
        enable_pre_round=True,
        replay_centers_after_macro_swallow=False,
    )
    spans = daily_date_spans(engine)
    assert spans == [
        ("2017-01-09", "2018-10-19", "Down"),
        ("2018-10-19", "2019-04-08", "Up"),
        ("2019-04-08", "2020-04-28", "Down"),
        ("2020-04-28", "2020-09-04", "Up"),
        ("2020-09-04", "2021-01-04", "Down"),
    ]
    assert engine.daily_segments[1].cache["independence_kind"] == "no_daily_center"


def test_regression_002346_daily_window_rejects_non_extreme_end():
    bars = research.get_raw_bars_origin("002346", sdt="20161201", edt="20211001")
    if not bars:
        pytest.skip("no bars for 002346")

    engine = MooreCZSC(
        bars,
        ma34_cross_as_valid_gate=True,
        ma34_cross_expand_one_k=False,
        audit_link_rounds=3,
        enable_pre_round=True,
        replay_centers_after_macro_swallow=False,
    )
    start = next(i for i, seg in enumerate(engine.segments) if seg.start_k.dt.strftime("%Y-%m-%d") == "2018-04-02")
    end = next(i for i, seg in enumerate(engine.segments) if seg.end_k.dt.strftime("%Y-%m-%d") == "2019-01-31") + 1
    window = engine.segments[start:end]

    assert (window[0].start_k.dt.strftime("%Y-%m-%d"), window[-1].end_k.dt.strftime("%Y-%m-%d")) == (
        "2018-04-02",
        "2019-01-31",
    )
    assert not engine.daily_segment_analyzer._check_global_trend_relationship(window)


def test_600707_daily_centers_use_completed_source_and_owner_chain():
    engine = make_600707_engine()
    label, _ = make_visible_labelers(engine)

    centers = engine.daily_centers
    assert centers
    first = centers[0]

    assert (label(first.segments[0].start_k), label(first.segments[-1].end_k)) == ("mV1T", "mV8B")
    assert first.overlap_type == 3
    assert first.status == "FINAL"
    assert round(first.low, 3) == 11.431
    assert round(first.high, 3) == 11.875
    assert round(first.points["A"][1], 3) == 11.431
    assert round(first.points["B"][1], 3) == 11.875
    assert first.cache["source_segments_kind"] == "expanded_continuous_30f"


def test_regression_600707_direct_center_owner_chain_stays_v14_v15_v18_v19():
    engine = make_600707_engine()
    _, owner_label = make_visible_labelers(engine)

    # 直接链回归：最终 [8.046, 8.160] 必须继续由已有连续 owner 链生成。
    direct = next(c for c in engine.daily_centers if round(c.low, 3) == 8.046 and round(c.high, 3) == 8.160)
    assert direct.overlap_type == 3
    assert direct.status == "FINAL"
    assert direct.cache["owner_chain_valid"] is True
    assert [owner_label(key) for key in direct.cache["owner_chain"]] == ["mV14B", "mV15T", "mV18B", "mV19T"]
    assert any(seg.cache.get("is_macro_swallow") for seg in direct.segments)


def test_600707_daily_centers_follow_parent_daily_segment_direction():
    engine = make_600707_engine()
    label, _ = make_visible_labelers(engine)

    center = next(
        c
        for c in engine.daily_centers
        if (label(c.segments[0].start_k), label(c.segments[-1].end_k)) == ("mV1T", "mV8B")
    )

    assert center.cache["daily_segment_direction"] == Direction.Down.value
    assert round(center.points["B"][1], 3) == 11.875


def test_600707_daily_centers_drop_overlapping_sliding_derivatives():
    engine = make_600707_engine()
    label, _ = make_visible_labelers(engine)

    spans = [(label(c.segments[0].start_k), label(c.segments[-1].end_k)) for c in engine.daily_centers]

    assert ("mV1T", "mV8B") in spans
    assert ("mV11T", "mV20B") in spans
    assert ("mV9T", "mV20B") not in spans
    assert ("mV2B", "mV10B") not in spans
    assert ("mV3T", "mV11T") not in spans


def test_regression_600707_candidate_owner_repair_infers_v14_to_v19_without_mutating_30f():
    engine = make_600707_engine()
    label, owner_label = make_visible_labelers(engine)
    analyzer = engine.daily_segment_analyzer
    daily_segment = engine.daily_segments[0]
    full_source = analyzer._daily_segment_center_source_segments(daily_segment)
    compact_source = analyzer._compact_repair_source_segments(full_source)

    # 反推链回归：若 compact 候选 [8.466, 8.629] 被拿来解释，
    # 必须归因到 mV10B -> mV11T -> mV14B -> mV19T，并只生成日线层 refined segment。
    target_result = None
    for start in range(max(0, len(compact_source) - 3)):
        result = find_center(compact_source[start:], analyzer.state.ma34, trend_direction=daily_segment.direction)
        if result and round(result["low"], 3) == 8.466 and round(result["high"], 3) == 8.629:
            target_result = result
            break

    assert target_result is not None
    repair = analyzer._candidate_owner_chain_repair(target_result, full_source, daily_segment.direction)

    assert repair is not None
    assert repair["source_segments_kind"] == "owner_chain_repair"
    assert repair["repair_reason"] == "missing_continuous_owner_segment_for_badc"
    assert [owner_label(key) for key in repair["owner_chain"]] == ["mV10B", "mV11T", "mV14B", "mV19T"]
    assert [(label(seg.start_k), label(seg.end_k)) for seg in repair["refined_segments"]] == [("mV14B", "mV19T")]

    raw_30f_spans = {(label(seg.start_k), label(seg.end_k)) for seg in engine.segments}
    assert ("mV14B", "mV19T") not in raw_30f_spans
    assert ("mV14B", "mV19T") not in {(label(seg.start_k), label(seg.end_k)) for seg in analyzer.refined_segments}


def test_regression_603178_source_non_same_proposal_builds_v28_to_v35_when_region_is_in_source():
    engine = make_603178_engine()
    label, _ = make_visible_labelers(engine)
    analyzer = engine.daily_segment_analyzer
    source = [
        seg
        for seg in engine.segments
        if label(seg.start_k) in {"mV24T", "mV25B", "mV26T", "mV27B", "mV28T", "mV29B", "mV30T", "mV31B", "mV32T", "mV35B", "mV40T", "mV41B", "mV42T"}
    ]

    target_result = None
    for start in range(max(0, len(source) - 3)):
        result = find_center(source[start:], analyzer.state.ma34, trend_direction=Direction.Down)
        if result and round(result["low"], 3) == 7.561 and round(result["high"], 3) == 8.699:
            target_result = result
            break

    assert target_result is not None
    proposals = analyzer._build_owner_chain_repair_proposals(source, target_result, Direction.Down)

    assert [(label(p.refined_segment.start_k), label(p.refined_segment.end_k)) for p in proposals] == [("mV28T", "mV35B")]
    promoted = proposals[0].promoted_result
    assert promoted["overlap_type"] == 3
    assert promoted["status"] == "FINAL"
    assert proposals[0].refined_segment.cache["repair_reason"] == "daily_center_source_non_same"


def test_regression_603178_unfrozen_tail_extends_and_pending_stays_open_ended():
    engine = make_603178_engine()
    label, _ = make_visible_labelers(engine)

    daily_pairs = [(label(ds.start_seg.start_k), label(ds.end_seg.end_k)) for ds in engine.daily_segments]
    assert daily_pairs == [("mV2T", "mV25B")]
    assert engine.daily_segments[0].cache["extended_from_unfrozen_end"] is True
    assert engine.daily_segments[0].cache["end_state"] == "extendable_end"

    pending = engine.daily_pending_segments[0]
    assert (label(pending.start_seg.start_k), label(pending.end_seg.end_k)) == ("mV25B", "mV28T")
    assert pending.direction == Direction.Up
    assert pending.cache["open_ended"] is True
    assert (label(pending.segments[3].start_k), label(pending.segments[7].end_k)) == ("mV28T", "mV35B")

    refined_spans = {(label(seg.start_k), label(seg.end_k), seg.cache.get("repair_reason")) for seg in engine.daily_refined_segments}
    assert ("mV28T", "mV35B", "daily_center_source_non_same") in refined_spans

    repaired_center = next(c for c in engine.daily_pending_centers if c.cache.get("repair_reason") == "daily_center_source_non_same")
    assert repaired_center.overlap_type == 3
    assert repaired_center.status == "FINAL"
    assert repaired_center.cache["construction_direction"] == Direction.Up.value
    assert round(repaired_center.low, 3) == 8.589
    assert round(repaired_center.high, 3) == 8.699


def test_regression_603178_default_centers_use_construction_time_source_after_tail_extension():
    bars = research.get_raw_bars_origin("603178", sdt="20171015", edt="20211101")
    if not bars:
        pytest.skip("no bars for 603178")

    engine = MooreCZSC(
        bars,
        ma34_cross_as_valid_gate=True,
        ma34_cross_expand_one_k=False,
        audit_link_rounds=3,
        enable_pre_round=True,
        replay_centers_after_macro_swallow=False,
    )
    label, _ = make_visible_labelers(engine)

    daily_pairs = [(label(ds.start_seg.start_k), label(ds.end_seg.end_k)) for ds in engine.daily_segments]
    assert daily_pairs == [("mV2T", "mV25B")]
    assert engine.daily_segments[0].cache["extended_from_unfrozen_end"] is True
    assert engine.daily_centers
    assert all(c.cache.get("source") == "daily_segment_construction" for c in engine.daily_centers)
    assert len(engine.daily_centers) >= 3
    center_spans = {(label(c.segments[0].start_k), label(c.segments[-1].end_k)) for c in engine.daily_centers}
    assert {("mV9B", "mV16T"), ("mV16T", "mV25B")} <= center_spans

    pending = engine.daily_pending_segments[0]
    assert (label(pending.start_seg.start_k), label(pending.end_seg.end_k)) == ("mV25B", "mV28T")
    assert pending.cache["open_ended"] is True
    refined_spans = {(label(seg.start_k), label(seg.end_k), seg.cache.get("repair_reason")) for seg in engine.daily_refined_segments}
    assert ("mV28T", "mV35B", "daily_center_source_non_same") in refined_spans

    repaired_center = next(c for c in engine.daily_pending_centers if c.cache.get("repair_reason") == "daily_center_source_non_same")
    assert repaired_center.cache["source"] == "daily_segment_pending_construction"
    assert round(repaired_center.low, 3) == 8.589
    assert round(repaired_center.high, 3) == 8.699


def test_regression_300339_source_repair_does_not_create_same_mark_refined_segment():
    bars = research.get_raw_bars_origin("300339", sdt="20150415", edt="20210701")
    if not bars:
        pytest.skip("no bars for 300339")

    engine = MooreCZSC(
        bars,
        ma34_cross_as_valid_gate=True,
        ma34_cross_expand_one_k=False,
        audit_link_rounds=3,
        enable_pre_round=True,
        replay_centers_after_macro_swallow=False,
        rebuild_daily_centers_after_segment_change=True,
    )
    label, _ = make_visible_labelers(engine)

    refined_spans = {(label(seg.start_k), label(seg.end_k)) for seg in engine.daily_refined_segments}
    assert ("mV34B", "mV36B") not in refined_spans
    assert all(seg.start_k.mark != seg.end_k.mark for seg in engine.daily_refined_segments)


def test_regression_300339_daily_segments_split_on_confirmed_independence():
    bars = research.get_raw_bars_origin("300339", sdt="20150415", edt="20210701")
    if not bars:
        pytest.skip("no bars for 300339")

    engine = MooreCZSC(
        bars,
        ma34_cross_as_valid_gate=True,
        ma34_cross_expand_one_k=False,
        audit_link_rounds=3,
        enable_pre_round=True,
        replay_centers_after_macro_swallow=False,
        rebuild_daily_centers_after_segment_change=True,
    )
    label, _ = make_visible_labelers(engine)

    daily_pairs = [(label(ds.start_seg.start_k), label(ds.end_seg.end_k)) for ds in engine.daily_segments]
    assert daily_pairs[:4] == [("mV4B", "mV7T"), ("mV7T", "mV24B"), ("mV24B", "mV27T"), ("mV27T", "mV38B")]
    assert engine.daily_segments[0].cache["independence_kind"] == "no_daily_center"
    assert engine.daily_segments[1].cache["independence_kind"] == "third_buy_sell"
    assert engine.daily_segments[1].cache["center_kind"] == "turning"
    assert engine.daily_segments[2].cache["independence_kind"] == "no_daily_center"
    assert engine.daily_segments[3].cache["independence_kind"] == "third_buy_sell"
    assert engine.daily_segments[3].cache["center_kind"] == "turning"

    center_spans = {(label(c.segments[0].start_k), label(c.segments[-1].end_k), c.overlap_type) for c in engine.daily_centers}
    assert ("mV11T", "mV22B", 3) not in center_spans
    assert ("mV12B", "mV21T", 3) in center_spans
    assert ("mV8B", "mV12B", 1) in center_spans


def test_regression_603020_tail_extension_yields_to_reverse_independence():
    bars = research.get_raw_bars_origin("603020", sdt="20150515", edt="20210801")
    if not bars:
        pytest.skip("no bars for 603020")

    engine = MooreCZSC(
        bars,
        ma34_cross_as_valid_gate=True,
        ma34_cross_expand_one_k=False,
        audit_link_rounds=3,
        enable_pre_round=True,
        replay_centers_after_macro_swallow=False,
        rebuild_daily_centers_after_segment_change=True,
    )
    label, _ = make_visible_labelers(engine)

    daily_pairs = [(label(ds.start_seg.start_k), label(ds.end_seg.end_k)) for ds in engine.daily_segments]
    assert daily_pairs == [("mV2T", "mV21B"), ("mV21B", "mV38T"), ("mV38T", "mV45B")]

    assert engine.daily_segments[1].cache["independence_kind"] == "no_daily_center"
    assert engine.daily_segments[1].cache["extended_from_unfrozen_end"] is True
    assert engine.daily_segments[2].cache["independence_kind"] == "third_buy_sell"
    assert engine.daily_segments[2].cache["center_kind"] == "turning"

    pending_pairs = [(label(ds.start_seg.start_k), label(ds.end_seg.end_k)) for ds in engine.daily_pending_segments]
    assert pending_pairs[:1] == [("mV45B", "mV48T")]


def test_regression_300311_rejects_type3_with_invalid_owner_chain():
    bars = research.get_raw_bars_origin("300311", sdt="20170115", edt="20210801")
    if not bars:
        pytest.skip("no bars for 300311")

    engine = MooreCZSC(
        bars,
        ma34_cross_as_valid_gate=True,
        ma34_cross_expand_one_k=False,
        audit_link_rounds=3,
        enable_pre_round=True,
        replay_centers_after_macro_swallow=False,
        rebuild_daily_centers_after_segment_change=True,
    )
    label, _ = make_visible_labelers(engine)

    refined_spans = {(label(seg.start_k), label(seg.end_k)) for seg in engine.daily_refined_segments}
    assert ("mV24B", "mV27T") not in refined_spans

    center_spans = {
        (label(center.segments[0].start_k), label(center.segments[-1].end_k), center.overlap_type)
        for center in engine.daily_centers
    }
    assert ("mV23T", "mV28B", 3) not in center_spans
    assert all(center.cache["owner_chain_valid"] for center in engine.daily_centers if center.overlap_type == 3)


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
    spans = daily_date_spans(engine)
    assert spans == [
        ("2016-11-28", "2018-02-09", "Down"),
        ("2018-02-09", "2018-04-11", "Up"),
        ("2018-04-11", "2018-10-19", "Down"),
        ("2018-10-19", "2019-04-22", "Up"),
        ("2019-04-22", "2020-02-04", "Down"),
        ("2020-02-04", "2020-07-10", "Up"),
    ]
    assert non_same_date_spans(engine) == []
    assert pending_date_spans(engine)[:1] == [("2020-07-10", "2021-02-10", "Down")]
    assert engine.daily_segments[0].cache["independence_kind"] == "swallow_one_segment"
