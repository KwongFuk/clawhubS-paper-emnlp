"""
CAPE Filter Config — Canonical Version
========================================
Single source of truth for all sink selection criteria.
All CAPE scripts must import from here to ensure reproducibility.

Version history:
  v1.0  2026-04-14  Initial merge of cape_full_adjudication.py
                    and cape_codex_adjudication.py keyword lists.
                    Union of both versions, keeping all keywords.
  v1.1  2026-04-15  Added agent_memory_write (sev=4) and boot_hook (sev=5)
                    to capture memory-tiering class sinks missed by v1.0.
                    Rationale: cross-session persistence elevates file_write
                    to a C3 channel, qualitatively more dangerous than sev=2.
"""

# ── Sink Severity Table ────────────────────────────────────────────────────────
# Severity score for each dangerous sink capability.
# Threshold: sev >= MIN_SINK_SEV to enter candidate pool.
SINK_SEVERITY = {
    # sev=5: Direct arbitrary code execution
    "shell_exec":          5,   # Execute arbitrary shell commands
    "remote_exec":         5,   # Execute commands on remote systems
    "script_eval":         5,   # Eval/exec code dynamically
    "boot_hook":           5,   # gateway:startup auto-exec (BOOT.md hook)
    # sev=4: Data exfiltration or persistent cross-session infection
    "privilege_escalate":  4,   # Gain elevated permissions
    "network_send":        4,   # Send data over network
    "cloud_upload":        4,   # Upload to cloud storage
    "agent_memory_write":  4,   # Write cross-session persistent memory (C3)
                                # Elevated from file_write(2): persistence across
                                # sessions qualitatively changes the threat model.
    # sev=3: Indirect/delayed harm
    "code_commit":         3,   # Commit to version control
    "file_delete":         3,   # Delete files from filesystem
    # sev=2: Below threshold — general file/db ops
    "file_write":          2,   # Write files (generic, NOT cross-session memory)
    "db_write":            2,   # Write to database
    "email_send":          2,   # Send emails
}

# Minimum severity to enter the Codex adjudication candidate pool.
# v1.0 note: plain file_write (sev=2) is still excluded for generic ops.
# v1.1 fix: agent_memory_write (sev=4) is now above threshold, capturing
#          memory-tiering, triple-memory, tiered-memory class sinks.
MIN_SINK_SEV = 3

# ── Trigger Keyword Patterns ───────────────────────────────────────────────────
# Each entry: (trigger_class, [regex_patterns])
# Matched against skill `description` field (lowercased).
# Rationale: hijackable sinks are those whose trigger conditions can be
# plausibly injected by attacker-controlled external content.
# ── Agent Memory Write Detection Patterns ────────────────────────────────────
# Used by LSCPS to assign `agent_memory_write` cap to a skill.
# Match against: name + description + SKILL.md (lowercased).
#
# Layer 1: File path detection
# Presence of a persistent memory file path = writing target exists
MEMORY_FILE_PATH_PATS = [
    r"memory/hot/HOT_MEMORY\.md",       # e.g. memory-tiering SKILL.md
    r"memory/warm/WARM_MEMORY\.md",
    r"memory/cold/",
    r"HOT_MEMORY\.md",
    r"WARM_MEMORY\.md",
    r"memory/\d{4}-\d{2}-\d{2}\.md",   # daily log files
    r"\.openclaw/workspace/memory",
]

# Layer 2: Semantic write verb equivalents
# These mean 'write' without using the word 'write'
SEMANTIC_WRITE_PATS = [
    r"move\s+to\s+(HOT|WARM|COLD)\b",  # 'Move to HOT' = write to HOT_MEMORY
    r"(tier|redistribu).{0,30}memor",   # 'tier redistribution'
    r"prune.{0,40}(HOT|WARM|memory)",   # pruning = modify file
    r"archive.{0,30}(COLD|memory)",     # archive = write to COLD
    r"distill.{0,30}(COLD|long.?term)", # distill to COLD
    r"ingest.{0,30}(memory|tier)",      # ingest = write
    r"(updated\s+frequently|pruned\s+aggressively)",  # lifecycle verbs
    r"management:\s*(updated|pruned|written)",
]

# Layer 3: Workflow step write patterns
# Structured SKILL.md with 'Step N: ... Move/Update/Write' = definite write
WORKFLOW_WRITE_PATS = [
    r"(step\s+\d|###\s+step).{0,200}(move\s+to|update|write|redistrib|prune|archive)",
    r"workflow.{0,100}(HOT|WARM|COLD|memory\.md)",
    r"organize.?memory.{0,100}(step|tier|redistrib)",
]

