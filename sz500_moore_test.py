# -*- coding: utf-8 -*-
"""
author: moore_czsc
describe: 基于中证500真实行情数据跑测 MooreCZSC 状态机
"""
import sys
import pandas as pd
from loguru import logger
from tqdm import tqdm
from czsc.connectors import research
from czsc.moore.analyze import MooreCZSC
from czsc.py.enum import Mark


def run_single_symbol(symbol: str, freq: str = "日线", sdt: str = '20150101', edt: str = '20230101'):
    """跑测单一标的并打印结果"""
    logger.info(f"开始拉取 {symbol} [{freq}] 行情数据 ({sdt} -> {edt})")
    
    try:
        bars = research.get_raw_bars(symbol, freq=freq, sdt=sdt, edt=edt)
        logger.info(f"成功获取 {len(bars)} 根 K 线, 开始注入 MooreCZSC 量化状态机...")
        
        # 将原始 K 线送入摩尔引擎
        engine = MooreCZSC(bars)
        
        # 结果统计
        tk_count = len(engine.turning_ks)
        seg_count = len(engine.segments)
        
        # 中枢分类统计
        total_centers = 0
        visible_centers = 0
        invisible_centers = 0
        for seg in engine.segments:
            for c in seg.centers:
                total_centers += 1
                if c.is_visible:
                    visible_centers += 1
                else:
                    invisible_centers += 1
                    
        logger.success("-" * 50)
        logger.success(f"标的 [{symbol}] - MooreCZSC 状态机跑盘成果:")
        logger.success(f"1. 确认顶底极值 (TurningK): {tk_count} 个")
        logger.success(f"2. 确立摩尔线段 (MooreSegment): {seg_count} 个")
        logger.success(f"3. 挂载各类中枢 (MooreCenter): 共 {total_centers} 个")
        logger.success(f"   - 肉眼可见中枢 (VISIBLE): {visible_centers} 个")
        logger.success(f"   - 非肉眼慢轨中枢 (INVISIBLE): {invisible_centers} 个")
        
        # 挑选最后一条线段打印其内部结构
        if engine.segments:
            last_seg = engine.segments[-1]
            logger.info("*" * 50)
            logger.info(f"最后一断本质线段剖析: {last_seg}")
            logger.info(f" -> 起始K线: {last_seg.start_k}")
            logger.info(f" -> 终了K线: {last_seg.end_k}")
            for idx, c in enumerate(last_seg.centers):
                logger.info(f" -> 内部中枢[{idx}]: {c}")
                
        logger.success("-" * 50)

    except Exception as e:
        logger.error(f"处理 {symbol} 发生异常: {e}")
        import traceback
        traceback.print_exc()

if __name__ == '__main__':
    # 为了防止命令行输出太乱，设置 loguru 级别
    logger.remove()
    logger.add(sys.stdout, level="INFO")
    
    # 获取中证 500 的第一只股票进行单例测试
    symbols = research.get_symbols('中证500成分股')[:30]
    if symbols:
        target_symbol = symbols[0]
        # 这里我们就使用日线来做宏观级别的线段推演测试
        run_single_symbol(target_symbol, freq="日线", sdt='20100101', edt='20240101')
    else:
        logger.error("未能从中证500获取到任何标的！请检查网络或配置。")
