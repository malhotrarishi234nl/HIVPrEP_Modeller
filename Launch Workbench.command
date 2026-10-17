#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

if [ ! -x .venv-workbench/bin/python ]; then
  echo "The workbench environment is missing. Run 'Setup Workbench.command' first."
  read -r -p "Press Return to close."
  exit 1
fi

export MPLCONFIGDIR="$SCRIPT_DIR/outputs/workbench/.matplotlib"
mkdir -p "$MPLCONFIGDIR"
exec .venv-workbench/bin/python -m streamlit run workbench_ui/app.py \
  --server.address 127.0.0.1
