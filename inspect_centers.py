
import pandas as pd
from czsc.connectors import research
from czsc.moore.analyze import MooreCZSC
from czsc.py.enum import Mark, Direction

symbol = '300371'
bars = research.get_raw_bars_origin(symbol, sdt='20190122', edt='20200908')
engine = MooreCZSC(bars)

print("\n--- Segments ---")
for i, seg in enumerate(engine.segments):
    print(f"Seg {i}: {seg.start_k.dt} -> {seg.end_k.dt}, Dir: {seg.direction}, Perfect: {seg.is_perfect}")
    for j, ct in enumerate(seg.centers):
        print(f"  Center {j}: {ct.start_dt} -> {ct.end_dt}, Method: {ct.method}, Rails: [{ct.lower_rail:.3f}, {ct.upper_rail:.3f}]")
