"""
CAPE — Capability Extraction from All files Pipeline
extract.py: File-type specific capability extractors.

Each extractor takes raw text + filepath, returns list[Capability].
Detection is rule-based and deterministic — no probability scores.
"""
from __future__ import annotations

import ast
import json
import re
import warnings
from pathlib import Path
from typing import Iterator

from clawhub_analyzer.cape.types import Capability, CapType

# ─── Python AST extractor ─────────────────────────────────────────────────────

# Import name → list of (cap_type, label)
_PY_IMPORT_CAPS: dict[str, list[tuple[CapType, str]]] = {
    # Source — network/web
    "requests":     [("source", "web_fetch")],
    "httpx":        [("source", "web_fetch")],
    "urllib":       [("source", "web_fetch")],
    "aiohttp":      [("source", "web_fetch")],
    "feedparser":   [("source", "web_fetch")],
    "yt_dlp":       [("source", "web_fetch")],
    # Source — email receive
    "imaplib":      [("source", "email_receive")],
    "poplib":       [("source", "email_receive")],
    # Source — audio / screen
    "pyaudio":      [("source", "audio_capture")],
    "speech_recognition": [("source", "audio_capture")],
    "SpeechRecognition":  [("source", "audio_capture")],
    "mss":          [("source", "screen_capture")],
    # Source — credential
    "dotenv":       [("source", "credential_read")],
    # Sink — shell execution
    "subprocess":   [("sink", "shell_exec")],
    "pty":          [("sink", "shell_exec")],
    # Sink — email send
    "smtplib":      [("sink", "email_send")],
    "sendgrid":     [("sink", "email_send")],
    "mailchimp3":   [("sink", "email_send")],
    # Sink — remote execution
    "paramiko":     [("sink", "remote_exec")],
    "fabric":       [("sink", "remote_exec")],
    # Sink — git / code commit
    "git":          [("sink", "code_commit")],
    "pygit2":       [("sink", "code_commit")],
    # Sink — cloud upload
    "boto3":        [("sink", "cloud_upload")],
    "google.cloud": [("sink", "cloud_upload")],
    "dropbox":      [("sink", "cloud_upload")],
    # Sink — database write
    "sqlite3":      [("sink", "db_write")],
    "psycopg2":     [("sink", "db_write")],
    "pymysql":      [("sink", "db_write")],
    "motor":        [("sink", "db_write")],
    "pymongo":      [("sink", "db_write")],
    # Sink — Slack
    "slack_sdk":    [("sink", "chat_post")],
    "slackclient":  [("sink", "chat_post")],
    # Channel — clipboard
    "pyperclip":    [("channel", "clipboard")],
    "xerox":        [("channel", "clipboard")],
    # Channel — browser session
    "selenium":     [("channel", "browser_session")],
    "playwright":   [("channel", "browser_session")],
    "pyppeteer":    [("channel", "browser_session")],
    "undetected_chromedriver": [("channel", "browser_session")],
}

# Specific function-call signatures → (cap_type, label)
# Format: ("module_or_attr", "method") → [(cap_type, label)]
_PY_CALL_CAPS: dict[tuple[str, str], list[tuple[CapType, str]]] = {
    # requests
    ("requests", "get"):    [("source", "web_fetch")],
    ("requests", "post"):   [("sink",   "network_send")],
    ("requests", "put"):    [("sink",   "network_send")],
    ("requests", "delete"): [("sink",   "network_send")],
    ("requests", "patch"):  [("sink",   "network_send")],
    # httpx
    ("httpx", "get"):       [("source", "web_fetch")],
    ("httpx", "post"):      [("sink",   "network_send")],
    # subprocess
    ("subprocess", "run"):  [("sink", "shell_exec")],
    ("subprocess", "call"): [("sink", "shell_exec")],
    ("subprocess", "Popen"):[("sink", "shell_exec")],
    # os
    ("os", "system"):       [("sink", "shell_exec")],
    ("os", "execv"):        [("sink", "shell_exec")],
    ("os", "execvp"):       [("sink", "shell_exec")],
    ("os", "popen"):        [("sink", "shell_exec")],
    ("os", "remove"):       [("sink", "file_delete")],
    ("os", "unlink"):       [("sink", "file_delete")],
    # shutil
    ("shutil", "rmtree"):   [("sink", "file_delete")],
    ("shutil", "move"):     [("sink", "file_write")],
    ("shutil", "copy"):     [("sink", "file_write")],
    # smtplib
    ("smtplib", "sendmail"):     [("sink", "email_send")],
    ("SMTP", "sendmail"):        [("sink", "email_send")],
    ("SMTP_SSL", "sendmail"):    [("sink", "email_send")],
    # pyperclip
    ("pyperclip", "copy"):  [("channel", "clipboard", "write")],
    ("pyperclip", "paste"): [("channel", "clipboard", "read")],
    # os.environ — only when the key name looks like a credential
    # (plain os.environ.get('NODE_ENV') or os.environ.get('HOME') is NOT a credential)
    # This entry intentionally omitted; credential detection done via text-level regex below
}

