"""
CAPE — Capability Extraction from All files Pipeline
types.py: Core data structures.

Three capability types, no confidence scores, no subjective weights.
Detection is binary: rule fires → capability present.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

# ─── Capability types ─────────────────────────────────────────────────────────

CapType = Literal["source", "sink", "channel"]

# Source labels (S-type): skill exposes agent to untrusted/sensitive data
SOURCE_LABELS: frozenset[str] = frozenset({
    "web_fetch",          # HTTP GET from arbitrary URL (attacker-controllable)
    "email_receive",      # Read incoming emails
    "chat_receive",       # Read incoming chat messages (Slack/Discord events)
    "user_file_read",     # Read file at user-provided / external path
    "audio_capture",      # Microphone / speech recognition
    "screen_capture",     # Screenshot / OCR
    "credential_read",    # Read API keys / tokens from env or file
    "memory_read",        # Read agent memory (previous sessions' context)
    "browser_state_read", # Read DOM / cookies / localStorage
    "calendar_read",      # Read calendar events
    "contact_read",       # Read contacts / address book
})

# Sink labels (K-type): skill produces irreversible or out-of-boundary effects
SINK_LABELS: frozenset[str] = frozenset({
    "shell_exec",         # Execute shell command / script
    "script_eval",        # eval() / exec() with dynamic string
    "remote_exec",        # ssh / paramiko remote command
    "network_send",       # HTTP POST/PUT to external endpoint
    "email_send",         # Send email
    "chat_post",          # Post to Slack / Discord / Teams
    "webhook_call",       # Trigger external webhook
    "cloud_upload",       # Upload to S3 / GCS / Dropbox
    "file_write",         # Write local file
    "file_delete",        # Delete local file
    "db_write",           # INSERT / UPDATE / DELETE in database
    "calendar_write",     # Create / delete calendar event
    "code_commit",        # git push / GitHub API commit
    "privilege_escalate", # sudo / su / chmod +s
})

# Channel labels (C-type): mediates data flow between two skills
CHANNEL_LABELS: frozenset[str] = frozenset({
    "workspace_file",   # Shared file in agent workspace (~/.openclaw/workspace/)
    "agent_memory",     # Agent memory system (MCP memory tool)
    "env_variable",     # Process environment variable
    "stdout_pipe",      # Shell stdout → stdin pipe
    "tmp_file",         # /tmp/ shared file
    "clipboard",        # System clipboard
    "browser_session",  # Browser DOM / cookies / localStorage
    "credential_store", # Shared credential / secret store
    "same_turn_ctx",    # LLM context window (structural, always present)
})


# ─── Core dataclasses ──────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Capability:
    """
    A single detected capability.

    cap_type : "source" | "sink" | "channel"
    label    : specific label from SOURCE_LABELS / SINK_LABELS / CHANNEL_LABELS
    evidence : audit trail — e.g. "SKILL.md:use_mcp_tool:browser_search"
                              or "scripts/run.py:L42:subprocess.run"
    role     : for channel caps only — "write" | "read" | "both" | None
               write = skill produces data into this channel
               read  = skill consumes data from this channel
               both  = skill both produces and consumes (e.g. same_turn_ctx)
    """
    cap_type: CapType
    label:    str
    evidence: str
    role:     str | None = None  # only meaningful for cap_type == "channel"


@dataclass
class SkillProfile:
    """
    A skill and its full capability set, extracted from all its files.
    No subjective scores — only structural facts.
    """
    skill_id:     str
    name:         str
    downloads:    int
    author:       str | None
    capabilities: list[Capability] = field(default_factory=list)

    # ── Accessors ──────────────────────────────────────────────────────────────

    def source_caps(self) -> list[Capability]:
        return [c for c in self.capabilities if c.cap_type == "source"]

    def sink_caps(self) -> list[Capability]:
        return [c for c in self.capabilities if c.cap_type == "sink"]

    def channel_caps(self) -> list[Capability]:
        return [c for c in self.capabilities if c.cap_type == "channel"]

    def source_labels(self) -> frozenset[str]:
        return frozenset(c.label for c in self.source_caps())

    def sink_labels(self) -> frozenset[str]:
        return frozenset(c.label for c in self.sink_caps())

    def channel_labels(self) -> frozenset[str]:
        return frozenset(c.label for c in self.channel_caps())

    def channel_write_labels(self) -> frozenset[str]:
        """Channel labels where this skill PRODUCES data (write/both role)."""
        return frozenset(
            c.label for c in self.channel_caps()
            if c.role in ("write", "both") or c.role is None  # None = legacy undirected
        )

    def channel_read_labels(self) -> frozenset[str]:
        """Channel labels where this skill CONSUMES data (read/both role)."""
        return frozenset(
            c.label for c in self.channel_caps()
            if c.role in ("read", "both") or c.role is None  # None = legacy undirected
        )

    def channel_directed_write(self) -> frozenset[str]:
        """Only channels with explicit write role (excludes undirected legacy)."""
        return frozenset(c.label for c in self.channel_caps() if c.role in ("write", "both"))

    def channel_directed_read(self) -> frozenset[str]:
        """Only channels with explicit read role (excludes undirected legacy)."""
        return frozenset(c.label for c in self.channel_caps() if c.role in ("read", "both"))

    def is_dual_role(self) -> bool:
        """Skill is both source and sink — complete attack chain alone."""
        return bool(self.source_caps() and self.sink_caps())

    def is_pure_channel(self) -> bool:
        """No source or sink — only bridges other skills."""
        return bool(self.channel_caps()) and not self.source_caps() and not self.sink_caps()

    def capability_count(self) -> int:
        return len(self.capabilities)


@dataclass(frozen=True)
class ToxicPair:
    """
    A pair of skills that together form a dangerous composition.

    Condition (all three must hold):
      1. skill_a has at least one Source capability
      2. skill_b has at least one Sink capability
      3. They share at least one Channel capability where:
         - skill_a WRITES to the channel (producer)
         - skill_b READS from the channel (consumer)
         This is the directed IoTMon condition: A produces → channel → B consumes
    """
    skill_a:          SkillProfile   # contributes Source capability
    skill_b:          SkillProfile   # contributes Sink capability
    a_source_labels:  frozenset[str] # which source labels on A
    b_sink_labels:    frozenset[str] # which sink labels on B
    shared_channels:  frozenset[str] # channel labels connecting A → B (undirected)
    directed_channels: frozenset[str] = field(default_factory=frozenset)
    # directed_channels: A writes AND B reads this channel (strongest evidence)

    @property
    def has_explicit_channel(self) -> bool:
        """True if they share a code-detected channel (not just same_turn_ctx)."""
        return bool(self.shared_channels - {"same_turn_ctx"})

    @property
    def has_directed_channel(self) -> bool:
        """True if A writes to a channel that B reads from (IoTMon condition)."""
        return bool(self.directed_channels)

    @property
    def attack_surface(self) -> str:
        """Human-readable attack path."""
        src = ", ".join(sorted(self.a_source_labels))
        snk = ", ".join(sorted(self.b_sink_labels))
        ch  = ", ".join(sorted(self.directed_channels or self.shared_channels))
        return f"[{src}] --({ch})--> [{snk}]"
