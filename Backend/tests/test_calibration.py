"""Tests for the calibration trainer."""
import unittest

from resolv_engine.calibration import (Episode, fit, log_loss, predict,
                                       reliability_bins, synthetic_history)


class TestCalibration(unittest.TestCase):
    def test_recovers_known_parameters(self):
        eps = synthetic_history(n=900, seed=11, true_a=0.05, true_b=1.18, true_k=0.55)
        r = fit(eps, iters=2500, lr=0.5)
        self.assertAlmostEqual(r.b, 1.18, delta=0.20)
        self.assertAlmostEqual(r.k, 0.55, delta=0.15)
        self.assertAlmostEqual(r.a, 0.05, delta=0.15)

    def test_fitted_beats_default_on_independent_sample(self):
        # fit on one sample, evaluate on a LARGE independent sample from the
        # same process — large enough that holdout noise cannot flip the sign
        r = fit(synthetic_history(n=600, seed=3), iters=2500, lr=0.5)
        eval_eps = synthetic_history(n=4000, seed=99)
        default = (0.0, 1.0, 0.35)
        fitted = (r.a, r.b, r.k)
        self.assertLess(log_loss(fitted, eval_eps), log_loss(default, eval_eps))

    def test_deterministic(self):
        r1 = fit(synthetic_history(n=300, seed=5))
        r2 = fit(synthetic_history(n=300, seed=5))
        self.assertEqual((r1.a, r1.b, r1.k), (r2.a, r2.b, r2.k))

    def test_needs_enough_data(self):
        with self.assertRaises(ValueError):
            fit([Episode(0.5, 0.0, 1)] * 10)

    def test_reliability_bins_shape(self):
        eps = synthetic_history(n=400, seed=9)
        bins = reliability_bins((0.0, 1.0, 0.35), eps)
        self.assertGreater(len(bins), 3)
        for b in bins:
            self.assertTrue(0.0 <= b["mean_forecast"] <= 1.0)
            self.assertTrue(0.0 <= b["realized"] <= 1.0)
            self.assertGreater(b["count"], 0)

    def test_predict_bounds(self):
        ep = Episode(p_market=0.97, evidence_lo=5.0, outcome=1)
        self.assertLess(predict(0, 2.0, 1.0, ep), 1.0)
        self.assertGreater(predict(0, 2.0, 1.0, ep), 0.0)


if __name__ == "__main__":
    unittest.main()
