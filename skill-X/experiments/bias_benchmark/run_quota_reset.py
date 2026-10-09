#!/usr/bin/env python3
"""
run_quota_reset.py — Run ALL remaining experiments when quota resets
===================================================================
Quota resets at ~3:17 PM EST May 19, 2026.

Runs:
1. P7 expanded (description swap on 8 clusters) — 200 calls
2. H5H6 (social proof + authority on 4 clusters) — 200 calls  
Total: ~400 calls

Usage:
  nohup python3 run_quota_reset.py > results/quota_reset.log 2>&1 &
"""
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent

def main():
    print("=" * 60)
    print("POST-QUOTA-RESET EXPERIMENT BATCH")
    print("=" * 60)
    
    # 1. P7 expanded (most important)
    print("\n>>> Phase 1: P7 Description Swap (8 clusters)")
    p7_script = SCRIPT_DIR / "run_p7_expanded.py"
    subprocess.run([sys.executable, str(p7_script)])
    
    # 2. H5H6 remaining
    print("\n>>> Phase 2: H5H6 Social Proof + Authority (4 clusters)")
    h5h6_script = SCRIPT_DIR / "run_remaining_experiments.py"
    subprocess.run([sys.executable, str(h5h6_script)])
    
    print("\n" + "=" * 60)
    print("ALL EXPERIMENTS COMPLETE!")
    print("=" * 60)

if __name__ == "__main__":
    main()
