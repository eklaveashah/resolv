"""Build the training matrix from the store.

Three rules protect the backtest:

1. Horizons count back from the SCHEDULED end (close_ts), and a cut at or
   after the moment trading actually stopped (stop_ts) is dropped. Markets that
   close early usually do so because the answer arrived. Anchoring on that
   moment, or cutting after it, hands the model the label.
2. The split is by resolution time, never random. The test set is the most
   recent slice of resolutions, so every score is "what would this have done
   on markets it had not seen settle".
3. No event group straddles the split. Kalshi and Polymarket both list many
   markets under one event (every strike of the same CPI print), and sibling
   series share one release (CPI, core CPI and CPI YoY). Letting siblings sit
   on both sides of the split leaks the answer and flatters the model. The
   store's event_group is that key, falling back to event_id.
"""
import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from . import features, store
from .config import (FEATURE_SCHEMA, GATE_MIN_EVENTS, GATE_MIN_RESOLUTION_DAYS,
                     HORIZONS_HOURS, TEST_FRACTION, VAL_FRACTION)

# Metadata written by the current venue adapters. Older rows recorded Kalshi's
# close_time (the moment trading stopped, which can depend on the outcome) as
# close_ts, so their horizons may be anchored on the answer. They still train,
# but they cannot decide a promotion. See train.assess.
META_V_SCHEDULED = 2


@dataclass
class Sample:
    market_id: str
    event_id: str
    venue: str
    category: Optional[str]
    resolve_ts: int
    cut_ts: int
    horizon_h: int
    p_market: float
    x: List[float]
    y: int
    event_group: Optional[str] = None
    legacy_anchor: bool = False

    def __post_init__(self):
        if not self.event_group:
            self.event_group = self.event_id or self.market_id


@dataclass
class Dataset:
    train: List[Sample] = field(default_factory=list)
    val: List[Sample] = field(default_factory=list)
    test: List[Sample] = field(default_factory=list)
    feature_names: List[str] = field(default_factory=lambda: list(features.FEATURE_NAMES))
    note: str = ""
    counts: Dict[str, int] = field(default_factory=dict)

    def sizes(self) -> Dict[str, int]:
        return {"train": len(self.train), "val": len(self.val), "test": len(self.test)}

    def parts(self) -> List[Tuple[str, List[Sample]]]:
        return [("train", self.train), ("val", self.val), ("test", self.test)]


def is_legacy_anchor(m: Dict) -> bool:
    """True when close_ts may be the moment trading stopped rather than the
    scheduled end. Synthetic rows are generated with a fixed end."""
    if m.get("venue") == "synthetic":
        return False
    try:
        return int(m.get("meta_v") or 0) < META_V_SCHEDULED
    except (TypeError, ValueError):
        return True


def samples_for_market(m: Dict, pts: List[Tuple[int, float, Optional[float]]]) -> List[Sample]:
    out: List[Sample] = []
    close = m.get("close_ts")
    resolve = m.get("resolve_ts")
    # When trading really stopped. Old rows never recorded it; settlement is
    # the conservative stand-in because nothing trades after it.
    stop = m.get("stop_ts") or resolve
    if not close or not resolve or m.get("outcome") is None or not pts:
        return out
    legacy = is_legacy_anchor(m)
    for h in HORIZONS_HOURS:
        cut = int(close - h * 3600)
        if cut <= pts[0][0]:
            continue
        # Being still open at the cut is observable at the cut, so this guard
        # is not look-ahead. A cut after the stop would read the settled price.
        if cut >= int(stop):
            continue
        f = features.build(pts, cut, m)
        if f is None:
            continue
        out.append(Sample(
            market_id=m["market_id"],
            event_id=m.get("event_id") or m["market_id"],
            venue=m["venue"], category=m.get("category"),
            resolve_ts=int(resolve), cut_ts=cut, horizon_h=h,
            p_market=features.inv_logit(f["logit_p"]),
            x=features.vector(f), y=int(m["outcome"]),
            event_group=m.get("event_group") or m.get("event_id") or m["market_id"],
            legacy_anchor=legacy,
        ))
    return out


def build(con=None, verbose: bool = True, provenance: Optional[str] = None) -> Dataset:
    close = False
    if con is None:
        ctx = store.connect(readonly=True)
        con = ctx.__enter__()
        close = ctx
    try:
        rows = store.resolved_markets(con, include_synthetic=(provenance != "live"))
        if provenance:
            rows = [r for r in rows if (r["venue"] == "synthetic") == (provenance == "synthetic")]
        all_samples: List[Sample] = []
        skipped_no_path = no_close = no_samples = 0
        for r in rows:
            m = dict(r)
            if isinstance(m.get("raw"), str):
                try:
                    m["raw"] = json.loads(m["raw"])
                except Exception:
                    m["raw"] = {}
            if not m.get("close_ts"):
                no_close += 1
                continue
            pts = store.price_path(con, m["market_id"])
            if not pts:
                skipped_no_path += 1
                continue
            got = samples_for_market(m, pts)
            if not got:
                no_samples += 1
            all_samples.extend(got)
    finally:
        if close:
            close.__exit__(None, None, None)

    counts = {"resolved_markets": len(rows), "no_price_path": skipped_no_path,
              "no_scheduled_close": no_close, "no_usable_horizon": no_samples,
              "markets": len({s.market_id for s in all_samples}),
              "legacy_anchor_markets": len({s.market_id for s in all_samples if s.legacy_anchor})}
    ds = Dataset(counts=counts)
    if not all_samples:
        ds.note = ("No samples. Store holds %d resolved markets, %d of them with no "
                   "price path and %d with no usable horizon before trading stopped. "
                   "Run collect before training."
                   % (len(rows), skipped_no_path, no_samples))
        return ds

    note = ("%d markets, %d samples, %d skipped for missing price path, %d with no "
            "horizon before trading stopped"
            % (counts["markets"], len(all_samples), skipped_no_path, no_samples))
    if no_close:
        note += ", %d without a scheduled close" % no_close
    if counts["legacy_anchor_markets"]:
        note += (", %d anchored on pre-repair close metadata"
                 % counts["legacy_anchor_markets"])
    ds = _split(all_samples, note=note)
    ds.counts = counts
    return ds


