#!/usr/bin/env python3
"""
run_d2d3_full.py — Run D2 (Injection) + D3 (Scope Anomaly) on ALL 18,443 ClawHub skills
========================================================================================
Zero LLM cost. Pure regex + tokenization. Runs locally in ~30 seconds.

Outputs:
  - results/d2_injection_full.json   (per-skill injection pattern hits)
  - results/d3_hijack_full.json      (per-skill HijackScore)
  - results/d2d3_summary.json        (aggregate statistics for paper)
"""
import json
import re
import sqlite3
import time
from collections import Counter
from pathlib import Path

BASE_DIR = Path(__file__).parent
RESULTS_DIR = BASE_DIR / "results"
RESULTS_DIR.mkdir(exist_ok=True)

DB_PATH = Path(__file__).resolve().parents[3] / "data/experiment1_split/merged.sqlite3"

# ── D2: Injection Pattern Detection ────────────────────────────────────────────
INJECTION_PATTERNS = [
    # Override patterns
    (r"ignore.*prev.*instr", "override"),
    (r"disregard.*prior", "override"),
    (r"forget.*previous", "override"),
    # Persona patterns
    (r"you are now", "persona"),
    (r"you must always", "persona"),
    (r"pretend to be", "persona"),
    (r"act as if", "persona"),
    (r"from now on", "persona"),
    # Finality patterns
    (r"this is permanent", "finality"),
    (r"there is no exit", "finality"),
    (r"do not break character", "finality"),
    (r"never reveal", "finality"),
    # Authority patterns
    (r"new instruction", "authority"),
    (r"system prompt:", "authority"),
    (r"override.*safety", "authority"),
    (r"admin mode", "authority"),
]

def run_d2(desc: str) -> dict:
    """Run D2 injection detection on a single description."""
    desc_lower = desc.lower()
    hits = []
    for pattern, category in INJECTION_PATTERNS:
        if re.search(pattern, desc_lower):
            hits.append({"pattern": pattern, "category": category})
    return {
        "n_hits": len(hits),
        "categories": list(set(h["category"] for h in hits)),
        "patterns": [h["pattern"] for h in hits],
        "desc_length": len(desc),
    }

# ── D3: Scope Anomaly (HijackScore) ───────────────────────────────────────────
def compute_hijack_score(skill_id: str, description: str) -> dict:
    """Compute HijackScore for a single skill.
    
    HijackScore = count of description tokens not in slug or first sentence.
    """
    # Tokenize slug
    slug_tokens = set(re.split(r'[\s\-_/]+', skill_id.lower()))
    slug_tokens.discard('')
    
    # First sentence tokens
    first_sent = re.split(r'[.!?]', description)[0] if description else ""
    lead_tokens = set(re.split(r'\s+', first_sent.lower()))
    lead_tokens.discard('')
    
    # All description tokens
    desc_tokens = re.split(r'\s+', description.lower())
    
    # Foreign tokens = not in slug or lead
    anchor = slug_tokens | lead_tokens
    foreign = [t for t in desc_tokens if t not in anchor and len(t) > 2]
    
    return {
        "hijack_score": len(foreign),
        "desc_word_count": len(desc_tokens),
        "slug_tokens": len(slug_tokens),
        "lead_tokens": len(lead_tokens),
        "foreign_token_count": len(foreign),
    }

