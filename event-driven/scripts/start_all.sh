#!/usr/bin/env bash
# Starts the three services and exactly one edge, then waits until everything answers.
#
# The edge is a choice this week: --edge=python runs the hand-written gateway from previous weeks, --edge=nginx runs nginx over the same three services. Both listen on 8080, so the load client never knows which one it is talking to, which is the point of measuring them against each other.
# Logs go to logs/, process ids to logs/*.pid so stop_all.sh can find them.
set -e
cd "$(dirname "$0")/.."
mkdir -p logs logs/nginx-tmp

edge=python
for arg in "$@"; do
    case "$arg" in
        --edge=nginx)  edge=nginx ;;
        --edge=python) edge=python ;;
        *) echo "usage: $0 [--edge=nginx|--edge=python]"; exit 2 ;;
    esac
done

# Refuse rather than warn. Two edges cannot both hold 8080, and the one that lost the race would be the one the report was named after.
if lsof -ti tcp:8080 -sTCP:LISTEN >/dev/null 2>&1; then
    echo "something is already listening on 8080. Run ./scripts/stop_all.sh first."
    exit 1
fi

start() {
    local name=$1 port=$2
    uv run --directory "$name" python -m app.serve > "logs/$name.log" 2>&1 &
    echo $! > "logs/$name.pid"
    printf "  %-10s port %s  pid %s\n" "$name" "$port" "$(cat logs/$name.pid)"
}

echo "starting:"
# Services first, so the edge has something to forward to by the time it accepts anything.
start checkout 8081
start inventory 8082
start analytics 8083

if [ "$edge" = "nginx" ]; then
    # -p sets the prefix every relative path in the config resolves against, which is how edge/nginx.conf stays free of absolute paths.
    nginx -p "$PWD/edge" -c "$PWD/edge/nginx.conf"
    printf "  %-10s port 8080  (native, %s)\n" "nginx" "$(nginx -v 2>&1 | sed 's/.*nginx\///')"
else
    uv run --directory edge/gateway python -m app.serve > logs/gateway.log 2>&1 &
    echo $! > logs/gateway.pid
    printf "  %-10s port 8080  pid %s\n" "gateway" "$(cat logs/gateway.pid)"
fi
echo "$edge" > logs/edge

echo "waiting for everything to answer:"
for pair in "checkout 8081 /transactions/none" "inventory 8082 /items" "analytics 8083 /analytics/popular-items" "edge 8080 /items"; do
    set -- $pair
    name=$1 port=$2 path=$3
    for i in $(seq 1 40); do
        # Any HTTP answer means it is up. A 404 from checkout for a missing basket still proves it is listening.
        if curl -s -m 2 -o /dev/null "http://localhost:$port$path" 2>/dev/null; then
            printf "  %-10s ready after %ss\n" "$name" "$i"
            break
        fi
        if [ "$i" = 40 ]; then
            echo "  $name did not come up. See logs/"
            exit 1
        fi
        sleep 1
    done
done

echo
echo "up, with the $edge edge. the load client should target http://localhost:8080"
