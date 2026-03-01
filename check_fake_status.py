
from czsc.connectors import research
from czsc.moore.analyze import MooreCZSC

symbol = '300371'
bars = research.get_raw_bars_origin(symbol, sdt='20190122', edt='20201228')
engine = MooreCZSC(bars)
s = engine.segment_analyzer.state

print("Turning Points Audit:")
for i, tk in enumerate(s.turning_ks):
    is_perf = engine.segment_analyzer._check_actual_perfection(s.turning_ks[i-1] if i>0 else None, tk)
    print(f"[{i}] {tk.dt} mark={tk.mark.name} price={tk.price} fake={tk.maybe_is_fake} live_perf={is_perf}")
