
import pandas as pd
from czsc.connectors import research
from czsc.moore.analyze import MooreCZSC
from czsc.py.enum import Mark, Direction

symbol = '300371'
bars = research.get_raw_bars_origin(symbol, sdt='20190122', edt='20200908')
engine = MooreCZSC(bars)

# Simulate replay of Seg 6
state = engine.segment_analyzer.state
ce = engine.segment_analyzer._center_engine

# Seg 6: TK 6 (idx 247) -> TK 7 (idx 268)
start_idx = 247
trig_idx = 257
end_idx = 268
direction = Direction.Up

print(f"DEBUG REPLAY Seg 6: {start_idx} -> {end_idx}, Dir: {direction}")

# Manual rollback to clear state
ce.rollback()
state.potential_centers = [] # Clear for replay
state.last_center_end_idx = 247 # Assume Seg 5 ended at 247

for i in range(start_idx, end_idx + 1):
    bar = bars[i]
    ce.update(bar, i, force_direction=direction, force_anchor_idx=start_idx, force_trigger_idx=trig_idx)
    print(f"[{i}] {bar.dt}: State={state.center_state}, K0={state.current_k0.dt if state.current_k0 else 'None'}, Method={state.center_method_found}, BK={state.center_black_k_pass}")
    if i == end_idx:
        ce.seal_on_boundary()
        print(f"--- SEALED ---")

print(f"\nFinal Centers size: {len(state.potential_centers)}")
for ct in state.potential_centers:
    print(f"  Center: {ct.start_dt} -> {ct.end_dt}, Method: {ct.method}")
