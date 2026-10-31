#!/usr/bin/env bash
# Stops or starts a single service, leaving the other three alone.
#
# Exists for the outage scenarios. stop_all.sh takes the whole system down, which proves nothing: the claim being tested is that one service can be missing while the rest keeps serving shoppers.
# Usage: ./scripts/stop_one.sh inventory        ./scripts/stop_one.sh inventory --start
set -u
cd "$(dirname "$0")/.."

name=${1:-}
action=${2:-stop}

case "$name" in
    checkout)  port=8081 ;;
    inventory) port=8082 ;;
    analytics) port=8083 ;;
    gateway)   port=8080 ;;
    *) echo "usage: $0 {checkout|inventory|analytics|gateway} [--start]"; exit 2 ;;
esac

if [ "$action" = "--start" ]; then
    mkdir -p logs
    uv run --directory "$name" python -m app.serve > "logs/$name.log" 2>&1 &
    echo $! > "logs/$name.pid"
    for i in $(seq 1 40); do
        if curl -s -m 2 -o /dev/null "http://localhost:$port/" 2>/dev/null; then
            echo "  $name back up on $port after ${i}s"
            exit 0
        fi
        sleep 1
    done
    echo "  $name did not come back. See logs/$name.log"
    exit 1
fi

# The pid file first, then whatever still holds the port. A service started by hand leaves no pid file, and one left running would make the outage scenario silently test nothing.
if [ -f "logs/$name.pid" ]; then
    kill "$(cat logs/$name.pid)" 2>/dev/null
    rm -f "logs/$name.pid"
fi
# -sTCP:LISTEN matters and is not tidiness. Without it lsof also lists whoever has an open connection to this port, which here is the gateway, and stopping one service would quietly stop the edge with it.
leftover=$(lsof -ti tcp:$port -sTCP:LISTEN 2>/dev/null || true)
if [ -n "$leftover" ]; then
    echo "$leftover" | xargs kill -9 2>/dev/null || true
fi

# Confirm it is really gone, so a scenario cannot report success against a service that was still answering.
if curl -s -m 2 -o /dev/null "http://localhost:$port/" 2>/dev/null; then
    echo "  $name is still answering on $port"
    exit 1
fi
echo "  $name stopped"
