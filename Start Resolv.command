#!/bin/zsh
RESOLV_ROOT="${0:A:h}"
cd "$RESOLV_ROOT/Backend"
export RESOLV_ML_HOME="$RESOLV_ROOT/runtime"
RESOLV_URL="http://127.0.0.1:${RESOLV_PORT:-8899}"
if curl -fsS "$RESOLV_URL/api/model" >/dev/null 2>&1; then
  open "$RESOLV_URL"
  exit 0
fi
PYTHON="$RESOLV_ROOT/MLModel/.venv/bin/python"
[[ -x "$PYTHON" ]] || PYTHON=python3
"$PYTHON" -m resolv_engine.app &
RESOLV_PID=$!
trap 'kill "$RESOLV_PID" 2>/dev/null' EXIT INT TERM
for attempt in {1..30}; do
  if curl -fsS "$RESOLV_URL/health" >/dev/null 2>&1; then
    open "$RESOLV_URL"
    break
  fi
  sleep 0.2
done
wait "$RESOLV_PID"
