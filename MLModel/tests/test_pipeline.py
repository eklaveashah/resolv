"""Tests. The leakage ones matter most: a backtest you cannot trust is worse
than no backtest, because it produces a confident wrong number."""
import math
import os
import random
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np

from resolv_ml import dataset, evaluate, features, store, venues
from resolv_ml.features import inv_logit, logit


class TestFeatures(unittest.TestCase):
    def _path(self, n=200, start=1_700_000_000, step=3600):
        rng = random.Random(1)
        x, pts = 0.0, []
        for i in range(n):
            pts.append((start + i * step, inv_logit(x), 100.0))
            x += rng.gauss(0, 0.1)
        return pts

    def test_no_future_leakage(self):
        """Features at time T must not change when the future is appended.
        This is the single most important property in the package."""
        pts = self._path(300)
        cut = pts[150][0]
        m = {"created_ts": pts[0][0], "resolve_ts": pts[-1][0],
             "venue": "kalshi", "volume": 1e5, "liquidity": 1e4}
        a = features.build(pts[:151], cut, m)
        b = features.build(pts, cut, m)
        self.assertIsNotNone(a)
        for k in a:
            self.assertAlmostEqual(a[k], b[k], places=12,
                                   msg="feature %s leaked future data" % k)

    def test_thin_path_rejected(self):
        m = {"created_ts": 0, "resolve_ts": 10 ** 10, "venue": "kalshi"}
        self.assertIsNone(features.build(self._path(3), 1_700_000_000 + 10 ** 6, m))

    def test_cut_after_resolution_rejected(self):
        pts = self._path(100)
        m = {"created_ts": pts[0][0], "resolve_ts": pts[50][0], "venue": "kalshi"}
        self.assertIsNone(features.build(pts, pts[80][0], m))

    def test_logit_roundtrip(self):
        for p in (0.01, 0.2, 0.5, 0.83, 0.99):
            self.assertAlmostEqual(inv_logit(logit(p)), p, places=9)

    def test_vector_matches_names(self):
        pts = self._path(200)
        m = {"created_ts": pts[0][0], "resolve_ts": pts[-1][0] + 3600, "venue": "polymarket"}
        f = features.build(pts, pts[150][0], m)
        self.assertEqual(len(features.vector(f)), len(features.FEATURE_NAMES))
        self.assertTrue(all(math.isfinite(v) for v in features.vector(f)))


class TestSplit(unittest.TestCase):
    def _samples(self, n_events=40):
        out = []
        for e in range(n_events):
            for k in range(3):
                out.append(dataset.Sample(
                    market_id="m%d_%d" % (e, k), event_id="e%d" % e,
                    venue="kalshi", category="x",
                    resolve_ts=1_700_000_000 + e * 86400, cut_ts=0,
                    horizon_h=24, p_market=0.5,
                    x=[0.0] * len(features.FEATURE_NAMES), y=e % 2))
        return out

    def test_events_never_straddle(self):
        ds = dataset._split(self._samples())
        tr = {s.event_id for s in ds.train}
        va = {s.event_id for s in ds.val}
        te = {s.event_id for s in ds.test}
        self.assertFalse(tr & te, "event in both train and test")
        self.assertFalse(tr & va)
        self.assertFalse(va & te)

    def test_test_is_the_future(self):
        ds = dataset._split(self._samples())
        self.assertGreaterEqual(min(s.resolve_ts for s in ds.test),
                                max(s.resolve_ts for s in ds.train),
                                "test set resolves before training data")

    def test_all_splits_populated(self):
        ds = dataset._split(self._samples())
        for k, v in ds.sizes().items():
            self.assertGreater(v, 0, "%s split empty" % k)


