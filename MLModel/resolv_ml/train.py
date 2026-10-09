"""Train, score against the market baseline, promote only if better.

Four candidates, all judged on the same held-out slice of recent resolutions:

  market       the market price itself. Not a model, the bar to clear.
  logit_calib  logistic regression on logit(price) alone. Learns exactly the
               (a, b) pair the terminal hardcodes as the crowd-bias constant,
               so a win here is directly portable into the product.
  logit_horizon  the same, but b is allowed to vary with time to resolution.
               A far-out price is a noisier reading of the truth than a price
               an hour before settlement, and regression dilution means one
               global b is wrong for both. This candidate lets the correction
               shrink as the horizon lengthens.
  logit_full   L2 logistic regression on the whole feature set.
  gbm          gradient boosting, then isotonic calibration fitted on the
               validation slice, because raw boosting output is not a
               probability you should size a position with.

The winner on validation Brier is scored once on test and becomes the
challenger. It replaces the champion only if it improves test Brier by more
than PROMOTION_MARGIN, which keeps version numbers from churning on noise.
"""
import argparse
import json
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

from . import dataset, evaluate, store
from .config import MODEL_DIR, PROMOTION_MARGIN, RANDOM_SEED, REPORT_DIR


def log(msg: str) -> None:
    print("[train %s] %s" % (time.strftime("%H:%M:%S"), msg), flush=True)


# ------------------------------------------------------------------ candidates

def _candidates(seed: int):
    from sklearn.calibration import CalibratedClassifierCV
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import StandardScaler

    from .anchored import MarketAnchoredLogit
    return {
        "market_residual": ("regularized correction anchored to market log-odds", MarketAnchoredLogit()),
        "logit_calib": ("logit(price) only, learns the crowd-bias constant",
                        LogisticRegression(C=1e6, max_iter=2000)),
        "logit_horizon": ("logit(price) with the correction varying by horizon",
                          LogisticRegression(C=1e6, max_iter=3000)),
        "logit_full": ("L2 logistic regression, all features",
                       Pipeline([("s", StandardScaler()),
                                 ("m", LogisticRegression(C=0.5, max_iter=4000))])),
        "gbm": ("gradient boosting, isotonic-calibrated",
                HistGradientBoostingClassifier(
                        max_iter=300, learning_rate=0.06, max_depth=4,
                        min_samples_leaf=40, l2_regularization=1.0,
                        random_state=seed, early_stopping=False)),
    }


CALIB_COLS = ["logit_p"]
HORIZON_COLS = ["logit_p", "log_hours_left", "logit_p_x_hours"]


def _augment(X: np.ndarray, names: List[str]) -> Tuple[np.ndarray, List[str]]:
    """Add the price-by-horizon interaction the dilution diagnostic calls for."""
    i_p, i_h = names.index("logit_p"), names.index("log_hours_left")
    inter = (X[:, i_p] * X[:, i_h]).reshape(-1, 1)
    return np.hstack([X, inter]), names + ["logit_p_x_hours"]


def _subset(X: np.ndarray, names: List[str], want: List[str]) -> np.ndarray:
    idx = [names.index(w) for w in want]
    return X[:, idx]


def fit_all(ds: dataset.Dataset, seed: int = RANDOM_SEED) -> Dict[str, Dict]:
    Xtr, ytr, pmtr = dataset.matrices(ds.train)
    Xva, yva, pmva = dataset.matrices(ds.val)
    names = ds.feature_names
    results: Dict[str, Dict] = {}

    results["market"] = {"desc": "the market price, unchanged", "est": None,
                         "val": evaluate.evaluate(yva, pmva, pmva, "market/val")}

    if len(set(ytr.tolist())) < 2:
        log("training slice has a single class, cannot fit")
        return results

    Xtr_a, names_a = _augment(Xtr, names)
    Xva_a, _ = _augment(Xva, names)

    for name, (desc, est) in _candidates(seed).items():
        cols = {"market_residual": HORIZON_COLS, "logit_calib": CALIB_COLS,
                "logit_horizon": HORIZON_COLS}.get(name, names)
        A, B = _subset(Xtr_a, names_a, cols), _subset(Xva_a, names_a, cols)
        try:
            if name == "gbm":
                # Calibration uses a later, event-disjoint, purged slice of
                # TRAIN only. Validation remains untouched for selection.
                from sklearn.calibration import CalibratedClassifierCV
                from sklearn.frozen import FrozenEstimator
                inner = dataset._split(ds.train)
                fit_rows, cal_rows = inner.train, inner.val + inner.test
                if not fit_rows or not cal_rows or len({s.y for s in fit_rows}) < 2 or len({s.y for s in cal_rows}) < 2:
                    raise ValueError("insufficient event-disjoint calibration history")
                Xi, yi, _ = dataset.matrices(fit_rows)
                Xc, yc, _ = dataset.matrices(cal_rows)
                est.fit(Xi, yi)
                est = CalibratedClassifierCV(FrozenEstimator(est), method="sigmoid")
                est.fit(Xc, yc)
            else:
                est.fit(A, ytr)
            pv = est.predict_proba(B)[:, 1]
        except Exception as e:
            log("%s failed to fit: %s" % (name, e))
            continue
        results[name] = {"desc": desc, "est": est, "cols": cols,
                         "val": evaluate.evaluate(yva, pv, pmva, name + "/val")}
        log("  %-12s val Brier %.5f  skill %+.4f"
            % (name, results[name]["val"]["brier"], results[name]["val"]["brier_skill_score"]))
    return results


