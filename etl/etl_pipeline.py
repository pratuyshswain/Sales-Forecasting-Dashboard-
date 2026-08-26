# -*- coding: utf-8 -*-
"""
etl_pipeline.py
===============
End-to-end ETL orchestrator for the Sales Forecasting Dashboard.

Pipeline phases:
  1. LOAD        — Read raw CSVs into pandas DataFrames
  2. PROFILE     — Print data quality report
  3. CLEAN       — Handle nulls, negatives, duplicates, type coercion
  4. DB SETUP    — Apply star-schema DDL (01 + 02 SQL scripts)
  5. STAGE       — Write cleaned data into staging tables
  6. TRANSFORM   — Execute 03_transform_load.sql to populate dims + fact
  7. FORECAST    — Call forecaster.py, write mart_forecast
  8. BI VIEWS    — Execute 04_bi_views.sql to create analytics views
  9. EXPORT      — Export all BI-ready CSVs to exports/
 10. REPORT      — Print final validation summary

Usage:
    cd "Sales Forecasting Dashboard"
    pip install -r etl/requirements.txt
    python etl/etl_pipeline.py
"""

import sqlite3
import sys
import time
from pathlib import Path

import io
import numpy as np
import pandas as pd
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeElapsedColumn
from rich.table import Table
from rich import box

# ---------------------------------------------------------------------------
# Paths (all relative to project root)
# ---------------------------------------------------------------------------
ROOT       = Path(__file__).resolve().parent.parent
SQL_DIR    = ROOT / "sql"
EXPORTS    = ROOT / "exports"
DB_PATH    = ROOT / "sales_warehouse.db"

RAW_SALES    = ROOT / "sales.csv"
RAW_FEATURES = ROOT / "features.csv"
RAW_STORES   = ROOT / "stores.csv"

EXPORTS.mkdir(exist_ok=True)

# Force UTF-8 output so Rich spinner/box characters work on Windows terminals
import sys
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

console = Console(force_terminal=True, highlight=False)

# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------
def _exec_sql_file(conn: sqlite3.Connection, path: Path):
    """Execute a multi-statement SQL file against the given connection."""
    sql = path.read_text(encoding="utf-8")
    conn.executescript(sql)
    conn.commit()


def _step(msg: str):
    console.rule(f"[bold cyan]{msg}")


# =============================================================================
# PHASE 1 — LOAD
# =============================================================================
def load_raw() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    _step("Phase 1 - Loading Raw CSVs")
    console.print("  Reading sales.csv...")
    sales    = pd.read_csv(RAW_SALES,    dtype=str)
    console.print(f"  [green]OK sales.csv    -- {len(sales):,} rows[/green]")

    console.print("  Reading features.csv...")
    features = pd.read_csv(RAW_FEATURES, dtype=str)
    console.print(f"  [green]OK features.csv -- {len(features):,} rows[/green]")

    console.print("  Reading stores.csv...")
    stores   = pd.read_csv(RAW_STORES,   dtype=str)
    console.print(f"  [green]OK stores.csv   -- {len(stores):,} rows[/green]")

    console.print()
    return sales, features, stores


# =============================================================================
# PHASE 2 — PROFILE
# =============================================================================
def profile(sales: pd.DataFrame, features: pd.DataFrame, stores: pd.DataFrame):
    _step("Phase 2 · Data Quality Profile")

    t = Table(title="Raw Data Overview", box=box.SIMPLE_HEAVY, show_lines=True)
    t.add_column("Source",  style="cyan", no_wrap=True)
    t.add_column("Rows",    style="white", justify="right")
    t.add_column("Columns", style="white", justify="right")
    t.add_column("Null %",  style="yellow", justify="right")
    t.add_column("Notes",   style="dim")

    def null_pct(df):
        return f"{100 * df.isnull().sum().sum() / (df.shape[0] * df.shape[1]):.1f}%"

    t.add_row("sales.csv",    f"{len(sales):,}",    str(sales.shape[1]),
              null_pct(sales),    "Core transaction data")
    t.add_row("features.csv", f"{len(features):,}", str(features.shape[1]),
              null_pct(features), "Markdowns have many NULLs (expected)")
    t.add_row("stores.csv",   f"{len(stores):,}",   str(stores.shape[1]),
              null_pct(stores),   "Complete — no nulls expected")
    console.print(t)

    # Date range
    dates = pd.to_datetime(sales["date"], errors="coerce").dropna()
    console.print(f"  Date range : [bold]{dates.min().date()}[/bold] → [bold]{dates.max().date()}[/bold]")
    console.print(f"  Unique stores     : {sales['store_id'].nunique()}")
    console.print(f"  Unique departments: {sales['department'].nunique()}")
    neg = (pd.to_numeric(sales["weekly_sales"], errors="coerce") < 0).sum()
    console.print(f"  Negative sales rows to clean: [bold red]{neg:,}[/bold red]\n")


