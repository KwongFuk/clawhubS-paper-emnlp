"""
Unified style for ALL TrustDrift paper figures.
Every gen_fig*.py imports this and ONLY this.

Style contract:
  - Single-column width: figsize width = 3.5 in
  - Double-column width: figsize width = 7.2 in
  - Heights: 2.4–3.0 for data charts, taller for pipeline
  - Fonts: 9pt labels, 8pt ticks, 8pt legend
  - Bars: alpha=0.88, edgecolor='white', linewidth=0.4
  - No top/right spines
  - All serif fonts (Times)
"""
import matplotlib
import matplotlib.pyplot as plt
import os

# ── Global rcParams ─────────────────────────────────────
matplotlib.rcParams.update({
    'font.size': 9,
    'font.family': 'serif',
    'font.serif': ['Times New Roman', 'Times', 'DejaVu Serif'],
    'axes.labelsize': 9,
    'axes.titlesize': 10,
    'xtick.labelsize': 8,
    'ytick.labelsize': 8,
    'legend.fontsize': 8,
    'figure.dpi': 300,
    'savefig.dpi': 300,
    'savefig.bbox': 'tight',
    'savefig.pad_inches': 0.04,
    'axes.grid': False,
    'axes.spines.top': False,
    'axes.spines.right': False,
    'axes.linewidth': 0.6,
    'xtick.major.width': 0.5,
    'ytick.major.width': 0.5,
    'text.usetex': False,
    'mathtext.fontset': 'stix',
})

# ── Unified Color Palette ───────────────────────────────
# 4 semantic roles (bar chart legend order)
C_PRIMARY   = '#2563EB'   # Blue
C_SECONDARY = '#EA580C'   # Orange
C_TERTIARY  = '#7C3AED'   # Purple
C_POSITIVE  = '#059669'   # Green

# Supporting roles
C_NEGATIVE  = '#DC2626'   # Red
C_NEUTRAL   = '#6B7280'   # Gray
C_ACCENT    = '#D97706'   # Amber

# Light fills (pipeline boxes)
C_PRIMARY_LIGHT   = '#DBEAFE'
C_SECONDARY_LIGHT = '#FFF7ED'
C_TERTIARY_LIGHT  = '#F3E8FF'
C_POSITIVE_LIGHT  = '#ECFDF5'
C_NEGATIVE_LIGHT  = '#FEF2F2'
C_NEUTRAL_LIGHT   = '#F3F4F6'
C_ACCENT_LIGHT    = '#FFFBEB'

# Legacy dict
COLORS = {
    'blue': C_PRIMARY, 'orange': C_SECONDARY, 'purple': C_TERTIARY,
    'green': C_POSITIVE, 'red': C_NEGATIVE, 'gray': C_NEUTRAL,
    'amber': C_ACCENT,
}

# ── Standard sizes ──────────────────────────────────────
W1 = 3.5   # single-column width
W2 = 7.2   # double-column width
BAR_KW = dict(edgecolor='white', linewidth=0.4, alpha=0.88)

# ── Output ──────────────────────────────────────────────
FIG_DIR = os.path.dirname(os.path.abspath(__file__))

def save_fig(fig, name):
    for fmt in ('pdf', 'png'):
        path = os.path.join(FIG_DIR, f'{name}.{fmt}')
        fig.savefig(path, dpi=300 if fmt == 'png' else None)
        print(f'Saved: {path}')