def calib_constants(est, ds: dataset.Dataset) -> Optional[Dict[str, float]]:
    """Pull (a, b) out of the single-feature model, the form the terminal uses:
    logit(p_hat) = a + b * logit(p_market)."""
    try:
        return {"a": float(est.intercept_[0]), "b": float(est.coef_[0][0])}
    except Exception:
        return None


# ------------------------------------------------------------------ persistence

def save(version: str, name: str, est, ds: dataset.Dataset, cols: List[str],
         provenance: str, metrics: Dict, extra: Dict) -> Path:
    import joblib
    d = MODEL_DIR / version
    d.mkdir(parents=True, exist_ok=True)
    joblib.dump({"estimator": est, "columns": cols,
                 "feature_names": ds.feature_names,
                 "augmented": True}, d / "model.joblib")
    meta = {"version": version, "algo": name, "provenance": provenance,
            "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "columns": cols, "feature_names": ds.feature_names,
            "sizes": ds.sizes(), "metrics": metrics, **extra}
    (d / "meta.json").write_text(json.dumps(meta, indent=2))
    return d


def write_report(version: str, ds: dataset.Dataset, results: Dict,
                 chosen: str, test_metrics: Dict, horizons: List,
                 provenance: str, promoted: bool, prev: Optional[Dict]) -> Path:
    lines = []
    A = lines.append
    A("# Resolv ML model %s" % version)
    A("")
    A("Provenance: **%s**" % provenance.upper())
    if provenance == "synthetic":
        A("")
        A("> This model was fitted on generated data, not on real settled")
        A("> markets. It proves the pipeline runs end to end. It has no")
        A("> predictive value and must never be exported into the terminal.")
    A("")
    A("Built %s" % time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()))
    A("")
    A("## Data")
    A("")
    A("| Split | Samples |")
    A("|---|---|")
    for k, v in ds.sizes().items():
        A("| %s | %d |" % (k, v))
    A("")
    A(ds.note)
    A("")
    A("## Candidates, scored on validation")
    A("")
    A("| Model | Brier | Skill vs market | Log loss | AUC |")
    A("|---|---|---|---|---|")
    for n, r in results.items():
        m = r["val"]
        A("| %s | %.5f | %+.4f | %.5f | %.4f |"
          % (n, m["brier"], m["brier_skill_score"], m["log_loss"], m["auc"]))
    A("")
    A("Chosen: **%s**" % chosen)
    A("")
    A("## Held-out test")
    A("")
    A("```")
    A(evaluate.summary_text(test_metrics))
    A("```")
    A("")
    if horizons:
        A("### Skill by forecast horizon")
        A("")
        A("| Hours before resolution | n | Brier | Market Brier | Skill |")
        A("|---|---|---|---|---|")
        for h in horizons:
            A("| %d | %d | %.5f | %.5f | %+.4f |"
              % (h["horizon_h"], h["n"], h["brier"], h["brier_market"], h["skill"]))
        A("")
    A("### Reliability")
    A("")
    A("| Predicted | Actual | n |")
    A("|---|---|---|")
    for r in test_metrics["reliability"]:
        A("| %.3f | %.3f | %d |" % (r["predicted"], r["actual"], r["n"]))
    A("")
    A("## Promotion")
    A("")
    if prev:
        A("Champion was `%s` at test Brier %.5f." % (prev["version"], prev["brier"]))
    else:
        A("No previous champion.")
    A("")
    A("**%s**" % ("PROMOTED to champion." if promoted else
                  "Not promoted. Baseline, independent-event coverage or champion comparison gate was not met."))
    p = REPORT_DIR / ("%s.md" % version)
    p.write_text("\n".join(lines))
    return p


# ------------------------------------------------------------------ entry point

