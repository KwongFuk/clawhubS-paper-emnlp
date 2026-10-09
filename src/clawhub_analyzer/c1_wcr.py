"""WCR helpers for pair-discovery experiments.

WCR = When-Condition Reachability:
whether a source skill's outputs or process-side effects can satisfy
the sink skill's documented invocation condition.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
import os
import sqlite3
from typing import Iterable

from clawhub_analyzer.cape.extract import extract_from_file
from clawhub_analyzer.cape.skill_profile import (
    SkillProfile as CapeSkillProfile,
    build_profile,
    compute_pair_score,
)


STATIC_TO_CAPE_CHANNELS: dict[str, tuple[str, ...]] = {
    "workspace": ("C11", "C2"),
    "memory": ("C3",),
    "transform_buffer": ("C1",),
    "credential_state": ("C4",),
    "browser_state": ("C1", "C13"),
    "audio_capture": ("C1",),
    "video_capture": ("C1",),
    "device_signal": ("C13",),
    "desktop_state": ("C1", "C13"),
    "power_state": ("C13",),
    "notification_signal": ("C14", "C1"),
    "window_focus": ("C1",),
    "popup_dialog": ("C1",),
    "keyboard_io": ("C1",),
    "clipboard": ("C7",),
}

MARKDOWN_SUFFIXES = {".md", ".mdx", ".txt", ".yml", ".yaml", ".json"}
MAX_PROFILE_TEXT_CHARS = 60_000


@dataclass(frozen=True)
class PairWCREvidence:
    source_id: str
    sink_id: str
    shared_channels: tuple[str, ...]
    best_channel: str
    channel_alignment_score: float
    source_output_type: str
    sink_trigger_type: str
    sink_when_spec: str
    sink_trigger_keywords: tuple[str, ...]
    trigger_compatibility: float
    combined_risk: float
    when_reachability_tier: str
    wcr_score: float
    minimum_viable_injection: str


def _chunked(items: list[str], size: int) -> Iterable[list[str]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


def _profile_text_priority(path: str) -> tuple[int, str]:
    lower = path.lower()
    if lower.endswith("skill.md"):
        return (0, lower)
    if lower.endswith("readme.md"):
        return (1, lower)
    if lower.endswith(".md"):
        return (2, lower)
    if lower.endswith((".txt", ".yml", ".yaml", ".json")):
        return (3, lower)
    return (4, lower)


def _include_in_profile_text(path: str) -> bool:
    lower = path.lower()
    return Path(lower).suffix in MARKDOWN_SUFFIXES or lower.endswith("skill.md")


def _dedup_labels(values: Iterable[str]) -> list[str]:
    return sorted({value for value in values if value})


def load_cape_profiles_for_skill_ids(
    db_path: str | Path,
    skill_ids: Iterable[str],
) -> dict[str, CapeSkillProfile]:
    skill_ids = sorted(set(skill_ids))
    if not skill_ids:
        return {}

    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row

    meta_by_id: dict[str, sqlite3.Row] = {}
    files_by_id: dict[str, list[sqlite3.Row]] = defaultdict(list)

    for batch in _chunked(skill_ids, 800):
        placeholders = ",".join("?" for _ in batch)
        meta_rows = con.execute(
            f"""
            SELECT skill_id, name, description, author_handle, downloads, installs_all_time
            FROM skills
            WHERE skill_id IN ({placeholders})
            """,
            batch,
        ).fetchall()
        for row in meta_rows:
            meta_by_id[row["skill_id"]] = row

        file_rows = con.execute(
            f"""
            SELECT skill_id, path, text_path
            FROM skill_files
            WHERE skill_id IN ({placeholders})
              AND text_path IS NOT NULL
              AND text_path != ''
            """,
            batch,
        ).fetchall()
        for row in file_rows:
            files_by_id[row["skill_id"]].append(row)

    con.close()

    profiles: dict[str, CapeSkillProfile] = {}
    for skill_id in skill_ids:
        meta = meta_by_id.get(skill_id)
        if meta is None:
            continue

        source_caps: list[str] = []
        sink_caps: list[str] = []
        channel_caps: list[str] = []
        profile_text_parts: list[str] = []

        for row in sorted(files_by_id.get(skill_id, []), key=lambda item: _profile_text_priority(item["path"] or "")):
            text_path = row["text_path"]
            file_path = row["path"] or ""
            if not text_path or not os.path.exists(text_path):
                continue
            try:
                text = Path(text_path).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue

            for cap in extract_from_file(text, file_path):
                if cap.cap_type == "source":
                    source_caps.append(cap.label)
                elif cap.cap_type == "sink":
                    sink_caps.append(cap.label)
                elif cap.cap_type == "channel":
                    channel_caps.append(cap.label)

            if _include_in_profile_text(file_path) and len("".join(profile_text_parts)) < MAX_PROFILE_TEXT_CHARS:
                remaining = MAX_PROFILE_TEXT_CHARS - len("".join(profile_text_parts))
                profile_text_parts.append(text[:remaining])

        description = meta["description"] or ""
        profile_text = "\n\n".join(part for part in [description, *profile_text_parts] if part)[:MAX_PROFILE_TEXT_CHARS]

        profiles[skill_id] = build_profile(
            skill_id=skill_id,
            name=meta["name"] or skill_id,
            downloads=(meta["downloads"] or 0) + (meta["installs_all_time"] or 0),
            description=description,
            skill_md=profile_text,
            source_caps=_dedup_labels(source_caps),
            sink_caps=_dedup_labels(sink_caps),
            channel_caps=_dedup_labels(channel_caps),
        )

    return profiles


def static_carrier_to_cape_channels(carrier_type: str) -> tuple[str, ...]:
    return STATIC_TO_CAPE_CHANNELS.get(carrier_type, ())


def channel_alignment_score(
    carrier_type: str,
    shared_channels: Iterable[str],
    best_channel: str,
) -> float:
    static_channels = set(static_carrier_to_cape_channels(carrier_type))
    shared = set(shared_channels)
    if best_channel and best_channel in static_channels:
        return 1.0
    if shared & static_channels:
        return 0.75
    if best_channel == "C1":
        return 0.40
    if shared:
        return 0.20
    return 0.0


def when_reachability_tier(trigger_compatibility: float, *, has_when_spec: bool, alignment_score: float) -> str:
    if trigger_compatibility >= 0.85 and has_when_spec and alignment_score >= 0.75:
        return "W3_high"
    if trigger_compatibility >= 0.70 and alignment_score >= 0.40:
        return "W2_medium"
    if trigger_compatibility >= 0.50:
        return "W1_low"
    return "W0_none"


def compute_wcr_evidence(
    *,
    row: dict[str, str],
    source_profile: CapeSkillProfile,
    sink_profile: CapeSkillProfile,
) -> PairWCREvidence | None:
    pair = compute_pair_score(source_profile, sink_profile)
    if pair is None:
        return None

    alignment = channel_alignment_score(
        row["carrier_type"],
        pair["shared_channels"],
        pair["best_channel"],
    )
    has_when_spec = bool(sink_profile.when_spec.strip())
    trigger_compatibility = float(pair["trigger_compat"])
    combined_risk = float(pair["combined_risk"])
    tier = when_reachability_tier(
        trigger_compatibility,
        has_when_spec=has_when_spec,
        alignment_score=alignment,
    )

    # WCR score biases toward actual trigger reachability, but still
    # rewards explicit when clauses and static/cape channel alignment.
    wcr_score = min(
        1.0,
        trigger_compatibility * 0.45
        + combined_risk * 0.20
        + alignment * 0.20
        + (0.10 if has_when_spec else 0.0)
        + min(len(sink_profile.trigger_keywords), 4) * 0.0125,
    )

    return PairWCREvidence(
        source_id=source_profile.skill_id,
        sink_id=sink_profile.skill_id,
        shared_channels=tuple(pair["shared_channels"]),
        best_channel=pair["best_channel"],
        channel_alignment_score=round(alignment, 4),
        source_output_type=source_profile.output_type,
        sink_trigger_type=sink_profile.trigger_type,
        sink_when_spec=sink_profile.when_spec[:300],
        sink_trigger_keywords=tuple(sink_profile.trigger_keywords[:8]),
        trigger_compatibility=round(trigger_compatibility, 4),
        combined_risk=round(combined_risk, 4),
        when_reachability_tier=tier,
        wcr_score=round(wcr_score, 4),
        minimum_viable_injection=pair["mvi_hint"],
    )

