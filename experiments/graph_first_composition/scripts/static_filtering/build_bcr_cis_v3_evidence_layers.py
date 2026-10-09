#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
from collections import Counter
from pathlib import Path


EXPERIMENT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INPUT = EXPERIMENT_ROOT / "data/analysis_bcr_cis_v3/bcr_cis_v3_pairs.csv"
DEFAULT_OUTPUT = EXPERIMENT_ROOT / "data/analysis_bcr_cis_v3/bcr_cis_v3_evidence_layers.md"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default=str(DEFAULT_INPUT))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--medium-confidence-threshold", type=float, default=0.25)
    parser.add_argument("--high-confidence-threshold", type=float, default=0.50)
    return parser.parse_args()


def as_bool(value: str) -> bool:
    return value.strip().lower() == "true"


def as_float(value: str) -> float:
    return float(value) if value else 0.0


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def format_share(count: int, total: int) -> str:
    if total <= 0:
        return "n/a"
    share = count / total
    if share == 0:
        return "0.0%"
    if share < 0.001:
        return "<0.1%"
    return f"{share:.1%}"


def sort_key(row: dict[str, str]) -> tuple[float, float, float, int]:
    return (
        as_float(row["plausibility_score"]),
        as_float(row["channel_confidence"]),
        as_float(row["ph_score"]),
        -int(float(row["source_installs_all_time"] or 0)),
    )


def render_top_table(rows: list[dict[str, str]], *, limit: int) -> list[str]:
    lines = [
        "| Rank | Source | Sink | Carrier | Source Domain | Sink Domain | CH | PH | Plausibility | Ecological |",
        "| ---: | --- | --- | --- | --- | --- | ---: | ---: | ---: | :---: |",
    ]
    for idx, row in enumerate(rows[:limit], start=1):
        lines.append(
            "| "
            + " | ".join(
                [
                    str(idx),
                    f"`{row['source_skill_id']}`",
                    f"`{row['sink_skill_id']}`",
                    f"`{row['carrier_type']}`",
                    f"`{row['source_domain']}`",
                    f"`{row['sink_domain']}`",
                    row["channel_confidence"],
                    row["ph_score"],
                    row["plausibility_score"],
                    "✅" if as_bool(row["ecological_pass"]) else "❌",
                ]
            )
            + " |"
        )
    return lines


def render_counter_table(counter: Counter[str], title: str) -> list[str]:
    lines = [title, "", "| Value | Count |", "| --- | ---: |"]
    for key, count in counter.most_common():
        lines.append(f"| `{key}` | {count} |")
    return lines


def build_markdown(
    rows: list[dict[str, str]],
    *,
    medium_confidence_threshold: float,
    high_confidence_threshold: float,
) -> str:
    candidates = rows
    total_rows = len(rows)
    ecological = [row for row in rows if as_bool(row["ecological_pass"])]
    medium_confidence = [
        row for row in ecological if as_float(row["channel_confidence"]) >= medium_confidence_threshold
    ]
    high_confidence = [
        row for row in ecological if as_float(row["channel_confidence"]) >= high_confidence_threshold
    ]
    high_confidence_new = [row for row in high_confidence if as_bool(row["is_new_arrival"])]

    high_confidence_sorted = sorted(high_confidence, key=sort_key, reverse=True)
    medium_sorted = sorted(medium_confidence, key=sort_key, reverse=True)

    lines = [
        "# BCR+CIS v3 Evidence Layers",
        "",
        "> Purpose: separate the latest static output into candidate-discovery and higher-confidence evidence layers.",
        "",
        "## Evidence Layers",
        "",
        "| Layer | Rule | Count | Share of 10k | Intended use |",
        "| --- | --- | ---: | ---: | --- |",
        f"| `candidate_pool` | all latest `BCR+CIS v3` output pairs | {len(candidates)} | {format_share(len(candidates), total_rows)} | high-recall discovery only |",
        f"| `ecological_support` | `ecological_pass = true` | {len(ecological)} | {format_share(len(ecological), total_rows)} | better-supported static subset |",
        f"| `medium_confidence_support` | ecological support + `channel_confidence >= {medium_confidence_threshold:.2f}` | {len(medium_confidence)} | {format_share(len(medium_confidence), total_rows)} | paper-quality shortlist / runtime seeding |",
        f"| `higher_confidence_support` | ecological support + `channel_confidence >= {high_confidence_threshold:.2f}` | {len(high_confidence)} | {format_share(len(high_confidence), total_rows)} | strongest static evidence layer |",
        f"| `higher_confidence_new_arrivals` | higher-confidence support + `is_new_arrival = true` | {len(high_confidence_new)} | {format_share(len(high_confidence_new), total_rows)} | newly surfaced high-confidence cases |",
        "",
        "## Higher-Confidence Support By Carrier",
        "",
        "| Carrier | Count |",
        "| --- | ---: |",
    ]

    for carrier, count in Counter(row["carrier_type"] for row in high_confidence).most_common():
        lines.append(f"| `{carrier}` | {count} |")

    lines.extend(
        [
            "",
            "## Higher-Confidence Support By Sink Type",
            "",
            "| Sink Type | Count |",
            "| --- | ---: |",
        ]
    )
    for sink_type, count in Counter(row["sink_type"] for row in high_confidence).most_common():
        lines.append(f"| `{sink_type}` | {count} |")

    lines.extend(
        [
            "",
            "## Higher-Confidence Support By Source Domain",
            "",
            "| Source Domain | Count |",
            "| --- | ---: |",
        ]
    )
    for source_domain, count in Counter(row["source_domain"] for row in high_confidence).most_common():
        lines.append(f"| `{source_domain}` | {count} |")

    lines.extend(
        [
            "",
            "## Top Higher-Confidence Pairs",
            "",
        ]
    )
    lines.extend(render_top_table(high_confidence_sorted, limit=25))

    lines.extend(
        [
            "",
            "## Top Medium-Confidence Pairs",
            "",
        ]
    )
    lines.extend(render_top_table(medium_sorted, limit=25))

    return "\n".join(lines) + "\n"


def main() -> int:
    args = parse_args()
    rows = read_rows(Path(args.input))
    markdown = build_markdown(
        rows,
        medium_confidence_threshold=args.medium_confidence_threshold,
        high_confidence_threshold=args.high_confidence_threshold,
    )
    Path(args.output).write_text(markdown, encoding="utf-8")
    print(markdown, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
