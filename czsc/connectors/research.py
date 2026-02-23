# -*- coding: utf-8 -*-
"""
author: zengbin93
email: zeng_bin8888@163.com
create_dt: 2023/3/5 20:45
describe: CZSC投研数据共享接口

投研数据共享说明（含下载地址）：https://s0cqcxuy3p.feishu.cn/wiki/wikcnzuPawXtBB7Cj7mqlYZxpDh
"""
import os
import czsc
import glob
import pandas as pd
from datetime import datetime
from pathlib import Path


# 投研共享数据的本地缓存路径，需要根据实际情况修改
cache_path = os.environ.get("czsc_research_cache", r"/Users/akuai/缠论/allData")
if not os.path.exists(cache_path):
    raise ValueError(
        f"请设置环境变量 czsc_research_cache 为投研共享数据的本地缓存路径，当前路径不存在：{cache_path}。\n\n"
        f"投研数据共享说明（含下载地址）：https://s0cqcxuy3p.feishu.cn/wiki/wikcnzuPawXtBB7Cj7mqlYZxpDh"
    )


def get_groups():
    """获取投研共享数据的分组信息

    :return: 分组信息
    """
    # return ["A股主要指数", "A股场内基金", "中证500成分股", "期货主力"]
    return ["中证500成分股", "originData", "test"]



def get_symbols(name, **kwargs):
    """获取指定分组下的所有标的代码

    :param name: 分组名称，可选值：'A股主要指数', 'A股场内基金', '中证500成分股', '期货主力', 'originData' 等
    :param kwargs:
    :return:
    """
    if name.upper() == "ALL":
        p_files = glob.glob(os.path.join(cache_path, "**", "*.parquet"), recursive=True)
        c_files = glob.glob(os.path.join(cache_path, "**", "*.csv"), recursive=True)
        files = p_files + c_files
    else:
        p_files = glob.glob(os.path.join(cache_path, name, "*.parquet"))
        c_files = glob.glob(os.path.join(cache_path, name, "*.csv"))
        files = p_files + c_files
    
    symbols = []
    for x in files:
        # 处理类似 000001.SH.csv 或 000001.csv 的情况
        base = os.path.basename(x)
        if base.endswith('.parquet'):
            symbols.append(base.replace('.parquet', ''))
        elif base.endswith('.csv'):
            symbols.append(base.replace('.csv', ''))
            
    return sorted(list(set(symbols)))


def get_raw_bars(symbol, freq, sdt, edt, fq="前复权", **kwargs):
    """获取 CZSC 库定义的标准 RawBar 对象列表

    :param symbol: 标的代码
    :param freq: 周期，支持 Freq 对象，或者字符串，如
            '1分钟', '5分钟', '15分钟', '30分钟', '60分钟', '日线', '周线', '月线', '季线', '年线'
    :param sdt: 开始时间
    :param edt: 结束时间
    :param fq: 除权类型，投研共享数据默认都是后复权，不需要再处理
    :param kwargs:
    :return:
    """
    raw_bars = kwargs.get("raw_bars", True)
    kwargs["fq"] = fq
    
    # 优先查找 parquet 文件
    p_files = list(Path(cache_path).rglob(f"{symbol}.parquet"))
    if p_files:
        file = p_files[0]
        freq = czsc.Freq(freq)
        kline = pd.read_parquet(file)
        if "dt" not in kline.columns:
            kline["dt"] = pd.to_datetime(kline["datetime"])
        kline = kline[(kline["dt"] >= pd.to_datetime(sdt)) & (kline["dt"] <= pd.to_datetime(edt))]
        if kline.empty:
            return []

        df = kline.copy()
        if symbol in ["SFIC9001", "SFIF9001", "SFIH9001"]:
            # 股指：仅保留 09:31 - 11:30, 13:01 - 15:00
            dt1 = datetime.strptime("09:31:00", "%H:%M:%S")
            dt2 = datetime.strptime("11:30:00", "%H:%M:%S")
            c1 = (df["dt"].dt.time >= dt1.time()) & (df["dt"].dt.time <= dt2.time())

            dt3 = datetime.strptime("13:01:00", "%H:%M:%S")
            dt4 = datetime.strptime("15:00:00", "%H:%M:%S")
            c2 = (df["dt"].dt.time >= dt3.time()) & (df["dt"].dt.time <= dt4.time())

            df = df[c1 | c2].copy().reset_index(drop=True)

        _bars = czsc.resample_bars(df, freq, raw_bars=raw_bars, base_freq="1分钟")
        return _bars

    # 如果没有 parquet，查找 csv 文件
    c_files = list(Path(cache_path).rglob(f"{symbol}.csv"))
    if c_files:
        file_path = c_files[0]
        bars = format_csv_kline(file_path)
        # 过滤时间范围
        sdt_dt = pd.to_datetime(sdt)
        edt_dt = pd.to_datetime(edt)
        bars = [x for x in bars if sdt_dt <= x.dt <= edt_dt]
        return bars

    return []


