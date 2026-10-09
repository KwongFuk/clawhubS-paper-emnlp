"""Fig 2: Abstraction Gap — top-venue style (single-column).

Top-venue conventions:
  - Hatched comparison bar for B&W printing
  - Inline delta annotation with a clean bracket
  - Minimal ink, maximum data
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from paper_plot_style import *
import numpy as np

fig, ax = plt.subplots(figsize=(W1, 2.2))

methods = ['Regex\nDetector', 'LLM\nAuditor']
flagged = [75.8, 5.0]

bars = ax.bar(methods, flagged, width=0.45,
              color=[C_NEUTRAL_LIGHT, C_PRIMARY],
              edgecolor=[C_NEUTRAL, C_PRIMARY],
              linewidth=1.0, zorder=3)
# Hatch for regex (B&W friendly)
bars[0].set_hatch('///')
bars[0].set_edgecolor(C_NEUTRAL)

ax.set_ylabel('Flagged as Mismatched (%)')
ax.set_ylim(0, 92)
ax.set_yticks([0, 20, 40, 60, 80])

# Value labels
for bar, val in zip(bars, flagged):
    ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 1.5,
            f'{val}%', ha='center', va='bottom', fontsize=9, fontweight='bold')

# Clean bracket annotation
bx1 = bars[0].get_x() + bars[0].get_width()
bx2 = bars[1].get_x()
mid = (bx1 + bx2) / 2
ax.plot([bx1+0.05, bx1+0.05, bx2-0.05, bx2-0.05],
        [75.8, 82, 82, 5.0], color=C_NEGATIVE, lw=1.0, zorder=4)
ax.text(mid, 84, r'$\mathbf{15\times}$ reduction',
        ha='center', va='bottom', fontsize=8, color=C_NEGATIVE,
        fontweight='bold')

# Subtle grid
ax.yaxis.grid(True, alpha=0.15, lw=0.5)
ax.set_axisbelow(True)

plt.tight_layout()
save_fig(fig, 'abstraction_gap_v2')
plt.close()
print('Done: abstraction_gap_v2.pdf')
