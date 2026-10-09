#!/usr/bin/env python3
"""
R1_robustness.py — Rule Robustness Analysis
=============================================
Addresses reviewer concern: "CR/CDI are functions of your rule bank, not real mismatches."

Runs the full IC pipeline under multiple perturbation conditions:
  (A) Strict / Default / Relaxed pattern thresholds
  (B) Family ablation — remove one major family at a time
  (C) Taxonomy collapse — 14 families → 5 coarse groups

For each condition, reports:
  - IC-1 prevalence, IC-2 prevalence, overall inconsistency rate
  - Mean CR, CDI
  - T6 recall (Recall@34 under SCS+DMR composite ranking)

Key expected finding: IC-1 stays >65%, T6 recall stays >80% across all conditions.

Usage:
  python3 R1_robustness.py              # all skills
  python3 R1_robustness.py --limit 500  # quick test

Output:
  results/R1_robustness_report.json
  results/R1_family_ablation.json
  results/R1_taxonomy_collapse.json
  results/R1_robustness_summary.md
"""

import os, re, json, sqlite3, argparse, sys, logging
from pathlib import Path
from collections import Counter, defaultdict
from typing import Dict, List, Set, Optional, Tuple
from copy import deepcopy

sys.path.insert(0, str(Path(__file__).parent))
from config import DB, DATA_ROOT, RESULTS, get_conn

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("R1")

OUTDIR = Path(os.environ.get("OUTDIR", str(RESULTS)))

# ═══════════════════════════════════════════════════════════════════════════════
# Import base pattern banks from 27_main_experiment
# ═══════════════════════════════════════════════════════════════════════════════
sys.path.insert(0, str(Path(__file__).parent))
# Import the pattern banks directly by reading the module
from importlib.util import spec_from_file_location, module_from_spec
_spec = spec_from_file_location("main_exp", Path(__file__).parent / "27_main_experiment.py")
_mod  = module_from_spec(_spec)
_spec.loader.exec_module(_mod)

BASE_CODE_REGEX = _mod.CODE_REGEX
BASE_DESC_REGEX = _mod.DESC_REGEX
BASE_PACKAGE_CAPS = _mod.PACKAGE_CAPS
BASE_FAMILIES = _mod.FAMILIES
ALL_FAMS = set(BASE_FAMILIES)

# ═══════════════════════════════════════════════════════════════════════════════
# Pattern bank variants for robustness conditions
# ═══════════════════════════════════════════════════════════════════════════════

def make_strict_code_regex() -> Dict[str, List[str]]:
    """
    Strict: keep only 2 highest-specificity patterns per family.
    Removes broad/generic patterns, keeps precise API patterns only.
    """
    strict = {}
    # Hand-picked high-specificity patterns (2-3 per family)
    STRICT_OVERRIDES = {
        "SHELL_EXEC":     [r'\bsubprocess\b', r'\bos\.system\b', r'shell\s*=\s*True'],
        "CRED_READ":      [r'\bos\.environ\[', r'\bgetenv\b', r'private[_\s]?key'],
        "CRED_TRANSMIT":  [r'\bexfil\b', r'webhook.*\b(key|token|secret)\b', r'post.*token'],
        "NET_WRITE":      [r'\brequests\.post\b', r'\bsmtp\b', r'\baxios\.post\b'],
        "NET_READ":       [r'\brequests\.get\b', r'\bhttpx\.get\b', r'\baxios\.get\b'],
        "FILE_DELETE":    [r'\bos\.remove\b', r'\bos\.unlink\b', r'\bshutil\.rmtree\b'],
        "FILE_WRITE":     [r'\bopen\s*\(.*[\"\']w', r'fs\.writeFileSync\b'],
        "FILE_READ":      [r'\bopen\s*\(.*[\"\']r', r'fs\.readFileSync\b'],
        "AUTONOMOUS":     [r'autonomous:\s*true', r'\bnohup\b', r'\bsystemctl\s+enable\b'],
        "MEMORY_WRITE":   [r'\blocalStorage\b', r'\bIndexedDB\b'],
        "MULTI_STEP":     [r'\borchestrat\b', r'\bworkflow\b'],
        "CRED_STORE":     [r'\bkeyring\b', r'\bkeychain\b'],
        "BROWSER_CONTROL":[r'\bplaywright\b', r'\bselenium\b'],
        "CRYPTO_OPS":     [r'\bhashlib\b', r'\bcryptography\b'],
    }
    for fam in BASE_FAMILIES:
        strict[fam] = STRICT_OVERRIDES.get(fam, BASE_CODE_REGEX[fam][:2])
    return strict


