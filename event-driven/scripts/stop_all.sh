#!/usr/bin/env bash
# Stops all four processes.
#
# Uses the process ids start_all.sh recorded, then falls back to whatever is holding the four ports. The fallback matters because a crashed or hand-started process leaves no pid file, and a leftover server holding port 8080 would silently serve the next benchmark run.
cd "$(dirname "$0")/.."

for name in gateway analytics inventory checkout; do
    if [ -f "logs/$name.pid" ]; then
        pid=$(cat "logs/$name.pid")
        if kill "$pid" 2>/dev/null; then
            printf "  stopped %-10s pid %s\n" "$name" "$pid"
        fi
        rm -f "logs/$name.pid"
    fi
done

# Catch anything still holding a port, whatever started it.
leftover=$(lsof -ti:8080 -ti:8081 -ti:8082 -ti:8083 2>/dev/null || true)
if [ -n "$leftover" ]; then
    echo "  killing leftovers on the four ports: $leftover"
    echo "$leftover" | xargs kill -9 2>/dev/null || true
fi

echo "all stopped"
