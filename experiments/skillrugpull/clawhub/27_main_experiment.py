#!/usr/bin/env python3
"""
27_main_experiment.py — TrustDrift Main Experiment
====================================================
Full analysis of ALL 17,738+ skills:

  C_visible(s):  What the user SEES before installing
    ├── Market description text  (keyword + pattern matching)
    └── YAML capability fields   (autonomous, permissions, capabilities)

  C_actual(s):   What the skill ACTUALLY does  (every file scanned)
    ├── SKILL.md / skill.md      code blocks + prose
    ├── README.md                documentation claims
    ├── *.py                     Python source files
    ├── *.js / *.ts              JavaScript/TypeScript
    ├── install.sh / setup.sh    Shell scripts (run at install time!)
    ├── agents/*.yaml            Agent system prompts
    ├── requirements.txt         Python dependencies → inferred caps
    ├── package.json             npm dependencies → inferred caps
    ├── _meta.json / skill.json  Structured capability declarations
    └── references/*.md          API docs, examples, troubleshooting

  IC Framework:
    DM(s) = 4-cell matrix per capability family f:
        C_visible has f | C_visible no f
        ─────────────────────────────────
        C_actual has f  │  AD    │  UD  │
        C_actual no f   │  FC    │  TN  │

    CR(s)  = AD / (AD + UD)          Capability Recall
    CP(s)  = AD / (AD + FC)          Description Precision
    CDI(s) = (UD + FC) / total       Capability Discrepancy Index

  IC Types:
    IC-0  Accurate     UD=0, FC=0, AD>0
    IC-1  Full Omit    UD>0, AD=0, FC=0   CR=0
    IC-2  Partial Omit UD>0, AD>0, FC=0   0<CR<1
    IC-3  False Claim  FC>0, UD=0
    IC-4  Mixed        UD>0, FC>0
    TN    No caps declared, none detected

Run:
    python3 27_main_experiment.py              # all 17K+
    python3 27_main_experiment.py --limit 200  # quick test
"""

import os, re, json, yaml, sqlite3, argparse, logging, sys
from pathlib import Path
from collections import Counter, defaultdict
from typing import Dict, List, Set, Optional, Tuple

sys.path.insert(0, str(Path(__file__).parent))
from config import DB, DATA_ROOT, RESULTS, get_conn

