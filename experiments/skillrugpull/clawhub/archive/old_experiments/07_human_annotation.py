#!/usr/bin/env python3
"""
clawhub/07_human_annotation.py
--------------------------------
Generate human annotation sheets for T1/T2 precision measurement.

Samples:
  - 50 T1_silent_injection candidates
  - 50 T2_credential_creep candidates
  - 30 benign controls (randomly sampled from is_candidate=0)

For each, provide annotators with:
  - skill description
  - prev_version changelog
  - highlighted new risk keywords by family
  - key text diff excerpt
  - 3-choice annotation: CONFIRMED / FALSE_POSITIVE / UNCLEAR

Output: clawhub/annotation/annotation_sheet.csv
        clawhub/annotation/annotator_instructions.md
"""
from __future__ import annotations

import csv
import json
import random
from pathlib import Path

HERE = Path(__file__).parent
DATA_DIR = HERE / "data"
ANNOTATION_DIR = HERE / "annotation"
ANNOTATION_DIR.mkdir(exist_ok=True)

TAXONOMY_CSV = DATA_DIR / "taxonomy_classified.csv"
JSONL = DATA_DIR / "version_transitions_enriched.jsonl"

OUTPUT_CSV = ANNOTATION_DIR / "annotation_sheet.csv"
INSTRUCTIONS_PATH = ANNOTATION_DIR / "annotator_instructions.md"

SAMPLE_PER_CLASS = {
    "T1_silent_injection": 50,
    "T2_credential_creep": 50,
    "T3a_doc_lagged": 30,
    "benign": 30,
}

random.seed(42)


def main():
    print("[clawhub/07_human_annotation] Loading taxonomy...")
    with open(TAXONOMY_CSV, newline="", encoding="utf-8") as f:
        all_rows = list(csv.DictReader(f))

    print(f"  Total rows: {len(all_rows)}")

    # Load full text pairs
    pairs = {}
    with open(JSONL, encoding="utf-8") as f:
        for line in f:
            p = json.loads(line)
            pairs[p["pair_id"]] = p

    # Stratified sample
    sampled = []
    for label, n in SAMPLE_PER_CLASS.items():
        if label == "benign":
            pool = [r for r in all_rows if r.get("primary_label") == "benign"
                    and float(r.get("ces_drift", 0) or 0) == 0]
        else:
            pool = [r for r in all_rows if label in r.get("all_labels", "")]

        selected = random.sample(pool, min(n, len(pool)))
        for r in selected:
            r["annotation_class"] = label
        sampled.extend(selected)
        print(f"  {label}: sampled {len(selected)}/{len(pool)}")

    random.shuffle(sampled)  # Blind annotators to class

    # Build annotation rows
    annotation_rows = []
    for idx, row in enumerate(sampled):
        pair = pairs.get(row["pair_id"], {})
        prev_text = pair.get("prev_skill_text", "")
        curr_text = pair.get("curr_skill_text", "")

        # Text diff excerpt
        prev_words = set(prev_text.lower().split())
        curr_words = set(curr_text.lower().split())
        new_words = sorted(curr_words - prev_words)
        diff_sample = " | ".join(new_words[:30])

        # Risk keywords new this version
        new_kws = "; ".join(
            f"{fam}: {row.get(f'new_kw_{fam}','')}"
            for fam in ["execution", "secret", "network", "file_system", "agentic"]
            if row.get(f"new_kw_{fam}", "")
        )

        annotation_rows.append({
            "sample_id": f"SAMPLE_{idx+1:03d}",
            "pair_id": row["pair_id"],
            "skill_id": row["skill_id"],
            "v_prev": row.get("v_prev", ""),
            "v_curr": row.get("v_curr", ""),
            "description": (pair.get("description", "") or row.get("description", ""))[:300],
            "changelog": (pair.get("changelog", "") or row.get("changelog", ""))[:300],
            "new_risk_keywords": new_kws[:200],
            "new_content_sample": diff_sample[:300],
            "ces_drift": row.get("ces_drift", ""),
            "sds": row.get("sds", ""),
            "auto_label": row.get("all_labels", ""),
            # Columns for annotators to fill
            "annotator_1_label": "",   # CONFIRMED / FALSE_POSITIVE / UNCLEAR
            "annotator_1_reason": "",
            "annotator_2_label": "",
            "annotator_2_reason": "",
            # Hidden from annotators during annotation (for analysis):
            "_annotation_class": row.get("annotation_class", ""),
        })

    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(annotation_rows[0].keys()))
        writer.writeheader()
        writer.writerows(annotation_rows)

    print(f"  Annotation sheet: {OUTPUT_CSV} ({len(annotation_rows)} rows)")

    # Write instructions
    instructions = """# TrustDrift Annotation Instructions

## Your Task

You are reviewing version updates to LLM "skills" (AI agent plugins).
For each sample, determine whether the version update introduced new risky capabilities
that were **NOT adequately disclosed** in the changelog.

## Fields You Will See

- **skill_id**: The skill identifier (owner/repo format)
- **description**: What the skill claims to do (user-visible)
- **changelog**: What the author wrote about this version update
- **new_risk_keywords**: Keywords related to risky capabilities that appeared in the NEW version
  but were NOT in the previous version (grouped by category)
- **new_content_sample**: A sample of words present in the new version but not the old
- **ces_drift**: Automated measure of capability expansion (higher = more expansion)
- **sds**: Automated disclosure score (lower = less disclosure in changelog)

## Label Definitions

| Label | When to Use |
|-------|-------------|
| **CONFIRMED** | The new version added risky capabilities (shell/exec/credential/network/agentic) that the changelog does NOT mention or understates clearly |
| **FALSE_POSITIVE** | The changelog adequately describes the changes, OR the new keywords are benign in context |
| **UNCLEAR** | Not enough information to determine, or the skill text is too technical to assess |

## Decision Guide

Ask yourself:
1. Do the `new_risk_keywords` represent genuinely dangerous capabilities? (e.g., `subprocess`, `rm -rf`, `private key`)
2. Does the `changelog` mention these capabilities clearly?
   - If YES → FALSE_POSITIVE
   - If NO/vague → CONFIRMED
3. Is the description still accurate given the new capabilities?

## Examples

**CONFIRMED example**:
- new_risk_keywords: `execution: bash, subprocess | secret: api_key, private key`
- changelog: "Updated documentation and fixed typos"
→ Risky capabilities added, changelog says nothing → CONFIRMED

**FALSE_POSITIVE example**:
- new_risk_keywords: `execution: run`
- changelog: "Added bash command to run the server process (see docs)"
→ Changelog explicitly mentions the bash usage → FALSE_POSITIVE

## Important Notes

- You are NOT judging whether the skill itself is malicious
- You are judging whether the VERSION UPDATE was adequately disclosed
- When in doubt, mark UNCLEAR rather than guessing
- Provide a 1-sentence reason for every decision

## Cohen's κ Target: ≥ 0.70

Both annotators should independently label all samples.
Agreement will be measured using Cohen's κ.
"""
    with open(INSTRUCTIONS_PATH, "w", encoding="utf-8") as f:
        f.write(instructions)

    print(f"  Instructions: {INSTRUCTIONS_PATH}")
    print(f"\n  Summary by class:")
    from collections import Counter
    cls_counts = Counter(r["_annotation_class"] for r in annotation_rows)
    for cls, cnt in cls_counts.most_common():
        print(f"    {cls}: {cnt}")


if __name__ == "__main__":
    main()
