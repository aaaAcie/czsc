
import pandas as pd
from czsc.connectors import research
from czsc.moore.analyze import MooreCZSC
from czsc.py.enum import Mark, Direction

symbol = '300371'
bars = research.get_raw_bars_origin(symbol, sdt='20190122', edt='20200908')
engine = MooreCZSC(bars)

print("\n--- Turning Ks Audit ---")
for i, tk in enumerate(engine.turning_ks):
    print(f"TK {i}: dt={tk.dt}, mark={tk.mark}, price={tk.price}, perfect={tk.is_perfect}, visible={tk.has_visible_center}, fake={tk.maybe_is_fake}")

print("\n--- Segments ---")
for i, seg in enumerate(engine.segments, start=1):
    print(f"线段{i}: {seg.start_k.dt} -> {seg.end_k.dt}, Dir: {seg.direction}, Perfect: {seg.is_perfect}")
    for c in seg.centers:
        print(f"  Center: {c.method}, Visible: {c.is_visible}, Rails: [{c.lower_rail}, {c.upper_rail}]")
