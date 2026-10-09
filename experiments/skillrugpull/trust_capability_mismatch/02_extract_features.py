#!/usr/bin/env python3
"""
02_extract_features.py
----------------------
Extract CES, PES, SDS, DGS, RPS features for every skill in the DB.

This operates in "snapshot" mode — each skill's current text content and
permission hints form a single data point. When real version transitions
are available (transitions.csv has rows), the script also runs a diff-based
feature extraction between old and new versions.

Output: data/snapshots_features.csv
"""

from __future__ import annotations

import csv
import json
import math
import re
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
from config import (
    DB_PATH, DATA_DIR, RISK_FAMILIES, CES_WEIGHTS, PES_WEIGHTS,
    DGS_LAMBDAS, VAGUE_DISCLOSURE_PHRASES,
)

OUTPUT_PATH = DATA_DIR / "snapshots_features.csv"

PERMISSION_COLUMNS = list(PES_WEIGHTS.keys())


# ── Text helpers ──────────────────────────────────────────────────────────────

def normalize(text: str | None) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip().lower()


def count_family_hits(text: str, keywords: list[str]) -> int:
    return sum(1 for kw in keywords if kw.lower() in text)


# ── CES: Capability Expansion Score ──────────────────────────────────────────

def compute_ces(skill_text: str, summary_text: str) -> dict[str, float]:
    """
    Compute raw family hit counts and weighted CES from skill_text + summary_text.
    Returns dict with per-family counts + total ces_raw + ces_normalized.
    """
    combined = normalize(skill_text + " " + summary_text)
    family_counts: dict[str, int] = {}
    weighted_sum = 0.0
    for family, keywords in RISK_FAMILIES.items():
        cnt = count_family_hits(combined, keywords)
        family_counts[f"ces_{family}_count"] = cnt
        weighted_sum += CES_WEIGHTS.get(family, 1.0) * min(cnt, 10)  # cap at 10

    # Normalize to [0, 1] — max possible = sum(weight * 10) for all families
    max_possible = sum(w * 10 for w in CES_WEIGHTS.values())
    ces_raw = weighted_sum
    ces_normalized = min(weighted_sum / max_possible, 1.0)
    return {**family_counts, "ces_raw": round(ces_raw, 4), "ces": round(ces_normalized, 4)}


# ── PES: Permission Expansion Score ──────────────────────────────────────────

def compute_pes(hint_row: dict | None) -> dict[str, float]:
    """Compute PES from permission hint flags."""
    if hint_row is None:
        return {f"perm_{col}": 0 for col in PERMISSION_COLUMNS} | {"pes_raw": 0.0, "pes": 0.0}

    weighted_sum = 0.0
    perm_flags: dict[str, int] = {}
    for col in PERMISSION_COLUMNS:
        val = int(hint_row.get(col, 0) or 0)
        perm_flags[f"perm_{col}"] = val
        weighted_sum += PES_WEIGHTS.get(col, 1.0) * val

    max_possible = sum(PES_WEIGHTS.values())
    pes_normalized = min(weighted_sum / max_possible, 1.0)
    return {**perm_flags, "pes_raw": round(weighted_sum, 4), "pes": round(pes_normalized, 4)}


# ── SDS: Semantic Disclosure Score ───────────────────────────────────────────

