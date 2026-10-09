#!/usr/bin/env python3
"""Watch for new exp3_*_P{9,10,ctrl}*.json and copy to exp4_gpt54mini_*."""
import json, time, shutil
from pathlib import Path

RESULTS = Path("/Users/guangfu/codex/clawhubS/skill-X/experiments/bias_benchmark/results")
TRACKED = set()

while True:
    for f in RESULTS.glob("exp3_*_P*.json"):
        if f.name in TRACKED:
            continue
        # Check if this is a GPT result (created after 11:10 today)
        if f.stat().st_mtime > 1747666200:  # ~11:10 AM May 19
            new_name = f.name.replace("exp3_", "exp4_gpt54mini_")
            target = RESULTS / new_name
            if not target.exists():
                shutil.copy2(f, target)
                print(f"Copied: {f.name} → {new_name}", flush=True)
            TRACKED.add(f.name)
    time.sleep(30)