# Workspace path patterns → channel:workspace_file
_WORKSPACE_PATH_RE = re.compile(
    r"\.openclaw[\\/]workspace|openclaw_workspace",
    re.IGNORECASE,
)
# Temp file patterns → channel:tmp_file
_TMP_PATH_RE = re.compile(r"[\"']/tmp/", re.IGNORECASE)
# eval/exec with dynamic content → sink:script_eval

def _get_attr_chain(node: ast.expr) -> list[str]:
    """Flatten an attribute access chain to a list of names."""
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return list(reversed(parts))


def extract_python(text: str, filepath: str) -> list[Capability]:
    """Extract capabilities from a Python source file via AST."""
    try:
        # Suppress SyntaxWarning: skills may contain invalid escape sequences
        # (e.g. \$ in Python string literals for shell commands). These are
        # warnings about the SKILL CODE, not about our extractor.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", SyntaxWarning)
            tree = ast.parse(text)
    except SyntaxError:
        return []

    caps: list[Capability] = []
    seen: set[tuple[str, str, str | None]] = set()  # (cap_type, label, role) dedup

    def add(cap_type: CapType, label: str, evidence_detail: str,
            role: str | None = None) -> None:
        key = (cap_type, label, role)
        if key not in seen:
            seen.add(key)
            caps.append(Capability(cap_type=cap_type, label=label,
                                   evidence=f"{filepath}:{evidence_detail}",
                                   role=role))

    imported: set[str] = set()

    for node in ast.walk(tree):
        # ── Imports ──────────────────────────────────────────────────────────
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name.split(".")[0] for alias in node.names]
            elif node.module:
                names = [node.module.split(".")[0]]
            for mod in names:
                imported.add(mod)
                for cap_type, label in _PY_IMPORT_CAPS.get(mod, []):
                    add(cap_type, label, f"import:{mod}")

        # ── Function calls ────────────────────────────────────────────────────
        elif isinstance(node, ast.Call):
            chain = _get_attr_chain(node.func if isinstance(node.func, (ast.Attribute, ast.Name)) else ast.Name(id=""))
            if len(chain) >= 2:
                key = (chain[-2], chain[-1])
                for entry in _PY_CALL_CAPS.get(key, []):
                    cap_type = entry[0]
                    label    = entry[1]
                    role     = entry[2] if len(entry) > 2 else None
                    lineno   = getattr(node, "lineno", "?")
                    add(cap_type, label, f"L{lineno}:{'.'.join(chain)}", role=role)


    # open() — detect via source walk (separate pass for clarity)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        fname = ""
        if isinstance(func, ast.Name):
            fname = func.id
        elif isinstance(func, ast.Attribute):
            fname = func.attr
        if fname != "open":
            continue
        lineno = getattr(node, "lineno", "?")
        # Check mode argument (2nd positional or keyword 'mode')
        mode = "r"
        if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
            mode = str(node.args[1].value)
        for kw in node.keywords:
            if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
                mode = str(kw.value.value)
        if "w" in mode or "a" in mode or "x" in mode:
            add("sink", "file_write", f"L{lineno}:open({mode!r})")
            # Check if path mentions workspace → channel:write
            if len(node.args) >= 1 and isinstance(node.args[0], ast.Constant):
                path_val = str(node.args[0].value)
                if _WORKSPACE_PATH_RE.search(path_val):
                    add("channel", "workspace_file", f"L{lineno}:open-workspace-write", role="write")
                elif _TMP_PATH_RE.search(path_val):
                    add("channel", "tmp_file", f"L{lineno}:open-tmp-write", role="write")
        else:
            # read mode
            if len(node.args) >= 1 and isinstance(node.args[0], ast.Constant):
                path_val = str(node.args[0].value).lower()
                if any(k in path_val for k in (".env", "secrets", "credentials", "token", "api_key")):
                    add("source", "credential_read", f"L{lineno}:open-credentials")
                if _WORKSPACE_PATH_RE.search(path_val):
                    add("channel", "workspace_file", f"L{lineno}:open-workspace-read", role="read")
                elif _TMP_PATH_RE.search(path_val):
                    add("channel", "tmp_file", f"L{lineno}:open-tmp-read", role="read")

    # eval/exec with non-literal arg → script_eval
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            fname = func.id if isinstance(func, ast.Name) else ""
            if fname in ("eval", "exec"):
                # If argument is not a literal string, it's dynamic
                if node.args and not isinstance(node.args[0], ast.Constant):
                    lineno = getattr(node, "lineno", "?")
                    add("sink", "script_eval", f"L{lineno}:eval/exec(dynamic)")

    # Text-level fallback for workspace / tmp paths (in string literals not caught above)
    # These are undirected (role=None) — we can't tell write vs read from a bare path string
    if _WORKSPACE_PATH_RE.search(text):
        add("channel", "workspace_file", "text:workspace_path_literal", role=None)
    if _TMP_PATH_RE.search(text):
        add("channel", "tmp_file", "text:tmp_path_literal", role=None)

    return caps


