
import pandas as pd
from czsc.connectors import research
from czsc.moore.analyze import MooreCZSC
from czsc.py.enum import Mark, Direction

symbol = '300371'
bars = research.get_raw_bars_origin(symbol, sdt='20200301', edt='20200331')

print("\n--- Bars in March 2020 ---")
for i, b in enumerate(bars):
    ma5 = b.cache.get('ma5', 0)
    print(f"[{i}] {b.dt} O:{b.open} C:{b.close} H:{b.high} L:{b.low} MA5:{ma5}")

# Simulation of CenterEngine for V5-V6 (Down trend)
# V5 confirms Top on March 4.
# V6 confirms Bottom on March 23.
