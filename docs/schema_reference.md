# Database Schema Reference

Complete data dictionary for the `sales_warehouse.db` SQLite database.

---

## Star Schema Overview

```
        ┌─────────────┐
        │  dim_date   │
        │  (date_key) │
        └──────┬──────┘
               │
┌──────────┐   │   ┌────────────────┐
│ dim_store│───┼───│   fact_sales   │───┬───────────────┐
│(store_key)   │   │ (FK: all dims) │   │               │
└──────────┘   │   └────────────────┘   │               │
               │                   ┌───┴───────────┐    │
        ┌──────┴──────┐            │ dim_department│    │
        │dim_department            │  (dept_key)   │    │
        │ (dept_key)  │            └───────────────┘    │
        └─────────────┘                                  │
                                                ┌────────┴──────┐
                                                │ mart_forecast │
                                                └───────────────┘
```

---

## Tables

### `dim_date`
Date dimension. One row per distinct calendar week in the dataset.

| Column | Type | Description |
|--------|------|-------------|
| `date_key` | TEXT PK | ISO date `YYYY-MM-DD` (week-start date) |
| `week_num` | INTEGER | ISO week number (1–52 / 53) |
| `month_num` | INTEGER | Calendar month (1–12) |
| `month_name` | TEXT | Full month name (e.g., `January`) |
| `quarter_num` | INTEGER | Fiscal quarter (1–4) |
| `year_num` | INTEGER | Calendar year (e.g., `2022`) |
| `season` | TEXT | `Winter` \| `Spring` \| `Summer` \| `Fall` |
| `is_holiday` | INTEGER | `1` = holiday week, `0` = regular week |
| `holiday_name` | TEXT | Holiday name (e.g., `Thanksgiving`) or `None` |
| `is_year_start` | INTEGER | `1` if first week of January |
| `is_quarter_end` | INTEGER | `1` if week falls in March, June, Sep, or Dec |

---

### `dim_store`
Store dimension. One row per physical retail location.

| Column | Type | Description |
|--------|------|-------------|
| `store_key` | INTEGER PK | Store ID (1–50) |
| `store_type` | TEXT | `A` (large format) \| `B` (medium) \| `C` (small) |
| `store_size` | INTEGER | Floor area in square feet |
| `region` | TEXT | `North` \| `South` \| `East` \| `West` |
| `size_bucket` | TEXT | Derived: `Small` (<80k sqft) \| `Medium` \| `Large` (>150k) |

**Store Type Reference:**

| Type | Typical Size | Description |
|------|-------------|-------------|
| A | 150k–220k sqft | Supercenter format |
| B | 80k–150k sqft | Standard discount format |
| C | 30k–80k sqft | Small neighbourhood format |

---

### `dim_department`
Department dimension. One row per merchandise department.

| Column | Type | Description |
|--------|------|-------------|
| `dept_key` | INTEGER PK | Department number (1–20) |
| `dept_label` | TEXT | Human-readable label, e.g., `Dept 01` |

---

### `fact_sales`
Central fact table. **Grain: one row per store × department × week.**

| Column | Type | Description |
|--------|------|-------------|
| `fact_id` | INTEGER PK | Auto-increment surrogate key |
| `date_key` | TEXT FK | → `dim_date.date_key` |
| `store_key` | INTEGER FK | → `dim_store.store_key` |
| `dept_key` | INTEGER FK | → `dim_department.dept_key` |
| `weekly_sales` | REAL | Total sales revenue for the week ($) — negatives zeroed |
| `temperature` | REAL | Average temperature at store location (°F) |
| `fuel_price` | REAL | Regional fuel price ($/gallon) |
| `markdown_1` | REAL | Promotional markdown amount type 1 ($) — NULL → 0 |
| `markdown_2` | REAL | Promotional markdown amount type 2 ($) — NULL → 0 |
| `markdown_3` | REAL | Promotional markdown amount type 3 ($) — NULL → 0 |
| `markdown_4` | REAL | Promotional markdown amount type 4 ($) — NULL → 0 |
| `markdown_5` | REAL | Promotional markdown amount type 5 ($) — NULL → 0 |
| `cpi` | REAL | Consumer Price Index for the store's region |
| `unemployment` | REAL | Regional unemployment rate (%) |
| `is_holiday` | INTEGER | `1` = holiday week (denormalised from `dim_date`) |

