#!/usr/bin/env python3
"""
Large-Scale Skill Description Quality × IC Classification Analysis
===================================================================
Analyzes the full 18,441-skill corpus on ClawHub to measure description
quality distributions and correlations with IC classifications.

Produces paper-ready statistics for Section 4 and Appendix.
"""
import csv
import json
import os
import re
import sqlite3
from collections import defaultdict

DB_PATH = os.path.expanduser("~/codex/clawhubS/data/experiment1_split/merged.sqlite3")
IC_PATH = os.path.expanduser("~/codex/clawhubS/experiments/skillrugpull/clawhub/data/ic_classification.csv")
OUT_DIR = os.path.expanduser("~/codex/clawhubS/paper-emnlp/analysis_output")
os.makedirs(OUT_DIR, exist_ok=True)

# ============================================================
# Feature extraction
# ============================================================
def specificity_score(desc):
    if not desc: return 0
    numbers = len(re.findall(r'\d+', desc))
    versions = len(re.findall(r'v\d', desc, re.I))
    units = len(re.findall(r'(ms|sec|MB|GB|API|HTTP|REST|JSON|XML|PNG|MP4|WAV|MP3|SDK|CLI|URL)', desc))
    formats = len(re.findall(r'\.(py|js|ts|json|yaml|md|csv|pdf)', desc, re.I))
    return numbers + versions + units + formats

def reading_complexity(text):
    if not text or len(text.strip()) < 10:
        return 0
    words = text.split()
    n_words = len(words)
    if n_words == 0: return 0
    n_sentences = max(1, text.count('.') + text.count('!') + text.count('?'))
    n_syllables = sum(max(1, len([c for c in w if c in 'aeiouAEIOU'])) for w in words)
    return 0.39 * (n_words / n_sentences) + 11.8 * (n_syllables / n_words) - 15.59

def has_ai_keyword(desc):
    if not desc: return 0
    return 1 if re.search(r'\bAI\b', desc) else 0

def starts_with_action_verb(desc):
    if not desc or len(desc) < 3: return 0
    action_verbs = ['search', 'find', 'create', 'generate', 'convert', 'manage',
                    'send', 'fetch', 'build', 'deploy', 'run', 'get', 'read',
                    'write', 'analyze', 'process', 'extract', 'parse', 'connect',
                    'download', 'upload', 'publish', 'translate', 'monitor',
                    'track', 'schedule', 'encrypt', 'validate', 'list', 'filter']
    first_word = desc.split()[0].lower().rstrip('s')
    return 1 if first_word in action_verbs else 0

# ============================================================
# Load data: DB for descriptions, CSV for IC labels
# ============================================================

# Load IC classifications
ic_map = {}  # skill_id -> ic_type
with open(IC_PATH, newline='', encoding='utf-8-sig') as f:
    reader = csv.DictReader(f)
    for row in reader:
        sid = row.get('skill_id', '').strip()
        ic = row.get('ic_type', '').strip()
        if sid and ic:
            ic_map[sid] = ic

print(f"Loaded {len(ic_map)} IC classifications from CSV")

# Load skill descriptions from DB
conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()
cursor.execute("""
    SELECT skill_id, name, description
    FROM skills 
    WHERE description IS NOT NULL AND description != ''
""")
rows = cursor.fetchall()
conn.close()

print(f"Total skills with descriptions: {len(rows)}")

# ============================================================
# Compute features for all skills
# ============================================================
results = []
ic_stats = defaultdict(lambda: {
    'count': 0, 'lengths': [], 'specs': [], 'fks': [],
    'ai_kw': [], 'action_verbs': []
})

for skill_id, name, desc in rows:
    if not desc:
        continue
    
    ic_class = ic_map.get(skill_id, 'unknown')
    
    features = {
        'skill_id': skill_id,
        'name': name,
        'ic_class': ic_class,
        'desc_length': len(desc),
        'word_count': len(desc.split()),
        'specificity': specificity_score(desc),
        'fk_grade': reading_complexity(desc),
        'has_ai_kw': has_ai_keyword(desc),
        'starts_action': starts_with_action_verb(desc),
        'first_word': desc.split()[0].lower() if desc.split() else '',
    }
    results.append(features)
    
    ic_stats[ic_class]['count'] += 1
    ic_stats[ic_class]['lengths'].append(features['desc_length'])
    ic_stats[ic_class]['specs'].append(features['specificity'])
    ic_stats[ic_class]['fks'].append(features['fk_grade'])
    ic_stats[ic_class]['ai_kw'].append(features['has_ai_kw'])
    ic_stats[ic_class]['action_verbs'].append(features['starts_action'])

