
import pandas as pd
from czsc.connectors import research
from czsc.moore.analyze import MooreCZSC
from czsc.py.enum import Mark, Direction

symbol = '300371'
bars = research.get_raw_bars_origin(symbol, sdt='20190122', edt='20200908')

# 逐根步进，在 V6 确认瞬间捕获中枢引擎状态
engine = MooreCZSC.__new__(MooreCZSC)
from czsc.moore.segment.analyzer import SegmentAnalyzer
engine.segment_analyzer = SegmentAnalyzer(bars=[])

prev_tk_count = 0
target_dt = pd.Timestamp('2020-05-22')

for bar in bars:
    engine.segment_analyzer.update(bar)
    tks = engine.segment_analyzer.state.turning_ks
    s = engine.segment_analyzer.state
    
    # 监测 V6 (底) 确认的瞬间
    if len(tks) > prev_tk_count:
        new_tk = tks[-1]
        if pd.Timestamp(new_tk.dt) == target_dt:
            print(f"=== V6 确认瞬间 ({new_tk.dt}) ===")
            print(f"  V6.is_perfect = {new_tk.is_perfect}")
            print(f"  center_state  = {s.center_state}")
            print(f"  method_found  = {s.center_method_found}")
            print(f"  black_k_pass  = {s.center_black_k_pass}")
            print(f"  center_start_k_index = {s.center_start_k_index}")
            print(f"  V5.k_index ~ V6.k_index = [{tks[-2].k_index}, {new_tk.k_index}]")
            
            # 检查固化中枢
            all_c = s.all_centers + s.potential_centers
            print(f"\n  固化/暂存中枢总数: {len(all_c)}")
            for c in all_c:
                if tks[-2].k_index <= c.start_k_index <= new_tk.k_index:
                    print(f"  *** 段内中枢: {c.start_dt} -> {c.end_dt}, method={c.method}")
            break
        prev_tk_count = len(tks)
    prev_tk_count = len(tks)
