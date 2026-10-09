"""Graph2 pair-risk analysis aligned to the stricter pair threat model."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
import csv
from pathlib import Path
import sqlite3
from statistics import mean
from typing import Iterable

from clawhub_analyzer.toxicity import (
    HINT_COLUMNS,
    SkillCapabilities,
    derive_capabilities,
    input_object_types,
    output_object_types,
)


HIGH_IMPACT_ACTIONS = ("transact", "delete", "control", "execute", "modify", "exfil")
HIGH_RISK_LABELS = ("Sensitive", "Tainted")
ALLOWED_CHANNELS = {
    "workspace",
    "memory",
    "browser_state",
    "credential_state",
    "clipboard",
    "keyboard_io",
    "mouse_io",
    "screen_capture",
    "audio_capture",
    "video_capture",
    "desktop_state",
    "device_signal",
    "notification_signal",
    "popup_dialog",
    "window_focus",
    "power_state",
    "time",
}
GENERIC_JOIN_KEYS = {
    ("credential_state", "private_content"),
    ("credential_state", "raw_content"),
    ("credential_state", "secret"),
    ("credential_state", "state_snapshot"),
    ("browser_state", "private_content"),
    ("browser_state", "raw_content"),
    ("browser_state", "secret"),
    ("browser_state", "state_snapshot"),
    ("workspace", "private_content"),
    ("workspace", "secret"),
    ("workspace", "raw_content"),
}
MAX_SINKS_PER_KEY = 800
HIGH_RISK_OBJECTS = {
    "private_content",
    "secret",
    "observation_trace",
    "ui_instruction",
    "state_snapshot",
    "runtime_signal",
    "schedule_event",
    "raw_content",
}
SOURCE_LABEL_PRIORS = {
    "Sensitive": 0.90,
    "Tainted": 0.82,
}
ACTION_PRIORS = {
    "exfil": 0.74,
    "modify": 0.86,
    "execute": 0.92,
    "control": 0.93,
    "delete": 0.97,
    "transact": 1.00,
}
CHANNEL_PRIORS = {
    "memory": 0.82,
    "workspace": 0.88,
    "browser_state": 0.84,
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
    "time": 0.60,
}
OBJECT_PRIORS = {
    "private_content": 0.96,
    "secret": 1.00,
    "observation_trace": 0.84,
    "ui_instruction": 0.92,
    "state_snapshot": 0.78,
    "runtime_signal": 0.68,
    "schedule_event": 0.62,
    "raw_content": 0.58,
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
MAX_PRIVILEGE_WEIGHT = sum(PRIVILEGE_WEIGHTS.values())


@dataclass(frozen=True)
class Graph2SkillProfile:
    skill_id: str
    name: str
    author_handle: str | None
    downloads: int
    stars: int
    text: str
    hint_flags: dict[str, bool]
    capabilities: SkillCapabilities
    input_objects: tuple[str, ...]
    output_objects: tuple[str, ...]
    channels: tuple[str, ...]
    source_labels: tuple[str, ...]
    source_evidence: tuple[str, ...]
    sink_actions: tuple[str, ...]
    sink_evidence: tuple[str, ...]
    mediation_gates: tuple[str, ...]
    domains: tuple[str, ...]

    def authority_surface(self) -> float:
        if MAX_PRIVILEGE_WEIGHT <= 0:
            return 0.0
        weight = sum(
            PRIVILEGE_WEIGHTS[name]
            for name, enabled in self.hint_flags.items()
            if enabled and name in PRIVILEGE_WEIGHTS
        )
        return min(1.0, weight / MAX_PRIVILEGE_WEIGHT)


@dataclass(frozen=True)
class Graph2Pair:
    classification: str
    source_skill_id: str
    source_name: str
    sink_skill_id: str
    sink_name: str
    source_label: str
    sink_action: str
    shared_channels: tuple[str, ...]
    shared_objects: tuple[str, ...]
    gate_status: str
    mediation_gates: tuple[str, ...]
    state_evidence: tuple[str, ...]
    sink_evidence: tuple[str, ...]
    cross_author: bool
    cross_domain: bool
    authority_score: float
    score: float
    rationale: str


@dataclass(frozen=True)
class Graph2Summary:
    total_skills: int
    source_skills: int
    sink_skills: int
    reachable_pairs: int
    potentially_unsafe_pairs: int
    unmediated_candidates: int
    label_counts: dict[str, int]
    action_counts: dict[str, int]
    channel_counts: dict[str, int]
    gate_counts: dict[str, int]


def _normalize_text(*parts: object) -> str:
    return " ".join(str(part or "") for part in parts).lower()


def _contains_any(text: str, keywords: Iterable[str]) -> bool:
    return any(keyword in text for keyword in keywords)


def _unique_sorted(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(sorted(dict.fromkeys(value for value in values if value)))


def _load_rows(db_path: Path) -> list[sqlite3.Row]:
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    query = """
    SELECT
        s.skill_id,
        s.author_handle,
        COALESCE(s.name, s.skill_id) AS name,
        COALESCE(s.downloads, 0) AS downloads,
        COALESCE(s.stars, 0) AS stars,
        COALESCE(s.description, '') AS description,
        COALESCE(s.tags, '') AS tags,
        COALESCE(s.metadata_systems, '') AS metadata_systems,
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
    """
    rows = connection.execute(query).fetchall()
    connection.close()
    return rows


def _derive_source_labels(
    capabilities: SkillCapabilities,
    hint_flags: dict[str, bool],
    text: str,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    labels: list[str] = []
    evidence: list[str] = []
    if (
        hint_flags["requires_local_file_access"]
        or hint_flags["requires_docs"]
        or hint_flags["requires_pdf"]
        or hint_flags["requires_email"]
        or hint_flags["requires_calendar"]
        or hint_flags["requires_notion"]
        or hint_flags["requires_slack"]
        or hint_flags["requires_github"]
        or _contains_any(text, ["private", "secret", "credential", "token", "password", "workspace"])
    ):
        labels.append("Sensitive")
        evidence.append("explicit_sensitive")
    elif capabilities.sensitive_read:
        labels.append("Sensitive")
        evidence.append("inferred_sensitive")

    if hint_flags["requires_browser_access"] or _contains_any(
        text,
        [
            "search",
            "web",
            "browser",
            "crawl",
            "scrape",
            "feed",
            "rss",
            "remote content",
            "external content",
        ],
    ):
        labels.append("Tainted")
        evidence.append("explicit_tainted")
    elif capabilities.untrusted_input:
        labels.append("Tainted")
        evidence.append("inferred_tainted")

    return _unique_sorted(labels), _unique_sorted(evidence)


def _derive_sink_actions(
    capabilities: SkillCapabilities,
    hint_flags: dict[str, bool],
    text: str,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    actions: list[str] = []
    evidence: list[str] = []

    if _contains_any(
        text,
        [
            "purchase",
            "checkout",
            "payment",
            "invoice",
            "refund",
            "book ",
            "booking",
            "reserve",
            "reservation",
            "order ",
        ],
    ):
        actions.append("transact")
        evidence.append("explicit_transact")
    if _contains_any(text, ["delete", "erase", "wipe", "purge", "remove file", "remove folder"]):
        actions.append("delete")
        evidence.append("explicit_delete")
    if _contains_any(
        text,
        [
            "desktop control",
            "browser automation",
            "gui automation",
            "mouse control",
            "keyboard automation",
            "window management",
            "light scene",
            "philips hue",
            "openhue",
            "lights/scenes",
            "lights",
            "scenes",
            "iot",
            "device control",
        ],
    ):
        actions.append("control")
        evidence.append("explicit_control")
    if hint_flags["requires_shell_access"] and _contains_any(
        text,
        [
            "run command",
            "shell command",
            "execute",
            "terminal",
            "cli",
            "automation",
            "script",
        ],
    ):
        actions.append("execute")
        evidence.append("explicit_execute")
    if hint_flags["requires_local_file_access"] and _contains_any(
        text,
        [
            "write",
            "update",
            "edit",
            "modify",
            "create",
            "save",
            "commit",
            "restore",
            "apply",
            "overwrite",
            "sync",
        ],
    ):
        actions.append("modify")
        evidence.append("explicit_modify")
    if (
        hint_flags["requires_shell_access"] or hint_flags["requires_local_file_access"]
    ) and _contains_any(
        text,
        [
            "config update",
            "config updates",
            "config apply",
            "apply config",
            "rollback",
            "restore",
            "restore from backup",
            "backup and restore",
            "overwrite",
            "rollback snapshot",
        ],
    ):
        actions.append("modify")
        evidence.append("explicit_modify_restore")
    if (
        hint_flags["requires_api_key"]
        or hint_flags["requires_oauth"]
        or hint_flags["requires_token"]
        or _contains_any(
            text,
            [
                "upload",
                "publish",
                "post to",
                "send ",
                "webhook",
                "api endpoint",
                "slack message",
                "email",
                "github issue",
                "pull request",
            ],
        )
    ):
        actions.append("exfil")
        evidence.append("explicit_exfil")

    return _unique_sorted(actions), _unique_sorted(evidence)


def _derive_gates(text: str) -> tuple[str, ...]:
    gates: list[str] = []
    if _contains_any(
        text,
        [
            "approval",
            "approved",
            "confirm",
            "confirmation",
            "consent",
            "ask before",
            "book now code",
            "token required",
        ],
    ):
        gates.append("Auth")
    if _contains_any(
        text,
        [
            "validate",
            "verify",
            "check",
            "inspect",
            "preview",
            "dry-run",
            "checksum",
            "lint",
        ],
    ):
        gates.append("Validate")
    if _contains_any(
        text,
        [
            "sanitize",
            "redact",
            "mask",
            "filter sensitive",
            "strip secrets",
            "declassify",
        ],
    ):
        gates.append("Declassify")
    if _contains_any(
        text,
        [
            "guardrail",
            "guard",
            "policy",
            "only allowed",
            "never",
            "blocked unless",
            "safety",
            "safe mode",
        ],
    ):
        gates.append("Policy")
    return _unique_sorted(gates)


def _derive_domains(capabilities: SkillCapabilities) -> tuple[str, ...]:
    return _unique_sorted(capabilities.domains())


def _extra_channels(text: str) -> tuple[str, ...]:
    channels: list[str] = []
    if _contains_any(
        text,
        [
            "config",
            "openclaw",
            "backup",
            "restore",
            "rollback",
            "~/.openclaw",
            ".openclaw",
            "archive",
        ],
    ):
        channels.append("workspace")
    if _contains_any(
        text,
        [
            "philips hue",
            "openhue",
            "light scene",
            "lights",
            "scenes",
            "device control",
        ],
    ):
        channels.append("device_signal")
    return _unique_sorted(channels)


def _extra_input_objects(text: str, channels: tuple[str, ...]) -> tuple[str, ...]:
    objects: list[str] = []
    if "workspace" in channels and _contains_any(
        text,
        [
            "config",
            "backup",
            "restore",
            "rollback",
            "workspace",
            "openclaw",
            "archive",
            "directory",
            "file",
        ],
    ):
        objects.extend(["state_snapshot", "private_content"])
    if "device_signal" in channels and _contains_any(
        text,
        [
            "philips hue",
            "openhue",
            "light",
            "lights",
            "scene",
            "scenes",
        ],
    ):
        objects.extend(["runtime_signal", "ui_instruction"])
    return _unique_sorted(objects)


def _extra_output_objects(text: str, channels: tuple[str, ...]) -> tuple[str, ...]:
    objects: list[str] = []
    if "workspace" in channels and _contains_any(
        text,
        [
            "backup",
            "restore",
            "rollback",
            "config",
            "workspace",
            "openclaw",
            "archive",
        ],
    ):
        objects.append("state_snapshot")
    if "device_signal" in channels and _contains_any(
        text,
        [
            "light",
            "lights",
            "scene",
            "scenes",
            "hue",
        ],
    ):
        objects.append("runtime_signal")
    return _unique_sorted(objects)


def derive_graph2_profile(row: sqlite3.Row) -> Graph2SkillProfile:
    capabilities = derive_capabilities(row)
    hint_flags = {column: bool(row[column]) for column in HINT_COLUMNS}
    text = _normalize_text(row["name"], row["description"], row["tags"], row["metadata_systems"])
    base_channels = [
        channel
        for channel in (*capabilities.semantic_channels(), *capabilities.system_channels())
        if channel in ALLOWED_CHANNELS
    ]
    extra_channels = [channel for channel in _extra_channels(text) if channel in ALLOWED_CHANNELS]
    channels = _unique_sorted((*base_channels, *extra_channels))
    input_objects = _unique_sorted(
        obj
        for obj in (*input_object_types(capabilities), *_extra_input_objects(text, channels))
        if obj in HIGH_RISK_OBJECTS
    )
    output_objects = _unique_sorted(
        obj
        for obj in (*output_object_types(capabilities), *_extra_output_objects(text, channels))
        if obj in HIGH_RISK_OBJECTS
    )
    source_labels, source_evidence = _derive_source_labels(capabilities, hint_flags, text)
    sink_actions, sink_evidence = _derive_sink_actions(capabilities, hint_flags, text)
    return Graph2SkillProfile(
        skill_id=capabilities.skill_id,
        name=capabilities.name,
        author_handle=capabilities.author_handle,
        downloads=capabilities.downloads,
        stars=capabilities.stars,
        text=text,
        hint_flags=hint_flags,
        capabilities=capabilities,
        input_objects=_unique_sorted(input_objects),
        output_objects=_unique_sorted(output_objects),
        channels=_unique_sorted(channels),
        source_labels=source_labels,
        source_evidence=source_evidence,
        sink_actions=sink_actions,
        sink_evidence=sink_evidence,
        mediation_gates=_derive_gates(text),
        domains=_derive_domains(capabilities),
    )


def load_graph2_skill_profiles(db_path: Path) -> list[Graph2SkillProfile]:
    return [derive_graph2_profile(row) for row in _load_rows(db_path)]


def _pair_keys(profile: Graph2SkillProfile, *, source_side: bool) -> set[tuple[str, str]]:
    objects = profile.output_objects if source_side else profile.input_objects
    return {
        (channel, obj)
        for channel in profile.channels
        for obj in objects
        if (channel, obj) not in GENERIC_JOIN_KEYS
    }


def _pick_label(labels: tuple[str, ...]) -> str:
    if "Sensitive" in labels:
        return "Sensitive"
    if "Tainted" in labels:
        return "Tainted"
    return ""


def _pick_action(actions: tuple[str, ...]) -> str:
    for action in HIGH_IMPACT_ACTIONS:
        if action in actions:
            return action
    return ""


def _gate_status(profile: Graph2SkillProfile) -> str:
    if profile.mediation_gates:
        return "present"
    return "missing"


def _sink_priority(profile: Graph2SkillProfile) -> tuple[float, float, int, int]:
    action = _pick_action(profile.sink_actions)
    gate_bonus = 1.0 if not profile.mediation_gates else 0.0
    return (
        ACTION_PRIORS.get(action, 0.0),
        gate_bonus + profile.authority_surface(),
        profile.downloads,
        profile.stars,
    )


def _score_pair(
    source: Graph2SkillProfile,
    sink: Graph2SkillProfile,
    source_label: str,
    sink_action: str,
    shared_channels: tuple[str, ...],
    shared_objects: tuple[str, ...],
    gate_status: str,
) -> float:
    label_score = SOURCE_LABEL_PRIORS.get(source_label, 0.50)
    action_score = ACTION_PRIORS.get(sink_action, 0.70)
    channel_score = max((CHANNEL_PRIORS.get(channel, 0.60) for channel in shared_channels), default=0.60)
    object_score = max((OBJECT_PRIORS.get(obj, 0.60) for obj in shared_objects), default=0.60)
    authority = mean((source.authority_surface(), sink.authority_surface()))
    score = (
        0.28 * label_score
        + 0.27 * action_score
        + 0.16 * channel_score
        + 0.12 * object_score
        + 0.12 * authority
        + 0.05 * min(1.0, (source.downloads + sink.downloads) / 50000.0)
    )
    if source.author_handle and sink.author_handle and source.author_handle != sink.author_handle:
        score += 0.03
    if set(source.domains) and set(sink.domains) and set(source.domains).isdisjoint(set(sink.domains)):
        score += 0.05
    if gate_status == "present":
        score *= 0.72
    return round(min(1.0, score) * 100.0, 6)


def _classification(
    source: Graph2SkillProfile,
    sink: Graph2SkillProfile,
    gate_status: str,
) -> str | None:
    source_label = _pick_label(source.source_labels)
    sink_action = _pick_action(sink.sink_actions)
    if not source_label or not sink_action:
        return None
    if gate_status == "missing" and any(ev.startswith("explicit_") for ev in source.source_evidence):
        return "unmediated_candidate"
    return "potentially_unsafe"


def _build_rationale(
    source_label: str,
    sink_action: str,
    shared_channels: tuple[str, ...],
    shared_objects: tuple[str, ...],
    gate_status: str,
    mediation_gates: tuple[str, ...],
) -> str:
    bits = [
        f"label={source_label}",
        f"action={sink_action}",
        f"channels={','.join(shared_channels[:3]) or 'none'}",
        f"objects={','.join(shared_objects[:3]) or 'none'}",
        f"gate_status={gate_status}",
    ]
    if mediation_gates:
        bits.append(f"gates={','.join(mediation_gates)}")
    return ", ".join(bits)


def mine_graph2_pairs(
    profiles: list[Graph2SkillProfile],
    *,
    top_k: int | None = 500,
    sink_allowlist: set[str] | None = None,
) -> list[Graph2Pair]:
    if not profiles:
        return []

    sources = [profile for profile in profiles if profile.source_labels and profile.channels and profile.output_objects]
    sinks = [
        profile
        for profile in profiles
        if profile.sink_actions
        and profile.channels
        and profile.input_objects
        and (sink_allowlist is None or profile.skill_id in sink_allowlist)
    ]
    sink_map = {sink.skill_id: sink for sink in sinks}

    sink_index: dict[tuple[str, str], list[Graph2SkillProfile]] = defaultdict(list)
    for sink in sinks:
        for key in _pair_keys(sink, source_side=False):
            sink_index[key].append(sink)
    for key, sink_list in sink_index.items():
        sink_list.sort(key=_sink_priority, reverse=True)
        if len(sink_list) > MAX_SINKS_PER_KEY:
            sink_index[key] = sink_list[:MAX_SINKS_PER_KEY]

    pairs_by_identity: dict[tuple[str, str, str, str], Graph2Pair] = {}
    for source in sources:
        sink_matches: dict[str, dict[str, set[str]]] = defaultdict(lambda: {"channels": set(), "objects": set()})
        for channel, obj in _pair_keys(source, source_side=True):
            for sink in sink_index.get((channel, obj), ()):
                if sink.skill_id == source.skill_id:
                    continue
                sink_matches[sink.skill_id]["channels"].add(channel)
                sink_matches[sink.skill_id]["objects"].add(obj)

        for sink_id, matched in sink_matches.items():
            sink = sink_map[sink_id]
            shared_channels = _unique_sorted(matched["channels"])
            shared_objects = _unique_sorted(matched["objects"])
            if not shared_channels or not shared_objects:
                continue
            source_label = _pick_label(source.source_labels)
            sink_action = _pick_action(sink.sink_actions)
            gate_status = _gate_status(sink)
            classification = _classification(source, sink, gate_status)
            if classification is None:
                continue
            cross_author = bool(
                source.author_handle
                and sink.author_handle
                and source.author_handle != sink.author_handle
            )
            cross_domain = bool(set(source.domains) and set(sink.domains) and set(source.domains).isdisjoint(set(sink.domains)))
            authority_score = round(mean((source.authority_surface(), sink.authority_surface())), 6)
            score = _score_pair(source, sink, source_label, sink_action, shared_channels, shared_objects, gate_status)
            pair = Graph2Pair(
                classification=classification,
                source_skill_id=source.skill_id,
                source_name=source.name,
                sink_skill_id=sink.skill_id,
                sink_name=sink.name,
                source_label=source_label,
                sink_action=sink_action,
                shared_channels=shared_channels,
                shared_objects=shared_objects,
                gate_status=gate_status,
                mediation_gates=sink.mediation_gates,
                state_evidence=source.source_evidence,
                sink_evidence=sink.sink_evidence,
                cross_author=cross_author,
                cross_domain=cross_domain,
                authority_score=authority_score,
                score=score,
                rationale=_build_rationale(
                    source_label,
                    sink_action,
                    shared_channels,
                    shared_objects,
                    gate_status,
                    sink.mediation_gates,
                ),
            )
            identity = (
                pair.source_skill_id,
                pair.sink_skill_id,
                pair.source_label,
                pair.sink_action,
            )
            current = pairs_by_identity.get(identity)
            if current is None or pair.score > current.score:
                pairs_by_identity[identity] = pair

    pairs = list(pairs_by_identity.values())
    pairs.sort(
        key=lambda pair: (
            {"unmediated_candidate": 2, "potentially_unsafe": 1}.get(pair.classification, 0),
            pair.score,
        ),
        reverse=True,
    )
    if top_k is None:
        return pairs
    return pairs[:top_k]


def summarize_graph2_pairs(
    profiles: list[Graph2SkillProfile],
    pairs: list[Graph2Pair],
) -> Graph2Summary:
    label_counts = Counter(pair.source_label for pair in pairs)
    action_counts = Counter(pair.sink_action for pair in pairs)
    channel_counts = Counter(channel for pair in pairs for channel in pair.shared_channels)
    gate_counts = Counter(pair.gate_status for pair in pairs)
    return Graph2Summary(
        total_skills=len(profiles),
        source_skills=sum(1 for profile in profiles if profile.source_labels),
        sink_skills=sum(1 for profile in profiles if profile.sink_actions),
        reachable_pairs=len(pairs),
        potentially_unsafe_pairs=sum(1 for pair in pairs if pair.classification in {"potentially_unsafe", "unmediated_candidate"}),
        unmediated_candidates=sum(1 for pair in pairs if pair.classification == "unmediated_candidate"),
        label_counts=dict(label_counts),
        action_counts=dict(action_counts),
        channel_counts=dict(channel_counts),
        gate_counts=dict(gate_counts),
    )


def write_graph2_pairs_csv(path: Path, pairs: list[Graph2Pair]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "rank",
                "classification",
                "source_skill_id",
                "source_name",
                "sink_skill_id",
                "sink_name",
                "source_label",
                "sink_action",
                "shared_channels",
                "shared_objects",
                "gate_status",
                "mediation_gates",
                "state_evidence",
                "sink_evidence",
                "cross_author",
                "cross_domain",
                "authority_score",
                "score",
                "rationale",
            ],
        )
        writer.writeheader()
        for index, pair in enumerate(pairs, start=1):
            writer.writerow(
                {
                    "rank": index,
                    "classification": pair.classification,
                    "source_skill_id": pair.source_skill_id,
                    "source_name": pair.source_name,
                    "sink_skill_id": pair.sink_skill_id,
                    "sink_name": pair.sink_name,
                    "source_label": pair.source_label,
                    "sink_action": pair.sink_action,
                    "shared_channels": "|".join(pair.shared_channels),
                    "shared_objects": "|".join(pair.shared_objects),
                    "gate_status": pair.gate_status,
                    "mediation_gates": "|".join(pair.mediation_gates),
                    "state_evidence": "|".join(pair.state_evidence),
                    "sink_evidence": "|".join(pair.sink_evidence),
                    "cross_author": int(pair.cross_author),
                    "cross_domain": int(pair.cross_domain),
                    "authority_score": f"{pair.authority_score:.6f}",
                    "score": f"{pair.score:.6f}",
                    "rationale": pair.rationale,
                }
            )


def write_graph2_report(path: Path, summary: Graph2Summary, pairs: list[Graph2Pair], *, top_k: int = 30) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        handle.write("# Graph2 Pair Threat Analysis\n\n")
        handle.write("## Scope\n\n")
        handle.write(
            "Graph2 reports static pair-risk candidates under the stricter pair threat model. "
            "It distinguishes structural reachability from potentially unsafe pairs and prioritizes "
            "pairs with no evident mediation gate in the public metadata. "
            "To avoid ubiquitous storage matches dominating the graph, the analysis filters a small set of "
            "generic join keys such as `credential_state/private_content` and `workspace/private_content`. "
            f"For high-fanout keys, it also caps candidate sinks per join key at `{MAX_SINKS_PER_KEY}` before pair ranking.\n\n"
        )
        handle.write("## Summary\n\n")
        handle.write(f"- total skills: `{summary.total_skills}`\n")
        handle.write(f"- source-labeled skills: `{summary.source_skills}`\n")
        handle.write(f"- sink-capable skills: `{summary.sink_skills}`\n")
        handle.write(f"- reachable pairs exported: `{summary.reachable_pairs}`\n")
        handle.write(f"- potentially unsafe pairs: `{summary.potentially_unsafe_pairs}`\n")
        handle.write(f"- unmediated candidates: `{summary.unmediated_candidates}`\n\n")

        handle.write("## Counts by Source Label\n\n")
        handle.write("| Label | Count |\n")
        handle.write("| --- | ---: |\n")
        for label, count in sorted(summary.label_counts.items(), key=lambda item: item[1], reverse=True):
            handle.write(f"| {label} | {count} |\n")

        handle.write("\n## Counts by Sink Action\n\n")
        handle.write("| Action | Count |\n")
        handle.write("| --- | ---: |\n")
        for action, count in sorted(summary.action_counts.items(), key=lambda item: item[1], reverse=True):
            handle.write(f"| {action} | {count} |\n")

        handle.write("\n## Gate Status\n\n")
        handle.write("| Gate Status | Count |\n")
        handle.write("| --- | ---: |\n")
        for gate_status, count in sorted(summary.gate_counts.items(), key=lambda item: item[1], reverse=True):
            handle.write(f"| {gate_status} | {count} |\n")

        handle.write("\n## Top Pairs\n\n")
        handle.write("| Rank | Class | Source | Sink | Label | Action | Channels | Objects | Gate | Score |\n")
        handle.write("| ---: | --- | --- | --- | --- | --- | --- | --- | --- | ---: |\n")
        for index, pair in enumerate(pairs[:top_k], start=1):
            handle.write(
                f"| {index} | {pair.classification} | `{pair.source_skill_id}` | `{pair.sink_skill_id}` | "
                f"{pair.source_label} | {pair.sink_action} | {','.join(pair.shared_channels[:2])} | "
                f"{','.join(pair.shared_objects[:2])} | {pair.gate_status} | {pair.score:.2f} |\n"
            )


def generate_graph2_pair_artifacts(
    *,
    db_path: Path,
    output_dir: Path,
    top_k: int = 500,
    report_top_k: int = 30,
    sink_allowlist: set[str] | None = None,
) -> dict[str, Path]:
    profiles = load_graph2_skill_profiles(db_path)
    pairs = mine_graph2_pairs(profiles, top_k=top_k, sink_allowlist=sink_allowlist)
    summary = summarize_graph2_pairs(profiles, pairs)

    output_dir.mkdir(parents=True, exist_ok=True)
    pairs_path = output_dir / "graph2_pair_candidates.csv"
    report_path = output_dir / "graph2_report.md"
    write_graph2_pairs_csv(pairs_path, pairs)
    write_graph2_report(report_path, summary, pairs, top_k=report_top_k)
    return {
        "pairs_csv": pairs_path,
        "report_markdown": report_path,
    }