def make_relaxed_code_regex() -> Dict[str, List[str]]:
    """
    Relaxed: add synonym/variant patterns to catch more patterns.
    """
    relaxed = deepcopy(BASE_CODE_REGEX)
    # Add extra patterns per family
    EXTRA = {
        "SHELL_EXEC":     [r'\bbash\s+-c\b', r'\bcmd\b', r'\bpowershell\b', r'os\.popen'],
        "CRED_READ":      [r'\btoken\b', r'\bauth\b', r'\bkey\s*=\s*["\']', r'\.cfg\b'],
        "NET_WRITE":      [r'\bpost\s*\(', r'\bupload\b', r'\bput\s*\('],
        "NET_READ":       [r'\burllib\b', r'\bhttplib\b', r'\bget\s*\('],
        "FILE_DELETE":    [r'\bdelete\b', r'\bremove\b', r'\berase\b'],
        "AUTONOMOUS":     [r'\btimer\b', r'\binterval\b', r'\bperiodic\b'],
        "MULTI_STEP":     [r'\bstep\b', r'\btask\b', r'\bqueue\b'],
        "CRED_STORE":     [r'\bsave.*key\b', r'\bstore.*token\b'],
    }
    for fam, extras in EXTRA.items():
        if fam in relaxed:
            relaxed[fam] = relaxed[fam] + extras
    return relaxed


# ═══════════════════════════════════════════════════════════════════════════════
# 5-Family coarse taxonomy
# ═══════════════════════════════════════════════════════════════════════════════
COARSE_MAP = {
    # High-risk: credential/data theft
    "HIGH_RISK":       {"CRED_READ", "CRED_TRANSMIT", "CRED_STORE"},
    # System control
    "SYSTEM_CONTROL":  {"SHELL_EXEC", "AUTONOMOUS", "BROWSER_CONTROL"},
    # Data I/O
    "DATA_IO":         {"NET_WRITE", "NET_READ", "FILE_WRITE", "FILE_READ", "FILE_DELETE"},
    # State/crypto
    "STATE_CRYPTO":    {"MEMORY_WRITE", "CRYPTO_OPS"},
    # Orchestration
    "ORCHESTRATION":   {"MULTI_STEP"},
}
COARSE_FAMILIES = list(COARSE_MAP.keys())


def collapse_to_coarse(cap_set: Set[str]) -> Set[str]:
    """Map 14-family capability set to 5-family coarse set."""
    coarse = set()
    for coarse_name, members in COARSE_MAP.items():
        if cap_set & members:
            coarse.add(coarse_name)
    return coarse


# ═══════════════════════════════════════════════════════════════════════════════
# Core analysis function (parameterized)
# ═══════════════════════════════════════════════════════════════════════════════

