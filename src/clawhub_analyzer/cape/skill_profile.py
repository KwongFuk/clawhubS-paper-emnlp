"""
CAPE Skill Profile v2
=====================
完整的技能结构化提取，包含三层：

Layer 1: Capability (能力)
  ├─ source_caps  — 从外部获取什么数据 (web_fetch, email_receive, ...)
  ├─ sink_caps    — 执行什么危险动作   (shell_exec, file_delete, ...)
  └─ channel_caps — 读写什么持久渠道   (agent_memory, workspace_file, ...)

Layer 2: When-Trigger (触发器)
  ├─ when_spec    — `when` 字段原文
  ├─ trigger_type — T1_VERBATIM / T2_CONTENT_TYPE / T3_SITUATIONAL / T4_AUTONOMOUS
  └─ trigger_keywords — 触发关键词列表

Layer 3: Channel Model (信息渠道 C1-C14)
  ├─ channels_write — 这个技能能向哪些渠道写入
  ├─ channels_read  — 这个技能从哪些渠道读取
  └─ output_type    — source 输出的内容类型 (FREE_TEXT / CODE_OUTPUT / STRUCTURED / MULTIMEDIA)

用途：对任意两个技能 (A, B)，检测是否构成危险 pair：
  Rule 1 (Channel): channels_write(A) ∩ channels_read(B) ≠ ∅
  Rule 2 (Trigger): A 的输出内容能否语义满足 B 的 when 条件
"""
from __future__ import annotations

import re
import json
from dataclasses import dataclass, field
from typing import Optional


# ══════════════════════════════════════════════════════════════════════════════
# 信息渠道定义 (C1–C14)
# ══════════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class Channel:
    id: str           # C1, C2, ...
    name: str
    persistence: str  # session / persistent / cross-session / loop / process
    description: str


CHANNEL_DEFS: dict[str, Channel] = {
    "C1":  Channel("C1",  "LLM Context Window",    "session",       "Skill output returned to LLM context; all co-installed skills share this"),
    "C2":  Channel("C2",  "File System",            "persistent",    "Files written to disk; any file-reading skill can read"),
    "C3":  Channel("C3",  "Memory System",          "cross-session", "Agent memory store; persists across conversations"),
    "C4":  Channel("C4",  "Environment/Config",     "process",       "Environment variables or config files; read by credential-using skills"),
    "C5":  Channel("C5",  "Database",               "persistent",    "SQL/NoSQL database; any db-reading code/skill can read"),
    "C6":  Channel("C6",  "Git Repository",         "persistent",    "Git commits/branches; triggers CI or is read by repo-aware skills"),
    "C7":  Channel("C7",  "Clipboard",              "process",       "System clipboard; shared within host session"),
    "C8":  Channel("C8",  "System Log",             "host",          "syslog/journald/log files; read by monitoring skills"),
    "C9":  Channel("C9",  "Cron/Scheduler",         "loop",          "Crontab or systemd timers; triggers any skill periodically"),
    "C10": Channel("C10", "Message Queue",          "persistent",    "Redis/RabbitMQ/SQS; consumed by queue-listening skills"),
    "C11": Channel("C11", "Workspace Files",        "persistent",    "Agent workspace directory; shared by all skills with file access"),
    "C12": Channel("C12", "Process/Signal",         "runtime",       "OS process state or signals; read by monitoring/watchdog skills"),
    "C13": Channel("C13", "Local Network/Socket",   "realtime",      "HTTP server or Unix socket on localhost"),
    "C14": Channel("C14", "Cross-Agent Message",    "session",       "Agent-to-agent messaging; received by other agents"),
}


# ══════════════════════════════════════════════════════════════════════════════
# Capability → Channel 映射
# ══════════════════════════════════════════════════════════════════════════════

