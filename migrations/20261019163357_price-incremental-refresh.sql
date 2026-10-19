-- Incremental price cache: ``fetched_at`` now marks the last update of the
-- recent tail, and ``full_refreshed_at`` the last full download of a ticker's
-- adjusted history (refreshed weekly or when a dividend/split is detected).
-- Existing rows were fully downloaded, so they start at their fetch time.
ALTER TABLE public.price_coverage
  ADD COLUMN full_refreshed_at TIMESTAMPTZ;

UPDATE public.price_coverage SET full_refreshed_at = fetched_at;

ALTER TABLE public.price_coverage
  ALTER COLUMN full_refreshed_at SET NOT NULL,
  ALTER COLUMN full_refreshed_at SET DEFAULT now();
