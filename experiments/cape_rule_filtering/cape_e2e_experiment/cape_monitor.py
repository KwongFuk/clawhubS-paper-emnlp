#!/usr/bin/env python3
"""
CAPE 125-Pair Experiment Monitor
=================================
Periodically checks progress and prints a live summary.
Run separately from the main experiment: python3 cape_monitor.py
"""
import json, time, sys
from pathlib import Path
from datetime import datetime

RESULTS = Path("/Users/guangfu/codex/clawhubS/experiments/cape_rule_filtering/cape_e2e_experiment/results/mcp_125")
TOTAL   = 125


def status():
    files = sorted(RESULTS.glob("P*.json"))
    n     = len(files)
    ok    = sum(1 for f in files if json.loads(f.read_text())["success"])
    fail  = n - ok

    # Latest 3
    recent = sorted(files, key=lambda f: f.stat().st_mtime, reverse=True)[:3]
    recent_info = []
    for f in recent:
        d = json.loads(f.read_text())
        pid = d["pair_id"]
        task= d["task"][:12]
        s   = "✅" if d["success"] else "❌"
        ev  = (d.get("side_effect") or "no probe")[:30]
        recent_info.append(f"  {s} {pid} [{task}]: {ev}")

    pct   = 100 * n  // TOTAL
    eta_s = (TOTAL - n) * 4 * 60  # 4 min/pair estimate
    eta_m = eta_s // 60

    now = datetime.now().strftime("%H:%M:%S")
    print(f"\r[{now}] Done: {n}/{TOTAL} ({pct}%) | ✅{ok} ❌{fail} | ETA: ~{eta_m}m  ", end="", flush=True)
    return n, ok

interval = int(sys.argv[1]) if len(sys.argv) > 1 else 30  # seconds between checks

print(f"CAPE Monitor — checking every {interval}s (Ctrl-C to stop)")
print(f"Results dir: {RESULTS}")
print()

prev_n = 0
while True:
    try:
        n, ok = status()
        if n > prev_n:
            # Print newline when new results arrive
            print()
            prev_n = n
        if n >= TOTAL:
            print(f"\n\n✅ EXPERIMENT COMPLETE: {ok}/{TOTAL} success rate = {100*ok//TOTAL}%")
            break
        time.sleep(interval)
    except KeyboardInterrupt:
        print("\n\nMonitor stopped.")
        break
    except Exception as e:
        print(f"\nError: {e}")
        time.sleep(interval)
