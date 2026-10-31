#!/usr/bin/env bash
# One command to reset all three databases and the broker between test runs.
# Exits non-zero if any store is missing or if their catalogue copies disagree, since a silent mismatch would corrupt the comparison.
set -e
cd "$(dirname "$0")/.."

uv run python -m db.seed

# The databases are only half the state this week. A message left over from the last run would reduce stock that this run never sold, so the queues are emptied too.
# Both are tolerated as missing, because the queues do not exist until a service has started once.
for queue in inventory.sales analytics.scans; do
  if docker exec self-checkout-broker rabbitmqctl purge_queue "$queue" >/dev/null 2>&1; then
    echo "  purged $queue"
  else
    echo "  $queue not present yet, nothing to purge"
  fi
done

echo
echo "Databases seeded and broker queues empty."
