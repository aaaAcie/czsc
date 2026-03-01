
from czsc.connectors import research
from czsc.moore.analyze import MooreCZSC

symbol = '300371'
# 延伸到2020年底看看合并是否发生
bars = research.get_raw_bars_origin(symbol, sdt='20190122', edt='20201231')
engine = MooreCZSC(bars)

print("--- Final Segments (edt=20201231) ---")
for i, seg in enumerate(engine.segments):
    print(f"Seg {i+1}: {seg.start_k.dt} -> {seg.end_k.dt}, Perfect={seg.is_perfect}")

print(f"\nturning_ks: {len(engine.turning_ks)}")
for i, tk in enumerate(engine.turning_ks):
    print(f"  [{i}] {tk.dt} {tk.mark} p={tk.price:.3f} perf={tk.is_perfect}")
