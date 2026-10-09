#!/bin/bash
#SBATCH --job-name=cape_vllm
#SBATCH --partition=work1
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=8
#SBATCH --mem=64G
#SBATCH --gres=gpu:a100:1          # 1x A100 (80GB) for 32B model
#SBATCH --time=12:00:00
#SBATCH --output=/tmp/cape_vllm_%j.log
#SBATCH --error=/tmp/cape_vllm_%j.err

# ── CAPE vLLM Server on Slurm ──────────────────────────────────────────────
# Usage:
#   sbatch experiments/cape_rule_filtering/scripts/slurm_vllm_server.sh
#
# After job starts, get the node name:
#   squeue --me
#
# Then forward port from local machine:
#   ssh -L 8000:<NODENAME>:8000 p -N &   (replace <NODENAME> with actual)
#   OR (if can't reach compute node directly):
#   ssh -J p <NODENAME> -L 8000:localhost:8000 -N &
#
# Then run pipeline on local machine:
#   python experiments/cape_rule_filtering/scripts/cape_llm_pipeline.py \
#       --step extract --limit 500 --vllm-url http://localhost:8000/v1

MODEL="Qwen/Qwen2.5-32B-Instruct"
# Smaller alternatives if A100 not available:
# MODEL="Qwen/Qwen2.5-14B-Instruct"    # needs 1x A100(40GB) or 2x V100
# MODEL="Qwen/Qwen2.5-7B-Instruct"     # needs 1x V100(16GB)
# MODEL="Qwen/Qwen3-8B"                # newest, good at structured JSON

PORT=8000

echo "Job starting on node: $(hostname)"
echo "Model: $MODEL"
echo "Time: $(date)"

# Load modules if needed (adjust for your cluster)
# module load cuda/12.4
# module load python/3.10

# Activate conda env with vLLM
source activate base 2>/dev/null || true
conda activate vllm 2>/dev/null || true

# Check GPU
python3 -c "
import torch
n = torch.cuda.device_count()
print(f'GPUs: {n}')
for i in range(n):
    p = torch.cuda.get_device_properties(i)
    vram = p.total_memory // 1024**3
    print(f'  GPU {i}: {p.name} {vram}GB VRAM')
"

# Start vLLM server
# --tensor-parallel-size: use all available GPUs
# --max-model-len 8192: enough for SKILL.md extraction
python3 -m vllm.entrypoints.openai.api_server \
    --model "$MODEL" \
    --port "$PORT" \
    --tensor-parallel-size 1 \
    --max-model-len 8192 \
    --dtype bfloat16 \
    --trust-remote-code \
    --disable-log-requests \
    2>&1

echo "vLLM server stopped at $(date)"