# ─── Shell extractor ──────────────────────────────────────────────────────────

# Each entry: (regex_pattern, cap_type, label, evidence_tag)
_SHELL_RULES: list[tuple[re.Pattern[str], CapType, str, str]] = [
    # Source
    (re.compile(r"\bcurl\b(?!.*-[XPpDd].*(?:POST|PUT|PATCH))", re.I), "source", "web_fetch",   "curl-GET"),
    (re.compile(r"\bwget\b(?!.*--post)",                                re.I), "source", "web_fetch",   "wget-GET"),
    (re.compile(r"\bgit\s+clone\b",                                     re.I), "source", "web_fetch",   "git-clone"),
    # Sink — shell exec
    (re.compile(r"\beval\b",                         re.I), "sink", "shell_exec",       "eval"),
    (re.compile(r"\bbash\s+-c\b",                    re.I), "sink", "shell_exec",       "bash-c"),
    (re.compile(r"\bsh\s+-c\b",                      re.I), "sink", "shell_exec",       "sh-c"),
    # Sink — network send
    (re.compile(r"\bcurl\b.*-[XxPp]\s*POST",         re.I), "sink", "network_send",    "curl-POST"),
    (re.compile(r"\bcurl\b.*-[Dd]\b",                re.I), "sink", "network_send",    "curl-data"),
    (re.compile(r"\bwget\b.*--post-data",             re.I), "sink", "network_send",    "wget-post"),
    (re.compile(r"\bnc\b.*[0-9]{2,5}",               re.I), "sink", "network_send",    "netcat"),
    # Sink — email
    (re.compile(r"\bsendmail\b|\bmail\s+-s\b|\bmsmtp\b", re.I), "sink", "email_send", "sendmail"),
    # Sink — remote exec
    (re.compile(r"\bssh\b.+\b\w",                    re.I), "sink", "remote_exec",     "ssh-cmd",    None),
    (re.compile(r"\brsync\b",                        re.I), "sink", "remote_exec",     "rsync",       None),
    # Sink — file delete
    (re.compile(r"\brm\s+(-[rfRF]+\s+|--recursive)", re.I), "sink", "file_delete",    "rm-rf",       None),
    (re.compile(r"\bshred\b",                        re.I), "sink", "file_delete",     "shred",       None),
    (re.compile(r"\bdd\b.*of=",                      re.I), "sink", "file_write",      "dd-of",       None),
    # Sink — privilege escalate
    (re.compile(r"\bsudo\b",                         re.I), "sink", "privilege_escalate", "sudo",     None),
    (re.compile(r"\bsu\s+-",                         re.I), "sink", "privilege_escalate", "su",       None),
    # Channel — workspace (directed: >/.openclaw = write, cat/.openclaw = read)
    (re.compile(r">\s*.*\.openclaw[\\/]workspace",   re.I), "channel", "workspace_file", "workspace-write", "write"),
    (re.compile(r">>\s*.*\.openclaw[\\/]workspace",  re.I), "channel", "workspace_file", "workspace-append", "write"),
    (re.compile(r"\bcat\b.*\.openclaw[\\/]workspace",re.I), "channel", "workspace_file", "workspace-read",  "read"),
    (re.compile(r"\.openclaw[\\/]workspace",         re.I), "channel", "workspace_file", "workspace-path",  None),
    # Channel — tmp file (directed)
    (re.compile(r">\s*/tmp/",                        re.I), "channel", "tmp_file",      "tmp-write",   "write"),
    (re.compile(r"\bcat\b\s+/tmp/",                  re.I), "channel", "tmp_file",      "tmp-read",    "read"),
    (re.compile(r"\$TMPDIR|\$TMP\b|/tmp/",           re.I), "channel", "tmp_file",      "tmp-path",    None),
    # Channel — stdout pipe: undirected (role=None).
    # An internal pipe (cmd1 | cmd2) within one skill's code does NOT prove
    # that this skill's output flows into another skill's stdin as a cross-skill channel.
    # Directionality for stdout_pipe would require explicit evidence of inter-skill piping.
    (re.compile(r"\|"),                                     "channel", "stdout_pipe",    "pipe",        None),
    # Sink — git push / commit
    (re.compile(r"\bgit\s+push\b",                   re.I), "sink", "code_commit",      "git-push",    None),
    (re.compile(r"\bgit\s+commit\b",                 re.I), "sink", "code_commit",      "git-commit",  None),
    # Source — env credential read
    # Require word boundary after TOKEN to avoid matching $TOKENS (counter variable)
    (re.compile(r"\$\{?(?:API_KEY|SECRET_KEY|SECRET_ID|PASSWORD|CREDENTIALS|AUTH_TOKEN|ACCESS_TOKEN|BEARER_TOKEN)\b"), "source", "credential_read", "env-secret-var", None),
]


