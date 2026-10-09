"""SC runtime seed-pool selection from stateful pair extraction."""

from __future__ import annotations

from collections import Counter, defaultdict
import csv
import json
from pathlib import Path


DEFAULT_INPUT_CSV = Path("data/experiment1_split/analysis_sc_stateful/sc_stateful_pairs.csv")
DEFAULT_OUTPUT_DIR = Path("data/experiment1_split/analysis_sc_stateful")

DEFAULT_FAMILY_QUOTAS = {
    "booking_create": 6,
    "order_submit": 4,
    "workspace_reseller": 6,
    "device_control": 12,
}

MAX_PER_SOURCE = 1
MAX_PER_SINK = 10
MAX_PER_CLASSIFICATION = 8


def priority(row: dict[str, str]) -> tuple[int, int, int, int, float, float, str, str]:
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
        "device_signal": 1,
        "memory": 2,
        "session_context": 3,
        "credential_state": 4,
    }.get(row.get("primary_surface", ""), 9)
    role_rank = {"bridge_only": 0, "mixed": 1, "self_acting": 2}.get(row.get("source_singleton_role", ""), 9)
    hazard_rank = {
        "likely_safe_alone": 0,
        "uncertain": 1,
        "likely_unsafe_alone": 2,
    }.get(row.get("sink_singleton_hazard", ""), 9)
    return (
        trigger_rank,
        surface_rank,
        role_rank,
        hazard_rank,
        -float(row.get("chain_confidence", 0) or 0),
        -float(row.get("composition_support_score", 0) or 0),
        row.get("sink_skill_id", ""),
        row.get("source_skill_id", ""),
    )


def row_identity(row: dict[str, str]) -> tuple[str, str, str, str]:
    return (
        row["source_skill_id"],
        row["sink_skill_id"],
        row.get("trigger_kind", ""),
        row.get("primary_surface", ""),
    )


def select_seed_pool(
    rows: list[dict[str, str]],
    *,
    family_quotas: dict[str, int] | None = None,
) -> tuple[list[dict[str, str]], dict[str, object]]:
    quotas = dict(family_quotas or DEFAULT_FAMILY_QUOTAS)
    rows_by_family: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        rows_by_family[row.get("runnable_family", "")].append(row)

    selected: list[dict[str, str]] = []
    selected_ids: set[tuple[str, str, str, str]] = set()
    per_source: Counter[str] = Counter()
    per_sink: Counter[str] = Counter()
    per_classification: Counter[str] = Counter()
    audit: dict[str, object] = {
        "requested": quotas,
        "per_family": {},
        "rejections": Counter(),
        "shortfalls": {},
    }

    for family, quota in quotas.items():
        picked = 0
        family_rejections: Counter[str] = Counter()
        for row in sorted(rows_by_family.get(family, []), key=priority):
            if picked >= quota:
                break
            identity = row_identity(row)
            if identity in selected_ids:
                continue
            source_id = row["source_skill_id"]
            sink_id = row["sink_skill_id"]
            classification = row.get("stateful_pair_classification", "")
            if per_source[source_id] >= MAX_PER_SOURCE:
                family_rejections["source_cap"] += 1
                audit["rejections"]["source_cap"] += 1
                continue
            if per_sink[sink_id] >= MAX_PER_SINK:
                family_rejections["sink_cap"] += 1
                audit["rejections"]["sink_cap"] += 1
                continue
            if classification and per_classification[classification] >= MAX_PER_CLASSIFICATION:
                family_rejections["classification_cap"] += 1
                audit["rejections"]["classification_cap"] += 1
                continue
            selected.append(row)
            selected_ids.add(identity)
            per_source[source_id] += 1
            per_sink[sink_id] += 1
            if classification:
                per_classification[classification] += 1
            picked += 1
        audit["per_family"][family] = {"selected": picked, "rejections": dict(family_rejections)}
        if picked < quota:
            audit["shortfalls"][family] = quota - picked

    audit["rejections"] = dict(audit["rejections"])
    return sorted(selected, key=priority), audit


