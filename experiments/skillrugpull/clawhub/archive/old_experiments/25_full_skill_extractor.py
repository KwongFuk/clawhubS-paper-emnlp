#!/usr/bin/env python3
"""
25_full_skill_extractor.py — Full-Corpus Multi-File Capability Extractor
=========================================================================
Analyzes ALL 17,738 skills using extended LSCPS regex patterns covering:
  - SKILL.md (Python, JS, Shell code blocks + prose)
  - README.md
  - index.js / .ts files
  - install.sh / setup.sh
  - agents/*.yaml (system prompt analysis)
  - requirements.txt / package.json (dependency-based inference)
  - _meta.json / skill.json (structured permission fields)

14 capability families (12 original + BROWSER_CONTROL + CRYPTO_OPS)

Run:
  python3 25_full_skill_extractor.py              # all 17,738 skills
  python3 25_full_skill_extractor.py --limit 100  # test run
"""

import os, re, json, yaml, argparse, logging, sys
from pathlib import Path
from collections import defaultdict, Counter
from typing import Set, Dict, List, Optional

# Always use the unified config (17K full corpus)
sys.path.insert(0, str(Path(__file__).parent))
from config import load_all_skills, read_file, get_conn, RESULTS, DATA_ROOT, DB

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("ext25")

OUTDIR  = Path(os.environ.get("OUTDIR", str(RESULTS)))
OUTPUT  = OUTDIR / "full_skill_extraction.jsonl"
REPORT  = OUTDIR / "full_extraction_report.json"

# ==============================================================================
# EXTENDED LSCPS — Python + JavaScript/TypeScript + Shell + YAML
# 14 capability families
# ==============================================================================
LSCPS_FAMILIES = [
    "SHELL_EXEC", "CRED_READ", "CRED_TRANSMIT", "FILE_DELETE",
    "NET_WRITE", "AUTONOMOUS", "FILE_WRITE", "NET_READ",
    "FILE_READ", "MEMORY_WRITE", "MULTI_STEP", "CRED_STORE",
    "BROWSER_CONTROL", "CRYPTO_OPS",
]

