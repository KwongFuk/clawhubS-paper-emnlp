#!/usr/bin/env python3
"""
28_skillssh_main_experiment.py — TrustDrift skills.sh Cross-Platform Validation
================================================================================
Applies the SAME IC framework + LSCPS (14 families, 300+ patterns) used in
Script 27 (main ClawHub experiment) to the skills.sh corpus.

Purpose:
  - Cross-platform validation of IC prevalence findings
  - Report IC-0 through IC-4 + TN distribution on skills.sh
  - Compare mean CR / CDI with ClawHub baseline

DB expected at: ~/data/skills_sh/skills_sh.sqlite3
  (or set SKILLSSH_DB env var)

Run:
    python3 28_skillssh_main_experiment.py              # full corpus
    python3 28_skillssh_main_experiment.py --limit 500  # quick test
"""

import os, re, json, sqlite3, argparse, logging, sys
from pathlib import Path
from collections import Counter
from typing import Dict, List, Set, Optional, Tuple

logging.basicConfig(level=logging.INFO,
                    format="[%(asctime)s] %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("skillssh_exp")

HERE     = Path(__file__).parent
ROOT     = HERE.parent.parent.parent
RESULTS  = HERE / "results"
RESULTS.mkdir(exist_ok=True)

OUTPUT  = RESULTS / "skillssh_main_experiment_results.jsonl"
REPORT  = RESULTS / "skillssh_main_experiment_report.json"

# ── Database path ────────────────────────────────────────────────────────────
DEFAULT_DB = ROOT / "data" / "skills_sh" / "skills_sh.sqlite3"
DB_PATH = Path(os.environ.get("SKILLSSH_DB", str(DEFAULT_DB)))

# ════════════════════════════════════════════════════════════════════════════
# Reuse the EXACT same 14 families + patterns as Script 27
# ════════════════════════════════════════════════════════════════════════════
FAMILIES = [
    "SHELL_EXEC", "CRED_READ", "CRED_TRANSMIT", "FILE_DELETE",
    "NET_WRITE",  "AUTONOMOUS", "FILE_WRITE",   "NET_READ",
    "FILE_READ",  "MEMORY_WRITE", "MULTI_STEP", "CRED_STORE",
    "BROWSER_CONTROL", "CRYPTO_OPS",
]
ALL_FAMS = set(FAMILIES)

# Import shared patterns from Script 27's directory
sys.path.insert(0, str(HERE))
try:
    from _27_patterns import CODE_REGEX, DESC_REGEX, NEGATION_PATTERNS
    log.info("Loaded patterns from _27_patterns module")
except ImportError:
    # Inline minimal pattern set (aligned with Script 27)
    log.warning("_27_patterns not found; using inline patterns (subset of Script 27)")
    CODE_REGEX: Dict[str, List[str]] = {
        "SHELL_EXEC":       [r'\bsubprocess\b', r'\bos\.system\b', r'\beval\s*\(', r'\bshell\s*=\s*True\b',
                             r'#!/bin/bash', r'#!/bin/sh', r'\bchild_process\b', r'\bexecSync\b', r'\bspawn\s*\('],
        "CRED_READ":        [r'\bapi[_\s]?key\b', r'\bpassword\b', r'\bsecret\b', r'\baccess[_\s]?token\b',
                             r'\bbearer\b', r'\boauth\b', r'\.env\b', r'\bcredential\b',
                             r'\bprocess\.env\b', r'\bgetenv\b', r'os\.environ\['],
        "CRED_STORE":       [r'\bkeyring\b', r'\bkeychain\b', r'\bsecretmanager\b', r'\bcredential.*store\b',
                             r'\bvault\b', r'\bkeystore\b'],
        "CRED_TRANSMIT":    [r'\bexfil\b', r'\bwebhook\b.*\b(key|token|secret)\b', r'\bpost.*token\b',
                             r'axios\.post.*token', r'fetch.*Authorization.*Bearer'],
        "NET_WRITE":        [r'\brequests\.post\b', r'\brequests\.put\b', r'\bwebhook\b',
                             r'\baxios\.post\b', r'\bfetch\b.*\bPOST\b', r'\bnodemailer\b',
                             r'\bsmtp\b', r'\bsend.*email\b'],
        "NET_READ":         [r'\brequests\.get\b', r'\bhttpx\.get\b', r'\bwget\b',
                             r'\bfetch\s*\(', r'\bcurl\s+https?\b', r'\baxios\.get\b'],
        "FILE_DELETE":      [r'\bos\.remove\b', r'\bos\.unlink\b', r'\bshutil\.rmtree\b',
                             r'\brm\s+-rf\b', r'\bfs\.unlinkSync\b', r'\bfs\.rmSync\b'],
        "FILE_WRITE":       [r'\bopen\s*\(.*["\']w["\']', r'\bopen\s*\(.*["\']a["\']',
                             r'\bfs\.writeFile\b', r'\bfs\.appendFile\b', r'\bshutil\.copy\b'],
        "FILE_READ":        [r'\bopen\s*\(.*["\']r["\']', r'\bfs\.readFile\b', r'\bfs\.readFileSync\b',
                             r'\bread_csv\b', r'\bjson\.load\b'],
        "MEMORY_WRITE":     [r'\bshelve\b', r'\bpickle\b', r'\bjoblib\b', r'\bredis\b',
                             r'\bmemcache\b', r'\bglobalThis\b'],
        "MULTI_STEP":       [r'\bworkflow\b', r'\bpipeline\b', r'\borchestrat\b',
                             r'\bchain\b.*\bagent\b', r'\bfor\s+\w+\s+in\b.*\bstep\b'],
        "AUTONOMOUS":       [r'\bschedul\b', r'\bcron\b', r'\bbackground\b.*\btask\b',
                             r'\bdaemon\b', r'\bauto.*run\b', r'\bunattended\b'],
        "BROWSER_CONTROL":  [r'\bselenium\b', r'\bplaywright\b', r'\bpuppeteer\b',
                             r'\bwebdriver\b', r'\bheadless\b'],
        "CRYPTO_OPS":       [r'\bencrypt\b', r'\bdecrypt\b', r'\baes\b', r'\brsa\b',
                             r'\bhashlib\b', r'\bcryptography\b', r'\bhmac\b'],
    }
    DESC_REGEX: Dict[str, List[str]] = {
        "SHELL_EXEC":       [r'\b(run|execute|shell|terminal|command|bash|subprocess)\b'],
        "CRED_READ":        [r'\b(api.?key|credential|token|secret|oauth|password|env)\b'],
        "CRED_STORE":       [r'\b(store|save|vault|keychain|secure).*(key|credential|token)\b'],
        "CRED_TRANSMIT":    [r'\b(send|transmit|upload|export).*(key|credential|token)\b'],
        "NET_WRITE":        [r'\b(send|post|publish|webhook|email|notify|upload)\b'],
        "NET_READ":         [r'\b(fetch|download|request|retrieve|pull|api.call)\b'],
        "FILE_DELETE":      [r'\b(delete|remove|clean\s*up|purge|wipe)\b'],
        "FILE_WRITE":       [r'\b(write|save|create|generate|export|output).*(file|report|log)\b'],
        "FILE_READ":        [r'\b(read|load|parse|open|import).*(file|csv|json|data)\b'],
        "MEMORY_WRITE":     [r'\b(cache|memory|persist|store|database|redis)\b'],
        "MULTI_STEP":       [r'\b(workflow|pipeline|orchestrat|automat|multi.?step|chain)\b'],
        "AUTONOMOUS":       [r'\b(scheduled|background|daemon|automatic|unattended|autonomous)\b'],
        "BROWSER_CONTROL":  [r'\b(browser|selenium|playwright|puppeteer|headless|web.?scraping)\b'],
        "CRYPTO_OPS":       [r'\b(encrypt|decrypt|hash|sign|crypto|secure.?channel)\b'],
    }
    NEGATION_PATTERNS = [r'\bno\b', r'\bnot\b', r'\bwithout\b', r"\bdon't\b", r'\bavoid\b']

# Compile patterns
_CODE_COMPILED  = {f: [re.compile(p, re.I) for p in pats] for f, pats in CODE_REGEX.items()}
_DESC_COMPILED  = {f: [re.compile(p, re.I) for p in pats] for f, pats in DESC_REGEX.items()}
_NEG_COMPILED   = [re.compile(p, re.I) for p in NEGATION_PATTERNS]


def scan_code(text: str) -> Set[str]:
    found = set()
    for fam, patterns in _CODE_COMPILED.items():
        for pat in patterns:
            if pat.search(text):
                found.add(fam)
                break
    return found


def scan_desc(text: str) -> Set[str]:
    if not text or len(text.strip()) < 5:
        return set()
    found = set()
    for fam, patterns in _DESC_COMPILED.items():
        for pat in patterns:
            m = pat.search(text)
            if m:
                # Check for negation in surrounding context
                start = max(0, m.start() - 30)
                context = text[start:m.start()]
                if not any(neg.search(context) for neg in _NEG_COMPILED):
                    found.add(fam)
                    break
    return found


def classify_ic(c_actual: Set[str], c_visible: Set[str]):
    AD = c_actual & c_visible
    UD = c_actual - c_visible   # hidden
    FC = c_visible - c_actual   # false claim

    if not c_actual and not c_visible:
        ic = "TN"
    elif UD and not FC and not AD:
        ic = "IC-1"   # full omission
    elif UD and AD and not FC:
        ic = "IC-2"   # partial omission
    elif FC and not UD:
        ic = "IC-3"   # false claim
    elif UD and FC:
        ic = "IC-4"   # bidirectional
    elif AD and not UD and not FC:
        ic = "IC-0"   # accurate
    else:
        ic = "TN"

    CR = len(AD) / len(c_actual) if c_actual else None
    CP = len(AD) / len(c_visible) if c_visible else None
    CDI = 1 - CR if CR is not None else (1.0 if c_actual else 0.0)

    return {
        "ic_type": ic,
        "AD": sorted(AD), "UD": sorted(UD), "FC": sorted(FC),
        "CR": round(CR, 4) if CR is not None else None,
        "CP": round(CP, 4) if CP is not None else None,
        "CDI": round(CDI, 4),
    }


def get_skillssh_db_schema(conn):
    """Detect available columns in skills.sh DB."""
    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = [r[0] for r in cur.fetchall()]
    log.info(f"Tables in skills.sh DB: {tables}")
    if not tables:
        raise RuntimeError("Empty database!")
    # Try to find skill + text columns
    for table in tables:
        cur = conn.execute(f"PRAGMA table_info({table})")
        cols = [r[1] for r in cur.fetchall()]
        log.info(f"  {table}: {cols}")
    return tables


def load_skills_sh(conn, limit: Optional[int]) -> List[dict]:
    """
    Load skills from skills.sh DB.
    Tries to find skill_id, description, and skill body text.
    Falls back gracefully if schema differs.
    """
    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = [r[0] for r in cur.fetchall()]

    # Heuristic: find table with 'skill' or description-like columns
    for table in tables:
        cur = conn.execute(f"PRAGMA table_info({table})")
        cols = [r[1] for r in cur.fetchall()]
        col_set = set(cols)

        # Find ID column
        id_col = next((c for c in ['skill_id','id','path','name'] if c in col_set), cols[0])
        # Find description column
        desc_col = next((c for c in ['description','desc','summary','title'] if c in col_set), None)
        # Find body/text column
        body_col = next((c for c in ['skill_text','body','content','text','skill_body','readme'] if c in col_set), None)

        if not desc_col and not body_col:
            log.warning(f"Table {table} has no usable text columns, skipping")
            continue

        select_cols = [id_col]
        if desc_col:
            select_cols.append(desc_col)
        if body_col:
            select_cols.append(body_col)

        q = f"SELECT {', '.join(select_cols)} FROM {table}"
        if limit:
            q += f" LIMIT {limit}"

        log.info(f"Querying: {q[:80]}...")
        cur = conn.execute(q)
        rows = cur.fetchall()
        log.info(f"Loaded {len(rows)} skills from table '{table}'")

        skills = []
        for row in rows:
            skill = {"skill_id": str(row[0]), "description": "", "body": ""}
            idx = 1
            if desc_col:
                skill["description"] = str(row[idx] or "")
                idx += 1
            if body_col:
                skill["body"] = str(row[idx] or "")
            skills.append(skill)
        return skills

    raise RuntimeError(f"Could not find usable table in {tables}")


def analyze_skill(skill: dict) -> dict:
    description = skill.get("description", "")
    body        = skill.get("body", "")

    c_visible = scan_desc(description)
    c_actual  = scan_code(body)
    # Also scan description code blocks for C_actual
    c_actual |= scan_code(description)  # catches inline code patterns

    result = classify_ic(c_actual, c_visible)
    result.update({
        "skill_id":    skill["skill_id"],
        "description": description[:300],
        "c_visible":   sorted(c_visible),
        "c_actual":    sorted(c_actual),
        "n_visible":   len(c_visible),
        "n_actual":    len(c_actual),
    })
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None,
                        help="Limit number of skills (default: all)")
    parser.add_argument("--db", type=str, default=str(DB_PATH),
                        help="Path to skills.sh SQLite database")
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        log.error(f"Database not found: {db_path}")
        log.error("Please set SKILLSSH_DB env var or --db flag to the skills.sh sqlite path")
        sys.exit(1)

    log.info(f"Connecting to: {db_path} ({db_path.stat().st_size/1e6:.1f} MB)")
    conn = sqlite3.connect(str(db_path))
    get_skillssh_db_schema(conn)

    skills = load_skills_sh(conn, args.limit)
    conn.close()

    log.info(f"Analyzing {len(skills):,} skills with 14-family LSCPS...")

    results = []
    ic_counter = Counter()
    cr_vals, cdi_vals = [], []

    with open(OUTPUT, "w", encoding="utf-8") as fout:
        for i, skill in enumerate(skills):
            r = analyze_skill(skill)
            fout.write(json.dumps(r, ensure_ascii=False) + "\n")
            results.append(r)
            ic_counter[r["ic_type"]] += 1
            if r["CR"] is not None:
                cr_vals.append(r["CR"])
            cdi_vals.append(r["CDI"])
            if (i+1) % 10000 == 0:
                log.info(f"  {i+1:,}/{len(skills):,} done...")

    N = len(results)
    mean_cr  = sum(cr_vals)  / len(cr_vals)  if cr_vals  else 0
    mean_cdi = sum(cdi_vals) / len(cdi_vals) if cdi_vals else 0

    report = {
        "platform": "skills.sh",
        "N_total": N,
        "ic_distribution": dict(ic_counter),
        "ic_pct": {k: round(v/N*100, 2) for k, v in ic_counter.items()},
        "mean_CR":  round(mean_cr,  4),
        "mean_CDI": round(mean_cdi, 4),
        "inconsistency_total_pct": round(
            (ic_counter["IC-1"] + ic_counter["IC-2"] + ic_counter["IC-4"]) / N * 100, 2
        ),
        "note": (
            "Cross-platform validation using identical LSCPS 14-family pipeline as "
            "ClawHub Script 27. No T6 ground truth available for skills.sh "
            "(static snapshot only, no version history)."
        ),
    }

    with open(REPORT, "w") as f:
        json.dump(report, f, indent=2)

    # ── Print summary ────────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print(f"skills.sh Cross-Platform Validation — {N:,} skills")
    print(f"{'='*60}")
    for ic in ["IC-1","IC-2","IC-4","IC-3","IC-0","TN"]:
        n   = ic_counter.get(ic, 0)
        pct = n/N*100
        print(f"  {ic:6s}: {n:7,}  ({pct:5.1f}%)")
    print(f"  {'─'*35}")
    inconsist = ic_counter["IC-1"] + ic_counter["IC-2"] + ic_counter["IC-4"]
    print(f"  IC-1+2+4 total: {inconsist:,}  ({inconsist/N*100:.1f}%)")
    print(f"  mean CR  = {mean_cr:.4f}")
    print(f"  mean CDI = {mean_cdi:.4f}")
    print(f"\nOutput: {OUTPUT}")
    print(f"Report: {REPORT}")


if __name__ == "__main__":
    main()
