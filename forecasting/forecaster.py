# -*- coding: utf-8 -*-
"""
forecaster.py
=============
Holt-Winters Exponential Smoothing forecaster for the Sales Dashboard.

Trains one ExponentialSmoothing model per (store × department) time series,
generates a 12-week forward forecast with 95% prediction intervals, and
flags stockout-risk weeks.

Usage (standalone):
    python forecasting/forecaster.py

Usage (from etl_pipeline.py):
    from forecasting.forecaster import run_forecast
    forecast_df = run_forecast(fact_df)
"""

import warnings
import sqlite3
import sys
import io
from pathlib import Path
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
from statsmodels.tsa.holtwinters import ExponentialSmoothing  # type: ignore[import-untyped]
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeElapsedColumn
from rich.table import Table

warnings.filterwarnings("ignore")

# Force UTF-8 so Rich box/spinner chars work on Windows CP1252 terminals
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

console = Console(force_terminal=True, highlight=False)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
FORECAST_HORIZON   = 12          # weeks ahead to forecast
SEASONAL_PERIOD    = 52          # weekly seasonality
MIN_HISTORY_WEEKS  = 26          # minimum data points needed to fit model
STOCKOUT_THRESHOLD = 1.15        # flag if predicted > 1.15× rolling 8-week avg
ROLLING_WINDOW     = 8           # weeks for rolling average baseline
CI_ALPHA           = 0.05        # 95% confidence interval


def _build_series_index(series: pd.Series, last_date: pd.Timestamp) -> pd.DatetimeIndex:
    """Generate the 12-week forecast date index starting from the week after last_date."""
    freq = pd.tseries.frequencies.to_offset("W-SAT")
    return pd.date_range(start=last_date + timedelta(weeks=1), periods=FORECAST_HORIZON, freq="W")


def _fit_holt_winters(series: pd.Series) -> tuple | None:
    """
    Fit a Holt-Winters model to a weekly time series.

    Returns (fitted_model, predicted_mean, lower_ci, upper_ci) or None on failure.

    Model configuration:
      - trend='add'           : linear trend component
      - seasonal='mul'        : multiplicative seasonality (handles scale variance)
      - seasonal_periods=52   : one full year of weekly cycles
      - damped_trend=True     : prevents over-extrapolation in long horizons
    """
    try:
        # Clip any remaining negatives and add a tiny floor to avoid log issues
        clean = series.clip(lower=0.01)

        model = ExponentialSmoothing(
            clean,
            trend="add",
            seasonal="mul",
            seasonal_periods=SEASONAL_PERIOD,
            damped_trend=True,
            initialization_method="estimated",
        )
        fit = model.fit(optimized=True, remove_bias=True)

        # Simulate forecast distribution for CI estimation
        simulations = fit.simulate(
            nsimulations=FORECAST_HORIZON,
            repetitions=200,
            error="mul",
            random_errors="bootstrap",
        )
        predicted = fit.forecast(FORECAST_HORIZON)
        lower_ci  = simulations.quantile(CI_ALPHA / 2,    axis=1)
        upper_ci  = simulations.quantile(1 - CI_ALPHA / 2, axis=1)

        return fit, predicted, lower_ci, upper_ci

    except Exception:
        # Fallback: naive seasonal forecast (repeat last year's same-week values)
        return None


