"""Fig 4: Routing Repair — top-venue style (single-column).

Top-venue conventions:
  - Grouped bar with error-bar-like delta indicators
  - Horizontal reference line for baseline
  - Hatching for control conditions
  - Clean annotation box
"""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from paper_plot_style import *
import numpy as np
from collections import defaultdict

# ── Load data ────────────────────────────────────────────
DATA_PATH = os.path.join(os.path.dirname(__file__),
                         '..', '..', 'skill-X', 'experiments',
                         'routing_repair', 'results', 'routing_results.jsonl')
with open(DATA_PATH) as f:
    results = [json.loads(line) for line in f if line.strip()]

ROUTER_MAP = {
    'openclaw_default': 'OpenClaw',
    'gpt54_mini': 'GPT-5.4-mini',
    'gemini_flash': 'Gemini 2.5 Flash',
}
CONDITION_ORDER = ['original', 'minimal_repair', 'length_control', 'structured_repair']
COND_SHORT = ['Original', 'Disclosed', 'Length Ctl', 'Structured']

counts = defaultdict(lambda: {'correct': 0, 'total': 0})
for r in results:
    if not r.get('expected_target', True):
        continue
    router = r.get('router', '')
    condition = r.get('condition', '')
    if router not in ROUTER_MAP or condition not in CONDITION_ORDER:
        continue
    key = (ROUTER_MAP[router], condition)
    counts[key]['total'] += 1
    if r.get('correct', False):
        counts[key]['correct'] += 1

routers = ['OpenClaw', 'GPT-5.4-mini', 'Gemini 2.5 Flash']
colors = [C_PRIMARY, C_SECONDARY, C_TERTIARY]
hatches = ['', '', '']

data = {}
for rt in routers:
    accs = []
    for cond in CONDITION_ORDER:
        c = counts[(rt, cond)]
        acc = 100.0 * c['correct'] / c['total'] if c['total'] > 0 else 0
        accs.append(round(acc, 1))
    data[rt] = accs
    print(f'  {rt}: {accs}')

lifts = [data[rt][1] - data[rt][0] for rt in routers]
mean_lift = np.mean(lifts)

# ── Plot ─────────────────────────────────────────────────
x = np.arange(len(COND_SHORT))
width = 0.23
n = len(routers)

fig, ax = plt.subplots(figsize=(W1 + 1.0, 2.5))

for i, (rt, vals) in enumerate(data.items()):
    offset = (i - n/2 + 0.5) * width
    bars = ax.bar(x + offset, vals, width, label=rt,
                  color=colors[i], **BAR_KW)

ax.set_ylabel('Routing Accuracy (%)')
ax.set_xticks(x)
ax.set_xticklabels(COND_SHORT, fontsize=8)
ax.set_ylim(72, 87)
ax.set_yticks([72, 75, 78, 81, 84, 87])

# Baseline reference
ax.axhline(y=82.0, color=C_NEUTRAL, ls=':', lw=0.7, alpha=0.5)
ax.text(3.55, 82.2, 'Avg baseline', fontsize=5.5, color=C_NEUTRAL, ha='right')

# Lift annotation — clean box
ax.annotate(
    f'Mean lift: {mean_lift:+.1f}pp\n(name-blind: +8.1pp)',
    xy=(1, data['OpenClaw'][1]),
    xytext=(2.2, 74.5),
    fontsize=6.5, color='#374151', ha='center',
    bbox=dict(boxstyle='round,pad=0.4', facecolor='#FEFCE8',
              edgecolor='#CA8A04', linewidth=0.6),
    arrowprops=dict(arrowstyle='->', color='#CA8A04', lw=0.8,
                    connectionstyle='arc3,rad=0.2'))

ax.legend(loc='upper right', frameon=True, fancybox=False,
          edgecolor=C_NEUTRAL, framealpha=0.9, fontsize=6.5, ncol=1)

ax.yaxis.grid(True, alpha=0.12, lw=0.5)
ax.set_axisbelow(True)

plt.tight_layout()
save_fig(fig, 'routing_repair')
plt.close()
print('Done: routing_repair.pdf')
