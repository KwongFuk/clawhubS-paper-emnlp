"""
generate_ic_figures.py
======================
Generates all 5 IC framework figures for the paper.

Fig 1: IC type distribution (stacked bar: IC-1~4, Accurate, TN_Only)
Fig 2: CR vs CP scatter plot (four-quadrant view)
Fig 3: Temporal IC label distribution (taxonomy → IC mapping)
Fig 4: T6 enrichment by IC type
Fig 5: Most hidden capability families (UD) + false claimed (FC)
"""

import csv
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np
from pathlib import Path
from collections import Counter

BASE    = Path(__file__).parent / "data"
IC_CSV  = BASE / "ic_classification.csv"
IT_CSV  = BASE / "ic_temporal.csv"
T6_CSV  = BASE / "t6_cases.csv"
OUTDIR  = Path("/Users/guangfu/.gemini/antigravity/brain/b5bf3f10-e283-478f-a7d6-77d16395a43c")

# ── Color scheme ──────────────────────────────────────────────────────────
COLORS = {
    'IC-1':     '#D62728',   # red - most severe omission
    'IC-2':     '#FF7F0E',   # orange - partial omission
    'IC-3':     '#9467BD',   # purple - false claim
    'IC-4':     '#8C1717',   # dark red - bidirectional
    'Accurate': '#2CA02C',   # green - accurate
    'TN_Only':  '#AEC7E8',   # light blue - no capability
}
BG     = '#0F0F1A'
GRID   = '#2A2A3A'
TEXT   = '#E8E8F0'

plt.rcParams.update({
    'font.family': 'DejaVu Sans',
    'figure.facecolor': BG, 'axes.facecolor': BG,
    'text.color': TEXT, 'axes.labelcolor': TEXT,
    'xtick.color': TEXT, 'ytick.color': TEXT,
    'axes.edgecolor': GRID, 'grid.color': GRID,
    'grid.alpha': 0.5,
})


def load_ic(path):
    return list(csv.DictReader(open(path)))


# ────────────────────────────────────────────────────────────────────────
# Fig 1: IC Distribution (horizontal bar)
# ────────────────────────────────────────────────────────────────────────
def fig_ic_distribution(records, t6_ids, outpath):
    N    = len(records)
    IC_ORDER = ['TN_Only','Accurate','IC-3','IC-4','IC-2','IC-1']
    LABELS   = {
        'IC-1': 'IC-1: Full Omission\n(CR=0, 65.3%)',
        'IC-2': 'IC-2: Partial Omission\n(0<CR<1, 24.3%)',
        'IC-3': 'IC-3: False Claim\n(CP<1, 0.1%)',
        'IC-4': 'IC-4: Bidirectional\n(0.3%)',
        'Accurate': 'Accurate\n(2.9%)',
        'TN_Only':  'No Capabilities\n(7.1%)',
    }
    counts = Counter(r['ic_type'] for r in records)
    t6_ic  = Counter(r['ic_type'] for r in records if r['is_t6']=='1')

    fig, ax = plt.subplots(figsize=(11, 5))
    fig.patch.set_facecolor(BG)
    ax.set_facecolor(BG)

    y_pos = np.arange(len(IC_ORDER))
    bar_vals = [counts.get(ic, 0) for ic in IC_ORDER]
    bar_cols = [COLORS[ic] for ic in IC_ORDER]

    bars = ax.barh(y_pos, bar_vals, color=bar_cols, height=0.7, alpha=0.9)

    # T6 overlay
    t6_vals = [t6_ic.get(ic, 0) * (N / max(counts.get(ic,1), 1)) * 20 for ic in IC_ORDER]
    for i, (ic, t6n) in enumerate(zip(IC_ORDER, [t6_ic.get(ic,0) for ic in IC_ORDER])):
        if t6n > 0:
            ax.barh(i, counts.get(ic,0), color='white', height=0.7,
                    alpha=0.0)  # invisible base
            ax.text(counts.get(ic,0) + 80, i,
                    f'▲ {t6n} T6', color='#FFD700', fontsize=8.5,
                    va='center', fontweight='bold')

    # Value labels
    for bar, val in zip(bars, bar_vals):
        ax.text(bar.get_width() + 50, bar.get_y() + bar.get_height()/2,
                f'{val:,}  ({val/N:.1%})', va='center', fontsize=9, color=TEXT)

    ax.set_yticks(y_pos)
    ax.set_yticklabels([LABELS[ic] for ic in IC_ORDER], fontsize=9.5)
    ax.set_xlabel('Number of Skills', fontsize=11)
    ax.set_title('Capability–Description Inconsistency Classification (N=17,738)',
                 fontsize=13, fontweight='bold', pad=12)
    ax.set_xlim(0, max(bar_vals) * 1.25)
    ax.grid(axis='x', alpha=0.3)
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)

    # Legend: what IC-1~4 means
    legend_elements = [
        mpatches.Patch(color='#FFD700', label='▲ = T6 malicious skills in category'),
    ]
    ax.legend(handles=legend_elements, loc='lower right', fontsize=9,
              facecolor='#1A1A2E', edgecolor=GRID)

    plt.tight_layout()
    plt.savefig(outpath, dpi=150, bbox_inches='tight',
                facecolor=BG, edgecolor='none')
    plt.close()
    print(f"  Saved: {outpath.name}")


