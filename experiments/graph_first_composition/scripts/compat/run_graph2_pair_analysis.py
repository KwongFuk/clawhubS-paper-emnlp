#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[4]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from clawhub_analyzer.graph2_pairs import generate_graph2_pair_artifacts


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run Graph2 static pair analysis over the lower-version OpenClaw archive.",
    )
    parser.add_argument(
        "--db-path",
        type=Path,
        default=Path("data/experiment1_split/merged.sqlite3"),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data/experiment1_split/analysis_graph2"),
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=500,
        help="Number of top pair candidates to export.",
    )
    parser.add_argument(
        "--report-top-k",
        type=int,
        default=30,
        help="Number of top rows to render in the markdown report.",
    )
    parser.add_argument(
        "--sink-allowlist",
        default="",
        help="Comma-separated sink skill_ids to keep for experiment-focused Graph2 runs.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    sink_allowlist = {item.strip() for item in args.sink_allowlist.split(",") if item.strip()} or None
    outputs = generate_graph2_pair_artifacts(
        db_path=args.db_path,
        output_dir=args.output_dir,
        top_k=args.top_k,
        report_top_k=args.report_top_k,
        sink_allowlist=sink_allowlist,
    )
    for name, path in outputs.items():
        print(f"{name}={path}")


if __name__ == "__main__":
    main()