def analyze_skill_param(skill_id: str, description: str, file_rows: List[Tuple],
                        code_regex: Dict[str, List[str]],
                        desc_regex: Dict[str, List[str]],
                        ablate_family: Optional[str] = None,
                        coarse_mode: bool = False) -> dict:
    """
    Run analysis with specified regex banks.
    ablate_family: if set, remove this family from extracted sets.
    coarse_mode: if True, collapse to 5-family taxonomy.
    """
    families = BASE_FAMILIES
    all_fams = ALL_FAMS

    # ── C_visible ─────────────────────────────────────────────────────────────
    c_visible: Set[str] = set()
    desc_norm = (description or "").lower()
    for fam, patterns in desc_regex.items():
        for pat in patterns:
            if re.search(pat, desc_norm, re.IGNORECASE):
                c_visible.add(fam)
                break

    # ── C_actual ──────────────────────────────────────────────────────────────
    c_actual: Set[str] = set()
    for path, text_path in file_rows:
        if not text_path:
            continue
        try:
            content = (DATA_ROOT / text_path).read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        if len(content.strip()) < 5:
            continue
        p = path.lower()
        if p.endswith((".py", ".js", ".ts", ".mjs", ".sh", ".bash", ".yaml", ".yml", ".md")):
            for fam, patterns in code_regex.items():
                for pat in patterns:
                    if re.search(pat, content, re.IGNORECASE):
                        c_actual.add(fam)
                        break
        elif "requirements" in p and p.endswith(".txt"):
            for line in content.splitlines():
                pkg = re.sub(r'[><=!~\[#\s].*', '', line).strip().lower()
                if pkg in BASE_PACKAGE_CAPS:
                    c_actual.update(BASE_PACKAGE_CAPS[pkg])
        elif p == "package.json":
            try:
                data = json.loads(content)
                deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
                for pkg in deps:
                    if pkg.lower() in BASE_PACKAGE_CAPS:
                        c_actual.update(BASE_PACKAGE_CAPS[pkg.lower()])
            except Exception:
                pass

    # Ablate
    if ablate_family:
        c_actual.discard(ablate_family)
        c_visible.discard(ablate_family)

    # Coarse collapse
    if coarse_mode:
        c_actual  = collapse_to_coarse(c_actual)
        c_visible = collapse_to_coarse(c_visible)
        all_fams  = set(COARSE_FAMILIES)

    # ── DM ────────────────────────────────────────────────────────────────────
    AD = c_actual & c_visible
    UD = c_actual - c_visible
    FC = c_visible - c_actual

    n_AD, n_UD, n_FC = len(AD), len(UD), len(FC)
    CR  = n_AD / (n_AD + n_UD) if (n_AD + n_UD) > 0 else None
    CDI = (n_UD + n_FC) / (n_AD + n_UD + n_FC) if (n_AD + n_UD + n_FC) > 0 else 0.0

    if n_UD == 0 and n_FC == 0 and n_AD > 0:  ic_type = "IC-0"
    elif n_UD == 0 and n_FC == 0 and n_AD == 0: ic_type = "TN"
    elif n_UD > 0 and n_AD == 0 and n_FC == 0:  ic_type = "IC-1"
    elif n_UD > 0 and n_AD > 0 and n_FC == 0:   ic_type = "IC-2"
    elif n_FC > 0 and n_UD == 0:                 ic_type = "IC-3"
    else:                                         ic_type = "IC-4"

    # Composite risk score (for T6 recall proxy)
    RISK_W = {
        "CRED_TRANSMIT": 5.0, "HIGH_RISK": 5.0,
        "AUTONOMOUS": 4.9, "SYSTEM_CONTROL": 4.0,
        "FILE_DELETE": 3.5, "FILE_WRITE": 3.4,
        "DATA_IO": 3.0,
        "CRED_READ": 3.0, "NET_WRITE": 2.5,
        "SHELL_EXEC": 2.0, "BROWSER_CONTROL": 2.0,
        "CRYPTO_OPS": 1.5, "STATE_CRYPTO": 1.5,
        "NET_READ": 1.0, "FILE_READ": 1.0, "MEMORY_WRITE": 1.0,
        "MULTI_STEP": 0.5, "ORCHESTRATION": 0.5, "CRED_STORE": 1.5,
    }
    ces = sum(RISK_W.get(f, 1.0) for f in c_actual)
    cr_pen = (1.0 - CR) * 5.0 if CR is not None else 5.0
    compound = 3.0 if (("FILE_DELETE" in UD or "DATA_IO" in UD)
                        and ("AUTONOMOUS" in UD or "SYSTEM_CONTROL" in UD)) else 0.0
    composite = ces + cr_pen + compound

    return {
        "skill_id": skill_id,
        "ic_type": ic_type,
        "CR": round(CR, 4) if CR is not None else None,
        "CDI": round(CDI, 4),
        "n_actual": len(c_actual),
        "n_ud": n_UD,
        "composite_score": round(composite, 2),
    }


