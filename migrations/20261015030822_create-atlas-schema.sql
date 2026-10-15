-- Atlas schema: persistent price cache (server only), saved portfolios and
-- backtest history (owned by each authenticated user, isolated with RLS).

-- ---------------------------------------------------------------------------
-- Price cache: written and read only by the Flask server with the project API
-- key. Runtime roles (anon, authenticated) get no access at all.
-- ---------------------------------------------------------------------------
CREATE TABLE public.price_history (
  ticker TEXT NOT NULL,
  day DATE NOT NULL,
  close DOUBLE PRECISION NOT NULL CHECK (close > 0),
  PRIMARY KEY (ticker, day)
);

CREATE TABLE public.price_coverage (
  ticker TEXT PRIMARY KEY,
  start_date DATE NOT NULL,
  end_date DATE NOT NULL,
  fetched_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK (start_date <= end_date)
);

ALTER TABLE public.price_history ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.price_coverage ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.price_history FROM anon, authenticated;
REVOKE ALL ON public.price_coverage FROM anon, authenticated;

-- ---------------------------------------------------------------------------
-- Saved portfolios (HRP or Black-Litterman) and their weights.
-- ---------------------------------------------------------------------------
CREATE TABLE public.saved_portfolios (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL DEFAULT auth.uid() REFERENCES auth.users(id) ON DELETE CASCADE,
  name TEXT NOT NULL CHECK (char_length(btrim(name)) BETWEEN 1 AND 120),
  model TEXT NOT NULL CHECK (model IN ('hrp', 'black_litterman')),
  benchmark TEXT,
  start_date DATE,
  end_date DATE,
  expected_return DOUBLE PRECISION,
  volatility DOUBLE PRECISION,
  sharpe DOUBLE PRECISION,
  sortino DOUBLE PRECISION,
  max_drawdown DOUBLE PRECISION,
  -- Small model parameters (objective, risk-free rate, weight limits, views).
  params JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_saved_portfolios_user_created
  ON public.saved_portfolios (user_id, created_at DESC);

CREATE TABLE public.portfolio_weights (
  portfolio_id UUID NOT NULL REFERENCES public.saved_portfolios(id) ON DELETE CASCADE,
  user_id UUID NOT NULL DEFAULT auth.uid() REFERENCES auth.users(id) ON DELETE CASCADE,
  ticker TEXT NOT NULL,
  weight DOUBLE PRECISION NOT NULL,
  PRIMARY KEY (portfolio_id, ticker)
);

CREATE INDEX idx_portfolio_weights_user ON public.portfolio_weights (user_id);

-- ---------------------------------------------------------------------------
-- Backtest history: one run per distinct parameter set (fingerprint) and one
-- result row per strategy of that run.
-- ---------------------------------------------------------------------------
CREATE TABLE public.backtest_runs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL DEFAULT auth.uid() REFERENCES auth.users(id) ON DELETE CASCADE,
  fingerprint TEXT NOT NULL,
  tickers TEXT[] NOT NULL,
  start_date DATE NOT NULL,
  end_date DATE NOT NULL,
  trading_days INTEGER NOT NULL,
  lookback INTEGER NOT NULL,
  drift_threshold DOUBLE PRECISION NOT NULL,
  vol_window INTEGER NOT NULL,
  vol_threshold DOUBLE PRECISION,
  fees DOUBLE PRECISION NOT NULL,
  slippage DOUBLE PRECISION NOT NULL,
  init_cash DOUBLE PRECISION NOT NULL,
  best_strategy TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (user_id, fingerprint)
);

CREATE INDEX idx_backtest_runs_user_created
  ON public.backtest_runs (user_id, created_at DESC);

