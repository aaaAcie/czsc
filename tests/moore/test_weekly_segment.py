# -*- coding: utf-8 -*-
import pytest

from czsc.connectors import research
from czsc.moore.analyze import MooreCZSC
from czsc.moore.daily_segment import DailySegment, DailySegmentCenter
from czsc.moore.weekly_segment import WeeklySegmentAnalyzer
from czsc.py.enum import Direction

from tests.moore.test_daily_segment import make_seg, make_visible_labelers


def make_daily_segment(seg):
    return DailySegment(
        symbol=seg.symbol,
        direction=seg.direction,
        start_seg=seg,
        end_seg=seg,
        segments=[seg],
    )


def make_daily_segment_from_segments(segments):
    return DailySegment(
        symbol=segments[0].symbol,
        direction=segments[0].direction,
        start_seg=segments[0],
        end_seg=segments[-1],
        segments=list(segments),
    )


def make_ma170(values: dict[int, float], size: int = 80):
    ma170 = [11.0] * size
    for idx, value in values.items():
        ma170[idx] = value
    return ma170


def test_regular_weekly_segment_uses_three_daily_segments_and_ma170_center():
    daily_segments = [
        make_daily_segment(make_seg(0, 10, Direction.Up, 10, 14)),
        make_daily_segment(make_seg(10, 20, Direction.Down, 14, 12)),
        make_daily_segment(make_seg(20, 30, Direction.Up, 12, 16)),
    ]
    ma170 = make_ma170({4: 12, 5: 13, 6: 12, 14: 10, 15: 9, 16: 10})

    analyzer = WeeklySegmentAnalyzer(daily_segments, daily_centers=[], ma170=ma170)

    assert len(analyzer.weekly_segments) == 1
    weekly = analyzer.weekly_segments[0]
    assert weekly.segments == daily_segments
    assert weekly.cache["candidate_kind"] == "regular"
    assert len(weekly.centers) == 1
    assert weekly.centers[0].overlap_type == 1
    assert weekly.centers[0].cache["source_algorithm"] == "weekly_three_segment_ba_on_ma170"
    assert weekly.centers[0].low == 9
    assert weekly.centers[0].high == 13


def test_weekly_non_same_uses_one_or_two_daily_segments_with_three_internal_centers():
    source_segments = [
        make_seg(0, 10, Direction.Up, 10, 14),
        make_seg(10, 20, Direction.Down, 14, 12),
        make_seg(20, 30, Direction.Up, 12, 16),
    ]
    daily_segments = [
        make_daily_segment_from_segments(source_segments[:2]),
        make_daily_segment_from_segments(source_segments[2:]),
    ]
    centers = [
        DailySegmentCenter(
            segments=[source_segments[i]],
            high=12 + i,
            low=10 + i,
            overlap_type=1,
            points={"A": (i + 1, 10 + i), "B": (i + 2, 12 + i)},
            cache={"center_kind": "trend_class", "identity_key": ("c", i)},
        )
        for i in range(3)
    ]

    analyzer = WeeklySegmentAnalyzer(daily_segments, daily_centers=centers, ma170=make_ma170({}))

    assert len(analyzer.weekly_segments) == 1
    weekly = analyzer.weekly_segments[0]
    assert weekly.cache["candidate_kind"] == "non_same"
    assert analyzer.weekly_non_same_segments == [weekly]
    assert weekly.segments == daily_segments
    assert weekly.centers[0].low == centers[0].low
    assert weekly.centers[0].high == centers[0].high
    assert weekly.centers[0].cache["source_rails"] == "first_daily_trend_center"


def test_weekly_non_same_requires_later_center_to_return_to_first_center_range():
    source_segments = [
        make_seg(0, 10, Direction.Up, 10, 14),
        make_seg(10, 20, Direction.Down, 14, 12),
        make_seg(20, 30, Direction.Up, 12, 16),
    ]
    daily_segments = [
        make_daily_segment_from_segments(source_segments[:2]),
        make_daily_segment_from_segments(source_segments[2:]),
    ]
    centers = [
        DailySegmentCenter(
            segments=[source_segments[0]],
            high=12,
            low=10,
            overlap_type=1,
            points={"A": (1, 10), "B": (2, 12)},
            cache={"center_kind": "trend_class", "identity_key": ("c", 0)},
        ),
        DailySegmentCenter(
            segments=[source_segments[1]],
            high=15,
            low=12,
            overlap_type=1,
            points={"A": (11, 12), "B": (12, 15)},
            cache={"center_kind": "trend_class", "identity_key": ("c", 1)},
        ),
        DailySegmentCenter(
            segments=[source_segments[2]],
            high=17,
            low=15,
            overlap_type=1,
            points={"A": (21, 15), "B": (22, 17)},
            cache={"center_kind": "trend_class", "identity_key": ("c", 2)},
        ),
    ]

    analyzer = WeeklySegmentAnalyzer(daily_segments, daily_centers=centers, ma170=make_ma170({}))

    assert analyzer.weekly_non_same_segments == []
    assert analyzer.weekly_segments == []


