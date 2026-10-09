"""Score a live market with the current champion.

Input is the same thing the trainer saw: a price path and the market's
metadata. Output is a probability plus the edge against the current price.
"""
import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from . import features, store
from .config import MODEL_DIR


class NoChampion(Exception):
    pass


def load(version: Optional[str] = None, provenance: str = "live"):
    import joblib
    with store.connect() as con:
        if version:
            row = con.execute("SELECT * FROM models WHERE version=?", (version,)).fetchone()
        else:
            row = store.champion(con, provenance)
    if row is None:
        raise NoChampion(
            "no %s champion model. Run collect then train first." % provenance)
    if row["provenance"] != provenance:
        raise NoChampion("requested model provenance does not match")
    meta = json.loads((Path(row["path"]) / "meta.json").read_text())
    if meta.get("feature_schema") != 2 or not meta.get("deployment_eligible") and provenance == "live":
        raise NoChampion("model needs retraining with the corrected feature schema")
    bundle = joblib.load(Path(row["path"]) / "model.joblib")
    return bundle, dict(row)


def _design(x: List[float], feature_names: List[str], cols: List[str]):
    import numpy as np
    d = dict(zip(feature_names, x))
    d["logit_p_x_hours"] = d.get("logit_p", 0.0) * d.get("log_hours_left", 0.0)
    return np.asarray([[d.get(c, 0.0) for c in cols]], dtype=float)


def score(price_path: Sequence[Tuple[int, float, Optional[float]]],
          market: Dict, at_ts: Optional[int] = None,
          version: Optional[str] = None, provenance: str = "live") -> Dict:
    if not price_path:
        return {"ok": False, "reason": "empty price history"}
    bundle, row = load(version, provenance)
    cut = int(at_ts or max(t for t, _, _ in price_path))
    f = features.build(price_path, cut, market)
    if f is None:
        return {"ok": False, "reason": "price path too thin to score",
                "model": row["version"]}
    X = _design(features.vector(f), bundle["feature_names"], bundle["columns"])
    p = float(bundle["estimator"].predict_proba(X)[0][1])
    p_mkt = features.inv_logit(f["logit_p"])
    return {
        "ok": True,
        "model": row["version"],
        "provenance": row["provenance"],
        "p_model": round(p, 4),
        "p_market": round(p_mkt, 4),
        "edge_points": round((p - p_mkt) * 100, 2),
        "hours_to_resolution": round(pow(2.718281828, f["log_hours_left"]) - 1, 1),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Score a market. Reads {market, price_path} JSON on stdin.")
    ap.add_argument("--version")
    ap.add_argument("--provenance", default="live", choices=["live", "synthetic"])
    a = ap.parse_args(argv)
    payload = json.load(sys.stdin)
    pts = [(int(t), float(p), None) for t, p in payload["price_path"]]
    try:
        print(json.dumps(score(pts, payload["market"], version=a.version,
                               provenance=a.provenance), indent=2))
    except NoChampion as e:
        print(json.dumps({"ok": False, "reason": str(e)}, indent=2))
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
