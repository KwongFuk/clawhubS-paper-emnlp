#!/bin/bash
# run_all_expanded.sh — Launch expanded bias experiments
# 
# Run this from a SEPARATE terminal (not through Antigravity):
#   cd ~/codex/clawhubS/skill-X/experiments/bias_benchmark
#   bash run_all_expanded.sh
#
# The OpenClaw gateway conflicts with Antigravity, so this must run independently.

set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

LOG_DIR="$DIR/results/logs"
mkdir -p "$LOG_DIR"

echo "============================================"
echo "Expanded Bias Experiments (P9-P16)"
echo "Started: $(date)"
echo "============================================"

# H1: Performance & Cost Keywords (all 8 clusters)
echo ""
echo ">>> Starting H1: Performance/Cost Keywords..."
python3 run_expanded_experiments.py --hypothesis H1 2>&1 | tee "$LOG_DIR/h1_$(date +%Y%m%d_%H%M%S).log"

# H2: Brand Preference (4 clusters)  
echo ""
echo ">>> Starting H2: Self-Brand Preference..."
python3 run_expanded_experiments.py --hypothesis H2 2>&1 | tee "$LOG_DIR/h2_$(date +%Y%m%d_%H%M%S).log"

# H5+H6: Social Proof & Authority (4 clusters)
echo ""
echo ">>> Starting H5+H6: Social Proof & Authority..."
python3 run_expanded_experiments.py --hypothesis H5H6 2>&1 | tee "$LOG_DIR/h5h6_$(date +%Y%m%d_%H%M%S).log"

echo ""
echo "============================================"
echo "All experiments complete! $(date)"
echo "============================================"

# Run analysis
echo ""
echo ">>> Running analysis..."
python3 analyze_expanded.py 2>&1 | tee "$LOG_DIR/analysis_$(date +%Y%m%d_%H%M%S).log"

echo ""
echo "Done! Check results in: $DIR/results/"
