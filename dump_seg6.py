
import pandas as pd
from czsc.connectors import research
from czsc.moore.analyze import MooreCZSC
from czsc.py.enum import Mark, Direction

symbol = '300371'
bars = research.get_raw_bars_origin(symbol, sdt='20190122', edt='20200908')
engine = MooreCZSC(bars)

# Find segment 6
seg = engine.segments[6]
print(f"Seg 6: {seg.start_k.dt} (idx {seg.start_k.k_index}) -> {seg.end_k.dt} (idx {seg.end_k.k_index}), Dir: {seg.direction}")

# Bars in Segment 6
idx_s = seg.start_k.k_index
idx_e = seg.end_k.k_index
s_bars = bars[idx_s : idx_e + 1]
print(f"Bars in Segment: {len(s_bars)}")
for i, b in enumerate(s_bars):
    ma5 = b.cache.get('ma5', 0)
    print(f"  [{idx_s + i}] {b.dt}: O={b.open}, C={b.close}, H={b.high}, L={b.low}, MA5={ma5:.3f}")

# Check for potential centers in this range
print("\n--- Potential Centers in Seg 6 ---")
for ct in engine.all_centers:
    if seg.start_k.dt <= ct.confirm_k.dt <= seg.end_k.dt:
        print(f"  Center: {ct.start_dt} -> {ct.end_dt}, Method: {ct.method}, Ghost: {ct.is_ghost}")
