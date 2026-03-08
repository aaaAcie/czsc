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
    desc_text: str = "",
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

    micro_segments = getattr(engine, "micro_segments", engine.segments)
    
    micro_centers = getattr(engine, "micro_centers", [])
    macro_centers = getattr(engine, "macro_centers", getattr(engine, "all_centers", []))
    ghost_centers = getattr(engine, "ghost_centers", [])

    # ── 诊断：实线但无中枢的微观线段 ─────────────────────────────────
    for i, seg in enumerate(micro_segments):
        if not seg.is_perfect:
            continue
        si, ei = seg.start_k.k_index, seg.end_k.k_index
        seg_micro = [c for c in micro_centers if c.start_k_index <= ei and c.end_k_index >= si]
        if not seg_micro:
            logger.warning(
                f"[诊断] 微观线段 {i} (v{i}-v{i+1}) 是实线但无微观中枢: "
                f"{_dt_str(seg.start_k.dt)}→{_dt_str(seg.end_k.dt)} "
                f"dir={seg.direction.value} k_idx=[{si},{ei}]"
            )

    # --- 【增压逻辑】：中枢层级过滤 ---
    # 1. 微观过滤：排除已被宏观审计拆解（进入幽灵仓）的微观中枢
    ghost_ids = {getattr(c, 'center_id', None) for c in ghost_centers if getattr(c, 'center_id', None) is not None}
    micro_centers = [c for c in micro_centers if getattr(c, 'center_id', None) not in ghost_ids]

    # 2. 宏观过滤：只有当宏观中枢的 ID 未出现在微观事实仓中时，才认为它是“纯宏观重播”产物
    micro_ids = {getattr(c, 'center_id', None) for c in micro_centers if getattr(c, 'center_id', None) is not None}
    macro_centers = [c for c in macro_centers if getattr(c, 'center_id', None) not in micro_ids]

    # 按三仓独立展示：微观仓、宏观仓、幽灵仓
    display_tks = getattr(engine, "micro_turning_ks", engine.turning_ks)
    ghost_forks = getattr(engine, "ghost_forks", [])
    refreshed_segments = getattr(engine, "refreshed_segments", [])
    
    display_tks = getattr(engine, "micro_turning_ks", engine.turning_ks)
    ghost_forks = getattr(engine, "ghost_forks", [])
    refreshed_segments = getattr(engine, "refreshed_segments", [])

    # ── 3. K0 + 确认K markPoint（分层提取）────
    # 不再预提取，直接在循环中根据 center 属性判断
    tk_points_macro = []
    for ct in macro_centers:
        if ct.confirm_k:
            is_up = ct.direction == Direction.Up
            tk_points_macro.append({
                "name": "Macro CK",
                "coord": [_dt_str(ct.confirm_k.dt), ct.confirm_k.close],
                # 使用与转折K一致的瘦长箭头路径
                "symbol": "path://M46 0 L46 75 L20 49 L12 57 L50 95 L88 57 L80 49 L54 75 L54 0 Z",
                "symbolSize": [14, 40], # 线条更细，整体更长
                "symbolRotate": 180 if is_up else 0,
                "symbolOffset": [0, "100%"] if is_up else [0, "-100%"], # 偏移更多一点
                "itemStyle": {"color": "#FFD600"}
            })
        if ct.anchor_k0:
            k0 = ct.anchor_k0
            tk_points_macro.append({
                "name": "Macro K0",
                "coord": [_dt_str(k0.dt), k0.low if k0.close > k0.open else k0.high],
                "symbol": "rect",
                "symbolSize": 5,
                "itemStyle": {"color": "#FFD600"}
            })
            
    tk_points_micro = []
    for ct in micro_centers:
        if ct.confirm_k:
            is_up = ct.direction == Direction.Up
            tk_points_micro.append({
                "name": "Micro CK",
                "coord": [_dt_str(ct.confirm_k.dt), ct.confirm_k.close],
                "symbol": "path://M46 0 L46 75 L20 49 L12 57 L50 95 L88 57 L80 49 L54 75 L54 0 Z",
                "symbolSize": [14, 35],
                "symbolRotate": 180 if is_up else 0,
                "symbolOffset": [0, "85%"] if is_up else [0, "-85%"],
                "itemStyle": {"color": "#CE93D8"}
            })
        if ct.anchor_k0:
            k0 = ct.anchor_k0
            tk_points_micro.append({
                "name": "Micro K0",
                "coord": [_dt_str(k0.dt), k0.low if k0.close > k0.open else k0.high],
                "symbol": "rect",
                "symbolSize": 6,
                "itemStyle": {"color": "#BA68C8"}
            })

    # 顶底极值 → 两个 Scatter 系列，分别控制 label 位置

    # 顶底极值 → 两个 Scatter 系列，分别控制 label 位置
    top_tks = [(i, tk) for i, tk in enumerate(display_tks) if tk.mark == Mark.G]
    bot_tks = [(i, tk) for i, tk in enumerate(display_tks) if tk.mark == Mark.D]

    def _tk_scatter(name, tk_list, label_pos):
        if not tk_list:
            return None
            
        val_map = { _dt_str(tk.dt): {"price": tk.price, "idx": i, "mark": tk.mark} for i, tk in tk_list }
        ys = []
        labels_js_arr = []
        for d in dates:
            if d in val_map:
                v = val_map[d]
                ys.append(v["price"])
                labels_js_arr.append(f"mV{v['idx']}{'T' if v['mark'] == Mark.G else 'B'}")
            else:
                ys.append(None)
                labels_js_arr.append("")

        labels_js = str(labels_js_arr)
        formatter = JsCode("function(p){ var L=" + labels_js + "; return L[p.dataIndex] || ''; }")
        
        return (
            Scatter()
            .add_xaxis(dates)
            .add_yaxis(
                name, ys,
                symbol="circle", symbol_size=7,
                label_opts=opts.LabelOpts(
                    is_show=True, position=label_pos, color="#000000",
                    font_size=14, font_weight="bold", formatter=formatter,
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

    def _arrow_series(name, tk_list, is_up):
        if not tk_list:
            return None
            
        mp_data = []
        has_data = False
        for _, tk in tk_list:
            if tk.turning_k:
                dt_str = _dt_str(tk.turning_k.dt)
                y_val = tk.turning_k.low if is_up else tk.turning_k.high
                mp_data.append({
                    "coord": [dt_str, y_val],
                    "symbol": "path://M46 0 L46 75 L15 48 L10 56 L50 95 L90 56 L85 48 L54 75 L54 0 Z",
                    "symbolSize": [14, 35],
                    "symbolRotate": 180 if is_up else 0,
                    "symbolOffset": [0, "85%"] if is_up else [0, "-85%"],
                    "itemStyle": {"color": "#000000"} # 统一黑色
                })
                has_data = True
                
        if not has_data:
            return None

        return (
            _dummy_line(dates, name, "#000000")
            .set_series_opts(markpoint_opts=opts.MarkPointOpts(
                data=mp_data,
                label_opts=opts.LabelOpts(is_show=False),
            ))
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
                     "lineStyle": {"color": "#555555", "width": 2, "type": tp, "opacity": 0.75}},
                    {"coord": [_dt_str(tb.dt), tb.price], "symbol": "none"},
                ])

    # 演变刷新路径：统一灰色虚线
    refresh_data = []
    for seg in refreshed_segments:
        refresh_data.append([
            {"coord": [_dt_str(seg.start_k.dt), seg.start_k.price], "symbol": "none",
             "lineStyle": {"color": "#666666", "width": 1.5, "type": "dashed", "opacity": 0.7}},
            {"coord": [_dt_str(seg.end_k.dt), seg.end_k.price], "symbol": "none"},
        ])

    # ── 5. 中枢 markArea 数据分类 ─────────────────────────────────────
    logger.info(f"三仓中枢统计 -> Macro: {len(macro_centers)}, Micro: {len(micro_centers)}, Ghost: {len(ghost_centers)}")

    def _prepare_area_data(clist, layer_name):
        data = []
        for ct in clist:
            if not ct.start_dt or not ct.end_dt: continue
            y_lo, y_hi = ct.lower_rail, ct.upper_rail
            cid = getattr(ct, "center_id", "?")
            
            if layer_name == "macro":
                fill, border = "rgba(41,128,185,0.15)", "#2980B9" # 蓝色：宏观
            elif layer_name == "micro":
                fill, border = "rgba(142,68,173,0.12)", "#8E44AD" # 紫色：微观
            else:
                fill, border = "rgba(149,165,166,0.15)", "#7F8C8D" # 灰色：幽灵

            label_txt = f"ID:{cid} {layer_name}-{getattr(ct, 'method', '?')}"
            data.append([
                {
                    "xAxis": _dt_str(ct.start_dt), "yAxis": y_lo,
                    "label": {"show": True, "position": "top", "formatter": label_txt, "fontSize": 9, "color": border, "opacity": 0.8},
                    "itemStyle": {"color": fill, "borderWidth": 1.5, "borderColor": border, "opacity": 0.6,
                                  "borderType": "dashed" if layer_name == "ghost" else "solid"}
                },
                {"xAxis": _dt_str(ct.end_dt), "yAxis": y_hi}
            ])
        return data

    macro_area_data = _prepare_area_data(macro_centers, "macro")
    micro_area_data = _prepare_area_data(micro_centers, "micro")
    ghost_area_data = _prepare_area_data(ghost_centers, "ghost")
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
    s = _seg_series("微观线段", micro_all, "#555555", 2.5, alpha=0.75)
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

    # 中枢绘制
    for name, data, color in [
        ("宏观中枢", macro_area_data, "#2980B9"),
        ("微观中枢", micro_area_data, "#8E44AD"),
        ("幽灵中枢", ghost_area_data, "#7F8C8D"),
    ]:
        s = _center_series(name, data, color)
        if s: overlay_series.append(s)

    # 顶底极值 Scatter（顶标签在上，底标签在下）
    sc_top = _tk_scatter("微观顶极值", top_tks, "top")
    sc_bot = _tk_scatter("微观底极值", bot_tks, "bottom")
    if sc_top:
        overlay_series.append(sc_top)
    if sc_bot:
        overlay_series.append(sc_bot)

    # 转折K箭头（指向触发K线的高低点）
    arr_up = _arrow_series("向上转折确立", bot_tks, is_up=True)
    arr_dn = _arrow_series("向下转折确立", top_tks, is_up=False)
    if arr_up:
        overlay_series.append(arr_up)
    if arr_dn:
        overlay_series.append(arr_dn)

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
            markpoint_opts=opts.MarkPointOpts(
                data=tk_points_macro + tk_points_micro,
                label_opts=opts.LabelOpts(is_show=False)
            )
        )
        .set_global_opts(
            title_opts=opts.TitleOpts(
                title=title + "\n" + desc_text,
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
                    range_start=0, range_end=100,
                ),
                opts.DataZoomOpts(
                    is_show=True, type_="slider",
                    xaxis_index=[0, 1],
                    range_start=0, range_end=100,
                    pos_bottom="55px",
                ),
            ],
            legend_opts=opts.LegendOpts(
                is_show=True,
                pos_top="5%", pos_right="2%",
                orient="vertical",
                textstyle_opts=opts.TextStyleOpts(font_size=11),
                selected_map={
                    "幽灵中枢": False,
                    "幽灵枝丫": False,
                }
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
                data=tk_points_macro + tk_points_micro,
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
        AnalyzeTask("300371", sdt="20181220",edt="20201030", desc="汇中股份"),
        AnalyzeTask("002346", sdt="20180901", edt="20200908", desc="柘中股份"),
        AnalyzeTask("sz002286", sdt="20210101", edt="20210701", desc="保利发展"),
        AnalyzeTask("300137", sdt="20190415", edt="20201130", desc="先河环保"),
        AnalyzeTask("000993", sdt="20190515", edt="20200920", desc="闽东电力"),
    ]

    # 🎯 切换这里
    task = tasks[4]

    try:
        symbol = task.symbol
        logger.info(f"正在拉取标的 {symbol} ({task.desc}) | 时间: {task.sdt} ~ {task.edt}")
        bars = research.get_raw_bars_origin(symbol, sdt=task.sdt, edt=task.edt)

        engine = MooreCZSC(bars, ma34_cross_as_valid_gate=False, audit_link_rounds=3)

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
            desc_text="金色: 宏观 | 紫色: 微观 | 箭头: 中枢线确认K (CK) | 圆圈: 起始锚点 (K0)"
        )

    except Exception as e:
        import traceback
        traceback.print_exc()
        logger.error(f"测绘失败: {e}")
