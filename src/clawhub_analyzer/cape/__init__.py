"""
CAPE — Capability Extraction from All files Pipeline
__init__.py: Public API.

Usage:
    from clawhub_analyzer.cape import stream_clawhub_profiles, find_toxic_pairs

    profiles = list(stream_clawhub_profiles("path/to/merged.sqlite3"))
    pairs    = find_toxic_pairs(profiles)
"""
from __future__ import annotations

from typing import Iterator

from clawhub_analyzer.cape.types import (
    Capability,
    CapType,
    SkillProfile,
    ToxicPair,
    SOURCE_LABELS,
    SINK_LABELS,
    CHANNEL_LABELS,
)
from clawhub_analyzer.cape.extract import extract_from_file
from clawhub_analyzer.cape.load import (
    stream_clawhub_profiles,
    stream_skillssh_profiles,
)


def find_toxic_pairs(
    profiles: list[SkillProfile],
    *,
    require_explicit_channel: bool = False,
    require_directed_channel: bool = False,
) -> list[ToxicPair]:
    """
    Find all toxic pairs from a list of SkillProfiles.

    A pair (A, B) is toxic when:
      1. Skill A has at least one Source capability
      2. Skill B has at least one Sink capability
      3. For the channel condition, three modes:
         - Default: any shared channel label (incl. same_turn_ctx)
         - require_explicit_channel=True: code-detected channel only
         - require_directed_channel=True: A WRITES, B READS (IoTMon condition)

    Returns pairs sorted by combined downloads descending.
    Each pair also carries directed_channels for separate reporting.
    """
    source_profiles = [p for p in profiles if p.source_caps()]
    sink_profiles   = [p for p in profiles if p.sink_caps()]

    results: list[ToxicPair] = []

    for skill_a in source_profiles:
        ch_a = skill_a.channel_labels()
        for skill_b in sink_profiles:
            if skill_b.skill_id == skill_a.skill_id:
                continue

            ch_b = skill_b.channel_labels()
            shared_channels = ch_a & ch_b

            # ── Channel directionality (IoTMon condition) ──────────────────
            # Computed for ALL pairs regardless of mode — always reported.
            # Only counts capabilities with explicit write/read role tags.
            a_write = skill_a.channel_directed_write()
            b_read  = skill_b.channel_directed_read()
            directed_channels = frozenset(a_write & b_read)

            if require_directed_channel:
                # Strictest mode: must have proven data-flow direction
                if not directed_channels:
                    continue
                channels_for_pair = directed_channels
            elif require_explicit_channel:
                # Must have a code-detected channel beyond same_turn_ctx
                explicit = shared_channels - {"same_turn_ctx"}
                if not explicit:
                    continue
                channels_for_pair = explicit
            else:
                if not shared_channels:
                    continue
                channels_for_pair = shared_channels

            results.append(ToxicPair(
                skill_a=skill_a,
                skill_b=skill_b,
                a_source_labels=skill_a.source_labels(),
                b_sink_labels=skill_b.sink_labels(),
                shared_channels=channels_for_pair,
                directed_channels=directed_channels,
            ))

    # Sort by combined downloads (objective, not subjective)
    results.sort(
        key=lambda p: p.skill_a.downloads + p.skill_b.downloads,
        reverse=True,
    )
    return results


__all__ = [
    # types
    "Capability",
    "CapType",
    "SkillProfile",
    "ToxicPair",
    "SOURCE_LABELS",
    "SINK_LABELS",
    "CHANNEL_LABELS",
    # extract
    "extract_from_file",
    # load
    "stream_clawhub_profiles",
    "stream_skillssh_profiles",
    # pair finding
    "find_toxic_pairs",
]
