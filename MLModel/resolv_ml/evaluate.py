"""Scoring. The headline number is skill against the market price.

A prediction-market model that cannot beat "just read the price" is worth
nothing, so every metric here is reported next to that baseline and the
summary leads with the difference.
"""
import math
from typing import Dict, List, Sequence

import numpy as np


def _clip(p):
    return np.clip(np.asarray(p, dtype=float), 1e-6, 1 - 1e-6)


def brier(y, p) -> float:
    p = _clip(p)
    return float(np.mean((p - np.asarray(y, dtype=float)) ** 2))


def log_loss(y, p) -> float:
    p = _clip(p)
    y = np.asarray(y, dtype=float)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def auc(y, p) -> float:
    y = np.asarray(y, dtype=int)
    p = np.asarray(p, dtype=float)
    pos, neg = int(y.sum()), int((1 - y).sum())
    if pos == 0 or neg == 0:
        return float("nan")
    order = np.argsort(p, kind="mergesort")
    ranks = np.empty(len(p), dtype=float)
    ranks[order] = np.arange(1, len(p) + 1, dtype=float)
    # average ranks within ties
    sp = p[order]
    i = 0
    while i < len(sp):
        j = i
        while j + 1 < len(sp) and sp[j + 1] == sp[i]:
            j += 1
        if j > i:
            ranks[order[i:j + 1]] = (i + 1 + j + 1) / 2.0
        i = j + 1
    return float((ranks[y == 1].sum() - pos * (pos + 1) / 2.0) / (pos * neg))


def reliability(y, p, bins: int = 10) -> List[Dict[str, float]]:
    y = np.asarray(y, dtype=float)
    p = _clip(p)
    edges = np.linspace(0, 1, bins + 1)
    out = []
    for i in range(bins):
        lo, hi = edges[i], edges[i + 1]
        sel = (p >= lo) & (p < hi if i < bins - 1 else p <= hi)
        n = int(sel.sum())
        if n == 0:
            continue
        out.append({"bin_lo": round(float(lo), 3), "bin_hi": round(float(hi), 3),
                    "n": n, "predicted": round(float(p[sel].mean()), 4),
                    "actual": round(float(y[sel].mean()), 4)})
    return out


def ece(y, p, bins: int = 10) -> float:
    """Expected calibration error: average |predicted - actual| weighted by bin size."""
    rel = reliability(y, p, bins)
    n = sum(r["n"] for r in rel) or 1
    return float(sum(r["n"] * abs(r["predicted"] - r["actual"]) for r in rel) / n)


def sharpness(p) -> float:
    """Mean distance from 0.5. A perfectly calibrated but useless model sits
    near 0; a confident calibrated model is what you want."""
    return float(np.mean(np.abs(_clip(p) - 0.5)))


def evaluate(y, p, p_market, label: str = "") -> Dict[str, object]:
    b, bm = brier(y, p), brier(y, p_market)
    return {
        "label": label,
        "n": int(len(y)),
        "base_rate": round(float(np.mean(y)), 4),
        "brier": round(b, 5),
        "brier_market": round(bm, 5),
        "brier_skill_score": round((bm - b) / bm, 4) if bm > 0 else 0.0,
        "log_loss": round(log_loss(y, p), 5),
        "log_loss_market": round(log_loss(y, p_market), 5),
        "auc": round(auc(y, p), 4),
        "auc_market": round(auc(y, p_market), 4),
        "ece": round(ece(y, p), 4),
        "ece_market": round(ece(y, p_market), 4),
        "sharpness": round(sharpness(p), 4),
        "reliability": reliability(y, p),
    }


