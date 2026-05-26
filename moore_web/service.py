"""Service helpers for dynamic Moore ECharts rendering.

The web layer intentionally depends on a small bars-provider interface.  The
current implementation reads from the local research connector, and a future
remote API client can replace it without changing routes or plotting code.
"""

from __future__ import annotations

import glob
import os
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Protocol

import pandas as pd

from czsc.connectors import research
from czsc.moore.analyze import MooreCZSC
from sz500_moore_echarts_plot import _initial_daily_ma_relax, plot_moore_structure_echarts


DEFAULT_YEARS = 5
DEFAULT_OUTPUT_DIR = Path("moore_plots") / "web_cache"
_SYMBOL_RE = re.compile(r"^[A-Za-z0-9._-]+$")


class BarsProvider(Protocol):
    """Contract for a data source that returns CZSC RawBar objects."""

    def get_bars(self, symbol: str, sdt: str, edt: str, fq: str = "前复权") -> list:
        """Return bars for ``symbol`` in [sdt, edt]."""


@dataclass(frozen=True)
class ResearchBarsProvider:
    """Local CSV/originData backed provider used by the existing scripts."""

    def get_bars(self, symbol: str, sdt: str, edt: str, fq: str = "前复权") -> list:
        return research.get_raw_bars_origin(symbol, sdt=sdt, edt=edt, fq=fq)

    def get_symbol_name(self, symbol: str) -> str | None:
        file_path = _find_origin_csv(symbol)
        if not file_path:
            return None

        for encoding in ("utf-8-sig", "gbk", "utf-8"):
            try:
                df = pd.read_csv(file_path, encoding=encoding, nrows=1, usecols=["股票名称"])
                if not df.empty:
                    name = str(df.iloc[0]["股票名称"]).strip()
                    return name or None
            except Exception:
                continue
        return None


@dataclass(frozen=True)
class MooreRenderRequest:
    symbol: str
    sdt: str
    edt: str
    years: int = DEFAULT_YEARS
    fq: str = "前复权"
    allow_initial_daily_ma_relax: bool = False
    show_daily_shadow_b: bool = True
    enable_pre_round: bool = True
    replay_centers_after_macro_swallow: bool = False


@dataclass(frozen=True)
class MooreRenderResult:
    html: str
    output_file: Path
    bars_count: int
    request: MooreRenderRequest


def validate_symbol(symbol: str) -> str:
    symbol = symbol.strip()
    if not symbol or not _SYMBOL_RE.match(symbol):
        raise ValueError("symbol 只能包含字母、数字、点、下划线和短横线")
    return symbol


def normalize_yyyymmdd(value: str, field_name: str) -> str:
    try:
        return pd.Timestamp(value).strftime("%Y%m%d")
    except Exception as exc:
        raise ValueError(f"{field_name} 不是有效日期: {value}") from exc


def resolve_date_range(
    sdt: str | None = None,
    edt: str | None = None,
    years: int = DEFAULT_YEARS,
    *,
    today: pd.Timestamp | None = None,
) -> tuple[str, str]:
    if years <= 0:
        raise ValueError("years 必须大于 0")

    end = pd.Timestamp(edt) if edt else (today or pd.Timestamp.now(tz="Asia/Shanghai")).tz_localize(None)
    start = pd.Timestamp(sdt) if sdt else end - pd.DateOffset(years=years)
    if start > end:
        raise ValueError("sdt 不能晚于 edt")
    return start.strftime("%Y%m%d"), end.strftime("%Y%m%d")


def make_render_request(
    symbol: str,
    sdt: str | None = None,
    edt: str | None = None,
    years: int = DEFAULT_YEARS,
    fq: str = "前复权",
    allow_initial_daily_ma_relax: bool = False,
    show_daily_shadow_b: bool = True,
    enable_pre_round: bool = True,
    replay_centers_after_macro_swallow: bool = False,
) -> MooreRenderRequest:
    resolved_sdt, resolved_edt = resolve_date_range(sdt=sdt, edt=edt, years=years)
    return MooreRenderRequest(
        symbol=validate_symbol(symbol),
        sdt=resolved_sdt,
        edt=resolved_edt,
        years=years,
        fq=fq,
        allow_initial_daily_ma_relax=allow_initial_daily_ma_relax,
        show_daily_shadow_b=show_daily_shadow_b,
        enable_pre_round=enable_pre_round,
        replay_centers_after_macro_swallow=replay_centers_after_macro_swallow,
    )


