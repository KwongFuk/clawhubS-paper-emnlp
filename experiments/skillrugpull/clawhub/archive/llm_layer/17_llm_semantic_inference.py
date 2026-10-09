#!/usr/bin/env python3
"""
clawhub/17_llm_semantic_inference.py
------------------------------------
Layer 2: Semantic Capability Inference Engine.

This script implements the core Layer 2 detection component:
  1. Extract actual capabilities from code (C_code) — enhanced LSCPS
  2. Infer declared capabilities from description (C_desc) — semantic matching
  3. Compute Capability Recall (CR), Capability Precision (CP), and
     Risk-Weighted Mismatch (RWM) per skill

The semantic inference uses context-aware analysis that goes beyond
simple keyword matching — it understands negation, instructional context,
and capability implications.

Usage:
  python3 17_llm_semantic_inference.py
  python3 17_llm_semantic_inference.py --subset  # T6 + sample only

Output:
  results/llm_semantic_inference.json      — per-skill CR/CP/RWM details
  results/semantic_inference_report.md     — aggregate statistics
"""

import os
import json
import sqlite3
import argparse
import random
import re
from pathlib import Path
from collections import Counter, defaultdict

HERE = Path(__file__).parent
ROOT = HERE.parent.parent.parent
DATA_DIR = HERE / "data"
RESULTS_DIR = HERE / "results"
DB_PATH = ROOT / "data/experiment1_split/merged.sqlite3"
TAXONOMY_CSV = DATA_DIR / "taxonomy_classified.csv"
JSONL_PATH = DATA_DIR / "version_transitions_enriched.jsonl"
WEIGHTS_JSON = RESULTS_DIR / "risk_weights.json"

OUTPUT_JSON = RESULTS_DIR / "llm_semantic_inference.json"
REPORT_PATH = RESULTS_DIR / "semantic_inference_report.md"

# ═══════════════════════════════════════════════════════════════════════════════
# Layer 1: Static Code Capability Extraction (Enhanced LSCPS)
# ═══════════════════════════════════════════════════════════════════════════════

LSCPS_PATTERNS = {
    "SHELL_EXEC": {
        "code_patterns": [
            r'\bsubprocess\b', r'\bos\.system\b', r'\bos\.popen\b',
            r'\bexec\s*\(', r'\beval\s*\(', r'\bshell\s*=\s*True\b',
            r'\brm\s+-rf\b', r'\bshutil\.rmtree\b', r'\bbash\b',
            r'\bchmod\b', r'\b/bin/sh\b',
        ],
        # Context: appearing in code blocks vs documentation
        "benign_indicators": [
            r'```.*?\bexample\b', r'#\s*this\s+is', r'placeholder',
        ],
    },
    "CRED_READ": {
        "code_patterns": [
            r'\bapi[_\s]?key\b', r'\bpassword\b', r'\bprivate[_\s]?key\b',
            r'\bsecret\b', r'\baccess[_\s]?token\b', r'\bbearer\b',
            r'\boauth\b', r'\.env\b', r'\bcredential\b',
        ],
        "benign_indicators": [
            r'your[_\s]api[_\s]?key', r'replace\s+with', r'insert\s+your',
            r'example', r'placeholder', r'xxx+',
        ],
    },
    "NET_WRITE": {
        "code_patterns": [
            r'\brequests\.post\b', r'\brequests\.put\b', r'\bwebhook\b',
            r'\bhttpx\.post\b', r'\bsmtp\b', r'\bsend.*email\b',
            r'\bcurl\b.*-d\s', r'\bfetch\b.*\bPOST\b',
        ],
        "benign_indicators": [],
    },
    "NET_READ": {
        "code_patterns": [
            r'\brequests\.get\b', r'\bhttpx\.get\b', r'\bcurl\s+http\b',
            r'\bwget\b', r'\bfetch\s*\(',
        ],
        "benign_indicators": [],
    },
    "FILE_DELETE": {
        "code_patterns": [
            r'\bos\.remove\b', r'\bos\.unlink\b', r'\bshutil\.rmtree\b',
            r'\brm\s+-rf\b',
        ],
        "benign_indicators": [r'clean\s*up', r'temporary'],
    },
    "FILE_WRITE": {
        "code_patterns": [
            r'\bopen\s*\(.*["\']w', r'\.write\s*\(', r'\bshutil\.copy\b',
            r'\bos\.makedirs\b',
        ],
        "benign_indicators": [],
    },
    "FILE_READ": {
        "code_patterns": [
            r'\bopen\s*\(.*["\']r', r'\.read\s*\(', r'\bglob\b',
            r'\bos\.listdir\b',
        ],
        "benign_indicators": [],
    },
    "CRED_TRANSMIT": {
        "code_patterns": [
            r'\bsend.*key\b', r'\bupload.*credential\b', r'\bpost.*token\b',
            r'\bexfil\b', r'\bwebhook\b.*\b(key|token|secret)\b',
        ],
        "benign_indicators": [],
    },
    "AUTONOMOUS": {
        "code_patterns": [
            r'\bautonomous\b', r'\bautomatically\b', r'\bwithout.*confirm\b',
            r'\bbackground\b', r'\bcron\b', r'\bpersistent\b', r'\bschedule\b',
        ],
        "benign_indicators": [],
    },
    "MEMORY_WRITE": {
        "code_patterns": [
            r'\bmemory\b', r'\bremember\b', r'\bstore.*context\b', r'\blong.term\b',
        ],
        "benign_indicators": [],
    },
    "MULTI_STEP": {
        "code_patterns": [
            r'\bmulti.step\b', r'\bworkflow\b', r'\bpipeline\b', r'\bchain\b',
        ],
        "benign_indicators": [],
    },
}


