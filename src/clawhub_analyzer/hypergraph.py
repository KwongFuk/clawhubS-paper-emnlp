"""Stage 2 hypergraph risk modeling for multi-skill compositions."""

from __future__ import annotations

from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
import csv
import math
import os
import sqlite3
from pathlib import Path
from statistics import mean
from typing import Iterable, Mapping
import multiprocessing

from clawhub_analyzer.cape.extract import extract_from_file
from clawhub_analyzer.cape.types import Capability
from clawhub_analyzer.toxicity import (
    HINT_COLUMNS,
    SkillCapabilities,
    derive_capabilities,
    effect_types,
    input_object_types,
    output_object_types,
    semantic_channel_types,
    sink_types,
    source_types,
    system_channel_types,
    trust_domains,
)


SOURCE_PRIORS = {
    "contaminated_state": 0.92,
    "sensitive_read": 0.84,
    "untrusted_input": 0.76,
}

SINK_PRIORS = {
    "exfil": 0.74,
    "publish": 0.80,
    "modify": 0.86,
    "execute": 0.92,
    "control": 0.93,
    "delete": 0.97,
    "transact": 1.00,
}

CHANNEL_BASE_PRIORS = {
    "transform_buffer": 0.67,
    "memory": 0.82,
    "workspace": 0.88,
    "browser_state": 0.84,
    "time": 0.60,
    "credential_state": 0.73,
    "clipboard": 0.79,
    "notification_signal": 0.66,
    "popup_dialog": 0.69,
    "window_focus": 0.72,
    "power_state": 0.58,
    "keyboard_io": 0.88,
    "mouse_io": 0.84,
    "screen_capture": 0.86,
    "audio_capture": 0.82,
    "video_capture": 0.84,
    "desktop_state": 0.81,
    "device_signal": 0.79,
}

NODE_FAMILY_WEIGHTS = {
    "source": 0.80,
    "object": 0.92,
    "semantic_channel": 1.00,
    "system_channel": 0.92,
    "domain": 0.35,
    "require": 0.50,
    "sink": 0.65,
    "effect": 0.55,
}

NODE_WEIGHTS = {
    "semantic_channel:memory": 1.00,
    "semantic_channel:workspace": 0.98,
    "semantic_channel:browser_state": 0.94,
    "semantic_channel:transform_buffer": 0.72,
    "system_channel:time": 0.52,
    "system_channel:credential_state": 0.82,
    "system_channel:clipboard": 0.83,
    "system_channel:notification_signal": 0.66,
    "system_channel:popup_dialog": 0.69,
    "system_channel:window_focus": 0.74,
    "system_channel:power_state": 0.50,
    "system_channel:browser_state": 0.90,
    "system_channel:keyboard_io": 0.92,
    "system_channel:mouse_io": 0.86,
    "system_channel:screen_capture": 0.88,
    "system_channel:audio_capture": 0.83,
    "system_channel:video_capture": 0.86,
    "system_channel:desktop_state": 0.82,
    "system_channel:device_signal": 0.80,
}

PRIVILEGE_WEIGHTS = {
    "requires_api_key": 0.65,
    "requires_oauth": 0.68,
    "requires_token": 0.70,
    "requires_local_file_access": 0.85,
    "requires_shell_access": 1.00,
    "requires_browser_access": 0.75,
    "requires_github": 0.55,
    "requires_slack": 0.55,
    "requires_notion": 0.55,
    "requires_calendar": 0.62,
    "requires_email": 0.60,
    "requires_docs": 0.55,
    "requires_pdf": 0.45,
}

PRIMARY_SOURCE_PRIORITY = ("contaminated_state", "sensitive_read", "untrusted_input")
PRIMARY_SINK_PRIORITY = ("transact", "delete", "control", "execute", "modify", "publish", "exfil")
JOIN_NODE_PREFIXES = ("object:", "semantic_channel:", "system_channel:")
MAX_JOIN_FANOUT = 6000
MAX_ACTIVATION_MATCHES_PER_NODE = 240
SECURITY_FLAGGED_STATUSES = {"malicious", "suspicious", "error", "failed"}
MAX_PRIVILEGE_WEIGHT = sum(PRIVILEGE_WEIGHTS.values())
MAX_FILE_CAPABILITY_TEXT_BYTES = 128_000
EXTRACTABLE_FILE_EXTENSIONS = {
    ".py",
    ".sh",
    ".bash",
    ".zsh",
    ".fish",
    ".js",
    ".mjs",
    ".cjs",
    ".ts",
    ".tsx",
    ".rs",
    ".go",
    ".md",
    ".mdx",
    ".json",
    ".yaml",
    ".yml",
}
EXTRACTABLE_FILE_NAMES = {"skill.md", "readme.md", "system_prompt.md", "install.md"}


@dataclass(frozen=True)
class HyperEdge:
    edge_id: str
    skill_id: str
    edge_kind: str
    tail: tuple[str, ...]
    head: tuple[str, ...]
    order: int


@dataclass(frozen=True)
class HypergraphSkillProfile:
    skill_id: str
    name: str
    author_handle: str | None
    downloads: int
    stars: int
    installs_all_time: int
    permission_flag_count: int
    raw_evidence_count: int
    public_version_count: int
    install_command: str
    has_install_command: bool
    has_scan_status: bool
    is_security_flagged: bool
    scan_status: str
    hint_flags: dict[str, bool]
    capabilities: SkillCapabilities
    source_nodes: tuple[str, ...]
    input_nodes: tuple[str, ...]
    output_nodes: tuple[str, ...]
    semantic_channel_nodes: tuple[str, ...]
    system_channel_nodes: tuple[str, ...]
    channel_read_nodes: tuple[str, ...]
    channel_write_nodes: tuple[str, ...]
    file_capability_count: int
    file_capability_evidence: tuple[str, ...]
    sink_nodes: tuple[str, ...]
    domain_nodes: tuple[str, ...]
    effect_nodes: tuple[str, ...]
    guard_nodes: tuple[str, ...]
    activation_edge: HyperEdge
    emission_edge: HyperEdge

    @property
    def carrier_nodes(self) -> tuple[str, ...]:
        return _unique_sorted((*self.semantic_channel_nodes, *self.system_channel_nodes))

    @property
    def channel_nodes(self) -> tuple[str, ...]:
        return self.carrier_nodes

    @property
    def has_file_channel_capabilities(self) -> bool:
        return bool(self.channel_read_nodes or self.channel_write_nodes)

    def activation_channel_nodes(self) -> tuple[str, ...]:
        if self.has_file_channel_capabilities:
            return self.channel_read_nodes
        return self.channel_nodes

    def emission_channel_nodes(self) -> tuple[str, ...]:
        if self.has_file_channel_capabilities:
            return self.channel_write_nodes
        return self.channel_nodes

    def join_head_nodes(self) -> tuple[str, ...]:
        return tuple(node for node in self.emission_edge.head if node.startswith(JOIN_NODE_PREFIXES))

    def join_tail_nodes(self) -> tuple[str, ...]:
        return tuple(node for node in self.activation_edge.tail if node.startswith(JOIN_NODE_PREFIXES))

    def primary_source_type(self) -> str:
        present = {node.split(":", 1)[1] for node in self.source_nodes}
        for source_type in PRIMARY_SOURCE_PRIORITY:
            if source_type in present:
                return source_type
        return "untrusted_input"

    def primary_sink_type(self) -> str:
        present = {node.split(":", 1)[1] for node in self.sink_nodes}
        for sink_type in PRIMARY_SINK_PRIORITY:
            if sink_type in present:
                return sink_type
        return "execute"

    def primary_domain(self) -> str:
        present = {node.split(":", 1)[1] for node in self.domain_nodes}
        for domain in (
            "External",
            "EnterpriseInternal",
            "AgentWorkspace",
            "LocalHost",
            "ThirdPartySaaS",
            "PhysicalDevice",
            "UserProvided",
        ):
            if domain in present:
                return domain
        return "UserProvided"

    def authority_surface(self) -> float:
        if MAX_PRIVILEGE_WEIGHT <= 0:
            return 0.0
        weight = sum(
            PRIVILEGE_WEIGHTS[name]
            for name, enabled in self.hint_flags.items()
            if enabled and name in PRIVILEGE_WEIGHTS
        )
        return min(1.0, weight / MAX_PRIVILEGE_WEIGHT)

    def audit_gap(self) -> float:
        scan_gap = 0.0 if self.has_scan_status else 1.0
        version_gap = 1.0 - min(1.0, self.public_version_count / 5.0)
        security_gap = 1.0 if self.is_security_flagged else 0.0
        install_gap = 0.0 if self.has_install_command else 0.25
        return min(1.0, 0.35 * scan_gap + 0.25 * version_gap + 0.30 * security_gap + 0.10 * install_gap)

    def exposure_score(self, *, max_downloads: float, max_stars: float) -> float:
        download_score = math.log10(self.downloads + 10.0) / max_downloads if max_downloads > 0 else 0.0
        star_score = math.log10(self.stars + 10.0) / max_stars if max_stars > 0 else 0.0
        return min(1.0, 0.70 * download_score + 0.30 * star_score)


@dataclass(frozen=True)
class DirectedHypergraph:
    nodes: tuple[str, ...]
    activation_edges: tuple[HyperEdge, ...]
    emission_edges: tuple[HyperEdge, ...]
    profiles: dict[str, HypergraphSkillProfile]

    @property
    def edge_count(self) -> int:
        return len(self.activation_edges) + len(self.emission_edges)


