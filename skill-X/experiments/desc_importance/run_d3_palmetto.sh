#!/bin/bash
#SBATCH --job-name=d3-domain
#SBATCH --partition=work1
#SBATCH --gres=gpu:h100:1
#SBATCH --cpus-per-task=8
#SBATCH --mem=80G
#SBATCH --time=03:00:00
#SBATCH --output=/scratch/gguo/d3_domain_%j.log

echo '=== D3 Domain Alignment Experiment ==='
echo "Node: $(hostname)  Date: $(date)"
nvidia-smi --query-gpu=name,memory.total --format=csv,noheader

module load gcc/12.3.0
module load cuda/12.3.0
export CC=$(which gcc)
export CXX=$(which g++)

source activate /scratch/gguo/envs/vllm_env
export LD_LIBRARY_PATH=/scratch/gguo/envs/vllm_env/lib:$LD_LIBRARY_PATH
export HF_HOME=/scratch/gguo/.cache/huggingface
rm -rf ~/.triton/cache 2>/dev/null

# Start vLLM on port 8002 (avoid conflict with other experiments)
python3 -m vllm.entrypoints.openai.api_server \
    --model openai/gpt-oss-20b \
    --download-dir /scratch/gguo/.cache/huggingface/hub \
    --host 0.0.0.0 --port 8002 \
    --max-model-len 4096 --dtype auto \
    --enforce-eager --gpu-memory-utilization 0.9 2>&1 &
VLLM_PID=$!

echo "Waiting for vLLM (PID=$VLLM_PID)..."
for i in $(seq 1 120); do
    if curl -s http://localhost:8002/v1/models > /dev/null 2>&1; then
        echo "=== vLLM READY at iteration $i ==="
        break
    fi
    sleep 5
done

if ! curl -s http://localhost:8002/v1/models > /dev/null 2>&1; then
    echo 'ERROR: vLLM did not start within 600s'
    kill $VLLM_PID 2>/dev/null
    exit 1
fi

MODEL_ID=$(curl -s http://localhost:8002/v1/models | \
    python3 -c "import json,sys; d=json.load(sys.stdin); print(d['data'][0]['id'])" 2>/dev/null \
    || echo 'openai/gpt-oss-20b')
echo "Model: $MODEL_ID"

export VLLM_BASE_URL=http://localhost:8002/v1
export VLLM_MODEL=$MODEL_ID
python3 /scratch/gguo/clawhubS/experiments/exp_d3_domain_alignment.py 2>&1

EXIT_CODE=$?
echo "=== Experiment finished (exit=$EXIT_CODE): $(date) ==="
echo "Results: /scratch/gguo/clawhubS/experiments/results/d3_domain_alignment.json"
kill $VLLM_PID 2>/dev/null
exit $EXIT_CODE
