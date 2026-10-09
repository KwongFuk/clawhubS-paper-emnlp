#!/bin/bash
#SBATCH --job-name=full-audit
#SBATCH --partition=work1
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=8
#SBATCH --mem=48G
#SBATCH --gpus=a100:1
#SBATCH --time=08:00:00
#SBATCH --output=/scratch/gguo/clawhubS/logs/full_audit_%j.log

set -e
PROJ=/scratch/gguo/clawhubS
AUDIT_DIR=$PROJ/skill-X/experiments/full_audit
MODEL=/scratch/gguo/models/qwen/Qwen2.5-7B-Instruct
mkdir -p $PROJ/logs

echo "=== Job $SLURM_JOB_ID on $(hostname) at $(date) ==="
nvidia-smi --query-gpu=name,memory.total --format=csv,noheader

# Check vLLM version
python3 -c "import vllm; print(f'vLLM version: {vllm.__version__}')" 2>/dev/null || true

echo "[$(date)] Starting vLLM with local model: $MODEL"
# Set max-model-len=4096 to bypass RoPE scaling validation in vLLM 0.6.3
python3 -m vllm.entrypoints.openai.api_server \
    --model $MODEL \
    --port 8000 \
    --max-model-len 4096 \
    --gpu-memory-utilization 0.90 \
    --enforce-eager \
    --disable-log-requests \
    2>&1 | tee $PROJ/logs/vllm_server.log &
VLLM_PID=$!

for i in $(seq 1 300); do
    if curl -s http://localhost:8000/v1/models > /dev/null 2>&1; then
        echo "  vLLM ready after ${i}s"; break
    fi
    if ! kill -0 $VLLM_PID 2>/dev/null; then
        echo "FATAL: vLLM process died"
        tail -30 $PROJ/logs/vllm_server.log
        exit 1
    fi
    [ $i -eq 300 ] && { echo "FATAL: vLLM timeout"; exit 1; }
    sleep 2
done

echo "[$(date)] Starting audit (merged: 11974 skills)..."
python3 $AUDIT_DIR/run_audit_from_jsonl.py \
    --input $AUDIT_DIR/audit_input_merged.jsonl \
    --output $AUDIT_DIR/full_audit_results.jsonl \
    --model $MODEL \
    --workers 16

echo "[$(date)] Done."
kill $VLLM_PID 2>/dev/null || true