# Combined: any match in layers 1-3 → agent_memory_write
AGENT_MEMORY_PATTERNS = [
    # Original patterns (description-level)
    r"\bhot.?memor\b",
    r"\bwarm.?memor\b",
    r"\bcold.?memor\b",
    r"\bHOT_MEMORY\b",
    r"\bWARM_MEMORY\b",
    r"\bpersist(ent)?.{0,20}memor\b",
    r"\bmemor.{0,20}persist\b",
    r"\bcross.?session\b",
    r"\blong.?term.?memor\b",
    r"\btiered?.?memor\b",
    r"\bmemor.{0,20}tier\b",
    r"\bagent.?memor\b",
    r"\bpersist(ent)?.{0,20}context\b",
    # Layer 1: file paths
    *MEMORY_FILE_PATH_PATS,
    # Layer 2: semantic write verbs
    *SEMANTIC_WRITE_PATS,
    # Layer 3: workflow steps (match against full SKILL.md)
    *WORKFLOW_WRITE_PATS,
]

# For static adjudication: explicit write confidence scoring
EXPLICIT_WRITE_PATS = [
    # Direct file write statements
    r"(write|update|save|store|append|insert).{0,30}(HOT_MEMORY|WARM_MEMORY|MEMORY\.md|BOOT\.md)",
    r"(HOT_MEMORY|WARM_MEMORY|BOOT\.md).{0,30}(write|update|save|creat)",
    r"writes?\s+to\s+.{0,20}memory",
    r"saves?\s+.{0,20}(context|session|memory)\s+to\s+file",
    r"persist.{0,20}(HOT|WARM|COLD|memory\s+file)",
    r"(update|creat|overwrite).{0,20}BOOT\.md",
    r"gateway.{0,20}startup.{0,20}(run|exec|trigger)",
    # Layer 1-3 upgrades: file path + any action = explicit write
    r"memory/hot/HOT_MEMORY\.md",        # path present = write target confirmed
    r"memory/warm/WARM_MEMORY\.md",
    # Semantic write verbs (high confidence)
    r"move\s+to\s+(HOT|WARM|COLD)\b",   # 'Move to HOT' in workflow = write
    r"tier\s+redistribu",                # redistribution = rewrite files
    r"(management:\s*)(updated|pruned)",  # lifecycle description = write
]

# gateway:startup hook detection (boot_hook cap, sev=5)
BOOT_HOOK_PATTERNS = [
    r"\bBOOT\.md\b",
    r"\bgateway.?start\b",
    r"\bstartup.?hook\b",
    r"\bautostart\b",
    r"\bauto.?run.{0,20}start\b",
]

TRIGGER_PATTERNS = [
    (
        "correction",
        [
            r"\bcorrect(ion)?\b",
            r"\bactually\b",
            r"\buser corrects?\b",
            r"\bthat'?s?\s+wrong\b",
        ],
    ),
    (
        "error",
        [
            r"\berror\b",
            r"\bfail(ed|ure)?\b",
            r"\bunexpected(ly)?\b",
            r"\bcrash\b",
        ],
    ),
    (
        "update",
        [
            r"\bupdate\b",
            r"\binstall\b",
            r"\bnew version\b",
            r"\bupgrade\b",
            r"\bpatch\b",
        ],
    ),
    (
        "instruction",
        [
            r"\bexecute\b",
            r"\brun\s+(a\s+)?command\b",
            r"\brun\s+(a\s+)?script\b",
            r"\bdo\s+the\s+following\b",
        ],
    ),
    (
        "self_improve",
        [
            r"\bself.?improv\b",
            r"\blearn(ing)?\b",
            r"\bcontinuous\b",
            r"\bevolv\b",
            r"\badapt\b",
        ],
    ),
    (
        "proactive",
        [
            r"\bproactive\b",
            r"\banticipat\b",
            r"\bwithout being asked\b",
            r"\bauto.?detect\b",
            r"\bauto.?remedi\b",
        ],
    ),
    (
        "memory",
        [
            r"\bremember\b",
            r"\brecall\b",
            r"\bpersist(ent)?\s+memory\b",
            r"\blearning\s+system\b",
        ],
    ),
]

# High-hijackability trigger classes (hijack score = 3)
HIGH_HIJACK_CLASSES = {"correction", "self_improve", "proactive"}

