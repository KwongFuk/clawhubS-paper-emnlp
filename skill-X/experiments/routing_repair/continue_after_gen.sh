#!/bin/bash
# continue_after_gen.sh — Wait for Phase 2+3, then run Phase 4+5
# Run this after Phase 2+3 (PID 36695) is in progress:
#   nohup bash continue_after_gen.sh > results/continue.log 2>&1 &

set -e
cd "$(dirname "$0")"

GEN_PID=${1:-36695}

echo "Waiting for Phase 2+3 (PID $GEN_PID) to complete..."
while kill -0 "$GEN_PID" 2>/dev/null; do
    CACHE_COUNT=$(ls .cache_gen/ 2>/dev/null | wc -l)
    echo "  $(date +%H:%M:%S) — cache files: $CACHE_COUNT"
    sleep 60
done
echo "Phase 2+3 completed at $(date)"

# Verify outputs exist
if [ ! -f results/benchmark_queries.json ] || [ ! -f results/benchmark_repairs.json ]; then
    echo "ERROR: Phase 2+3 output files missing!"
    exit 1
fi

echo ""
echo "========================================================================"
echo "[PHASE 4] Running routing benchmark — Router 1 (openclaw_default)"
echo "Start: $(date)"
echo "========================================================================"
python3 run_routing_benchmark.py --routers openclaw_default

echo ""
echo "[PHASE 4] Running routing benchmark — Router 2 (gpt54_mini)"
python3 run_routing_benchmark.py --routers gpt54_mini

echo ""
echo "========================================================================"
echo "[PHASE 5] Running analysis"
echo "========================================================================"
python3 analyze_results.py

echo ""
echo "PIPELINE COMPLETE at $(date)"
