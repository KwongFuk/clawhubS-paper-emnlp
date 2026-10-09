"""Fig 3: Selection Concentration — top-venue style (single-column).

Top-venue conventions:
  - Lollipop chart (dot + stem) instead of bar for paired comparison
  - Sorted by delta_skill descending
  - Value labels on dots
  - Clean horizontal layout
"""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from paper_plot_style import *
import numpy as np

# ── Load data ────────────────────────────────────────────
DATA_PATH = os.path.join(os.path.dirname(__file__),
                         '..', '..', 'skill-X', 'experiments',
                         'bias_benchmark', 'results', 'name_randomization',
                         'all_clusters_aggregate.json')
with open(DATA_PATH) as f:
    agg = json.load(f)

clusters = agg['clusters']
NAME_MAP = {
    'github_management': 'GitHub Mgmt',
    'image_generation': 'Image Gen',
    'video_generation': 'Video Gen',
    'memory_system': 'Memory Sys',
    'speech_to_text': 'STT',
    'twitter_x': 'Twitter/X',
    'text_to_speech': 'TTS',
    'web_search': 'Web Search',
}
labels = [NAME_MAP.get(c['cluster'], c['cluster'].replace('_', ' ').title())
          for c in clusters]
ds = [round(c['delta_api'], 3) for c in clusters]
dp = [round(c['delta_pos'], 3) for c in clusters]

# Sort descending
order = np.argsort(ds)
labels = [labels[i] for i in order]
ds = [ds[i] for i in order]
dp = [dp[i] for i in order]

for l, s, p in zip(labels, ds, dp):
    print(f'  {l}: δ_skill={s}, δ_pos={p}')

# ── Horizontal lollipop ──────────────────────────────────
fig, ax = plt.subplots(figsize=(W1 + 0.8, 2.6))

y = np.arange(len(labels))

# Stems
for i in range(len(labels)):
    ax.plot([dp[i], ds[i]], [y[i], y[i]], color=C_NEUTRAL, lw=1.2, zorder=1)

# Dots
ax.scatter(ds, y, color=C_PRIMARY, s=55, zorder=3,
           label=r'$\delta_{\mathrm{skill}}$ (content)', edgecolors='white', linewidth=0.5)
ax.scatter(dp, y, color=C_NEUTRAL, s=35, zorder=3, marker='D',
           label=r'$\delta_{\mathrm{pos}}$ (position)', edgecolors='white', linewidth=0.5)

# Value labels on skill dots
for i, v in enumerate(ds):
    ax.text(v + 0.03, y[i], f'{v:.2f}', va='center', ha='left',
            fontsize=6.5, color=C_PRIMARY, fontweight='bold')

ax.set_yticks(y)
ax.set_yticklabels(labels)
ax.set_xlabel('Concentration Score')
ax.set_xlim(-0.05, 1.0)

# Mean lines
ms, mp = np.mean(ds), np.mean(dp)
ax.axvline(ms, color=C_PRIMARY, ls=':', lw=0.8, alpha=0.5)
ax.axvline(mp, color=C_NEUTRAL, ls=':', lw=0.8, alpha=0.5)
ax.text(ms+0.02, len(labels)-0.3, f'μ={ms:.2f}',
        fontsize=6, color=C_PRIMARY, ha='left')

ax.legend(loc='lower right', frameon=True, fancybox=False,
          edgecolor=C_NEUTRAL, framealpha=0.9, fontsize=7)

ax.xaxis.grid(True, alpha=0.12, lw=0.5)
ax.set_axisbelow(True)

plt.tight_layout()
save_fig(fig, 'selection_concentration')
plt.close()
print('Done: selection_concentration.pdf')
