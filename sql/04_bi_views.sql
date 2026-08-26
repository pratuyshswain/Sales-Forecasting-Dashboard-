-- =============================================================================
-- FILE: 04_bi_views.sql
-- PURPOSE: Create analytics views as the final data layer for BI tools.
--          These views are the ONLY layer Tableau / Power BI should query.
--          All heavy joins and aggregations are pre-computed here.
-- VIEWS:
--   v_weekly_sales_summary  — granular fact view for drill-down
--   v_store_performance     — store-level KPIs (Scorecards / KPI tiles)
--   v_forecast_export       — 12-week forward predictions + stockout alerts
-- =============================================================================

DROP VIEW IF EXISTS v_weekly_sales_summary;
DROP VIEW IF EXISTS v_store_performance;
DROP VIEW IF EXISTS v_forecast_export;

-- =============================================================================
-- VIEW 1: v_weekly_sales_summary
-- Grain  : store × department × week
-- Use for: Time-series charts, department heat-maps, markdown impact analysis
-- =============================================================================
CREATE VIEW v_weekly_sales_summary AS
SELECT
    -- Date dimension
    d.date_key                          AS week_date,
    d.week_num,
    d.month_num,
    d.month_name,
    d.quarter_num,
    d.year_num,
    d.season,
    d.is_holiday,
    d.holiday_name,

    -- Store dimension
    st.store_key                        AS store_id,
    st.store_type,
    st.store_size,
    st.region,
    st.size_bucket,

    -- Department dimension
    dp.dept_key                         AS department,
    dp.dept_label,

    -- Sales facts
    f.weekly_sales,
    f.temperature,
    f.fuel_price,
    f.markdown_1,
    f.markdown_2,
    f.markdown_3,
    f.markdown_4,
    f.markdown_5,
    -- Total markdown spend this week
    (f.markdown_1 + f.markdown_2 + f.markdown_3 +
     f.markdown_4 + f.markdown_5)       AS total_markdown,
    f.cpi,
    f.unemployment

FROM fact_sales      f
JOIN dim_date        d  ON f.date_key  = d.date_key
JOIN dim_store       st ON f.store_key = st.store_key
JOIN dim_department  dp ON f.dept_key  = dp.dept_key;


-- =============================================================================
-- VIEW 2: v_store_performance
-- Grain  : store × year
-- Use for: Executive KPI tiles, ranked leaderboards, YoY growth cards
-- =============================================================================
CREATE VIEW v_store_performance AS
WITH yearly AS (
    SELECT
        f.store_key,
        d.year_num,
        SUM(f.weekly_sales)                                 AS annual_revenue,
        AVG(f.weekly_sales)                                 AS avg_weekly_sales,
        COUNT(DISTINCT d.date_key)                          AS weeks_active,
        SUM(CASE WHEN f.is_holiday = 1 THEN f.weekly_sales ELSE 0 END)
                                                            AS holiday_revenue,
        SUM(CASE WHEN f.is_holiday = 0 THEN f.weekly_sales ELSE 0 END)
                                                            AS non_holiday_revenue,
        SUM(f.markdown_1 + f.markdown_2 + f.markdown_3 +
            f.markdown_4 + f.markdown_5)                    AS total_markdown_spend
    FROM fact_sales f
    JOIN dim_date d ON f.date_key = d.date_key
    GROUP BY f.store_key, d.year_num
),
with_growth AS (
    SELECT
        y.*,
        LAG(y.annual_revenue) OVER (
            PARTITION BY y.store_key ORDER BY y.year_num
        )                                                   AS prev_year_revenue
    FROM yearly y
)
SELECT
    wg.store_key                        AS store_id,
    st.store_type,
    st.region,
    st.size_bucket,
    st.store_size,
    wg.year_num,
    ROUND(wg.annual_revenue, 2)         AS annual_revenue,
    ROUND(wg.avg_weekly_sales, 2)       AS avg_weekly_sales,
    wg.weeks_active,
    ROUND(wg.holiday_revenue, 2)        AS holiday_revenue,
    ROUND(wg.non_holiday_revenue, 2)    AS non_holiday_revenue,
    -- Holiday lift = % uplift during holiday weeks vs non-holiday average
    CASE
        WHEN wg.non_holiday_revenue > 0 THEN
            ROUND(
                100.0 * (wg.holiday_revenue / wg.weeks_active -
                         wg.non_holiday_revenue / wg.weeks_active)
                / (wg.non_holiday_revenue / wg.weeks_active),
            2)
        ELSE NULL
    END                                 AS holiday_lift_pct,
    ROUND(wg.total_markdown_spend, 2)   AS total_markdown_spend,
    -- YoY revenue growth %
    CASE
        WHEN wg.prev_year_revenue IS NOT NULL AND wg.prev_year_revenue > 0 THEN
            ROUND(100.0 * (wg.annual_revenue - wg.prev_year_revenue)
                  / wg.prev_year_revenue, 2)
        ELSE NULL
    END                                 AS yoy_growth_pct,
    -- Revenue rank within year (1 = top store)
    RANK() OVER (
        PARTITION BY wg.year_num ORDER BY wg.annual_revenue DESC
    )                                   AS revenue_rank
FROM with_growth wg
JOIN dim_store st ON wg.store_key = st.store_key;


-- =============================================================================
-- VIEW 3: v_forecast_export
-- Grain  : store × department × forecast week (12 weeks forward)
-- Use for: Forecast vs actual overlays, stockout alert tables, inventory planning
-- =============================================================================
CREATE VIEW v_forecast_export AS
WITH latest_date AS (
    SELECT MAX(date_key) AS max_date FROM fact_sales
),
last_actuals AS (
    SELECT
        store_key,
        dept_key,
        ROUND(SUM(weekly_sales), 2) AS last_actual_sales
    FROM fact_sales
    WHERE date_key = (SELECT max_date FROM latest_date)
    GROUP BY store_key, dept_key
)
SELECT
    mf.forecast_date                    AS forecast_week,
    mf.store_key                        AS store_id,
    st.store_type,
    st.region,
    st.size_bucket,
    mf.dept_key                         AS department,
    dp.dept_label,
    ROUND(mf.predicted_sales, 2)        AS predicted_sales,
    ROUND(mf.lower_ci_95, 2)            AS lower_ci_95,
    ROUND(mf.upper_ci_95, 2)            AS upper_ci_95,
    -- Prediction interval width (proxy for forecast uncertainty)
    ROUND(mf.upper_ci_95 - mf.lower_ci_95, 2)
                                        AS ci_width,
    mf.stockout_risk_flag,
    CASE mf.stockout_risk_flag
        WHEN 1 THEN 'HIGH RISK'
        ELSE 'Normal'
    END                                 AS stockout_risk_label,
    mf.model_type,
    mf.created_at                       AS forecast_generated_at,
    la.last_actual_sales                AS last_actual_sales
FROM mart_forecast mf
JOIN dim_store      st ON mf.store_key = st.store_key
JOIN dim_department dp ON mf.dept_key  = dp.dept_key
LEFT JOIN last_actuals la ON mf.store_key = la.store_key AND mf.dept_key = la.dept_key
ORDER BY mf.stockout_risk_flag DESC, mf.forecast_date, mf.store_key, mf.dept_key;

-- End of 04_bi_views.sql
