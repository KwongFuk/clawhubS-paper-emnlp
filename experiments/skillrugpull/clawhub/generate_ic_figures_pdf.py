#!/usr/bin/env python3
"""
Generate IC framework figures as PDF for the paper.
Reads from ic_classification.csv and ic_temporal.csv.
Outputs to paper/figures/ directory.
"""

import csv
import os
from collections import Counter, defaultdict
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np

HERE = Path(__file__).parent
DATA_DIR = HERE / "data"
FIG_DIR = HERE.parent.parent.parent / "paper" / "figures"
FIG_DIR.mkdir(parents=True, exist_ok=True)

IC_CSV = DATA_DIR / "ic_classification.csv"
TEMPORAL_CSV = DATA_DIR / "ic_temporal.csv"

# Color palette
COLORS = {
    'IC-1': '#d62728',   # red - dangerous full omission
    'IC-2': '#ff7f0e',   # orange - partial omission
    'IC-3': '#9467bd',   # purple - false claim
    'IC-4': '#e377c2',   # pink - bidirectional
    'Accurate': '#2ca02c',  # green
    'TN_Only': '#7f7f7f',   # gray
}

T6_COLOR = '#FFD700'  # gold for T6

IC_ORDER = ['IC-1', 'IC-2', 'IC-3', 'IC-4', 'Accurate', 'TN_Only']
IC_LABELS = {
    'IC-1': 'IC-1: Full Omission',
    'IC-2': 'IC-2: Partial Omission',
    'IC-3': 'IC-3: False Claim',
    'IC-4': 'IC-4: Bidirectional',
    'Accurate': 'Accurate',
    'TN_Only': 'No Capabilities',
}


def load_ic_data():
    with open(IC_CSV, newline='') as f:
        return list(csv.DictReader(f))


def load_temporal_data():
    with open(TEMPORAL_CSV, newline='') as f:
        return list(csv.DictReader(f))


def fig1_ic_distribution(rows):
    """IC type distribution horizontal bar chart."""
    ic_counts = Counter(r['ic_type'] for r in rows)
    t6_counts = Counter(r['ic_type'] for r in rows if r.get('is_t6') == '1')
    N = len(rows)

    fig, ax = plt.subplots(figsize=(8, 4))

    order = IC_ORDER
    y_pos = np.arange(len(order))
    counts = [ic_counts.get(ic, 0) for ic in order]
    t6_cnts = [t6_counts.get(ic, 0) for ic in order]
    colors = [COLORS[ic] for ic in order]

    bars = ax.barh(y_pos, counts, color=colors, edgecolor='white', linewidth=0.5, alpha=0.85)
    ax.set_yticks(y_pos)
    ax.set_yticklabels([IC_LABELS[ic] for ic in order], fontsize=10)
    ax.set_xlabel('Number of Skills', fontsize=11)
    ax.set_title(f'IC Type Distribution (N={N:,})', fontsize=13, fontweight='bold')
    ax.invert_yaxis()

    # Add count labels
    for i, (bar, cnt, t6) in enumerate(zip(bars, counts, t6_cnts)):
        pct = cnt / N * 100
        label = f'{cnt:,} ({pct:.1f}%)'
        ax.text(bar.get_width() + N * 0.01, bar.get_y() + bar.get_height()/2,
                label, va='center', fontsize=9)
        # T6 marker
        if t6 > 0:
            ax.scatter(cnt * 0.98, bar.get_y() + bar.get_height()/2,
                      marker='^', s=100, c=T6_COLOR, edgecolors='black',
                      linewidths=0.8, zorder=5)
            ax.text(cnt * 0.95, bar.get_y() + bar.get_height()/2 - 0.15,
                   f'T6: {t6}', fontsize=7, ha='right', color='#8B6914')

    ax.set_xlim(0, max(counts) * 1.25)
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)

    # Add T6 legend
    t6_marker = plt.Line2D([0], [0], marker='^', color='w', markerfacecolor=T6_COLOR,
                           markeredgecolor='black', markersize=10, label='T6 (malicious)')
    ax.legend(handles=[t6_marker], loc='lower right', fontsize=9)

    plt.tight_layout()
    out = FIG_DIR / "fig_ic_distribution.pdf"
    fig.savefig(out, bbox_inches='tight', dpi=150)
    plt.close(fig)
    print(f"  Saved: {out}")


