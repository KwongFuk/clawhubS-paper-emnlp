#!/usr/bin/env python3
"""
clawhub/02_extract_drift_features.py
--------------------------------------
Extract version drift features from ClawHub transition pairs.
For each (v_prev, v_curr) pair:

  1. CES_drift   = CES(curr) - CES(prev)  [capability expansion in skill_text]
  2. PES_drift   = PES(curr) - PES(prev)  [permission expansion]
  3. SDS         = disclosure score for changelog vs drift
  4. changelog_vague = penalty for vague changelog ("minor fixes", "bug fix", etc.)
  5. text_diff_ratio = fraction of curr_skill_text that is new vs prev
  6. risk_kw_added   = new risk keywords appearing in curr not in prev (per family)

Requires: clawhub/data/version_transitions_enriched.jsonl (from 01)

Output: clawhub/data/drift_features.csv
"""

from __future__ import annotations

import csv
import json
import re
import sys
from pathlib import Path
from collections import Counter

HERE = Path(__file__).parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE.parent))
from config import RISK_FAMILIES, VAGUE_DISCLOSURE_PHRASES

DATA_DIR = HERE / "data"
INPUT_JSONL = DATA_DIR / "version_transitions_enriched.jsonl"
OUTPUT_CSV = DATA_DIR / "drift_features.csv"

# Weights for CES families
FAMILY_WEIGHTS = {
    "execution": 1.0,
    "secret": 0.9,
    "network": 0.7,
    "file_system": 0.6,
    "external_action": 0.5,
    "agentic": 0.8,
}


def normalize(text: str | None) -> str:
    return re.sub(r"\s+", " ", (text or "")).lower().strip()


def compute_ces(text: str) -> dict[str, float]:
    """Compute weighted CES per family."""
    norm = normalize(text)
    scores = {}
    for family, keywords in RISK_FAMILIES.items():
        hits = sum(1 for kw in keywords if kw in norm)
        scores[family] = round(hits * FAMILY_WEIGHTS.get(family, 0.5), 3)
    scores["total"] = round(sum(scores.values()) / max(len(RISK_FAMILIES), 1), 4)
    return scores


def compute_diff(prev_text: str, curr_text: str) -> dict[str, list[str]]:
    """Return new risk keywords in curr not present in prev, per family."""
    prev_norm = normalize(prev_text)
    curr_norm = normalize(curr_text)
    new_kws: dict[str, list[str]] = {}
    for family, keywords in RISK_FAMILIES.items():
        prev_hits = {kw for kw in keywords if kw in prev_norm}
        curr_hits = {kw for kw in keywords if kw in curr_norm}
        new_in_curr = sorted(curr_hits - prev_hits)
        removed = sorted(prev_hits - curr_hits)
        if new_in_curr:
            new_kws[family] = new_in_curr
    return new_kws


def changelog_score(changelog: str, new_risk_kws: dict) -> dict[str, float]:
    """
    SDS for version transition:
    - Does the changelog mention the new risk-relevant capabilities?
    - Is the changelog vague?
    """
    cl_norm = normalize(changelog)
    cl_words = len(cl_norm.split())

    total_new = sum(len(v) for v in new_risk_kws.values())
    if total_new == 0:
        # No new risk capabilities → changelog adequacy is trivially 1.0
        return {
            "sds": 1.0,
            "changelog_vague": 0.0,
            "changelog_word_count": cl_words,
            "new_risk_kw_count": 0,
            "disclosed_risk_kw_count": 0,
        }

    # How many new risk keywords are mentioned in changelog?
    all_new_kws = [kw for kws in new_risk_kws.values() for kw in kws]
    disclosed = sum(1 for kw in all_new_kws if kw in cl_norm)

    coverage = disclosed / max(total_new, 1)

    # Vagueness penalty
    vague_count = sum(1 for ph in VAGUE_DISCLOSURE_PHRASES if ph in cl_norm)
    vague_penalty = min(vague_count * 0.15, 0.5)

    # Length bonus
    length_bonus = min(cl_words / 50.0, 0.2)

    sds = max(0.0, min(coverage * 0.7 + length_bonus - vague_penalty, 1.0))

    return {
        "sds": round(sds, 4),
        "changelog_vague": round(vague_penalty, 4),
        "changelog_word_count": cl_words,
        "new_risk_kw_count": total_new,
        "disclosed_risk_kw_count": disclosed,
    }