@dataclass(frozen=True)
class HypergraphRiskCombination:
    category: str
    schema_name: str
    risk_outcome: str
    severity_tier: str
    source_type: str
    sink_type: str
    source_skill_id: str
    source_name: str
    source_domain: str
    bridge_skill_id: str
    bridge_name: str
    bridge_domain: str
    sink_skill_id: str
    sink_name: str
    sink_domain: str
    channel_family: str
    carrier_type: str
    cross_domain: bool
    author_diversity: int
    source_bridge_overlap: float
    bridge_sink_overlap: float
    semantic_overlap: float
    coordination_ease: float
    carrier_score: float
    authority_score: float
    impact_score: float
    source_score: float
    exposure_score: float
    audit_gap_score: float
    risk_score: float
    source_bridge_nodes: tuple[str, ...]
    bridge_sink_nodes: tuple[str, ...]
    rationale: str


@dataclass(frozen=True)
class HypergraphSkillRisk:
    skill_id: str
    name: str
    author_handle: str | None
    downloads: int
    stars: int
    as_source_count: int
    as_bridge_count: int
    as_sink_count: int
    total_incident_count: int
    bridge_brokerage: int
    domain_span: int
    carrier_span: int
    max_risk_score: float
    mean_risk_score: float
    aggregate_risk_score: float


@dataclass(frozen=True)
class HypergraphAnalysisSummary:
    total_skills: int
    total_nodes: int
    total_hyperedges: int
    total_combinations: int
    source_candidates: int
    bridge_candidates: int
    sink_candidates: int
    node_family_counts: dict[str, int]
    source_type_counts: dict[str, int]
    sink_type_counts: dict[str, int]
    carrier_counts: dict[str, int]
    channel_family_counts: dict[str, int]
    semantic_channel_counts: dict[str, int]
    system_channel_counts: dict[str, int]
    outcome_counts: dict[str, int]
    severity_counts: dict[str, int]
    schema_counts: dict[str, int]
    domain_cross_counts: dict[str, int]
    activation_order_mean: float
    activation_order_median: float


@dataclass(frozen=True)
class HypergraphChannelInventory:
    channel_family: str
    channel_subtype: str
    channel_name: str
    skill_count: int
    source_skill_count: int
    bridge_skill_count: int
    sink_skill_count: int
    example_skill_ids: tuple[str, ...]


