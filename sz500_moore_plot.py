# -*- coding: utf-8 -*-
"""
author: moore_czsc
describe: 验证摩尔状态机并生成带有中枢/线段展示的 HTML 图表
"""
import os
import pandas as pd
import plotly.graph_objects as go
from loguru import logger
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
    
    # 彻底去掉 type="date" 的强制转换。底层 Plotly 使用默认的 category 分类轴，会天然跳过非交易数据。
    chart.fig.update_xaxes(rangeslider_visible=False)
    
    chart.add_kline(df, name="K线蜡烛")
    chart.add_sma(df, ma_seq=(5, 34), row=1, visible=True, line_width=1.2)
    chart.add_macd(df, row=3, visible=False)

    # 1. 叠加顶底极值与转折K (信号游标)
    if engine.turning_ks:
        # 画出真正的顶底极值
        tk_df = pd.DataFrame([
            {
                "dt": tk.dt.strftime("%Y-%m-%d %H:%M"), 
                "fx": tk.price, 
                "text": f"V{i}{'顶' if tk.mark == Mark.G else '底'}"
            } 
            for i, tk in enumerate(engine.turning_ks)
        ])
        # 为顶底增加动态文字位置，避免遮挡
        tk_df["pos"] = ["top center" if tk.mark == Mark.G else "bottom center" for tk in engine.turning_ks]
        
        chart.add_scatter_indicator(
            tk_df["dt"], tk_df["fx"], name="顶底极值", row=1, 
            text=tk_df["text"], mode="markers+text", 
            textposition=tk_df["pos"],
            marker_size=8, marker_color="#E0E0E0"
        )
        
        # 叠加带有箭杆的指向性箭头（转折K）
        for tk in engine.turning_ks:
            if tk.trigger_k:
                if tk.mark == Mark.D:  # 底分型确立 -> 向上转折确立
                    chart.fig.add_annotation(
                        x=tk.trigger_k.dt.strftime("%Y-%m-%d %H:%M"),
                        y=tk.trigger_k.low,
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
                        x=tk.trigger_k.dt.strftime("%Y-%m-%d %H:%M"),
                        y=tk.trigger_k.high,
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
    
    # 1b. 标记所有中枢的 K0 锚点 (淡棕色)
    k0_data = []
    for seg in engine.segments:
        for ct in seg.centers:
            if ct.anchor_k0:
                k0_data.append({
                    "dt": ct.anchor_k0.dt.strftime("%Y-%m-%d %H:%M"),
                    "fx": ct.anchor_k0.close,
                    "text": "K0"
                })
    if k0_data:
        k0_df = pd.DataFrame(k0_data)
        chart.add_scatter_indicator(
            k0_df["dt"], k0_df["fx"], name="K0锚点", row=1,
            text=k0_df["text"], mode="markers", marker_size=7, marker_color="#A1887F" # 淡棕色
        )

    # 2. 叠加摩尔本质线段
    if engine.segments:
        logger.info(f"本次生成的摩尔线段数量: {len(engine.segments)}")
        for i, seg in enumerate(engine.segments):
            x0, y0 = seg.start_k.dt.strftime("%Y-%m-%d %H:%M"), seg.start_k.price
            x1, y1 = seg.end_k.dt.strftime("%Y-%m-%d %H:%M"), seg.end_k.price

            is_up = seg.direction == Direction.Up
            line_color = "#E91E63" if is_up else "#00A65A"
            
            # 结构完美性决定线段虚实（法则三：端点 TurningK 内部是否有中枢）
            line_style = "solid" if seg.is_perfect else "dash"
            line_width = 3 if seg.is_perfect else 1

            # 画线
            chart.fig.add_shape(
                type="line", x0=x0, y0=y0, x1=x1, y1=y1, 
                xref="x", yref="y", line=dict(color=line_color, width=line_width, dash=line_style), 
                layer="above"
            )
            # 打标签
            # chart.fig.add_annotation(
            #     x=x1, y=y1, xref="x", yref="y", text=f"线段{i}",
            #     showarrow=False, font=dict(size=10, color=line_color),
            #     xanchor="left", yanchor="middle"
            # )

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
    ghost_forks = getattr(engine, 'ghost_forks', [])
    if ghost_forks:
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
                    name="幽灵中枢", legendgroup="幽灵元素", showlegend=True, hoverinfo='skip'
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
            else:
                # ── 非幽灵中枢：中枢矩形框（淡色填充） ──
                chart.fig.add_shape(
                    type="rect", x0=x0, y0=y_lower, x1=x1, y1=y_upper,
                    xref="x", yref="y", line=dict(color=line_color, width=2, dash=line_dash),
                    fillcolor=fill_color, layer="below"
                )
                # 轨道/中枢线用 shape 保持轻量
                for y_val, dash, width in [(y_upper, line_dash, 2), (y_lower, line_dash, 2), (y_center, "dot", 1.2)]:
                    chart.fig.add_shape(
                        type="line", x0=x0, y0=y_val, x1=x1, y1=y_val,
                        xref="x", yref="y", line=dict(color=line_color, width=width, dash=dash), layer="above"
                    )

                for y_val, label_text, ys in [(y_upper, label_upper, ys_upper), (y_lower, label_lower, ys_lower)]:
                    chart.fig.add_annotation(
                        x=x1, y=y_val, xref="x", yref="y", text=label_text,
                        showarrow=False, font=dict(size=8, color=line_color),
                        xanchor="left", yanchor="middle", xshift=5, yshift=ys
                    )
            c_idx += 1



    # 4. 叠加中枢的确认K标记 (包含肉眼与非肉眼)
    confirm_ks = []
    for seg in engine.segments:
        for ct in seg.centers:
            if ct.confirm_k:
                confirm_ks.append(ct.confirm_k)
                
    if confirm_ks:
        ck_df = pd.DataFrame([
            {"dt": ck.dt.strftime("%Y-%m-%d %H:%M"), "fx": ck.close, "text": "确认K"} 
            for ck in confirm_ks
        ])
        chart.add_scatter_indicator(
            ck_df["dt"], ck_df["fx"], name="确认K", row=1, 
            text=ck_df["text"], mode="markers", marker_size=7, marker_color="#CE93D8" # 淡紫色
        )

    # --- 增加交互控制按钮 ---
    ghost_names = ["幽灵枝丫", "幽灵顶底", "幽灵中枢"]
    ghost_indices = [i for i, t in enumerate(chart.fig.data) if t.name in ghost_names]

    if ghost_indices:
        chart.fig.update_layout(
            margin=dict(t=80), # 增加顶部边距
            updatemenus=[
                dict(
                    type="buttons", direction="left", showactive=True,
                    x=0.01, xanchor="left", y=1.1, yanchor="top",
                    buttons=[
                        dict(label="显示所有幽灵", method="restyle", args=[{"visible": True}, ghost_indices]),
                        dict(label="一键清洗幽灵", method="restyle", args=[{"visible": ["legendonly"] * len(ghost_indices)}, ghost_indices]),
                    ]
                )
            ]
        )

    chart.fig.write_html(output_file)
    logger.success(f"成功！摩尔结构图表已保存至: {os.path.abspath(output_file)}")
    return chart

if __name__ == '__main__':
    try:
        # symbols = research.get_symbols('中证500成分股')[:30]
        # symbols = ['sz002286']
        # symbols = ['sz002346'] # 柘中股份
        symbols = ['300371'] # 汇中股份


        if not symbols:
            raise ValueError("未能获取中证500成分股")
            
        symbol = symbols[0]
        # 获取真实的数据
        logger.info(f"拉取标的 {symbol} 真实 K 线...")
        # bars = research.get_raw_bars(symbol, freq='30分钟', sdt='20210101', edt='20210701')
        # bars = research.get_raw_bars_30m(symbol, freq='30分钟', sdt='20200301', edt='20200901')
        # bars = research.get_raw_bars_origin(symbol, sdt='20180922', edt='20200908')
        bars = research.get_raw_bars_origin(symbol, sdt='20190122', edt='20201228')


        
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
        plot_moore_structure(bars, engine, output_file=output_file, title=f"摩尔缠论 {symbol} 结构测试")
    except Exception as e:
        import traceback
        traceback.print_exc()
        logger.error(f"测绘失败: {e}")
