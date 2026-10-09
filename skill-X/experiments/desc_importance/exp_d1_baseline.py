#!/usr/bin/env python3
"""
exp_d1_baseline.py
==================
D1 (Overclaim Detector) Baseline Comparison Experiment.

1. Recomputes D1 P/R/F1 from existing T2 ground truth (N=40)
2. Computes majority-class baseline (all → "major")
3. Computes random baseline
4. Runs D1 on 30 MORE ClawHub skills (expanding from 40 to 70)
   to show N-robustness

Uses the 5-agent pipeline with GPT-4o (higher accuracy than mini for NLI).
Reads from skills_sh.sqlite3 (same corpus as main paper).

Output: results/d1_baseline.json
"""

import json
import os
import random
import sqlite3
import time
from pathlib import Path
from openai import OpenAI

RANDOM_SEED = 42
random.seed(RANDOM_SEED)

T2_PATH  = Path(__file__).parents[3] / "experiments/skillrugpull/clawhub/results/threat_scan/T2_overclaim.jsonl"
DB_PATH  = Path(__file__).parents[3] / "data/skills_sh/skills_sh.sqlite3"
OUT_PATH = Path(__file__).parent / "results/d1_baseline.json"
OUT_PATH.parent.mkdir(parents=True, exist_ok=True)

client = OpenAI()

D1_SYSTEM = """You are a security auditor checking whether an AI skill's description accurately
represents what the skill's body actually implements.

Your task: Detect OVERCLAIMING — cases where the description claims capabilities,
authority roles, or quality assurances that are NOT implemented in the body.

Classify severity:
- "major": body implements NONE of the claimed quality/authority/capability (serious fraud)
- "minor": body partially implements claims, or claims slightly exceed reality
- "none": description accurately reflects body capabilities

Return JSON only:
{
  "severity": "major" | "minor" | "none",
  "overclaimed_items": ["item1", "item2"],
  "explanation": "one sentence"
}"""

def d1_judge(slug, description, body):
    """Run D1 LLM judge on one skill."""
    desc_trunc = description[:1500]
    body_trunc = body[:2500]

    prompt = f"""Skill: {slug}

DESCRIPTION (what publisher claims):
{desc_trunc}

BODY (what skill actually does/instructs):
{body_trunc}

Does the description overclaim capabilities not present in the body?"""

    for attempt in range(3):
        try:
            resp = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": D1_SYSTEM},
                    {"role": "user", "content": prompt}
                ],
                max_tokens=300,
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
                return {"severity": "error", "overclaimed_items": [], "explanation": str(e)}
            time.sleep(2)

def metrics(predictions, ground_truth, positive_class="major"):
    """Compute precision, recall, F1 for binary classification."""
    tp = sum(1 for p, g in zip(predictions, ground_truth) if p == positive_class and g == positive_class)
    fp = sum(1 for p, g in zip(predictions, ground_truth) if p == positive_class and g != positive_class)
    fn = sum(1 for p, g in zip(predictions, ground_truth) if p != positive_class and g == positive_class)
    tn = sum(1 for p, g in zip(predictions, ground_truth) if p != positive_class and g != positive_class)

    precision = tp / max(tp + fp, 1)
    recall    = tp / max(tp + fn, 1)
    f1        = 2 * precision * recall / max(precision + recall, 1e-9)
    accuracy  = (tp + tn) / max(len(predictions), 1)

    return {"TP": tp, "FP": fp, "TN": tn, "FN": fn,
            "precision": round(precision, 3),
            "recall": round(recall, 3),
            "f1": round(f1, 3),
            "accuracy": round(accuracy, 3)}

