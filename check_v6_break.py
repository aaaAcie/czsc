
from czsc.connectors import research
import pandas as pd

symbol = '300371'
bars = research.get_raw_bars_origin(symbol, sdt='20200323', edt='20200903')
df = pd.DataFrame([vars(b) for b in bars])
v6_low = 8.801
broken = df[df['low'] < v6_low]
print(f"V6 Low: {v6_low}")
if not broken.empty:
    print("Broken at:")
    print(broken[['dt', 'low']])
else:
    print("Not broken.")
