#!/usr/bin/env python3
"""
annotation/sampling.py
----------------------
Stratified sampling of 500 skills for human annotation.

Strata:
  1. low_risk_low_gap     (100): low RPS, high SDS — expected negatives
  2. high_risk_high_exp   (150): high CES+PES, high RPS — likely positives
  3. high_rps_candidates  (150): top RPS candidates
  4. uncertain            (100): mid-range RPS, borderline

Requires: data/feature_matrix.csv

Output: annotation/annotation_sample_500.csv
"""

from __future__ import annotations

import csv
import random
import re
import sys
from pathlib import Path

HERE = Path(__file__).parent.parent
sys.path.insert(0, str(HERE))
from config import DATA_DIR, ANNOTATION_DIR, ANNOTATION_STRATA, RISK_FAMILIES

MATRIX_PATH = DATA_DIR / "feature_matrix.csv"
OUTPUT_PATH = ANNOTATION_DIR / "annotation_sample_500.csv"
ANNOTATION_DIR.mkdir(exist_ok=True)


def safe_float(val, default: float = 0.0) -> float:
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def assign_stratum(row: dict) -> str:
    rps = safe_float(row.get("rps"))
    risk_exp = safe_float(row.get("risk_expansion"))
    sds = safe_float(row.get("sds"), 1.0)
    ces = safe_float(row.get("ces"))
    pes = safe_float(row.get("pes"))

    if rps >= 0.55:
        return "high_rps_candidates"
    elif risk_exp >= 0.4 and (ces >= 0.3 or pes >= 0.3):
        return "high_risk_high_exp"
    elif rps <= 0.15 and sds >= 0.5:
        return "low_risk_low_gap"
    else:
        return "uncertain"


def main() -> None:
    print("[annotation/sampling] Loading feature matrix...")
    if not MATRIX_PATH.exists():
        print("  ERROR: Run 08_build_feature_matrix.py first")
        return

    with open(MATRIX_PATH, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    print(f"  Loaded {len(rows):,} skills")
    random.seed(42)

    # Assign strata — keys must match values returned by assign_stratum()
    STRATUM_KEYS = ["low_risk_low_gap", "high_risk_high_exp", "high_rps_candidates", "uncertain"]
    strata: dict[str, list[dict]] = {k: [] for k in STRATUM_KEYS}
    for row in rows:
        s = assign_stratum(row)
        strata[s].append(row)

    # Sample
    sampled: list[dict] = []
    target_map = {
        "low_risk_low_gap": "low_risk_low_gap",
        "high_risk_high_exp": "high_risk_high_exp",
        "high_rps_candidates": "high_rps_candidates",
        "uncertain": "uncertain",
    }
    targets = {
        "low_risk_low_gap": 100,
        "high_risk_high_exp": 150,
        "high_rps_candidates": 150,
        "uncertain": 100,
    }

    for stratum_key, n in targets.items():
        pool = strata[stratum_key]
        if len(pool) >= n:
            chosen = random.sample(pool, n)
        else:
            chosen = pool  # use all if not enough
            print(f"  WARNING: {stratum_key} has only {len(pool)} items, needed {n}")
        for row in chosen:
            row["stratum"] = stratum_key
        sampled.extend(chosen)

    random.shuffle(sampled)
    print(f"  Total sampled: {len(sampled)}")
    for k in targets:
        cnt = sum(1 for r in sampled if r.get("stratum") == k)
        print(f"    {k}: {cnt}")

    # Write output
    if sampled:
        out_fields = [
            "skill_id", "owner", "repo", "stratum",
            "rps", "risk_expansion", "sds", "dgs",
            "ces", "pes", "disclosure_adequacy",
            "sds_risk_coverage", "vague_mismatch_score",
            "perm_requires_shell_access", "perm_requires_token", "perm_requires_api_key",
            "perm_requires_browser_access", "perm_requires_github",
            "perm_requires_email", "perm_requires_local_file_access",
            "ces_execution_count", "ces_secret_count", "ces_network_count",
            "semantic_gap", "risk_divergence_overall",
            "label",
            # Annotation columns (to be filled by annotators)
            "label_risk_expansion", "label_disclosure", "label_rugpull",
            "annotator_notes",
        ]
        # Fill empty annotation columns
        for row in sampled:
            row.setdefault("label_risk_expansion", "")
            row.setdefault("label_disclosure", "")
            row.setdefault("label_rugpull", "")
            row.setdefault("annotator_notes", "")

        with open(OUTPUT_PATH, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=out_fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(sampled)

    print(f"  Output: {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
