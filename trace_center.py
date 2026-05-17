
import pandas as pd
from czsc.connectors import research
from czsc.moore.analyze import MooreCZSC
from czsc.py.enum import Mark, Direction

symbol = '300371'
bars = research.get_raw_bars_origin(symbol, sdt='20200201', edt='20200501')

engine = MooreCZSC(bars)
base_dt = pd.to_datetime('2020-03-01')
end_dt = pd.to_datetime('2020-03-31')

for bar in bars:
    engine.segment_analyzer.update(bar)
    s = engine.segment_analyzer.state
    b_dt = pd.to_datetime(bar.dt)
    if base_dt <= b_dt <= end_dt:
        print(f"[{bar.dt}] State:{s.center_state} Method:{s.center_method_found} BK:{s.center_black_k_pass} Rails:[{s.center_lower_rail}, {s.center_upper_rail}]")
        if s.center_state == 2:
             ok, idx = engine.segment_analyzer._center_engine._check_5k_overlap_with_idx()
             if ok: print(f"  >>> 5K Overlap FOUND at index {idx}!")

print("\n--- Final Potential Centers for March ---")
for c in s.potential_centers:
    c_dt = pd.to_datetime(c.start_dt)
    if base_dt <= c_dt <= end_dt:
        print(f"Center: {c.start_dt} -> {c.end_dt}, method: {c.method}")
