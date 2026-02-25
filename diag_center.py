# -*- coding: utf-8 -*-
"""诊断脚本：追踪 CenterEngine 的 State2 进入次数和验证门通过率"""
import sys
sys.path.insert(0, '/Users/akuai/code/stock/czsc')

from czsc.connectors import research
from czsc.moore.segment.center import CenterEngine

state2_enter = 0
finalize_call = 0
finalize_pass  = 0
hidden = {'2c': 0, '5k': 0, '3bi': 0}
degenerate = 0  # upper_rail == lower_rail == center_line

orig_enter = CenterEngine._enter_forming_state.__wrapped__ if hasattr(CenterEngine._enter_forming_state, '__wrapped__') else CenterEngine._enter_forming_state
orig_finalize = CenterEngine._finalize_and_mount_center

def patched_enter(self, direction, k0, confirm_k, cf_index):
    global state2_enter, degenerate
    state2_enter += 1
    s = self.s
    orig_enter(self, direction, k0, confirm_k, cf_index)
    if s.center_upper_rail == s.center_lower_rail:
        degenerate += 1

def patched_finalize(self):
    global finalize_call, finalize_pass, hidden
    finalize_call += 1
    s = self.s
    if s.center_direction is None:
        return
    is_vis = s.center_is_visible
    c2  = self._check_fan_zheng_liang_chuan()
    r5k = self._check_5k_overlap()
    r3b = self._check_san_bi()
    if c2:  hidden['2c'] += 1
    if r5k: hidden['5k'] += 1
    if r3b: hidden['3bi'] += 1
    if is_vis or c2 or r5k or r3b:
        finalize_pass += 1
    orig_finalize(self)

CenterEngine._enter_forming_state = patched_enter
CenterEngine._finalize_and_mount_center = patched_finalize

from czsc.moore.analyze import MooreCZSC
symbols = research.get_symbols('中证500成分股')[:1]
bars = research.get_raw_bars(symbols[0], freq='30分钟', sdt='20210101', edt='20210701')
engine = MooreCZSC(bars)

print("=== 中枢引擎诊断 ===")
print(f"State2 进入次数:          {state2_enter}")
print(f"  其中退化结界(上=下=cl): {degenerate}")
print(f"finalize 调用次数:        {finalize_call}")
print(f"通过验证门次数:           {finalize_pass}")
print(f"  反正两穿成立:           {hidden['2c']}")
print(f"  5K重叠成立:             {hidden['5k']}")
print(f"  三笔成立:               {hidden['3bi']}")
print(f"最终 potential_centers:   {len(engine.potential_centers)}")
print(f"最终 all_centers:         {len(engine.all_centers)}")
for i, c in enumerate(engine.all_centers[:5]):
    print(f"  all_centers[{i}]: {c.type_name} dir={c.direction} {c.start_dt} ~ {c.end_dt} upper={c.upper_rail:.2f} lower={c.lower_rail:.2f}")

