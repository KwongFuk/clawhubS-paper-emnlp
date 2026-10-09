#!/bin/bash
# run_all_experiments.sh — Run EXP-1 remaining + EXP-2 perturbation upon quota reset
# Usage: nohup bash run_all_experiments.sh > results/full_exp_log.txt 2>&1 &

cd "$(dirname "$0")"

echo "$(date): Starting full experiment pipeline..."
echo "============================================================"

# Phase 1: Complete EXP-1 remaining clusters
echo ""
echo "$(date): Phase 1 — EXP-1 remaining clusters"
echo "============================================================"
PYTHONUNBUFFERED=1 python3 -u run_all_clusters.py 2>&1

# Brief cooldown before EXP-2
echo ""
echo "$(date): Cooling down 30s before EXP-2..."
sleep 30

# Phase 2: EXP-2 perturbation experiments
echo ""
echo "$(date): Phase 2 — EXP-2 perturbation experiments"
echo "============================================================"
PYTHONUNBUFFERED=1 python3 -u run_perturbation_exp.py 2>&1

echo ""
echo "$(date): All experiments complete!"
echo "============================================================"

# Run analysis
echo ""
echo "$(date): Running analysis..."
python3 analyze_exp1.py 2>&1
