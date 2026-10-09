"""Adaptive SC runtime sampling with reviewer-safe coverage constraints."""

from __future__ import annotations

from collections import Counter
import csv
import json
import math
from pathlib import Path

from clawhub_analyzer.p25r_protocol import source_author


DEFAULT_INPUT_CSV = Path("data/experiment1_split/analysis_sc_stateful/sc_candidate_universe_from_p25r.csv")
DEFAULT_OUTPUT_DIR = Path("data/experiment1_split/analysis_sc_stateful")


def row_priority(row: dict[str, str]) -> tuple[int, int, int, int, int, float, float, float, str, str]:
    band_rank = {"main": 0, "expansion": 1, "reject": 2}.get(row.get("sc_candidate_band", ""), 9)
    trigger_rank = {
        "planner_selection": 0,
        "threshold_rule": 1,
        "artifact_consumption": 2,
        "memory_recall": 3,
        "same_turn_context_reuse": 4,
        "credential_carryover": 5,
        "auto_rule": 6,
        "session_reuse": 7,
    }.get(row.get("trigger_kind", ""), 9)
    surface_rank = {
        "workspace": 0,
        "memory": 1,
        "device_signal": 2,
        "session_context": 3,
        "credential_state": 4,
        "browser_state": 5,
    }.get(row.get("primary_surface", ""), 9)
    role_rank = {"bridge_only": 0, "mixed": 1, "self_acting": 2}.get(row.get("source_singleton_role", ""), 9)
    hazard_rank = {"likely_safe_alone": 0, "uncertain": 1, "likely_unsafe_alone": 2}.get(row.get("sink_singleton_hazard", ""), 9)
    return (
        band_rank,
        trigger_rank,
        surface_rank,
        role_rank,
        hazard_rank,
        -float(row.get("chain_confidence", 0) or 0),
        -float(row.get("composition_support_score", 0) or 0),
        -float(row.get("singleton_safety_plausibility", 0) or 0),
        row.get("sink_skill_id", ""),
        row.get("source_skill_id", ""),
    )


def marginal_gain(row: dict[str, str], coverage: dict[str, set[str]]) -> int:
    gain = 0
    if row["runnable_family"] not in coverage["family"]:
        gain += 100
    if row["trigger_kind"] not in coverage["trigger"]:
        gain += 60
    if row["primary_surface"] not in coverage["surface"]:
        gain += 50
    if row["stateful_pair_classification"] not in coverage["classification"]:
        gain += 50
    signature = "|".join(
        [
            row["runnable_family"],
            row["trigger_kind"],
            row["primary_surface"],
            row["stateful_pair_classification"],
        ]
    )
    if signature not in coverage["signature"]:
        gain += 15
    return gain


