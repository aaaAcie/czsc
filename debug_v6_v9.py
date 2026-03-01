
import pandas as pd
from czsc.connectors import research
from czsc.moore.analyze import MooreCZSC
from czsc.py.enum import Mark, Direction

symbol = '300371'
bars = research.get_raw_bars_origin(symbol, sdt='20200201', edt='20200908')
engine = MooreCZSC(bars)

print("\n--- Final Segments ---")
for i, seg in enumerate(engine.segments):
    print(f"Seg {i}: {seg.start_k.dt} -> {seg.end_k.dt}")

# Audit V6-V9 merger (P1 at V9)
# Indices in final turning_ks (if 10 tks): 0..9
# V9 is index 9. V8 is index 8. V7 is index 7. V6 is index 6.
tks = engine.turning_ks
v6 = [tk for tk in tks if tk.dt == '2020-03-23 00:00:00'][0]
v7 = [tk for tk in tks if tk.dt == '2020-05-11 00:00:00'][0]
v8 = [tk for tk in tks if tk.dt == '2020-05-22 00:00:00'][0]
v9 = [tk for tk in tks if tk.dt == '2020-07-30 00:00:00'][0]

print(f"\nAudit V6-V9: n1={v6.dt}, n2={v7.dt}, n3={v8.dt}, n4={v9.dt}")
print(f"n3.is_perfect: {v8.is_perfect}")
live_p = engine.segment_analyzer._check_actual_perfection(v7, v8)
print(f"Live perfection for Line 8: {live_p}")

res = engine.segment_analyzer._check_leap_physics(v6, v9, v7, v8)
print(f"Check Leap Physics (V6 to V9): {res}")

def check_rule1_manually(tk_start, tk_end, tk_mid_same):
    tk_end_top = max(tk_end.raw_bar.open, tk_end.raw_bar.close)
    tk_mid_bottom = min(tk_mid_same.raw_bar.open, tk_mid_same.raw_bar.close)
    growth_ok = tk_end.price > tk_mid_same.price and tk_end_top > tk_mid_bottom
    
    path_bars = bars[tk_start.k_index : tk_end.k_index + 1]
    path_min = min(b.low for b in path_bars)
    gravity_ok = path_min >= tk_start.price
    print(f"  Growth: {growth_ok}, Gravity: {gravity_ok} (min {path_min} >= start {tk_start.price})")
    return growth_ok and gravity_ok

print("\n--- Manual Rule 1 Breakdown ---")
res_m = check_rule1_manually(v6, v9, v7)
print(f"Manual Result: {res_m}")
