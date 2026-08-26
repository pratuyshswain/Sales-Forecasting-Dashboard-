-- =============================================================================
-- FILE: 03_transform_load.sql
-- PURPOSE: Clean staging data and load into the star-schema dimension + fact
--          tables. Executed by etl_pipeline.py after staging tables are filled.
-- ORDER:   dims first (dim_date → dim_store → dim_department), then fact_sales.
-- =============================================================================

PRAGMA foreign_keys = ON;

-- =============================================================================
-- STEP 1: Populate dim_date
-- Derives all date attributes from the distinct week-dates in stg_sales.
-- Python will pre-compute month_name, quarter, season etc. before calling SQL.
-- Here we INSERT from a pre-built Python-generated temp table.
-- =============================================================================
INSERT OR IGNORE INTO dim_date (
    date_key, week_num, month_num, month_name,
    quarter_num, year_num, season,
    is_holiday, holiday_name,
    is_year_start, is_quarter_end
)
SELECT
    f.date                                                  AS date_key,
    CAST(strftime('%W', f.date) AS INTEGER)                 AS week_num,
    CAST(strftime('%m', f.date) AS INTEGER)                 AS month_num,
    CASE CAST(strftime('%m', f.date) AS INTEGER)
        WHEN 1  THEN 'January'   WHEN 2  THEN 'February'
        WHEN 3  THEN 'March'     WHEN 4  THEN 'April'
        WHEN 5  THEN 'May'       WHEN 6  THEN 'June'
        WHEN 7  THEN 'July'      WHEN 8  THEN 'August'
        WHEN 9  THEN 'September' WHEN 10 THEN 'October'
        WHEN 11 THEN 'November'  ELSE    'December'
    END                                                     AS month_name,
    CASE
        WHEN CAST(strftime('%m', f.date) AS INTEGER) BETWEEN 1  AND 3  THEN 1
        WHEN CAST(strftime('%m', f.date) AS INTEGER) BETWEEN 4  AND 6  THEN 2
        WHEN CAST(strftime('%m', f.date) AS INTEGER) BETWEEN 7  AND 9  THEN 3
        ELSE 4
    END                                                     AS quarter_num,
    CAST(strftime('%Y', f.date) AS INTEGER)                 AS year_num,
    COALESCE(f.season, 'Unknown')                           AS season,
    COALESCE(CAST(f.is_holiday AS INTEGER), 0)              AS is_holiday,
    COALESCE(f.holiday_name, 'None')                        AS holiday_name,
    -- is_year_start: first week of January
    CASE WHEN CAST(strftime('%m', f.date) AS INTEGER) = 1
              AND CAST(strftime('%W', f.date) AS INTEGER) <= 1
         THEN 1 ELSE 0 END                                  AS is_year_start,
    -- is_quarter_end: March, June, September, December
    CASE WHEN CAST(strftime('%m', f.date) AS INTEGER) IN (3,6,9,12) THEN 1
         ELSE 0 END                                         AS is_quarter_end
FROM (
    SELECT DISTINCT date, is_holiday, holiday_name, season FROM stg_features
) f;

-- =============================================================================
-- STEP 2: Populate dim_store
-- =============================================================================
INSERT OR IGNORE INTO dim_store (
    store_key, store_type, store_size, region, size_bucket
)
SELECT
    CAST(store_id   AS INTEGER)                             AS store_key,
    UPPER(TRIM(store_type))                                 AS store_type,
    CAST(store_size AS INTEGER)                             AS store_size,
    TRIM(region)                                            AS region,
    CASE
        WHEN CAST(store_size AS INTEGER) <  80000  THEN 'Small'
        WHEN CAST(store_size AS INTEGER) <= 150000 THEN 'Medium'
        ELSE 'Large'
    END                                                     AS size_bucket
FROM stg_stores
WHERE store_id IS NOT NULL
  AND TRIM(store_id) != '';

