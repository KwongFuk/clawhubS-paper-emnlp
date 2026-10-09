#!/usr/bin/env python3
"""
28_autodesc_generator.py
========================
AutoDesc + CapManifest prototype — CapMismatch companion system.

Reads LSCPS extraction results from merged.sqlite3, calls an LLM to generate
capability-accurate description drafts and CapManifest YAML files.

Usage:
    python 28_autodesc_generator.py --n 100 --ic-filter IC-1 --model gpt-4o-mini
    python 28_autodesc_generator.py --skill-id guard-scanner --model gpt-4o

Outputs:
    results/autodesc_samples.jsonl       — per-skill AutoDesc results
    results/capmanifest_examples/        — CapManifest YAML files
    results/autodesc_cr_stats.json       — CR delta statistics
"""

import argparse
import json
import os
import sqlite3
import time
import yaml
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

# ── Configurable constants ─────────────────────────────────────────────────────
DB_PATH      = Path(__file__).parent.parent / "merged.sqlite3"
RESULTS_DIR  = Path(__file__).parent.parent / "results"
AUTODESC_DIR = RESULTS_DIR / "autodesc"
MANIFEST_DIR = AUTODESC_DIR / "capmanifest_examples"
SAMPLE_FILE  = AUTODESC_DIR / "autodesc_samples.jsonl"
STATS_FILE   = AUTODESC_DIR / "autodesc_cr_stats.json"

# Risk levels by capability family (from RWM weights §7)
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
    "MULTI_STEP":    "LOW",
}

# ── Dataclasses ────────────────────────────────────────────────────────────────
@dataclass
class CapabilityEntry:
    family:        str
    risk_level:    str
    confidence:    float
    evidence_file: str
    evidence_line: int
    evidence_snip: str
    user_consent:  str

@dataclass
class AutoDescResult:
    skill_id:            str
    ic_type_before:      str
    cr_before:           float
    cdi_before:          float
    original_desc:       str
    revised_description: str
    consent_notice:      str
    missing_families:    list
    confidence:          float
    cr_after:            float   # recomputed after applying revised desc
    cdi_after:           float
    model:               str
    generated_at:        str

# ── Prompt builder ─────────────────────────────────────────────────────────────
SYSTEM_PROMPT = """You are a capability disclosure specialist for LLM agent skills.
Your task: generate an accurate, user-friendly marketplace description that
explicitly covers every detected capability.

STRICT RULES:
1. Mention EVERY capability family in the detected list — do not omit any.
2. Do NOT claim capabilities absent from the detected list (no IC-3 generation).
3. Description: 2–4 sentences, plain English for non-technical users.
4. Consent notice: exactly 1 sentence beginning with "By installing this skill,".
5. Output JSON ONLY, no markdown fences."""

def build_prompt(c_act_families: list[str],
                 evidence_snippets: dict,
                 current_desc: str) -> str:
    fam_lines = "\n".join(
        f"  - {f} [{RISK_LEVEL.get(f, 'UNKNOWN')} risk]" for f in c_act_families
    )
    evid_lines = "\n".join(
        f"  {fam}: {snip}" for fam, snip in evidence_snippets.items()
    )
    return f"""{SYSTEM_PROMPT}

DETECTED CAPABILITIES (from LSCPS static analysis):
{fam_lines}

CODE EVIDENCE (representative snippets):
{evid_lines}

EXISTING DESCRIPTION (may be incomplete):
\"\"\"{current_desc}\"\"\"

Generate a JSON object with exactly these keys:
{{
  "revised_description": "<2-4 sentence description mentioning ALL capabilities>",
  "consent_notice": "<1 sentence starting with 'By installing this skill,'>",
  "missing_from_original": ["<family1>", ...],
  "confidence": <0.0-1.0>
}}"""

