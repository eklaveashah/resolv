# Resolv connected workspace — October 8, 2026

Double-click **Start Resolv.command**. The website, terminal, public feeds and
model status share http://127.0.0.1:8899. Keep the terminal window running.
Double-click **Train Resolv.command** to collect recent and archived settlements
and train research candidates. This is a manual job, not a new scheduled task.

## What works locally

- Website links into all ten terminal views.
- Public Kalshi and Polymarket quotes and BBC Business RSS, refreshed every
  minute in the terminal; independent per-source caches, failures and ages.
- Current fixed-point Kalshi prices and candlesticks; correct Polymarket YES
  outcome selection. No exchange credentials are required for these reads.
- Search, venue/topic/source filters, evidence graph, model attribution,
  hypothetical simulations and sizing, watchlist, browser-session alerts,
  paper portfolio, and CSV/JSON export remain browser features.
- Server-side access requests in `runtime/signups.sqlite`; no email is sent.
- Model status and validation results from `runtime/`; the forecast button
  uses only a qualifying live champion and authentic market histories.
- Static-file allowlist keeps databases and model artifacts off the web.

## Model changes

Historical features no longer use final market volume/liquidity. Time-left
uses the listed market close instead of a later settlement. Training, validation
and test retain whole events and purge training labels unavailable before the
next forecast window. Synthetic and real rows are explicitly separated.
Boosting calibration uses a later, event-disjoint training slice instead of
random cross-validation. A market-anchored residual candidate learns bounded,
regularized changes to the market log-odds. Older feature-schema models cannot
be silently loaded.

A model must beat the exchange baseline on validation and test and have at
least 50 training, 20 validation and 20 test events before promotion. A previous
champion is compared on the same untouched test rows, not its old test score.
Candidate runs and metrics are saved even when they fail these gates. These
are research gates, not proof of profitability or a substitute for forward
validation. Repeated development runs on the same test data are exploratory.

The initial real-data run does **not** qualify. The current history is dominated
by Kalshi and has only 32 train, 7 validation and 13 test independent events.

Unit fix (Oct 8, 2026): Kalshi's `/historical` candlestick endpoint returns
dollar strings under bare keys (`close: "0.9300"`), which were being read as
cents and divided by 100. Every archived training path was stored 100x too low,
which is why the first two reports show validation skill +0.83 and test skill
-11.6. `candle_price` now takes the endpoint into account; the 147 affected
markets were re-fetched (pre-fix backup: `runtime/data/resolv_ml.pre-unitfix.sqlite`).
Ignore the `20261009-0050*` and `20261009-0052*` reports. On clean data the best
candidate ties the market on test (Brier 0.04581 vs 0.04581) and the fitted
crowd-bias slope is b=1.115.
Live prices are available; a demonstrably superior live forecast is not yet
established. The terminal explicitly keeps live trade verdicts on hold.
Historical source close times can be revised; future work should archive
point-in-time contract metadata, books and news as well as price candles.

Generic headline tone is not the direction of a particular binary outcome.
Live news is linked for context, with zero numerical news adjustment until
contract-specific evidence is validated. Sample headlines never price live
contracts. Fuzzy text matches cannot establish identical settlement rules,
so live cross-venue matches require an explicit reviewed match key; an empty
scanner is a supported result.

## API

- GET `/api/feed` — news and website tape plus per-source health.
- GET `/api/poly`, `/api/kalshi`, `/api/news` — normalized source snapshots.
- GET `/api/model` — data counts, champion or latest candidate metrics.
- POST `/api/forecast` with `{"market_id":"live-k-..."}` — fetch a current
  contract's history and score it with the qualifying live champion.
- POST `/api/predict` with `{market, price_path}` — supplied historical inputs.
- POST `/api/waitlist` with `{email, source}` — local signup persistence.
- Existing `/health`, `/demo`, `/price`, `/sentiment`, `/kelly`, and volatility
  routes remain available. This loopback service is for local research.

## Publishing

The public domain currently serves an older static site from GitHub Pages.
These local edits have not been published. GitHub Pages cannot run the Python
service: public use needs backend hosting with TLS, authentication for private
features, storage, monitoring, production signup/email integration and an API
origin configured in the frontend/CSP. Billing, user accounts, durable alerts,
team seats and exchange execution are not implemented by this local upgrade.
The older Cloudflare Worker is retained as an optional deployment starting
point; it is not the service used or tested by this workspace.

Connect the GitHub plugin and supply the repository URL to prepare a reviewed
repo change. Do not upload `runtime/`, virtual environments or local databases.
The new `.gitignore` excludes them. Existing training schedules/runtime copies
were not replaced; they must be updated separately if you choose to adopt this
code in scheduled operation.

API references:
- https://docs.kalshi.com/api-reference/market/get-markets
- https://docs.kalshi.com/api-reference/historical/get-historical-markets
- https://docs.kalshi.com/api-reference/historical/get-historical-market-candlesticks
- https://github.com/Polymarket/agent-skills/blob/main/market-data.md
