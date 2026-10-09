"""SQLite store. Append-only in spirit: collection runs add, never overwrite
a resolved outcome, so the corpus only grows as more markets settle.
"""
import json
import sqlite3
import time
from contextlib import contextmanager
from typing import Any, Dict, Iterable, List, Optional

from .config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS markets (
  market_id    TEXT PRIMARY KEY,
  venue        TEXT NOT NULL,
  native_id    TEXT NOT NULL,
  event_id     TEXT,
  title        TEXT,
  category     TEXT,
  created_ts   INTEGER,
  close_ts     INTEGER,
  resolve_ts   INTEGER,
  outcome      INTEGER,
  volume       REAL,
  liquidity    REAL,
  raw          TEXT,
  collected_ts INTEGER
);
CREATE INDEX IF NOT EXISTS ix_markets_resolve ON markets(resolve_ts);
CREATE INDEX IF NOT EXISTS ix_markets_venue   ON markets(venue);
CREATE INDEX IF NOT EXISTS ix_markets_event   ON markets(event_id);

CREATE TABLE IF NOT EXISTS prices (
  market_id TEXT NOT NULL,
  ts        INTEGER NOT NULL,
  price     REAL NOT NULL,
  volume    REAL,
  PRIMARY KEY (market_id, ts)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS collect_log (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  venue       TEXT,
  kind        TEXT,
  cursor      TEXT,
  last_run_ts INTEGER,
  ok          INTEGER,
  note        TEXT
);

CREATE TABLE IF NOT EXISTS models (
  version     TEXT PRIMARY KEY,
  created_ts  INTEGER,
  algo        TEXT,
  provenance  TEXT,          -- 'live' | 'synthetic'  (never mix them)
  n_train     INTEGER,
  n_test      INTEGER,
  metrics     TEXT,
  params      TEXT,
  is_champion INTEGER DEFAULT 0,
  path        TEXT
);

CREATE TABLE IF NOT EXISTS runs (
  run_id      TEXT PRIMARY KEY,
  started_ts  INTEGER,
  finished_ts INTEGER,
  stage       TEXT,
  status      TEXT,
  detail      TEXT
);
"""


@contextmanager
def connect(path=None):
    con = sqlite3.connect(str(path or DB_PATH), timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    try:
        yield con
        con.commit()
    finally:
        con.close()


def init(path=None) -> None:
    with connect(path) as con:
        con.executescript(SCHEMA)


def upsert_market(con, m: Dict[str, Any]) -> None:
    """Insert or refresh a market. An outcome already recorded is never
    reverted to NULL by a later partial fetch."""
    con.execute(
        """
        INSERT INTO markets (market_id, venue, native_id, event_id, title, category,
                             created_ts, close_ts, resolve_ts, outcome, volume,
                             liquidity, raw, collected_ts)
        VALUES (:market_id,:venue,:native_id,:event_id,:title,:category,
                :created_ts,:close_ts,:resolve_ts,:outcome,:volume,
                :liquidity,:raw,:collected_ts)
        ON CONFLICT(market_id) DO UPDATE SET
          event_id   = COALESCE(excluded.event_id, markets.event_id),
          title      = COALESCE(excluded.title, markets.title),
          category   = COALESCE(excluded.category, markets.category),
          created_ts = COALESCE(excluded.created_ts, markets.created_ts),
          close_ts   = COALESCE(excluded.close_ts, markets.close_ts),
          resolve_ts = COALESCE(excluded.resolve_ts, markets.resolve_ts),
          outcome    = COALESCE(excluded.outcome, markets.outcome),
          volume     = COALESCE(excluded.volume, markets.volume),
          liquidity  = COALESCE(excluded.liquidity, markets.liquidity),
          raw        = COALESCE(excluded.raw, markets.raw),
          collected_ts = excluded.collected_ts
        """,
        {
            "market_id": m["market_id"], "venue": m["venue"],
            "native_id": m["native_id"], "event_id": m.get("event_id"),
            "title": m.get("title"), "category": m.get("category"),
            "created_ts": m.get("created_ts"), "close_ts": m.get("close_ts"),
            "resolve_ts": m.get("resolve_ts"), "outcome": m.get("outcome"),
            "volume": m.get("volume"), "liquidity": m.get("liquidity"),
            "raw": json.dumps(m.get("raw"))[:200000] if m.get("raw") is not None else None,
            "collected_ts": int(time.time()),
        },
    )


def insert_prices(con, market_id: str, points: Iterable) -> int:
    rows = [(market_id, int(ts), float(p), (float(v) if v is not None else None))
            for ts, p, v in points]
    if not rows:
        return 0
    con.executemany(
        "INSERT OR IGNORE INTO prices (market_id, ts, price, volume) VALUES (?,?,?,?)",
        rows,
    )
    return len(rows)


def has_prices(con, market_id: str) -> bool:
    r = con.execute("SELECT 1 FROM prices WHERE market_id=? LIMIT 1", (market_id,)).fetchone()
    return r is not None


def markets_needing_prices(con, venue: Optional[str] = None, limit: int = 500) -> List[sqlite3.Row]:
    q = """SELECT m.* FROM markets m
           WHERE m.outcome IS NOT NULL
             AND NOT EXISTS (SELECT 1 FROM prices p WHERE p.market_id = m.market_id)"""
    args: List[Any] = []
    if venue:
        q += " AND m.venue = ?"
        args.append(venue)
    q += " ORDER BY m.resolve_ts DESC LIMIT ?"
    args.append(limit)
    return con.execute(q, args).fetchall()


def resolved_markets(con) -> List[sqlite3.Row]:
    return con.execute(
        """SELECT * FROM markets
           WHERE outcome IS NOT NULL AND resolve_ts IS NOT NULL
           ORDER BY resolve_ts ASC"""
    ).fetchall()


def price_path(con, market_id: str) -> List[tuple]:
    return [(r["ts"], r["price"], r["volume"]) for r in con.execute(
        "SELECT ts, price, volume FROM prices WHERE market_id=? ORDER BY ts ASC",
        (market_id,))]


def log_collection(con, venue: str, kind: str, cursor: str, ok: bool, note: str = "") -> None:
    con.execute(
        "INSERT INTO collect_log (venue, kind, cursor, last_run_ts, ok, note) VALUES (?,?,?,?,?,?)",
        (venue, kind, cursor, int(time.time()), 1 if ok else 0, note[:500]),
    )


def last_cursor(con, venue: str, kind: str) -> Optional[str]:
    r = con.execute(
        """SELECT cursor FROM collect_log WHERE venue=? AND kind=? AND ok=1
           ORDER BY id DESC LIMIT 1""", (venue, kind)).fetchone()
    return r["cursor"] if r else None


def register_model(con, rec: Dict[str, Any]) -> None:
    con.execute(
        """INSERT OR REPLACE INTO models
           (version, created_ts, algo, provenance, n_train, n_test, metrics, params, is_champion, path)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (rec["version"], int(time.time()), rec["algo"], rec["provenance"],
         rec["n_train"], rec["n_test"], json.dumps(rec["metrics"]),
         json.dumps(rec.get("params", {})), 0, rec["path"]),
    )


