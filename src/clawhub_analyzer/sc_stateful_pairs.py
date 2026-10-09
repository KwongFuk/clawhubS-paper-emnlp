"""SC stateful pair extraction for skill composition analysis."""

from __future__ import annotations

from collections import Counter
import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from clawhub_analyzer.graph2_pairs import Graph2SkillProfile, load_graph2_skill_profiles


DEFAULT_DB_PATH = Path("data/experiment1_split/merged.sqlite3")
DEFAULT_INPUT_CSV = Path(
    "experiments/graph_first_composition/data/analysis_c1_static/c1_cas_pairs.csv"
)
DEFAULT_OUTPUT_DIR = Path("data/experiment1_split/analysis_sc_stateful")

NEW_FIELDS = [
    "primary_surface",
    "primary_object",
    "bridge_state",
    "bridge_artifact",
    "trigger_kind",
    "trigger_condition",
    "trigger_style",
    "state_persistence",
    "sink_action",
    "forbidden_effect",
    "higher_level_conflict",
    "missing_guards",
    "stateful_pair_classification",
    "stateful_chain",
    "chain_confidence",
]

SURFACE_PRIORITY = {
    "workspace": 100,
    "memory": 95,
    "transform_buffer": 90,
    "credential_state": 85,
    "device_signal": 84,
    "browser_state": 80,
    "session_context": 78,
    "tool_discovery": 76,
    "desktop_state": 74,
    "notification_signal": 70,
    "popup_dialog": 68,
    "window_focus": 66,
    "screen_capture": 64,
    "audio_capture": 64,
    "video_capture": 64,
    "clipboard": 62,
    "time": 60,
    "power_state": 58,
}

OBJECT_PRIORITY = {
    "secret": 100,
    "private_content": 96,
    "ui_instruction": 90,
    "state_snapshot": 88,
    "observation_trace": 82,
    "runtime_signal": 78,
    "schedule_event": 68,
    "raw_content": 62,
}

ACTION_LABELS = {
    "transact": "submit_transaction_or_booking",
    "delete": "delete_or_remove_target",
    "control": "control_device_or_gui",
    "execute": "execute_command_or_script",
    "modify": "modify_configuration_or_state",
    "exfil": "send_or_exfiltrate_data",
    "publish": "publish_external_content",
}

FORBIDDEN_EFFECTS = {
    "transact": "unauthorized_transaction_submission",
    "delete": "unauthorized_deletion",
    "control": "unauthorized_device_or_gui_actuation",
    "execute": "unauthorized_command_execution",
    "modify": "unauthorized_state_modification",
    "exfil": "unauthorized_data_transfer",
    "publish": "unauthorized_external_publication",
}

FAMILY_FORBIDDEN_EFFECTS = {
    "booking_create": "unauthorized_booking_creation",
    "order_submit": "unauthorized_order_submission",
    "workspace_reseller": "unauthorized_subscription_change",
    "device_control": "unauthorized_device_actuation",
}

