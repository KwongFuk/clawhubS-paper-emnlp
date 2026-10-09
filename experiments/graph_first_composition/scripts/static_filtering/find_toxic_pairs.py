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

from clawhub_analyzer.toxicity import (
    load_capabilities,
    rank_covert_pairs,
    rank_obvious_pairs,
    write_candidates_csv,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Mine overt and covert toxic-composition candidates from a ClawHub archive DB.",
    )
    parser.add_argument(
        "--db-path",
        type=Path,
        default=PROJECT_ROOT / "data/experiment1_split/merged.sqlite3",
        help="Path to the merged SQLite archive.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=EXPERIMENT_ROOT / "data/legacy_analysis",
        help="Directory for CSV outputs.",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=50,
        help="Number of top candidates to write per category.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    capabilities = load_capabilities(args.db_path)
    obvious = rank_obvious_pairs(capabilities, top_k=args.top_k)
    covert = rank_covert_pairs(capabilities, top_k=args.top_k)

    output_dir = args.output_dir
    write_candidates_csv(output_dir / "toxic_pairs_obvious.csv", obvious)
    write_candidates_csv(output_dir / "toxic_pairs_covert.csv", covert)

    print(f"Loaded {len(capabilities)} skills from {args.db_path}")
    print(f"Wrote {len(obvious)} overt candidates to {output_dir / 'toxic_pairs_obvious.csv'}")
    print(f"Wrote {len(covert)} covert candidates to {output_dir / 'toxic_pairs_covert.csv'}")


if __name__ == "__main__":
    main()
