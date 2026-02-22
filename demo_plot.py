import os
import pandas as pd
from typing import List, Dict, Any
from czsc.core import CZSC, format_standard_kline, Freq
from czsc.mock import generate_symbol_kines
from czsc.utils.plotting.kline import KlineChart


def _bi_direction_text(bi) -> str:
    """统一笔方向文本：向上 / 向下"""
    text = str(bi.direction)
    return "向上" if "上" in text else "向下"


def _three_bi_overlap(bis3: List[Any]) -> bool:
    """三笔是否存在价格重叠区间"""
    high_min = min(x.high for x in bis3)
    low_max = max(x.low for x in bis3)
    return low_max <= high_min


def build_segments_from_bis(bis: List[Any]) -> List[Dict[str, Any]]:
    """按规则从去包含后的笔序列构建线段（含未完成草稿）

    规则：
    1) 线段至少由三笔组成，且三笔必须有价格重叠；
    2) 线段方向仅向上/向下，起始笔与终结笔方向一致；
    3) 线段终结由下一反向线段的出现确认；
    4) 最后一段若未被反向线段确认，则标记为未完成草稿。
    """
    if len(bis) < 3:
        return []

    def opposite(d: str) -> str:
        return "向下" if d == "向上" else "向上"

    def find_segment(start_idx: int, expected_dir: str = ""):
        """从 start_idx 开始寻找一条线段，并尽量向后延伸"""
        n = len(bis)
        for s in range(start_idx, n - 2):
            d0 = _bi_direction_text(bis[s])
            if expected_dir and d0 != expected_dir:
                continue

            d1 = _bi_direction_text(bis[s + 1])
            d2 = _bi_direction_text(bis[s + 2])
            if not (d0 != d1 and d2 == d0):
                continue
            if not _three_bi_overlap([bis[s], bis[s + 1], bis[s + 2]]):
                continue

            # 先满足三笔成段，再按“两笔一延伸”继续扩展
            e = s + 2
            while e + 2 < n:
                dn1 = _bi_direction_text(bis[e + 1])
                dn2 = _bi_direction_text(bis[e + 2])
                if dn1 != opposite(d0) or dn2 != d0:
                    break
                if not _three_bi_overlap([bis[e], bis[e + 1], bis[e + 2]]):
                    break
                e += 2
            return {"start": s, "end": e, "direction": d0}
        return None

    first = find_segment(0)
    if not first:
        return []

    segments: List[Dict[str, Any]] = []
    current = first

    while True:
        next_dir = opposite(current["direction"])
        nxt = find_segment(current["end"] + 1, expected_dir=next_dir)

        # A 规则：后一段从前段结束后开始，端点可共用（通过 bi.fx_b == next_bi.fx_a 实现）
        if not nxt or nxt["start"] != current["end"] + 1:
            current["confirmed"] = False
            segments.append(current)
            break

        current["confirmed"] = True
        segments.append(current)
        current = nxt

    return segments


def _segment_price_range(seg: Dict[str, Any], bis: List[Any]) -> Dict[str, float]:
    """按 1A 规则：线段价格区间使用线段两端点的 max/min"""
    sb = bis[seg["start"]]
    eb = bis[seg["end"]]
    y0, y1 = sb.fx_a.fx, eb.fx_b.fx
    return {"high": max(y0, y1), "low": min(y0, y1)}


def _ranges_overlap(r1: Dict[str, float], r2: Dict[str, float]) -> bool:
    """按 2A 规则：有任意交集即视为回到中枢"""
    return r1["high"] >= r2["low"] and r1["low"] <= r2["high"]


