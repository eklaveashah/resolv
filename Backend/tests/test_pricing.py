"""Unit tests for the Resolv pricing engine.

Run from Backend/:  python3 -m unittest discover -s tests -v
"""
import math
import unittest

from resolv_engine import (Evidence, Market, QualRubric, debias_market_prob,
                           digital_option_prob, evidence_log_odds, kelly_no,
                           kelly_yes, norm_cdf, price, realized_vol,
                           sentiment_score)
from resolv_engine.pricing import EVIDENCE_CAP, KELLY_CAP, inv_logit, logit


def mk(kind="event", p=0.50, **kw):
    base = dict(id="t", title="test", kind=kind, price_yes=p,
                liquidity_score=0.8, days_to_expiry=30.0)
    base.update(kw)
    return Market(**base)


class TestPrimitives(unittest.TestCase):
    def test_norm_cdf_known_values(self):
        self.assertAlmostEqual(norm_cdf(0.0), 0.5, places=12)
        self.assertAlmostEqual(norm_cdf(1.959963985), 0.975, places=6)
        self.assertAlmostEqual(norm_cdf(-1.959963985), 0.025, places=6)

    def test_logit_roundtrip(self):
        for p in (0.01, 0.2, 0.5, 0.77, 0.99):
            self.assertAlmostEqual(inv_logit(logit(p)), p, places=9)

    def test_realized_vol(self):
        flat = [100.0] * 30
        self.assertAlmostEqual(realized_vol(flat), 0.0, places=9)
        # alternating +/-1% daily moves -> annual vol near 1% * sqrt(365) * ~1
        seq = [100.0]
        for i in range(60):
            seq.append(seq[-1] * (1.01 if i % 2 == 0 else 0.99))
        v = realized_vol(seq)
        self.assertGreater(v, 0.10)
        self.assertLess(v, 0.30)
        with self.assertRaises(ValueError):
            realized_vol([100.0, 101.0])


class TestDigitalOption(unittest.TestCase):
    def test_atm_below_half_zero_drift(self):
        # lognormal median < mean: at S==K with mu=0, P(S_T > K) slightly < 0.5
        p = digital_option_prob(100, 100, 0.5, 30, drift=0.0)
        self.assertLess(p, 0.5)
        self.assertGreater(p, 0.40)

    def test_monotone_in_spot(self):
        ps = [digital_option_prob(s, 100, 0.5, 30) for s in (80, 90, 100, 110, 120)]
        self.assertEqual(ps, sorted(ps))

    def test_deep_itm_otm(self):
        self.assertGreater(digital_option_prob(200, 100, 0.3, 10), 0.99)
        self.assertLess(digital_option_prob(50, 100, 0.3, 10), 0.01)

    def test_expiry_collapses_to_indicator(self):
        self.assertEqual(digital_option_prob(101, 100, 0.5, 0), 1.0)
        self.assertEqual(digital_option_prob(99, 100, 0.5, 0), 0.0)

    def test_more_time_pulls_toward_uncertainty(self):
        near = digital_option_prob(120, 100, 0.6, 5)
        far = digital_option_prob(120, 100, 0.6, 180)
        self.assertGreater(near, far)  # ITM certainty erodes with time

    def test_input_validation(self):
        with self.assertRaises(ValueError):
            digital_option_prob(-1, 100, 0.5, 30)
        with self.assertRaises(ValueError):
            digital_option_prob(100, 100, 0.0, 30)


class TestDebias(unittest.TestCase):
    def test_longshot_marked_down_favorite_up(self):
        self.assertLess(debias_market_prob(0.05), 0.05)
        self.assertGreater(debias_market_prob(0.95), 0.95)
        self.assertAlmostEqual(debias_market_prob(0.5), 0.5, places=9)


class TestEvidence(unittest.TestCase):
    def test_sentiment_directions(self):
        s_pos, _ = sentiment_score("ETF inflows surge to record as demand beats forecasts")
        s_neg, _ = sentiment_score("Bill delayed as committee dissent stalls markup")
        self.assertGreater(s_pos, 0.3)
        self.assertLess(s_neg, -0.3)

    def test_evidence_moves_price_in_direction(self):
        m = mk(p=0.50)
        up = price(m, [Evidence(text="approval surges as inflows beat records", age_hours=1)])
        dn = price(m, [Evidence(text="plan fails as regulators reject and delay", age_hours=1)])
        self.assertGreater(up.fair_prob, dn.fair_prob)

    def test_evidence_total_is_capped(self):
        many = [Evidence(text="x", sentiment=1.0, relevance=1.0,
                         source_weight=1.0, age_hours=0.0) for _ in range(50)]
        total, _ = evidence_log_odds(many)
        self.assertAlmostEqual(total, EVIDENCE_CAP, places=9)

    def test_recency_decay(self):
        fresh, _ = evidence_log_odds([Evidence(text="x", sentiment=0.8, age_hours=0)])
        stale, _ = evidence_log_odds([Evidence(text="x", sentiment=0.8, age_hours=144)])
        self.assertGreater(fresh, stale)
        self.assertGreater(stale, 0)


