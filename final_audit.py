
import pandas as pd
from czsc.connectors import research
from czsc.moore.analyze import MooreCZSC
from czsc.py.enum import Mark, Direction

symbol = '300371'
bars = research.get_raw_bars_origin(symbol, sdt='20190122', edt='20200908')
engine = MooreCZSC(bars)

print(f"Penetration Level: {engine.penetration_level}")

print("\n--- Turning Ks ---")
for i, tk in enumerate(engine.turning_ks):
    print(f"TK {i}: {tk.dt}, {tk.mark.value}, Price: {tk.price}, Perfect: {tk.is_perfect}")

print("\n--- Segments ---")
for i, seg in enumerate(engine.segments, start=1):
    print(f"线段{i}: {seg.start_k.dt} -> {seg.end_k.dt}, Dir: {seg.direction}, Perfect: {seg.is_perfect}")
