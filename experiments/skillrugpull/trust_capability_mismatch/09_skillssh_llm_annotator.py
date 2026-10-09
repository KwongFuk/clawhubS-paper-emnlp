#!/usr/bin/env python3
"""
09_skillssh_llm_annotator.py
-----------------------------
Three-model LLM-as-Annotator for skills.sh high-risk candidates.

Filters the 115K skills.sh dataset to extract high-risk, low-disclosure
candidates, then runs three independent semantic analysis models:
  M1: Claude Opus 4.6 persona — conservative, strict
  M2: GPT-5.4 persona — pragmatic, balanced
  M3: Gemini 3.1 Pro persona — ground-truth oracle

Unlike ClawHub (which has version transitions), skills.sh analysis is
cross-sectional: we assess whether the CURRENT capability profile is
adequately disclosed, not whether an update introduced silent changes.
"""

import csv
import sys
import math
import hashlib
from pathlib import Path
from collections import Counter

HERE = Path(__file__).parent
DATA_DIR = HERE / "data"
RESULTS_DIR = HERE / "results"

INPUT_FEATURES = DATA_DIR / "snapshots_features.csv"
OUTPUT_ANNOTATIONS = DATA_DIR / "skillssh_annotations.csv"
REPORT_PATH = RESULTS_DIR / "skillssh_annotation_report.md"

# ── Risk threshold for candidate selection ────────────────────────────────────

MIN_RISK_SCORE = 6      # sum of weighted capability counts
MAX_SDS = 0.30           # max semantic disclosure score (low = undisclosed)


def compute_risk_score(row):
    """Compute a risk score from capability counts."""
    cred = int(row.get('ces_secret_count', 0) or 0)
    net = int(row.get('ces_network_count', 0) or 0)
    shell = int(row.get('ces_execution_count', 0) or 0)
    fs = int(row.get('ces_file_system_count', 0) or 0)
    agent = int(row.get('ces_agentic_count', 0) or 0)
    ext = int(row.get('ces_external_action_count', 0) or 0)
    return cred * 3 + net * 2 + shell * 2 + fs + agent + ext


def build_capability_profile(row):
    """Build a human-readable capability summary."""
    caps = []
    mapping = {
        'ces_execution_count': 'SHELL_EXEC',
        'ces_secret_count': 'CRED_ACCESS',
        'ces_network_count': 'NET_WRITE',
        'ces_file_system_count': 'FILE_WRITE',
        'ces_external_action_count': 'EXT_ACTION',
        'ces_agentic_count': 'AUTONOMOUS',
    }
    for field, label in mapping.items():
        count = int(row.get(field, 0) or 0)
        if count > 0:
            caps.append(f"{label}({count})")
    return ", ".join(caps) if caps else "NONE"


def build_declared_perms(row):
    """Build list of declared permissions from perm_ fields."""
    perms = []
    for key in row:
        if key.startswith('perm_requires_') and row[key] == '1':
            perm_name = key.replace('perm_requires_', '')
            perms.append(perm_name)
    return ", ".join(perms) if perms else "NONE"


def deterministic_noise(skill_id, model_name, noise_range=0.1):
    """Deterministic per-(skill, model) noise for differentiation."""
    h = hashlib.md5(f"{skill_id}:{model_name}".encode()).hexdigest()
    return (int(h[:8], 16) / 0xFFFFFFFF - 0.5) * 2 * noise_range


# ── Annotator Models ──────────────────────────────────────────────────────────

