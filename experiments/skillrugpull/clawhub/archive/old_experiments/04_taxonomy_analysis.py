#!/usr/bin/env python3
"""
clawhub/04_taxonomy_analysis.py
--------------------------------
Classify TrustDrift candidates into a rigorous security taxonomy.

Taxonomy (grounded in real ClawHub data):
  T1: Silent Capability Injection
      - Large new content added (text_diff_ratio high), changelog says "docs only"
      - Pattern: skill goes from minimal to full operational guides with shell/exec
      - Example: reclaim-disk-space 1.0.0→1.0.1, changelog="content clarified"
                 but added rm -rf, bash, terminal, password

  T2: Credential Scope Creep
      - New secret-family capabilities (api_key, private_key, token, password)
      - Description scope stays same or narrows
      - Example: agent-wallet 0.1.0→0.2.0 adds subprocess, private key, webhook

  T3: Documentation-Lagged Expansion
      - Genuine feature addition but changelog only describes UI/docs changes
      - Capability risk in code exceeds what changelog mentions
      - Example: gitlab-cli 1.0.4→1.1.0: "expanded documentation" but added
                 bash, shell, terminal, access token, deploy

  T4: Archive/Binary Substitution
      - Previous version was binary/zip, current is functional SKILL.md
      - Transition from opaque bundle to readable code (or vice versa)
      - Forensic significance: earlier version cannot be audited

  T5: Covert Agentic Expansion
      - New autonomous/memory/multi-step keywords appear without disclosure
      - User approved a "tool" (single-call), skill becomes a persistent agent

  T6: Malicious Content Insertion/Removal
      - Changelog explicitly mentions "removed malicious code" or "security fix"
      - High risk: if we can detect the removal, was the insertion version deployed?
      - Example: reclaim-disk-space prev version was a binary bundle

Output: clawhub/data/taxonomy_classified.csv
        clawhub/results/taxonomy_report.md
"""

from __future__ import annotations

import csv
import json
import re
import sys
from pathlib import Path
from collections import Counter

HERE = Path(__file__).parent
DATA_DIR = HERE / "data"
RESULTS_DIR = HERE / "results"
RESULTS_DIR.mkdir(exist_ok=True)

DRIFT_CSV = DATA_DIR / "drift_features.csv"
JSONL = DATA_DIR / "version_transitions_enriched.jsonl"
OUTPUT_CSV = DATA_DIR / "taxonomy_classified.csv"
REPORT_PATH = RESULTS_DIR / "taxonomy_report.md"


def safe_float(v, d=0.0):
    try: return float(v)
    except: return d


def normalize(text):
    return re.sub(r"\s+", " ", (text or "")).lower().strip()


# ── Taxonomy classifiers ──────────────────────────────────────────────────────

def is_binary_prev(prev_text: str) -> bool:
    """Detect if prev version was a binary/zip bundle (non-text)."""
    if not prev_text:
        return True
    # Look for binary markers
    non_ascii = sum(1 for c in prev_text[:200] if ord(c) > 127)
    null_bytes = prev_text[:200].count('\x00')
    return non_ascii > 30 or null_bytes > 5 or prev_text.startswith('PK')


DOCS_ONLY_CHANGELOG = [
    "documentation", "readme", "typo", "spelling", "grammar",
    "formatting", "whitespace", "comment", "clarif", "improve",
    "update doc", "docs update", "doc fix", "minor fix",
    "cosmetic", "style", "cleanup", "reorganiz", "restructur",
    "reformat", "translation", "add example",
]

MALICIOUS_MARKERS = [
    "malicious", "malware", "harmful", "dangerous", "removed exploit",
    "security issue", "removed malicious", "backdoor", "vulnerability fixed",
    "reverted", "hotfix security",
]

TRANSPARENCY_MARKERS = [
    "transparency", "trust review", "added security", "explicit documentation",
    "per clawhub", "review feedback", "security section", "disclose",
]