# 每个能力标签 → (writes_to, reads_from) 渠道 ID 列表
_CAP_TO_CHANNELS: dict[str, tuple[list[str], list[str]]] = {
    # source capabilities → writes to C1 (brings external content into LLM context)
    "web_fetch":        (["C1"], []),
    "email_receive":    (["C1"], []),
    "screen_capture":   (["C1"], []),
    "audio_capture":    (["C1"], []),
    "credential_read":  (["C1"], []),
    "user_file_read":   (["C1", "C2"], []),
    "memory_read":      (["C1"], ["C3"]),   # reads from memory, brings to context

    # sink capabilities → writes to various channels
    "shell_exec":       (["C2", "C8", "C9", "C11"], []),   # can create files, logs, crons
    "remote_exec":      (["C2", "C8", "C13"], []),
    "script_eval":      (["C1", "C2"], []),
    "file_write":       (["C2", "C11"], []),
    "file_delete":      (["C2", "C11"], []),
    "network_send":     (["C13"], []),
    "cloud_upload":     (["C2"], []),
    "email_send":       (["C14"], []),
    "chat_post":        (["C14"], []),
    "db_write":         (["C5"], []),
    "code_commit":      (["C6"], []),
    "privilege_escalate":(["C4", "C2"], []),

    # channel capabilities (middle layer)
    "agent_memory_write": (["C3"], []),
    "agent_memory_read":  ([], ["C3"]),
    "workspace_file":     (["C11"], ["C11"]),  # bidirectional
    "browser_session":    (["C1", "C7"], []),
    "tmp_file":           (["C2"], ["C2"]),
    "cron_write":         (["C9"], []),
}

# SKILL.md 文本关键词 → 隐含渠道写入/读取
_TEXT_CHANNEL_HINTS: list[tuple[re.Pattern, str, str]] = [
    # (pattern, channel_id, direction)
    (re.compile(r'crontab|cron\s+job|systemd\s+timer|scheduled?\s+task|@reboot', re.I), "C9", "write"),
    (re.compile(r'git\s+commit|git\s+push|git\s+notes|code_commit', re.I), "C6", "write"),
    (re.compile(r'memory|remember|persist|recall|hot_memory|MEMORY\.md|knowledge\s+graph', re.I), "C3", "write"),
    (re.compile(r'memory.*read|recall|retrieve.*memory|load.*memory', re.I), "C3", "read"),
    (re.compile(r'database|sqlite|mysql|postgres|mongo|DB_|SQL\s+query', re.I), "C5", "write"),
    (re.compile(r'agent.*message|send.*agent|multi.*agent|cross.*agent', re.I), "C14", "write"),
    (re.compile(r'workspace|/.openclaw/workspace|working\s+directory', re.I), "C11", "write"),
    (re.compile(r'environment\s+variable|\.env\s+file|os\.environ|process\.env', re.I), "C4", "write"),
    (re.compile(r'clipboard|pbcopy|xclip', re.I), "C7", "write"),
    (re.compile(r'syslog|journalctl|tail.*log|log\s+file', re.I), "C8", "read"),
    (re.compile(r'HTTP\s+server|localhost:\d+|unix\s+socket|listen.*port', re.I), "C13", "read"),
    (re.compile(r'SILENTLY|AUTOMATICALLY|without being asked|do not ask|auto-run', re.I), "C1", "write"),  # always-on = always writes to context
]


# ══════════════════════════════════════════════════════════════════════════════
# When-field 提取 & T1-T4 分类
# ══════════════════════════════════════════════════════════════════════════════

_WHEN_INLINE_RE = [
    re.compile(r'(?:Use\s+(?:this\s+skill\s+)?when|trigger\s+(?:this\s+)?when|invoke\s+when|activate\s+when)\s*[:\-]?\s*(.{10,500})', re.I),
    re.compile(r'(?:Use\s+PROACTIVELY|Auto-?trigger)\s+when\s*[:\-]?\s*(.{10,300})', re.I),
    re.compile(r'Triggers?\s*[:\-]\s*(.{10,300})', re.I),
    re.compile(r'Natural[-_ ]language\s+triggers?\s*[:\-]\s*(.{10,400})', re.I),
]
_WHEN_SECTION_HDR = re.compile(
    r'^#{1,4}\s+(?:When\s+to\s+use|Use\s+when|When\b|Triggers?|Activation|Auto-?run)', re.I)

