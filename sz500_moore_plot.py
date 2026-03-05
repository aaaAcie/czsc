# -*- coding: utf-8 -*-
"""
author: moore_czsc
describe: 验证摩尔状态机并生成带有中枢/线段展示的 HTML 图表
"""
import os
import pandas as pd
import plotly.graph_objects as go
from loguru import logger
from dataclasses import dataclass
from czsc.connectors import research
from czsc.moore.analyze import MooreCZSC
from czsc.core import format_standard_kline, Freq
from czsc.utils.plotting.kline import KlineChart
from czsc.py.enum import Direction, Mark

def plot_moore_structure(
    bars: list,
    engine: MooreCZSC,
    output_file: str = "moore_czsc_demo_plot.html",
    title: str = "摩尔缠论结构图",
):
    """提取摩尔引擎吐出的成分数据，叠加绘制到交互K线轴上"""
    # 构造标准 df 喂给图表库
    data = []
    for b in bars:
        data.append({
            "dt": b.dt.strftime("%Y-%m-%d %H:%M"), 
            "open": b.open, 
            "close": b.close, 
            "high": b.high, 
            "low": b.low, 
            "vol": b.vol, 
            "amount": b.amount
        })
    df = pd.DataFrame(data)

    logger.info("正在绘制基础图表，包括 K 线与 MA...")
    chart = KlineChart(n_rows=3, row_heights=[0.75, 0.001, 0.249], title=title)
    chart.fig.update_layout(
        hoverlabel=dict(
            bgcolor="rgba(20,20,20,0.8)",
            bordercolor="rgba(220,220,220,0.35)",
        )
    )
    
    # 彻底去掉 type="date" 的强制转换。底层 Plotly 使用默认的 category 分类轴，会天然跳过非交易数据。
    chart.fig.update_xaxes(rangeslider_visible=False)
    
    chart.add_kline(df, name="K线蜡烛")
    k_hover = [
        f"开/收: {o:.2f} / {c:.2f}<br>高/低: {h:.2f} / {l:.2f}"
        for x, o, c, h, l in zip(df["dt"], df["open"], df["close"], df["high"], df["low"])
    ]
    chart.fig.update_traces(
        hovertext=k_hover,
        hoverinfo="text",
        selector=dict(type="candlestick"),
    )
    chart.add_sma(df, ma_seq=(5, 34), row=1, visible=True, line_width=1.2)
    ma34_vals = df["close"].rolling(34).mean()
    chart.fig.update_traces(
        customdata=ma34_vals,
        hovertemplate="MA5/MA34: %{y:.2f} / %{customdata:.2f}<extra></extra>",
        selector=dict(name="MA5"),
    )
    chart.fig.update_traces(
        hoverinfo="skip",
        selector=dict(name="MA34"),
    )
    chart.add_macd(df, row=3, visible=False)

    # 1. 叠加微观世界顶底极值与转折K（来源层）
    display_tks = getattr(engine, "micro_turning_ks", engine.turning_ks)
    if display_tks:
        # 画出真正的顶底极值
        tk_df = pd.DataFrame([
            {
                "dt": tk.dt.strftime("%Y-%m-%d %H:%M"), 
                "fx": tk.price, 
                "text": f"mV{i}{'顶' if tk.mark == Mark.G else '底'}"
            } 
            for i, tk in enumerate(display_tks)
        ])
        # 为顶底增加动态文字位置，避免遮挡
        tk_df["pos"] = ["top center" if tk.mark == Mark.G else "bottom center" for tk in display_tks]
        
        chart.add_scatter_indicator(
            tk_df["dt"], tk_df["fx"], name="微观顶底极值", row=1, 
            text=tk_df["text"], mode="markers+text", 
            textposition=tk_df["pos"],
            marker_size=8, marker_color="#E0E0E0"
        )
        
        # 叠加带有箭杆的指向性箭头（转折K）
        for tk in display_tks:
            if tk.turning_k:
                if tk.mark == Mark.D:  # 底分型确立 -> 向上转折确立
                    chart.fig.add_annotation(
                        x=tk.turning_k.dt.strftime("%Y-%m-%d %H:%M"),
                        y=tk.turning_k.low,
                        showarrow=True,
                        arrowhead=2,
                        arrowsize=1.2,
                        arrowwidth=2,
                        arrowcolor="rgba(255, 255, 255, 0.7)", # 白色带透明度
                        ax=0,
                        ay=40,    # 箭柄长度
                        standoff=10, # 箭头尖端离开 K 线的距离
                        text="",
                        xref="x", yref="y"
                    )
                else:  # 顶分型确立 -> 向下转折确立
                    chart.fig.add_annotation(
                        x=tk.turning_k.dt.strftime("%Y-%m-%d %H:%M"),
                        y=tk.turning_k.high,
                        showarrow=True,
                        arrowhead=2,
                        arrowsize=1.2,
                        arrowwidth=2,
                        arrowcolor="rgba(255, 255, 255, 0.7)", # 白色带透明度
                        ax=0,
                        ay=-40,   # 箭柄长度
                        standoff=10, # 箭头尖端离开 K 线的距离
                        text="",
                        xref="x", yref="y"
                    )
    
    segment_centers = [ct for seg in engine.segments for ct in seg.centers]

    # 1b. 标记所有中枢的 K0 锚点 (淡棕色)
    k0_data = []
    for ct in segment_centers:
        if ct.anchor_k0:
            k0_data.append({
                "dt": ct.anchor_k0.dt.strftime("%Y-%m-%d %H:%M"),
                "fx": ct.anchor_k0.close,
                "text": "K0"
            })
    confirm_ks = [ct.confirm_k for ct in segment_centers if ct.confirm_k]
    confirm_map = {ck.dt.strftime("%Y-%m-%d %H:%M"): ck.close for ck in confirm_ks}
    k0_map = {}
    for item in k0_data:
        k0_map[item["dt"]] = item["fx"]
    if k0_data:
        k0_df = pd.DataFrame(k0_data)
        chart.add_scatter_indicator(
            k0_df["dt"], k0_df["fx"], name="K0锚点", row=1,
            text=k0_df["text"], mode="markers", marker_size=7, marker_color="#A1887F" # 淡棕色
        )
        k0_pair_hover = [
            f"K0/确认K: {k0_v:.2f} / "
            f"{confirm_map[dt]:.2f}" if dt in confirm_map else f"K0/确认K: {k0_v:.2f} / --"
            for dt, k0_v in zip(k0_df["dt"], k0_df["fx"])
        ]
        chart.fig.update_traces(
            hovertext=k0_pair_hover,
            hoverinfo="text",
            selector=dict(name="K0锚点"),
        )

    macro_segments = getattr(engine, "segments", [])
    swallowed_micro_ids = set()
    for mseg in macro_segments:
        swallowed_micro_ids.update(mseg.cache.get("swallow_internal_micro_ids", []))

    # 2. 叠加微观线段（来源层）
    micro_segments = getattr(engine, "micro_segments", [])
    if micro_segments:
        logger.info(f"本次生成的微观线段数量: {len(micro_segments)}")
        for seg in micro_segments:
            x0, y0 = seg.start_k.dt.strftime("%Y-%m-%d %H:%M"), seg.start_k.price
            x1, y1 = seg.end_k.dt.strftime("%Y-%m-%d %H:%M"), seg.end_k.price

            start_mid = seg.start_k.cache.get("micro_id")
            end_mid = seg.end_k.cache.get("micro_id")
            is_swallowed_micro = (start_mid in swallowed_micro_ids) or (end_mid in swallowed_micro_ids)
            line_style = "solid" if seg.is_perfect else "dash"
            if is_swallowed_micro:
                line_color = "rgba(160,160,160,0.75)"
                line_width = 1
            else:
                line_color = "rgba(233,30,99,0.45)" if seg.direction == Direction.Up else "rgba(0,166,90,0.45)"
                line_width = 1.2
            chart.fig.add_shape(
                type="line", x0=x0, y0=y0, x1=x1, y1=y1, 
                xref="x", yref="y",
                line=dict(color=line_color, width=line_width, dash=line_style),
                layer="below"
            )

    # 2b. 叠加宏观线段（结果层，全部展示；吞噬段高亮）
    if macro_segments:
        logger.info(f"本次展示的宏观线段数量: {len(macro_segments)}")
        first_macro = True
        first_swallow = True
        for i, seg in enumerate(macro_segments):
            x0, y0 = seg.start_k.dt.strftime("%Y-%m-%d %H:%M"), seg.start_k.price
            x1, y1 = seg.end_k.dt.strftime("%Y-%m-%d %H:%M"), seg.end_k.price
            is_up = seg.direction == Direction.Up
            line_color = "#FF5A5F" if is_up else "#00A65A"
            is_macro_swallow = seg.cache.get("is_macro_swallow", False)
            chart.fig.add_trace(go.Scatter(
                x=[x0, x1],
                y=[y0, y1],
                mode="lines",
                line=dict(
                    color=line_color,
                    width=4 if is_macro_swallow else (3 if seg.is_perfect else 2),
                    dash="solid" if seg.is_perfect else "dash",
                ),
                name="宏观吞噬线段" if is_macro_swallow else "宏观线段",
                legendgroup="宏观吞噬" if is_macro_swallow else "宏观线段",
                showlegend=(first_swallow if is_macro_swallow else first_macro),
                hoverinfo="skip",
            ), row=1, col=1)
            if is_macro_swallow:
                first_swallow = False
            else:
                first_macro = False

    # 3. 叠加被刷新掉的“演变路径”（虚线）
    if hasattr(engine, 'refreshed_segments') and engine.refreshed_segments:
        for seg in engine.refreshed_segments:
            x0, y0 = seg.start_k.dt.strftime("%Y-%m-%d %H:%M"), seg.start_k.price
            x1, y1 = seg.end_k.dt.strftime("%Y-%m-%d %H:%M"), seg.end_k.price
            line_color = "#E91E63" if seg.direction == Direction.Up else "#00A65A"
            # 使用虚线画出曾尝试确立但被生新刷新的路径
            chart.fig.add_shape(
                type="line", x0=x0, y0=y0, x1=x1, y1=y1, 
                xref="x", yref="y", line=dict(color=line_color, width=1, dash="dash"), layer="below"
            )

    # 3b. 叠加幽灵分叉枝丫（被趋势穿透吞噬的陷阱化石）
    # 双世界绘图中，被吞噬微观段已在上文用灰色画出，避免与幽灵枝丫重复描线
    show_ghost_overlay = not (micro_segments and swallowed_micro_ids)
    ghost_forks = getattr(engine, 'ghost_forks', [])
    if ghost_forks and show_ghost_overlay:
        all_ghost_tks = []
        is_first_trace = True
        for fork_tk, consumed in ghost_forks:
            path = [fork_tk] + consumed
            for i in range(len(path) - 1):
                ta, tb = path[i], path[i + 1]
                # 根据终点状态恢复幽灵线原始的虚实
                style = "solid" if tb.is_perfect else "dash"
                
                chart.fig.add_trace(go.Scatter(
                    x=[ta.dt.strftime("%Y-%m-%d %H:%M"), tb.dt.strftime("%Y-%m-%d %H:%M")],
                    y=[ta.price, tb.price],
                    line=dict(color="rgba(160,160,160,0.6)", width=1.5, dash=style),
                    mode="lines", name="幽灵枝丫线", legendgroup="幽灵元素",
                    showlegend=is_first_trace, hoverinfo='skip'
                ), row=1, col=1)
                is_first_trace = False
            all_ghost_tks.extend(consumed)

        # 幽灵点标记（虚线圆圈）
        if all_ghost_tks:
            ghost_df = pd.DataFrame([
                {"dt": tk.dt.strftime("%Y-%m-%d %H:%M"), "fx": tk.price,
                 "text": "👻顶" if tk.mark == Mark.G else "👻底"}
                for tk in all_ghost_tks
            ])
            chart.add_scatter_indicator(
                ghost_df["dt"], ghost_df["fx"], name="幽灵顶底", row=1,
                text=ghost_df["text"], mode="markers", 
                marker=dict(size=6, color="rgba(150,150,150,0.55)", symbol="circle-open")
            )

    # 3. 叠加摩尔双轨中枢 (engine.all_centers 已包含历史固化与当前潜在)
    display_centers = getattr(engine, 'all_centers', [])
    total_centers = len(display_centers)
    if total_centers > 0:
        logger.info(f"本次展示的中枢总分（含幽灵）: {total_centers}")
        c_idx = 0
        first_ghost = True
        for ct in display_centers:
            # 取得时间跨度和上下轨
            if not ct.start_dt or not ct.end_dt: continue
            x0 = ct.start_dt.strftime("%Y-%m-%d %H:%M")
            x1 = ct.end_dt.strftime("%Y-%m-%d %H:%M")
            y_lower = ct.lower_rail
            y_upper = ct.upper_rail
            y_center = getattr(ct, 'center_line', (y_lower + y_upper) / 2)

            # 彻底信赖引擎内部的幽灵打标状态
            is_ghost = getattr(ct, 'is_ghost', False)
            
            if is_ghost:
                fill_color  = "rgba(150, 150, 150, 0.2)"  # 幽灵灰
                line_color  = "#888888"                   # 灰线
                line_dash   = "dash"                      # 虚线框
            elif ct.is_visible:
                fill_color  = "rgba(233, 30, 99, 0.15)"   # 粉润
                line_color  = "#E91E63"
                line_dash   = "solid"
            else:
                fill_color  = "rgba(46, 204, 113, 0.15)"  # 翠绿
                line_color  = "#2ECC71"
                line_dash   = "solid"

            # ── 6. 右端轨道价格标注 ──
            c_type = "肉" if ct.is_visible else "非肉"
            if is_ghost:
                c_type = "👻-" + c_type
                
            method = getattr(ct, 'method', '未知')
            
            if ct.direction == Direction.Up:
                label_upper, label_lower = f"上轨 {y_upper:.2f}", f"#{c_idx} {c_type}-{method}-下轨 {y_lower:.2f}"
                ys_upper, ys_lower = 5, -5
            else:
                label_upper, label_lower = f"#{c_idx} {c_type}-{method}-上轨 {y_upper:.2f}", f"下轨 {y_lower:.2f}"
                ys_upper, ys_lower = 5, -5

            if is_ghost:
                # 幽灵中枢改用 Trace 绘制，以便支持按钮交互
                rect_x = [x0, x1, x1, x0, x0]
                rect_y = [y_lower, y_lower, y_upper, y_upper, y_lower]
                # 矩形框
                chart.fig.add_trace(go.Scatter(
                    x=rect_x, y=rect_y, fill="toself", fillcolor=fill_color,
                    line=dict(color=line_color, width=2, dash=line_dash),
                    name="幽灵中枢", legendgroup="幽灵元素", showlegend=first_ghost, hoverinfo='skip'
                ), row=1, col=1)
                # 轨道线（中枢线）
                chart.fig.add_trace(go.Scatter(
                    x=[x0, x1], y=[y_center, y_center],
                    line=dict(color=line_color, width=1, dash="dot"),
                    name="幽灵中枢", legendgroup="幽灵元素", showlegend=False, hoverinfo='skip'
                ), row=1, col=1)
                # 文字标签
                chart.fig.add_trace(go.Scatter(
                    x=[x1, x1], y=[y_upper, y_lower], text=[label_upper, label_lower],
                    mode="text", name="幽灵中枢", legendgroup="幽灵元素", showlegend=False,
                    textposition="middle right", textfont=dict(size=8, color=line_color)
                ), row=1, col=1)
                first_ghost = False
            else:
                # ── 非幽灵中枢：中枢矩形框（淡色填充） ──
                rect_x = [x0, x1, x1, x0, x0]
                rect_y = [y_lower, y_lower, y_upper, y_upper, y_lower]
                chart.fig.add_trace(go.Scatter(
                    x=rect_x, y=rect_y, fill="toself", fillcolor=fill_color,
                    line=dict(color=line_color, width=2, dash=line_dash),
                    name="常规中枢", legendgroup="常规中枢", showlegend=(c_idx == 0), hoverinfo='skip'
                ), row=1, col=1)
                
                # 轨道/中枢线
                chart.fig.add_trace(go.Scatter(
                    x=[x0, x1], y=[y_center, y_center],
                    line=dict(color=line_color, width=1, dash="dot"),
                    name="常规中枢", legendgroup="常规中枢", showlegend=False, hoverinfo='skip'
                ), row=1, col=1)

                # 文字标签
                chart.fig.add_trace(go.Scatter(
                    x=[x1, x1], y=[y_upper, y_lower], text=[label_upper, label_lower],
                    mode="text", name="中枢", legendgroup="常规中枢", showlegend=(c_idx == 0),
                    textposition="middle right", textfont=dict(size=8, color=line_color)
                ), row=1, col=1)
            c_idx += 1



    # 4. 叠加中枢的确认K标记 (包含肉眼与非肉眼)
    if confirm_ks:
        ck_df = pd.DataFrame([
            {"dt": ck.dt.strftime("%Y-%m-%d %H:%M"), "fx": ck.close, "text": "确认K"} 
            for ck in confirm_ks
        ])
        chart.add_scatter_indicator(
            ck_df["dt"], ck_df["fx"], name="确认K", row=1, 
            text=ck_df["text"], mode="markers", marker_size=7, marker_color="#CE93D8" # 淡紫色
        )
        ck_pair_hover = [
            f"K0/确认K: {k0_map[dt]:.2f} / {ck_v:.2f}" if dt in k0_map else f"K0/确认K: -- / {ck_v:.2f}"
            for dt, ck_v in zip(ck_df["dt"], ck_df["fx"])
        ]
        chart.fig.update_traces(
            hovertext=ck_pair_hover,
            hoverinfo="text",
            selector=dict(name="确认K"),
        )

    # --- 增加交互控制按钮 ---
    # 定义中枢相关 trace 组
    center_names = ["常规中枢", "中枢", "幽灵中枢", "K0锚点", "确认K"]
    center_indices = [i for i, t in enumerate(chart.fig.data) if t.name in center_names]
    
    ghost_names = ["幽灵枝丫", "幽灵顶底", "幽灵中枢"]
    ghost_indices = [i for i, t in enumerate(chart.fig.data) if t.name in ghost_names]

    updatemenus = []
    
    # 按钮组1：中枢总控
    if center_indices:
        updatemenus.append(dict(
            type="buttons", direction="left", showactive=True,
            x=0.01, xanchor="left", y=1.16, yanchor="top",
            buttons=[
                dict(label="显示中枢及标注", method="restyle", args=[{"visible": True}, center_indices]),
                dict(label="隐藏中枢及标注", method="restyle", args=[{"visible": ["legendonly"] * len(center_indices)}, center_indices]),
            ]
        ))

    # 按钮组2：幽灵控制（保持原有逻辑）
    if ghost_indices:
        updatemenus.append(dict(
            type="buttons", direction="left", showactive=True,
            x=0.7, xanchor="left", y=1.16, yanchor="top",
            buttons=[
                dict(label="显示所有幽灵", method="restyle", args=[{"visible": True}, ghost_indices]),
                dict(label="一键清洗幽灵", method="restyle", args=[{"visible": ["legendonly"] * len(ghost_indices)}, ghost_indices]),
            ]
        ))

    if updatemenus:
        chart.fig.update_layout(
            margin=dict(t=120), 
            updatemenus=updatemenus
        )

    chart.fig.write_html(output_file)
    logger.success(f"成功！摩尔结构图表已保存至: {os.path.abspath(output_file)}")
    return chart

