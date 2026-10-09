#!/usr/bin/env python3
"""
28b_autodesc_rulebased.py
=========================
Rule-based AutoDesc simulation — no LLM API needed.

Generates capability-accurate descriptions using pre-defined templates
for each capability family. This produces a deterministic upper-bound
on what AutoDesc can achieve (CR = 1.0 by construction).

Used as:
  (a) Proof-of-concept for the AutoDesc paper section
  (b) Baseline for comparison with LLM-generated descriptions

Usage:
    python 28b_autodesc_rulebased.py --n 100 --ic-filter IC-1
    python 28b_autodesc_rulebased.py --n 500
"""

import argparse
import json
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

# ── Paths ─────────────────────────────────────────────────────────────────────
RESULTS_DIR  = Path(__file__).parents[2] / "clawhub/results"
JSONL_PATH   = RESULTS_DIR / "main_experiment_results.jsonl"
AUTODESC_DIR = RESULTS_DIR / "autodesc"
RULEBASED_SAMPLES = AUTODESC_DIR / "rulebased_samples.jsonl"
RULEBASED_STATS   = AUTODESC_DIR / "rulebased_cr_stats.json"

# ── Per-family description templates ─────────────────────────────────────────
# Each template is a sentence that a user can understand.
# Together they form a "complete" disclosure description.
FAMILY_SENTENCES = {
    "CRED_READ": (
        "It reads API keys, authentication tokens, or other credentials "
        "stored in your environment variables or configuration files."
    ),
    "CRED_TRANSMIT": (
        "It transmits credentials or authentication tokens to external services."
    ),
    "CRED_STORE": (
        "It stores credentials, tokens, or secrets to local storage or "
        "configuration files for future use."
    ),
    "NET_WRITE": (
        "It sends data to external network endpoints, including webhooks, "
        "APIs, or third-party services."
    ),
    "NET_READ": (
        "It fetches data from external URLs or APIs over the network."
    ),
    "SHELL_EXEC": (
        "It executes shell commands or system-level processes on your machine."
    ),
    "FILE_DELETE": (
        "It deletes or permanently removes files from your local filesystem."
    ),
    "FILE_READ": (
        "It reads files from your local filesystem."
    ),
    "FILE_WRITE": (
        "It writes or modifies files on your local filesystem."
    ),
    "AUTONOMOUS": (
        "It operates autonomously and may take actions without asking for "
        "per-operation confirmation."
    ),
    "MEMORY_WRITE": (
        "It writes data to persistent memory or local cache for future sessions."
    ),
    "MULTI_STEP": (
        "It executes multi-step automated pipelines that may involve "
        "several sequential operations."
    ),
}

# Risk classification (HIGH → explicit consent required)
RISK_LEVEL = {
    "CRED_READ":     "HIGH",
    "CRED_TRANSMIT": "HIGH",
    "CRED_STORE":    "HIGH",
    "NET_WRITE":     "MEDIUM",
    "AUTONOMOUS":    "MEDIUM",
    "FILE_DELETE":   "MEDIUM",
    "SHELL_EXEC":    "LOW",
    "FILE_READ":     "LOW",
    "NET_READ":      "LOW",
    "MEMORY_WRITE":  "LOW",
    "FILE_WRITE":    "LOW",
    "MULTI_STEP":    "LOW",
}

# ── Dataclass ─────────────────────────────────────────────────────────────────
@dataclass
class RuleBasedResult:
    skill_id:            str
    ic_type_before:      str
    cr_before:           float
    cdi_before:          float
    n_actual:            int
    hidden_families:     list
    original_desc:       str
    revised_description: str
    consent_notice:      str
    cr_after:            float   # 1.0 by construction
    cdi_after:           float   # ~0.025
    high_risk_families:  list
    generated_by:        str
    generated_at:        str

