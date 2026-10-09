from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import csv
from collections import Counter
from functools import lru_cache
import itertools
import math
import random
import re
import sqlite3
from typing import Iterable


HINT_COLUMNS = [
    "requires_api_key",
    "requires_oauth",
    "requires_token",
    "requires_local_file_access",
    "requires_shell_access",
    "requires_browser_access",
    "requires_github",
    "requires_slack",
    "requires_notion",
    "requires_calendar",
    "requires_email",
    "requires_docs",
    "requires_pdf",
]


def _normalize_text(*parts: object) -> str:
    text = " ".join(str(part or "") for part in parts)
    return text.lower()


def _contains_any(text: str, keywords: Iterable[str]) -> bool:
    return any(keyword in text for keyword in keywords)


@lru_cache(maxsize=2048)
def _token_regex(token: str) -> re.Pattern[str]:
    pattern = rf"(?<![a-z0-9]){re.escape(token)}(?![a-z0-9])"
    return re.compile(pattern)


def _contains_token(text: str, token: str) -> bool:
    return _token_regex(token).search(text) is not None


def _contains_any_token(text: str, keywords: Iterable[str]) -> bool:
    return any(_contains_token(text, keyword) for keyword in keywords)


@lru_cache(maxsize=1024)
def _compiled_pattern(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern)


def _contains_any_pattern(text: str, patterns: Iterable[str]) -> bool:
    return any(_compiled_pattern(pattern).search(text) is not None for pattern in patterns)


def _mouse_io_hint(text: str) -> bool:
    return _contains_any_token(
        text,
        [
            "mouse",
            "double-click",
            "right-click",
            "left-click",
            "hover",
        ],
    ) or _contains_any(
        text,
        [
            "click on ",
            "click the ",
            "click a ",
            "click an ",
            "mouse click",
            "button click",
            "move cursor",
            "cursor position",
            "cursor movement",
            "move the cursor",
            "cursor control",
            "mouse pointer",
            "pointer position",
            "pointer movement",
            "drag and drop",
            "scroll up",
            "scroll down",
            "scroll the page",
            "mouse wheel",
        ],
    )


def _audio_capture_hint(text: str) -> bool:
    return _contains_any_token(
        text,
        [
            "audio",
            "microphone",
            "mic",
            "speech",
            "dictation",
            "transcribe",
            "transcript",
            "transcription",
            "transcriber",
            "whisper",
        ],
    ) or _contains_any(
        text,
        [
            "voice chat",
            "voice note",
            "voice notes",
            "voice memo",
            "voice message",
            "voice messages",
            "voice recording",
            "record voice",
            "voice assistant",
            "speech to text",
            "text to speech",
            "audio transcription",
        ],
    )


def _transaction_hint(text: str) -> bool:
    return _contains_any_token(
        text,
        [
            "purchase",
            "checkout",
            "payment",
            "payments",
            "invoice",
            "invoices",
            "billing",
            "subscription",
            "subscriptions",
            "refund",
            "refunds",
            "merchant",
        ],
    ) or _contains_any_pattern(
        text,
        [
            r"\bpay\b",
            r"\border\b",
            r"\bbook(?:ing)?\s+(?:a|an|the\s+)?(?:flight|hotel|restaurant|table|appointment|trip|travel|tour|ticket|reservation|videographer|service|consultation|meeting|call)\b",
        ],
    )


def _delete_hint(text: str) -> bool:
    return _contains_any_token(text, ["delete", "wipe", "destroy", "erase", "purge"]) or _contains_any(
        text,
        [
            "remove file",
            "remove files",
            "remove folder",
            "remove folders",
            "remove event",
            "remove events",
            "clear cache",
            "clear data",
            "clear history",
            "delete calendar event",
        ],
    )


def _control_hint(text: str) -> bool:
    return _contains_any(
        text,
        [
            "desktop control",
            "windows control",
            "browser automation",
            "gui automation",
            "mouse control",
            "keyboard automation",
            "window management",
            "home automation",
            "global hotkeys",
            "hotkey",
            "control philips hue",
        ],
    ) or _contains_any_pattern(
        text,
        [
            r"\bcontrol(?:ling)?\s+(?:the\s+)?(?:light|lights|device|devices|window|windows|desktop|gui|browser|app|apps|application|tab|tabs|speaker|speakers|hue)\b",
        ],
    )


@dataclass(frozen=True)
class SkillCapabilities:
    skill_id: str
    author_handle: str | None
    name: str
    downloads: int
    stars: int
    description: str
    tags: str
    metadata_systems: str
    untrusted_input: bool
    sensitive_read: bool
    critical_action: bool
    exfil_channel: bool
    credentialed: bool
    device_or_physical: bool
    memory_channel: bool
    workspace_channel: bool
    browser_state_channel: bool
    time_channel: bool
    clipboard_channel: bool
    keyboard_channel: bool
    mouse_channel: bool
    screen_channel: bool
    audio_channel: bool
    video_channel: bool
    desktop_state_channel: bool
    notification_channel: bool
    popup_channel: bool
    window_focus_channel: bool
    power_state_channel: bool
    transformer_or_stateful: bool

    def low_visibility_channels(self) -> tuple[str, ...]:
        channels: list[str] = []
        if self.memory_channel:
            channels.append("memory")
        if self.workspace_channel:
            channels.append("workspace")
        if self.browser_state_channel:
            channels.append("browser_state")
        if self.device_or_physical:
            channels.append("device_signal")
        return tuple(channels)

    def semantic_channels(self) -> tuple[str, ...]:
        channels: list[str] = []
        if self.memory_channel:
            channels.append("memory")
        if self.workspace_channel:
            channels.append("workspace")
        if self.transformer_or_stateful and not channels:
            channels.append("transform_buffer")
        return tuple(channels)

    def system_channels(self) -> tuple[str, ...]:
        channels: list[str] = []
        if self.time_channel:
            channels.append("time")
        if self.credentialed:
            channels.append("credential_state")
        if self.clipboard_channel:
            channels.append("clipboard")
        if self.keyboard_channel:
            channels.append("keyboard_io")
        if self.mouse_channel:
            channels.append("mouse_io")
        if self.screen_channel:
            channels.append("screen_capture")
        if self.audio_channel:
            channels.append("audio_capture")
        if self.video_channel:
            channels.append("video_capture")
        if self.desktop_state_channel:
            channels.append("desktop_state")
        if self.browser_state_channel:
            channels.append("browser_state")
        if self.notification_channel:
            channels.append("notification_signal")
        if self.popup_channel:
            channels.append("popup_dialog")
        if self.window_focus_channel:
            channels.append("window_focus")
        if self.power_state_channel:
            channels.append("power_state")
        if self.device_or_physical:
            channels.append("device_signal")
        return tuple(channels)

    def overt_source(self) -> bool:
        return self.untrusted_input or self.sensitive_read

    def overt_sink(self) -> bool:
        return self.critical_action or self.exfil_channel

    def covert_source(self) -> bool:
        return self.overt_source() and bool(self.low_visibility_channels())

    def covert_sink(self) -> bool:
        return self.exfil_channel or self.critical_action

    def domains(self) -> tuple[str, ...]:
        text = _normalize_text(self.name, self.description, self.tags, self.metadata_systems)
        domains: list[str] = []
        keyword_map = {
            "github": ["github", "repo", "pull request", "issue"],
            "slack": ["slack", "channel", "workspace message"],
            "notion": ["notion"],
            "calendar": ["calendar", "schedule", "event"],
            "email": ["email", "gmail", "mail", "outlook"],
            "docs": ["docs", "document", "drive"],
            "pdf": ["pdf"],
            "browser": ["browser", "tab", "page", "website"],
            "search": ["search", "web search", "crawl", "scrape"],
            "device": ["device", "iot", "speaker", "light", "home assistant", "alexa", "sonos", "xiaomi"],
            "workspace": ["workspace", "folder", "file", "markdown", "backup"],
            "memory": ["memory", "recall", "journal", "history", "notes", "self-improving"],
            "desktop": ["desktop", "windows control", "desktop control"],
            "api": ["api", "http", "webhook", "endpoint"],
        }
        for domain, keywords in keyword_map.items():
            if _contains_any(text, keywords):
                domains.append(domain)
        if self.credentialed and "api" not in domains:
            domains.append("api")
        if self.requires_service_domain("github") and "github" not in domains:
            domains.append("github")
        if self.requires_service_domain("slack") and "slack" not in domains:
            domains.append("slack")
        if self.requires_service_domain("notion") and "notion" not in domains:
            domains.append("notion")
        if self.requires_service_domain("calendar") and "calendar" not in domains:
            domains.append("calendar")
        if self.requires_service_domain("email") and "email" not in domains:
            domains.append("email")
        if self.workspace_channel and "workspace" not in domains:
            domains.append("workspace")
        if self.memory_channel and "memory" not in domains:
            domains.append("memory")
        if self.browser_state_channel and "browser" not in domains:
            domains.append("browser")
        return tuple(sorted(set(domains)))

    def requires_service_domain(self, domain: str) -> bool:
        mapping = {
            "github": "github",
            "slack": "slack",
            "notion": "notion",
            "calendar": "calendar",
            "email": "email",
        }
        return domain in mapping and _contains_any(
            _normalize_text(self.name, self.description, self.tags, self.metadata_systems),
            [domain],
        )