def _unique_sorted(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(sorted(dict.fromkeys(value for value in values if value)))


def _prefixed(prefix: str, values: Iterable[str]) -> tuple[str, ...]:
    return tuple(f"{prefix}:{value}" for value in values)


def _node_family(node: str) -> str:
    return node.split(":", 1)[0]


def _node_weight(node: str) -> float:
    if node in NODE_WEIGHTS:
        return NODE_WEIGHTS[node]
    return NODE_FAMILY_WEIGHTS.get(_node_family(node), 0.0)


def _weighted_overlap(shared_nodes: Iterable[str], required_nodes: Iterable[str]) -> float:
    shared = [node for node in shared_nodes if _node_weight(node) > 0]
    required = [node for node in required_nodes if _node_weight(node) > 0]
    if not shared or not required:
        return 0.0
    numerator = sum(_node_weight(node) for node in shared)
    denominator = sum(_node_weight(node) for node in required)
    return min(1.0, numerator / denominator) if denominator > 0 else 0.0


def _normalize_hint_flags(row: sqlite3.Row) -> dict[str, bool]:
    return {column: bool(row[column]) for column in HINT_COLUMNS}


def _guard_nodes_from_flags(flags: dict[str, bool]) -> tuple[str, ...]:
    nodes: list[str] = []
    for column, enabled in flags.items():
        if enabled:
            nodes.append(f"require:{column.removeprefix('requires_')}")
    return _unique_sorted(nodes)


CAPE_SOURCE_NODE_MAP = {
    "web_fetch": ("source:untrusted_input",),
    "email_receive": ("source:sensitive_read",),
    "chat_receive": ("source:sensitive_read",),
    "user_file_read": ("source:sensitive_read",),
    "audio_capture": ("source:sensitive_read",),
    "screen_capture": ("source:sensitive_read",),
    "credential_read": ("source:sensitive_read",),
    "memory_read": ("source:contaminated_state", "source:sensitive_read"),
    "browser_state_read": ("source:contaminated_state", "source:sensitive_read"),
    "calendar_read": ("source:sensitive_read",),
    "contact_read": ("source:sensitive_read",),
}

CAPE_SOURCE_OBJECT_MAP = {
    "web_fetch": ("object:raw_content", "object:instruction_bearing_text"),
    "email_receive": ("object:private_content", "object:instruction_bearing_text"),
    "chat_receive": ("object:private_content", "object:instruction_bearing_text"),
    "user_file_read": ("object:private_content", "object:secret"),
    "audio_capture": ("object:observation_trace",),
    "screen_capture": ("object:observation_trace",),
    "credential_read": ("object:secret", "object:private_content"),
    "memory_read": ("object:private_content", "object:state_snapshot"),
    "browser_state_read": ("object:private_content", "object:state_snapshot"),
    "calendar_read": ("object:schedule_event", "object:private_content"),
    "contact_read": ("object:private_content",),
}

CAPE_SOURCE_DOMAIN_MAP = {
    "web_fetch": ("domain:External",),
    "email_receive": ("domain:EnterpriseInternal", "domain:ThirdPartySaaS"),
    "chat_receive": ("domain:EnterpriseInternal", "domain:ThirdPartySaaS"),
    "user_file_read": ("domain:AgentWorkspace", "domain:LocalHost"),
    "audio_capture": ("domain:LocalHost",),
    "screen_capture": ("domain:LocalHost",),
    "credential_read": ("domain:LocalHost", "domain:ThirdPartySaaS"),
    "memory_read": ("domain:AgentWorkspace",),
    "browser_state_read": ("domain:LocalHost",),
    "calendar_read": ("domain:EnterpriseInternal", "domain:ThirdPartySaaS"),
    "contact_read": ("domain:EnterpriseInternal", "domain:ThirdPartySaaS"),
}

CAPE_SINK_NODE_MAP = {
    "shell_exec": ("sink:execute",),
    "script_eval": ("sink:execute",),
    "remote_exec": ("sink:execute",),
    "network_send": ("sink:exfil",),
    "email_send": ("sink:publish", "sink:exfil"),
    "chat_post": ("sink:publish", "sink:exfil"),
    "webhook_call": ("sink:exfil",),
    "cloud_upload": ("sink:exfil",),
    "file_write": ("sink:modify",),
    "file_delete": ("sink:delete",),
    "db_write": ("sink:modify",),
    "calendar_write": ("sink:modify", "sink:transact"),
    "code_commit": ("sink:modify", "sink:publish"),
    "privilege_escalate": ("sink:execute", "sink:control"),
}

CAPE_SINK_EFFECT_MAP = {
    "shell_exec": ("effect:desktop_or_shell", "effect:critical_action"),
    "script_eval": ("effect:desktop_or_shell", "effect:critical_action"),
    "remote_exec": ("effect:desktop_or_shell", "effect:critical_action"),
    "network_send": ("effect:external_exfil",),
    "email_send": ("effect:external_exfil",),
    "chat_post": ("effect:external_exfil",),
    "webhook_call": ("effect:external_exfil",),
    "cloud_upload": ("effect:external_exfil",),
    "file_write": ("effect:critical_action",),
    "file_delete": ("effect:critical_action",),
    "db_write": ("effect:critical_action",),
    "calendar_write": ("effect:schedule_state", "effect:critical_action"),
    "code_commit": ("effect:critical_action",),
    "privilege_escalate": ("effect:desktop_or_shell", "effect:critical_action"),
}

CAPE_SINK_DOMAIN_MAP = {
    "shell_exec": ("domain:LocalHost",),
    "script_eval": ("domain:LocalHost",),
    "remote_exec": ("domain:External", "domain:LocalHost"),
    "network_send": ("domain:ThirdPartySaaS", "domain:External"),
    "email_send": ("domain:ThirdPartySaaS", "domain:EnterpriseInternal"),
    "chat_post": ("domain:ThirdPartySaaS", "domain:EnterpriseInternal"),
    "webhook_call": ("domain:ThirdPartySaaS", "domain:External"),
    "cloud_upload": ("domain:ThirdPartySaaS",),
    "file_write": ("domain:AgentWorkspace", "domain:LocalHost"),
    "file_delete": ("domain:AgentWorkspace", "domain:LocalHost"),
    "db_write": ("domain:EnterpriseInternal", "domain:ThirdPartySaaS"),
    "calendar_write": ("domain:EnterpriseInternal", "domain:ThirdPartySaaS"),
    "code_commit": ("domain:EnterpriseInternal", "domain:ThirdPartySaaS"),
    "privilege_escalate": ("domain:LocalHost",),
}

CAPE_CHANNEL_NODE_MAP = {
    "workspace_file": "semantic_channel:workspace",
    "agent_memory": "semantic_channel:memory",
    "browser_session": "system_channel:browser_state",
    "env_variable": "system_channel:credential_state",
    "credential_store": "system_channel:credential_state",
    "clipboard": "system_channel:clipboard",
    "tmp_file": "semantic_channel:transform_buffer",
    "stdout_pipe": "semantic_channel:transform_buffer",
}

CAPE_CHANNEL_OBJECT_MAP = {
    "workspace_file": ("object:private_content", "object:state_snapshot"),
    "agent_memory": ("object:private_content", "object:state_snapshot"),
    "browser_session": ("object:private_content", "object:state_snapshot"),
    "env_variable": ("object:secret",),
    "credential_store": ("object:secret",),
    "clipboard": ("object:private_content",),
    "tmp_file": ("object:raw_content",),
    "stdout_pipe": ("object:raw_content", "object:action_argument"),
}


def _cape_profile_nodes(
    file_capabilities: Iterable[Capability],
) -> tuple[
    tuple[str, ...],
    tuple[str, ...],
    tuple[str, ...],
    tuple[str, ...],
    tuple[str, ...],
    tuple[str, ...],
    tuple[str, ...],
    tuple[str, ...],
]:
    source_nodes: list[str] = []
    input_nodes: list[str] = []
    output_nodes: list[str] = []
    channel_read_nodes: list[str] = []
    channel_write_nodes: list[str] = []
    sink_nodes: list[str] = []
    domain_nodes: list[str] = []
    effect_nodes: list[str] = []

    for capability in file_capabilities:
        if capability.cap_type == "source":
            source_nodes.extend(CAPE_SOURCE_NODE_MAP.get(capability.label, ()))
            input_nodes.extend(CAPE_SOURCE_OBJECT_MAP.get(capability.label, ()))
            output_nodes.extend(CAPE_SOURCE_OBJECT_MAP.get(capability.label, ()))
            domain_nodes.extend(CAPE_SOURCE_DOMAIN_MAP.get(capability.label, ()))
            if capability.label == "audio_capture":
                channel_read_nodes.append("system_channel:audio_capture")
                channel_write_nodes.append("system_channel:audio_capture")
            elif capability.label == "screen_capture":
                channel_read_nodes.append("system_channel:screen_capture")
                channel_write_nodes.append("system_channel:screen_capture")
            elif capability.label == "credential_read":
                channel_read_nodes.append("system_channel:credential_state")
                channel_write_nodes.append("system_channel:credential_state")
            elif capability.label == "memory_read":
                channel_read_nodes.append("semantic_channel:memory")
            elif capability.label == "browser_state_read":
                channel_read_nodes.append("system_channel:browser_state")
            elif capability.label == "calendar_read":
                channel_read_nodes.append("system_channel:time")
                channel_write_nodes.append("system_channel:time")
        elif capability.cap_type == "sink":
            sink_nodes.extend(CAPE_SINK_NODE_MAP.get(capability.label, ()))
            output_nodes.extend(("object:action_argument",))
            domain_nodes.extend(CAPE_SINK_DOMAIN_MAP.get(capability.label, ()))
            effect_nodes.extend(CAPE_SINK_EFFECT_MAP.get(capability.label, ()))
        elif capability.cap_type == "channel":
            channel_node = CAPE_CHANNEL_NODE_MAP.get(capability.label)
            if channel_node is None:
                continue
            objects = CAPE_CHANNEL_OBJECT_MAP.get(capability.label, ())
            role = capability.role
            if role in ("read", "both", None):
                channel_read_nodes.append(channel_node)
                input_nodes.extend(objects)
            if role in ("write", "both", None):
                channel_write_nodes.append(channel_node)
                output_nodes.extend(objects)

    return (
        _unique_sorted(source_nodes),
        _unique_sorted(input_nodes),
        _unique_sorted(output_nodes),
        _unique_sorted(channel_read_nodes),
        _unique_sorted(channel_write_nodes),
        _unique_sorted(sink_nodes),
        _unique_sorted(domain_nodes),
        _unique_sorted(effect_nodes),
    )


def _source_risk_prior(source_type: str) -> float:
    return SOURCE_PRIORS.get(source_type, 0.70)


def _sink_impact_prior(sink_type: str) -> float:
    return SINK_PRIORS.get(sink_type, 0.75)


def _carrier_score(carrier_type: str, carrier_prevalence: dict[str, float]) -> float:
    base = CHANNEL_BASE_PRIORS.get(carrier_type, 0.70)
    prevalence = carrier_prevalence.get(carrier_type, 0.0)
    return min(1.0, 0.60 * base + 0.40 * prevalence)


def _coordination_ease(bridge_tail_order: int, sink_tail_order: int) -> float:
    conjunctive_load = (bridge_tail_order + sink_tail_order) / 2.0
    return max(0.20, min(1.0, 4.0 / max(conjunctive_load, 4.0)))


def _primary_shared_carrier(
    bridge: HypergraphSkillProfile,
    source_bridge_nodes: tuple[str, ...],
    bridge_sink_nodes: tuple[str, ...],
) -> tuple[str, str]:
    channel_candidates = {
        node
        for node in (*source_bridge_nodes, *bridge_sink_nodes)
        if node.startswith("semantic_channel:") or node.startswith("system_channel:")
    }
    if not channel_candidates:
        channel_candidates = {
            node
            for node in bridge.channel_nodes
        }
    if not channel_candidates:
        return ("semantic_channel", "transform_buffer")
    selected = sorted(
        channel_candidates,
        key=lambda node: CHANNEL_BASE_PRIORS.get(node.split(":", 1)[1], 0.0),
        reverse=True,
    )[0]
    family, channel_name = selected.split(":", 1)
    return family, channel_name


def _combination_category(source_type: str, sink_type: str) -> str:
    source_label = source_type.removeprefix("contaminated_")
    if sink_type in {"exfil", "publish", "transact"}:
        return f"hypergraph_{source_label}_to_channel"
    return f"hypergraph_{source_label}_to_action"


def _schema_name(sink_type: str) -> str:
    if sink_type == "exfil":
        return "Schema_Exfil"
    if sink_type in {"execute", "control"}:
        return "Schema_ExecOrControl"
    if sink_type == "modify":
        return "Schema_Modify"
    if sink_type == "delete":
        return "Schema_Delete"
    if sink_type in {"transact", "publish"}:
        return "Schema_TransactOrPublish"
    return f"Schema_{sink_type.title()}"


def _channel_subtype(channel_family: str, channel_name: str) -> str:
    if channel_family == "semantic_channel":
        return "semantic_state"
    if channel_name in {"time", "credential_state", "approval_state", "browser_state", "desktop_state", "clipboard", "notification_signal", "popup_dialog", "window_focus", "power_state"}:
        return "runtime_state"
    if channel_name in {"keyboard_io", "mouse_io", "screen_capture", "audio_capture", "video_capture"}:
        return "host_io"
    if channel_name == "device_signal":
        return "physical_device"
    return "runtime_state"


def _risk_outcome(
    sink: HypergraphSkillProfile,
    sink_type: str,
    channel_family: str,
    channel_name: str,
) -> str:
    text = sink.name.lower() + " " + sink.capabilities.description.lower()
    disruption_markers = (
        "stop",
        "pause",
        "interrupt",
        "disable",
        "block",
        "terminate",
        "kill",
        "close",
        "mute",
        "lock",
        "logout",
        "sleep",
        "suspend",
    )
    if any(marker in text for marker in disruption_markers):
        return "availability_disruption"
    if sink_type in {"exfil", "publish"}:
        return "information_leakage"
    if sink_type in {"modify"} and channel_name in {"time", "notification_signal", "popup_dialog", "window_focus", "power_state"}:
        return "availability_disruption"
    if sink_type in {"execute", "control", "delete", "transact", "modify"}:
        return "harmful_operation"
    return "harmful_operation"


def _severity_tier(risk_outcome: str, sink_type: str, channel_name: str) -> str:
    if risk_outcome == "availability_disruption":
        return "low"
    if sink_type in {"exfil", "publish", "modify"}:
        return "medium"
    if sink_type in {"execute", "control"}:
        if channel_name in {"keyboard_io", "mouse_io", "screen_capture", "audio_capture", "video_capture", "device_signal"}:
            return "critical"
        return "high"
    if sink_type in {"delete", "transact"}:
        return "critical"
    return "high"


def _combination_identity(
    combination: HypergraphRiskCombination,
) -> tuple[str, str, str, str, str, str, str, str]:
    return (
        combination.category,
        combination.schema_name,
        combination.source_type,
        combination.sink_type,
        combination.source_skill_id,
        combination.bridge_skill_id,
        combination.sink_skill_id,
        f"{combination.channel_family}:{combination.carrier_type}",
    )


def _deduplicate_combinations(
    combinations: list[HypergraphRiskCombination],
) -> list[HypergraphRiskCombination]:
    best_by_identity: dict[tuple[str, str, str, str, str, str, str, str], HypergraphRiskCombination] = {}
    for combination in combinations:
        identity = _combination_identity(combination)
        current = best_by_identity.get(identity)
        if current is None:
            best_by_identity[identity] = combination
            continue
        current_rank = (
            current.risk_score,
            current.semantic_overlap,
            current.authority_score,
            len(current.source_bridge_nodes) + len(current.bridge_sink_nodes),
        )
        candidate_rank = (
            combination.risk_score,
            combination.semantic_overlap,
            combination.authority_score,
            len(combination.source_bridge_nodes) + len(combination.bridge_sink_nodes),
        )
        if candidate_rank > current_rank:
            best_by_identity[identity] = combination
    return list(best_by_identity.values())


def derive_hypergraph_profile(
    row: sqlite3.Row | Mapping[str, object],
    file_capabilities: Iterable[Capability] | None = None,
) -> HypergraphSkillProfile:
    capabilities = derive_capabilities(row)
    file_capabilities_tuple = tuple(file_capabilities or ())
    (
        file_source_nodes,
        file_input_nodes,
        file_output_nodes,
        file_channel_read_nodes,
        file_channel_write_nodes,
        file_sink_nodes,
        file_domain_nodes,
        file_effect_nodes,
    ) = _cape_profile_nodes(file_capabilities_tuple)
    hint_flags = _normalize_hint_flags(row)
    skill_node = f"skill:{capabilities.skill_id}"
    metadata_semantic_channel_nodes = _prefixed("semantic_channel", semantic_channel_types(capabilities))
    metadata_system_channel_nodes = _prefixed("system_channel", system_channel_types(capabilities))
    file_channel_nodes = _unique_sorted((*file_channel_read_nodes, *file_channel_write_nodes))
    source_nodes = _unique_sorted((*_prefixed("source", source_types(capabilities)), *file_source_nodes))
    input_nodes = _unique_sorted((*_prefixed("object", input_object_types(capabilities)), *file_input_nodes))
    output_nodes = _unique_sorted((*_prefixed("object", output_object_types(capabilities)), *file_output_nodes))
    semantic_channel_nodes = _unique_sorted(
        (
            *metadata_semantic_channel_nodes,
            *(node for node in file_channel_nodes if node.startswith("semantic_channel:")),
        )
    )
    system_channel_nodes = _unique_sorted(
        (
            *metadata_system_channel_nodes,
            *(node for node in file_channel_nodes if node.startswith("system_channel:")),
        )
    )
    sink_nodes = _unique_sorted((*_prefixed("sink", sink_types(capabilities)), *file_sink_nodes))
    domain_nodes = _unique_sorted((*_prefixed("domain", trust_domains(capabilities)), *file_domain_nodes))
    effect_nodes = _unique_sorted((*_prefixed("effect", effect_types(capabilities)), *file_effect_nodes))
    guard_nodes = _guard_nodes_from_flags(hint_flags)
    channel_read_nodes = file_channel_read_nodes
    channel_write_nodes = file_channel_write_nodes
    activation_channels = channel_read_nodes if file_channel_nodes else (*semantic_channel_nodes, *system_channel_nodes)
    emission_channels = channel_write_nodes if file_channel_nodes else (*semantic_channel_nodes, *system_channel_nodes)
    activation_tail = _unique_sorted(
        (*source_nodes, *input_nodes, *activation_channels, *domain_nodes, *guard_nodes)
    )
    emission_head = _unique_sorted(
        (
            *output_nodes,
            *source_nodes,
            *emission_channels,
            *domain_nodes,
            *sink_nodes,
            *effect_nodes,
        )
    )
    activation_edge = HyperEdge(
        edge_id=f"{capabilities.skill_id}#activate",
        skill_id=capabilities.skill_id,
        edge_kind="activation",
        tail=activation_tail,
        head=(skill_node,),
        order=len(activation_tail),
    )
    emission_edge = HyperEdge(
        edge_id=f"{capabilities.skill_id}#emit",
        skill_id=capabilities.skill_id,
        edge_kind="emission",
        tail=(skill_node,),
        head=emission_head,
        order=1,
    )
    scan_status = (row["scan_status"] or "").strip()
    return HypergraphSkillProfile(
        skill_id=capabilities.skill_id,
        name=capabilities.name,
        author_handle=capabilities.author_handle,
        downloads=capabilities.downloads,
        stars=capabilities.stars,
        installs_all_time=int(row["installs_all_time"] or 0),
        permission_flag_count=sum(int(value) for value in hint_flags.values()),
        raw_evidence_count=0,
        public_version_count=int(row["public_version_count"] or 0),
        install_command=(row["install_command"] or "").strip(),
        has_install_command=bool(row["install_command"]),
        has_scan_status=bool(scan_status),
        is_security_flagged=scan_status.lower() in SECURITY_FLAGGED_STATUSES,
        scan_status=scan_status,
        hint_flags=hint_flags,
        capabilities=capabilities,
        source_nodes=source_nodes,
        input_nodes=input_nodes,
        output_nodes=output_nodes,
        semantic_channel_nodes=semantic_channel_nodes,
        system_channel_nodes=system_channel_nodes,
        channel_read_nodes=channel_read_nodes,
        channel_write_nodes=channel_write_nodes,
        file_capability_count=len(file_capabilities_tuple),
        file_capability_evidence=tuple(capability.evidence for capability in file_capabilities_tuple[:20]),
        sink_nodes=sink_nodes,
        domain_nodes=domain_nodes,
        effect_nodes=effect_nodes,
        guard_nodes=guard_nodes,
        activation_edge=activation_edge,
        emission_edge=emission_edge,
    )


def _resolve_skill_text_path(db_path: Path, text_path: str) -> Path | None:
    candidate = Path(text_path)
    if candidate.is_absolute() and candidate.exists():
        return candidate
    if candidate.exists():
        return candidate
    search_roots = [
        Path.cwd(),
        db_path.parent,
        db_path.parent.parent,
        db_path.parent.parent.parent if len(db_path.parents) >= 3 else db_path.parent,
    ]
    for root in search_roots:
        resolved = root / text_path
        if resolved.exists():
            return resolved
    project_root = db_path.parent.parent.parent if len(db_path.parents) >= 3 else Path.cwd()
    basename = candidate.name
    for snapshot_root in (
        project_root / "data/collection_archive/remote_scratch_html_snapshots/api_files",
        project_root / "data/collection_archive/remote_scratch_html_snapshots_shard1/api_files",
        project_root / "data/collection_archive/html_snapshots/api_files",
        project_root / "data/collection_archive/runs/2026-03-06_public_archive_partial/html_snapshots/api_files",
    ):
        resolved = snapshot_root / basename
        if resolved.exists():
            return resolved
    return None


def _dedupe_capabilities(capabilities: Iterable[Capability]) -> tuple[Capability, ...]:
    seen: set[tuple[str, str, str | None]] = set()
    deduped: list[Capability] = []
    for capability in capabilities:
        if capability.label == "same_turn_ctx":
            continue
        key = (capability.cap_type, capability.label, capability.role)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(capability)
    return tuple(deduped)


def _is_extractable_skill_file(path: str) -> bool:
    file_path = Path(path)
    return file_path.suffix.lower() in EXTRACTABLE_FILE_EXTENSIONS or file_path.name.lower() in EXTRACTABLE_FILE_NAMES


def _load_current_file_capabilities(
    connection: sqlite3.Connection,
    db_path: Path,
    *,
    progress_label: str | None = None,
) -> dict[str, tuple[Capability, ...]]:
    file_rows = connection.execute(
        """
        SELECT f.skill_id, f.path, f.text_path, COALESCE(f.size, 0) AS size
        FROM skill_files f
        JOIN skills s USING(skill_id)
        WHERE f.text_path IS NOT NULL
          AND f.text_path != ''
          AND (
            COALESCE(s.current_version, '') = ''
            OR f.version = s.current_version
          )
        ORDER BY f.skill_id, f.path
        """
    ).fetchall()
    caps_by_skill: dict[str, list[Capability]] = defaultdict(list)
    total = len(file_rows)
    missing = 0
    skipped_large = 0
    skipped_type = 0

    def report_progress(index: int) -> None:
        if progress_label and (index == 1 or index % 25000 == 0 or index == total):
            print(
                f"{progress_label}: extracted_file_capabilities={index}/{total} "
                f"missing={missing} skipped_large={skipped_large} skipped_type={skipped_type}",
                flush=True,
            )

    for index, row in enumerate(file_rows, start=1):
        if not _is_extractable_skill_file(row["path"] or ""):
            skipped_type += 1
            report_progress(index)
            continue
        if int(row["size"] or 0) > MAX_FILE_CAPABILITY_TEXT_BYTES:
            skipped_large += 1
            report_progress(index)
            continue
        text_path = row["text_path"]
        resolved = _resolve_skill_text_path(db_path, text_path)
        if resolved is None:
            missing += 1
            report_progress(index)
            continue
        try:
            text = resolved.read_text(encoding="utf-8", errors="replace")
        except OSError:
            missing += 1
            report_progress(index)
            continue
        caps_by_skill[row["skill_id"]].extend(extract_from_file(text, row["path"] or ""))
        report_progress(index)
    return {
        skill_id: _dedupe_capabilities(capabilities)
        for skill_id, capabilities in caps_by_skill.items()
    }


def load_hypergraph_skill_profiles(
    db_path: Path,
    *,
    progress_label: str | None = None,
    include_file_capabilities: bool = True,
) -> list[HypergraphSkillProfile]:
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    skill_columns = {
        row["name"]
        for row in connection.execute("PRAGMA table_info(skills)").fetchall()
    }
    where_clauses: list[str] = []
    if "detail_complete" in skill_columns:
        where_clauses.append("COALESCE(s.detail_complete, 0) = 1")
    elif "detail_completed" in skill_columns:
        where_clauses.append("COALESCE(s.detail_completed, 0) = 1")
    if "excluded_reason" in skill_columns:
        where_clauses.append("COALESCE(s.excluded_reason, '') = ''")
    installs_expr = "0"
    if "installs_all_time" in skill_columns:
        installs_expr = "COALESCE(s.installs_all_time, 0)"
    elif "total_installs" in skill_columns:
        installs_expr = "COALESCE(s.total_installs, 0)"
    where_sql = ""
    if where_clauses:
        where_sql = "\n    WHERE " + "\n      AND ".join(where_clauses)
    query = """
    WITH version_counts AS (
        SELECT skill_id, COUNT(DISTINCT version) AS public_version_count
        FROM skill_versions
        GROUP BY skill_id
    )
    SELECT
        s.skill_id,
        s.author_handle,
        COALESCE(s.name, s.skill_id) AS name,
        COALESCE(s.downloads, 0) AS downloads,
        COALESCE(s.stars, 0) AS stars,
        """ + installs_expr + """ AS installs_all_time,
        COALESCE(s.description, '') AS description,
        COALESCE(s.tags, '') AS tags,
        COALESCE(s.metadata_systems, '') AS metadata_systems,
        COALESCE(s.install_command, '') AS install_command,
        COALESCE(s.scan_status, '') AS scan_status,
        COALESCE(v.public_version_count, 0) AS public_version_count,
        COALESCE(h.requires_api_key, 0) AS requires_api_key,
        COALESCE(h.requires_oauth, 0) AS requires_oauth,
        COALESCE(h.requires_token, 0) AS requires_token,
        COALESCE(h.requires_local_file_access, 0) AS requires_local_file_access,
        COALESCE(h.requires_shell_access, 0) AS requires_shell_access,
        COALESCE(h.requires_browser_access, 0) AS requires_browser_access,
        COALESCE(h.requires_github, 0) AS requires_github,
        COALESCE(h.requires_slack, 0) AS requires_slack,
        COALESCE(h.requires_notion, 0) AS requires_notion,
        COALESCE(h.requires_calendar, 0) AS requires_calendar,
        COALESCE(h.requires_email, 0) AS requires_email,
        COALESCE(h.requires_docs, 0) AS requires_docs,
        COALESCE(h.requires_pdf, 0) AS requires_pdf
    FROM skills s
    LEFT JOIN permission_secret_hints h USING(skill_id)
    LEFT JOIN version_counts v USING(skill_id)
    """ + where_sql
    rows = connection.execute(query).fetchall()
    if not rows:
        connection.close()
        return []

    file_capabilities_by_skill: dict[str, tuple[Capability, ...]] = {}
    if include_file_capabilities and "skill_files" in {
        row["name"] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }:
        if progress_label:
            print(f"{progress_label}: extracting_file_capabilities", flush=True)
        file_capabilities_by_skill = _load_current_file_capabilities(connection, db_path, progress_label=progress_label)

    connection.close()

    worker_count = min(8, os.cpu_count() or 1)
    row_dicts = [dict(row) for row in rows]
    if progress_label:
        print(
            f"{progress_label}: deriving_profiles rows={len(row_dicts)} workers={worker_count}",
            flush=True,
        )

    if file_capabilities_by_skill:
        profiles: list[HypergraphSkillProfile] = []
        total = len(row_dicts)
        for index, row in enumerate(row_dicts, start=1):
            skill_id = str(row["skill_id"])
            profiles.append(derive_hypergraph_profile(row, file_capabilities_by_skill.get(skill_id, ())))
            if progress_label and (index == 1 or index % 5000 == 0 or index == total):
                print(f"{progress_label}: derived_profiles={index}/{total}", flush=True)
        return profiles

    if worker_count <= 1 or len(row_dicts) < 5000:
        profiles: list[HypergraphSkillProfile] = []
        total = len(row_dicts)
        for index, row in enumerate(row_dicts, start=1):
            profiles.append(derive_hypergraph_profile(row))
            if progress_label and (index == 1 or index % 5000 == 0 or index == total):
                print(f"{progress_label}: derived_profiles={index}/{total}", flush=True)
        return profiles

    mp_context = None
    if os.name != "nt":
        try:
            mp_context = multiprocessing.get_context("fork")
        except ValueError:
            mp_context = None
    profiles = []
    with ProcessPoolExecutor(max_workers=worker_count, mp_context=mp_context) as executor:
        total = len(row_dicts)
        for index, profile in enumerate(
            executor.map(derive_hypergraph_profile, row_dicts, chunksize=200),
            start=1,
        ):
            profiles.append(profile)
            if progress_label and (index == 1 or index % 5000 == 0 or index == total):
                print(f"{progress_label}: derived_profiles={index}/{total}", flush=True)
    return profiles


def build_directed_skill_hypergraph(
    profiles: list[HypergraphSkillProfile],
) -> DirectedHypergraph:
    nodes = set[str]()
    activation_edges: list[HyperEdge] = []
    emission_edges: list[HyperEdge] = []
    for profile in profiles:
        activation_edges.append(profile.activation_edge)
        emission_edges.append(profile.emission_edge)
        nodes.update(profile.activation_edge.tail)
        nodes.update(profile.activation_edge.head)
        nodes.update(profile.emission_edge.tail)
        nodes.update(profile.emission_edge.head)
    return DirectedHypergraph(
        nodes=tuple(sorted(nodes)),
        activation_edges=tuple(activation_edges),
        emission_edges=tuple(emission_edges),
        profiles={profile.skill_id: profile for profile in profiles},
    )


def _carrier_prevalence(profiles: Iterable[HypergraphSkillProfile]) -> dict[str, float]:
    profile_list = list(profiles)
    total = max(len(profile_list), 1)
    counts: Counter[str] = Counter()
    for profile in profile_list:
        for node in profile.carrier_nodes:
            counts[node.split(":", 1)[1]] += 1
    return {
        carrier: count / total
        for carrier, count in counts.items()
    }


def _candidate_profiles(
    profiles: list[HypergraphSkillProfile],
) -> tuple[list[HypergraphSkillProfile], list[HypergraphSkillProfile], list[HypergraphSkillProfile]]:
    sources = [profile for profile in profiles if profile.source_nodes]
    bridges = [
        profile
        for profile in profiles
        if profile.capabilities.transformer_or_stateful or profile.channel_nodes
    ]
    sinks = [profile for profile in profiles if profile.sink_nodes]
    return sources, bridges, sinks


def _build_activation_index(
    profiles: Iterable[HypergraphSkillProfile],
    *,
    allowed_nodes: set[str],
) -> dict[str, tuple[str, ...]]:
    buckets: dict[str, list[HypergraphSkillProfile]] = defaultdict(list)
    for profile in profiles:
        for node in profile.join_tail_nodes():
            if node in allowed_nodes:
                buckets[node].append(profile)
    index: dict[str, tuple[str, ...]] = {}
    for node, candidates in buckets.items():
        candidates.sort(
            key=lambda profile: (
                profile.downloads,
                profile.permission_flag_count,
                profile.stars,
            ),
            reverse=True,
        )
        index[node] = tuple(profile.skill_id for profile in candidates[:MAX_ACTIVATION_MATCHES_PER_NODE])
    return index


def _mean(values: Iterable[float]) -> float:
    value_list = list(values)
    if not value_list:
        return 0.0
    return float(mean(value_list))


def mine_hypergraph_risk_combinations(
    profiles: list[HypergraphSkillProfile],
    *,
    top_k: int | None = 500,
    source_limit: int = 400,
    bridge_limit: int = 80,
    sink_limit: int = 50,
) -> list[HypergraphRiskCombination]:
    if not profiles:
        return []
    graph = build_directed_skill_hypergraph(profiles)
    sources, bridges, sinks = _candidate_profiles(profiles)
    sources = sorted(
        sources,
        key=lambda profile: (profile.downloads, profile.permission_flag_count, profile.stars),
        reverse=True,
    )[:source_limit]
    bridge_ids = {profile.skill_id for profile in bridges}
    sink_ids = {profile.skill_id for profile in sinks}
    join_counts = Counter[str]()
    for profile in bridges + sinks:
        join_counts.update(profile.join_tail_nodes())
    allowed_join_nodes = {
        node
        for node, count in join_counts.items()
        if count <= MAX_JOIN_FANOUT
    }
    activation_index = _build_activation_index(bridges + sinks, allowed_nodes=allowed_join_nodes)
    max_downloads = math.log10(max(profile.downloads for profile in profiles) + 10.0)
    max_stars = math.log10(max(profile.stars for profile in profiles) + 10.0)
    carrier_prevalence = _carrier_prevalence(profiles)

    combinations: list[HypergraphRiskCombination] = []
    for source in sources:
        bridge_candidates: dict[str, tuple[float, tuple[str, ...]]] = {}
        for join_node in source.join_head_nodes():
            if join_node not in allowed_join_nodes:
                continue
            for bridge_id in activation_index.get(join_node, set()):
                if bridge_id == source.skill_id or bridge_id not in bridge_ids:
                    continue
                bridge = graph.profiles[bridge_id]
                shared_nodes = _unique_sorted(
                    set(source.emission_edge.head) & set(bridge.activation_edge.tail)
                )
                overlap = _weighted_overlap(shared_nodes, bridge.activation_edge.tail)
                if overlap <= 0.14:
                    continue
                pre_score = overlap + 0.20 * bridge.exposure_score(max_downloads=max_downloads, max_stars=max_stars)
                current = bridge_candidates.get(bridge_id)
                if current is None or pre_score > current[0]:
                    bridge_candidates[bridge_id] = (pre_score, shared_nodes)
        if not bridge_candidates:
            continue

        seen_bridge_ids: set[str] = set()
        ordered_bridges = sorted(
            (
                (score, graph.profiles[bridge_id], shared_nodes)
                for bridge_id, (score, shared_nodes) in bridge_candidates.items()
            ),
            key=lambda item: item[0],
            reverse=True,
        )
        for _, bridge, shared_source_bridge in ordered_bridges:
            if bridge.skill_id in seen_bridge_ids:
                continue
            seen_bridge_ids.add(bridge.skill_id)
            sink_candidates: dict[str, tuple[float, tuple[str, ...]]] = {}
            for join_node in bridge.join_head_nodes():
                if join_node not in allowed_join_nodes:
                    continue
                for sink_id in activation_index.get(join_node, set()):
                    if sink_id in {source.skill_id, bridge.skill_id} or sink_id not in sink_ids:
                        continue
                    sink = graph.profiles[sink_id]
                    shared_bridge_sink = _unique_sorted(
                        set(bridge.emission_edge.head) & set(sink.activation_edge.tail)
                    )
                    overlap = _weighted_overlap(shared_bridge_sink, sink.activation_edge.tail)
                    if overlap <= 0.14:
                        continue
                    pre_score = overlap + 0.25 * _sink_impact_prior(sink.primary_sink_type())
                    current = sink_candidates.get(sink_id)
                    if current is None or pre_score > current[0]:
                        sink_candidates[sink_id] = (pre_score, shared_bridge_sink)
            if not sink_candidates:
                continue

            ordered_sinks = sorted(
                (
                    (score, graph.profiles[sink_id], shared_nodes)
                    for sink_id, (score, shared_nodes) in sink_candidates.items()
                ),
                key=lambda item: item[0],
                reverse=True,
            )
            for _, sink, shared_bridge_sink in ordered_sinks[:sink_limit]:
                source_type = source.primary_source_type()
                sink_type = sink.primary_sink_type()
                source_bridge_overlap = _weighted_overlap(shared_source_bridge, bridge.activation_edge.tail)
                bridge_sink_overlap = _weighted_overlap(shared_bridge_sink, sink.activation_edge.tail)
                semantic_overlap = math.sqrt(max(source_bridge_overlap * bridge_sink_overlap, 0.0))
                if semantic_overlap <= 0.18:
                    continue

                channel_family, carrier_type = _primary_shared_carrier(
                    bridge,
                    shared_source_bridge,
                    shared_bridge_sink,
                )
                risk_outcome = _risk_outcome(sink, sink_type, channel_family, carrier_type)
                severity_tier = _severity_tier(risk_outcome, sink_type, carrier_type)
                authority_score = (
                    0.20 * source.authority_surface()
                    + 0.35 * bridge.authority_surface()
                    + 0.45 * sink.authority_surface()
                )
                impact_score = _sink_impact_prior(sink_type)
                source_score = _source_risk_prior(source_type)
                exposure_score = _mean(
                    (
                        source.exposure_score(max_downloads=max_downloads, max_stars=max_stars),
                        bridge.exposure_score(max_downloads=max_downloads, max_stars=max_stars),
                        sink.exposure_score(max_downloads=max_downloads, max_stars=max_stars),
                    )
                )
                audit_gap_score = _mean((bridge.audit_gap(), sink.audit_gap()))
                coordination_ease = _coordination_ease(
                    bridge.activation_edge.order,
                    sink.activation_edge.order,
                )
                carrier_score = _carrier_score(carrier_type, carrier_prevalence)
                base_score = (
                    0.26 * semantic_overlap
                    + 0.16 * carrier_score
                    + 0.15 * impact_score
                    + 0.12 * authority_score
                    + 0.09 * source_score
                    + 0.09 * exposure_score
                    + 0.06 * audit_gap_score
                    + 0.07 * coordination_ease
                )
                cross_domain = source.primary_domain() != sink.primary_domain()
                author_diversity = len(
                    {
                        author_handle
                        for author_handle in (
                            source.author_handle,
                            bridge.author_handle,
                            sink.author_handle,
                        )
                        if author_handle
                    }
                )
                risk_score = 100.0 * base_score
                risk_score *= 1.0 + (0.12 if cross_domain else 0.0) + 0.03 * max(author_diversity - 1, 0)
                risk_score = min(100.0, risk_score)
                rationale = ", ".join(
                    [
                        f"source={source_type}",
                        f"sink={sink_type}",
                        f"outcome={risk_outcome}",
                        f"severity={severity_tier}",
                        f"channel={channel_family}:{carrier_type}",
                        f"source_bridge={';'.join(shared_source_bridge[:4]) or 'none'}",
                        f"bridge_sink={';'.join(shared_bridge_sink[:4]) or 'none'}",
                    ]
                )
                combinations.append(
                    HypergraphRiskCombination(
                        category=_combination_category(source_type, sink_type),
                        schema_name=_schema_name(sink_type),
                        risk_outcome=risk_outcome,
                        severity_tier=severity_tier,
                        source_type=source_type,
                        sink_type=sink_type,
                        source_skill_id=source.skill_id,
                        source_name=source.name,
                        source_domain=source.primary_domain(),
                        bridge_skill_id=bridge.skill_id,
                        bridge_name=bridge.name,
                        bridge_domain=bridge.primary_domain(),
                        sink_skill_id=sink.skill_id,
                        sink_name=sink.name,
                        sink_domain=sink.primary_domain(),
                        channel_family=channel_family,
                        carrier_type=carrier_type,
                        cross_domain=cross_domain,
                        author_diversity=author_diversity,
                        source_bridge_overlap=source_bridge_overlap,
                        bridge_sink_overlap=bridge_sink_overlap,
                        semantic_overlap=semantic_overlap,
                        coordination_ease=coordination_ease,
                        carrier_score=carrier_score,
                        authority_score=authority_score,
                        impact_score=impact_score,
                        source_score=source_score,
                        exposure_score=exposure_score,
                        audit_gap_score=audit_gap_score,
                        risk_score=risk_score,
                        source_bridge_nodes=shared_source_bridge,
                        bridge_sink_nodes=shared_bridge_sink,
                        rationale=rationale,
                    )
                )

            if len(seen_bridge_ids) >= bridge_limit:
                break

    combinations = _deduplicate_combinations(combinations)
    combinations.sort(
        key=lambda combination: (
            combination.risk_score,
            combination.semantic_overlap,
            combination.authority_score,
        ),
        reverse=True,
    )
    if top_k is None:
        return combinations
    return combinations[:top_k]


def summarize_hypergraph_skill_risks(
    profiles: list[HypergraphSkillProfile],
    combinations: list[HypergraphRiskCombination],
    *,
    top_k: int | None = 50,
) -> list[HypergraphSkillRisk]:
    if not combinations:
        return []
    profile_map = {profile.skill_id: profile for profile in profiles}
    stats: dict[str, dict[str, object]] = defaultdict(
        lambda: {
            "as_source_count": 0,
            "as_bridge_count": 0,
            "as_sink_count": 0,
            "risk_sum": 0.0,
            "risk_weighted_count": 0.0,
            "max_risk_score": 0.0,
            "bridge_pairs": set(),
        }
    )

    for combination in combinations:
        for role, skill_id, weight in (
            ("source", combination.source_skill_id, 0.85),
            ("bridge", combination.bridge_skill_id, 1.00),
            ("sink", combination.sink_skill_id, 0.95),
        ):
            record = stats[skill_id]
            record[f"as_{role}_count"] = int(record[f"as_{role}_count"]) + 1
            record["risk_sum"] = float(record["risk_sum"]) + combination.risk_score * weight
            record["risk_weighted_count"] = float(record["risk_weighted_count"]) + weight
            record["max_risk_score"] = max(float(record["max_risk_score"]), combination.risk_score)
            if role == "bridge":
                cast_pair = (combination.source_skill_id, combination.sink_skill_id)
                cast_pairs = record["bridge_pairs"]
                assert isinstance(cast_pairs, set)
                cast_pairs.add(cast_pair)

    risks: list[HypergraphSkillRisk] = []
    for skill_id, record in stats.items():
        profile = profile_map[skill_id]
        weighted_count = max(float(record["risk_weighted_count"]), 1.0)
        mean_risk_score = float(record["risk_sum"]) / weighted_count
        bridge_brokerage = len(record["bridge_pairs"])
        aggregate_risk_score = (
            0.50 * float(record["max_risk_score"])
            + 0.30 * mean_risk_score
            + 20.0 * min(1.0, math.log1p(bridge_brokerage + int(record["as_bridge_count"])) / 5.0)
        )
        risks.append(
            HypergraphSkillRisk(
                skill_id=skill_id,
                name=profile.name,
                author_handle=profile.author_handle,
                downloads=profile.downloads,
                stars=profile.stars,
                as_source_count=int(record["as_source_count"]),
                as_bridge_count=int(record["as_bridge_count"]),
                as_sink_count=int(record["as_sink_count"]),
                total_incident_count=(
                    int(record["as_source_count"])
                    + int(record["as_bridge_count"])
                    + int(record["as_sink_count"])
                ),
                bridge_brokerage=bridge_brokerage,
                domain_span=len(profile.domain_nodes),
                carrier_span=len(profile.carrier_nodes),
                max_risk_score=float(record["max_risk_score"]),
                mean_risk_score=mean_risk_score,
                aggregate_risk_score=min(100.0, aggregate_risk_score),
            )
        )
    risks.sort(
        key=lambda risk: (risk.aggregate_risk_score, risk.bridge_brokerage, risk.max_risk_score),
        reverse=True,
    )
    if top_k is None:
        return risks
    return risks[:top_k]


def summarize_hypergraph_analysis(
    graph: DirectedHypergraph,
    combinations: list[HypergraphRiskCombination],
) -> HypergraphAnalysisSummary:
    node_family_counts = Counter(_node_family(node) for node in graph.nodes)
    source_type_counts = Counter(combination.source_type for combination in combinations)
    sink_type_counts = Counter(combination.sink_type for combination in combinations)
    carrier_counts = Counter(combination.carrier_type for combination in combinations)
    channel_family_counts = Counter(combination.channel_family for combination in combinations)
    semantic_channel_counts = Counter(
        node.split(":", 1)[1]
        for profile in graph.profiles.values()
        for node in profile.semantic_channel_nodes
    )
    system_channel_counts = Counter(
        node.split(":", 1)[1]
        for profile in graph.profiles.values()
        for node in profile.system_channel_nodes
    )
    outcome_counts = Counter(combination.risk_outcome for combination in combinations)
    severity_counts = Counter(combination.severity_tier for combination in combinations)
    schema_counts = Counter(combination.schema_name for combination in combinations)
    domain_cross_counts = Counter(
        f"{combination.source_domain}->{combination.sink_domain}" for combination in combinations
    )
    orders = [edge.order for edge in graph.activation_edges]
    sorted_orders = sorted(orders)
    if sorted_orders:
        middle = len(sorted_orders) // 2
        if len(sorted_orders) % 2 == 0:
            median_order = (sorted_orders[middle - 1] + sorted_orders[middle]) / 2.0
        else:
            median_order = float(sorted_orders[middle])
    else:
        median_order = 0.0
    source_candidates, bridge_candidates, sink_candidates = _candidate_profiles(list(graph.profiles.values()))
    return HypergraphAnalysisSummary(
        total_skills=len(graph.profiles),
        total_nodes=len(graph.nodes),
        total_hyperedges=graph.edge_count,
        total_combinations=len(combinations),
        source_candidates=len(source_candidates),
        bridge_candidates=len(bridge_candidates),
        sink_candidates=len(sink_candidates),
        node_family_counts=dict(node_family_counts),
        source_type_counts=dict(source_type_counts),
        sink_type_counts=dict(sink_type_counts),
        carrier_counts=dict(carrier_counts),
        channel_family_counts=dict(channel_family_counts),
        semantic_channel_counts=dict(semantic_channel_counts),
        system_channel_counts=dict(system_channel_counts),
        outcome_counts=dict(outcome_counts),
        severity_counts=dict(severity_counts),
        schema_counts=dict(schema_counts),
        domain_cross_counts=dict(domain_cross_counts),
        activation_order_mean=_mean(orders),
        activation_order_median=median_order,
    )


def summarize_hypergraph_channel_inventory(
    profiles: list[HypergraphSkillProfile],
) -> list[HypergraphChannelInventory]:
    channel_stats: dict[str, dict[str, object]] = defaultdict(
        lambda: {
            "skill_ids": set(),
            "source_skill_ids": set(),
            "bridge_skill_ids": set(),
            "sink_skill_ids": set(),
            "example_profiles": [],
        }
    )
    for profile in profiles:
        bridge_capable = bool(profile.channel_nodes) or profile.capabilities.transformer_or_stateful
        for node in (*profile.semantic_channel_nodes, *profile.system_channel_nodes):
            family, channel_name = node.split(":", 1)
            key = f"{family}:{channel_name}"
            record = channel_stats[key]
            cast_skill_ids = record["skill_ids"]
            assert isinstance(cast_skill_ids, set)
            cast_skill_ids.add(profile.skill_id)
            if profile.source_nodes:
                cast_source_ids = record["source_skill_ids"]
                assert isinstance(cast_source_ids, set)
                cast_source_ids.add(profile.skill_id)
            if bridge_capable:
                cast_bridge_ids = record["bridge_skill_ids"]
                assert isinstance(cast_bridge_ids, set)
                cast_bridge_ids.add(profile.skill_id)
            if profile.sink_nodes:
                cast_sink_ids = record["sink_skill_ids"]
                assert isinstance(cast_sink_ids, set)
                cast_sink_ids.add(profile.skill_id)
            cast_examples = record["example_profiles"]
            assert isinstance(cast_examples, list)
            cast_examples.append(profile)

    inventory: list[HypergraphChannelInventory] = []
    for key, record in channel_stats.items():
        channel_family, channel_name = key.split(":", 1)
        example_profiles = sorted(
            record["example_profiles"],
            key=lambda profile: (profile.downloads, profile.stars),
            reverse=True,
        )[:5]
        inventory.append(
            HypergraphChannelInventory(
                channel_family=channel_family,
                channel_subtype=_channel_subtype(channel_family, channel_name),
                channel_name=channel_name,
                skill_count=len(record["skill_ids"]),
                source_skill_count=len(record["source_skill_ids"]),
                bridge_skill_count=len(record["bridge_skill_ids"]),
                sink_skill_count=len(record["sink_skill_ids"]),
                example_skill_ids=tuple(profile.skill_id for profile in example_profiles),
            )
        )
    inventory.sort(
        key=lambda row: (row.channel_family, row.skill_count, row.source_skill_count, row.channel_name),
        reverse=True,
    )
    return inventory


def write_hypergraph_combinations_csv(
    path: Path,
    combinations: list[HypergraphRiskCombination],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "category",
                "schema_name",
                "risk_outcome",
                "severity_tier",
                "source_type",
                "sink_type",
                "source_skill_id",
                "source_name",
                "source_domain",
                "bridge_skill_id",
                "bridge_name",
                "bridge_domain",
                    "sink_skill_id",
                    "sink_name",
                    "sink_domain",
                    "channel_family",
                    "carrier_type",
                    "cross_domain",
                    "author_diversity",
                "source_bridge_overlap",
                "bridge_sink_overlap",
                "semantic_overlap",
                "coordination_ease",
                "carrier_score",
                "authority_score",
                "impact_score",
                "source_score",
                "exposure_score",
                "audit_gap_score",
                "risk_score",
                "source_bridge_nodes",
                "bridge_sink_nodes",
                "rationale",
            ],
        )
        writer.writeheader()
        for combination in combinations:
            writer.writerow(
                {
                    "category": combination.category,
                    "schema_name": combination.schema_name,
                    "risk_outcome": combination.risk_outcome,
                    "severity_tier": combination.severity_tier,
                    "source_type": combination.source_type,
                    "sink_type": combination.sink_type,
                    "source_skill_id": combination.source_skill_id,
                    "source_name": combination.source_name,
                    "source_domain": combination.source_domain,
                    "bridge_skill_id": combination.bridge_skill_id,
                    "bridge_name": combination.bridge_name,
                    "bridge_domain": combination.bridge_domain,
                    "sink_skill_id": combination.sink_skill_id,
                    "sink_name": combination.sink_name,
                    "sink_domain": combination.sink_domain,
                    "channel_family": combination.channel_family,
                    "carrier_type": combination.carrier_type,
                    "cross_domain": int(combination.cross_domain),
                    "author_diversity": combination.author_diversity,
                    "source_bridge_overlap": f"{combination.source_bridge_overlap:.6f}",
                    "bridge_sink_overlap": f"{combination.bridge_sink_overlap:.6f}",
                    "semantic_overlap": f"{combination.semantic_overlap:.6f}",
                    "coordination_ease": f"{combination.coordination_ease:.6f}",
                    "carrier_score": f"{combination.carrier_score:.6f}",
                    "authority_score": f"{combination.authority_score:.6f}",
                    "impact_score": f"{combination.impact_score:.6f}",
                    "source_score": f"{combination.source_score:.6f}",
                    "exposure_score": f"{combination.exposure_score:.6f}",
                    "audit_gap_score": f"{combination.audit_gap_score:.6f}",
                    "risk_score": f"{combination.risk_score:.6f}",
                    "source_bridge_nodes": "|".join(combination.source_bridge_nodes),
                    "bridge_sink_nodes": "|".join(combination.bridge_sink_nodes),
                    "rationale": combination.rationale,
                }
            )


