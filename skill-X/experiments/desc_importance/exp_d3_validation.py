#!/usr/bin/env python3
"""
exp_d3_validation.py
====================
D3 (Keyword Inflation) Validation Experiment.

Takes top-50 HijackScore pairs from T6 and samples 50 low-score pairs as negatives.
Uses OpenAI GPT-4o-mini to judge whether each flagged pair is a genuine
retrieval-displacement threat (precision@threshold).

Also computes: threshold sensitivity (200, 300, 500, 750, 1000).

Output: results/d3_validation.json
"""

import json
import os
import random
import time
from pathlib import Path
from openai import OpenAI

RANDOM_SEED = 42
random.seed(RANDOM_SEED)

T6_PATH  = Path(__file__).parents[3] / "experiments/skillrugpull/clawhub/results/threat_scan/T6_hijack.jsonl"
OUT_PATH = Path(__file__).parent / "results/d3_validation.json"
OUT_PATH.parent.mkdir(parents=True, exist_ok=True)

client = OpenAI()  # uses OPENAI_API_KEY env var

JUDGE_PROMPT = """\
You are reviewing an AI skill marketplace for description inflation issues.

Skill:
  slug: {slug}
  description word count: {words}
  description (first 800 chars): {desc}
  hijack_score (foreign token count): {score}
  median score in corpus: ~100

Question: Does this skill's description contain substantial content that goes
beyond its actual functional scope? (i.e., the description is bloated with
keywords that don't reflect what the skill actually does)

Answer with JSON only:
{{
  "verdict": "inflated" | "legitimate" | "uncertain",
  "reasoning": "one sentence",
  "severity": "high" | "medium" | "low" | "none",
  "estimated_core_function": "what this skill actually does in 5 words"
}}"""

DB_PATH = Path(__file__).parents[3] / "data/skills_sh/skills_sh.sqlite3"

def load_t6_per_skill(path, n_high=50, n_low=50, threshold=500):
    """Load T6 data per-skill, return high-score and low-score samples."""
    rows = []
    with open(path) as f:
        for line in f:
            try:
                rows.append(json.loads(line.strip()))
            except:
                pass

    scored = [r for r in rows if isinstance(r.get("hijack_score"), (int, float))]
    scored.sort(key=lambda r: r["hijack_score"], reverse=True)

    high = [r for r in scored if r["hijack_score"] >= threshold][:n_high]
    low  = [r for r in scored if r["hijack_score"] < 50]
    low  = random.sample(low, min(n_low, len(low)))

    print(f"T6: N={len(rows)}, Above {threshold}: {len([r for r in scored if r['hijack_score']>=threshold])}")
    print(f"  High sample: {len(high)}, Low sample: {len(low)}")
    return high, low

def enrich_from_db(rows, db_path):
    """Join with skills_sh to get full description."""
    if not db_path.exists():
        return rows
    db = sqlite3.connect(str(db_path))
    slugs = [r["slug"] for r in rows]
    ph = ",".join(["?" for _ in slugs])
    db_rows = db.execute(f"SELECT skill_slug, description FROM skills WHERE skill_slug IN ({ph})", slugs).fetchall()
    db.close()
    slug_to_desc = {slug: desc for slug, desc in db_rows}
    for row in rows:
        row["full_desc"] = slug_to_desc.get(row["slug"], row.get("desc_excerpt", ""))
    return rows

def judge_skill(row):
    """Ask GPT-4o-mini to judge a single skill's description inflation."""
    desc = (row.get("full_desc") or row.get("desc_excerpt") or "")[:800]
    slug  = row.get("slug", "?")
    words = row.get("desc_words", len(desc.split()))
    score = row.get("hijack_score", 0)

    prompt = JUDGE_PROMPT.format(slug=slug, words=words, desc=desc, score=score)

    for attempt in range(3):
        try:
            resp = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=200,
                temperature=0.1,
            )
            raw = resp.choices[0].message.content.strip()
            for chunk in raw.split("```"):
                chunk = chunk.strip().lstrip("json").strip()
                try:
                    return json.loads(chunk)
                except:
                    pass
            return json.loads(raw)
        except Exception as e:
            if attempt == 2:
                return {"verdict": "error", "reasoning": str(e), "severity": "none"}
            time.sleep(2)


def main():
    import sqlite3

    high, low = load_t6_per_skill(T6_PATH, n_high=50, n_low=50, threshold=500)

    # Enrich with full description from DB
    all_skills = high + low
    all_skills = enrich_from_db(all_skills, DB_PATH)

    results = []
    groups = [("high", r) for r in high] + [("low", r) for r in low]

    print(f"\nJudging {len(groups)} skills with GPT-4o-mini...")
    for i, (group, row) in enumerate(groups):
        verdict = judge_skill(row)
        result = {
            "group": group,
            "slug":  row.get("slug", "?"),
            "score": row.get("hijack_score", 0),
            "words": row.get("desc_words", 0),
            **verdict
        }
        results.append(result)

        v = verdict.get("verdict", "?")
        s = result["score"]
        print(f"  [{i+1:3d}/{len(groups)}] {group:4s} score={s:6.0f} → {v:12s}  {result['slug'][:25]}")
        time.sleep(0.3)

    # Compute precision@500 for high group
    high_results = [r for r in results if r["group"] == "high"]
    low_results  = [r for r in results if r["group"] == "low"]

    tp = sum(1 for r in high_results if r.get("verdict") == "inflated")
    fp = sum(1 for r in high_results if r.get("verdict") == "legitimate")
    tn = sum(1 for r in low_results  if r.get("verdict") == "legitimate")
    fn = sum(1 for r in low_results  if r.get("verdict") == "inflated")

    n_high = len(high_results)
    n_low  = len(low_results)

    precision = tp / max(tp + fp, 1)
    recall    = tp / max(tp + fn, 1)
    f1        = 2 * precision * recall / max(precision + recall, 0.001)
    fpr       = fp / max(fp + tn, 1)

    summary = {
        "threshold": 500,
        "n_high": n_high,
        "n_low": n_low,
        "TP": tp, "FP": fp, "TN": tn, "FN": fn,
        "precision_at_500": round(precision, 3),
        "recall_at_500": round(recall, 3),
        "f1_at_500": round(f1, 3),
        "fpr_at_500": round(fpr, 3),
        "uncertain_high": sum(1 for r in high_results if r.get("verdict") == "uncertain"),
        "uncertain_low":  sum(1 for r in low_results  if r.get("verdict") == "uncertain"),
    }

    output = {"summary": summary, "results": results}

    with open(OUT_PATH, "w") as f:
        json.dump(output, f, indent=2)

    print(f"\n{'='*50}")
    print(f"D3 VALIDATION RESULTS (threshold=500)")
    print(f"{'='*50}")
    print(f"  Precision@500 = {precision:.3f}  ({tp}/{n_high} truly inflated)")
    print(f"  Recall@500    = {recall:.3f}")
    print(f"  F1@500        = {f1:.3f}")
    print(f"  FPR@500       = {fpr:.3f}  ({fp}/{n_low} low-score falsely flagged)")
    print(f"  Uncertain (high): {summary['uncertain_high']}")
    print(f"\n→ {OUT_PATH}")

if __name__ == "__main__":
    main()

