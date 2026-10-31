#!/usr/bin/env bash
# Reports how many CPU seconds each process burned over a window, so the two edges can be compared by cost rather than only by latency.
#
# Throughput alone cannot settle the edge question: if both edges keep up, they look the same, and the difference only shows as how hard each one had to work. This takes a reading before and after and prints the difference.
# Usage: ./scripts/measure_cpu.sh 60      (run it alongside a load run of the same length)
set -u
cd "$(dirname "$0")/.."

seconds=${1:-60}

# The pid file names the uv wrapper, whose child does the actual serving, so both are summed. For nginx the file names the master and the worker is its child, which is the same shape.
cpu_of () {
    local pid=$1 total=0 t
    for p in $pid $(pgrep -P "$pid" 2>/dev/null); do
        t=$(ps -o cputime= -p "$p" 2>/dev/null | tr -d ' ')
        [ -z "$t" ] && continue
        total=$(python3 -c "
parts = '$t'.split(':')
seconds = float(parts[-1]) + 60 * int(parts[-2] or 0) + 3600 * int(parts[-3]) if len(parts) == 3 else float(parts[-1]) + 60 * int(parts[-2] or 0)
print(f'{$total + seconds:.2f}')")
    done
    echo "$total"
}

names=(checkout inventory analytics)
[ -f logs/nginx.pid ] && edge=nginx || edge=gateway
pidfile () { [ "$1" = nginx ] && echo logs/nginx.pid || echo "logs/$1.pid"; }

declare -a before
all=("$edge" "${names[@]}")
for i in "${!all[@]}"; do
    f=$(pidfile "${all[$i]}")
    before[$i]=$([ -f "$f" ] && cpu_of "$(cat "$f")" || echo 0)
done

echo "sampling for ${seconds}s with the $edge edge"
sleep "$seconds"

printf "\n  %-12s %10s\n" "process" "CPU sec"
for i in "${!all[@]}"; do
    f=$(pidfile "${all[$i]}")
    after=$([ -f "$f" ] && cpu_of "$(cat "$f")" || echo 0)
    printf "  %-12s %10.2f\n" "${all[$i]}" "$(python3 -c "print(${after} - ${before[$i]})")"
done
