
import pandas as pd
from czsc.connectors import research
from czsc.moore.analyze import MooreCZSC
from czsc.py.enum import Mark, Direction

symbol = '300371'
bars = research.get_raw_bars_origin(symbol, sdt='20190122', edt='20200908')
engine = MooreCZSC(bars)

# Find segment 5
seg = engine.segments[5]
print(f"Seg 5: {seg.start_k.dt} -> {seg.end_k.dt}")
for ct in seg.centers:
    print(f"  Center: {ct.start_dt} -> {ct.end_dt}, Method: {ct.method}, Confirm: {ct.confirm_k.dt}")
    # Let's inspect the bars in this center
    c_bars = [b for b in bars if ct.start_dt <= b.dt <= ct.end_dt]
    print(f"    Bars in center: {len(c_bars)}")
    
    # Simulate 5K overlap detection
    ov_high, ov_low = ct.upper_rail, ct.lower_rail
    ov_indices = [i for i, k in enumerate(c_bars) if k.high >= ov_low and k.low <= ov_high]
    print(f"    Overlapping bars: {len(ov_indices)}")
    for idx in ov_indices:
        print(f"      {c_bars[idx].dt}: H={c_bars[idx].high}, L={c_bars[idx].low}")
