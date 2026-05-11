# -*- coding: utf-8 -*-
"""冷启动定锚 helper。"""

from czsc.py.enum import Mark


# 本地实验开关：默认关闭，避免冷启动改写既有 30F 事实仓。
ENABLE_MICRO_COLD_START = False


class ColdStartHelper:
    def __init__(self, state, ma34_lookback: int = 8, seed_hold_bars: int = 120):
        self.s = state
        self._ma34_lookback = ma34_lookback
        self._seed_hold_bars = seed_hold_bars

    def try_seed_initial_anchor(self, trigger_bar, trigger_index: int):
        """返回冷启动锚点描述；若不触发则返回 None。"""
        if not ENABLE_MICRO_COLD_START:
            return None
        s = self.s
        if s.cache.get("cold_start_seeded"):
            return None
        if len(s.turning_ks) > 1:
            return None

        if trigger_index <= 0:
            return None

        ma34_ready = [i for i, b in enumerate(s.bars_raw[: trigger_index + 1]) if b.cache.get("ma34") is not None]
        if len(ma34_ready) >= self._ma34_lookback:
            curr_ma34 = s.bars_raw[ma34_ready[-1]].cache.get("ma34")
            prev_ma34 = s.bars_raw[ma34_ready[-self._ma34_lookback]].cache.get("ma34")
            if curr_ma34 is None or prev_ma34 is None:
                return None
            is_up_trend = curr_ma34 >= prev_ma34
        else:
            # MA34 尚未就绪时放宽冷启动：用价格斜率近似初始方向。
            lookback = min(trigger_index, self._ma34_lookback)
            base_close = s.bars_raw[trigger_index - lookback].close
            curr_close = s.bars_raw[trigger_index].close
            is_up_trend = curr_close >= base_close
        scope = s.bars_raw[:trigger_index]
        if not scope:
            return None

        if is_up_trend:
            ext_bar = min(scope, key=lambda x: x.low)
            seed_mark = Mark.D
            seed_price = ext_bar.low
        else:
            ext_bar = max(scope, key=lambda x: x.high)
            seed_mark = Mark.G
            seed_price = ext_bar.high

        ext_idx = s.bars_raw.index(ext_bar)
        s.cache["cold_start_seeded"] = True
        s.cache["cold_start_seed_hold_until_k"] = trigger_index + self._seed_hold_bars
        s.cache["cold_start_seed_anchor_mark"] = seed_mark.name
        s.cache["cold_start_seed_anchor_k_index"] = ext_idx

        return {
            "ext_bar": ext_bar,
            "ext_idx": ext_idx,
            "seed_mark": seed_mark,
            "seed_price": seed_price,
            "trigger_bar": trigger_bar,
            "trigger_index": trigger_index,
        }

    def should_block_same_mark_refresh(self, trigger_index: int, new_mark: Mark) -> bool:
        """冷启动首锚限时保护：保护窗口内不允许同向刷新推迟 V0。"""
        s = self.s
        if len(s.turning_ks) != 1:
            return False
        seed = s.turning_ks[0]
        if not seed.cache.get("cold_start_seed"):
            return False
        hold_until = s.cache.get("cold_start_seed_hold_until_k")
        if hold_until is None:
            return False
        return trigger_index <= hold_until and seed.mark == new_mark

    def on_turning_committed(self):
        """当第二个端点形成且与首锚异向时，关闭强锚窗口。"""
        s = self.s
        if len(s.turning_ks) < 2:
            return
        first = s.turning_ks[0]
        second = s.turning_ks[1]
        if not first.cache.get("cold_start_seed"):
            return
        if first.mark != second.mark:
            s.cache["cold_start_seed_hold_until_k"] = -1
