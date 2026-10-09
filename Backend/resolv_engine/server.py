"""Resolv engine HTTP API — stdlib only, no installs.

Run:    python3 -m resolv_engine.server            (port 8899)
        python3 -m resolv_engine.server 9000       (custom port)

Endpoints (all JSON, CORS-open for local frontends):
  GET  /health              -> {ok, version}
  GET  /demo                -> a worked threshold-market pricing example
  POST /price               -> full pipeline; body: {market, evidence?, rubric?}
  POST /sentiment           -> {text} -> {sentiment, hits}
  POST /kelly               -> {p, price_yes, bankroll?, fraction?}
  POST /vol/realized        -> {prices:[...], periods_per_year?} -> {annual_vol}

POST /price body example:
{
  "market": {"id":"btc-120k","title":"BTC > $120K Aug 1","kind":"threshold",
             "price_yes":0.44,"liquidity_score":0.9,"days_to_expiry":18,
             "spot":112500,"strike":120000,"annual_vol":0.55},
  "evidence": [{"text":"Bitcoin ETF inflows hit multi-month high",
                "source":"reuters.com","age_hours":3,"relevance":0.9,
                "source_weight":0.8}],
  "rubric": {"resolution_clarity":"clear"}
}
"""
import dataclasses
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict

from .calibration import Episode, fit, synthetic_history
from .models import Evidence, Market, QualRubric
from .pricing import kelly_yes, price, realized_vol
from .sentiment import score as sentiment_score
from . import __version__

DEFAULT_PORT = 8899


def _to_jsonable(obj: Any) -> Any:
    if dataclasses.is_dataclass(obj):
        return {k: _to_jsonable(v) for k, v in dataclasses.asdict(obj).items()}
    if isinstance(obj, list):
        return [_to_jsonable(x) for x in obj]
    if isinstance(obj, float):
        return round(obj, 6)
    return obj


def _build_market(d: Dict) -> Market:
    fields = {f.name for f in dataclasses.fields(Market)}
    unknown = set(d) - fields
    if unknown:
        raise ValueError(f"unknown market fields: {sorted(unknown)}")
    return Market(**d)


def _build_evidence(items) -> list:
    fields = {f.name for f in dataclasses.fields(Evidence)}
    out = []
    for d in items or []:
        unknown = set(d) - fields
        if unknown:
            raise ValueError(f"unknown evidence fields: {sorted(unknown)}")
        out.append(Evidence(**d))
    return out


def _build_rubric(d) -> QualRubric:
    if not d:
        return QualRubric()
    fields = {f.name for f in dataclasses.fields(QualRubric)}
    unknown = set(d) - fields
    if unknown:
        raise ValueError(f"unknown rubric fields: {sorted(unknown)}")
    return QualRubric(**d)


DEMO_REQUEST = {
    "market": {
        "id": "demo-btc-120k", "title": "Bitcoin above $120,000 on Aug 1 (demo)",
        "kind": "threshold", "price_yes": 0.44, "liquidity_score": 0.9,
        "days_to_expiry": 18, "spot": 112500.0, "strike": 120000.0,
        "annual_vol": 0.55,
    },
    "evidence": [
        {"text": "Bitcoin ETF inflows hit multi-month high on institutional demand",
         "source": "demo.reuters.com", "age_hours": 3, "relevance": 0.9, "source_weight": 0.8},
        {"text": "Bitcoin slips as profit-taking follows record ETF week",
         "source": "demo.coindesk.com", "age_hours": 9, "relevance": 0.8, "source_weight": 0.7},
    ],
    "rubric": {"resolution_clarity": "clear"},
}


class Handler(BaseHTTPRequestHandler):
    server_version = "ResolvEngine/" + __version__

    def _send(self, code: int, payload: Dict) -> None:
        body = json.dumps(payload, indent=1).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()
        self.wfile.write(body)

    def _err(self, code: int, msg: str) -> None:
        self._send(code, {"error": msg})

    def _body(self) -> Dict:
        n = int(self.headers.get("Content-Length") or 0)
        if n == 0:
            return {}
        return json.loads(self.rfile.read(n).decode())

    def do_OPTIONS(self):  # CORS preflight
        self._send(204, {})

    def do_GET(self):
        if self.path == "/health":
            self._send(200, {"ok": True, "version": __version__})
        elif self.path == "/calibrate/demo":
            # trains on labeled SYNTHETIC history: demonstrates the loop,
            # does not claim real-world predictive power
            r = fit(synthetic_history(n=800), iters=2500, lr=0.5)
            self._send(200, {"note": "fitted on synthetic sample history (demo of the "
                                     "training loop); POST /calibrate with real resolved "
                                     "episodes for real calibration",
                             "result": _to_jsonable(r)})
        elif self.path == "/demo":
            req = DEMO_REQUEST
            result = price(_build_market(req["market"]),
                           _build_evidence(req["evidence"]),
                           _build_rubric(req["rubric"]))
            self._send(200, {"request": req, "result": _to_jsonable(result)})
        else:
            self._err(404, "unknown path; see /health, /demo, POST /price")

    def do_POST(self):
        try:
            body = self._body()
        except (ValueError, json.JSONDecodeError) as e:
            return self._err(400, f"invalid JSON: {e}")
        try:
            if self.path == "/price":
                if "market" not in body:
                    return self._err(400, "body must contain 'market'")
                result = price(_build_market(body["market"]),
                               _build_evidence(body.get("evidence")),
                               _build_rubric(body.get("rubric")))
                self._send(200, _to_jsonable(result))
            elif self.path == "/sentiment":
                s, hits = sentiment_score(body.get("text", ""))
                self._send(200, {"sentiment": round(s, 4), "hits": hits})
            elif self.path == "/kelly":
                p, m = float(body["p"]), float(body["price_yes"])
                if not (0 < p < 1 and 0 < m < 1):
                    return self._err(400, "p and price_yes must be in (0,1)")
                frac = float(body.get("fraction", 0.5))
                bank = float(body.get("bankroll", 0.0))
                kf = kelly_yes(p, m)
                out = {"kelly_full": round(kf, 6),
                       "kelly_fraction": round(kf * frac, 6)}
                if bank > 0:
                    out["stake"] = round(kf * frac * bank, 2)
                self._send(200, out)
            elif self.path == "/calibrate":
                hist = body.get("history") or []
                eps = [Episode(p_market=float(h["p_market"]),
                               evidence_lo=float(h.get("evidence_lo", 0.0)),
                               outcome=int(h["outcome"])) for h in hist]
                self._send(200, _to_jsonable(fit(eps)))
            elif self.path == "/vol/realized":
                prices = body.get("prices") or []
                ppy = float(body.get("periods_per_year", 365.0))
                self._send(200, {"annual_vol": round(realized_vol([float(x) for x in prices], ppy), 6)})
            else:
                self._err(404, "unknown path")
        except (KeyError, TypeError, ValueError) as e:
            self._err(400, f"bad request: {e}")

    def log_message(self, fmt, *args):  # quieter default log line
        sys.stderr.write("engine: %s\n" % (fmt % args))


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PORT
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"Resolv engine v{__version__} on http://127.0.0.1:{port}  (GET /health, /demo)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