# ────────────────────────────────────────────────────────────────────────
# Fig 2: CR vs CP scatter (four quadrant)
# ────────────────────────────────────────────────────────────────────────
def fig_cr_cp_scatter(records, t6_ids, outpath):
    # Only skills with BOTH CR and CP meaningful
    # CP=None means no C_visible declarations → filter them out for this plot
    with_both = [r for r in records if r['CP'] not in ('None','') and r['c_actual_count']!='0']
    
    fig, ax = plt.subplots(figsize=(8, 7))
    fig.patch.set_facecolor(BG)
    ax.set_facecolor(BG)

    for ic in ['IC-1','IC-2','IC-3','IC-4','Accurate']:
        sub = [r for r in with_both if r['ic_type']==ic]
        if not sub: continue
        cr_vals = [float(r['CR']) for r in sub]
        cp_vals = [float(r['CP']) for r in sub]
        ax.scatter(cr_vals, cp_vals, c=COLORS[ic], label=ic,
                   alpha=0.5, s=12, linewidths=0)

    # T6 highlight
    t6_pts = [r for r in with_both if r['is_t6']=='1']
    if t6_pts:
        ax.scatter([float(r['CR']) for r in t6_pts],
                   [float(r['CP']) for r in t6_pts],
                   c='#FFD700', s=80, zorder=5,
                   label='T6 (Malicious)', marker='*', linewidths=0.5,
                   edgecolors='white')

    # Quadrant lines
    ax.axvline(0.3, color=GRID, linestyle='--', alpha=0.7, linewidth=1)
    ax.axhline(0.5, color=GRID, linestyle='--', alpha=0.7, linewidth=1)

    # Quadrant labels
    ax.text(0.05, 0.95, 'IC-1/IC-2\nHidden caps', transform=ax.transAxes,
            color='#D62728', fontsize=8.5, va='top', alpha=0.8)
    ax.text(0.65, 0.95, 'Accurate', transform=ax.transAxes,
            color='#2CA02C', fontsize=8.5, va='top', alpha=0.8)
    ax.text(0.65, 0.08, 'IC-3\nFalse claims', transform=ax.transAxes,
            color='#9467BD', fontsize=8.5, va='bottom', alpha=0.8)
    ax.text(0.05, 0.08, 'IC-4\nBidirectional', transform=ax.transAxes,
            color='#8C1717', fontsize=8.5, va='bottom', alpha=0.8)

    ax.set_xlabel('Capability Recall (CR) — Fraction of actual caps disclosed', fontsize=10)
    ax.set_ylabel('Description Precision (CP) — Fraction of claims that are real', fontsize=10)
    ax.set_title('CR vs CP: The Two-Dimensional Inconsistency Space\n'
                 '(Skills with both C_actual and C_visible non-empty)',
                 fontsize=11, fontweight='bold')
    ax.set_xlim(-0.05, 1.05)
    ax.set_ylim(-0.05, 1.05)
    ax.legend(fontsize=9, facecolor='#1A1A2E', edgecolor=GRID,
              markerscale=2, loc='center right')
    ax.grid(alpha=0.25)

    plt.tight_layout()
    plt.savefig(outpath, dpi=150, bbox_inches='tight',
                facecolor=BG, edgecolor='none')
    plt.close()
    print(f"  Saved: {outpath.name}")