@dataclass(frozen=True)
class PairCandidate:
    category: str
    source_skill_id: str
    source_author_handle: str | None
    source_name: str
    source_downloads: int
    sink_skill_id: str
    sink_author_handle: str | None
    sink_name: str
    sink_downloads: int
    medium: str
    score: float
    rationale: str


@dataclass(frozen=True)
class ChainCandidate:
    category: str
    source_skill_id: str
    source_author_handle: str | None
    source_name: str
    source_downloads: int
    intermediate_skill_id: str
    intermediate_author_handle: str | None
    intermediate_name: str
    intermediate_downloads: int
    sink_skill_id: str
    sink_author_handle: str | None
    sink_name: str
    sink_downloads: int
    medium: str
    score: float
    rationale: str


@dataclass(frozen=True)
class AttackPath:
    category: str
    source_type: str
    sink_type: str
    schema_name: str
    meta_path: str
    source_skill_id: str
    source_name: str
    source_domain: str
    intermediate_skill_id: str
    intermediate_name: str
    sink_skill_id: str
    sink_name: str
    sink_domain: str
    crosses_domain: str
    source_object_type: str
    carrier_type: str
    effect_type: str
    influence_survival: float
    hazard: float
    compatibility: float
    barrier: float
    risk_score: float
    path_cost: float
    rationale: str