# YAML front-matter `when:` field
_YAML_WHEN_RE = re.compile(r'^when\s*:\s*["\']?(.+)', re.I)


def extract_when_spec(text: str) -> str:
    """从 SKILL.md 中提取完整的 when/trigger 规范"""
    found: list[str] = []
    lines = text.split('\n')
    in_yaml = False
    yaml_done = False
    in_section = False
    buf: list[str] = []

    for i, line in enumerate(lines):
        s = line.strip()

        # YAML front matter
        if i == 0 and s == '---':
            in_yaml = True
            continue
        if in_yaml:
            if s == '---':
                in_yaml = False
                yaml_done = True
                continue
            m = _YAML_WHEN_RE.match(s)
            if m:
                val = m.group(1).strip().strip('"\'')
                if len(val) > 5:
                    found.append(val[:300])
            continue

        # Markdown section header
        if _WHEN_SECTION_HDR.match(s):
            if buf:
                found.append(' '.join(buf)[:400])
            in_section = True
            buf = []
            continue

        if in_section:
            if s.startswith('#'):
                if buf:
                    found.append(' '.join(buf)[:400])
                in_section = False
                buf = []
            elif s:
                buf.append(s)

        # Inline patterns
        for pat in _WHEN_INLINE_RE:
            m = pat.search(line)
            if m and len(m.group(1)) > 10:
                found.append(m.group(1).strip()[:300])

    if buf:
        found.append(' '.join(buf)[:400])

    return ' | '.join(dict.fromkeys(found))[:1000]  # deduplicated


def classify_trigger_type(when_text: str) -> str:
    """T1/T2/T3/T4 分类"""
    t = when_text.lower()

    # T4: 无需用户操作，自动触发
    if any(k in t for k in [
        'proactively', 'automatically', 'silently', 'do not ask',
        'without being asked', 'always run', 'at session start',
        'at task start', 'new task', 'every time', 'whenever',
        'auto-run', 'background', 'on service restart',
    ]):
        return 'T4_AUTONOMOUS'

    # T1: 包含精确引用字符串或 slash 命令
    if re.search(r'["\'`](/[a-z]|[a-z]{3,30})["\'\`]', t) or \
       re.search(r'"([a-z][a-z ]{2,30})"', when_text, re.I):
        return 'T1_VERBATIM'

    # T2: 需要特定内容类型（邮件/网页/文档）
    if re.search(
        r'\b(email|webpage|web page|document|notification|alert|'
        r'message|article|report|log output|feed|rss)\b', t
    ):
        return 'T2_CONTENT_TYPE'

    return 'T3_SITUATIONAL'


def extract_trigger_keywords(when_text: str) -> list[str]:
    """从 when 文本提取可注入的最小触发关键词"""
    kws = []

    # 提取引号中的精确短语
    for m in re.finditer(r'"([^"]{3,40})"', when_text):
        kws.append(m.group(1))

    # 提取 slash 命令
    for m in re.finditer(r'/[a-z][a-z\-]+', when_text):
        kws.append(m.group(0))

    # 提取 backtick 命令
    for m in re.finditer(r'`([^`]{3,30})`', when_text):
        kws.append(m.group(1))

    # 提取核心动词短语 (use when user says/asks X)
    for m in re.finditer(
        r'(?:user (?:says?|asks?|mentions?|requests?|wants?))\s+(?:to\s+)?([A-Za-z][\w\s]{3,30})',
        when_text, re.I
    ):
        kws.append(m.group(1).strip())

    # 去重、截断
    seen, result = set(), []
    for k in kws:
        k = k.strip()
        if k and k not in seen and len(k) >= 3:
            seen.add(k)
            result.append(k)
    return result[:10]


# ══════════════════════════════════════════════════════════════════════════════
# Source 输出类型
# ══════════════════════════════════════════════════════════════════════════════