> **Unique constraint:** `(date_key, store_key, dept_key)` — no duplicate grains.

---

### `mart_forecast`
Forecast mart. **Grain: one row per store × department × future week.**
Populated by `forecasting/forecaster.py` — not by SQL transforms.

| Column | Type | Description |
|--------|------|-------------|
| `forecast_id` | INTEGER PK | Auto-increment surrogate key |
| `store_key` | INTEGER FK | → `dim_store.store_key` |
| `dept_key` | INTEGER FK | → `dim_department.dept_key` |
| `forecast_date` | TEXT | ISO date of the predicted week (`YYYY-MM-DD`) |
| `predicted_sales` | REAL | Point forecast — expected weekly sales ($) |
| `lower_ci_95` | REAL | 95% prediction interval lower bound ($) |
| `upper_ci_95` | REAL | 95% prediction interval upper bound ($) |
| `stockout_risk_flag` | INTEGER | `1` = demand spike risk (predicted > 115% of rolling avg) |
| `model_type` | TEXT | `HoltWinters` or `NaiveSeasonal` (fallback) |
| `created_at` | TEXT | UTC timestamp when forecast was generated |

---

## Views

### `v_weekly_sales_summary`
**Use for:** Time-series charts, department heat-maps, markdown impact analysis.

Joins `fact_sales` with all three dimension tables. Adds a computed `total_markdown` column (sum of markdowns 1–5).

**Key columns added:**
- `total_markdown` — sum of all markdown types for the week

---

### `v_store_performance`
**Use for:** Executive KPI scorecards, YoY growth analysis, store leaderboards.

Aggregated to store × year grain. Uses window functions to compute:

| Computed Column | Description |
|----------------|-------------|
| `annual_revenue` | Total yearly sales per store |
| `avg_weekly_sales` | Mean weekly sales across all depts |
| `holiday_revenue` | Revenue in holiday weeks only |
| `holiday_lift_pct` | % uplift in holiday vs non-holiday average |
| `total_markdown_spend` | Sum of all markdown types for the year |
| `yoy_growth_pct` | Year-over-year revenue growth % |
| `revenue_rank` | Store rank by revenue within each year (1 = top) |

---

### `v_forecast_export`
**Use for:** Forecast vs actual overlay charts, stockout alert tables, inventory planning grids.

Joins `mart_forecast` with store and department dims. Adds:

| Computed Column | Description |
|----------------|-------------|
| `ci_width` | Prediction interval width (proxy for uncertainty) |
| `stockout_risk_label` | `HIGH RISK` or `Normal` (human-readable flag) |
| `last_actual_sales` | Most recent actual weekly sales for context |

---

## Indexes

| Index | Table | Column(s) | Purpose |
|-------|-------|-----------|---------|
| `idx_fact_sales_date` | `fact_sales` | `date_key` | Time-series filtering |
| `idx_fact_sales_store` | `fact_sales` | `store_key` | Store drill-down |
| `idx_fact_sales_dept` | `fact_sales` | `dept_key` | Dept filtering |
| `idx_fact_sales_holiday` | `fact_sales` | `is_holiday` | Holiday segmentation |
| `idx_forecast_store` | `mart_forecast` | `store_key` | Forecast by store |
| `idx_forecast_dept` | `mart_forecast` | `dept_key` | Forecast by dept |
| `idx_forecast_date` | `mart_forecast` | `forecast_date` | Forecast timeline |
| `idx_forecast_risk` | `mart_forecast` | `stockout_risk_flag` | Alert filtering |