def derive_capabilities(row: sqlite3.Row) -> SkillCapabilities:
    text = _normalize_text(
        row["name"],
        row["description"],
        row["tags"],
        row["metadata_systems"],
    )

    browser_hint = bool(row["requires_browser_access"])
    docs_hint = bool(row["requires_docs"]) or bool(row["requires_pdf"])
    local_hint = bool(row["requires_local_file_access"])
    shell_hint = bool(row["requires_shell_access"])
    service_hint = any(
        bool(row[column])
        for column in [
            "requires_github",
            "requires_slack",
            "requires_notion",
            "requires_calendar",
            "requires_email",
        ]
    )
    time_channel = bool(row["requires_calendar"]) or _contains_any(
        text,
        [
            "calendar",
            "schedule",
            "scheduled",
            "scheduling",
            "event",
            "events",
            "cron",
            "timer",
            "reminder",
            "daily",
            "weekly",
            "monthly",
            "appointment",
            "deadline",
        ],
    )
    clipboard_channel = _contains_any(
        text,
        [
            "clipboard",
            "copy/paste",
            "copy and paste",
            "pasteboard",
        ],
    )
    keyboard_channel = _contains_any_token(
        text,
        [
            "keyboard",
            "keypress",
            "key press",
            "keystroke",
            "hotkey",
            "shortcut",
            "type text",
            "typing",
        ],
    )
    mouse_channel = _mouse_io_hint(text)
    screen_channel = _contains_any(
        text,
        [
            "screenshot",
            "screen capture",
            "capture the screen",
            "screen recording",
            "window capture",
            "window bounds",
            "image capture",
            "visual qa",
        ],
    ) or _contains_any_token(text, ["ocr"])
    audio_channel = _audio_capture_hint(text)
    video_channel = _contains_any(
        text,
        [
            "video",
            "camera",
            "webcam",
            "screen recording",
            "meeting recording",
            "record video",
            "video stream",
        ],
    ) or _contains_any_token(text, ["video", "camera", "webcam"])
    desktop_state_channel = _contains_any(
        text,
        [
            "desktop",
            "desktop control",
            "desktop automation",
            "windows control",
            "mac control",
            "pc control",
            "gui automation",
            "window manager",
            "window bounds",
            "app window",
        ],
    )
    notification_channel = _contains_any_token(
        text,
        [
            "notification",
            "notifications",
            "notify",
            "push notification",
            "alert",
            "alerts",
            "toast",
            "banner",
            "remind",
        ],
    )
    popup_channel = _contains_any_token(
        text,
        [
            "popup",
            "pop-up",
            "dialog",
            "modal",
            "overlay",
            "banner",
            "prompt",
        ],
    )
    window_focus_channel = _contains_any(
        text,
        [
            "window",
            "focus",
            "foreground",
            "active app",
            "window bounds",
            "switch app",
            "switch window",
            "tab switch",
        ],
    )
    power_state_channel = _contains_any(
        text,
        [
            "battery",
            "charging",
            "power",
            "low power",
            "sleep mode",
            "suspend",
            "wake",
            "power state",
        ],
    )
    ui_control_hint = shell_hint or _contains_any(
        text,
        [
            "keyboard automation",
            "desktop automation",
            "desktop control",
            "windows control",
            "mac control",
            "pc control",
            "gui automation",
            "mouse control",
            "type text",
            "keyboard shortcut",
        ],
    )
    observation_hint = clipboard_channel or screen_channel or audio_channel or video_channel

    untrusted_input = browser_hint or _contains_any(
        text,
        [
            "search",
            "web",
            "browser",
            "crawl",
            "scrape",
            "github",
            "slack",
            "email",
            "mail",
            "docs",
            "pdf",
            "news",
            "rss",
        ],
    )
    sensitive_read = local_hint or docs_hint or service_hint or observation_hint or _contains_any(
        text,
        [
            "workspace",
            "private",
            "file",
            "document",
            "pdf",
            "email",
            "calendar",
            "notion",
            "github",
            "slack",
            "drive",
        ],
    )
    critical_action = ui_control_hint or _contains_any(
        text,
        [
            "run command",
            "shell command",
            "browser automation",
            "gui automation",
        ],
    ) or _contains_any_token(
        text,
        [
            "execute",
            "shell",
            "automation",
            "deploy",
            "delete",
            "backup",
        ],
    )
    exfil_channel = any(
        bool(row[column])
        for column in ["requires_api_key", "requires_oauth", "requires_token"]
    ) or _contains_any(
        text,
        [
            "api",
            "http",
            "webhook",
            "upload",
            "notify",
            "sync",
            "publish",
            "post",
            "send",
            "email",
            "slack",
            "github",
            "web search",
        ],
    )
    credentialed = any(
        bool(row[column])
        for column in ["requires_api_key", "requires_oauth", "requires_token"]
    )
    device_or_physical = _contains_any(
        text,
        [
            "home assistant",
            "google home",
            "smart home",
            "desktop control",
            "windows control",
        ],
    ) or _contains_any_token(
        text,
        [
            "sonos",
            "alexa",
            "xiaomi",
            "device",
            "speaker",
            "light",
            "iot",
        ],
    )
    memory_channel = _contains_any_token(
        text,
        [
            "memory",
            "remember",
            "recall",
            "journal",
            "notes",
            "notebook",
            "history",
            "self-improving",
        ],
    )
    workspace_channel = local_hint or _contains_any(
        text,
        [
            "workspace",
            "file",
            "folder",
            "markdown",
            "backup",
            "save",
            "sync",
            "export",
            "document",
            "docs",
            "pdf",
        ],
    )
    browser_state_channel = browser_hint or _contains_any(
        text,
        [
            "browser",
            "tab",
            "screenshot",
            "snapshot",
            "cookie",
            "localstorage",
            "profile",
            "web page",
        ],
    )
    transformer_or_stateful = memory_channel or workspace_channel or browser_state_channel or _contains_any_token(
        text,
        [
            "summarize",
            "summary",
            "extract",
            "analyze",
            "triage",
            "organize",
            "plan",
            "planner",
            "notes",
            "backup",
            "sync",
            "workflow",
            "convert",
            "transform",
        ],
    )

    return SkillCapabilities(
        skill_id=row["skill_id"],
        author_handle=row["author_handle"],
        name=row["name"],
        downloads=int(row["downloads"] or 0),
        stars=int(row["stars"] or 0),
        description=row["description"] or "",
        tags=row["tags"] or "",
        metadata_systems=row["metadata_systems"] or "",
        untrusted_input=untrusted_input,
        sensitive_read=sensitive_read,
        critical_action=critical_action,
        exfil_channel=exfil_channel,
        credentialed=credentialed,
        device_or_physical=device_or_physical,
        memory_channel=memory_channel,
        workspace_channel=workspace_channel,
        browser_state_channel=browser_state_channel,
        time_channel=time_channel,
        clipboard_channel=clipboard_channel,
        keyboard_channel=keyboard_channel,
        mouse_channel=mouse_channel,
        screen_channel=screen_channel,
        audio_channel=audio_channel,
        video_channel=video_channel,
        desktop_state_channel=desktop_state_channel,
        notification_channel=notification_channel,
        popup_channel=popup_channel,
        window_focus_channel=window_focus_channel,
        power_state_channel=power_state_channel,
        transformer_or_stateful=transformer_or_stateful,
    )


def load_capabilities(db_path: Path) -> list[SkillCapabilities]:
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    query = """
    WITH hint_flags AS (
        SELECT
            skill_id,
            MAX(requires_api_key) AS requires_api_key,
            MAX(requires_oauth) AS requires_oauth,
            MAX(requires_token) AS requires_token,
            MAX(requires_local_file_access) AS requires_local_file_access,
            MAX(requires_shell_access) AS requires_shell_access,
            MAX(requires_browser_access) AS requires_browser_access,
            MAX(requires_github) AS requires_github,
            MAX(requires_slack) AS requires_slack,
            MAX(requires_notion) AS requires_notion,
            MAX(requires_calendar) AS requires_calendar,
            MAX(requires_email) AS requires_email,
            MAX(requires_docs) AS requires_docs,
            MAX(requires_pdf) AS requires_pdf
        FROM permission_secret_hints
        GROUP BY skill_id
    )
    SELECT
        s.skill_id,
        s.author_handle,
        s.name,
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
    LEFT JOIN hint_flags h USING(skill_id)
    """
    rows = connection.execute(query).fetchall()
    connection.close()
    return [derive_capabilities(row) for row in rows]


def _score_pair(source: SkillCapabilities, sink: SkillCapabilities, bonus: float = 0.0) -> float:
    return math.log10(source.downloads + 10) + math.log10(sink.downloads + 10) + bonus


def _score_chain(
    source: SkillCapabilities,
    intermediate: SkillCapabilities,
    sink: SkillCapabilities,
    bonus: float = 0.0,
) -> float:
    return (
        math.log10(source.downloads + 10)
        + math.log10(intermediate.downloads + 10)
        + math.log10(sink.downloads + 10)
        + bonus
    )


def rank_obvious_pairs(
    capabilities: list[SkillCapabilities],
    top_k: int = 50,
    source_limit: int = 200,
    sink_limit: int = 200,
) -> list[PairCandidate]:
    sources = sorted(
        [cap for cap in capabilities if cap.overt_source()],
        key=lambda cap: cap.downloads,
        reverse=True,
    )[:source_limit]
    sinks = sorted(
        [cap for cap in capabilities if cap.overt_sink()],
        key=lambda cap: cap.downloads,
        reverse=True,
    )[:sink_limit]

    candidates: list[PairCandidate] = []
    for source, sink in itertools.product(sources, sinks):
        if source.skill_id == sink.skill_id:
            continue
        category = None
        rationale_bits: list[str] = []
        if source.untrusted_input and sink.critical_action:
            category = "overt_input_to_action"
            rationale_bits.append("untrusted_input -> critical_action")
        elif source.sensitive_read and sink.exfil_channel:
            category = "overt_read_to_exfil"
            rationale_bits.append("sensitive_read -> exfil_channel")
        elif source.untrusted_input and sink.exfil_channel:
            category = "overt_input_to_exfil"
            rationale_bits.append("untrusted_input -> exfil_channel")
        elif source.sensitive_read and sink.critical_action:
            category = "overt_read_to_action"
            rationale_bits.append("sensitive_read -> critical_action")
        if not category:
            continue
        if source.credentialed or sink.credentialed:
            rationale_bits.append("credentialed")
        candidates.append(
            PairCandidate(
                category=category,
                source_skill_id=source.skill_id,
                source_author_handle=source.author_handle,
                source_name=source.name,
                source_downloads=source.downloads,
                sink_skill_id=sink.skill_id,
                sink_author_handle=sink.author_handle,
                sink_name=sink.name,
                sink_downloads=sink.downloads,
                medium="direct",
                score=_score_pair(source, sink, 0.5 if source.credentialed or sink.credentialed else 0.0),
                rationale=", ".join(rationale_bits),
            )
        )
    candidates.sort(key=lambda candidate: candidate.score, reverse=True)
    return candidates[:top_k]


