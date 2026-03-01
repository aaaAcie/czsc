
import pandas as pd
from czsc.connectors import research
from czsc.moore.analyze import MooreCZSC
from czsc.py.enum import Mark, Direction

symbol = '300371'
bars = research.get_raw_bars_origin(symbol, sdt='20190122', edt='20200908')
engine = MooreCZSC(bars)

print("\n--- Turning Ks with Prices ---")
for i, tk in enumerate(engine.turning_ks):
    print(f"TK {i}: dt={tk.dt}, mark={tk.mark}, price={tk.price}, perfect={tk.is_perfect}")

print("\n--- Rule 1 Audit (V4-V5-V6-V7) ---")
v4 = next(tk for tk in engine.turning_ks if tk.dt == '2020-02-04 00:00:00')
v5 = next(tk for tk in engine.turning_ks if tk.dt == '2020-03-04 00:00:00' or tk.dt == '2020-05-11 00:00:00')
# Wait, if it merged, 2020.03.04 is GONE.
# I need to find the GHOSTS.
print("\n--- Ghost Forks ---")
for fork, ghosts in engine.ghost_forks:
    print(f"Fork: {fork.dt} -> Ghosts: {[g.dt for g in ghosts]}")
