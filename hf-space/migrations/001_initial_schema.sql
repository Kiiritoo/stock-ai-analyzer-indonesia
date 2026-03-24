-- ============================================================
-- IDX Stock Analyzer — Supabase Schema
-- Run this in: Supabase Dashboard > SQL Editor > Run
-- ============================================================

-- Enable UUID extension
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ─────────────────────────────────────────────────────────────
-- 1. STOCK ANALYSIS (inti: hasil AI per saham)
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS stock_analysis (
  id               UUID             DEFAULT gen_random_uuid() PRIMARY KEY,
  stock_code       TEXT             NOT NULL,
  company_name     TEXT,

  -- AI analysis payload (full JSON dari Gemini)
  analysis         JSONB            NOT NULL DEFAULT '{}',

  -- Quick-access indexed fields (supaya query cepat tanpa parse JSON)
  recommendation   TEXT,        -- BELI / TAHAN / JUAL
  investment_score INTEGER,     -- 0-100
  summary          TEXT,

  -- Cache invalidation
  news_hash        TEXT         NOT NULL DEFAULT '',
  article_count    INTEGER      DEFAULT 0,
  is_stale         BOOLEAN      DEFAULT FALSE,

  -- Timestamps
  generated_at     TIMESTAMPTZ  DEFAULT NOW(),
  expires_at       TIMESTAMPTZ,  -- NULL = tidak expire kecuali news berubah

  -- Constraint: satu row per saham (upsert/update saat refresh)
  CONSTRAINT stock_analysis_code_unique UNIQUE (stock_code)
);

CREATE INDEX IF NOT EXISTS idx_stock_analysis_code    ON stock_analysis (stock_code);
CREATE INDEX IF NOT EXISTS idx_stock_analysis_rec     ON stock_analysis (recommendation);
CREATE INDEX IF NOT EXISTS idx_stock_analysis_stale   ON stock_analysis (is_stale);
CREATE INDEX IF NOT EXISTS idx_stock_analysis_gen_at  ON stock_analysis (generated_at DESC);

-- ─────────────────────────────────────────────────────────────
-- 2. NEWS CACHE (menyimpan artikel + hash untuk deteksi perubahan)
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS news_cache (
  id          UUID         DEFAULT gen_random_uuid() PRIMARY KEY,
  stock_code  TEXT         NOT NULL,
  articles    JSONB        NOT NULL DEFAULT '[]',   -- Array of article objects
  ai_articles JSONB        NOT NULL DEFAULT '[]',   -- Articles used by AI (max 15)
  news_hash   TEXT         NOT NULL DEFAULT '',     -- MD5 hash dari 15 judul
  article_count INTEGER    DEFAULT 0,
  fetched_at  TIMESTAMPTZ  DEFAULT NOW(),
  expires_at  TIMESTAMPTZ  DEFAULT (NOW() + INTERVAL '1 hour'),

  CONSTRAINT news_cache_code_unique UNIQUE (stock_code)
);

CREATE INDEX IF NOT EXISTS idx_news_cache_code       ON news_cache (stock_code);
CREATE INDEX IF NOT EXISTS idx_news_cache_expires_at ON news_cache (expires_at);

-- ─────────────────────────────────────────────────────────────
-- 3. FUNDAMENTAL DATA (valuation, income stmt, cash flow)
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS fundamental_data (
  id          UUID         DEFAULT gen_random_uuid() PRIMARY KEY,
  stock_code  TEXT         NOT NULL,
  data        JSONB        NOT NULL DEFAULT '{}',   -- Full fundamental JSON
  fetched_at  TIMESTAMPTZ  DEFAULT NOW(),
  expires_at  TIMESTAMPTZ  DEFAULT (NOW() + INTERVAL '24 hours'),

  CONSTRAINT fundamental_data_code_unique UNIQUE (stock_code)
);

CREATE INDEX IF NOT EXISTS idx_fundamental_code      ON fundamental_data (stock_code);
CREATE INDEX IF NOT EXISTS idx_fundamental_expires   ON fundamental_data (expires_at);

-- ─────────────────────────────────────────────────────────────
-- 4. MACRO SNAPSHOT (IHSG, USD/IDR, BI Rate, Fed Rate, dll)
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS macro_snapshot (
  id          UUID         DEFAULT gen_random_uuid() PRIMARY KEY,
  data        JSONB        NOT NULL DEFAULT '{}',
  fetched_at  TIMESTAMPTZ  DEFAULT NOW(),
  expires_at  TIMESTAMPTZ  DEFAULT (NOW() + INTERVAL '2 hours')
  -- Single row: always upsert by deleting old + inserting new
);

-- ─────────────────────────────────────────────────────────────
-- 5. ANALYSIS REQUESTS (log untuk rate-limiting & analytics)
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS analysis_requests (
  id                UUID         DEFAULT gen_random_uuid() PRIMARY KEY,
  stock_code        TEXT         NOT NULL,
  ip_address        TEXT,
  user_agent        TEXT,
  from_cache        BOOLEAN      DEFAULT FALSE,
  cache_hit_type    TEXT,        -- 'full_cache' | 'stale' | 'miss'
  processing_time_ms INTEGER,
  triggered_refresh BOOLEAN      DEFAULT FALSE,
  requested_at      TIMESTAMPTZ  DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_requests_stock     ON analysis_requests (stock_code);
CREATE INDEX IF NOT EXISTS idx_requests_ip        ON analysis_requests (ip_address);
CREATE INDEX IF NOT EXISTS idx_requests_at        ON analysis_requests (requested_at DESC);

-- ─────────────────────────────────────────────────────────────
-- ROW LEVEL SECURITY (RLS)
-- Public: read-only. Write: service role key only (dari HF Spaces).
-- ─────────────────────────────────────────────────────────────

-- Enable RLS on all tables
ALTER TABLE stock_analysis    ENABLE ROW LEVEL SECURITY;
ALTER TABLE news_cache         ENABLE ROW LEVEL SECURITY;
ALTER TABLE fundamental_data  ENABLE ROW LEVEL SECURITY;
ALTER TABLE macro_snapshot     ENABLE ROW LEVEL SECURITY;
ALTER TABLE analysis_requests  ENABLE ROW LEVEL SECURITY;

-- stock_analysis: public read
CREATE POLICY "public_read_stock_analysis"
  ON stock_analysis FOR SELECT
  USING (true);

-- news_cache: public read
CREATE POLICY "public_read_news_cache"
  ON news_cache FOR SELECT
  USING (true);

-- fundamental_data: public read
CREATE POLICY "public_read_fundamental_data"
  ON fundamental_data FOR SELECT
  USING (true);

-- macro_snapshot: public read
CREATE POLICY "public_read_macro_snapshot"
  ON macro_snapshot FOR SELECT
  USING (true);

-- analysis_requests: NO public read (private log)
-- Only service role can read/write all tables (no policy needed — service role bypasses RLS)

-- ─────────────────────────────────────────────────────────────
-- HELPER FUNCTION: get latest macro snapshot
-- ─────────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION get_latest_macro()
RETURNS TABLE (data JSONB, fetched_at TIMESTAMPTZ, expires_at TIMESTAMPTZ)
LANGUAGE SQL STABLE
AS $$
  SELECT data, fetched_at, expires_at
  FROM macro_snapshot
  ORDER BY fetched_at DESC
  LIMIT 1;
$$;