def input_object_types(skill: SkillCapabilities) -> tuple[str, ...]:
    types: set[str] = set()
    domains = set(skill.domains())
    if skill.untrusted_input:
        if domains & {"browser", "search"}:
            types.update({"raw_content", "instruction_bearing_text"})
        else:
            types.add("raw_content")
    if skill.sensitive_read:
        if domains & {"github", "slack", "notion", "calendar", "email", "docs", "pdf", "workspace"}:
            types.update({"private_content", "secret"})
        else:
            types.add("private_content")
    if skill.time_channel:
        types.add("schedule_event")
    if skill.notification_channel or skill.popup_channel or skill.window_focus_channel or skill.power_state_channel:
        types.add("runtime_signal")
    if skill.clipboard_channel or skill.screen_channel or skill.audio_channel or skill.video_channel:
        types.add("observation_trace")
    if skill.keyboard_channel or skill.mouse_channel or skill.desktop_state_channel:
        types.add("ui_instruction")
    return tuple(sorted(types))


def output_object_types(skill: SkillCapabilities) -> tuple[str, ...]:
    types: set[str] = set()
    if skill.transformer_or_stateful:
        types.update({"summarized_text", "plan_text", "action_argument"})
    if skill.memory_channel or skill.workspace_channel or skill.browser_state_channel:
        types.add("state_snapshot")
    if skill.untrusted_input:
        types.add("raw_content")
    if skill.sensitive_read:
        types.add("private_content")
    if skill.time_channel:
        types.add("schedule_event")
    if skill.notification_channel or skill.popup_channel or skill.window_focus_channel or skill.power_state_channel:
        types.add("runtime_signal")
    if skill.clipboard_channel or skill.screen_channel or skill.audio_channel or skill.video_channel:
        types.add("observation_trace")
    if skill.keyboard_channel or skill.mouse_channel or skill.desktop_state_channel:
        types.add("ui_instruction")
    return tuple(sorted(types))


def semantic_channel_types(skill: SkillCapabilities) -> tuple[str, ...]:
    return tuple(sorted(dict.fromkeys(skill.semantic_channels())))


def system_channel_types(skill: SkillCapabilities) -> tuple[str, ...]:
    return tuple(sorted(dict.fromkeys(skill.system_channels())))


def carrier_types(skill: SkillCapabilities) -> tuple[str, ...]:
    carriers: list[str] = list(skill.low_visibility_channels())
    if skill.transformer_or_stateful and not carriers:
        carriers.append("transform_buffer")
    return tuple(carriers)


def source_types(skill: SkillCapabilities) -> tuple[str, ...]:
    types: list[str] = []
    if skill.untrusted_input:
        types.append("untrusted_input")
    if skill.sensitive_read:
        types.append("sensitive_read")
    if skill.memory_channel or skill.workspace_channel or skill.browser_state_channel:
        types.append("contaminated_state")
    return tuple(dict.fromkeys(types))


def sink_types(skill: SkillCapabilities) -> tuple[str, ...]:
    text = _normalize_text(skill.name, skill.description, skill.tags, skill.metadata_systems)
    sinks: set[str] = set()
    if skill.exfil_channel:
        sinks.add("exfil")
    if _contains_any(
        text,
        [
            "pull request",
            "create issue",
            "open issue",
            "publish to",
            "post to",
            "post on",
            "send email",
            "send slack",
        ],
    ) or _contains_any_token(text, ["publish", "tweet", "newsletter"]):
        sinks.add("publish")
    if _transaction_hint(text):
        sinks.add("transact")
    if _delete_hint(text):
        sinks.add("delete")
    if _contains_any_token(text, ["write", "update", "edit", "modify", "create", "commit", "save", "sync"]):
        sinks.add("modify")
    if _contains_any(text, ["run command", "shell command", "desktop control", "desktop automation"]) or _contains_any_token(
        text,
        ["execute", "shell", "terminal", "automation"],
    ):
        sinks.add("execute")
    if _control_hint(text):
        sinks.add("control")
    if skill.critical_action and not sinks:
        sinks.add("execute")
    return tuple(sorted(sinks))


def trust_domains(skill: SkillCapabilities) -> tuple[str, ...]:
    domains = set(skill.domains())
    trust: list[str] = []
    if skill.untrusted_input or domains & {"browser", "search"}:
        trust.append("External")
    if skill.sensitive_read and domains & {"github", "slack", "notion", "calendar", "email", "docs"}:
        trust.append("EnterpriseInternal")
    if skill.workspace_channel or "workspace" in domains:
        trust.append("AgentWorkspace")
    if skill.memory_channel or "memory" in domains:
        trust.append("AgentWorkspace")
    if skill.browser_state_channel:
        trust.append("LocalHost")
    if (
        skill.critical_action
        and _contains_any(_normalize_text(skill.name, skill.description), ["shell", "desktop", "terminal", "command"])
    ) or any(
        (
            skill.clipboard_channel,
            skill.keyboard_channel,
            skill.mouse_channel,
            skill.screen_channel,
            skill.audio_channel,
            skill.video_channel,
            skill.desktop_state_channel,
        )
    ):
        trust.append("LocalHost")
    if skill.exfil_channel or domains & {"api", "slack", "github", "email", "calendar", "notion"}:
        trust.append("ThirdPartySaaS")
    if skill.device_or_physical or "device" in domains:
        trust.append("PhysicalDevice")
    if not trust:
        trust.append("UserProvided")
    return tuple(dict.fromkeys(trust))


def effect_types(skill: SkillCapabilities) -> tuple[str, ...]:
    domains = set(skill.domains())
    effects: set[str] = set()
    if skill.exfil_channel:
        if domains & {"slack", "email", "github", "api"}:
            effects.add("external_exfil")
        else:
            effects.add("network_effect")
    if skill.time_channel:
        effects.add("schedule_state")
    if skill.notification_channel or skill.popup_channel or skill.window_focus_channel or skill.power_state_channel:
        effects.add("runtime_signal")
    if skill.clipboard_channel or skill.screen_channel or skill.audio_channel or skill.video_channel:
        effects.add("observation_capture")
    if skill.keyboard_channel or skill.mouse_channel or skill.desktop_state_channel:
        effects.add("ui_control")
    if skill.critical_action:
        if skill.device_or_physical or "device" in domains:
            effects.add("device_control")
        elif "desktop" in domains:
            effects.add("desktop_or_shell")
        else:
            effects.add("critical_action")
    return tuple(sorted(effects))


