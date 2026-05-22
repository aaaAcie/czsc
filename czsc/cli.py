# -*- coding: utf-8 -*-
"""Command-line helpers for local CZSC workflows."""

from __future__ import annotations

import argparse
import os
import runpy
import sys
from pathlib import Path


def _normalize_data_root(raw_root: str) -> Path:
    root = Path(raw_root).expanduser()
    if root.name.lower() in {"alldata", "origindata", "30min", "30分钟"}:
        root = root.parent
    return root.resolve()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run sz500_moore_echarts_plot.py with local stock data.")
    parser.add_argument(
        "--data-root",
        default=r"E:\stockData",
        help="Stock data root directory. Default: E:\\stockData",
    )
    parser.add_argument(
        "--daily-source",
        choices=["auto", "origin", "30m"],
        default="auto",
        help="Daily bar source. Default: auto",
    )
    args = parser.parse_args(argv)

    repo_root = Path(__file__).resolve().parent.parent
    data_root = _normalize_data_root(args.data_root)
    all_data_30m = data_root / "allData" / "30min"
    uv_cache = repo_root / ".uv-cache-local"

    if not data_root.exists():
        raise FileNotFoundError(f"Data root not found: {data_root}")
    if not all_data_30m.exists():
        raise FileNotFoundError(f"30-minute data not found: {all_data_30m}")

    uv_cache.mkdir(parents=True, exist_ok=True)
    os.environ["UV_CACHE_DIR"] = str(uv_cache)
    os.environ["czsc_research_cache"] = str(data_root)
    os.environ["CZSC_DAILY_SOURCE"] = args.daily_source

    script_path = repo_root / "sz500_moore_echarts_plot.py"
    runpy.run_path(str(script_path), run_name="__main__")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
