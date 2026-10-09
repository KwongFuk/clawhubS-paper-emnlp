#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import sys
from dataclasses import asdict
from pathlib import Path

LEGACY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LEGACY_ROOT / "src"))

from trust_drift import DriftTransition, analyze_trust_drift


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build stronger proxy evidence for trust-drift candidates.",
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
        default=20,
    )
    return parser.parse_args()


def behavior_change_score(item: DriftTransition) -> int:
    return (
        item.agent_facing_changed_files * 3
        + item.permission_term_increase * 2
        + item.action_term_increase * 2
        + item.covert_term_increase * 2
        + item.trust_term_increase
        + (4 if item.scan_worsened else 0)
        - min(item.code_changed_files, 3)
    )


def review_gap_score(item: DriftTransition) -> int:
    return (
        behavior_change_score(item)
        + (4 if item.generic_or_empty_changelog else 0)
        + (2 if item.code_changed_files <= 2 else 0)
        + (1 if item.trust_term_increase > 0 else 0)
    )


def malicious_intent_proxy_score(item: DriftTransition) -> int:
    deceptive_trust = 2 if item.trust_term_increase > 0 and (
        item.permission_term_increase > 0
        or item.action_term_increase > 0
        or item.covert_term_increase > 0
    ) else 0
    stealth = 2 if item.covert_term_increase >= 2 else 0
    underdisclosure = 2 if item.generic_or_empty_changelog else 0
    scan_penalty = 3 if item.scan_worsened else 0
    return review_gap_score(item) + deceptive_trust + stealth + underdisclosure + scan_penalty


def is_behavior_change_proxy(item: DriftTransition) -> bool:
    semantic_delta = (
        item.permission_term_increase
        + item.action_term_increase
        + item.trust_term_increase
        + item.covert_term_increase
    )
    return item.semantic_drift_candidate and semantic_delta > 0 and (
        item.action_term_increase >= 3
        or item.permission_term_increase >= 3
        or item.covert_term_increase >= 2
        or behavior_change_score(item) >= 12
    )


def is_review_gap_proxy(item: DriftTransition) -> bool:
    return is_behavior_change_proxy(item) and (
        item.generic_or_empty_changelog
        or (
            item.code_changed_files <= 2
            and (item.permission_term_increase + item.action_term_increase + item.covert_term_increase) >= 4
        )
    )


def is_malicious_intent_proxy(item: DriftTransition) -> bool:
    return is_review_gap_proxy(item) and (
        item.scan_worsened
        or (
            item.trust_term_increase > 0
            and (item.permission_term_increase + item.action_term_increase + item.covert_term_increase) >= 5
        )
        or (item.covert_term_increase >= 2 and item.action_term_increase >= 2)
    )


