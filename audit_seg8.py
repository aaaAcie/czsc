
import pandas as pd
from czsc.connectors import research
from czsc.moore.analyze import MooreCZSC
from czsc.py.enum import Mark, Direction

symbol = '300371'
bars = research.get_raw_bars_origin(symbol, sdt='20190122', edt='20200908')
engine = MooreCZSC(bars)

tks = engine.turning_ks
# 线段8：V5(May11) -> V6(May22)
v5 = next(tk for tk in tks if str(tk.dt).startswith('2020-05-11'))
v6 = next(tk for tk in tks if str(tk.dt).startswith('2020-05-22'))
print(f"V5: {v5.dt}, mark={v5.mark}, price={v5.price}")
print(f"V6: {v6.dt}, mark={v6.mark}, price={v6.price}, is_perfect={v6.is_perfect}")

# 检查 V5->V6 区间内有没有中枢起始点落在这段里
s = engine.segment_analyzer.state
seg8_start = v5.k_index
seg8_end   = v6.k_index
print(f"\nSeg8 k_index range: [{seg8_start}, {seg8_end}]")

print("\n已固化中枢中起点在此区间内的：")
all_c = s.all_centers + s.potential_centers
for c in all_c:
    if seg8_start <= c.start_k_index <= seg8_end:
        print(f"  {c.start_dt} -> {c.end_dt}, method={c.method}, is_ghost={getattr(c,'is_ghost',False)}")
