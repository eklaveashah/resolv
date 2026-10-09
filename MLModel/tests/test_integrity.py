import math
import unittest
from unittest.mock import patch
from resolv_ml import dataset, features, venues

class IntegrityTests(unittest.TestCase):
    def test_settlement_totals_do_not_leak(self):
        pts=[(10000+i*3600,.4+i*.001,10.) for i in range(40)]
        m={'created_ts':10000,'close_ts':200000,'resolve_ts':250000,'venue':'kalshi'}
        a=features.build(pts,120000,{**m,'volume':10,'liquidity':100})
        b=features.build(pts,120000,{**m,'volume':1e12,'liquidity':1e12})
        self.assertEqual(a,b)
        self.assertEqual(a['log_liquidity'],0)

    def test_horizon_uses_close_not_later_settlement(self):
        pts=[(10000+i*3600,.4,None) for i in range(40)]
        m={'created_ts':10000,'close_ts':200000,'resolve_ts':250000,'venue':'kalshi'}
        f=features.build(pts,120000,m)
        self.assertAlmostEqual(math.expm1(f['log_hours_left']),80000/3600)

    def test_nonfinite_ticks_are_excluded(self):
        pts=[(10000+i*3600,.4,None) for i in range(40)]+[(120001,float('nan'),None)]
        f=features.build(pts,130000,{'resolve_ts':200000})
        self.assertTrue(all(math.isfinite(x) for x in f.values()))

    def test_future_labels_purged_before_forecasts(self):
        rows=[dataset.Sample(str(i),str(i),'kalshi',None,100000+i*86400,100000+i*86400-4*86400,96,.5,[0]*22,i%2) for i in range(80)]
        ds=dataset._split(rows)
        self.assertLess(max(s.resolve_ts for s in ds.train),min(s.cut_ts for s in ds.val))
        self.assertLess(max(s.resolve_ts for s in ds.val),min(s.cut_ts for s in ds.test))

    def test_reversed_yes_history_token(self):
        with patch.object(venues.http,'get_json',return_value={'history':[]}) as get:
            venues.polymarket_history({'raw':{'clobTokenIds':['no-token','yes-token'],'yes_index':1}})
        self.assertEqual(get.call_args.args[1]['market'],'yes-token')

    def test_non_binary_outcome_is_rejected(self):
        self.assertIsNone(venues.polymarket_outcome({'outcomes':['A','B'],'outcomePrices':['1','0']}))

    def test_fixed_point_candles(self):
        self.assertEqual(venues.candle_price({'price':{'close_dollars':'.5200'}}),.52)
        self.assertEqual(venues.candle_price({'price':{'close':52}}),.52)
        self.assertEqual(venues.candle_price({'yes_bid':{'close_dollars':'.50'},'yes_ask':{'close_dollars':'.54'}}),.52)
        self.assertIsNone(venues.candle_price({'price':{'close_dollars':'NaN'}}))

    def test_historical_candles_are_dollars_not_cents(self):
        # Verbatim shape of /historical/markets/{ticker}/candlesticks (Oct 2026):
        # bare keys, dollar strings, null close when no trade printed.
        c = {'price': {'close': None, 'previous': '0.9100'},
             'yes_bid': {'close': '0.9000'}, 'yes_ask': {'close': '0.9300'}}
        self.assertAlmostEqual(venues.candle_price(c, plain_dollars=True), .915)
        self.assertEqual(venues.candle_price({'price': {'close': '0.4200'}}, plain_dollars=True), .42)
        # Legacy live payloads with bare keys are still cents.
        self.assertEqual(venues.candle_price({'yes_bid': {'close': 50}, 'yes_ask': {'close': 54}}), .52)

    def test_open_market_never_labelled_even_at_one(self):
        row={'id':'a','outcomes':['Yes','No'],'outcomePrices':['1','0'],'closed':False}
        with patch.object(venues.http,'get_json',return_value=[row]):
            self.assertIsNone(venues.polymarket_markets()[0]['outcome'])

if __name__=='__main__':unittest.main()

class AnchoredModelTests(unittest.TestCase):
    def test_out_of_distribution_correction_stays_bounded(self):
        import numpy as np
        from scipy.special import logit
        from resolv_ml.anchored import MarketAnchoredLogit
        X=np.array([[i/20-2, i%7, (i/20-2)*(i%7)] for i in range(80)])
        y=np.array([i%2 for i in range(80)])
        m=MarketAnchoredLogit().fit(X,y)
        test=np.array([[.3,10000,3000],[-.7,-10000,7000]])
        p=m.predict_proba(test)[:,1]
        self.assertTrue(np.all(np.isfinite(p)))
        self.assertTrue(np.all(np.abs(logit(p)-test[:,0]) <= .5000001))
