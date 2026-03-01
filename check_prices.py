
import pandas as pd
from czsc.connectors import research
from czsc.moore.analyze import MooreCZSC
from czsc.py.enum import Mark, Direction

symbol = '300371'
bars = research.get_raw_bars_origin(symbol, sdt='20190122', edt='20200908')
# We need to find the prices
for b in bars:
    if b.dt == '2020-02-04 00:00:00':
        print(f"V4 (2020-02-04) Price: {b.low}")
    if b.dt == '2020-03-23 00:00:00':
        print(f"V6 (2020-03-23) Price: {b.low}")
    if b.dt == '2019-09-11 00:00:00':
        print(f"V1? (2019-09-11) Price: {b.low}")

# Also check 2020.02.03
for b in bars:
    if b.dt == '2020-02-03 00:00:00':
        print(f"2020-02-03 Price: {b.low}")
