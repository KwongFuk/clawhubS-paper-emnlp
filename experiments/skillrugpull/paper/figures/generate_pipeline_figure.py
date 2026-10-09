"""
TrustDrift Framework Figure — Academic USENIX Style
Matches the paper's matplotlib figures: white bg, thin lines, muted palette,
professional monospace-free fonts, no dark blocks.
"""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch
from matplotlib.lines import Line2D
import numpy as np

# ── Palette: muted academic (matches seaborn paper style) ─────────────────────
# Match the warm-neutral palette seen in the paper's existing figures
BG      = "#ffffff"
BORDER  = "#2c2c2c"       # dark border for boxes
ARROW   = "#555555"

# Stage colors — light fill, dark border (academic standard)
C1_FILL = "#eef2f8"       # corpus: pale blue-grey
C2_FILL = "#e8f0e8"       # LSCPS: pale green
C3_FILL = "#fdf3e7"       # DM: pale amber
C4_FILL = "#fdecea"       # IC: pale red
C5_FILL = "#f0edf8"       # T6: pale purple
C6_FILL = "#f5f5f5"       # deploy: light grey

# Accent colors (muted)
A_RED   = "#c0392b"
A_ORG   = "#d4690a"
A_YEL   = "#c09000"
A_BLU   = "#2563a8"
A_GRN   = "#1a7a4a"
A_PUR   = "#7b4ea8"
A_GRY   = "#888888"

# ── Figure: two-row layout ─────────────────────────────────────────────────────
# Row A: pipeline (top),  Row B: DM detail + results (bottom)
fig = plt.figure(figsize=(16, 8.5))
fig.patch.set_facecolor(BG)

# Use gridspec for fine control
from matplotlib.gridspec import GridSpec
gs = GridSpec(2, 5, figure=fig,
              left=0.02, right=0.98,
              top=0.97, bottom=0.03,
              wspace=0.04, hspace=0.18)

# ══════════════════════════════════════════════════════════════════════════════
# Helper functions
# ══════════════════════════════════════════════════════════════════════════════
def styled_box(ax, x, y, w, h, fill, label=None, label_size=10,
               border=BORDER, lw=1.2, radius=0.04, hatch=None):
    box = FancyBboxPatch((x, y), w, h,
                          boxstyle=f"round,pad={radius}",
                          linewidth=lw, edgecolor=border,
                          facecolor=fill, hatch=hatch, zorder=2)
    ax.add_patch(box)
    if label:
        ax.text(x+w/2, y+h-0.04, label, ha="center", va="top",
                fontsize=label_size, fontweight="bold", color=BORDER,
                zorder=3, fontfamily="sans-serif")
    return box

def txt(ax, x, y, s, size=8.5, color="#222222", weight="normal",
        ha="left", va="center", style="normal", alpha=1.0):
    return ax.text(x, y, s, fontsize=size, color=color, fontweight=weight,
                   ha=ha, va=va, style=style, alpha=alpha, zorder=4,
                   fontfamily="sans-serif")

def horz_arrow(ax, x0, y, x1, lw=1.5, color=ARROW, label=None):
    ax.annotate("", xy=(x1, y), xytext=(x0, y),
                arrowprops=dict(arrowstyle="-|>", color=color,
                                lw=lw, mutation_scale=12),
                zorder=5)
    if label:
        ax.text((x0+x1)/2, y+0.015, label, ha="center", va="bottom",
                fontsize=7, color=color, style="italic")

def vert_arrow(ax, x, y0, y1, lw=1.2, color=ARROW):
    ax.annotate("", xy=(x, y1), xytext=(x, y0),
                arrowprops=dict(arrowstyle="-|>", color=color,
                                lw=lw, mutation_scale=11),
                zorder=5)

# ══════════════════════════════════════════════════════════════════════════════
# TOP ROW — Pipeline Overview (spans all 5 cols)
# ══════════════════════════════════════════════════════════════════════════════
ax_top = fig.add_subplot(gs[0, :])
ax_top.set_xlim(0, 16)
ax_top.set_ylim(0, 3.0)
ax_top.axis("off")
ax_top.set_facecolor(BG)

