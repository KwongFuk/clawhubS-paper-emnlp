"""Fig 1: TrustDrift Pipeline – top-venue style (double-column, horizontal).

Design principles from EMNLP/ACL/NeurIPS framework figures:
  - Horizontal left-to-right flow with a single main spine
  - 3 color families max (blue=data, purple=model, amber=validation)
  - Compact: 7.2 × 2.2 in
  - 8-9pt serif fonts, thin rounded boxes
  - Section numbers as small superscript labels
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from paper_plot_style import *
import matplotlib.patches as mpatches

fig, ax = plt.subplots(figsize=(W2, 2.2))
ax.set_xlim(0, 14.4)
ax.set_ylim(-0.3, 4.2)
ax.axis('off')

# ── Colors ──────────────────────────────────────────────
BG_DATA  = '#DBEAFE';  BD_DATA  = '#2563EB'
BG_MODEL = '#F3E8FF';  BD_MODEL = '#7C3AED'
BG_VALID = '#FFFBEB';  BD_VALID = '#D97706'
BG_EXP   = '#ECFDF5';  BD_EXP   = '#059669'
BG_FIND  = '#FEF2F2';  BD_FIND  = '#DC2626'

# ── Helpers ─────────────────────────────────────────────
def rbox(x, y, w, h, title, fc, ec, sub=None, fs=7.5):
    r = mpatches.FancyBboxPatch(
        (x, y), w, h, boxstyle='round,pad=0.1',
        facecolor=fc, edgecolor=ec, linewidth=0.8, zorder=2)
    ax.add_patch(r)
    cy = y + h/2
    if sub:
        ax.text(x+w/2, cy+0.15, title, ha='center', va='center',
                fontsize=fs, fontweight='bold', zorder=3)
        ax.text(x+w/2, cy-0.2, sub, ha='center', va='center',
                fontsize=6, color='#555', style='italic', zorder=3)
    else:
        ax.text(x+w/2, cy, title, ha='center', va='center',
                fontsize=fs, fontweight='bold', zorder=3)
    return x+w/2, cy

def arr(x1, y1, x2, y2, c='#4B5563'):
    ax.annotate('', xy=(x2, y2), xytext=(x1, y1),
        arrowprops=dict(arrowstyle='->', color=c, lw=0.9), zorder=1)

def stag(x, y, t):
    ax.text(x, y, t, ha='center', va='bottom',
            fontsize=6.5, color='#9CA3AF', fontweight='bold')

# ── Y positions ─────────────────────────────────────────
YM = 1.5   # main row
YT = 2.9   # top row (BM25, IC bubbles)
YB = 0.0   # bottom row

# ── Section tags (above all content) ────────────────────
stag(1.0, 4.0, '§3')
stag(3.5, 4.0, '§3')
stag(6.1, 4.0, '§4')
stag(10.2, 4.0, '§5–6')

# ── Stage 1: Data ───────────────────────────────────────
cx1, cy1 = rbox(0.1, YM, 1.7, 0.9,
    'ClawHub', BG_DATA, BD_DATA, sub='18,441 listings')

# ── Stage 2: Auditor ────────────────────────────────────
cx2, cy2 = rbox(2.4, YM, 2.1, 0.9,
    'LLM Auditor', BG_MODEL, BD_MODEL, sub='Qwen2.5-7B')

# IC bubbles above auditor
ic_data = [('IC-0','#D1FAE5','#059669','95%'),
           ('IC-1s','#FEF3C7','#D97706','3.5%'),
           ('IC-1q','#E0E7FF','#4F46E5','1.1%'),
           ('IC-2','#FEE2E2','#DC2626','0.4%')]
for i, (nm, fc, ec, pct) in enumerate(ic_data):
    bx = 2.45 + i*0.53
    r = mpatches.FancyBboxPatch(
        (bx, YT), 0.46, 0.5, boxstyle='round,pad=0.04',
        facecolor=fc, edgecolor=ec, linewidth=0.6, zorder=2)
    ax.add_patch(r)
    ax.text(bx+0.23, YT+0.32, nm, ha='center', va='center',
            fontsize=5.5, fontweight='bold', color=ec, zorder=3)
    ax.text(bx+0.23, YT+0.12, pct, ha='center', va='center',
            fontsize=5, color='#555', zorder=3)

arr(cx2, cy2+0.45, cx2, YT, BD_MODEL)

# ── Stage 3: Validation ────────────────────────────────
cx3a, cy3a = rbox(5.2, YT-0.1, 1.5, 0.7,
    'Expert Val.', BG_VALID, BD_VALID, sub='n=150, κ=0.71', fs=7)

cx3b, cy3b = rbox(5.2, YB, 1.5, 0.7,
    'Impl. Val.', BG_VALID, BD_VALID, sub='n=150, P=0.90', fs=7)

# ── Stage 4: Experiments ───────────────────────────────
cx4a, cy4a = rbox(7.6, YT-0.1, 2.2, 0.7,
    'BM25 Retrieval', BG_EXP, BD_EXP, sub='197 skills, 5K index', fs=7)

cx4b, cy4b = rbox(7.6, YM+0.05, 2.2, 0.7,
    'Routing Bench.', BG_EXP, BD_EXP, sub='97 skills, 8,779 dec.', fs=7)

cx4c, cy4c = rbox(7.6, YB, 2.2, 0.7,
    'Selection Bias', BG_EXP, BD_EXP, sub='8 clusters, 36 skills', fs=7)

# ── Stage 5: Key Findings ──────────────────────────────
rbox(10.4, YT-0.1, 1.5, 0.7,
    '10× gap', BG_FIND, BD_FIND, fs=7.5)
rbox(10.4, YM+0.05, 1.5, 0.7,
    '+0.9pp agg', '#ECFDF5', '#059669', fs=7.5)
rbox(10.4, YB, 1.5, 0.7,
    '+8.1pp blind', BG_DATA, BD_DATA, fs=7.5)

# ── Arrows ──────────────────────────────────────────────
arr(1.8, cy1, 2.4, cy2)                   # data → auditor
arr(4.5, cy2+0.2, 5.2, cy3a)             # auditor → expert
arr(4.5, cy2-0.2, 5.2, cy3b+0.45)        # auditor → impl

arr(6.7, cy3a, 7.6, cy4a)                # expert → bm25
arr(6.7, cy3a-0.2, 7.6, cy4b+0.2)        # expert → routing
arr(6.7, cy3b, 7.6, cy4c+0.2)            # impl → selection

arr(9.8, cy4a, 10.4, cy4a)               # bm25 → finding
arr(9.8, cy4b, 10.4, cy4b)               # routing → finding
arr(9.8, cy4c, 10.4, cy4c)               # selection → finding

# ── Summary brace ──────────────────────────────────────
bx = 12.1
yt, yb = cy4a+0.3, cy4c-0.3
ax.plot([bx, bx+0.12, bx+0.12, bx],
        [yt, yt, yb, yb],
        color='#9CA3AF', lw=0.8, zorder=1, clip_on=False)
ax.text(bx+0.3, (yt+yb)/2, 'Descriptions\nshape agent\nbehavior',
        ha='left', va='center', fontsize=6.5, color='#374151',
        fontweight='bold', linespacing=1.3)

plt.tight_layout()
save_fig(fig, 'pipeline_v3')
plt.close()
print('Done: pipeline_v3.pdf')