def fig2_cr_cp_scatter(rows):
    """CR vs CP scatter plot."""
    cr_vals = []
    cp_vals = []
    t6_cr = []
    t6_cp = []
    colors = []

    for r in rows:
        cr = float(r['CR'])
        cp_raw = r.get('CP', '')
        if cp_raw in ('', 'None', 'null'):
            continue
        cp = float(cp_raw)
        cr_vals.append(cr)
        cp_vals.append(cp)
        if r.get('is_t6') == '1':
            t6_cr.append(cr)
            t6_cp.append(cp)
            colors.append(T6_COLOR)
        else:
            colors.append(COLORS.get(r['ic_type'], '#7f7f7f'))

    fig, ax = plt.subplots(figsize=(7, 6))

    # Background quadrant labels
    ax.axhline(0.5, color='#cccccc', linewidth=0.8, linestyle='--', alpha=0.5)
    ax.axvline(0.5, color='#cccccc', linewidth=0.8, linestyle='--', alpha=0.5)

    ax.text(0.25, 0.95, 'Low CR, High CP\n(IC-1/IC-2: Under-disclosure)',
            ha='center', va='top', fontsize=8, color='#666666',
            transform=ax.transAxes, style='italic')
    ax.text(0.75, 0.95, 'High CR, High CP\n(Accurate)',
            ha='center', va='top', fontsize=8, color='#666666',
            transform=ax.transAxes, style='italic')

    # Main scatter
    ax.scatter(cr_vals, cp_vals, c=colors, s=8, alpha=0.15, edgecolors='none')

    # T6 overlay
    if t6_cr:
        ax.scatter(t6_cr, t6_cp, c=T6_COLOR, s=120, marker='*',
                  edgecolors='#333333', linewidths=0.6, zorder=10, label='T6 malicious')

    ax.set_xlabel('Capability Recall (CR)', fontsize=12)
    ax.set_ylabel('Description Precision (CP)', fontsize=12)
    ax.set_title('CR vs CP: Two-Dimensional Inconsistency Space', fontsize=13, fontweight='bold')
    ax.set_xlim(-0.05, 1.05)
    ax.set_ylim(-0.05, 1.05)
    ax.legend(loc='lower right', fontsize=9)
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)

    plt.tight_layout()
    out = FIG_DIR / "fig_cr_cp_scatter.pdf"
    fig.savefig(out, bbox_inches='tight', dpi=150)
    plt.close(fig)
    print(f"  Saved: {out}")


