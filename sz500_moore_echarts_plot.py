# -*- coding: utf-8 -*-
"""
author: moore_czsc
describe: 用 pyecharts (Apache ECharts) 实现摩尔缠论 K 线结构图。
          功能与 sz500_moore_plot.py (Plotly版) 完全对齐。

交互特性：
  - 🖱️ 滚轮放大 → K 线变宽，可见区间自动收窄（time-range zoom，同花顺风格）
  - 🖱️ 拖拽平移
  - 🖱️ Hover 悬停显示 OHLC
  - 🔘 图例点击可独立显示/隐藏各类元素
  - 📤 输出独立 .html，浏览器直接打开
"""
import os
from dataclasses import dataclass

import pandas as pd
from loguru import logger

from pyecharts import options as opts
from pyecharts.charts import Kline, Line, Bar, Grid, Scatter
from pyecharts.commons.utils import JsCode

from czsc.connectors import research
from czsc.moore.analyze import MooreCZSC
from czsc.py.enum import Direction, Mark


# ─────────────────────────────────────────────────────────────────────
# 工具函数
# ─────────────────────────────────────────────────────────────────────
def _dt_str(dt) -> str:
    return pd.Timestamp(dt).strftime("%Y-%m-%d")


def _ma(series: pd.Series, n: int) -> list:
    return [round(v, 3) if not pd.isna(v) else None
            for v in series.rolling(n).mean()]


def _dummy_line(dates: list, name: str, icon_color: str) -> Line:
    """创建一个不可见的 Line 系列，用来承载 markLine / markArea 并在图例中有独立条目。"""
    return (
        Line()
        .add_xaxis([dates[0], dates[-1]])
        .add_yaxis(
            name, [None, None],
            symbol="none",
            linestyle_opts=opts.LineStyleOpts(opacity=0),
            label_opts=opts.LabelOpts(is_show=False),
            itemstyle_opts=opts.ItemStyleOpts(color=icon_color),
        )
    )


def _seg_markline_data(segs: list, color: str, width: float, alpha: float = 0.85) -> list:
    """把线段列表转为 markLine data（支持 per-item lineStyle，用虚实区分 is_perfect）。"""
    data = []
    for seg in segs:
        tp = "solid" if seg.is_perfect else "dashed"
        data.append([
            {"coord": [_dt_str(seg.start_k.dt), seg.start_k.price],
             "symbol": "none",
             "lineStyle": {"color": color, "width": width, "type": tp, "opacity": alpha}},
            {"coord": [_dt_str(seg.end_k.dt), seg.end_k.price],
             "symbol": "none"},
        ])
    return data