def write_hypergraph_channel_inventory_csv(
    path: Path,
    inventory: list[HypergraphChannelInventory],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "channel_family",
                "channel_subtype",
                "channel_name",
                "skill_count",
                "source_skill_count",
                "bridge_skill_count",
                "sink_skill_count",
                "example_skill_ids",
            ],
        )
        writer.writeheader()
        for row in inventory:
            writer.writerow(
                {
                    "channel_family": row.channel_family,
                    "channel_subtype": row.channel_subtype,
                    "channel_name": row.channel_name,
                    "skill_count": row.skill_count,
                    "source_skill_count": row.source_skill_count,
                    "bridge_skill_count": row.bridge_skill_count,
                    "sink_skill_count": row.sink_skill_count,
                    "example_skill_ids": "|".join(row.example_skill_ids),
                }
            )


def write_hypergraph_skill_risks_csv(path: Path, risks: list[HypergraphSkillRisk]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "skill_id",
                "name",
                "author_handle",
                "downloads",
                "stars",
                "as_source_count",
                "as_bridge_count",
                "as_sink_count",
                "total_incident_count",
                "bridge_brokerage",
                "domain_span",
                "carrier_span",
                "max_risk_score",
                "mean_risk_score",
                "aggregate_risk_score",
            ],
        )
        writer.writeheader()
        for risk in risks:
            writer.writerow(
                {
                    "skill_id": risk.skill_id,
                    "name": risk.name,
                    "author_handle": risk.author_handle or "",
                    "downloads": risk.downloads,
                    "stars": risk.stars,
                    "as_source_count": risk.as_source_count,
                    "as_bridge_count": risk.as_bridge_count,
                    "as_sink_count": risk.as_sink_count,
                    "total_incident_count": risk.total_incident_count,
                    "bridge_brokerage": risk.bridge_brokerage,
                    "domain_span": risk.domain_span,
                    "carrier_span": risk.carrier_span,
                    "max_risk_score": f"{risk.max_risk_score:.6f}",
                    "mean_risk_score": f"{risk.mean_risk_score:.6f}",
                    "aggregate_risk_score": f"{risk.aggregate_risk_score:.6f}",
                }
            )