def build_centers_from_segments(segments: List[Dict[str, Any]], bis: List[Any]) -> List[Dict[str, Any]]:
    """按线段构建中枢（1A/2A/3A/4A/5A）

    - 至少 5 根线段：进入段 + 中间至少 3 根重叠线段 + 走出段
    - 中枢方向 = 进入段方向，且进入段与走出段方向一致
    - ZG/ZD 固定使用中枢本体前三根线段计算（3A）
    - 完成确认：走出后再出现一根线段且仍未回到中枢（4A）
    - 下一中枢从“确认段”之后开始（5A）
    """
    centers: List[Dict[str, Any]] = []
    if len(segments) < 5:
        return centers

    i = 0
    n = len(segments)
    while i <= n - 5:
        enter = segments[i]
        enter_dir = enter["direction"]
        opp_dir = "向下" if enter_dir == "向上" else "向上"

        # 中枢本体至少 3 根，且方向应为 opp-enter-opp（上升中枢即 下-上-下）
        b1, b2, b3 = segments[i + 1], segments[i + 2], segments[i + 3]
        if not (b1["direction"] == opp_dir and b2["direction"] == enter_dir and b3["direction"] == opp_dir):
            i += 1
            continue

        r1 = _segment_price_range(b1, bis)
        r2 = _segment_price_range(b2, bis)
        r3 = _segment_price_range(b3, bis)
        zg = min(r1["high"], r2["high"], r3["high"])
        zd = max(r1["low"], r2["low"], r3["low"])
        if zd > zg:
            i += 1
            continue

        center_range = {"high": zg, "low": zd}
        body_start = i + 1
        body_end = i + 3

        # 中枢延续：后续线段若与中枢有交集，则仍属中枢本体
        j = body_end + 1
        while j < n:
            rj = _segment_price_range(segments[j], bis)
            if _ranges_overlap(rj, center_range):
                body_end = j
                j += 1
            else:
                break

        # 走出段：第一根不回到中枢的线段，方向应与进入段一致
        exit_idx = j if j < n else None
        if exit_idx is None or segments[exit_idx]["direction"] != enter_dir:
            centers.append(
                {
                    "direction": enter_dir,
                    "zg": zg,
                    "zd": zd,
                    "enter_idx": i,
                    "body_start": body_start,
                    "body_end": body_end,
                    "exit_idx": None,
                    "confirm_idx": None,
                    "confirmed": False,
                }
            )
            break

        # 完成确认：再来一根线段仍不回中枢
        confirm_idx = exit_idx + 1 if exit_idx + 1 < n else None
        confirmed = False
        if confirm_idx is not None:
            rc = _segment_price_range(segments[confirm_idx], bis)
            confirmed = not _ranges_overlap(rc, center_range)

        centers.append(
            {
                "direction": enter_dir,
                "zg": zg,
                "zd": zd,
                "enter_idx": i,
                "body_start": body_start,
                "body_end": body_end,
                "exit_idx": exit_idx,
                "confirm_idx": confirm_idx,
                "confirmed": confirmed,
            }
        )

        # 5A：从确认段之后开始找下一中枢；若未确认则停止（实时中枢）
        if confirmed and confirm_idx is not None:
            i = confirm_idx + 1
        else:
            break

    return centers


