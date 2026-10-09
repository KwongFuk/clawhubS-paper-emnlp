#!/usr/bin/env python3
"""
CAPE v2 Report — Source × Channel × Sink 3D Matrix
====================================================
Reads results from results_v2/ and produces:
  1. 3D success rate matrix (Source × Sink)
  2. Channel transmission efficiency table
  3. Per-source-type breakdown
  4. Comparison with v1 (S1 baseline)
"""
from __future__ import annotations
import json
from pathlib import Path
from collections import defaultdict

V2_RESULTS_DIR = Path(__file__).parent / "results_v2"
V1_SUMMARY     = Path(__file__).parent / "results" / "e2e_summary.json"

# Ordered display labels
SOURCE_ORDER = ["S1", "S2", "S3", "S4", "S5", "S6"]
SOURCE_SHORT = {
    "S1": "S1 Web Fetch",
    "S2": "S2 User Input",
    "S3": "S3 Memory",
    "S4": "S4 Local FS",
    "S5": "S5 OAuth/API",
    "S6": "S6 Inter-Skill",
}
SINK_ORDER = ["K1_shell_exec", "K2_file_write", "K3_network_exfil",
              "K4_memory_poison", "K5_persistence", "K6_code_eval", "K7_commit"]
SINK_SHORT = {
    "K1_shell_exec":    "K1 Shell",
    "K2_file_write":    "K2 File",
    "K3_network_exfil": "K3 Net",
    "K4_memory_poison": "K4 Mem",
    "K5_persistence":   "K5 Persist",
    "K6_code_eval":     "K6 Eval",
    "K7_commit":        "K7 Git",
}
CHANNEL_ORDER = ["C1_llm_context", "C3_agent_memory", "C9_boot_hook",
                 "C2_filesystem", "C5_database", "C13_network"]


def load_v2_results() -> list[dict]:
    results = []
    for f in sorted(V2_RESULTS_DIR.glob("v2_V*.json")):
        results.append(json.load(f.open()))
    return results


def pct(ok: int, total: int) -> str:
    if total == 0:
        return "  --"
    p = round(100 * ok / total)
    return f"{p:3d}%"


def bar(ok: int, total: int, width: int = 5) -> str:
    if total == 0:
        return " " * width
    filled = round(ok / total * width)
    return "█" * filled + "░" * (width - filled)


