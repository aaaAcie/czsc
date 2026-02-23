# -*- coding: utf-8 -*-
"""
缠论123类买卖点 - 多品种回测示例
"""
import sys
import os
from pathlib import Path
from loguru import logger
import pandas as pd
import glob

# 确保可以导入项目模块
sys.path.insert(0, '.')
sys.path.insert(0, '..')

import czsc
from czsc.connectors import research
from czsc import Event, Position, CTAResearch

def create_123_pos(symbol, **kwargs):
    """创建缠论123类买卖点持仓策略"""
    base_freq = kwargs.get('base_freq', '日线')
    
    # 定义开多事件（一买、二买、三买）
    opens = [
        {
            "operate": "开多",
            "name": "一买",
            "signals_all": [
                f"{base_freq}_D1MACD12#26#9_BS1辅助V221201_一买_任意_任意_0",
                f"{base_freq}_D1K#MACD12#26#9形态_BS辅助V221208_绿抽脚_任意_任意_0"
            ]
        },
        {
            "operate": "开多",
            "name": "二买",
            "signals_all": [
                f"{base_freq}_D1MACD12#26#9_BS2辅助V221201_二买_金叉_任意_0",
                f"{base_freq}_D1K5B_放量V221112_是_任意_任意_0"
            ]
        },
        {
            "operate": "开多",
            "name": "三买",
            "signals_all": [
                f"{base_freq}_D1#SMA#34_BS3辅助V230319_三买_均线底分_任意_0",
                f"{base_freq}_D1#SMA#34_BS3辅助V230319_三买_均线新高_任意_0"
            ]
        }
    ]

    # 定义平多事件
    exits = [
        {
            "operate": "平多",
            "name": "一卖",
            "signals_all": [
                f"{base_freq}_D1MACD12#26#9_BS1辅助V221201_一卖_任意_任意_0",
                f"{base_freq}_D1K#MACD12#26#9形态_BS辅助V221208_红缩柱_任意_任意_0"
            ]
        },
        {"operate": "平多", "name": "二卖", "signals_all": [f"{base_freq}_D1MACD12#26#9_BS2辅助V221201_二卖_死叉_任意_0"]},
        {"operate": "平多", "name": "三卖", "signals_all": [f"{base_freq}_D1#SMA#34_BS3辅助V230319_三卖_均线顶分_任意_0"]}
    ]

    pos = Position(
        name=f"{base_freq}123类买卖点",
        symbol=symbol,
        opens=[Event.load(x) for x in opens],
        exits=[Event.load(x) for x in exits],
        interval=3600 * 4,
        timeout=16 * 100,
        stop_loss=500,
    )
    return pos

class Chan123Strategy(czsc.CzscStrategyBase):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    @property
    def positions(self):
        base_freqs = self.kwargs.get('base_freqs', ['日线'])
        return [create_123_pos(self.symbol, base_freq=freq) for freq in base_freqs]

if __name__ == '__main__':
    results_path = Path('results/缠论123多品种回测')
    
    # 1. 获取所有待测试品种
    symbols = research.get_symbols('originData')
    if not symbols:
        logger.error("没有找到数据")
        sys.exit()
    
    # 取前 5 个品种进行测试
    test_symbols = symbols[:25]
    logger.info(f"待测试品种列表：{test_symbols}")

    # 2. 使用 CTAResearch 进行多品种回测
    cta = CTAResearch(
        strategy=Chan123Strategy,
        read_bars=research.get_raw_bars,
        results_path=str(results_path),
        base_freqs=['日线'], # 策略参数
    )

    # 3. 执行回测
    # sdt: 回测开始时间, edt: 回测结束时间, bar_sdt: 数据加载开始时间（用于初始化）
    logger.info("开始多品种并行回测...")
    # 在沙盒环境下，我们将 max_workers 设为 1，避免多进程权限问题
    cta.backtest(test_symbols, sdt='20180101', edt='20251201', bar_sdt='20160101', max_workers=1)
    
    # 4. 汇总结果并生成综合收益报告
    from czsc.py.weight_backtest import WeightBacktest
    
    # 加载所有生成的 trader 对象
    traders = []
    # 找到最新的回测目录
    backtest_dirs = sorted(glob.glob(str(results_path / "backtest_*")))
    if not backtest_dirs:
        logger.error("未找到回测结果目录")
        sys.exit()
        
    latest_dir = backtest_dirs[-1]
    trader_files = glob.glob(os.path.join(latest_dir, "traders", "*.trader"))
    
    for file in trader_files:
        traders.append(czsc.dill_load(file))
    
    if traders:
        logger.info(f"正在对 {len(traders)} 个品种进行综合权重回测...")
        # 这里的 WeightBacktest.from_traders 是一个便捷方法，如果不存在则手动构建
        # 我们手动构建一个多品种的权重回测
        from czsc.py.weight_backtest import get_ensemble_weight
        
        dfws = []
        for trader in traders:
            dfw = get_ensemble_weight(trader, method='mean')
            dfws.append(dfw)
        
        dfw_all = pd.concat(dfws, ignore_index=True)
        wb = WeightBacktest(dfw_all, fee_rate=0.0002)
        wb.report(results_path / "report", title="缠论123类买卖点 - 多品种综合报告")
        
        logger.info(f"多品种回测完成，综合结果保存在：{results_path / 'report'}")
    else:
        logger.error("没有成功生成任何 trader 对象")