-- =============================================================================
-- STEP 3: Populate dim_department
-- =============================================================================
INSERT OR IGNORE INTO dim_department (dept_key, dept_label)
SELECT DISTINCT
    CAST(department AS INTEGER)                             AS dept_key,
    'Dept ' || PRINTF('%02d', CAST(department AS INTEGER))  AS dept_label
FROM stg_sales
WHERE department IS NOT NULL
  AND TRIM(department) != '';

-- =============================================================================
-- STEP 4: Populate fact_sales
-- Joins sales + features on (store_id, date).
-- Cleaning rules applied inline:
--   • Negative weekly_sales → set to 0.0 (returns on negative-sale weeks exist)
--   • NULL markdowns → 0.0  (not promoted = no discount active)
--   • NULL CPI / unemployment → NULL retained (forward-filled in Python)
--   • Duplicate (store, dept, date) rows → kept first occurrence via MIN(raw_id)
-- =============================================================================
INSERT OR IGNORE INTO fact_sales (
    date_key, store_key, dept_key, weekly_sales,
    temperature, fuel_price,
    markdown_1, markdown_2, markdown_3, markdown_4, markdown_5,
    cpi, unemployment, is_holiday
)
SELECT
    s.date                                                          AS date_key,
    CAST(s.store_id   AS INTEGER)                                   AS store_key,
    CAST(s.department AS INTEGER)                                   AS dept_key,
    -- Negative sales capped to 0 (returns / reversals)
    MAX(0.0, CAST(s.weekly_sales AS REAL))                          AS weekly_sales,
    CAST(f.temperature   AS REAL)                                   AS temperature,
    CAST(f.fuel_price    AS REAL)                                   AS fuel_price,
    COALESCE(CAST(f.markdown_1 AS REAL), 0.0)                       AS markdown_1,
    COALESCE(CAST(f.markdown_2 AS REAL), 0.0)                       AS markdown_2,
    COALESCE(CAST(f.markdown_3 AS REAL), 0.0)                       AS markdown_3,
    COALESCE(CAST(f.markdown_4 AS REAL), 0.0)                       AS markdown_4,
    COALESCE(CAST(f.markdown_5 AS REAL), 0.0)                       AS markdown_5,
    CAST(f.cpi          AS REAL)                                    AS cpi,
    CAST(f.unemployment AS REAL)                                    AS unemployment,
    COALESCE(CAST(s.is_holiday AS INTEGER), 0)                      AS is_holiday
FROM (
    -- Dedup: keep only first raw row per (store, dept, date) combination
    SELECT *
    FROM stg_sales
    WHERE raw_id IN (
        SELECT MIN(raw_id)
        FROM stg_sales
        GROUP BY store_id, department, date
    )
      AND weekly_sales IS NOT NULL
      AND TRIM(weekly_sales) != ''
      AND date IS NOT NULL
      AND TRIM(date) != ''
) s
LEFT JOIN stg_features f
    ON  CAST(s.store_id AS INTEGER) = CAST(f.store_id AS INTEGER)
    AND s.date = f.date
-- Only load rows where the store and date exist in their respective dims
WHERE EXISTS (SELECT 1 FROM dim_store   WHERE store_key = CAST(s.store_id   AS INTEGER))
  AND EXISTS (SELECT 1 FROM dim_date    WHERE date_key  = s.date)
  AND EXISTS (SELECT 1 FROM dim_department WHERE dept_key = CAST(s.department AS INTEGER));

-- =============================================================================
-- STEP 5: Validation counts (review after ETL run)
-- =============================================================================
SELECT 'dim_date'        AS table_name, COUNT(*) AS row_count FROM dim_date
UNION ALL
SELECT 'dim_store',                     COUNT(*) FROM dim_store
UNION ALL
SELECT 'dim_department',                COUNT(*) FROM dim_department
UNION ALL
SELECT 'fact_sales',                    COUNT(*) FROM fact_sales
UNION ALL
SELECT 'mart_forecast',                 COUNT(*) FROM mart_forecast;

-- End of 03_transform_load.sql
