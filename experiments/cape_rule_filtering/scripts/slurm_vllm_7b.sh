#!/bin/bash
#SBATCH --job-name=cape_vllm_7b
#SBATCH --partition=work1
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=8
#SBATCH --mem=32G
#SBATCH --gres=gpu:v100:1
#SBATCH --time=08:00:00
#SBATCH --output=%x_%j.log
#SBATCH --error=%x_%j.err

MODEL="Qwen/Qwen2.5-7B-Instruct"
PORT=8000

echo "Node: $(hostname), Job: $SLURM_JOB_ID"
nvidia-smi 2>/dev/null | head -8 || true
echo "Model: $MODEL  Time: $(date)"

# HuggingFace cache → shared home (not node-local /tmp)
export HF_HOME=${HF_HOME:-$HOME/hf_cache}
export HF_HUB_CACHE=$HF_HOME/hub
mkdir -p $HF_HUB_CACHE

echo "HF_HOME: $HF_HOME"
python3 -c "
from huggingface_hub import snapshot_download
import os
try:
    p = snapshot_download('$MODEL', local_files_only=True, cache_dir=os.environ.get('HF_HUB_CACHE'))
    print('Cached at:', p)
except:
    print('Not cached — downloading now...')
    p = snapshot_download('$MODEL', cache_dir=os.environ.get('HF_HUB_CACHE'))
    print('Done:', p)
"

python3 -m vllm.entrypoints.openai.api_server \
    --model "$MODEL" \
    --port "$PORT" \
    --max-model-len 8192 \
    --dtype bfloat16 \
    --trust-remote-code \
    --disable-log-requests

echo "vLLM exited at $(date)"