# ── LLM call ──────────────────────────────────────────────────────────────────
def call_llm(prompt: str, model: str, api_key: Optional[str] = None) -> dict:
    """Call OpenAI-compatible LLM. Reads OPENAI_API_KEY from environment."""
    key = api_key or os.environ.get("OPENAI_API_KEY", "")
    if not key:
        print("[WARN] No API key found. Using mock response.")
        return _mock_response()
    try:
        from openai import OpenAI
        client = OpenAI(api_key=key)
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            response_format={"type": "json_object"},
        )
        return json.loads(resp.choices[0].message.content)
    except ImportError:
        print("[WARN] openai not installed. Using mock response.")
        return _mock_response()
    except Exception as e:
        print(f"[ERROR] LLM call failed: {e}")
        return _mock_response()

def _mock_response() -> dict:
    return {
        "revised_description": (
            "This skill automates task execution and may access system credentials, "
            "execute shell commands, and send data to external endpoints. "
            "It requires explicit user consent for credential access and network operations."
        ),
        "consent_notice": (
            "By installing this skill, you consent to it reading stored credentials, "
            "executing shell commands, and transmitting data externally on your behalf."
        ),
        "missing_from_original": ["CRED_READ", "SHELL_EXEC", "NET_WRITE"],
        "confidence": 0.85,
    }

# ── CR recomputation (simplified) ─────────────────────────────────────────────
FAMILY_KEYWORDS = {
    "CRED_READ":     ["credential", "api key", "token", "password", "secret", "auth"],
    "CRED_TRANSMIT": ["transmit", "exfiltrat", "send credential", "upload secret"],
    "CRED_STORE":    ["store credential", "save token", "persist key"],
    "NET_WRITE":     ["send", "outbound", "external endpoint", "webhook", "post request",
                      "transmit", "upload"],
    "NET_READ":      ["fetch", "download", "http request", "api call"],
    "SHELL_EXEC":    ["shell", "command", "subprocess", "execute", "system command"],
    "FILE_DELETE":   ["delete", "remove", "rm -rf", "unlink"],
    "FILE_READ":     ["read file", "open file", "file access"],
    "AUTONOMOUS":    ["autonomous", "automatic", "without confirmation", "background"],
    "MEMORY_WRITE":  ["memory", "cache", "store data"],
    "MULTI_STEP":    ["pipeline", "multi-step", "workflow"],
}

def recompute_cr_after(revised_desc: str, c_act_families: list[str]) -> float:
    """Simple keyword-based CR recomputation on generated description."""
    desc_lower = revised_desc.lower()
    disclosed = sum(
        1 for f in c_act_families
        if any(kw in desc_lower for kw in FAMILY_KEYWORDS.get(f, [f.lower()]))
    )
    return round(disclosed / len(c_act_families), 3) if c_act_families else 1.0

def recompute_cdi(cr: float, cp: float = 0.95) -> float:
    """CDI = 1 - (CR + CP) / 2. Assume high CP for generated descriptions."""
    return round(1.0 - (cr + cp) / 2.0, 3)