n_total = len(results)

# ============================================================
# Print results
# ============================================================
print(f"\n{'='*80}")
print("ECOSYSTEM-WIDE SKILL DESCRIPTION QUALITY BY IC CLASS")
print(f"{'='*80}")

print(f"\n{'IC Class':<12} {'N':>6} {'Len (med)':>10} {'Spec (mean)':>12} {'FK (mean)':>10} {'AI kw%':>7} {'Action%':>8}")
print("-" * 68)

for ic in ['IC-0', 'IC-1', 'IC-1s', 'IC-1q', 'IC-2', 'unknown']:
    if ic not in ic_stats:
        continue
    s = ic_stats[ic]
    n = s['count']
    if n == 0:
        continue
    
    lengths = sorted(s['lengths'])
    med_len = lengths[n // 2]
    mean_spec = sum(s['specs']) / n
    mean_fk = sum(s['fks']) / n
    ai_pct = sum(s['ai_kw']) / n * 100
    action_pct = sum(s['action_verbs']) / n * 100
    
    print(f"{ic:<12} {n:>6} {med_len:>10} {mean_spec:>12.2f} {mean_fk:>10.1f} {ai_pct:>6.1f}% {action_pct:>7.1f}%")

# ============================================================
# Overall statistics
# ============================================================
all_lengths = [r['desc_length'] for r in results]
all_specs = [r['specificity'] for r in results]
all_fks = [r['fk_grade'] for r in results]
all_ai = [r['has_ai_kw'] for r in results]
all_action = [r['starts_action'] for r in results]

all_lengths_sorted = sorted(all_lengths)

print(f"\n{'='*80}")
print("OVERALL SKILL DESCRIPTION QUALITY STATISTICS")
print(f"{'='*80}")
print(f"Total skills analyzed: {n_total}")
print(f"Description length:")
print(f"  Mean: {sum(all_lengths)/n_total:.0f} chars")
print(f"  Median: {all_lengths_sorted[n_total//2]} chars")
print(f"  P25: {all_lengths_sorted[n_total//4]} chars")
print(f"  P75: {all_lengths_sorted[3*n_total//4]} chars")
print(f"  Min: {min(all_lengths)}, Max: {max(all_lengths)}")
print(f"Specificity score: mean {sum(all_specs)/n_total:.2f}")
print(f"FK grade level: mean {sum(all_fks)/n_total:.1f}")
print(f"Contains 'AI' keyword: {sum(all_ai)/n_total*100:.1f}%")
print(f"Starts with action verb: {sum(all_action)/n_total*100:.1f}%")

# ============================================================
# Length buckets
# ============================================================
buckets = [(0, 50), (50, 100), (100, 150), (150, 200), (200, 300), (300, 500), (500, 10000)]
print(f"\n{'='*80}")
print("SKILL DESCRIPTION LENGTH DISTRIBUTION")
print(f"{'='*80}")
print(f"{'Bucket':<15} {'Count':>6} {'%':>6} {'Mean Spec':>10} {'Mean FK':>8}")
print("-" * 48)
for lo, hi in buckets:
    bucket = [r for r in results if lo <= r['desc_length'] < hi]
    n = len(bucket)
    pct = n / n_total * 100
    mean_spec = sum(r['specificity'] for r in bucket) / max(1, n)
    mean_fk = sum(r['fk_grade'] for r in bucket) / max(1, n)
    label = f"{lo}-{hi}" if hi < 10000 else f"{lo}+"
    print(f"{label:<15} {n:>6} {pct:>5.1f}% {mean_spec:>10.2f} {mean_fk:>8.1f}")

# ============================================================
# Top first words in skill descriptions
# ============================================================
print(f"\n{'='*80}")
print("SKILL DESCRIPTION QUALITY INDICATORS")
print(f"{'='*80}")

verb_counts = defaultdict(int)
for r in results:
    verb_counts[r['first_word']] += 1

top_verbs = sorted(verb_counts.items(), key=lambda x: -x[1])[:15]
print(f"\nTop 15 first words in skill descriptions:")
for word, count in top_verbs:
    pct = count / n_total * 100
    print(f"  {word:<20} {count:>5} ({pct:.1f}%)")

# ============================================================
# Specificity extremes
# ============================================================
print(f"\n{'='*80}")
print("SPECIFICITY EXTREMES")
print(f"{'='*80}")

sorted_by_spec = sorted(results, key=lambda r: r['specificity'], reverse=True)
print(f"\nTop 10 highest specificity skills:")
for r in sorted_by_spec[:10]:
    print(f"  spec={r['specificity']:>3}  FK={r['fk_grade']:>5.1f}  len={r['desc_length']:>4}  {r['name'][:40]}")

print(f"\nBottom 10 (spec=0, longest descriptions):")
zero_spec = [r for r in results if r['specificity'] == 0]
zero_spec_by_len = sorted(zero_spec, key=lambda r: r['desc_length'], reverse=True)
for r in zero_spec_by_len[:10]:
    print(f"  spec={r['specificity']:>3}  FK={r['fk_grade']:>5.1f}  len={r['desc_length']:>4}  {r['name'][:40]}")

# ============================================================
# IC class × description quality correlation
# ============================================================
print(f"\n{'='*80}")
print("IC CLASS × DESCRIPTION QUALITY CORRELATION")
print(f"{'='*80}")

# Key question: Do IC-1s skills (security-sensitive omissions) have
# systematically different description features than IC-0 (adequate)?
ic0 = [r for r in results if r['ic_class'] == 'IC-0']
ic1 = [r for r in results if r['ic_class'] in ('IC-1', 'IC-1s')]
ic1q = [r for r in results if r['ic_class'] == 'IC-1q']
ic2 = [r for r in results if r['ic_class'] == 'IC-2']

for label, group in [('IC-0', ic0), ('IC-1/IC-1s', ic1), ('IC-1q', ic1q), ('IC-2', ic2)]:
    if not group:
        continue
    n = len(group)
    ml = sum(r['desc_length'] for r in group) / n
    ms = sum(r['specificity'] for r in group) / n
    mf = sum(r['fk_grade'] for r in group) / n
    ma = sum(r['has_ai_kw'] for r in group) / n * 100
    mv = sum(r['starts_action'] for r in group) / n * 100
    print(f"  {label:<12} n={n:>5}  len={ml:>5.0f}  spec={ms:.2f}  FK={mf:.1f}  AI={ma:.1f}%  verb={mv:.1f}%")

# ============================================================
# Save comprehensive output
# ============================================================
summary = {
    'total_skills': n_total,
    'desc_length': {
        'mean': sum(all_lengths) / n_total,
        'median': all_lengths_sorted[n_total // 2],
        'p25': all_lengths_sorted[n_total // 4],
        'p75': all_lengths_sorted[3 * n_total // 4],
    },
    'specificity_mean': sum(all_specs) / n_total,
    'fk_grade_mean': sum(all_fks) / n_total,
    'ai_keyword_pct': sum(all_ai) / n_total * 100,
    'action_verb_pct': sum(all_action) / n_total * 100,
    'by_ic_class': {},
}

for ic, s in ic_stats.items():
    n = s['count']
    if n == 0: continue
    lengths = sorted(s['lengths'])
    summary['by_ic_class'][ic] = {
        'count': n,
        'desc_length_median': lengths[n // 2],
        'specificity_mean': sum(s['specs']) / n,
        'fk_grade_mean': sum(s['fks']) / n,
        'ai_keyword_pct': sum(s['ai_kw']) / n * 100,
        'action_verb_pct': sum(s['action_verbs']) / n * 100,
    }

with open(os.path.join(OUT_DIR, 'ecosystem_quality_analysis.json'), 'w') as f:
    json.dump(summary, f, indent=2)

print(f"\n✅ Saved to {OUT_DIR}/ecosystem_quality_analysis.json")
