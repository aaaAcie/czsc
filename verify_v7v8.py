
from czsc.connectors import research
from czsc.moore.analyze import MooreCZSC
import os

os.environ['LOGURU_LEVEL'] = 'INFO'
symbol = 'sz002346'
bars = research.get_raw_bars_origin(symbol, sdt='20180901', edt='20200908')
engine = MooreCZSC(bars)
s = engine.segment_analyzer.state

print(f"Total TurningKs: {len(engine.turning_ks)}")
for i, tk in enumerate(engine.turning_ks):
    print(f"V{i}: {tk.dt} {tk.mark} perfect={tk.is_perfect}")

# Combine both all_centers and potential_centers for listing
all_c = s.all_centers + s.potential_centers
print(f"\nTotal Centers (Solidified + Potential): {len(all_c)}")
for i, c in enumerate(all_c):
    print(f"Center {i}: {c.start_dt} to {c.end_dt} dir={c.direction} type={c.type_name}")

# Check specifically for V7-V8 (2020-01-20 to 2020-02-14)
tk7 = engine.turning_ks[7]
tk8 = engine.turning_ks[8]
print(f"\nAudit V7 ({tk7.dt}) to V8 ({tk8.dt})")
found = [c for c in all_c if tk7.k_index <= c.start_k_index <= tk8.k_index]
if found:
    for c in found:
        print(f"-> FOUND CENTER: {c.start_dt} to {c.end_dt} price={c.lower_rail:.3f}-{c.upper_rail:.3f} type={c.type_name}")
else:
    print("-> NO CENTER FOUND in this range")