def _render_metric_table() -> list[str]:
    return [
        "| Metric | Definition | Dataset-backed indicator |",
        "| --- | --- | --- |",
        "| `source_bridge_overlap` | Weighted head-tail overlap between source emission and bridge activation. | Shared `object/channel` nodes induced from public skill metadata. |",
        "| `bridge_sink_overlap` | Weighted head-tail overlap between bridge emission and sink activation. | Shared `object/channel` nodes induced from public skill metadata. |",
        "| `semantic_overlap` | `sqrt(source_bridge_overlap * bridge_sink_overlap)` as static reachability prior. | Hyperedge composition strength in the directed hypergraph. |",
        "| `coordination_ease` | Penalty for large conjunctive tails. | Activation hyperedge order from observed precondition nodes. |",
        "| `carrier_score` | Persistence and reuse strength of the dominant semantic/system channel. | Channel type plus observed ecosystem prevalence. |",
        "| `authority_score` | Privilege surface needed by the combination. | Public permission hints (`requires_*`). |",
        "| `impact_score` | Severity of the terminal effect. | Stage 2 sink taxonomy (`exfil/publish/modify/execute/control/delete/transact`). |",
        "| `exposure_score` | Ecosystem exposure if a combination exists. | `downloads` and `stars`. |",
        "| `audit_gap_score` | Lower public assurance increases uncertainty and risk. | `scan_status`, `public_version_count`, `install_command`. |",
    ]