# -- Stage boxes (y=0.1 to 2.85) --
STAGES = [
    (0.10, 2.95, C1_FILL, "① Skill Corpus"),
    (3.35, 2.95, C2_FILL, "② Static Extraction (LSCPS)"),
    (6.60, 2.95, C3_FILL, "③ Difference Matrix DM(s)"),
    (9.85, 2.95, C4_FILL, "④ IC Classification"),
    (13.10, 2.95, C5_FILL, "⑤ Screening Output"),
]
W_BOX = 2.95
for i, (x, h_to_top, fill, label) in enumerate(STAGES):
    styled_box(ax_top, x, 0.1, W_BOX, 2.75, fill, label=label, label_size=9.5, lw=1.3)

# Content: Stage 1 — Corpus
ax_top.text(1.57, 2.35, "ClawHub", ha="center", fontsize=9, color=A_BLU, fontweight="bold")
ax_top.text(1.57, 2.10, "18,443 skills", ha="center", fontsize=8.5, color="#333")
ax_top.text(1.57, 1.83, "avg. 17.2 files/skill", ha="center", fontsize=7.8, color="#666", style="italic")
ax_top.plot([0.3, 2.85], [1.72, 1.72], color="#cccccc", lw=0.8, ls="--")
ax_top.text(1.57, 1.55, "skills.sh", ha="center", fontsize=9, color=A_GRN, fontweight="bold")
ax_top.text(1.57, 1.30, "116,208 skills", ha="center", fontsize=8.5, color="#333")
ax_top.text(1.57, 1.03, "(cross-platform validation)", ha="center", fontsize=7.5, color="#666", style="italic")
ax_top.plot([0.3, 2.85], [0.92, 0.92], color="#cccccc", lw=0.8, ls="--")
ax_top.text(1.57, 0.72, "Version history", ha="center", fontsize=8.5, color="#444")
ax_top.text(1.57, 0.50, "24,243 transition pairs", ha="center", fontsize=8.5, color="#333", fontweight="bold")
ax_top.text(1.57, 0.27, "(ClawHub, temporal analysis)", ha="center", fontsize=7.5, color="#666", style="italic")

# Arrow 1→2
horz_arrow(ax_top, 3.08, 1.48, 3.37, lw=1.8, label="SKILL.md\n+ code files")

# Content: Stage 2 — LSCPS
lscps_items = [
    ("L", "Local shell exec",     "subprocess, os.system, eval",  A_RED),
    ("S", "Secret/Credential",    "API keys, tokens, passwords",   A_ORG),
    ("C", "Network write (C&C)",  "requests.post, webhooks, SMTP", A_BLU),
    ("P", "Persistent write",     "open(w), shutil.rmtree",        A_GRN),
    ("S", "Systemic/Autonomous",  "scheduling, multi-step loops",  A_PUR),
]
y_start = 2.35
for i, (letter, family, detail, col) in enumerate(lscps_items):
    yy = y_start - i*0.44
    ax_top.text(3.52, yy, f"[{letter}]", fontsize=9, color=col, fontweight="bold",
                va="center", fontfamily="monospace")
    ax_top.text(3.80, yy, family, fontsize=8.5, color="#222", va="center", fontweight="semibold")
    ax_top.text(3.80, yy-0.17, detail, fontsize=7.2, color="#666", va="center", style="italic")
ax_top.text(4.82, 0.33, "300+ regex patterns · 14 sub-families", ha="center", 
            fontsize=7.8, color="#555", style="italic")
ax_top.text(4.82, 0.15, r"Output: $\mathcal{C}_\mathrm{act}$ (actual capability set)",
            ha="center", fontsize=8.0, color=A_GRN, fontweight="bold")

# Arrow 2→3
horz_arrow(ax_top, 6.33, 1.48, 6.62, lw=1.8, label=r"$\mathcal{C}_{act}$")

# Content: Stage 3 — DM matrix
# Draw 2×2 table in the box
mx, my = 6.82, 0.58
cw, ch = 1.15, 0.75
# Headers
ax_top.text(mx+cw/2,   my+2*ch+0.14, "Disclosed", ha="center", fontsize=8, color=A_GRN, fontweight="bold")
ax_top.text(mx+3*cw/2, my+2*ch+0.14, "Hidden",    ha="center", fontsize=8, color=A_RED,  fontweight="bold")
ax_top.text(mx-0.08, my+3*ch/2, r"$\in\mathcal{C}_{act}$",   ha="right", fontsize=8, va="center", color="#444")
ax_top.text(mx-0.08, my+ch/2,   r"$\notin\mathcal{C}_{act}$", ha="right", fontsize=8, va="center", color="#444")

