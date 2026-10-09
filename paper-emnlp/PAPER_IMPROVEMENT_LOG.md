# Paper Improvement Log

## Score Progression

| Round | Score | Key Changes | Figures |
|-------|-------|-------------|---------|
| Round 0 | 6.5/10 | Baseline | 2 AI-gen PNGs |
| Round 1 | 7.5/10 | §6.4→Appendix, Obs trimmed | 2 |
| Round 2 | 8.0/10 | Zero strong words | 2 |
| Round 3 | 8.5/10 | Obs dedup, BibTeX fixed | 2 |
| Round 4 | 9.0/10 | Redundancy removal | 2 |
| **Round 5** | **9.5/10** | **Publication-quality figures** | **5 vector PDFs** |

## Round 5: Figure Overhaul

### What Changed
Replaced 2 AI-generated PNGs with 5 publication-quality vector PDF figures using `paper-figure` skill (matplotlib).

| Figure | Type | Status | Description |
|--------|------|--------|-------------|
| Fig 1: Pipeline | matplotlib diagram | ✅ Replaced | Updated from "5-tool" to "110 skills, 3,520 calls" |
| Fig 2: Abstraction Gap | Bar chart | ✅ Replaced | Clean 2-bar comparison with 10× annotation |
| **Fig 3: Routing Repair** | Grouped bar | ✅ **NEW** | 4-condition × 4-query-type routing accuracy |
| **Fig 4: Selection Concentration** | Paired bar | ✅ **NEW** | δ_skill vs δ_pos across 8 clusters |
| **Fig 5: Feature Correlation** | Horizontal bar | ✅ **NEW** | Pearson r for 5 description features |

### Technical Details
- All figures use shared `paper_plot_style.py` (Times New Roman, 10pt, 300 DPI)
- Output: both PDF (vector, for LaTeX) and PNG (preview)
- Color palette: professional academic (blue/green/orange/purple/red/gray)
- Each figure has a standalone generation script for reproducibility

### File Inventory
```
figures/
├── paper_plot_style.py          # Shared style
├── gen_fig1_pipeline.py         # Pipeline diagram
├── gen_fig2_abstraction.py      # Abstraction gap
├── gen_fig3_selection.py        # Selection concentration
├── gen_fig4_routing.py          # Routing repair
├── gen_fig5_features.py         # Feature correlation
├── pipeline_v2.pdf/.png         # Fig 1
├── abstraction_gap_v2.pdf/.png  # Fig 2
├── routing_repair.pdf/.png      # Fig 3
├── selection_concentration.pdf/.png  # Fig 4
└── feature_correlation.pdf/.png      # Fig 5
```

### Final Metrics
- Figures: 5 (was 2)
- Tables: 17
- Labels: 46 defined, 24 referenced, 0 missing
- Braces: 634/634 balanced
- Compilation: success, 0 errors
- Total lines: 1,535

## PDFs
- `main_round5_figures.pdf` — With publication-quality figures
- `main.pdf` — = main_round5_figures.pdf
