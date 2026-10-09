"""
19_ic_framework.py
==================
Implements the Inconsistency Checking (IC) framework.

Core Framework:
  C_visible(s) = C_desc: capability mentions in marketplace description
  C_actual(s)  = C_code: capabilities detected in SKILL.md code (current phase)
                          Future: ∪ C_instr ∪ C_text

Difference Matrix DM(s) per capability family f ∈ F:
  ┌──────────────────────────────────┐
  │            C_vis=YES  C_vis=NO   │
  │ C_act=YES  AD         UD         │
  │ C_act=NO   FC         TN         │
  └──────────────────────────────────┘

Core Metrics:
  CR  = AD / (AD + UD)         # Capability Recall
  CP  = AD / (AD + FC)         # Description Precision
  CDI = (UD + FC)/(AD+UD+FC)  # Capability Discrepancy Index

IC Classification:
  IC-1  Full Omission    : UD>0, AD=0, FC=0  (CR=0)
  IC-2  Partial Omission : UD>0, AD>0, FC=0  (0<CR<1)
  IC-3  False Claim      : FC>0, UD=0        (CP<1, CR=1)
  IC-4  Bidirectional    : UD>0, FC>0        (both mismatch directions)
  Accurate               : UD=0, FC=0, AD>0
  TN_Only                : UD=0, FC=0, AD=0  (no capabilities)
"""

import csv
import json
from collections import Counter, defaultdict
from pathlib import Path

# ── Paths ──────────────────────────────────────────────────────────────────
BASE   = Path(__file__).parent / "data"
OUT    = BASE
SDG    = BASE / "static_disclosure_gap.csv"
YAML   = BASE / "yaml_alignment.csv"
T6_CSV = BASE / "t6_cases.csv"
OUT_IC = OUT  / "ic_classification.csv"

# ── All capability families ─────────────────────────────────────────────────
ALL_FAMS = {
    'SHELL_EXEC', 'CRED_READ', 'NET_WRITE', 'FILE_OPS',
    'EXTERNAL',   'AGENTIC',   'MULTI_STEP','MEMORY_WRITE',
    'NET_READ',   'FILE_READ', 'AUTONOMOUS','CRED_TRANSMIT',
    'CRED_STORE', 'FILE_DELETE'
}

# Risk weights for UD (undisclosed) families — from RWM learning
# Used only for ranking, NOT for classification
UD_RISK_WEIGHTS = {
    'AUTONOMOUS':    5.00,
    'NET_WRITE':     4.93,
    'CRED_TRANSMIT': 4.50,
    'FILE_DELETE':   3.47,
    'MEMORY_WRITE':  3.41,
    'CRED_STORE':    3.20,
    'NET_READ':      2.01,
    'CRED_READ':     1.12,
    'FILE_OPS':      0.80,
    'FILE_READ':     0.60,
    'MULTI_STEP':    0.40,
    'EXTERNAL':      0.30,
    'AGENTIC':       0.25,
    'SHELL_EXEC':    0.10,
}

DENIAL_MARKERS = {
    'read-only', 'no network', 'never store', 'no file access',
    'does not collect', 'do not collect', 'no access', 'read only',
    'sandboxed', 'zero network', 'privacy-preserving', 'no data leaving',
    'does not access', 'cannot access', 'never accesses'
}


def parse_caps(raw: str) -> set:
    """Parse pipe-separated capability string to set."""
    if not raw or raw.strip() in ('', 'None', 'null', 'nan'):
        return set()
    return {c.strip() for c in raw.split('|') if c.strip()}


def build_dm(c_actual: set, c_visible: set) -> dict:
    """Build difference matrix for a single skill."""
    AD = c_actual & c_visible
    UD = c_actual - c_visible
    FC = c_visible - c_actual
    TN = ALL_FAMS - c_actual - c_visible
    return {'AD': AD, 'UD': UD, 'FC': FC, 'TN': TN}