STATE_TEMPLATES = {
    ("workspace", "private_content"): ("workspace_private_context_ready", "workspace artifact", "workspace_persistent"),
    ("workspace", "state_snapshot"): ("workspace_state_snapshot_ready", "workspace state file", "workspace_persistent"),
    ("workspace", "ui_instruction"): ("workspace_actionable_instruction", "workspace instruction artifact", "workspace_persistent"),
    ("workspace", "raw_content"): ("workspace_raw_content_ready", "workspace content artifact", "workspace_persistent"),
    ("memory", "private_content"): ("memory_private_context_ready", "memory entry", "cross_run_persistent"),
    ("memory", "observation_trace"): ("memory_observation_ready", "memory trace", "cross_run_persistent"),
    ("memory", "state_snapshot"): ("memory_state_snapshot_ready", "memory state record", "cross_run_persistent"),
    ("memory", "runtime_signal"): ("memory_runtime_signal_ready", "memory signal record", "cross_run_persistent"),
    ("transform_buffer", "raw_content"): ("transformed_intermediate_ready", "transform buffer artifact", "ephemeral"),
    ("transform_buffer", "private_content"): ("transformed_private_context_ready", "transform buffer artifact", "ephemeral"),
    ("credential_state", "secret"): ("credential_authority_context_ready", "credential state", "session_persistent"),
    ("credential_state", "private_content"): ("credential_backed_private_context_ready", "credential-backed state", "session_persistent"),
    ("browser_state", "ui_instruction"): ("browser_ui_state_ready", "browser runtime state", "session_persistent"),
    ("browser_state", "state_snapshot"): ("browser_state_snapshot_ready", "browser state snapshot", "session_persistent"),
    ("device_signal", "runtime_signal"): ("device_runtime_signal_ready", "device runtime state", "session_persistent"),
    ("device_signal", "ui_instruction"): ("device_instruction_ready", "device instruction state", "session_persistent"),
    ("audio_capture", "raw_content"): ("audio_transcript_ready", "captured transcript", "ephemeral"),
    ("video_capture", "raw_content"): ("video_observation_ready", "visual observation record", "ephemeral"),
    ("session_context", "raw_content"): ("session_decision_context_ready", "session context", "ephemeral"),
    ("tool_discovery", "raw_content"): ("tool_selection_context_ready", "planner tool context", "ephemeral"),
}


@dataclass(frozen=True)
class StatefulExtractionSummary:
    total_pairs: int
    dynamic_candidates: int
    trigger_style_counts: dict[str, int]
    trigger_kind_counts: dict[str, int]
    surface_counts: dict[str, int]
    action_counts: dict[str, int]
    conflict_counts: dict[str, int]
    confidence_band_counts: dict[str, int]


def _split_nodes(value: str) -> tuple[str, ...]:
    if not value:
        return ()
    return tuple(part.strip() for part in value.split("|") if part.strip())


def _strip_prefix(value: str) -> str:
    return value.split(":", 1)[1] if ":" in value else value


def choose_primary_surface(row: dict[str, str], source: Graph2SkillProfile, sink: Graph2SkillProfile) -> str:
    candidates: list[str] = []
    candidates.append(row.get("coinstall_surface", ""))
    candidates.append(row.get("carrier_type", ""))
    candidates.extend(_strip_prefix(item) for item in _split_nodes(row.get("shared_channels", "")))
    shared = set(source.channels) & set(sink.channels)
    candidates.extend(shared)
    filtered = [item for item in candidates if item]
    if not filtered:
        return "session_context"
    return sorted(filtered, key=lambda item: SURFACE_PRIORITY.get(item, 0), reverse=True)[0]


def choose_primary_object(row: dict[str, str], source: Graph2SkillProfile, sink: Graph2SkillProfile) -> str:
    candidates: list[str] = []
    candidates.extend(_strip_prefix(item) for item in _split_nodes(row.get("shared_objects", "")))
    shared = set(source.output_objects) & set(sink.input_objects)
    candidates.extend(shared)
    filtered = [item for item in candidates if item]
    if not filtered:
        if source.output_objects:
            filtered.extend(source.output_objects)
        elif sink.input_objects:
            filtered.extend(sink.input_objects)
    if not filtered:
        return "raw_content"
    return sorted(filtered, key=lambda item: OBJECT_PRIORITY.get(item, 0), reverse=True)[0]


def infer_bridge_state(surface: str, primary_object: str, source_type: str) -> tuple[str, str, str]:
    template = STATE_TEMPLATES.get((surface, primary_object))
    if template is not None:
        return template
    object_core = primary_object or "content"
    artifact = {
        "workspace": "workspace artifact",
        "memory": "memory entry",
        "transform_buffer": "transform buffer artifact",
        "credential_state": "credential-backed context",
        "browser_state": "browser runtime state",
        "device_signal": "device state",
        "session_context": "session context",
        "tool_discovery": "tool selection context",
    }.get(surface, f"{surface} artifact")
    persistence = {
        "workspace": "workspace_persistent",
        "memory": "cross_run_persistent",
        "transform_buffer": "ephemeral",
        "credential_state": "session_persistent",
        "browser_state": "session_persistent",
        "device_signal": "session_persistent",
        "session_context": "ephemeral",
        "tool_discovery": "ephemeral",
    }.get(surface, "session_persistent")
    state = f"{surface}_{object_core}_{source_type}_ready"
    return state, artifact, persistence


