#!/usr/bin/env bash
# Performs all four load runs: the two graded ones against nginx, and the same two against the Python gateway for comparison.
#
# Four runs rather than two because this week changed two things at once, the communication style and the edge. One pair cannot say which of them moved a number, and two pairs differing only in the edge can.
# Everything is reset between runs, including the broker queues, so a backlog from one run cannot be delivered during the next and appear as that run's throughput.
set -e
cd "$(dirname "$0")/.."

CLIENT=../load-client

# The client uses virtual threads, so it needs Java 21 and will not run on 17. The Temurin 21 that earlier weeks named is an x86 build and will not start on this machine at all, so the arm64 one from Homebrew is preferred and anything already set is the fallback.
for candidate in /opt/homebrew/opt/openjdk@21 "${JAVA_HOME:-}"; do
    if [ -x "$candidate/bin/java" ] && "$candidate/bin/java" -version 2>&1 | grep -q 'version "21'; then
        export JAVA_HOME="$candidate"
        break
    fi
done
export PATH="$JAVA_HOME/bin:$PATH"
echo "using $(java -version 2>&1 | head -1) from $JAVA_HOME"

run_one () {
    local edge=$1 stations=$2 duration=$3 outdir=$4

    echo
    echo "=============================================================="
    echo "$edge edge, $stations stations, ${duration}s"
    echo "=============================================================="
    ./scripts/stop_all.sh >/dev/null
    ./scripts/reset_db.sh >/dev/null
    ./scripts/start_all.sh "--edge=$edge" >/dev/null

    # CPU is sampled over the run rather than after it, because what is being compared is how hard each edge worked to serve the same traffic.
    ./scripts/measure_cpu.sh "$duration" > "logs/cpu-$edge-$stations.txt" &
    local cpu_sampler=$!

    # Absolute, because the client runs from its own directory and would otherwise write the report beside itself.
    local reports; reports=$(cd "$outdir" && pwd)
    (cd "$CLIENT" && ./run.sh "--baseUrl=http://localhost:8080" "--stations=$stations" "--duration=$duration" "--reportDir=$reports")
    wait $cpu_sampler
    cat "logs/cpu-$edge-$stations.txt"

    # The invariant can only be judged once checkout has finished announcing what it sold, so this waits for the outbox rather than reading a system still catching up.
    ./scripts/check_invariant.sh
}

mkdir -p reports reports/comparison

# One pair can be re-run on its own, because a run spoiled by something touching the databases mid-flight should not cost the three good ones. --only=nginx or --only=python.
only=${1:---only=all}

if [ "$only" != "--only=python" ]; then
    run_one nginx  10  60  reports
    run_one nginx  100 120 reports
fi
if [ "$only" != "--only=nginx" ]; then
    run_one python 10  60  reports/comparison
    run_one python 100 120 reports/comparison
fi

echo
echo "runs complete"
ls -1 reports/*.json reports/comparison/*.json
