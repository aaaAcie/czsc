# Moore ECharts Web

一个很薄的 FastAPI 服务，用 URL 参数动态生成摩尔缠论 ECharts HTML。

当前数据源来自本地 `research.get_raw_bars_origin`。后续如果要接远程 API，只需要替换 `moore_web.service.BarsProvider` 的实现，路由和绘图逻辑可以继续复用。

## 启动服务

在仓库根目录运行：

```bash
uv run --extra web uvicorn moore_web.app:app --reload --port 8866
```

启动后访问：

```text
http://127.0.0.1:8866/moore/603126
```

健康检查：

```text
http://127.0.0.1:8866/health
```

## 常用访问方式

默认展示最近 5 年：

```text
http://127.0.0.1:8866/moore/603126
```

显式指定最近 N 年：

```text
http://127.0.0.1:8866/moore/603126?years=5
http://127.0.0.1:8866/moore/603126?years=10
```

指定结束日期，开始日期按 `years` 自动向前推：

```text
http://127.0.0.1:8866/moore/603126?years=5&edt=2026-05-22
```

指定完整区间：

```text
http://127.0.0.1:8866/moore/603126?sdt=20181220&edt=20201030
```

强制重新生成，忽略已有缓存：

```text
http://127.0.0.1:8866/moore/603126?years=5&refresh=true
```

## 参数说明

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `symbol` | 必填 | 股票代码，来自路径 `/moore/{symbol}` |
| `sdt` | 空 | 开始日期，支持 `YYYYMMDD` 或 `YYYY-MM-DD` |
| `edt` | 今天 | 结束日期 |
| `years` | `5` | 未传 `sdt` 时，从 `edt` 向前取几年，范围 `1 ~ 30` |
| `fq` | `前复权` | 复权方式 |
| `refresh` | `false` | 是否忽略缓存并重新生成 HTML |
| `allow_initial_daily_ma_relax` | `false` | 是否放宽最早日线候选 MA 门槛 |
| `show_daily_shadow_b` | `true` | 是否展示 Daily Shadow B 参考层 |
| `enable_pre_round` | `true` | 是否启用 Pre-Round |
| `replay_centers_after_macro_swallow` | `false` | 宏观吞噬后是否重播中枢 |

## 数据范围兼容

请求会先按 `sdt ~ edt` 拉取数据。

如果这个区间拿不到数据，会自动回退到：

```text
19000101 ~ edt
```

然后使用实际拿到的第一根 K 线作为图表起点。也就是说：

```text
/moore/603126?years=5
```

如果本地或远程数据不足 5 年，会从数据本身的最早时间开始画。

显式传入 `sdt` 也一样：如果该 `sdt` 之前没有可用数据，会自动回退到实际最早数据点；只有 `edt` 之前完全没有数据时才返回 404。

## 缓存位置

生成的 HTML 会缓存到：

```text
moore_plots/web_cache/
```

缓存文件名只使用股票代码，例如：

```text
moore_plots/web_cache/603126.html
```

同一个股票再次请求会复用这份缓存。需要重新生成时，加：

```text
refresh=true
```
