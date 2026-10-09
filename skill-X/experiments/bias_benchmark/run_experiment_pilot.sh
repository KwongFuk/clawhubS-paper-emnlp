#!/bin/bash
# run_experiment_pilot.sh — Pilot experiment runner
# Run a quick validation with 10 queries × 5 rotations × 1 cluster = 50 calls
# Use this to validate the pipeline before launching the full experiment.
#
# Usage:
#   # On local Mac:
#   OPENAI_API_KEY=sk-... bash run_experiment_pilot.sh
#
#   # On Palmetto (via SSH proxy):
#   export OPENAI_API_KEY=sk-...
#   export OPENAI_BASE_URL=http://localhost:8089/v1  # via SSH proxy
#   bash run_experiment_pilot.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "============================================"
echo "  Bias Benchmark — Pilot Run"
echo "============================================"
echo ""

# Verify prerequisites
if [ -z "${OPENAI_API_KEY:-}" ]; then
    echo "ERROR: OPENAI_API_KEY not set"
    echo "  export OPENAI_API_KEY=sk-..."
    exit 1
fi

if [ ! -f "clusters_final.json" ]; then
    echo "ERROR: clusters_final.json not found. Run build_clusters.py first."
    exit 1
fi

if [ ! -d "queries" ]; then
    echo "Generating pilot queries..."
    python3 generate_sample_queries.py
fi

echo ""
echo "Step 1: Running pilot on web_search cluster..."
python3 run_bias_experiment.py \
    --cluster web_search \
    --model gpt-4.1-mini \
    --run 1 \
    --delay 0.2

echo ""
echo "Step 2: Running pilot on image_generation cluster..."
python3 run_bias_experiment.py \
    --cluster image_generation \
    --model gpt-4.1-mini \
    --run 1 \
    --delay 0.2

echo ""
echo "============================================"
echo "  Pilot complete! Check results/ directory"
echo "============================================"
echo ""
echo "Next steps:"
echo "  1. Verify results in results/exp1_gpt-4.1-mini_run1.json"
echo "  2. Generate full 100 queries: python generate_queries.py --all"
echo "  3. Run full benchmark: python run_bias_experiment.py --all --model gpt-4.1-mini --run 1"
