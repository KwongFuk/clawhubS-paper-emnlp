#!/usr/bin/env python3
from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path
import sys

EXPERIMENT_ROOT = Path(__file__).resolve().parents[2]
PROJECT_ROOT = EXPERIMENT_ROOT.parents[1]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from clawhub_analyzer.toxicity import (
    load_capabilities,
    mine_lsagm_attack_paths,
    write_attack_paths_csv,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Mine layered skill attack graph (LSAGM) paths from the merged public archive.",
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
        default=100,
    )
    parser.add_argument(
        "--intermediate-limit",
        type=int,
        default=140,
    )
    parser.add_argument(
        "--sink-limit",
        type=int,
        default=100,
    )
    parser.add_argument(
        "--csv-top-k",
        type=int,
        default=250,
        help="Number of top-ranked paths to export to CSV.",
    )
    parser.add_argument(
        "--report-top-k",
        type=int,
        default=20,
        help="Number of top-ranked paths to render in the markdown report.",
    )
    return parser.parse_args()


def diversify_paths(paths: list, limit: int) -> list:
    buckets: dict[tuple[str, str], list] = {}
    for path in paths:
        key = (path.category, path.carrier_type)
        buckets.setdefault(key, []).append(path)

    ordered_keys = sorted(
        buckets,
        key=lambda item: max(path.risk_score for path in buckets[item]),
        reverse=True,
    )

    selected: list = []
    seen_triplets: set[tuple[str, str, str]] = set()
    bucket_index = {key: 0 for key in ordered_keys}

    while len(selected) < limit:
        progressed = False
        for key in ordered_keys:
            candidates = buckets[key]
            while bucket_index[key] < len(candidates):
                candidate = candidates[bucket_index[key]]
                bucket_index[key] += 1
                triplet = (
                    candidate.source_skill_id,
                    candidate.intermediate_skill_id,
                    candidate.sink_skill_id,
                )
                if triplet in seen_triplets:
                    continue
                selected.append(candidate)
                seen_triplets.add(triplet)
                progressed = True
                break
            if len(selected) >= limit:
                break
        if not progressed:
            break
    return selected