def infer_trigger_style(row: dict[str, str], surface: str) -> str:
    trigger_type = row.get("composition_trigger_type", "")
    joint_surface = row.get("joint_invocation_surface", "")
    if trigger_type == "mixed":
        return "hybrid"
    if trigger_type == "same_turn" or joint_surface in {"same_turn_possible", "parallel_possible"}:
        return "automatic"
    if surface in {"workspace", "memory", "credential_state"}:
        return "passive"
    if surface in {"tool_discovery", "session_context", "device_signal", "browser_state"}:
        return "automatic"
    return "passive"


def infer_trigger_kind(surface: str, trigger_style: str, sink_action: str) -> str:
    if surface == "tool_discovery":
        return "planner_selection"
    if surface == "session_context":
        return "same_turn_context_reuse"
    if surface == "memory":
        return "memory_recall"
    if surface == "workspace":
        return "artifact_consumption"
    if surface == "credential_state":
        return "credential_carryover"
    if surface == "browser_state":
        return "session_reuse"
    if surface == "device_signal" and sink_action == "control_device_or_gui":
        return "threshold_rule"
    if trigger_style == "automatic":
        return "auto_rule"
    return "artifact_consumption"


def infer_sink_action(row: dict[str, str], sink: Graph2SkillProfile) -> str:
    sink_type = row.get("sink_type", "")
    if sink_type in ACTION_LABELS:
        return ACTION_LABELS[sink_type]
    if sink.sink_actions:
        action = sorted(sink.sink_actions, key=lambda item: ACTION_LABELS.get(item, item))[0]
        return ACTION_LABELS.get(action, action)
    return "perform_high_impact_action"


def infer_forbidden_effect(row: dict[str, str]) -> str:
    family = row.get("runnable_family", "")
    if family in FAMILY_FORBIDDEN_EFFECTS:
        return FAMILY_FORBIDDEN_EFFECTS[family]
    sink_type = row.get("sink_type", "")
    return FORBIDDEN_EFFECTS.get(sink_type, "unauthorized_high_impact_effect")


def infer_higher_level_conflict(row: dict[str, str]) -> str:
    family = row.get("runnable_family", "")
    sink_type = row.get("sink_type", "")
    if family == "device_control" or sink_type == "control":
        return "physical_or_operational_safety"
    if family in {"booking_create", "order_submit", "workspace_reseller"} or sink_type == "transact":
        return "financial_or_account_authorization"
    if sink_type == "delete":
        return "data_integrity"
    if sink_type == "modify":
        return "configuration_integrity"
    if sink_type == "execute":
        return "system_execution_integrity"
    if sink_type in {"exfil", "publish"}:
        return "data_confidentiality"
    return "authorization_boundary"


def infer_missing_guards(row: dict[str, str], source: Graph2SkillProfile, sink: Graph2SkillProfile, surface: str) -> tuple[str, ...]:
    guards: list[str] = []
    gates = set(sink.mediation_gates)
    sink_type = row.get("sink_type", "")

    if sink_type in {"transact", "delete", "control", "execute", "modify", "publish"} and "Auth" not in gates:
        guards.append("explicit_approval")
    if surface in {"workspace", "memory", "transform_buffer", "session_context"} and "Validate" not in gates:
        guards.append("artifact_provenance_validation")
    if any(label in {"Sensitive", "Tainted"} for label in source.source_labels) and "Declassify" not in gates:
        guards.append("taint_or_declassification_check")
    if sink_type in {"control", "execute", "delete", "modify"} and "Policy" not in gates:
        guards.append("execution_policy")
    if row.get("runnable_family", "") == "device_control":
        guards.append("physical_safety_interlock")
    return tuple(dict.fromkeys(guards))