def sort_key(item: DriftTransition, score_fn) -> tuple[int, int, int]:
    total_delta = (
        item.permission_term_increase
        + item.action_term_increase
        + item.trust_term_increase
        + item.covert_term_increase
    )
    return (
        score_fn(item),
        total_delta,
        item.agent_facing_changed_files,
    )


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    args = parse_args()
    _, transitions = analyze_trust_drift(args.db_path, args.project_root.resolve())
    args.output_dir.mkdir(parents=True, exist_ok=True)

    behavior_rows: list[dict[str, object]] = []
    review_rows: list[dict[str, object]] = []
    malicious_rows: list[dict[str, object]] = []

    for item in transitions:
        base = asdict(item)
        base["behavior_change_score"] = behavior_change_score(item)
        base["review_gap_score"] = review_gap_score(item)
        base["malicious_intent_proxy_score"] = malicious_intent_proxy_score(item)
        base["behavior_change_proxy"] = int(is_behavior_change_proxy(item))
        base["review_gap_proxy"] = int(is_review_gap_proxy(item))
        base["malicious_intent_proxy"] = int(is_malicious_intent_proxy(item))

        if base["behavior_change_proxy"]:
            behavior_rows.append(base)
        if base["review_gap_proxy"]:
            review_rows.append(base)
        if base["malicious_intent_proxy"]:
            malicious_rows.append(base)

    behavior_rows.sort(
        key=lambda row: (
            row["behavior_change_score"],
            row["permission_term_increase"] + row["action_term_increase"] + row["covert_term_increase"],
            row["agent_facing_changed_files"],
        ),
        reverse=True,
    )
    review_rows.sort(
        key=lambda row: (
            row["review_gap_score"],
            row["generic_or_empty_changelog"],
            row["code_changed_files"] * -1,
        ),
        reverse=True,
    )
    malicious_rows.sort(
        key=lambda row: (
            row["malicious_intent_proxy_score"],
            row["scan_worsened"],
            row["trust_term_increase"] + row["covert_term_increase"],
        ),
        reverse=True,
    )

    csv_path = args.output_dir / "trust_drift_proxy_candidates.csv"
    report_path = args.output_dir / "trust_drift_proxy_report.md"

    combined_rows = malicious_rows + [
        row for row in review_rows if row["skill_id"] not in {item["skill_id"] for item in malicious_rows}
    ] + [
        row for row in behavior_rows if row["skill_id"] not in {item["skill_id"] for item in malicious_rows + review_rows}
    ]
    write_csv(csv_path, combined_rows)

    with report_path.open("w", encoding="utf-8") as handle:
        handle.write("# Trust Drift Stronger Proxy Analysis\n\n")
        handle.write("本报告不把代理证据当成最终证明，而是回答：在现有数据库里，哪些候选最接近以下三类更强声明。\n\n")
        handle.write("1. 更可能已经改变 OpenClaw 的实际决策或执行行为\n")
        handle.write("2. 更可能在版本披露不足的情况下滑过常规人工审查\n")
        handle.write("3. 更接近具有误导/恶意意图的语义 rug-pull 形态\n\n")

        handle.write("## Proxy Counts\n\n")
        handle.write(f"- `behavior_change_proxy_count`: `{len(behavior_rows)}`\n")
        handle.write(f"- `review_gap_proxy_count`: `{len(review_rows)}`\n")
        handle.write(f"- `malicious_intent_proxy_count`: `{len(malicious_rows)}`\n\n")

        def write_section(title: str, rows: list[dict[str, object]], score_field: str) -> None:
            handle.write(f"## {title}\n\n")
            handle.write(
                "| Skill | Prev -> Curr | Score | Agent-facing | Code | Permission + | Action + | Trust + | Covert + | Scan Worse | Generic Changelog |\n"
            )
            handle.write(
                "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |\n"
            )
            for row in rows[: args.top_k]:
                handle.write(
                    f"| `{row['skill_id']}` | `{row['previous_version']}` -> `{row['current_version']}` | "
                    f"{row[score_field]} | {row['agent_facing_changed_files']} | {row['code_changed_files']} | "
                    f"{row['permission_term_increase']} | {row['action_term_increase']} | "
                    f"{row['trust_term_increase']} | {row['covert_term_increase']} | "
                    f"{'yes' if int(row['scan_worsened']) else 'no'} | "
                    f"{'yes' if int(row['generic_or_empty_changelog']) else 'no'} |\n"
                )
            handle.write("\n")

        write_section("Behavior-Change Proxies", behavior_rows, "behavior_change_score")
        write_section("Review-Gap / Underdisclosure Proxies", review_rows, "review_gap_score")
        write_section("Malicious-Intent Proxies", malicious_rows, "malicious_intent_proxy_score")

        handle.write("## Interpretation Boundaries\n\n")
        handle.write("- 这些是更强的**代理证据**，不是最终事实认定。\n")
        handle.write("- `behavior_change_proxy` 代表更可能影响 agent 的选择、调用或执行路径。\n")
        handle.write("- `review_gap_proxy` 代表语义变化和披露强度之间存在明显缺口。\n")
        handle.write("- `malicious_intent_proxy` 代表更像“信任措辞 + 权限/动作扩张 + 披露不足/安全恶化”的组合，但仍不能单独证明主观恶意。\n")

    print(f"Wrote stronger trust-drift proxy report to {report_path}")
    print(f"Wrote stronger trust-drift proxy candidates to {csv_path}")
    print(f"behavior_change_proxy_count={len(behavior_rows)}")
    print(f"review_gap_proxy_count={len(review_rows)}")
    print(f"malicious_intent_proxy_count={len(malicious_rows)}")


if __name__ == "__main__":
    main()
