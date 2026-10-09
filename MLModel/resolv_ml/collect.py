"""Collection orchestrator. Incremental, resumable, and safe to re-run:
markets already stored are refreshed, price paths already stored are skipped.

Run it as often as you like. Every run should end with more resolved markets
in the store than it started with, which is what makes the model keep learning.
"""
import argparse
import sys
import time
from typing import Optional

from . import http, store, venues
from .http import HttpError
from .config import KALSHI_API, PAGE_LIMIT, POLYMARKET_CLOB, POLYMARKET_GAMMA


def log(msg: str) -> None:
    print("[collect %s] %s" % (time.strftime("%H:%M:%S"), msg), flush=True)


PREFLIGHT = {
    "polymarket": POLYMARKET_GAMMA + "/markets?limit=1",
    "kalshi": KALSHI_API + "/markets?limit=1",
}


def preflight(venue: str) -> bool:
    ok, detail = http.reachable(PREFLIGHT[venue])
    log("%s reachable: %s (%s)" % (venue, ok, detail))
    return ok


def collect_polymarket(con, pages: int = 10) -> dict:
    if not preflight("polymarket"):
        store.log_collection(con, "polymarket", "markets", "", False, "unreachable")
        return {"seen": 0, "labelled": 0, "unreachable": True}
    added = kept = 0
    for i in range(pages):
        offset = i * PAGE_LIMIT
        try:
            rows = venues.polymarket_markets(offset=offset, limit=PAGE_LIMIT)
        except HttpError as e:
            log("polymarket markets page %d failed: %s" % (i, e))
            store.log_collection(con, "polymarket", "markets", str(offset), False, str(e))
            break
        except Exception as e:
            log("polymarket unreachable: %s" % e)
            store.log_collection(con, "polymarket", "markets", str(offset), False, repr(e))
            break
        if not rows:
            break
        for m in rows:
            if m["outcome"] is None:
                continue
            store.upsert_market(con, m)
            added += 1
        kept += len(rows)
        con.commit()
        log("polymarket page %d: %d rows, %d labelled" % (i, len(rows), added))
    store.log_collection(con, "polymarket", "markets", str(pages * PAGE_LIMIT), True,
                         "%d labelled" % added)
    return {"seen": kept, "labelled": added}


def collect_kalshi(con, pages: int = 10) -> dict:
    if not preflight("kalshi"):
        store.log_collection(con, "kalshi", "markets", "", False, "unreachable")
        return {"seen": 0, "labelled": 0, "unreachable": True}
    cursor: Optional[str] = None
    added = seen = 0
    for i in range(pages):
        try:
            rows, cursor = venues.kalshi_markets(cursor=cursor, limit=200)
        except HttpError as e:
            log("kalshi markets page %d failed: %s" % (i, e))
            store.log_collection(con, "kalshi", "markets", cursor or "", False, str(e))
            break
        except Exception as e:
            log("kalshi unreachable: %s" % e)
            store.log_collection(con, "kalshi", "markets", cursor or "", False, repr(e))
            break
        if not rows:
            break
        for m in rows:
            if m["outcome"] is None:
                continue
            store.upsert_market(con, m)
            added += 1
        seen += len(rows)
        con.commit()
        log("kalshi page %d: %d rows, %d labelled" % (i, len(rows), added))
        if not cursor:
            break
    store.log_collection(con, "kalshi", "markets", cursor or "", True, "%d labelled" % added)
    return {"seen": seen, "labelled": added}


def collect_histories(con, venue: Optional[str] = None, limit: int = 300) -> dict:
    """Fetch price paths for resolved markets that do not have one yet."""
    todo = store.markets_needing_prices(con, venue=venue, limit=limit)
    log("price paths to fetch: %d" % len(todo))
    if todo and not any(preflight(v) for v in ({venue} if venue else {"polymarket", "kalshi"})):
        log("no venue reachable, skipping price paths")
        return {"paths": 0, "empty": 0, "failed": 0, "points": 0, "unreachable": True}
    ok = empty = failed = points = 0
    for i, row in enumerate(todo):
        m = dict(row)
        fn = venues.ADAPTERS.get(m["venue"], {}).get("history")
        if not fn:
            continue
        try:
            pts = fn(m)
        except HttpError as e:
            failed += 1
            if e.status in (401, 403):
                log("auth required for %s history, stopping this venue" % m["venue"])
                break
            continue
        except Exception:
            failed += 1
            continue
        if not pts:
            empty += 1
            continue
        points += store.insert_prices(con, m["market_id"], pts)
        ok += 1
        if i % 25 == 0:
            con.commit()
            log("  %d/%d paths, %d points" % (i + 1, len(todo), points))
    con.commit()
    return {"paths": ok, "empty": empty, "failed": failed, "points": points}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Collect resolved market history")
    ap.add_argument("--venue", choices=["polymarket", "kalshi", "both"], default="both")
    ap.add_argument("--pages", type=int, default=10, help="pages of markets per venue")
    ap.add_argument("--histories", type=int, default=300, help="max price paths per run")
    ap.add_argument("--skip-histories", action="store_true")
    args = ap.parse_args(argv)

    store.init()
    with store.connect() as con:
        before = store.stats(con)
        log("before: %s" % before)

        if args.venue in ("polymarket", "both"):
            log("polymarket markets: %s" % collect_polymarket(con, args.pages))
        if args.venue in ("kalshi", "both"):
            log("kalshi markets: %s" % collect_kalshi(con, args.pages))
        if not args.skip_histories:
            v = None if args.venue == "both" else args.venue
            log("histories: %s" % collect_histories(con, venue=v, limit=args.histories))

        after = store.stats(con)
        log("after:  %s" % after)
        gained = after["resolved"] - before["resolved"]
        log("net new resolved markets this run: %d" % gained)
        if after["resolved"] == 0:
            log("NOTHING COLLECTED. The venue APIs were not reachable from here.")
            log("No model can be trained on live data until this run succeeds.")
            log("Check the network, then re-run. Nothing else needs changing.")
            return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
