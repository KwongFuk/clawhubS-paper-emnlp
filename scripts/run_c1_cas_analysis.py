#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
import math
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
import sys


EXPERIMENT_ROOT = Path(__file__).resolve().parents[2]
PROJECT_ROOT = EXPERIMENT_ROOT.parents[1]
SRC_DIR = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_DIR))

from clawhub_analyzer.hypergraph import HypergraphSkillProfile, load_hypergraph_skill_profiles  # noqa: E402
from clawhub_analyzer.p25r_protocol import SUPPORTED_SINKS  # noqa: E402


IN_CSV = EXPERIMENT_ROOT / "data/analysis_bcr_cis_v3/bcr_cis_v3_pairs.csv"
DB_PATH = PROJECT_ROOT / "data/experiment1_split/merged.sqlite3"
OUT_DIR = EXPERIMENT_ROOT / "data/analysis_c1_static"
OUT_CSV = OUT_DIR / "c1_cas_pairs.csv"
OUT_SUMMARY = OUT_DIR / "c1_cas_summary.md"
BASE_FIELDS = [
    "source_skill_id",
    "source_name",
    "sink_skill_id",
    "sink_name",
    "carrier_type",
    "source_type",
    "sink_type",
    "source_domain",
    "sink_domain",
    "cis_sink_severity",
    "cis_domain_crossing",
    "cis_tier",
    "ph_score",
    "channel_confidence",
    "source_channel_support_count",
    "sink_channel_support_count",
    "source_channel_support_signals",
    "sink_channel_support_signals",
    "is_new_arrival",
    "pair_recency_days",
    "source_installs_all_time",
    "sink_installs_all_time",
    "shared_clusters",
    "plausibility_score",
    "ecological_pass",
    "ecological_reason",
    "shared_channels",
    "shared_objects",
    "source_downloads",
    "sink_downloads",
]
CAS_FIELDS = [
    "source_singleton_role",
    "sink_singleton_hazard",
    "coinstall_surface",
    "joint_invocation_surface",
    "composition_trigger_type",
    "singleton_safety_plausibility",
    "composition_support_score",
    "runnable_family",
    "c1_dynamic_candidate",
    "c1_exclusion_reason",
]


@dataclass(frozen=True)
class SkillSignals:
    skill_id: str
    author_handle: str | None
    primary_source_type: str
    primary_domain: str
    authority_surface: float
    has_dangerous_sink: bool
    source_count: int
    sink_count: int
    has_browser_access: bool
    has_local_file_access: bool
    has_shell_access: bool


def signals_from_profile(profile: HypergraphSkillProfile) -> SkillSignals:
    return SkillSignals(
        skill_id=profile.skill_id,
        author_handle=profile.author_handle,
        primary_source_type=profile.primary_source_type(),
        primary_domain=profile.primary_domain(),
        authority_surface=profile.authority_surface(),
        has_dangerous_sink=bool(profile.sink_nodes),
        source_count=len(profile.source_nodes),
        sink_count=len(profile.sink_nodes),
        has_browser_access=bool(profile.hint_flags.get("requires_browser_access")),
        has_local_file_access=bool(profile.hint_flags.get("requires_local_file_access")),
        has_shell_access=bool(profile.hint_flags.get("requires_shell_access")),
    )


def classify_source_singleton_role(signal: SkillSignals) -> str:
    if not signal.has_dangerous_sink:
        return "bridge_only"
    if signal.primary_source_type == "contaminated_state" and signal.authority_surface <= 0.25:
        return "bridge_only"
    if signal.primary_source_type in {"contaminated_state", "sensitive_read"} and signal.authority_surface <= 0.45:
        return "mixed"
    return "self_acting"


def classify_sink_singleton_hazard(signal: SkillSignals) -> str:
    if signal.source_count == 0 and signal.authority_surface <= 0.55:
        return "likely_safe_alone"
    if signal.primary_source_type in {"untrusted_input", "contaminated_state"} and signal.authority_surface >= 0.65:
        return "likely_unsafe_alone"
    if (signal.has_shell_access or signal.has_local_file_access) and signal.authority_surface >= 0.55:
        return "likely_unsafe_alone"
    if signal.primary_source_type in {"contaminated_state", "sensitive_read"} and signal.authority_surface <= 0.45:
        return "likely_safe_alone"
    return "uncertain"


