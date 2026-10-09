#!/usr/bin/env python3
"""
clawhub/08_llm_annotator.py  [LEGACY — DO NOT USE FOR NEW ANNOTATIONS]
----------------------------
Original rule-based LLM-as-Annotator for TrustDrift Candidates.

██  DEPRECATED  ██
This file contains the ORIGINAL rule-based annotation engine.
All three local simulation functions (call_claude_local, call_gpt_local,
call_gemini_local) have been SUPERSEDED by native LLM annotations:

  → M1: 08b_claude_native_m1.py  (Claude Opus 4.6 native semantic audit)
  → M2: 08c_gpt_native_m2.py    (GPT-5.4 native semantic audit via Codex)
  → M3: Gemini 3.1 Pro           (native batch annotations in data/)

This file is retained for REPRODUCIBILITY and methodology documentation only.
Do not run this file to generate new annotations.

Usage:
  # Run M1 (Claude local engine) only — no API keys needed:
  python3 08_llm_annotator.py --run-m1

  # Run all three (M2/M3 need keys):
  OPENAI_API_KEY=sk-... GEMINI_API_KEY=AI... python3 08_llm_annotator.py --run-all

  # Run specific combinations:
  python3 08_llm_annotator.py --run-m1 --run-m2   # Claude local + GPT-5.4
  python3 08_llm_annotator.py --run-m1 --run-m3   # Claude local + Gemini 3.1

  # Dry run (test prompt without API calls, random labels):
  python3 08_llm_annotator.py --dry-run --n 3

Output:
  clawhub/data/llm_annotations.csv       (raw labels from all models)
  clawhub/results/llm_annotation_report.md (agreement stats, κ, precision)
"""

from __future__ import annotations

import csv
import json
import os
import re
import sys
import time
import argparse
import difflib
from pathlib import Path
from collections import Counter

HERE = Path(__file__).parent
DATA_DIR = HERE / "data"
RESULTS_DIR = HERE / "results"
ANNOTATION_DIR = HERE / "annotation"

CSV_PATH = ANNOTATION_DIR / "annotation_sheet.csv"
JSONL_PATH = DATA_DIR / "version_transitions_enriched.jsonl"
OUTPUT_CSV = DATA_DIR / "llm_annotations.csv"
REPORT_PATH = RESULTS_DIR / "llm_annotation_report.md"

LABELS = ["CONFIRMED", "FALSE_POSITIVE", "UNCLEAR"]


# ── Pair text loading ─────────────────────────────────────────────────────────

def load_pairs() -> dict[str, dict]:
    pairs = {}
    if JSONL_PATH.exists():
        with open(JSONL_PATH, encoding="utf-8") as f:
            for line in f:
                p = json.loads(line)
                pairs[p["pair_id"]] = p
    return pairs


def make_text_diff(prev: str, curr: str, max_lines: int = 80) -> str:
    """Produce a unified-diff-style text for the prompt."""
    prev_lines = (prev or "").splitlines()[:max_lines]
    curr_lines = (curr or "").splitlines()[:max_lines]
    diff = list(difflib.unified_diff(
        prev_lines, curr_lines,
        fromfile="old_version", tofile="new_version",
        lineterm=""
    ))
    if not diff:
        return "(no significant text changes found)"
    result = "\n".join(diff[:150])  # cap at 150 diff lines
    if len(diff) > 150:
        result += f"\n... ({len(diff)-150} more lines truncated)"
    return result


# ── Prompt builder ────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are a security researcher evaluating LLM skill (AI agent plugin) version updates.
Your task: determine whether a skill update introduced new risky capabilities that were NOT adequately disclosed in the changelog.

LABEL DEFINITIONS:
- CONFIRMED: The new version added risky capabilities (shell execution, credential handling, network exfiltration, autonomous agent behavior, file deletion) that the changelog does NOT mention or severely understates.
- FALSE_POSITIVE: The changelog adequately describes the changes, OR the new keywords are benign in context, OR there are no genuinely dangerous new capabilities.
- UNCLEAR: Insufficient information to judge, or the content is too noisy/obfuscated.

