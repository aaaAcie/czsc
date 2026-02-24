# -*- coding: utf-8 -*-
from datetime import datetime, timedelta
from czsc.py.objects import RawBar
from czsc.py.enum import Freq, Mark, Direction
from czsc.moore.analyze import MooreCZSC

def create_fake_bars(prices):
    """根据收盘价序列快速生成测试用的 RawBar 列表"""
    bars = []
    base_dt = datetime(2023, 1, 1, 9, 30)
    for i, p in enumerate(prices):
        # 简单模拟：高低等于收市，开盘等于前收，形成实体
        open_p = prices[i-1] if i > 0 else p
        high_p = p * 1.01
        low_p = p * 0.99
        b = RawBar(
            symbol="TEST.MOORE", id=i, 
            dt=base_dt + timedelta(minutes=i*5),
            freq=Freq.F5, open=open_p, close=p, 
            high=high_p, low=low_p, vol=1000, amount=10000
        )
        bars.append(b)
    return bars

def test_moore_engine_warmup():
    """测试引擎的冷启动拦截特性"""
    # 造 33 根 K 线，MA5 满足，但 MA34 不满
    prices = [10.0 + i * 0.1 for i in range(33)]
    bars = create_fake_bars(prices)
    
    engine = MooreCZSC(bars)
    # 不应该触发任何转折寻址，哪怕价格已经走出长单边趋势
    assert len(engine.turning_ks) == 0
    assert engine.last_ma5 is None

    # 加入第 34 根，刚好满 MA34，只记录了一次 ma5 并未发生跨越与趋势对比
    final_bar = create_fake_bars([13.4])[0]
    final_bar.dt = bars[-1].dt + timedelta(minutes=5)
    bars.append(final_bar)
    engine.update(bars[-1])
    assert engine.last_ma5 is not None
    assert len(engine.turning_ks) == 0