def _split(samples: List[Sample], note: str = "") -> Dataset:
    """Time split with event-group grouping: order groups by their last
    resolution, then cut. A group lands wholly on one side."""
    by_group: Dict[str, List[Sample]] = {}
    for s in samples:
        by_group.setdefault(s.event_group, []).append(s)
    # The group key breaks ties so the split never depends on row order.
    groups = sorted(by_group, key=lambda g: (max(x.resolve_ts for x in by_group[g]), g))

    n = len(groups)
    n_test = max(1, int(round(n * TEST_FRACTION)))
    n_val = max(1, int(round(n * VAL_FRACTION)))
    if n_test + n_val >= n:                      # tiny corpus, keep a train set
        n_test = max(1, n // 3)
        n_val = max(1, n // 6)

    tr_g = groups[: n - n_test - n_val]
    va_g = groups[n - n_test - n_val: n - n_test]
    te_g = groups[n - n_test:]

    pick = lambda gs: [s for g in gs for s in by_group[g]]
    ds = Dataset(train=pick(tr_g), val=pick(va_g), test=pick(te_g), note=note)

    # Purge whole groups whose labels were not available before the next
    # partition's first forecast. Grouping alone does not prevent this leak.
    def purge(earlier, later):
        cuts = [s.cut_ts for s in later if s.cut_ts > 0]
        if not cuts:
            return earlier
        boundary = min(cuts)
        bad = {s.event_group for s in earlier if s.resolve_ts >= boundary}
        return [s for s in earlier if s.event_group not in bad]
    ds.val = purge(ds.val, ds.test)
    ds.train = purge(ds.train, ds.val or ds.test)
    ds.note += "; event-grouped temporal split, labels purged before next forecast window"

    for part in (ds.train, ds.val, ds.test):
        part.sort(key=lambda s: (s.resolve_ts, s.market_id, -s.horizon_h))
    return ds


# ------------------------------------------------------------------ coverage gate

def utc_day(ts: int) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(int(ts)))


def event_counts(ds: Dataset) -> Dict[str, int]:
    """Independent event groups per split. Horizons of one market, sibling
    strikes and sibling series on one release count once."""
    return {k: len({s.event_group for s in part}) for k, part in ds.parts()}


def resolution_days(ds: Dataset) -> Dict[str, int]:
    """Distinct UTC days on which the split's markets resolved. Fifty events
    that all settle on one afternoon are one draw of the news, not fifty."""
    return {k: len({utc_day(s.resolve_ts) for s in part}) for k, part in ds.parts()}


def coverage(ds: Dataset) -> Dict[str, object]:
    """The independent-coverage gate. Returns the counts, whether each minimum
    is met, and a plain sentence for every miss."""
    ev, days = event_counts(ds), resolution_days(ds)
    misses: List[str] = []
    for k, need in GATE_MIN_EVENTS.items():
        if ev.get(k, 0) < need:
            misses.append("%s has %d independent event group(s); the gate needs %d."
                          % (k, ev.get(k, 0), need))
    for k, need in GATE_MIN_RESOLUTION_DAYS.items():
        if days.get(k, 0) < need:
            misses.append("%s resolves on %d distinct UTC day(s); the gate needs %d."
                          % (k, days.get(k, 0), need))
    return {"event_counts": ev, "resolution_days": days,
            "min_events": dict(GATE_MIN_EVENTS),
            "min_resolution_days": dict(GATE_MIN_RESOLUTION_DAYS),
            "ok": not misses, "misses": misses}


def legacy_counts(ds: Dataset) -> Dict[str, int]:
    return {k: sum(1 for s in part if s.legacy_anchor) for k, part in ds.parts()}


def data_fingerprint(ds: Dataset) -> str:
    """sha256 over everything training reads: the split each sample landed in,
    its identity, cut, label, market price and feature values, plus the
    feature names and schema. Equal fingerprints mean a retrain would see
    exactly the same data. Floats go through repr, so equal means bit-equal."""
    h = hashlib.sha256()
    h.update(json.dumps({"schema": FEATURE_SCHEMA, "names": ds.feature_names}).encode())
    for k, part in ds.parts():
        for s in sorted(part, key=lambda s: (s.market_id, s.cut_ts, s.horizon_h)):
            h.update(json.dumps(
                [k, s.market_id, s.event_group, s.venue, s.category, s.resolve_ts,
                 s.cut_ts, s.horizon_h, s.y, repr(float(s.p_market)),
                 [repr(float(v)) for v in s.x], bool(s.legacy_anchor)]).encode())
            h.update(b"\n")
    return h.hexdigest()


def matrices(part: List[Sample]):
    """(X, y, p_market) as numpy arrays. Imported lazily so the collector and
    feature code keep working without numpy installed."""
    import numpy as np
    if not part:
        return (np.zeros((0, len(features.FEATURE_NAMES))), np.zeros(0), np.zeros(0))
    X = np.asarray([s.x for s in part], dtype=float)
    y = np.asarray([s.y for s in part], dtype=int)
    pm = np.asarray([s.p_market for s in part], dtype=float)
    return X, y, pm