def compute_family_targets(
    family_counts: Counter[str],
    *,
    target_size: int,
    min_cases: int,
) -> dict[str, int]:
    families = [family for family, count in family_counts.items() if count > 0]
    if not families:
        return {}
    floor = max(10, min_cases // max(2 * len(families), 1))
    weights = {family: math.sqrt(family_counts[family]) for family in families}
    total_weight = sum(weights.values()) or 1.0

    quotas: dict[str, int] = {}
    remainders: list[tuple[float, str]] = []
    assigned = 0
    for family in families:
        raw = target_size * weights[family] / total_weight
        quota = max(min(floor, family_counts[family]), int(raw))
        quota = min(quota, family_counts[family])
        quotas[family] = quota
        assigned += quota
        remainders.append((raw - int(raw), family))

    if assigned > target_size:
        # Trim from the largest quotas first while keeping each family above 1.
        for _, family in sorted(((quotas[f], f) for f in families), reverse=True):
            while assigned > target_size and quotas[family] > 1:
                quotas[family] -= 1
                assigned -= 1
    elif assigned < target_size:
        for _, family in sorted(remainders, reverse=True):
            while assigned < target_size and quotas[family] < family_counts[family]:
                quotas[family] += 1
                assigned += 1
                break
        while assigned < target_size:
            changed = False
            for family in sorted(families, key=lambda item: weights[item], reverse=True):
                if quotas[family] < family_counts[family]:
                    quotas[family] += 1
                    assigned += 1
                    changed = True
                    if assigned >= target_size:
                        break
            if not changed:
                break
    return quotas


def select_runtime_pool(
    rows: list[dict[str, str]],
    *,
    min_cases: int = 200,
    preferred_cases: int = 300,
    max_cases: int = 500,
) -> tuple[list[dict[str, str]], dict[str, object]]:
    eligible = [row for row in rows if row.get("sc_methodology_pass") == "True"]
    remaining = sorted(eligible, key=row_priority)
    selected: list[dict[str, str]] = []
    selected_ids: set[tuple[str, str, str, str]] = set()
    per_source: Counter[str] = Counter()
    per_author: Counter[str] = Counter()
    per_sink: Counter[str] = Counter()
    coverage = {"family": set(), "trigger": set(), "surface": set(), "classification": set(), "signature": set()}
    family_targets = compute_family_targets(
        Counter(row["runnable_family"] for row in eligible),
        target_size=preferred_cases,
        min_cases=min_cases,
    )

    zero_gain_streak = 0
    stop_reason = "exhausted_candidates"

    def identity(row: dict[str, str]) -> tuple[str, str, str, str]:
        return (
            row["source_skill_id"],
            row["sink_skill_id"],
            row["trigger_kind"],
            row["primary_surface"],
        )

    def best_candidate(*, family: str | None = None) -> int | None:
        best_index = None
        best_score = None
        for index, row in enumerate(remaining):
            if family is not None and row["runnable_family"] != family:
                continue
            row_id = identity(row)
            if row_id in selected_ids:
                continue
            if per_source[row["source_skill_id"]] >= 1:
                continue
            author = source_author(row["source_skill_id"])
            if per_author[author] >= 3:
                continue
            if per_sink[row["sink_skill_id"]] >= 120:
                continue
            score = (marginal_gain(row, coverage),) + tuple(-value if isinstance(value, float) else value for value in ())
            actual_score = (
                marginal_gain(row, coverage),
                -row_priority(row)[0],
                -row_priority(row)[1],
                -float(row.get("chain_confidence", 0) or 0),
                -float(row.get("composition_support_score", 0) or 0),
            )
            if best_score is None or actual_score > best_score:
                best_score = actual_score
                best_index = index
        return best_index

    # Phase A: satisfy family targets first to avoid large families dominating the pool.
    for family, quota in sorted(family_targets.items(), key=lambda item: item[1], reverse=True):
        while Counter(row["runnable_family"] for row in selected)[family] < quota and len(selected) < max_cases:
            best_index = best_candidate(family=family)
            if best_index is None:
                break
            row = remaining.pop(best_index)
            gain = marginal_gain(row, coverage)
            selected.append(row)
            selected_ids.add(identity(row))
            per_source[row["source_skill_id"]] += 1
            per_author[source_author(row["source_skill_id"])] += 1
            per_sink[row["sink_skill_id"]] += 1
            coverage["family"].add(row["runnable_family"])
            coverage["trigger"].add(row["trigger_kind"])
            coverage["surface"].add(row["primary_surface"])
            coverage["classification"].add(row["stateful_pair_classification"])
            coverage["signature"].add(
                "|".join(
                    [
                        row["runnable_family"],
                        row["trigger_kind"],
                        row["primary_surface"],
                        row["stateful_pair_classification"],
                    ]
                )
            )
            zero_gain_streak = 0 if gain > 0 else zero_gain_streak + 1

    # Phase B: global fill with coverage gain until preferred size.
    while remaining and len(selected) < max_cases:
        best_index = best_candidate()
        if best_index is None:
            stop_reason = "caps_exhausted"
            break

        row = remaining.pop(best_index)
        gain = marginal_gain(row, coverage)
        selected.append(row)
        selected_ids.add(identity(row))
        per_source[row["source_skill_id"]] += 1
        per_author[source_author(row["source_skill_id"])] += 1
        per_sink[row["sink_skill_id"]] += 1
        coverage["family"].add(row["runnable_family"])
        coverage["trigger"].add(row["trigger_kind"])
        coverage["surface"].add(row["primary_surface"])
        coverage["classification"].add(row["stateful_pair_classification"])
        coverage["signature"].add(
            "|".join(
                [
                    row["runnable_family"],
                    row["trigger_kind"],
                    row["primary_surface"],
                    row["stateful_pair_classification"],
                ]
            )
        )

        if gain <= 0:
            zero_gain_streak += 1
        else:
            zero_gain_streak = 0

        if len(selected) >= preferred_cases and zero_gain_streak >= 25:
            stop_reason = "preferred_reached_and_coverage_plateau"
            break

    if len(selected) >= max_cases and stop_reason == "exhausted_candidates":
        stop_reason = "max_cases_reached"
    elif len(selected) < min_cases and not remaining:
        stop_reason = "universe_below_minimum"

    audit = {
        "input_rows": len(rows),
        "min_cases": min_cases,
        "preferred_cases": preferred_cases,
        "max_cases": max_cases,
        "eligible_rows": len(eligible),
        "selected_rows": len(selected),
        "stop_reason": stop_reason,
        "coverage": {key: sorted(values) for key, values in coverage.items()},
        "per_family": dict(Counter(row["runnable_family"] for row in selected)),
        "per_trigger": dict(Counter(row["trigger_kind"] for row in selected)),
        "per_surface": dict(Counter(row["primary_surface"] for row in selected)),
        "per_classification": dict(Counter(row["stateful_pair_classification"] for row in selected)),
        "family_counts": dict(Counter(row["runnable_family"] for row in selected)),
        "trigger_counts": dict(Counter(row["trigger_kind"] for row in selected)),
        "surface_counts": dict(Counter(row["primary_surface"] for row in selected)),
        "mechanism_counts": dict(Counter(row["stateful_pair_classification"] for row in selected)),
    }
    return selected, audit


def generate_sc_runtime_sampling_artifacts(
    *,
    input_csv: Path = DEFAULT_INPUT_CSV,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    min_cases: int = 200,
    preferred_cases: int = 300,
    max_cases: int = 500,
) -> dict[str, Path]:
    if not input_csv.exists():
        raise FileNotFoundError(f"missing SC candidate universe: {input_csv}")

    with input_csv.open(encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise ValueError(f"no rows found in {input_csv}")

    selected, audit = select_runtime_pool(
        rows,
        min_cases=min_cases,
        preferred_cases=preferred_cases,
        max_cases=max_cases,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    pool_csv = output_dir / "sc_expansion_set_200_500.csv"
    summary_md = output_dir / "sc_sampling_summary.md"
    audit_json = output_dir / "sc_sampling_audit.json"

    with pool_csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(selected)

    lines = [
        "# SC Runtime Sampling Summary",
        "",
        "> Reviewer-safe sampling policy: filter the full runnable SC universe for singleton-safe, observable, and runnable cases; then stratify across family, trigger, surface, and mechanism within a target range of 200–500 cases.",
        "",
        "## Overview",
        "",
        f"- eligible universe: `{audit['eligible_rows']}`",
        f"- selected pool: `{audit['selected_rows']}`",
        f"- minimum target: `{audit['min_cases']}`",
        f"- preferred target: `{audit['preferred_cases']}`",
        f"- maximum cap: `{audit['max_cases']}`",
        f"- stop reason: `{audit['stop_reason']}`",
        "",
    ]

    def add_table(title: str, mapping: dict[str, int]) -> None:
        lines.extend([f"## {title}", "", "| Value | Count |", "| --- | ---: |"])
        for key, count in sorted(mapping.items(), key=lambda item: item[1], reverse=True):
            lines.append(f"| `{key}` | {count} |")
        lines.append("")

    add_table("By Family", audit["per_family"])
    add_table("By Trigger", audit["per_trigger"])
    add_table("By Surface", audit["per_surface"])
    add_table("By Classification", audit["per_classification"])

    summary_md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    audit_json.write_text(json.dumps(audit, indent=2, ensure_ascii=False), encoding="utf-8")
    return {
        "sc_expansion_set_csv": pool_csv,
        "sc_sampling_summary_markdown": summary_md,
        "sc_sampling_audit_json": audit_json,
    }
