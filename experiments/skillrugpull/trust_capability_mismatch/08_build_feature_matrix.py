#!/usr/bin/env python3
"""
08_build_feature_matrix.py
--------------------------
Assemble the unified feature matrix for detector training.
Merges: snapshots_features + semantic_drift + disclosure_adequacy.

Output: data/feature_matrix.csv  (one row per skill, all features + label)
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
from config import DATA_DIR, RISK_EXPANSION_THRESHOLD, DISCLOSURE_THRESHOLD

FEATURES_PATH = DATA_DIR / "snapshots_features.csv"
DRIFT_PATH = DATA_DIR / "semantic_drift.csv"
DISCLOSURE_PATH = DATA_DIR / "disclosure_adequacy.csv"
OUTPUT_PATH = DATA_DIR / "feature_matrix.csv"

# Feature groups for ablation
EXPANSION_FEATURES = [
    "ces", "pes", "ces_raw", "pes_raw",
    "ces_execution_count", "ces_secret_count", "ces_network_count",
    "ces_file_system_count", "ces_external_action_count", "ces_agentic_count",
    "perm_requires_shell_access", "perm_requires_token", "perm_requires_api_key",
    "perm_requires_oauth", "perm_requires_browser_access", "perm_requires_github",
    "perm_requires_email", "perm_requires_local_file_access",
    "perm_requires_docs", "perm_requires_pdf", "perm_requires_slack",
    "perm_requires_notion", "perm_requires_calendar",
]

DISCLOSURE_FEATURES = [
    "sds", "sds_risk_coverage", "sds_vagueness_penalty",
    "sds_length_adequacy", "sds_scan_bonus",
    "disclosure_adequacy", "n_risky_facts", "n_disclosed_facts",
    "fact_coverage", "marketing_phrase_count",
    "explicit_risk_disclosure_count", "vague_changelog_count",
    "vague_mismatch_score",
]

SEMANTIC_FEATURES = [
    "semantic_gap", "semantic_similarity",
    "desc_coverage_ratio", "log_coverage",
    "risk_divergence_overall", "risk_kw_in_skill", "risk_kw_undisclosed",
    "div_execution", "div_secret", "div_network",
    "div_file_system", "div_external_action", "div_agentic",
]

CONTEXT_FEATURES = [
    "downloads", "total_installs",
    "scan_status_present",  # binarized from scan_status
]


def load_csv(path: Path) -> dict[str, dict]:
    if not path.exists():
        print(f"  MISSING: {path}")
        return {}
    with open(path, newline="", encoding="utf-8") as f:
        return {r["skill_id"]: r for r in csv.DictReader(f)}


def binarize_scan_status(val: str) -> int:
    return 1 if val and val.strip().lower() not in ("", "none", "unknown") else 0


def safe_float(val, default: float = 0.0) -> float:
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def safe_int(val, default: int = 0) -> int:
    try:
        return int(float(val))
    except (TypeError, ValueError):
        return default


def compute_label(risk_expansion: float, sds: float, disclosure_adequacy: float) -> int:
    """Binary SkillRugPull label: 1 = candidate, 0 = non-candidate."""
    is_high_risk = risk_expansion >= RISK_EXPANSION_THRESHOLD
    is_low_disclosure = sds <= DISCLOSURE_THRESHOLD or disclosure_adequacy <= DISCLOSURE_THRESHOLD
    return 1 if (is_high_risk and is_low_disclosure) else 0


def main() -> None:
    print("[08_build_feature_matrix] Loading feature files...")
    feats = load_csv(FEATURES_PATH)
    drift = load_csv(DRIFT_PATH)
    discl = load_csv(DISCLOSURE_PATH)

    if not feats:
        print("  ERROR: Run 02_extract_features.py first")
        return

    all_ids = list(feats.keys())
    print(f"  Building feature matrix for {len(all_ids):,} skills...")

    ALL_FEATURES = EXPANSION_FEATURES + DISCLOSURE_FEATURES + SEMANTIC_FEATURES + CONTEXT_FEATURES

    fieldnames = ["skill_id", "owner", "repo"] + ALL_FEATURES + [
        "risk_expansion", "dgs", "rps", "label", "label_soft"
    ]

    rows_out: list[dict] = []
    n_pos = 0

    for sid in all_ids:
        f = feats[sid]
        d = drift.get(sid, {})
        dl = discl.get(sid, {})

        risk_exp = safe_float(f.get("risk_expansion"))
        sds_val = safe_float(f.get("sds"), 1.0)
        discl_val = safe_float(dl.get("disclosure_adequacy"), 1.0)
        label = compute_label(risk_exp, sds_val, discl_val)
        label_soft = round(safe_float(f.get("rps")), 4)

        n_pos += label

        row: dict = {
            "skill_id": sid,
            "owner": f.get("owner", ""),
            "repo": f.get("repo", ""),
        }

        for feat in EXPANSION_FEATURES:
            row[feat] = safe_float(f.get(feat))

        for feat in DISCLOSURE_FEATURES:
            if feat in f:
                row[feat] = safe_float(f.get(feat))
            elif feat in dl:
                row[feat] = safe_float(dl.get(feat))
            else:
                row[feat] = 0.0  

        for feat in SEMANTIC_FEATURES:
            row[feat] = safe_float(d.get(feat))

        row["downloads"] = safe_float(f.get("downloads"))
        row["total_installs"] = safe_float(f.get("total_installs"))
        row["scan_status_present"] = binarize_scan_status(f.get("scan_status", ""))

        row["risk_expansion"] = risk_exp
        row["dgs"] = safe_float(f.get("dgs"))
        row["rps"] = label_soft
        row["label"] = label
        row["label_soft"] = label_soft

        rows_out.append(row)

    with open(OUTPUT_PATH, "w", newline="", encoding="utf-8") as f_out:
        writer = csv.DictWriter(f_out, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows_out)

    print(f"  Features: {len(ALL_FEATURES)} | Skills: {len(rows_out):,}")
    print(f"  Positive labels (SkillRugPull candidates): {n_pos:,} ({100*n_pos/max(len(rows_out),1):.1f}%)")
    print(f"  Feature groups: {len(EXPANSION_FEATURES)} expansion, {len(DISCLOSURE_FEATURES)} disclosure, {len(SEMANTIC_FEATURES)} semantic, {len(CONTEXT_FEATURES)} context")
    print(f"  Output: {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