def build_engine(bars: list, request: MooreRenderRequest) -> MooreCZSC:
    with _initial_daily_ma_relax(request.allow_initial_daily_ma_relax):
        return MooreCZSC(
            bars,
            ma34_cross_as_valid_gate=True,
            ma34_cross_expand_one_k=False,
            audit_link_rounds=3,
            enable_pre_round=request.enable_pre_round,
            replay_centers_after_macro_swallow=request.replay_centers_after_macro_swallow,
        )


def request_cache_path(request: MooreRenderRequest, output_dir: Path = DEFAULT_OUTPUT_DIR) -> Path:
    safe_symbol = re.sub(r"[^A-Za-z0-9._-]+", "_", request.symbol)
    return output_dir / f"{safe_symbol}.html"


def _find_origin_csv(symbol: str) -> str | None:
    origin_dir = os.path.join(research.cache_path, "originData")
    if symbol.endswith(".csv"):
        file_path = os.path.join(origin_dir, symbol)
        return file_path if os.path.exists(file_path) else None

    candidate = symbol
    if "." not in symbol and (symbol.startswith("sz") or symbol.startswith("sh")):
        market = symbol[:2].upper()
        code = symbol[2:]
        candidate = f"{code}.{market}"

    exact_path = os.path.join(origin_dir, f"{candidate}.csv")
    if os.path.exists(exact_path):
        return exact_path

    for ext in [".SZ", ".SH", ".BJ"]:
        ext_path = os.path.join(origin_dir, f"{candidate.upper()}{ext}.csv")
        if os.path.exists(ext_path):
            return ext_path

    matches = glob.glob(os.path.join(origin_dir, f"*{candidate}*.csv"))
    return matches[0] if matches else None


def _bar_dt_yyyymmdd(bar) -> str:
    return pd.Timestamp(bar.dt).strftime("%Y%m%d")


def get_symbol_name(provider: BarsProvider, symbol: str) -> str | None:
    resolver = getattr(provider, "get_symbol_name", None)
    if not resolver:
        return None
    return resolver(symbol)


def make_chart_title(request: MooreRenderRequest, bars: list, provider: BarsProvider) -> str:
    chart_symbol = getattr(bars[0], "symbol", request.symbol) if bars else request.symbol
    symbol_name = get_symbol_name(provider, request.symbol)
    if symbol_name:
        return f"摩尔缠论 {chart_symbol} {symbol_name} ({request.sdt} ~ {request.edt})"
    return f"摩尔缠论 {chart_symbol} ({request.sdt} ~ {request.edt})"


def load_bars_with_available_range(request: MooreRenderRequest, provider: BarsProvider) -> tuple[list, MooreRenderRequest]:
    bars = provider.get_bars(request.symbol, sdt=request.sdt, edt=request.edt, fq=request.fq)
    if not bars:
        # If the requested start is earlier than the available dataset, widen
        # leftward and let the returned bars define the real chart range.  This
        # applies to both the default 5-year window and an explicit user sdt.
        bars = provider.get_bars(request.symbol, sdt="19000101", edt=request.edt, fq=request.fq)

    if not bars:
        raise LookupError(f"没有找到 {request.symbol} 在 {request.sdt} ~ {request.edt} 的行情数据")

    effective_request = replace(
        request,
        sdt=_bar_dt_yyyymmdd(bars[0]),
        edt=_bar_dt_yyyymmdd(bars[-1]),
    )
    return bars, effective_request


def render_moore_html(
    request: MooreRenderRequest,
    *,
    provider: BarsProvider | None = None,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    refresh: bool = False,
) -> MooreRenderResult:
    provider = provider or ResearchBarsProvider()
    bars, effective_request = load_bars_with_available_range(request, provider)
    output_file = request_cache_path(effective_request, output_dir=output_dir)
    if output_file.exists() and not refresh:
        return MooreRenderResult(
            html=output_file.read_text(encoding="utf-8"),
            output_file=output_file,
            bars_count=-1,
            request=effective_request,
        )

    engine = build_engine(bars, effective_request)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    plot_moore_structure_echarts(
        bars,
        engine,
        output_file=str(output_file),
        title=make_chart_title(effective_request, bars, provider),
        desc_text="金色: 宏观 | 紫色: 微观 | 箭头: 中枢线确认K (CK) | 圆圈: 起始锚点 (K0)",
        show_daily_shadow_b=effective_request.show_daily_shadow_b,
    )
    return MooreRenderResult(
        html=output_file.read_text(encoding="utf-8"),
        output_file=output_file,
        bars_count=len(bars),
        request=effective_request,
    )