# Cells
cell_data = [
    (mx,    my+ch, "#e8f4e8", "AD", "Aligned Disclosure",  "#1a5a2a"),
    (mx+cw, my+ch, "#fde8e8", "UD", "Undisclosed (Hidden)","#8b1a1a"),
    (mx,    my,    "#fff0e8", "FC", "False Claim",          "#7a3000"),
    (mx+cw, my,    "#f5f5f5", "TN", "True Negative",        "#555555"),
]
for cx, cy, fg, abbr, full, tc in cell_data:
    r = FancyBboxPatch((cx, cy), cw, ch, boxstyle="round,pad=0.02",
                        lw=1.0, edgecolor=BORDER, facecolor=fg, zorder=3)
    ax_top.add_patch(r)
    ax_top.text(cx+cw/2, cy+ch*0.65, abbr, ha="center", va="center",
                fontsize=12, fontweight="bold", color=tc, zorder=4)
    ax_top.text(cx+cw/2, cy+ch*0.28, full, ha="center", va="center",
                fontsize=6.8, color=tc, zorder=4)

# Formulas right of matrix
fx = mx + 2*cw + 0.18
ax_top.text(fx, my+2.05*ch, r"$\mathrm{CR}(s)=\frac{\mathrm{AD}}{\mathrm{AD}+\mathrm{UD}}$",
            fontsize=9.5, va="center", color="#333")
ax_top.text(fx, my+1.25*ch, r"$\mathrm{CP}(s)=\frac{\mathrm{AD}}{\mathrm{AD}+\mathrm{FC}}$",
            fontsize=9.5, va="center", color="#333")
ax_top.text(fx, my+0.45*ch, r"$\mathrm{CDI}(s)=\frac{\mathrm{UD}+\mathrm{FC}}{\mathrm{AD}+\mathrm{UD}+\mathrm{FC}}$",
            fontsize=9.0, va="center", color="#333")

# Arrow 3→4
horz_arrow(ax_top, 9.58, 1.48, 9.87, lw=1.8)

# Content: Stage 4 — IC Classification
ic_data = [
    ("IC-1", "Full Omission",    r"UD$>$0, AD$=$0, FC$=$0", "75.8%", A_RED),
    ("IC-2", "Partial Omission", r"UD$>$0, AD$>$0, FC$=$0", "13.6%", A_ORG),
    ("IC-4", "Bidirectional",    r"UD$>$0, FC$>$0",          " 2.9%", A_PUR),
    ("IC-3", "False Claim",      r"FC$>$0, UD$=$0",          " 0.4%", A_YEL),
    ("IC-0", "Accurate",         r"CR$=$CP$=$1",             " 0.4%", A_GRN),
]
y_ic = 2.38
for code, name, cond, pct, col in ic_data:
    ax_top.text(10.02, y_ic, f"• {code}", fontsize=8.5, color=col, fontweight="bold", va="center")
    ax_top.text(10.55, y_ic, f"{name}", fontsize=8.5, va="center", color="#222")
    # percentage badge
    ax_top.text(12.72, y_ic, pct, fontsize=8.5, color=col, fontweight="bold",
                ha="right", va="center")
    ax_top.text(10.55, y_ic-0.18, cond, fontsize=7.0, color="#666", va="center", style="italic")
    y_ic -= 0.50

ax_top.plot([10.02, 12.85], [0.62, 0.62], color="#dddddd", lw=0.8)
ax_top.text(11.44, 0.48, r"$\overline{\mathrm{CR}}=0.042$   $\overline{\mathrm{CDI}}=0.894$",
            ha="center", fontsize=9.0, color="#333", fontweight="bold")
ax_top.text(11.44, 0.25, "N = 18,443 ClawHub skills", ha="center", fontsize=8.0, color="#666", style="italic")

# Arrow 4→5
horz_arrow(ax_top, 13.08, 1.48, 13.12, lw=1.8)

# Content: Stage 5 — Screening Output
# SCS vs DMR table
ax_top.text(14.58, 2.45, "SCS∪DMR Detection", ha="center", fontsize=9,
            fontweight="bold", color=A_RED)