def write_hypergraph_report(
    path: Path,
    summary: HypergraphAnalysisSummary,
    combinations: list[HypergraphRiskCombination],
    skill_risks: list[HypergraphSkillRisk],
    channel_inventory: list[HypergraphChannelInventory],
    *,
    source_limit: int,
    bridge_limit: int,
    sink_limit: int,
    report_top_k: int = 20,
    skill_top_k: int = 20,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    semantic_inventory = [row for row in channel_inventory if row.channel_family == "semantic_channel"]
    system_inventory = [row for row in channel_inventory if row.channel_family == "system_channel"]
    with path.open("w", encoding="utf-8") as handle:
        handle.write("# Stage 2 Channel-Centric Hypergraph Risk Analysis\n\n")
        handle.write("## Model\n\n")
        handle.write(
            "Following the channel-centric intuition of IoTMon, we model each skill as a directed hypergraph "
            "fragment with two hyperedges: an `activation` edge from public precondition nodes to the skill "
            "node, and an `emission` edge from the skill node to output/state/effect nodes.\n\n"
        )
        handle.write("For skill `s`, the fragment is:\n\n")
        handle.write("```text\n")
        handle.write("T_activate(s) -> skill:s -> H_emit(s)\n")
        handle.write("```\n\n")
        handle.write("A static high-risk composition is a 3-skill hyperpath `(source, bridge, sink)` where:\n\n")
        handle.write("1. `H_emit(source) ∩ T_activate(bridge) != ∅`\n")
        handle.write("2. `H_emit(bridge) ∩ T_activate(sink) != ∅`\n")
        handle.write("3. the source carries a Stage 2 source type and the sink exposes a high-impact sink type\n\n")
        handle.write(
            "We explicitly distinguish `semantic_channel` nodes (for reusable state and content carriers such "
            "as memory, workspace, and transform buffers) from `system_channel` nodes (for "
            "OpenClaw runtime surfaces such as time, credential state, clipboard, keyboard/mouse I/O, screen "
            "capture, audio/video capture, desktop state, and device signals). This makes the hypergraph layer "
            "a candidate-discovery engine rather than a runtime proof of exploitability.\n\n"
        )
        handle.write("The final risk score is a weighted linear risk functional over structural overlap, "
                     "channel persistence, privilege surface, sink impact, source prior, exposure, "
                     "audit gap, and conjunctive coordination ease, followed by small bonuses for "
                     "cross-domain and cross-author compositions.\n\n")
        handle.write("## Metric Definitions\n\n")
        for line in _render_metric_table():
            handle.write(f"{line}\n")

        handle.write("\n## Search Configuration\n\n")
        handle.write(f"- source limit: `{source_limit}`\n")
        handle.write(f"- bridge candidates kept per source: `{bridge_limit}`\n")
        handle.write(f"- sink candidates kept per bridge: `{sink_limit}`\n")
        handle.write(f"- total combinations mined: `{summary.total_combinations}`\n\n")

        handle.write("## Graph Size\n\n")
        handle.write(f"- skills: `{summary.total_skills}`\n")
        handle.write(f"- hypernodes: `{summary.total_nodes}`\n")
        handle.write(f"- hyperedges: `{summary.total_hyperedges}`\n")
        handle.write(f"- source-capable skills: `{summary.source_candidates}`\n")
        handle.write(f"- bridge-capable skills: `{summary.bridge_candidates}`\n")
        handle.write(f"- sink-capable skills: `{summary.sink_candidates}`\n")
        handle.write(f"- mean activation order: `{summary.activation_order_mean:.2f}`\n")
        handle.write(f"- median activation order: `{summary.activation_order_median:.2f}`\n\n")

        handle.write("## Node Families\n\n")
        handle.write("| Family | Count |\n")
        handle.write("| --- | ---: |\n")
        for family, count in sorted(summary.node_family_counts.items()):
            handle.write(f"| {family} | {count} |\n")

        handle.write("\n## Schema Distribution\n\n")
        handle.write("| Schema | Count | Ratio |\n")
        handle.write("| --- | ---: | ---: |\n")
        total_combinations = max(summary.total_combinations, 1)
        for schema_name, count in sorted(summary.schema_counts.items(), key=lambda item: item[1], reverse=True):
            handle.write(f"| {schema_name} | {count} | {count / total_combinations:.2%} |\n")

        handle.write("\n## OpenClaw System Channels\n\n")
        handle.write("| System Channel | Subtype | Skills | Source-capable | Bridge-capable | Sink-capable | Example Skills |\n")
        handle.write("| --- | --- | ---: | ---: | ---: | ---: | --- |\n")
        for row in sorted(system_inventory, key=lambda item: item.skill_count, reverse=True):
            examples = ", ".join(f"`{skill_id}`" for skill_id in row.example_skill_ids[:3])
            handle.write(
                f"| {row.channel_name} | {row.channel_subtype} | {row.skill_count} | {row.source_skill_count} | "
                f"{row.bridge_skill_count} | {row.sink_skill_count} | {examples or '-'} |\n"
            )

        handle.write("\n## Semantic Channels\n\n")
        handle.write("| Semantic Channel | Subtype | Skills | Source-capable | Bridge-capable | Sink-capable | Example Skills |\n")
        handle.write("| --- | --- | ---: | ---: | ---: | ---: | --- |\n")
        for row in sorted(semantic_inventory, key=lambda item: item.skill_count, reverse=True):
            examples = ", ".join(f"`{skill_id}`" for skill_id in row.example_skill_ids[:3])
            handle.write(
                f"| {row.channel_name} | {row.channel_subtype} | {row.skill_count} | {row.source_skill_count} | "
                f"{row.bridge_skill_count} | {row.sink_skill_count} | {examples or '-'} |\n"
            )

        handle.write("\n## Risk Outcomes\n\n")
        handle.write("| Outcome | Count | Ratio |\n")
        handle.write("| --- | ---: | ---: |\n")
        for outcome, count in sorted(summary.outcome_counts.items(), key=lambda item: item[1], reverse=True):
            handle.write(f"| {outcome} | {count} | {count / total_combinations:.2%} |\n")

        handle.write("\n## Severity Distribution\n\n")
        handle.write("| Severity | Count | Ratio |\n")
        handle.write("| --- | ---: | ---: |\n")
        for severity, count in sorted(summary.severity_counts.items(), key=lambda item: item[1], reverse=True):
            handle.write(f"| {severity} | {count} | {count / total_combinations:.2%} |\n")

        handle.write("\n## Risky Channel Distribution\n\n")
        handle.write("| Channel | Family | Count | Ratio |\n")
        handle.write("| --- | --- | ---: | ---: |\n")
        risky_channel_family = {
            combination.carrier_type: combination.channel_family.removesuffix("_channel")
            for combination in combinations
        }
        for carrier_name, count in sorted(summary.carrier_counts.items(), key=lambda item: item[1], reverse=True):
            family = risky_channel_family.get(carrier_name, "unknown")
            handle.write(f"| {carrier_name} | {family} | {count} | {count / total_combinations:.2%} |\n")

        handle.write("\n## Trust Domain Crossings\n\n")
        handle.write("| Crossing | Count | Ratio |\n")
        handle.write("| --- | ---: | ---: |\n")
        for crossing, count in sorted(summary.domain_cross_counts.items(), key=lambda item: item[1], reverse=True):
            handle.write(f"| {crossing} | {count} | {count / total_combinations:.2%} |\n")

        handle.write("\n## Top Risky Skills\n\n")
        handle.write("| Skill | Role Counts (S/B/T) | Brokerage | Max Risk | Mean Risk | Aggregate |\n")
        handle.write("| --- | ---: | ---: | ---: | ---: | ---: |\n")
        for risk in skill_risks[:skill_top_k]:
            role_counts = f"{risk.as_source_count}/{risk.as_bridge_count}/{risk.as_sink_count}"
            handle.write(
                f"| `{risk.skill_id}` | {role_counts} | {risk.bridge_brokerage} | "
                f"{risk.max_risk_score:.2f} | {risk.mean_risk_score:.2f} | {risk.aggregate_risk_score:.2f} |\n"
            )

        handle.write("\n## Top Hypergraph Combinations\n\n")
        handle.write(
            "| Category | Outcome | Severity | Source | Bridge | Sink | Channel | Family | Overlap | Authority | Impact | Risk |\n"
        )
        handle.write("| --- | --- | --- | --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: |\n")
        for combination in combinations[:report_top_k]:
            handle.write(
                f"| {combination.category} | {combination.risk_outcome} | {combination.severity_tier} | `{combination.source_skill_id}` | "
                f"`{combination.bridge_skill_id}` | `{combination.sink_skill_id}` | "
                f"{combination.carrier_type} | {combination.channel_family} | {combination.semantic_overlap:.3f} | "
                f"{combination.authority_score:.3f} | {combination.impact_score:.3f} | {combination.risk_score:.2f} |\n"
            )


def generate_hypergraph_risk_artifacts(
    *,
    db_path: Path,
    output_dir: Path,
    source_limit: int = 400,
    bridge_limit: int = 80,
    sink_limit: int = 50,
    csv_top_k: int = 500,
    report_top_k: int = 20,
    skill_top_k: int = 50,
) -> dict[str, Path]:
    profiles = load_hypergraph_skill_profiles(db_path)
    graph = build_directed_skill_hypergraph(profiles)
    combinations = mine_hypergraph_risk_combinations(
        profiles,
        top_k=csv_top_k,
        source_limit=source_limit,
        bridge_limit=bridge_limit,
        sink_limit=sink_limit,
    )
    skill_risks = summarize_hypergraph_skill_risks(profiles, combinations, top_k=skill_top_k)
    channel_inventory = summarize_hypergraph_channel_inventory(profiles)
    summary = summarize_hypergraph_analysis(graph, combinations)

    output_dir.mkdir(parents=True, exist_ok=True)
    combinations_path = output_dir / "hypergraph_risk_combinations.csv"
    skills_path = output_dir / "hypergraph_risky_skills.csv"
    channels_path = output_dir / "hypergraph_channel_inventory.csv"
    report_path = output_dir / "hypergraph_risk_report.md"

    write_hypergraph_combinations_csv(combinations_path, combinations)
    write_hypergraph_skill_risks_csv(skills_path, skill_risks)
    write_hypergraph_channel_inventory_csv(channels_path, channel_inventory)
    write_hypergraph_report(
        report_path,
        summary,
        combinations,
        skill_risks,
        channel_inventory,
        source_limit=source_limit,
        bridge_limit=bridge_limit,
        sink_limit=sink_limit,
        report_top_k=report_top_k,
        skill_top_k=skill_top_k,
    )
    return {
        "combinations_csv": combinations_path,
        "skills_csv": skills_path,
        "channel_inventory_csv": channels_path,
        "report_markdown": report_path,
    }
