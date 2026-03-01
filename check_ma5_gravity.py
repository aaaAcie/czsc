
from czsc.connectors import research
import pandas as pd

symbol = '300371'
bars = research.get_raw_bars_origin(symbol, sdt='20200301', edt='20200410')
for b in bars:
    print(f"{b.dt.strftime('%Y-%m-%d')} Price: {b.low}, MA5: {b.cache.get('ma5', 'N/A')}")

v6_dt = '2020-03-23'
v6_price = 8.801
# find V6's MA5
v6_ma5 = None
for b in bars:
    if b.dt.strftime('%Y-%m-%d') == v6_dt:
        v6_ma5 = b.cache.get('ma5')
print(f"V6 Anchor: Price={v6_price}, MA5={v6_ma5}")