# ── Template-based description generator ─────────────────────────────────────
def generate_rulebased_desc(families: list[str],
                             original_desc: str,
                             skill_name: str) -> tuple[str, str]:
    """
    Generate a capability-accurate description from family templates.
    Returns (revised_description, consent_notice).
    """
    if not families:
        return original_desc, ""

    # Sort: HIGH risk first, then MEDIUM, then LOW
    risk_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    sorted_fams = sorted(families,
                         key=lambda f: risk_order.get(RISK_LEVEL.get(f, "LOW"), 2))

    high_risk = [f for f in sorted_fams if RISK_LEVEL.get(f, "LOW") == "HIGH"]
    med_risk  = [f for f in sorted_fams if RISK_LEVEL.get(f, "LOW") == "MEDIUM"]

    # Opening sentence — preserve original purpose if available, otherwise generic
    if original_desc and len(original_desc.strip()) > 10:
        # Trim to first sentence max
        first_sent = original_desc.strip().split(".")[0].rstrip("。").strip()
        if len(first_sent) > 120:
            first_sent = first_sent[:120] + "..."
        intro = f"{first_sent}."
    else:
        intro = "This skill automates tasks in your environment."

    # Capability sentences (one per family, ordered by risk)
    cap_sentences = []
    for fam in sorted_fams:
        sent = FAMILY_SENTENCES.get(fam, f"It uses the {fam} capability.")
        cap_sentences.append(sent)

    # Cap at 5 sentences to keep description concise
    if len(cap_sentences) > 5:
        # Include all HIGH/MEDIUM, sample LOW
        priority = [FAMILY_SENTENCES.get(f, "") for f in sorted_fams
                    if RISK_LEVEL.get(f, "LOW") in ("HIGH", "MEDIUM")]
        low_sents = [FAMILY_SENTENCES.get(f, "") for f in sorted_fams
                     if RISK_LEVEL.get(f, "LOW") == "LOW"][:2]
        cap_sentences = priority + low_sents
        if len(sorted_fams) > len(priority) + 2:
            cap_sentences.append(
                f"It also uses {len(sorted_fams) - len(priority) - 2} "
                f"additional system-level capabilities."
            )

    revised = intro + " " + " ".join(cap_sentences)

    # Consent notice
    if high_risk:
        hr_names = ", ".join(f.replace("_", " ").lower() for f in high_risk)
        consent = (
            f"By installing this skill, you consent to it accessing "
            f"{hr_names}"
        )
        if med_risk:
            mr_names = ", ".join(f.replace("_", " ").lower() for f in med_risk)
            consent += f", performing {mr_names}"
        consent += " on your behalf."
    elif med_risk:
        mr_names = ", ".join(f.replace("_", " ").lower() for f in med_risk)
        consent = (
            f"By installing this skill, you consent to it performing "
            f"{mr_names} on your behalf."
        )
    else:
        consent = (
            "By installing this skill, you consent to it using "
            "system-level capabilities as described above."
        )

    return revised, consent

# ── Keyword-based CR recomputation ────────────────────────────────────────────
FAMILY_KEYWORDS = {
    "CRED_READ":     ["credential", "api key", "token", "password", "secret", "auth",
                      "environment variable", "config"],
    "CRED_TRANSMIT": ["transmit", "exfiltrat", "send credential", "upload secret",
                      "authentication token", "credential"],
    "CRED_STORE":    ["store credential", "save token", "persist", "credential"],
    "NET_WRITE":     ["send", "outbound", "external", "webhook", "endpoint",
                      "transmit", "upload", "network"],
    "NET_READ":      ["fetch", "download", "url", "api", "network"],
    "SHELL_EXEC":    ["shell", "command", "process", "execute", "system"],
    "FILE_DELETE":   ["delete", "remove", "filesystem"],
    "FILE_READ":     ["read file", "local file", "filesystem"],
    "FILE_WRITE":    ["write", "modif", "file", "filesystem"],
    "AUTONOMOUS":    ["autonomous", "automatic", "without", "confirmation"],
    "MEMORY_WRITE":  ["memory", "cache", "persistent"],
    "MULTI_STEP":    ["pipeline", "multi-step", "sequential", "automated"],
}

def compute_cr(desc: str, families: list[str]) -> float:
    desc_lower = desc.lower()
    if not families:
        return 1.0
    hits = sum(
        1 for f in families
        if any(kw in desc_lower for kw in FAMILY_KEYWORDS.get(f, [f.lower()]))
    )
    return round(hits / len(families), 3)

def compute_cdi(cr: float, cp: float = 0.97) -> float:
    return round(1.0 - (cr + cp) / 2.0, 3)

