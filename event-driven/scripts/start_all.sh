#!/usr/bin/env bash
# Starts all four processes in the background and waits until each answers.
#
# Convenience only. Each service is independently deployable and can be started on its own; this just saves four terminals. Logs go to logs/, process ids to logs/*.pid so stop_all.sh can find them.
set -e
cd "$(dirname "$0")/.."
mkdir -p logs

start() {
    local name=$1 port=$2
    uv run --directory "$name" python -m app.serve > "logs/$name.log" 2>&1 &
    echo $! > "logs/$name.pid"
    printf "  %-10s port %s  pid %s\n" "$name" "$port" "$(cat logs/$name.pid)"
}

echo "starting:"
# Services first, so the gateway has something to forward to by the time it accepts anything.
start checkout 8081
start inventory 8082
start analytics 8083
start gateway 8080

echo "waiting for all four to answer:"
for pair in "checkout 8081 /transactions/none" "inventory 8082 /items" "analytics 8083 /analytics/popular-items" "gateway 8080 /items"; do
    set -- $pair
    name=$1 port=$2 path=$3
    for i in $(seq 1 40); do
        # Any HTTP answer means it is up. A 404 from checkout for a missing basket still proves it is listening.
        if curl -s -m 2 -o /dev/null "http://localhost:$port$path" 2>/dev/null; then
            printf "  %-10s ready after %ss\n" "$name" "$i"
            break
        fi
        if [ "$i" = 40 ]; then
            echo "  $name did not come up. See logs/$name.log"
            exit 1
        fi
        sleep 1
    done
done

echo
echo "all four up. the load client should target http://localhost:8080"
