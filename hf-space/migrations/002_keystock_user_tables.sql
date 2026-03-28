-- ================================================================
-- Keystock User Tables Migration (v2 — Idempotent, Safe to Re-run)
-- Run in: Supabase Dashboard > SQL Editor > Run
-- ================================================================

-- ── Profiles (linked to auth.users) ──────────────────────────
CREATE TABLE IF NOT EXISTS profiles (
  id          UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
  email       TEXT NOT NULL,
  full_name   TEXT,
  avatar_url  TEXT,
  role        TEXT NOT NULL DEFAULT 'user' CHECK (role IN ('user','admin')),
  created_at  TIMESTAMPTZ DEFAULT NOW()
);
ALTER TABLE profiles ENABLE ROW LEVEL SECURITY;

-- Drop existing policies before recreating (idempotent)
DROP POLICY IF EXISTS "users_own_profile"    ON profiles;
DROP POLICY IF EXISTS "admin_read_profiles"  ON profiles;
CREATE POLICY "users_own_profile"   ON profiles FOR ALL USING (auth.uid() = id);
CREATE POLICY "admin_read_profiles" ON profiles FOR SELECT USING (
  EXISTS (SELECT 1 FROM profiles WHERE id = auth.uid() AND role = 'admin')
);

-- Auto-create profile row on signup (handles email AND Google OAuth)
CREATE OR REPLACE FUNCTION handle_new_user()
RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
BEGIN
  INSERT INTO profiles (id, email, full_name, avatar_url)
  VALUES (
    NEW.id,
    COALESCE(NEW.email, ''),
    COALESCE(NEW.raw_user_meta_data->>'full_name', NEW.raw_user_meta_data->>'name', ''),
    COALESCE(NEW.raw_user_meta_data->>'avatar_url', NEW.raw_user_meta_data->>'picture', '')
  )
  ON CONFLICT (id) DO UPDATE SET
    email      = EXCLUDED.email,
    full_name  = COALESCE(NULLIF(EXCLUDED.full_name, ''), profiles.full_name),
    avatar_url = COALESCE(NULLIF(EXCLUDED.avatar_url, ''), profiles.avatar_url);
  RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS on_auth_user_created ON auth.users;
CREATE TRIGGER on_auth_user_created
  AFTER INSERT ON auth.users FOR EACH ROW EXECUTE FUNCTION handle_new_user();

-- Backfill profiles for any existing auth users who don't have a row yet
INSERT INTO profiles (id, email, full_name, avatar_url)
SELECT
  id,
  COALESCE(email, ''),
  COALESCE(raw_user_meta_data->>'full_name', raw_user_meta_data->>'name', ''),
  COALESCE(raw_user_meta_data->>'avatar_url', raw_user_meta_data->>'picture', '')
FROM auth.users
ON CONFLICT (id) DO NOTHING;

-- ── Watchlists ────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS watchlists (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id     UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  name        TEXT NOT NULL DEFAULT 'Watchlist Saya',
  color       TEXT NOT NULL DEFAULT '#3b82f6',
  created_at  TIMESTAMPTZ DEFAULT NOW(),
  CONSTRAINT watchlists_user_name_unique UNIQUE (user_id, name)
);
ALTER TABLE watchlists ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "watchlist_owner" ON watchlists;
CREATE POLICY "watchlist_owner" ON watchlists FOR ALL USING (auth.uid() = user_id);
CREATE INDEX IF NOT EXISTS idx_watchlists_user ON watchlists (user_id);

-- ── Watchlist Items ───────────────────────────────────────────
CREATE TABLE IF NOT EXISTS watchlist_items (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  watchlist_id   UUID NOT NULL REFERENCES watchlists(id) ON DELETE CASCADE,
  stock_code     TEXT NOT NULL,
  company_name   TEXT NOT NULL DEFAULT '',
  buy_price      NUMERIC(12,2),
  target_price   NUMERIC(12,2),
  lots           INTEGER,
  notes          TEXT,
  added_at       TIMESTAMPTZ DEFAULT NOW(),
  CONSTRAINT watchlist_items_unique UNIQUE (watchlist_id, stock_code)
);
ALTER TABLE watchlist_items ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "watchlist_items_owner" ON watchlist_items;
CREATE POLICY "watchlist_items_owner" ON watchlist_items FOR ALL USING (
  EXISTS (SELECT 1 FROM watchlists w WHERE w.id = watchlist_id AND w.user_id = auth.uid())
);
CREATE INDEX IF NOT EXISTS idx_watchlist_items_watchlist ON watchlist_items (watchlist_id);

-- ── Portfolio Transactions ────────────────────────────────────
CREATE TABLE IF NOT EXISTS portfolio_transactions (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id          UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  stock_code       TEXT NOT NULL,
  company_name     TEXT NOT NULL DEFAULT '',
  type             TEXT NOT NULL CHECK (type IN ('BUY','SELL')),
  lots             INTEGER NOT NULL CHECK (lots > 0),
  price_per_share  NUMERIC(12,2) NOT NULL,
  total_value      NUMERIC(14,2) GENERATED ALWAYS AS (lots * 100 * price_per_share) STORED,
  date             DATE NOT NULL DEFAULT CURRENT_DATE,
  notes            TEXT,
  created_at       TIMESTAMPTZ DEFAULT NOW()
);
ALTER TABLE portfolio_transactions ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "portfolio_owner" ON portfolio_transactions;
CREATE POLICY "portfolio_owner" ON portfolio_transactions FOR ALL USING (auth.uid() = user_id);
CREATE INDEX IF NOT EXISTS idx_portfolio_user  ON portfolio_transactions (user_id);
CREATE INDEX IF NOT EXISTS idx_portfolio_stock ON portfolio_transactions (stock_code);

-- ── Analysis History ──────────────────────────────────────────
CREATE TABLE IF NOT EXISTS analysis_history (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id      UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  stock_code   TEXT NOT NULL,
  company_name TEXT NOT NULL DEFAULT '',
  analyzed_at  TIMESTAMPTZ DEFAULT NOW(),
  CONSTRAINT analysis_history_unique UNIQUE (user_id, stock_code)
);
ALTER TABLE analysis_history ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "history_owner" ON analysis_history;
CREATE POLICY "history_owner" ON analysis_history FOR ALL USING (auth.uid() = user_id);
CREATE INDEX IF NOT EXISTS idx_history_user ON analysis_history (user_id);

-- ── Announcements (admin only write, public read) ─────────────
CREATE TABLE IF NOT EXISTS announcements (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  title       TEXT NOT NULL,
  message     TEXT NOT NULL,
  type        TEXT NOT NULL DEFAULT 'info' CHECK (type IN ('info','warning','success','error')),
  active      BOOLEAN NOT NULL DEFAULT TRUE,
  created_at  TIMESTAMPTZ DEFAULT NOW()
);
ALTER TABLE announcements ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "public_read_announcements"  ON announcements;
DROP POLICY IF EXISTS "admin_write_announcements"  ON announcements;
CREATE POLICY "public_read_announcements" ON announcements FOR SELECT USING (active = TRUE);
CREATE POLICY "admin_write_announcements" ON announcements FOR ALL USING (
  EXISTS (SELECT 1 FROM profiles WHERE id = auth.uid() AND role = 'admin')
);