def test_weekly_non_same_ignores_source_segments_trend_relationship():
    source_segments = [
        make_seg(0, 10, Direction.Up, 10, 14),
        make_seg(10, 20, Direction.Down, 14, 12),
        make_seg(20, 30, Direction.Up, 12, 13),
    ]
    daily_segments = [
        make_daily_segment_from_segments(source_segments[:2]),
        make_daily_segment_from_segments(source_segments[2:]),
    ]
    centers = [
        DailySegmentCenter(
            segments=[source_segments[i]],
            high=12 + i,
            low=10 + i,
            overlap_type=1,
            points={"A": (i + 1, 10 + i), "B": (i + 2, 12 + i)},
            cache={"center_kind": "trend_class", "identity_key": ("c", i)},
        )
        for i in range(3)
    ]

    analyzer = WeeklySegmentAnalyzer(daily_segments, daily_centers=centers, ma170=make_ma170({}))

    assert len(analyzer.weekly_non_same_segments) == 1
    weekly = analyzer.weekly_non_same_segments[0]
    assert weekly.cache["candidate_kind"] == "non_same"
    assert weekly.segments == daily_segments
    assert weekly.centers[0].cache["source_rails"] == "first_daily_trend_center"


def test_weekly_non_same_ignores_turning_centers():
    daily_segments = [
        make_daily_segment(make_seg(0, 10, Direction.Up, 10, 14)),
        make_daily_segment(make_seg(10, 20, Direction.Down, 14, 12)),
    ]
    turning_centers = [
        DailySegmentCenter(
            segments=[daily_segments[0].segments[0]],
            high=12 + i,
            low=10 + i,
            overlap_type=0,
            points={"A": (i + 1, 10 + i), "B": (i + 2, 12 + i)},
            cache={"center_kind": "turning", "identity_key": ("t", i)},
        )
        for i in range(3)
    ]

    analyzer = WeeklySegmentAnalyzer(daily_segments, daily_centers=turning_centers, ma170=make_ma170({}))

    assert analyzer.weekly_non_same_segments == []
    assert analyzer.weekly_segments == []
    assert analyzer.weekly_pending_segments == []


def test_weekly_does_not_publish_pending_tail_with_fewer_than_three_daily_segments():
    daily_segments = [
        make_daily_segment(make_seg(0, 10, Direction.Up, 10, 14)),
        make_daily_segment(make_seg(10, 20, Direction.Down, 14, 12)),
    ]

    analyzer = WeeklySegmentAnalyzer(daily_segments, daily_centers=[], ma170=make_ma170({}))

    assert analyzer.weekly_segments == []
    assert analyzer.weekly_pending_segments == []


def test_regression_603020_first_three_daily_segments_form_weekly_segment():
    bars = research.get_raw_bars_origin("603020", sdt="20151215", edt="20210801")
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
    assert daily_pairs[:3] == [("mV11B", "mV16T"), ("mV16T", "mV25B"), ("mV25B", "mV28T")]

    weekly_pairs = [(label(ws.start_seg.start_seg.start_k), label(ws.end_seg.end_seg.end_k)) for ws in engine.weekly_segments]
    assert weekly_pairs[:1] == [("mV11B", "mV28T")]
    assert engine.weekly_segments[0].segments == engine.daily_segments[:3]
    assert engine.weekly_centers


def test_regression_002612_skips_non_same_when_later_regular_trend_window_exists():
    bars = research.get_raw_bars_origin("002612", sdt="20100101", edt="20210701")
    if not bars:
        pytest.skip("no bars for 002612")

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

    assert engine.weekly_non_same_segments == []
    assert len(engine.weekly_segments) == 1

    weekly = engine.weekly_segments[0]
    assert weekly.cache["candidate_kind"] == "regular"
    assert (label(weekly.start_seg.start_seg.start_k), label(weekly.end_seg.end_seg.end_k)) == ("mV20T", "mV53B")
    assert [label(segment.start_seg.start_k) for segment in weekly.segments] == ["mV20T", "mV35B", "mV40T"]
    assert weekly.centers


def test_regression_603908_weekly_skips_invalid_start_until_trend_window():
    bars = research.get_raw_bars_origin("603908", sdt="20170301", edt="20221001")
    if not bars:
        pytest.skip("no bars for 603908")

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

    assert engine.weekly_pending_segments == []
    assert len(engine.weekly_segments) == 1
    weekly = engine.weekly_segments[0]
    assert (label(weekly.start_seg.start_seg.start_k), label(weekly.end_seg.end_seg.end_k)) == ("mV10T", "mV25B")
    assert [label(segment.start_seg.start_k) for segment in weekly.segments] == ["mV10T", "mV17B", "mV22T"]
    assert len(weekly.centers) == 1

    center = weekly.centers[0]
    assert center.status == "FINAL"
    assert center.cache["source"] == "weekly_segment_regular"
    assert center.overlap_type == 1
    assert round(center.low, 3) == 13.933
    assert round(center.high, 3) == 15.990