def classify(row: dict, prev_text: str, curr_text: str) -> list[str]:
    """Return list of taxonomy labels for this transition."""
    labels = []
    cl_norm = normalize(row.get("changelog", ""))
    prev_norm = normalize(prev_text)
    curr_norm = normalize(curr_text)

    ces_drift = safe_float(row.get("ces_drift"))
    sds = safe_float(row.get("sds"), 1.0)
    diff_ratio = safe_float(row.get("text_diff_ratio"))
    new_secret = row.get("new_kw_secret", "")
    new_exec = row.get("new_kw_execution", "")
    new_agentic = row.get("new_kw_agentic", "")
    new_external = row.get("new_kw_external_action", "")

    # T4: Archive/Binary Substitution — check first (overrides others)
    if is_binary_prev(prev_text) and curr_text and len(curr_text) > 100:
        labels.append("T4_archive_substitution")
        return labels  # early return, other labels not meaningful here

    # T6: Malicious Content (explicit markers in changelog)
    if any(m in cl_norm for m in MALICIOUS_MARKERS):
        labels.append("T6_malicious_marker")

    # T3: Transparency-Triggered (author added disclosure due to platform feedback)
    if any(m in cl_norm for m in TRANSPARENCY_MARKERS):
        labels.append("T3b_transparency_triggered")

    # T1: Silent Capability Injection
    # High CES drift + docs-only changelog
    docs_only = any(ph in cl_norm for ph in DOCS_ONLY_CHANGELOG)
    if ces_drift >= 0.15 and sds <= 0.3 and docs_only:
        labels.append("T1_silent_injection")

    # T2: Credential Scope Creep
    if new_secret and ces_drift > 0:
        labels.append("T2_credential_creep")

    # T3: Documentation-Lagged Expansion (real feature, but underdisclosed)
    if ces_drift >= 0.1 and sds <= 0.35 and diff_ratio >= 0.3 and not docs_only:
        labels.append("T3a_doc_lagged")

    # T5: Covert Agentic Expansion
    # NOTE: Both "autonomous" and "memory" checks require new_agentic to be True
    if new_agentic and ("autonomous" in curr_norm or "memory" in curr_norm):
        if "autonomous" not in (normalize(row.get("changelog",""))):
            labels.append("T5_covert_agentic")

    # Generic TrustDrift (doesn't fit neatly above but is still a candidate)
    if not labels and safe_float(row.get("is_candidate")) == 1:
        labels.append("T0_generic_drift")

    return labels if labels else ["benign"]


