
#!/usr/bin/env bash

# Stop immediately if any command fails.
set -euo pipefail

# Move to the project root.
cd "$(dirname "$0")/.."

REDIS_CLI="$HOME/redis-stable/src/redis-cli"
SERVER_PID=""

mkdir -p benchmarks/results

# Stop the benchmark server after each trial.
stop_server() {
    if [ -n "$SERVER_PID" ]; then
        kill -TERM "$SERVER_PID" 2>/dev/null || true
        wait "$SERVER_PID" 2>/dev/null || true
        SERVER_PID=""
    fi
}

# Make sure the server is stopped if the script exits.
trap stop_server EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# Make sure we're using the isolated benchmark Redis instance.
REDIS_DIR=$("$REDIS_CLI" -p 6380 --raw CONFIG GET dir | tail -n 1)

if [ "$REDIS_DIR" != "$HOME/redis-benchmark" ]; then
    echo "Incorrect Redis directory. Aborting."
    exit 1
fi

# Make sure another server isn't already using our port.
if lsof -iTCP:8001 -sTCP:LISTEN >/dev/null 2>&1; then
    echo "Port 8001 is already in use. Stop Uvicorn first."
    exit 1
fi

# Do not overwrite any previous benchmark results.
for trial in {101..107}; do
    if [ -e "benchmarks/results/trial_$trial" ]; then
        echo "Trial $trial already exists. Aborting."
        exit 1
    fi
done

# Execute seven independent benchmark trials.
for trial in {101..107}; do

    echo ""
    echo "======================================"
    echo "STARTING TRIAL $trial"
    echo "======================================"

    # Reset the benchmark database.
    psql -v ON_ERROR_STOP=1 -d rush_benchmark \
        -c "TRUNCATE TABLE campaigns RESTART IDENTITY CASCADE;"

    # Reset benchmark Redis and initialize the index version.
    "$REDIS_CLI" -p 6380 FLUSHDB
    "$REDIS_CLI" -p 6380 SET rush:index:version 0

    # Start four fresh workers with profiling disabled.
    DATABASE_URL="postgresql+psycopg:///rush_benchmark" \
    REDIS_URL="redis://127.0.0.1:6380/0" \
    RUSH_LEASE_SIZE=100 \
    RUSH_PROFILE=0 \
    python -m uvicorn app.main:app \
        --host 127.0.0.1 \
        --port 8001 \
        --workers 4 \
        > "benchmarks/results/server_trial_$trial.log" 2>&1 &

    SERVER_PID=$!

    # Wait until the API becomes available.
    READY=0

    for attempt in {1..60}; do
        if curl -fsS --max-time 1 \
            http://127.0.0.1:8001/health \
            >/dev/null 2>&1; then
            READY=1
            break
        fi

        sleep 1
    done

    if [ "$READY" -ne 1 ]; then
        echo "Server failed to start."
        exit 1
    fi

    # Allow the remaining workers to finish starting.
    sleep 3

    # Create and activate all 105 campaigns.
    python benchmarks/seed_campaigns.py

    # Execute warm-up, primary and replay traffic.
    python benchmarks/locustfile.py --trial "$trial"

    # Verify correctness before resetting any state.
    python benchmarks/audit_results.py --trial "$trial"

    # Stop all workers and discard worker-local state.
    stop_server

    echo "TRIAL $trial COMPLETED"

    # Allow a short cooldown between independent trials.
    sleep 5

done

echo ""
echo "ALL SEVEN BENCHMARK TRIALS COMPLETED"
