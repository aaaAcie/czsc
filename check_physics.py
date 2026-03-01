
import pandas as pd
from czsc.connectors import research
from czsc.moore.analyze import MooreCZSC
from czsc.py.enum import Mark, Direction

symbol = '300371'
bars = research.get_raw_bars_origin(symbol, sdt='20190122', edt='20200908')
engine = MooreCZSC(bars)

# Refined indices based on full TK list
# TK 3: 2019-09-11 (Peak 11.972) - Matches labeled V4
# TK 4: 2019-11-29 (Low 9.036) - Matches labeled V5
# TK 5: 2020-01-15 (Peak 10.978) - Matches labeled V6
# TK 6: 2020-02-04 (Low 8.027) - Matches labeled V7

n1_idx, n2_idx, n3_idx, n4_idx = 3, 4, 5, 6

v_n1 = engine.turning_ks[n1_idx]
v_n2 = engine.turning_ks[n2_idx]
v_n3 = engine.turning_ks[n3_idx]
v_n4 = engine.turning_ks[n4_idx]

print(f"n1 (V4): {v_n1.dt}, Price: {v_n1.price}, Mark: {v_n1.mark}")
print(f"n2 (V5): {v_n2.dt}, Price: {v_n2.price}, Mark: {v_n2.mark}")
print(f"n3 (V6): {v_n3.dt}, Price: {v_n3.price}, Mark: {v_n3.mark}")
print(f"n4 (V7): {v_n4.dt}, Price: {v_n4.price}, Mark: {v_n4.mark}")

# Physics P1 Leap (n1 -> n4) swallowing n2/n3
# Direction Down.
# Price check: v_n4 (Low) < v_n2 (Low)
print(f"\nPrice Check (V7 < V5): {v_n4.price < v_n2.price} ({v_n4.price} vs {v_n2.price})")

# MA5 check: v_n4.ma5 < v_n2.ma5
v4_ma5 = v_n4.raw_bar.cache.get('ma5')
v2_ma5 = v_n2.raw_bar.cache.get('ma5')
v1_ma5 = v_n1.raw_bar.cache.get('ma5')
print(f"Energy Check (V7_ma5 < V5_ma5): {v4_ma5 < v2_ma5} ({v4_ma5} vs {v2_ma5})")

# Gravity Lock Check: Path max ma5 <= v1_ma5
path_ma5 = [b.cache.get('ma5') for b in bars[v_n1.k_index : v_n4.k_index+1]]
print(f"Gravity Lock (Path max MA5 <= V4_ma5 {v1_ma5}): {max(path_ma5) <= v1_ma5} ({max(path_ma5)})")
