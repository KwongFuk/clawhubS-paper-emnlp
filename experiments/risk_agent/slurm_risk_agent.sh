#!/bin/bash
# ============================================================
# Slurm Job: SkillRiskAgent — Multi-Agent LLM Scan
# Uses: Qwen2.5-32B-Instruct via vllm (OpenAI-compatible API)
# Target: 226 high-priority LLM candidates (A3/Q4 patterns)
# ============================================================
#SBATCH --job-name=risk-agent-llm
#SBATCH --partition=work1
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=8
#SBATCH --mem=64G
#SBATCH --gres=gpu:a100:1
#SBATCH --time=04:00:00
#SBATCH --output=/scratch/gguo/clawhubS/experiments/risk_agent/logs/risk_agent_%j.log
#SBATCH --error=/scratch/gguo/clawhubS/experiments/risk_agent/logs/risk_agent_%j.err

set -e
SCRATCH=/scratch/gguo
PROJ=$SCRATCH/clawhubS
AGENT_DIR=$PROJ/experiments/risk_agent
MODEL_DIR=$SCRATCH/models/qwen/Qwen2.5-32B-Instruct
VLLM_PORT=9127
LOG_DIR=$AGENT_DIR/logs
mkdir -p $LOG_DIR

echo "========================================"
echo " SkillRiskAgent LLM Scan"
echo " Job: $SLURM_JOB_ID  Node: $SLURMD_NODENAME"
echo " $(date)"
echo "========================================"

# ── Python env ──────────────────────────────────────────────
module load miniforge3/24.3.0 2>/dev/null || true
eval "$(conda shell.bash hook)" 2>/dev/null || true
conda activate vllm 2>/dev/null || source $SCRATCH/vllm_env/bin/activate 2>/dev/null || true
which python3 && python3 --version

# ── Start vllm server (background) ──────────────────────────
echo ""
echo "[1/3] Starting vllm server (Qwen2.5-32B)..."
python3 -m vllm.entrypoints.openai.api_server \
    --model $MODEL_DIR \
    --port $VLLM_PORT \
    --max-model-len 8192 \
    --dtype bfloat16 \
    --tensor-parallel-size 1 \
    --served-model-name "qwen-32b" \
    --disable-log-requests \
    > $LOG_DIR/vllm_server_$SLURM_JOB_ID.log 2>&1 &

VLLM_PID=$!
echo "  vllm PID: $VLLM_PID"
echo "  Waiting for server to start..."

# Wait for server ready (poll /health)
for i in $(seq 1 60); do
    if curl -s http://localhost:$VLLM_PORT/health > /dev/null 2>&1; then
        echo "  ✅ vllm ready after ${i}s"
        break
    fi
    sleep 5
    if [ $i -eq 60 ]; then
        echo "  ❌ vllm failed to start. Check $LOG_DIR/vllm_server_$SLURM_JOB_ID.log"
        cat $LOG_DIR/vllm_server_$SLURM_JOB_ID.log | tail -20
        exit 1
    fi
done

# ── Run the multi-agent scan ─────────────────────────────────
echo ""
echo "[2/3] Running multi-agent scan on LLM candidates..."
cd $AGENT_DIR

# Export vllm endpoint as "openai compatible"
export OPENAI_API_BASE=http://localhost:$VLLM_PORT/v1
export OPENAI_API_KEY=placeholder

python3 skill_risk_agent_vllm.py \
    --candidates results/risk_batch/llm_candidates_all.jsonl \
    --output results/risk_agent/risk_llm_scan_$SLURM_JOB_ID.jsonl \
    --model qwen-32b \
    --endpoint http://localhost:$VLLM_PORT/v1 \
    --limit 226 \
    2>&1 | tee $LOG_DIR/agent_run_$SLURM_JOB_ID.log

echo ""
echo "[3/3] Generating summary..."
python3 - << 'PYEOF'
import json
from pathlib import Path
import os

job_id = os.environ.get("SLURM_JOB_ID", "local")
out_file = Path(f"results/risk_agent/risk_llm_scan_{job_id}.jsonl")

if not out_file.exists():
    print("Output file not found")
    exit(1)

results = [json.loads(l) for l in open(out_file)]
from collections import Counter
pat_counter = Counter()
sev_counter = Counter()
for r in results:
    for p in r.get("risk_patterns", []):
        pat_counter[p] += 1
    sev_counter[r.get("severity", "none")] += 1

print(f"\n=== SCAN SUMMARY ({len(results)} skills) ===")
print(f"Severity: {dict(sev_counter)}")
print(f"Patterns: {dict(pat_counter)}")
print(f"\nTop A3 (Trust Amplification) confirmed:")
a3 = [r for r in results if "A3" in r.get("risk_patterns", [])]
for r in a3[:10]:
    print(f"  {r['slug']:40s} {r.get('severity','?')} | {r.get('evidence',{}).get('A3',{}).get('explanation','')[:60]}")
PYEOF

# ── Cleanup ──────────────────────────────────────────────────
echo ""
echo "Stopping vllm server..."
kill $VLLM_PID 2>/dev/null || true
wait $VLLM_PID 2>/dev/null || true

echo ""
echo "========================================"
echo " Done! $(date)"
echo " Results: $AGENT_DIR/results/risk_agent/risk_llm_scan_$SLURM_JOB_ID.jsonl"
echo "========================================"
