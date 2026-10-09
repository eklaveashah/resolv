"""Unified loopback workspace: website, terminal, feeds, pricing and ML."""
import json
import mimetypes
import os
from pathlib import Path
import re
import sqlite3
import sys
import time
from urllib.parse import urlsplit
from http.server import ThreadingHTTPServer

ROOT = Path(__file__).resolve().parents[2]
WEB_ROOT = ROOT/'Website' if (ROOT/'Website').is_dir() else ROOT
TERMINAL = ROOT/'terminal.html' if (ROOT/'terminal.html').exists() else ROOT/'Prototype/Resolv Terminal.html'
os.environ.setdefault('RESOLV_ML_HOME', str(ROOT / 'runtime'))
sys.path.insert(0, str(ROOT / 'MLModel'))
from .server import Handler as EngineHandler
from .live import FEEDS


def model_status():
    from resolv_ml import store
    store.init()
    with store.connect() as con:
        row = store.champion(con, 'live')
        stats = store.stats(con)
        latest = con.execute("SELECT * FROM models WHERE provenance='live' ORDER BY created_ts DESC LIMIT 1").fetchone()
    if not row:
        return {'available': False, 'reason': 'No validated live champion. Market prices remain the baseline.', 'data': stats, 'latest': ({'version':latest['version'], 'algo':latest['algo'], 'metrics':json.loads(latest['metrics'])} if latest else None)}
    meta = json.loads((Path(row['path']) / 'meta.json').read_text())
    return {'available': meta.get('feature_schema') == 2 and bool(meta.get('deployment_eligible')), 'version': row['version'],
            'algo': row['algo'], 'provenance': row['provenance'], 'metrics': json.loads(row['metrics']),
            'data': stats, 'reason': 'Only models that beat the baseline qualify.'}


def live_forecast(market_id):
    from resolv_ml import venues
    from resolv_ml.predict import load, score, NoChampion
    try:
        load(provenance='live')
    except NoChampion as e:
        return {'ok': False, 'reason': str(e)}
    key = 'kalshi' if market_id.startswith('live-k-') else 'poly' if market_id.startswith('live-p-') else None
    if key is None:
        raise ValueError('choose a live exchange market')
    source = FEEDS.source(key)
    if source['status'] != 'live':
        return {'ok': False, 'reason': 'source is stale or unavailable'}
    native = market_id[7:]
    raw = next((m for m in source['items'] if str(m.get('ticker') if key=='kalshi' else m.get('id')) == native), None)
    if raw is None:
        raise ValueError('market not in the current live feed')
    now = int(time.time())
    end = venues._ts(raw.get('close_time') if key=='kalshi' else raw.get('endDate'))
    if not end or end<=now:
        return {'ok':False, 'reason':'market is no longer open'}
    m = {'native_id':native, 'venue': 'kalshi' if key=='kalshi' else 'polymarket',
         'created_ts':venues._ts(raw.get('open_time') or raw.get('startDate') or raw.get('createdAt')),
         'close_ts':end, 'resolve_ts':end,
         'raw': {'series':native.split('-')[0]} if key=='kalshi' else raw}
    if key=='kalshi':
        path = venues.kalshi_history({**m, 'resolve_ts':now})
        current = raw['last_price']/100
    else:
        path = venues.polymarket_history(m)
        current = float(raw['outcomePrices'][0])
    path = [p for p in path if p[0] < now]
    path.append((now,current,None))
    return score(path,m,at_ts=now,provenance='live')


class Handler(EngineHandler):
    def _send(self, code, payload):
        body = json.dumps(payload, allow_nan=False).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get('Content-Length', '0'))
        if n < 0 or n > 262144:
            raise ValueError('request must be at most 256 KB')
        body = json.loads(self.rfile.read(n) or b'{}', parse_constant=lambda s: (_ for _ in ()).throw(ValueError('nonfinite JSON')))
        if not isinstance(body, dict):
            raise ValueError('body must be an object')
        return body

    def do_GET(self):
        path = urlsplit(self.path).path
        try:
            if path == '/api/feed':
                return self._send(200, FEEDS.payload())
            if path in ('/api/poly', '/api/kalshi', '/api/news'):
                return self._send(200, FEEDS.source(path.rsplit('/', 1)[1]))
            if path == '/api/model':
                return self._send(200, model_status())
            if path in ('/health', '/demo', '/calibrate/demo'):
                return super().do_GET()
            # Explicit allowlist: never serve model artifacts, signup addresses,
            # runtime SQLite files or the rest of the Desktop.
            files = {'/': WEB_ROOT/'index.html', '/index.html': WEB_ROOT/'index.html',
                     '/methodology.html': WEB_ROOT/'methodology.html',
                     '/style.css': WEB_ROOT/'style.css', '/script.js': WEB_ROOT/'script.js',
                     '/terminal': TERMINAL,
                     '/terminal.html': TERMINAL}
            target = files.get(path)
            if not target:
                return self._err(404, 'page not found')
            body = target.read_bytes()
            if target.suffix == '.html':
                body = body.replace(b'</head>', b'<meta name="resolv-workspace" content="connected"></head>', 1)
            self.send_response(200)
            self.send_header('Content-Type', (mimetypes.guess_type(str(target))[0] or 'text/plain') + '; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Cache-Control', 'no-cache')
            self.end_headers()
            self.wfile.write(body)
        except Exception as e:
            self._err(503, str(e)[:200])

    def do_POST(self):
        origin = self.headers.get('Origin')
        if origin and origin != 'http://' + self.headers.get('Host', ''):
            return self._err(403, 'same-origin requests only')
        if self.path == '/api/forecast':
            try:
                body = self._body()
                return self._send(200, live_forecast(str(body.get('market_id', ''))))
            except (ValueError, TypeError) as e:
                return self._err(400, str(e))
            except Exception as e:
                return self._err(503, str(e)[:200])
        if self.path == '/api/predict':
            try:
                from resolv_ml.predict import score, NoChampion
                body = self._body()
                pts = [(int(p[0]), float(p[1]), float(p[2]) if len(p)>2 and p[2] is not None else None) for p in body['price_path']]
                result = score(pts, body['market'], at_ts=int(time.time()), provenance='live')
                return self._send(200, result)
            except NoChampion as e:
                return self._send(200, {'ok': False, 'reason': str(e)})
            except (KeyError, ValueError, TypeError, IndexError) as e:
                return self._err(400, str(e))
        if self.path == '/api/waitlist':
            try:
                body = self._body()
                email = str(body.get('email', '')).strip().lower()
                if len(email)>254 or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email):
                    return self._err(400, 'enter a valid email address')
                runtime = Path(os.environ['RESOLV_ML_HOME']); runtime.mkdir(parents=True, exist_ok=True)
                with sqlite3.connect(runtime/'signups.sqlite') as con:
                    con.execute('CREATE TABLE IF NOT EXISTS signups (email TEXT PRIMARY KEY, created INTEGER, source TEXT)')
                    con.execute('INSERT OR IGNORE INTO signups VALUES (?,?,?)', (email, int(time.time()), str(body.get('source', 'website'))[:100]))
                return self._send(201, {'ok': True, 'message': 'Access request saved on this Resolv server.'})
            except (ValueError, TypeError) as e:
                return self._err(400, str(e))
        return super().do_POST()


def main():
    port = int(os.environ.get('RESOLV_PORT', '8899'))
    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    print('Resolv workspace: http://127.0.0.1:%s • terminal: /terminal' % port, flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.server_close()

if __name__ == '__main__':
    main()
