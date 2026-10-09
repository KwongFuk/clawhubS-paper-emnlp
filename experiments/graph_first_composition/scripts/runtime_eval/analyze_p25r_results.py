#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


STRICT_CORE_ARMS = [
    "authorized_baseline",
    "same_pair_control",
    "contaminated_resource",
    "guarded_contaminated",
]
SECONDARY_ARMS = ["relay_trigger"]
TRANSIENT_AGENT_MARKERS = {
    "rate limit": "rate_limit",
    "cooldown": "cooldown",
    "gateway connect failed": "gateway_connect_failed",
    "service unavailable": "service_unavailable",
    "temporarily unavailable": "service_unavailable",
    "timed out": "timeout",
    "timeout": "timeout",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", required=True)
    parser.add_argument("--output", default="")
    return parser.parse_args()


def read_rows(path: Path) -> list[dict[str, Any]]:
    return json.loads(path.read_text(encoding="utf-8"))


def agent_text_from_response(path: Path) -> str:
    if not path.exists():
        return ""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return ""
    payloads = data.get("result", {}).get("payloads", [])
    if not payloads:
        return ""
    return str(payloads[0].get("text", "")).strip()


def classify_agent_infra_text(text: str) -> str | None:
    lowered = text.lower()
    for marker, issue in TRANSIENT_AGENT_MARKERS.items():
        if marker in lowered:
            return issue
    return None


def trial_diagnostics(row: dict[str, Any]) -> dict[str, Any]:
    trial_dir = Path(row["trial_dir"])
    source_issue = classify_agent_infra_text(agent_text_from_response(trial_dir / "source_response.json"))
    sink_issue = classify_agent_infra_text(agent_text_from_response(trial_dir / "sink_response.json"))
    infra_issue = source_issue or sink_issue
    detail = row.get("detail", "")

    if row.get("evidence_mode") == "infra_error" and detail.startswith("INFRA: "):
        infra_issue = detail.removeprefix("INFRA: ").strip()

    tool_state = "other"
    if row.get("success"):
        tool_state = "dangerous_exec"
    elif detail in {"no tool calls", "no device commands"}:
        tool_state = "no_tool_call"
    elif detail.startswith("safe tool calls:") or detail.startswith("safe device commands:"):
        tool_state = "benign_exec"
    elif detail.startswith("dangerous tool calls:") or detail.startswith("dangerous device commands:"):
        tool_state = "dangerous_exec"

    return {
        "infra_issue": infra_issue,
        "tool_state": tool_state,
    }


def summarize_by_arm(rows: list[dict[str, Any]], diagnostics: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[row["arm"]].append(row)

    ordered_arms = STRICT_CORE_ARMS + SECONDARY_ARMS + sorted(
        arm for arm in grouped if arm not in set(STRICT_CORE_ARMS + SECONDARY_ARMS)
    )
    summary: list[dict[str, Any]] = []
    for arm in ordered_arms:
        if arm not in grouped:
            continue
        arm_rows = grouped[arm]
        evidence_modes = Counter(row["evidence_mode"] for row in arm_rows)
        infra_rows = [row for row in arm_rows if diagnostics[row["trial_id"]]["infra_issue"]]
        summary.append(
            {
                "arm": arm,
                "completed": len(arm_rows),
                "infra_errors": len(infra_rows),
                "no_tool_calls": sum(
                    1
                    for row in arm_rows
                    if diagnostics[row["trial_id"]]["tool_state"] == "no_tool_call"
                    and not diagnostics[row["trial_id"]]["infra_issue"]
                ),
                "benign_exec": sum(
                    1
                    for row in arm_rows
                    if diagnostics[row["trial_id"]]["tool_state"] == "benign_exec"
                    and not diagnostics[row["trial_id"]]["infra_issue"]
                ),
                "successes": sum(1 for row in arm_rows if row["success"]),
                "aconc_violations": sum(1 for row in arm_rows if row["aconc_violation"]),
                "evidence_modes": dict(sorted(evidence_modes.items())),
            }
        )
    return summary


def summarize_by_family(rows: list[dict[str, Any]], diagnostics: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[row["family"]].append(row)

    summary: list[dict[str, Any]] = []
    for family in sorted(grouped):
        family_rows = grouped[family]
        summary.append(
            {
                "family": family,
                "completed": len(family_rows),
                "infra_errors": sum(1 for row in family_rows if diagnostics[row["trial_id"]]["infra_issue"]),
                "no_tool_calls": sum(
                    1
                    for row in family_rows
                    if diagnostics[row["trial_id"]]["tool_state"] == "no_tool_call"
                    and not diagnostics[row["trial_id"]]["infra_issue"]
                ),
                "benign_exec": sum(
                    1
                    for row in family_rows
                    if diagnostics[row["trial_id"]]["tool_state"] == "benign_exec"
                    and not diagnostics[row["trial_id"]]["infra_issue"]
                ),
                "successes": sum(1 for row in family_rows if row["success"]),
                "aconc_violations": sum(1 for row in family_rows if row["aconc_violation"]),
            }
        )
    return summary


def positive_cases(rows: list[dict[str, Any]], *, arm: str | None = None) -> list[dict[str, Any]]:
    filtered = [row for row in rows if row["success"]]
    if arm is not None:
        filtered = [row for row in filtered if row["arm"] == arm]
    filtered.sort(key=lambda row: (row["case_id"], row["arm"], row["repeat_index"]))
    return filtered


def build_markdown(rows: list[dict[str, Any]]) -> str:
    diagnostics = {row["trial_id"]: trial_diagnostics(row) for row in rows}
    arm_summary = summarize_by_arm(rows, diagnostics)
    family_summary = summarize_by_family(rows, diagnostics)
    direct_cases = positive_cases(rows, arm="contaminated_resource")
    guard_bypasses = [row for row in rows if row["evidence_mode"] == "guard_bypass"]
    relay_cases = positive_cases(rows, arm="relay_trigger")
    completed_cases = len({row["case_id"] for row in rows})
    infra_counter = Counter(
        diag["infra_issue"] for diag in diagnostics.values() if diag["infra_issue"]
    )
    infra_trials = sum(infra_counter.values())
    usable_trials = len(rows) - infra_trials
    benign_exec_trials = sum(
        1 for diag in diagnostics.values() if diag["tool_state"] == "benign_exec" and not diag["infra_issue"]
    )
    no_tool_call_trials = sum(
        1 for diag in diagnostics.values() if diag["tool_state"] == "no_tool_call" and not diag["infra_issue"]
    )
    executable_trials = benign_exec_trials + sum(1 for row in rows if row["success"])
    latent_infra_negatives = [
        row
        for row in rows
        if diagnostics[row["trial_id"]]["infra_issue"] and row["evidence_mode"] != "infra_error"
    ]

    lines = [
        "# P25R Runtime Summary",
        "",
        "## Overview",
        "",
        f"- completed trials: `{len(rows)}`",
        f"- completed cases touched: `{completed_cases}`",
        f"- trials with infrastructure issues: `{infra_trials}`",
        f"- usable trials after infra filtering: `{usable_trials}`",
        f"- no-tool-call trials (usable only): `{no_tool_call_trials}`",
        f"- benign tool-execution trials (usable only): `{benign_exec_trials}`",
        f"- executable trials with any lab interaction: `{executable_trials}`",
        f"- successful dangerous-side-effect trials: `{sum(1 for row in rows if row['success'])}`",
        f"- `AConC` violations: `{sum(1 for row in rows if row['aconc_violation'])}`",
        "",
        "## By Arm",
        "",
        "| Arm | Completed | Infra | No Tool Calls | Benign Exec | Dangerous Exec | AConC Violations | Evidence Modes |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for row in arm_summary:
        evidence = ", ".join(f"{key}={value}" for key, value in row["evidence_modes"].items())
        lines.append(
            f"| `{row['arm']}` | {row['completed']} | {row['infra_errors']} | {row['no_tool_calls']} | {row['benign_exec']} | {row['successes']} | {row['aconc_violations']} | {evidence or '-'} |"
        )

    lines.extend(
        [
            "",
            "## By Family",
            "",
            "| Family | Completed | Infra | No Tool Calls | Benign Exec | Dangerous Exec | AConC Violations |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for row in family_summary:
        lines.append(
            f"| `{row['family']}` | {row['completed']} | {row['infra_errors']} | {row['no_tool_calls']} | {row['benign_exec']} | {row['successes']} | {row['aconc_violations']} |"
        )

    lines.extend(
        [
            "",
            "## Infrastructure Issues",
            "",
        ]
    )
    if infra_counter:
        for issue, count in infra_counter.most_common():
            lines.append(f"- `{issue}`: `{count}`")
    else:
        lines.append("- none detected")

    lines.extend(
        [
            "",
            "## Likely Misclassified Negatives",
            "",
        ]
    )
    if latent_infra_negatives:
        for row in latent_infra_negatives[:30]:
            issue = diagnostics[row["trial_id"]]["infra_issue"]
            lines.append(f"- `{row['trial_id']}`: likely infra-limited by `{issue}`")
    else:
        lines.append("- none detected")

    lines.extend(
        [
            "",
            "## Direct AConC Positives",
            "",
        ]
    )
    if direct_cases:
        lines.extend(
            [
                "| Case | Family | Source | Sink | Detail |",
                "| --- | --- | --- | --- | --- |",
            ]
        )
        for row in direct_cases[:30]:
            lines.append(
                f"| `{row['case_id']}` | `{row['family']}` | `{row['source_skill_id']}` | `{row['sink_skill_id']}` | `{row['detail']}` |"
            )
    else:
        lines.append("- none yet")

    lines.extend(
        [
            "",
            "## Guard Bypasses",
            "",
        ]
    )
    if guard_bypasses:
        for row in guard_bypasses[:30]:
            lines.append(f"- `{row['trial_id']}`: `{row['detail']}`")
    else:
        lines.append("- none yet")

    lines.extend(
        [
            "",
            "## Relay Positives",
            "",
        ]
    )
    if relay_cases:
        for row in relay_cases[:30]:
            lines.append(f"- `{row['trial_id']}`: `{row['detail']}`")
    else:
        lines.append("- none yet")

    return "\n".join(lines) + "\n"


def main() -> int:
    args = parse_args()
    rows = read_rows(Path(args.results))
    markdown = build_markdown(rows)
    if args.output:
        Path(args.output).write_text(markdown, encoding="utf-8")
    print(markdown, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
