#!/bin/zsh
cd "${0:A:h}/MLModel"
export RESOLV_ML_HOME="${0:A:h}/runtime"
if [[ ! -x .venv/bin/python ]]; then
  ./setup.sh || exit 1
fi
./.venv/bin/python -m resolv_ml.collect --pages 3 --histories 150
./.venv/bin/python -m resolv_ml.backfill --events 20 --strikes 5
./.venv/bin/python -m resolv_ml.train
printf '\nTraining finished. Reports are in Resolv/runtime/reports.\n'
read -r '?Press Enter to close.'