def run(seed: int = RANDOM_SEED, force_promote: bool = False, provenance: str = "live") -> int:
    store.init()
    ds = dataset.build(provenance=provenance)
    if not ds.train or not ds.val or not ds.test:
        log("not enough data to train. %s" % (ds.note or "store is empty"))
        log("run:  python3 -m resolv_ml.collect")
        return 2

    venues = {s.venue for s in ds.train}
    if "synthetic" in venues and venues != {"synthetic"}:
        raise ValueError("mixed synthetic and live data")
    log("data: %s  provenance=%s  venues=%s" % (ds.sizes(), provenance, sorted(venues)))

    results = fit_all(ds, seed)
    fitted = {k: v for k, v in results.items() if v.get("est") is not None}
    if not fitted:
        log("no candidate fitted")
        return 3

    chosen = min(fitted, key=lambda k: fitted[k]["val"]["brier"])
    if results["market"]["val"]["brier"] <= fitted[chosen]["val"]["brier"]:
        log("no candidate beat the market price on validation. Keeping the best "
            "anyway for the record, but skill is not positive.")

    est, cols = fitted[chosen]["est"], fitted[chosen]["cols"]
    Xte, yte, pmte = dataset.matrices(ds.test)
    Xte_a, names_a = _augment(Xte, ds.feature_names)
    pte = est.predict_proba(_subset(Xte_a, names_a, cols))[:, 1]
    test_metrics = evaluate.evaluate(yte, pte, pmte, chosen + "/test")
    horizons = evaluate.by_horizon(ds.test, pte)

    log(evaluate.summary_text(test_metrics))

    event_counts = {k:len({s.event_id for s in getattr(ds,k)}) for k in ("train","val","test")}
    # Multiple horizons and sibling strikes do not count as independent events.
    sufficient = event_counts['train'] >= 50 and event_counts['val'] >= 20 and event_counts['test'] >= 20
    eligible = (sufficient and results[chosen]["val"]["brier"] + PROMOTION_MARGIN < results["market"]["val"]["brier"]
                and test_metrics["brier_skill_score"] > 0)
    log("independent events: %s; deployment eligible=%s" % (event_counts,eligible))
    extra: Dict = {"deployment_eligible": eligible, "event_counts":event_counts,"feature_schema": 2, "train_market_ids": sorted({s.market_id for s in ds.train}),
                   "train_event_ids": sorted({s.event_id for s in ds.train}),
                   "train_label_end": max(s.resolve_ts for s in ds.train),"candidates": {k: v["val"] for k, v in results.items()},
                   "by_horizon": horizons}
    if "logit_calib" in fitted:
        c = calib_constants(fitted["logit_calib"]["est"], ds)
        if c:
            extra["calibration_constants"] = c
            log("calibration constants: a=%.4f  b=%.4f   (terminal ships b=1.06)"
                % (c["a"], c["b"]))

    version = "%s-%s-%s" % (time.strftime("%Y%m%d-%H%M%S", time.gmtime()),
                            provenance, chosen)
    path = save(version, chosen, est, ds, cols, provenance, test_metrics, extra)

    with store.connect() as con:
        prev = store.champion(con, provenance)
        prev_info = None
        # A first model must also clear the market baseline on both slices.
        promoted = eligible
        if prev is not None:
            pm = json.loads(prev["metrics"])
            prev_info = {"version": prev["version"], "brier": pm.get("brier", 9.9)}
            # Old scores on a different test set are not comparable. Only
            # compare on this test set if it is untouched by champion training.
            import joblib
            old_meta = json.loads((Path(prev["path"]) / "meta.json").read_text())
            test_events = {s.event_id for s in ds.test}
            untouched = (old_meta.get("feature_schema") == 2
                         and not test_events.intersection(old_meta.get("train_event_ids", []))
                         and old_meta.get("train_label_end", float("inf")) < min(s.cut_ts for s in ds.test))
            if untouched:
                old = joblib.load(Path(prev["path"]) / "model.joblib")
                old_p = old["estimator"].predict_proba(_subset(Xte_a, names_a, old["columns"]))[:, 1]
                prev_info["brier"] = evaluate.brier(yte, old_p)
                promoted = eligible and (prev_info["brier"] - test_metrics["brier"] > PROMOTION_MARGIN)
            else:
                promoted = False
                log("champion lacks comparable untouched test history; promotion withheld")
        store.register_model(con, {
            "version": version, "algo": chosen, "provenance": provenance,
            "n_train": len(ds.train), "n_test": len(ds.test),
            "metrics": test_metrics, "params": extra.get("calibration_constants", {}),
            "path": str(path),
        })
        if promoted:
            store.promote(con, version, provenance)

    rp = write_report(version, ds, results, chosen, test_metrics, horizons,
                      provenance, promoted, prev_info)
    log("%s  saved %s" % ("PROMOTED" if promoted else "research candidate only", path))
    log("report %s" % rp)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Train the Resolv market model")
    ap.add_argument("--provenance", choices=["live", "synthetic"], default="live")
    ap.add_argument("--seed", type=int, default=RANDOM_SEED)
    ap.add_argument("--force-promote", action="store_true")
    a = ap.parse_args(argv)
    return run(seed=a.seed, force_promote=a.force_promote, provenance=a.provenance)


if __name__ == "__main__":
    sys.exit(main())
