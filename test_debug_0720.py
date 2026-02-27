from czsc.moore.analyze import MooreCZSC
from czsc.connectors import research
import czsc.moore.segment.center as mod

original = mod.CenterEngine._check_center_formation

def test_check(self):
    res = original(self)
    
    if len(self.s.bars_raw) > 0:
        dt_str = str(self.s.bars_raw[-1].dt)
        if '2020-07-20' in dt_str:
            fz = self._check_fan_zheng_liang_chuan()
            five = self._check_5k_overlap()
            san = self._check_san_bi()
            window_bars = self.s.bars_raw[self.s.center_line_k_index : self.s.center_end_k_index + 1]
            bk = self._check_black_k(self.s.center_direction, 0, window_bars)
            print(f'[_check_center_formation at {dt_str}]: final={res}, fz={fz}, 5k={five}, 3bi={san}, black_k={bk}')
    return res

mod.CenterEngine._check_center_formation = test_check

bars = research.get_raw_bars_origin('sz002286', sdt='20200128', edt='20200828')
engine = MooreCZSC(bars)
