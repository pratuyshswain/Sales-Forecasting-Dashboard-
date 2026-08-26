# BI Connection Guide
## Connecting Tableau & Power BI to the Sales Forecasting Dashboard

This guide covers two connection methods:
1. **CSV Folder** — simplest, works immediately after running the pipeline
2. **SQLite Database** — live connection to the data warehouse (requires a driver)

---

## Prerequisites

Run the ETL pipeline first:
```bash
cd "Sales Forecasting Dashboard"
pip install -r etl/requirements.txt
python etl/etl_pipeline.py
```

This produces:
- `sales_warehouse.db` — SQLite database with star schema + views
- `exports/` — 6 ready-to-import CSV files

---

## Method 1 — CSV Import (Recommended for Quick Start)

### Tableau Desktop

1. Open Tableau Desktop → **Connect** → **Text File**
2. Navigate to your `exports/` folder
3. Select `weekly_sales_summary.csv` → **Open**
4. In the Data Source pane, drag in additional CSVs:
   - Drag `dim_date.csv` → join on `week_date = date_key`
   - Drag `dim_store.csv` → join on `store_id = store_key`
   - Drag `dim_department.csv` → join on `department = dept_key`
5. For forecasts: add a second data source using `forecast_12w.csv`
6. Click **Sheet 1** to start building

**Recommended sheets:**
| Sheet | Data Source | Viz Type |
|-------|------------|---------|
| Weekly Revenue Trend | `weekly_sales_summary.csv` | Line chart (week_date × weekly_sales) |
| Store KPI Scorecard | `store_performance_kpis.csv` | KPI tiles (annual_revenue, yoy_growth_pct) |
| Department Heat-Map | `weekly_sales_summary.csv` | Heat map (dept_label × month_name) |
| Forecast vs Actuals | `forecast_12w.csv` | Dual-axis line (predicted + CI band) |
| Stockout Alert Table | `forecast_12w.csv` | Highlight table (filter: stockout_risk_flag = 1) |

---

### Power BI Desktop

1. Open Power BI Desktop → **Get Data** → **Text/CSV**
2. Select `exports/weekly_sales_summary.csv` → **Load**
3. Repeat for all CSVs:
   - `store_performance_kpis.csv`
   - `forecast_12w.csv`
   - `dim_date.csv`
   - `dim_store.csv`
   - `dim_department.csv`
4. Go to **Model** view → draw relationships:
   - `weekly_sales_summary[week_date]` → `dim_date[date_key]` (Many-to-One)
   - `weekly_sales_summary[store_id]` → `dim_store[store_key]` (Many-to-One)
   - `weekly_sales_summary[department]` → `dim_department[dept_key]` (Many-to-One)
   - `forecast_12w[store_id]` → `dim_store[store_key]` (Many-to-One)
   - `forecast_12w[department]` → `dim_department[dept_key]` (Many-to-One)
5. Go to **Report** view → start adding visuals

**Useful DAX measures:**
```dax
-- Total Revenue
Total Revenue = SUM(weekly_sales_summary[weekly_sales])

-- Holiday Lift %
Holiday Lift % = 
    DIVIDE(
        CALCULATE([Total Revenue], weekly_sales_summary[is_holiday] = 1),
        CALCULATE([Total Revenue], weekly_sales_summary[is_holiday] = 0)
    ) - 1

-- Stockout Risk Count
Stockout Risk Count = 
    CALCULATE(COUNTROWS(forecast_12w), forecast_12w[stockout_risk_flag] = 1)

-- YoY Growth %
YoY Revenue Growth % = 
    DIVIDE(
        [Total Revenue] - CALCULATE([Total Revenue], SAMEPERIODLASTYEAR(dim_date[date_key])),
        CALCULATE([Total Revenue], SAMEPERIODLASTYEAR(dim_date[date_key]))
    )
```

---

## Method 2 — Live SQLite Connection

### Install the SQLite ODBC Driver

1. Download from: https://www.ch-werner.de/sqliteodbc/
   - Windows 64-bit: `sqliteodbc_w64.exe`