def infer_coinstall_surface(carrier_type: str, source: SkillSignals, sink: SkillSignals) -> str:
    if carrier_type in {"workspace", "memory", "transform_buffer", "credential_state"}:
        return carrier_type
    if source.has_browser_access and sink.has_browser_access:
        return "session_context"
    if source.primary_domain == sink.primary_domain == "AgentWorkspace":
        return "tool_discovery"
    return "session_context"


def infer_joint_invocation_surface(carrier_type: str, source: SkillSignals, sink: SkillSignals) -> str:
    if carrier_type in {"workspace", "memory", "transform_buffer"}:
        return "same_turn_possible"
    if carrier_type in {"credential_state", "browser_state"}:
        return "parallel_possible"
    if source.has_browser_access and sink.has_browser_access:
        return "parallel_possible"
    return "sequential_only"


def infer_composition_trigger_type(coinstall_surface: str, joint_surface: str) -> str:
    if joint_surface in {"same_turn_possible", "parallel_possible"} and coinstall_surface in {"tool_discovery", "session_context"}:
        return "mixed"
    if joint_surface == "same_turn_possible":
        return "same_turn"
    if joint_surface == "parallel_possible":
        return "same_turn"
    if coinstall_surface in {"tool_discovery", "session_context"}:
        return "coinstall_only"
    return "sequential"


def singleton_safety_plausibility(
    *,
    source_role: str,
    sink_hazard: str,
    channel_confidence: float,
    shared_clusters: int,
    source_installs: int,
) -> float:
    source_component = {"bridge_only": 0.45, "mixed": 0.25, "self_acting": 0.0}[source_role]
    sink_component = {"likely_safe_alone": 0.35, "uncertain": 0.18, "likely_unsafe_alone": 0.0}[sink_hazard]
    cluster_component = min(shared_clusters, 4) / 4.0 * 0.10
    confidence_component = min(max(channel_confidence, 0.0), 1.0) * 0.10
    install_component = min(1.0, math.log1p(max(source_installs, 0)) / 8.0) * 0.10
    return round(min(1.0, source_component + sink_component + cluster_component + confidence_component + install_component), 4)


def composition_support_score(
    *,
    singleton_score: float,
    composition_trigger_type: str,
    coinstall_surface: str,
    channel_confidence: float,
    shared_clusters: int,
    ecological_pass: bool,
) -> float:
    trigger_component = {
        "same_turn": 0.28,
        "mixed": 0.25,
        "sequential": 0.18,
        "coinstall_only": 0.14,
    }[composition_trigger_type]
    surface_component = 0.12 if coinstall_surface in {"workspace", "memory", "tool_discovery", "session_context"} else 0.08
    confidence_component = min(max(channel_confidence, 0.0), 1.0) * 0.15
    cluster_component = min(shared_clusters, 4) / 4.0 * 0.10
    ecology_component = 0.10 if ecological_pass else 0.0
    return round(min(1.0, singleton_score * 0.45 + trigger_component + surface_component + confidence_component + cluster_component + ecology_component), 4)


def c1_candidate_reason(
    *,
    source_role: str,
    sink_hazard: str,
    composition_support: float,
    singleton_score: float,
    supported_sink: bool,
) -> str:
    if not supported_sink:
        return "unsupported_sink"
    if source_role == "self_acting":
        return "source_self_acting"
    if sink_hazard == "likely_unsafe_alone":
        return "sink_likely_unsafe_alone"
    if singleton_score < 0.45:
        return "low_singleton_support"
    if composition_support < 0.55:
        return "low_composition_support"
    return ""


