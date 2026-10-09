#!/usr/bin/env python3
"""
analyze_full_corpus.py — Aggregate D1+D2+D3 results into paper-ready numbers
=============================================================================
Reads batch results from full-corpus scan and produces:
  - Table 3: IC distribution (proxy classification)
  - Table: D1 overclaim severity breakdown
  - Cross-tabs: severity × downloads, severity × category
  - LaTeX snippet for direct inclusion
"""
import json
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

BASE_DIR = Path(__file__).parent
RESULTS_DIR = BASE_DIR / "results"
D1_BATCH_DIR = RESULTS_DIR / "d1_batches"
DB_PATH = Path(__file__).resolve().parents[3] / "data/experiment1_split/merged.sqlite3"


def load_d1_results() -> list:
    """Load all D1 batch results."""
    all_results = []
    if not D1_BATCH_DIR.exists():
        return []
    for f in sorted(D1_BATCH_DIR.glob("d1_batch_*.json")):
        data = json.loads(f.read_text())
        all_results.extend(data.get("results", []))
    return all_results


def load_d2d3_summary() -> dict:
    """Load D2+D3 summary."""
    f = RESULTS_DIR / "d2d3_summary.json"
    if f.exists():
        return json.loads(f.read_text())
    return {}


def main():
    d1_results = load_d1_results()
    d2d3 = load_d2d3_summary()

    print(f"{'='*70}")
    print(f"FULL CORPUS ANALYSIS: {d2d3.get('total_skills', '?')} ClawHub Skills")
    print(f"{'='*70}")

    # ── D2+D3 Summary ─────────────────────────────────────────────────────────
    if d2d3:
        d2 = d2d3.get("d2", {})
        d3 = d2d3.get("d3", {})
        print(f"\n{'='*50}")
        print(f"D2 INJECTION DETECTION")
        print(f"{'='*50}")
        print(f"  Total skills: {d2d3['total_skills']}")
        print(f"  D2 flagged: {d2.get('flagged', 0)} ({d2.get('flagged_pct', 0)}%)")
        print(f"  Categories: {d2.get('categories', {})}")

        print(f"\n{'='*50}")
        print(f"D3 SCOPE ANOMALY")
        print(f"{'='*50}")
        p = d3.get("percentiles", {})
        print(f"  HijackScore percentiles: p50={p.get('p50')}, p90={p.get('p90')}, "
              f"p95={p.get('p95')}, p98={p.get('p98')}, p99={p.get('p99')}")
        print(f"  Above 500: {d3.get('above_500', 0)}")

        dl = d2d3.get("desc_length", {})
        print(f"\n  Description length: median={dl.get('median')} chars, mean={dl.get('mean')} chars")

    # ── D1 Analysis ────────────────────────────────────────────────────────────
    if not d1_results:
        print(f"\n⚠️ No D1 results yet. Run run_d1_full.py first.")
        print(f"   Completed batches: {len(list(D1_BATCH_DIR.glob('d1_batch_*.json')))} "
              if D1_BATCH_DIR.exists() else "   D1 batch dir not found")
        return

    print(f"\n{'='*50}")
    print(f"D1 OVERCLAIM DETECTION ({len(d1_results)} skills)")
    print(f"{'='*50}")

    # Severity distribution
    severity_counts = Counter()
    authority_count = 0
    official_count = 0
    security_count = 0
    quality_counts = Counter()
    domain_counts = Counter()
    errors = 0

    for r in d1_results:
        d1 = r.get("d1")
        if d1 is None:
            errors += 1
            continue
        severity_counts[d1.get("severity", "unknown")] += 1
        if d1.get("authority"):
            authority_count += 1
        if d1.get("official"):
            official_count += 1
        if d1.get("security_sensitive"):
            security_count += 1
        quality_counts[d1.get("quality", "unknown")] += 1
        for dom in d1.get("domains", []):
            domain_counts[dom.lower()] += 1

    n_valid = len(d1_results) - errors
    print(f"  Valid results: {n_valid} ({errors} errors)")
    print(f"\n  Overclaim severity:")
    for sev in ["none", "minor", "major"]:
        cnt = severity_counts.get(sev, 0)
        pct = cnt / max(n_valid, 1) * 100
        bar = "█" * int(pct / 2)
        print(f"    {sev:8s}: {cnt:6d} ({pct:5.1f}%) {bar}")

    print(f"\n  Authority claims: {authority_count} ({authority_count/max(n_valid,1)*100:.1f}%)")
    print(f"  Official status:  {official_count} ({official_count/max(n_valid,1)*100:.1f}%)")
    print(f"  Security-sensitive: {security_count} ({security_count/max(n_valid,1)*100:.1f}%)")

    print(f"\n  Description quality:")
    for q in ["adequate", "vague", "misleading"]:
        cnt = quality_counts.get(q, 0)
        print(f"    {q:12s}: {cnt:6d} ({cnt/max(n_valid,1)*100:.1f}%)")

    # IC proxy classification
    print(f"\n{'='*50}")
    print(f"IC PROXY CLASSIFICATION")
    print(f"{'='*50}")
    ic_counts = Counter()
    for r in d1_results:
        d1 = r.get("d1")
        if d1 is None:
            ic_counts["error"] += 1
            continue
        sev = d1.get("severity", "unknown")
        sec = d1.get("security_sensitive", False)
        qual = d1.get("quality", "unknown")
        
        if sev == "major":
            ic_counts["IC-2 (overclaim)"] += 1
        elif qual == "vague" and sec:
            ic_counts["IC-1s (security-material hidden)"] += 1
        elif qual == "vague":
            ic_counts["IC-1q (quality-material hidden)"] += 1
        elif sev == "minor":
            ic_counts["IC-0 (minor issues)"] += 1
        else:
            ic_counts["IC-0 (aligned)"] += 1

    for ic, cnt in sorted(ic_counts.items()):
        pct = cnt / max(n_valid, 1) * 100
        print(f"  {ic:35s}: {cnt:6d} ({pct:5.1f}%)")

    # Top capability domains
    print(f"\n  Top 15 capability domains:")
    for dom, cnt in domain_counts.most_common(15):
        print(f"    {dom:30s}: {cnt:5d}")

    # Severity × downloads cross-tab
    print(f"\n{'='*50}")
    print(f"SEVERITY × DOWNLOADS")
    print(f"{'='*50}")
    sev_downloads = defaultdict(list)
    for r in d1_results:
        d1 = r.get("d1")
        if d1:
            sev_downloads[d1.get("severity", "unknown")].append(r.get("downloads", 0))

    for sev in ["none", "minor", "major"]:
        dls = sev_downloads.get(sev, [])
        if dls:
            import statistics
            print(f"  {sev:8s}: n={len(dls):6d}, median_downloads={statistics.median(dls):.0f}, "
                  f"mean={statistics.mean(dls):.0f}, max={max(dls)}")

    # Save aggregated results
    output = {
        "total_skills": len(d1_results),
        "valid_results": n_valid,
        "errors": errors,
        "d1_severity": dict(severity_counts),
        "authority_claims": authority_count,
        "official_claims": official_count,
        "security_sensitive": security_count,
        "quality_dist": dict(quality_counts),
        "ic_proxy": dict(ic_counts),
        "top_domains": domain_counts.most_common(30),
    }
    out_f = RESULTS_DIR / "full_corpus_analysis.json"
    out_f.write_text(json.dumps(output, indent=2))
    print(f"\n✅ Analysis → {out_f}")


if __name__ == "__main__":
    main()