@dataclass
class AnalyzeTask:
    symbol: str
    sdt: str
    edt: str
    desc: str = ""

if __name__ == '__main__':
    # 定义测试任务列表，可以在这里添加更多想测试的标的和时间段
    tasks = [
        AnalyzeTask("300371", sdt="20181220", edt="20201030", desc="汇中股份"),
        AnalyzeTask("sz002346", sdt="20180901", edt="20200908", desc="柘中股份"),
        AnalyzeTask("sz002286", sdt="20210101", edt="20210701", desc="保利发展"),
    ]
    
    # 🎯 切换这里即可同时切换股票 and 时间范围 (例如改为 tasks[1] 或 tasks[2])
    task = tasks[0]

    try:
        symbol = task.symbol
        logger.info(f"正在拉取标的 {symbol} ({task.desc}) | 时间: {task.sdt} ~ {task.edt}")
        bars = research.get_raw_bars_origin(symbol, sdt=task.sdt, edt=task.edt)


        
        # 喂入引擎
        engine = MooreCZSC(bars)
        
        # 诊断：打印每条四法则的拦截计数
        total_fail = sum(engine._debug_rule_fail.values())
        logger.info(f"[诊断] 四法则拦截总计: {sum(engine._debug_rule_fail.values())} 次 | "
                f"法则1(3K): {engine._debug_rule_fail.get(1, 0)} | "
                f"法则1.1(脱离): {engine._debug_rule_fail.get(1.1, 0)} | "
                f"法则2(金死叉): {engine._debug_rule_fail.get(2, 0)} | "
                f"法则3(中枢): {engine._debug_rule_fail.get(3, 0)}")
        logger.info(f"[诊断] turning_ks 确立数量: {len(engine.turning_ks)} | 触发事件总次数: {engine._debug_trigger_count} | 实体推升拦截: {engine._debug_body_filter} | candidate_tk 当前: {engine.candidate_tk}")
        
        # 绘制输出
        output_dir = "moore_plots"
        if not os.path.exists(output_dir):
            os.makedirs(output_dir)
        
        output_file = os.path.join(output_dir, f"{symbol}_moore.html")
        plot_moore_structure(bars, engine, output_file=output_file, title=f"摩尔缠论 {symbol} ({task.desc})结构测试")
    except Exception as e:
        import traceback
        traceback.print_exc()
        logger.error(f"测绘失败: {e}")
