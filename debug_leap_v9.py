
import pandas as pd
from czsc.connectors import research
from czsc.moore.analyze import MooreCZSC
from czsc.py.enum import Mark, Direction
from czsc.moore.objects import TurningK

symbol = '300371'
bars = research.get_raw_bars_origin(symbol, sdt='20190122', edt='20200908')

# Monkey-patch _macro_audit_and_replay to add debug output
engine = MooreCZSC.__new__(MooreCZSC)
from czsc.moore.segment.analyzer import SegmentAnalyzer
analyzer = SegmentAnalyzer(bars=[])
engine.segment_analyzer = analyzer

target_n4_dt = pd.Timestamp('2020-07-30')
prev_tk_count = 0

for bar in bars:
    analyzer.update(bar)
    s = analyzer.state
    tks = s.turning_ks
    
    # 当 TK9 (Jul30) 刚刚被确认时
    if len(tks) > prev_tk_count and tks and pd.Timestamp(tks[-1].dt) == target_n4_dt:
        print(f"\n=== V9 (Jul30) 确认瞬间，turning_ks 数量={len(tks)} ===")
        
        if len(tks) >= 4:
            n4 = tks[-1]
            n3 = tks[-2]
            n2 = tks[-3]
            n1 = tks[-4]
            print(f"n1: {n1.dt} price={n1.price} mark={n1.mark} locked={n1.is_locked}")
            print(f"n2: {n2.dt} price={n2.price} mark={n2.mark} locked={n2.is_locked}")
            print(f"n3: {n3.dt} price={n3.price} mark={n3.mark} perfect={n3.is_perfect}")
            print(f"n4: {n4.dt} price={n4.price} mark={n4.mark}")
            
            # _check_actual_perfection
            perf_live = analyzer._check_actual_perfection(n2, n3)
            print(f"\n_check_actual_perfection(n2, n3): {perf_live}  (True=block, False=proceed)")
            
            # physics check
            result = analyzer._check_leap_physics(n1, n4, n2, n3)
            print(f"_check_leap_physics(n1→n4, mid=n2, pullback=n3): {result}")
            
            # manual breakdown
            bar_start = n1.k_index
            bar_end   = n4.k_index
            path_bars = s.bars_raw[bar_start : bar_end + 1]
            path_min  = min(b.low for b in path_bars)
            path_max  = max(b.high for b in path_bars)
            tk_end_top    = max(n4.raw_bar.open, n4.raw_bar.close)
            tk_mid_bottom = min(n2.raw_bar.open, n2.raw_bar.close)
            growth_ok  = n4.price > n2.price and tk_end_top > tk_mid_bottom
            gravity_ok = path_min >= n1.price
            print(f"\n[Rule1] growth_ok={growth_ok}  (n4.price={n4.price} > n2.price={n2.price}, end_top={tk_end_top:.3f} > mid_bot={tk_mid_bottom:.3f})")
            print(f"[Rule1] gravity_ok={gravity_ok} (path_min={path_min:.3f} >= n1.price={n1.price})")
            
            # MA5 Rule2
            start_ma5 = n1.raw_bar.cache.get('ma5')
            mid_ma5   = n2.raw_bar.cache.get('ma5')
            end_ma5   = n4.raw_bar.cache.get('ma5')
            path_ma5  = [b.cache.get('ma5') for b in path_bars if b.cache.get('ma5') is not None]
            rule2a    = end_ma5 > mid_ma5 if end_ma5 and mid_ma5 else False
            rule2b    = min(path_ma5) >= start_ma5 if path_ma5 and start_ma5 else False
            print(f"\n[Rule2a] end_ma5={end_ma5:.3f} > mid_ma5={mid_ma5:.3f}: {rule2a}")
            print(f"[Rule2b] min_path_ma5={min(path_ma5):.3f} >= start_ma5={start_ma5:.3f}: {rule2b}")
        break
    prev_tk_count = len(tks)
