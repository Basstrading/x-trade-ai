-- ============================================================
-- x-trade.ai — Schema multi-tenant Supabase
-- ============================================================

-- 1. Profils utilisateurs (lie a Supabase Auth)
CREATE TABLE IF NOT EXISTS user_profiles (
    id UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
    email TEXT,
    display_name TEXT,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

-- 2. Comptes broker (credentials Topstep par user)
CREATE TABLE IF NOT EXISTS broker_accounts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    broker TEXT NOT NULL DEFAULT 'topstep',
    account_id BIGINT NOT NULL,
    account_name TEXT,
    account_size NUMERIC,
    plan TEXT,
    username TEXT,
    api_key_encrypted TEXT,
    is_active BOOLEAN DEFAULT true,
    last_sync_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT now()
);

-- 3. Trades (coeur du journal)
CREATE TABLE IF NOT EXISTS trades (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    broker_account_id UUID REFERENCES broker_accounts(id) ON DELETE SET NULL,
    external_id BIGINT,
    instrument TEXT NOT NULL,
    direction TEXT NOT NULL CHECK (direction IN ('long', 'short')),
    entry_price NUMERIC,
    exit_price NUMERIC,
    size INTEGER NOT NULL DEFAULT 1,
    pnl NUMERIC NOT NULL DEFAULT 0,
    fees NUMERIC DEFAULT 0,
    net_pnl NUMERIC GENERATED ALWAYS AS (pnl - COALESCE(fees, 0)) STORED,
    entry_time TIMESTAMPTZ,
    exit_time TIMESTAMPTZ,
    duration_seconds INTEGER,
    session TEXT CHECK (session IN ('asia', 'london', 'new_york', 'overnight')),
    strategy TEXT,
    notes TEXT,
    tags TEXT[],
    is_revenge_trade BOOLEAN DEFAULT false,
    is_overtrading BOOLEAN DEFAULT false,
    consecutive_loss_number INTEGER DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT now()
);

-- 4. Resumes journaliers (cache pour le dashboard)
CREATE TABLE IF NOT EXISTS daily_summaries (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    broker_account_id UUID REFERENCES broker_accounts(id) ON DELETE SET NULL,
    trade_date DATE NOT NULL,
    total_trades INTEGER DEFAULT 0,
    wins INTEGER DEFAULT 0,
    losses INTEGER DEFAULT 0,
    gross_pnl NUMERIC DEFAULT 0,
    fees NUMERIC DEFAULT 0,
    net_pnl NUMERIC DEFAULT 0,
    peak_pnl NUMERIC DEFAULT 0,
    max_drawdown NUMERIC DEFAULT 0,
    win_rate NUMERIC,
    avg_win NUMERIC,
    avg_loss NUMERIC,
    profit_factor NUMERIC,
    largest_win NUMERIC,
    largest_loss NUMERIC,
    max_consec_wins INTEGER DEFAULT 0,
    max_consec_losses INTEGER DEFAULT 0,
    first_trade_time TIMESTAMPTZ,
    last_trade_time TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE(user_id, broker_account_id, trade_date)
);

-- 5. Rapports du coach IA
CREATE TABLE IF NOT EXISTS coaching_reports (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    report_type TEXT NOT NULL CHECK (report_type IN ('daily', 'weekly', 'monthly', 'on_demand')),
    period_start DATE,
    period_end DATE,
    total_trades INTEGER,
    win_rate NUMERIC,
    profit_factor NUMERIC,
    net_pnl NUMERIC,
    patterns_detected JSONB DEFAULT '[]',
    strengths JSONB DEFAULT '[]',
    weaknesses JSONB DEFAULT '[]',
    action_plan JSONB DEFAULT '[]',
    desk_rule TEXT,
    full_report TEXT,
    trades_analyzed INTEGER,
    confidence_level TEXT CHECK (confidence_level IN ('low', 'medium', 'high')),
    created_at TIMESTAMPTZ DEFAULT now()
);

-- 6. Index pour performance
CREATE INDEX IF NOT EXISTS idx_trades_user_date ON trades(user_id, entry_time);
CREATE INDEX IF NOT EXISTS idx_trades_user_instrument ON trades(user_id, instrument);
CREATE INDEX IF NOT EXISTS idx_daily_summaries_user_date ON daily_summaries(user_id, trade_date);
CREATE INDEX IF NOT EXISTS idx_coaching_reports_user ON coaching_reports(user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_broker_accounts_user ON broker_accounts(user_id);

-- ============================================================
-- RLS — Row Level Security
-- ============================================================

ALTER TABLE user_profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE broker_accounts ENABLE ROW LEVEL SECURITY;
ALTER TABLE trades ENABLE ROW LEVEL SECURITY;
ALTER TABLE daily_summaries ENABLE ROW LEVEL SECURITY;
ALTER TABLE coaching_reports ENABLE ROW LEVEL SECURITY;

-- user_profiles
CREATE POLICY "Users read own profile" ON user_profiles FOR SELECT USING (id = auth.uid());
CREATE POLICY "Users update own profile" ON user_profiles FOR UPDATE USING (id = auth.uid());
CREATE POLICY "Users insert own profile" ON user_profiles FOR INSERT WITH CHECK (id = auth.uid());

-- broker_accounts
CREATE POLICY "Users read own accounts" ON broker_accounts FOR SELECT USING (user_id = auth.uid());
CREATE POLICY "Users manage own accounts" ON broker_accounts FOR ALL USING (user_id = auth.uid());

-- trades
CREATE POLICY "Users read own trades" ON trades FOR SELECT USING (user_id = auth.uid());
CREATE POLICY "Users insert own trades" ON trades FOR INSERT WITH CHECK (user_id = auth.uid());
CREATE POLICY "Users update own trades" ON trades FOR UPDATE USING (user_id = auth.uid());
CREATE POLICY "Users delete own trades" ON trades FOR DELETE USING (user_id = auth.uid());

-- daily_summaries
CREATE POLICY "Users read own summaries" ON daily_summaries FOR SELECT USING (user_id = auth.uid());
CREATE POLICY "Users manage own summaries" ON daily_summaries FOR ALL USING (user_id = auth.uid());

-- coaching_reports
CREATE POLICY "Users read own reports" ON coaching_reports FOR SELECT USING (user_id = auth.uid());
CREATE POLICY "Users manage own reports" ON coaching_reports FOR ALL USING (user_id = auth.uid());

-- ============================================================
-- Service role policy (pour le backend qui insere les trades)
-- ============================================================
-- Le service_role bypass le RLS par defaut dans Supabase.
-- Pas besoin de policy speciale pour le backend.