LSCPS_REGEX = {
    "SHELL_EXEC": [
        # Python
        r'\bsubprocess\b', r'\bos\.system\b', r'\bos\.popen\b',
        r'\bexec\s*\(', r'\beval\s*\(', r'\bshell\s*=\s*True\b',
        r'\bshutil\.rmtree\b', r'\bchmod\b', r'\b/bin/sh\b', r'\bbash\b',
        # Shell
        r'\brm\s+-rf\b', r'\bsudo\b', r'\bapt-get\b', r'\byum\s+install\b',
        r'\bchown\b', r'\bkill\b', r'\bpkill\b',
        r'\bcurl\b.*\|\s*bash\b', r'\bwget\b.*\|\s*sh\b',
        r'#!/bin/bash', r'#!/bin/sh',
        # JS / Node.js
        r'\bchild_process\b', r'\bexecSync\b', r'\bspawnSync\b',
        r'\bexecFile\b', r'\bspawn\s*\(', r"require\(['\"]child_process",
    ],
    "CRED_READ": [
        # Python / general
        r'\bapi[_\s]?key\b', r'\bpassword\b', r'\bprivate[_\s]?key\b',
        r'\bsecret\b', r'\baccess[_\s]?token\b', r'\bbearer\b',
        r'\boauth\b', r'\.env\b', r'\bcredential\b',
        # JS / TS
        r'\bprocess\.env\b', r'\bdotenv\b', r"require\(['\"]dotenv",
        r'\bgetenv\b', r'os\.environ\[',
        # Shell (env vars like $API_KEY, $SECRET)
        r'\$[A-Z_]{3,}\b',
        r'\bsource\b.*\.env', r'\bcat\b.*secrets',
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

# Package → capability mapping for requirements.txt / package.json
PACKAGE_CAPS = {
    "selenium": {"BROWSER_CONTROL"}, "playwright": {"BROWSER_CONTROL"},
    "pyautogui": {"BROWSER_CONTROL", "AUTONOMOUS"}, "puppeteer": {"BROWSER_CONTROL"},
    "paramiko": {"SHELL_EXEC", "NET_WRITE"}, "fabric": {"SHELL_EXEC", "NET_WRITE"},
    "cryptography": {"CRYPTO_OPS"}, "pycryptodome": {"CRYPTO_OPS"},
    "keyring": {"CRED_STORE"}, "python-dotenv": {"CRED_READ"}, "dotenv": {"CRED_READ"},
    "boto3": {"NET_READ", "NET_WRITE", "FILE_WRITE"},
    "aws-sdk": {"NET_READ", "NET_WRITE", "FILE_WRITE"},
    "google-cloud": {"NET_READ", "NET_WRITE"},
    "googleapis": {"NET_READ", "NET_WRITE"},
    "twilio": {"NET_WRITE"}, "sendgrid": {"NET_WRITE"},
    "nodemailer": {"NET_WRITE"}, "axios": {"NET_READ", "NET_WRITE"},
    "schedule": {"AUTONOMOUS"}, "apscheduler": {"AUTONOMOUS"},
    "node-cron": {"AUTONOMOUS"}, "node-schedule": {"AUTONOMOUS"},
    "celery": {"AUTONOMOUS", "MULTI_STEP"},
    "sqlalchemy": {"FILE_WRITE", "FILE_READ"}, "pymongo": {"FILE_WRITE", "FILE_READ"},
    "redis": {"MEMORY_WRITE"}, "keytar": {"CRED_STORE"},
}


def scan_text(text: str, src: str = "") -> Dict[str, str]:
    """Apply LSCPS regex. Returns {family: first_matching_pattern}."""
    hits = {}
    for family, patterns in LSCPS_REGEX.items():
        for pat in patterns:
            if re.search(pat, text, re.IGNORECASE):
                hits[family] = f"{src}:{pat[:25]}"
                break
    return hits


def from_packages(content: str, kind: str) -> Dict[str, str]:
    """Infer caps from requirements.txt or package.json."""
    hits = {}
    if kind == "requirements":
        for line in content.splitlines():
            pkg = re.sub(r'[><=!~\[#].*', '', line).strip().lower()
            if pkg in PACKAGE_CAPS:
                for cap in PACKAGE_CAPS[pkg]:
                    hits.setdefault(cap, f"pkg:{pkg}")
    elif kind == "package_json":
        try:
            data = json.loads(content)
            all_deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
            for pkg in all_deps:
                if pkg.lower() in PACKAGE_CAPS:
                    for cap in PACKAGE_CAPS[pkg.lower()]:
                        hits.setdefault(cap, f"pkg:{pkg.lower()}")
        except Exception:
            pass
    return hits


def from_meta_json(content: str) -> Dict[str, str]:
    """Extract caps from structured _meta.json / skill.json."""
    hits = {}
    try:
        data = json.loads(content)
        for key in ("permissions", "capabilities", "scopes", "requires"):
            for item in (data.get(key) or []):
                s = str(item).lower()
                if any(k in s for k in ("shell", "exec", "command")): hits.setdefault("SHELL_EXEC", f"meta:{item}")
                if any(k in s for k in ("file", "disk", "storage")):   hits.setdefault("FILE_WRITE", f"meta:{item}")
                if any(k in s for k in ("net", "http", "api")):        hits.setdefault("NET_WRITE",  f"meta:{item}")
                if any(k in s for k in ("cred", "secret", "token")):   hits.setdefault("CRED_READ",  f"meta:{item}")
        if data.get("autonomous") is True:
            hits["AUTONOMOUS"] = "meta:autonomous=true"
    except Exception:
        pass
    return hits


def extract_skill(skill: dict) -> dict:
    """Run full multi-file extraction for one skill."""
    source_caps: Dict[str, Dict[str, str]] = defaultdict(dict)

    # ── SKILL.md (always available) ──────────────────────────────────────
    if skill.get("skill_text"):
        source_caps["SKILL.md"].update(scan_text(skill["skill_text"], "SKILL.md"))

    # ── All other files from DB ──────────────────────────────────────────
    for path, text_path in skill.get("file_paths", []):
        try:
            content = (DATA_ROOT / text_path).read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        if not content or len(content.strip()) < 5:
            continue

        p = path.lower()

        if p in ("readme.md", "readme.txt"):
            source_caps["README.md"].update(scan_text(content, "README"))
        elif p.endswith((".js", ".ts", ".mjs", ".cjs")):
            source_caps["JS/TS"].update(scan_text(content, f"js:{path}"))
        elif p.endswith((".sh", ".bash")) or p in ("install.sh", "setup.sh", "entrypoint.sh"):
            source_caps["Shell"].update(scan_text(content, f"sh:{path}"))
        elif p.endswith((".yaml", ".yml")) and "agent" in p:
            source_caps["YAML"].update(scan_text(content, "yaml"))
            try:
                d = yaml.safe_load(content)
                if isinstance(d, dict):
                    sp = str(d.get("system_prompt", d.get("instructions", "")))
                    source_caps["YAML"].update(scan_text(sp, "yaml_prompt"))
            except Exception:
                pass
        elif p in ("requirements.txt", "requirements-dev.txt"):
            source_caps["requirements.txt"].update(from_packages(content, "requirements"))
        elif p == "package.json":
            source_caps["package.json"].update(from_packages(content, "package_json"))
        elif p in ("_meta.json", "skill.json", "manifest.json"):
            source_caps["meta_json"].update(from_meta_json(content))
        elif p.endswith(".py"):
            source_caps["Python"].update(scan_text(content, f"py:{path}"))

    # ── Aggregate ────────────────────────────────────────────────────────
    c_actual_skill_md = set(source_caps["SKILL.md"].keys())
    c_actual_full     = set()
    source_breakdown  = {}
    for src, caps in source_caps.items():
        if caps:
            source_breakdown[src] = sorted(caps.keys())
            c_actual_full.update(caps.keys())
    new_caps = c_actual_full - c_actual_skill_md

    return {
        "skill_id":          skill["skill_id"],
        "description":        skill["description"],
        "c_actual_skill_md":  sorted(c_actual_skill_md),
        "c_actual_full":      sorted(c_actual_full),
        "new_caps_found":     sorted(new_caps),
        "source_breakdown":   source_breakdown,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0, help="0 = all 17K skills")
    args = parser.parse_args()

    OUTDIR.mkdir(parents=True, exist_ok=True)
    log.info(f"Output: {OUTPUT}")
    log.info(f"Loading full 17K corpus from DB...")

    skills = load_all_skills(limit=args.limit, load_skill_md=True, load_all_files=True)
    log.info(f"Processing {len(skills)} skills")

    new_cap_ctr   = Counter()
    source_ctr    = Counter()
    skills_w_new  = 0

    with open(OUTPUT, "w") as f:
        for i, skill in enumerate(skills):
            if i % 1000 == 0:
                log.info(f"  [{i}/{len(skills)}] {skill['skill_id'][:40]}")
            result = extract_skill(skill)
            f.write(json.dumps(result, ensure_ascii=False) + "\n")
            if result["new_caps_found"]:
                skills_w_new += 1
                for c in result["new_caps_found"]: new_cap_ctr[c] += 1
            for s in result["source_breakdown"]: source_ctr[s] += 1

    total = len(skills)
    report = {
        "total_skills": total,
        "skills_with_new_caps": skills_w_new,
        "skills_with_new_caps_pct": round(skills_w_new/total*100, 1),
        "new_caps_by_family": {k: {"n": v, "pct": round(v/total*100,1)}
                               for k, v in new_cap_ctr.most_common()},
        "files_by_source": dict(source_ctr.most_common()),
    }
    with open(REPORT, "w") as f:
        json.dump(report, f, indent=2)

    log.info(f"\n{'='*55}")
    log.info(f"DONE: {total} skills")
    log.info(f"Skills with NEW caps beyond SKILL.md: {skills_w_new} ({skills_w_new/total*100:.1f}%)")
    log.info("New caps by family:")
    for fam, n in new_cap_ctr.most_common(8):
        log.info(f"  {fam:20s}: {n} ({n/total*100:.1f}%)")


if __name__ == "__main__":
    main()
