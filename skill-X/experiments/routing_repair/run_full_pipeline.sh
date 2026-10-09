#!/bin/bash
# run_full_pipeline.sh — Run the full routing repair benchmark pipeline
# Phase 2+3 (queries + repairs) → Phase 4 (routing benchmark) → Phase 5 (analysis)
#
# Usage: nohup bash run_full_pipeline.sh > results/pipeline.log 2>&1 &

set -e
cd "$(dirname "$0")"

echo "========================================================================"
echo "ROUTING REPAIR BENCHMARK — Full Pipeline"
echo "Start: $(date)"
echo "========================================================================"

# Phase 2+3: Generate queries and repairs for all 110 skills
echo ""
echo "[PHASE 2+3] Generating queries and repairs..."
python3 generate_queries_and_repairs.py
echo "[PHASE 2+3] COMPLETE at $(date)"

# Phase 4: Run routing benchmark (single router first for speed)
echo ""
echo "[PHASE 4] Running routing benchmark (openclaw_default router)..."
python3 run_routing_benchmark.py --routers openclaw_default
echo "[PHASE 4 / Router 1] COMPLETE at $(date)"

# Phase 4b: Run with second router (if first succeeded)
if [ -f results/routing_results.jsonl ]; then
    echo ""
    echo "[PHASE 4b] Running routing benchmark (gpt54_mini router)..."
    python3 run_routing_benchmark.py --routers gpt54_mini
    echo "[PHASE 4b / Router 2] COMPLETE at $(date)"
fi

# Phase 5: Analysis
echo ""
echo "[PHASE 5] Running analysis..."
python3 analyze_results.py
echo "[PHASE 5] COMPLETE at $(date)"

echo ""
echo "========================================================================"
echo "FULL PIPELINE COMPLETE at $(date)"
echo "========================================================================"