def build_trigger_condition(trigger_kind: str, trigger_style: str, bridge_state: str, sink_action: str, primary_surface: str) -> str:
    if trigger_kind == "planner_selection":
        return f"agent planner sees `{bridge_state}` in the {primary_surface} context and auto-selects the sink to {sink_action}"
    if trigger_kind == "same_turn_context_reuse":
        return f"sink receives `{bridge_state}` in the same turn and treats it as sufficient context to {sink_action}"
    if trigger_kind == "memory_recall":
        return f"sink recalls `{bridge_state}` from memory and treats it as durable authority to {sink_action}"
    if trigger_kind == "credential_carryover":
        return f"sink inherits `{bridge_state}` from credential state and proceeds to {sink_action}"
    if trigger_kind == "session_reuse":
        return f"sink reuses browser/session state `{bridge_state}` and proceeds to {sink_action}"
    if trigger_kind == "threshold_rule":
        return f"sink's control rule treats `{bridge_state}` as a triggering runtime condition and auto-executes `{sink_action}`"
    if trigger_style == "automatic":
        return f"once `{bridge_state}` appears on {primary_surface}, sink auto-executes `{sink_action}`"
    return f"sink later consumes `{bridge_state}` from {primary_surface} and interprets it as actionable basis to `{sink_action}`"


def classify_stateful_pair(surface: str, trigger_kind: str, sink_type: str) -> str:
    if trigger_kind == "planner_selection":
        return "same_turn_planner_chain"
    if trigger_kind in {"memory_recall", "artifact_consumption"} and surface in {"workspace", "memory"}:
        return "persisted_artifact_chain"
    if trigger_kind == "threshold_rule" or surface == "device_signal":
        return "device_rule_chain"
    if sink_type == "transact":
        return "authorization_confusion_chain"
    if sink_type in {"delete", "modify"}:
        return "state_replay_chain"
    if sink_type in {"execute", "control"}:
        return "action_escalation_chain"
    return "stateful_composition_chain"


def confidence_band(score: float) -> str:
    if score >= 0.85:
        return "very_high"
    if score >= 0.70:
        return "high"
    if score >= 0.55:
        return "medium"
    return "low"


def compute_chain_confidence(
    *,
    row: dict[str, str],
    source: Graph2SkillProfile,
    sink: Graph2SkillProfile,
    primary_object: str,
    trigger_kind: str,
    missing_guards: Iterable[str],
) -> float:
    composition_support = float(row.get("composition_support_score", 0) or 0)
    singleton_support = float(row.get("singleton_safety_plausibility", 0) or 0)
    channel_confidence = float(row.get("channel_confidence", 0) or 0)
    object_component = OBJECT_PRIORITY.get(primary_object, 50) / 100.0
    trigger_component = 0.10 if trigger_kind in {"planner_selection", "threshold_rule", "memory_recall", "artifact_consumption"} else 0.05
    guard_component = min(0.10, 0.03 * len(tuple(missing_guards)))
    evidence_component = 0.05 if source.source_labels and sink.sink_actions else 0.0
    score = (
        composition_support * 0.35
        + singleton_support * 0.20
        + channel_confidence * 0.15
        + object_component * 0.15
        + trigger_component
        + guard_component
        + evidence_component
    )
    return round(min(1.0, score), 4)


def build_stateful_chain(bridge_artifact: str, bridge_state: str, trigger_condition: str, sink_action: str, forbidden_effect: str) -> str:
    return (
        f"source writes {bridge_artifact} carrying `{bridge_state}`"
        f" -> {trigger_condition}"
        f" -> sink performs `{sink_action}`"
        f" -> forbidden effect `{forbidden_effect}`"
    )