def fig3_ic_temporal(temporal_rows):
    """Temporal IC label distribution."""
    label_counts = Counter(r.get('ic_temporal_label', '') for r in temporal_rows)
    t6_counts = Counter(r.get('ic_temporal_label', '') for r in temporal_rows if r.get('is_t6') == '1')
    N = len(temporal_rows)

    # Sort by count
    sorted_labels = sorted(label_counts.items(), key=lambda x: -x[1])
    labels = [lbl for lbl, _ in sorted_labels]
    counts = [cnt for _, cnt in sorted_labels]
    t6_cnts = [t6_counts.get(lbl, 0) for lbl in labels]

    temporal_colors = {
        'T-Benign': '#2ca02c',
        'T-Silent-Expand': '#d62728',
        'T-Silent-Cred': '#ff7f0e',
        'T-DocLag': '#9467bd',
        'T-Silent-Minor': '#8c564b',
        'T-Silent-Major': '#e377c2',
        'T-CovertAgent': '#17becf',
        'T6-Malicious': '#FFD700',
        'T-Other': '#7f7f7f',
    }

    fig, ax = plt.subplots(figsize=(9, 4))
    y_pos = np.arange(len(labels))
    bar_colors = [temporal_colors.get(lbl, '#7f7f7f') for lbl in labels]

    bars = ax.barh(y_pos, counts, color=bar_colors, edgecolor='white', linewidth=0.5, alpha=0.85)
    ax.set_yticks(y_pos)
    ax.set_yticklabels(labels, fontsize=9)
    ax.set_xlabel('Number of Version Transitions', fontsize=11)
    ax.set_title(f'Temporal IC Classification (M={N:,} pairs)', fontsize=13, fontweight='bold')
    ax.invert_yaxis()

    for bar, cnt, t6 in zip(bars, counts, t6_cnts):
        pct = cnt / N * 100
        label = f'{cnt:,} ({pct:.1f}%)'
        ax.text(bar.get_width() + N * 0.005, bar.get_y() + bar.get_height()/2,
                label, va='center', fontsize=8)
        if t6 > 0:
            ax.text(bar.get_width() * 0.5, bar.get_y() + bar.get_height()/2,
                   f'T6={t6}', va='center', ha='center', fontsize=7,
                   color='white', fontweight='bold')

    ax.set_xlim(0, max(counts) * 1.3)
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)

    plt.tight_layout()
    out = FIG_DIR / "fig_ic_temporal.pdf"
    fig.savefig(out, bbox_inches='tight', dpi=150)
    plt.close(fig)
    print(f"  Saved: {out}")


def fig4_ud_fc_families(rows):
    """UD vs FC per capability family comparison."""
    ud_counter = Counter()
    fc_counter = Counter()

    for r in rows:
        for cap in r.get('ud_caps', '').split('|'):
            if cap.strip():
                ud_counter[cap.strip()] += 1
        for cap in r.get('fc_caps', '').split('|'):
            if cap.strip():
                fc_counter[cap.strip()] += 1

    all_fams = sorted(set(list(ud_counter.keys()) + list(fc_counter.keys())),
                      key=lambda f: -(ud_counter.get(f, 0) + fc_counter.get(f, 0)))
    top_fams = all_fams[:12]

    fig, ax = plt.subplots(figsize=(10, 5))
    x = np.arange(len(top_fams))
    width = 0.35

    ud_vals = [ud_counter.get(f, 0) for f in top_fams]
    fc_vals = [fc_counter.get(f, 0) for f in top_fams]

    bars1 = ax.bar(x - width/2, ud_vals, width, label='UD (Hidden)', color='#d62728', alpha=0.8)
    bars2 = ax.bar(x + width/2, fc_vals, width, label='FC (False Claim)', color='#9467bd', alpha=0.8)

    ax.set_xticks(x)
    ax.set_xticklabels([f.replace('_', '\n') for f in top_fams], fontsize=8, rotation=0)
    ax.set_ylabel('Number of Skills', fontsize=11)
    ax.set_title('UD (Hidden) vs FC (False-Claimed) per Capability Family', fontsize=13, fontweight='bold')
    ax.legend(fontsize=10)
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)

    # Add value labels on top bars
    for bar in bars1:
        if bar.get_height() > 0:
            ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 50,
                   f'{int(bar.get_height()):,}', ha='center', va='bottom', fontsize=6)

    plt.tight_layout()
    out = FIG_DIR / "fig_ud_fc_families.pdf"
    fig.savefig(out, bbox_inches='tight', dpi=150)
    plt.close(fig)
    print(f"  Saved: {out}")


ENSEMBLE_JSON = HERE / "results" / "ensemble_merged.json"