2. Run the installer (no admin required on most systems)
3. Verify: Open **ODBC Data Source Administrator (64-bit)** → **Drivers** tab → look for `SQLite3 ODBC Driver`

### Tableau Desktop (SQLite via ODBC)

1. **Connect** → **Other Databases (ODBC)**
2. DSN dropdown → select `SQLite3 Datasource` (or configure new)
3. In the connection dialog, set the database path:
   ```
   C:\Users\pratu\OneDrive\Desktop\Sales Forecasting Dashboard\sales_warehouse.db
   ```
4. Click **Sign In**
5. In the left panel, select **Schema: main**
6. Drag views from the table list:
   - `v_weekly_sales_summary`
   - `v_store_performance`
   - `v_forecast_export`
7. Click **Sheet 1** to build dashboards

> **Tip:** Use `v_forecast_export` sorted by `stockout_risk_flag DESC` to surface the highest-risk items first.

---

### Power BI Desktop (SQLite via ODBC)

1. **Get Data** → **ODBC**
2. DSN: select `SQLite3 Datasource` from the dropdown
3. In **Advanced options**, paste the connection string:
   ```
   Database=C:\Users\pratu\OneDrive\Desktop\Sales Forecasting Dashboard\sales_warehouse.db
   ```
4. Click **OK** → **Connect**
5. In the Navigator, expand `main` schema
6. Select views:
   - ✅ `v_weekly_sales_summary`
   - ✅ `v_store_performance`
   - ✅ `v_forecast_export`
   - ✅ `dim_date`, `dim_store`, `dim_department`
7. Click **Load** (or **Transform Data** for Power Query edits)
8. Build relationships in **Model** view (same as Method 1)

---

## Recommended Dashboard Pages

### Page 1 — Executive Summary
| Visual | Fields |
|--------|--------|
| KPI Card — Total Revenue | `annual_revenue` (current year) |
| KPI Card — YoY Growth | `yoy_growth_pct` |
| KPI Card — Stockout Alerts | `stockout_risk_flag` count |
| Bar chart — Top 10 Stores | `store_id` × `annual_revenue` |
| Line chart — Revenue Trend | `week_date` × `weekly_sales` |

### Page 2 — Sales Deep Dive
| Visual | Fields |
|--------|--------|
| Heat map — Dept × Month | `dept_label` × `month_name` × `weekly_sales` |
| Scatter — Size vs Revenue | `store_size` × `annual_revenue` (colour by `region`) |
| Bar — Holiday Lift by Store | `store_id` × `holiday_lift_pct` |
| Filter panel | `region`, `store_type`, `season` |

### Page 3 — Forecast & Inventory
| Visual | Fields |
|--------|--------|
| Line — Forecast vs Actuals | `forecast_week` × `predicted_sales` + `last_actual_sales` |
| Ribbon — Confidence band | `lower_ci_95` / `upper_ci_95` |
| Alert table — Stockout risk | Filter `stockout_risk_flag = 1`, sorted by `predicted_sales` DESC |
| Slicer | `store_id`, `department`, `forecast_week` |

---

## Refreshing the Data

After re-running the ETL pipeline:

### CSV connections
- Tableau: **Data** → **Refresh All Extracts**
- Power BI: **Home** → **Refresh**

### Live SQLite connection
- Data updates automatically on next query (no refresh needed)
- To fully rebuild: delete `sales_warehouse.db` and re-run `python etl/etl_pipeline.py`

---

## Troubleshooting

| Issue | Fix |
|-------|-----|
| ODBC driver not found | Re-install `sqliteodbc_w64.exe`, restart Tableau/Power BI |
| CSV loads with wrong types | In Tableau: right-click column header → Change Data Type |
| Relationships not auto-detected | Set manually in Model view (Power BI) or Join dialog (Tableau) |
| `forecast_12w.csv` is empty | Re-run `python etl/etl_pipeline.py` — forecasting phase may have failed |
| SQLite locked error | Close DB Browser for SQLite before connecting from BI tool |