# ─────────────────────────────────────────────────────────────────────
# 核心绘图函数
# ─────────────────────────────────────────────────────────────────────
def plot_moore_structure_echarts(
    bars: list,
    engine: MooreCZSC,
    output_file: str = "moore_czsc_echarts.html",
    title: str = "摩尔缠论结构图 (ECharts)",
):
    # ── 1. 基础数据 ───────────────────────────────────────────────────
    dates, ohlcv = [], []
    for b in bars:
        dates.append(_dt_str(b.dt))
        ohlcv.append([round(b.open, 3), round(b.close, 3),
                      round(b.low, 3), round(b.high, 3)])

    close_s = pd.Series([b.close for b in bars])
    ma5_list  = _ma(close_s, 5)
    ma34_list = _ma(close_s, 34)
    vol_list  = [b.vol for b in bars]

    # ── 2. 引擎数据提取 ───────────────────────────────────────────────
    macro_segments = getattr(engine, "segments", [])
    swallowed_micro_ids: set = set()
    for mseg in macro_segments:
        swallowed_micro_ids.update(mseg.cache.get("swallow_internal_micro_ids", []))

    micro_segments = getattr(engine, "micro_segments", [])
    segment_centers = [ct for seg in engine.segments for ct in seg.centers]
    display_tks = getattr(engine, "micro_turning_ks", engine.turning_ks)
    display_centers = getattr(engine, "all_centers", [])
    ghost_forks = getattr(engine, "ghost_forks", [])
    refreshed_segments = getattr(engine, "refreshed_segments", [])

    # ── 3. K0 + 确认K markPoint（顶底改用 Scatter，见下方 overlay_series）────
    confirm_ks = [ct.confirm_k  for ct in segment_centers if ct.confirm_k]
    anchor_k0s = [ct.anchor_k0  for ct in segment_centers if ct.anchor_k0]

    # 只把 K0 / 确认K 放入 markPoint（无须文字标签）
    tk_points = []
    for ck in confirm_ks:
        tk_points.append(opts.MarkPointItem(
            name="CK", coord=[_dt_str(ck.dt), ck.close],
            symbol="diamond", symbol_size=9,
            itemstyle_opts=opts.ItemStyleOpts(color="#CE93D8"),
        ))
    for k0 in anchor_k0s:
        tk_points.append(opts.MarkPointItem(
            name="K0", coord=[_dt_str(k0.dt), k0.close],
            symbol="rect", symbol_size=[8, 8],
            itemstyle_opts=opts.ItemStyleOpts(color="#A1887F"),
        ))

    # 顶底极值 → 两个 Scatter 系列，分别控制 label 位置
    top_tks = [(i, tk) for i, tk in enumerate(display_tks) if tk.mark == Mark.G]
    bot_tks = [(i, tk) for i, tk in enumerate(display_tks) if tk.mark == Mark.D]

    def _tk_scatter(name, tk_list, label_pos):
        if not tk_list:
            return None
        xs = [_dt_str(tk.dt) for (_, tk) in tk_list]
        # y 只传价格
        ys = [tk.price for (_, tk) in tk_list]
        # 标签文字预编译为 JS 数组，用 dataIndex 索引取值
        labels_js = str([f"mV{i}{'T' if tk.mark == Mark.G else 'B'}" for (i, tk) in tk_list])
        formatter = JsCode(
            "function(p){ var L=" + labels_js + "; return L[p.dataIndex] || ''; }"
        )
        return (
            Scatter()
            .add_xaxis(xs)
            .add_yaxis(
                name, ys,
                symbol="circle", symbol_size=7,
                label_opts=opts.LabelOpts(
                    is_show=True,
                    position=label_pos,
                    color="#000000",
                    font_size=14,
                    font_weight="bold",
                    formatter=formatter,
                ),
                itemstyle_opts=opts.ItemStyleOpts(
                    color="#FFFFFF", border_color="#000000", border_width=2
                ),
            )
            .set_global_opts(
                xaxis_opts=opts.AxisOpts(type_="category"),
                legend_opts=opts.LegendOpts(is_show=False),
            )
        )

    # ── 4. 线段数据分类 ───────────────────────────────────────────────
    def _is_sw(seg):
        mid_s = seg.start_k.cache.get("micro_id")
        mid_e = seg.end_k.cache.get("micro_id")
        return (mid_s in swallowed_micro_ids) or (mid_e in swallowed_micro_ids)

    micro_all    = [s for s in micro_segments]         # 微观全部，不区分被吞与否
    macro_all    = [s for s in macro_segments]         # 宏观全部，不区分吸噬与否
    macro_swallow = [s for s in macro_segments if s.cache.get("is_macro_swallow", False)]  # 仅用于宽度计算

    # 宏观吸噬段：只用于宽度前是否加粗（现已合并，保留宏观吸噬列表备用）
    show_ghost_overlay = not (micro_segments and swallowed_micro_ids)

    # 幽灵枝丫
    ghost_fork_data = []
    if ghost_forks and show_ghost_overlay:
        for fork_tk, consumed in ghost_forks:
            path = [fork_tk] + consumed
            for i in range(len(path) - 1):
                ta, tb = path[i], path[i + 1]
                tp = "solid" if tb.is_perfect else "dashed"
                ghost_fork_data.append([
                    {"coord": [_dt_str(ta.dt), ta.price], "symbol": "none",
                     "lineStyle": {"color": "#AAAAAA", "width": 1.5, "type": tp, "opacity": 0.6}},
                    {"coord": [_dt_str(tb.dt), tb.price], "symbol": "none"},
                ])

    # 演变刷新路径：统一灰色虚线
    refresh_data = []
    for seg in refreshed_segments:
        refresh_data.append([
            {"coord": [_dt_str(seg.start_k.dt), seg.start_k.price], "symbol": "none",
             "lineStyle": {"color": "#999999", "width": 1, "type": "dashed", "opacity": 0.5}},
            {"coord": [_dt_str(seg.end_k.dt), seg.end_k.price], "symbol": "none"},
        ])

    # ── 5. 中枢 markArea 数据分类 ─────────────────────────────────────
    logger.info(f"中枢总数（含幽灵）: {len(display_centers)}")

    center_vis_data, center_hid_data, center_ghost_data = [], [], []
    for c_idx, ct in enumerate(display_centers):
        if not ct.start_dt or not ct.end_dt:
            continue
        is_ghost  = getattr(ct, "is_ghost", False)
        y_lo = ct.lower_rail
        y_hi = ct.upper_rail
        method = getattr(ct, "method", "?")
        y_mid = getattr(ct, "center_line", (y_lo + y_hi) / 2)

        if is_ghost:
            fill = "rgba(160,160,160,0.12)"
            border = "#999999"
            c_type = "幽灵"
            target = center_ghost_data
        elif ct.is_visible:
            fill = "rgba(233,30,99,0.10)"
            border = "#E91E63"
            c_type = "肉眼"
            target = center_vis_data
        else:
            fill = "rgba(46,204,113,0.10)"
            border = "#2ECC71"
            c_type = "隐性"
            target = center_hid_data

        # markArea 标注放在框上方（position="top"），清晰不遮挡 K 线
        c_type_str = "幽灵" if is_ghost else ("肉眼" if ct.is_visible else "隐性")
        label_txt = f"#{c_idx} {c_type_str}-{method} [{y_lo:.2f},{y_hi:.2f}]"

        target.append([
            {
                "xAxis": _dt_str(ct.start_dt), "yAxis": y_lo,
                "label": {
                    "show": True,
                    "position": "top",
                    "formatter": label_txt,
                    "fontSize": 8,
                    "color": border,
                    "fontWeight": "bold",
                },
                "itemStyle": {
                    "color": fill,
                    "borderColor": border,
                    "borderWidth": 1.5,
                    "borderType": "dashed" if is_ghost else "solid",
                },
            },
            {"xAxis": _dt_str(ct.end_dt), "yAxis": y_hi},
        ])


    # ── 6. 构建各图例系列 ─────────────────────────────────────────────
    def _seg_series(name, seg_list, color, width, alpha=0.85):
        data = _seg_markline_data(seg_list, color, width, alpha)
        if not data:
            return None
        return (
            _dummy_line(dates, name, color)
            .set_series_opts(markline_opts=opts.MarkLineOpts(
                is_silent=True, data=data,
                label_opts=opts.LabelOpts(is_show=False),
            ))
        )

    def _raw_seg_series(name, raw_data, color):
        if not raw_data:
            return None
        return (
            _dummy_line(dates, name, color)
            .set_series_opts(markline_opts=opts.MarkLineOpts(
                is_silent=True, data=raw_data,
                label_opts=opts.LabelOpts(is_show=False),
            ))
        )

    def _center_series(name, area_data, color):
        if not area_data:
            return None
        return (
            _dummy_line(dates, name, color)
            .set_series_opts(markarea_opts=opts.MarkAreaOpts(
                is_silent=False, data=area_data,
                label_opts=opts.LabelOpts(is_show=True),
            ))
        )

    overlay_series = []

    # MA 线（直接用 add_yaxis 叠加，不需要 dummy）
    line_ma = (
        Line()
        .add_xaxis(dates)
        .add_yaxis("MA5",  ma5_list,  symbol="none", is_smooth=True,
                   linestyle_opts=opts.LineStyleOpts(width=1.2, color="#F39C12"),
                   label_opts=opts.LabelOpts(is_show=False),
                   itemstyle_opts=opts.ItemStyleOpts(color="#F39C12"))
        .add_yaxis("MA34", ma34_list, symbol="none", is_smooth=True,
                   linestyle_opts=opts.LineStyleOpts(width=1.2, color="#2980B9"),
                   label_opts=opts.LabelOpts(is_show=False),
                   itemstyle_opts=opts.ItemStyleOpts(color="#2980B9"))
        .set_global_opts(xaxis_opts=opts.AxisOpts(type_="category", is_scale=True))
    )
    overlay_series.append(line_ma)

    # 微观线段（全部合并，统一灰色）
    s = _seg_series("微观线段", micro_all, "#AAAAAA", 1.2, alpha=0.6)
    if s:
        overlay_series.append(s)

    # 宏观线段（全部合并，黑色，吞噬段线宽略大）
    def _macro_all_data():
        data = []
        for seg in macro_all:
            is_sw = seg.cache.get("is_macro_swallow", False)
            tp = "solid" if seg.is_perfect else "dashed"
            w = 4 if is_sw else 2.5
            data.append([
                {"coord": [_dt_str(seg.start_k.dt), seg.start_k.price],
                 "symbol": "none",
                 "lineStyle": {"color": "#000000", "width": w, "type": tp, "opacity": 0.92}},
                {"coord": [_dt_str(seg.end_k.dt), seg.end_k.price], "symbol": "none"},
            ])
        return data
    s = _raw_seg_series("宏观线段", _macro_all_data(), "#000000")
    if s:
        overlay_series.append(s)

    s = _raw_seg_series("演变路径", refresh_data, "#BBBBBB")
    if s:
        overlay_series.append(s)

    s = _raw_seg_series("幽灵枝丫", ghost_fork_data, "#AAAAAA")
    if s:
        overlay_series.append(s)

    # 中枢
    for name, data, color in [
        ("常规肉眼中枢", center_vis_data,   "#E91E63"),
        ("常规隐性中枢", center_hid_data,   "#2ECC71"),
        ("幽灵中枢",     center_ghost_data, "#999999"),
    ]:
        s = _center_series(name, data, color)
        if s:
            overlay_series.append(s)

    # 顶底极值 Scatter（顶标签在上，底标签在下）
    sc_top = _tk_scatter("微观顶极值", top_tks, "top")
    sc_bot = _tk_scatter("微观底极值", bot_tks, "bottom")
    if sc_top:
        overlay_series.append(sc_top)
    if sc_bot:
        overlay_series.append(sc_bot)

    # ── 7. 主 K 线 ────────────────────────────────────────────────────
    kline = (
        Kline()
        .add_xaxis(dates)
        .add_yaxis(
            "K线", ohlcv,
            itemstyle_opts=opts.ItemStyleOpts(
                color="#D32F2F", color0="#2E7D32",
                border_color="#D32F2F", border_color0="#2E7D32",
            ),
        )
        .set_global_opts(
            title_opts=opts.TitleOpts(
                title=title,
                title_textstyle_opts=opts.TextStyleOpts(font_size=14, color="#222"),
            ),
            tooltip_opts=opts.TooltipOpts(
                trigger="axis",
                axis_pointer_type="cross",
                formatter=JsCode("""function(params){
                    if(!params || params.length === 0) return '';
                    var k, m5, m34;
                    for(var i=0; i<params.length; i++){
                        var p = params[i];
                        if(p.seriesName === 'K线'){ k = p; }
                        else if(p.seriesName === 'MA5'){ m5 = Array.isArray(p.value) ? p.value[1] : p.value; }
                        else if(p.seriesName === 'MA34'){ m34 = Array.isArray(p.value) ? p.value[1] : p.value; }
                    }
                    if(!k || !k.value) return '';
                    var d = k.value;
                    var res = '<b>' + k.axisValue + '</b><br/>' +
                              '开: ' + d[1] + '  收: <b>' + d[2] + '</b><br/>' +
                              '高: ' + d[4] + '  低: ' + d[3];
                    if(m5 != null && !isNaN(parseFloat(m5))){
                        res += '<br/><span style=\"color:#F39C12\">● MA5: ' + parseFloat(m5).toFixed(2) + '</span>';
                    }
                    if(m34 != null && !isNaN(parseFloat(m34))){
                        res += '  <span style=\"color:#2980B9\">● MA34: ' + parseFloat(m34).toFixed(2) + '</span>';
                    }
                    return res;
                }"""),
            ),
            toolbox_opts=opts.ToolboxOpts(
                pos_left="center",
                pos_top="1%",
                feature={
                    "myZoomIn": {
                        "show": True,
                        "title": "放大",
                        "icon": "path://M19 13h-6v6h-2v-6H5v-2h6V5h2v6h6v2z",
                        "onclick": JsCode("""
                            function (model, api) {
                                var opt = api.getOption();
                                var dz = opt.dataZoom[0];
                                var start = dz.start;
                                var end = dz.end;
                                var center = (start + end) / 2;
                                var span = (end - start) * 0.8;
                                if (span < 0.1) span = 0.1;
                                var newStart = Math.max(0, center - span / 2);
                                var newEnd = Math.min(100, center + span / 2);
                                api.dispatchAction({
                                    type: 'dataZoom',
                                    start: newStart,
                                    end: newEnd
                                });
                            }
                        """)
                    },
                    "myZoomOut": {
                        "show": True,
                        "title": "缩小",
                        "icon": "path://M19 13H5v-2h14v2z",
                        "onclick": JsCode("""
                            function (model, api) {
                                var opt = api.getOption();
                                var dz = opt.dataZoom[0];
                                var start = dz.start;
                                var end = dz.end;
                                var center = (start + end) / 2;
                                var span = (end - start) / 0.8;
                                if (span > 100) span = 100;
                                var newStart = Math.max(0, center - span / 2);
                                var newEnd = Math.min(100, center + span / 2);
                                api.dispatchAction({
                                    type: 'dataZoom',
                                    start: newStart,
                                    end: newEnd
                                });
                            }
                        """)
                    },
                    "restore": {"show": True, "title": "重置"},
                    "saveAsImage": {"show": True, "title": "导出图片"},
                }
            ),
            datazoom_opts=[
                opts.DataZoomOpts(
                    is_show=False, type_="inside",
                    xaxis_index=[0, 1],
                    range_start=75, range_end=100,
                ),
                opts.DataZoomOpts(
                    is_show=True, type_="slider",
                    xaxis_index=[0, 1],
                    range_start=75, range_end=100,
                    pos_bottom="55px",
                ),
            ],
            legend_opts=opts.LegendOpts(
                is_show=True,
                pos_top="5%", pos_right="2%",
                orient="vertical",
                textstyle_opts=opts.TextStyleOpts(font_size=11),
            ),
            yaxis_opts=opts.AxisOpts(
                is_scale=True,
                splitarea_opts=opts.SplitAreaOpts(is_show=True),
                axislabel_opts=opts.LabelOpts(formatter="{value}"),
                splitline_opts=opts.SplitLineOpts(
                    is_show=True,
                    linestyle_opts=opts.LineStyleOpts(type_="dashed", opacity=0.4),
                ),
            ),
            xaxis_opts=opts.AxisOpts(
                type_="category", is_scale=True,
                boundary_gap=True,
                axisline_opts=opts.AxisLineOpts(is_on_zero=False),
                splitline_opts=opts.SplitLineOpts(is_show=False),
            ),
        )
        .set_series_opts(
            markpoint_opts=opts.MarkPointOpts(
                data=tk_points,
                label_opts=opts.LabelOpts(is_show=False),  # K0/CK 不显示文字标签
            ),
        )
    )

    # ── 8. 叠加所有系列 ───────────────────────────────────────────────
    for s in overlay_series:
        kline.overlap(s)

    # ── 9. 成交量 Bar ─────────────────────────────────────────────────
    vol_colors = [
        "#FFCDD2" if ohlcv[i][1] >= ohlcv[i][0] else "#C8E6C9"
        for i in range(len(ohlcv))
    ]
    bar = (
        Bar()
        .add_xaxis(dates)
        .add_yaxis(
            "成交量", vol_list,
            label_opts=opts.LabelOpts(is_show=False),
            itemstyle_opts=opts.ItemStyleOpts(
                color=JsCode("function(p){var c=" + str(vol_colors) + "; return c[p.dataIndex] || '#BBBBBB';}")
            ),
        )
        .set_global_opts(
            xaxis_opts=opts.AxisOpts(
                type_="category", is_scale=True,
                axislabel_opts=opts.LabelOpts(is_show=False),
                splitline_opts=opts.SplitLineOpts(is_show=False),
            ),
            yaxis_opts=opts.AxisOpts(
                is_scale=True,
                axislabel_opts=opts.LabelOpts(
                    formatter=JsCode(
                        "function(v){if(v>=1e8)return (v/1e8).toFixed(1)+'亿';"
                        "if(v>=1e4)return (v/1e4).toFixed(0)+'万';return v;}"
                    )
                ),
            ),
            legend_opts=opts.LegendOpts(is_show=False),
        )
    )

    # ── 10. Grid 布局 ─────────────────────────────────────────────────
    grid = (
        Grid(init_opts=opts.InitOpts(
            width="100%",
            height="860px",
            page_title=title,
            bg_color="#FAFAFA",
        ))
        .add(
            kline,
            grid_opts=opts.GridOpts(
                pos_left="4%", pos_right="16%",
                pos_top="8%", pos_bottom="26%",
            ),
        )
        .add(
            bar,
            grid_opts=opts.GridOpts(
                pos_left="4%", pos_right="16%",
                pos_top="76%", pos_bottom="8%",
            ),
        )
    )

    grid.render(output_file)
    logger.success(f"成功！ECharts 交互图已保存至: {os.path.abspath(output_file)}")
    return grid


