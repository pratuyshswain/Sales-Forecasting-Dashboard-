-- =============================================================================
-- FILE: 01_schema_create.sql
-- PURPOSE: Create the star-schema data warehouse for the Sales Dashboard.
--          Compatible with SQLite (used locally) and PostgreSQL (production).
-- TABLES:
--   Dimensions : dim_date, dim_store, dim_department
--   Facts      : fact_sales
--   Mart       : mart_forecast
-- =============================================================================

PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------------------
-- Drop existing tables (safe re-run)
-- ---------------------------------------------------------------------------
DROP TABLE IF EXISTS mart_forecast;
DROP TABLE IF EXISTS fact_sales;
DROP TABLE IF EXISTS dim_date;
DROP TABLE IF EXISTS dim_store;
DROP TABLE IF EXISTS dim_department;

-- =============================================================================
-- DIMENSION: dim_date
-- Grain: one row per calendar week (Saturday week-end date, matching source)
-- =============================================================================
CREATE TABLE dim_date (
    date_key        TEXT PRIMARY KEY,   -- ISO date string YYYY-MM-DD (week start)
    week_num        INTEGER NOT NULL,   -- ISO week number (1-52/53)
    month_num       INTEGER NOT NULL,   -- Calendar month (1-12)
    month_name      TEXT    NOT NULL,   -- e.g. 'January'
    quarter_num     INTEGER NOT NULL,   -- 1-4
    year_num        INTEGER NOT NULL,   -- Calendar year
    season          TEXT    NOT NULL,   -- Winter | Spring | Summer | Fall
    is_holiday      INTEGER NOT NULL DEFAULT 0,  -- 1 = holiday week
    holiday_name    TEXT             DEFAULT 'None',
    -- Derived flags for BI slicing
    is_year_start   INTEGER NOT NULL DEFAULT 0,
    is_quarter_end  INTEGER NOT NULL DEFAULT 0,
    CHECK (is_holiday IN (0, 1)),
    CHECK (quarter_num BETWEEN 1 AND 4),
    CHECK (month_num BETWEEN 1 AND 12)
);

-- =============================================================================
-- DIMENSION: dim_store
-- Grain: one row per physical retail store
-- =============================================================================
CREATE TABLE dim_store (
    store_key       INTEGER PRIMARY KEY,  -- Natural key = store_id from source
    store_type      TEXT    NOT NULL,     -- A (large) | B (medium) | C (small)
    store_size      INTEGER NOT NULL,     -- Square footage
    region          TEXT    NOT NULL,     -- North | South | East | West
    size_bucket     TEXT    NOT NULL,     -- Small (<80k) | Medium | Large (>150k)
    CHECK (store_type IN ('A','B','C')),
    CHECK (region IN ('North','South','East','West'))
);

-- =============================================================================
-- DIMENSION: dim_department
-- Grain: one row per department number
-- =============================================================================
CREATE TABLE dim_department (
    dept_key        INTEGER PRIMARY KEY,  -- Natural key = department number
    dept_label      TEXT    NOT NULL      -- Human-readable label, e.g. 'Dept 01'
);

-- =============================================================================
-- FACT: fact_sales
-- Grain: one row per store × department × week
-- All markdown values default to 0.0 when not promoted (not NULL) for BI ease.
-- =============================================================================
CREATE TABLE fact_sales (
    fact_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    date_key        TEXT    NOT NULL REFERENCES dim_date(date_key),
    store_key       INTEGER NOT NULL REFERENCES dim_store(store_key),
    dept_key        INTEGER NOT NULL REFERENCES dim_department(dept_key),

    -- Core measure
    weekly_sales    REAL    NOT NULL,

    -- External regressors (from features.csv, joined on store+date)
    temperature     REAL,
    fuel_price      REAL,
    markdown_1      REAL    NOT NULL DEFAULT 0.0,
    markdown_2      REAL    NOT NULL DEFAULT 0.0,
    markdown_3      REAL    NOT NULL DEFAULT 0.0,
    markdown_4      REAL    NOT NULL DEFAULT 0.0,
    markdown_5      REAL    NOT NULL DEFAULT 0.0,
    cpi             REAL,
    unemployment    REAL,

    -- Holiday flag (denormalised from dim_date for fast BI filtering)
    is_holiday      INTEGER NOT NULL DEFAULT 0,

    UNIQUE (date_key, store_key, dept_key),
    CHECK (is_holiday IN (0, 1))
);

-- =============================================================================
-- MART: mart_forecast
-- Grain: one row per store × department × future week (12-week horizon)
-- Populated by the Python forecasting module, NOT by SQL transforms.
-- =============================================================================
CREATE TABLE mart_forecast (
    forecast_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    store_key           INTEGER NOT NULL REFERENCES dim_store(store_key),
    dept_key            INTEGER NOT NULL REFERENCES dim_department(dept_key),
    forecast_date       TEXT    NOT NULL,   -- ISO week date being predicted
    predicted_sales     REAL    NOT NULL,
    lower_ci_95         REAL    NOT NULL,   -- 95% prediction interval lower bound
    upper_ci_95         REAL    NOT NULL,   -- 95% prediction interval upper bound
    stockout_risk_flag  INTEGER NOT NULL DEFAULT 0,  -- 1 = at-risk week
    model_type          TEXT    NOT NULL DEFAULT 'HoltWinters',
    created_at          TEXT    NOT NULL DEFAULT (datetime('now')),

    UNIQUE (store_key, dept_key, forecast_date),
    CHECK (stockout_risk_flag IN (0, 1))
);

-- =============================================================================
-- INDEXES (improves BI query performance significantly)
-- =============================================================================
CREATE INDEX IF NOT EXISTS idx_fact_sales_date    ON fact_sales(date_key);
CREATE INDEX IF NOT EXISTS idx_fact_sales_store   ON fact_sales(store_key);
CREATE INDEX IF NOT EXISTS idx_fact_sales_dept    ON fact_sales(dept_key);
CREATE INDEX IF NOT EXISTS idx_fact_sales_holiday ON fact_sales(is_holiday);
CREATE INDEX IF NOT EXISTS idx_forecast_store     ON mart_forecast(store_key);
CREATE INDEX IF NOT EXISTS idx_forecast_dept      ON mart_forecast(dept_key);
CREATE INDEX IF NOT EXISTS idx_forecast_date      ON mart_forecast(forecast_date);
CREATE INDEX IF NOT EXISTS idx_forecast_risk      ON mart_forecast(stockout_risk_flag);

-- End of 01_schema_create.sql
