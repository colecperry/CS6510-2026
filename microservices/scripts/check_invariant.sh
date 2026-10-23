#!/usr/bin/env bash
# Checks after a load run that every unit sold actually came off the shelf.
# Exits non-zero on any unexplained discrepancy, or on a payment left unfinished, so a bad run cannot be mistaken for a good one.
set -e
cd "$(dirname "$0")/.."
uv run python -m db.check_invariant