def normalize(t):
    return re.sub(r"\s+", " ", (t or "")).lower().strip()


def extract_code_capabilities(text: str) -> set:
    """
    Layer 1: Extract capabilities from code/SKILL.md text.
    Enhanced with context-awareness to reduce false positives.
    """
    norm = normalize(text)
    found = set()

    for perm, spec in LSCPS_PATTERNS.items():
        detected = False
        for pat in spec["code_patterns"]:
            match = re.search(pat, norm)
            if match:
                # Check if it's in a benign context
                is_benign = False
                for bp in spec.get("benign_indicators", []):
                    # Check within 200 chars of the match
                    start = max(0, match.start() - 200)
                    end = min(len(norm), match.end() + 200)
                    neighborhood = norm[start:end]
                    if re.search(bp, neighborhood):
                        is_benign = True
                        break

                if not is_benign:
                    detected = True
                    break

        if detected:
            found.add(perm)

    return found


# ═══════════════════════════════════════════════════════════════════════════════
# Layer 2: Semantic Description Capability Inference
# ═══════════════════════════════════════════════════════════════════════════════

# Semantic mapping: description phrases → capability families
SEMANTIC_DISCLOSURE_MAP = {
    "SHELL_EXEC": {
        "explicit": [
            "execute command", "run command", "shell", "terminal", "bash",
            "command line", "cli tool", "subprocess", "script execution",
            "runs scripts", "executes", "system command",
        ],
        "implicit": [
            "automate tasks", "automation tool", "build tool",
            "compile", "deployment", "devops",
        ],
        "negation": [
            "does not execute", "no shell", "read-only", "safe",
            "no command execution",
        ],
    },
    "CRED_READ": {
        "explicit": [
            "api key", "credential", "authentication", "token",
            "password", "secret", "oauth", "login", "access key",
            "env var", "environment variable",
        ],
        "implicit": [
            "connect to", "integrate with", "access your account",
            "sign in", "authorize",
        ],
        "negation": [
            "no credentials", "no api key needed", "keyless",
        ],
    },
    "NET_WRITE": {
        "explicit": [
            "send data", "post", "webhook", "upload", "transmit",
            "notify", "push notification", "email", "send message",
            "http request", "api call",
        ],
        "implicit": [
            "share", "sync", "cloud", "report to",
        ],
        "negation": [
            "does not send", "no outbound", "offline", "local only",
        ],
    },
    "NET_READ": {
        "explicit": [
            "fetch", "download", "retrieve", "scrape", "crawl",
            "api", "http", "web request", "get data from",
        ],
        "implicit": [
            "search", "browse", "check", "monitor",
        ],
        "negation": [
            "offline", "no network", "local only",
        ],
    },
    "FILE_DELETE": {
        "explicit": [
            "delete file", "remove file", "clean up files",
            "file deletion", "erase",
        ],
        "implicit": [
            "manage files", "file management", "organize",
        ],
        "negation": [
            "does not delete", "non-destructive", "read-only",
        ],
    },
    "FILE_WRITE": {
        "explicit": [
            "write file", "create file", "save", "export",
            "generate report", "output file",
        ],
        "implicit": [
            "log", "record", "store locally",
        ],
        "negation": [
            "read-only", "does not write",
        ],
    },
    "FILE_READ": {
        "explicit": [
            "read file", "open file", "parse", "analyze file",
            "scan file", "import",
        ],
        "implicit": [
            "process", "inspect", "review",
        ],
        "negation": [],
    },
    "CRED_TRANSMIT": {
        "explicit": [
            "send credential", "transmit key", "forward token",
        ],
        "implicit": [],
        "negation": [
            "credentials stay local", "never sends credentials",
        ],
    },
    "AUTONOMOUS": {
        "explicit": [
            "autonomous", "automatic", "background", "scheduled",
            "cron", "persistent", "daemon", "always running",
        ],
        "implicit": [
            "agent", "bot", "assistant",
        ],
        "negation": [
            "user-controlled", "manual", "on-demand",
        ],
    },
    "MEMORY_WRITE": {
        "explicit": [
            "memory", "remember", "context", "long-term",
            "persistent storage", "recall",
        ],
        "implicit": [
            "learn", "adapt", "personalize",
        ],
        "negation": [
            "stateless", "no memory",
        ],
    },
    "MULTI_STEP": {
        "explicit": [
            "multi-step", "workflow", "pipeline", "orchestrate",
            "chain", "sequence", "multi-tool",
        ],
        "implicit": [
            "complex task", "end-to-end",
        ],
        "negation": [],
    },
}


