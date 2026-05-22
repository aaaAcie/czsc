"""FastAPI entrypoint for dynamic Moore ECharts pages."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from moore_web.service import DEFAULT_YEARS, make_render_request, render_moore_html


app = FastAPI(title="Moore ECharts Web", version="0.1.0")
ASSETS_DIR = Path("moore_plots") / "assets"
app.mount("/moore/assets", StaticFiles(directory=ASSETS_DIR), name="moore-assets")


@app.get("/", include_in_schema=False)
def index() -> RedirectResponse:
    return RedirectResponse(url="/moore/300339")


@app.get("/health")
def health() -> dict:
    return {"ok": True}


@app.get("/moore/{symbol}", response_class=HTMLResponse)
def moore_chart(
    symbol: str,
    sdt: str | None = Query(default=None, description="开始日期，支持 YYYYMMDD / YYYY-MM-DD"),
    edt: str | None = Query(default=None, description="结束日期，默认今天"),
    years: int = Query(default=DEFAULT_YEARS, ge=1, le=30, description="未传 sdt 时向前取几年"),
    fq: str = Query(default="前复权", description="复权方式"),
    refresh: bool = Query(default=False, description="忽略缓存并重新生成"),
    allow_initial_daily_ma_relax: bool = Query(default=False, description="放宽最早日线候选 MA 门槛"),
    show_daily_shadow_b: bool = Query(default=True, description="展示 Daily Shadow B 参考层"),
    enable_pre_round: bool = Query(default=True, description="启用 Pre-Round"),
    replay_centers_after_macro_swallow: bool = Query(default=False, description="宏观吞噬后重播中枢"),
) -> HTMLResponse:
    try:
        request = make_render_request(
            symbol=symbol,
            sdt=sdt,
            edt=edt,
            years=years,
            fq=fq,
            allow_initial_daily_ma_relax=allow_initial_daily_ma_relax,
            show_daily_shadow_b=show_daily_shadow_b,
            enable_pre_round=enable_pre_round,
            replay_centers_after_macro_swallow=replay_centers_after_macro_swallow,
        )
        result = render_moore_html(request, refresh=refresh)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"生成图表失败: {exc}") from exc

    headers = {
        "X-Moore-Symbol": result.request.symbol,
        "X-Moore-Sdt": result.request.sdt,
        "X-Moore-Edt": result.request.edt,
        "X-Moore-Cache-File": str(result.output_file),
    }
    return HTMLResponse(content=result.html, headers=headers)
