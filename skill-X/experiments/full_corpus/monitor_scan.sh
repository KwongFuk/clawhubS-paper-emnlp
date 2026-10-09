#!/bin/bash
# monitor_scan.sh — Monitor the full-corpus scan job on Palmetto
# Usage: bash monitor_scan.sh

REMOTE="p"
WORK_DIR="~/clawhubS/skill-X/experiments/full_corpus"

echo "============================================================"
echo "SCAN MONITOR ($(date))"
echo "============================================================"

# Job status
echo ""
echo "=== SLURM Job Status ==="
ssh $REMOTE "squeue -u \$USER --format='%i %P %j %T %M %l %N' 2>/dev/null | head -10"

# Check output log
echo ""
echo "=== Recent Log (last 20 lines) ==="
ssh $REMOTE "tail -20 $WORK_DIR/results/scan_log.txt 2>/dev/null || echo 'No log yet'"

# Check progress
echo ""
echo "=== Batch Progress ==="
ssh $REMOTE "
cd $WORK_DIR 2>/dev/null
DONE=\$(ls results/scan_batches/scan_batch_*.json 2>/dev/null | wc -l)
TOTAL=369
echo \"Completed: \$DONE / \$TOTAL batches\"
if [ -f results/scan_progress.json ]; then
    python3 -c \"
import json
d = json.load(open('results/scan_progress.json'))
ic = d.get('global_ic_dist', {})
sev = d.get('global_severity_dist', {})
print(f'IC distribution: {ic}')
print(f'D1 severity:     {sev}')
print(f'LLM calls:       {d.get(\"total_calls\", 0)}')
print(f'Errors:          {d.get(\"total_errors\", 0)}')
\" 2>/dev/null
fi
"

# Check SLURM output
echo ""
echo "=== SLURM Output (last 10 lines) ==="
ssh $REMOTE "ls -t $WORK_DIR/results/slurm_*.out 2>/dev/null | head -1 | xargs tail -10 2>/dev/null || echo 'No SLURM output yet'"
