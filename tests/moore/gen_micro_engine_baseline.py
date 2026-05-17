# -*- coding: utf-8 -*-
"""生成 micro_engine helper 化重构回归基线。"""

import json
from pathlib import Path

from czsc.connectors import research
from czsc.moore.analyze import MooreCZSC
from tests.moore.baseline_scenarios import SCENARIOS
from tests.moore_audit.audit_engine import build_audit_payload


def main():
    out_path = Path("tests/moore/baselines/micro_engine_refactor_v1.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"version": "v1", "scenarios": []}

    for sc in SCENARIOS:
        bars = research.get_raw_bars_origin(sc["symbol"], sdt=sc["sdt"], edt=sc["edt"])
        if not bars:
            payload["scenarios"].append(
                {"name": sc["name"], "symbol": sc["symbol"], "error": "no_bars"}
            )
            continue
        engine = MooreCZSC(bars, **sc["kwargs"])
        item = build_audit_payload(
            engine=engine,
            symbol=sc["symbol"],
            sdt=sc["sdt"],
            edt=sc["edt"],
            audit_link_rounds=sc["kwargs"].get("audit_link_rounds", 5),
            ma34_cross_as_valid_gate=sc["kwargs"].get("ma34_cross_as_valid_gate", True),
            replay_centers_after_macro_swallow=sc["kwargs"].get("replay_centers_after_macro_swallow", True),
        )
        item["name"] = sc["name"]
        payload["scenarios"].append(item)

    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(str(out_path))


if __name__ == "__main__":
    main()