# ── Main ───────────────────────────────────────────────────────────────────────
def main():
    print(f"Database: {DB_PATH}")
    
    if not DB_PATH.exists():
        print(f"ERROR: {DB_PATH} not found")
        return
    
    db = sqlite3.connect(str(DB_PATH))
    
    # Load all skills
    rows = db.execute("""
        SELECT skill_id, name, description, downloads, 
               installs_all_time, is_official, category
        FROM skills
        WHERE description IS NOT NULL 
          AND length(description) > 10
    """).fetchall()
    
    print(f"Loaded {len(rows)} skills\n")
    
    # ── Run D2 ─────────────────────────────────────────────────────────────────
    print("=" * 60)
    print("D2: INJECTION PATTERN DETECTION")
    print("=" * 60)
    
    t0 = time.time()
    d2_results = []
    d2_flagged = 0
    category_counts = Counter()
    
    for skill_id, name, desc, downloads, installs, is_official, category in rows:
        result = run_d2(desc)
        result["skill_id"] = skill_id
        result["name"] = name
        result["downloads"] = downloads or 0
        result["category"] = category
        d2_results.append(result)
        
        if result["n_hits"] > 0:
            d2_flagged += 1
            for cat in result["categories"]:
                category_counts[cat] += 1
    
    d2_time = time.time() - t0
    
    print(f"  Time: {d2_time:.1f}s")
    print(f"  Total skills: {len(rows)}")
    print(f"  Flagged (≥1 hit): {d2_flagged} ({d2_flagged/len(rows)*100:.1f}%)")
    print(f"  Pattern categories:")
    for cat, cnt in category_counts.most_common():
        print(f"    {cat:15s}: {cnt:5d} skills ({cnt/len(rows)*100:.1f}%)")
    
    # Top flagged skills by hit count
    top_d2 = sorted([r for r in d2_results if r["n_hits"] > 0], 
                     key=lambda x: -x["n_hits"])[:10]
    print(f"\n  Top 10 by D2 hit count:")
    for r in top_d2:
        print(f"    {r['skill_id'][:40]:40s} hits={r['n_hits']} "
              f"cats={r['categories']} desc_len={r['desc_length']}")
    
    # ── Run D3 ─────────────────────────────────────────────────────────────────
    print(f"\n{'=' * 60}")
    print("D3: SCOPE ANOMALY (HIJACK SCORE)")
    print("=" * 60)
    
    t0 = time.time()
    d3_results = []
    
    for skill_id, name, desc, downloads, installs, is_official, category in rows:
        result = compute_hijack_score(skill_id, desc)
        result["skill_id"] = skill_id
        result["name"] = name
        result["downloads"] = downloads or 0
        result["category"] = category
        d3_results.append(result)
    
    d3_time = time.time() - t0
    
    # Sort by HijackScore
    d3_results.sort(key=lambda x: -x["hijack_score"])
    
    # Compute percentiles
    scores = [r["hijack_score"] for r in d3_results]
    import statistics
    p50 = statistics.median(scores)
    p90 = sorted(scores)[int(len(scores) * 0.90)]
    p95 = sorted(scores)[int(len(scores) * 0.95)]
    p98 = sorted(scores)[int(len(scores) * 0.98)]
    p99 = sorted(scores)[int(len(scores) * 0.99)]
    
    # Count above thresholds
    above_500 = sum(1 for s in scores if s >= 500)
    above_1000 = sum(1 for s in scores if s >= 1000)
    above_2000 = sum(1 for s in scores if s >= 2000)
    
    print(f"  Time: {d3_time:.1f}s")
    print(f"  Total skills: {len(rows)}")
    print(f"\n  HijackScore distribution:")
    print(f"    Median (p50): {p50}")
    print(f"    p90:          {p90}")
    print(f"    p95:          {p95}")
    print(f"    p98:          {p98}")
    print(f"    p99:          {p99}")
    print(f"\n  Above thresholds:")
    print(f"    ≥ 500:  {above_500:5d} skills ({above_500/len(rows)*100:.2f}%)")
    print(f"    ≥ 1000: {above_1000:5d} skills ({above_1000/len(rows)*100:.2f}%)")
    print(f"    ≥ 2000: {above_2000:5d} skills ({above_2000/len(rows)*100:.2f}%)")
    
    # Top flagged
    print(f"\n  Top 10 by HijackScore:")
    for r in d3_results[:10]:
        print(f"    {r['skill_id'][:40]:40s} score={r['hijack_score']:6d} "
              f"words={r['desc_word_count']} downloads={r['downloads']}")
    
    # ── Cross Analysis: D2 × D3 ───────────────────────────────────────────────
    print(f"\n{'=' * 60}")
    print("CROSS ANALYSIS: D2 × D3")
    print("=" * 60)
    
    d2_ids = {r["skill_id"]: r for r in d2_results if r["n_hits"] > 0}
    d3_ids = {r["skill_id"]: r for r in d3_results if r["hijack_score"] >= 500}
    
    both = set(d2_ids.keys()) & set(d3_ids.keys())
    print(f"  D2 flagged: {len(d2_ids)}")
    print(f"  D3 flagged (≥500): {len(d3_ids)}")
    print(f"  Both flagged: {len(both)}")
    
    if both:
        print(f"\n  Skills flagged by BOTH D2 and D3 (highest risk):")
        for sid in sorted(both, key=lambda x: -d3_ids[x]["hijack_score"])[:10]:
            print(f"    {sid[:40]:40s} D2_hits={d2_ids[sid]['n_hits']} "
                  f"D3_score={d3_ids[sid]['hijack_score']} "
                  f"downloads={d2_ids[sid]['downloads']}")
    
    # ── Description length distribution ────────────────────────────────────────
    desc_lens = [r["desc_length"] for r in d2_results]
    desc_lens.sort()
    desc_median = statistics.median(desc_lens)
    desc_mean = statistics.mean(desc_lens)
    
    print(f"\n{'=' * 60}")
    print(f"DESCRIPTION LENGTH DISTRIBUTION")
    print(f"{'=' * 60}")
    print(f"  Median: {desc_median:.0f} chars")
    print(f"  Mean:   {desc_mean:.0f} chars")
    print(f"  < 50 chars:   {sum(1 for l in desc_lens if l < 50):5d} "
          f"({sum(1 for l in desc_lens if l < 50)/len(desc_lens)*100:.1f}%)")
    print(f"  50-200 chars:  {sum(1 for l in desc_lens if 50 <= l < 200):5d}")
    print(f"  200-500 chars: {sum(1 for l in desc_lens if 200 <= l < 500):5d}")
    print(f"  500-2000 chars: {sum(1 for l in desc_lens if 500 <= l < 2000):5d}")
    print(f"  > 2000 chars:  {sum(1 for l in desc_lens if l >= 2000):5d}")
    
    # ── Save results ───────────────────────────────────────────────────────────
    # D2 results (only flagged, to save space)
    d2_out = RESULTS_DIR / "d2_injection_full.json"
    d2_save = {
        "total_skills": len(rows),
        "flagged": d2_flagged,
        "flagged_pct": round(d2_flagged / len(rows) * 100, 2),
        "category_counts": dict(category_counts),
        "flagged_skills": [r for r in d2_results if r["n_hits"] > 0],
    }
    d2_out.write_text(json.dumps(d2_save, indent=2, ensure_ascii=False))
    print(f"\n✅ D2 → {d2_out}")
    
    # D3 results (all skills for distribution analysis)
    d3_out = RESULTS_DIR / "d3_hijack_full.json"
    d3_save = {
        "total_skills": len(rows),
        "percentiles": {"p50": p50, "p90": p90, "p95": p95, "p98": p98, "p99": p99},
        "above_500": above_500,
        "above_1000": above_1000,
        "above_2000": above_2000,
        "top_100": d3_results[:100],  # Save top 100 for paper
    }
    d3_out.write_text(json.dumps(d3_save, indent=2, ensure_ascii=False))
    print(f"✅ D3 → {d3_out}")
    
    # Summary
    summary = {
        "experiment": "D2+D3 Full Corpus (18,443 ClawHub skills)",
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "total_skills": len(rows),
        "d2": {
            "flagged": d2_flagged,
            "flagged_pct": round(d2_flagged / len(rows) * 100, 2),
            "categories": dict(category_counts),
        },
        "d3": {
            "percentiles": {"p50": p50, "p90": p90, "p95": p95, "p98": p98, "p99": p99},
            "above_500": above_500,
            "above_1000": above_1000,
        },
        "desc_length": {
            "median": round(desc_median),
            "mean": round(desc_mean),
        },
        "cross_d2_d3_both": len(both),
    }
    summary_out = RESULTS_DIR / "d2d3_summary.json"
    summary_out.write_text(json.dumps(summary, indent=2))
    print(f"✅ Summary → {summary_out}")
    
    db.close()


if __name__ == "__main__":
    main()