def extract_shell(text: str, filepath: str) -> list[Capability]:
    """Extract capabilities from a shell script via regex rules."""
    caps: list[Capability] = []
    seen: set[tuple[str, str, str | None]] = set()

    for line_no, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue  # skip comments
        for rule in _SHELL_RULES:
            pattern, cap_type, label, tag = rule[0], rule[1], rule[2], rule[3]
            role: str | None = rule[4] if len(rule) > 4 else None
            if pattern.search(stripped):
                key = (cap_type, label, role)
                if key not in seen:
                    seen.add(key)
                    caps.append(Capability(
                        cap_type=cap_type,
                        label=label,
                        evidence=f"{filepath}:L{line_no}:{tag}",
                        role=role,
                    ))
    return caps


# ─── JavaScript / TypeScript extractor ───────────────────────────────────────

_JS_RULES: list[tuple[re.Pattern[str], CapType, str, str]] = [
    # Source
    (re.compile(r"\bfetch\s*\([^)]*https?://",         re.I), "source", "web_fetch",     "fetch-GET"),
    (re.compile(r"\baxios\.get\s*\(",                  re.I), "source", "web_fetch",     "axios.get"),
    (re.compile(r"\bxhr\.open\s*\(['\"]GET",           re.I), "source", "web_fetch",     "xhr-GET"),
    (re.compile(r"\brequire\s*\(['\"]node-fetch['\"]\)",re.I), "source", "web_fetch",    "node-fetch"),
    # Sink — network
    (re.compile(r"\bfetch\s*\([^,)]+,\s*\{[^}]*method\s*:\s*['\"]POST", re.I), "sink", "network_send", "fetch-POST"),
    (re.compile(r"\baxios\.post\s*\(",                 re.I), "sink", "network_send",   "axios.post"),
    (re.compile(r"\baxios\.put\s*\(",                  re.I), "sink", "network_send",   "axios.put"),
    # Sink — shell exec
    (re.compile(r"\bchild_process\b",                  re.I), "sink", "shell_exec",     "child_process"),
    (re.compile(r"\bexecSync\s*\(|\bexec\s*\(",        re.I), "sink", "shell_exec",     "exec"),
    (re.compile(r"\bspawnSync\s*\(|\bspawn\s*\(",      re.I), "sink", "shell_exec",     "spawn"),
    # Sink — file write
    (re.compile(r"\bfs\.writeFile\b|\bfs\.appendFile\b",re.I), "sink", "file_write",   "fs.writeFile"),
    (re.compile(r"\bfs\.unlinkSync\b|\bfs\.unlink\b",  re.I), "sink", "file_delete",   "fs.unlink"),
    # Sink — eval
    (re.compile(r"\beval\s*\(",                        re.I), "sink", "script_eval",    "eval"),
    (re.compile(r"\bnew\s+Function\s*\(",              re.I), "sink", "script_eval",    "new-Function"),
    # Channel — browser session
    (re.compile(r"\bpuppeteer\b|\bplaywright\b",       re.I), "channel", "browser_session", "puppeteer/playwright"),
    (re.compile(r"\blocalStorage\b|\bsessionStorage\b",re.I), "channel", "browser_session", "localStorage"),
    # Source — credential (only when accessing a credential-like env var name)
    (re.compile(r"\bprocess\.env\.(?:[A-Z_]*(?:KEY|SECRET|TOKEN|PASSWORD|CREDENTIAL|AUTH)[A-Z_]*)", re.I), "source", "credential_read", "process.env-credential"),
    # Channel — workspace
    (re.compile(r"\.openclaw[\\/]workspace",           re.I), "channel", "workspace_file", "workspace-path"),
    (re.compile(r"['\"][/\\]tmp[/\\]",                 re.I), "channel", "tmp_file",       "tmp-path"),
    # Sink — post to Slack
    (re.compile(r"slack.*\.post\b|WebClient.*chat\.postMessage", re.I), "sink", "chat_post", "slack-post"),
]


def extract_js(text: str, filepath: str) -> list[Capability]:
    """Extract capabilities from JavaScript / TypeScript via regex rules."""
    caps: list[Capability] = []
    seen: set[tuple[str, str]] = set()

    for line_no, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith("//") or stripped.startswith("*"):
            continue
        for pattern, cap_type, label, tag in _JS_RULES:
            if pattern.search(stripped):
                key = (cap_type, label)
                if key not in seen:
                    seen.add(key)
                    caps.append(Capability(
                        cap_type=cap_type,
                        label=label,
                        evidence=f"{filepath}:L{line_no}:{tag}",
                    ))
    return caps