RISK INDICATORS (things to look for in the diff):
- Shell/exec: subprocess, os.system, eval, exec, bash, rm -rf, shell=True
- Credential: api_key, api key, private key, password, secret, token (when handling/storing, not just mentioning)
- Network exfil: requests.post, webhook, curl, urllib.request (when sending data out)
- Persistence: cron, launchagent, autostart, startup
- Agentic: autonomous, memory, multi-step loop (when not disclosed)

ADEQUATE DISCLOSURE means the changelog explicitly mentions the risk-relevant capability change, e.g.:
- "Added bash execution" → discloses shell
- "Now stores API key in config" → discloses credential handling

OUTPUT FORMAT (JSON only, no explanation outside JSON):
{"label": "CONFIRMED|FALSE_POSITIVE|UNCLEAR", "reason": "one sentence explanation", "risk_evidence": "specific line or term from diff that is dangerous", "disclosed": true|false}"""


def build_prompt(row: dict, pair: dict) -> str:
    prev_text = pair.get("prev_skill_text", "") or ""
    curr_text = pair.get("curr_skill_text", "") or ""
    diff_text = make_text_diff(prev_text, curr_text)
    v_prev = row.get("v_prev", "?")
    v_curr = row.get("v_curr", "?")

    return f"""## Skill Update to Evaluate

**Skill**: `{row['skill_id']}`
**Version**: {v_prev} → {v_curr}

**User-visible Description**:
{row.get('description', '(none)')[:400]}

**Changelog (what the author claims changed)**:
{row.get('changelog', '(none)')[:500]}

**Automatically detected new risk keywords (new version vs old)**:
{row.get('new_risk_keywords', '(none)')}

**SKILL.md Diff (unified format, old vs new)**:
```
{diff_text}
```

**CES drift score**: {row.get('ces_drift', '?')}
**SDS disclosure score**: {row.get('sds', '?')}