def build_stateful_pair_row(row: dict[str, str], profile_by_id: dict[str, Graph2SkillProfile]) -> dict[str, str]:
    source = profile_by_id[row["source_skill_id"]]
    sink = profile_by_id[row["sink_skill_id"]]
    primary_surface = choose_primary_surface(row, source, sink)
    primary_object = choose_primary_object(row, source, sink)
    bridge_state, bridge_artifact, persistence = infer_bridge_state(primary_surface, primary_object, row.get("source_type", "source"))
    sink_action = infer_sink_action(row, sink)
    trigger_style = infer_trigger_style(row, primary_surface)
    trigger_kind = infer_trigger_kind(primary_surface, trigger_style, sink_action)
    trigger_condition = build_trigger_condition(trigger_kind, trigger_style, bridge_state, sink_action, primary_surface)
    forbidden_effect = infer_forbidden_effect(row)
    higher_level_conflict = infer_higher_level_conflict(row)
    missing_guard_values = infer_missing_guards(row, source, sink, primary_surface)
    classification = classify_stateful_pair(primary_surface, trigger_kind, row.get("sink_type", ""))
    chain_conf = compute_chain_confidence(
        row=row,
        source=source,
        sink=sink,
        primary_object=primary_object,
        trigger_kind=trigger_kind,
        missing_guards=missing_guard_values,
    )
    stateful_chain = build_stateful_chain(
        bridge_artifact=bridge_artifact,
        bridge_state=bridge_state,
        trigger_condition=trigger_condition,
        sink_action=sink_action,
        forbidden_effect=forbidden_effect,
    )
    enriched = dict(row)
    enriched.update(
        {
            "primary_surface": primary_surface,
            "primary_object": primary_object,
            "bridge_state": bridge_state,
            "bridge_artifact": bridge_artifact,
            "trigger_kind": trigger_kind,
            "trigger_condition": trigger_condition,
            "trigger_style": trigger_style,
            "state_persistence": persistence,
            "sink_action": sink_action,
            "forbidden_effect": forbidden_effect,
            "higher_level_conflict": higher_level_conflict,
            "missing_guards": "|".join(missing_guard_values),
            "stateful_pair_classification": classification,
            "stateful_chain": stateful_chain,
            "chain_confidence": f"{chain_conf:.4f}",
        }
    )
    return enriched


def summarize_stateful_pairs(rows: list[dict[str, str]]) -> StatefulExtractionSummary:
    trigger_style_counts = Counter(row["trigger_style"] for row in rows)
    trigger_kind_counts = Counter(row["trigger_kind"] for row in rows)
    surface_counts = Counter(row["primary_surface"] for row in rows)
    action_counts = Counter(row["sink_action"] for row in rows)
    conflict_counts = Counter(row["higher_level_conflict"] for row in rows)
    confidence_band_counts = Counter(confidence_band(float(row["chain_confidence"])) for row in rows)
    dynamic_candidates = sum(row.get("c1_dynamic_candidate", "") == "True" for row in rows)
    return StatefulExtractionSummary(
        total_pairs=len(rows),
        dynamic_candidates=dynamic_candidates,
        trigger_style_counts=dict(trigger_style_counts),
        trigger_kind_counts=dict(trigger_kind_counts),
        surface_counts=dict(surface_counts),
        action_counts=dict(action_counts),
        conflict_counts=dict(conflict_counts),
        confidence_band_counts=dict(confidence_band_counts),
    )