class TestMetrics(unittest.TestCase):
    def test_perfect_and_worst(self):
        # Probabilities are clipped to 1e-6 so log loss stays finite, which
        # puts the extremes a hair inside 0 and 1 by design.
        y = np.array([1, 1, 0, 0])
        self.assertAlmostEqual(evaluate.brier(y, np.array([1, 1, 0, 0])), 0.0, places=4)
        self.assertAlmostEqual(evaluate.brier(y, np.array([0, 0, 1, 1])), 1.0, places=4)
        self.assertTrue(math.isfinite(evaluate.log_loss(y, np.array([0, 0, 1, 1]))))

    def test_auc_matches_sklearn(self):
        from sklearn.metrics import roc_auc_score
        rng = np.random.default_rng(4)
        for _ in range(5):
            p = rng.random(500)
            y = (rng.random(500) < p).astype(int)
            self.assertAlmostEqual(evaluate.auc(y, p), roc_auc_score(y, p), places=9)

    def test_auc_handles_ties(self):
        y = np.array([1, 0, 1, 0])
        self.assertAlmostEqual(evaluate.auc(y, np.array([0.5] * 4)), 0.5, places=9)

    def test_skill_score_sign(self):
        y = np.array([1] * 50 + [0] * 50)
        good = np.where(y == 1, 0.9, 0.1)
        m = evaluate.evaluate(y, good, np.full(100, 0.5), "t")
        self.assertGreater(m["brier_skill_score"], 0)
        m2 = evaluate.evaluate(y, np.full(100, 0.5), good, "t")
        self.assertLess(m2["brier_skill_score"], 0)

    def test_ece_zero_when_calibrated(self):
        rng = np.random.default_rng(9)
        p = rng.random(40000)
        y = (rng.random(40000) < p).astype(int)
        self.assertLess(evaluate.ece(y, p), 0.02)


class TestEstimatorRecovery(unittest.TestCase):
    def test_recovers_known_bias(self):
        """With a clean signal the fitter must return the bias we put in.
        Establishes the machinery is right, separately from data noise."""
        from sklearn.linear_model import LogisticRegression
        rng = random.Random(3)
        A, B, N = 0.05, 1.18, 120000
        X = np.empty((N, 1)); Y = np.empty(N, dtype=int)
        for i in range(N):
            pm = min(0.97, max(0.03, rng.betavariate(1.1, 1.1)))
            X[i, 0] = logit(pm)
            Y[i] = 1 if rng.random() < inv_logit(A + B * logit(pm)) else 0
        m = LogisticRegression(C=1e6, max_iter=2000).fit(X, Y)
        self.assertAlmostEqual(m.coef_[0][0], B, delta=0.04)
        self.assertAlmostEqual(m.intercept_[0], A, delta=0.04)

    def test_noise_attenuates_coefficient(self):
        """Documents why a single global crowd-bias constant is wrong: the
        further from resolution, the noisier the price, the smaller the fitted
        b. This is the finding the horizon-aware model exists to handle."""
        from sklearn.linear_model import LogisticRegression
        rng = random.Random(5)
        A, B, N = 0.05, 1.18, 80000
        fits = []
        for sd in (0.0, 1.0):
            X = np.empty((N, 1)); Y = np.empty(N, dtype=int)
            for i in range(N):
                pe = min(0.97, max(0.03, rng.betavariate(1.1, 1.1)))
                Y[i] = 1 if rng.random() < inv_logit(A + B * logit(pe)) else 0
                X[i, 0] = logit(pe) + rng.gauss(0, sd)
            fits.append(LogisticRegression(C=1e6, max_iter=2000).fit(X, Y).coef_[0][0])
        self.assertGreater(fits[0], fits[1] + 0.2)