def classify_output_type(source_caps: list[str], name: str,
                          desc: str, skill_md: str) -> str:
    """
    分类 source skill 的输出类型：
    FREE_TEXT      — 完全的自然语言文本（攻击者可任意控制）
    CODE_OUTPUT    — 代码文件、log、commit message（部分可控）
    STRUCTURED     — JSON/数值数据（较难嵌入触发词）
    MULTIMEDIA     — 图像/音频（极难控制文本内容）
    """
    if any(c in source_caps for c in ['screen_capture', 'audio_capture']):
        return 'MULTIMEDIA'

    text = (name + ' ' + (desc or '') + ' ' + skill_md[:500]).lower()

    # 结构化数据信号
    structured_kws = [
        'json output', 'returns json', 'temperature', 'humidity',
        'stock price', 'market data', 'sensor', 'metric', 'numeric',
        'csv', 'spreadsheet', 'ticker symbol', 'price feed',
    ]
    if any(k in text for k in structured_kws):
        # unless it also fetches free text
        free_text_kws = ['web search', 'search results', 'webpage content',
                         'article', 'email body', 'message content', 'blog']
        if not any(k in text for k in free_text_kws):
            return 'STRUCTURED'

    # 代码/日志信号
    code_kws = [
        'github', 'git repo', 'commit', 'pull request', 'diff',
        'terminal output', 'command output', 'log file', 'stack trace',
        'error message', 'build output', 'lint', 'test result',
    ]
    if any(k in text for k in code_kws):
        return 'CODE_OUTPUT'

    # 默认：自由文本（网页/邮件/文档/消息）
    return 'FREE_TEXT'


# ══════════════════════════════════════════════════════════════════════════════
# SkillProfile 数据结构
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class SkillProfile:
    skill_id:   str
    name:       str
    downloads:  int = 0
    description: str = ""

    # ── Layer 1: Capabilities ──────────────────────────────────────────────
    source_caps:  list[str] = field(default_factory=list)  # web_fetch, email_receive, ...
    sink_caps:    list[str] = field(default_factory=list)  # shell_exec, file_delete, ...
    channel_caps: list[str] = field(default_factory=list)  # agent_memory, workspace_file, ...

    # ── Layer 2: When-Trigger ─────────────────────────────────────────────
    when_spec:         str       = ""
    trigger_type:      str       = "T3_SITUATIONAL"  # T1 / T2 / T3 / T4
    trigger_keywords:  list[str] = field(default_factory=list)

    # ── Layer 3: Channel Model ────────────────────────────────────────────
    channels_write: list[str] = field(default_factory=list)  # C1, C2, ...
    channels_read:  list[str] = field(default_factory=list)   # C1, C3, ...
    output_type:    str       = "FREE_TEXT"  # source output classification
    process_effects: list[str] = field(default_factory=list)
    invocation_clues: list[str] = field(default_factory=list)

    def is_source(self) -> bool:
        return bool(self.source_caps)

    def is_sink(self) -> bool:
        return bool(self.sink_caps)

    def shared_channels(self, other: "SkillProfile") -> list[str]:
        """Rule 1: channels this skill WRITES that `other` READS"""
        return sorted(set(self.channels_write) & set(other.channels_read))

    def can_trigger(self, other: "SkillProfile") -> bool:
        """Quick check: does this have any channel connecting to other?"""
        return bool(self.shared_channels(other))

    def to_dict(self) -> dict:
        return {
            "skill_id":        self.skill_id,
            "name":            self.name,
            "downloads":       self.downloads,
            "source_caps":     self.source_caps,
            "sink_caps":       self.sink_caps,
            "channel_caps":    self.channel_caps,
            "when_spec":       self.when_spec,
            "trigger_type":    self.trigger_type,
            "trigger_keywords":self.trigger_keywords,
            "channels_write":  self.channels_write,
            "channels_read":   self.channels_read,
            "output_type":     self.output_type,
            "process_effects": self.process_effects,
            "invocation_clues": self.invocation_clues,
        }


# ══════════════════════════════════════════════════════════════════════════════
# Profile 构建器
# ══════════════════════════════════════════════════════════════════════════════

