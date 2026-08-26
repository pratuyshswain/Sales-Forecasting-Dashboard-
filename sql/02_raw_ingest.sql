-- =============================================================================
-- FILE: 02_raw_ingest.sql
-- PURPOSE: Define staging tables that mirror the raw CSV structure exactly.
--          These act as a landing zone before cleaning / transformation.
--          Data is loaded programmatically by etl_pipeline.py via pandas.
-- NOTE:    All columns are TEXT in staging — type-casting happens in 03_.sql
-- =============================================================================

PRAGMA foreign_keys = OFF;   -- Disable during staging load

-- ---------------------------------------------------------------------------
-- Drop staging tables (safe re-run)
-- ---------------------------------------------------------------------------
DROP TABLE IF EXISTS stg_sales;
DROP TABLE IF EXISTS stg_features;
DROP TABLE IF EXISTS stg_stores;

-- =============================================================================
-- STAGING: stg_sales
-- Source: sales.csv  (156,000 rows)
-- Columns: store_id, department, date, weekly_sales, is_holiday
-- =============================================================================
CREATE TABLE stg_sales (
    raw_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    store_id        TEXT,
    department      TEXT,
    date            TEXT,
    weekly_sales    TEXT,
    is_holiday      TEXT,
    _loaded_at      TEXT DEFAULT (datetime('now'))
);

-- =============================================================================
-- STAGING: stg_features
-- Source: features.csv  (~7,000 rows)
-- Columns: store_id, date, temperature, fuel_price, markdown_1..5,
--          cpi, unemployment, is_holiday, holiday_name, season
-- =============================================================================
CREATE TABLE stg_features (
    raw_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    store_id        TEXT,
    date            TEXT,
    temperature     TEXT,
    fuel_price      TEXT,
    markdown_1      TEXT,
    markdown_2      TEXT,
    markdown_3      TEXT,
    markdown_4      TEXT,
    markdown_5      TEXT,
    cpi             TEXT,
    unemployment    TEXT,
    is_holiday      TEXT,
    holiday_name    TEXT,
    season          TEXT,
    _loaded_at      TEXT DEFAULT (datetime('now'))
);

-- =============================================================================
-- STAGING: stg_stores
-- Source: stores.csv  (50 rows)
-- Columns: store_id, store_type, store_size, region
-- =============================================================================
CREATE TABLE stg_stores (
    raw_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    store_id        TEXT,
    store_type      TEXT,
    store_size      TEXT,
    region          TEXT,
    _loaded_at      TEXT DEFAULT (datetime('now'))
);

-- =============================================================================
-- DATA QUALITY CHECKS (run AFTER loading via Python)
-- These queries expose issues that the transform step must handle.
-- =============================================================================

-- Check 1: Negative weekly_sales (returns count; should be 0 after cleaning)
-- SELECT COUNT(*) AS negative_sales_count FROM stg_sales WHERE CAST(weekly_sales AS REAL) < 0;

-- Check 2: NULL or empty dates
-- SELECT COUNT(*) AS null_dates FROM stg_sales WHERE date IS NULL OR TRIM(date) = '';

-- Check 3: Missing store IDs in sales not present in stores
-- SELECT DISTINCT s.store_id FROM stg_sales s LEFT JOIN stg_stores st ON s.store_id = st.store_id WHERE st.store_id IS NULL;

-- Check 4: Markdown coverage (expect NULLs — they get filled with 0)
-- SELECT COUNT(*) AS null_md1 FROM stg_features WHERE markdown_1 IS NULL OR markdown_1 = '';

-- Check 5: Feature rows without a matching sales row (date × store orphans)
-- SELECT COUNT(*) FROM stg_features f LEFT JOIN stg_sales s ON f.store_id = s.store_id AND f.date = s.date WHERE s.store_id IS NULL;

-- End of 02_raw_ingest.sql
