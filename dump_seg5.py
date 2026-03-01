
import pandas as pd
from czsc.connectors import research
from czsc.moore.analyze import MooreCZSC
from czsc.py.enum import Mark, Direction

symbol = '300371'
bars = research.get_raw_bars_origin(symbol, sdt='20190122', edt='20200908')
engine = MooreCZSC(bars)

# Find segment 5
seg = engine.segments[5]
print(f"Seg 5: {seg.start_k.dt} (idx {seg.start_k.k_index}) -> {seg.end_k.dt} (idx {seg.end_k.k_index})")
print(f"Total bars in raw: {len(bars)}")

# The center reported
ct = seg.centers[0]
print(f"Center: {ct.start_dt} -> {ct.end_dt}, Method: {ct.method}")
print(f"Confirm K: {ct.confirm_k.dt} (idx {ct.confirm_k.cache.get('idx_in_raw', 'unknown')})")

# Let's see the bars from start_k to end_k
idx_s = seg.start_k.k_index
idx_e = seg.end_k.k_index
s_bars = bars[idx_s : idx_e + 1]
print(f"Bars in Segment: {len(s_bars)}")
for i, b in enumerate(s_bars):
    print(f"  [{idx_s + i}] {b.dt}: O={b.open}, C={b.close}, H={b.high}, L={b.low}")