def text_diff_ratio(prev: str, curr: str) -> float:
    """Jaccard-based new content ratio."""
    prev_tokens = set(normalize(prev).split())
    curr_tokens = set(normalize(curr).split())
    new_tokens = curr_tokens - prev_tokens
    return round(len(new_tokens) / max(len(curr_tokens), 1), 4)


def compute_rugpull_score(ces_drift: float, sds: float) -> float:
    """
    RPS for a transition pair:
    High CES drift + Low SDS = high RugPull risk.
    """
    return round(max(0.0, min(ces_drift * (1.0 - sds), 1.0)), 4)


def main() -> None:
    if not INPUT_JSONL.exists():
        print(f"ERROR: {INPUT_JSONL} not found — run 01_build_version_transitions.py first")
        return

    print(f"[clawhub/02_extract_drift_features] Reading {INPUT_JSONL}")
    rows_out: list[dict] = []
    fieldnames = None

    with open(INPUT_JSONL, encoding="utf-8") as f:
        lines = f.readlines()

    print(f"  Processing {len(lines):,} transition pairs...")

    for line in lines:
        pair = json.loads(line)
        pair_id = pair["pair_id"]
        prev_text = pair.get("prev_skill_text", "")
        curr_text = pair.get("curr_skill_text", "")
        changelog = pair.get("changelog", "")
        description = pair.get("description", "")

        ces_prev = compute_ces(prev_text)
        ces_curr = compute_ces(curr_text)

        ces_drift = round(ces_curr["total"] - ces_prev["total"], 4)
        new_kws = compute_diff(prev_text, curr_text)
        removed_kws = compute_diff(curr_text, prev_text)  # removed = prev-curr

        cl_scores = changelog_score(changelog, new_kws)
        diff_ratio = text_diff_ratio(prev_text, curr_text)

        rps = compute_rugpull_score(max(ces_drift, 0), cl_scores["sds"])
        is_candidate = int(ces_drift >= 0.1 and cl_scores["sds"] <= 0.35)

        # Flatten new_kws per family
        family_new_kw_cols = {
            f"new_kw_{family}": ",".join(new_kws.get(family, [])[:5])
            for family in RISK_FAMILIES
        }
        family_ces_drift_cols = {
            f"ces_drift_{family}": round(ces_curr.get(family, 0) - ces_prev.get(family, 0), 3)
            for family in RISK_FAMILIES
        }

        row = {
            "pair_id": pair_id,
            "skill_id": pair["skill_id"],
            "prev_version": pair.get("prev_version", ""),
            "curr_version": pair.get("curr_version", ""),
            "time_delta_days": pair.get("time_delta_days", ""),
            "changelog": changelog[:200],  # truncated for CSV
            "changelog_word_count": cl_scores["changelog_word_count"],
            "changelog_vague": cl_scores["changelog_vague"],
            "ces_prev": ces_prev["total"],
            "ces_curr": ces_curr["total"],
            "ces_drift": ces_drift,
            **family_ces_drift_cols,
            "new_risk_kw_count": cl_scores["new_risk_kw_count"],
            "disclosed_risk_kw_count": cl_scores["disclosed_risk_kw_count"],
            "sds": cl_scores["sds"],
            "text_diff_ratio": diff_ratio,
            "rps": rps,
            "is_candidate": is_candidate,
            **family_new_kw_cols,
        }

        if fieldnames is None:
            fieldnames = list(row.keys())
        rows_out.append(row)

    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames or [])
        writer.writeheader()
        writer.writerows(rows_out)

    # Summary stats
    n_candidates = sum(1 for r in rows_out if r["is_candidate"])
    n_drift_pos = sum(1 for r in rows_out if float(r["ces_drift"]) > 0)
    n_vague = sum(1 for r in rows_out if float(r["changelog_vague"]) > 0.3)

    print(f"  Total pairs: {len(rows_out):,}")
    print(f"  Positive CES drift: {n_drift_pos:,} ({100*n_drift_pos/max(len(rows_out),1):.1f}%)")
    print(f"  Vague changelog: {n_vague:,} ({100*n_vague/max(len(rows_out),1):.1f}%)")
    print(f"  SkillTrustDrift candidates (ces_drift≥0.1 + sds≤0.35): {n_candidates:,} ({100*n_candidates/max(len(rows_out),1):.1f}%)")
    print(f"  Output: {OUTPUT_CSV}")


if __name__ == "__main__":
    main()
