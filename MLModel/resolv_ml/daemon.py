"""The loop that keeps the model learning.

One cycle: collect whatever settled since last time, rebuild the dataset,
retrain, and promote the challenger only if it genuinely beats the champion
out of sample. Nothing is deleted, so the corpus and the model both improve
as more markets resolve.

Run it in a terminal with --interval, or once per cycle from launchd/cron.
This machine has to be awake for it to run. Nothing keeps training while the
laptop is shut.
"""
import argparse
import signal
import sys
import time
import traceback
from pathlib import Path

from . import collect, store, train
from .config import REPORT_DIR

LOG = REPORT_DIR / "training.log"
_stop = {"now": False}


def _handle(signum, frame):
    _stop["now"] = True
    say("signal %s received, finishing this cycle then stopping" % signum)


def say(msg: str) -> None:
    line = "[%s] %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
    print(line, flush=True)
    try:
        with open(LOG, "a") as f:
            f.write(line + "\n")
    except OSError:
        pass


def cycle(pages: int, histories: int) -> dict:
    out = {"collected": 0, "trained": False, "error": None}
    store.init()
    try:
        with store.connect() as con:
            before = store.stats(con)
            if pages > 0:
                collect.collect_polymarket(con, pages)
                collect.collect_kalshi(con, pages)
            if histories > 0:
                collect.collect_histories(con, limit=histories)
            after = store.stats(con)
        out["collected"] = after["resolved"] - before["resolved"]
        say("collected %d new resolved markets (store now %d resolved, %d price points)"
            % (out["collected"], after["resolved"], after["price_points"]))
    except Exception as e:
        out["error"] = "collect: %s" % e
        say("collection failed: %s" % e)

    try:
        rc = train.run()
        out["trained"] = (rc == 0)
        if rc == 2:
            say("skipped training, not enough data yet")
    except Exception:
        out["error"] = "train"
        say("training raised:\n%s" % traceback.format_exc())
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Continuous collect and retrain")
    ap.add_argument("--interval", type=int, default=0,
                    help="seconds between cycles. 0 runs one cycle and exits, "
                         "which is what you want under launchd or cron.")
    ap.add_argument("--pages", type=int, default=6, help="market pages per venue per cycle")
    ap.add_argument("--histories", type=int, default=250, help="price paths per cycle")
    ap.add_argument("--max-cycles", type=int, default=0, help="0 means forever")
    a = ap.parse_args(argv)

    signal.signal(signal.SIGINT, _handle)
    signal.signal(signal.SIGTERM, _handle)

    n = 0
    while True:
        n += 1
        say("cycle %d start" % n)
        r = cycle(a.pages, a.histories)
        say("cycle %d done: %s" % (n, r))
        if a.interval <= 0 or _stop["now"]:
            break
        if a.max_cycles and n >= a.max_cycles:
            break
        say("sleeping %ds" % a.interval)
        slept = 0
        while slept < a.interval and not _stop["now"]:
            time.sleep(min(5, a.interval - slept))
            slept += 5
        if _stop["now"]:
            break
    say("stopped after %d cycle(s)" % n)
    return 0


if __name__ == "__main__":
    sys.exit(main())