class TestPipeline(unittest.TestCase):
    def test_attribution_sums_exactly(self):
        m = mk(kind="threshold", p=0.44, spot=112500.0, strike=120000.0,
               annual_vol=0.55, days_to_expiry=18)
        r = price(m, [Evidence(text="inflows surge", age_hours=2)],
                  QualRubric(resolution_clarity="clear", base_rate=0.4))
        total = logit(r.market_prob) + sum(c.delta_log_odds for c in r.contributions)
        self.assertAlmostEqual(total, logit(r.fair_prob), places=6)

    def test_hold_when_no_evidence_event(self):
        r = price(mk(p=0.5))
        self.assertEqual(r.verdict, "HOLD")
        self.assertTrue(any("no evidence" in h for h in r.hold_reasons))
        self.assertEqual(r.kelly_suggested, 0.0)

    def test_threshold_needs_fields(self):
        with self.assertRaises(ValueError):
            price(mk(kind="threshold", p=0.5))  # missing spot/strike/vol

    def test_invalid_price_rejected(self):
        with self.assertRaises(ValueError):
            price(mk(p=0.0))
        with self.assertRaises(ValueError):
            price(mk(p=1.0))

    def test_ambiguity_shrinks_toward_half_and_cuts_confidence(self):
        ev = [Evidence(text="approval surges as inflows beat records",
                       age_hours=1, sentiment=0.9)]
        clear = price(mk(p=0.70), ev, QualRubric(resolution_clarity="clear"))
        vague = price(mk(p=0.70), ev, QualRubric(resolution_clarity="vague"))
        self.assertLess(abs(vague.fair_prob - 0.5), abs(clear.fair_prob - 0.5))
        self.assertLess(vague.confidence, clear.confidence)

    def test_threshold_model_pulls_toward_structural_prob(self):
        # market says 60%, but spot far below strike -> N(d2) low -> fair < market
        m = mk(kind="threshold", p=0.60, spot=100000.0, strike=140000.0,
               annual_vol=0.45, days_to_expiry=20)
        r = price(m, [], QualRubric(resolution_clarity="clear"))
        self.assertLess(r.fair_prob, 0.60)
        self.assertIn("digital_option_N(d2)", [c.name for c in r.contributions])

    def test_verdict_gates_and_sizing(self):
        strong = [Evidence(text="x", sentiment=0.95, relevance=1.0,
                           source_weight=1.0, age_hours=0.0) for _ in range(4)]
        r = price(mk(p=0.45, liquidity_score=0.95), strong,
                  QualRubric(resolution_clarity="clear"))
        self.assertEqual(r.verdict, "BUY_YES")
        self.assertGreater(r.edge, 0.04)
        self.assertGreater(r.kelly_suggested, 0.0)
        self.assertLessEqual(r.kelly_suggested, KELLY_CAP)
        self.assertLessEqual(r.kelly_suggested, 0.5 * r.kelly_full)

    def test_buy_no_side(self):
        bear = [Evidence(text="x", sentiment=-0.95, relevance=1.0,
                         source_weight=1.0, age_hours=0.0) for _ in range(4)]
        r = price(mk(p=0.55, liquidity_score=0.95), bear,
                  QualRubric(resolution_clarity="clear"))
        self.assertEqual(r.verdict, "BUY_NO")
        self.assertLess(r.edge, 0)

    def test_probability_bounds(self):
        crazy = [Evidence(text="x", sentiment=1.0, relevance=1.0,
                          source_weight=1.0, age_hours=0.0) for _ in range(100)]
        r = price(mk(p=0.97), crazy)
        self.assertLess(r.fair_prob, 1.0)
        self.assertGreater(r.fair_prob, 0.0)


class TestKelly(unittest.TestCase):
    def test_zero_edge_zero_kelly(self):
        self.assertAlmostEqual(kelly_yes(0.5, 0.5), 0.0, places=12)

    def test_positive_edge_positive_kelly(self):
        self.assertGreater(kelly_yes(0.6, 0.5), 0.0)
        self.assertGreater(kelly_no(0.4, 0.5), 0.0)

    def test_negative_edge_floored(self):
        self.assertEqual(kelly_yes(0.4, 0.5), 0.0)

    def test_known_value(self):
        # p=0.6 at m=0.5: b=1, f* = (0.6 - 0.4)/1 = 0.2
        self.assertAlmostEqual(kelly_yes(0.6, 0.5), 0.2, places=12)


if __name__ == "__main__":
    unittest.main()