def main() -> None:
    if not DRIFT_CSV.exists():
        print("ERROR: run 02 first")
        return

    print("[clawhub/04_taxonomy_analysis] Loading data...")
    with open(DRIFT_CSV, newline="", encoding="utf-8") as f:
        rows = {r["pair_id"]: r for r in csv.DictReader(f)}

    # Load full text pairs
    pairs: dict[str, dict] = {}
    with open(JSONL, encoding="utf-8") as f:
        for line in f:
            p = json.loads(line)
            pairs[p["pair_id"]] = p

    classified = []
    label_counts: Counter = Counter()

    for pair_id, row in rows.items():
        pair = pairs.get(pair_id, {})
        prev_text = pair.get("prev_skill_text", "")
        curr_text = pair.get("curr_skill_text", "")
        labels = classify(row, prev_text, curr_text)

        primary_label = labels[0] if labels else "benign"
        all_labels = "|".join(labels)

        for lbl in labels:
            label_counts[lbl] += 1

        out_row = {
            **row,
            "primary_label": primary_label,
            "all_labels": all_labels,
            "description": pair.get("description", "")[:200],
        }
        classified.append(out_row)

    # Write CSV
    if classified:
        with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(classified[0].keys()))
            writer.writeheader()
            writer.writerows(classified)

    total = len(classified)

    # ── Write taxonomy report ──────────────────────────────────────────────

    lines = [
        "# Trust Drift Taxonomy: ClawHub Analysis",
        "",
        f"Total transition pairs analyzed: **{total:,}**",
        f"Pairs with SKILL.md full text: **{len(pairs):,}**",
        "",
        "---",
        "",
        "## Taxonomy Overview",
        "",
        "| ID | Name | Count | % of all pairs | Description |",
        "|----|------|-------|---------------|-------------|",
        f"| T1 | Silent Capability Injection | {label_counts.get('T1_silent_injection',0)} | {100*label_counts.get('T1_silent_injection',0)/max(total,1):.1f}% | High capability expansion masked by docs-only changelog |",
        f"| T2 | Credential Scope Creep | {label_counts.get('T2_credential_creep',0)} | {100*label_counts.get('T2_credential_creep',0)/max(total,1):.1f}% | New secret/token/key handling added without disclosure |",
        f"| T3a | Doc-Lagged Expansion | {label_counts.get('T3a_doc_lagged',0)} | {100*label_counts.get('T3a_doc_lagged',0)/max(total,1):.1f}% | Real feature added, changelog lags behind capability |",
        f"| T3b | Transparency-Triggered | {label_counts.get('T3b_transparency_triggered',0)} | {100*label_counts.get('T3b_transparency_triggered',0)/max(total,1):.1f}% | Platform review forced author to add disclosures |",
        f"| T4 | Archive Substitution | {label_counts.get('T4_archive_substitution',0)} | {100*label_counts.get('T4_archive_substitution',0)/max(total,1):.1f}% | Binary/opaque version → readable SKILL.md (or vice versa) |",
        f"| T5 | Covert Agentic Expansion | {label_counts.get('T5_covert_agentic',0)} | {100*label_counts.get('T5_covert_agentic',0)/max(total,1):.1f}% | Tool becomes autonomous agent without changelog mention |",
        f"| T6 | Malicious Marker | {label_counts.get('T6_malicious_marker',0)} | {100*label_counts.get('T6_malicious_marker',0)/max(total,1):.1f}% | Changelog mentions removing/fixing malicious content |",
        f"| T0 | Generic Drift | {label_counts.get('T0_generic_drift',0)} | {100*label_counts.get('T0_generic_drift',0)/max(total,1):.1f}% | Candidate not fitting specific patterns |",
        "",
        "---",
        "",
        "## Security Impact Analysis Per Type",
        "",
        "### T1: Silent Capability Injection",
        "",
        "> **Threat**: Author adds dangerous operational capability under the guise of documentation updates.",
        "> The marketplace auditor (and user) sees 'fixed typos' but the skill gained shell execution.",
        "",
        "- **Attack vector**: Supply chain — malicious actor publishes docs-only changelog",
        "  to evade automated scanners that skip non-code-changes",
        "- **Real example**: `reclaim-disk-space` gained `rm -rf`, `bash`, `terminal`, `password`",
        "  with changelog `'content and cleanup command description were clarified'`",
        "- **Dangerous because**: Most CI/CD security scanners and marketplace audits",
        "  prioritize 'code changes' — a pure doc-framed update bypasses them",
        "- **CVSS analog**: High — arbitrary command execution, user not notified",
        "",
        "### T2: Credential Scope Creep",
        "",
        "> **Threat**: A skill's version update silently adds handling for secrets,",
        "> API keys, tokens, or private keys beyond what the user originally authorized.",
        "",
        "- **Attack vector**: Confused Deputy — user's approved-context keys are now",
        "  accessible to expanded credential-handling code",
        "- **Real example**: `mdk-agent-wallet` 0.1.0→0.2.0 adds `subprocess`, `private key`, `webhook`",
        "  after user approved a 'Bitcoin Lightning wallet' with no subprocess access",
        "- **Dangerous because**: LLM agents pass available credentials to skill instructions;",
        "  expanded handlers mean new exfiltration surface even without user re-approval",
        "- **CVSS analog**: High — credential exfiltration, privilege escalation",
        "",
        "### T3a: Documentation-Lagged Expansion",
        "",
        "> **Threat**: Author genuinely adds features but changelog summary understates the",
        "> security-relevant changes. Not necessarily malicious, but creates audit gap.",
        "",
        "- **Attack vector**: Unintentional but systematic — authors focus on user-facing",
        "  features, not on security-relevant capability surface in changelog",
        "- **Real example**: `gitlab-cli` 1.0.4→1.1.0: changelog='expanded documentation'",
        "  but added `shell`, `bash`, `terminal`, `access token`, `deploy`",
        "- **Dangerous because**: Platform trust scores rely on changelogs for delta auditing;",
        "  this systematically evades that mechanism even without malicious intent",
        "- **CVSS analog**: Medium — enables privilege escalation via audit gap",
        "",
        "### T3b: Transparency-Triggered Update",
        "",
        "> **Security insight**: Some authors added capability disclosures only AFTER platform",
        "> review feedback. This reveals that WITHOUT the review, these would be T1/T2 cases.",
        "",
        "- **Implication**: Platform-level enforcement works but is reactive, not proactive",
        "- **Research insight**: The existence of this pattern proves the audit mechanism has",
        "  gaps that authors only close when explicitly prompted",
        "- **Real example**: `mdk-agent-wallet` changelog: 'Added security/transparency section",
        "  per ClawHub trust review feedback'",
        "",
        "### T4: Archive/Binary Substitution",
        "",
        "> **Threat**: Early version was an opaque binary bundle; later version is readable",
        "> SKILL.md. The transition makes prior behavior forensically unverifiable.",
        "",
        "- **Attack vector**: Retroactive obfuscation — malicious early version gets replaced",
        "  with clean version; users who installed v1 remain exposed",
        "- **Research insight**: Any skill where early versions are binary bundles",
        "  cannot be audited retrospectively — marketplace has no immutability guarantee",
        "- **CVSS analog**: High — supply chain persistence, forensic evasion",
        "",
        "### T5: Covert Agentic Expansion",
        "",
        "> **Threat**: User approved a deterministic tool (single-call, bounded scope).",
        "> Version update makes it an autonomous agent with memory and multi-step loops.",
        "",
        "- **Attack vector**: Scope creep on the agent abstraction layer",
        "  — violates the principle of minimal delegation in human-AI interaction",
        "- **Dangerous because**: Agent memory + autonomy enables persistent side effects",
        "  (e.g., scheduled actions, stored state) beyond what the user consented to",
        "- **CVSS analog**: High — user consent violation, persistent unauthorized actions",
        "",
        "### T6: Malicious Marker",
        "",
        "> **Critical finding**: Changelog explicitly mentions 'removed malicious code'.",
        "> This means a malicious version existed and was installed by users.",
        "",
        "- **Research significance**: These are confirmed SkillTrustDrift events",
        "  — ground truth for what the automated system should catch BEFORE deployment",
        "- **Implication**: Post-hoc removal is not sufficient; the window of exposure",
        "  is the interval from malicious version publication to revision",
        "",
        "---",
        "",
        "## Cross-Cutting Security Principles",
        "",
        "| Principle | Violated By |",
        "|-----------|------------|",
        "| Principle of Least Privilege | T2, T5 |",
        "| Transparency / Informed Consent | T1, T2, T3a |",
        "| Immutability / Supply Chain Integrity | T4, T6 |",
        "| Minimal Footprint (Agent Design) | T5 |",
        "| Audit Trail Completeness | T1, T3a, T3b |",
        "",
        "---",
        "",
        "## Implications for LLM Skill Marketplace Design",
        "",
        "1. **Changelog-gated deployment**: Updates with CES drift above threshold",
        "   should require re-audit before auto-deployment to existing users",
        "2. **Immutable version history**: Binary/zip bundles should be rejected or",
        "   stored in content-addressed storage with public hash",
        "3. **Re-consent on agentic expansion**: If skill gains `autonomous`, `memory`,",
        "   or `loop` capabilities, active user re-consent should be required",
        "4. **Credential delta audit**: Any new secret-family keyword in new version",
        "   should trigger automated security review",
        "5. **T3b as ground truth**: Platform trust review mechanism works —",
        "   but should be automated rather than manual",
    ]

    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    print(f"  Label distribution:")
    for lbl, cnt in label_counts.most_common():
        print(f"    {lbl}: {cnt} ({100*cnt/max(total,1):.1f}%)")
    print(f"  Taxonomy CSV: {OUTPUT_CSV}")
    print(f"  Report: {REPORT_PATH}")


if __name__ == "__main__":
    main()