# ─────────────────────────────────────────────────────────────────────
# 主入口
# ─────────────────────────────────────────────────────────────────────
@dataclass
class AnalyzeTask:
    symbol: str
    sdt: str
    edt: str
    desc: str = ""


if __name__ == "__main__":
    tasks = [
        AnalyzeTask("300371",   sdt="20181220", edt="20201030", desc="汇中股份"),
        AnalyzeTask("sz002346", sdt="20180901", edt="20200908", desc="柘中股份"),
        AnalyzeTask("sz002286", sdt="20210101", edt="20210701", desc="保利发展"),
    ]

    # 🎯 切换这里
    task = tasks[0]

    try:
        symbol = task.symbol
        logger.info(f"正在拉取标的 {symbol} ({task.desc}) | 时间: {task.sdt} ~ {task.edt}")
        bars = research.get_raw_bars_origin(symbol, sdt=task.sdt, edt=task.edt)

        engine = MooreCZSC(bars)

        total_fail = sum(engine._debug_rule_fail.values())
        logger.info(
            f"[诊断] 四法则拦截总计: {total_fail} 次 | "
            f"法则1(3K): {engine._debug_rule_fail.get(1, 0)} | "
            f"法则1.1(脱离): {engine._debug_rule_fail.get(1.1, 0)} | "
            f"法则2(金死叉): {engine._debug_rule_fail.get(2, 0)} | "
            f"法则3(中枢): {engine._debug_rule_fail.get(3, 0)}"
        )
        logger.info(
            f"[诊断] turning_ks: {len(engine.turning_ks)} | "
            f"触发: {engine._debug_trigger_count} | "
            f"实体拦截: {engine._debug_body_filter} | "
            f"candidate_tk: {engine.candidate_tk}"
        )

        output_dir = "moore_plots"
        os.makedirs(output_dir, exist_ok=True)
        output_file = os.path.join(output_dir, f"{symbol}_moore_echarts.html")

        plot_moore_structure_echarts(
            bars, engine,
            output_file=output_file,
            title=f"摩尔缠论 {symbol} ({task.desc})",
        )

    except Exception as e:
        import traceback
        traceback.print_exc()
        logger.error(f"测绘失败: {e}")
