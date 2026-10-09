#!/bin/bash
# Wait for shard1 rsync to finish (check file count), then submit job
TARGET=150000  # approximate target file count
DIR="/scratch/gguo/clawhub_archive/html_snapshots_shard1/api_files"
SCRIPT="/scratch/gguo/clawhubS/skill-X/experiments/full_audit/submit_audit_shard1.sh"

echo "Waiting for shard1 rsync to finish (target: ~${TARGET} files)..."
while true; do
    COUNT=$(ls "$DIR" 2>/dev/null | wc -l)
    echo "  $(date +%H:%M): $COUNT files"
    if [ "$COUNT" -ge "$TARGET" ]; then
        echo "  Rsync appears done! Submitting shard1 job..."
        cd /scratch/gguo/clawhubS && sbatch "$SCRIPT"
        break
    fi
    sleep 60
done