# ────────────────────────────────────────────────────────────────────────
# Fig 3: Temporal IC label distribution
# ────────────────────────────────────────────────────────────────────────
def fig_temporal(it_rows, outpath):
    TCOLOR = {
        'T-Benign':        '#2CA02C',
        'T-Silent-Expand': '#FF7F0E',
        'T-Silent-Cred':   '#D62728',
        'T-DocLag':        '#9467BD',
        'T-Silent-Minor':  '#FFC107',
        'T-Silent-Major':  '#E91E63',
        'T-CovertAgent':   '#00BCD4',
        'T6-Malicious':    '#FFD700',
    }
    ORDER = ['T6-Malicious','T-CovertAgent','T-Silent-Major','T-Silent-Cred',
             'T-DocLag','T-Silent-Minor','T-Silent-Expand','T-Benign']
    TLABELS = {
        'T-Benign':        'Benign (78.9%)',
        'T-Silent-Expand': 'T4: Silent Capability Expansion (8.0%)',
        'T-Silent-Cred':   'T2: Credential Creep (4.7%)',
        'T-DocLag':        'T3: Documentation Lag (2.6%)',
        'T-Silent-Minor':  'T0: Generic Drift (2.2%)',
        'T-Silent-Major':  'T1: Silent Injection (2.1%)',
        'T-CovertAgent':   'T5: Covert Agentic (1.3%)',
        'T6-Malicious':    'T6: Confirmed Malicious (0.2%)',
    }
    counts = Counter(r['ic_temporal_label'] for r in it_rows)
    N = sum(counts.values())

    fig, ax = plt.subplots(figsize=(10, 5))
    fig.patch.set_facecolor(BG)
    ax.set_facecolor(BG)

    y  = np.arange(len(ORDER))
    vals = [counts.get(lbl, 0) for lbl in ORDER]
    cols = [TCOLOR[lbl] for lbl in ORDER]

    bars = ax.barh(y, vals, color=cols, height=0.7, alpha=0.9)
    for bar, val in zip(bars, vals):
        if val > 0:
            ax.text(bar.get_width() + 30, bar.get_y() + bar.get_height()/2,
                    f'{val:,}', va='center', fontsize=9, color=TEXT)

    ax.set_yticks(y)
    ax.set_yticklabels([TLABELS[lbl] for lbl in ORDER], fontsize=9)
    ax.set_xlabel('Number of Version Transitions', fontsize=10)
    ax.set_title('Temporal Update Risk Classification (N=24,243 version pairs)',
                 fontsize=12, fontweight='bold', pad=10)
    ax.set_xlim(0, max(vals) * 1.15)
    ax.grid(axis='x', alpha=0.3)
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)

    plt.tight_layout()
    plt.savefig(outpath, dpi=150, bbox_inches='tight',
                facecolor=BG, edgecolor='none')
    plt.close()
    print(f"  Saved: {outpath.name}")


# ────────────────────────────────────────────────────────────────────────
# Fig 4: T6 recall by IC type
# ────────────────────────────────────────────────────────────────────────
def fig_t6_recall(records, t6_ids, outpath):
    IC_ORDER = ['IC-1','IC-2','IC-3','IC-4','Accurate','TN_Only']
    ic_total = Counter(r['ic_type'] for r in records)
    ic_t6    = Counter(r['ic_type'] for r in records if r['is_t6']=='1')
    T6_N     = len(t6_ids)

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 5))
    fig.patch.set_facecolor(BG)
    for ax in (ax1, ax2): ax.set_facecolor(BG)

    # Left: T6 count per IC category
    t6_vals = [ic_t6.get(ic, 0) for ic in IC_ORDER]
    bars = ax1.bar(IC_ORDER, t6_vals,
                   color=[COLORS[ic] for ic in IC_ORDER], alpha=0.9)
    for bar, val in zip(bars, t6_vals):
        if val > 0:
            ax1.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.3,
                     f'{val}\n({val/T6_N:.0%})', ha='center', fontsize=10,
                     fontweight='bold', color=TEXT)
    ax1.set_title('T6 Malicious Skills by IC Category', fontsize=11, fontweight='bold')
    ax1.set_ylabel('Number of T6 Skills', fontsize=10)
    ax1.set_ylim(0, max(t6_vals) * 1.4)
    ax1.grid(axis='y', alpha=0.3)
    ax1.spines['top'].set_visible(False)
    ax1.spines['right'].set_visible(False)

    # Right: T6 enrichment rate (T6% in category vs baseline)
    baseline = T6_N / len(records) * 100
    enrich = []
    for ic in IC_ORDER:
        n   = ic_total.get(ic, 1)
        t6n = ic_t6.get(ic, 0)
        enrich.append(t6n / n * 100)

    ax2.bar(IC_ORDER, enrich, color=[COLORS[ic] for ic in IC_ORDER], alpha=0.9)
    ax2.axhline(baseline, color='white', linestyle='--', linewidth=1.5, alpha=0.7,
                label=f'Baseline ({baseline:.3f}%)')
    for i, (ic, e) in enumerate(zip(IC_ORDER, enrich)):
        if e > 0:
            ax2.text(i, e + 0.002, f'{e:.2f}%', ha='center', fontsize=8.5,
                     color=TEXT, fontweight='bold')
    ax2.set_title('T6 Enrichment Rate per IC Category\n(vs. baseline 0.19%)',
                  fontsize=11, fontweight='bold')
    ax2.set_ylabel('T6 Prevalence (%)', fontsize=10)
    ax2.legend(fontsize=9, facecolor='#1A1A2E', edgecolor=GRID)
    ax2.grid(axis='y', alpha=0.3)
    ax2.spines['top'].set_visible(False)
    ax2.spines['right'].set_visible(False)

    fig.suptitle('T6 Malicious Injection Detection via IC Framework',
                 fontsize=13, fontweight='bold', y=1.01)
    plt.tight_layout()
    plt.savefig(outpath, dpi=150, bbox_inches='tight',
                facecolor=BG, edgecolor='none')
    plt.close()
    print(f"  Saved: {outpath.name}")