CREATE TABLE public.backtest_results (
  run_id UUID NOT NULL REFERENCES public.backtest_runs(id) ON DELETE CASCADE,
  user_id UUID NOT NULL DEFAULT auth.uid() REFERENCES auth.users(id) ON DELETE CASCADE,
  strategy TEXT NOT NULL,
  total_return DOUBLE PRECISION,
  cagr DOUBLE PRECISION,
  volatility DOUBLE PRECISION,
  sharpe DOUBLE PRECISION,
  sortino DOUBLE PRECISION,
  max_drawdown DOUBLE PRECISION,
  rebalances INTEGER,
  total_costs DOUBLE PRECISION,
  rebalance_dates DATE[] NOT NULL DEFAULT '{}',
  PRIMARY KEY (run_id, strategy)
);

CREATE INDEX idx_backtest_results_user ON public.backtest_results (user_id);

-- ---------------------------------------------------------------------------
-- Access control: owners only. Rows are immutable except a portfolio's name.
-- ---------------------------------------------------------------------------
ALTER TABLE public.saved_portfolios ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.portfolio_weights ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.backtest_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.backtest_results ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON public.saved_portfolios, public.portfolio_weights,
  public.backtest_runs, public.backtest_results FROM anon, authenticated;
GRANT USAGE ON SCHEMA public TO authenticated;
GRANT SELECT, INSERT, DELETE ON public.saved_portfolios, public.portfolio_weights,
  public.backtest_runs, public.backtest_results TO authenticated;
GRANT UPDATE (name) ON public.saved_portfolios TO authenticated;

CREATE POLICY saved_portfolios_owner_select ON public.saved_portfolios
  FOR SELECT TO authenticated
  USING (user_id = (SELECT auth.uid()));
CREATE POLICY saved_portfolios_owner_insert ON public.saved_portfolios
  FOR INSERT TO authenticated
  WITH CHECK (user_id = (SELECT auth.uid()));
CREATE POLICY saved_portfolios_owner_update ON public.saved_portfolios
  FOR UPDATE TO authenticated
  USING (user_id = (SELECT auth.uid()))
  WITH CHECK (user_id = (SELECT auth.uid()));
CREATE POLICY saved_portfolios_owner_delete ON public.saved_portfolios
  FOR DELETE TO authenticated
  USING (user_id = (SELECT auth.uid()));

-- Child rows must belong to the caller and point at the caller's own parent
-- (the parent lookup runs under saved_portfolios RLS, which never reads back
-- the child tables, so there is no recursion).
CREATE POLICY portfolio_weights_owner_select ON public.portfolio_weights
  FOR SELECT TO authenticated
  USING (user_id = (SELECT auth.uid()));
CREATE POLICY portfolio_weights_owner_insert ON public.portfolio_weights
  FOR INSERT TO authenticated
  WITH CHECK (
    user_id = (SELECT auth.uid())
    AND EXISTS (
      SELECT 1 FROM public.saved_portfolios p
      WHERE p.id = portfolio_id AND p.user_id = (SELECT auth.uid())
    )
  );
CREATE POLICY portfolio_weights_owner_delete ON public.portfolio_weights
  FOR DELETE TO authenticated
  USING (user_id = (SELECT auth.uid()));

CREATE POLICY backtest_runs_owner_select ON public.backtest_runs
  FOR SELECT TO authenticated
  USING (user_id = (SELECT auth.uid()));
CREATE POLICY backtest_runs_owner_insert ON public.backtest_runs
  FOR INSERT TO authenticated
  WITH CHECK (user_id = (SELECT auth.uid()));
CREATE POLICY backtest_runs_owner_delete ON public.backtest_runs
  FOR DELETE TO authenticated
  USING (user_id = (SELECT auth.uid()));

CREATE POLICY backtest_results_owner_select ON public.backtest_results
  FOR SELECT TO authenticated
  USING (user_id = (SELECT auth.uid()));
CREATE POLICY backtest_results_owner_insert ON public.backtest_results
  FOR INSERT TO authenticated
  WITH CHECK (
    user_id = (SELECT auth.uid())
    AND EXISTS (
      SELECT 1 FROM public.backtest_runs r
      WHERE r.id = run_id AND r.user_id = (SELECT auth.uid())
    )
  );
CREATE POLICY backtest_results_owner_delete ON public.backtest_results
  FOR DELETE TO authenticated
  USING (user_id = (SELECT auth.uid()));
