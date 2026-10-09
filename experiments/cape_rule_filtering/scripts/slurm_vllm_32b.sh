#!/bin/bash
#SBATCH --job-name=cape_vllm_32b
#SBATCH --partition=work1
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=16
#SBATCH --mem=80G
#SBATCH --gres=gpu:a100:2          # 2x A100 80GB = 160GB for 32B (bf16 needs ~65GB)
#SBATCH --time=24:00:00
#SBATCH --output=/tmp/cape_vllm_32b_%j.log
#SBATCH --error=/tmp/cape_vllm_32b_%j.err

MODEL="Qwen/Qwen2.5-32B-Instruct"
PORT=8000

echo "Node: $(hostname)"
nvidia-smi --query-gpu=name,memory.total --format=csv,noheader 2>/dev/null
echo "Model: $MODEL"

python3 -m vllm.entrypoints.openai.api_server \
    --model "$MODEL" \
    --port "$PORT" \
    --tensor-parallel-size 2 \
    --max-model-len 8192 \
    --dtype bfloat16 \
    --trust-remote-code \
    --disable-log-requests \
    2>&1 | tee /tmp/cape_vllm_32b_${SLURM_JOB_ID}.log
