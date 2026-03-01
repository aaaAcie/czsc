
from czsc.connectors import research
from czsc.moore.analyze import MooreCZSC
import os

os.environ['LOGURU_LEVEL'] = 'INFO'
bars = research.get_raw_bars_origin('sz002346', '20190101', '20200908')
engine = MooreCZSC(bars)
s = engine.segment_analyzer.state

print(f"Total TurningKs: {len(engine.turning_ks)}")
for i, tk in enumerate(engine.turning_ks):
    print(f"V{i}: {tk.dt} {tk.mark} perfect={tk.is_perfect}")

print(f"\nTotal Confirmed Centers: {len(s.all_centers)}")
for i, c in enumerate(s.all_centers):
    print(f"Center {i}: {c.start_dt} to {c.end_dt} dir={c.direction} type={c.type_name}")
