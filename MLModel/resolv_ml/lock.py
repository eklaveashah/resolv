"""One writer at a time on the training store.

The scheduled job, a manual run, schedule.sh swapping the runtime code and the
migration all write the same home. They coordinate through one lock file,
data/pipeline.lock, held with flock(2):

- the kernel releases it when the holder exits or crashes, so there is never a
  stale lock to clean up;
- macOS /usr/bin/lockf takes the same lock, so schedule.sh can wait on it
  (`lockf -k`; without -k lockf deletes the file when it is done).

The holder writes {pid, since, trigger} into the file so a refused caller can
say who is running. The server never takes this lock: it only reads the
training store and writes forward.sqlite.
"""
import errno
import fcntl
import hashlib
import json
import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

from . import config

PKG_DIR = Path(__file__).resolve().parent

# Paths this process already holds, so a CLI main() called from inside the
# daemon's cycle re-enters instead of refusing itself. flock is per open file,
# so without this a second open in the same process would see "busy".
_held = {}
_held_guard = threading.Lock()


class Busy(RuntimeError):
    """The lock is held by someone else. .holder is what they wrote, or {}."""

    def __init__(self, holder: Optional[dict]):
        self.holder = holder or {}
        super().__init__("pipeline busy: %s" % (_describe(self.holder),))


def _describe(h: dict) -> str:
    if not h:
        return "held (holder unknown)"
    since = h.get("since")
    when = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(since)) if since else "?"
    return "%s since %s (pid %s)" % (h.get("trigger", "?"), when, h.get("pid", "?"))


def _read(fd: int) -> dict:
    try:
        raw = os.pread(fd, 4096, 0)
        return json.loads(raw.decode("utf-8")) if raw.strip() else {}
    except (OSError, ValueError):
        return {}


@contextmanager
def pipeline_lock(trigger: str = "manual", wait_s: float = 0.0, path=None, poll_s: float = 1.0):
    """Hold the pipeline lock for the body. Raises Busy when another process
    holds it for longer than wait_s seconds."""
    path = Path(path or config.LOCK_PATH)
    key = str(path.resolve())
    me = threading.get_ident()
    with _held_guard:
        owner = _held.get(key)
        if owner and owner[0] == me:
            _held[key] = (me, owner[1] + 1)
            reentered = True
        else:
            reentered = False
    if reentered:
        try:
            yield
        finally:
            with _held_guard:
                o = _held[key]
                _held[key] = (o[0], o[1] - 1)
        return

    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path), os.O_RDWR | os.O_CREAT, 0o644)
    # A short grace even when wait_s is 0: holder() probes with a momentary
    # shared lock, and that probe must not make a starting cycle refuse.
    deadline = time.time() + max(0.3, wait_s)
    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as e:
                if e.errno not in (errno.EAGAIN, errno.EWOULDBLOCK, errno.EACCES):
                    raise
                if time.time() >= deadline:
                    raise Busy(_read(fd))
                left = deadline - time.time()
                time.sleep(max(0.02, min(left, 0.1 if wait_s <= 0.3 else poll_s)))
        with _held_guard:
            if key in _held:
                # Another thread of this process holds it.
                fcntl.flock(fd, fcntl.LOCK_UN)
                raise Busy(_read(fd))
            _held[key] = (me, 1)
        try:
            os.ftruncate(fd, 0)
            os.pwrite(fd, json.dumps({"pid": os.getpid(), "since": int(time.time()),
                                      "trigger": trigger}).encode("utf-8"), 0)
            yield
        finally:
            with _held_guard:
                _held.pop(key, None)
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def holder(path=None) -> Optional[dict]:
    """Who holds the lock right now, or None when it is free. Never creates
    the lock file and never blocks."""
    path = Path(path or config.LOCK_PATH)
    try:
        fd = os.open(str(path), os.O_RDONLY)
    except OSError:
        return None
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except OSError as e:
            if e.errno in (errno.EAGAIN, errno.EWOULDBLOCK, errno.EACCES):
                return _read(fd)
            raise
        fcntl.flock(fd, fcntl.LOCK_UN)
        return None
    finally:
        os.close(fd)


def code_version(pkg_dir=None) -> str:
    """12 hex digits of sha256 over the package's *.py files (name and bytes,
    sorted by name). Same code, same version, wherever it is copied."""
    h = hashlib.sha256()
    for p in sorted(Path(pkg_dir or PKG_DIR).glob("*.py")):
        h.update(p.name.encode("utf-8"))
        h.update(b"\0")
        h.update(p.read_bytes())
        h.update(b"\0")
    return h.hexdigest()[:12]


def runtime_build(home=None) -> Optional[dict]:
    """The BUILD file schedule.sh writes into the runtime home after a sync:
    {code_version, source, synced_ts, python}. None when absent."""
    p = Path(home or config.HOME_DIR) / "BUILD"
    try:
        text = p.read_text().strip()
    except OSError:
        return None
    try:
        d = json.loads(text)
        return d if isinstance(d, dict) else {"code_version": str(d)}
    except ValueError:
        return {"code_version": text.split()[0] if text else None}