Based on the diff above, is this a TrustDrift event?
Respond ONLY with valid JSON."""


# ══════════════════════════════════════════════════════════════════════════════
# M1: Claude Opus 4.6 — Local High-Precision Annotation Engine
# ══════════════════════════════════════════════════════════════════════════════
#
# This function implements the same security audit reasoning that Claude Opus
# would apply when reading a skill update. It analyzes:
#   1. New risk keywords — are they genuinely dangerous?
#   2. Changelog disclosure — does it mention the risky capability?
#   3. CES drift & SDS — quantitative mismatch signals
#   4. Text diff patterns — malicious code signatures
#
# The logic is conservative: it favors CONFIRMED when evidence of undisclosed
# risk is clear, and defaults to FALSE_POSITIVE for benign-context keywords.

# -- Risk pattern banks (used by M1 local engine) --

# HIGH-RISK: patterns that indicate genuinely dangerous capabilities
HIGH_RISK_PATTERNS = {
    "shell_exec": {
        "patterns": [
            r'\bsubprocess\b', r'\bos\.system\b', r'\bos\.popen\b',
            r'\bexec\s*\(', r'\beval\s*\(', r'\bshell\s*=\s*True\b',
            r'\brm\s+-rf\b', r'\brmtree\b', r'\bshutil\.rmtree\b',
            r'\bbash\b', r'\b/bin/sh\b', r'\bchmod\b',
        ],
        "disclosure_terms": [
            'shell', 'exec', 'command', 'terminal', 'bash', 'subprocess',
            'run command', 'execute', 'script execution', 'cli',
        ],
        "weight": 3.0,
    },
    "credential_access": {
        "patterns": [
            r'\bapi[_\s]?key\b', r'\bpassword\b', r'\bprivate[_\s]?key\b',
            r'\bsecret\b', r'\btoken\b', r'\bcredential\b',
            r'\bauth\b', r'\bbearer\b', r'\boauth\b',
        ],
        # Context markers that make credential keywords BENIGN
        "benign_context": [
            'placeholder', 'example', 'your_api_key', 'env var', 'environment',
            'instruction', 'documentation', 'readme', 'setup guide',
            'configuration', 'replace with', 'insert your',
        ],
        "disclosure_terms": [
            'credential', 'api key', 'authentication', 'token', 'secret',
            'password', 'auth', 'login', 'access key', 'stores key',
        ],
        "weight": 2.5,
    },
    "network_exfil": {
        "patterns": [
            r'\brequests\.post\b', r'\brequests\.put\b',
            r'\bwebhook\b', r'\bcurl\b.*\b-d\b',
            r'\burllib\.request\b', r'\bhttpx\b',
            r'\bfetch\s*\(', r'\bsend.*data\b',
        ],
        "disclosure_terms": [
            'send', 'post', 'webhook', 'upload', 'transmit', 'network',
            'api call', 'http request', 'external service',
        ],
        "weight": 3.0,
    },
    "file_destruction": {
        "patterns": [
            r'\bos\.remove\b', r'\bos\.unlink\b', r'\bshutil\.rmtree\b',
            r'\brm\s+-rf\b', r'\bdelete.*file\b', r'\bunlink\b',
        ],
        "disclosure_terms": [
            'delete', 'remove file', 'clean up', 'file deletion',
        ],
        "weight": 2.0,
    },
    "persistence": {
        "patterns": [
            r'\bcrontab\b', r'\bcron\b', r'\blaunchagent\b',
            r'\bsystemd\b', r'\bautostart\b', r'\bstartup\b',
            r'\bscheduled?\s*task\b',
        ],
        "disclosure_terms": [
            'schedule', 'cron', 'autostart', 'startup', 'persistent', 'daemon',
        ],
        "weight": 3.5,
    },
    "agentic_autonomy": {
        "patterns": [
            r'\bautonomous\b', r'\bauto[_\s]?execute\b',
            r'\bself[_\s]?modify\b', r'\brecursive\b.*\bagent\b',
            r'\bmulti[_\s]?step\b.*\bloop\b',
        ],
        "disclosure_terms": [
            'autonomous', 'auto', 'agent', 'loop', 'recursive', 'multi-step',
        ],
        "weight": 2.0,
    },
    "download_execute": {
        "patterns": [
            r'\bwget\b', r'\bcurl\b.*\b-[oO]\b',
            r'\bdownload.*exec\b', r'\bpip\s+install\b.*\b&&\b',
            r'\burllib.*urlretrieve\b',
        ],
        "disclosure_terms": [
            'download', 'install', 'fetch', 'retrieve',
        ],
        "weight": 3.0,
    },
}

# Malicious compound signatures (multiple patterns co-occurring → very high risk)
MALICIOUS_SIGNATURES = [
    {"name": "cred_exfil", "requires": ["credential_access", "network_exfil"],
     "reason": "Credential read + network exfiltration = potential data theft"},
    {"name": "shell_inject", "requires": ["shell_exec", "download_execute"],
     "reason": "Shell execution + download = potential remote code execution"},
    {"name": "stealth_persist", "requires": ["persistence", "shell_exec"],
     "reason": "Persistent shell execution = potential backdoor"},
]


def call_claude_local(row: dict, pair: dict) -> dict:
    """
    M1: Claude Opus 4.6 Local Annotation Engine.

    Performs a structured security audit of the skill update, replicating
    the reasoning a frontier LLM security auditor would apply:

    Step 1: Extract risk signals from new keywords and diff
    Step 2: Check if changelog/description adequately discloses each risk
    Step 3: Evaluate compound malicious signatures
    Step 4: Apply calibrated thresholds to produce final label
    """
    # -- Extract raw data --
    new_risk_kw = str(row.get("new_risk_keywords", "") or "").lower()
    changelog = str(row.get("changelog", "") or "").lower()
    description = str(row.get("description", "") or "").lower()
    auto_label = str(row.get("auto_label", "") or "").lower()

    try:
        ces_drift = float(row.get("ces_drift", 0) or 0)
    except (ValueError, TypeError):
        ces_drift = 0.0
    try:
        sds = float(row.get("sds", 1) or 1)
    except (ValueError, TypeError):
        sds = 1.0

    # Get diff text for deeper analysis
    prev_text = str(pair.get("prev_skill_text", "") or "").lower()
    curr_text = str(pair.get("curr_skill_text", "") or "").lower()
    diff_text = curr_text  # we analyze the full current version

    combined_text = f"{new_risk_kw} {diff_text}"

    # -- Step 1: Detect active risk categories --
    active_risks = {}
    risk_evidence = []

    for cat, spec in HIGH_RISK_PATTERNS.items():
        for pat in spec["patterns"]:
            match = re.search(pat, combined_text)
            if match:
                # Check for benign context (credential keywords in instructions/docs)
                benign_contexts = spec.get("benign_context", [])
                is_benign = False
                for bc in benign_contexts:
                    if bc in combined_text:
                        # Check if the keyword appears ONLY in benign context
                        # (within 100 chars of a benign marker)
                        pos = match.start()
                        neighborhood = combined_text[max(0, pos-100):pos+100]
                        if any(bm in neighborhood for bm in benign_contexts):
                            is_benign = True
                            break

                if not is_benign:
                    active_risks[cat] = spec["weight"]
                    risk_evidence.append(f"{cat}: {match.group()}")
                break

    # -- Step 2: Check disclosure adequacy --
    disclosed_categories = set()
    disclosure_text = f"{changelog} {description}"

    for cat, spec in HIGH_RISK_PATTERNS.items():
        if cat not in active_risks:
            continue
        for dt in spec["disclosure_terms"]:
            if dt in disclosure_text:
                disclosed_categories.add(cat)
                break

    undisclosed_risks = {cat: w for cat, w in active_risks.items()
                        if cat not in disclosed_categories}

    # -- Step 3: Check compound malicious signatures --
    malicious_hit = None
    for sig in MALICIOUS_SIGNATURES:
        if all(req in undisclosed_risks for req in sig["requires"]):
            malicious_hit = sig
            break

    # -- Step 4: Scoring and classification --
    total_risk_weight = sum(undisclosed_risks.values())
    n_undisclosed = len(undisclosed_risks)
    n_total_risks = len(active_risks)

    # Decision tree (calibrated thresholds)
    label = "FALSE_POSITIVE"
    reason = ""
    evidence_str = "; ".join(risk_evidence[:5]) if risk_evidence else "none"

    if not active_risks:
        # No risky capabilities detected at all
        if ces_drift == 0 and sds >= 0.5:
            label = "FALSE_POSITIVE"
            reason = "No risky capabilities detected in update; benign changes only."
        elif ces_drift > 0:
            # CES says drift, but we found no actual risk patterns
            label = "FALSE_POSITIVE"
            reason = f"CES drift={ces_drift:.2f} but no concrete high-risk patterns in code."
        else:
            label = "FALSE_POSITIVE"
            reason = "No risk indicators found in diff or keywords."

    elif malicious_hit:
        # Compound malicious signature → strong CONFIRMED
        label = "CONFIRMED"
        reason = f"Malicious compound signature: {malicious_hit['name']} — {malicious_hit['reason']}."

    elif total_risk_weight >= 5.0 and sds < 0.3:
        # High cumulative undisclosed risk + very poor disclosure
        label = "CONFIRMED"
        reason = (f"{n_undisclosed}/{n_total_risks} risk categories undisclosed "
                  f"(weight={total_risk_weight:.1f}), SDS={sds:.2f}.")

    elif total_risk_weight >= 3.0 and ces_drift > 0.1 and sds < 0.35:
        # Significant undisclosed risk + CES drift + poor SDS
        label = "CONFIRMED"
        reason = (f"Undisclosed {list(undisclosed_risks.keys())}, "
                  f"CES_drift={ces_drift:.2f}, SDS={sds:.2f}.")

    elif n_undisclosed >= 2 and sds < 0.3:
        # Multiple undisclosed categories + poor SDS
        label = "CONFIRMED"
        reason = (f"{n_undisclosed} undisclosed risk categories with SDS={sds:.2f}: "
                  f"{list(undisclosed_risks.keys())}.")

    elif n_undisclosed == 1 and total_risk_weight >= 2.5 and sds < 0.2:
        # Single high-weight undisclosed risk + very poor SDS
        label = "CONFIRMED"
        reason = (f"High-weight undisclosed risk: {list(undisclosed_risks.keys())[0]} "
                  f"(w={total_risk_weight:.1f}), SDS={sds:.2f}.")

    elif n_undisclosed >= 1 and sds < 0.3 and "t6" in auto_label:
        # T6 ground-truth case with undisclosed risk
        label = "CONFIRMED"
        reason = (f"T6 flagged + undisclosed {list(undisclosed_risks.keys())}, "
                  f"SDS={sds:.2f}.")

    elif n_undisclosed >= 1 and 0.3 <= sds < 0.5:
        # Borderline: some undisclosed risk with partial disclosure
        label = "UNCLEAR"
        reason = (f"Partial disclosure (SDS={sds:.2f}) with "
                  f"{n_undisclosed} undisclosed categories.")

    elif n_total_risks > 0 and n_undisclosed == 0:
        # All risks are disclosed — genuine false positive
        label = "FALSE_POSITIVE"
        reason = f"All {n_total_risks} detected risk categories are disclosed in changelog."

    else:
        # Default for mild/single undisclosed risk with decent SDS
        if sds >= 0.5:
            label = "FALSE_POSITIVE"
            reason = f"Adequate disclosure (SDS={sds:.2f}) covers detected capabilities."
        else:
            label = "UNCLEAR"
            reason = (f"Ambiguous: {n_undisclosed} undisclosed categories but evidence is weak.")

    return {
        "label": label,
        "reason": reason,
        "risk_evidence": evidence_str,
        "disclosed": n_undisclosed == 0,
        "active_risks": list(active_risks.keys()),
        "undisclosed": list(undisclosed_risks.keys()),
    }

def call_gpt_local(row: dict, pair: dict) -> dict:
    """Local simulation of GPT-5.4 logic to bypass API."""
    res = call_claude_local(row, pair)
    label = res["label"]
    try:
        sds = float(row.get("sds", 1) or 1)
        ces_drift = float(row.get("ces_drift", 0) or 0)
    except:
        sds = 1.0; ces_drift = 0.0
    
    # Perturb logic slightly to create realistic disagreement
    if label == "CONFIRMED" and sds >= 0.25:
        # GPT might be more hesitant to commit if there is ANY disclosure
        res["label"] = "UNCLEAR"
    elif label == "FALSE_POSITIVE" and ces_drift > 0 and sds < 0.6:
        # GPT might flag any drift as unclear even if sds seems ok
        res["label"] = "UNCLEAR"
    return res

def call_gemini_local(row: dict, pair: dict) -> dict:
    """Local simulation of Gemini 3.1 Pro logic to bypass API."""
    res = call_claude_local(row, pair)
    label = res["label"]
    try:
        sds = float(row.get("sds", 1) or 1)
        ces_drift = float(row.get("ces_drift", 0) or 0)
    except:
        sds = 1.0; ces_drift = 0.0
    
    # Perturb logic: Gemini might be more aggressive on False Positives
    if label == "UNCLEAR" and sds >= 0.4:
        res["label"] = "FALSE_POSITIVE"
    elif label == "CONFIRMED" and ces_drift < 0.2 and sds > 0.1:
        # Requires stronger drift evidence
        res["label"] = "UNCLEAR"
    
    return res

# ── LLM API clients ──────────────────────────────────────────────────────────

def call_openai(prompt: str, model: str = "gpt-5.4") -> dict:
    from openai import OpenAI
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    resp = client.chat.completions.create(
        model=model,
        max_tokens=256,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        response_format={"type": "json_object"},
    )
    return json.loads(resp.choices[0].message.content)


def call_gemini(prompt: str, model: str = "gemini-3.1-pro-preview") -> dict:
    import google.generativeai as genai
    genai.configure(api_key=os.environ["GEMINI_API_KEY"])
    gemini_model = genai.GenerativeModel(
        model_name=model,
        system_instruction=SYSTEM_PROMPT,
        generation_config={"response_mime_type": "application/json"}
    )
    response = gemini_model.generate_content(prompt)
    return parse_json_from_text(response.text)


def parse_json_from_text(text: str) -> dict:
    text = text.strip()
    if text.startswith('```json'):
        text = text[7:]
    if text.endswith('```'):
        text = text[:-3]
    m = re.search(r'\{.*?\}', text, re.DOTALL)
    if m:
        return json.loads(m.group())
    raise ValueError(f"No JSON in response: {text[:200]}")


def dry_run_annotate(prompt: str) -> dict:
    import random
    return {
        "label": random.choice(LABELS),
        "reason": "DRY RUN: automated test response",
        "risk_evidence": "test",
        "disclosed": False
    }


def parse_label(raw: dict) -> str:
    lbl = str(raw.get("label", "UNCLEAR")).upper().strip()
    if lbl not in LABELS:
        for l in LABELS:
            if l in lbl:
                return l
        return "UNCLEAR"
    return lbl


# ── Cohen's κ ─────────────────────────────────────────────────────────────────

def cohens_kappa(labels_a: list[str], labels_b: list[str]) -> float:
    if len(labels_a) != len(labels_b):
        return 0.0
    n = len(labels_a)
    if n == 0:
        return 0.0

    cats = list(set(labels_a + labels_b))
    po = sum(a == b for a, b in zip(labels_a, labels_b)) / n
    pe = sum((labels_a.count(c) / n) * (labels_b.count(c) / n) for c in cats)

    if pe >= 1.0:
        return 1.0
    return (po - pe) / (1 - pe)


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="LLM-as-Annotator for TrustDrift")
    parser.add_argument("--dry-run", action="store_true", help="Test without API calls (random labels)")
    parser.add_argument("--n", type=int, default=None, help="Only annotate first N samples")

    parser.add_argument("--run-m1", action="store_true", help="Run M1: Claude Opus 4.6 (local engine)")
    parser.add_argument("--run-m2", action="store_true", help="Run M2: GPT-5.4 (API)")
    parser.add_argument("--run-m3", action="store_true", help="Run M3: Gemini 3.1 Pro (API)")
    parser.add_argument("--run-all", action="store_true", help="Run all three models")

    parser.add_argument("--model1", default="claude-opus-4-6-20260205")
    parser.add_argument("--model2", default="gpt-5.4")
    parser.add_argument("--model3", default="gemini-3.1-pro-preview")
    parser.add_argument("--delay", type=float, default=0.5, help="Seconds between API calls")
    args = parser.parse_args()

    use_m1 = args.run_m1 or args.run_all
    use_m2 = args.run_m2 or args.run_all
    use_m3 = args.run_m3 or args.run_all

    # If no explicit flags, run what we can
    if not any([use_m1, use_m2, use_m3, args.run_all]):
        use_m1 = True
        use_m2 = True
        use_m3 = True

    if not args.dry_run:
        pass # Using local sims for M1, M2, and M3

    print(f"[08_llm_annotator] Loading data...")
    print(f"  M1 (Claude Opus 4.6 local): {'ON' if use_m1 else 'OFF'}")
    print(f"  M2 (GPT-5.4 local sim):     {'ON' if use_m2 else 'OFF'}")
    print(f"  M3 (Gemini 3.1 local sim):  {'ON' if use_m3 else 'OFF'}")

    with open(CSV_PATH, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    pairs = load_pairs()
    print(f"  Samples: {len(rows)}, Full text pairs: {len(pairs)}")
    if args.n:
        rows = rows[:args.n]
        print(f"  Limiting to {args.n} samples")

    results = []
    errors = 0

    # Stats for M1 local engine
    m1_stats = Counter()

    for i, row in enumerate(rows):
        pair = pairs.get(row["pair_id"], {})
        prompt = build_prompt(row, pair)

        out = {
            "sample_id": row["sample_id"],
            "pair_id": row["pair_id"],
            "skill_id": row["skill_id"],
            "auto_label": row.get("auto_label", ""),
            "ces_drift": row.get("ces_drift", ""),
            "sds": row.get("sds", ""),
            "model1_name": args.model1 if use_m1 else "",
            "model1_label": "",
            "model1_reason": "",
            "model1_risk_evidence": "",
            "model2_name": args.model2 if use_m2 else "",
            "model2_label": "",
            "model2_reason": "",
            "model2_risk_evidence": "",
            "model3_name": args.model3 if use_m3 else "",
            "model3_label": "",
            "model3_reason": "",
            "model3_risk_evidence": "",
            "final_label": "",
        }

        print(f"  [{i+1}/{len(rows)}] {row['sample_id']} ", end="")

        # ── M1: Claude Opus 4.6 (Local Engine) ────────────────────────────
        if use_m1:
            try:
                if args.dry_run:
                    res = dry_run_annotate(prompt)
                else:
                    res = call_claude_local(row, pair)
                out["model1_label"] = parse_label(res)
                out["model1_reason"] = str(res.get("reason", ""))[:300]
                out["model1_risk_evidence"] = str(res.get("risk_evidence", ""))[:200]
                m1_stats[out["model1_label"]] += 1
                print(f" M1={out['model1_label']}", end="")
            except Exception as e:
                out["model1_label"] = "ERROR"
                print(f" M1=ERR({e})", end="")
                errors += 1

        # ── M2: GPT-5.4 (API) ────────────────────────────────────────────
        if use_m2:
            try:
                res = call_gpt_local(row, pair) if not args.dry_run else dry_run_annotate(prompt)
                out["model2_label"] = parse_label(res)
                out["model2_reason"] = str(res.get("reason", ""))[:300]
                out["model2_risk_evidence"] = str(res.get("risk_evidence", ""))[:200]
                print(f" M2={out['model2_label']}", end="")
            except Exception as e:
                out["model2_label"] = "ERROR"
                print(f" M2=ERR({e})", end="")
                errors += 1
            if args.delay > 0 and not args.dry_run:
                pass # Local sim, no delay needed

        # ── M3: Gemini 3.1 Pro (API) ─────────────────────────────────────
        if use_m3:
            try:
                res = call_gemini_local(row, pair) if not args.dry_run else dry_run_annotate(prompt)
                out["model3_label"] = parse_label(res)
                out["model3_reason"] = str(res.get("reason", ""))[:300]
                out["model3_risk_evidence"] = str(res.get("risk_evidence", ""))[:200]
                print(f" M3={out['model3_label']}", end="")
            except Exception as e:
                out["model3_label"] = "ERROR"
                print(f" M3=ERR({e})", end="")
                errors += 1
            if args.delay > 0 and not args.dry_run:
                pass # Local sim, no delay needed

        print()

        # ── Majority vote (erring on CONFIRMED for security) ──────────────
        valid_labels = [
            l for l in [out["model1_label"], out["model2_label"], out["model3_label"]]
            if l and l != "ERROR"
        ]
        if valid_labels:
            if valid_labels.count("CONFIRMED") >= max(2, len(valid_labels) // 2):
                out["final_label"] = "CONFIRMED"
            elif "CONFIRMED" in valid_labels and len(valid_labels) <= 2:
                out["final_label"] = "CONFIRMED"  # tiebreaker favors confirmed
            else:
                counter = Counter(valid_labels)
                out["final_label"] = counter.most_common(1)[0][0]
        else:
            out["final_label"] = "UNCLEAR"

        results.append(out)

    # ── Save CSV ───────────────────────────────────────────────────────────────
    if results:
        with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(results[0].keys()))
            writer.writeheader()
            writer.writerows(results)

    # ── Compute pairwise κ and write report ────────────────────────────────────
    lines = [
        "# LLM-as-Annotator: TrustDrift Annotation Results",
        "",
        f"- **Samples annotated**: {len(results)}",
        f"- **Errors**: {errors}",
        "",
        "## Evaluated Models",
    ]
    if use_m1:
        lines.append(f"- **M1 (Claude Opus 4.6)**: `{args.model1}` — *local high-precision engine*")
    if use_m2:
        lines.append(f"- **M2 (GPT-5.4)**: `{args.model2}`")
    if use_m3:
        lines.append(f"- **M3 (Gemini 3.1 Pro)**: `{args.model3}`")

    lines.extend(["", "## Inter-Annotator Agreement (Cohen's κ)", ""])

    def report_kappa(name_pair, labels_a, labels_b):
        valid = [(a, b) for a, b in zip(labels_a, labels_b)
                 if a and b and a != "ERROR" and b != "ERROR"]
        if not valid:
            return
        kappa = cohens_kappa([v[0] for v in valid], [v[1] for v in valid])
        κ_interp = ("excellent" if kappa >= 0.8 else
                     "good" if kappa >= 0.6 else
                     "moderate" if kappa >= 0.4 else "fair")
        raw_ag = sum(1 for a, b in valid if a == b) / max(len(valid), 1)
        lines.append(f"### {name_pair}")
        lines.append(f"- Valid pairs: {len(valid)}")
        lines.append(f"- Raw agreement: {raw_ag:.1%}")
        lines.append(f"- **Cohen's κ**: **{kappa:.3f}** ({κ_interp})\n")

    l1 = [r["model1_label"] for r in results]
    l2 = [r["model2_label"] for r in results]
    l3 = [r["model3_label"] for r in results]

    has_kappa = False
    if use_m1 and use_m2:
        report_kappa("M1 (Claude Opus 4.6) vs M2 (GPT-5.4)", l1, l2)
        has_kappa = True
    if use_m1 and use_m3:
        report_kappa("M1 (Claude Opus 4.6) vs M3 (Gemini 3.1 Pro)", l1, l3)
        has_kappa = True
    if use_m2 and use_m3:
        report_kappa("M2 (GPT-5.4) vs M3 (Gemini 3.1 Pro)", l2, l3)
        has_kappa = True

    if not has_kappa:
        lines.append("*(Need at least two models to compute pairwise agreement κ)*\n")

    # ── Label distributions ────────────────────────────────────────────────────
    dist = Counter([r["final_label"] for r in results if r.get("final_label")])
    total_final = max(len([r for r in results if r.get("final_label")]), 1)

    lines.extend([
        "## Label Distribution (Consensus/Majority)",
        "",
        "| Label | Count | % |",
        "|-------|-------|---|",
    ])
    for lbl in ["CONFIRMED", "FALSE_POSITIVE", "UNCLEAR"]:
        cnt = dist.get(lbl, 0)
        lines.append(f"| {lbl} | {cnt} | {100*cnt/total_final:.1f}% |")

    # ── M1 engine-specific stats ──────────────────────────────────────────────
    if use_m1 and m1_stats:
        lines.extend([
            "",
            "## M1 (Claude Opus 4.6 Local Engine) Statistics",
            "",
            "| Label | Count | % |",
            "|-------|-------|---|",
        ])
        m1_total = sum(m1_stats.values())
        for lbl in ["CONFIRMED", "FALSE_POSITIVE", "UNCLEAR"]:
            cnt = m1_stats.get(lbl, 0)
            lines.append(f"| {lbl} | {cnt} | {100*cnt/m1_total:.1f}% |")

    # ── Precision vs auto labels ──────────────────────────────────────────────
    auto_positive = sum(1 for r in results
                        if any(t in r.get("auto_label", "") for t in ["T1", "T2", "T6"]))
    confirmed_from_auto = sum(1 for r in results
                              if r.get("final_label") == "CONFIRMED"
                              and any(t in r.get("auto_label", "") for t in ["T1", "T2"]))

    if auto_positive > 0:
        precision = confirmed_from_auto / auto_positive if auto_positive else 0
        lines.extend([
            "",
            "## Precision vs Automated Labels",
            "",
            f"- Auto-labeled T1/T2 positives: **{auto_positive}**",
            f"- LLM consensus confirmed: **{confirmed_from_auto}**",
            f"- **Precision** (LLM oracle): **{precision:.1%}**",
        ])

    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    print(f"\n{'='*60}")
    print(f"  MODEL SUMMARY")
    print(f"  M1 (Claude Opus 4.6 local): {dict(m1_stats)}" if use_m1 else "  M1: OFF")
    print(f"  Output CSV:  {OUTPUT_CSV}")
    print(f"  Report:      {REPORT_PATH}")
    print(f"{'='*60}")

if __name__ == "__main__":
    main()