def format_csv_kline(file_path, **kwargs):
    """将特定格式的 CSV 转换为 CZSC 标准 RawBar 列表

    CSV 格式要求：
    股票代码, 股票名称, 交易日, 开盘价, 最高价, 最低价, 收盘价, 前收盘价, 涨跌额, 涨跌幅（%）, 成交量（手）, 成交额（千元）, ...

    :param file_path: CSV 文件路径
    :param kwargs:
    :return: list of RawBar
    """
    encoding = kwargs.get('encoding', None)
    sep = kwargs.get('sep', None)
    
    if encoding is None:
        # 尝试常用编码
        for enc in ['utf-8', 'gbk', 'utf-8-sig', 'gb18030']:
            try:
                # 自动检测分隔符
                if sep is None:
                    df = pd.read_csv(file_path, encoding=enc, nrows=5)
                    if df.shape[1] <= 1:
                        df = pd.read_csv(file_path, encoding=enc, sep='\t', nrows=5)
                        if df.shape[1] > 1:
                            sep = '\t'
                        else:
                            continue
                    else:
                        sep = ','
                
                df = pd.read_csv(file_path, encoding=enc, sep=sep)
                break
            except:
                continue
        else:
            raise ValueError(f"无法读取文件 {file_path}，请检查编码和格式")
    else:
        df = pd.read_csv(file_path, encoding=encoding, sep=sep if sep else ',')
    
    # 定义列名映射
    col_map = {
        '股票代码': 'symbol',
        '交易日': 'dt',
        '开盘价': 'open',
        '最高价': 'high',
        '最低价': 'low',
        '收盘价': 'close',
        '成交量（手）': 'vol',
        '成交额（千元）': 'amount',
    }
    
    # 检查必需列是否存在
    for col in col_map.keys():
        if col not in df.columns:
            raise ValueError(f"CSV 文件缺少必需列：{col}")
            
    # 去除包含空值的行
    df = df.dropna(subset=list(col_map.keys()))
    
    # 重命名列
    df = df[list(col_map.keys())].rename(columns=col_map)
    
    # 转换日期格式
    df['dt'] = pd.to_datetime(df['dt'], format='%Y%m%d')
    
    # 确保数据按时间正序排列
    df = df.sort_values('dt', ascending=True).reset_index(drop=True)
    
    # 转换成交量（手 -> 股）
    df['vol'] = df['vol'] * 100
    
    # 转换成交额（千元 -> 元）
    df['amount'] = df['amount'] * 1000
    
    # 转换为 RawBar 列表
    bars = czsc.format_standard_kline(df, freq=czsc.Freq.D)
    return bars


def get_raw_bars_csv(symbol, freq, sdt, edt, **kwargs):
    """从本地 CSV 目录读取数据，对接 CTAResearch

    :param symbol: 标的代码
    :param freq: 周期
    :param sdt: 开始时间
    :param edt: 结束时间
    :param kwargs:
        - data_path: CSV 文件所在目录，默认为 cache_path
    :return: list of RawBar
    """
    data_path = kwargs.get("data_path", cache_path)
    
    # 参考 get_symbols 的逻辑，支持在子目录中搜索
    files = glob.glob(os.path.join(data_path, "**", f"{symbol}.csv"), recursive=True)
    if not files:
        return []
    
    file_path = files[0]

    # 使用刚才定义的 format_csv_kline
    bars = format_csv_kline(file_path)

    # 过滤时间范围
    import pandas as pd
    sdt_dt = pd.to_datetime(sdt)
    edt_dt = pd.to_datetime(edt)
    bars = [x for x in bars if sdt_dt <= x.dt <= edt_dt]

    return bars