# ─── SKILL.md / Markdown extractor ────────────────────────────────────────────

# MCP tool name → capabilities
_MCP_TOOL_CAPS: dict[str, list[tuple[CapType, str]]] = {
    # Source tools
    "browser_search":         [("source", "web_fetch")],
    "web_search":             [("source", "web_fetch")],
    "google_search":          [("source", "web_fetch")],
    "tavily_search":          [("source", "web_fetch")],
    "browser_navigate":       [("source", "web_fetch"), ("channel", "browser_session")],
    "browser_take_screenshot":[("source", "screen_capture"), ("channel", "browser_session")],
    "get_webpage":            [("source", "web_fetch")],
    "fetch_url":              [("source", "web_fetch")],
    "read_email":             [("source", "email_receive")],
    "gmail_read":             [("source", "email_receive")],
    "read_file":              [("source", "user_file_read")],
    "read_document":          [("source", "user_file_read")],
    "memory_read":            [("source", "memory_read"), ("channel", "agent_memory")],
    "remember":               [("source", "memory_read"), ("channel", "agent_memory")],
    "recall":                 [("source", "memory_read"), ("channel", "agent_memory")],
    "get_calendar_events":    [("source", "calendar_read")],
    "list_contacts":          [("source", "contact_read")],
    # Sink tools
    "run_command":            [("sink", "shell_exec")],
    "execute_bash":           [("sink", "shell_exec")],
    "shell":                  [("sink", "shell_exec")],
    "bash":                   [("sink", "shell_exec")],
    "terminal":               [("sink", "shell_exec")],
    "send_email":             [("sink", "email_send")],
    "gmail_send":             [("sink", "email_send")],
    "send_message":           [("sink", "chat_post")],
    "slack_post":             [("sink", "chat_post")],
    "slack_message":          [("sink", "chat_post")],
    "write_file":             [("sink", "file_write")],
    "save_file":              [("sink", "file_write")],
    "delete_file":            [("sink", "file_delete")],
    "http_post":              [("sink", "network_send")],
    "webhook":                [("sink", "webhook_call")],
    "github_push":            [("sink", "code_commit")],
    "git_push":               [("sink", "code_commit")],
    "create_calendar_event":  [("sink", "calendar_write")],
    "db_insert":              [("sink", "db_write")],
    # Channel tools
    "memory_write":           [("channel", "agent_memory")],
    "store_memory":           [("channel", "agent_memory")],
    "write_memory":           [("channel", "agent_memory")],
    "clipboard_copy":         [("channel", "clipboard")],
    "clipboard_paste":        [("channel", "clipboard")],
    "browser_get_cookies":    [("channel", "browser_session")],
}

# <use_mcp_tool> block parser
_MCP_TOOL_BLOCK_RE = re.compile(
    r"<use_mcp_tool>(.*?)</use_mcp_tool>",
    re.DOTALL | re.IGNORECASE,
)
_MCP_TOOL_NAME_RE = re.compile(
    r"<tool_name>\s*([^\s<]+)\s*</tool_name>",
    re.IGNORECASE,
)
# Also catch inline patterns: `run_command`, shell mentions in system prompt
_SYSTEM_PROMPT_SINK_RE = re.compile(
    r"\b(?:execute|run)\s+(?:a\s+)?(?:shell|bash|command|script)\b",
    re.IGNORECASE,
)
_SYSTEM_PROMPT_SOURCE_RE = re.compile(
    r"\b(?:fetch|search|scrape|crawl|browse)\b.*\b(?:web|url|page|site)\b",
    re.IGNORECASE,
)
# Only match env vars / access patterns that are clearly credential-related
_CREDENTIAL_NAMES = r"(?:API_KEY|SECRET|TOKEN|PASSWORD|CREDENTIAL|AUTH_KEY|PRIVATE_KEY|ACCESS_KEY|CLIENT_SECRET|OPENAI_API|ANTHROPIC_API|BEARER)"
_ENV_VAR_RE = re.compile(
    # export SECRET_KEY=... or SECRET_KEY=...
    r"(?:export\s+)?"
    + _CREDENTIAL_NAMES + r"[A-Z_]*\s*="
    # os.environ['API_KEY'] or os.environ.get('TOKEN')
    r"|os\.environ(?:\[\s*[\"']|\.get\s*\(\s*[\"'])" + _CREDENTIAL_NAMES,
    re.IGNORECASE,
)
_WORKSPACE_PATH_MD_RE = re.compile(
    r"\.openclaw[\\/]workspace|~/\.openclaw",
    re.IGNORECASE,
)
_MEMORY_WRITE_RE = re.compile(
    r"\b(?:write|store|save)\s+(?:to\s+)?memory\b|\bmemory\s+write\b",
    re.IGNORECASE,
)
_MEMORY_READ_RE = re.compile(
    r"\b(?:read|recall|retrieve|load|get)\s+(?:from\s+)?memory\b|\bmemory\s+read\b",
    re.IGNORECASE,
)

