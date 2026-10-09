#!/usr/bin/env python3
"""
run_remaining_experiments.py — Run only the remaining H5H6 experiments
=====================================================================
The H1 (24 results) and H2 (8 results) are complete.
Only H5H6 (social proof + authority) on 4 clusters remain.

Run when quota resets (~3:17 PM EST May 19):
  nohup python3 run_remaining_experiments.py > results/remaining.log 2>&1 &
"""
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).parent / "run_expanded_experiments.py"

# Only H5H6 remains (blocks 11-14 from original batch)
EXPERIMENTS = [
    ("H5H6", "web_search"),
    ("H5H6", "image_generation"),
    ("H5H6", "text_to_speech"),
    ("H5H6", "video_generation"),
]

def main():
    total = len(EXPERIMENTS)
    for i, (hyp, cluster) in enumerate(EXPERIMENTS, 1):
        print(f"\n[{i}/{total}] Running {hyp} on {cluster}...", flush=True)
        
        log_file = Path(__file__).parent / "results" / f"exp3_{cluster}_{hyp}_remaining.log"
        cmd = [sys.executable, str(SCRIPT), "--hypothesis", hyp, "--cluster", cluster]
        
        with open(log_file, "w") as lf:
            proc = subprocess.run(cmd, stdout=lf, stderr=subprocess.STDOUT)
        
        with open(log_file) as lf:
            for line in lf:
                if "δ_API" in line or "Selection:" in line or "✅" in line:
                    print(f"  {line.rstrip()}")
    
    print(f"\nAll remaining experiments complete!")
    
    # Run full analysis
    print("\nRunning analysis...")
    analysis = Path(__file__).parent / "analyze_expanded.py"
    subprocess.run([sys.executable, str(analysis)])

if __name__ == "__main__":
    main()