# ── CapManifest writer ─────────────────────────────────────────────────────────
def write_capmanifest(result: AutoDescResult,
                      cap_entries: list[CapabilityEntry],
                      out_path: Path) -> None:
    highest_risk = max(
        (RISK_LEVEL.get(e.family, "LOW") for e in cap_entries),
        key=lambda r: {"HIGH": 3, "MEDIUM": 2, "LOW": 1}.get(r, 0),
        default="LOW",
    )
    manifest = {
        "capmatch_manifest_version": "1.0",
        "skill_id": result.skill_id,
        "generated_at": result.generated_at,
        "generated_by": f"CapMismatch-AutoDesc/{result.model}",
        "capabilities": [
            {
                "family": e.family,
                "risk_level": e.risk_level,
                "confidence": e.confidence,
                "evidence": {
                    "file": e.evidence_file,
                    "line": e.evidence_line,
                    "snippet": e.evidence_snip,
                },
                "user_consent": e.user_consent,
            }
            for e in cap_entries
        ],
        "consent_summary": {
            "risk_tier": highest_risk,
            "requires_explicit_consent": highest_risk in ("HIGH", "MEDIUM"),
            "consent_scope": ", ".join(
                e.family for e in cap_entries
                if e.risk_level in ("HIGH", "MEDIUM")
            ),
        },
        "disclosure_metrics": {
            "cr_before": result.cr_before,
            "cr_after":  result.cr_after,
            "cdi_before": result.cdi_before,
            "cdi_after":  result.cdi_after,
            "ic_type_before": result.ic_type_before,
            "ic_type_after":
                "IC-0" if result.cr_after >= 0.99 else
                "IC-2" if result.cr_after > 0.0  else "IC-1",
        },
        "revised_description": result.revised_description,
        "consent_notice":      result.consent_notice,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        yaml.dump(manifest, f, default_flow_style=False, allow_unicode=True)
    print(f"  CapManifest → {out_path}")

# ── DB queries ────────────────────────────────────────────────────────────────
JSONL_PATH = (Path(__file__).parents[2]
              / "clawhub/results/main_experiment_results.jsonl")


def load_skills(db_path: Path,
                n: int,
                ic_filter: Optional[str],
                skill_id: Optional[str],
                jsonl_path: Optional[Path] = None) -> list[dict]:
    """Load skills preferring JSONL, falling back to SQLite."""

    # ── JSONL path (primary) ──────────────────────────────────────────────
    src = jsonl_path or JSONL_PATH
    if src.exists():
        rows = []
        with open(src) as f:
            for line in f:
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if skill_id and skill_id not in r.get("skill_id", ""):
                    continue
                if ic_filter and r.get("ic_type") != ic_filter:
                    continue
                if r.get("n_actual", 0) == 0:
                    continue
                rows.append(r)
                if skill_id and len(rows) >= 1:
                    break
                if not skill_id and len(rows) >= n:
                    break
        # Sort by CDI desc (highest-risk first)
        rows.sort(key=lambda r: r.get("CDI", 0), reverse=True)
        return rows[:n]

    # ── SQLite fallback ───────────────────────────────────────────────────
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    if skill_id:
        cur.execute(
            "SELECT * FROM skills WHERE id = ? OR name LIKE ? LIMIT 1",
            (skill_id, f"%{skill_id}%"),
        )
    elif ic_filter:
        cur.execute(
            "SELECT * FROM skills WHERE ic_type = ? ORDER BY cdi DESC LIMIT ?",
            (ic_filter, n),
        )
    else:
        cur.execute("SELECT * FROM skills ORDER BY cdi DESC LIMIT ?", (n,))
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def extract_capabilities_from_row(row: dict) -> tuple[list[str], dict, list[CapabilityEntry]]:
    """Parse capabilities from a JSONL row (c_actual) or DB row (hidden_families)."""

    # JSONL format: c_actual is already a list; UD = hidden families
    if "c_actual" in row:
        families = row.get("UD") or row.get("c_actual") or []
        if isinstance(families, str):
            try:
                families = json.loads(families)
            except Exception:
                families = []
    else:
        try:
            families = json.loads(row.get("hidden_families") or "[]")
        except Exception:
            families = []

    # No evidence snippets in JSONL — generate placeholders from sources
    sources = row.get("sources", {}) or {}
    evidence = {}
    for fam in families:
        src_files = sources.get(fam, [])
        if src_files and isinstance(src_files, list) and len(src_files) > 0:
            evidence[fam] = {
                "file": src_files[0].get("file", "code.py") if isinstance(src_files[0], dict) else str(src_files[0]),
                "line": src_files[0].get("line", 0) if isinstance(src_files[0], dict) else 0,
                "snippet": src_files[0].get("snippet", f"[{fam} pattern matched]") if isinstance(src_files[0], dict) else f"[{fam}]",
                "confidence": src_files[0].get("confidence", 0.85) if isinstance(src_files[0], dict) else 0.85,
            }
        else:
            evidence[fam] = {
                "file": "unknown.py", "line": 0,
                "snippet": f"[{fam} detected via LSCPS pattern]",
                "confidence": 0.80,
            }

    consent_map = {
        "CRED_READ":     "Read access to stored credentials and API keys",
        "CRED_TRANSMIT": "Transmit credentials or tokens to external services",
        "CRED_STORE":    "Persist credentials to local storage",
        "NET_WRITE":     "Send data to external network endpoints",
        "NET_READ":      "Fetch data from external network resources",
        "SHELL_EXEC":    "Execute system shell commands",
        "FILE_DELETE":   "Delete or overwrite local files",
        "FILE_READ":     "Read local file contents",
        "FILE_WRITE":    "Write data to local files",
        "AUTONOMOUS":    "Operate autonomously without per-action confirmation",
        "MEMORY_WRITE":  "Write data to persistent memory or cache",
        "MULTI_STEP":    "Execute multi-step automated pipelines",
    }

    entries = []
    for fam in families:
        ev = evidence[fam]
        entries.append(CapabilityEntry(
            family        = fam,
            risk_level    = RISK_LEVEL.get(fam, "LOW"),
            confidence    = float(ev.get("confidence", 0.8)),
            evidence_file = ev["file"],
            evidence_line = int(ev["line"]),
            evidence_snip = ev["snippet"],
            user_consent  = consent_map.get(fam, f"Access related to {fam}"),
        ))
    return families, evidence, entries


# ── Main ──────────────────────────────────────────────────────────────────────
def process_skill(row: dict, model: str) -> Optional[AutoDescResult]:
    # Support both JSONL (skill_id, CR, CDI) and SQLite (id/name, cr, cdi) schemas
    skill_id   = str(row.get("skill_id") or row.get("id") or row.get("name") or "unknown")
    orig_desc  = row.get("description", "") or ""
    cr_before  = float(row.get("CR") or row.get("cr") or 0.0)
    cdi_before = float(row.get("CDI") or row.get("cdi") or 1.0)
    ic_type    = row.get("ic_type", "IC-1")

    families, evidence_raw, cap_entries = extract_capabilities_from_row(row)

    if not families:
        print(f"  [SKIP] {skill_id}: no hidden_families data")
        return None

    # Build evidence snippets for prompt (first match per family)
    evidence_snips = {
        e.family: e.evidence_snip for e in cap_entries
    }

    prompt = build_prompt(families, evidence_snips, orig_desc)
    llm_out = call_llm(prompt, model, api_key=row.get("_api_key"))

    cr_after  = recompute_cr_after(llm_out.get("revised_description", ""), families)
    cdi_after = recompute_cdi(cr_after)

    result = AutoDescResult(
        skill_id            = skill_id,
        ic_type_before      = ic_type,
        cr_before           = cr_before,
        cdi_before          = cdi_before,
        original_desc       = orig_desc,
        revised_description = llm_out.get("revised_description", ""),
        consent_notice      = llm_out.get("consent_notice", ""),
        missing_families    = llm_out.get("missing_from_original", families),
        confidence          = float(llm_out.get("confidence", 0.0)),
        cr_after            = cr_after,
        cdi_after           = cdi_after,
        model               = model,
        generated_at        = datetime.utcnow().isoformat() + "Z",
    )
    return result, cap_entries

def compute_stats(results: list[AutoDescResult]) -> dict:
    if not results:
        return {}
    cr_deltas  = [r.cr_after - r.cr_before for r in results]
    cdi_deltas = [r.cdi_before - r.cdi_after for r in results]
    ic_fixed   = sum(1 for r in results if r.cr_after >= 0.99)
    return {
        "n_processed":        len(results),
        "mean_cr_before":     round(sum(r.cr_before for r in results) / len(results), 3),
        "mean_cr_after":      round(sum(r.cr_after  for r in results) / len(results), 3),
        "mean_cr_delta":      round(sum(cr_deltas)  / len(results), 3),
        "mean_cdi_reduction": round(sum(cdi_deltas) / len(results), 3),
        "ic0_after_pct":      round(ic_fixed / len(results) * 100, 1),
        "mean_confidence":    round(sum(r.confidence for r in results) / len(results), 3),
    }

def main():
    parser = argparse.ArgumentParser(description="AutoDesc + CapManifest generator")
    parser.add_argument("--api-key",   type=str,   default=None,       help="OpenAI API key (overrides OPENAI_API_KEY env var)")
    parser.add_argument("--n",         type=int,   default=50,         help="Number of skills to process")
    parser.add_argument("--ic-filter", type=str,   default="IC-1",     help="Only process IC-1, IC-2, etc.")
    parser.add_argument("--skill-id",  type=str,   default=None,       help="Specific skill ID to process")
    parser.add_argument("--model",     type=str,   default="gpt-4o-mini", help="LLM model name")
    parser.add_argument("--db",        type=str,   default=str(DB_PATH), help="Path to SQLite DB")
    parser.add_argument("--no-manifest", action="store_true",         help="Skip CapManifest YAML generation")
    args = parser.parse_args()

    AUTODESC_DIR.mkdir(parents=True, exist_ok=True)
    MANIFEST_DIR.mkdir(parents=True, exist_ok=True)

    print(f"[AutoDesc] Loading skills from {args.db}")
    print(f"           Model: {args.model} | IC filter: {args.ic_filter} | N: {args.n}")

    rows = load_skills(
        Path(args.db), args.n,
        ic_filter=args.ic_filter,
        skill_id=args.skill_id,
    )
    # Inject api_key into rows so process_skill can access it
    api_key = args.api_key or os.environ.get("OPENAI_API_KEY", "")
    for r in rows:
        r["_api_key"] = api_key

    print(f"[AutoDesc] Loaded {len(rows)} skills")
    print(f"           API key: {'SET (' + api_key[:8] + '...)' if api_key else 'NOT SET — will use mock'}")

    results = []
    with open(SAMPLE_FILE, "w") as out_f:
        for i, row in enumerate(rows):
            skill_id = str(row.get("id", row.get("name", f"skill_{i}")))
            print(f"\n[{i+1}/{len(rows)}] {skill_id}")

            ret = process_skill(row, args.model)
            if ret is None:
                continue

            result, cap_entries = ret
            results.append(result)

            # Write JSONL sample
            out_f.write(json.dumps(asdict(result)) + "\n")
            out_f.flush()

            # Write CapManifest YAML
            if not args.no_manifest:
                manifest_path = MANIFEST_DIR / f"{skill_id}.capmanifest.yaml"
                write_capmanifest(result, cap_entries, manifest_path)

            print(f"  CR: {result.cr_before:.3f} → {result.cr_after:.3f}  "
                  f"| CDI: {result.cdi_before:.3f} → {result.cdi_after:.3f}  "
                  f"| conf: {result.confidence:.2f}")

            time.sleep(0.5)  # rate limit

    # Stats summary
    stats = compute_stats(results)
    with open(STATS_FILE, "w") as f:
        json.dump(stats, f, indent=2)

    print("\n" + "="*60)
    print("AutoDesc Summary:")
    print(f"  Processed:        {stats.get('n_processed', 0)} skills")
    print(f"  Mean CR:          {stats.get('mean_cr_before', 0):.3f} → {stats.get('mean_cr_after', 0):.3f}")
    print(f"  Mean CR delta:    +{stats.get('mean_cr_delta', 0):.3f}")
    print(f"  Mean CDI drop:    -{stats.get('mean_cdi_reduction', 0):.3f}")
    print(f"  IC-0 after (%):   {stats.get('ic0_after_pct', 0)}%")
    print(f"  Samples:          {SAMPLE_FILE}")
    print(f"  CapManifests:     {MANIFEST_DIR}/")
    print(f"  Stats:            {STATS_FILE}")
    print("="*60)

if __name__ == "__main__":
    main()
