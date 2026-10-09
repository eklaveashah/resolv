#!/usr/bin/env bash
# One-time setup. Creates .venv and installs the training dependencies.
set -euo pipefail
cd "$(dirname "$0")"
python3 -m venv .venv
./.venv/bin/pip install --quiet --upgrade pip
./.venv/bin/pip install --quiet -r requirements.txt
echo "ready. python at ./.venv/bin/python"
./.venv/bin/python -c "import numpy, sklearn; print('numpy', numpy.__version__, 'sklearn', sklearn.__version__)"