def build_channel_model(
    source_caps: list[str],
    sink_caps: list[str],
    channel_caps: list[str],
    skill_md: str,
) -> tuple[list[str], list[str]]:
    """
    从能力列表 + SKILL.md 文本推导该技能的渠道读写集合。
    Returns (channels_write, channels_read)
    """
    writes: set[str] = set()
    reads:  set[str] = set()

    all_caps = source_caps + sink_caps + channel_caps

    for cap in all_caps:
        cap_normalized = cap.replace("-", "_")
        # 匹配 "agent_memory_write" vs "agent_memory_read"
        if cap_normalized in _CAP_TO_CHANNELS:
            w, r = _CAP_TO_CHANNELS[cap_normalized]
            writes.update(w)
            reads.update(r)
        else:
            # fuzzy match
            for key, (w, r) in _CAP_TO_CHANNELS.items():
                if key in cap_normalized or cap_normalized in key:
                    writes.update(w)
                    reads.update(r)
                    break

    # Source skills ALWAYS write to C1 (bring external content into LLM context)
    if source_caps:
        writes.add("C1")

    # Sink skills ALWAYS read from C1 (their `when` is evaluated against LLM context)
    if sink_caps:
        reads.add("C1")

    # Text-based channel hints from SKILL.md
    for pattern, channel_id, direction in _TEXT_CHANNEL_HINTS:
        if pattern.search(skill_md):
            if direction == "write":
                writes.add(channel_id)
            else:
                reads.add(channel_id)

    return sorted(writes), sorted(reads)


def extract_process_effects(name: str, description: str, skill_md: str) -> list[str]:
    text = " ".join(part for part in (name, description or "", skill_md[:4000]) if part)
    effects: list[str] = []
    for pattern, label in _PROCESS_EFFECT_PATTERNS:
        if pattern.search(text):
            effects.append(label)
    return sorted(set(effects))


def extract_invocation_clues(name: str, description: str, skill_md: str, when_spec: str) -> list[str]:
    text = " ".join(part for part in (name, description or "", when_spec, skill_md[:3000]) if part)
    clues: list[str] = []
    for pattern, label in _INVOCATION_CLUE_PATTERNS:
        if pattern.search(text):
            clues.append(label)
    return sorted(set(clues))


def trigger_process_boost(source: "SkillProfile", sink: "SkillProfile") -> float:
    when_text = sink.when_spec.lower()
    if not when_text:
        return 0.0

    boost = 0.0
    effect_set = set(source.process_effects)

    if "order_context" in effect_set and any(k in when_text for k in ("order", "menu", "cart", "food", "drink")):
        boost += 0.18
    if "device_context" in effect_set and any(k in when_text for k in ("device", "tasmota", "govee", "status", "power state", "network scanning")):
        boost += 0.18
    if "subscription_context" in effect_set and any(k in when_text for k in ("subscription", "customer", "account", "plan", "license")):
        boost += 0.18
    if "status_signal" in effect_set and any(k in when_text for k in ("status", "monitor", "alert", "notification")):
        boost += 0.10
    if "workspace_artifact" in effect_set and any(k in when_text for k in ("document", "report", "note", "notes", "workspace")):
        boost += 0.08
    if "approval_like" in effect_set and any(k in when_text for k in ("submit", "finalize", "confirm", "approval")):
        boost += 0.08
    if "explicit_when" in sink.invocation_clues and "summary_output" in effect_set:
        boost += 0.05

    return min(0.25, boost)


def build_profile(
    skill_id: str,
    name: str,
    downloads: int,
    description: str,
    skill_md: str,
    source_caps: list[str],
    sink_caps: list[str],
    channel_caps: list[str],
) -> SkillProfile:
    """Build a complete SkillProfile from raw data."""

    # Layer 2: When-Trigger
    when = extract_when_spec(skill_md)
    trigger_type = classify_trigger_type(when) if when else "T3_SITUATIONAL"
    trigger_kws  = extract_trigger_keywords(when)

    # Layer 3: Channels
    channels_write, channels_read = build_channel_model(
        source_caps, sink_caps, channel_caps, skill_md)

    # Source output type
    output_type = classify_output_type(
        source_caps, name, description, skill_md) if source_caps else ""
    process_effects = extract_process_effects(name, description, skill_md)
    invocation_clues = extract_invocation_clues(name, description, skill_md, when)

    return SkillProfile(
        skill_id       = skill_id,
        name           = name,
        downloads      = downloads,
        description    = description,
        source_caps    = source_caps,
        sink_caps      = sink_caps,
        channel_caps   = channel_caps,
        when_spec      = when,
        trigger_type   = trigger_type,
        trigger_keywords = trigger_kws,
        channels_write = channels_write,
        channels_read  = channels_read,
        output_type    = output_type,
        process_effects = process_effects,
        invocation_clues = invocation_clues,
    )