_MARKDOWN_HEURISTIC_RULES: list[tuple[re.Pattern[str], CapType, str, str, str | None]] = [
    (
        re.compile(
            r"\b(?:read|view|list|search|find|get|show|inspect|retrieve|monitor)\b.{0,40}\b"
            r"(?:note|notes|menu|goods|cart|status|device|devices|subscription|subscriptions|"
            r"customer|customers|account|accounts|document|documents|file|files|folder|folders|"
            r"email|emails|message|messages|events|calendar)\b",
            re.IGNORECASE | re.DOTALL,
        ),
        "source",
        "user_file_read",
        "markdown:read_view_search_object",
        None,
    ),
    (
        re.compile(
            r"\b(?:browse|fetch|scrape|crawl|search)\b.{0,40}\b"
            r"(?:web|website|page|pages|url|urls|internet|site|sites|article|articles|feed|feeds)\b",
            re.IGNORECASE | re.DOTALL,
        ),
        "source",
        "web_fetch",
        "markdown:web_fetch_object",
        None,
    ),
    (
        re.compile(
            r"\b(?:create|add|submit|send|post|publish|write|save|export|move|update|change|"
            r"suspend|delete|remove|clear|commit|push|turn on|turn off|control)\b.{0,50}\b"
            r"(?:note|notes|order|orders|cart|subscription|subscriptions|license|licenses|"
            r"device|devices|file|files|folder|folders|event|events|message|messages|post|posts|"
            r"customer|customers|account|accounts|plan|plans)\b",
            re.IGNORECASE | re.DOTALL,
        ),
        "sink",
        "file_write",
        "markdown:write_or_control_object",
        None,
    ),
    (
        re.compile(
            r"\b(?:submit|place|create)\b.{0,30}\b(?:order|booking|reservation|subscription)\b",
            re.IGNORECASE | re.DOTALL,
        ),
        "sink",
        "network_send",
        "markdown:transactional_submit",
        None,
    ),
    (
        re.compile(
            r"\b(?:delete|remove|clear)\b.{0,30}\b(?:file|files|note|notes|folder|folders|cart)\b",
            re.IGNORECASE | re.DOTALL,
        ),
        "sink",
        "file_delete",
        "markdown:delete_object",
        None,
    ),
    (
        re.compile(
            r"\b(?:write|save|export|store|persist)\b.{0,40}\b(?:workspace|markdown|html|report|summary|note|notes)\b",
            re.IGNORECASE | re.DOTALL,
        ),
        "channel",
        "workspace_file",
        "markdown:workspace_write_pattern",
        "write",
    ),
    (
        re.compile(
            r"\b(?:read|view|search|list|load|get)\b.{0,40}\b(?:workspace|markdown|html|report|summary|note|notes)\b",
            re.IGNORECASE | re.DOTALL,
        ),
        "channel",
        "workspace_file",
        "markdown:workspace_read_pattern",
        "read",
    ),
    (
        re.compile(
            r"\b(?:send|post|notify|alert)\b.{0,40}\b(?:slack|discord|telegram|email|message|messages|notification)\b",
            re.IGNORECASE | re.DOTALL,
        ),
        "sink",
        "chat_post",
        "markdown:notify_or_post_pattern",
        None,
    ),
]