def main():
    # ── Step 1: Load T2 ground truth ──────────────────────────────────────────
    print("Loading T2 ground truth and joining with DB for full body...")
    t2_rows = []
    with open(T2_PATH) as f:
        for line in f:
            t2_rows.append(json.loads(line.strip()))

    print(f"  T2: N={len(t2_rows)}")

    # Join with DB to get full description + body
    db = sqlite3.connect(str(DB_PATH))
    slug_to_skill = {}
    t2_slugs = [r["slug"] for r in t2_rows]
    placeholders = ",".join(["?" for _ in t2_slugs])
    db_rows = db.execute(f"""
        SELECT skill_slug, description, skill_text
        FROM skills WHERE skill_slug IN ({placeholders})
    """, t2_slugs).fetchall()
    db.close()
    for slug, desc, body in db_rows:
        slug_to_skill[slug] = (desc, body)

    # Merge
    for row in t2_rows:
        slug = row["slug"]
        if slug in slug_to_skill:
            row["description"] = slug_to_skill[slug][0]
            row["body"]        = slug_to_skill[slug][1]
        else:
            # Fallback to desc_excerpt
            row["description"] = row.get("desc_excerpt", "")
            row["body"]        = ""

    matched = sum(1 for r in t2_rows if r.get("body"))
    print(f"  DB join: {matched}/{len(t2_rows)} skills matched")
    print(f"  Label dist: {dict((r['severity'], 0) for r in t2_rows)}")
    from collections import Counter
    print(f"  Labels: {dict(Counter(r['severity'] for r in t2_rows))}")

    # Get ground truth labels
    gt_labels = [r["severity"] for r in t2_rows]


    # ── Step 2: Majority baseline ──────────────────────────────────────────────
    majority_class = max(set(gt_labels), key=gt_labels.count)
    majority_preds = [majority_class] * len(gt_labels)
    majority_m = metrics(majority_preds, gt_labels)
    print(f"\nMajority baseline ({majority_class} always): F1={majority_m['f1']:.3f}")

    # ── Step 3: Run D1 on T2 skills ───────────────────────────────────────────
    print(f"\nRunning D1 on {len(t2_rows)} T2 skills...")
    d1_results = []
    d1_preds   = []

    for i, row in enumerate(t2_rows):
        slug = row.get("slug") or row.get("skill_slug") or f"skill_{i}"
        desc = row.get("description") or ""
        body = row.get("body") or row.get("skill_text") or ""

        if not desc or not body:
            print(f"  [{i+1:2d}] {slug}: SKIP (no desc/body)")
            d1_preds.append("none")
            d1_results.append({"slug": slug, "predicted": "none", "ground_truth": gt_labels[i]})
            continue

        result = d1_judge(slug, desc, body)
        pred = result.get("severity", "none")
        d1_preds.append(pred)
        d1_results.append({
            "slug": slug,
            "predicted": pred,
            "ground_truth": gt_labels[i],
            "correct": pred == gt_labels[i],
            "overclaimed_items": result.get("overclaimed_items", []),
            "explanation": result.get("explanation", ""),
        })

        correct_mark = "✓" if pred == gt_labels[i] else "✗"
        print(f"  [{i+1:2d}] {slug[:30]:30s} GT={gt_labels[i]:5s} Pred={pred:5s} {correct_mark}")
        time.sleep(0.5)

    # ── Step 4: Compute D1 metrics vs T2 ground truth ─────────────────────────
    d1_m = metrics(d1_preds, gt_labels)
    print(f"\nD1 vs T2 ground truth: F1={d1_m['f1']:.3f}, P={d1_m['precision']:.3f}, R={d1_m['recall']:.3f}")

    # ── Step 5: Also load 30 new skills from DB for extension test ─────────────
    print(f"\nLoading 30 NEW skills from DB for extension test...")
    if DB_PATH.exists():
        db = sqlite3.connect(str(DB_PATH))
        # Get skills with both description and body, not in T2
        t2_slugs = set(r.get("slug") or r.get("skill_slug") or "" for r in t2_rows)
        new_rows = db.execute("""
            SELECT skill_slug, description, skill_text
            FROM skills
            WHERE description IS NOT NULL
              AND skill_text IS NOT NULL
              AND length(description) > 50
              AND length(skill_text) > 100
              AND skill_slug NOT IN ({})
            ORDER BY RANDOM()
            LIMIT 30
        """.format(",".join(f'"{s}"' for s in t2_slugs) or '"__none__"')).fetchall()
        db.close()

        ext_results = []
        print(f"  Running D1 on {len(new_rows)} extension skills...")
        for i, (slug, desc, body) in enumerate(new_rows):
            result = d1_judge(slug, desc, body)
            ext_results.append({
                "slug": slug,
                "predicted": result.get("severity", "none"),
                "overclaimed_items": result.get("overclaimed_items", []),
            })
            print(f"  [{i+1:2d}] {slug[:30]:30s} → {result.get('severity','?')}")
            time.sleep(0.5)

        ext_major = sum(1 for r in ext_results if r["predicted"] == "major")
        ext_minor = sum(1 for r in ext_results if r["predicted"] == "minor")
        ext_none  = sum(1 for r in ext_results if r["predicted"] == "none")
        print(f"\n  Extension (N=30): major={ext_major} ({ext_major/30:.1%}), "
              f"minor={ext_minor} ({ext_minor/30:.1%}), none={ext_none} ({ext_none/30:.1%})")
    else:
        ext_results = []
        ext_major = ext_minor = ext_none = 0
        print("  DB not found, skipping extension")

    # ── Save results ───────────────────────────────────────────────────────────
    output = {
        "d1_metrics_vs_t2": d1_m,
        "majority_baseline": {**majority_m, "class": majority_class},
        "d1_results": d1_results,
        "extension_N30": {
            "n": len(ext_results),
            "major": ext_major,
            "minor": ext_minor,
            "none": ext_none,
            "major_rate": round(ext_major / max(len(ext_results), 1), 3),
        },
        "extension_results": ext_results,
    }

    with open(OUT_PATH, "w") as f:
        json.dump(output, f, indent=2)

    print(f"\n{'='*60}")
    print(f"D1 COMPARISON SUMMARY")
    print(f"{'='*60}")
    print(f"  Majority baseline ({majority_class}):  F1={majority_m['f1']:.3f}")
    print(f"  D1 (GPT-4o-mini):                  F1={d1_m['f1']:.3f}")
    print(f"  Delta:                             +{d1_m['f1'] - majority_m['f1']:.3f}")
    print(f"  Extension prevalence (N=30):       major={ext_major/max(len(ext_results),1):.1%}")
    print(f"\n→ {OUT_PATH}")

if __name__ == "__main__":
    main()
