#!/bin/bash
#SBATCH --job-name=audit-shard1
#SBATCH --partition=work1
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=8
#SBATCH --mem=48G
#SBATCH --gpus=a100:1
#SBATCH --time=06:00:00
#SBATCH --output=/scratch/gguo/clawhubS/logs/audit_shard1_%j.log

set -e
PROJ=/scratch/gguo/clawhubS
AUDIT_DIR=$PROJ/skill-X/experiments/full_audit
mkdir -p $PROJ/logs

echo "=== Shard1 Audit Job $SLURM_JOB_ID on $(hostname) at $(date) ==="
nvidia-smi --query-gpu=name,memory.total --format=csv,noheader

# Step 1: Re-extract with shard1 data now available
echo "[$(date)] Re-extracting with shard1 data..."
python3 $AUDIT_DIR/extract_input_remote.py

# Step 2: Find skills NOT in job1's merged input, write shard1-only
echo "[$(date)] Filtering shard1-only skills..."
python3 -c "
import json, os

# Load already-covered skill_ids from job1
covered = set()
merged = '$AUDIT_DIR/audit_input_merged.jsonl'
if os.path.exists(merged):
    with open(merged) as f:
        for line in f:
            covered.add(json.loads(line)['skill_id'])
print(f'Job1 covers: {len(covered)} skills')

# Load full extraction, keep only uncovered
full = '$AUDIT_DIR/audit_input_full.jsonl'
out = '$AUDIT_DIR/audit_input_shard1_only.jsonl'
count = 0
with open(full) as fin, open(out, 'w') as fout:
    for line in fin:
        entry = json.loads(line)
        if entry['skill_id'] not in covered:
            fout.write(line)
            count += 1
print(f'Shard1-only: {count} skills')
mb = os.path.getsize(out) / 1024 / 1024
print(f'Size: {mb:.1f} MB')
"

# Step 3: Start vLLM
echo "[$(date)] Starting vLLM..."
python3 -m vllm.entrypoints.openai.api_server \
    --model /scratch/gguo/models/qwen/Qwen2.5-7B-Instruct \
    --port 8000 \
    --max-model-len 4096 \
    --gpu-memory-utilization 0.90 \
    --enforce-eager \
    --disable-log-requests \
    2>&1 | tee $PROJ/logs/vllm_shard1.log &
VLLM_PID=$!

for i in $(seq 1 180); do
    if curl -s http://localhost:8000/v1/models > /dev/null 2>&1; then
        echo "  vLLM ready after ${i}s"; break
    fi
    [ $i -eq 180 ] && { echo "FATAL"; exit 1; }
    sleep 2
done

# Step 4: Run audit on shard1-only
echo "[$(date)] Auditing shard1-only skills..."
python3 $AUDIT_DIR/run_audit_from_jsonl.py \
    --input $AUDIT_DIR/audit_input_shard1_only.jsonl \
    --output $AUDIT_DIR/audit_results_shard1.jsonl \
    --model /scratch/gguo/models/qwen/Qwen2.5-7B-Instruct \
    --workers 16

echo "[$(date)] Shard1 audit done."
kill $VLLM_PID 2>/dev/null || true
