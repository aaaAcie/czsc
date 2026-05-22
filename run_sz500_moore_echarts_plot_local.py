from czsc.cli import main

# 现在有两层概念：

# 30min 是原始 30 分钟数据源
# daily_source 决定 get_raw_bars_origin(...) 这层日线数据从哪来
# 所以现在的行为是：

# --daily-source origin：直接读 originData 里的日线文件，不做合成
# --daily-source 30m：从 allData/30min 把 30 分钟 K 线合成日线
# --daily-source auto：优先 originData，没有就回退到 30 分钟合成
# 如果你只想拿日线，最直接就是：
# uv run --no-sync sz500-local --data-root E:\stockData --daily-source origin

# 数据源取 30 分钟进行日线合成
# uv run --no-sync sz500-local --data-root E:\stockData --daily-source 30m
if __name__ == "__main__":
    raise SystemExit(main())
