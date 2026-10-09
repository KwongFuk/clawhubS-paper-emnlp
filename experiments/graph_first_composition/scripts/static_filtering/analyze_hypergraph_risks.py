#!/usr/bin/env python3
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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run Stage 2 directed-hypergraph risk analysis over the merged public archive.",
    )
    parser.add_argument(
        "--db-path",
        type=Path,
        default=PROJECT_ROOT / "data/experiment1_split/merged.sqlite3",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=EXPERIMENT_ROOT / "data/legacy_analysis",
    )
    parser.add_argument(
        "--source-limit",
        type=int,
        default=400,
        help="Number of top source-capable skills to seed the search.",
    )
    parser.add_argument(
        "--bridge-limit",
        type=int,
        default=80,
        help="Number of bridge candidates retained per source after head-tail overlap filtering.",
    )
    parser.add_argument(
        "--sink-limit",
        type=int,
        default=50,
        help="Number of sink candidates retained per bridge after head-tail overlap filtering.",
    )
    parser.add_argument(
        "--csv-top-k",
        type=int,
        default=500,
        help="Number of top hypergraph combinations to export to CSV.",
    )
    parser.add_argument(
        "--report-top-k",
        type=int,
        default=20,
        help="Number of top combinations to render in the markdown report.",
    )
    parser.add_argument(
        "--skill-top-k",
        type=int,
        default=50,
        help="Number of risky skills to export to the summary CSV and report.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    outputs = generate_hypergraph_risk_artifacts(
        db_path=args.db_path,
        output_dir=args.output_dir,
        source_limit=args.source_limit,
        bridge_limit=args.bridge_limit,
        sink_limit=args.sink_limit,
        csv_top_k=args.csv_top_k,
        report_top_k=args.report_top_k,
        skill_top_k=args.skill_top_k,
    )
    for name, path in outputs.items():
        print(f"{name}={path}")


if __name__ == "__main__":
    main()
