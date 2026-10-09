#!/bin/bash
#SBATCH --job-name=framework-x
#SBATCH --partition=work1
#SBATCH --gres=gpu:h100:1
#SBATCH --cpus-per-task=8
#SBATCH --mem=80G
#SBATCH --time=06:00:00
#SBATCH --output=/scratch/gguo/framework_x_%j.log

echo "=== Framework X on Palmetto ==="
echo "Node: $(hostname)  Date: $(date)"
nvidia-smi --query-gpu=name,memory.total --format=csv,noheader

module load gcc/12.3.0 cuda/12.3.0
source activate /scratch/gguo/envs/vllm_env
export LD_LIBRARY_PATH=/scratch/gguo/envs/vllm_env/lib:$LD_LIBRARY_PATH
export HF_HOME=/scratch/gguo/.cache/huggingface
rm -rf ~/.triton/cache 2>/dev/null

# Use Qwen3-8B (supported by vLLM)
MODEL_ID="Qwen/Qwen3-8B"

python3 -m vllm.entrypoints.openai.api_server \
    --model $MODEL_ID \
    --download-dir /scratch/gguo/.cache/huggingface/hub \
    --host 0.0.0.0 --port 8003 \
    --max-model-len 8192 --dtype auto \
    --enforce-eager --gpu-memory-utilization 0.85 2>&1 &
VLLM_PID=$!

echo "Waiting for vLLM (PID=$VLLM_PID)..."
for i in $(seq 1 120); do
    if curl -s http://localhost:8003/v1/models > /dev/null 2>&1; then
        echo "=== vLLM READY at step $i ==="
        break
    fi
    sleep 5
done

if ! curl -s http://localhost:8003/v1/models > /dev/null 2>&1; then
    echo "ERROR: vLLM failed"; kill $VLLM_PID 2>/dev/null; exit 1
fi

MODEL_NAME=$(curl -s http://localhost:8003/v1/models | \
    python3 -c "import json,sys; print(json.load(sys.stdin)['data'][0]['id'])" 2>/dev/null)
echo "Model: $MODEL_NAME"

mkdir -p /scratch/gguo/clawhubS/framework_x/results

# Copy framework to Palmetto work dir
cp /scratch/gguo/clawhubS/framework_x/framework_x.py /scratch/gguo/

OPENAI_BASE_URL=http://localhost:8003/v1 \
OPENAI_API_KEY=dummy \
OPENAI_MODEL=$MODEL_NAME \
python3 /scratch/gguo/framework_x.py \
    --n 300 \
    --out /scratch/gguo/clawhubS/framework_x/results/framework_x_300.jsonl \
    2>&1

echo "=== Done: $(date) ==="
kill $VLLM_PID 2>/dev/null