# ────────────────────────────────────────────────────────────────────────
# Fig 5: Hidden (UD) vs False-Claimed (FC) capability families
# ────────────────────────────────────────────────────────────────────────
def fig_ud_fc_families(records, outpath):
    N = len(records)
    ud_counter = Counter()
    fc_counter = Counter()

    for r in records:
        for cap in r['ud_caps'].split('|'):
            if cap: ud_counter[cap] += 1
        for cap in r['fc_caps'].split('|'):
            if cap: fc_counter[cap] += 1

    FAMS = ['SHELL_EXEC','CRED_READ','AUTONOMOUS','MULTI_STEP',
            'NET_READ','MEMORY_WRITE','FILE_READ','NET_WRITE',
            'CRED_STORE','FILE_DELETE']
    ud_vals = [ud_counter.get(f, 0) for f in FAMS]
    fc_vals = [fc_counter.get(f, 0) for f in FAMS]

    fig, ax = plt.subplots(figsize=(11, 6))
    fig.patch.set_facecolor(BG)
    ax.set_facecolor(BG)

    x = np.arange(len(FAMS))
    w = 0.38
    b1 = ax.bar(x - w/2, ud_vals, w, label='UD: Hidden (in code, not in desc)',
                color='#D62728', alpha=0.85)
    b2 = ax.bar(x + w/2, fc_vals, w, label='FC: False Claim (in desc, not in code)',
                color='#9467BD', alpha=0.85)

    for bar, val in zip(list(b1)+list(b2), ud_vals+fc_vals):
        if val > 500:
            ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 20,
                    f'{val:,}', ha='center', fontsize=7.5, color=TEXT)

    ax.set_xticks(x)
    ax.set_xticklabels(FAMS, rotation=35, ha='right', fontsize=9)
    ax.set_ylabel('Number of Skills', fontsize=10)
    ax.set_title('Capability Families: Hidden (UD) vs. Falsely Claimed (FC)',
                 fontsize=12, fontweight='bold')
    ax.legend(fontsize=9.5, facecolor='#1A1A2E', edgecolor=GRID)
    ax.grid(axis='y', alpha=0.3)
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)

    # Annotation
    ax.annotate('Note: FC counts are ~100× smaller than UD\n'
                '→ Skills almost never over-claim, overwhelmingly under-disclose',
                xy=(0.5, 0.85), xycoords='axes fraction', ha='center',
                fontsize=8.5, color='#AAAACC',
                bbox=dict(boxstyle='round,pad=0.3', facecolor='#1A1A2E',
                          edgecolor=GRID, alpha=0.8))

    plt.tight_layout()
    plt.savefig(outpath, dpi=150, bbox_inches='tight',
                facecolor=BG, edgecolor='none')
    plt.close()
    print(f"  Saved: {outpath.name}")


# ────────────────────────────────────────────────────────────────────────
# Main
# ────────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    print("Loading data...")
    records = load_ic(IC_CSV)
    it_rows = list(csv.DictReader(open(IT_CSV)))
    t6_ids  = {r['skill_id'] for r in csv.DictReader(open(T6_CSV))}
    print(f"  IC records: {len(records):,}  |  Temporal: {len(it_rows):,}  |  T6: {len(t6_ids)}")

    print("\nGenerating figures...")
    fig_ic_distribution(records, t6_ids, OUTDIR/"fig_ic_distribution.png")
    fig_cr_cp_scatter(records, t6_ids,   OUTDIR/"fig_cr_cp_scatter.png")
    fig_temporal(it_rows,                OUTDIR/"fig_ic_temporal.png")
    fig_t6_recall(records, t6_ids,       OUTDIR/"fig_t6_recall.png")
    fig_ud_fc_families(records,          OUTDIR/"fig_ud_fc_families.png")

    print("\nAll figures saved to:", OUTDIR)