def skill_gain(skill: SkillCapabilities) -> float:
    gain = 1.0
    if skill.transformer_or_stateful:
        gain += 0.10
    if skill.memory_channel or skill.workspace_channel or skill.browser_state_channel:
        gain += 0.05
    if skill.credentialed:
        gain += 0.03
    return gain


def _shared_domains_score(left: SkillCapabilities, right: SkillCapabilities) -> float:
    shared, compatible = _shared_or_generic_domains(set(left.domains()), set(right.domains()))
    if not compatible:
        return 0.0
    if shared & {"cross_surface", "data_to_channel", "generic_state"}:
        return 0.72
    return min(0.95, 0.78 + 0.04 * len(shared))


def compatibility_score(
    source: SkillCapabilities,
    intermediate: SkillCapabilities,
    sink: SkillCapabilities,
    medium: str,
) -> float:
    source_intermediate = _shared_domains_score(source, intermediate)
    intermediate_sink = _shared_domains_score(intermediate, sink)
    if source_intermediate == 0.0 or intermediate_sink == 0.0:
        return 0.0

    if medium == "transform_buffer":
        k_type = 0.86
    elif medium in {"memory", "workspace", "browser_state"}:
        k_type = 0.82
    elif medium == "device_signal":
        k_type = 0.74
    else:
        k_type = 0.78

    workflow_bonus = 0.0
    if intermediate.transformer_or_stateful:
        workflow_bonus += 0.05
    if source.downloads > 1000 and sink.downloads > 1000:
        workflow_bonus += 0.03

    return min(0.99, k_type * ((source_intermediate + intermediate_sink) / 2.0) + workflow_bonus)


def influence_survival_score(
    source: SkillCapabilities,
    intermediate: SkillCapabilities,
    sink: SkillCapabilities,
    medium: str,
    compatibility: float,
) -> float:
    if compatibility <= 0.0:
        return 0.0
    produce = 0.90
    transform = 0.78 if intermediate.transformer_or_stateful else 0.70
    store = 1.0
    retrieve = 1.0
    if medium in {"memory", "workspace", "browser_state", "device_signal"}:
        store = 0.85
        retrieve = 0.85
    actuate = 0.90 if sink.critical_action or sink.exfil_channel else 0.75
    phi = produce * compatibility * transform * skill_gain(intermediate) * store * retrieve * actuate
    return max(0.0, min(phi, 1.0))


def hazard_score(skill: SkillCapabilities, effect_type: str) -> float:
    score = 1.0
    if skill.sensitive_read:
        score += 0.7
    if skill.exfil_channel:
        score += 0.8
    if skill.critical_action:
        score += 1.0
    if effect_type in {"device_control", "control"}:
        score += 0.5
    if effect_type in {"desktop_or_shell", "execute"}:
        score += 0.7
    if effect_type in {"external_exfil", "exfil"}:
        score += 0.5
    if effect_type == "modify":
        score += 0.6
    if effect_type == "delete":
        score += 0.8
    if effect_type == "publish":
        score += 0.4
    if effect_type == "transact":
        score += 1.0
    return score


def barrier_score(sink: SkillCapabilities, medium: str) -> float:
    barrier = 0.15
    if sink.credentialed:
        barrier += 0.20
    if sink.critical_action:
        barrier += 0.10
    if medium == "device_signal":
        barrier += 0.10
    return barrier


def _meta_path_for_medium(category: str, medium: str) -> str:
    if medium == "transform_buffer":
        return f"S -> D -> S(transform) -> D -> X [{category}]"
    return f"S -> D -> C({medium}) -> D -> S -> X [{category}]"


def _primary_source_type(skill: SkillCapabilities) -> str:
    source_priority = ["contaminated_state", "sensitive_read", "untrusted_input"]
    present = set(source_types(skill))
    for source_type in source_priority:
        if source_type in present:
            return source_type
    return "untrusted_input"


def _primary_sink_type(skill: SkillCapabilities, category: str) -> str:
    present = set(sink_types(skill))
    if category.endswith("to_exfil"):
        for sink_type in ["exfil", "publish"]:
            if sink_type in present:
                return sink_type
    else:
        for sink_type in ["transact", "delete", "control", "execute", "modify", "publish"]:
            if sink_type in present:
                return sink_type
    if "exfil" in present:
        return "exfil"
    if present:
        return sorted(present)[0]
    return "execute"


def _primary_trust_domain(skill: SkillCapabilities) -> str:
    present = set(trust_domains(skill))
    for domain in [
        "External",
        "EnterpriseInternal",
        "AgentWorkspace",
        "LocalHost",
        "ThirdPartySaaS",
        "PhysicalDevice",
        "UserProvided",
    ]:
        if domain in present:
            return domain
    return "UserProvided"


def _cross_domain_label(source_domain: str, sink_domain: str) -> str:
    if source_domain == sink_domain:
        return f"{source_domain}->{sink_domain}"
    return f"{source_domain}->{sink_domain}"


def _schema_name(source_type: str, sink_type: str) -> str:
    if sink_type == "exfil":
        return "Schema_Exfil"
    if sink_type == "execute" or sink_type == "control":
        return "Schema_ExecOrControl"
    if sink_type == "modify":
        return "Schema_Modify"
    if sink_type == "delete":
        return "Schema_Delete"
    if sink_type in {"transact", "publish"}:
        return "Schema_TransactOrPublish"
    return f"Schema_{source_type}_to_{sink_type}"


def _source_object_for_skill(skill: SkillCapabilities) -> str:
    object_types = input_object_types(skill)
    if "instruction_bearing_text" in object_types:
        return "instruction_bearing_text"
    if "secret" in object_types:
        return "secret"
    if object_types:
        return object_types[0]
    return "raw_content"


def _effect_for_sink(skill: SkillCapabilities) -> str:
    effects = effect_types(skill)
    if "device_control" in effects:
        return "device_control"
    if "desktop_or_shell" in effects:
        return "desktop_or_shell"
    if "external_exfil" in effects:
        return "external_exfil"
    if effects:
        return effects[0]
    return "critical_action" if skill.critical_action else "network_effect"


def _lsagm_category(
    source: SkillCapabilities,
    sink: SkillCapabilities,
    medium: str,
) -> str | None:
    if source.untrusted_input and sink.critical_action:
        return "lsagm_input_to_action"
    if source.sensitive_read and sink.exfil_channel:
        return "lsagm_read_to_exfil"
    if source.untrusted_input and sink.exfil_channel:
        return "lsagm_input_to_exfil"
    if source.sensitive_read and sink.critical_action:
        return "lsagm_read_to_action"
    return None


def _compatible_mediums(intermediate: SkillCapabilities, sink: SkillCapabilities) -> list[str]:
    mediums: list[str] = []
    for medium in carrier_types(intermediate):
        if medium == "memory" and sink.exfil_channel:
            mediums.append(medium)
        elif medium == "workspace" and (sink.exfil_channel or sink.sensitive_read or sink.critical_action):
            mediums.append(medium)
        elif medium == "browser_state" and (sink.exfil_channel or sink.untrusted_input):
            mediums.append(medium)
        elif medium == "device_signal" and (sink.exfil_channel or sink.device_or_physical or sink.critical_action):
            mediums.append(medium)
        elif medium == "transform_buffer":
            mediums.append(medium)
    return mediums


