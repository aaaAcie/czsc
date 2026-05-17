# -*- coding: utf-8 -*-
"""
author: zengbin93
email: zeng_bin8888@163.com
create_dt: 2023/9/10 19:45
describe: 缠论123类买卖点策略示例
"""
import sys
import os
sys.path.insert(0, '.')
sys.path.insert(0, '..')

import czsc
from pathlib import Path
from loguru import logger
from czsc.connectors import research
from czsc import Event, Position, CTAResearch

def create_123_pos(symbol, **kwargs):
    """创建缠论123类买卖点持仓策略"""
    base_freq = kwargs.get('base_freq', '30分钟')
    
    # 定义开多事件（一买、二买、三买）
    opens = [
        {
            "operate": "开多",
            "name": "一买",
            "signals_all": [
                # 结构确认：MACD辅助的一买信号
                f"{base_freq}_D1MACD12#26#9_BS1辅助V221201_一买_任意_任意_0",
                # 指标确认：增加“绿抽脚”形态（MACD绿柱缩短），确认下跌动能衰减
                f"{base_freq}_D1K#MACD12#26#9形态_BS辅助V221208_绿抽脚_任意_任意_0"
            ],
        },
        {
            "operate": "开多",
            "name": "二买",
            "signals_all": [
                # 结构确认：MACD辅助的二买信号
                f"{base_freq}_D1MACD12#26#9_BS2辅助V221201_二买_金叉_任意_0",
                # 指标确认：增加成交量放量确认，确保有资金入场
                f"{base_freq}_D1K5B_放量V221112_是_任意_任意_0"
            ],
        },
        {
            "operate": "开多",
            "name": "三买",
            "signals_all": [
                # 结构确认：标准的线段/中枢三买（回抽不破中枢）
                f"{base_freq}_D1#SMA#34_BS3辅助V230319_三买_均线底分_任意_0",
                # 指标确认：增加“均线新高”，确认趋势强度
                f"{base_freq}_D1#SMA#34_BS3辅助V230319_三买_均线新高_任意_0"
            ],
        }
    ]

    # 定义开空事件（一卖、二卖、三卖）
    # 注意：在 CZSC 中，开空通常用于做空或者对冲，如果是股票策略，通常只关注开多和对应的平多
    exits = [
        {
            "operate": "平多",
            "name": "一卖",
            "signals_all": [
                # 结构确认：MACD辅助的一卖信号
                f"{base_freq}_D1MACD12#26#9_BS1辅助V221201_一卖_任意_任意_0",
                # 指标确认：红缩柱（MACD红柱缩短），确认上涨动能衰减
                f"{base_freq}_D1K#MACD12#26#9形态_BS辅助V221208_红缩柱_任意_任意_0"
            ],
        },
        {
            "operate": "平多",
            "name": "二卖",
            "signals_all": [
                # 结构确认：MACD辅助的二卖信号
                f"{base_freq}_D1MACD12#26#9_BS2辅助V221201_二卖_死叉_任意_0",
            ],
        },
        {
            "operate": "平多",
            "name": "三卖",
            "signals_all": [
                # 结构确认：标准的三卖结构
                f"{base_freq}_D1#SMA#34_BS3辅助V230319_三卖_均线顶分_任意_0",
            ],
        }
    ]

    pos = Position(
        name=f"{base_freq}123类买卖点",
        symbol=symbol,
        opens=[Event.load(x) for x in opens],
        exits=[Event.load(x) for x in exits],
        interval=3600 * 4,
        timeout=16 * 100, # 增加超时限制
        stop_loss=500,    # 5% 止损
    )
    return pos

class Chan123Strategy(czsc.CzscStrategyBase):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    @property
    def positions(self):
        # 根据 kwargs 中的 base_freqs 来创建持仓策略，默认为 ['日线']
        # 这样可以避免在 positions 属性中调用 self.base_freq 导致的递归
        base_freqs = self.kwargs.get('base_freqs', ['日线'])
        pos_list = [create_123_pos(self.symbol, base_freq=freq) for freq in base_freqs]
        return pos_list

if __name__ == '__main__':
    results_path = Path('results/缠论123策略')
    results_path.mkdir(exist_ok=True, parents=True)
    
    # 获取数据
    # symbols = research.get_symbols('中证500成分股')
    symbols = research.get_symbols('originData')

    if not symbols:
        logger.error("没有找到数据，请检查 /Users/akuai/Documents/缠论/allData 路径")
        sys.exit()
        
    symbol = symbols[9]
    # 尝试获取股票名称
    _bars = research.get_raw_bars(symbol, freq='日线', sdt='20210101', edt='20210105')
    symbol_name = _bars[0].symbol if _bars else symbol
    logger.info(f"开始测试品种：{symbol} ({symbol_name})")
    
    # 初始化策略：强制只使用日线级别，适配您的日线 CSV 数据
    tactic = Chan123Strategy(symbol=symbol, base_freqs=['日线'])
    
    # 获取K线数据：明确指定 freq='日线'
    bars = research.get_raw_bars(symbol, freq='日线', sdt='20160501', edt='20251201')
    
    if not bars:
        logger.error(f"未能获取到 {symbol} 的K线数据")
        sys.exit()

    # 1. 执行回测（不生成HTML，速度快）
    logger.info("正在执行回测...")
    trader = tactic.backtest(bars, sdt='20170101')

    # 2. 生成收益报告
    logger.info("正在生成收益报告...")
    wb = trader.weight_backtest(fee_rate=0.0002)
    
    # 将股票名称和编号放入图表标题
    report_title = f"{symbol_name} - 缠论123类买卖点回测报告"
    wb.report(results_path / "report", title=report_title)
    
    # 3. 如果你想看具体的交易快照（HTML），可以取消下面这一行的注释
    # tactic.replay(bars, sdt='20200101', res_path=results_path / "replay", refresh=True)

    logger.info(f"测试完成，结果保存在：{results_path}")