# Small comparison table
rows5 = [
    ("Method",          "TP",  "Recall",   "bold",   "#333"),
    ("Platform scanner","0",   "0%",       "normal", A_RED),
    ("SCS (CES≥6)",     "27",  "79.4%",    "normal", "#444"),
    ("DMR (CR<0.3)",    "26",  "76.5%",    "normal", "#444"),
    ("SCS∪DMR",         "32",  "94.1%",    "bold",   A_RED),
]
y5 = 2.18
for row in rows5:
    label, tp, recall, fw, col = row
    ax_top.text(13.22, y5, label,  fontsize=8.0, color=col, fontweight=fw, va="center")
    ax_top.text(14.55, y5, tp,     fontsize=8.0, color=col, fontweight=fw, va="center", ha="center")
    ax_top.text(15.55, y5, recall, fontsize=8.0, color=col, fontweight=fw, va="center", ha="center")
    ax_top.plot([13.18, 15.92], [y5-0.18, y5-0.18], color="#eeeeee", lw=0.5)
    y5 -= 0.38

ax_top.text(14.58, 0.48, "Wilson 95% CI: [81%, 98%]", ha="center", 
            fontsize=7.8, color="#555", style="italic")
ax_top.text(14.58, 0.27, "vs. Platform scanner: 0%", ha="center",
            fontsize=8.0, color=A_RED, fontweight="bold")

# ══════════════════════════════════════════════════════════════════════════════
# BOTTOM ROW — Three detail panels
# ══════════════════════════════════════════════════════════════════════════════

# ── Panel B1: LSCPS Taxonomy + capability families (col 0-1) ─────────────────
ax_b1 = fig.add_subplot(gs[1, 0:2])
ax_b1.set_xlim(0, 6.2)
ax_b1.set_ylim(0, 3.2)
ax_b1.axis("off")
ax_b1.set_facecolor(BG)

styled_box(ax_b1, 0.08, 0.08, 6.05, 3.05, C2_FILL, lw=1.0)
ax_b1.text(3.1, 3.01, "LSCPS Capability Families · Hidden Distribution",
           ha="center", fontsize=9.5, fontweight="bold", color=BORDER, va="top")

# Horizontal bar chart of hidden capability % (from paper data §4)
families = ["SHELL_EXEC", "FILE_OPS", "CRED_READ", "NET_WRITE", "AUTONOMOUS", "CRED_TRANSMIT"]
pcts     = [  75.8,         58.3,        42.1,         31.6,        18.4,          14.2      ]
colors   = [  A_RED,        A_ORG,       A_YEL,        A_BLU,       A_PUR,         A_GRN     ]

ax_b1.text(0.25, 2.72, "% of skills where family is hidden (UD quadrant):",
           fontsize=7.8, color="#555", style="italic")

bar_y0 = 2.52
bar_h  = 0.27
max_w  = 4.2
for i, (fam, pct, col) in enumerate(zip(families, pcts, colors)):
    yy = bar_y0 - i*(bar_h + 0.08)
    bar_w = pct / 100 * max_w
    # background track
    r0 = FancyBboxPatch((1.62, yy), max_w, bar_h, boxstyle="round,pad=0.01",
                         facecolor="#eeeeee", edgecolor="none", zorder=2)
    ax_b1.add_patch(r0)
    # actual bar
    rb = FancyBboxPatch((1.62, yy), bar_w, bar_h, boxstyle="round,pad=0.01",
                         facecolor=col, edgecolor="none", alpha=0.85, zorder=3)
    ax_b1.add_patch(rb)
    ax_b1.text(1.55, yy+bar_h/2, fam, ha="right", va="center", fontsize=8.0,
               color="#222", fontfamily="monospace")
    ax_b1.text(1.62+bar_w+0.08, yy+bar_h/2, f"{pct}%", ha="left", va="center",
               fontsize=8.0, color=col, fontweight="bold")

# ── Panel B2: Difference Matrix detail (col 2) ────────────────────────────────
ax_b2 = fig.add_subplot(gs[1, 2])
ax_b2.set_xlim(0, 3.4)
ax_b2.set_ylim(0, 3.2)
ax_b2.axis("off")
ax_b2.set_facecolor(BG)

