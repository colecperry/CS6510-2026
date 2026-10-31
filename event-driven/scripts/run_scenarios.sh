#!/usr/bin/env bash
# Runs the three outage scenarios that justify having added a broker at all.
#
# Each one stops a part of the system, drives real traffic through the edge, brings the part back and checks that nothing was lost. The point is not that the system survives: it is that a shopper never finds out, which is why every scenario checks the shopper's response first and the eventual state second.
# Resets the databases and the queues before each scenario, so one scenario's backlog cannot explain another's result.
set -u
cd "$(dirname "$0")/.."

# A quorum queue's depth is served from a cached view that refreshes every few seconds, so sampling it immediately after publishing reports zero for a queue that is filling up. The pause is the difference between reading the broker and reading a stale summary of it.
queue_depth () {
    sleep 6
    docker exec self-checkout-broker rabbitmqctl list_queues --quiet name messages 2>/dev/null | grep "^$1" || echo "$1 (absent)"
}

outbox_pending () {
    docker exec self-checkout-db-checkout-ed psql -U checkout -d self_checkout -t -A \
        -c "SELECT count(*) FROM outbox WHERE sent_at IS NULL"
}

fresh () {
    ./scripts/stop_all.sh >/dev/null 2>&1
    ./scripts/reset_db.sh >/dev/null
    ./scripts/start_all.sh >/dev/null
}

echo "=============================================================="
echo "Scenario 1: inventory is down while shoppers keep paying"
echo "=============================================================="
fresh
./scripts/stop_one.sh inventory
echo "  20 baskets, 3 items each, with nothing listening for the sales:"
uv run python scripts/buy.py 20 3
echo "  sales waiting in the broker: $(queue_depth inventory.sales)"
echo "  messages checkout still owes: $(outbox_pending)"
./scripts/stop_one.sh inventory --start
sleep 3
echo "  after inventory comes back:"
echo "  sales waiting in the broker: $(queue_depth inventory.sales)"
./scripts/check_invariant.sh

echo
echo "=============================================================="
echo "Scenario 2: the broker is down while shoppers keep paying"
echo "=============================================================="
fresh
docker compose stop rabbitmq >/dev/null 2>&1
echo "  20 baskets, 3 items each, with nowhere to announce them:"
uv run python scripts/buy.py 20 3
echo "  messages checkout still owes: $(outbox_pending)"
docker compose start rabbitmq >/dev/null 2>&1
echo "  broker back, waiting for the relay to catch up"
sleep 25
echo "  messages checkout still owes: $(outbox_pending)"
./scripts/check_invariant.sh

echo
echo "=============================================================="
echo "Scenario 3: analytics is down while shoppers keep scanning"
echo "=============================================================="
fresh
echo "  scan latency with analytics running:"
uv run python scripts/scan_latency.py 10
./scripts/stop_one.sh analytics
echo "  scan latency with analytics stopped:"
uv run python scripts/scan_latency.py 30
echo "  scans waiting in the broker: $(queue_depth analytics.scans)"
./scripts/stop_one.sh analytics --start
sleep 5
echo "  scans waiting in the broker: $(queue_depth analytics.scans)"
echo "  the ranking after analytics catches up:"
curl -s "http://localhost:8080/analytics/popular-items?limit=3" | python3 -m json.tool | head -20