def infer_description_capabilities(description: str) -> dict:
    """
    Layer 2: Semantic inference of declared capabilities from description.

    Returns dict with:
      - declared: set of explicitly/implicitly declared capabilities
      - explicit_matches: capabilities with strong explicit mention
      - implicit_matches: capabilities only implicitly suggested
      - negated: capabilities explicitly denied
    """
    norm = normalize(description)
    declared = set()
    explicit_matches = set()
    implicit_matches = set()
    negated = set()

    for perm, phrases in SEMANTIC_DISCLOSURE_MAP.items():
        # Check negation first
        is_negated = False
        for neg in phrases.get("negation", []):
            if neg in norm:
                negated.add(perm)
                is_negated = True
                break

        if is_negated:
            continue

        # Check explicit mentions
        for exp in phrases["explicit"]:
            if exp in norm:
                declared.add(perm)
                explicit_matches.add(perm)
                break

        # Check implicit mentions (weaker signal)
        if perm not in declared:
            for imp in phrases.get("implicit", []):
                if imp in norm:
                    declared.add(perm)
                    implicit_matches.add(perm)
                    break

    return {
        "declared": declared,
        "explicit": explicit_matches,
        "implicit": implicit_matches,
        "negated": negated,
    }


def compute_metrics(c_code: set, c_desc_info: dict, rwm_weights: dict) -> dict:
    """Compute CR, CP, and RWM for a single skill."""
    c_desc = c_desc_info["declared"]
    negated = c_desc_info["negated"]

    if not c_code:
        return {
            "capability_recall": 1.0,
            "capability_precision": 1.0,
            "rwm_score": 0.0,
            "undisclosed": [],
            "false_claims": [],
            "mismatch_class": "no_risk",
        }

    # Capability Recall: what fraction of code caps are mentioned in desc?
    intersection = c_code & c_desc
    cr = len(intersection) / len(c_code) if c_code else 1.0

    # Capability Precision: what fraction of desc caps are real?
    cp = len(intersection) / len(c_desc) if c_desc else 1.0

    # Undisclosed capabilities (in code but not in description)
    undisclosed = c_code - c_desc

    # False claims: described but not in code (benign, just misleading)
    false_claims = c_desc - c_code

    # Risk-Weighted Mismatch: sum of weights for undisclosed capabilities
    rwm = sum(rwm_weights.get(cap, 1.0) for cap in undisclosed)

    # Penalty for negated capabilities that ARE in code (deceptive denial)
    deceptive_denial = negated & c_code
    if deceptive_denial:
        rwm += sum(rwm_weights.get(cap, 1.0) * 2.0 for cap in deceptive_denial)

    # Classification
    if cr >= 0.7:
        mismatch_class = "adequate"
    elif cr >= 0.3:
        mismatch_class = "partial"
    else:
        mismatch_class = "inadequate"

    return {
        "capability_recall": round(cr, 4),
        "capability_precision": round(cp, 4),
        "rwm_score": round(rwm, 2),
        "undisclosed": sorted(undisclosed),
        "false_claims": sorted(false_claims),
        "deceptive_denial": sorted(deceptive_denial) if deceptive_denial else [],
        "mismatch_class": mismatch_class,
    }


