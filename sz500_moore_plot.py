# -*- coding: utf-8 -*-
"""
author: moore_czsc
describe: 验证摩尔状态机并生成带有中枢/线段展示的 HTML 图表
"""
import os
import pandas as pd
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
            {"dt": tk.dt.strftime("%Y-%m-%d %H:%M"), "fx": tk.price, "text": "顶" if tk.mark == Mark.G else "底"} 
            for tk in engine.turning_ks
        ])
        chart.add_scatter_indicator(
            tk_df["dt"], tk_df["fx"], name="顶底极值", row=1, 
            text=tk_df["text"], mode="markers", marker_size=7, marker_color="#E0E0E0"
        )
        
        # 提取其中带出来的“触发K（转折K）”
        trigger_data = []
        for tk in engine.turning_ks:
            if tk.trigger_k:
                trigger_data.append({
                    "dt": tk.trigger_k.dt.strftime("%Y-%m-%d %H:%M"), 
                    "fx": tk.trigger_k.close, # 画在触发K的收盘价位置
                    "text": "转折K"
                })
        if trigger_data:
            tr_df = pd.DataFrame(trigger_data)
            chart.add_scatter_indicator(
                tr_df["dt"], tr_df["fx"], name="转折K", row=1, 
                text=tr_df["text"], mode="markers", marker_size=8, marker_color="#FFF59D" # 淡黄色
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
        for i, seg in enumerate(engine.segments, start=1):
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
            chart.fig.add_annotation(
                x=x1, y=y1, xref="x", yref="y", text=f"线段{i}",
                showarrow=False, font=dict(size=10, color=line_color),
                xanchor="left", yanchor="middle"
            )

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
    # 每条幽灵枝丫从仍存活的实线锚点出发，画出被消灭的两个被砍点
    ghost_forks = getattr(engine, 'ghost_forks', [])
    if ghost_forks:
        all_ghost_tks = []
        for fork_tk, consumed in ghost_forks:
            # 从 fork_tk → consumed[0]（旧同向点）→ consumed[1]（旧异向中继）画枝丫
            path = [fork_tk] + consumed
            for i in range(len(path) - 1):
                ta, tb = path[i], path[i + 1]
                chart.fig.add_shape(
                    type="line",
                    x0=ta.dt.strftime("%Y-%m-%d %H:%M"), y0=ta.price,
                    x1=tb.dt.strftime("%Y-%m-%d %H:%M"), y1=tb.price,
                    xref="x", yref="y",
                    line=dict(color="rgba(160,160,160,0.45)", width=1, dash="dot"),
                    layer="below"
                )
            all_ghost_tks.extend(consumed)  # 仅标记被消灭的点，不重复标 fork_tk

        # 幽灵点标记（灰色小圆）
        if all_ghost_tks:
            ghost_df = pd.DataFrame([
                {"dt": tk.dt.strftime("%Y-%m-%d %H:%M"), "fx": tk.price,
                 "text": "👻顶" if tk.mark == Mark.G else "👻底"}
                for tk in all_ghost_tks
            ])
            chart.add_scatter_indicator(
                ghost_df["dt"], ghost_df["fx"], name="幽灵顶底", row=1,
                text=ghost_df["text"], mode="markers",
                marker=dict(size=5, color="rgba(150,150,150,0.55)", symbol="circle-open")
            )

    # 3. 叠加摩尔双轨中枢 (合并历史与当前潜在)
    display_centers = getattr(engine, 'all_centers', []) + getattr(engine, 'potential_centers', [])
    total_centers = len(display_centers)
    if total_centers > 0:
        logger.info(f"本次生成的中枢数量: {total_centers}")
        c_idx = 1
        for ct in display_centers:
            # 取得时间跨度和上下轨
            if not ct.start_dt or not ct.end_dt: continue
            x0 = ct.start_dt.strftime("%Y-%m-%d %H:%M")
            x1 = ct.end_dt.strftime("%Y-%m-%d %H:%M")
            y_lower = ct.lower_rail
            y_upper = ct.upper_rail
            y_center = getattr(ct, 'center_line', (y_lower + y_upper) / 2)

            # 颜色按判定方式区分
            method = getattr(ct, 'method', '肉眼' if ct.is_visible else '?')
            if ct.is_visible:
                fill_color  = "rgba(233, 30, 99, 0.15)"
                line_color  = "#E91E63"
            elif method == "反正两穿":
                fill_color  = "rgba(46, 204, 113, 0.15)"
                line_color  = "#2ECC71"
            elif method == "5K重叠":
                fill_color  = "rgba(46, 134, 222, 0.15)"
                line_color  = "#2E86DE"
            elif method == "三笔":
                fill_color  = "rgba(155, 89, 182, 0.15)"
                line_color  = "#9B59B6"
            else:
                fill_color  = "rgba(150,150,150,0.15)"
                line_color  = "#999999"

            # ── 1. 中枢矩形框（淡色填充） ──
            chart.fig.add_shape(
                type="rect", x0=x0, y0=y_lower, x1=x1, y1=y_upper,
                xref="x", yref="y", line=dict(color=line_color, width=2),
                fillcolor=fill_color, layer="below"
            )

            # ── 2. 上轨横线（实线，颜色同矩形） ──
            chart.fig.add_shape(
                type="line", x0=x0, y0=y_upper, x1=x1, y1=y_upper,
                xref="x", yref="y",
                line=dict(color=line_color, width=2, dash="solid"),
                layer="above"
            )
            # ── 3. 下轨横线（实线，颜色同矩形） ──
            chart.fig.add_shape(
                type="line", x0=x0, y0=y_lower, x1=x1, y1=y_lower,
                xref="x", yref="y",
                line=dict(color=line_color, width=2, dash="solid"),
                layer="above"
            )
            # ── 4. 中枢线横线（虚线，半透明） ──
            chart.fig.add_shape(
                type="line", x0=x0, y0=y_center, x1=x1, y1=y_center,
                xref="x", yref="y",
                line=dict(color=line_color, width=1.2, dash="dot"),
                layer="above"
            )

            # ── 6. 右端轨道价格标注（深度简化：合并编号/方法/中枢线） ──
            method   = getattr(ct, 'method', '肉眼' if ct.is_visible else '?')
            if ct.direction == Direction.Up:
                # 上涨线段：中枢线 = 下轨
                labels = [
                    (y_upper, f"上轨 {y_upper:.2f}"),
                    (y_lower, f"#{c_idx} {method}-中枢线下轨 {y_lower:.2f}")
                ]
            else:
                # 下跌线段：中枢线 = 上轨
                labels = [
                    (y_upper, f"#{c_idx} {method}-中枢线上轨 {y_upper:.2f}"),
                    (y_lower, f"下轨 {y_lower:.2f}")
                ]

            for y_val, label_text in labels:
                chart.fig.add_annotation(
                    x=x1, y=y_val, xref="x", yref="y",
                    text=label_text,
                    showarrow=False,
                    font=dict(size=7, color=line_color),  # 字体缩小到 7
                    xanchor="left", yanchor="middle", xshift=5,
                    bgcolor="rgba(255,255,255,0.4)"
                )
            c_idx += 1


    # 4. 叠加当前的“潜在中枢”（即还在寻找中的探测器结果）
    if hasattr(engine, 'potential_centers') and engine.potential_centers:
        for ct in engine.potential_centers:
            x0 = ct.start_dt.strftime("%Y-%m-%d %H:%M")
            x1 = ct.end_dt.strftime("%Y-%m-%d %H:%M")
            chart.fig.add_shape(
                type="rect", x0=x0, y0=ct.lower_rail, x1=x1, y1=ct.upper_rail,
                xref="x", yref="y", line_width=1, line_dash="dot",
                fillcolor="rgba(144, 164, 174, 0.05)", line_color="#90A4AE"
            )

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

    chart.fig.write_html(output_file)
    logger.success(f"成功！摩尔结构图表已保存至: {os.path.abspath(output_file)}")
    return chart

if __name__ == '__main__':
    try:
        symbols = research.get_symbols('中证500成分股')[:30]
        if not symbols:
            raise ValueError("未能获取中证500成分股")
            
        symbol = symbols[0]
        # 获取真实的数据
        logger.info(f"拉取标的 {symbol} 真实 K 线...")
        bars = research.get_raw_bars(symbol, freq='30分钟', sdt='20210101', edt='20210701')
        
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
        plot_moore_structure(bars, engine, output_file="moore_sz500_30f_plot.html", title=f"摩尔缠论 {symbol} 30分钟 结构测试")
    except Exception as e:
        import traceback
        traceback.print_exc()
        logger.error(f"测绘失败: {e}")
