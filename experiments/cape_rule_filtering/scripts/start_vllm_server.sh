#!/bin/bash
# CAPE vLLM Server — Remote Setup Script
# Run on remote server (ssh p):
#   bash experiments/cape_rule_filtering/scripts/start_vllm_server.sh
#
# Then on local machine, forward port:
#   ssh -L 8000:localhost:8000 p -N &
#
# Then run pipeline locally:
#   python experiments/cape_rule_filtering/scripts/cape_llm_pipeline.py --step extract --limit 200

set -e

MODEL=${1:-"Qwen/Qwen2.5-32B-Instruct"}
PORT=${2:-8000}
TP=${3:-1}   # tensor-parallel-size (number of GPUs)

echo "=== CAPE vLLM Server ==="
echo "Model: $MODEL"
echo "Port:  $PORT"
echo "Tensor parallel: $TP"
echo ""

# Check GPU
python3 -c "
import torch
n = torch.cuda.device_count()
print(f'Available GPUs: {n}')
for i in range(n):
    p = torch.cuda.get_device_properties(i)
    print(f'  GPU {i}: {p.name} {p.total_memory//1024//1024//1024}GB VRAM')
if n == 0:
    print('WARNING: No GPU found. Will run on CPU (very slow).')
"

# Model size guidance:
# Qwen2.5-7B-Instruct   → ~16GB VRAM → 1x A100/H100 or 2x 3090/4090
# Qwen2.5-14B-Instruct  → ~30GB VRAM → 1x A100(80GB) or 2x A6000
# Qwen2.5-32B-Instruct  → ~65GB VRAM → 2x A100(80GB) or 4x A6000
# Qwen3-8B              → ~18GB VRAM → 1x A100

echo ""
echo "Starting vLLM server..."
python3 -m vllm.entrypoints.openai.api_server \
    --model "$MODEL" \
    --port "$PORT" \
    --tensor-parallel-size "$TP" \
    --max-model-len 8192 \
    --dtype bfloat16 \
    --trust-remote-code \
    --served-model-name "$(basename $MODEL)" \
    2>&1 | tee /tmp/vllm_cape.log
