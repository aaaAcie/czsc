
import pandas as pd
from czsc.connectors import research
from czsc.moore.analyze import MooreCZSC
from czsc.py.enum import Mark, Direction

symbol = '300371'
bars = research.get_raw_bars_origin(symbol, sdt='20190122', edt='20200908')
engine = MooreCZSC(bars)

# Print all segments to find indices
print("--- Segments ---")
for i, seg in enumerate(engine.segments):
    print(f"Seg {i}: {seg.start_k.dt} -> {seg.end_k.dt}, Dir: {seg.direction}, Perfect: {seg.is_perfect}")

# We care about Seg 6 (Line 7), Seg 7 (Line 8), Seg 8 (Line 9)
n1 = engine.segments[6].start_k
n2 = engine.segments[6].end_k
n3 = engine.segments[7].end_k
n4 = engine.segments[8].end_k

print(f"\nn1: {n1.dt}, Price: {n1.price}, Mark: {n1.mark}")
print(f"n2: {n2.dt}, Price: {n2.price}, Mark: {n2.mark}")
print(f"n3: {n3.dt}, Price: {n3.price}, Mark: {n3.mark}, Perfect: {n3.is_perfect}")
print(f"n4: {n4.dt}, Price: {n4.price}, Mark: {n4.mark}")

def check_rule1(tk_end, tk_mid_same):
    tk_end_top = max(tk_end.raw_bar.open, tk_end.raw_bar.close)
    tk_end_bottom = min(tk_end.raw_bar.open, tk_end.raw_bar.close)
    tk_mid_top = max(tk_mid_same.raw_bar.open, tk_mid_same.raw_bar.close)
    tk_mid_bottom = min(tk_mid_same.raw_bar.open, tk_mid_same.raw_bar.close)
    
    if tk_end.mark == Mark.G:
        price_ok = tk_end.price > tk_mid_same.price
        body_ok = tk_end_top > tk_mid_bottom
        print(f"  [Rule 1 Up] Price: {tk_end.price} > {tk_mid_same.price} -> {price_ok}")
        print(f"  [Rule 1 Up] Body: {tk_end_top} > {tk_mid_bottom} -> {body_ok}")
        return price_ok and body_ok
    else:
        price_ok = tk_end.price < tk_mid_same.price
        body_ok = tk_end_bottom < tk_mid_top
        print(f"  [Rule 1 Down] Price: {tk_end.price} < {tk_mid_same.price} -> {price_ok}")
        print(f"  [Rule 1 Down] Body: {tk_end_bottom} < {tk_mid_top} -> {body_ok}")
        return price_ok and body_ok

print("\n--- Rule 1 Check (P1: n1->n4, swallowing n2/n3) ---")
result_r1 = check_rule1(n4, n2)

print("\n--- Rule 2 Check ---")
def check_rule2(tk_start, tk_mid_same, tk_end):
    start_ma5 = tk_start.raw_bar.cache.get('ma5')
    mid_ma5 = tk_mid_same.raw_bar.cache.get('ma5')
    end_ma5 = tk_end.raw_bar.cache.get('ma5')
    print(f"  MA5s: start={start_ma5}, mid={mid_ma5}, end={end_ma5}")
    
    rule2a = False
    if tk_end.mark == Mark.G:
        rule2a = end_ma5 > mid_ma5
    else:
        rule2a = end_ma5 < mid_ma5
    print(f"  Rule 2A (Energy): {rule2a}")
    
    rule2b = False
    path_ma5 = [b.cache.get('ma5') for b in bars[tk_start.k_index : tk_end.k_index+1]]
    if tk_end.mark == Mark.G:
        rule2b = min(path_ma5) >= start_ma5
        print(f"  Rule 2B (Gravity): min({min(path_ma5)}) >= {start_ma5} -> {rule2b}")
    else:
        rule2b = max(path_ma5) <= start_ma5
        print(f"  Rule 2B (Gravity): max({max(path_ma5)}) <= {start_ma5} -> {rule2b}")
    return rule2a and rule2b

result_r2 = check_rule2(n1, n2, n4)
print(f"Rule 2 Result: {result_r2}")
print(f"\nP1 Merger (R1 OR R2): {result_r1 or result_r2}")
