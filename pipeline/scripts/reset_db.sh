#!/usr/bin/env bash
set -e
# One command to fully reset the database between test runs.
cd "$(dirname "$0")/.."
uv run python -m db.seed
