
import pandas as pd
from czsc.connectors import research
from czsc.moore.analyze import MooreCZSC
from czsc.py.enum import Mark, Direction

symbol = '300371'
bars = research.get_raw_bars_origin(symbol, sdt='20190122', edt='20200908')

engine = MooreCZSC.__new__(MooreCZSC)
from czsc.moore.segment.analyzer import SegmentAnalyzer
analyzer = SegmentAnalyzer(bars=[])
engine.segment_analyzer = analyzer

prev_tk_count = 0
prev_last_dt = None

for bar in bars:
    analyzer.update(bar)
    s = analyzer.state
    tks = s.turning_ks
    cur_count = len(tks)
    cur_last_dt = tks[-1].dt if tks else None
    
    if cur_count > prev_tk_count:
        new_tk = tks[-1]
        print(f"[NEW TK #{cur_count-1}] bar.dt={bar.dt} | TK={new_tk.dt} mark={new_tk.mark} price={new_tk.price:.3f} perfect={new_tk.is_perfect}")
        # 打印此时的四点模型状态
        if cur_count >= 4:
            n4=tks[-1]; n3=tks[-2]; n2=tks[-3]; n1=tks[-4]
            print(f"   n1={n1.dt}({n1.mark.name[:1]}), n2={n2.dt}({n2.mark.name[:1]}), n3={n3.dt}({n3.mark.name[:1]},perf={n3.is_perfect}), n4={n4.dt}({n4.mark.name[:1]})")
    elif cur_last_dt != prev_last_dt and cur_count > 0:
        print(f"[REFRESH]  bar.dt={bar.dt} | TK refreshed to {tks[-1].dt} mark={tks[-1].mark} price={tks[-1].price:.3f}")
    
    prev_tk_count = cur_count
    prev_last_dt  = cur_last_dt
