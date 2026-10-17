#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

if ! command -v python3.11 >/dev/null 2>&1; then
  echo "Python 3.11 is required. Install Python 3.11, then run this file again."
  read -r -p "Press Return to close."
  exit 1
fi

python3.11 -m venv .venv-workbench
.venv-workbench/bin/python -m pip install --upgrade pip
.venv-workbench/bin/python -m pip install -r requirements-workbench.txt

echo
echo "Setup complete. Double-click 'Launch Workbench.command'."
read -r -p "Press Return to close."
