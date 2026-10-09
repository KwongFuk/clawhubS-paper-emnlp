"""Shared configuration and constants for SkillRugPull experiments."""

from __future__ import annotations

from pathlib import Path

# ── Paths ──────────────────────────────────────────────────────────────────────
ROOT = Path(__file__).parent
PROJECT_ROOT = ROOT.parent.parent.parent
DB_PATH = PROJECT_ROOT / "data" / "skills_sh" / "skills_sh.sqlite3"
DATA_DIR = ROOT / "data"
RESULTS_DIR = ROOT / "results"
ANNOTATION_DIR = ROOT / "annotation"

DATA_DIR.mkdir(exist_ok=True)
RESULTS_DIR.mkdir(exist_ok=True)

# ── Risk family keyword banks ──────────────────────────────────────────────────

EXECUTION_KEYWORDS = [
    "shell", "exec(", "subprocess", "terminal", "command line",
    "bash", " sh ", "powershell", "os.system", "popen", "spawn",
    "run_command", "execute command", "system call",
]

SECRET_KEYWORDS = [
    "api_key", "apikey", "api key", "secret key", "client secret",
    "access token", "bearer token", "personal access token",
    "oauth", "oauth2", "authorization code", "credential", "password",
    "private key", "signing key", "webhook secret",
]

NETWORK_KEYWORDS = [
    "requests.get", "requests.post", "fetch(", "http.get", "http.post",
    "websocket", "webhook", "remote url", "external api",
    "urllib", "httpx", "aiohttp", "curl ", "wget ",
]

FILE_SYSTEM_KEYWORDS = [
    "read file", "write file", "open(", "file.write", "file.read",
    "upload", "download", "temp file", "local storage",
    "os.path", "pathlib", "shutil", "glob(",
]

EXTERNAL_ACTION_KEYWORDS = [
    "send email", "send message", "post message", "create pr",
    "create pull request", "deploy ", "purchase", "schedule meeting",
    "browser automation", "click button", "fill form", "submit form",
    "database", "db.execute", "sql query",
]

AGENTIC_KEYWORDS = [
    "plan and execute", "chain of thought", "tool use", "autonomous",
    "multi-step", "memory", "loop until", "delegate to",
    "agent workflow", "reflection", "self-correct",
]

RISK_FAMILIES: dict[str, list[str]] = {
    "execution": EXECUTION_KEYWORDS,
    "secret": SECRET_KEYWORDS,
    "network": NETWORK_KEYWORDS,
    "file_system": FILE_SYSTEM_KEYWORDS,
    "external_action": EXTERNAL_ACTION_KEYWORDS,
    "agentic": AGENTIC_KEYWORDS,
}

# ── Vagueness indicators (penalize disclosure if these dominate) ──────────────

VAGUE_DISCLOSURE_PHRASES = [
    "minor fix", "minor fixes", "bug fix", "bug fixes",
    "performance improvement", "general improvement",
    "minor improvement", "internal refactoring",
    "various updates", "miscellaneous", "small update",
    "patch release", "hotfix",
]

# ── Score weights (tunable) ────────────────────────────────────────────────────

CES_WEIGHTS: dict[str, float] = {
    "execution": 3.0,
    "secret": 2.5,
    "network": 2.0,
    "file_system": 1.5,
    "external_action": 2.0,
    "agentic": 1.5,
}

PES_WEIGHTS: dict[str, float] = {
    "requires_shell_access": 3.0,
    "requires_token": 2.5,
    "requires_api_key": 2.5,
    "requires_oauth": 2.0,
    "requires_browser_access": 2.0,
    "requires_github": 1.5,
    "requires_email": 2.0,
    "requires_slack": 1.5,
    "requires_notion": 1.5,
    "requires_calendar": 1.5,
    "requires_local_file_access": 1.5,
    "requires_docs": 1.0,
    "requires_pdf": 0.5,
}

DGS_LAMBDAS: dict[str, float] = {
    "lambda_ces": 1.0,
    "lambda_pes": 1.0,
    "lambda_sds": 1.5,
}

# ── SkillRugPull thresholds ────────────────────────────────────────────────────

# A skill is a "candidate" if:
#   risk_expansion >= RISK_THRESHOLD  AND  sds <= DISCLOSURE_THRESHOLD
RISK_EXPANSION_THRESHOLD = 0.4   # top ~20% by risk
DISCLOSURE_THRESHOLD = 0.35       # bottom ~35% by disclosure

# ── Annotation sampling parameters ────────────────────────────────────────────

ANNOTATION_SAMPLE_SIZE = 500
ANNOTATION_STRATA = {
    "low_risk_low_gap": 100,
    "high_risk_high_expansion": 150,
    "high_rps_candidates": 150,
    "uncertain": 100,
}
