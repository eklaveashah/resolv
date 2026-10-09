"""The dashboard server: a JSON API over the store plus the static files in ui/.

Standard library only. http.server, sqlite3, json, argparse. joblib and numpy
are imported inside the scoring path and nowhere else, so the dashboard starts
and every endpoint answers even when the venv has no scientific stack.

Two rules run through the whole file:

  1. Nothing is invented. A number the store does not hold comes back as null
     and the page renders "n/a". An empty store is a valid state, not an error,
     and every endpoint returns well formed JSON in it.
  2. Expected conditions (no model yet, unknown version, unknown market) are
     HTTP 200 with an "error" or "ok": false field. 404 is only for a route
     that does not exist.

Run it:

    python -m resolv_ml.ui_server --port 8901 [--open]
    python -m resolv_ml.ui_server --selftest      # checks, including traversal
"""
import argparse
import json
import math
import os
import re
import subprocess
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import store
from .config import DB_PATH, MIN_TICKS, MODEL_DIR, REPORT_DIR, ROOT

# ------------------------------------------------------------------ constants

HOST = "127.0.0.1"          # local tool. Never bind anything else.
DEFAULT_PORT = 8901

# Static root. Every served path must resolve inside this directory.
UI_DIR = (ROOT / "ui").resolve()

LAUNCHD_LABEL = "trade.resolv.ml.train"
LAUNCHD_PLIST = Path.home() / "Library" / "LaunchAgents" / (LAUNCHD_LABEL + ".plist")

# A version string names a directory under MODEL_DIR, so it is checked before
# it is ever joined to a path.
VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".ico": "image/x-icon",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".txt": "text/plain; charset=utf-8",
    ".md": "text/plain; charset=utf-8",
}

STAT_KEYS = ["markets", "resolved", "with_prices", "price_points", "polymarket",
             "kalshi", "earliest_resolve", "latest_resolve", "models"]

# Model metadata is read on every poll, so it is cached by version and keyed on
# the file's mtime and size. A retrain writes a new directory, and a rewritten
# meta.json changes mtime, so the cache can never serve a stale shape.
_cache_lock = threading.Lock()
_meta_cache = {}        # version -> (key, meta dict)
_coef_cache = {}        # version -> (key, features payload)


# ------------------------------------------------------------------ small helpers

def _finite(obj, depth=0):
    """Replace NaN and Infinity with null so the payload is parseable JSON.

    A non-finite metric is not a number worth showing, so it renders "n/a"
    rather than crashing JSON.parse in the browser.
    """
    if depth > 12:
        return None
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {str(k): _finite(v, depth + 1) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_finite(v, depth + 1) for v in obj]
    return obj


def _default(o):
    if isinstance(o, Path):
        return str(o)
    if isinstance(o, bytes):
        return o.decode("utf-8", "replace")
    return str(o)


def _row(r):
    return {k: r[k] for k in r.keys()}


def _int(v, fallback=None):
    try:
        return int(v)
    except (TypeError, ValueError):
        return fallback


def _metrics_of(row):
    try:
        return json.loads(row["metrics"] or "{}")
    except (TypeError, ValueError):
        return {}


def _model_obj(row):
    """The compact model record the top bar and the tables read."""
    return {
        "version": row["version"],
        "algo": row["algo"],
        "provenance": row["provenance"],
        "created_ts": row["created_ts"],
        "n_train": row["n_train"],
        "n_test": row["n_test"],
        "metrics": _metrics_of(row),
    }