# ── Main ─────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Rule-based AutoDesc simulation")
    parser.add_argument("--n",            type=int, default=100,   help="Number of skills")
    parser.add_argument("--ic-filter",    type=str, default=None,  help="IC type filter (IC-1, IC-2, ...)")
    parser.add_argument("--min-families", type=int, default=2,     help="Min hidden families to include")
    parser.add_argument("--all",          action="store_true",     help="Process ALL IC-1/IC-2 skills")
    args = parser.parse_args()

    AUTODESC_DIR.mkdir(parents=True, exist_ok=True)

    # ── Load skills ───────────────────────────────────────────────────────
    print(f"[RuleBasedAutoDesc] Loading from {JSONL_PATH}")
    rows = []
    with open(JSONL_PATH) as f:
        for line in f:
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if args.ic_filter and r.get("ic_type") != args.ic_filter:
                continue
            if not args.ic_filter:
                # Default: IC-1 and IC-2
                if r.get("ic_type") not in ("IC-1", "IC-2"):
                    continue
            ud = r.get("UD") or []
            if isinstance(ud, str):
                try:
                    ud = json.loads(ud)
                except Exception:
                    ud = []
            if len(ud) < args.min_families:
                continue
            rows.append(r)
            if not args.all and len(rows) >= args.n:
                break

    rows.sort(key=lambda r: r.get("CDI", 0), reverse=True)
    if not args.all:
        rows = rows[:args.n]

    print(f"[RuleBasedAutoDesc] Processing {len(rows)} skills "
          f"(IC filter: {args.ic_filter or 'IC-1+IC-2'}, "
          f"min families: {args.min_families})")

    # ── Process ───────────────────────────────────────────────────────────
    results = []
    with open(RULEBASED_SAMPLES, "w") as out_f:
        for i, row in enumerate(rows):
            skill_id = row.get("skill_id", f"skill_{i}")
            orig_desc = row.get("description", "") or ""
            cr_before = float(row.get("CR") or row.get("cr") or 0.0)
            cdi_before = float(row.get("CDI") or row.get("cdi") or 1.0)
            ic_type = row.get("ic_type", "IC-1")
            ud = row.get("UD") or []
            if isinstance(ud, str):
                try:
                    ud = json.loads(ud)
                except Exception:
                    ud = []

            revised, consent = generate_rulebased_desc(ud, orig_desc, skill_id)

            cr_after = compute_cr(revised, ud)
            cdi_after = compute_cdi(cr_after)
            high_risk = [f for f in ud if RISK_LEVEL.get(f, "LOW") == "HIGH"]

            result = RuleBasedResult(
                skill_id            = skill_id,
                ic_type_before      = ic_type,
                cr_before           = cr_before,
                cdi_before          = cdi_before,
                n_actual            = int(row.get("n_actual", len(ud))),
                hidden_families     = ud,
                original_desc       = orig_desc[:200],
                revised_description = revised,
                consent_notice      = consent,
                cr_after            = cr_after,
                cdi_after           = cdi_after,
                high_risk_families  = high_risk,
                generated_by        = "CapMismatch-AutoDesc/rule-based-v1.0",
                generated_at        = datetime.utcnow().isoformat() + "Z",
            )
            results.append(result)
            out_f.write(json.dumps(asdict(result)) + "\n")

    # ── Stats ─────────────────────────────────────────────────────────────
    n = len(results)
    if n == 0:
        print("No skills processed.")
        return

    mean_cr_before = sum(r.cr_before for r in results) / n
    mean_cr_after  = sum(r.cr_after  for r in results) / n
    mean_delta     = mean_cr_after - mean_cr_before
    mean_cdi_drop  = sum(r.cdi_before - r.cdi_after for r in results) / n
    ic0_count      = sum(1 for r in results if r.cr_after >= 0.95)
    high_risk_pct  = sum(1 for r in results if r.high_risk_families) / n * 100
    n_families_list = [len(r.hidden_families) for r in results]
    mean_families  = sum(n_families_list) / n

    stats = {
        "method":             "rule-based AutoDesc v1.0",
        "n_processed":        n,
        "ic_filter":          args.ic_filter or "IC-1+IC-2",
        "mean_cr_before":     round(mean_cr_before, 3),
        "mean_cr_after":      round(mean_cr_after, 3),
        "mean_cr_delta":      round(mean_delta, 3),
        "mean_cdi_before":    round(sum(r.cdi_before for r in results)/n, 3),
        "mean_cdi_after":     round(sum(r.cdi_after  for r in results)/n, 3),
        "mean_cdi_reduction": round(mean_cdi_drop, 3),
        "ic0_pct":            round(ic0_count / n * 100, 1),
        "high_risk_skill_pct":round(high_risk_pct, 1),
        "mean_hidden_families": round(mean_families, 1),
        "total_cr_improvement": round(mean_delta / max(1e-6, mean_cr_before), 1) if mean_cr_before > 0 else "∞",
    }
    with open(RULEBASED_STATS, "w") as f:
        json.dump(stats, f, indent=2)

    # ── Print example ─────────────────────────────────────────────────────
    print("\n" + "="*65)
    print("RuleBased AutoDesc — Results")
    print("="*65)
    print(f"  Skills processed:       {n}")
    print(f"  Mean CR:       {mean_cr_before:.3f} → {mean_cr_after:.3f}  "
          f"(+{mean_delta:.3f})")
    print(f"  Mean CDI:      {stats['mean_cdi_before']:.3f} → "
          f"{stats['mean_cdi_after']:.3f}  (-{mean_cdi_drop:.3f})")
    print(f"  CR≥0.95 (≈IC-0) rate:   {stats['ic0_pct']}%")
    print(f"  High-risk skill rate:   {stats['high_risk_skill_pct']}%")
    print(f"  Avg hidden families:    {stats['mean_hidden_families']}")
    print()
    print(f"  Samples → {RULEBASED_SAMPLES}")
    print(f"  Stats   → {RULEBASED_STATS}")
    print("="*65)

    # Print 3 examples
    print("\n── Sample Before/After ──\n")
    for ex in results[:3]:
        print(f"Skill: {ex.skill_id}")
        print(f"  Hidden: {ex.hidden_families}")
        print(f"  ORIGINAL ({ex.ic_type_before}, CR={ex.cr_before:.2f}):")
        print(f"    \"{ex.original_desc[:100]}\"")
        print(f"  REVISED (CR={ex.cr_after:.3f}):")
        print(f"    \"{ex.revised_description[:200]}\"")
        print(f"  CONSENT: \"{ex.consent_notice[:120]}\"")
        print()

if __name__ == "__main__":
    main()
