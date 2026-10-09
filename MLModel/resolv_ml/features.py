"""Turn a price path into a feature vector.

The model is asked a specific question: standing at time T, with only the
prices that existed at time T, what is the probability this resolves YES?
Every feature below is computable at T. Nothing looks forward. That discipline
is the whole reason the backtest means anything.

Time is measured against the market's SCHEDULED end (close_ts), which is known
the moment the market lists. Never against the moment trading actually stopped
(stop_ts) or settled: a market that closes early usually closes early because
the answer arrived, so anchoring on that moment hands the model the label.
"""
import math
from typing import Dict, List, Optional, Sequence, Tuple

# FEATURE_SCHEMA is re-exported: a model records it and refuses to load when
# the meaning of the vector below has changed since it was fitted.
from .config import FEATURE_SCHEMA, MAX_PRICE, MIN_PRICE, MIN_TICKS  # noqa: F401

Point = Tuple[int, float, Optional[float]]

FEATURE_NAMES: List[str] = [
    "logit_p",          # the market's own opinion, in log-odds. The baseline.
    "abs_dev",          # |p - 0.5|, how confident the crowd is
    "log_hours_left",
    "life_frac",        # 0 at open, 1 at the scheduled end
    "dlogit_1h", "dlogit_6h", "dlogit_24h", "dlogit_72h", "dlogit_168h",
    "vol_24h", "vol_168h",
    "drawdown", "runup",
    "dev_from_mean_24h",
    "log_ticks", "tick_rate",
    "log_volume", "log_liquidity",
    "venue_kalshi",
    "crossed_50", "frac_extreme",
    "range_life",
]


def clampp(p: float) -> float:
    return max(MIN_PRICE, min(MAX_PRICE, p))


def logit(p: float) -> float:
    p = clampp(p)
    return math.log(p / (1.0 - p))


def inv_logit(x: float) -> float:
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


def _at_or_before(pts: Sequence[Point], ts: int) -> Optional[float]:
    lo, hi, best = 0, len(pts) - 1, None
    while lo <= hi:
        mid = (lo + hi) // 2
        if pts[mid][0] <= ts:
            best = pts[mid][1]
            lo = mid + 1
        else:
            hi = mid - 1
    return best


def _stdev(xs: Sequence[float]) -> float:
    n = len(xs)
    if n < 2:
        return 0.0
    m = sum(xs) / n
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1))


def anchor_ts(market: Dict) -> Optional[int]:
    """The time hours_left counts down to: the scheduled end. resolve_ts is a
    fallback for callers that pass no close_ts (old payloads); the training
    set never relies on it, dataset.samples_for_market requires close_ts."""
    v = market.get("close_ts") or market.get("resolve_ts")
    return int(v) if v else None


def build(pts: Sequence[Point], cut_ts: int, market: Dict) -> Optional[Dict[str, float]]:
    """Feature vector at cut_ts, or None if the path is too thin to trust, the
    cut is at or after the scheduled end, or trading had already stopped."""
    # Once trading has stopped the last price is the settlement, not a forecast.
    # Live markets carry no stop_ts, so this only ever bites on stored history.
    stop = market.get("stop_ts") or market.get("resolve_ts")
    if stop and cut_ts >= int(stop):
        return None

    hist = sorted((q for q in pts if q[0] <= cut_ts and q[1] is not None
                   and math.isfinite(q[1]) and 0 < q[1] < 1), key=lambda q: q[0])
    if len(hist) < MIN_TICKS:
        return None

    p = hist[-1][1]
    if p is None or not (0.0 < p < 1.0):
        return None
    lp = logit(p)

    created = market.get("created_ts") or hist[0][0]
    close = anchor_ts(market)
    if not close or close <= cut_ts:
        return None
    hours_left = (close - cut_ts) / 3600.0
    span = max(1, close - created)
    life_frac = max(0.0, min(1.0, (cut_ts - created) / span))

    def dlogit(hours: float) -> float:
        prev = _at_or_before(hist, cut_ts - int(hours * 3600))
        return 0.0 if prev is None else lp - logit(prev)

    def window(hours: float) -> List[float]:
        floor = cut_ts - int(hours * 3600)
        return [q[1] for q in hist if q[0] >= floor]

    w24, w168 = window(24), window(168)
    ldiff = lambda w: [logit(w[i]) - logit(w[i - 1]) for i in range(1, len(w))]

    prices = [q[1] for q in hist]
    pmax, pmin = max(prices), min(prices)
    mean24 = sum(w24) / len(w24) if w24 else p

    elapsed_h = max(1.0, (cut_ts - hist[0][0]) / 3600.0)
    n_extreme = sum(1 for q in prices if q < 0.10 or q > 0.90)
    crossed = 1.0 if (pmax > 0.5 > pmin) else 0.0

    # Settlement-time totals are future information. Only timestamped candles
    # may contribute volume; liquidity is unknown without archived snapshots.
    vol = sum(max(0.0, q[2]) for q in hist if q[2] is not None and math.isfinite(q[2]))
    liq = 0.0

    return {
        "logit_p": lp,
        "abs_dev": abs(p - 0.5),
        "log_hours_left": math.log1p(max(0.0, hours_left)),
        "life_frac": life_frac,
        "dlogit_1h": dlogit(1), "dlogit_6h": dlogit(6), "dlogit_24h": dlogit(24),
        "dlogit_72h": dlogit(72), "dlogit_168h": dlogit(168),
        "vol_24h": _stdev(ldiff(w24)), "vol_168h": _stdev(ldiff(w168)),
        "drawdown": pmax - p, "runup": p - pmin,
        "dev_from_mean_24h": lp - logit(mean24),
        "log_ticks": math.log1p(len(hist)),
        "tick_rate": len(hist) / elapsed_h,
        "log_volume": math.log1p(max(0.0, vol)),
        "log_liquidity": math.log1p(max(0.0, liq)),
        "venue_kalshi": 1.0 if market.get("venue") == "kalshi" else 0.0,
        "crossed_50": crossed,
        "frac_extreme": n_extreme / len(prices),
        "range_life": pmax - pmin,
    }


def vector(feat: Dict[str, float]) -> List[float]:
    return [float(feat.get(n, 0.0)) for n in FEATURE_NAMES]