def compute_sds(description: str, skill_text: str, summary_text: str, scan_status: str) -> dict[str, float]:
    """
    Compute SDS as:
      - risk_coverage: fraction of risk keywords in skill_text also present in description
      - vagueness_penalty: presence of vague phrases in description while skill_text is risky
      - length_adequacy: description length relative to skill_text length (log ratio)
      - scan_bonus: small bonus if scan_status is present (explicitly reviewed)
    """
    desc_norm = normalize(description)
    skill_norm = normalize(skill_text + " " + summary_text)

    # Risk keyword coverage
    all_risk_kws = [kw for kws in RISK_FAMILIES.values() for kw in kws]
    risky_in_skill = [kw for kw in all_risk_kws if kw.lower() in skill_norm]
    risky_in_desc = [kw for kw in risky_in_skill if kw.lower() in desc_norm]

    risk_coverage = (
        len(risky_in_desc) / len(risky_in_skill) if risky_in_skill else 1.0
    )

    # Vagueness penalty
    vague_count = sum(1 for phrase in VAGUE_DISCLOSURE_PHRASES if phrase in desc_norm)
    vagueness_penalty = min(vague_count * 0.1, 0.4)

    # Description length adequacy (log-scaled)
    desc_len = len(desc_norm.split())
    skill_len = max(len(skill_norm.split()), 1)
    length_ratio = desc_len / skill_len
    length_adequacy = min(math.log1p(length_ratio * 10) / math.log1p(10), 1.0)

    # Scan bonus
    scan_bonus = 0.1 if (scan_status and scan_status.lower() not in ("", "none", "unknown")) else 0.0

    sds_raw = (
        0.5 * risk_coverage
        + 0.25 * length_adequacy
        + scan_bonus
        - vagueness_penalty
    )
    sds = max(0.0, min(sds_raw, 1.0))

    return {
        "sds_risk_coverage": round(risk_coverage, 4),
        "sds_vagueness_penalty": round(vagueness_penalty, 4),
        "sds_length_adequacy": round(length_adequacy, 4),
        "sds_scan_bonus": round(scan_bonus, 4),
        "sds": round(sds, 4),
    }


# ── DGS: Disclosure Gap Score ─────────────────────────────────────────────────

def compute_dgs(ces: float, pes: float, sds: float) -> dict[str, float]:
    l1 = DGS_LAMBDAS["lambda_ces"]
    l2 = DGS_LAMBDAS["lambda_pes"]
    l3 = DGS_LAMBDAS["lambda_sds"]
    risk_expansion = (l1 * ces + l2 * pes) / (l1 + l2)  # weighted avg of risk
    dgs = risk_expansion - l3 * sds / (l1 + l2 + l3)
    dgs = max(-1.0, min(dgs, 1.0))
    rps = (risk_expansion + max(0.0, dgs)) / 2.0
    return {
        "risk_expansion": round(risk_expansion, 4),
        "dgs": round(dgs, 4),
        "rps": round(rps, 4),
    }


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    print(f"[02_extract_features] Reading from {DB_PATH}")
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row

    skills = db.execute("SELECT * FROM skills WHERE detail_complete = 1").fetchall()
    print(f"  Processing {len(skills):,} skills...")

    # Build permission hint lookup
    hints_rows = db.execute("SELECT * FROM permission_secret_hints").fetchall()
    hints_map: dict[str, dict] = {r["skill_id"]: dict(r) for r in hints_rows}
    db.close()

    fieldnames: list[str] | None = None
    rows_out: list[dict] = []

    for skill in skills:
        skill_id = skill["skill_id"]
        hint = hints_map.get(skill_id)

        ces_feats = compute_ces(skill["skill_text"] or "", skill["summary_text"] or "")
        pes_feats = compute_pes(hint)
        sds_feats = compute_sds(
            skill["description"] or "",
            skill["skill_text"] or "",
            skill["summary_text"] or "",
            skill["scan_status"] or "",
        )
        dgs_feats = compute_dgs(ces_feats["ces"], pes_feats["pes"], sds_feats["sds"])

        row = {
            "skill_id": skill_id,
            "owner": skill["owner"],
            "repo": skill["repo"],
            "downloads": skill["downloads"] or 0,
            "total_installs": skill["total_installs"] or 0,
            "scan_status": skill["scan_status"] or "",
            **ces_feats,
            **pes_feats,
            **sds_feats,
            **dgs_feats,
        }

        if fieldnames is None:
            fieldnames = list(row.keys())

        rows_out.append(row)

    with open(OUTPUT_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames or [])
        writer.writeheader()
        writer.writerows(rows_out)

    high_ces = sum(1 for r in rows_out if r["ces"] >= 0.3)
    high_pes = sum(1 for r in rows_out if r["pes"] >= 0.3)
    candidates = sum(1 for r in rows_out if r["rps"] >= 0.4 and r["sds"] <= 0.35)
    print(f"  High CES (≥0.3): {high_ces:,} ({100*high_ces/max(len(rows_out),1):.1f}%)")
    print(f"  High PES (≥0.3): {high_pes:,} ({100*high_pes/max(len(rows_out),1):.1f}%)")
    print(f"  SkillRugPull candidates (RPS≥0.4 + SDS≤0.35): {candidates:,}")
    print(f"  Output: {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