def main() -> int:
    if not IN_CSV.exists():
        raise FileNotFoundError(f"missing static input: {IN_CSV}")
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    profiles = load_hypergraph_skill_profiles(DB_PATH, progress_label="c1-cas")
    profile_by_id = {profile.skill_id: profile for profile in profiles}

    rows: list[dict[str, object]] = []
    missing_profiles = 0
    with IN_CSV.open(encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            src_profile = profile_by_id.get(row["source_skill_id"])
            sink_profile = profile_by_id.get(row["sink_skill_id"])
            if src_profile is None or sink_profile is None:
                missing_profiles += 1
                continue

            source_signal = signals_from_profile(src_profile)
            sink_signal = signals_from_profile(sink_profile)
            source_role = classify_source_singleton_role(source_signal)
            sink_hazard = classify_sink_singleton_hazard(sink_signal)
            carrier_type = row["carrier_type"]
            coinstall_surface = infer_coinstall_surface(carrier_type, source_signal, sink_signal)
            joint_surface = infer_joint_invocation_surface(carrier_type, source_signal, sink_signal)
            trigger_type = infer_composition_trigger_type(coinstall_surface, joint_surface)
            singleton_score = singleton_safety_plausibility(
                source_role=source_role,
                sink_hazard=sink_hazard,
                channel_confidence=float(row["channel_confidence"]),
                shared_clusters=int(row["shared_clusters"]),
                source_installs=int(row["source_installs_all_time"]),
            )
            support_score = composition_support_score(
                singleton_score=singleton_score,
                composition_trigger_type=trigger_type,
                coinstall_surface=coinstall_surface,
                channel_confidence=float(row["channel_confidence"]),
                shared_clusters=int(row["shared_clusters"]),
                ecological_pass=(row["ecological_pass"] == "True"),
            )
            sink_spec = SUPPORTED_SINKS.get(row["sink_skill_id"])
            runnable_family = sink_spec["family"] if sink_spec else ""
            reason = c1_candidate_reason(
                source_role=source_role,
                sink_hazard=sink_hazard,
                composition_support=support_score,
                singleton_score=singleton_score,
                supported_sink=sink_spec is not None,
            )
            rows.append(
                {
                    **row,
                    "source_singleton_role": source_role,
                    "sink_singleton_hazard": sink_hazard,
                    "coinstall_surface": coinstall_surface,
                    "joint_invocation_surface": joint_surface,
                    "composition_trigger_type": trigger_type,
                    "singleton_safety_plausibility": singleton_score,
                    "composition_support_score": support_score,
                    "runnable_family": runnable_family,
                    "c1_dynamic_candidate": reason == "",
                    "c1_exclusion_reason": reason,
                }
            )

    rows.sort(
        key=lambda item: (
            not bool(item["c1_dynamic_candidate"]),
            -float(item["composition_support_score"]),
            -float(item["singleton_safety_plausibility"]),
            -float(item["channel_confidence"]),
            -float(item["ph_score"]),
            str(item["sink_skill_id"]),
            str(item["source_skill_id"]),
        )
    )

    with OUT_CSV.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=BASE_FIELDS + CAS_FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    candidate_rows = [row for row in rows if row["c1_dynamic_candidate"]]
    by_trigger = Counter(str(row["composition_trigger_type"]) for row in candidate_rows)
    by_family = Counter(str(row["runnable_family"]) for row in candidate_rows if row["runnable_family"])
    by_exclusion = Counter(str(row["c1_exclusion_reason"]) for row in rows if row["c1_exclusion_reason"])
    by_role = Counter(str(row["source_singleton_role"]) for row in rows)
    by_hazard = Counter(str(row["sink_singleton_hazard"]) for row in rows)

    lines = [
        "# C1 CAS Summary",
        "",
        "> Source: latest `BCR+CIS v3` pair table with profile-backed composition-only heuristics.",
        "",
        "## Overview",
        "",
        f"- input pairs scanned: `{len(rows)}`",
        f"- dynamic candidates retained: `{len(candidate_rows)}`",
        f"- missing profiles skipped: `{missing_profiles}`",
        "",
        "## Source Singleton Role",
        "",
        "| Role | Count |",
        "| --- | ---: |",
    ]
    for role, count in sorted(by_role.items()):
        lines.append(f"| `{role}` | {count} |")
    lines.extend(
        [
            "",
            "## Sink Singleton Hazard",
            "",
            "| Hazard | Count |",
            "| --- | ---: |",
        ]
    )
    for hazard, count in sorted(by_hazard.items()):
        lines.append(f"| `{hazard}` | {count} |")
    lines.extend(
        [
            "",
            "## Candidate Composition Trigger Types",
            "",
            "| Trigger | Count |",
            "| --- | ---: |",
        ]
    )
    for trigger, count in sorted(by_trigger.items()):
        lines.append(f"| `{trigger}` | {count} |")
    lines.extend(
        [
            "",
            "## Candidate Runnable Families",
            "",
            "| Family | Count |",
            "| --- | ---: |",
        ]
    )
    for family, count in sorted(by_family.items()):
        lines.append(f"| `{family}` | {count} |")
    lines.extend(
        [
            "",
            "## Exclusion Reasons",
            "",
            "| Reason | Count |",
            "| --- | ---: |",
        ]
    )
    for reason, count in sorted(by_exclusion.items()):
        lines.append(f"| `{reason}` | {count} |")

    OUT_SUMMARY.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "scanned_pairs": len(rows),
                "dynamic_candidates": len(candidate_rows),
                "cas_csv": str(OUT_CSV),
                "summary": str(OUT_SUMMARY),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