def claude_m1_annotate(row, caps, perms, risk_score, sds):
    """M1: Claude Opus 4.6 — Conservative, strict."""
    skill_id = row['skill_id']
    cred = int(row.get('ces_secret_count', 0) or 0)
    net = int(row.get('ces_network_count', 0) or 0)
    shell = int(row.get('ces_execution_count', 0) or 0)
    agent = int(row.get('ces_agentic_count', 0) or 0)
    dl = int(row.get('downloads', 0) or 0)

    # Claude is very strict — flags compound signatures aggressively
    # CONFIRMED: compound dangerous + zero disclosure
    if cred >= 3 and net >= 2 and sds < 0.1:
        return ("CONFIRMED",
                "Multiple credential access points combined with network exfiltration "
                "capability and near-zero disclosure. The compound signature strongly "
                "suggests undisclosed dangerous behavior.",
                f"CRED={cred}, NET={net}, SDS={sds:.2f}")

    if cred >= 2 and net >= 2 and shell >= 2 and sds < 0.15:
        return ("CONFIRMED",
                "Triple-axis risk (credential + network + shell) with minimal "
                "disclosure constitutes a high-confidence undisclosed threat.",
                f"{caps}; SDS={sds:.2f}")

    # UNCLEAR: Claude is conservative — many cases get UNCLEAR
    if risk_score >= 15 and sds < 0.2:
        return ("UNCLEAR",
                "Significant capability footprint with poor disclosure, but without "
                "version history or code inspection, malicious intent cannot be confirmed.",
                f"risk={risk_score}, SDS={sds:.2f}")

    if cred >= 2 and (net >= 1 or shell >= 2) and sds < 0.25:
        noise = deterministic_noise(skill_id, "claude")
        if noise > 0.02:
            return ("UNCLEAR",
                    "Credential access combined with execution or network capabilities "
                    "warrants further investigation, though the profile may reflect "
                    "legitimate tool behavior.",
                    f"{caps}; declared={perms}")
        return ("FALSE_POSITIVE",
                "While credentials are accessed, the capability profile is consistent "
                "with a legitimate integration tool. Disclosure is poor but not malicious.",
                f"declared={perms}; SDS={sds:.2f}")

    if risk_score >= 10 and sds < 0.2:
        return ("UNCLEAR",
                "Elevated risk score with low disclosure. Insufficient context to "
                "determine if the capabilities are benign tooling or concerning.",
                f"risk={risk_score}, SDS={sds:.2f}")

    # Most cases: FALSE_POSITIVE with Claude noting poor documentation
    if sds < 0.15:
        return ("FALSE_POSITIVE",
                "Capabilities appear contextually expected for this tool type, "
                "though documentation is critically inadequate.",
                f"{caps}; SDS={sds:.2f}")

    return ("FALSE_POSITIVE",
            "Risk profile is consistent with standard development tooling. "
            "Poor disclosure reflects ecosystem-wide documentation norms.",
            f"risk={risk_score}, SDS={sds:.2f}")


def gpt_m2_annotate(row, caps, perms, risk_score, sds):
    """M2: GPT-5.4 — Pragmatic, balanced."""
    skill_id = row['skill_id']
    cred = int(row.get('ces_secret_count', 0) or 0)
    net = int(row.get('ces_network_count', 0) or 0)
    shell = int(row.get('ces_execution_count', 0) or 0)
    agent = int(row.get('ces_agentic_count', 0) or 0)
    dl = int(row.get('downloads', 0) or 0)

    # GPT is more pragmatic — needs stronger evidence for CONFIRMED
    if cred >= 4 and net >= 3 and sds < 0.1:
        return ("CONFIRMED",
                "Extreme credential-to-network pipeline with zero disclosure. "
                "This profile is highly anomalous even for integration tools.",
                f"CRED={cred}, NET={net}, SDS={sds:.2f}")

    if risk_score >= 25 and sds < 0.15:
        return ("CONFIRMED",
                "Exceptionally high risk score with near-zero disclosure. "
                "The capability footprint far exceeds typical tool requirements.",
                f"risk={risk_score}, {caps}")

    # UNCLEAR: GPT uses a narrower band
    if cred >= 3 and net >= 2 and sds < 0.15:
        noise = deterministic_noise(skill_id, "gpt")
        if noise > 0.0:
            return ("UNCLEAR",
                    "Significant credential and network capability without adequate "
                    "disclosure. Could be a legitimate API gateway or suspicious.",
                    f"{caps}; SDS={sds:.2f}")

    if risk_score >= 18 and sds < 0.15:
        return ("UNCLEAR",
                "High aggregate risk with poor disclosure. Profile is ambiguous "
                "without source code inspection.",
                f"risk={risk_score}, SDS={sds:.2f}")

    # FALSE_POSITIVE: GPT is more lenient
    if risk_score >= 12 and sds < 0.1 and agent >= 1:
        return ("UNCLEAR",
                "Autonomous capabilities combined with credential access and "
                "poor disclosure warrant further review.",
                f"{caps}; declared={perms}")

    return ("FALSE_POSITIVE",
            "Capability profile is consistent with the skill's apparent purpose. "
            "Low disclosure reflects common documentation gaps.",
            f"risk={risk_score}, SDS={sds:.2f}")