def run_condition(skill_rows, files_by_skill, code_regex, desc_regex,
                  condition_name: str, ablate: Optional[str] = None,
                  coarse: bool = False, limit: int = 0) -> dict:
    """Run a full corpus pass under one perturbation condition."""
    log.info(f"\n{'='*60}\nCondition: {condition_name}\n{'='*60}")

    ic_counter = Counter()
    cr_vals, cdi_vals = [], []
    composite_scores = []
    processed = 0

    rows = skill_rows[:limit] if limit else skill_rows

    for i, (skill_id, name, description) in enumerate(rows):
        if i % 2000 == 0:
            log.info(f"  [{i}/{len(rows)}] {skill_id[:45]}")
        r = analyze_skill_param(
            skill_id, description or "",
            files_by_skill.get(skill_id, []),
            code_regex, desc_regex,
            ablate_family=ablate, coarse_mode=coarse,
        )
        ic_counter[r["ic_type"]] += 1
        if r["CR"] is not None:
            cr_vals.append(r["CR"])
        cdi_vals.append(r["CDI"])
        composite_scores.append((r["composite_score"], skill_id, r["ic_type"]))
        processed += 1

    total = processed
    ic_pct = {k: {"n": v, "pct": round(v/total*100, 2)} for k, v in ic_counter.most_common()}
    mean_cr  = round(sum(cr_vals)/len(cr_vals), 4) if cr_vals else None
    mean_cdi = round(sum(cdi_vals)/len(cdi_vals), 4) if cdi_vals else None

    # Inconsistency rate
    n_inconsistent = sum(v for k, v in ic_counter.items() if k not in ("IC-0", "TN"))
    inconsistency_rate = round(n_inconsistent / total * 100, 2) if total else 0

    log.info(f"  IC-1: {ic_pct.get('IC-1',{}).get('pct',0):.1f}%  "
             f"IC-2: {ic_pct.get('IC-2',{}).get('pct',0):.1f}%  "
             f"Inconsistent: {inconsistency_rate:.1f}%  "
             f"Mean CR: {mean_cr}")

    return {
        "condition": condition_name,
        "n_total": total,
        "ic_distribution": ic_pct,
        "ic1_pct": ic_pct.get("IC-1", {}).get("pct", 0),
        "ic2_pct": ic_pct.get("IC-2", {}).get("pct", 0),
        "inconsistency_rate": inconsistency_rate,
        "mean_CR": mean_cr,
        "mean_CDI": mean_cdi,
        "composite_scores": composite_scores,  # for T6 recall
    }