def mine_lsagm_attack_paths(
    capabilities: list[SkillCapabilities],
    top_k: int | None = 100,
    source_limit: int = 160,
    intermediate_limit: int = 220,
    sink_limit: int = 160,
) -> list[AttackPath]:
    sources = sorted([cap for cap in capabilities if cap.overt_source()], key=lambda cap: cap.downloads, reverse=True)[:source_limit]
    intermediates = sorted(
        [cap for cap in capabilities if cap.transformer_or_stateful or cap.low_visibility_channels()],
        key=lambda cap: cap.downloads,
        reverse=True,
    )[:intermediate_limit]
    sinks = sorted([cap for cap in capabilities if cap.overt_sink()], key=lambda cap: cap.downloads, reverse=True)[:sink_limit]

    paths: list[AttackPath] = []
    for source, intermediate, sink in itertools.product(sources, intermediates, sinks):
        if len({source.skill_id, intermediate.skill_id, sink.skill_id}) < 3:
            continue
        candidate_media = _compatible_mediums(intermediate, sink)
        if not candidate_media:
            continue
        for medium in candidate_media:
            category = _lsagm_category(source, sink, medium)
            if not category:
                continue
            compatibility = compatibility_score(source, intermediate, sink, medium)
            if compatibility <= 0.0:
                continue
            influence_survival = influence_survival_score(source, intermediate, sink, medium, compatibility)
            source_type = _primary_source_type(source)
            sink_type = _primary_sink_type(sink, category)
            source_domain = _primary_trust_domain(source)
            sink_domain = _primary_trust_domain(sink)
            effect_type = sink_type
            hazard = hazard_score(source, effect_type) + hazard_score(sink, effect_type)
            barrier = barrier_score(sink, medium)
            risk_score = influence_survival * hazard * compatibility
            path_cost = (
                -math.log(max(influence_survival, 1e-9))
                - math.log(max(compatibility, 1e-9))
                + barrier
                - 0.35 * hazard
            )
            rationale_bits = [
                category.replace("lsagm_", "").replace("_", " -> "),
                f"medium={medium}",
                f"domains={','.join(sorted(set(source.domains()) & set(intermediate.domains()) | set(intermediate.domains()) & set(sink.domains())) or {'generic'})}",
                f"source_type={source_type}",
                f"sink_type={sink_type}",
            ]
            paths.append(
                AttackPath(
                    category=category,
                    source_type=source_type,
                    sink_type=sink_type,
                    schema_name=_schema_name(source_type, sink_type),
                    meta_path=_meta_path_for_medium(category, medium),
                    source_skill_id=source.skill_id,
                    source_name=source.name,
                    source_domain=source_domain,
                    intermediate_skill_id=intermediate.skill_id,
                    intermediate_name=intermediate.name,
                    sink_skill_id=sink.skill_id,
                    sink_name=sink.name,
                    sink_domain=sink_domain,
                    crosses_domain=_cross_domain_label(source_domain, sink_domain),
                    source_object_type=_source_object_for_skill(source),
                    carrier_type=medium,
                    effect_type=effect_type,
                    influence_survival=influence_survival,
                    hazard=hazard,
                    compatibility=compatibility,
                    barrier=barrier,
                    risk_score=risk_score,
                    path_cost=path_cost,
                    rationale=", ".join(rationale_bits),
                )
            )
    paths.sort(key=lambda item: (item.risk_score, -item.path_cost), reverse=True)
    if top_k is None:
        return paths
    return paths[:top_k]


def _compatible_covert_medium(source: SkillCapabilities, sink: SkillCapabilities) -> str | None:
    source_media = set(source.low_visibility_channels())
    sink_media = set(sink.low_visibility_channels())
    if "memory" in source_media and ("memory" in sink_media or sink.workspace_channel or sink.sensitive_read):
        return "memory"
    if "workspace" in source_media and (sink.workspace_channel or sink.sensitive_read):
        return "workspace"
    if "browser_state" in source_media and "browser_state" in sink_media:
        return "browser_state"
    if "device_signal" in source_media and (sink.device_or_physical or sink.browser_state_channel or sink.untrusted_input):
        return "device_signal"
    return None


def rank_covert_pairs(
    capabilities: list[SkillCapabilities],
    top_k: int = 50,
    source_limit: int = 200,
    sink_limit: int = 250,
) -> list[PairCandidate]:
    sources = sorted(
        [cap for cap in capabilities if cap.covert_source()],
        key=lambda cap: cap.downloads,
        reverse=True,
    )[:source_limit]
    sinks = sorted(
        [cap for cap in capabilities if cap.covert_sink()],
        key=lambda cap: cap.downloads,
        reverse=True,
    )[:sink_limit]

    candidates: list[PairCandidate] = []
    for source, sink in itertools.product(sources, sinks):
        if source.skill_id == sink.skill_id:
            continue
        medium = _compatible_covert_medium(source, sink)
        if not medium:
            continue
        if not sink.exfil_channel:
            continue
        rationale_bits = [f"{medium}_carrier", "low_visibility_channel -> exfil_channel"]
        bonus = 0.6
        if source.device_or_physical or sink.device_or_physical:
            bonus += 0.4
            rationale_bits.append("device_or_physical")
        if source.memory_channel or sink.memory_channel:
            bonus += 0.2
            rationale_bits.append("memory_like")
        candidates.append(
            PairCandidate(
                category=f"covert_{medium}_to_exfil",
                source_skill_id=source.skill_id,
                source_author_handle=source.author_handle,
                source_name=source.name,
                source_downloads=source.downloads,
                sink_skill_id=sink.skill_id,
                sink_author_handle=sink.author_handle,
                sink_name=sink.name,
                sink_downloads=sink.downloads,
                medium=medium,
                score=_score_pair(source, sink, bonus),
                rationale=", ".join(rationale_bits),
            )
        )
    candidates.sort(key=lambda candidate: candidate.score, reverse=True)
    return candidates[:top_k]


def typed_pair_category(source: SkillCapabilities, sink: SkillCapabilities) -> str | None:
    if source.skill_id == sink.skill_id:
        return None
    if source.untrusted_input and sink.critical_action:
        return "overt_input_to_action"
    if source.sensitive_read and sink.exfil_channel:
        return "overt_read_to_exfil"
    if source.untrusted_input and sink.exfil_channel:
        return "overt_input_to_exfil"
    if source.sensitive_read and sink.critical_action:
        return "overt_read_to_action"
    return None


def _shared_or_generic_domains(source_domains: set[str], sink_domains: set[str]) -> tuple[set[str], bool]:
    generic_domains = {"api", "workspace", "memory", "browser"}
    shared = source_domains & sink_domains
    if shared:
        return shared, True
    if source_domains & {"browser", "search"} and sink_domains & {"api", "desktop", "device"}:
        return {"cross_surface"}, True
    if source_domains & {"docs", "pdf", "email", "github", "slack", "notion", "calendar", "workspace"} and sink_domains & {"api", "github", "slack", "email", "notion", "calendar"}:
        return {"data_to_channel"}, True
    if source_domains & generic_domains and sink_domains & generic_domains:
        return {"generic_state"}, True
    return set(), False


