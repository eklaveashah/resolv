"""Generated markets with a KNOWN bias, used only to prove the pipeline works.

Why this exists: a training pipeline that has never run is not a pipeline.
This module builds markets whose true probability is related to the quoted
price by a bias we chose ourselves,

    logit(p_true) = A_TRUE + B_TRUE * logit(p_market)

so after training we can check that the fitted b comes back near B_TRUE. That
is a test of the machinery, nothing more. Rows written here carry
venue='synthetic' and ids prefixed 'syn:', the trainer tags any model fitted
on them as synthetic provenance, and export refuses to ship them.

It does NOT make the model predictive. Only real settled markets do that.
"""
import argparse
import math
import random
import sys
import time
from typing import List, Tuple

from . import store
from .features import inv_logit, logit

A_TRUE = 0.05
B_TRUE = 1.18          # the crowd-bias the fitter should recover
CATEGORIES = ["rates", "politics", "crypto", "sports", "economy", "geopolitics"]


def one_market(rng: random.Random, idx: int, resolve_ts: int
               ) -> Tuple[dict, List[Tuple[int, float, float]]]:
    life_h = rng.choice([24 * 7, 24 * 21, 24 * 60, 24 * 120])
    created = resolve_ts - life_h * 3600

    # Where the price ends up, and therefore what the truth is.
    p_end = min(0.97, max(0.03, rng.betavariate(1.1, 1.1)))
    p_true = inv_logit(A_TRUE + B_TRUE * logit(p_end))
    outcome = 1 if rng.random() < p_true else 0

    # Walk backwards from the closing price: uncertainty grows with time left,
    # which is how a real contract's path looks in reverse.
    pts: List[Tuple[int, float, float]] = []
    step_h = 1 if life_h <= 24 * 21 else 6
    n = life_h // step_h
    x = logit(p_end)
    drift = rng.gauss(0, 0.02)
    for i in range(n + 1):
        ts = resolve_ts - i * step_h * 3600
        pts.append((ts, inv_logit(x), abs(rng.gauss(0, 1)) * 500))
        x += rng.gauss(drift, 0.10 * math.sqrt(step_h))
        x = max(-6.0, min(6.0, x))
    pts.reverse()

    m = {
        "market_id": "syn:%06d" % idx,
        "venue": "synthetic",
        "native_id": "syn%06d" % idx,
        "event_id": "syn-ev:%06d" % (idx // 3),      # 3 markets share an event
        "title": "Synthetic contract %d" % idx,
        "category": rng.choice(CATEGORIES),
        "created_ts": created,
        "close_ts": resolve_ts,
        "resolve_ts": resolve_ts,
        "outcome": outcome,
        "volume": abs(rng.gauss(0, 1)) * 250000,
        "liquidity": abs(rng.gauss(0, 1)) * 50000,
        "raw": {"synthetic": True, "p_true": round(p_true, 4)},
    }
    return m, pts


def generate(n: int = 1200, seed: int = 11) -> dict:
    rng = random.Random(seed)
    store.init()
    now = int(time.time())
    written = points = 0
    with store.connect() as con:
        for i in range(n):
            # Spread resolutions over two years so the time split has something
            # to cut on.
            resolve = now - int(rng.random() * 730) * 86400 - 3600
            m, pts = one_market(rng, i, resolve)
            store.upsert_market(con, m)
            points += store.insert_prices(con, m["market_id"], pts)
            written += 1
            if i % 200 == 0:
                con.commit()
    return {"markets": written, "points": points,
            "a_true": A_TRUE, "b_true": B_TRUE}


def purge() -> int:
    """Remove every synthetic row. Run this before the first real training so
    generated data can never contaminate a live model."""
    with store.connect() as con:
        n = con.execute("SELECT COUNT(*) FROM markets WHERE venue='synthetic'").fetchone()[0]
        con.execute("DELETE FROM prices WHERE market_id LIKE 'syn:%'")
        con.execute("DELETE FROM markets WHERE venue='synthetic'")
        con.execute("DELETE FROM models WHERE provenance='synthetic'")
    return n


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Generate or purge synthetic markets")
    ap.add_argument("--n", type=int, default=1200)
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--purge", action="store_true")
    a = ap.parse_args(argv)
    if a.purge:
        print("purged %d synthetic markets" % purge())
        return 0
    print(generate(a.n, a.seed))
    return 0


if __name__ == "__main__":
    sys.exit(main())
