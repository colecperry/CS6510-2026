#!/usr/bin/env bash
# Stops the three services and whichever edge is running.
#
# Uses the process ids start_all.sh recorded, then falls back to whatever is still listening on the four ports. The fallback matters because a crashed or hand-started process leaves no pid file, and a leftover server holding 8080 would silently serve the next benchmark run.
cd "$(dirname "$0")/.."

# nginx has to be asked to quit rather than killed by pid, because the pid file names a master process that would leave its worker behind.
if [ -f logs/nginx.pid ]; then
    nginx -p "$PWD/edge" -c "$PWD/edge/nginx.conf" -s quit 2>/dev/null && echo "  stopped nginx"
    rm -f logs/nginx.pid
fi

for name in gateway analytics inventory checkout; do
    if [ -f "logs/$name.pid" ]; then
        pid=$(cat "logs/$name.pid")
        if kill "$pid" 2>/dev/null; then
            printf "  stopped %-10s pid %s\n" "$name" "$pid"
        fi
        rm -f "logs/$name.pid"
    fi
done

# Catch anything still listening, whatever started it. Only listeners: without -sTCP:LISTEN this also matches whoever has an open connection to one of these ports.
leftover=$(lsof -ti tcp:8080,tcp:8081,tcp:8082,tcp:8083 -sTCP:LISTEN 2>/dev/null || true)
if [ -n "$leftover" ]; then
    echo "  killing leftovers on the four ports: $leftover"
    echo "$leftover" | xargs kill -9 2>/dev/null || true
fi

rm -f logs/edge
echo "all stopped"
