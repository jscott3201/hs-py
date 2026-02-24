#!/usr/bin/env bash
# run_benchmarks.sh — Orchestrate all benchmark runs
#
# Runs HTTP and WebSocket throughput benchmarks against each backend
# (InMemory, Redis, TimescaleDB) with 3 client containers each.
#
# Results are written to benchmarks/results/ as JSON files and
# aggregated into a summary.

set -euo pipefail
cd "$(dirname "$0")"

COMPOSE="docker compose -f docker-compose.yml"
RESULTS_DIR="./results"

rm -rf "$RESULTS_DIR"
mkdir -p "$RESULTS_DIR"

echo "============================================================"
echo "  hs-py Benchmark Suite"
echo "============================================================"
echo ""
echo "  Server RAM limit:  3 GB"
echo "  Client RAM limit:  0.75 GB (×3 per test)"
echo "  HTTP concurrency:  50 connections per client"
echo "  WS concurrency:    20 connections per client"
echo "  Duration:          30s per test (+ 5s warmup)"
echo "  Data:              3,109 entities (Alpha + Bravo)"
echo "  Formats decoded:   JSON, Trio, Zinc"
echo ""

# Build images
echo ">>> Building images..."
$COMPOSE build --quiet 2>&1 | tail -5
echo ""

# ---------------------------------------------------------------------------
# Helper: run one backend benchmark
# ---------------------------------------------------------------------------
run_backend() {
    local backend="$1"
    local server="server-$backend"

    echo "============================================================"
    echo "  Backend: $backend"
    echo "============================================================"

    # Start infrastructure + server
    echo ">>> Starting $server..."
    if [ "$backend" = "redis" ]; then
        $COMPOSE up -d --wait redis "$server" 2>&1 | grep -E "Healthy|Started|Error" || true
    elif [ "$backend" = "timescale" ]; then
        $COMPOSE up -d --wait timescaledb "$server" 2>&1 | grep -E "Healthy|Started|Error" || true
    else
        $COMPOSE up -d --wait "$server" 2>&1 | grep -E "Healthy|Started|Error" || true
    fi

    # HTTP benchmark — run 3 clients sequentially (parallel causes resource contention)
    echo ""
    echo "--- HTTP benchmark ($backend) ---"
    for i in 1 2 3; do
        echo "  Client $i..."
        $COMPOSE run --rm "http-${backend}-${i}" 2>&1 | grep -E "rps|error|Results"
    done
    echo ""

    # WebSocket benchmark — run 3 clients sequentially
    echo "--- WebSocket benchmark ($backend) ---"
    for i in 1 2 3; do
        echo "  Client $i..."
        $COMPOSE run --rm "ws-${backend}-${i}" 2>&1 | grep -E "messages_per_sec|error|Results"
    done
    echo ""

    # Stop the server
    echo ">>> Stopping $server..."
    $COMPOSE stop "$server" 2>&1 | tail -2
    echo ""
}

# ---------------------------------------------------------------------------
# Run benchmarks for each backend
# ---------------------------------------------------------------------------
run_backend "inmemory"
run_backend "redis"
run_backend "timescale"

# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------
echo ">>> Stopping all containers..."
$COMPOSE down -v --remove-orphans 2>&1 | tail -3
echo ""

# ---------------------------------------------------------------------------
# Aggregate results
# ---------------------------------------------------------------------------
echo "============================================================"
echo "  Aggregating results..."
echo "============================================================"
python3 aggregate_results.py
echo ""
echo "Results written to $RESULTS_DIR/"
echo "Done."
