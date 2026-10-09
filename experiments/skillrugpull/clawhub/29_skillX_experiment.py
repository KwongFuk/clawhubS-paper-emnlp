#!/usr/bin/env python3
"""
29_skillX_experiment.py — TrustDrift SkillX Ablation
=====================================================
Ablation variant of Script 27:

  C_visible(s) = capabilities extracted from SKILL.md body
                 (what a careful user who reads the detail page would see)

  C_actual(s)  = capabilities extracted from ALL OTHER files
                 (*.py, *.js, *.ts, *.sh, requirements.txt, package.json, etc.)
                 EXCLUDING SKILL.md / skill.md

Compares with Script 27 (description-only C_visible) to answer:
  "If users DID read SKILL.md, how much capability is still hidden in code?"

Run:
    python3 29_skillX_experiment.py              # full 18K corpus
    python3 29_skillX_experiment.py --limit 500  # quick test
"""

import os, re, json, yaml, sqlite3, argparse, logging, sys
from pathlib import Path
from collections import Counter
from typing import Dict, List, Set, Optional, Tuple

logging.basicConfig(level=logging.INFO,
                    format="[%(asctime)s] %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("skillX")

HERE    = Path(__file__).parent
RESULTS = HERE / "results"
RESULTS.mkdir(exist_ok=True)

OUTPUT  = RESULTS / "skillX_experiment_results.jsonl"
REPORT  = RESULTS / "skillX_experiment_report.json"

sys.path.insert(0, str(HERE))
from config import get_conn

# ── Reuse exact same 14-family patterns as Script 27 ───────────────────────
FAMILIES = [
    "SHELL_EXEC", "CRED_READ", "CRED_TRANSMIT", "FILE_DELETE",
    "NET_WRITE",  "AUTONOMOUS", "FILE_WRITE",   "NET_READ",
    "FILE_READ",  "MEMORY_WRITE", "MULTI_STEP", "CRED_STORE",
    "BROWSER_CONTROL", "CRYPTO_OPS",
]

CODE_REGEX: Dict[str, List[str]] = {
    "SHELL_EXEC": [
        r'\bsubprocess\b', r'\bos\.system\b', r'\bos\.popen\b',
        r'\bexec\s*\(', r'\beval\s*\(', r'\bshell\s*=\s*True\b',
        r'\brm\s+-rf\b', r'\bsudo\b', r'#!/bin/bash', r'#!/bin/sh',
        r'\bchild_process\b', r'\bexecSync\b', r'\bspawn\s*\(',
    ],
    "CRED_READ": [
        r'\bapi[_\s]?key\b', r'\bpassword\b', r'\bprivate[_\s]?key\b',
        r'\bsecret\b', r'\baccess[_\s]?token\b', r'\bbearer\b',
        r'\boauth\b', r'\.env\b', r'\bcredential\b',
        r'\bprocess\.env\b', r'\bgetenv\b', r'os\.environ\[',
        r'\$[A-Z_]{3,}\b',
    ],
    "CRED_STORE": [
        r'\bkeyring\b', r'\bkeychain\b', r'\bsecretmanager\b',
        r'\bvault\b', r'\bkeystore\b',
    ],
    "CRED_TRANSMIT": [
        r'\bexfil\b', r'\bwebhook\b.*\b(key|token|secret)\b',
        r'axios\.post.*token', r'fetch.*Authorization.*Bearer',
    ],
    "NET_WRITE": [
        r'\brequests\.post\b', r'\brequests\.put\b', r'\bwebhook\b',
        r'\baxios\.post\b', r'\bfetch\b.*\bPOST\b',
        r'\bnodemailer\b', r'\bsmtp\b', r'\bsend.*email\b',
    ],
    "NET_READ": [
        r'\brequests\.get\b', r'\bhttpx\.get\b', r'\bwget\b',
        r'\bfetch\s*\(', r'\bcurl\s+https?\b', r'\baxios\.get\b',
    ],
    "FILE_DELETE": [
        r'\bos\.remove\b', r'\bos\.unlink\b', r'\bshutil\.rmtree\b',
        r'\brm\s+-rf\b', r'\bfs\.unlinkSync\b', r'\bfs\.rmSync\b',
    ],
    "FILE_WRITE": [
        r'\bopen\s*\(.*["\']w["\']', r'\bopen\s*\(.*["\']a["\']',
        r'\bfs\.writeFile\b', r'\bfs\.appendFile\b',
    ],
    "FILE_READ": [
        r'\bopen\s*\(.*["\']r["\']', r'\bfs\.readFile\b',
        r'\bread_csv\b', r'\bjson\.load\b',
    ],
    "MEMORY_WRITE": [
        r'\bshelve\b', r'\bpickle\b', r'\bredis\b', r'\bmemcache\b',
    ],
    "MULTI_STEP": [
        r'\bworkflow\b', r'\bpipeline\b', r'\borchestrat\b',
    ],
    "AUTONOMOUS": [
        r'\bschedul\b', r'\bcron\b', r'\bdaemon\b', r'\bauto.*run\b',
    ],
    "BROWSER_CONTROL": [
        r'\bselenium\b', r'\bplaywright\b', r'\bpuppeteer\b',
        r'\bwebdriver\b', r'\bheadless\b',
    ],
    "CRYPTO_OPS": [
        r'\bencrypt\b', r'\bdecrypt\b', r'\baes\b', r'\brsa\b',
        r'\bhashlib\b', r'\bcryptography\b',
    ],
}

_CODE_COMPILED = {f: [re.compile(p, re.I) for p in pats]
                  for f, pats in CODE_REGEX.items()}

NEGATION = [re.compile(p, re.I) for p in
            [r'\bno\b', r'\bnot\b', r'\bwithout\b', r"\bdon't\b",
             r'\bnever\b', r'\bavoid\b']]


def scan_text(text: str) -> Set[str]:
    """Scan any text for capability signals (used for both SKILL.md and code)."""
    if not text or len(text.strip()) < 3:
        return set()
    found = set()
    for fam, patterns in _CODE_COMPILED.items():
        for pat in patterns:
            if pat.search(text):
                found.add(fam)
                break
    return found


def scan_skillmd_visible(text: str) -> Set[str]:
    """
    Extract C_visible from SKILL.md body.
    Applies light negation filtering for explicit denial statements
    (e.g. "does NOT access credentials", "never sends network requests").
    """
    if not text or len(text.strip()) < 5:
        return set()
    found = set()
    for fam, patterns in _CODE_COMPILED.items():
        for pat in patterns:
            m = pat.search(text)
            if m:
                # Check surrounding context for negation
                start = max(0, m.start() - 40)
                context = text[start:m.start()]
                negated = any(neg.search(context) for neg in NEGATION)
                if not negated:
                    found.add(fam)
                    break
    return found


def is_skillmd(path: str) -> bool:
    """Returns True if this file is SKILL.md or skill.md."""
    fname = Path(path).name.lower()
    return fname in ("skill.md", "skills.md")


ROOT_DATA = Path(__file__).parent.parent.parent.parent  # clawhubS/

def read_file(text_path: str) -> Optional[str]:
    if not text_path:
        return None
    p = Path(text_path)
    if not p.is_absolute():
        p = ROOT_DATA / p
    if not p.exists():
        return None
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None


def classify_ic(c_actual: Set[str], c_visible: Set[str]) -> dict:
    AD = c_actual & c_visible
    UD = c_actual - c_visible
    FC = c_visible - c_actual

    if not c_actual and not c_visible:
        ic = "TN"
    elif UD and not FC and not AD:
        ic = "IC-1"
    elif UD and AD and not FC:
        ic = "IC-2"
    elif FC and not UD:
        ic = "IC-3"
    elif UD and FC:
        ic = "IC-4"
    elif AD and not UD and not FC:
        ic = "IC-0"
    else:
        ic = "TN"

    CR  = len(AD) / len(c_actual)  if c_actual  else None
    CP  = len(AD) / len(c_visible) if c_visible else None
    CDI = 1 - CR if CR is not None else (1.0 if c_actual else 0.0)

    return {
        "ic_type": ic,
        "AD": sorted(AD), "UD": sorted(UD), "FC": sorted(FC),
        "CR":  round(CR,  4) if CR  is not None else None,
        "CP":  round(CP,  4) if CP  is not None else None,
        "CDI": round(CDI, 4),
    }


def analyze_skill(skill_id: str, description: str,
                  file_rows: List[Tuple]) -> dict:
    """
    SkillX variant:
      C_visible = SKILL.md body (what a careful reader sees on the detail page)
      C_actual  = all other code files (py, js, ts, sh, requirements, package.json)
    """
    c_visible: Set[str] = set()
    c_actual:  Set[str] = set()
    skillmd_found = False
    sources_visible = {}
    sources_actual  = {}

    for (path, text_path) in file_rows:
        content = read_file(text_path)
        if content is None or len(content.strip()) < 5:
            continue

        ext = Path(path).suffix.lower()
        fname = Path(path).name.lower()

        if is_skillmd(path):
            # ── C_visible: scan full SKILL.md content ──────────────────
            caps = scan_skillmd_visible(content)
            if caps:
                c_visible.update(caps)
                sources_visible[path] = sorted(caps)
            skillmd_found = True

        elif ext in (".py", ".js", ".ts", ".sh", ".bash",
                     ".zsh", ".fish", ".ps1", ".rb", ".go"):
            # ── C_actual: code files ────────────────────────────────────
            caps = scan_text(content)
            if caps:
                c_actual.update(caps)
                sources_actual[path] = sorted(caps)

        elif fname in ("requirements.txt", "pyproject.toml",
                       "setup.py", "setup.cfg"):
            # infer from Python deps
            caps = set()
            for line in content.splitlines():
                pkg = line.strip().lower().split("==")[0].split(">=")[0]
                if pkg in ("requests", "httpx", "aiohttp", "urllib3"):
                    caps.add("NET_READ")
                if pkg in ("sendgrid", "yagmail", "smtplib"):
                    caps.add("NET_WRITE")
                if pkg in ("keyring", "cryptography", "pycryptodome"):
                    caps.add("CRYPTO_OPS")
                if pkg in ("selenium", "playwright", "pyppeteer"):
                    caps.add("BROWSER_CONTROL")
                if pkg in ("redis", "pymemcache"):
                    caps.add("MEMORY_WRITE")
            if caps:
                c_actual.update(caps)
                sources_actual[path] = sorted(caps)

        elif fname == "package.json":
            try:
                data = json.loads(content)
                deps = {**data.get("dependencies", {}),
                        **data.get("devDependencies", {})}
                caps = set()
                for pkg in deps:
                    if "axios" in pkg or "node-fetch" in pkg or "got" in pkg:
                        caps.add("NET_READ")
                    if "nodemailer" in pkg or "sendgrid" in pkg:
                        caps.add("NET_WRITE")
                    if "playwright" in pkg or "puppeteer" in pkg:
                        caps.add("BROWSER_CONTROL")
                    if "redis" in pkg:
                        caps.add("MEMORY_WRITE")
                if caps:
                    c_actual.update(caps)
                    sources_actual[path] = sorted(caps)
            except Exception:
                pass

        elif fname in ("readme.md",) or ext == ".md":
            # README / other md files: scan code blocks only for C_actual
            # (not full prose — to avoid double-counting with SKILL.md)
            code_blocks = re.findall(r'```[\w]*\n(.*?)```', content,
                                     re.DOTALL)
            for block in code_blocks:
                caps = scan_text(block)
                if caps:
                    c_actual.update(caps)
                    if path not in sources_actual:
                        sources_actual[path] = []
                    sources_actual[path] = sorted(
                        set(sources_actual.get(path, [])) | caps)

    result = classify_ic(c_actual, c_visible)
    result.update({
        "skill_id":       skill_id,
        "description":    description[:200],
        "skillmd_found":  skillmd_found,
        "c_visible":      sorted(c_visible),
        "c_actual":       sorted(c_actual),
        "n_visible":      len(c_visible),
        "n_actual":       len(c_actual),
        "sources_visible": sources_visible,
        "sources_actual":  sources_actual,
    })
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    conn = get_conn()

    # Load all skills
    q = "SELECT skill_id, description FROM skills"
    if args.limit:
        q += f" LIMIT {args.limit}"
    skill_rows = conn.execute(q).fetchall()
    log.info(f"Loaded {len(skill_rows):,} skills")

    # Load all file paths
    log.info("Loading file index...")
    all_files: Dict[str, List[Tuple]] = {}
    for (skill_id, path, text_path) in conn.execute(
        "SELECT skill_id, path, text_path FROM skill_files"
    ).fetchall():
        all_files.setdefault(skill_id, []).append((path, text_path))

    conn.close()

    log.info("Running SkillX experiment (C_visible=SKILL.md, C_actual=code files)...")

    results = []
    ic_counter    = Counter()
    cr_vals, cdi_vals = [], []
    no_skillmd    = 0

    with open(OUTPUT, "w", encoding="utf-8") as fout:
        for i, (skill_id, description) in enumerate(skill_rows):
            file_rows = all_files.get(skill_id, [])
            r = analyze_skill(skill_id, description or "", file_rows)
            fout.write(json.dumps(r, ensure_ascii=False) + "\n")
            results.append(r)
            ic_counter[r["ic_type"]] += 1
            if r["CR"] is not None:
                cr_vals.append(r["CR"])
            cdi_vals.append(r["CDI"])
            if not r["skillmd_found"]:
                no_skillmd += 1
            if (i + 1) % 2000 == 0:
                log.info(f"  {i+1:,}/{len(skill_rows):,} done...")

    N = len(results)
    mean_cr  = sum(cr_vals)  / len(cr_vals)  if cr_vals  else 0
    mean_cdi = sum(cdi_vals) / len(cdi_vals) if cdi_vals else 0

    # ── Compare with Script 27 baseline ─────────────────────────────────────
    s27_ic1 = 0.758  # from main experiment
    s27_cr  = 0.042
    s27_cdi = 0.894

    report = {
        "experiment":  "SkillX (C_visible=SKILL.md, C_actual=code-only)",
        "N_total":     N,
        "no_skillmd":  no_skillmd,
        "ic_distribution": dict(ic_counter),
        "ic_pct": {k: round(v / N * 100, 2) for k, v in ic_counter.items()},
        "mean_CR":     round(mean_cr,  4),
        "mean_CDI":    round(mean_cdi, 4),
        "inconsistency_pct": round(
            (ic_counter["IC-1"] + ic_counter["IC-2"] + ic_counter["IC-4"])
            / N * 100, 2),
        "comparison_vs_script27": {
            "IC-1_script27_pct": s27_ic1 * 100,
            "IC-1_skillX_pct":   round(ic_counter["IC-1"] / N * 100, 2),
            "CR_script27":       s27_cr,
            "CR_skillX":         round(mean_cr, 4),
            "CDI_script27":      s27_cdi,
            "CDI_skillX":        round(mean_cdi, 4),
        },
        "interpretation": (
            "SkillX tests: if users read SKILL.md before installing, "
            "how much capability is STILL hidden in code files? "
            "Lower IC-1 than Script 27 → SKILL.md provides meaningful disclosure. "
            "Similar IC-1 → SKILL.md is as silent as the one-line description."
        ),
    }

    with open(REPORT, "w") as f:
        json.dump(report, f, indent=2)

    # ── Print comparison ─────────────────────────────────────────────────────
    print(f"\n{'='*65}")
    print(f"SkillX Ablation: C_visible=SKILL.md | C_actual=code-only")
    print(f"N = {N:,}  (skills without SKILL.md: {no_skillmd:,})")
    print(f"{'='*65}")
    print(f"{'IC type':<8} {'SkillX':>10} {'Script27 (desc)':>18}  Δ")
    print(f"{'─'*55}")
    s27_ref = {
        "IC-1": 75.8, "IC-2": 13.6, "IC-4": 2.9,
        "IC-3": 0.4,  "IC-0": 0.4,  "TN":   6.8,
    }
    for ic in ["IC-1", "IC-2", "IC-4", "IC-3", "IC-0", "TN"]:
        n   = ic_counter.get(ic, 0)
        pct = n / N * 100
        ref = s27_ref.get(ic, 0)
        delta = pct - ref
        sign  = "▲" if delta > 0 else ("▼" if delta < 0 else "=")
        print(f"{ic:<8} {pct:>9.1f}%  {ref:>16.1f}%  "
              f"{sign}{abs(delta):.1f}pp")
    print(f"{'─'*55}")
    incon = ic_counter["IC-1"] + ic_counter["IC-2"] + ic_counter["IC-4"]
    print(f"{'Total≠':<8} {incon/N*100:>9.1f}%  {75.8+13.6+2.9:>16.1f}%")
    print(f"{'mean CR':<8} {mean_cr:>10.4f}  {s27_cr:>17.4f}")
    print(f"{'mean CDI':<8} {mean_cdi:>10.4f}  {s27_cdi:>17.4f}")
    print(f"\nOutput: {OUTPUT}")
    print(f"Report: {REPORT}")


if __name__ == "__main__":
    main()