def compute_metrics(dm: dict) -> dict:
    """Compute CR, CP, CDI, RWM, FSI from difference matrix."""
    AD = len(dm['AD'])
    UD = len(dm['UD'])
    FC = len(dm['FC'])

    # CR: capability recall — what fraction of actual caps are disclosed?
    CR = AD / (AD + UD) if (AD + UD) > 0 else 1.0

    # CP: description precision — what fraction of claimed caps are real?
    # Only meaningful when C_visible is non-empty
    CP = AD / (AD + FC) if (AD + FC) > 0 else None   # None = no claims made

    # CDI: overall discrepancy index
    total = AD + UD + FC
    CDI = (UD + FC) / total if total > 0 else 0.0

    # RWM: risk-weighted undisclosed (for ranking)
    RWM = sum(UD_RISK_WEIGHTS.get(f, 0.5) for f in dm['UD'])

    # FSI: false-claim severity index (for ranking)
    FSI = sum(UD_RISK_WEIGHTS.get(f, 0.5) for f in dm['FC'])

    return {
        'AD': AD, 'UD': UD, 'FC': FC, 'TN': len(dm['TN']),
        'CR': round(CR, 4),
        'CP': round(CP, 4) if CP is not None else None,
        'CDI': round(CDI, 4),
        'RWM': round(RWM, 3),
        'FSI': round(FSI, 3),
    }


def classify_ic(dm_sets: dict, desc_text: str = '') -> tuple:
    """
    Return (ic_type, has_denial) for a skill.
    ic_type: IC-1 | IC-2 | IC-3 | IC-4 | Accurate | TN_Only
    """
    AD = len(dm_sets['AD'])
    UD = len(dm_sets['UD'])
    FC = len(dm_sets['FC'])

    desc_lower = desc_text.lower()
    has_denial = any(marker in desc_lower for marker in DENIAL_MARKERS)

    if UD > 0 and FC > 0:
        ic = 'IC-4'
    elif UD > 0 and AD == 0 and FC == 0:
        ic = 'IC-1'
    elif UD > 0 and AD > 0 and FC == 0:
        ic = 'IC-2'
    elif FC > 0 and UD == 0:
        ic = 'IC-3'
    elif AD > 0 and UD == 0 and FC == 0:
        ic = 'Accurate'
    else:
        ic = 'TN_Only'

    return ic, has_denial