def extract_skillmd(text: str, filepath: str) -> list[Capability]:
    """
    Extract capabilities from SKILL.md or any Markdown file.
    Primary signal: <use_mcp_tool> declarations.
    Secondary: system prompt keyword patterns.
    """
    caps: list[Capability] = []
    seen: set[tuple[str, str, str | None]] = set()

    def add(cap_type: CapType, label: str, evidence_detail: str,
            role: str | None = None) -> None:
        key = (cap_type, label, role)
        if key not in seen:
            seen.add(key)
            caps.append(Capability(cap_type=cap_type, label=label,
                                   evidence=f"{filepath}:{evidence_detail}",
                                   role=role))

    # Primary: parse <use_mcp_tool> blocks
    for block in _MCP_TOOL_BLOCK_RE.finditer(text):
        block_text = block.group(1)
        tool_match = _MCP_TOOL_NAME_RE.search(block_text)
        if not tool_match:
            continue
        tool_name = tool_match.group(1).strip().lower()
        mapped = _MCP_TOOL_CAPS.get(tool_name)
        if mapped:
            for cap_type, label in mapped:
                add(cap_type, label, f"use_mcp_tool:{tool_name}")
        else:
            # Unknown tool: use name heuristics
            if any(k in tool_name for k in ("search", "browse", "fetch", "read", "get")):
                add("source", "web_fetch", f"use_mcp_tool:{tool_name}(inferred)")
            if any(k in tool_name for k in ("send", "post", "write", "create", "delete", "push")):
                add("sink", "network_send", f"use_mcp_tool:{tool_name}(inferred)")
            if any(k in tool_name for k in ("memory", "store", "remember")):
                # remember/store = write; recall/retrieve = read
                if any(k in tool_name for k in ("recall", "retrieve", "read", "search", "get")):
                    add("channel", "agent_memory", f"use_mcp_tool:{tool_name}(inferred)", role="read")
                else:
                    add("channel", "agent_memory", f"use_mcp_tool:{tool_name}(inferred)", role="write")

    # Secondary: system prompt keyword patterns
    if _SYSTEM_PROMPT_SINK_RE.search(text):
        add("sink", "shell_exec", "system_prompt:execute_shell_pattern")
    if _SYSTEM_PROMPT_SOURCE_RE.search(text):
        add("source", "web_fetch", "system_prompt:fetch_web_pattern")
    # NOTE: _ENV_VAR_RE intentionally NOT used for credential_read in Markdown.
    # Markdown files commonly DOCUMENT that a skill needs an API key (e.g., README setup
    # instructions), but this is not runtime credential reading. Only code files (.py/.sh/.js)
    # can reliably signal actual runtime credential access.
    # credential_read from Markdown is therefore suppressed to avoid systematic false positives.
    if _WORKSPACE_PATH_MD_RE.search(text):
        # role=None: markdown mentions workspace path but we can't determine read vs write
        add("channel", "workspace_file", "text:workspace_path", role=None)
    if _MEMORY_WRITE_RE.search(text):
        add("channel", "agent_memory", "text:memory_write_pattern", role="write")
    if _MEMORY_READ_RE.search(text):
        add("channel", "agent_memory", "text:memory_read_pattern", role="read")
    for pattern, cap_type, label, tag, role in _MARKDOWN_HEURISTIC_RULES:
        if pattern.search(text):
            add(cap_type, label, tag, role=role)

    return caps


# ─── JSON extractor ────────────────────────────────────────────────────────────

_JSON_CMD_FIELDS = {"command", "cmd", "run", "script", "exec", "args"}
_JSON_URL_FIELDS = {"url", "endpoint", "webhook", "baseUrl", "apiUrl"}


def extract_json(text: str, filepath: str) -> list[Capability]:
    """
    Extract capabilities from JSON (e.g. MCP tool schema, config).
    Looks for command / URL fields that indicate shell or network capability.
    """
    caps: list[Capability] = []
    seen: set[tuple[str, str]] = set()

    def add(cap_type: CapType, label: str, evidence_detail: str) -> None:
        key = (cap_type, label)
        if key not in seen:
            seen.add(key)
            caps.append(Capability(cap_type=cap_type, label=label,
                                   evidence=f"{filepath}:{evidence_detail}"))

    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return []

    def walk_json(obj: object, path: str = "") -> None:
        if isinstance(obj, dict):
            for key, val in obj.items():
                child_path = f"{path}.{key}" if path else key
                if key.lower() in _JSON_CMD_FIELDS and isinstance(val, str):
                    add("sink", "shell_exec", f"json:{child_path}")
                if key.lower() in _JSON_URL_FIELDS and isinstance(val, str):
                    if val.startswith("http"):
                        add("source", "web_fetch", f"json:{child_path}(url)")
                walk_json(val, child_path)
        elif isinstance(obj, list):
            for i, item in enumerate(obj):
                walk_json(item, f"{path}[{i}]")

    walk_json(data)
    return caps


# ─── Rust extractor ───────────────────────────────────────────────────────────

_RUST_RULES: list[tuple[re.Pattern[str], CapType, str, str]] = [
    # Sink — shell exec
    (re.compile(r"\bstd::process::Command\b|\bCommand::new\b",   re.I), "sink", "shell_exec",   "process::Command"),
    (re.compile(r"\.spawn\s*\(\)|\bstd::process::exit\b",        re.I), "sink", "shell_exec",   "spawn/exit"),
    # Source — network GET
    (re.compile(r"\breqwest::get\b|\bclient\.get\b",             re.I), "source", "web_fetch",  "reqwest::get"),
    # Sink — network POST
    (re.compile(r"\bclient\.post\b|\breqwest::Client.*\.post\b", re.I), "sink", "network_send", "reqwest::post"),
    # Sink — file write
    (re.compile(r"\bstd::fs::write\b|\bFile::create\b",          re.I), "sink", "file_write",   "fs::write/create"),
    (re.compile(r"\bstd::fs::remove_file\b|\bfs::remove_dir\b",  re.I), "sink", "file_delete",  "fs::remove"),
    # Source — credential
    (re.compile(r'std::env::var\s*\(\s*"(?:[A-Z_]*(?:KEY|SECRET|TOKEN|PASSWORD|AUTH)[A-Z_]*)"', re.I), "source", "credential_read", "env::var-credential"),
    # Channel — workspace
    (re.compile(r"\.openclaw.workspace",                          re.I), "channel", "workspace_file", "workspace-path"),
    (re.compile(r'"/tmp/',                                               ), "channel", "tmp_file",      "tmp-path"),
    # Sink — remote exec (SSH crates)
    (re.compile(r"\bssh2\b|\bssh::Session\b",                    re.I), "sink", "remote_exec",  "ssh2-crate"),
]


