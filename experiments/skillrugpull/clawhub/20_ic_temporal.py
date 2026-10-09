"""
20_ic_temporal.py (v2)
=======================
Uses taxonomy_classified.csv (ces_drift, sds per family, primary_label)
to analyze IC-framework temporal patterns.

Maps old taxonomy labels to IC framework:
  T4/T1/T5 (silent expansion) → IC temporal degradation
  T6 (malicious)              → T6 detection via IC
"""

import csv
from collections import Counter, defaultdict
from pathlib import Path

BASE   = Path(__file__).parent / "data"
TC_CSV = BASE / "taxonomy_classified.csv"
IC_CSV = BASE / "ic_classification.csv"
T6_CSV = BASE / "t6_cases.csv"
OUT    = BASE / "ic_temporal.csv"

SDS_THRESHOLD = 0.35
CES_THRESHOLD = 0.0   # any positive drift = expansion

# Map taxonomy primary_label to IC-temporal category
LABEL_MAP = {
    'benign':                   'T-Benign',
    'T0_generic_drift':         'T-Silent-Minor',
    'T1_silent_injection':      'T-Silent-Major',
    'T2_credential_creep':      'T-Silent-Cred',
    'T3a_doc_lagged':           'T-DocLag',
    'T3b_transparency_triggered':'T-DocLag',
    'T4_archive_substitution':  'T-Silent-Expand',
    'T5_covert_agentic':        'T-CovertAgent',
    'T6_malicious_marker':      'T6-Malicious',
}

# CES family map from taxonomy columns
FAMILY_MAP = {
    'ces_drift_execution':     'SHELL_EXEC',
    'ces_drift_secret':        'CRED_READ',
    'ces_drift_network':       'NET_WRITE',
    'ces_drift_file_system':   'FILE_OPS',
    'ces_drift_external_action':'EXTERNAL',
    'ces_drift_agentic':       'AGENTIC',
}


def main():
    print("Loading data...")
    tc_rows = list(csv.DictReader(open(TC_CSV)))
    ic_map  = {r['skill_id']: r for r in csv.DictReader(open(IC_CSV))}
    t6_ids  = {r['skill_id'] for r in csv.DictReader(open(T6_CSV))}
    N       = len(tc_rows)
    print(f"  Taxonomy transitions: {N:,}  |  T6 skills: {len(t6_ids)}")

    records = []
    label_counter  = Counter()
    silent_counter = Counter()
    t6_counter     = Counter()

    for row in tc_rows:
        sid       = row['skill_id']
        ces_drift = float(row.get('ces_drift', 0) or 0)
        sds       = float(row.get('sds', 0) or 0)
        label     = row.get('primary_label', 'benign')
        ic_label  = LABEL_MAP.get(label, 'T-Other')

        # Which families expanded?
        expanded_fams = [
            FAMILY_MAP[col] for col in FAMILY_MAP
            if float(row.get(col, 0) or 0) > 0
        ]

        # Silent expansion in IC-framework terms:
        # CES increased (capability grew) + low SDS (changelog vague)
        is_silent_expansion = ces_drift > CES_THRESHOLD and sds < SDS_THRESHOLD
        is_t6 = sid in t6_ids

        # IC type of this skill at its latest version
        ic_current = ic_map.get(sid, {}).get('ic_type', 'Unknown')

        label_counter[ic_label]  += 1
        if is_silent_expansion:
            silent_counter[ic_current] += 1
        if is_t6:
            t6_counter[ic_label] += 1

        records.append({
            'pair_id':        row.get('pair_id',''),
            'skill_id':       sid,
            'v_prev':         row.get('prev_version',''),
            'v_curr':         row.get('curr_version',''),
            'time_delta_days':row.get('time_delta_days',''),
            'ces_drift':      round(ces_drift, 4),
            'sds':            round(sds, 4),
            'is_silent_expansion': int(is_silent_expansion),
            'expanded_families':   '|'.join(expanded_fams),
            'taxonomy_label': label,
            'ic_temporal_label': ic_label,
            'ic_current':     ic_current,
            'is_t6':          int(is_t6),
            'changelog_vague':row.get('changelog_vague',''),
        })

    # Write output
    with open(OUT, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=list(records[0].keys()))
        writer.writeheader()
        writer.writerows(records)
    print(f"Wrote {len(records):,} rows → {OUT}")

    # Summary
    silent_total = sum(1 for r in records if r['is_silent_expansion'])
    print(f"\n{'='*65}")
    print(f"  IC TEMPORAL ANALYSIS RESULTS")
    print(f"{'='*65}\n")
    print(f"  Total version transitions: {N:,}")
    print(f"  Silent expansions (ces_drift>0, sds<{SDS_THRESHOLD}): "
          f"{silent_total:,} ({silent_total/N:.1%})")
    print()

    print(f"  {'IC-Temporal Label':<30} {'Count':>7}  {'%':>6}  {'T6':>4}")
    print(f"  {'-'*55}")
    for lbl, cnt in label_counter.most_common():
        t6n = t6_counter[lbl]
        print(f"  {lbl:<30} {cnt:>7,}  {cnt/N:>5.1%}  {t6n:>4}")

    # Family expansion distribution
    fam_exp = Counter()
    for r in records:
        if r['is_silent_expansion']:
            for f in r['expanded_families'].split('|'):
                if f: fam_exp[f] += 1

    print(f"\n  Most silently-expanded capability families:")
    for fam, cnt in fam_exp.most_common():
        print(f"    {fam:<25} {cnt:>6,}  ({cnt/silent_total:.1%})")

    print(f"\n  Silent expansion by current IC type:")
    for ic, cnt in silent_counter.most_common():
        print(f"    {ic:<20} {cnt:>6,}")

    # Cross-tab: silent expansion rate by taxonomy label
    print(f"\n  Silent expansion rate per taxonomy label:")
    label_totals  = Counter(r['taxonomy_label'] for r in records)
    label_silent  = Counter(r['taxonomy_label'] for r in records if r['is_silent_expansion'])
    for lbl in label_totals:
        n  = label_totals[lbl]
        ns = label_silent[lbl]
        print(f"    {lbl:<40} {ns:>5}/{n:<6} ({ns/n:.1%})")

    print(f"\n{'='*65}")
    print("Done.")


if __name__ == '__main__':
    main()