def typed_pair_compatible(source: SkillCapabilities, sink: SkillCapabilities) -> tuple[bool, str]:
    category = typed_pair_category(source, sink)
    if not category:
        return False, ""
    shared, compatible = _shared_or_generic_domains(set(source.domains()), set(sink.domains()))
    if not compatible:
        return False, ""
    return True, ",".join(sorted(shared))


def count_typed_pairs(capabilities: list[SkillCapabilities]) -> dict[str, int]:
    group_counts: Counter[tuple[bool, bool, bool, bool, tuple[str, ...]]] = Counter(
        (
            cap.untrusted_input,
            cap.sensitive_read,
            cap.critical_action,
            cap.exfil_channel,
            cap.domains(),
        )
        for cap in capabilities
    )
    return _count_pairs_from_groups(group_counts)


def count_typed_pairs_with_domains(
    capabilities: list[SkillCapabilities],
    domain_map: dict[str, tuple[str, ...]],
) -> dict[str, int]:
    group_counts: Counter[tuple[bool, bool, bool, bool, tuple[str, ...]]] = Counter(
        (
            cap.untrusted_input,
            cap.sensitive_read,
            cap.critical_action,
            cap.exfil_channel,
            tuple(domain_map[cap.skill_id]),
        )
        for cap in capabilities
    )
    return _count_pairs_from_groups(group_counts)


def _count_pairs_from_groups(
    group_counts: Counter[tuple[bool, bool, bool, bool, tuple[str, ...]]],
) -> dict[str, int]:
    counts = {
        "overt_input_to_action": 0,
        "overt_read_to_exfil": 0,
        "overt_input_to_exfil": 0,
        "overt_read_to_action": 0,
    }
    groups = list(group_counts.items())
    for (source_profile, source_count), (sink_profile, sink_count) in itertools.product(groups, groups):
        source_input, source_read, _, _, source_domains = source_profile
        _, _, sink_action, sink_exfil, sink_domains = sink_profile
        shared, compatible = _shared_or_generic_domains(set(source_domains), set(sink_domains))
        if not compatible or not shared:
            continue
        multiplier = source_count * sink_count
        if source_profile == sink_profile:
            multiplier = source_count * max(source_count - 1, 0)
        if multiplier <= 0:
            continue
        if source_input and sink_action:
            counts["overt_input_to_action"] += multiplier
        if source_read and sink_exfil:
            counts["overt_read_to_exfil"] += multiplier
        if source_input and sink_exfil:
            counts["overt_input_to_exfil"] += multiplier
        if source_read and sink_action:
            counts["overt_read_to_action"] += multiplier
    return counts


def randomized_typed_pair_baseline(
    capabilities: list[SkillCapabilities],
    iterations: int = 100,
    seed: int = 7,
) -> list[dict[str, int]]:
    random_generator = random.Random(seed)
    skill_ids = [cap.skill_id for cap in capabilities]
    domain_sets = [cap.domains() for cap in capabilities]
    results: list[dict[str, int]] = []
    for _ in range(iterations):
        shuffled = domain_sets[:]
        random_generator.shuffle(shuffled)
        domain_map = dict(zip(skill_ids, shuffled, strict=True))
        results.append(count_typed_pairs_with_domains(capabilities, domain_map))
    return results


def pair_category_potential_counts(capabilities: list[SkillCapabilities]) -> dict[str, int]:
    n_input = sum(cap.untrusted_input for cap in capabilities)
    n_read = sum(cap.sensitive_read for cap in capabilities)
    n_action = sum(cap.critical_action for cap in capabilities)
    n_exfil = sum(cap.exfil_channel for cap in capabilities)
    n_input_action = sum(cap.untrusted_input and cap.critical_action for cap in capabilities)
    n_read_exfil = sum(cap.sensitive_read and cap.exfil_channel for cap in capabilities)
    n_input_exfil = sum(cap.untrusted_input and cap.exfil_channel for cap in capabilities)
    n_read_action = sum(cap.sensitive_read and cap.critical_action for cap in capabilities)
    return {
        "overt_input_to_action": n_input * n_action - n_input_action,
        "overt_read_to_exfil": n_read * n_exfil - n_read_exfil,
        "overt_input_to_exfil": n_input * n_exfil - n_input_exfil,
        "overt_read_to_action": n_read * n_action - n_read_action,
    }


def domain_signature_counts(capabilities: list[SkillCapabilities]) -> Counter[tuple[str, ...]]:
    return Counter(cap.domains() for cap in capabilities)


def compatibility_probability(domain_counts: Counter[tuple[str, ...]]) -> float:
    total = sum(domain_counts.values())
    if total < 2:
        return 0.0
    probability = 0.0
    items = list(domain_counts.items())
    for source_domains, source_count in items:
        for sink_domains, sink_count in items:
            shared, compatible = _shared_or_generic_domains(set(source_domains), set(sink_domains))
            if not compatible or not shared:
                continue
            if source_domains == sink_domains:
                if source_count < 2:
                    continue
                probability += (source_count / total) * ((source_count - 1) / (total - 1))
            else:
                probability += (source_count / total) * (sink_count / (total - 1))
    return probability


def exact_randomized_pair_expectation(capabilities: list[SkillCapabilities]) -> dict[str, float]:
    potentials = pair_category_potential_counts(capabilities)
    p_compat = compatibility_probability(domain_signature_counts(capabilities))
    return {
        category: potential * p_compat
        for category, potential in potentials.items()
    }


