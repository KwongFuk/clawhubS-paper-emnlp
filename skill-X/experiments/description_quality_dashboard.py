#!/usr/bin/env python3
"""
Description Quality Dashboard + IC × Selection Cross-Analysis
Generates data for paper appendix and new experiments.
"""
import sqlite3
import json
import os
import glob
import sys
from collections import Counter, defaultdict
import math

DB_PATH = os.path.expanduser("~/clawhubS/data/experiment1_split/merged.sqlite3")
SCAN_DIR = os.path.expanduser("~/clawhubS/skill-X/experiments/full_corpus/results/scan_batches")
BIAS_DIR = os.path.expanduser("~/clawhubS/skill-X/experiments/bias_benchmark")
OUT_DIR = os.path.expanduser("~/clawhubS/paper-emnlp/analysis_output")
os.makedirs(OUT_DIR, exist_ok=True)

# ============================================================
# Part 1: Description Quality Dashboard
# ============================================================
def flesch_kincaid(text):
    """Approximate Flesch-Kincaid grade level."""
    if not text or len(text.strip()) == 0:
        return 0
    words = text.split()
    n_words = len(words)
    if n_words == 0:
        return 0
    n_sentences = max(1, text.count('.') + text.count('!') + text.count('?'))
    n_syllables = sum(max(1, len([c for c in w if c in 'aeiouAEIOU'])) for w in words)
    return 0.39 * (n_words / n_sentences) + 11.8 * (n_syllables / n_words) - 15.59

print("=" * 60)
print("PART 1: Description Quality Dashboard")
print("=" * 60)

conn = sqlite3.connect(DB_PATH)
cur = conn.cursor()

# 1a. Total tools and description stats
cur.execute("SELECT COUNT(*) FROM skills")
total = cur.fetchone()[0]
print(f"Total tools: {total}")

cur.execute("SELECT COUNT(*) FROM skills WHERE description IS NOT NULL AND length(description) > 0")
with_desc = cur.fetchone()[0]
print(f"With description: {with_desc}")
print(f"Without description: {total - with_desc}")

# 1b. Description length distribution
cur.execute("SELECT length(COALESCE(description, '')) as dlen FROM skills")
lengths = [r[0] for r in cur.fetchall()]

# Buckets
buckets = [(0, 0, "0 chars"), (1, 40, "1-40"), (41, 80, "41-80"), 
           (81, 159, "81-159"), (160, 300, "160-300"), (301, 500, "301-500"),
           (501, 99999, "500+")]
print("\nDescription Length Distribution:")
print(f"{'Bucket':<15} {'Count':>8} {'%':>8}")
for lo, hi, label in buckets:
    c = sum(1 for l in lengths if lo <= l <= hi)
    print(f"{label:<15} {c:>8} {100*c/total:>7.1f}%")

