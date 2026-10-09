import json
import os
import sqlite3
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest.mock import patch
from resolv_engine.app import Handler

class AppTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True)
        cls.thread.start()
        cls.base='http://127.0.0.1:%s'%cls.server.server_port
    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown();cls.server.server_close();cls.thread.join()
    def request(self,path,body=None,headers=None):
        req=urllib.request.Request(self.base+path,data=body,headers=headers or {})
        try:
            with urllib.request.urlopen(req) as r:return r.status,r.read()
        except urllib.error.HTTPError as e:return e.code,e.read()
    def test_private_runtime_cannot_be_served(self):
        for path in ['/runtime/signups.sqlite','/../MLModel/config.py','/MLModel/models/model.joblib']:
            self.assertEqual(self.request(path)[0],404)
    def test_pages_and_price_engine_share_one_service(self):
        self.assertEqual(self.request('/terminal')[0],200)
        status,raw=self.request('/demo')
        self.assertEqual(status,200)
        self.assertIn('result',json.loads(raw))
    def test_json_arrays_and_nan_rejected(self):
        for body in [b'[]',b'{"email":NaN}']:
            self.assertEqual(self.request('/api/waitlist',body)[0],400)
    def test_cross_origin_write_rejected(self):
        self.assertEqual(self.request('/api/waitlist',b'{}',{'Origin':'https://foreign.example'})[0],403)
    def test_waitlist_saved_on_server_and_deduplicated(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ,{'RESOLV_ML_HOME':tmp}):
            for _ in range(2):
                self.assertEqual(self.request('/api/waitlist',b'{"email":"test@example.com"}')[0],201)
            with sqlite3.connect(tmp+'/signups.sqlite') as con:
                self.assertEqual(con.execute('SELECT count(*) FROM signups').fetchone()[0],1)
    def test_no_champion_is_a_readable_result(self):
        from resolv_ml.predict import NoChampion
        with patch('resolv_ml.predict.load',side_effect=NoChampion('no live model')):
            code,body=self.request('/api/forecast',b'{"market_id":"live-k-example"}')
        self.assertEqual(code,200)
        self.assertEqual(json.loads(body),{'ok':False,'reason':'no live model'})