def write_stateful_pairs_csv(path: Path, rows: list[dict[str, str]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_stateful_summary(path: Path, summary: StatefulExtractionSummary, rows: list[dict[str, str]], *, top_k: int = 30) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        handle.write("# SC Stateful Pair Analysis\n\n")
        handle.write(
            "This report upgrades the existing SC/CAS pair table into a deterministic stateful chain extraction. "
            "For every pair, it emits a stable hypothesis of the form "
            "`source -> bridge_state/artifact -> trigger -> sink action -> forbidden effect`.\n\n"
        )
        handle.write("## Overview\n\n")
        handle.write(f"- total pairs extracted: `{summary.total_pairs}`\n")
        handle.write(f"- C1 dynamic candidates within extracted rows: `{summary.dynamic_candidates}`\n\n")

        def write_counter(title: str, values: dict[str, int]) -> None:
            handle.write(f"## {title}\n\n")
            handle.write("| Value | Count |\n")
            handle.write("| --- | ---: |\n")
            for key, count in sorted(values.items(), key=lambda item: item[1], reverse=True):
                handle.write(f"| `{key}` | {count} |\n")
            handle.write("\n")

        write_counter("Primary Surface", summary.surface_counts)
        write_counter("Trigger Style", summary.trigger_style_counts)
        write_counter("Trigger Kind", summary.trigger_kind_counts)
        write_counter("Sink Action", summary.action_counts)
        write_counter("Higher-Level Conflict", summary.conflict_counts)
        write_counter("Confidence Bands", summary.confidence_band_counts)

        top_rows = sorted(rows, key=lambda item: float(item["chain_confidence"]), reverse=True)[:top_k]
        handle.write("## Top Stateful Pairs\n\n")
        handle.write("| Rank | Source | Sink | Surface | Trigger | Action | Forbidden Effect | Confidence |\n")
        handle.write("| ---: | --- | --- | --- | --- | --- | --- | ---: |\n")
        for index, row in enumerate(top_rows, start=1):
            handle.write(
                f"| {index} | `{row['source_skill_id']}` | `{row['sink_skill_id']}` | "
                f"`{row['primary_surface']}` | `{row['trigger_kind']}` | `{row['sink_action']}` | "
                f"`{row['forbidden_effect']}` | {float(row['chain_confidence']):.4f} |\n"
            )


def generate_sc_stateful_pair_artifacts(
    *,
    db_path: Path = DEFAULT_DB_PATH,
    input_csv: Path = DEFAULT_INPUT_CSV,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    top_report_k: int = 30,
) -> dict[str, Path]:
    if not input_csv.exists():
        raise FileNotFoundError(f"missing SC/CAS pair table: {input_csv}")
    if not db_path.exists():
        raise FileNotFoundError(f"missing SQLite database: {db_path}")

    profiles = load_graph2_skill_profiles(db_path)
    profile_by_id = {profile.skill_id: profile for profile in profiles}

    with input_csv.open(encoding="utf-8") as handle:
        base_rows = list(csv.DictReader(handle))
    if not base_rows:
        raise ValueError(f"no rows found in {input_csv}")

    enriched_rows: list[dict[str, str]] = []
    for row in base_rows:
        if row["source_skill_id"] not in profile_by_id or row["sink_skill_id"] not in profile_by_id:
            continue
        enriched_rows.append(build_stateful_pair_row(row, profile_by_id))

    fieldnames = list(base_rows[0].keys()) + NEW_FIELDS
    summary = summarize_stateful_pairs(enriched_rows)

    output_dir.mkdir(parents=True, exist_ok=True)
    all_pairs_path = output_dir / "sc_stateful_pairs.csv"
    top_pairs_path = output_dir / "sc_stateful_top_pairs.csv"
    summary_path = output_dir / "sc_stateful_summary.md"

    write_stateful_pairs_csv(all_pairs_path, enriched_rows, fieldnames)
    top_rows = sorted(enriched_rows, key=lambda item: float(item["chain_confidence"]), reverse=True)[:500]
    write_stateful_pairs_csv(top_pairs_path, top_rows, fieldnames)
    write_stateful_summary(summary_path, summary, enriched_rows, top_k=top_report_k)

    return {
        "stateful_pairs_csv": all_pairs_path,
        "stateful_top_pairs_csv": top_pairs_path,
        "stateful_summary_markdown": summary_path,
    }
