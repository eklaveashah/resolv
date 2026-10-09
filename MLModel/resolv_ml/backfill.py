"""Collect a bounded, diverse historical corpus rather than only today's closes."""
import argparse
from . import http, store, venues
from .config import KALSHI_API


def run(series, events=20, strikes=5):
    store.init()
    with store.connect() as con:
        for name in series:
            groups = {}
            for endpoint in ('/markets', '/historical/markets'):
                params = {'series_ticker': name, 'limit': 1000}
                if endpoint == '/markets':
                    params['status'] = 'settled'
                try:
                    data = http.get_json(KALSHI_API + endpoint, params)
                except http.HttpError as e:
                    store.log_collection(con, 'kalshi', 'backfill', name, False, str(e))
                    print(name, str(e), flush=True)
                    continue
                for m in data.get('markets', []):
                    if m.get('result') in ('yes', 'no'):
                        groups.setdefault(m['event_ticker'], {})[m['ticker']] = m
            event_rows = sorted(groups.values(), key=lambda ms: max(venues._ts(m.get('close_time')) or 0 for m in ms.values()))
            # Evenly cover the returned history; never select by outcome.
            if len(event_rows) > events:
                event_rows = [event_rows[round(i*(len(event_rows)-1)/(events-1))] for i in range(events)]
            print(name, len(groups), 'available events;', len(event_rows), 'sampled', flush=True)
            for ms in event_rows:
                rows = sorted(ms.values(), key=lambda m: m['ticker'])
                if len(rows)>strikes:
                    rows = [rows[round(i*(len(rows)-1)/(strikes-1))] for i in range(strikes)]
                for m in rows:
                    rec = {'market_id':'ks:'+m['ticker'], 'venue':'kalshi', 'native_id':m['ticker'],
                           'event_id':'ks-ev:'+m['event_ticker'], 'title':m.get('title'),
                           'created_ts':venues._ts(m.get('open_time')), 'close_ts':venues._ts(m.get('close_time')),
                           'resolve_ts':venues._ts(m.get('settlement_ts') or m.get('settled_time') or m.get('close_time')),
                           'outcome': {'yes':1,'no':0}[m['result']], 'raw':{'series':name}}
                    store.upsert_market(con, rec)
                    try:
                        pts = venues.kalshi_history(rec)
                        store.insert_prices(con, rec['market_id'], pts)
                        store.log_collection(con,'kalshi','backfill',m['ticker'],True,'%s price points'%len(pts))
                        print(m['ticker'], len(pts), 'points', flush=True)
                    except Exception as e:
                        store.log_collection(con,'kalshi','backfill',m['ticker'],False,str(e))
                        print(m['ticker'], str(e)[:180], flush=True)
                    con.commit()


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--series', nargs='+', default=['KXFED','KXCPI','KXINX'])
    p.add_argument('--events',type=int,default=20)
    p.add_argument('--strikes',type=int,default=5)
    a=p.parse_args()
    if not 2<=a.events<=100 or not 2<=a.strikes<=20:
        p.error('events must be 2–100; strikes must be 2–20')
    run(a.series,a.events,a.strikes)

if __name__=='__main__':
    main()
