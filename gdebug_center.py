import sys
sys.path.insert(0, '/Users/akuai/code/stock/czsc')

from loguru import logger
from czsc.connectors import research
from czsc.moore.analyze import MooreCZSC
from czsc.moore.segment.center import CenterEngine
import pandas as pd

# Target time range for detailed debugging
START_DT = pd.to_datetime('2021-06-01 09:30:00')
END_DT = pd.to_datetime('2021-06-07 15:00:00')

orig_update = CenterEngine.update
orig_finalize = CenterEngine._finalize_and_mount_center

def hooked_update(self, bar, k_index):
    s = self.s
    # Only print logs within our target window
    if START_DT <= bar.dt <= END_DT:
        ma5 = bar.cache.get('ma5', 0)
        
        # Calculate pure/break for logging
        if s.center_state > 0:
            current_dir = s.center_direction
        else:
            _, macro_dir = self._get_macro_anchor()
            current_dir = macro_dir
            
        is_pure = False
        is_break = False
        if current_dir == 1: # Up
            is_pure = min(bar.open, bar.close) > ma5
            is_break = min(bar.open, bar.close) < ma5
        elif current_dir == -1: # Down
            is_pure = max(bar.open, bar.close) < ma5
            is_break = max(bar.open, bar.close) > ma5
            
        logger.info(f"[{bar.dt}] start_st={s.center_state} dir={current_dir} ma5={ma5:.2f} k0={getattr(s.current_k0, 'dt', None)} lat_k0={getattr(s.latest_k0, 'dt', None)} pure={is_pure} break={is_break}")
        
        if s.center_state == 2:
            logger.info(f"   病房中: rail=[{s.center_lower_rail:.2f}, {s.center_upper_rail:.2f}]")

    orig_update(self, bar, k_index)
    
    if START_DT <= bar.dt <= END_DT:
        if s.center_state == 2:
            logger.info(f"   执行后病房状态: rail=[{s.center_lower_rail:.2f}, {s.center_upper_rail:.2f}] end_dt={s.center_end_dt}")
        logger.info(f"[{bar.dt}] end_st={s.center_state}\n")

def hooked_finalize(self):
    s = self.s
    logger.info(f"====== 开始审批中枢: dir={s.center_direction} confirm_k={s.center_line_k.dt} rail=[{s.center_lower_rail:.2f}, {s.center_upper_rail:.2f}] ======")
    is_formed = self._check_center_formation()
    logger.info(f"   起手三式及黑K验证结果: {is_formed}")
    
    # log if it checks each pattern
    c2 = self._check_fan_zheng_liang_chuan()
    r5k = self._check_5k_overlap()
    r3b = self._check_san_bi()
    logger.info(f"   2C={c2}, 5K={r5k}, 3B={r3b}")
    
    window_bars = s.bars_raw[s.center_line_k_index : s.center_end_k_index + 1]
    has_black_y = self._check_black_k(s.center_direction, 0, window_bars)
    logger.info(f"   黑K={has_black_y}")
    
    orig_finalize(self)

CenterEngine.update = hooked_update
CenterEngine._finalize_and_mount_center = hooked_finalize

if __name__ == '__main__':
    bars = research.get_raw_bars('000001.SH', freq='30分钟', sdt='20210101', edt='20210701')
    engine = MooreCZSC(bars)
    print(f"生成的 centers 数量: {len(engine.all_centers)}")
