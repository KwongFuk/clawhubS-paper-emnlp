#!/usr/bin/env python3
"""
03_semantic_drift.py
--------------------
Measure within-skill semantic gap between the *description* (short, user-visible)
and the *skill_text* (full operational SKILL.md content).

High gap = description understates what the skill actually does → potential for
user/gatekeeper to miss risky capabilities.

This is a proxy for the TOCTOU (Time-of-Check vs Time-of-Use) semantic risk:
"what the user approved" vs "what the skill actually does".

Output: data/semantic_drift.csv
"""

from __future__ import annotations

import csv
import math
import re
import sqlite3
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
from config import DB_PATH, DATA_DIR, RISK_FAMILIES

OUTPUT_PATH = DATA_DIR / "semantic_drift.csv"


def normalize(text: str | None) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip().lower()


def tokenize(text: str) -> list[str]:
    return re.findall(r"\b[a-z][a-z0-9_\-]{1,30}\b", text)


STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "in", "to", "for", "is", "it",
    "that", "this", "with", "you", "your", "by", "as", "are", "can", "be",
    "at", "from", "on", "have", "has", "not", "will", "also", "when", "how",
    "what", "which", "all", "more", "use", "used", "using", "new", "do", "get",
    "set", "any", "its", "if", "one", "than", "up", "so", "then", "into",
    "out", "about", "but", "our", "their", "they", "we", "my", "am", "me",
}


def tfidf_vector(text: str, idf: dict[str, float]) -> dict[str, float]:
    tokens = [t for t in tokenize(normalize(text)) if t not in STOPWORDS]
    tf = Counter(tokens)
    total = max(sum(tf.values()), 1)
    vec: dict[str, float] = {}
    for tok, count in tf.items():
        if tok in idf:
            vec[tok] = (count / total) * idf[tok]
    return vec


def cosine_similarity(a: dict[str, float], b: dict[str, float]) -> float:
    common = set(a.keys()) & set(b.keys())
    if not common:
        return 0.0
    dot = sum(a[k] * b[k] for k in common)
    norm_a = math.sqrt(sum(v ** 2 for v in a.values()))
    norm_b = math.sqrt(sum(v ** 2 for v in b.values()))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def build_idf(corpus: list[str]) -> dict[str, float]:
    """Compute IDF over list of texts."""
    N = len(corpus)
    df: Counter = Counter()
    for text in corpus:
        tokens = set(t for t in tokenize(normalize(text)) if t not in STOPWORDS)
        df.update(tokens)
    return {tok: math.log((N + 1) / (count + 1)) + 1 for tok, count in df.items()}


def compute_risk_divergence(description: str, skill_text: str) -> dict[str, float]:
    """
    For each risk family: compute fraction of family keywords present in skill_text
    but absent from description. High divergence = risky capabilities not disclosed.
    """
    desc_norm = normalize(description)
    skill_norm = normalize(skill_text)
    family_divergence: dict[str, float] = {}
    total_risk_in_skill = 0
    total_undisclosed = 0
    for family, keywords in RISK_FAMILIES.items():
        in_skill = [kw for kw in keywords if kw in skill_norm]
        in_desc = [kw for kw in in_skill if kw in desc_norm]
        n_in_skill = len(in_skill)
        n_undisclosed = n_in_skill - len(in_desc)
        div = n_undisclosed / max(n_in_skill, 1)
        family_divergence[f"div_{family}"] = round(div, 4)
        total_risk_in_skill += n_in_skill
        total_undisclosed += n_undisclosed

    overall_risk_divergence = total_undisclosed / max(total_risk_in_skill, 1)
    family_divergence["risk_divergence_overall"] = round(overall_risk_divergence, 4)
    family_divergence["risk_kw_in_skill"] = total_risk_in_skill
    family_divergence["risk_kw_undisclosed"] = total_undisclosed
    return family_divergence


def main() -> None:
    print(f"[03_semantic_drift] Reading from {DB_PATH}")
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    skills = db.execute(
        "SELECT skill_id, owner, repo, description, summary_text, skill_text "
        "FROM skills WHERE detail_complete = 1"
    ).fetchall()
    db.close()
    print(f"  Computing IDF over {len(skills):,} skill texts...")

    # build IDF over skill_text corpus
    skill_corpus = [(s["skill_text"] or "") + " " + (s["summary_text"] or "") for s in skills]
    idf = build_idf(skill_corpus)

    fieldnames: list[str] | None = None
    rows_out: list[dict] = []

    for skill in skills:
        desc = (skill["description"] or "").strip()
        full_text = (skill["skill_text"] or "") + " " + (skill["summary_text"] or "")

        # Cosine similarity between description and skill_text
        desc_vec = tfidf_vector(desc, idf)
        skill_vec = tfidf_vector(full_text, idf)
        sim = cosine_similarity(desc_vec, skill_vec)
        semantic_gap = 1.0 - sim  # higher = more divergent

        # Risk-specific divergence
        risk_div = compute_risk_divergence(desc, full_text)

        # Description coverage ratio: len(desc) / len(skill_text) (log-scaled)
        desc_len = len(desc.split())
        skill_len = max(len(full_text.split()), 1)
        coverage_ratio = desc_len / skill_len
        log_coverage = min(math.log1p(coverage_ratio * 20) / math.log1p(20), 1.0)

        row = {
            "skill_id": skill["skill_id"],
            "owner": skill["owner"],
            "repo": skill["repo"],
            "desc_word_count": desc_len,
            "skill_text_word_count": skill_len,
            "desc_coverage_ratio": round(coverage_ratio, 4),
            "log_coverage": round(log_coverage, 4),
            "semantic_similarity": round(sim, 4),
            "semantic_gap": round(semantic_gap, 4),
            **risk_div,
        }

        if fieldnames is None:
            fieldnames = list(row.keys())
        rows_out.append(row)

    with open(OUTPUT_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames or [])
        writer.writeheader()
        writer.writerows(rows_out)

    high_gap = sum(1 for r in rows_out if r["semantic_gap"] >= 0.7)
    high_risk_div = sum(1 for r in rows_out if r["risk_divergence_overall"] >= 0.5)
    print(f"  High semantic gap (≥0.7): {high_gap:,} ({100*high_gap/max(len(rows_out),1):.1f}%)")
    print(f"  High risk divergence (≥0.5): {high_risk_div:,} ({100*high_risk_div/max(len(rows_out),1):.1f}%)")
    print(f"  Output: {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
