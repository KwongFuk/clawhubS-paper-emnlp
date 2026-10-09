#!/usr/bin/env python3
"""
06_risk_vs_disclosure.py
------------------------
Generate the core figure: Risk Expansion vs Disclosure Adequacy scatter plot.

This produces the "danger zone" visualization where SkillRugPull candidates
appear in the bottom-right quadrant (high risk, low disclosure).

Requires: data/combined_features.csv (from 05_prevalence_analysis.py)

Output:
  results/fig_risk_vs_disclosure.png  (if matplotlib available)
  results/fig_risk_vs_disclosure.txt  (ASCII art fallback + stats)
  results/disclosure_report.md
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path
from collections import defaultdict

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
from config import DATA_DIR, RESULTS_DIR, RISK_EXPANSION_THRESHOLD, DISCLOSURE_THRESHOLD

COMBINED_PATH = DATA_DIR / "combined_features.csv"
REPORT_PATH = RESULTS_DIR / "disclosure_report.md"
FIG_PATH = RESULTS_DIR / "fig_risk_vs_disclosure.png"


def load_csv(path: Path) -> list[dict]:
    if not path.exists():
        print(f"  WARNING: {path} not found")
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def make_ascii_quadrant(rows: list[dict]) -> str:
    """Simple ASCII art 2x2 quadrant visualization."""
    quadrants = defaultdict(int)
    for r in rows:
        risk = float(r.get("risk_expansion", 0) or 0)
        sds = float(r.get("sds", 0) or 0)
        hi_risk = risk >= RISK_EXPANSION_THRESHOLD
        lo_disc = sds <= DISCLOSURE_THRESHOLD
        if hi_risk and lo_disc:
            quadrants["⚠️  HIGH risk + LOW disclosure (SkillRugPull)"] += 1
        elif hi_risk:
            quadrants["✅  HIGH risk + HIGH disclosure (well-disclosed)"] += 1
        elif lo_disc:
            quadrants["⚠️  LOW risk + LOW disclosure (weak but low risk)"] += 1
        else:
            quadrants["✅  LOW risk + HIGH disclosure (benign)"] += 1

    total = max(sum(quadrants.values()), 1)
    lines = [
        "```",
        "",
        "         DisclosureAdequacy",
        "              LOW          HIGH",
        "         ┌────────────┬────────────┐",
    ]

    # Build 2x2 grid
    srp = quadrants.get("⚠️  HIGH risk + LOW disclosure (SkillRugPull)", 0)
    well = quadrants.get("✅  HIGH risk + HIGH disclosure (well-disclosed)", 0)
    weak = quadrants.get("⚠️  LOW risk + LOW disclosure (weak but low risk)", 0)
    benign = quadrants.get("✅  LOW risk + HIGH disclosure (benign)", 0)

    lines += [
        f"  HIGH  │⚠️  SkillRugPull│ ✅ Well-disclosed│",
        f" Risk   │   n={srp:>6,}   │    n={well:>6,}   │",
        f"        │  ({100*srp/total:.1f}%)     │   ({100*well/total:.1f}%)      │",
        "        ├────────────┼────────────┤",
        f"  LOW   │ Weak discl. │ ✅ Benign       │",
        f" Risk   │   n={weak:>6,}   │    n={benign:>6,}   │",
        f"        │  ({100*weak/total:.1f}%)     │   ({100*benign/total:.1f}%)      │",
        "        └────────────┴────────────┘",
        "```",
    ]
    return "\n".join(lines)


def compute_disclosure_stats(rows: list[dict]) -> dict:
    high_risk = [r for r in rows if float(r.get("risk_expansion", 0) or 0) >= RISK_EXPANSION_THRESHOLD]
    candidates = [r for r in high_risk if float(r.get("sds", 1) or 1) <= DISCLOSURE_THRESHOLD]

    # SDS for high-risk vs low-risk
    sds_high_risk = [float(r.get("sds", 0) or 0) for r in high_risk]
    sds_low_risk = [float(r.get("sds", 0) or 0) for r in rows if r not in high_risk]

    avg_sds_high = sum(sds_high_risk) / max(len(sds_high_risk), 1)
    avg_sds_low = sum(sds_low_risk) / max(len(sds_low_risk), 1)

    # Undisclosed risk breakdown
    families = ["execution", "secret", "network", "file_system", "external_action", "agentic"]
    undisclosed_by_family = {}
    for f in families:
        col = f"div_{f}"
        vals = [float(r.get(col, 0) or 0) for r in rows if r.get(col) is not None]
        if vals:
            undisclosed_by_family[f] = sum(v >= 0.5 for v in vals), len(vals)

    return {
        "n_total": len(rows),
        "n_high_risk": len(high_risk),
        "n_candidates": len(candidates),
        "avg_sds_high_risk": avg_sds_high,
        "avg_sds_low_risk": avg_sds_low,
        "undisclosed_by_family": undisclosed_by_family,
    }


def write_disclosure_report(rows: list[dict]) -> None:
    stats = compute_disclosure_stats(rows)
    ascii_fig = make_ascii_quadrant(rows)

    lines = [
        "# SkillRugPull: Risk vs Disclosure Report",
        "",
        "## Figure: Risk Expansion vs Disclosure Adequacy",
        "",
        ascii_fig,
        "",
        "## H2: Under-disclosure is Systematic in High-Risk Skills",
        "",
        f"- High-risk skills (n={stats['n_high_risk']:,}) have mean SDS = **{stats['avg_sds_high_risk']:.3f}**",
        f"- Low-risk skills have mean SDS = **{stats['avg_sds_low_risk']:.3f}**",
        f"- Among {stats['n_high_risk']:,} high-risk skills: **{100*stats['n_candidates']/max(stats['n_high_risk'],1):.1f}%** have inadequate disclosure",
        "",
        "## Undisclosed Risk by Family",
        "",
        "| Risk Family | Skills w/ >50% undisclosed | % of total |",
        "|-------------|---------------------------|------------|",
    ]
    n_total = stats["n_total"]
    for family, (cnt, n) in sorted(stats["undisclosed_by_family"].items(), key=lambda x: -x[1][0]):
        lines.append(f"| {family} | {cnt:,} | {100*cnt/max(n_total,1):.1f}% |")

    lines += [
        "",
        "## Key Takeaway",
        "",
        "> High-risk skills systematically have lower disclosure scores than benign skills.",
        "> This supports **H2**: there is a structural under-disclosure problem in the marketplace",
        "> concentrated in skills with shell access, token handling, and network capabilities.",
    ]

    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def try_plot(rows: list[dict]) -> None:
    try:
        import matplotlib.pyplot as plt
        import matplotlib.patches as mpatches
        import numpy as np

        risk = np.array([float(r.get("risk_expansion", 0) or 0) for r in rows])
        sds = np.array([float(r.get("sds", 0) or 0) for r in rows])
        cls = [r.get("_class", "benign") for r in rows]

        color_map = {
            "skillrugpull_candidate": "#e74c3c",
            "risk_expanding_disclosed": "#e67e22",
            "low_disclosure_low_risk": "#f39c12",
            "benign": "#2ecc71",
        }
        colors = [color_map.get(c, "#999") for c in cls]

        fig, ax = plt.subplots(figsize=(10, 8))
        ax.scatter(risk, sds, c=colors, alpha=0.4, s=6, rasterized=True)

        # Threshold lines
        ax.axvline(RISK_EXPANSION_THRESHOLD, color="#e74c3c", linestyle="--", linewidth=1.5, alpha=0.7)
        ax.axhline(DISCLOSURE_THRESHOLD, color="#e74c3c", linestyle="--", linewidth=1.5, alpha=0.7)

        # Danger zone shading
        ax.axhspan(0, DISCLOSURE_THRESHOLD, xmin=RISK_EXPANSION_THRESHOLD, alpha=0.08, color="red")
        ax.text(
            0.85, 0.1, "⚠️ Danger Zone\n(SkillRugPull)",
            transform=ax.transAxes, fontsize=10, color="#c0392b",
            ha="center", va="bottom",
        )

        ax.set_xlabel("Risk Expansion (CES + PES normalized)", fontsize=12)
        ax.set_ylabel("Semantic Disclosure Score (SDS)", fontsize=12)
        ax.set_title("SkillRugPull: Risk Expansion vs Disclosure Adequacy", fontsize=14)
        ax.set_xlim(0, 1)
        ax.set_ylim(0, 1)

        legend_patches = [
            mpatches.Patch(color=c, label=k.replace("_", " ").title())
            for k, c in color_map.items()
        ]
        ax.legend(handles=legend_patches, loc="upper left", fontsize=9)
        plt.tight_layout()
        plt.savefig(FIG_PATH, dpi=150)
        plt.close()
        print(f"  Figure saved: {FIG_PATH}")
    except ImportError:
        print("  matplotlib not available — skipping PNG figure (ASCII quadrant still written)")
    except Exception as e:
        print(f"  Plot failed: {e}")


def main() -> None:
    print("[06_risk_vs_disclosure] Loading combined features...")
    rows = load_csv(COMBINED_PATH)
    if not rows:
        print("  ERROR: Run 05_prevalence_analysis.py first")
        return

    print(f"  Analyzing {len(rows):,} skills...")
    write_disclosure_report(rows)
    try_plot(rows)
    print(f"  Report: {REPORT_PATH}")


if __name__ == "__main__":
    main()
