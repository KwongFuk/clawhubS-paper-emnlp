#!/bin/bash
# resume_experiment.sh — Wait for Gemini quota reset, then run all experiments
# Usage: nohup bash resume_experiment.sh > results/exp_resume_log.txt 2>&1 &

cd "$(dirname "$0")"
LOG="results/exp_resume_log.txt"

echo "$(date): Waiting for Gemini quota to reset..." | tee -a "$LOG"

# Try every 30 minutes
while true; do
    RESULT=$(python3 -c "
import subprocess, json, os, signal
p = subprocess.Popen(
    ['/opt/homebrew/bin/openclaw', 'infer', 'model', 'run', '--gateway', '--prompt', 'Say ok', '--json'],
    stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
    env={**os.environ, 'PATH': '/opt/homebrew/bin:' + os.environ.get('PATH', '')},
    preexec_fn=os.setsid
)
stdout, stderr = p.communicate(timeout=60)
if p.returncode == 0:
    print('OK')
else:
    print('FAIL')
" 2>/dev/null)
    
    if [ "$RESULT" = "OK" ]; then
        echo "$(date): Gemini is available! Starting experiments..." | tee -a "$LOG"
        
        # Run both EXP-1 (remaining) and EXP-2 (perturbation)
        bash run_all_experiments.sh 2>&1 | tee -a "$LOG"
        
        echo "$(date): All experiments completed!" | tee -a "$LOG"
        break
    else
        echo "$(date): Gemini still rate-limited, waiting 30 min..." | tee -a "$LOG"
        sleep 1800
    fi
done