logging.basicConfig(level=logging.INFO,
                    format="[%(asctime)s] %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("main_exp")

OUTDIR  = Path(os.environ.get("OUTDIR", str(RESULTS)))
OUTPUT  = OUTDIR / "main_experiment_results.jsonl"
REPORT  = OUTDIR / "main_experiment_report.json"

# ════════════════════════════════════════════════════════════════════════════
# 14 Capability Families — Extended LSCPS
# ════════════════════════════════════════════════════════════════════════════
FAMILIES = [
    "SHELL_EXEC", "CRED_READ", "CRED_TRANSMIT", "FILE_DELETE",
    "NET_WRITE",  "AUTONOMOUS", "FILE_WRITE",   "NET_READ",
    "FILE_READ",  "MEMORY_WRITE", "MULTI_STEP", "CRED_STORE",
    "BROWSER_CONTROL", "CRYPTO_OPS",
]
ALL_FAMS = set(FAMILIES)

# ────────────────────────────────────────────────────────────────────────────
# C_actual regex patterns  (Python + JS/TS + Shell + YAML)
# ────────────────────────────────────────────────────────────────────────────
CODE_REGEX: Dict[str, List[str]] = {
    "SHELL_EXEC": [
        r'\bsubprocess\b', r'\bos\.system\b', r'\bos\.popen\b',
        r'\bexec\s*\(', r'\beval\s*\(', r'\bshell\s*=\s*True\b',
        r'\bshutil\.rmtree\b', r'\bchmod\b', r'\b/bin/sh\b', r'\bbash\b',
        r'\brm\s+-rf\b', r'\bsudo\b', r'\bapt-get\b', r'\byum\s+install\b',
        r'\bchown\b', r'\bkill\b', r'\bpkill\b',
        r'\bcurl\b.*\|\s*bash\b', r'\bwget\b.*\|\s*sh\b',
        r'#!/bin/bash', r'#!/bin/sh',
        r'\bchild_process\b', r'\bexecSync\b', r'\bspawnSync\b',
        r'\bexecFile\b', r'\bspawn\s*\(', r"require\(['\"]child_process",
    ],
    "CRED_READ": [
        r'\bapi[_\s]?key\b', r'\bpassword\b', r'\bprivate[_\s]?key\b',
        r'\bsecret\b', r'\baccess[_\s]?token\b', r'\bbearer\b',
        r'\boauth\b', r'\.env\b', r'\bcredential\b',
        r'\bprocess\.env\b', r'\bdotenv\b', r"require\(['\"]dotenv",
        r'\bgetenv\b', r'os\.environ\[',
        r'\$[A-Z_]{3,}\b', r'\bsource\b.*\.env', r'\bcat\b.*secret',
    ],
    "CRED_TRANSMIT": [
        r'\bsend.*key\b', r'\bupload.*credential\b', r'\bpost.*token\b',
        r'\bexfil\b', r'\bwebhook\b.*\b(key|token|secret)\b',
        r'axios\.post.*token', r'fetch.*Authorization.*Bearer',
    ],
    "NET_WRITE": [
        r'\brequests\.post\b', r'\brequests\.put\b', r'\bwebhook\b',
        r'\bhttpx\.post\b', r'\bsmtp\b', r'\bsend.*email\b',
        r'\bcurl\b.*-[dX].*POST', r'\bcurl\b.*--data\b',
        r'\baxios\.post\b', r'\bfetch\b.*\bPOST\b',
        r'\bnodemailer\b', r'\bsendgrid\b',
    ],
    "NET_READ": [
        r'\brequests\.get\b', r'\bhttpx\.get\b', r'\bwget\b',
        r'\bfetch\s*\(', r'\bcurl\s+https?\b',
        r'\baxios\.get\b', r'\bnode-fetch\b', r'\bgot\s*\(',
    ],
    "FILE_DELETE": [
        r'\bos\.remove\b', r'\bos\.unlink\b', r'\bshutil\.rmtree\b',
        r'\brm\s+-rf\b',
        r'\bfs\.unlinkSync\b', r'\bfs\.rmSync\b', r'\bfs\.rmdirSync\b',
    ],
    "FILE_WRITE": [
        r'\bopen\s*\(.*["\'](w|a|wb)', r'\.write\s*\(', r'\bshutil\.copy\b',
        r'\bos\.makedirs\b',
        r'\bfs\.writeFileSync\b', r'\bfs\.appendFileSync\b',
        r'\bfs\.createWriteStream\b',
        r'\btee\b', r'>\s*\S+\.(log|txt|json|csv)',
    ],
    "FILE_READ": [
        r'\bopen\s*\(.*["\'](r|rb)', r'\.read\s*\(', r'\bglob\b',
        r'\bos\.listdir\b', r'\bos\.walk\b', r'\bpathlib\b',
        r'\bfs\.readFileSync\b', r'\bfs\.readdirSync\b',
        r'\bcat\s+\S+\.(txt|json|csv|log|yaml)\b', r'\bfind\b.*-name\b',
    ],
    "AUTONOMOUS": [
        r'\bautonomous\b', r'\bautomatically\b', r'\bwithout.*confirm\b',
        r'\bbackground\b', r'\bcron\b', r'\bpersistent\b', r'\bschedule\b',
        r'\bno.*human.*approval\b', r'\bsilently\b',
        r'autonomous:\s*true', r'confirm_actions:\s*false',
        r'\bnohup\b', r'\bdaemon\b', r'\bsystemctl\s+enable\b',
    ],
    "MEMORY_WRITE": [
        r'\bmemory\b', r'\bremember\b', r'\bstore.*context\b',
        r'\blong.term\b', r'\bpersist.*state\b',
        r'\blocalStorage\b', r'\bIndexedDB\b', r'\bsessionStorage\b',
    ],
    "MULTI_STEP": [
        r'\bmulti.step\b', r'\bworkflow\b', r'\bpipeline\b', r'\bchain\b',
        r'\borchestrat\b', r'\bsequential\b', r'\bcoordinat\b',
    ],
    "CRED_STORE": [
        r'\bos\.environ\b', r'\bkeychain\b', r'\bkeyring\b',
        r'\bsecret[s]?\s*store\b', r'\bvault\b',
        r'\bprocess\.env\b', r'\.env\b', r'\bdotenv\b',
        r'\bexport\s+[A-Z_]+=',
    ],
    "BROWSER_CONTROL": [
        r'\bselenium\b', r'\bplaywright\b', r'\bpuppeteer\b',
        r'\bwebdriver\b', r'\bchromium\b', r'\bheadless\b',
        r'\bwebdriverio\b', r'\bpage\.click\b', r'\bbrowser\.launch\b',
    ],
    "CRYPTO_OPS": [
        r'\bcryptography\b', r'\bcrypto\b', r'\bhashlib\b',
        r'\brsa\b.*encrypt', r'\baes\b', r'\bpycryptodome\b',
        r'\bssl\b', r'\btls\b', r'\bhmac\b',
        r'\bcrypto\.createHash\b', r'\bcrypto\.createCipher\b',
        r"require\(['\"]crypto['\"]",
    ],
}

# ────────────────────────────────────────────────────────────────────────────
# C_visible patterns (description text = what user reads before installing)
# ────────────────────────────────────────────────────────────────────────────
DESC_REGEX: Dict[str, List[str]] = {
    "SHELL_EXEC": [
        r'\b(run|execute|perform)\s+(shell|bash|command|script|terminal)\b',
        r'\bshell\s+(access|command|execution)\b',
        r'\b(command.line|terminal)\s+(tool|interface|access)\b',
        r'\bexecute\s+(code|script|command)\b',
        r'\brun\s+.*(code|script|command|shell)\b',
        r'\bterminal\b', r'\bshell\s+script\b',
    ],
    "CRED_READ": [
        r'\b(api.?key|api\s+key)\b', r'\bcredential\b',
        r'\b(password|passphrase)\b', r'\bsecret\s*(key|token)?\b',
        r'\baccess.token\b', r'\bbearer.token\b', r'\boauth\b',
        r'\bprivate.key\b', r'\bauthentication\b', r'\bauth.token\b',
    ],
    "NET_WRITE": [
        r'\b(send|post|upload|submit|transmit)\s+(data|file|request|email|message)\b',
        r'\bwebhook\b', r'\bnotif(y|ication)\b',
        r'\bsend\s+(email|message|notification)\b',
        r'\b(http|api)\s+(post|request)\b',
        r'\bpush\s+(data|notification)\b',
    ],
    "NET_READ": [
        r'\b(fetch|retrieve|download|pull)\s+(data|file|content|information)\b',
        r'\bhttp\s+(get|request)\b', r'\bweb\s+scraping\b',
        r'\b(browse|search)\s+the\s+web\b',
        r'\bapi\s+(call|request|integration)\b',
        r'\bfetch\s+(from|data)\b',
    ],
    "FILE_READ": [
        r'\b(read|open|load|parse|access)\s+(file|document|csv|pdf|data)\b',
        r'\bfile\s+(reading|reader|access)\b',
        r'\bparse\s+(file|document|text)\b',
        r'\b(analyze|process)\s+(file|document|data)\b',
    ],
    "FILE_WRITE": [
        r'\b(write|save|create|generate|export)\s+(file|document|report|csv|pdf)\b',
        r'\bfile\s+(writing|writer|creation|generation)\b',
        r'\bsave\s+(to\s+)?(\w+\s+)?(file|disk|storage)\b',
        r'\bexport\s+(to|as)\s+(file|csv|pdf|json)\b',
    ],
    "FILE_DELETE": [
        r'\b(delete|remove|erase|clean)\s+(file|folder|directory|data)\b',
        r'\bfile\s+(deletion|cleanup)\b',
        r'\bclean\s+up\s+(file|folder|disk)\b',
    ],
    "AUTONOMOUS": [
        r'\bautomati(c|cally|on)\b', r'\bautonomous(ly)?\b',
        r'\bbackground\s+(task|job|process|run)\b',
        r'\bscheduled?\s+(task|job|run)\b',
        r'\bwithout\s+(user\s+)?intervention\b',
        r'\bproactive(ly)?\b', r'\band\s+schedule\b',
        r'\bsilently\b', r'\bno.*confirmation\b',
        r'\b(always|continuously)\s+(monitor|run|check)\b',
    ],
    "MEMORY_WRITE": [
        r'\b(remember|recall|retain|store)\s+(context|conversation|history|preference)\b',
        r'\blong.term\s+(memory|context|storage)\b',
        r'\bpersistent\s+(memory|context|storage|state)\b',
        r'\bcross.session\b', r'\bconversation\s+history\b',
    ],
    "MULTI_STEP": [
        r'\bmulti.step\b', r'\bworkflow\b', r'\bpipeline\b',
        r'\bchain\s+(of\s+)?(task|action|step)\b',
        r'\borchestr(ate|ation)\b',
        r'\b(sequence|series)\s+of\s+(task|action|step)\b',
    ],
    "CRED_STORE": [
        r'\b(store|save|remember|manage)\s+(credential|password|api.?key|secret)\b',
        r'\bpassword\s+(manager|vault|storage)\b',
        r'\bcredential\s+(storage|management|vault)\b',
        r'\bsecure\s+(storage|vault)\b',
    ],
    "BROWSER_CONTROL": [
        r'\bbrowser\s+(automation|control|driver)\b',
        r'\bweb\s+automation\b', r'\bheadless\s+browser\b',
        r'\b(click|navigate|scrape)\s+(web|browser|page)\b',
        r'\bselenium\b', r'\bplaywright\b', r'\bpuppeteer\b',
    ],
    "CRYPTO_OPS": [
        r'\b(encrypt|decrypt|encryption|decryption)\b',
        r'\bhashing\b', r'\bcryptograph\b',
        r'\b(digital\s+)?signature\b', r'\bssl\s+certificate\b',
        r'\bsecure\s+(communication|channel|connection)\b',
    ],
    "CRED_TRANSMIT": [
        r'\b(forward|relay|send)\s+(credential|password|key|token)\b',
        r'\bdata\s+(exfiltration|leakage)\b',  # attacker-inserted
    ],
}

# Negation patterns for description (deny scanning)
DESC_NEGATION = [
    r'\bread.only\b', r'\bview.only\b', r'\bno\s+(file|internet|network|shell)',
    r'\bsandbox(ed)?\b', r'\bcannot\s+(access|execute|run|write|delete)\b',
    r'\bdoes\s+not\s+(access|execute|run|store|send)',
    r'\bwithout\s+(storing|saving|sending|accessing)\b',
]

# Package → capability inference
PACKAGE_CAPS: Dict[str, Set[str]] = {
    "selenium": {"BROWSER_CONTROL"}, "playwright": {"BROWSER_CONTROL"},
    "puppeteer": {"BROWSER_CONTROL"}, "pyautogui": {"BROWSER_CONTROL", "AUTONOMOUS"},
    "webdriverio": {"BROWSER_CONTROL"},
    "paramiko": {"SHELL_EXEC", "NET_WRITE"}, "fabric": {"SHELL_EXEC", "NET_WRITE"},
    "cryptography": {"CRYPTO_OPS"}, "pycryptodome": {"CRYPTO_OPS"},
    "keyring": {"CRED_STORE"}, "keytar": {"CRED_STORE"},
    "python-dotenv": {"CRED_READ"}, "dotenv": {"CRED_READ"},
    "boto3": {"NET_READ", "NET_WRITE", "FILE_WRITE"},
    "aws-sdk": {"NET_READ", "NET_WRITE", "FILE_WRITE"},
    "google-cloud": {"NET_READ", "NET_WRITE"},
    "googleapis": {"NET_READ", "NET_WRITE"},
    "twilio": {"NET_WRITE"}, "sendgrid": {"NET_WRITE"},
    "nodemailer": {"NET_WRITE"}, "smtplib": {"NET_WRITE"},
    "axios": {"NET_READ", "NET_WRITE"}, "node-fetch": {"NET_READ"},
    "schedule": {"AUTONOMOUS"}, "apscheduler": {"AUTONOMOUS"},
    "node-cron": {"AUTONOMOUS"}, "node-schedule": {"AUTONOMOUS"},
    "celery": {"AUTONOMOUS", "MULTI_STEP"},
    "sqlalchemy": {"FILE_WRITE", "FILE_READ"}, "pymongo": {"FILE_WRITE", "FILE_READ"},
    "redis": {"MEMORY_WRITE"}, "pymysql": {"FILE_WRITE", "FILE_READ"},
}


# ════════════════════════════════════════════════════════════════════════════
# Extraction Functions
# ════════════════════════════════════════════════════════════════════════════

def scan_code(text: str) -> Set[str]:
    """Apply CODE_REGEX patterns. Returns set of matched families."""
    found = set()
    for fam, patterns in CODE_REGEX.items():
        for pat in patterns:
            if re.search(pat, text, re.IGNORECASE):
                found.add(fam)
                break
    return found


def scan_desc(description: str) -> Set[str]:
    """
    Extract C_visible from marketplace description text.
    Uses DESC_REGEX patterns + negation filtering.
    """
    if not description or len(description.strip()) < 5:
        return set()

    desc_norm = description.lower()

    # Check for global negations (read-only, sandboxed, etc.)
    global_negated = any(re.search(p, desc_norm) for p in DESC_NEGATION)

    found = set()
    for fam, patterns in DESC_REGEX.items():
        for pat in patterns:
            if re.search(pat, desc_norm, re.IGNORECASE):
                # Skip SHELL_EXEC/FILE_DELETE if globally negated
                if global_negated and fam in ("SHELL_EXEC", "FILE_DELETE", "NET_WRITE"):
                    break
                found.add(fam)
                break
    return found


def scan_yaml_fields(text: str) -> Set[str]:
    """Extract C_visible from YAML frontmatter capability declarations."""
    found = set()
    try:
        m = re.search(r'^---\n([\s\S]+?)\n---', text, re.MULTILINE)
        if not m:
            return found
        data = yaml.safe_load(m.group(1)) or {}
        if data.get("autonomous") is True:
            found.add("AUTONOMOUS")
        for key in ("permissions", "capabilities", "scopes"):
            for item in (data.get(key) or []):
                s = str(item).lower()
                if any(k in s for k in ("shell", "exec")):    found.add("SHELL_EXEC")
                if any(k in s for k in ("net", "http", "api")): found.add("NET_WRITE")
                if any(k in s for k in ("file", "disk")):     found.add("FILE_WRITE")
                if any(k in s for k in ("cred", "secret")):   found.add("CRED_READ")
    except Exception:
        pass
    return found


def infer_from_packages(content: str, kind: str) -> Set[str]:
    """Infer capabilities from requirements.txt or package.json."""
    found = set()
    if kind == "requirements":
        for line in content.splitlines():
            pkg = re.sub(r'[><=!~\[#\s].*', '', line).strip().lower()
            if pkg in PACKAGE_CAPS:
                found.update(PACKAGE_CAPS[pkg])
    elif kind == "package_json":
        try:
            data = json.loads(content)
            all_deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
            for pkg in all_deps:
                if pkg.lower() in PACKAGE_CAPS:
                    found.update(PACKAGE_CAPS[pkg.lower()])
        except Exception:
            pass
    return found


def infer_from_meta(content: str) -> Set[str]:
    """Extract from _meta.json / skill.json / clawhub.json / claw.json."""
    found = set()
    try:
        data = json.loads(content)
        if data.get("autonomous") is True:
            found.add("AUTONOMOUS")
        for key in ("permissions", "capabilities", "scopes", "requires"):
            for item in (data.get(key) or []):
                s = str(item).lower()
                if any(k in s for k in ("shell", "exec", "command")): found.add("SHELL_EXEC")
                if any(k in s for k in ("file", "disk", "storage")):  found.add("FILE_WRITE")
                if any(k in s for k in ("net", "http", "internet")):  found.add("NET_WRITE")
                if any(k in s for k in ("cred", "secret", "token")):  found.add("CRED_READ")
    except Exception:
        pass
    return found


def read_file(text_path: str) -> Optional[str]:
    """Read a file from its text_path."""
    try:
        return (DATA_ROOT / text_path).read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return None


# ════════════════════════════════════════════════════════════════════════════
# Per-Skill Full Analysis
# ════════════════════════════════════════════════════════════════════════════

def analyze_skill(skill_id: str, description: str, file_rows: List[Tuple]) -> dict:
    """
    Complete analysis of one skill.
    file_rows: list of (path, text_path) from skill_files table.
    """
    # ── C_visible: what user sees ─────────────────────────────────────────
    c_visible: Set[str] = set()

    # 1. Description text
    c_visible.update(scan_desc(description))

    # 2. SKILL.md YAML frontmatter (structured declarations)
    # (handled below when we read SKILL.md)

    # ── C_actual: what the skill really does ──────────────────────────────
    c_actual: Set[str] = set()
    sources: Dict[str, Set[str]] = defaultdict(set)  # source → caps found

    files_read = 0
    files_missing = 0

    for path, text_path in file_rows:
        if not text_path:
            continue
        content = read_file(text_path)
        if content is None:
            files_missing += 1
            continue
        if len(content.strip()) < 5:
            continue

        files_read += 1
        p = path.lower()

        # ── SKILL.md / skill.md ───────────────────────────────────────────
        if p in ("skill.md", "skill.md"):
            caps = scan_code(content)
            c_actual.update(caps)
            sources["SKILL.md"].update(caps)
            # YAML frontmatter → C_visible supplement
            yaml_caps = scan_yaml_fields(content)
            c_visible.update(yaml_caps)
            sources["SKILL.md(yaml→vis)"].update(yaml_caps)

        # ── README.md ─────────────────────────────────────────────────────
        elif p in ("readme.md", "readme.txt", "readme"):
            caps = scan_code(content)
            c_actual.update(caps)
            sources["README.md"].update(caps)

        # ── Python files ──────────────────────────────────────────────────
        elif p.endswith(".py"):
            caps = scan_code(content)
            c_actual.update(caps)
            sources["Python"].update(caps)

        # ── JavaScript / TypeScript ───────────────────────────────────────
        elif p.endswith((".js", ".ts", ".mjs", ".cjs")):
            caps = scan_code(content)
            c_actual.update(caps)
            sources["JS/TS"].update(caps)

        # ── Shell scripts ─────────────────────────────────────────────────
        elif p.endswith((".sh", ".bash")) or p in (
            "install.sh", "setup.sh", "entrypoint.sh", "scripts/setup.sh"
        ):
            caps = scan_code(content)
            c_actual.update(caps)
            sources["Shell"].update(caps)

        # ── Agent YAML / skill.yaml / skill.json ──────────────────────────
        elif p.endswith((".yaml", ".yml")):
            caps = scan_code(content)
            c_actual.update(caps)
            sources["YAML"].update(caps)

        # ── requirements.txt ──────────────────────────────────────────────
        elif "requirements" in p and p.endswith(".txt"):
            caps = infer_from_packages(content, "requirements")
            c_actual.update(caps)
            sources["requirements.txt"].update(caps)

        # ── package.json ──────────────────────────────────────────────────
        elif p == "package.json":
            caps = infer_from_packages(content, "package_json")
            c_actual.update(caps)
            sources["package.json"].update(caps)

        # ── _meta.json / skill.json / clawhub.json / claw.json / config ──
        elif p in ("_meta.json", "skill.json", "manifest.json",
                   "clawhub.json", "claw.json", "config.json"):
            caps = infer_from_meta(content)
            c_actual.update(caps)
            sources["meta_json"].update(caps)

        # ── Reference docs (api.md, examples.md, etc.) ───────────────────
        elif "reference" in p or p in ("references/api.md",
              "references/api-reference.md", "references/examples.md"):
            caps = scan_code(content)
            c_actual.update(caps)
            sources["refs"].update(caps)

        # ── setup.md, SECURITY.md, HEARTBEAT.md ───────────────────────────
        elif p.endswith(".md"):
            caps = scan_code(content)
            c_actual.update(caps)
            sources["other_md"].update(caps)

    # ── Build IC Difference Matrix ─────────────────────────────────────────
    AD  = c_actual & c_visible
    UD  = c_actual - c_visible       # hidden capabilities
    FC  = c_visible - c_actual       # false claims
    TN  = ALL_FAMS - c_actual - c_visible

    n_AD, n_UD, n_FC = len(AD), len(UD), len(FC)
    total_relevant = n_AD + n_UD + n_FC

    # CR: of actual caps, what fraction did the description disclose?
    CR = n_AD / (n_AD + n_UD) if (n_AD + n_UD) > 0 else None
    # CP: of claimed caps, what fraction are real?
    CP = n_AD / (n_AD + n_FC) if (n_AD + n_FC) > 0 else None
    # CDI: overall discrepancy
    CDI = (n_UD + n_FC) / total_relevant if total_relevant > 0 else 0.0

    # IC type
    if n_UD == 0 and n_FC == 0 and n_AD > 0:
        ic_type = "IC-0"   # perfectly accurate
    elif n_UD == 0 and n_FC == 0 and n_AD == 0:
        ic_type = "TN"     # no capabilities at all
    elif n_UD > 0 and n_AD == 0 and n_FC == 0:
        ic_type = "IC-1"   # full omission (CR=0)
    elif n_UD > 0 and n_AD > 0 and n_FC == 0:
        ic_type = "IC-2"   # partial omission (0<CR<1)
    elif n_FC > 0 and n_UD == 0:
        ic_type = "IC-3"   # false claim only
    else:
        ic_type = "IC-4"   # mixed (both UD and FC)

    return {
        "skill_id":    skill_id,
        "description": description[:200],
        # C_visible
        "c_visible":   sorted(c_visible),
        "n_visible":   len(c_visible),
        # C_actual (full multi-file)
        "c_actual":    sorted(c_actual),
        "n_actual":    len(c_actual),
        # DM cells
        "AD": sorted(AD),  "UD": sorted(UD),
        "FC": sorted(FC),  "TN": sorted(TN),
        # Metrics
        "CR":  round(CR, 4) if CR is not None else None,
        "CP":  round(CP, 4) if CP is not None else None,
        "CDI": round(CDI, 4),
        # Classification
        "ic_type": ic_type,
        # File coverage
        "files_read":    files_read,
        "files_missing": files_missing,
        "sources":       {k: sorted(v) for k, v in sources.items() if v},
    }


# ════════════════════════════════════════════════════════════════════════════
# Main
# ════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0, help="0=all 17K")
    args = parser.parse_args()

    OUTDIR.mkdir(parents=True, exist_ok=True)
    log.info(f"DB:     {DB}")
    log.info(f"ROOT:   {DATA_ROOT}")
    log.info(f"Output: {OUTPUT}")

    conn = get_conn()

    # Load all skills with ALL their files in one pass
    log.info("Loading all skills + file lists from DB...")
    skill_q = """
        SELECT skill_id, name, description FROM skills
        WHERE description IS NOT NULL AND length(description) > 5
    """
    if args.limit:
        skill_q += f" LIMIT {args.limit}"
    skill_rows = conn.execute(skill_q).fetchall()
    log.info(f"  {len(skill_rows)} skills to process")

    # Load all file records (one query, then group by skill_id)
    log.info("Loading all file records...")
    file_rows_all = conn.execute(
        "SELECT skill_id, path, text_path FROM skill_files WHERE text_path IS NOT NULL"
    ).fetchall()
    conn.close()

    # Group by skill_id
    files_by_skill: Dict[str, List[Tuple]] = defaultdict(list)
    for skill_id, path, text_path in file_rows_all:
        files_by_skill[skill_id].append((path, text_path))
    log.info(f"  {len(file_rows_all):,} file records loaded")

    # ── Process ──────────────────────────────────────────────────────────
    ic_counter   = Counter()
    cr_vals, cp_vals, cdi_vals = [], [], []
    total_files_read = 0
    total_files_missing = 0

    with open(OUTPUT, "w") as out:
        for i, (skill_id, name, description) in enumerate(skill_rows):
            if i % 1000 == 0:
                log.info(f"  [{i}/{len(skill_rows)}] {skill_id[:45]}")

            result = analyze_skill(
                skill_id,
                description or "",
                files_by_skill.get(skill_id, []),
            )
            out.write(json.dumps(result, ensure_ascii=False) + "\n")

            ic_counter[result["ic_type"]] += 1
            if result["CR"] is not None: cr_vals.append(result["CR"])
            if result["CP"] is not None: cp_vals.append(result["CP"])
            cdi_vals.append(result["CDI"])
            total_files_read    += result["files_read"]
            total_files_missing += result["files_missing"]

    # ── Report ────────────────────────────────────────────────────────────
    total = len(skill_rows)
    report = {
        "total_skills": total,
        "ic_distribution": {k: {"n": v, "pct": round(v/total*100, 2)}
                            for k, v in ic_counter.most_common()},
        "metrics": {
            "mean_CR":  round(sum(cr_vals)/len(cr_vals), 4) if cr_vals  else None,
            "mean_CP":  round(sum(cp_vals)/len(cp_vals), 4) if cp_vals  else None,
            "mean_CDI": round(sum(cdi_vals)/len(cdi_vals), 4),
            "n_with_CR": len(cr_vals),
            "n_with_CP": len(cp_vals),
        },
        "file_coverage": {
            "total_files_read":    total_files_read,
            "total_files_missing": total_files_missing,
            "avg_files_per_skill": round(total_files_read / total, 1),
        },
    }
    with open(REPORT, "w") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    # ── Print summary ─────────────────────────────────────────────────────
    log.info(f"\n{'='*60}")
    log.info(f"DONE: {total:,} skills processed")
    log.info(f"\nIC Distribution:")
    for ic, data in report["ic_distribution"].items():
        log.info(f"  {ic:6s}: {data['n']:6,}  ({data['pct']:.1f}%)")
    log.info(f"\nMetrics:")
    log.info(f"  Mean CR  = {report['metrics']['mean_CR']}")
    log.info(f"  Mean CP  = {report['metrics']['mean_CP']}")
    log.info(f"  Mean CDI = {report['metrics']['mean_CDI']}")
    log.info(f"\nFile coverage:")
    log.info(f"  Files read:    {total_files_read:,}")
    log.info(f"  Files missing: {total_files_missing:,}")
    log.info(f"  Avg per skill: {report['file_coverage']['avg_files_per_skill']}")
    log.info(f"\nOutput: {OUTPUT}")


if __name__ == "__main__":
    main()
