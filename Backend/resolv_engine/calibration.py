"""Calibration trainer — fits the engine's forecast parameters to outcomes.

"Training" for a probability engine means CALIBRATION: given a history of
episodes (market price, evidence signal, what actually happened), fit the
parameters of the forecast

    logit(p_hat) = a + b * logit(p_market) + k * evidence_lo

by minimizing log loss (this is exactly logistic regression, so the problem
is convex and plain gradient descent converges). The fitted (a, b, k)
replace the hand-set defaults (0, CAL_B, EVIDENCE_K) in pricing.

Honesty note: synthetic_history() exists so the training LOOP can be
demonstrated and tested end-to-end. Fitting on synthetic data proves the
machinery recovers known parameters — it does NOT make the model predictive.
Real predictive power requires feeding fit() a real resolved-market history.
"""
import math
import random
from dataclasses import dataclass, field
from typing import Dict, List, Tuple

from .pricing import EVIDENCE_K, clamp_p, inv_logit, logit


@dataclass
class Episode:
    """One resolved contract: what the market said, what evidence said, outcome."""
    p_market: float      # market mid at forecast time, 0..1
    evidence_lo: float   # summed evidence feature (sentiment*relevance*weight*decay)
    outcome: int         # 1 = YES resolved, 0 = NO


@dataclass
class CalibrationResult:
    a: float
    b: float
    k: float
    n_train: int
    n_test: int
    metrics: Dict[str, float] = field(default_factory=dict)
    reliability: List[Dict[str, float]] = field(default_factory=list)


def predict(a: float, b: float, k: float, ep: Episode) -> float:
    return inv_logit(a + b * logit(ep.p_market) + k * ep.evidence_lo)


def log_loss(params: Tuple[float, float, float], eps: List[Episode]) -> float:
    a, b, k = params
    total = 0.0
    for ep in eps:
        p = clamp_p(predict(a, b, k, ep))
        total += -(ep.outcome * math.log(p) + (1 - ep.outcome) * math.log(1 - p))
    return total / max(1, len(eps))


def brier(params: Tuple[float, float, float], eps: List[Episode]) -> float:
    a, b, k = params
    return sum((predict(a, b, k, ep) - ep.outcome) ** 2 for ep in eps) / max(1, len(eps))


def reliability_bins(params: Tuple[float, float, float], eps: List[Episode],
                     n_bins: int = 10) -> List[Dict[str, float]]:
    """Reliability-diagram data: mean forecast vs realized frequency per bin."""
    a, b, k = params
    bins = [[] for _ in range(n_bins)]
    for ep in eps:
        p = predict(a, b, k, ep)
        bins[min(n_bins - 1, int(p * n_bins))].append((p, ep.outcome))
    out = []
    for i, cell in enumerate(bins):
        if not cell:
            continue
        out.append({
            "bin": i,
            "mean_forecast": sum(p for p, _ in cell) / len(cell),
            "realized": sum(y for _, y in cell) / len(cell),
            "count": len(cell),
        })
    return out


def fit(episodes: List[Episode], iters: int = 1500, lr: float = 0.4,
        test_frac: float = 0.3, seed: int = 7) -> CalibrationResult:
    """Fit (a, b, k) on a train split by gradient descent; report on test split.

    Gradients are the exact logistic-regression gradients:
        dL/da = mean(p - y);  dL/db = mean((p - y) * logit(pm));
        dL/dk = mean((p - y) * evidence_lo)
    """
    if len(episodes) < 20:
        raise ValueError("need >= 20 episodes to fit calibration")
    rng = random.Random(seed)
    eps = episodes[:]
    rng.shuffle(eps)
    n_test = max(5, int(len(eps) * test_frac))
    test, train = eps[:n_test], eps[n_test:]

    a, b, k = 0.0, 1.0, EVIDENCE_K
    n = len(train)
    feats = [(logit(ep.p_market), ep.evidence_lo, ep.outcome) for ep in train]
    for _ in range(iters):
        ga = gb = gk = 0.0
        for x, e, y in feats:
            p = inv_logit(a + b * x + k * e)
            d = p - y
            ga += d
            gb += d * x
            gk += d * e
        a -= lr * ga / n
        b -= lr * gb / n
        k -= lr * gk / n

    default = (0.0, 1.0, EVIDENCE_K)
    fitted = (a, b, k)
    return CalibrationResult(
        a=a, b=b, k=k, n_train=len(train), n_test=len(test),
        metrics={
            "logloss_default": round(log_loss(default, test), 6),
            "logloss_fitted": round(log_loss(fitted, test), 6),
            "brier_default": round(brier(default, test), 6),
            "brier_fitted": round(brier(fitted, test), 6),
        },
        reliability=reliability_bins(fitted, test),
    )


def synthetic_history(n: int = 240, seed: int = 11,
                      true_a: float = 0.05, true_b: float = 1.18,
                      true_k: float = 0.55) -> List[Episode]:
    """Deterministic synthetic episodes with KNOWN true parameters.

    Used to test that fit() recovers what generated the data. Clearly not a
    substitute for real resolved-market history.
    """
    rng = random.Random(seed)
    out = []
    for _ in range(n):
        pm = clamp_p(rng.betavariate(2.0, 2.0))
        ev = rng.gauss(0.0, 0.8)
        p_true = inv_logit(true_a + true_b * logit(pm) + true_k * ev)
        out.append(Episode(p_market=pm, evidence_lo=ev,
                           outcome=1 if rng.random() < p_true else 0))
    return out
