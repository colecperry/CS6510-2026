#!/usr/bin/env bash
# One command to reset all three databases between test runs.
# Exits non-zero if any store is missing or if their catalogue copies disagree, since a silent mismatch would corrupt the comparison.
set -e
cd "$(dirname "$0")/.."
uv run python -m db.seed
