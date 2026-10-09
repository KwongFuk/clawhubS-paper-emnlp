#!/usr/bin/env python3
"""Run Stage 2 hypergraph analysis on the isolated skills.sh archive."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

EXPERIMENT_ROOT = Path(__file__).resolve().parents[2]
PROJECT_ROOT = EXPERIMENT_ROOT.parents[1]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from clawhub_analyzer.hypergraph import generate_hypergraph_risk_artifacts
from clawhub_analyzer.hypergraph_pairs import generate_hypergraph_pair_risk_artifacts


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate isolated Stage 2 hypergraph artifacts for skills.sh.",
    )
    parser.add_argument(
        "--db-path",
        type=Path,
        default=PROJECT_ROOT / "data/skills_sh/skills_sh.sqlite3",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=EXPERIMENT_ROOT / "data/skills_sh_analysis",
    )
    parser.add_argument(
        "--mode",
        choices=("triples", "pairs", "both"),
        default="both",
        help="Which hypergraph variants to render.",
    )
    parser.add_argument(
        "--pair-source-limit",
        type=int,
        default=400,
        help="Maximum source-capable skills to consider for pair mining.",
    )
    parser.add_argument(
        "--pair-sink-limit",
        type=int,
        default=80,
        help="Maximum sink candidates retained per source during pair mining.",
    )
    parser.add_argument(
        "--pair-top-k",
        type=int,
        default=500,
        help="Maximum number of filtered risk pairs to write to CSV outputs.",
    )
    parser.add_argument(
        "--pair-min-risk-score",
        type=float,
        default=None,
        help="Optional minimum pair risk score threshold.",
    )
    parser.add_argument(
        "--pair-min-severity",
        choices=("low", "medium", "high", "critical"),
        default=None,
        help="Optional minimum severity threshold for filtered risk pairs.",
    )
    parser.add_argument(
        "--pair-report-top-k",
        type=int,
        default=20,
        help="Number of top risk pairs to render in the markdown report.",
    )
    parser.add_argument(
        "--pair-skill-top-k",
        type=int,
        default=50,
        help="Number of top risky skills to render in pair-analysis outputs.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.mode in {"triples", "both"}:
        outputs = generate_hypergraph_risk_artifacts(
            db_path=args.db_path,
            output_dir=args.output_dir / "triples",
        )
        for name, path in outputs.items():
            print(f"triples_{name}={path}")
    if args.mode in {"pairs", "both"}:
        outputs = generate_hypergraph_pair_risk_artifacts(
            db_path=args.db_path,
            output_dir=args.output_dir / "pairs",
            source_limit=args.pair_source_limit,
            sink_limit=args.pair_sink_limit,
            csv_top_k=args.pair_top_k,
            min_risk_score=args.pair_min_risk_score,
            min_severity=args.pair_min_severity,
            report_top_k=args.pair_report_top_k,
            skill_top_k=args.pair_skill_top_k,
        )
        for name, path in outputs.items():
            print(f"pairs_{name}={path}")


if __name__ == "__main__":
    main()