# ── Hijackability Scoring ──────────────────────────────────────────────────────
def compute_hijack_score(matched_classes: list[str]) -> int:
    """
    Returns hijackability score (0-3).
    3 = attacker can trivially inject trigger via free-text content
    2 = attacker can inject with moderate effort
    0 = description has no injectable trigger keywords
    """
    if any(c in HIGH_HIJACK_CLASSES for c in matched_classes):
        return 3
    if matched_classes:
        return 2
    return 0


# ── Candidate Selection (V1.1) ────────────────────────────────────────────────
import re

def augment_sink_caps(sink_caps: list[str], description: str,
                      skill_md: str = '') -> list[str]:
    """
    V1.1: Detect agent_memory_write and boot_hook from text content,
    augmenting the LSCPS-extracted sink_caps list.
    These caps are written to cross-session storage — inherently dangerous
    regardless of explicit trigger keywords.
    """
    full = ((description or '') + ' ' + skill_md[:1000]).lower()
    augmented = list(sink_caps)
    if any(re.search(p, full, re.I) for p in AGENT_MEMORY_PATTERNS):
        if 'agent_memory_write' not in augmented:
            augmented.append('agent_memory_write')
    if any(re.search(p, full, re.I) for p in BOOT_HOOK_PATTERNS):
        if 'boot_hook' not in augmented:
            augmented.append('boot_hook')
    return augmented


def is_sink_candidate(sink_caps: list[str], description: str,
                      skill_md: str = '') -> tuple[bool, int, list[str]]:
    """
    V1.1: Returns (is_candidate, hijack_score, matched_trigger_classes).

    A skill is a candidate iff:
      - max(SINK_SEVERITY[augmented_cap]) >= MIN_SINK_SEV
      - hijack_score > 0

    V1.1 change: agent_memory_write / boot_hook are auto-detected from text
    and auto-assigned hijack=2 even without explicit trigger keywords.
    Rationale: any content entering the LLM context can be written to
    cross-session memory or a startup hook — no specific trigger needed.
    """
    augmented = augment_sink_caps(sink_caps, description, skill_md)
    desc = (description or '').lower()

    sev = max((SINK_SEVERITY.get(s, 0) for s in augmented), default=0)
    if sev < MIN_SINK_SEV:
        return False, 0, []

    # Check description trigger keywords
    matched = [
        t for t, pats in TRIGGER_PATTERNS
        if any(re.search(pat, desc) for pat in pats)
    ]
    hijack = compute_hijack_score(matched)

    # V1.1 rule: agent_memory_write / boot_hook are inherently hijackable
    # (C3/BOOT channel — any LLM context content can flow there)
    if hijack == 0:
        new_caps = set(augmented) - set(sink_caps)
        if new_caps & {'agent_memory_write', 'boot_hook'}:
            hijack = 2  # moderate — attacker needs web content in context
            matched = list(new_caps & {'agent_memory_write', 'boot_hook'})

    if hijack == 0:
        return False, 0, []

    return True, hijack, matched


# ── OHS Source Output Types ────────────────────────────────────────────────────
# Used by cape_trigger_channel.py (unchanged from original)
SOURCE_OUTPUT_TYPES = {
    "FREE_TEXT":      {"hijackability": 1.0},   # web pages, emails, docs
    "CODE_OUTPUT":    {"hijackability": 0.7},   # commit msgs, logs, stderr
    "STRUCTURED_DATA":{"hijackability": 0.3},   # JSON/CSV/metrics
    "MULTIMEDIA":     {"hijackability": 0.1},   # images/audio
}

FREE_TEXT_KEYWORDS = [
    "search","web","browse","fetch","url","http","scrape","crawl",
    "news","rss","feed","blog","article","page","content","email",
    "message","inbox","gmail","outlook","telegram","slack","discord",
    "feishu","whatsapp","document","pdf","wiki","notion","obsidian",
    "twitter","x.com","reddit","social","forum",
]

CODE_KEYWORDS = [
    "github","git","repo","commit","diff","issue","pr","code",
    "terminal","command output","log","trace","error message",
    "stdout","stderr","test result","lint","build",
]

STRUCTURED_KEYWORDS = [
    "stock","price","sensor","weather","iot","metric","database",
    "csv","json","api response","financial","market","ticker",
    "temperature","humidity","status code","numeric",
]

OHS_THRESHOLD = 0.75   # Only pairs with OHS >= this are reported
