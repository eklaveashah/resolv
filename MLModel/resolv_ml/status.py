"""Where does the model stand right now."""
import json
import sys
import time
from pathlib import Path

from . import store
from .config import DB_PATH, MODEL_DIR


def fmt_ts(t):
    return time.strftime("%Y-%m-%d", time.gmtime(t)) if t else "n/a"


def main(argv=None) -> int:
    store.init()
    with store.connect() as con:
        s = store.stats(con)
        models = con.execute(
            "SELECT * FROM models ORDER BY created_ts DESC LIMIT 8").fetchall()
        champ_live = store.champion(con, "live")
        champ_syn = store.champion(con, "synthetic")
        recent = con.execute(
            """SELECT venue, kind, last_run_ts, ok, note FROM collect_log
               ORDER BY id DESC LIMIT 5""").fetchall()

    print("Resolv ML")
    print("=" * 64)
    print("store            %s" % DB_PATH)
    print("markets          %d  (%d resolved, %d with a price path)"
          % (s["markets"], s["resolved"], s["with_prices"]))
    print("price points     %d" % s["price_points"])
    print("venues           polymarket %d   kalshi %d" % (s["polymarket"], s["kalshi"]))
    print("resolutions span %s to %s"
          % (fmt_ts(s["earliest_resolve"]), fmt_ts(s["latest_resolve"])))
    print()

    if champ_live is None and champ_syn is None:
        print("NO TRAINED MODEL YET.")
        print("  1. python3 -m resolv_ml.collect")
        print("  2. python3 -m resolv_ml.train")
        return 0

    for label, c in (("live", champ_live), ("synthetic", champ_syn)):
        if c is None:
            print("champion (%s)   none" % label)
            continue
        m = json.loads(c["metrics"])
        print("champion (%s)   %s" % (label, c["version"]))
        print("  algo           %s" % c["algo"])
        print("  test Brier     %.5f   market %.5f   skill %+.4f"
              % (m.get("brier", 0), m.get("brier_market", 0),
                 m.get("brier_skill_score", 0)))
        print("  samples        %d train / %d test" % (c["n_train"], c["n_test"]))
    print()

    if champ_live is None and champ_syn is not None:
        print("WARNING: the only model was fitted on generated data. It proves the")
        print("pipeline runs. It predicts nothing. Collect real markets before")
        print("using any number from it.")
        print()

    print("recent models")
    for r in models:
        m = json.loads(r["metrics"])
        print("  %-42s %-14s brier %.5f %s"
              % (r["version"], r["provenance"], m.get("brier", 0),
                 "<- champion" if r["is_champion"] else ""))
    print()
    print("recent collection attempts")
    for r in recent:
        print("  %-12s %-9s %s  %s  %s"
              % (r["venue"], r["kind"], fmt_ts(r["last_run_ts"]),
                 "ok" if r["ok"] else "FAILED", r["note"] or ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