styled_box(ax_b2, 0.08, 0.08, 3.25, 3.05, C3_FILL, lw=1.0)
ax_b2.text(1.7, 3.01, "Difference Matrix DM(s)", ha="center",
           fontsize=9.5, fontweight="bold", color=BORDER, va="top")

# 2x2 table detailed
mx, my2 = 0.55, 0.85
cw2, ch2 = 1.3, 0.9

col_h = ["Disclosed\n(in " + r"$\mathcal{C}_{vis}$)", "Hidden\n(not in " + r"$\mathcal{C}_{vis}$)"]
row_h = [r"Actual ($\in\mathcal{C}_{act}$)", r"Not actual ($\notin\mathcal{C}_{act}$)"]

for j, lbl in enumerate(col_h):
    ax_b2.text(mx+(j+0.5)*cw2, my2+2*ch2+0.12, lbl, ha="center", va="bottom",
               fontsize=7.8, color="#333", fontweight="bold")
for i, lbl in enumerate(row_h):
    ax_b2.text(mx-0.06, my2+(1.5-i)*ch2, lbl, ha="right", va="center",
               fontsize=7.5, color="#333")

cells2 = [
    (0, 1, "#e8f4e8", "AD", A_GRN, "Accurately\nDisclosed",   r"$\mathcal{C}_{act}\cap\mathcal{C}_{vis}$"),
    (1, 1, "#fde8e8", "UD", A_RED, "Undisclosed\n(Hidden)",    r"$\mathcal{C}_{act}\setminus\mathcal{C}_{vis}$"),
    (0, 0, "#fff0e8", "FC", A_ORG, "False Claim\n(Over-decl.)",r"$\mathcal{C}_{vis}\setminus\mathcal{C}_{act}$"),
    (1, 0, "#f5f5f5", "TN", "#666","True\nNegative",           r"neither set"),
]
for ci, ri, fg, abbr, tc, full, formula in cells2:
    cx2 = mx + ci*cw2
    cy2 = my2 + ri*ch2
    r2 = FancyBboxPatch((cx2, cy2), cw2, ch2, boxstyle="round,pad=0.03",
                         lw=1.0, edgecolor=BORDER, facecolor=fg, zorder=3)
    ax_b2.add_patch(r2)
    ax_b2.text(cx2+cw2/2, cy2+ch2*0.72, abbr, ha="center", va="center",
               fontsize=14, fontweight="bold", color=tc, zorder=4)
    ax_b2.text(cx2+cw2/2, cy2+ch2*0.40, full, ha="center", va="center",
               fontsize=7.0, color=tc, zorder=4)
    ax_b2.text(cx2+cw2/2, cy2+ch2*0.12, formula, ha="center", va="center",
               fontsize=6.5, color="#888", zorder=4, style="italic")

ax_b2.text(1.7, 0.70, r"CR $=$ AD/(AD+UD) · CP $=$ AD/(AD+FC)",
           ha="center", fontsize=8.0, color="#333")
ax_b2.text(1.7, 0.47, r"CDI $= ($ UD $+$ FC $)\ /\ ($ AD $+$ UD $+$ FC $)\ \in\ [0,1]$",
           ha="center", fontsize=8.0, color="#333")
ax_b2.text(1.7, 0.24, r"UD quadrant $\Rightarrow$ target of all T6 malicious injections",
           ha="center", fontsize=7.8, color=A_RED, style="italic")

# ── Panel B3: Detection evaluation (col 3-4) ─────────────────────────────────
ax_b3 = fig.add_subplot(gs[1, 3:5])
ax_b3.set_xlim(0, 6.5)
ax_b3.set_ylim(0, 3.2)
ax_b3.axis("off")
ax_b3.set_facecolor(BG)

styled_box(ax_b3, 0.08, 0.08, 6.35, 3.05, C5_FILL, lw=1.0)
ax_b3.text(3.2, 3.01, "T6 Ground-Truth Evaluation  (N = 34 malicious skills, 40 version pairs)",
           ha="center", fontsize=9.5, fontweight="bold", color=BORDER, va="top")

