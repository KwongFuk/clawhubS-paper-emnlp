#!/bin/bash
# deploy_palmetto.sh — Sync code + data to Palmetto and submit D1 job
# Usage: bash deploy_palmetto.sh
#
# Prereqs:
#   1. SSH config has 'p' alias for Palmetto
#   2. Mac HTTP proxy running (~/mac_http_proxy.py)
#   3. SSH tunnel established (ssh -R 18082:localhost:18082 p)

set -e

REMOTE="p"
REMOTE_DIR="~/clawhubS"
LOCAL_DIR="$(cd "$(dirname "$0")/../.." && pwd)"

echo "============================================================"
echo "Deploying to Palmetto"
echo "============================================================"
echo "Local:  $LOCAL_DIR"
echo "Remote: $REMOTE:$REMOTE_DIR"
echo ""

# Step 1: Sync code
echo "Step 1: Syncing code..."
rsync -avz --exclude '__pycache__' --exclude '.cache*' --exclude 'results/' \
    "$LOCAL_DIR/skill-X/experiments/full_corpus/" \
    "$REMOTE:$REMOTE_DIR/skill-X/experiments/full_corpus/"
echo "✅ Code synced"

# Step 2: Sync database (only if not already there)
echo ""
echo "Step 2: Checking database..."
ssh "$REMOTE" "test -f $REMOTE_DIR/data/experiment1_split/merged.sqlite3 && echo 'DB exists' || echo 'DB missing'"

read -p "Sync database? (y/n) " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    echo "Syncing database (234MB)..."
    ssh "$REMOTE" "mkdir -p $REMOTE_DIR/data/experiment1_split"
    rsync -avz --progress \
        "$LOCAL_DIR/../data/experiment1_split/merged.sqlite3" \
        "$REMOTE:$REMOTE_DIR/data/experiment1_split/"
    echo "✅ Database synced"
fi

# Step 3: Create PBS job script on remote
echo ""
echo "Step 3: Creating PBS job..."
ssh "$REMOTE" "cat > $REMOTE_DIR/skill-X/experiments/full_corpus/palmetto_d1.pbs" << 'PBS_SCRIPT'
#!/bin/bash
#PBS -N d1_full_corpus
#PBS -l select=1:ncpus=4:mem=16gb
#PBS -l walltime=10:00:00
#PBS -o d1_full_corpus.out
#PBS -e d1_full_corpus.err
#PBS -q work1

cd $HOME/clawhubS/skill-X/experiments/full_corpus

# Activate python env if available
source $HOME/.venvs/research/bin/activate 2>/dev/null || true

# Set proxy for sub2api (requires SSH tunnel running)
export HTTPS_PROXY=http://127.0.0.1:18082
export HTTP_PROXY=http://127.0.0.1:18082

echo "$(date): Starting D1 full corpus scan..."
echo "Python: $(which python3)"
echo "Proxy: $HTTPS_PROXY"

# Run D1 with proxy mode
python3 run_d1_full.py \
    --mode proxy \
    --base-url http://localhost:18082/v1 \
    --model gpt-4o-mini \
    --workers 16 \
    --batch-size 100 \
    2>&1 | tee results/d1_full_log.txt

echo "$(date): D1 scan complete!"

# Run aggregation
python3 analyze_full_corpus.py 2>&1 | tee results/analysis_log.txt

echo "$(date): Analysis complete!"
PBS_SCRIPT

echo "✅ PBS job created"

# Step 4: Submit option
echo ""
echo "============================================================"
echo "Ready to submit! Options:"
echo "  ssh $REMOTE"
echo "  cd $REMOTE_DIR/skill-X/experiments/full_corpus"
echo ""
echo "  # Option A: PBS batch job (requires tunnel running)"
echo "  qsub palmetto_d1.pbs"
echo ""
echo "  # Option B: Interactive (better for debugging)"
echo "  salloc --ntasks=1 --cpus-per-task=4 --mem=16G --time=10:00:00"
echo "  python3 run_d1_full.py --mode proxy --base-url http://localhost:18082/v1"
echo ""
echo "  # Option C: Dry run first"
echo "  python3 run_d1_full.py --dry-run"
echo "============================================================"