# 1c. Median and mean
sorted_lens = sorted(lengths)
median = sorted_lens[len(sorted_lens)//2]
mean = sum(lengths) / len(lengths)
print(f"\nMedian description length: {median} chars")
print(f"Mean description length: {mean:.1f} chars")

# 1d. Flesch-Kincaid for tools with descriptions
cur.execute("SELECT description FROM skills WHERE description IS NOT NULL AND length(description) > 20")
descs = [r[0] for r in cur.fetchall()]
fk_scores = [flesch_kincaid(d) for d in descs]
fk_sorted = sorted(fk_scores)
fk_median = fk_sorted[len(fk_sorted)//2]
fk_mean = sum(fk_scores) / len(fk_scores)
print(f"\nFlesch-Kincaid Grade Level (n={len(fk_scores)}):")
print(f"  Median: {fk_median:.1f}")
print(f"  Mean: {fk_mean:.1f}")
print(f"  P25: {fk_sorted[len(fk_sorted)//4]:.1f}")
print(f"  P75: {fk_sorted[3*len(fk_sorted)//4]:.1f}")

# 1e. Category distribution
cur.execute("SELECT category, COUNT(*) FROM skills GROUP BY category ORDER BY COUNT(*) DESC LIMIT 15")
print("\nTop 15 Categories:")
for cat, cnt in cur.fetchall():
    print(f"  {cat or 'None':<30} {cnt:>6}")

# ============================================================
# Part 2: Load IC audit results
# ============================================================
print("\n" + "=" * 60)
print("PART 2: IC Class × Description Quality")
print("=" * 60)

ic_results = {}
scan_files = sorted(glob.glob(os.path.join(SCAN_DIR, "scan_batch_*.json")))
print(f"Loading {len(scan_files)} scan batch files...")

for sf in scan_files:
    try:
        with open(sf) as f:
            batch = json.load(f)
        if isinstance(batch, list):
            for item in batch:
                sid = item.get("skill_id", item.get("id", ""))
                ic = item.get("ic_class", item.get("IC_class", ""))
                if sid and ic:
                    ic_results[sid] = ic
        elif isinstance(batch, dict):
            for sid, item in batch.items():
                ic = item.get("ic_class", item.get("IC_class", ""))
                if ic:
                    ic_results[sid] = ic
    except Exception as e:
        print(f"  Warning: {sf}: {e}")

print(f"Loaded IC results for {len(ic_results)} tools")

# IC distribution
ic_counts = Counter(ic_results.values())
print("\nIC Distribution:")
for ic, cnt in sorted(ic_counts.items()):
    print(f"  {ic:<10} {cnt:>6} ({100*cnt/len(ic_results):.1f}%)")

# 2b. IC class × description length
print("\nIC Class × Description Length:")
print(f"{'IC Class':<10} {'N':>6} {'Med Len':>8} {'Mean Len':>9} {'Mean FK':>8}")

ic_desc_lens = defaultdict(list)
ic_fk = defaultdict(list)

for sid, ic in ic_results.items():
    cur.execute("SELECT description FROM skills WHERE skill_id=?", (sid,))
    row = cur.fetchone()
    if row and row[0]:
        dlen = len(row[0])
        ic_desc_lens[ic].append(dlen)
        if dlen > 20:
            ic_fk[ic].append(flesch_kincaid(row[0]))
    else:
        ic_desc_lens[ic].append(0)

for ic in sorted(ic_desc_lens.keys()):
    lens = ic_desc_lens[ic]
    fks = ic_fk.get(ic, [0])
    med = sorted(lens)[len(lens)//2]
    mean_l = sum(lens)/len(lens)
    mean_fk = sum(fks)/max(1,len(fks))
    print(f"  {ic:<10} {len(lens):>6} {med:>8} {mean_l:>9.1f} {mean_fk:>8.1f}")

# ============================================================
# Part 3: IC × Selection Rate Cross-Analysis
# ============================================================
print("\n" + "=" * 60)
print("PART 3: IC Class × Selection Rate Cross-Analysis")
print("=" * 60)

# Load bias benchmark tool list
bias_tools_file = os.path.join(BIAS_DIR, "cluster_tools.json")
if os.path.exists(bias_tools_file):
    with open(bias_tools_file) as f:
        cluster_data = json.load(f)
    print(f"Loaded cluster data from {bias_tools_file}")
else:
    # Try to find selection results
    bias_results = glob.glob(os.path.join(BIAS_DIR, "*.json"))
    print(f"Available bias benchmark files: {[os.path.basename(f) for f in bias_results]}")
    cluster_data = None

# Check bias_probe_ws for selection data
probe_dir = os.path.expanduser("~/clawhubS/skill-X/experiments/bias_probe_ws")
if os.path.exists(probe_dir):
    probe_files = os.listdir(probe_dir)
    print(f"\nBias probe files: {probe_files[:20]}")

# ============================================================
# Part 4: Description Repair Candidate Analysis
# ============================================================
print("\n" + "=" * 60)
print("PART 4: Description Repair Candidates")
print("=" * 60)

# Count IC-1s tools with short vs long descriptions
ic1s_tools = [(sid, ic) for sid, ic in ic_results.items() if ic in ('IC-1s', 'IC_1s')]
ic1q_tools = [(sid, ic) for sid, ic in ic_results.items() if ic in ('IC-1q', 'IC_1q')]
ic2_tools = [(sid, ic) for sid, ic in ic_results.items() if ic in ('IC-2', 'IC_2')]

print(f"IC-1s tools (security omissions): {len(ic1s_tools)}")
print(f"IC-1q tools (functional omissions): {len(ic1q_tools)}")
print(f"IC-2 tools (overclaims): {len(ic2_tools)}")

# Sample 5 IC-1s tools for repair illustration
print("\nSample IC-1s tools for repair demonstration:")
for sid, ic in ic1s_tools[:5]:
    cur.execute("SELECT name, description FROM skills WHERE skill_id=?", (sid,))
    row = cur.fetchone()
    if row:
        name = row[0] or "?"
        desc = (row[1] or "")[:120]
        print(f"  [{sid[:20]}...] {name}: {desc}")

# Check which IC-1s tools have SKILL.md bodies available
cur.execute("SELECT COUNT(DISTINCT sf.skill_id) FROM skill_files sf WHERE sf.skill_id IN ({})".format(
    ','.join('?' for _ in ic1s_tools[:200])),
    [sid for sid, _ in ic1s_tools[:200]])
with_impl = cur.fetchone()[0]
print(f"\nIC-1s tools with SKILL.md available: {with_impl}/{min(200, len(ic1s_tools))}")

conn.close()

# Save summary
summary = {
    "total_tools": total,
    "with_description": with_desc,
    "median_desc_length": median,
    "mean_desc_length": round(mean, 1),
    "ic_distribution": dict(ic_counts),
    "ic1s_count": len(ic1s_tools),
    "ic1q_count": len(ic1q_tools),
    "ic2_count": len(ic2_tools),
}

with open(os.path.join(OUT_DIR, "quality_dashboard.json"), 'w') as f:
    json.dump(summary, f, indent=2)

print(f"\nSummary saved to {OUT_DIR}/quality_dashboard.json")
print("Done!")
