"""Venue adapters: turn each exchange's JSON into our common market shape.

Both adapters are written defensively. These are public APIs that change
without notice, so every field is pulled with a fallback and a market that
cannot be parsed is skipped with a reason rather than guessed at.
"""
import json
import math
import re
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, List, Optional, Tuple

from . import http
from .config import KALSHI_API, PAGE_LIMIT, POLYMARKET_CLOB, POLYMARKET_GAMMA


def _ts(value: Any) -> Optional[int]:
    """Parse the several date shapes these APIs use into unix seconds."""
    if value in (None, "", 0):
        return None
    if isinstance(value, (int, float)):
        v = float(value)
        return int(v / 1000) if v > 1e11 else int(v)
    s = str(value).strip().replace("Z", "+00:00")
    for fmt in (None, "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d %H:%M:%S%z", "%Y-%m-%d"):
        try:
            dt = datetime.fromisoformat(s) if fmt is None else datetime.strptime(s, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return int(dt.timestamp())
        except Exception:
            continue
    return None


def _num(value: Any) -> Optional[float]:
    try:
        if value in (None, ""):
            return None
        n = float(value)
        return n if math.isfinite(n) else None
    except Exception:
        return None


def _jlist(value: Any) -> List[Any]:
    """Polymarket returns some arrays as JSON-encoded strings."""
    if isinstance(value, list):
        return value
    if isinstance(value, str) and value.strip().startswith("["):
        try:
            return json.loads(value)
        except Exception:
            return []
    return []


# ---------------------------------------------------------------- Polymarket

def polymarket_outcome(m: Dict[str, Any]) -> Optional[int]:
    """A settled Polymarket binary pays 1 on the winning side. outcomePrices
    reads like ["1","0"] (YES won) or ["0","1"] (NO won). Anything ambiguous
    returns None so the market is dropped rather than mislabelled."""
    outcomes = [str(o).strip().lower() for o in _jlist(m.get("outcomes"))]
    prices = [_num(p) for p in _jlist(m.get("outcomePrices"))]
    if len(prices) != 2 or any(p is None for p in prices):
        return None
    if abs((prices[0] or 0) + (prices[1] or 0) - 1.0) > 0.02:
        return None
    hi = 0 if prices[0] > prices[1] else 1
    if abs(prices[hi] - 1.0) > 0.02:            # not a clean settlement
        return None
    if outcomes[:2] == ["yes", "no"]:
        return 1 if hi == 0 else 0
    if outcomes[:2] == ["no", "yes"]:
        return 1 if hi == 1 else 0
    return None  # non-binary labels cannot safely be called YES/NO


def polymarket_markets(offset: int = 0, limit: int = PAGE_LIMIT) -> List[Dict[str, Any]]:
    """One page of closed markets, newest resolution first."""
    data = http.get_json(POLYMARKET_GAMMA + "/markets", {
        "closed": "true", "limit": limit, "offset": offset,
        "order": "endDate", "ascending": "false",
    })
    rows = data if isinstance(data, list) else data.get("data", [])
    out = []
    for m in rows:
        native = str(m.get("id") or m.get("conditionId") or "")
        if not native:
            continue
        outcome = polymarket_outcome(m) if m.get("closed") is True else None
        tokens = _jlist(m.get("clobTokenIds"))
        events = m.get("events") or []
        event_id = None
        category = m.get("category")
        if events and isinstance(events, list) and isinstance(events[0], dict):
            event_id = str(events[0].get("id") or "") or None
            category = category or events[0].get("category")
        out.append({
            "market_id": "pm:" + native,
            "venue": "polymarket",
            "native_id": native,
            "event_id": ("pm-ev:" + event_id) if event_id else ("pm:" + native),
            "title": m.get("question") or m.get("title"),
            "category": category,
            "created_ts": _ts(m.get("startDate") or m.get("createdAt")),
            "close_ts": _ts(m.get("endDate")),
            "resolve_ts": _ts(m.get("closedTime") or m.get("endDate")),
            "outcome": outcome,
            "volume": _num(m.get("volumeNum") or m.get("volume")),
            "liquidity": _num(m.get("liquidityNum") or m.get("liquidity")),
            "raw": {"clobTokenIds": tokens, "yes_index": next((i for i, v in enumerate(_jlist(m.get("outcomes"))) if str(v).lower() == "yes"), 0), "slug": m.get("slug"),
                    "conditionId": m.get("conditionId")},
        })
    return out


def polymarket_history(market: Dict[str, Any]) -> List[Tuple[int, float, Optional[float]]]:
    """Full price path for the YES token. `market` is a store row or dict."""
    raw = market.get("raw")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            raw = {}
    tokens = _jlist((raw or {}).get("clobTokenIds"))
    if not tokens:
        return []
    data = http.get_json(POLYMARKET_CLOB + "/prices-history", {
        "market": str(tokens[(raw or {}).get("yes_index", 0)]), "interval": "max", "fidelity": 60,
    })
    hist = data.get("history", []) if isinstance(data, dict) else []
    pts = []
    for h in hist:
        t, p = _ts(h.get("t")), _num(h.get("p"))
        if t and p is not None:
            pts.append((t, p, None))
    return pts


# -------------------------------------------------------------------- Kalshi

def kalshi_markets(cursor: Optional[str] = None,
                   limit: int = 200) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    data = http.get_json(KALSHI_API + "/markets", {
        "status": "settled", "limit": limit, "cursor": cursor, "mve_filter": "exclude",
    })
    rows = data.get("markets", []) if isinstance(data, dict) else []
    out = []
    for m in rows:
        ticker = m.get("ticker")
        if not ticker:
            continue
        result = str(m.get("result") or "").strip().lower()
        outcome = 1 if result == "yes" else (0 if result == "no" else None)
        out.append({
            "market_id": "ks:" + ticker,
            "venue": "kalshi",
            "native_id": ticker,
            "event_id": "ks-ev:" + str(m.get("event_ticker") or ticker),
            "title": m.get("title") or m.get("subtitle"),
            "category": m.get("category"),
            "created_ts": _ts(m.get("open_time")),
            "close_ts": _ts(m.get("close_time")),
            "resolve_ts": _ts(m.get("settlement_ts") or m.get("settled_time") or m.get("close_time")),
            "outcome": outcome,
            "volume": _num(m.get("volume_fp") or m.get("volume")),
            "liquidity": _num(m.get("liquidity_dollars") or m.get("liquidity")),
            "raw": {"series": ticker.split("-")[0], "event_ticker": m.get("event_ticker")},
        })
    return out, (data.get("cursor") or None if isinstance(data, dict) else None)


def kalshi_history(market: Dict[str, Any]) -> List[Tuple[int, float, Optional[float]]]:
    """Hourly candlesticks between open and settle."""
    raw = market.get("raw")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            raw = {}
    ticker = market.get("native_id")
    series = (raw or {}).get("series") or (ticker or "").split("-")[0]
    start, end = market.get("created_ts"), market.get("resolve_ts")
    if not (ticker and series and start and end):
        return []
    pts: List[Tuple[int, float, Optional[float]]] = []
    # Kalshi caps the number of candles per call, so walk in 30-day windows.
    window = 30 * 24 * 3600
    cur = max(int(start), int(end) - 45 * 86400)
    while cur < int(end):
        stop = min(int(end), cur + window)
        archived = False
        try:
            data = http.get_json(
                "%s/series/%s/markets/%s/candlesticks" % (KALSHI_API, series, ticker),
                {"start_ts": cur, "end_ts": stop, "period_interval": 60})
        except http.HttpError as e:
            if e.status != 404:
                raise
            archived = True
            data = http.get_json(
                "%s/historical/markets/%s/candlesticks" % (KALSHI_API, ticker),
                {"start_ts": cur, "end_ts": stop, "period_interval": 60})
        for c in data.get("candlesticks", []):
            t = _ts(c.get("end_period_ts"))
            price = candle_price(c, plain_dollars=archived)
            v = _num(c.get("volume_fp") or c.get("volume"))
            if t and price is not None and 0 < price < 1:
                pts.append((t, price, v))
        cur = stop
    return pts


ADAPTERS = {
    "polymarket": {"history": polymarket_history},
    "kalshi": {"history": kalshi_history},
}


def candle_price(c, plain_dollars=False):
    """Close price in dollars (0-1).

    The live endpoint suffixes fixed-point fields with `_dollars`. The
    historical endpoint uses bare keys (`close`) but the values are already
    dollar strings like "0.9300", not cents. Only a legacy live payload has
    bare keys in cents, so the caller must say which endpoint answered.
    Reading archived dollars as cents divides every price by 100 and turns a
    91c contract into 0.9c.
    """
    unit = 1.0 if plain_dollars else 100.0
    price = c.get("price") or {}
    p = _num(price.get("close_dollars"))
    if p is not None:
        return p
    p = _num(price.get("close"))
    if p is not None:
        return p / unit
    bid, ask = c.get("yes_bid") or {}, c.get("yes_ask") or {}
    b, a = _num(bid.get("close_dollars")), _num(ask.get("close_dollars"))
    if b is not None and a is not None and 0 < b <= a < 1:
        return (a + b) / 2
    b, a = _num(bid.get("close")), _num(ask.get("close"))
    if b is None or a is None:
        return None
    b, a = b / unit, a / unit
    return (a + b) / 2 if 0 < b <= a < 1 else None