def extract_rust(text: str, filepath: str) -> list[Capability]:
    """Extract capabilities from a Rust source file via regex rules."""
    caps: list[Capability] = []
    seen: set[tuple[str, str]] = set()
    for line_no, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith("//"):
            continue
        for pattern, cap_type, label, tag in _RUST_RULES:
            if pattern.search(stripped):
                key = (cap_type, label)
                if key not in seen:
                    seen.add(key)
                    caps.append(Capability(cap_type=cap_type, label=label,
                                           evidence=f"{filepath}:L{line_no}:{tag}"))
    return caps


# ─── Go extractor ─────────────────────────────────────────────────────────────

_GO_RULES: list[tuple[re.Pattern[str], CapType, str, str]] = [
    # Sink — shell exec
    (re.compile(r"\bexec\.Command\b|\bos\.StartProcess\b",      re.I), "sink", "shell_exec",   "exec.Command"),
    # Source — network GET
    (re.compile(r"\bhttp\.Get\b|\bclient\.Get\b",               re.I), "source", "web_fetch",  "http.Get"),
    # Sink — network POST
    (re.compile(r"\bhttp\.Post\b|\bclient\.Post\b",             re.I), "sink", "network_send", "http.Post"),
    (re.compile(r'\bhttp\.NewRequest\s*\(\s*"POST"',            re.I), "sink", "network_send", "http.NewRequest-POST"),
    # Sink — file write/delete
    (re.compile(r"\bioutil\.WriteFile\b|\bos\.Create\b",        re.I), "sink", "file_write",   "os.Create"),
    (re.compile(r"\bos\.Remove\b|\bos\.RemoveAll\b",            re.I), "sink", "file_delete",  "os.Remove"),
    # Source — credential
    (re.compile(r'os\.Getenv\s*\(\s*"(?:[A-Z_]*(?:KEY|SECRET|TOKEN|PASSWORD|AUTH)[A-Z_]*)"', re.I), "source", "credential_read", "os.Getenv-credential"),
    # Channel — workspace / tmp
    (re.compile(r"\.openclaw.workspace",                         re.I), "channel", "workspace_file", "workspace-path"),
    (re.compile(r'"/tmp/',                                              ), "channel", "tmp_file",      "tmp-path"),
    # Sink — email (common Go mail libs)
    (re.compile(r"\bgomail\b|\bnet/smtp\b|\bsmtp\.SendMail\b",  re.I), "sink", "email_send",   "smtp.SendMail"),
    # Sink — remote exec
    (re.compile(r"\bgolang\.org/x/crypto/ssh\b|\bssh\.Dial\b",  re.I), "sink", "remote_exec",  "crypto/ssh"),
]


def extract_go(text: str, filepath: str) -> list[Capability]:
    """Extract capabilities from a Go source file via regex rules."""
    caps: list[Capability] = []
    seen: set[tuple[str, str]] = set()
    for line_no, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith("//"):
            continue
        for pattern, cap_type, label, tag in _GO_RULES:
            if pattern.search(stripped):
                key = (cap_type, label)
                if key not in seen:
                    seen.add(key)
                    caps.append(Capability(cap_type=cap_type, label=label,
                                           evidence=f"{filepath}:L{line_no}:{tag}"))
    return caps


# ─── Dispatch ─────────────────────────────────────────────────────────────────

def extract_from_file(text: str, filepath: str) -> list[Capability]:
    """
    Route a file to the appropriate extractor based on its extension / name.
    Returns list of Capability (may be empty).
    """
    p = Path(filepath)
    ext = p.suffix.lower()
    name = p.name.lower()

    if ext == ".py":
        return extract_python(text, filepath)
    if ext in (".sh", ".bash", ".zsh", ".fish"):
        return extract_shell(text, filepath)
    if ext in (".js", ".mjs", ".cjs", ".ts", ".tsx"):
        return extract_js(text, filepath)
    if ext == ".rs":
        return extract_rust(text, filepath)
    if ext == ".go":
        return extract_go(text, filepath)
    if name in ("skill.md", "readme.md", "system_prompt.md", "install.md") or ext in (".md", ".mdx"):
        return extract_skillmd(text, filepath)
    if ext == ".json":
        return extract_json(text, filepath)
    if ext in (".yaml", ".yml"):
        # YAML: treat as markdown-like text (keywords in values)
        return extract_skillmd(text, filepath)
    return []