# ══════════════════════════════════════════════════════════════════════════════
# Pair Detection (Rule 1 + Rule 2)
# ══════════════════════════════════════════════════════════════════════════════

# Attacker control score per channel × output type
# How much control does the attacker have over content flowing through this channel?
_CHANNEL_CONTROL: dict[str, float] = {
    "C1": 0.90,   # LLM context: attacker controls source output text → high control
    "C2": 0.85,   # File system: attacker controls file content
    "C3": 0.80,   # Memory: attacker controls what gets stored (via C1)
    "C4": 0.50,   # Env vars: partial control (may need shell access)
    "C5": 0.80,   # Database: attacker controls DB content via injection
    "C6": 0.85,   # Git: attacker controls commit messages, file content
    "C7": 0.70,   # Clipboard: depends on host
    "C8": 0.60,   # Logs: some control via shell_exec
    "C9": 0.90,   # Cron: once written, loops forever
    "C10":0.75,   # Queue: attacker publishes to queue
    "C11":0.85,   # Workspace: attacker controls workspace content
    "C12":0.40,   # Signals: limited
    "C13":0.80,   # Network: attacker controls HTTP server responses
    "C14":0.70,   # Agent: attacker sends agent messages
}

_TRIGGER_DIFFICULTY: dict[str, float] = {
    "T4_AUTONOMOUS":  1.00,  # no injection needed
    "T1_VERBATIM":    0.95,  # copy a string
    "T2_CONTENT_TYPE":0.85,  # template a content format
    "T3_SITUATIONAL": 0.80,  # craft a narrative
}

_OUTPUT_TYPE_MODIFIER: dict[str, float] = {
    "FREE_TEXT":   1.00,  # full attacker control over text
    "CODE_OUTPUT": 0.85,  # partial (commit messages, comments)
    "STRUCTURED":  0.30,  # limited (JSON values)
    "MULTIMEDIA":  0.10,  # almost no text control
    "":            0.50,  # unknown
}

_PROCESS_EFFECT_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b(summary|summarize|report|writeup|brief|overview)\b", re.I), "summary_output"),
    (re.compile(r"\b(note|notes|markdown|html|document|docs)\b", re.I), "workspace_artifact"),
    (re.compile(r"\b(status|alert|notification|monitor|watch)\b", re.I), "status_signal"),
    (re.compile(r"\b(device|iot|tasmota|govee|sensor|power state)\b", re.I), "device_context"),
    (re.compile(r"\b(order|menu|cart|food|drink|booking|reservation)\b", re.I), "order_context"),
    (re.compile(r"\b(subscription|license|customer|account|plan|workspace reseller)\b", re.I), "subscription_context"),
    (re.compile(r"\b(approve|approval|authorized|confirm|finalize|submit)\b", re.I), "approval_like"),
    (re.compile(r"\b(memory|remember|persist|recall)\b", re.I), "memory_effect"),
]

_INVOCATION_CLUE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\buse this skill when\b", re.I), "explicit_when"),
    (re.compile(r"\bwhenever\b|\balways\b|\bauto(?:-| )?trigger\b", re.I), "autonomous_trigger"),
    (re.compile(r"\buser asks\b|\buser wants\b|\buser sends\b", re.I), "user_intent_trigger"),
    (re.compile(r"\bpaired android device\b|\bpaired device\b", re.I), "paired_device_trigger"),
]