def main() -> None:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    capabilities = load_capabilities(args.db_path)
    paths = mine_lsagm_attack_paths(
        capabilities,
        top_k=None,
        source_limit=args.source_limit,
        intermediate_limit=args.intermediate_limit,
        sink_limit=args.sink_limit,
    )

    top_csv_paths = diversify_paths(paths, args.csv_top_k)
    csv_path = args.output_dir / "lsagm_attack_paths.csv"
    write_attack_paths_csv(csv_path, top_csv_paths)

    category_counts = Counter(path.category for path in paths)
    source_type_counts = Counter(path.source_type for path in paths)
    sink_type_counts = Counter(path.sink_type for path in paths)
    schema_counts = Counter(path.schema_name for path in paths)
    carrier_counts = Counter(path.carrier_type for path in paths)
    effect_counts = Counter(path.effect_type for path in paths)
    domain_cross_counts = Counter(path.crosses_domain for path in paths)
    meta_path_counts = Counter(path.meta_path for path in paths)
    covert_count = sum(
        path.carrier_type in {"memory", "workspace", "browser_state", "device_signal"}
        for path in paths
    )
    overt_count = len(paths) - covert_count

    report_path = args.output_dir / "lsagm_attack_paths_report.md"
    with report_path.open("w", encoding="utf-8") as handle:
        handle.write("# LSAGM Attack Path Analysis\n\n")
        handle.write(f"- DB: `{args.db_path}`\n")
        handle.write(f"- Skills scanned: `{len(capabilities)}`\n")
        handle.write(
            f"- Search limits: source=`{args.source_limit}`, intermediate=`{args.intermediate_limit}`, sink=`{args.sink_limit}`\n"
        )
        handle.write(f"- Total mined paths: `{len(paths)}`\n")
        handle.write(f"- Overt paths: `{overt_count}`\n")
        handle.write(f"- Covert/carrier-backed paths: `{covert_count}`\n")
        handle.write(f"- Top paths exported to CSV: `{len(top_csv_paths)}`\n\n")

        handle.write("## Category Distribution\n\n")
        handle.write("| Category | Count | Ratio |\n")
        handle.write("| --- | ---: | ---: |\n")
        for category, count in category_counts.most_common():
            ratio = (count / len(paths)) if paths else 0.0
            handle.write(f"| {category} | {count} | {ratio:.2%} |\n")

        handle.write("\n## Source Type Distribution\n\n")
        handle.write("| Source Type | Count | Ratio |\n")
        handle.write("| --- | ---: | ---: |\n")
        for source_type, count in source_type_counts.most_common():
            ratio = (count / len(paths)) if paths else 0.0
            handle.write(f"| {source_type} | {count} | {ratio:.2%} |\n")

        handle.write("\n## Sink Type Distribution\n\n")
        handle.write("| Sink Type | Count | Ratio |\n")
        handle.write("| --- | ---: | ---: |\n")
        for sink_type, count in sink_type_counts.most_common():
            ratio = (count / len(paths)) if paths else 0.0
            handle.write(f"| {sink_type} | {count} | {ratio:.2%} |\n")

        handle.write("\n## Risk Schema Distribution\n\n")
        handle.write("| Schema | Count | Ratio |\n")
        handle.write("| --- | ---: | ---: |\n")
        for schema_name, count in schema_counts.most_common():
            ratio = (count / len(paths)) if paths else 0.0
            handle.write(f"| {schema_name} | {count} | {ratio:.2%} |\n")

        handle.write("\n## Carrier Distribution\n\n")
        handle.write("| Carrier | Count | Ratio |\n")
        handle.write("| --- | ---: | ---: |\n")
        for carrier, count in carrier_counts.most_common():
            ratio = (count / len(paths)) if paths else 0.0
            handle.write(f"| {carrier} | {count} | {ratio:.2%} |\n")

        handle.write("\n## Effect Distribution\n\n")
        handle.write("| Effect | Count | Ratio |\n")
        handle.write("| --- | ---: | ---: |\n")
        for effect, count in effect_counts.most_common():
            ratio = (count / len(paths)) if paths else 0.0
            handle.write(f"| {effect} | {count} | {ratio:.2%} |\n")

        handle.write("\n## Trust Domain Crossings\n\n")
        handle.write("| Domain Crossing | Count | Ratio |\n")
        handle.write("| --- | ---: | ---: |\n")
        for crossing, count in domain_cross_counts.most_common():
            ratio = (count / len(paths)) if paths else 0.0
            handle.write(f"| {crossing} | {count} | {ratio:.2%} |\n")

        handle.write("\n## Meta-path Distribution\n\n")
        handle.write("| Meta-path | Count |\n")
        handle.write("| --- | ---: |\n")
        for meta_path, count in meta_path_counts.most_common():
            handle.write(f"| {meta_path} | {count} |\n")

        handle.write("\n## Top Attack Paths\n\n")
        handle.write(
            "| Category | SourceType | SinkType | Schema | Source | Intermediate | Sink | Carrier | Crossing | Survival | Compatibility | Hazard | Risk |\n"
        )
        handle.write("| --- | --- | --- | --- | --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: |\n")
        for path in diversify_paths(paths, args.report_top_k):
            handle.write(
                f"| {path.category} | {path.source_type} | {path.sink_type} | {path.schema_name} | "
                f"`{path.source_skill_id}` | `{path.intermediate_skill_id}` | `{path.sink_skill_id}` | "
                f"{path.carrier_type} | {path.crosses_domain} | {path.influence_survival:.3f} | "
                f"{path.compatibility:.3f} | {path.hazard:.2f} | {path.risk_score:.3f} |\n"
            )

    print(f"Wrote LSAGM CSV to {csv_path}")
    print(f"Wrote LSAGM report to {report_path}")
    print(f"Total paths: {len(paths)}")
    for category, count in category_counts.most_common():
        print(f"{category}: {count}")


if __name__ == "__main__":
    main()