# =============================================================================
# PHASE 3 — CLEAN
# =============================================================================
def clean(
    sales: pd.DataFrame,
    features: pd.DataFrame,
    stores: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    _step("Phase 3 · Cleaning & Transforming")

    # --- Sales ---
    sales = sales.copy()
    sales.columns = [c.strip().lower() for c in sales.columns]
    sales["weekly_sales"] = pd.to_numeric(sales["weekly_sales"], errors="coerce")
    sales["is_holiday"]   = pd.to_numeric(sales["is_holiday"],   errors="coerce").fillna(0).astype(int)
    sales["store_id"]     = pd.to_numeric(sales["store_id"],     errors="coerce")
    sales["department"]   = pd.to_numeric(sales["department"],   errors="coerce")
    sales["date"]         = pd.to_datetime(sales["date"],         errors="coerce")

    before = len(sales)
    sales.dropna(subset=["store_id", "department", "date", "weekly_sales"], inplace=True)
    sales.drop_duplicates(subset=["store_id", "department", "date"],        inplace=True)

    # Cap negatives at 0 (returns / refunds)
    neg_mask = sales["weekly_sales"] < 0
    sales.loc[neg_mask, "weekly_sales"] = 0.0

    console.print(
        f"  sales: {before:,} → {len(sales):,} rows after dedup/null drop  "
        f"({before - len(sales):,} removed, {neg_mask.sum()} negatives zeroed)"
    )

    # --- Features ---
    features = features.copy()
    features.columns = [c.strip().lower() for c in features.columns]
    features["store_id"] = pd.to_numeric(features["store_id"], errors="coerce")
    features["date"]     = pd.to_datetime(features["date"],    errors="coerce")
    features.dropna(subset=["store_id", "date"], inplace=True)
    features.drop_duplicates(subset=["store_id", "date"], inplace=True)

    numeric_feats = ["temperature", "fuel_price",
                     "markdown_1", "markdown_2", "markdown_3", "markdown_4", "markdown_5",
                     "cpi", "unemployment"]
    for col in numeric_feats:
        if col in features.columns:
            features[col] = pd.to_numeric(features[col], errors="coerce")

    # Markdowns: NULL → 0 (no active markdown = no discount)
    for md in ["markdown_1","markdown_2","markdown_3","markdown_4","markdown_5"]:
        if md in features.columns:
            features[md] = features[md].fillna(0.0)

    # CPI / unemployment: forward-fill within each store (macro series rarely missing)
    features.sort_values(["store_id", "date"], inplace=True)
    features["cpi"]          = features.groupby("store_id")["cpi"].transform(lambda s: s.ffill().bfill())
    features["unemployment"] = features.groupby("store_id")["unemployment"].transform(lambda s: s.ffill().bfill())

    console.print(f"  features: {len(features):,} rows cleaned")

    # --- Stores ---
    stores = stores.copy()
    stores.columns = [c.strip().lower() for c in stores.columns]
    stores["store_id"]   = pd.to_numeric(stores["store_id"],   errors="coerce")
    stores["store_size"] = pd.to_numeric(stores["store_size"], errors="coerce")
    stores["store_type"] = stores["store_type"].str.strip().str.upper()
    stores["region"]     = stores["region"].str.strip().str.title()
    stores.dropna(subset=["store_id"], inplace=True)
    console.print(f"  stores:   {len(stores):,} rows cleaned\n")

    return sales, features, stores


# =============================================================================
# PHASE 4 + 5 — DATABASE SETUP & STAGING
# =============================================================================
def setup_and_stage(
    conn: sqlite3.Connection,
    sales: pd.DataFrame,
    features: pd.DataFrame,
    stores: pd.DataFrame,
):
    _step("Phase 4 · Database Setup (Star Schema DDL)")
    _exec_sql_file(conn, SQL_DIR / "01_schema_create.sql")
    console.print("  [green]✓ Star schema created (dims + fact + mart)[/green]")

    _exec_sql_file(conn, SQL_DIR / "02_raw_ingest.sql")
    console.print("  [green]✓ Staging tables created[/green]\n")

    _step("Phase 5 · Loading Staging Tables")

    # Convert dates back to strings for SQL storage
    sales_stg    = sales.copy()
    features_stg = features.copy()
    sales_stg["date"]    = sales_stg["date"].dt.strftime("%Y-%m-%d")
    features_stg["date"] = features_stg["date"].dt.strftime("%Y-%m-%d")

    console.print("  Loading stg_sales...")
    sales_stg.to_sql("stg_sales", conn, if_exists="append", index=False,
                      dtype={c: "TEXT" for c in sales_stg.columns})
    console.print(f"  [green]OK stg_sales    -- {len(sales_stg):,} rows[/green]")

    console.print("  Loading stg_features...")
    features_stg.to_sql("stg_features", conn, if_exists="append", index=False,
                          dtype={c: "TEXT" for c in features_stg.columns})
    console.print(f"  [green]OK stg_features -- {len(features_stg):,} rows[/green]")

    console.print("  Loading stg_stores...")
    stores.to_sql("stg_stores", conn, if_exists="append", index=False,
                   dtype={c: "TEXT" for c in stores.columns})
    console.print(f"  [green]OK stg_stores   -- {len(stores):,} rows[/green]")

    console.print()


# =============================================================================
# PHASE 6 — TRANSFORM & LOAD (pandas-native, fast)
# Replaces the SQL correlated-subquery approach which is O(n²) on 156k rows.
# All dedup/join/cast logic runs in pandas then is written to SQLite in bulk.
# =============================================================================
def transform_load(conn: sqlite3.Connection):
    _step("Phase 6 · Transform & Load (Star Schema — pandas-native)")

    # Read staging tables back into pandas (already cleaned in Phase 3)
    console.print("  Reading staging tables...")
    sales_df    = pd.read_sql("SELECT * FROM stg_sales",    conn)
    features_df = pd.read_sql("SELECT * FROM stg_features", conn)
    stores_df   = pd.read_sql("SELECT * FROM stg_stores",   conn)

    # Cast types on sales
    sales_df["store_id"]     = pd.to_numeric(sales_df["store_id"],     errors="coerce")
    sales_df["department"]   = pd.to_numeric(sales_df["department"],   errors="coerce")
    sales_df["weekly_sales"] = pd.to_numeric(sales_df["weekly_sales"], errors="coerce")
    sales_df["is_holiday"]   = pd.to_numeric(sales_df["is_holiday"],   errors="coerce").fillna(0).astype(int)
    sales_df["date"]         = pd.to_datetime(sales_df["date"], errors="coerce")
    sales_df.dropna(subset=["store_id","department","date","weekly_sales"], inplace=True)
    sales_df["weekly_sales"] = sales_df["weekly_sales"].clip(lower=0)
    sales_df.drop_duplicates(subset=["store_id","department","date"], keep="first", inplace=True)

    # Cast types on features
    features_df["store_id"] = pd.to_numeric(features_df["store_id"], errors="coerce")
    features_df["date"]     = pd.to_datetime(features_df["date"],    errors="coerce")
    for col in ["temperature","fuel_price","markdown_1","markdown_2","markdown_3",
                "markdown_4","markdown_5","cpi","unemployment"]:
        if col in features_df.columns:
            features_df[col] = pd.to_numeric(features_df[col], errors="coerce")
    for md in ["markdown_1","markdown_2","markdown_3","markdown_4","markdown_5"]:
        features_df[md] = features_df[md].fillna(0.0)
    features_df.drop_duplicates(subset=["store_id","date"], keep="first", inplace=True)

    # Cast types on stores
    stores_df["store_id"]   = pd.to_numeric(stores_df["store_id"],   errors="coerce")
    stores_df["store_size"] = pd.to_numeric(stores_df["store_size"], errors="coerce")

    # --- dim_date ---
    console.print("  Building dim_date...")
    date_src = features_df[["date","is_holiday","holiday_name","season"]].copy()
    date_src["is_holiday"] = pd.to_numeric(date_src["is_holiday"], errors="coerce").fillna(0).astype(int)
    date_src.drop_duplicates(subset=["date"], inplace=True)
    date_src.dropna(subset=["date"], inplace=True)
    week_num = date_src["date"].dt.isocalendar().week.astype(int)
    dim_date = pd.DataFrame({
        "date_key":      date_src["date"].dt.strftime("%Y-%m-%d"),
        "week_num":      week_num,
        "month_num":     date_src["date"].dt.month,
        "month_name":    date_src["date"].dt.strftime("%B"),
        "quarter_num":   date_src["date"].dt.quarter,
        "year_num":      date_src["date"].dt.year,
        "season":        date_src["season"].fillna("Unknown"),
        "is_holiday":    date_src["is_holiday"],
        "holiday_name":  date_src["holiday_name"].fillna("None"),
        "is_year_start": ((date_src["date"].dt.month == 1) & (week_num <= 1)).astype(int),
        "is_quarter_end":date_src["date"].dt.month.isin([3,6,9,12]).astype(int),
    })
    dim_date.to_sql("dim_date", conn, if_exists="append", index=False)

    # --- dim_store ---
    console.print("  Building dim_store...")
    dim_store = pd.DataFrame({
        "store_key":  stores_df["store_id"].astype(int),
        "store_type": stores_df["store_type"].str.strip().str.upper(),
        "store_size": stores_df["store_size"].astype(int),
        "region":     stores_df["region"].str.strip(),
        "size_bucket": pd.cut(
            stores_df["store_size"],
            bins=[0, 80000, 150000, float("inf")],
            labels=["Small","Medium","Large"]
        ).astype(str),
    })
    dim_store.to_sql("dim_store", conn, if_exists="append", index=False)

    # --- dim_department ---
    console.print("  Building dim_department...")
    depts = sorted(sales_df["department"].dropna().unique())
    dim_dept = pd.DataFrame({
        "dept_key":  [int(d) for d in depts],
        "dept_label":[f"Dept {int(d):02d}" for d in depts],
    })
    dim_dept.to_sql("dim_department", conn, if_exists="append", index=False)

    # --- fact_sales (pandas merge — fast O(n log n) instead of SQL O(n²)) ---
    console.print("  Building fact_sales (merge + bulk insert)...")
    sales_df["date_key"]    = sales_df["date"].dt.strftime("%Y-%m-%d")
    features_df["date_key"] = features_df["date"].dt.strftime("%Y-%m-%d")

    fact = sales_df.merge(
        features_df[["store_id","date_key","temperature","fuel_price",
                      "markdown_1","markdown_2","markdown_3","markdown_4","markdown_5",
                      "cpi","unemployment"]],
        on=["store_id","date_key"], how="left",
    )
    fact_sales = pd.DataFrame({
        "date_key":    fact["date_key"],
        "store_key":   fact["store_id"].astype(int),
        "dept_key":    fact["department"].astype(int),
        "weekly_sales":fact["weekly_sales"].round(2),
        "temperature": fact["temperature"],
        "fuel_price":  fact["fuel_price"],
        "markdown_1":  fact["markdown_1"].fillna(0.0),
        "markdown_2":  fact["markdown_2"].fillna(0.0),
        "markdown_3":  fact["markdown_3"].fillna(0.0),
        "markdown_4":  fact["markdown_4"].fillna(0.0),
        "markdown_5":  fact["markdown_5"].fillna(0.0),
        "cpi":         fact["cpi"],
        "unemployment":fact["unemployment"],
        "is_holiday":  fact["is_holiday"],
    })
    fact_sales.to_sql("fact_sales", conn, if_exists="append", index=False)
    conn.commit()

    # Summary counts
    rows_q = """
        SELECT 'dim_date'       AS tbl, COUNT(*) AS rows FROM dim_date
        UNION ALL SELECT 'dim_store',         COUNT(*) FROM dim_store
        UNION ALL SELECT 'dim_department',    COUNT(*) FROM dim_department
        UNION ALL SELECT 'fact_sales',        COUNT(*) FROM fact_sales
    """
    result = pd.read_sql(rows_q, conn)
    t = Table(title="Star Schema Row Counts", box=box.SIMPLE_HEAVY)
    t.add_column("Table", style="cyan")
    t.add_column("Rows",  style="white", justify="right")
    for _, row in result.iterrows():
        t.add_row(row["tbl"], f"{int(row['rows']):,}")
    console.print(t)
    console.print()


# =============================================================================
# PHASE 7 — FORECAST
# =============================================================================
def run_forecasting(conn: sqlite3.Connection):
    _step("Phase 7 · Running Holt-Winters Forecasting Model")

    # Import from sibling forecasting/ package
    sys.path.insert(0, str(ROOT))
    from forecasting.forecaster import run_forecast

    fact_df     = pd.read_sql(
        "SELECT store_key, dept_key, date_key, weekly_sales FROM fact_sales", conn
    )
    forecast_df = run_forecast(fact_df)

    forecast_df.to_sql("mart_forecast", conn, if_exists="append", index=False)
    conn.commit()

    stockout_n = (forecast_df["stockout_risk_flag"] == 1).sum()
    console.print(
        f"\n  [green]✓ mart_forecast written — {len(forecast_df):,} rows, "
        f"[bold red]{stockout_n}[/bold red] stockout-risk weeks[/green]\n"
    )
    return forecast_df


# =============================================================================
# PHASE 8 — BI VIEWS
# =============================================================================
def create_bi_views(conn: sqlite3.Connection):
    _step("Phase 8 · Creating BI Analytics Views")
    _exec_sql_file(conn, SQL_DIR / "04_bi_views.sql")
    console.print("  [green]✓ v_weekly_sales_summary created[/green]")
    console.print("  [green]✓ v_store_performance created[/green]")
    console.print("  [green]✓ v_forecast_export created[/green]\n")


# =============================================================================
# PHASE 9 — EXPORT CSVs
# =============================================================================
def export_csvs(conn: sqlite3.Connection):
    _step("Phase 9 · Exporting BI-Ready CSVs")

    exports = {
        "weekly_sales_summary.csv": "SELECT * FROM v_weekly_sales_summary",
        "store_performance_kpis.csv": "SELECT * FROM v_store_performance",
        "forecast_12w.csv": "SELECT * FROM v_forecast_export",
        "dim_date.csv": "SELECT * FROM dim_date",
        "dim_store.csv": "SELECT * FROM dim_store",
        "dim_department.csv": "SELECT * FROM dim_department",
    }

    t = Table(title="Export Files", box=box.SIMPLE_HEAVY)
    t.add_column("File",  style="cyan")
    t.add_column("Rows",  style="white", justify="right")
    t.add_column("Path",  style="dim")

    for fname, query in exports.items():
        df   = pd.read_sql(query, conn)
        path = EXPORTS / fname
        df.to_csv(path, index=False)
        t.add_row(fname, f"{len(df):,}", str(path))

    console.print(t)
    console.print()


# =============================================================================
# PHASE 10 — FINAL REPORT
# =============================================================================
def final_report(conn: sqlite3.Connection, start_time: float):
    _step("Phase 10 · Final Validation Report")

    elapsed = time.time() - start_time
    q = """
        SELECT 'dim_date'       AS t, COUNT(*) AS n FROM dim_date
        UNION ALL SELECT 'dim_store',         COUNT(*) FROM dim_store
        UNION ALL SELECT 'dim_department',    COUNT(*) FROM dim_department
        UNION ALL SELECT 'fact_sales',        COUNT(*) FROM fact_sales
        UNION ALL SELECT 'mart_forecast',     COUNT(*) FROM mart_forecast
    """
    df = pd.read_sql(q, conn)

    t = Table(title="Final Database State", box=box.DOUBLE_EDGE)
    t.add_column("Table",     style="bold cyan")
    t.add_column("Row Count", style="bold white", justify="right")
    for _, row in df.iterrows():
        t.add_row(row["t"], f"{row['n']:,}")
    console.print(t)

    export_files = list(EXPORTS.glob("*.csv"))
    console.print(f"\n  Exports  : {len(export_files)} CSV files in [bold]{EXPORTS}[/bold]")
    console.print(f"  Database : [bold]{DB_PATH}[/bold]")
    console.print(f"  Duration : [bold]{elapsed:.1f}s[/bold]")
    console.print(
        Panel(
            "[bold green]✓ ETL pipeline completed successfully![/bold green]\n\n"
            "Next steps:\n"
            "  1. Open [cyan]sales_warehouse.db[/cyan] in DB Browser for SQLite\n"
            "  2. Import [cyan]exports/[/cyan] folder into Tableau or Power BI\n"
            "  3. Read [cyan]docs/bi_connection_guide.md[/cyan] for setup instructions",
            title="Pipeline Complete",
            border_style="green",
        )
    )


# =============================================================================
# MAIN
# =============================================================================
def main():
    console.print(
        Panel(
            "[bold white]Sales Forecasting Dashboard[/bold white]\n"
            "[dim]End-to-End ETL Pipeline · Holt-Winters Forecasting[/dim]",
            title="[bold cyan]Starting Pipeline[/bold cyan]",
            border_style="cyan",
        )
    )
    start = time.time()

    # Validate input files exist
    for f in [RAW_SALES, RAW_FEATURES, RAW_STORES]:
        if not f.exists():
            console.print(f"[red]ERROR: Missing input file: {f}[/red]")
            sys.exit(1)

    # Remove old DB for a clean run
    if DB_PATH.exists():
        DB_PATH.unlink()
        console.print(f"  [dim]Removed old database: {DB_PATH}[/dim]\n")

    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")

    try:
        sales, features, stores = load_raw()
        profile(sales, features, stores)
        sales, features, stores = clean(sales, features, stores)
        setup_and_stage(conn, sales, features, stores)
        transform_load(conn)
        run_forecasting(conn)
        create_bi_views(conn)
        export_csvs(conn)
        final_report(conn, start)
    except Exception as exc:
        console.print_exception()
        console.print(f"[bold red]Pipeline failed: {exc}[/bold red]")
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