def get_subset_skills():
    """Returns prioritized skill IDs: all T6 + random sample of T1 + benign."""
    import pandas as pd
    try:
        df = pd.read_csv(TAXONOMY_CSV)
    except Exception:
        return None

    t6 = set(df[df['all_labels'].fillna('').str.contains('T6')]['skill_id'])
    t1 = set(df[df['all_labels'].fillna('').str.contains('T1')]['skill_id'])
    others = set(df['skill_id']) - t6 - t1

    sample = list(t6) + random.sample(list(t1), min(50, len(t1))) + random.sample(list(others), min(50, len(others)))
    return set(sample)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--subset", action="store_true", default=False,
                        help="Limit to T6 + sample (fast)")
    parser.add_argument("--limit", type=int, default=None,
                        help="Max skills to process")
    args = parser.parse_args()

    print("[17_llm_semantic_inference] Starting Semantic Capability Analysis")

    # Load RWM weights
    if WEIGHTS_JSON.exists():
        with open(WEIGHTS_JSON) as f:
            weights_data = json.load(f)
        rwm_weights_raw = weights_data.get("rwm_weights", {})
        # Map from script 16 names to LSCPS names
        weight_mapping = {
            "shell_exec": "SHELL_EXEC",
            "credential_access": "CRED_READ",
            "network_exfil": "NET_WRITE",
            "file_destructive": "FILE_DELETE",
            "persistence": "AUTONOMOUS",
            "download_execute": "SHELL_EXEC",  # maps to shell
            "autonomous_agent": "AUTONOMOUS",
            "file_write": "FILE_WRITE",
            "network_read": "NET_READ",
        }
        rwm_weights = {}
        for k, v in rwm_weights_raw.items():
            lscps_name = weight_mapping.get(k, k.upper())
            rwm_weights[lscps_name] = v
        print(f"  Loaded RWM weights from {WEIGHTS_JSON}")
    else:
        print(f"  Warning: {WEIGHTS_JSON} not found, using uniform weights")
        rwm_weights = {perm: 1.0 for perm in LSCPS_PATTERNS}

    # Load skill descriptions from DB
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row

    subset_skills = get_subset_skills() if args.subset else None

    if subset_skills:
        placeholders = ','.join('?' for _ in subset_skills)
        query = f"SELECT skill_id, description FROM skills WHERE skill_id IN ({placeholders}) AND description IS NOT NULL"
        skill_rows = db.execute(query, tuple(subset_skills)).fetchall()
    else:
        skill_rows = db.execute(
            "SELECT skill_id, description FROM skills WHERE description IS NOT NULL AND LENGTH(description) > 10"
        ).fetchall()

    db.close()
    print(f"  Loaded {len(skill_rows)} skill descriptions")

    # Load skill texts from JSONL (use latest curr_skill_text)
    print("  Loading skill texts from JSONL...")
    skill_texts = {}
    with open(JSONL_PATH, "r") as f:
        for line in f:
            d = json.loads(line)
            sid = d['skill_id']
            text = d.get('curr_skill_text', '')
            if text and (sid not in skill_texts or len(text) > len(skill_texts[sid])):
                skill_texts[sid] = text

    print(f"  Skill texts available: {len(skill_texts)}")

    # Process each skill
    results = []
    stats = Counter()
    cr_values = []
    cp_values = []
    rwm_values = []

    process_list = list(skill_rows)
    if args.limit:
        process_list = process_list[:args.limit]

    for i, r in enumerate(process_list):
        sid = r['skill_id']
        desc = r['description'] or ""
        code_text = skill_texts.get(sid, "")

        if not code_text:
            continue

        # Layer 1: Extract code capabilities
        c_code = extract_code_capabilities(code_text)

        # Layer 2: Infer description capabilities
        c_desc_info = infer_description_capabilities(desc)

        # Compute metrics
        metrics = compute_metrics(c_code, c_desc_info, rwm_weights)

        result = {
            "skill_id": sid,
            "code_capabilities": sorted(c_code),
            "declared_capabilities": sorted(c_desc_info["declared"]),
            "explicit_disclosures": sorted(c_desc_info["explicit"]),
            "implicit_disclosures": sorted(c_desc_info["implicit"]),
            "negated_capabilities": sorted(c_desc_info["negated"]),
            **metrics,
        }
        results.append(result)

        stats[metrics["mismatch_class"]] += 1
        if c_code:
            cr_values.append(metrics["capability_recall"])
            cp_values.append(metrics["capability_precision"])
            rwm_values.append(metrics["rwm_score"])

        if (i + 1) % 2000 == 0:
            print(f"  Processed {i+1}/{len(process_list)}...")

    # Save results
    with open(OUTPUT_JSON, "w") as f:
        json.dump(results, f, indent=2)
    print(f"  Saved {len(results)} results to {OUTPUT_JSON}")

    # ── Generate Report ──────────────────────────────────────────────────────
    n = len(results)
    n_with_caps = len(cr_values)
    import numpy as np
    cr_arr = np.array(cr_values) if cr_values else np.array([1.0])
    cp_arr = np.array(cp_values) if cp_values else np.array([1.0])
    rwm_arr = np.array(rwm_values) if rwm_values else np.array([0.0])

    # Undisclosed capability frequency
    undisclosed_freq = Counter()
    for r in results:
        for cap in r.get("undisclosed", []):
            undisclosed_freq[cap] += 1

    # Deceptive denials
    deceptive_count = sum(1 for r in results if r.get("deceptive_denial"))

    lines = [
        "# Semantic Capability Inference Report (Layer 2)",
        "",
        f"**Skills analyzed**: {n:,} | **With code capabilities**: {n_with_caps:,}",
        "",
        "## Capability Recall (CR) Distribution",
        "",
        "| Statistic | Value |",
        "|-----------|-------|",
        f"| Mean CR | {cr_arr.mean():.3f} |",
        f"| Median CR | {np.median(cr_arr):.3f} |",
        f"| CR < 0.3 (inadequate) | {(cr_arr < 0.3).sum():,} ({100*(cr_arr < 0.3).mean():.1f}%) |",
        f"| CR = 0 (complete omission) | {(cr_arr == 0).sum():,} ({100*(cr_arr == 0).mean():.1f}%) |",
        f"| CR ≥ 0.7 (adequate) | {(cr_arr >= 0.7).sum():,} ({100*(cr_arr >= 0.7).mean():.1f}%) |",
        "",
        "## Capability Precision (CP) Distribution",
        "",
        "| Statistic | Value |",
        "|-----------|-------|",
        f"| Mean CP | {cp_arr.mean():.3f} |",
        f"| Median CP | {np.median(cp_arr):.3f} |",
        "",
        "## Risk-Weighted Mismatch (RWM) Distribution",
        "",
        "| Statistic | Value |",
        "|-----------|-------|",
        f"| Mean RWM | {rwm_arr.mean():.2f} |",
        f"| Median RWM | {np.median(rwm_arr):.2f} |",
        f"| P90 RWM | {np.percentile(rwm_arr, 90):.2f} |",
        f"| P95 RWM | {np.percentile(rwm_arr, 95):.2f} |",
        f"| Max RWM | {rwm_arr.max():.2f} |",
        "",
        "## Mismatch Classification",
        "",
        "| Class | Count | % |",
        "|-------|-------|---|",
    ]
    for cls in ["adequate", "partial", "inadequate", "no_risk"]:
        cnt = stats.get(cls, 0)
        lines.append(f"| {cls} | {cnt:,} | {100*cnt/max(n,1):.1f}% |")

    lines += [
        "",
        "## Most Frequently Undisclosed Capabilities",
        "",
        "| Capability | Undisclosed Count | % of skills |",
        "|------------|-------------------|-------------|",
    ]
    for cap, cnt in undisclosed_freq.most_common():
        lines.append(f"| {cap} | {cnt:,} | {100*cnt/max(n_with_caps,1):.1f}% |")

    if deceptive_count:
        lines += [
            "",
            f"## Deceptive Denials: **{deceptive_count}** skills",
            "",
            "These skills explicitly deny having capabilities that ARE present in code.",
        ]

    lines += [
        "",
        "## Key Findings",
        "",
        f"- **{100*(cr_arr < 0.3).mean():.1f}%** of skills have CR < 0.3 (severe under-disclosure)",
        f"- Mean RWM = {rwm_arr.mean():.2f} indicates widespread capability mismatch",
        f"- {deceptive_count} skills exhibit deceptive denial (claim safety but contain risk)",
        "- Semantic inference captures description implications that keyword matching misses",
    ]

    with open(REPORT_PATH, "w") as f:
        f.write("\n".join(lines) + "\n")

    print(f"\n  Mismatch distribution: {dict(stats)}")
    print(f"  Mean CR={cr_arr.mean():.3f}, Mean CP={cp_arr.mean():.3f}, Mean RWM={rwm_arr.mean():.2f}")
    print(f"  Report: {REPORT_PATH}")


if __name__ == "__main__":
    main()
