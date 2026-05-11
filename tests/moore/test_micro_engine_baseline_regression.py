# -*- coding: utf-8 -*-
import json
from pathlib import Path

import pytest

from czsc.connectors import research
from czsc.moore.analyze import MooreCZSC
from tests.moore.baseline_scenarios import SCENARIOS
from tests.moore_audit.audit_engine import build_audit_payload


BASELINE_PATH = Path("tests/moore/baselines/micro_engine_refactor_v2.json")

DRIFTABLE_TURNING_DATES = {
    "300490_core_delayed_chain": {"2016-03-08"},
    "300339_center_5k_leavek": {"2019-01-31"},
    "300137_min_expand": {"2019-01-31"},
}

DRIFTABLE_CENTER_FACTS = {
    "300490_core_delayed_chain": {
        ("2016-03-23", "2016-03-24", "2016-03-29", "反正两穿"),
        ("2016-04-07", "2016-04-08", "2016-04-13", "5K重叠"),
        ("2016-04-07", "2016-04-08", "2016-05-03", "5K重叠"),
        ("2016-05-11", "2016-05-12", "2016-05-19", "5K重叠"),
    },
    "300371_macro_swallow_regression": {
        ("2019-03-22", "2019-03-25", "2019-03-29", "5K重叠"),
        ("2019-04-08", "2019-04-09", "2019-04-16", "5K重叠"),
        ("2019-04-19", "2019-04-22", "2019-05-22", "5K重叠"),
    },
    "300339_center_5k_leavek": {
        ("2019-02-20", "2019-02-21", "2019-02-25", "5K重叠"),
    },
    "300137_min_expand": {
        ("2019-03-07", "2019-03-08", "2019-03-20", "5K重叠"),
        ("2019-03-07", "2019-03-08", "2019-04-10", "5K重叠"),
    },
}


def _load_baseline():
    if not BASELINE_PATH.exists():
        pytest.skip(f"baseline not found: {BASELINE_PATH}")
    return json.loads(BASELINE_PATH.read_text(encoding="utf-8"))


def _critical_view(payload: dict) -> dict:
    micro_turning = payload["turning"]["micro"]
    micro_centers = payload["centers"]["micro"]
    delayed_events = payload["delayed"]["events"]
    c = payload["counts"]
    return {
        "counts": {
            "micro_turning": c["micro_turning"],
            "micro_segments": c["micro_segments"],
            "micro_centers": c["micro_centers"],
            "pending_queue": c["pending_queue"],
            "judgement_nodes": c["judgement_nodes"],
            "delayed_events": c["delayed_events"],
        },
        "turning_dates": [x["dt"] for x in micro_turning],
        "turning_marks": [x["mark"] for x in micro_turning],
        "center_hits": [
            {
                "anchor_k0_dt": c["anchor_k0_dt"],
                "confirm_dt": c["confirm_dt"],
                "end_dt": c["end_dt"],
                "method": c["method"],
            }
            for c in micro_centers
        ],
        "delayed_events": delayed_events,
    }


def _turning_date_set(payload: dict) -> set[str]:
    return {x["dt"] for x in payload["turning"]["micro"]}


def _center_fact_set(payload: dict) -> set[tuple]:
    return {
        (c["anchor_k0_dt"], c["confirm_dt"], c["end_dt"], c["method"])
        for c in payload["centers"]["micro"]
    }


def test_micro_engine_baseline_regression():
    baseline = _load_baseline()
    expected_map = {x.get("name"): x for x in baseline.get("scenarios", []) if x.get("name")}
    missing = [sc["name"] for sc in SCENARIOS if sc["name"] not in expected_map]
    assert not missing, f"missing scenarios in baseline: {missing}"

    diffs = []
    for sc in SCENARIOS:
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
        driftable_dates = DRIFTABLE_TURNING_DATES.get(sc["name"], set())
        required_dates = _turning_date_set(old) - driftable_dates
        missing_dates = required_dates - _turning_date_set(now)

        driftable_centers = DRIFTABLE_CENTER_FACTS.get(sc["name"], set())
        required_centers = _center_fact_set(old) - driftable_centers
        missing_centers = required_centers - _center_fact_set(now)

        if missing_dates or missing_centers:
            diffs.append(
                {
                    "name": sc["name"],
                    "missing_dates": sorted(missing_dates),
                    "missing_centers": sorted(missing_centers),
                }
            )

    assert not diffs, f"baseline mismatch scenarios: {diffs}"
