"""Hand the fitted constants to the terminal.

The terminal prices event contracts with

    logit(p_hat) = a + b * logit(p_market) + k * evidence

and currently ships b = 1.06, a hand-set number. This writes the fitted
replacement as JSON that the terminal can read.

Hard rule: a model fitted on generated data is never exported. Shipping a
synthetic constant into a product that sizes real positions would be exactly
the kind of invented number the rest of this project refuses to produce.
"""
import argparse
import json
import sys
import time
from pathlib import Path

from . import store
from .config import ROOT


class RefusedExport(Exception):
    pass


def export(out_path: Path = None, allow_synthetic: bool = False) -> Path:
    out_path = out_path or (ROOT / "calibration.json")
    with store.connect() as con:
        row = store.champion(con, "live")
        if row is None:
            syn = store.champion(con, "synthetic")
            if syn is not None and not allow_synthetic:
                raise RefusedExport(
                    "The only champion is model %s, fitted on SYNTHETIC data.\n"
                    "Refusing to export it. Constants from generated markets have\n"
                    "no predictive meaning and must not reach the terminal.\n"
                    "Collect real resolved markets first:\n"
                    "    python3 -m resolv_ml.collect" % syn["version"])
            if syn is None:
                raise RefusedExport("No trained model at all. Run collect then train.")
            row = syn

    meta = json.loads((Path(row["path"]) / "meta.json").read_text())
    if meta.get("feature_schema") != 2 or not meta.get("deployment_eligible"):
        raise RefusedExport("Model has not passed current live validation gates.")
    if row["algo"] != "logit_calib":
        raise RefusedExport("Champion is not a single-slope calibrator. Use predict instead of exporting constants from a different candidate.")
    consts = meta.get("calibration_constants")
    if not consts:
        raise RefusedExport(
            "Champion %s is a %s model with no closed-form (a, b) to export.\n"
            "Use resolv_ml.predict to score markets with it instead."
            % (row["version"], row["algo"]))

    metrics = json.loads(row["metrics"])
    payload = {
        "_comment": "Fitted by resolv_ml. Replaces the hand-set CAL_B in the terminal.",
        "generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "model_version": row["version"],
        "provenance": row["provenance"],
        "a": round(consts["a"], 6),
        "b": round(consts["b"], 6),
        "replaces": {"CAL_B": 1.06},
        "fitted_on": {"train_samples": row["n_train"], "test_samples": row["n_test"]},
        "out_of_sample": {
            "brier": metrics.get("brier"),
            "brier_market": metrics.get("brier_market"),
            "brier_skill_score": metrics.get("brier_skill_score"),
        },
        "caveat": ("b is fitted across all horizons at once. Because a price far "
                   "from resolution is a noisier reading than one near it, a single "
                   "b understates the correction late and overstates it early. The "
                   "horizon-aware model handles this properly; use predict.py for "
                   "per-market numbers."),
    }
    out_path.write_text(json.dumps(payload, indent=2))
    return out_path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Export fitted constants for the terminal")
    ap.add_argument("--out")
    ap.add_argument("--allow-synthetic", action="store_true",
                    help="export a synthetic-provenance model anyway. Do not use "
                         "for anything that touches a real position.")
    a = ap.parse_args(argv)
    try:
        p = export(Path(a.out) if a.out else None, a.allow_synthetic)
    except RefusedExport as e:
        print("EXPORT REFUSED\n\n%s" % e, file=sys.stderr)
        return 2
    print("wrote %s" % p)
    print(p.read_text())
    return 0


if __name__ == "__main__":
    sys.exit(main())
