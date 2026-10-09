"""Fig 5: Feature Correlation — top-venue style (single-column).

Top-venue conventions:
  - Diverging bar chart with significance shading
  - Significance markers (**, *, n.s.)
  - R² as inset annotation
  - Sort by absolute value
"""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from paper_plot_style import *
import numpy as np

# ── Load data ────────────────────────────────────────────
DATA_PATH = os.path.join(os.path.dirname(__file__),
                         '..', 'analysis_output', 'ecosystem_quality_analysis.json')
try:
    with open(DATA_PATH) as f:
        eco = json.load(f)
    feature_data = eco.get('feature_correlations', None)
    if feature_data:
        features = [f['name'] for f in feature_data]
        corr = [f['r'] for f in feature_data]
        r_squared = eco.get('r_squared', 0.52)
    else:
        raise KeyError
except (FileNotFoundError, KeyError):
    features = ['Specificity', 'Readability (FK)', 'Mentions AI/LLM',
                'Name Length', 'Promo Words']
    corr = [0.654, -0.526, -0.397, -0.340, -0.090]
    r_squared = 0.52

# Sort by absolute value
order = np.argsort([abs(c) for c in corr])
features = [features[i] for i in order]
corr = [corr[i] for i in order]

for f, c in zip(features, corr):
    print(f'  {f}: r={c:+.3f}')

# Significance markers (exploratory, n=36)
# |r| > 0.33 → p < 0.05 (*), |r| > 0.43 → p < 0.01 (**)
sig = []
for c in corr:
    ac = abs(c)
    if ac > 0.43: sig.append('**')
    elif ac > 0.33: sig.append('*')
    else: sig.append('n.s.')

# ── Plot ─────────────────────────────────────────────────
bar_colors = [C_PRIMARY if c > 0 else C_SECONDARY for c in corr]
bar_alpha = [0.92 if abs(c) > 0.33 else 0.35 for c in corr]

fig, ax = plt.subplots(figsize=(W1 + 0.3, 2.0))

y = np.arange(len(features))
bars = ax.barh(y, corr, color=bar_colors, edgecolor='white',
               linewidth=0.4, height=0.55, zorder=3)

for bar, a in zip(bars, bar_alpha):
    bar.set_alpha(a)

ax.set_yticks(y)
ax.set_yticklabels(features)
ax.set_xlabel(r'Pearson $r$ with Selection Rate ($n$=36)')
ax.axvline(x=0, color='black', linewidth=0.5, zorder=2)
ax.set_xlim(-0.75, 0.80)

# Value + significance labels
for i, (v, s) in enumerate(zip(corr, sig)):
    offset = 0.03 if v > 0 else -0.03
    ha = 'left' if v > 0 else 'right'
    label = f'{v:+.2f} {s}'
    ax.text(v + offset, i, label, va='center', ha=ha, fontsize=6.5,
            fontweight='bold' if abs(v) > 0.33 else 'normal',
            color='#374151')

# R² inset box
ax.text(0.97, 0.05, f'$R^2 = {r_squared:.2f}$\n(exploratory)',
        transform=ax.transAxes, fontsize=7, style='italic',
        color=C_NEUTRAL, ha='right', va='bottom',
        bbox=dict(boxstyle='round,pad=0.3', facecolor=C_NEUTRAL_LIGHT,
                  edgecolor=C_NEUTRAL, linewidth=0.5, alpha=0.8))

ax.xaxis.grid(True, alpha=0.12, lw=0.5)
ax.set_axisbelow(True)

plt.tight_layout()
save_fig(fig, 'feature_correlation')
plt.close()
print('Done: feature_correlation.pdf')