def champion(con, provenance: str = "live") -> Optional[sqlite3.Row]:
    return con.execute(
        "SELECT * FROM models WHERE is_champion=1 AND provenance=? LIMIT 1",
        (provenance,)).fetchone()


def promote(con, version: str, provenance: str) -> None:
    con.execute("UPDATE models SET is_champion=0 WHERE provenance=?", (provenance,))
    con.execute("UPDATE models SET is_champion=1 WHERE version=?", (version,))


def stats(con) -> Dict[str, Any]:
    def one(q, *a):
        r = con.execute(q, a).fetchone()
        return r[0] if r else 0
    return {
        "markets": one("SELECT COUNT(*) FROM markets"),
        "resolved": one("SELECT COUNT(*) FROM markets WHERE outcome IS NOT NULL"),
        "with_prices": one("SELECT COUNT(DISTINCT market_id) FROM prices"),
        "price_points": one("SELECT COUNT(*) FROM prices"),
        "polymarket": one("SELECT COUNT(*) FROM markets WHERE venue='polymarket'"),
        "kalshi": one("SELECT COUNT(*) FROM markets WHERE venue='kalshi'"),
        "earliest_resolve": one("SELECT MIN(resolve_ts) FROM markets WHERE outcome IS NOT NULL"),
        "latest_resolve": one("SELECT MAX(resolve_ts) FROM markets WHERE outcome IS NOT NULL"),
        "models": one("SELECT COUNT(*) FROM models"),
    }