def fig5_ensemble_agreement(ensemble_path):
    """Grouped bar: per-model IC distribution + majority-vote ensemble."""
    import json
    with open(ensemble_path) as f:
        data = json.load(f)

    model_names = ["Qwen3-8B", "Llama-3.1-8B", "Mistral-7B-v0.3"]
    ic_types = ["IC-0", "IC-1", "IC-2", "IC-3", "IC-4"]
    colors_ic = ["#2ca02c", "#d62728", "#ff7f0e", "#9467bd", "#e377c2"]

    # Per-model IC counts (from per_model_ic field)
    model_counts = [{ic: 0 for ic in ic_types} for _ in range(3)]
    ensemble_counts = {ic: 0 for ic in ic_types}

    for rec in data:
        # Ensemble (majority vote)
        ict = rec.get("ic_type", "IC-0")
        if ict in ensemble_counts:
            ensemble_counts[ict] += 1
        # Per-model
        per_model = rec.get("per_model_ic", [])
        for idx, model_ic in enumerate(per_model[:3]):
            if model_ic in ic_types:
                model_counts[idx][model_ic] += 1

    N = len(data)
    all_names = model_names + ["Ensemble\n(Majority Vote)"]
    all_counts = model_counts + [ensemble_counts]

    fig, ax = plt.subplots(figsize=(11, 5))
    x = np.arange(len(all_names))
    width = 0.15
    offsets = np.linspace(-(len(ic_types)-1)/2, (len(ic_types)-1)/2, len(ic_types))

    for i, (ic, color, offset) in enumerate(zip(ic_types, colors_ic, offsets * width)):
        vals = [cnt[ic] / max(sum(cnt.values()), 1) * 100 for cnt in all_counts]
        bars = ax.bar(x + offset, vals, width * 0.9, label=ic, color=color, alpha=0.85)
        # Add % label on ensemble bar
        ax.text(x[-1] + offset, vals[-1] + 0.5, f"{vals[-1]:.1f}%",
                ha='center', fontsize=6.5, color=color)

    ax.set_xticks(x)
    ax.set_xticklabels(all_names, fontsize=10)
    ax.set_ylabel("IC Type Proportion (%)", fontsize=11)
    ax.set_title(
        f"IC Classification: 3-Model Ensemble Agreement (N={N:,} skills)",
        fontsize=13, fontweight='bold'
    )
    ax.legend(title="IC Type", bbox_to_anchor=(1.01, 1), loc='upper left', fontsize=9)
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)
    # Highlight ensemble column
    ax.axvline(len(model_names) - 0.5, color='#aaaaaa', linestyle='--', linewidth=0.8)

    plt.tight_layout()
    out = FIG_DIR / "fig_ensemble_agreement.pdf"
    fig.savefig(out, bbox_inches='tight', dpi=150)
    plt.close(fig)
    print(f"  Saved: {out}")


def main():
    print("Generating IC framework figures (PDF)...")
    ic_rows = load_ic_data()
    print(f"  Loaded {len(ic_rows):,} IC records")

    fig1_ic_distribution(ic_rows)
    fig2_cr_cp_scatter(ic_rows)
    fig4_ud_fc_families(ic_rows)

    if TEMPORAL_CSV.exists():
        temporal_rows = load_temporal_data()
        print(f"  Loaded {len(temporal_rows):,} temporal records")
        fig3_ic_temporal(temporal_rows)
    else:
        print("  WARNING: ic_temporal.csv not found, skipping temporal figure")

    if ENSEMBLE_JSON.exists():
        print(f"  Loading ensemble results from {ENSEMBLE_JSON}")
        fig5_ensemble_agreement(ENSEMBLE_JSON)
    else:
        print(f"  NOTE: {ENSEMBLE_JSON} not yet available — skipping ensemble figure")
        print("        Run 23_merge_ensemble.py after Llama+Mistral extraction completes.")

    print("\nDone. All figures saved to paper/figures/")


if __name__ == '__main__':
    main()