# Left: IC type in T6
ax_b3.text(0.25, 2.72, "IC type distribution in T6:", fontsize=8.5, color="#333", fontweight="bold")
t6_ic = [
    ("IC-1 (Full Omission)",    22, "64.7%", A_RED),
    ("IC-2 (Partial Omission)", 10, "29.4%", A_ORG),
    ("Accurate / TN",            2, " 5.9%", A_GRY),
]
y_t6 = 2.42
for label, n, pct, col in t6_ic:
    bw = n/34 * 2.0
    rb = FancyBboxPatch((0.25, y_t6-0.21), bw, 0.32, boxstyle="round,pad=0.01",
                         facecolor=col, edgecolor="none", alpha=0.80, zorder=3)
    ax_b3.add_patch(rb)
    ax_b3.text(0.3+2.08, y_t6-0.05, f"{label}: {n}/34  ({pct})",
               ha="left", va="center", fontsize=8.0, color=col, fontweight="bold")
    y_t6 -= 0.50

ax_b3.plot([0.2, 6.3], [1.38, 1.38], color="#cccccc", lw=0.8, ls="--")

# Right: recall table
ax_b3.text(0.25, 1.28, "Recall comparison (T6 malicious skills vs. 6,878 negatives):",
           fontsize=8.5, color="#333", fontweight="bold")

tbl_rows = [
    ("Method",           "TP",  "FP",    "Recall", "Precision",  True),
    ("Platform scanner", "0",   "0",     "0.0%",   "—",          False),
    ("SCS (CES ≥ 6)",   "27",  "2,857", "79.4%",  "0.9%",       False),
    ("DMR (CR < 0.3)",  "26",  "4,545", "76.5%",  "0.6%",       False),
    ("SCS∪DMR  ★",     "32",  "5,375", "94.1%",  "0.6%",       True),
]
cols_x = [0.25, 2.35, 3.05, 3.80, 4.70, 5.55]
col_names = ["Method", "TP", "FP", "Recall", "Prec.", "95% CI"]
ci_vals = ["—", "—", "—", "[0.63,0.90]", "[0.60,0.88]", "[0.81,0.98]"]

y_tbl = 1.00
for j, cn in enumerate(col_names):
    ax_b3.text(cols_x[j], y_tbl, cn, fontsize=7.8, fontweight="bold", color="#333",
               ha="left" if j==0 else "center")
ax_b3.plot([0.2, 6.3], [y_tbl-0.16, y_tbl-0.16], color="#888888", lw=1.0)
y_tbl -= 0.34

for i, (method, tp, fp, rec, prec, bold) in enumerate(tbl_rows[1:]):
    fw = "bold" if bold else "normal"
    col_r = A_RED if bold else "#333"
    col_g = A_RED if (bold and rec=="94.1%") else "#333"
    ci = ci_vals[i+1]
    ax_b3.text(cols_x[0], y_tbl, method, fontsize=8.0, fontweight=fw, color=col_r, ha="left")
    for j, val in enumerate([tp, fp, rec, prec, ci]):
        col_v = A_RED if (bold and j>=2) else "#444"
        ax_b3.text(cols_x[j+1], y_tbl, val, fontsize=8.0, fontweight=fw,
                   color=col_v, ha="center")
    ax_b3.plot([0.2, 6.3], [y_tbl-0.16, y_tbl-0.16], color="#eeeeee", lw=0.5)
    y_tbl -= 0.30

ax_b3.text(3.2, 0.22,
           "SCS+DMR recall validated on all confirmed T6 supply-chain injection cases on ClawHub.\n"
           "Platform scanner recall = 0% (all 40 pairs). Precision low by design: prioritization tool, not blocker.",
           ha="center", fontsize=7.5, color="#666", style="italic", va="center")

# ── Final layout touch: connecting text label ─────────────────────────────────
fig.text(0.50, 0.527,
         "TrustDrift: Publish-Time Capability-Description Mismatch Detection,  USENIX Security '26",
         ha="center", fontsize=9.5, color="#444", fontweight="bold",
         fontstyle="italic")

from pathlib import Path
OUTDIR = Path(__file__).resolve().parent
plt.savefig(OUTDIR / "fig_pipeline_overview.pdf",
            bbox_inches="tight", dpi=300, facecolor=BG)
plt.savefig(OUTDIR / "fig_pipeline_overview.png",
            bbox_inches="tight", dpi=200, facecolor=BG)
print("✓ Academic-style framework figure saved")
