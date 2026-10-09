"""Tiny HTTP client. stdlib only, so collection runs even before the ML
dependencies are installed."""
import gzip
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional

from .config import HTTP_RETRIES, HTTP_TIMEOUT, RATE_LIMIT_SLEEP, USER_AGENT

_last_call = [0.0]


class HttpError(Exception):
    def __init__(self, status: int, url: str, body: str = ""):
        super().__init__("HTTP %s for %s %s" % (status, url, body[:200]))
        self.status = status
        self.url = url


def _throttle() -> None:
    gap = time.time() - _last_call[0]
    if gap < RATE_LIMIT_SLEEP:
        time.sleep(RATE_LIMIT_SLEEP - gap)
    _last_call[0] = time.time()


def get_json(url: str, params: Optional[Dict[str, Any]] = None,
             retries: int = HTTP_RETRIES) -> Any:
    """GET returning parsed JSON. Retries on 429/5xx with backoff.

    Raises HttpError on a final 4xx, and URLError when the host simply is not
    reachable. Callers treat those differently: a 4xx means the endpoint moved,
    an URLError usually means the network is blocked.
    """
    if params:
        clean = {k: v for k, v in params.items() if v is not None}
        url = url + ("&" if "?" in url else "?") + urllib.parse.urlencode(clean)

    last_exc = None
    for attempt in range(retries):
        _throttle()
        req = urllib.request.Request(url, headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
        })
        try:
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
                raw = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
                return json.loads(raw.decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8", "replace")
            except Exception:
                pass
            if e.code in (429, 500, 502, 503, 504) and attempt < retries - 1:
                time.sleep(min(30, 2 ** attempt * 2))
                last_exc = HttpError(e.code, url, body)
                continue
            raise HttpError(e.code, url, body)
        except Exception as e:               # URLError, timeout, JSON decode
            last_exc = e
            if attempt < retries - 1:
                time.sleep(min(15, 2 ** attempt))
                continue
            raise
    if last_exc:
        raise last_exc


def reachable(url: str, timeout: float = 6.0) -> tuple:
    """Cheap preflight. Returns (ok, detail).

    Collection runs against public hosts that are sometimes blocked by a
    corporate network, a VPN, or a sandbox. Without this the collector spends
    minutes burning retries and backoff on a host that was never going to
    answer, and looks like it has hung.
    """
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT,
                                               "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return True, "HTTP %s" % resp.status
    except urllib.error.HTTPError as e:
        # An HTTP error still proves the host answered.
        return True, "HTTP %s" % e.code
    except Exception as e:
        return False, "%s: %s" % (type(e).__name__, e)