def compute_pair_score(
    source: SkillProfile,
    sink: SkillProfile,
) -> Optional[dict]:
    """
    Given source and sink profiles, compute pair risk.

    Rule 1: ∃ channel C ∈ channels_write(source) ∩ channels_read(sink)
    Rule 2: source.output_type × sink.trigger_type compatibility ≥ threshold

    Returns None if pair is not dangerous, else dict with details.
    """

    # ── Rule 1: Shared Channel Check ──────────────────────────────────────
    shared = source.shared_channels(sink)
    if not shared:
        return None  # No channel → not a pair

    # Best channel (highest attacker control)
    best_channel = max(shared, key=lambda c: _CHANNEL_CONTROL.get(c, 0.5))
    channel_ctrl = _CHANNEL_CONTROL.get(best_channel, 0.5)

    # ── Rule 2: Trigger Compatibility ────────────────────────────────────
    trigger_score  = _TRIGGER_DIFFICULTY.get(sink.trigger_type, 0.8)
    output_mod     = _OUTPUT_TYPE_MODIFIER.get(source.output_type, 0.5)

    # Channel persistence bonus (persistent channels are more dangerous)
    ch_def = CHANNEL_DEFS.get(best_channel)
    persistence_bonus = {
        "loop":          1.30,  # Cron: perpetual
        "cross-session": 1.20,  # Memory: survives reboot
        "persistent":    1.15,  # Files/DB/Git
        "host":          1.10,  # Logs: host-level
        "session":       1.00,
        "process":       0.95,
        "runtime":       0.90,
        "realtime":      1.05,
    }.get(ch_def.persistence if ch_def else "session", 1.0)

    # Combined score
    trigger_compat = channel_ctrl * trigger_score * output_mod
    trigger_compat += trigger_process_boost(source, sink)
    trigger_compat = min(trigger_compat, 1.0)
    if trigger_compat < 0.50:
        return None  # Rule 2 fails

    combined_risk = (
        trigger_compat
        * persistence_bonus
        * (1.5 if sink.trigger_type == "T4_AUTONOMOUS" else 1.0)
        * (1 + min(source.downloads, sink.downloads) / 200_000)
    )

    return {
        "source_id":        source.skill_id,
        "source_name":      source.name,
        "source_dl":        source.downloads,
        "source_output":    source.output_type,
        "sink_id":          sink.skill_id,
        "sink_name":        sink.name,
        "sink_dl":          sink.downloads,
        "sink_trigger_type":sink.trigger_type,
        "sink_when":        sink.when_spec[:150],
        "sink_trigger_kws": sink.trigger_keywords[:5],
        "shared_channels":  shared,
        "best_channel":     best_channel,
        "channel_name":     ch_def.name if ch_def else "?",
        "channel_persist":  ch_def.persistence if ch_def else "?",
        "trigger_compat":   round(trigger_compat, 3),
        "combined_risk":    round(combined_risk, 4),
        "source_process_effects": source.process_effects[:6],
        "sink_invocation_clues": sink.invocation_clues[:6],
        # Min Viable Injection
        "mvi_channel":      best_channel,
        "mvi_hint":         _build_mvi(source, sink, best_channel),
    }


def _build_mvi(source: SkillProfile, sink: SkillProfile, channel: str) -> str:
    """Build a minimum-viable-injection description."""
    tt = sink.trigger_type
    kws = sink.trigger_keywords

    if tt == "T4_AUTONOMOUS":
        return f"(none — sink auto-activates via {channel})"

    if tt == "T1_VERBATIM" and kws:
        return f'Embed verbatim: "{kws[0]}" in {source.output_type} output via {channel}'

    if tt == "T2_CONTENT_TYPE":
        return f"Craft {channel} content formatted as expected type to match: \"{sink.when_spec[:60]}\""

    # T3: narrative
    first_when = sink.when_spec[:60]
    if channel == "C1":
        return f"Embed narrative matching \"{first_when}\" in {source.output_type} output"
    elif channel == "C3":
        return f"Store \"{first_when}\" in memory via {source.name}; sink reads at next session"
    elif channel == "C9":
        return f"Inject cron trigger via {source.name}; loops forever, satisfying \"{first_when}\""
    elif channel == "C6":
        return f"Put \"{first_when}\" in commit message; sink reads repo"
    else:
        return f"Insert content matching \"{first_when}\" into {channel} channel"
