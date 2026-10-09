"""Shared helpers for channel attribution confidence and pair plausibility."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from clawhub_analyzer.hypergraph import HypergraphSkillProfile


CHANNEL_KEYWORDS: dict[str, tuple[str, ...]] = {
    "memory": ("memory", "remember", "recall", "persist", "store memory", "long-term"),
    "workspace": ("workspace", "file", "folder", "directory", "project", "repository"),
    "credential_state": ("api key", "oauth", "token", "secret", "credential", "auth"),
    "audio_capture": ("audio", "voice", "microphone", "speech", "record", "transcri"),
    "video_capture": ("video", "camera", "stream", "record", "youtube", "mp4"),
    "device_signal": ("device", "smart home", "iot", "actuator", "sensor", "hue", "home assistant"),
    "desktop_state": ("desktop", "screen", "window", "gui", "ui automat", "computer use"),
    "keyboard_io": ("keyboard", "keystroke", "type", "input"),
    "mouse_io": ("mouse", "click", "cursor"),
    "screen_capture": ("screenshot", "screen capture", "display"),
    "browser_state": ("browser", "web page", "url", "chrome", "firefox", "selenium"),
    "clipboard": ("clipboard", "copy", "paste"),
    "transform_buffer": ("transform", "convert", "process", "pipeline"),
}

SEMANTIC_CLUSTERS: tuple[tuple[str, ...], ...] = (
    ("file", "folder", "directory", "document", "pdf", "note", "markdown", "storage", "drive", "workspace", "project", "repo", "git"),
    ("web", "search", "browser", "url", "http", "api", "rest", "fetch", "scrape", "crawl", "internet", "online"),
    ("audio", "video", "music", "podcast", "transcri", "stream", "youtube", "media", "voice", "speech", "mp3", "mp4"),
    ("calendar", "task", "todo", "remind", "schedule", "event", "meeting", "booking", "appointment", "agenda", "planner"),
    ("payment", "pay", "wallet", "crypto", "bitcoin", "solana", "invoice", "bank", "stripe", "finance", "money", "transact"),
    ("home", "iot", "hue", "device", "sensor", "actuator", "light", "switch", "smart", "homeassistant", "alexa", "gpio"),
    ("code", "github", "git", "developer", "programming", "debug", "build", "deploy", "ci", "test", "lint"),
    ("email", "mail", "slack", "discord", "telegram", "sms", "message", "chat", "social", "twitter", "linkedin"),
    ("memory", "remember", "knowledge", "rag", "embedding", "vector", "semantic", "context", "recall", "store"),
    ("shop", "order", "cart", "product", "inventory", "store", "purchase", "amazon", "shopify", "ecommerce"),
)

DEFAULT_MIN_CONFIDENCE = 0.10
DEFAULT_MIN_SOURCE_INSTALLS = 200
DEFAULT_MIN_SINK_INSTALLS = 0


@dataclass(frozen=True)
class ChannelConfidenceEvidence:
    carrier: str
    confidence: float
    support_count: int
    signal_count: int
    matched_signals: tuple[str, ...]


def _stringify_signal(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple, set)):
        return " ".join(str(item) for item in value)
    return str(value)


def _skill_slug_text(skill_id: str) -> str:
    return skill_id.lower().replace("/", " ").replace("-", " ").replace("_", " ")


def channel_signal_texts(profile: HypergraphSkillProfile) -> dict[str, str]:
    caps = profile.capabilities
    return {
        "description": (caps.description or "").lower(),
        "tags": _stringify_signal(getattr(caps, "tags", "")).lower(),
        "metadata_systems": _stringify_signal(getattr(caps, "metadata_systems", "")).lower(),
        "install_command": (profile.install_command or "").lower(),
        "skill_id": _skill_slug_text(profile.skill_id),
    }


def channel_confidence_evidence(
    profile: HypergraphSkillProfile,
    carrier: str,
) -> ChannelConfidenceEvidence:
    keywords = CHANNEL_KEYWORDS.get(carrier, ())
    if not keywords:
        return ChannelConfidenceEvidence(
            carrier=carrier,
            confidence=0.5,
            support_count=0,
            signal_count=0,
            matched_signals=(),
        )

    signal_map = channel_signal_texts(profile)
    matched = tuple(
        signal_name
        for signal_name, text in signal_map.items()
        if any(keyword in text for keyword in keywords)
    )
    signal_count = len(signal_map)
    confidence = len(matched) / signal_count if signal_count else 0.0
    return ChannelConfidenceEvidence(
        carrier=carrier,
        confidence=round(confidence, 3),
        support_count=len(matched),
        signal_count=signal_count,
        matched_signals=matched,
    )


def pair_skill_text(profile: HypergraphSkillProfile) -> str:
    caps = profile.capabilities
    return " ".join(
        part
        for part in (
            caps.description or "",
            _stringify_signal(getattr(caps, "tags", "")),
            _stringify_signal(getattr(caps, "metadata_systems", "")),
            profile.name or "",
            _skill_slug_text(profile.skill_id),
        )
        if part
    ).lower()


def shared_clusters(src_text: str, snk_text: str) -> int:
    count = 0
    for cluster in SEMANTIC_CLUSTERS:
        src_hit = any(keyword in src_text for keyword in cluster)
        snk_hit = any(keyword in snk_text for keyword in cluster)
        if src_hit and snk_hit:
            count += 1
    return count


def plausibility_score(
    conf: float,
    src_installs: int,
    snk_installs: int,
    clusters: int,
    max_installs: int,
) -> float:
    if max_installs <= 0:
        max_installs = 1
    conf_score = max(0.0, min(conf, 1.0))
    src_score = min(max(src_installs, 0), max_installs) / max_installs
    snk_score = min(max(snk_installs, 0), max_installs) / max_installs
    cluster_score = min(max(clusters, 0), 3) / 3
    return round(conf_score + src_score + snk_score + cluster_score, 4)


def ecological_filter_reason(
    *,
    conf: float,
    src_installs: int,
    snk_installs: int,
    clusters: int,
    min_confidence: float = DEFAULT_MIN_CONFIDENCE,
    min_source_installs: int = DEFAULT_MIN_SOURCE_INSTALLS,
    min_sink_installs: int = DEFAULT_MIN_SINK_INSTALLS,
) -> str | None:
    if conf < min_confidence:
        return "low_confidence"
    if snk_installs < min_sink_installs:
        return "low_sink_installs"
    if src_installs < min_source_installs:
        return "low_source_installs"
    if clusters < 1:
        return "no_shared_cluster"
    return None


def row_skill_text(row: Mapping[str, object]) -> str:
    return " ".join(
        part
        for part in (
            _stringify_signal(row.get("description")),
            _stringify_signal(row.get("tags")),
            _stringify_signal(row.get("metadata_systems")),
            _stringify_signal(row.get("name")),
            _skill_slug_text(_stringify_signal(row.get("skill_id"))),
        )
        if part
    ).lower()