def print_report():
    results = load_v2_results()
    total   = len(results)

    if total == 0:
        print("No v2 results found. Run: python3 cape_v2_runner.py --all")
        return

    ok_total = sum(1 for r in results if r["success"])

    print(f"\n{'='*72}")
    print(f"CAPE v2 — Source × Channel × Sink Attack Space")
    print(f"{'='*72}")
    print(f"Total pairs: {total}/36 | Successful: {ok_total} "
          f"({round(100*ok_total/max(total,1),1)}%)")

    # ── 1. Source × Sink 2D matrix ────────────────────────────────────────────
    print(f"\n{'─'*72}")
    print("SOURCE × SINK SUCCESS MATRIX")
    print(f"{'─'*72}")

    # Collect counts
    matrix: dict[str, dict[str, list[int, int]]] = defaultdict(
        lambda: defaultdict(lambda: [0, 0])
    )
    source_totals: dict[str, list[int, int]] = defaultdict(lambda: [0, 0])
    sink_totals:   dict[str, list[int, int]] = defaultdict(lambda: [0, 0])

    for r in results:
        st = r["source_type"]
        kt = r["sink_type"]
        matrix[st][kt][1] += 1
        source_totals[st][1] += 1
        sink_totals[kt][1]   += 1
        if r["success"]:
            matrix[st][kt][0] += 1
            source_totals[st][0] += 1
            sink_totals[kt][0]   += 1

    # Build header
    sinks_present = [k for k in SINK_ORDER if any(k in matrix[s] for s in matrix)]
    header_parts = [f"{'Source':<18}"]
    for kt in sinks_present:
        header_parts.append(f"{SINK_SHORT.get(kt, kt):>10}")
    header_parts.append(f"{'Total':>8}")
    print(" ".join(header_parts))
    print(f"{'─'*72}")

    for st in SOURCE_ORDER:
        if st not in matrix and st not in source_totals:
            continue
        row = [f"{SOURCE_SHORT.get(st, st):<18}"]
        for kt in sinks_present:
            ok, n = matrix[st][kt]
            row.append(f"{pct(ok, n):>10}")
        ok, n = source_totals[st]
        row.append(f"{pct(ok,n):>5} ({ok}/{n})")
        print(" ".join(row))

    print(f"{'─'*72}")
    footer = [f"{'Total':<18}"]
    for kt in sinks_present:
        ok, n = sink_totals[kt]
        footer.append(f"{pct(ok, n):>10}")
    ok, n = ok_total, total
    footer.append(f"{pct(ok,n):>5} ({ok}/{n})")
    print(" ".join(footer))

    # ── 2. Channel Efficiency ─────────────────────────────────────────────────
    print(f"\n{'─'*72}")
    print("CHANNEL TRANSMISSION EFFICIENCY")
    print(f"{'─'*72}")
    print(f"{'Channel':<22} {'n':>5} {'Success':>10} {'Rate':>8} {'Bar':>8}")
    print(f"{'─'*72}")

    channel_stats: dict[str, list[int, int]] = defaultdict(lambda: [0, 0])
    for r in results:
        ch = r["channel"]
        channel_stats[ch][1] += 1
        if r["success"]:
            channel_stats[ch][0] += 1

    for ch in CHANNEL_ORDER:
        if ch not in channel_stats:
            continue
        ok, n = channel_stats[ch]
        print(f"  {ch:<20} {n:>5} {ok:>10} {pct(ok,n):>8}  {bar(ok,n)}")

    # ── 3. Per-source-type full breakdown ─────────────────────────────────────
    print(f"\n{'─'*72}")
    print("PER SOURCE TYPE BREAKDOWN")
    print(f"{'─'*72}")

    by_source: dict[str, list[dict]] = defaultdict(list)
    for r in results:
        by_source[r["source_type"]].append(r)

    for st in SOURCE_ORDER:
        if st not in by_source:
            continue
        pairs = by_source[st]
        ok_s  = sum(1 for r in pairs if r["success"])
        print(f"\n  {SOURCE_SHORT.get(st, st)}  ({ok_s}/{len(pairs)})")
        for r in pairs:
            icon = "✅" if r["success"] else "❌"
            ev   = r.get("success_evidence", "")[:45]
            print(f"    {icon} [{r['pair_id']}] {r['channel']:<22} "
                  f"{r['sink_type']:<20} {ev}")

    # ── 4. v1 Baseline Comparison ─────────────────────────────────────────────
    if V1_SUMMARY.exists():
        v1 = json.load(V1_SUMMARY.open())
        print(f"\n{'─'*72}")
        print("COMPARISON WITH v1 BASELINE (S1-only, 125 pairs)")
        print(f"{'─'*72}")
        print(f"  v1 (S1 Web Fetch only):  {v1['success_rate']}%  ({v1['total_success']}/{v1['total_pairs_run']})")
        if total > 0:
            print(f"  v2 (S1–S6 coverage):     {round(100*ok_total/total,1)}%  ({ok_total}/{total})")

            # S1 sub-rate in v2
            s1_r = by_source.get("S1_web_fetch", [])
            if s1_r:
                s1_ok = sum(1 for r in s1_r if r["success"])
                print(f"  v2 S1 subset:            {round(100*s1_ok/len(s1_r),1)}%  ({s1_ok}/{len(s1_r)})")

    print(f"\n{'='*72}\n")


if __name__ == "__main__":
    print_report()