def plot_chan_structure(
    df: pd.DataFrame,
    output_file: str = "czsc_demo_plot.html",
    title: str = "缠论结构图",
    freq: Freq = Freq.F30,
    max_bi_num: int = 5000,
):
    """输入K线数据，直接绘制缠论结构图并导出 HTML。"""
    print("正在计算缠论结构（分型、笔）...")
    bars = format_standard_kline(df, freq=freq)
    c = CZSC(bars, max_bi_num=max_bi_num)

    # 使用 KlineChart 绘制交互式图表，并叠加缠论结构
    print("正在绘制图表...")
    chart = KlineChart(n_rows=3, row_heights=[0.75, 0.001, 0.249], title=title)
    # 强制使用时间轴，避免 category 轴对 shape 坐标映射产生歧义
    chart.fig.update_xaxes(type="date")
    chart.add_kline(df, name="")
    chart.add_sma(df, ma_seq=(5, 10, 20), row=1, visible=True, line_width=1.2)
    chart.add_macd(df, row=3)

    # 叠加分型
    if getattr(c, "fx_list", None):
        fx_df = pd.DataFrame([{"dt": x.dt, "fx": x.fx, "text": str(x.mark)} for x in c.fx_list])
        chart.add_scatter_indicator(fx_df["dt"], fx_df["fx"], name="分型", row=1, text=fx_df["text"], mode="markers")

    # 叠加笔
    if getattr(c, "bi_list", None):
        bi_points = [{"dt": x.fx_a.dt, "bi": x.fx_a.fx, "text": str(x.fx_a.mark)} for x in c.bi_list]
        bi_points.append({"dt": c.bi_list[-1].fx_b.dt, "bi": c.bi_list[-1].fx_b.fx, "text": str(c.bi_list[-1].fx_b.mark)})
        bi_df = pd.DataFrame(bi_points)
        chart.add_scatter_indicator(
            bi_df["dt"],
            bi_df["bi"],
            name="笔",
            row=1,
            text=bi_df["text"],
            mode="lines+markers+text",
            textposition="top center",
        )

        # 叠加线段（正式规则 + 实时草稿）
        segments = build_segments_from_bis(c.bi_list)
        print(f"本次线段数量: {len(segments)}")
        for i, seg in enumerate(segments, start=1):
            sb = c.bi_list[seg["start"]]
            eb = c.bi_list[seg["end"]]
            seg_msg = (
                f"线段{i}: dir={seg['direction']}, confirmed={seg['confirmed']}, "
                f"start=({sb.fx_a.dt}, {sb.fx_a.fx}), end=({eb.fx_b.dt}, {eb.fx_b.fx})"
            )
            print(seg_msg)

        for i, seg in enumerate(segments, start=1):
            sb = c.bi_list[seg["start"]]
            eb = c.bi_list[seg["end"]]
            x0, y0 = sb.fx_a.dt, sb.fx_a.fx
            x1, y1 = eb.fx_b.dt, eb.fx_b.fx

            is_up = seg["direction"] == "向上"
            is_confirmed = seg["confirmed"]
            line_color = "#E91E63" if is_up else "#00A65A"
            line_dash = "solid" if is_confirmed else "dash"
            line_width = 3 if is_confirmed else 2

            chart.fig.add_shape(
                type="line",
                x0=x0,
                y0=y0,
                x1=x1,
                y1=y1,
                xref="x",
                yref="y",
                line=dict(color=line_color, width=line_width, dash=line_dash),
                layer="above",
            )
            chart.fig.add_annotation(
                x=x1,
                y=y1,
                xref="x",
                yref="y",
                text=f"线段{i}{'' if is_confirmed else '(草稿)'}",
                showarrow=False,
                font=dict(size=9, color=line_color),
                xanchor="left",
                yanchor="middle",
            )

        # 叠加中枢：按线段定义构建（至少5段，进入/走出同向）
        centers = build_centers_from_segments(segments, c.bi_list)
        print(f"本次中枢数量: {len(centers)}")
        for i, ct in enumerate(centers, start=1):
            enter = segments[ct["enter_idx"]]
            body_s = segments[ct["body_start"]]
            body_e = segments[ct["body_end"]]
            exit_text = f"{ct['exit_idx']}" if ct["exit_idx"] is not None else "None"
            confirm_text = f"{ct['confirm_idx']}" if ct["confirm_idx"] is not None else "None"
            center_msg = (
                f"中枢{i}: dir={ct['direction']}, confirmed={ct['confirmed']}, "
                f"ZG={ct['zg']:.2f}, ZD={ct['zd']:.2f}, enter_seg={ct['enter_idx']}, "
                f"body=[{ct['body_start']}-{ct['body_end']}], exit_seg={exit_text}, confirm_seg={confirm_text}"
            )
            time_msg = (
                f"  时间: enter_start={c.bi_list[enter['start']].fx_a.dt}, "
                f"body_start={c.bi_list[body_s['start']].fx_a.dt}, "
                f"body_end={c.bi_list[body_e['end']].fx_b.dt}"
            )
            print(center_msg)
            print(time_msg)

        for i, ct in enumerate(centers, start=1):
            body_s = segments[ct["body_start"]]
            body_e = segments[ct["body_end"]]
            x0 = c.bi_list[body_s["start"]].fx_a.dt
            x1 = c.bi_list[body_e["end"]].fx_b.dt
            y0 = ct["zd"]
            y1 = ct["zg"]
            chart.fig.add_shape(
                type="rect",
                x0=x0,
                x1=x1,
                y0=y0,
                y1=y1,
                xref="x",
                yref="y",
                line=dict(color="#2E86DE", width=1),
                fillcolor="rgba(46, 134, 222, 0.14)" if ct["confirmed"] else "rgba(255, 165, 0, 0.14)",
                layer="above",
            )
            chart.fig.add_annotation(
                x=x1,
                y=y1,
                xref="x",
                yref="y",
                text=f"中枢{i}{'' if ct['confirmed'] else '(进行中)'}",
                showarrow=False,
                font=dict(size=10, color="#2E86DE" if ct["confirmed"] else "#FFA500"),
                xanchor="left",
                yanchor="bottom",
            )

    # 导出为 HTML 图表
    chart.fig.write_html(output_file)

    print(f"成功！图表已保存至: {os.path.abspath(output_file)}")
    print("你可以直接在浏览器中打开这个文件查看 K线、分型、笔、线段(含草稿)、中枢、均线和 MACD。")
    return chart, c


def create_demo_plot():
    """示例：使用 mock 数据快速出图。"""
    print("正在生成模拟数据...")
    df = generate_symbol_kines('000001', '30分钟', '20240701', '20241231')
    plot_chan_structure(
        df=df,
        output_file="czsc_demo_plot.html",
        title="000001-30分钟 测试数据预览",
        freq=Freq.F30,
        max_bi_num=5000,
    )

if __name__ == "__main__":
    try:
        create_demo_plot()
    except ImportError as e:
        print(f"错误: 缺少必要依赖 ({e})。请确保已安装 czsc 及其依赖。")
    except Exception as e:
        print(f"运行出错: {e}")
