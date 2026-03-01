
import pandas as pd
from czsc.connectors import research
from czsc.moore.analyze import MooreCZSC
from czsc.py.enum import Mark, Direction

symbol = '300371'
bars = research.get_raw_bars_origin(symbol, sdt='20190122', edt='20200908')
engine = MooreCZSC(bars)

print("\n--- All Centers Audit ---")
for i, c in enumerate(engine.all_centers):
    print(f"[{i}] {c.start_dt} -> {c.end_dt}, Dir: {c.direction}, Method: {c.method}, Visible: {c.is_visible}, Rails: [{c.lower_rail}, {c.upper_rail}]")

print("\n--- Identifying Segments and their Perfection ---")
# Since some are merged, we check current segments
for i, seg in enumerate(engine.segments):
    print(f"Seg {i}: {seg.start_k.dt} -> {seg.end_k.dt}, Perfect: {seg.is_perfect}")
    # Check what centers are inside
    inside = [c for c in engine.all_centers if c.start_k_index >= seg.start_k.k_index and c.end_k_index <= seg.end_k.k_index]
    print(f"  Centers inside: {len(inside)}")
