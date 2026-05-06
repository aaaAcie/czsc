# -*- coding: utf-8 -*-
from datetime import datetime, timedelta

from czsc.moore.analyze import MooreCZSC
from czsc.moore.daily_segment import DailySegmentAnalyzer
from czsc.moore.daily_segment.center_algo import find_b_point, find_d_point
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


def make_seg(start_idx: int, end_idx: int, direction: Direction, start_price: float, end_price: float) -> MooreSegment:
    start_bar = make_bar(start_idx, start_price)
    end_bar = make_bar(end_idx, end_price)
    start_mark = Mark.D if direction == Direction.Up else Mark.G
    end_mark = Mark.G if direction == Direction.Up else Mark.D
    start_tk = TurningK(
        symbol="TEST.DAILY",
        dt=start_bar.dt,
        raw_bar=start_bar,
        mark=start_mark,
        price=start_price,
        k_index=start_idx,
    )
    end_tk = TurningK(
        symbol="TEST.DAILY",
        dt=end_bar.dt,
        raw_bar=end_bar,
        mark=end_mark,
        price=end_price,
        k_index=end_idx,
    )
    bars = [make_bar(i, start_price + (end_price - start_price) * (i - start_idx) / max(end_idx - start_idx, 1)) for i in range(start_idx, end_idx + 1)]
    return MooreSegment(
        symbol="TEST.DAILY",
        start_k=start_tk,
        end_k=end_tk,
        direction=direction,
        bars=bars,
    )


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


def test_swallow_segment_direct_commit_and_moore_aliases():
    swallow = make_seg(30, 33, Direction.Up, 10, 15)
    swallow.cache["is_macro_swallow"] = True

    analyzer = DailySegmentAnalyzer([swallow])
    assert len(analyzer.daily_segments) == 1
    assert analyzer.daily_segments[0].segments == [swallow]
    assert analyzer.daily_segments[0].cache["from_macro_swallow"] is True
    assert analyzer.current_segments == []

    engine = MooreCZSC([])
    engine.daily_segment_analyzer.update([swallow])
    assert engine.daily_segments == engine.higher_segments
    assert engine.daily_active_center == engine.higher_active_center