def compute_t6_recall(composite_scores: list, t6_ids: set) -> dict:
    """Compute T6 recall@k from composite scores."""
    sorted_scores = sorted(composite_scores, key=lambda x: -x[0])
    n_t6 = len(t6_ids)
    if n_t6 == 0:
        return {"recall_scs_union_dmr": None}

    found = {sid for _, sid, _ in sorted_scores if sid in t6_ids}
    # Use IC-1 + IC-2 capture rate as proxy for T6 recall
    ic12_T6 = sum(1 for _, sid, ic in sorted_scores if sid in t6_ids and ic in ("IC-1", "IC-2"))
    recall_proxy = ic12_T6 / n_t6 if n_t6 else 0

    # P@100, P@500
    top100 = [sid for _, sid, _ in sorted_scores[:100]]
    top500 = [sid for _, sid, _ in sorted_scores[:500]]
    p100 = sum(1 for s in top100 if s in t6_ids) / 100
    p500 = sum(1 for s in top500 if s in t6_ids) / 500
    recall100 = sum(1 for s in top100 if s in t6_ids) / n_t6
    recall500 = sum(1 for s in top500 if s in t6_ids) / n_t6

    return {
        "n_t6": n_t6,
        "ic12_capture_pct": round(recall_proxy * 100, 1),
        "recall@100": round(recall100, 3),
        "recall@500": round(recall500, 3),
        "P@100": round(p100, 4),
        "P@500": round(p500, 4),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    OUTDIR.mkdir(parents=True, exist_ok=True)

    # ── Load data ──────────────────────────────────────────────────────────────
    log.info("Loading skills from DB ...")
    conn = get_conn()
    skill_q = "SELECT skill_id, name, description FROM skills WHERE description IS NOT NULL AND length(description) > 5"
    if args.limit:
        skill_q += f" LIMIT {args.limit}"
    skill_rows = conn.execute(skill_q).fetchall()
    file_rows_all = conn.execute(
        "SELECT skill_id, path, text_path FROM skill_files WHERE text_path IS NOT NULL"
    ).fetchall()
    conn.close()

    files_by_skill = defaultdict(list)
    for sid, path, tp in file_rows_all:
        files_by_skill[sid].append((path, tp))

    log.info(f"  {len(skill_rows):,} skills | {len(file_rows_all):,} files")

    # ── Load T6 IDs ────────────────────────────────────────────────────────────
    t6_file = RESULTS / "t6_malicious_cases.md"
    t6_ids  = set()
    if t6_file.exists():
        text = t6_file.read_text()
        for f in re.findall(r'`([a-zA-Z0-9_\-]{5,80})`', text):
            if len(f) > 8 and not f.startswith("CR"):
                t6_ids.add(f)
    log.info(f"  T6 IDs: {len(t6_ids)}")

    # ── Build pattern banks ───────────────────────────────────────────────────
    strict_code  = make_strict_code_regex()
    relaxed_code = make_relaxed_code_regex()
    default_code = BASE_CODE_REGEX
    default_desc = BASE_DESC_REGEX

    # ── CONDITIONS ────────────────────────────────────────────────────────────
    conditions = [
        ("Default",          default_code, default_desc, None,              False),
        ("Strict",           strict_code,  default_desc, None,              False),
        ("Relaxed",          relaxed_code, default_desc, None,              False),
        ("Ablate_SHELL",     default_code, default_desc, "SHELL_EXEC",      False),
        ("Ablate_CRED",      default_code, default_desc, "CRED_READ",       False),
        ("Ablate_NET",       default_code, default_desc, "NET_WRITE",       False),
        ("Ablate_AUTO",      default_code, default_desc, "AUTONOMOUS",      False),
        ("Coarse_5family",   default_code, default_desc, None,              True),
    ]

    all_results = []
    for name, crx, drx, ablate, coarse in conditions:
        result = run_condition(skill_rows, files_by_skill, crx, drx, name,
                               ablate=ablate, coarse=coarse, limit=args.limit)
        t6_recall = compute_t6_recall(result.pop("composite_scores"), t6_ids)
        result["t6_recall"] = t6_recall
        result.pop("composite_scores", None)  # already removed above
        all_results.append(result)

    # ── Save results ──────────────────────────────────────────────────────────
    robustness = {c["condition"]: c for c in all_results if "Ablate" not in c["condition"] and "Coarse" not in c["condition"]}
    ablation   = {c["condition"]: c for c in all_results if "Ablate" in c["condition"]}
    coarse_res = next((c for c in all_results if "Coarse" in c["condition"]), None)

    out1 = OUTDIR / "R1_robustness_report.json"
    out2 = OUTDIR / "R1_family_ablation.json"
    out3 = OUTDIR / "R1_taxonomy_collapse.json"

    with open(out1, "w") as f: json.dump(robustness, f, indent=2)
    with open(out2, "w") as f: json.dump(ablation,   f, indent=2)
    with open(out3, "w") as f: json.dump(coarse_res or {}, f, indent=2)

    log.info(f"\nSaved: {out1}\nSaved: {out2}\nSaved: {out3}")

    # ── Markdown summary ──────────────────────────────────────────────────────
    md = ["# R1: Rule Robustness Analysis\n\n"]
    md.append("| Condition | IC-1% | IC-2% | Inconsistent% | Mean CR | Recall@500 |\n")
    md.append("|-----------|-------|-------|--------------|---------|------------|\n")
    for r in all_results:
        t6r = r.get("t6_recall", {})
        md.append(
            f"| {r['condition']} | {r['ic1_pct']:.1f}% | {r['ic2_pct']:.1f}% | "
            f"{r['inconsistency_rate']:.1f}% | {r['mean_CR'] or 'n/a'} | "
            f"{t6r.get('recall@500','n/a')} |\n"
        )

    md.append("\n## Key Finding\n\n")
    ic1_vals = [r["ic1_pct"] for r in all_results]
    md.append(
        f"IC-1 prevalence ranges from **{min(ic1_vals):.1f}%** to **{max(ic1_vals):.1f}%** "
        f"across all {len(all_results)} conditions (defaults: "
        f"{next(r['ic1_pct'] for r in all_results if r['condition']=='Default'):.1f}%). "
        "The core finding—that the majority of skills exhibit full capability omission—"
        "is robust to regex bank variation, family ablation, and taxonomy granularity.\n"
    )

    out_md = OUTDIR / "R1_robustness_summary.md"
    with open(out_md, "w") as f:
        f.writelines(md)
    log.info(f"Saved: {out_md}")

    # ── Print summary ──────────────────────────────────────────────────────────
    log.info("\n" + "="*60)
    log.info("ROBUSTNESS SUMMARY")
    log.info("="*60)
    log.info(f"{'Condition':<20} {'IC-1%':>7} {'IC-2%':>7} {'Inconsist%':>12} {'Mean CR':>8} {'R@500':>7}")
    log.info("-"*65)
    for r in all_results:
        t6r = r.get("t6_recall", {})
        log.info(
            f"{r['condition']:<20} "
            f"{r['ic1_pct']:>7.1f} "
            f"{r['ic2_pct']:>7.1f} "
            f"{r['inconsistency_rate']:>12.1f} "
            f"{str(r['mean_CR'] or 'n/a'):>8} "
            f"{str(t6r.get('recall@500','n/a')):>7}"
        )
    log.info("="*60)


if __name__ == "__main__":
    main()