class TestVenueParsing(unittest.TestCase):
    def test_polymarket_outcome_yes(self):
        self.assertEqual(venues.polymarket_outcome(
            {"outcomes": '["Yes","No"]', "outcomePrices": '["1","0"]'}), 1)

    def test_polymarket_outcome_no(self):
        self.assertEqual(venues.polymarket_outcome(
            {"outcomes": '["Yes","No"]', "outcomePrices": '["0","1"]'}), 0)

    def test_polymarket_reversed_leg_order(self):
        self.assertEqual(venues.polymarket_outcome(
            {"outcomes": '["No","Yes"]', "outcomePrices": '["1","0"]'}), 0)

    def test_polymarket_unsettled_is_none(self):
        """A market still trading must never be labelled."""
        for prices in ('["0.62","0.38"]', '["0.5","0.5"]', '["1"]', "[]"):
            self.assertIsNone(venues.polymarket_outcome(
                {"outcomes": '["Yes","No"]', "outcomePrices": prices}), prices)

    def test_timestamp_shapes(self):
        self.assertEqual(venues._ts("2026-01-15T00:00:00Z"), 1768435200)
        self.assertEqual(venues._ts(1768435200), 1768435200)
        self.assertEqual(venues._ts(1768435200000), 1768435200)
        self.assertIsNone(venues._ts(None))
        self.assertIsNone(venues._ts("not a date"))

    def test_jlist_handles_encoded_arrays(self):
        self.assertEqual(venues._jlist('["a","b"]'), ["a", "b"])
        self.assertEqual(venues._jlist(["a"]), ["a"])
        self.assertEqual(venues._jlist("garbage"), [])


class TestStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
        self.tmp.close()
        self.path = self.tmp.name
        store.init(self.path)

    def tearDown(self):
        for s in ("", "-wal", "-shm"):
            try:
                os.unlink(self.path + s)
            except OSError:
                pass

    def _m(self, **kw):
        base = {"market_id": "ks:A", "venue": "kalshi", "native_id": "A",
                "event_id": "e", "title": "t", "category": "c",
                "created_ts": 1, "close_ts": 2, "resolve_ts": 3,
                "outcome": 1, "volume": 10.0, "liquidity": 5.0, "raw": {}}
        base.update(kw)
        return base

    def test_outcome_never_reverted_by_partial_refetch(self):
        with store.connect(self.path) as con:
            store.upsert_market(con, self._m(outcome=1))
            store.upsert_market(con, self._m(outcome=None, title="refetched"))
            r = con.execute("SELECT outcome, title FROM markets WHERE market_id='ks:A'").fetchone()
        self.assertEqual(r["outcome"], 1, "a later partial fetch erased a settled outcome")
        self.assertEqual(r["title"], "refetched")

    def test_prices_deduplicate(self):
        with store.connect(self.path) as con:
            store.upsert_market(con, self._m())
            store.insert_prices(con, "ks:A", [(100, 0.5, None), (200, 0.6, None)])
            store.insert_prices(con, "ks:A", [(100, 0.9, None), (300, 0.7, None)])
            path = store.price_path(con, "ks:A")
        self.assertEqual(len(path), 3)
        self.assertEqual(path[0][1], 0.5, "a re-run overwrote an existing price point")

    def test_needing_prices_excludes_those_with_paths(self):
        with store.connect(self.path) as con:
            store.upsert_market(con, self._m(market_id="ks:A", native_id="A"))
            store.upsert_market(con, self._m(market_id="ks:B", native_id="B"))
            store.insert_prices(con, "ks:A", [(1, 0.5, None)])
            need = [r["market_id"] for r in store.markets_needing_prices(con)]
        self.assertEqual(need, ["ks:B"])

    def test_promotion_is_exclusive(self):
        with store.connect(self.path) as con:
            for v in ("v1", "v2"):
                store.register_model(con, {"version": v, "algo": "a", "provenance": "live",
                                           "n_train": 1, "n_test": 1, "metrics": {},
                                           "path": "/tmp"})
            store.promote(con, "v1", "live")
            store.promote(con, "v2", "live")
            n = con.execute("SELECT COUNT(*) FROM models WHERE is_champion=1").fetchone()[0]
            champ = store.champion(con, "live")
        self.assertEqual(n, 1)
        self.assertEqual(champ["version"], "v2")


if __name__ == "__main__":
    unittest.main(verbosity=2)