def _tail(path, n_lines, max_bytes=262144):
    """Last n_lines of a file, reading only the tail of it."""
    with open(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        size = f.tell()
        back = min(size, max_bytes)
        f.seek(size - back)
        data = f.read()
    lines = data.decode("utf-8", "replace").splitlines()
    if back < size and lines:
        lines = lines[1:]           # the first line is a fragment, drop it
    return lines[-n_lines:]


def _like(term):
    """Escape a user search term for SQL LIKE."""
    out = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return "%" + out + "%"


def _percentile(sorted_vals, q):
    """Nearest-rank percentile. None on an empty list, never a made up zero."""
    if not sorted_vals:
        return None
    k = max(0, min(len(sorted_vals) - 1, int(math.ceil(q * len(sorted_vals))) - 1))
    return sorted_vals[k]


# ------------------------------------------------------------------ model files

def _meta_file(version):
    """Locate meta.json for a version: the path the store recorded first, then
    the conventional directory under MODEL_DIR. None if there is no such file."""
    if not version or not VERSION_RE.match(version):
        return None
    candidates = []
    try:
        with store.connect() as con:
            r = con.execute("SELECT path FROM models WHERE version=?", (version,)).fetchone()
        if r and r["path"]:
            candidates.append(Path(r["path"]) / "meta.json")
    except Exception:
        pass                         # store unreadable, fall through to the disk layout
    candidates.append(MODEL_DIR / version / "meta.json")
    for c in candidates:
        try:
            if c.is_file():
                return c
        except OSError:
            continue
    return None


def _cache_key(path):
    st = path.stat()
    return (str(path), st.st_mtime_ns, st.st_size)


def load_meta(version):
    """meta.json for a version, cached on version plus file mtime and size."""
    p = _meta_file(version)
    if p is None:
        return None
    try:
        key = _cache_key(p)
    except OSError:
        return None
    with _cache_lock:
        hit = _meta_cache.get(version)
    if hit and hit[0] == key:
        return hit[1]
    try:
        meta = json.loads(p.read_text())
    except (OSError, ValueError):
        return None
    with _cache_lock:
        _meta_cache[version] = (key, meta)
    return meta


def _champion_version(con=None):
    """Live champion if there is one, otherwise the synthetic champion, else
    the newest model of any kind. None when nothing has been trained."""
    def pick(c):
        for prov in ("live", "synthetic"):
            row = store.champion(c, prov)
            if row is not None:
                return row["version"]
        row = c.execute("SELECT version FROM models ORDER BY created_ts DESC LIMIT 1").fetchone()
        return row["version"] if row else None

    if con is not None:
        return pick(con)
    try:
        with store.connect() as c:
            return pick(c)
    except Exception:
        return None


def _estimator_coefficients(est):
    """(kind, coefficients, intercept, note) for a fitted estimator.

    Linear models expose coef_ directly. A Pipeline hides it on its last step,
    and if an earlier step standardises the columns the coefficients are in
    standardised units, which the note says out loud. Anything else is opaque
    and returns null rather than a fabricated importance score.
    """
    inner, note = est, None
    steps = getattr(est, "steps", None)
    if steps:
        try:
            inner = steps[-1][1]
            if any(hasattr(s[1], "scale_") or hasattr(s[1], "mean_") for s in steps[:-1]):
                note = ("coefficients are in standardised units: the pipeline "
                        "rescales every column before fitting")
        except Exception:
            return "opaque", None, None, None

    coef = getattr(inner, "coef_", None)
    if coef is None:
        return "opaque", None, None, None
    try:
        arr = coef
        first = arr[0]
        if hasattr(first, "__len__"):
            arr = first
        values = [float(v) for v in arr]
    except Exception:
        return "opaque", None, None, None

    intercept = None
    try:
        ic = getattr(inner, "intercept_", None)
        if ic is not None:
            intercept = float(ic[0]) if hasattr(ic, "__len__") else float(ic)
    except Exception:
        intercept = None
    return "linear", values, intercept, note


# ------------------------------------------------------------------ schedule

def _plist_interval_hours():
    """StartInterval from the launchd plist, in whole hours. None if the file
    is missing, unreadable, or the interval is not a whole number of hours."""
    try:
        import plistlib
        with open(LAUNCHD_PLIST, "rb") as f:
            data = plistlib.load(f)
        seconds = int(data.get("StartInterval") or 0)
    except Exception:
        return None
    if seconds >= 3600 and seconds % 3600 == 0:
        return seconds // 3600
    return None


def schedule_info():
    """Is the launchd job registered, and how did its last run exit.

    launchctl list prints three tab separated columns: PID, last exit status,
    label. A PID of "-" means the job is idle between runs. Any failure here is
    reported as not installed rather than allowed to take the page down.
    """
    info = {"installed": False, "interval_hours": None, "last_exit": None}
    try:
        p = subprocess.run(["launchctl", "list"], stdout=subprocess.PIPE,
                           stderr=subprocess.DEVNULL, timeout=5)
        if p.returncode != 0:
            return info
        text = p.stdout.decode("utf-8", "replace")
    except Exception:
        return info

    for line in text.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            parts = line.split()
        if len(parts) >= 3 and parts[-1].strip() == LAUNCHD_LABEL:
            info["installed"] = True
            info["last_exit"] = _int(parts[1].strip())
            break
    if info["installed"]:
        info["interval_hours"] = _plist_interval_hours()
    return info


# ------------------------------------------------------------------ endpoints

def api_overview(q):
    out = {
        "store": {k: None for k in STAT_KEYS},
        "champion": {"live": None, "synthetic": None},
        "schedule": schedule_info(),
        "runtime": {"db_path": str(DB_PATH), "model_dir": str(MODEL_DIR),
                    "report_dir": str(REPORT_DIR)},
        "generated": int(time.time()),
    }
    try:
        with store.connect() as con:
            out["store"] = store.stats(con)
            for prov in ("live", "synthetic"):
                row = store.champion(con, prov)
                out["champion"][prov] = _model_obj(row) if row is not None else None
    except Exception as e:
        # Stats stay null so the page shows n/a rather than a fabricated zero.
        out["error"] = "store unreadable: %s" % e
    return out


def api_models(q):
    try:
        with store.connect() as con:
            rows = con.execute(
                "SELECT * FROM models ORDER BY created_ts DESC").fetchall()
    except Exception as e:
        return {"error": "store unreadable: %s" % e}
    out = []
    for r in rows:
        m = _model_obj(r)
        m["is_champion"] = bool(r["is_champion"])
        out.append(m)
    return out


def api_model(q):
    version = (q.get("version") or "").strip() or _champion_version()
    if not version:
        return {"error": "no trained model yet"}
    if not VERSION_RE.match(version):
        return {"error": "unknown version"}
    meta = load_meta(version)
    if meta is None:
        return {"error": "no meta.json on disk for version %s" % version}
    return meta


def api_features(q):
    """Feature names and, for a linear estimator, the coefficients aligned to
    the columns that model was actually fitted on. Never the full feature list
    when the model used a subset of it."""
    version = (q.get("version") or "").strip() or _champion_version()
    base = {"names": [], "coefficients": None, "kind": "opaque"}
    if not version:
        base["error"] = "no trained model yet"
        return base
    if not VERSION_RE.match(version):
        base["error"] = "unknown version"
        return base

    meta = load_meta(version)
    if meta is None:
        base["error"] = "no meta.json on disk for version %s" % version
        return base

    names = list(meta.get("columns") or [])
    base["names"] = names
    base["version"] = version

    meta_file = _meta_file(version)
    if meta_file is None:
        base["error"] = "meta.json disappeared for version %s" % version
        return base

    model_file = meta_file.parent / "model.joblib"
    if not model_file.is_file():
        base["error"] = "model.joblib missing, coefficients cannot be read"
        return base

    try:
        key = _cache_key(model_file)
    except OSError:
        key = None
    if key is not None:
        with _cache_lock:
            hit = _coef_cache.get(version)
        if hit and hit[0] == key:
            return hit[1]

    try:
        import joblib                      # lazy: only the scoring path needs it
        bundle = joblib.load(model_file)
        est = bundle["estimator"] if isinstance(bundle, dict) else bundle
    except Exception as e:
        base["error"] = "could not load the estimator: %s: %s" % (type(e).__name__, e)
        return base

    kind, coefs, intercept, note = _estimator_coefficients(est)
    out = dict(base)
    out["kind"] = kind
    out["intercept"] = intercept
    if note:
        out["note"] = note
    if coefs is not None and len(coefs) != len(names):
        # Refuse to pair numbers with the wrong labels. The standardisation
        # note, if there was one, is still true, so it is kept alongside.
        out["coefficients"] = None
        mismatch = ("the estimator has %d coefficient(s) but meta.json "
                    "records %d column(s), so they are not shown"
                    % (len(coefs), len(names)))
        out["note"] = (out["note"] + ". " + mismatch) if out.get("note") else mismatch
    else:
        out["coefficients"] = coefs

    if key is not None:
        with _cache_lock:
            _coef_cache[version] = (key, out)
    return out


def api_coverage(q):
    # Counts start null. A zero is a fact about the store, so it is only
    # written once a query has actually come back.
    out = {"by_month": [],
           "venues": {"polymarket": None, "kalshi": None, "synthetic": None},
           "points_per_market": {"p50": None, "p90": None, "max": None}}
    try:
        with store.connect() as con:
            rows = con.execute(
                """SELECT strftime('%Y-%m', resolve_ts, 'unixepoch') AS month,
                          COUNT(*) AS n
                   FROM markets
                   WHERE outcome IS NOT NULL AND resolve_ts IS NOT NULL
                   GROUP BY month ORDER BY month ASC""").fetchall()
            out["by_month"] = [{"month": r["month"], "n": r["n"]}
                               for r in rows if r["month"]]

            venues = {"polymarket": 0, "kalshi": 0, "synthetic": 0}
            for r in con.execute("SELECT venue, COUNT(*) AS n FROM markets GROUP BY venue"):
                venues[r["venue"] or "unknown"] = r["n"]
            out["venues"] = venues

            counts = [r[0] for r in con.execute(
                "SELECT COUNT(*) FROM prices GROUP BY market_id")]
    except Exception as e:
        out["error"] = "store unreadable: %s" % e
        return out

    counts.sort()
    out["points_per_market"] = {
        "p50": _percentile(counts, 0.50),
        "p90": _percentile(counts, 0.90),
        "max": counts[-1] if counts else None,
    }
    return out


def api_markets(q):
    limit = _int(q.get("limit"), 200) or 200
    limit = max(1, min(2000, limit))
    term = (q.get("q") or "").strip()

    sql = ["""SELECT m.market_id, m.title, m.venue, m.category, m.resolve_ts, m.outcome,
                     (SELECT COUNT(*) FROM prices p WHERE p.market_id = m.market_id) AS n_points
              FROM markets m
              WHERE m.outcome IS NOT NULL
                AND EXISTS (SELECT 1 FROM prices p WHERE p.market_id = m.market_id)"""]
    args = []
    if term:
        sql.append("""AND (m.title LIKE ? ESCAPE '\\'
                        OR m.market_id LIKE ? ESCAPE '\\'
                        OR m.category LIKE ? ESCAPE '\\')""")
        args += [_like(term)] * 3
    sql.append("ORDER BY m.resolve_ts DESC LIMIT ?")
    args.append(limit)

    try:
        with store.connect() as con:
            rows = con.execute(" ".join(sql), args).fetchall()
    except Exception as e:
        return {"error": "store unreadable: %s" % e}
    return [{"market_id": r["market_id"], "title": r["title"], "venue": r["venue"],
             "category": r["category"], "resolve_ts": r["resolve_ts"],
             "outcome": r["outcome"], "n_points": r["n_points"]} for r in rows]


def api_market(q):
    market_id = (q.get("id") or "").strip()
    if not market_id:
        return {"error": "id is required"}
    try:
        with store.connect() as con:
            row = con.execute("SELECT * FROM markets WHERE market_id=?",
                              (market_id,)).fetchone()
            if row is None:
                return {"error": "unknown market"}
            path = [[r["ts"], r["price"]] for r in con.execute(
                "SELECT ts, price FROM prices WHERE market_id=? ORDER BY ts ASC",
                (market_id,))]
    except Exception as e:
        return {"error": "store unreadable: %s" % e}

    market = _row(row)
    if market.get("raw"):
        try:
            market["raw"] = json.loads(market["raw"])
        except (TypeError, ValueError):
            pass                     # keep the original string, do not discard it
    return {"market": market, "path": path}


def api_score(q):
    """What the champion would have said at a moment before resolution, using
    only the prices that existed then. predict.score does the work, so the
    dashboard and the CLI can never drift apart."""
    market_id = (q.get("id") or "").strip()
    if not market_id:
        return {"ok": False, "reason": "id is required"}
    raw_at = q.get("at_ts")
    at_ts = _int(raw_at)
    if at_ts is None and (raw_at or "").strip():
        # Falling back to the last tick here would score a moment the caller
        # never asked about and report it as if it had been requested.
        return {"ok": False,
                "reason": "at_ts must be an integer unix timestamp, got %r" % raw_at}
    version = (q.get("version") or "").strip() or None
    if version and not VERSION_RE.match(version):
        return {"ok": False, "reason": "unknown version"}

    try:
        with store.connect() as con:
            row = con.execute("SELECT * FROM markets WHERE market_id=?",
                              (market_id,)).fetchone()
            if row is None:
                return {"ok": False, "reason": "unknown market"}
            pts = store.price_path(con, market_id)
            provenance = None
            for prov in ("live", "synthetic"):
                if store.champion(con, prov) is not None:
                    provenance = prov
                    break
    except Exception as e:
        return {"ok": False, "reason": "store unreadable: %s" % e}

    if not pts:
        return {"ok": False, "reason": "no price path stored for this market"}
    if provenance is None and version is None:
        return {"ok": False, "reason": "no trained model yet"}

    # predict.score cuts at the last stored tick when at_ts is absent or zero.
    # Mirror that test exactly, so cut_ts always names the cut really used
    # rather than the one that was asked for.
    cut = int(at_ts) if at_ts else max(t for t, _, _ in pts)

    try:
        from . import predict          # lazy: pulls in joblib and numpy
        out = predict.score(pts, _row(row), at_ts=at_ts, version=version,
                            provenance=provenance or "live")
    except Exception as e:
        return {"ok": False, "reason": "%s: %s" % (type(e).__name__, e),
                "cut_ts": cut}

    out["cut_ts"] = cut
    resolve_ts = row["resolve_ts"]
    if not out.get("ok"):
        # features.build refuses for three separate reasons and predict reports
        # every one of them as a thin price path. Where the real cause is
        # knowable here it goes in "reason", the field the page actually shows,
        # because a wrong explanation on screen is as dishonest as a wrong
        # number. The message predict sent is kept in "detail".
        before = sum(1 for t, _, _ in pts if t <= cut)
        true_reason = None
        if resolve_ts and cut >= resolve_ts:
            true_reason = ("the cut is at or after this market settled, so there "
                           "is no forecast to make. Move it earlier.")
        elif before < MIN_TICKS:
            true_reason = ("only %d tick(s) exist at or before that moment and the "
                           "model needs %d. Move the cut later."
                           % (before, MIN_TICKS))
        if true_reason:
            original = out.get("reason")
            if original and original != true_reason:
                out["detail"] = original
            out["reason"] = true_reason
            out["hint"] = true_reason
    return out


def api_log(q):
    out = {"training": [], "collect": [], "training_source": None}
    # training.log is what the daemon writes. launchd.out.log carries the same
    # cycles plus the trainer's own output, so it is the fallback.
    for name in ("training.log", "launchd.out.log"):
        p = REPORT_DIR / name
        try:
            if p.is_file() and p.stat().st_size > 0:
                out["training"] = _tail(p, 60)
                out["training_source"] = name
                break
        except OSError:
            continue

    try:
        with store.connect() as con:
            rows = con.execute(
                """SELECT venue, kind, last_run_ts, ok, note FROM collect_log
                   ORDER BY id DESC LIMIT 20""").fetchall()
        out["collect"] = [{"venue": r["venue"], "kind": r["kind"],
                           "last_run_ts": r["last_run_ts"], "ok": bool(r["ok"]),
                           "note": r["note"] or ""} for r in rows]
    except Exception as e:
        out["error"] = "store unreadable: %s" % e
    return out


ROUTES = {
    "/api/overview": api_overview,
    "/api/models": api_models,
    "/api/model": api_model,
    "/api/features": api_features,
    "/api/coverage": api_coverage,
    "/api/markets": api_markets,
    "/api/market": api_market,
    "/api/score": api_score,
    "/api/log": api_log,
}


# ------------------------------------------------------------------ http

class Handler(BaseHTTPRequestHandler):
    server_version = "resolv-ml-ui"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    # -------------------------------------------------------------- plumbing
    def version_string(self):
        return self.server_version

    def log_message(self, fmt, *args):
        if self.server.quiet:
            return
        sys.stderr.write("%s %s\n" % (time.strftime("%H:%M:%S"), fmt % args))

    def _send(self, code, body, ctype):
        try:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            # A HEAD reply carries the headers and no body. Writing one anyway
            # would desynchronise the keep-alive connection.
            if body and self.command != "HEAD":
                self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass                     # the browser navigated away mid response

    def _json(self, obj, code=200):
        try:
            body = json.dumps(_finite(obj), default=_default).encode("utf-8")
        except Exception as e:
            body = json.dumps({"error": "could not serialise the response: %s" % e}
                              ).encode("utf-8")
            code = 500
        self._send(code, body, "application/json; charset=utf-8")

    # -------------------------------------------------------------- routing
    def do_GET(self):
        parsed = urllib.parse.urlsplit(self.path)
        path = urllib.parse.unquote(parsed.path)
        if "\x00" in path:
            return self._json({"error": "bad path"}, 400)

        if path.startswith("/api/"):
            fn = ROUTES.get(path)
            if fn is None:
                return self._json({"error": "no such endpoint: %s" % path}, 404)
            q = {k: v[0] for k, v in
                 urllib.parse.parse_qs(parsed.query, keep_blank_values=True).items()}
            try:
                return self._json(fn(q))
            except Exception as e:
                return self._json({"error": "%s: %s" % (type(e).__name__, e)}, 500)

        return self._static(path)

    def do_HEAD(self):
        self.do_GET()

    # -------------------------------------------------------------- static
    def _static(self, path):
        """Serve a file from UI_DIR and nothing else.

        The check is on the fully resolved path, so ../ segments, percent
        encoded ones, and symlinks that point out of the directory are all
        refused by the same test.
        """
        rel = path.lstrip("/")
        if rel in ("", "index.htm"):
            rel = "index.html"

        target = UI_DIR / rel
        try:
            resolved = target.resolve()
        except OSError:
            return self._json({"error": "not found"}, 404)

        try:
            resolved.relative_to(UI_DIR)
        except ValueError:
            return self._json({"error": "path outside the ui directory"}, 403)

        if not resolved.is_file():
            return self._json({"error": "not found: %s" % rel}, 404)

        try:
            body = resolved.read_bytes()
        except OSError as e:
            return self._json({"error": "could not read %s: %s" % (rel, e)}, 500)

        ctype = CONTENT_TYPES.get(resolved.suffix.lower(), "application/octet-stream")
        self._send(200, body, ctype)


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    quiet = False


# ------------------------------------------------------------------ selftest

def _selftest(port=0):
    """Prove the traversal guard holds and every endpoint answers.

    Requests go through http.client with the raw path, because curl and the
    browser normalise ../ away before it ever reaches the server.
    """
    import http.client

    srv = Server((HOST, port), Handler)
    srv.quiet = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    bound = srv.server_address[1]
    failures = []

    def check(name, ok, detail=""):
        print("  %-58s %s%s" % (name, "PASS" if ok else "FAIL",
                                "" if ok else "  " + detail))
        if not ok:
            failures.append(name)

    def get(raw_path):
        c = http.client.HTTPConnection(HOST, bound, timeout=15)
        c.putrequest("GET", raw_path, skip_host=True, skip_accept_encoding=True)
        c.putheader("Host", "%s:%d" % (HOST, bound))
        c.endheaders()
        r = c.getresponse()
        body = r.read()
        c.close()
        return r.status, body

    print("selftest on http://%s:%d   ui=%s" % (HOST, bound, UI_DIR))
    print(" traversal")
    attacks = [
        "/../../etc/passwd",
        "/../../../../../../etc/passwd",
        "/%2e%2e%2f%2e%2e%2f%2e%2e%2f%2e%2e%2f%2e%2e%2fetc/passwd",
        "/..%2f..%2f..%2f..%2fetc%2fpasswd",
        "/....//....//etc/passwd",
        "/../resolv_ml/config.py",
        "/../README.md",
        "/./../../etc/hosts",
    ]
    for a in attacks:
        try:
            status, body = get(a)
        except Exception as e:
            check("refuse %s" % a, False, str(e))
            continue
        refused = status in (403, 404)
        leaked = b"root:" in body or b"POLYMARKET_GAMMA" in body or b"# Resolv ML" in body
        check("refuse %s" % a, refused and not leaked,
              "status=%d leaked=%s" % (status, leaked))

    print(" static")
    status, body = get("/")
    check("GET / serves the shell", status == 200 and b'id="scorecard"' in body,
          "status=%d" % status)
    status, _ = get("/nope.js")
    check("GET /nope.js is 404", status == 404, "status=%d" % status)

    print(" api")
    endpoints = ["/api/overview", "/api/models", "/api/model", "/api/features",
                 "/api/coverage", "/api/markets?limit=3", "/api/log",
                 "/api/market?id=does-not-exist", "/api/score?id=does-not-exist",
                 "/api/markets?q=%25"]
    for ep in endpoints:
        try:
            status, body = get(ep)
            json.loads(body.decode("utf-8"))
            ok = status == 200
        except Exception as e:
            status, ok = -1, False
            body = str(e).encode()
        check("GET %s is 200 JSON" % ep, ok, "status=%d %s" % (status, body[:120]))

    status, body = get("/api/nothing")
    check("GET /api/nothing is 404 JSON",
          status == 404 and b"error" in body, "status=%d" % status)

    # A real market round trip, only if the store has one. Skipped, not faked,
    # when the store is empty.
    status, body = get("/api/markets?limit=1")
    try:
        rows = json.loads(body.decode())
    except Exception:
        rows = []
    if isinstance(rows, list) and rows:
        mid = urllib.parse.quote(rows[0]["market_id"], safe="")
        status, body = get("/api/market?id=" + mid)
        d = json.loads(body.decode())
        check("GET /api/market returns a path",
              status == 200 and isinstance(d.get("path"), list) and bool(d["path"]))
        status, body = get("/api/score?id=" + mid)
        d = json.loads(body.decode())
        check("GET /api/score answers ok or a reason",
              status == 200 and ("ok" in d), str(d)[:160])
    else:
        print("  %-58s SKIP  (store has no market with a price path)"
              % "market round trip")

    srv.shutdown()
    print("\n%d check(s) failed" % len(failures) if failures else "\nall checks passed")
    return 1 if failures else 0


# ------------------------------------------------------------------ entry point

def main(argv=None):
    global UI_DIR
    ap = argparse.ArgumentParser(description="Local dashboard for the Resolv ML package")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--open", action="store_true", dest="open_browser",
                    help="open the dashboard in the default browser")
    ap.add_argument("--ui", default=str(UI_DIR), help="directory of static files")
    ap.add_argument("--quiet", action="store_true", help="do not log each request")
    ap.add_argument("--selftest", action="store_true",
                    help="run the traversal and endpoint checks, then exit")
    a = ap.parse_args(argv)

    UI_DIR = Path(a.ui).expanduser().resolve()

    try:
        store.init()
    except Exception as e:
        print("warning: could not initialise the store at %s: %s" % (DB_PATH, e),
              file=sys.stderr)

    if a.selftest:
        return _selftest()

    if not UI_DIR.is_dir():
        print("warning: %s does not exist, only the API will answer" % UI_DIR,
              file=sys.stderr)

    try:
        srv = Server((HOST, a.port), Handler)
    except OSError as e:
        print("cannot bind %s:%d: %s" % (HOST, a.port, e), file=sys.stderr)
        return 1
    srv.quiet = a.quiet

    url = "http://%s:%d/" % (HOST, a.port)
    print("Resolv ML dashboard  %s" % url)
    print("  store    %s" % DB_PATH)
    print("  models   %s" % MODEL_DIR)
    print("  reports  %s" % REPORT_DIR)
    print("  ui       %s" % UI_DIR)
    print("Ctrl-C to stop.")

    if a.open_browser:
        import webbrowser
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()

    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
