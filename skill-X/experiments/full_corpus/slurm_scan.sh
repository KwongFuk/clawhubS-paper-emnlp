#!/bin/bash
#SBATCH --job-name=skill_audit
#SBATCH --partition=work1
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=8
#SBATCH --mem=64G
#SBATCH --gpus=a100:1
#SBATCH --time=12:00:00
#SBATCH --output=results/slurm_%j.out
#SBATCH --error=results/slurm_%j.err

# ============================================================
# Full-Corpus Skill Audit via HuggingFace Transformers Pipeline
# ============================================================
# Uses transformers directly instead of vLLM to avoid version issues
# Model: Qwen2.5-7B-Instruct on A100
set -e

MODEL="Qwen/Qwen2.5-7B-Instruct"
WORKERS=1  # Sequential for transformers pipeline (GPU-bound)
BATCH_SIZE=50
WORK_DIR="$HOME/clawhubS/skill-X/experiments/full_corpus"

cd "$WORK_DIR"
mkdir -p results/scan_batches

# Force offline mode (GPU nodes have no internet)
export HF_HUB_OFFLINE=1
export TRANSFORMERS_OFFLINE=1
export HF_DATASETS_OFFLINE=1

echo "============================================================"
echo "Job: $SLURM_JOB_ID | Node: $(hostname) | GPU: $CUDA_VISIBLE_DEVICES"
echo "Model: $MODEL | $(date)"
echo "Mode: transformers pipeline (no vLLM)"
echo "============================================================"

# Load CUDA
module load cuda/13.0.2 2>/dev/null || module load cuda/12.3.0 2>/dev/null || true

# Verify GPU
nvidia-smi --query-gpu=name,memory.total --format=csv,noheader || { echo "No GPU!"; exit 1; }

# Verify imports
python3 -c "
import torch
print(f'torch {torch.__version__}, CUDA: {torch.cuda.is_available()}')
print(f'GPU: {torch.cuda.get_device_name(0) if torch.cuda.is_available() else \"none\"}')
import transformers
print(f'transformers {transformers.__version__}')
"

# ── Run Scanner with transformers backend ───────────────────────
echo ""
echo "$(date): Starting full-corpus scan (transformers pipeline)..."
echo "============================================================"

python3 -u scan_transformers.py \
    --model "$MODEL" \
    --batch-size $BATCH_SIZE \
    2>&1 | tee results/scan_log.txt

# ── Analysis ───────────────────────────────────────────────────
echo ""
echo "$(date): Running analysis..."
python3 analyze_full_corpus.py 2>&1 | tee results/analysis_log.txt

echo "$(date): Job complete!"