def bootstrap_skill(y, p, p_market, groups: Sequence, n: int = 1000, seed: int = 7,
                    q: float = 0.05) -> Dict[str, float]:
    """Event-clustered bootstrap of Brier skill against the market.

    Whole event groups are resampled with replacement, never single rows: the
    horizons of one market and the strikes of one release move together, so
    resampling rows would pretend they are independent and draw the interval
    far too tight. Returns the q and 1-q quantiles (lo, hi), the bootstrap
    mean, and the full-sample skill as point.

    Skill of a resample is 1 - SSE_model / SSE_market over the rows it drew,
    which is exactly (Brier_market - Brier_model) / Brier_market. A resample
    whose market SSE is zero has no defined skill and scores 0, as
    evaluate() does.
    """
    y = np.asarray(y, dtype=float)
    se_p = (_clip(p) - y) ** 2
    se_m = (_clip(p_market) - y) ** 2
    keys = sorted(set(groups), key=str)
    if not len(y) or not keys:
        return {"lo": 0.0, "hi": 0.0, "mean": 0.0, "point": 0.0,
                "n_groups": 0, "n_resamples": 0, "q": q}
    index = {k: i for i, k in enumerate(keys)}
    gi = np.asarray([index[g] for g in groups], dtype=int)
    G = len(keys)
    sp = np.bincount(gi, weights=se_p, minlength=G)
    sm = np.bincount(gi, weights=se_m, minlength=G)

    rng = np.random.default_rng(seed)
    # Row r of counts says how many times each group was drawn in resample r.
    counts = rng.multinomial(G, np.full(G, 1.0 / G), size=int(n)).astype(float)
    tot_p, tot_m = counts @ sp, counts @ sm
    with np.errstate(divide="ignore", invalid="ignore"):
        skills = np.where(tot_m > 0, (tot_m - tot_p) / tot_m, 0.0)
    point = float((sm.sum() - sp.sum()) / sm.sum()) if sm.sum() > 0 else 0.0
    # Rounding is monotone, so it never reorders lo, mean and hi, and it
    # absorbs the last-bit noise of averaging identical values.
    return {"lo": round(float(np.quantile(skills, q)), 6),
            "hi": round(float(np.quantile(skills, 1.0 - q)), 6),
            "mean": round(float(skills.mean()), 6),
            "point": round(point, 6),
            "n_groups": int(G), "n_resamples": int(n), "q": q}


def by_horizon(samples, p) -> List[Dict[str, object]]:
    """Skill broken out by how far ahead the forecast was made. Edge usually
    lives far from resolution and vanishes near it."""
    import collections
    idx = collections.defaultdict(list)
    for i, s in enumerate(samples):
        idx[s.horizon_h].append(i)
    rows = []
    for h in sorted(idx, reverse=True):
        ii = np.asarray(idx[h])
        y = np.asarray([samples[i].y for i in ii], dtype=int)
        pm = np.asarray([samples[i].p_market for i in ii], dtype=float)
        pp = np.asarray(p)[ii]
        if len(y) < 20 or len(set(y.tolist())) < 2:
            continue
        b, bm = brier(y, pp), brier(y, pm)
        rows.append({"horizon_h": h, "n": int(len(y)),
                     "brier": round(b, 5), "brier_market": round(bm, 5),
                     "skill": round((bm - b) / bm, 4) if bm > 0 else 0.0})
    return rows


def summary_text(m: Dict[str, object]) -> str:
    bss = m["brier_skill_score"]
    verdict = ("BEATS the market price" if bss > 0.005 else
               "matches the market price" if bss > -0.005 else
               "LOSES to the market price")
    return (
        "%s  n=%d  base rate %.3f\n"
        "  Brier      %.5f   market %.5f   skill %+.4f  -> %s\n"
        "  Log loss   %.5f   market %.5f\n"
        "  AUC        %.4f   market %.4f\n"
        "  Calib err  %.4f   market %.4f   sharpness %.4f"
        % (m["label"], m["n"], m["base_rate"],
           m["brier"], m["brier_market"], bss, verdict,
           m["log_loss"], m["log_loss_market"],
           m["auc"], m["auc_market"],
           m["ece"], m["ece_market"], m["sharpness"])
    )
