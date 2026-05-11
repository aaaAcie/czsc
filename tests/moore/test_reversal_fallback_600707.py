# -*- coding: utf-8 -*-
import pytest

from czsc.connectors import research
from czsc.moore.analyze import MooreCZSC
from czsc.moore.segment.micro_engine import MicroStructureEngine


def _safe_get_bars(symbol: str, sdt: str, edt: str):
    bars = research.get_raw_bars_origin(symbol, sdt=sdt, edt=edt)
    if not bars:
        pytest.skip(f"no bars for {symbol}")
    return bars


def test_600707_trigger_20190104_uses_extreme_20190103():
    bars = _safe_get_bars("600707", "20140601", "20210820")
    captured = {}
    orig = MicroStructureEngine._process_confirmed_trigger

    def wrapped(self, trigger_bar, trigger_index, new_mark, *args, **kwargs):
        d = trigger_bar.dt.strftime("%Y-%m-%d")
        if d == "2019-01-04" and self.s.turning_ks and self.s.turning_ks[-1].mark.name == "G":
            last = self.s.turning_ks[-1]
            search_start = max(last.k_index + 2, (last.turning_k_index if last.turning_k_index is not None else last.k_index) + 1) + 1
            price, idx = self._extreme_locator.locate_reversal_extreme_by_trigger_rule(new_mark, search_start, trigger_index)
            captured["dt"] = self.s.bars_raw[idx].dt.strftime("%Y-%m-%d")
            captured["price"] = price
        return orig(self, trigger_bar, trigger_index, new_mark, *args, **kwargs)

    MicroStructureEngine._process_confirmed_trigger = wrapped
    try:
        _ = MooreCZSC(
            bars,
            ma34_cross_as_valid_gate=True,
            audit_link_rounds=3,
            enable_pre_round=True,
            replay_centers_after_macro_swallow=False,
        )
    finally:
        MicroStructureEngine._process_confirmed_trigger = orig

    assert captured.get("dt") == "2019-01-03"


def test_600707_has_c_20190201_and_d_candidate_20190307():
    bars = _safe_get_bars("600707", "20140601", "20190630")
    engine = MooreCZSC(
        bars,
        ma34_cross_as_valid_gate=True,
        audit_link_rounds=3,
        enable_pre_round=True,
        replay_centers_after_macro_swallow=False,
    )
    dates = [tk.dt.strftime("%Y-%m-%d") for tk in engine.micro_turning_ks]
    assert "2019-02-01" in dates

    captured = {}
    orig = MicroStructureEngine._process_confirmed_trigger

    def wrapped(self, trigger_bar, trigger_index, new_mark, *args, **kwargs):
        d = trigger_bar.dt.strftime("%Y-%m-%d")
        if d == "2019-03-08" and self.s.turning_ks and self.s.turning_ks[-1].dt.strftime("%Y-%m-%d") == "2019-02-01":
            last = self.s.turning_ks[-1]
            search_start = max(last.k_index + 2, (last.turning_k_index if last.turning_k_index is not None else last.k_index) + 1) + 1
            price, idx = self._extreme_locator.locate_reversal_extreme_by_trigger_rule(new_mark, search_start, trigger_index)
            captured["dt"] = self.s.bars_raw[idx].dt.strftime("%Y-%m-%d")
            captured["price"] = price
        return orig(self, trigger_bar, trigger_index, new_mark, *args, **kwargs)

    MicroStructureEngine._process_confirmed_trigger = wrapped
    try:
        _ = MooreCZSC(
            bars,
            ma34_cross_as_valid_gate=True,
            audit_link_rounds=3,
            enable_pre_round=True,
            replay_centers_after_macro_swallow=False,
        )
    finally:
        MicroStructureEngine._process_confirmed_trigger = orig

    assert captured.get("dt") == "2019-03-07"
