# -*- coding: utf-8 -*-
import pytest

from czsc.connectors import research
from czsc.moore.analyze import MooreCZSC


def _safe_get_bars(symbol: str, sdt: str, edt: str):
    bars = research.get_raw_bars_origin(symbol, sdt=sdt, edt=edt)
    if not bars:
        pytest.skip(f"no bars for {symbol}")
    return bars


def test_delayed_judgement_chain_300490():
    bars = _safe_get_bars("300490", "20160115", "20210701")
    engine = MooreCZSC(
        bars,
        ma34_cross_as_valid_gate=True,
        audit_link_rounds=3,
        enable_pre_round=True,
        replay_centers_after_macro_swallow=False,
    )
    s = engine.segment_analyzer.state

    assert any(ev.get("event") == "enqueue" for ev in s.debug_judgement_events)
    assert any(ev.get("event") == "anchor_real" for ev in s.debug_judgement_events)
    assert any(node.stage == "resolved" for node in s.judgement_nodes.values())


def test_delayed_judgement_has_parent_child_dependency():
    bars = _safe_get_bars("300490", "20160115", "20210701")
    engine = MooreCZSC(
        bars,
        ma34_cross_as_valid_gate=True,
        audit_link_rounds=3,
        enable_pre_round=True,
        replay_centers_after_macro_swallow=False,
    )
    nodes = list(engine.segment_analyzer.state.judgement_nodes.values())
    assert any(node.parent_id is not None for node in nodes), "expected at least one child node with parent_id"


def test_regression_key_turnings_300371():
    bars = _safe_get_bars("300371", "20181220", "20201030")
    engine = MooreCZSC(
        bars,
        ma34_cross_as_valid_gate=True,
        audit_link_rounds=3,
        replay_centers_after_macro_swallow=False,
    )
    dates = [tk.dt.strftime("%Y-%m-%d") for tk in engine.micro_turning_ks]
    for d in ("2019-09-11", "2019-11-29", "2020-01-03"):
        assert d in dates

    events = engine.segment_analyzer.state.debug_judgement_events
    assert any(ev.get("event") == "enqueue" and ev.get("dt").strftime("%Y-%m-%d") == "2020-01-15" for ev in events)
    assert any(
        ev.get("event") == "resolved"
        and ev.get("resolution") == "rollback_base"
        and ev.get("dt").strftime("%Y-%m-%d") == "2020-02-04"
        for ev in events
    )
    assert "2020-01-15" not in dates


def test_regression_center_5k_leavek_300339():
    bars = _safe_get_bars("300339", "20181201", "20190430")
    engine = MooreCZSC(
        bars,
        ma34_cross_as_valid_gate=True,
        audit_link_rounds=3,
        replay_centers_after_macro_swallow=False,
    )
    hit = [
        c for c in engine.micro_centers
        if c.anchor_k0
        and c.confirm_k
        and c.anchor_k0.dt.strftime("%Y-%m-%d") == "2019-02-20"
        and c.confirm_k.dt.strftime("%Y-%m-%d") == "2019-02-21"
        and c.end_dt.strftime("%Y-%m-%d") == "2019-02-25"
        and c.method == "5K重叠"
    ]
    assert hit, "expected 2019-02-20/02-21 center to include leave-K date 2019-02-25"
