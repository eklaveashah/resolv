"""Bounded public feeds. Each source has its own cache, age and failure state."""
import json
import math
import threading
import time
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

KALSHI = 'https://api.elections.kalshi.com/trade-api/v2'
POLY = 'https://gamma-api.polymarket.com'


def get(url):
    req = urllib.request.Request(url, headers={'User-Agent': 'Resolv/2.0 public-data research'})
    with urllib.request.urlopen(req, timeout=8) as res:
        body = res.read(4_000_001)
        if len(body) > 4_000_000:
            raise ValueError('upstream response too large')
        return body


def number(v):
    try:
        f = float(v)
        return f if math.isfinite(f) else None
    except (ValueError, TypeError):
        return None


def dollars(m, key):
    p = number(m.get(key + '_dollars'))
    if p is not None:
        return p
    p = number(m.get(key))
    return p / 100 if p is not None else None


def kalshi():
    # A diversified set avoids the default newest-first stream of multileg
    # combinations. Ordinary open contracts remain available as a fallback.
    series = ['', 'KXFED', 'KXCPI', 'KXBTC', 'KXINX', 'KXGDP']
    def page(s):
        q = {'status': 'open', 'limit': 100, 'mve_filter': 'exclude'}
        if s:
            q['series_ticker'] = s
        try:
            return json.loads(get(KALSHI + '/markets?' + urllib.parse.urlencode(q))).get('markets', [])
        except Exception:
            if not s:
                raise
            return []
    with ThreadPoolExecutor(max_workers=6) as pool:
        pages = list(pool.map(page, series))
    rows = {}
    for m in [m for page in pages for m in page]:
        if m.get('market_type', 'binary') != 'binary':
            continue
        bid, ask, last = dollars(m, 'yes_bid'), dollars(m, 'yes_ask'), dollars(m, 'last_price')
        valid_book = bid is not None and ask is not None and 0 < bid <= ask < 1
        p = (bid + ask) / 2 if valid_book else last
        if p is None or not 0.01 < p < 0.99:
            continue
        m = dict(m)
        m['last_price'] = p * 100  # compatibility with terminal's cents contract
        m['price_basis'] = 'bid/ask midpoint' if valid_book else 'last trade'
        m['spread'] = ask - bid if valid_book else None
        m['volume_24h'] = number(m.get('volume_24h_fp') or m.get('volume_24h')) or 0
        rows[m['ticker']] = m
    out = sorted(rows.values(), key=lambda m: m['volume_24h'], reverse=True)[:70]
    if not out:
        raise ValueError('no usable open Kalshi quotes')
    return out


def polymarket():
    rows = json.loads(get(POLY + '/markets?active=true&closed=false&limit=60&order=volume24hr&ascending=false'))
    out = []
    for m in rows:
        try:
            outcomes = m.get('outcomes') or []
            outcomes = json.loads(outcomes) if isinstance(outcomes, str) else outcomes
            names = [str(x).lower() for x in outcomes]
            if sorted(names) != ['no', 'yes'] or m.get('closed'):
                continue
            prices = m.get('outcomePrices') or []
            prices = json.loads(prices) if isinstance(prices, str) else prices
            p = number(prices[names.index('yes')])
            if p is None or not 0.01 < p < 0.99:
                continue
            m = dict(m)
            m['yes_index'] = names.index('yes')
            m['outcomePrices'] = [str(p), str(1-p)]
            out.append(m)
        except (ValueError, TypeError, IndexError):
            continue
    if not out:
        raise ValueError('no usable binary Polymarket quotes')
    return out


def news():
    root = ET.fromstring(get('https://feeds.bbci.co.uk/news/business/rss.xml'))
    out = []
    for el in root.findall('./channel/item')[:25]:
        title, url = el.findtext('title'), el.findtext('link')
        try:
            dt = parsedate_to_datetime(el.findtext('pubDate')).astimezone(timezone.utc)
        except (ValueError, TypeError):
            continue
        if title and url and url.startswith('https://'):
            out.append({'title': title[:240], 'url': url, 'domain': 'bbc.co.uk',
                        'seendate': dt.strftime('%Y%m%dT%H%M%SZ')})
    if not out:
        raise ValueError('news feed has no dated articles')
    return out


class Feeds:
    def __init__(self):
        self.entries = {}
        self.locks = {k: threading.Lock() for k in ('poly', 'kalshi', 'news')}

    def source(self, key):
        with self.locks[key]:
            now = time.time()
            old = self.entries.get(key, {})
            if now - old.get('checked', 0) < 60:
                return self._age(old)
            try:
                rows = {'poly': polymarket, 'kalshi': kalshi, 'news': news}[key]()
                entry = {'items': rows, 'fetchedAt': int(time.time()*1000), 'checked': time.time(),
                         'status': 'live', 'error': None}
            except Exception as e:
                entry = {'items': old.get('items', []), 'fetchedAt': old.get('fetchedAt'),
                         'checked': time.time(), 'status': 'stale' if old.get('items') else 'unavailable',
                         'error': str(e)[:200]}
            self.entries[key] = entry
            return self._age(entry)

    def _age(self, entry):
        out = dict(entry)
        out.pop('checked', None)
        out['ageSeconds'] = round(max(0, time.time() - out['fetchedAt']/1000)) if out['fetchedAt'] else None
        # A minute cache is live polling; retained data after errors is stale.
        out['stale'] = out['status'] != 'live'
        return out

    def payload(self):
        with ThreadPoolExecutor(max_workers=3) as pool:
            values = list(pool.map(self.source, ('poly', 'kalshi', 'news')))
        sources = dict(zip(('poly', 'kalshi', 'news'), values))
        markets = []
        for key, venue in [('poly', 'POLYMARKET'), ('kalshi', 'KALSHI')]:
            src = sources[key]
            if src['status'] != 'live':
                continue
            for m in src['items']:
                p = float(m['outcomePrices'][0])*100 if key == 'poly' else m['last_price']
                markets.append({'venue': venue, 'question': m.get('question') or m['title'],
                                'priceCents': round(p), 'volume24h': m.get('volume24hr') or m.get('volume_24h') or 0})
        return {'news': sources['news']['items'], 'markets': markets,
                'sources': {k: {a: b for a, b in v.items() if a != 'items'} for k, v in sources.items()},
                'fetchedAt': sources['news']['fetchedAt'], 'stale': sources['news']['stale'],
                'ageSeconds': sources['news']['ageSeconds']}

FEEDS = Feeds()
