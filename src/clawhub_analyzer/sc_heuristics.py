"""Shared heuristics for SC static and runtime selection."""

from __future__ import annotations

import math
from dataclasses import dataclass

from clawhub_analyzer.hypergraph import HypergraphSkillProfile


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
    if carrier_type in {"credential_state", "browser_state", "device_signal", "audio_capture", "video_capture"}:
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
    runtime_support_tier: str = "",
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
    runtime_component = {"tier_a": 0.08, "tier_b": 0.05, "tier_c": 0.02}.get(runtime_support_tier, 0.0)
    return round(min(1.0, singleton_score * 0.40 + trigger_component + surface_component + confidence_component + cluster_component + ecology_component + runtime_component), 4)

