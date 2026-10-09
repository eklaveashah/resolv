"""Paths, endpoints and the knobs worth turning."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------- one home
# Everything the pipeline writes lives in ONE place: the training store, the
# forward ledger, model artifacts, reports, the pipeline lock and signups.
#
# It defaults OUTSIDE the project on purpose. macOS protects ~/Desktop,
# ~/Documents and ~/Downloads with TCC, and a launchd agent running from one of
# them cannot read its own files: the scheduled job dies with "Operation not
# permitted" before Python even starts. Application Support is not protected,
# so the scheduled job, manual runs, the workspace server and the dashboard all
# share one database.
#
# RESOLV_ML_HOME overrides it (tests, the hosted container). The path is
# expanded and resolved so a relative override cannot leak cwd-relative paths
# into the model registry.
DEFAULT_HOME = Path.home() / "Library" / "Application Support" / "ResolvML"
HOME_DIR = Path(os.environ.get("RESOLV_ML_HOME") or DEFAULT_HOME).expanduser().resolve()

DATA_DIR = HOME_DIR / "data"
MODEL_DIR = HOME_DIR / "models"
REPORT_DIR = HOME_DIR / "reports"
ARCHIVE_DIR = HOME_DIR / "archive"
DB_PATH = Path(os.environ.get("RESOLV_ML_DB") or (DATA_DIR / "resolv_ml.sqlite")).expanduser().resolve()
# Synthetic rows never enter the canonical store. They get their own file.
SYNTHETIC_DB_PATH = DATA_DIR / "synthetic.sqlite"
# Point-in-time quotes and the forecast ledger. Irreplaceable, so it is kept
# apart from the training corpus (which can always be re-collected).
FORWARD_DB_PATH = Path(os.environ.get("RESOLV_ML_FORWARD_DB") or (DATA_DIR / "forward.sqlite")).expanduser().resolve()
SIGNUPS_DB_PATH = HOME_DIR / "signups.sqlite"
LOCK_PATH = DATA_DIR / "pipeline.lock"
KALSHI_SERIES_CACHE = DATA_DIR / "kalshi_series.json"

for _d in (DATA_DIR, MODEL_DIR, REPORT_DIR):
    _d.mkdir(parents=True, exist_ok=True)

SCHEMA_VERSION = 3          # store.py migrations; refuse to open a newer store
FEATURE_SCHEMA = 3          # bump whenever features change meaning; old models refuse to load

# ---------------------------------------------------------------- endpoints
# Public, no key required. Kalshi's read-only market endpoints are open;
# Polymarket's gamma (metadata) and clob (price history) are both public.
POLYMARKET_GAMMA = "https://gamma-api.polymarket.com"
POLYMARKET_CLOB = "https://clob.polymarket.com"
KALSHI_API = "https://api.elections.kalshi.com/trade-api/v2"

USER_AGENT = "resolv-ml/2.0 (research; https://resolvterminal.com)"
HTTP_TIMEOUT = 30
HTTP_RETRIES = 4
# Kalshi answered 429 after ~20 calls at 0.3s spacing during diagnosis, so the
# default pace is gentler and 429s honour Retry-After with backoff.
RATE_LIMIT_SLEEP = 0.4      # seconds between calls, be a good citizen
PAGE_LIMIT = 100

# ---------------------------------------------------------------- sampling
# For every resolved market we cut training rows at these horizons before
# the market's SCHEDULED end (close_ts). A cut at or after the moment trading
# actually stopped (stop_ts) is dropped: a market that already closed early is
# not something the model is ever asked to score, and its last price leaks the
# label. See dataset.samples_for_market.
HORIZONS_HOURS = [720, 336, 168, 72, 24, 6, 1]
MAX_HORIZON_HOURS = max(HORIZONS_HOURS)

MIN_TICKS = 8                    # drop markets with a near-empty price path
MIN_PRICE, MAX_PRICE = 0.005, 0.995
# A bid/ask midpoint is only a price when the book is reasonably tight.
MID_MAX_SPREAD = 0.10

# ---------------------------------------------------------------- Kalshi corpus
# The newest-settled stream is ~99.9% minute-long multivariate combos with no
# price path. The corpus is collected per series instead.
KALSHI_CATEGORIES_ALLOW = {
    "Economics", "Financials", "Commodities", "Climate and Weather", "Politics",
    "Elections", "Science and Technology", "Entertainment", "Crypto", "Health",
    "Transportation", "World", "Companies", "Sports",
}
KALSHI_CATEGORIES_DENY = {"Exotics", "Mentions", "Social"}
KALSHI_FREQ_DENY = {"fifteen_min", "hourly"}
KALSHI_MIN_SERIES_VOLUME = 1e5   # series-level volume_fp from /series?include_volume=true
KALSHI_MIN_LIFE_H = 36           # market open -> close; weather passes, intraday does not
KALSHI_EVENTS_PER_SERIES = 40    # backfill: events sampled evenly over each series' history
KALSHI_STRIKES_PER_EVENT = 3     # evenly spaced by ticker, never chosen by outcome
KALSHI_SPORTS_SHARE_MAX = 0.25   # keep the corpus from becoming 70% game winners
KALSHI_SERIES_PER_CYCLE = 60     # incremental series refreshed per daemon cycle (round robin)
HISTORY_WINDOW_DAYS = 31         # MAX_HORIZON_HOURS plus a day of lookback

# ---------------------------------------------------------------- Polymarket corpus
PM_MIN_VOLUME = 10000            # server-side volume_num_min
PM_MIN_LIFE_H = 48
CLOB_MAX_WINDOW_S = 14 * 86400   # prices-history rejects startTs/endTs spans over 15 days

# ---------------------------------------------------------------- history queue
# Every fetch attempt is recorded. Empty or failed paths back off and
# eventually become terminal, so the queue can never be head-of-line blocked
# by markets that will never yield a path.
MIN_LIFE_S = (min(HORIZONS_HOURS) + MIN_TICKS) * 3600   # shorter lives cannot yield a sample
HISTORY_BACKOFF_S = (6 * 3600, 86400, 3 * 86400, 7 * 86400)
HISTORY_MAX_ATTEMPTS = {"empty": 4, "thin": 1, "http_4xx": 2, "http_5xx": 6,
                        "network": 8, "error": 3}

# ---------------------------------------------------------------- daemon budgets
CYCLE_HISTORY_BUDGET = 300       # price paths per cycle, split across venues
CYCLE_TIME_BUDGET_S = 1500       # soft wall clock for the history stage
BACKFILL_MARKETS_PER_CYCLE = 400 # metadata rows discovered per cycle until exhausted
METADATA_REPAIR_PER_CYCLE = 150
FORWARD_SNAPSHOT_EVERY_S = 900   # one point-in-time quote per market per 15 min
FORWARD_SCORE_PER_CYCLE = 200

# ---------------------------------------------------------------- training
TEST_FRACTION = 0.25             # most recent 25% of resolutions, by time
VAL_FRACTION = 0.15              # carved out of train, also by time
RANDOM_SEED = 7

# Promotion: a challenger replaces the champion only if it improves
# out-of-sample Brier by at least this much. Stops version churn on noise.
PROMOTION_MARGIN = 0.0015
# Independent coverage required before any model may be deployed.
GATE_MIN_EVENTS = {"train": 50, "val": 20, "test": 20}
GATE_MIN_RESOLUTION_DAYS = {"val": 10, "test": 10}
# Event-clustered bootstrap of test skill. The lower bound must clear zero, so
# a model that merely ties the market cannot pass by luck across retrains.
BOOTSTRAP_RESAMPLES = 1000
BOOTSTRAP_LOWER_Q = 0.05
# Forward demotion: once a champion has this many scored live forecasts and
# loses to the market on them, it is demoted.
DEMOTE_MIN_SCORED = 40
MODEL_KEEP = 30                  # model directories retained (champion always kept)
