#!/usr/bin/env python3
"""
run_all_experiments_batch.py — Run ALL expanded experiments sequentially.
Uses gemini-2.5-flash via OpenClaw gateway (quota-independent from gemini-3.1-pro).

Runs: H1 (all 8 clusters) → H2 (4 clusters) → H5H6 (4 clusters)
"""
import subprocess
import sys
import time
from pathlib import Path

SCRIPT = Path(__file__).parent / "run_expanded_experiments.py"
LOG_DIR = Path(__file__).parent / "results"
LOG_DIR.mkdir(exist_ok=True)

# Ordered experiment plan
EXPERIMENTS = [
    # H1: Already done - web_search, running - image_generation
    # Remaining H1 clusters:
    ("H1", "video_generation"),
    ("H1", "text_to_speech"),
    ("H1", "speech_to_text"),
    ("H1", "github_management"),
    ("H1", "music_generation"),
    ("H1", "notion_management"),
    # H2: Brand experiments on 4 clusters  
    ("H2", "web_search"),
    ("H2", "image_generation"),
    ("H2", "text_to_speech"),
    ("H2", "video_generation"),
    # H5H6: Social proof & authority
    ("H5H6", "web_search"),
    ("H5H6", "image_generation"),
    ("H5H6", "text_to_speech"),
    ("H5H6", "video_generation"),
]

def main():
    total = len(EXPERIMENTS)
    for i, (hyp, cluster) in enumerate(EXPERIMENTS, 1):
        print(f"\n{'='*60}")
        print(f"[{i}/{total}] Running {hyp} on {cluster}...")
        print(f"{'='*60}", flush=True)
        
        log_file = LOG_DIR / f"exp3_{cluster}_{hyp}_batch.log"
        
        cmd = [sys.executable, str(SCRIPT), 
               "--hypothesis", hyp, "--cluster", cluster]
        
        with open(log_file, "w") as lf:
            proc = subprocess.run(cmd, stdout=lf, stderr=subprocess.STDOUT)
        
        print(f"  Exit code: {proc.returncode}")
        
        # Read and display key results
        with open(log_file) as lf:
            lines = lf.readlines()
        
        for line in lines:
            if "δ_API" in line or "Selection:" in line or "✅" in line or "QUOTA" in line:
                print(f"  {line.rstrip()}")
        
        # Brief pause between experiments
        time.sleep(3)
    
    print(f"\n{'='*60}")
    print("ALL EXPERIMENTS COMPLETE!")
    print(f"{'='*60}")
    
    # Run analysis
    print("\nRunning analysis...")
    analysis_script = Path(__file__).parent / "analyze_expanded.py"
    subprocess.run([sys.executable, str(analysis_script)])


if __name__ == "__main__":
    main()
