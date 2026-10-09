import unittest
from unittest.mock import patch
from resolv_engine import live

class FeedTests(unittest.TestCase):
    def test_fixed_point_quote_compatibility(self):
        self.assertEqual(live.dollars({'last_price_dollars':'.4210'},'last_price'),.421)
        self.assertEqual(live.dollars({'last_price':42},'last_price'),.42)
        self.assertIsNone(live.dollars({'last_price_dollars':'NaN'},'last_price'))

    def test_cache_retains_age_on_failure(self):
        feeds=live.Feeds()
        with patch.object(live,'news',return_value=[{'title':'real'}]) as fetch:
            first=feeds.source('news');again=feeds.source('news')
            self.assertEqual(fetch.call_count,1)
            self.assertEqual(first['fetchedAt'],again['fetchedAt'])
        feeds.entries['news']['checked']=0
        with patch.object(live,'news',side_effect=TimeoutError('timeout')):
            stale=feeds.source('news')
        self.assertEqual(stale['status'],'stale')
        self.assertEqual(stale['items'],first['items'])
        self.assertEqual(stale['fetchedAt'],first['fetchedAt'])

    def test_initial_failure_never_fabricates_rows(self):
        with patch.object(live,'polymarket',side_effect=RuntimeError('HTTP 403')):
            r=live.Feeds().source('poly')
        self.assertEqual(r['status'],'unavailable')
        self.assertEqual(r['items'],[])
        self.assertIsNone(r['fetchedAt'])

    def test_one_failed_source_does_not_hide_others(self):
        feeds=live.Feeds()
        with patch.object(live,'polymarket',side_effect=RuntimeError('offline')), patch.object(live,'news',side_effect=RuntimeError('offline')), patch.object(live,'kalshi',return_value=[{'title':'A real contract','last_price':42,'volume_24h':100}]):
            r=feeds.payload()
        self.assertEqual(len(r['markets']),1)
        self.assertEqual(r['markets'][0]['priceCents'],42)
        self.assertEqual(r['sources']['kalshi']['status'],'live')
        self.assertEqual(r['sources']['news']['status'],'unavailable')

if __name__=='__main__':unittest.main()