def write_seed_pool_csv(path: Path, rows: list[dict[str, str]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_seed_pool_summary(path: Path, rows: list[dict[str, str]], audit: dict[str, object]) -> None:
    by_family = Counter(row["runnable_family"] for row in rows)
    by_trigger = Counter(row["trigger_kind"] for row in rows)
    by_surface = Counter(row["primary_surface"] for row in rows)
    by_class = Counter(row["stateful_pair_classification"] for row in rows)
    by_conf = Counter(
        "very_high" if float(row["chain_confidence"]) >= 0.85 else "high" if float(row["chain_confidence"]) >= 0.70 else "medium"
        for row in rows
    )

    lines = [
        "# SC Runtime Seed Pool",
        "",
        "> Source: `analysis_sc_stateful/sc_stateful_pairs.csv` filtered to `c1_dynamic_candidate=True` and selected with SC diversity caps.",
        "",
        "## Overview",
        "",
        f"- selected rows: `{len(rows)}`",
        f"- requested quotas: `{json.dumps(audit['requested'], ensure_ascii=False)}`",
        f"- shortfalls: `{json.dumps(audit['shortfalls'], ensure_ascii=False)}`",
        f"- rejections: `{json.dumps(audit['rejections'], ensure_ascii=False)}`",
        "",
    ]

    def add_table(title: str, values: Counter[str]) -> None:
        lines.extend([f"## {title}", "", "| Value | Count |", "| --- | ---: |"])
        for key, count in sorted(values.items(), key=lambda item: item[1], reverse=True):
            lines.append(f"| `{key}` | {count} |")
        lines.append("")

    add_table("By Family", by_family)
    add_table("By Trigger Kind", by_trigger)
    add_table("By Surface", by_surface)
    add_table("By Stateful Classification", by_class)
    add_table("By Confidence Band", by_conf)

    lines.extend(["## Selected Pairs", "", "| Rank | Source | Sink | Family | Trigger | Surface | Confidence |", "| ---: | --- | --- | --- | --- | --- | ---: |"])
    for index, row in enumerate(rows, start=1):
        lines.append(
            f"| {index} | `{row['source_skill_id']}` | `{row['sink_skill_id']}` | "
            f"`{row['runnable_family']}` | `{row['trigger_kind']}` | `{row['primary_surface']}` | {float(row['chain_confidence']):.4f} |"
        )

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def generate_sc_seed_pool_artifacts(
    *,
    input_csv: Path = DEFAULT_INPUT_CSV,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    family_quotas: dict[str, int] | None = None,
) -> dict[str, Path]:
    if not input_csv.exists():
        raise FileNotFoundError(f"missing SC stateful pair table: {input_csv}")

    with input_csv.open(encoding="utf-8") as handle:
        all_rows = list(csv.DictReader(handle))
    if not all_rows:
        raise ValueError(f"no rows found in {input_csv}")

    candidate_rows = [row for row in all_rows if row.get("c1_dynamic_candidate") == "True" and row.get("runnable_family")]
    selected, audit = select_seed_pool(candidate_rows, family_quotas=family_quotas)

    output_dir.mkdir(parents=True, exist_ok=True)
    seed_csv = output_dir / "sc_runtime_seed_pool.csv"
    seed_summary = output_dir / "sc_runtime_seed_pool_summary.md"
    seed_audit = output_dir / "sc_runtime_seed_pool_audit.json"

    write_seed_pool_csv(seed_csv, selected, list(all_rows[0].keys()))
    write_seed_pool_summary(seed_summary, selected, audit)
    seed_audit.write_text(json.dumps(audit, indent=2, ensure_ascii=False), encoding="utf-8")

    return {
        "sc_runtime_seed_pool_csv": seed_csv,
        "sc_runtime_seed_pool_summary_markdown": seed_summary,
        "sc_runtime_seed_pool_audit_json": seed_audit,
    }

