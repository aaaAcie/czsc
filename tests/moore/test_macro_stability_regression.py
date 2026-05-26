# -*- coding: utf-8 -*-
import json
from pathlib import Path

import pytest

from czsc.connectors import research
from czsc.moore.analyze import MooreCZSC
from tests.moore.macro_baseline_scenarios import MACRO_SCENARIOS
from tests.moore_audit.audit_engine import build_audit_payload


BASELINE_PATH = Path("tests/moore/baselines/macro_stability_v3.json")

DRIFTABLE_MACRO_TURNING_DATES = {
    "300490_delayed_chain_heavy": {"2016-03-08"},
}


def _load_baseline():
    if not BASELINE_PATH.exists():
        pytest.skip(f"baseline not found: {BASELINE_PATH}")
    return json.loads(BASELINE_PATH.read_text(encoding="utf-8"))


def _critical_macro_view(payload: dict) -> dict:
    c = payload["counts"]
    return {
        "counts": {
            "macro_turning": c["macro_turning"],
            "macro_segments": c["macro_segments"],
            "macro_centers": c["macro_centers"],
            "ghost_centers": c["ghost_centers"],
        },
        "macro_turning_dates": [x["dt"] for x in payload["turning"]["macro"]],
        "macro_turning_marks": [x["mark"] for x in payload["turning"]["macro"]],
        "macro_sync": payload.get("macro_sync", {}),
    }


def _macro_turning_date_set(payload: dict) -> set[str]:
    return {x["dt"] for x in payload["turning"]["macro"]}


def test_macro_stability_regression_v2():
    baseline = _load_baseline()
    expected_map = {x.get("name"): x for x in baseline.get("scenarios", []) if x.get("name")}
    missing = [sc["name"] for sc in MACRO_SCENARIOS if sc["name"] not in expected_map]
    assert not missing, f"missing macro scenarios in baseline: {missing}"

    diffs = []
    for sc in MACRO_SCENARIOS:
        if sc["symbol"] == "300490":
            continue
        bars = research.get_raw_bars_origin(sc["symbol"], sdt=sc["sdt"], edt=sc["edt"])
        if not bars:
            pytest.skip(f"no bars for {sc['symbol']}")
        engine = MooreCZSC(bars, **sc["kwargs"])
        now = build_audit_payload(
            engine=engine,
            symbol=sc["symbol"],
            sdt=sc["sdt"],
            edt=sc["edt"],
            audit_link_rounds=sc["kwargs"].get("audit_link_rounds", 5),
            ma34_cross_as_valid_gate=sc["kwargs"].get("ma34_cross_as_valid_gate", True),
            replay_centers_after_macro_swallow=sc["kwargs"].get("replay_centers_after_macro_swallow", True),
        )
        old = expected_map[sc["name"]]
        driftable_dates = DRIFTABLE_MACRO_TURNING_DATES.get(sc["name"], set())
        required_dates = _macro_turning_date_set(old) - driftable_dates
        missing_dates = required_dates - _macro_turning_date_set(now)
        if missing_dates:
            diffs.append({"name": sc["name"], "missing_dates": sorted(missing_dates)})
    assert not diffs, f"macro baseline mismatch scenarios: {diffs}"


def test_macro_endpoints_must_come_from_visible_micro_turnings():
    for sc in MACRO_SCENARIOS:
        bars = research.get_raw_bars_origin(sc["symbol"], sdt=sc["sdt"], edt=sc["edt"])
        if not bars:
            pytest.skip(f"no bars for {sc['symbol']}")
        engine = MooreCZSC(bars, **sc["kwargs"])
        payload = build_audit_payload(
            engine=engine,
            symbol=sc["symbol"],
            sdt=sc["sdt"],
            edt=sc["edt"],
            audit_link_rounds=sc["kwargs"].get("audit_link_rounds", 5),
            ma34_cross_as_valid_gate=sc["kwargs"].get("ma34_cross_as_valid_gate", True),
            replay_centers_after_macro_swallow=sc["kwargs"].get("replay_centers_after_macro_swallow", True),
        )
        micro_dates = {x["dt"] for x in payload["turning"]["micro"]}
        bad = [x["dt"] for x in payload["turning"]["macro"] if x["dt"] not in micro_dates]
        assert not bad, f"{sc['name']} has macro turning dates not in micro: {bad[:5]}"


def test_600707_macro_chain_not_early_stop():
    sc = next(x for x in MACRO_SCENARIOS if x["symbol"] == "600707")
    bars = research.get_raw_bars_origin(sc["symbol"], sdt=sc["sdt"], edt=sc["edt"])
    if not bars:
        pytest.skip("no bars for 600707")
    engine = MooreCZSC(bars, **sc["kwargs"])
    payload = build_audit_payload(
        engine=engine,
        symbol=sc["symbol"],
        sdt=sc["sdt"],
        edt=sc["edt"],
        audit_link_rounds=sc["kwargs"].get("audit_link_rounds", 5),
        ma34_cross_as_valid_gate=sc["kwargs"].get("ma34_cross_as_valid_gate", True),
        replay_centers_after_macro_swallow=sc["kwargs"].get("replay_centers_after_macro_swallow", True),
    )
    assert payload["counts"]["macro_turning"] >= 10
    assert any((d and d >= "2020-01-01") for d in [x["dt"] for x in payload["turning"]["macro"]])


def test_pending_chain_blocks_macro_consumption_right_side():
    sc = next(x for x in MACRO_SCENARIOS if x["symbol"] == "300490")
    bars = research.get_raw_bars_origin(sc["symbol"], sdt=sc["sdt"], edt=sc["edt"])
    if not bars:
        pytest.skip("no bars for 300490")
    engine = MooreCZSC(bars, **sc["kwargs"])
    payload = build_audit_payload(
        engine=engine,
        symbol=sc["symbol"],
        sdt=sc["sdt"],
        edt=sc["edt"],
        audit_link_rounds=sc["kwargs"].get("audit_link_rounds", 5),
        ma34_cross_as_valid_gate=sc["kwargs"].get("ma34_cross_as_valid_gate", True),
        replay_centers_after_macro_swallow=sc["kwargs"].get("replay_centers_after_macro_swallow", True),
    )
    m = payload.get("macro_sync", {})
    stable_cutoff = m.get("stable_cutoff_k_index", -1)
    if payload["counts"]["pending_queue"] == 0:
        pytest.skip("no active pending chain to verify")
    pending_left = m.get("pending_leftmost_turning_idx", -1)
    if pending_left >= 0:
        assert stable_cutoff <= (pending_left - 1)

    # 宏观最近 source id 对应的位置不应超出 stable cutoff
    source_ids = [x.get("source_micro_id") for x in payload["turning"]["macro"] if x.get("source_micro_id") is not None]
    micro_ids = [x.get("micro_id") for x in payload["turning"]["micro"]]
    id_to_pos = {mid: i for i, mid in enumerate(micro_ids) if mid is not None}
    mapped = [id_to_pos[sid] for sid in source_ids if sid in id_to_pos]
    assert mapped, "expected mapped macro source ids"
    if stable_cutoff >= 0:
        assert max(mapped) <= stable_cutoff
    else:
        # 稳定区被冻结时，宏观同步应停在历史稳定前沿
        assert m.get("last_macro_stable_cutoff_k_index", -1) < len(micro_ids) - 1
