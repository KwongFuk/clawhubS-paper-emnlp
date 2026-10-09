#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

LEGACY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LEGACY_ROOT / "src"))

from trust_drift import analyze_trust_drift


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Analyze semantic trust drift / rug-pull candidates across skill versions.",
    )
    parser.add_argument(
        "--db-path",
        type=Path,
        default=Path("data/experiment1_split/merged.sqlite3"),
    )
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path("."),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data/experiment1_split/analysis"),
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=25,
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    summary, transitions = analyze_trust_drift(args.db_path, args.project_root.resolve())
    args.output_dir.mkdir(parents=True, exist_ok=True)

    report_path = args.output_dir / "trust_drift_report.md"
    csv_path = args.output_dir / "trust_drift_candidates.csv"

    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        handle.write(
            "skill_id,previous_version,current_version,agent_facing_changed_files,code_changed_files,"
            "permission_term_increase,action_term_increase,trust_term_increase,covert_term_increase,"
            "scan_worsened,generic_or_empty_changelog,semantic_drift_candidate,rug_pull_candidate\n"
        )
        for item in transitions:
            handle.write(
                f"{item.skill_id},{item.previous_version},{item.current_version},"
                f"{item.agent_facing_changed_files},{item.code_changed_files},"
                f"{item.permission_term_increase},{item.action_term_increase},"
                f"{item.trust_term_increase},{item.covert_term_increase},"
                f"{int(item.scan_worsened)},{int(item.generic_or_empty_changelog)},"
                f"{int(item.semantic_drift_candidate)},{int(item.rug_pull_candidate)}\n"
            )

    with report_path.open("w", encoding="utf-8") as handle:
        handle.write("# Trust Drift Analysis\n\n")
        handle.write(f"- DB: `{args.db_path}`\n")
        handle.write(f"- Project root: `{args.project_root.resolve()}`\n\n")

        handle.write("## Summary\n\n")
        for key, value in summary.items():
            handle.write(f"- `{key}`: `{value}`\n")

        handle.write("\n## Top Semantic Drift / Rug-Pull Candidates\n\n")
        handle.write(
            "| Skill | Prev -> Curr | Agent-facing Changed | Code Changed | Permission + | "
            "Action + | Trust + | Covert + | Scan Worse | Generic/Empty Changelog | "
            "Semantic Drift | Rug Pull |\n"
        )
        handle.write(
            "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- | --- | --- |\n"
        )
        for item in transitions[: args.top_k]:
            handle.write(
                f"| `{item.skill_id}` | `{item.previous_version}` -> `{item.current_version}` | "
                f"{item.agent_facing_changed_files} | {item.code_changed_files} | "
                f"{item.permission_term_increase} | {item.action_term_increase} | "
                f"{item.trust_term_increase} | {item.covert_term_increase} | "
                f"{'yes' if item.scan_worsened else 'no'} | "
                f"{'yes' if item.generic_or_empty_changelog else 'no'} | "
                f"{'yes' if item.semantic_drift_candidate else 'no'} | "
                f"{'yes' if item.rug_pull_candidate else 'no'} |\n"
            )

    print(f"Wrote trust drift report to {report_path}")
    print(f"Wrote trust drift CSV to {csv_path}")
    for key, value in summary.items():
        print(f"{key}={value}")


if __name__ == "__main__":
    main()