def _naive_seasonal_forecast(series: pd.Series) -> tuple:
    """
    Fallback for series too short or unstable for Holt-Winters.
    Repeats the pattern from the last available seasonal cycle.
    CI is ±20% of mean as a conservative band.
    """
    n = len(series)
    cycle     = min(SEASONAL_PERIOD, n)
    tail      = series.iloc[-cycle:]
    rep       = np.tile(tail.values, (FORECAST_HORIZON // cycle) + 2)
    predicted = pd.Series(rep[:FORECAST_HORIZON])
    lower_ci  = predicted * 0.80
    upper_ci  = predicted * 1.20
    return None, predicted, lower_ci, upper_ci


def _stockout_flag_series(predicted: pd.Series, series: pd.Series) -> float:
    """
    Return per-series baseline mean for use in global stockout flagging.
    """
    baseline = series.iloc[-ROLLING_WINDOW:].mean() if len(series) >= ROLLING_WINDOW \
               else series.mean()
    return float(baseline)


def run_forecast(fact_df: pd.DataFrame) -> pd.DataFrame:
    """
    Main entry point.

    Parameters
    ----------
    fact_df : pd.DataFrame
        Must contain columns: store_key, dept_key, date_key (YYYY-MM-DD), weekly_sales

    Returns
    -------
    pd.DataFrame with columns:
        store_key, dept_key, forecast_date, predicted_sales,
        lower_ci_95, upper_ci_95, stockout_risk_flag, model_type
    """
    console.rule("[bold cyan]Forecasting Module — Holt-Winters")

    fact_df = fact_df.copy()
    fact_df["date_key"] = pd.to_datetime(fact_df["date_key"])

    # Aggregate to store x dept x week (sum departments if any duplication)
    agg = (
        fact_df
        .groupby(["store_key", "dept_key", "date_key"], as_index=False)["weekly_sales"]
        .sum()
        .sort_values(["store_key", "dept_key", "date_key"])
    )

    groups      = list(agg.groupby(["store_key", "dept_key"]))
    total       = len(groups)
    results     = []
    hw_count    = 0
    naive_count = 0

    console.print(f"  Fitting models for [bold]{total}[/bold] store×department series…")

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("{task.completed}/{task.total}"),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Training…", total=total)

        for (store_key, dept_key), grp in groups:
            # Build series: sort by date, keep the actual dates (don't force a rigid freq)
            # so data is never lost due to calendar misalignment.
            series = (
                grp.set_index("date_key")["weekly_sales"]
                .sort_index()
                .fillna(0)
            )
            # Fill any gaps between the first and last observed date
            if len(series) > 1:
                full_idx = pd.date_range(series.index.min(), series.index.max(), freq="7D")
                series   = series.reindex(full_idx, method="nearest", tolerance="3D").ffill().fillna(0)
            last_date = series.index[-1]

            if len(series) >= MIN_HISTORY_WEEKS:
                result = _fit_holt_winters(series)
            else:
                result = None

            if result is None:
                _, predicted, lower_ci, upper_ci = _naive_seasonal_forecast(series)
                model_type = "NaiveSeasonal"
                naive_count += 1
            else:
                _, predicted, lower_ci, upper_ci = result
                model_type = "HoltWinters"
                hw_count += 1

            forecast_dates = pd.date_range(
                start=last_date + timedelta(weeks=1),
                periods=FORECAST_HORIZON,
                freq="W",
            )

            predicted = np.array(predicted).flatten()
            lower_ci  = np.array(lower_ci).flatten()
            upper_ci  = np.array(upper_ci).flatten()

            stockout_baseline = _stockout_flag_series(pd.Series(predicted), series)
            run_ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

            for i in range(FORECAST_HORIZON):
                results.append({
                    "store_key":          int(store_key),
                    "dept_key":           int(dept_key),
                    "forecast_date":      forecast_dates[i].strftime("%Y-%m-%d"),
                    "predicted_sales":    round(float(max(0, predicted[i])), 2),
                    "lower_ci_95":        round(float(max(0, lower_ci[i])),  2),
                    "upper_ci_95":        round(float(max(0, upper_ci[i])),  2),
                    "stockout_risk_flag": 0,   # filled below after global percentile calc
                    "model_type":         model_type,
                    "created_at":         run_ts,
                    "_baseline":          stockout_baseline,  # temp column, dropped later
                })

            progress.advance(task)

    forecast_df = pd.DataFrame(results)

    # -----------------------------------------------------------------------
    # Two-stage stockout risk flagging (applied globally after all series done)
    # Stage 1: predicted > 130% of that series' own recent baseline
    # Stage 2: predicted is in the global top 20% of all forecasted values
    # A week is HIGH RISK only if BOTH conditions are true.
    # This prevents the entire forecast from being flagged when HW trends upward.
    # -----------------------------------------------------------------------
    global_p80 = forecast_df["predicted_sales"].quantile(0.80)
    stage1 = forecast_df["predicted_sales"] > (forecast_df["_baseline"] * STOCKOUT_THRESHOLD)
    stage2 = forecast_df["predicted_sales"] >= global_p80
    forecast_df["stockout_risk_flag"] = (stage1 & stage2).astype(int)
    forecast_df.drop(columns=["_baseline"], inplace=True)

    # -----------------------------------------------------------------------
    # Evaluation: MAPE on held-out last 8 weeks of fact data
    # -----------------------------------------------------------------------
    _evaluate_mape(agg, forecast_df if False else None, fact_df)

    # Summary table
    stockout_count = (forecast_df["stockout_risk_flag"] == 1).sum()
    table = Table(title="Forecast Summary", show_header=True, header_style="bold magenta")
    table.add_column("Metric",     style="cyan", no_wrap=True)
    table.add_column("Value",      style="white")
    table.add_row("Total series forecasted",    str(total))
    table.add_row("Holt-Winters models",        str(hw_count))
    table.add_row("Naive seasonal fallbacks",   str(naive_count))
    table.add_row("Forecast horizon",           f"{FORECAST_HORIZON} weeks")
    table.add_row("Total forecast rows",        str(len(forecast_df)))
    table.add_row("Stockout-risk weeks flagged",f"[bold red]{stockout_count}[/bold red]")
    console.print(table)

    return forecast_df


def _evaluate_mape(agg: pd.DataFrame, _unused, fact_df: pd.DataFrame):
    """
    Compute MAPE on the last 8 weeks of actual data by fitting on the
    prior period and comparing against actuals. Reports per-series median MAPE.
    """
    console.print("\n  [bold yellow]Evaluating forecast accuracy (held-out MAPE)…[/bold yellow]")

    mapes = []
    groups = list(agg.groupby(["store_key", "dept_key"]))
    sample = groups[:min(50, len(groups))]  # evaluate on first 50 series for speed

    for (store_key, dept_key), grp in sample:
        series = (
            grp.set_index("date_key")["weekly_sales"]
            .sort_index()
            .fillna(0)
        )
        if len(series) > 1:
            full_idx = pd.date_range(series.index.min(), series.index.max(), freq="7D")
            series   = series.reindex(full_idx, method="nearest", tolerance="3D").ffill().fillna(0)
        if len(series) < MIN_HISTORY_WEEKS + ROLLING_WINDOW:
            continue
        train  = series.iloc[:-ROLLING_WINDOW]
        actual = series.iloc[-ROLLING_WINDOW:]
        try:
            model = ExponentialSmoothing(
                train.clip(lower=0.01),
                trend="add", seasonal="mul",
                seasonal_periods=SEASONAL_PERIOD,
                damped_trend=True,
                initialization_method="estimated",
            )
            fit  = model.fit(optimized=True, remove_bias=True)
            pred = fit.forecast(ROLLING_WINDOW)
            mask = actual > 1
            if mask.sum() == 0:
                continue
            mape = np.mean(np.abs((actual[mask].values - pred[mask].values) / actual[mask].values)) * 100
            mapes.append(mape)
        except Exception:
            continue

    if mapes:
        console.print(
            f"  Median MAPE on {len(mapes)} evaluated series: "
            f"[bold green]{np.median(mapes):.1f}%[/bold green]"
        )
    else:
        console.print("  [dim]Not enough data to evaluate MAPE.[/dim]")


# ---------------------------------------------------------------------------
# Standalone execution
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    DB_PATH = Path(__file__).resolve().parent.parent / "sales_warehouse.db"
    if not DB_PATH.exists():
        console.print(f"[red]Database not found at {DB_PATH}. Run etl_pipeline.py first.[/red]")
        sys.exit(1)

    conn    = sqlite3.connect(DB_PATH)
    fact_df = pd.read_sql("SELECT store_key, dept_key, date_key, weekly_sales FROM fact_sales", conn)
    conn.close()

    forecast_df = run_forecast(fact_df)

    conn = sqlite3.connect(DB_PATH)
    forecast_df.to_sql("mart_forecast", conn, if_exists="append", index=False)
    conn.close()
    console.print(f"\n[bold green]✓ Forecast written to mart_forecast ({len(forecast_df)} rows)[/bold green]")