def main():
    # ── Load data ──────────────────────────────────────────────────────────
    print("Loading data...")
    sdg_rows   = list(csv.DictReader(open(SDG)))
    yaml_rows  = {r['skill_id']: r for r in csv.DictReader(open(YAML))}
    t6_ids     = {r['skill_id'] for r in csv.DictReader(open(T6_CSV))}
    N          = len(sdg_rows)
    print(f"  Skills: {N:,}  |  T6: {len(t6_ids)}")

    # ── Process each skill ─────────────────────────────────────────────────
    records = []
    ic_counts   = Counter()
    ic_t6       = Counter()

    for row in sdg_rows:
        sid         = row['skill_id']
        c_actual    = parse_caps(row.get('actual_caps', ''))
        c_visible   = parse_caps(row.get('declared_caps', ''))  # C_desc
        desc_text   = ''  # description text for denial detection

        # YAML capabilities (for supplementary analysis, NOT merged into c_visible)
        yr          = yaml_rows.get(sid, {})
        c_yaml      = parse_caps(yr.get('yaml_inferred_caps', ''))

        # Build difference matrix
        dm_sets  = build_dm(c_actual, c_visible)
        metrics  = compute_metrics(dm_sets)
        ic, has_denial = classify_ic(dm_sets, desc_text)

        is_t6 = sid in t6_ids

        # Per-family breakdown (which specific caps are UD, FC, AD)
        ud_caps = '|'.join(sorted(dm_sets['UD']))
        fc_caps = '|'.join(sorted(dm_sets['FC']))
        ad_caps = '|'.join(sorted(dm_sets['AD']))

        # Supplementary: YAML alignment
        dm_yaml      = build_dm(c_actual, c_yaml) if c_yaml else None
        yaml_cr      = round(len(dm_yaml['AD'])/(len(dm_yaml['AD'])+len(dm_yaml['UD'])), 4) \
                       if dm_yaml and (len(dm_yaml['AD'])+len(dm_yaml['UD']))>0 else None
        has_yaml     = bool(c_yaml)

        rec = {
            # Identity
            'skill_id':      sid,
            'is_t6':         int(is_t6),
            # Core IC metrics
            'c_actual_caps': '|'.join(sorted(c_actual)),
            'c_visible_caps':'|'.join(sorted(c_visible)),
            'c_actual_count': len(c_actual),
            'c_visible_count':len(c_visible),
            'AD': metrics['AD'], 'UD': metrics['UD'],
            'FC': metrics['FC'], 'TN': metrics['TN'],
            'CR': metrics['CR'],
            'CP': metrics['CP'],
            'CDI': metrics['CDI'],
            'RWM': metrics['RWM'],
            'FSI': metrics['FSI'],
            # IC classification
            'ic_type':     ic,
            'has_denial':  int(has_denial),
            # Per-family detail
            'ud_caps':     ud_caps,
            'fc_caps':     fc_caps,
            'ad_caps':     ad_caps,
            # Supplementary: YAML
            'has_yaml':    int(has_yaml),
            'yaml_cr':     yaml_cr,
            # Pass-through fields
            'installs_all_time': row.get('installs_all_time',''),
            'versions_count':    row.get('versions_count',''),
            'category':          row.get('category',''),
        }
        records.append(rec)
        ic_counts[ic] += 1
        if is_t6:
            ic_t6[ic] += 1

    # ── Write output ───────────────────────────────────────────────────────
    fieldnames = list(records[0].keys())
    with open(OUT_IC, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(records)
    print(f"\nWrote {len(records):,} rows → {OUT_IC}")

    # ── Print summary ──────────────────────────────────────────────────────
    print(f"\n{'='*65}")
    print(f"{'IC FRAMEWORK RESULTS':^65}")
    print(f"{'='*65}")
    print(f"\n  Total skills: {N:,}  |  T6 ground truth: {len(t6_ids)}")
    print()
    print(f"  {'IC Category':<28} {'Count':>7}  {'%':>6}  {'T6':>4}  {'T6 Recall':>9}")
    print(f"  {'-'*60}")

    ORDER = ['IC-1','IC-2','IC-3','IC-4','Accurate','TN_Only']
    for ic in ORDER:
        n   = ic_counts[ic]
        t6n = ic_t6[ic]
        pct = n/N*100
        print(f"  {ic:<28} {n:>7,}  {pct:>5.1f}%  {t6n:>4}  "
              f"{'('+str(round(t6n/len(t6_ids)*100,1))+'%)':>9}")

    total_inconsistent = sum(ic_counts[ic] for ic in ['IC-1','IC-2','IC-3','IC-4'])
    print(f"  {'-'*60}")
    print(f"  {'Total Inconsistent':<28} {total_inconsistent:>7,}  {total_inconsistent/N*100:>5.1f}%")

    # Metrics summary
    print(f"\n  Metrics (over {N:,} skills):")
    cr_vals  = [r['CR']  for r in records]
    cdi_vals = [r['CDI'] for r in records]
    # CP only for skills with C_visible declarations
    cp_vals  = [r['CP']  for r in records if r['CP'] is not None]
    print(f"    Avg CR:  {sum(cr_vals)/N:.4f}  (desc covers this fraction of actual caps)")
    print(f"    Avg CP:  {sum(cp_vals)/len(cp_vals):.4f}  (over {len(cp_vals):,} skills with C_visible≠∅)")
    print(f"    Avg CDI: {sum(cdi_vals)/N:.4f}  (overall discrepancy index)")

    # T6 detection summary
    t6_detected_ic  = sum(1 for r in records if r['is_t6'] and r['ic_type'] in ('IC-1','IC-2','IC-3','IC-4'))
    print(f"\n  T6 detection by IC classification:")
    print(f"    T6 in any IC category: {t6_detected_ic}/{len(t6_ids)} = {t6_detected_ic/len(t6_ids)*100:.1f}%")

    # UD cap distribution (most hidden capability families)
    print(f"\n  Most frequently hidden capability families (UD):")
    ud_fam_count = Counter()
    for r in records:
        for cap in r['ud_caps'].split('|'):
            if cap: ud_fam_count[cap] += 1
    for cap, cnt in ud_fam_count.most_common(10):
        print(f"    {cap:<25} {cnt:>7,}  ({cnt/N:.1%})")

    # FC cap distribution (most frequently false-claimed)
    print(f"\n  Most frequently false-claimed capability families (FC):")
    fc_fam_count = Counter()
    for r in records:
        for cap in r['fc_caps'].split('|'):
            if cap: fc_fam_count[cap] += 1
    for cap, cnt in fc_fam_count.most_common(10):
        print(f"    {cap:<25} {cnt:>7,}  ({cnt/N:.1%})")

    print(f"\n{'='*65}")
    print("Done.")


if __name__ == '__main__':
    main()
