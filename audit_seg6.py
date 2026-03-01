
import pandas as pd
from czsc.connectors import research
from czsc.moore.analyze import MooreCZSC

symbol = '300371'
bars = research.get_raw_bars_origin(symbol, sdt='20190122', edt='20200908')
engine = MooreCZSC(bars)

tks = engine.turning_ks
print("All TKs:")
for i, tk in enumerate(tks):
    print(f"  [{i}] {tk.dt} mark={tk.mark} price={tk.price:.3f} perfect={tk.is_perfect}")

# 线段6 = TK4(Mar04,G)→TK5(Mar23,D)
# 找到 Mar04 和 Mar23 的 k_index
tk_mar04 = next((tk for tk in tks if str(tk.dt).startswith('2020-03-04')), None)
tk_mar23 = next((tk for tk in tks if str(tk.dt).startswith('2020-03-23')), None)

if tk_mar04 and tk_mar23:
    print(f"\nLine6: {tk_mar04.dt} ({tk_mar04.mark}) → {tk_mar23.dt} ({tk_mar23.mark})")
    print(f"  tk_mar23.is_perfect = {tk_mar23.is_perfect}")
    
    start_idx = tk_mar04.k_index
    end_idx   = tk_mar23.k_index
    
    s = engine.segment_analyzer.state
    print(f"  k_index range: [{start_idx}, {end_idx}]")
    
    print("\n已固化中枢（起点在此范围内）:")
    all_c = s.all_centers + s.potential_centers
    for c in all_c:
        if start_idx <= c.start_k_index <= end_idx:
            print(f"    {c.start_dt}->{c.end_dt} method={c.method} ghost={getattr(c,'is_ghost',False)}")
