
import pandas as pd
from czsc.connectors import research
from czsc.moore.analyze import MooreCZSC
from czsc.py.enum import Mark, Direction

symbol = '300371'
bars = research.get_raw_bars_origin(symbol, sdt='20190122', edt='20200908')
engine = MooreCZSC(bars)

# Analyze the state when V7 (2020.05.11) was confirmed
# We need to see why V4-V7 merged.
# This means N segment (V5-V6) was considered imperfect.
# V5: 2020-03-04
# V6: 2020-03-23

print("\n--- Detailed Audit of 2020.03-2020.05 ---")
# To find when they were added, we can iterate bars and check turning_ks length
for bar in bars:
    engine.segment_analyzer.update(bar)
    tks = engine.turning_ks
    if len(tks) > 1 and tks[-1].dt == '2020-05-11 00:00:00':
        print(f"TK {tks[-1].dt} confirmed at {bar.dt}")
        print(f"Turning Ks count: {len(tks)}")
        # Check n3 (tks[-2])
        n1, n2, n3, n4 = tks[-4:]
        print(f"n1: {n1.dt}, price: {n1.price}")
        print(f"n2: {n2.dt}, price: {n2.price}")
        print(f"n3: {n3.dt}, price: {n3.price}, perfect: {n3.is_perfect}")
        print(f"n4: {n4.dt}, price: {n4.price}")
        
# Also check if centers exist for those segments
print("\n--- Centers Audit ---")
for c in engine.all_centers:
    print(f"Center: {c.start_dt} -> {c.end_dt}, method: {c.method}")
