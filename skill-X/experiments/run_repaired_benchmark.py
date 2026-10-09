#!/usr/bin/env python3
"""
Run the selection benchmark with repaired descriptions.
Compares before/after selection rates to measure repair impact.

Uses the same run_bias_experiment.py infrastructure but with repaired clusters.
"""
import json
import os
import shutil
import subprocess
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ORIGINAL = os.path.join(BASE_DIR, "bias_benchmark", "clusters_final.json")
REPAIRED = os.path.join(BASE_DIR, "bias_benchmark", "clusters_repaired.json")
BACKUP = os.path.join(BASE_DIR, "bias_benchmark", "clusters_final_backup.json")

if not os.path.exists(REPAIRED):
    print("ERROR: clusters_repaired.json not found. Run description_repair_agent.py first.")
    sys.exit(1)

# Backup original
shutil.copy2(ORIGINAL, BACKUP)
print(f"✓ Backed up clusters_final.json → clusters_final_backup.json")

# Swap in repaired
shutil.copy2(REPAIRED, ORIGINAL)
print(f"✓ Installed repaired clusters as clusters_final.json")

# Run experiment for 8 target clusters
TARGET_CLUSTERS = ["web_search", "image_generation", "video_generation",
                   "text_to_speech", "speech_to_text", "github_management",
                   "memory_system", "twitter_x"]

try:
    for cluster in TARGET_CLUSTERS:
        print(f"\n{'='*60}")
        print(f"Running repaired benchmark: {cluster}")
        print(f"{'='*60}")
        
        cmd = [
            sys.executable, os.path.join(BASE_DIR, "bias_benchmark", "run_bias_experiment.py"),
            "--cluster", cluster,
            "--model", "gemini-3.1-pro",
            "--run", "2",  # run 2 = repaired version
            "--delay", "1",
        ]
        
        result = subprocess.run(cmd, cwd=os.path.join(BASE_DIR, "bias_benchmark"),
                                capture_output=True, text=True, timeout=600)
        print(result.stdout[-500:] if result.stdout else "No output")
        if result.returncode != 0:
            print(f"ERROR: {result.stderr[-300:]}")

finally:
    # Restore original
    shutil.copy2(BACKUP, ORIGINAL)
    print(f"\n✓ Restored original clusters_final.json")
    os.remove(BACKUP)

print("\n✓ Repaired benchmark complete. Check results/ for exp1_*_run2_*.json files.")
