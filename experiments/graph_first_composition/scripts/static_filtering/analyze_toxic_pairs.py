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
    exact_randomized_pair_expectation,
    load_capabilities,
    rank_covert_pairs,
    rank_obvious_pairs,
    count_typed_pairs,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate a pair-focused toxic composition report with randomized baselines.",
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
        "--top-k",
        type=int,
        default=20,
        help="Number of top overt/covert pair examples to include.",
    )
    return parser.parse_args()


def summarize_expectation(observed: int, baseline_mean: float) -> dict[str, float]:
    enrichment = (observed / baseline_mean) if baseline_mean else 0.0
    return {
        "observed": observed,
        "baseline_mean": baseline_mean,
        "enrichment": enrichment,
    }


def main() -> None:
    args = parse_args()
    capabilities = load_capabilities(args.db_path)
    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    categories = [
        "overt_input_to_action",
        "overt_read_to_exfil",
        "overt_input_to_exfil",
        "overt_read_to_action",
    ]
    observed = count_typed_pairs(capabilities)
    baseline_means = exact_randomized_pair_expectation(capabilities)

    summaries = {
        category: summarize_expectation(
            observed[category],
            baseline_means[category],
        )
        for category in categories
    }

    overt_examples = rank_obvious_pairs(capabilities, top_k=args.top_k)
    covert_examples = rank_covert_pairs(capabilities, top_k=args.top_k)

    report_path = output_dir / "toxic_pairs_report.md"
    with report_path.open("w", encoding="utf-8") as handle:
        handle.write("# Toxic Pair Analysis\n\n")
        handle.write(f"- DB: `{args.db_path}`\n")
        handle.write(f"- Skills scanned: `{len(capabilities)}`\n")
        handle.write("- Randomized baseline: exact expectation under domain-shuffle null model\n\n")

        handle.write("## Typed Pair Counts vs Randomized Baseline\n\n")
        handle.write("| Category | Observed | Expected Under Null | Enrichment |\n")
        handle.write("| --- | ---: | ---: | ---: |\n")
        for category in categories:
            summary = summaries[category]
            handle.write(
                f"| {category} | {summary['observed']} | "
                f"{summary['baseline_mean']:.2f} | {summary['enrichment']:.2f} |\n"
            )

        handle.write("\n## Top Overt Pair Seeds\n\n")
        handle.write("| Category | Source | Sink | Score | Rationale |\n")
        handle.write("| --- | --- | --- | ---: | --- |\n")
        for row in overt_examples:
            handle.write(
                f"| {row.category} | `{row.source_skill_id}` | `{row.sink_skill_id}` | "
                f"{row.score:.2f} | {row.rationale} |\n"
            )

        handle.write("\n## Top Covert Pair Seeds\n\n")
        handle.write("| Category | Source | Sink | Medium | Score | Rationale |\n")
        handle.write("| --- | --- | --- | --- | ---: | --- |\n")
        for row in covert_examples:
            handle.write(
                f"| {row.category} | `{row.source_skill_id}` | `{row.sink_skill_id}` | "
                f"{row.medium} | {row.score:.2f} | {row.rationale} |\n"
            )

    print(f"Wrote pair analysis report to {report_path}")
    for category in categories:
        summary = summaries[category]
        print(
            f"{category}: observed={summary['observed']} "
            f"expected={summary['baseline_mean']:.2f} "
            f"enrichment={summary['enrichment']:.2f}"
        )


if __name__ == "__main__":
    main()
