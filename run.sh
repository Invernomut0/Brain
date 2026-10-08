#!/usr/bin/env bash
# Starts Brain (backend + built dashboard).
set -euo pipefail
cd "$(dirname "$0")/backend"
exec ../.venv/bin/python -m brain.main