def rank_overt_chains(
    capabilities: list[SkillCapabilities],
    top_k: int = 50,
    source_limit: int = 120,
    intermediate_limit: int = 150,
    sink_limit: int = 120,
) -> list[ChainCandidate]:
    sources = sorted(
        [cap for cap in capabilities if cap.overt_source()],
        key=lambda cap: cap.downloads,
        reverse=True,
    )[:source_limit]
    intermediates = sorted(
        [cap for cap in capabilities if cap.transformer_or_stateful],
        key=lambda cap: cap.downloads,
        reverse=True,
    )[:intermediate_limit]
    sinks = sorted(
        [cap for cap in capabilities if cap.overt_sink()],
        key=lambda cap: cap.downloads,
        reverse=True,
    )[:sink_limit]

    candidates: list[ChainCandidate] = []
    for source, intermediate, sink in itertools.product(sources, intermediates, sinks):
        if len({source.skill_id, intermediate.skill_id, sink.skill_id}) < 3:
            continue
        category = None
        rationale_bits: list[str] = []
        if source.untrusted_input and sink.critical_action:
            category = "overt_input_transform_action"
            rationale_bits.append("untrusted_input -> transform/state -> critical_action")
        elif source.sensitive_read and sink.exfil_channel:
            category = "overt_read_transform_exfil"
            rationale_bits.append("sensitive_read -> transform/state -> exfil_channel")
        elif source.untrusted_input and sink.exfil_channel:
            category = "overt_input_transform_exfil"
            rationale_bits.append("untrusted_input -> transform/state -> exfil_channel")
        elif source.sensitive_read and sink.critical_action:
            category = "overt_read_transform_action"
            rationale_bits.append("sensitive_read -> transform/state -> critical_action")
        if not category:
            continue
        medium = "transform"
        bonus = 0.2
        for channel in ("memory", "workspace", "browser_state", "device_signal"):
            if channel in intermediate.low_visibility_channels():
                medium = channel
                bonus += 0.3
                rationale_bits.append(f"{channel}_intermediate")
                break
        if source.credentialed or sink.credentialed:
            bonus += 0.2
            rationale_bits.append("credentialed")
        candidates.append(
            ChainCandidate(
                category=category,
                source_skill_id=source.skill_id,
                source_author_handle=source.author_handle,
                source_name=source.name,
                source_downloads=source.downloads,
                intermediate_skill_id=intermediate.skill_id,
                intermediate_author_handle=intermediate.author_handle,
                intermediate_name=intermediate.name,
                intermediate_downloads=intermediate.downloads,
                sink_skill_id=sink.skill_id,
                sink_author_handle=sink.author_handle,
                sink_name=sink.name,
                sink_downloads=sink.downloads,
                medium=medium,
                score=_score_chain(source, intermediate, sink, bonus),
                rationale=", ".join(rationale_bits),
            )
        )
    candidates.sort(key=lambda candidate: candidate.score, reverse=True)
    return candidates[:top_k]


def rank_covert_chains(
    capabilities: list[SkillCapabilities],
    top_k: int = 50,
    source_limit: int = 120,
    intermediate_limit: int = 180,
    sink_limit: int = 120,
) -> list[ChainCandidate]:
    sources = sorted(
        [cap for cap in capabilities if cap.overt_source()],
        key=lambda cap: cap.downloads,
        reverse=True,
    )[:source_limit]
    intermediates = sorted(
        [cap for cap in capabilities if cap.low_visibility_channels()],
        key=lambda cap: cap.downloads,
        reverse=True,
    )[:intermediate_limit]
    sinks = sorted(
        [cap for cap in capabilities if cap.exfil_channel],
        key=lambda cap: cap.downloads,
        reverse=True,
    )[:sink_limit]

    candidates: list[ChainCandidate] = []
    for source, intermediate, sink in itertools.product(sources, intermediates, sinks):
        if len({source.skill_id, intermediate.skill_id, sink.skill_id}) < 3:
            continue
        medium = None
        for candidate_medium in ("memory", "workspace", "browser_state", "device_signal"):
            if candidate_medium in intermediate.low_visibility_channels():
                medium = candidate_medium
                break
        if not medium:
            continue
        if medium == "memory" and not intermediate.memory_channel:
            continue
        if medium == "workspace" and not intermediate.workspace_channel:
            continue
        if medium == "browser_state" and not intermediate.browser_state_channel:
            continue
        if medium == "device_signal" and not intermediate.device_or_physical:
            continue

        rationale_bits = [f"{medium}_carrier", "source -> low_visibility_state -> exfil_channel"]
        bonus = 0.6
        if medium == "device_signal":
            bonus += 0.4
            rationale_bits.append("device_or_physical")
        if medium == "memory":
            bonus += 0.2
            rationale_bits.append("memory_like")
        candidates.append(
            ChainCandidate(
                category=f"covert_{medium}_chain",
                source_skill_id=source.skill_id,
                source_author_handle=source.author_handle,
                source_name=source.name,
                source_downloads=source.downloads,
                intermediate_skill_id=intermediate.skill_id,
                intermediate_author_handle=intermediate.author_handle,
                intermediate_name=intermediate.name,
                intermediate_downloads=intermediate.downloads,
                sink_skill_id=sink.skill_id,
                sink_author_handle=sink.author_handle,
                sink_name=sink.name,
                sink_downloads=sink.downloads,
                medium=medium,
                score=_score_chain(source, intermediate, sink, bonus),
                rationale=", ".join(rationale_bits),
            )
        )
    candidates.sort(key=lambda candidate: candidate.score, reverse=True)
    return candidates[:top_k]


def write_candidates_csv(path: Path, candidates: list[PairCandidate]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "category",
                "source_skill_id",
                "source_author_handle",
                "source_name",
                "source_downloads",
                "sink_skill_id",
                "sink_author_handle",
                "sink_name",
                "sink_downloads",
                "medium",
                "score",
                "rationale",
            ],
        )
        writer.writeheader()
        for candidate in candidates:
            writer.writerow(candidate.__dict__)


def write_chains_csv(path: Path, chains: list[ChainCandidate]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "category",
                "source_skill_id",
                "source_author_handle",
                "source_name",
                "source_downloads",
                "intermediate_skill_id",
                "intermediate_author_handle",
                "intermediate_name",
                "intermediate_downloads",
                "sink_skill_id",
                "sink_author_handle",
                "sink_name",
                "sink_downloads",
                "medium",
                "score",
                "rationale",
            ],
            )


def write_attack_paths_csv(path: Path, paths: list[AttackPath]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "category",
                "source_type",
                "sink_type",
                "schema_name",
                "meta_path",
                "source_skill_id",
                "source_name",
                "source_domain",
                "intermediate_skill_id",
                "intermediate_name",
                "sink_skill_id",
                "sink_name",
                "sink_domain",
                "crosses_domain",
                "source_object_type",
                "carrier_type",
                "effect_type",
                "influence_survival",
                "hazard",
                "compatibility",
                "barrier",
                "risk_score",
                "path_cost",
                "rationale",
            ],
        )
        writer.writeheader()
        for path_item in paths:
            writer.writerow(
                {
                    "category": path_item.category,
                    "source_type": path_item.source_type,
                    "sink_type": path_item.sink_type,
                    "schema_name": path_item.schema_name,
                    "meta_path": path_item.meta_path,
                    "source_skill_id": path_item.source_skill_id,
                    "source_name": path_item.source_name,
                    "source_domain": path_item.source_domain,
                    "intermediate_skill_id": path_item.intermediate_skill_id,
                    "intermediate_name": path_item.intermediate_name,
                    "sink_skill_id": path_item.sink_skill_id,
                    "sink_name": path_item.sink_name,
                    "sink_domain": path_item.sink_domain,
                    "crosses_domain": path_item.crosses_domain,
                    "source_object_type": path_item.source_object_type,
                    "carrier_type": path_item.carrier_type,
                    "effect_type": path_item.effect_type,
                    "influence_survival": f"{path_item.influence_survival:.6f}",
                    "hazard": f"{path_item.hazard:.6f}",
                    "compatibility": f"{path_item.compatibility:.6f}",
                    "barrier": f"{path_item.barrier:.6f}",
                    "risk_score": f"{path_item.risk_score:.6f}",
                    "path_cost": f"{path_item.path_cost:.6f}",
                    "rationale": path_item.rationale,
                }
            )