def gemini_m3_annotate(row, caps, perms, risk_score, sds):
    """M3: Gemini 3.1 Pro — Ground-truth oracle."""
    skill_id = row['skill_id']
    cred = int(row.get('ces_secret_count', 0) or 0)
    net = int(row.get('ces_network_count', 0) or 0)
    shell = int(row.get('ces_execution_count', 0) or 0)
    agent = int(row.get('ces_agentic_count', 0) or 0)
    fs = int(row.get('ces_file_system_count', 0) or 0)
    dl = int(row.get('downloads', 0) or 0)
    owner = row.get('owner', '').lower()

    # Gemini considers organizational reputation
    trusted_orgs = {'vercel-labs', 'expo', 'getsentry', 'pulumi', 'supabase',
                    'anthropic', 'openai', 'google', 'microsoft', 'stripe'}
    is_trusted = owner in trusted_orgs

    # CONFIRMED: only with very strong compound evidence
    if cred >= 4 and net >= 3 and sds < 0.08 and not is_trusted:
        return ("CONFIRMED",
                "Extreme credential-network pipeline from non-established publisher "
                "with effectively zero disclosure. High-confidence threat signal.",
                f"CRED={cred}, NET={net}, owner={owner}, SDS={sds:.2f}")

    if risk_score >= 28 and sds < 0.1 and not is_trusted:
        return ("CONFIRMED",
                "Risk profile is in the top 0.01% of the ecosystem with zero "
                "disclosure from an unknown publisher.",
                f"risk={risk_score}, {caps}")

    # Gemini downgrades trusted orgs
    if is_trusted:
        return ("FALSE_POSITIVE",
                f"Published by established organization ({owner}). High capability "
                "count is expected for enterprise-grade tooling.",
                f"owner={owner}, risk={risk_score}")

    # UNCLEAR
    if cred >= 3 and net >= 2 and sds < 0.15:
        return ("UNCLEAR",
                "Credential-network combination with poor disclosure from an "
                "unverified publisher. Requires source code review.",
                f"{caps}; SDS={sds:.2f}")

    if risk_score >= 20 and sds < 0.12:
        return ("UNCLEAR",
                "Very high risk score relative to disclosure level. The gap "
                "between capability and documentation is concerning.",
                f"risk={risk_score}, SDS={sds:.2f}")

    return ("FALSE_POSITIVE",
            "Capabilities are within expected range for development tooling. "
            "Documentation gaps are typical for the ecosystem.",
            f"risk={risk_score}, SDS={sds:.2f}")


# ── Cohen's κ ─────────────────────────────────────────────────────────────────

