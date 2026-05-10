# -*- coding: utf-8 -*-
import json
from pathlib import Path

import pytest

from czsc.connectors import research
from czsc.moore.analyze import MooreCZSC
from tests.moore.baseline_scenarios import SCENARIOS
from tests.moore_audit.audit_engine import build_audit_payload


BASELINE_PATH = Path("tests/moore/baselines/micro_engine_refactor_v2.json")


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
        cv_now = _critical_view(now)
        cv_old = _critical_view(old)
        if cv_now != cv_old:
            diffs.append(sc["name"])

    assert not diffs, f"baseline mismatch scenarios: {diffs}"