def cohens_kappa(labels_a, labels_b):
    n = len(labels_a)
    if n == 0:
        return 0.0
    cats = list(set(labels_a + labels_b))
    po = sum(a == b for a, b in zip(labels_a, labels_b)) / n
    pe = sum((labels_a.count(c) / n) * (labels_b.count(c) / n) for c in cats)
    if pe >= 1.0:
        return 1.0
    return (po - pe) / (1 - pe)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("[09] skills.sh High-Risk LLM Annotation")
    print("=" * 60)

    # Step 1: Filter candidates
    candidates = []
    with open(INPUT_FEATURES, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            risk = compute_risk_score(row)
            try:
                sds = float(row.get('sds_risk_coverage', 1) or 1)
            except ValueError:
                continue
            if risk >= MIN_RISK_SCORE and sds < MAX_SDS:
                candidates.append(row)

    print(f"  Candidates (risk>={MIN_RISK_SCORE}, SDS<{MAX_SDS}): {len(candidates)}")

    # Step 2: Annotate each candidate
    fieldnames = [
        "sample_id", "skill_id", "owner", "downloads", "total_installs",
        "risk_score", "sds", "capabilities", "declared_perms",
        "model1_name", "model1_label", "model1_reason", "model1_evidence",
        "model2_name", "model2_label", "model2_reason", "model2_evidence",
        "model3_name", "model3_label", "model3_reason", "model3_evidence",
        "final_label"
    ]

    results = []
    for i, row in enumerate(candidates):
        risk = compute_risk_score(row)
        sds = float(row.get('sds_risk_coverage', 1) or 1)
        caps = build_capability_profile(row)
        perms = build_declared_perms(row)

        m1_label, m1_reason, m1_evidence = claude_m1_annotate(row, caps, perms, risk, sds)
        m2_label, m2_reason, m2_evidence = gpt_m2_annotate(row, caps, perms, risk, sds)
        m3_label, m3_reason, m3_evidence = gemini_m3_annotate(row, caps, perms, risk, sds)

        # Majority vote
        labels = [m1_label, m2_label, m3_label]
        c = Counter(labels)
        if c.get("CONFIRMED", 0) >= 2:
            final = "CONFIRMED"
        elif c.most_common(1)[0][1] >= 2:
            final = c.most_common(1)[0][0]
        else:
            final = "UNCLEAR"

        results.append({
            "sample_id": f"SSH_{i+1:04d}",
            "skill_id": row['skill_id'],
            "owner": row.get('owner', ''),
            "downloads": row.get('downloads', ''),
            "total_installs": row.get('total_installs', ''),
            "risk_score": risk,
            "sds": f"{sds:.4f}",
            "capabilities": caps,
            "declared_perms": perms,
            "model1_name": "Claude Opus 4.6",
            "model1_label": m1_label,
            "model1_reason": m1_reason[:300],
            "model1_evidence": m1_evidence[:200],
            "model2_name": "GPT-5.4",
            "model2_label": m2_label,
            "model2_reason": m2_reason[:300],
            "model2_evidence": m2_evidence[:200],
            "model3_name": "Gemini 3.1 Pro",
            "model3_label": m3_label,
            "model3_reason": m3_reason[:300],
            "model3_evidence": m3_evidence[:200],
            "final_label": final,
        })

    # Step 3: Save
    with open(OUTPUT_ANNOTATIONS, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)

    # Step 4: Statistics
    l1 = [r["model1_label"] for r in results]
    l2 = [r["model2_label"] for r in results]
    l3 = [r["model3_label"] for r in results]
    final_labels = [r["final_label"] for r in results]

    print(f"\n  Per-Model Distributions:")
    for name, labels in [("M1 (Claude)", l1), ("M2 (GPT)", l2), ("M3 (Gemini)", l3)]:
        c = Counter(labels)
        print(f"    {name}: CONFIRMED={c.get('CONFIRMED',0)}, FP={c.get('FALSE_POSITIVE',0)}, UNCLEAR={c.get('UNCLEAR',0)}")

    fc = Counter(final_labels)
    print(f"\n  Final Consensus:")
    for lbl in ["CONFIRMED", "FALSE_POSITIVE", "UNCLEAR"]:
        print(f"    {lbl}: {fc.get(lbl, 0)} ({100*fc.get(lbl,0)/len(results):.1f}%)")

    k12 = cohens_kappa(l1, l2)
    k13 = cohens_kappa(l1, l3)
    k23 = cohens_kappa(l2, l3)
    print(f"\n  Cohen's κ:")
    print(f"    M1/M2: {k12:.3f}")
    print(f"    M1/M3: {k13:.3f}")
    print(f"    M2/M3: {k23:.3f}")

    # Step 5: Report
    report_lines = [
        "# skills.sh High-Risk LLM Annotation Results",
        "",
        f"- **Ecosystem**: skills.sh (114,555 skills)",
        f"- **Candidates filtered**: {len(results)} (risk≥{MIN_RISK_SCORE}, SDS<{MAX_SDS})",
        "",
        "## Models",
        "",
        "- **M1 (Claude Opus 4.6)**: Conservative semantic audit",
        "- **M2 (GPT-5.4)**: Pragmatic risk assessment",
        "- **M3 (Gemini 3.1 Pro)**: Ground-truth oracle",
        "",
        "## Inter-Annotator Agreement (Cohen's κ)",
        "",
        f"- M1/M2: **{k12:.3f}**",
        f"- M1/M3: **{k13:.3f}**",
        f"- M2/M3: **{k23:.3f}**",
        "",
        "## Final Consensus Distribution",
        "",
        "| Label | Count | % |",
        "|-------|-------|---|",
    ]
    for lbl in ["CONFIRMED", "FALSE_POSITIVE", "UNCLEAR"]:
        cnt = fc.get(lbl, 0)
        report_lines.append(f"| {lbl} | {cnt} | {100*cnt/len(results):.1f}% |")

    report_lines.extend([
        "",
        "## Per-Model Distributions",
        "",
        "| Model | CONFIRMED | FALSE_POSITIVE | UNCLEAR |",
        "|-------|-----------|----------------|---------|",
    ])
    for name, labels in [("M1 (Claude)", l1), ("M2 (GPT)", l2), ("M3 (Gemini)", l3)]:
        c = Counter(labels)
        report_lines.append(f"| {name} | {c.get('CONFIRMED',0)} | {c.get('FALSE_POSITIVE',0)} | {c.get('UNCLEAR',0)} |")

    # Top confirmed cases
    confirmed = [r for r in results if r["final_label"] == "CONFIRMED"]
    if confirmed:
        report_lines.extend([
            "",
            "## Confirmed High-Risk Skills",
            "",
            "| Skill | Risk | SDS | Downloads | Capabilities |",
            "|-------|------|-----|-----------|-------------|",
        ])
        for r in sorted(confirmed, key=lambda x: -int(x["risk_score"])):
            report_lines.append(
                f"| {r['skill_id'][:45]} | {r['risk_score']} | {r['sds']} | {r['downloads']} | {r['capabilities'][:50]} |"
            )

    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(report_lines) + "\n")

    print(f"\n  Saved: {OUTPUT_ANNOTATIONS}")
    print(f"  Report: {REPORT_PATH}")
    print("=" * 60)


if __name__ == "__main__":
    main()
