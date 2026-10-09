#!/usr/bin/env python3
"""
exp_runner.py
=============
Unified experiment runner for D1 baseline, D3 validation, BM25 significance.
Uses openclaw CLI (no API key needed) for LLM calls.

Run:
  python3 exp_runner.py --exp d1   # D1 majority baseline
  python3 exp_runner.py --exp d3   # D3 precision validation
  python3 exp_runner.py --exp bm25 # BM25 significance test
  python3 exp_runner.py --exp all  # all three
"""

import json
import math
import random
import re
import sqlite3
import subprocess
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

RANDOM_SEED = 42
random.seed(RANDOM_SEED)

REPO_ROOT = Path(__file__).parents[3]
RESULTS   = Path(__file__).parent / "results"
RESULTS.mkdir(exist_ok=True)

T2_PATH = REPO_ROOT / "experiments/skillrugpull/clawhub/results/threat_scan/T2_overclaim.jsonl"
T6_PATH = REPO_ROOT / "experiments/skillrugpull/clawhub/results/threat_scan/T6_hijack.jsonl"
DB_PATH = REPO_ROOT / "data/skills_sh/skills_sh.sqlite3"

# ─────────────────────────────────────────────────────────────────────────────
# LLM call via openclaw CLI (no API key needed)
# ─────────────────────────────────────────────────────────────────────────────

def llm_call(prompt: str, system: str = "", max_retries: int = 3) -> str:
    """Call LLM via openclaw CLI. Returns raw text response."""
    full_prompt = f"{system}\n\n{prompt}" if system else prompt

    for attempt in range(max_retries):
        try:
            result = subprocess.run(
                ["openclaw", "infer", "model", "run", "--prompt", full_prompt],
                capture_output=True, text=True, timeout=60
            )
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()
            time.sleep(2)
        except subprocess.TimeoutExpired:
            if attempt == max_retries - 1:
                return ""
            time.sleep(5)
        except Exception as e:
            if attempt == max_retries - 1:
                return ""
            time.sleep(2)
    return ""

def llm_json(prompt: str, system: str = "") -> dict | None:
    """Call LLM and parse JSON from response."""
    raw = llm_call(prompt, system)
    if not raw:
        return None
    # Try to extract JSON block
    for chunk in re.split(r"```(?:json)?", raw):
        chunk = chunk.strip().rstrip("`").strip()
        if chunk.startswith("{"):
            try:
                return json.loads(chunk)
            except:
                pass
    # Try raw
    try:
        return json.loads(raw)
    except:
        return None

# ─────────────────────────────────────────────────────────────────────────────
# DB helpers
# ─────────────────────────────────────────────────────────────────────────────

def db_query(sql: str, params=()) -> list:
    if not DB_PATH.exists():
        return []
    db = sqlite3.connect(str(DB_PATH))
    rows = db.execute(sql, params).fetchall()
    db.close()
    return rows

def db_get_skills(slugs: list) -> dict:
    """Return {slug: (description, body)} for given slugs."""
    if not slugs:
        return {}
    ph = ",".join(["?"] * len(slugs))
    rows = db_query(f"SELECT skill_slug, description, skill_text FROM skills WHERE skill_slug IN ({ph})", slugs)
    return {slug: (desc, body) for slug, desc, body in rows}

# ─────────────────────────────────────────────────────────────────────────────
# D1 Baseline Experiment
# ─────────────────────────────────────────────────────────────────────────────

D1_SYSTEM = """You are a security auditor checking if an AI skill's description overclaims capabilities.
Classify as: "major" (body implements NONE of the claimed authority/quality/capability),
"minor" (partial overclaim or slight exaggeration), "none" (accurate description).
Return JSON only: {"severity": "major"|"minor"|"none", "items": ["overclaimed item1"], "reason": "one sentence"}"""

def d1_judge(slug: str, desc: str, body: str) -> dict:
    prompt = f"""Skill: {slug}

DESCRIPTION (what publisher claims):
{desc[:1500]}

BODY (what skill actually does):
{body[:2500]}

Verdict: does the description overclaim capabilities not in the body?"""
    result = llm_json(prompt, D1_SYSTEM)
    if result is None:
        return {"severity": "error", "items": [], "reason": "parse failed"}
    return result

def compute_metrics(predictions: list, ground_truth: list, positive_class: str = "major") -> dict:
    tp = sum(1 for p, g in zip(predictions, ground_truth) if p == positive_class and g == positive_class)
    fp = sum(1 for p, g in zip(predictions, ground_truth) if p == positive_class and g != positive_class)
    fn = sum(1 for p, g in zip(predictions, ground_truth) if p != positive_class and g == positive_class)
    tn = sum(1 for p, g in zip(predictions, ground_truth) if p != positive_class and g != positive_class)
    prec = tp / max(tp+fp, 1)
    rec  = tp / max(tp+fn, 1)
    f1   = 2*prec*rec / max(prec+rec, 1e-9)
    acc  = (tp+tn) / max(len(predictions), 1)
    return {"TP": tp, "FP": fp, "TN": tn, "FN": fn,
            "precision": round(prec, 3), "recall": round(rec, 3),
            "f1": round(f1, 3), "accuracy": round(acc, 3)}

def run_d1_experiment() -> dict:
    print("\n" + "="*60)
    print("D1 BASELINE EXPERIMENT")
    print("="*60)

    # Load T2 ground truth
    t2_rows = [json.loads(l) for l in open(T2_PATH)]
    t2_slugs = [r["slug"] for r in t2_rows]
    gt_labels = [r["severity"] for r in t2_rows]

    label_dist = Counter(gt_labels)
    print(f"T2 labels: {dict(label_dist)}")

    # Majority baseline
    majority_class = max(label_dist, key=label_dist.get)
    majority_preds = [majority_class] * len(gt_labels)
    majority_m = compute_metrics(majority_preds, gt_labels)
    print(f"Majority baseline ({majority_class}): F1={majority_m['f1']:.3f}")

    # Random baseline (class-proportional)
    classes = list(label_dist.keys())
    weights = [label_dist[c]/len(gt_labels) for c in classes]
    rand_preds = random.choices(classes, weights=weights, k=len(gt_labels))
    random_m = compute_metrics(rand_preds, gt_labels)
    print(f"Random baseline: F1={random_m['f1']:.3f}")

    # Get full bodies from DB
    slug_to_skill = db_get_skills(t2_slugs)
    print(f"DB join: {len(slug_to_skill)}/{len(t2_slugs)} matched")

    # D1 LLM detection
    d1_preds   = []
    d1_results = []
    for i, row in enumerate(t2_rows):
        slug = row["slug"]
        desc = slug_to_skill.get(slug, (row.get("desc_excerpt",""), ""))[0]
        body = slug_to_skill.get(slug, ("", ""))[1]

        if not body:
            print(f"  [{i+1:2d}] {slug[:25]:25s}  SKIP (no body in DB)")
            d1_preds.append("none")
            d1_results.append({"slug": slug, "predicted": "none", "ground_truth": gt_labels[i], "skipped": True})
            continue

        result = d1_judge(slug, desc, body)
        pred = result.get("severity", "none")
        d1_preds.append(pred)
        correct = pred == gt_labels[i]
        d1_results.append({
            "slug": slug,
            "predicted": pred,
            "ground_truth": gt_labels[i],
            "correct": correct,
            "overclaimed_items": result.get("items", []),
            "reason": result.get("reason", ""),
        })
        mark = "✓" if correct else "✗"
        print(f"  [{i+1:2d}] {slug[:25]:25s}  GT={gt_labels[i]:5s} Pred={pred:5s} {mark}")

    d1_m = compute_metrics(d1_preds, gt_labels)
    print(f"\nD1 metrics: F1={d1_m['f1']:.3f}  P={d1_m['precision']:.3f}  R={d1_m['recall']:.3f}")
    print(f"vs Majority:  F1={majority_m['f1']:.3f}  Δ=+{d1_m['f1']-majority_m['f1']:.3f}")

    output = {
        "d1_metrics": d1_m,
        "majority_baseline": {**majority_m, "class": majority_class},
        "random_baseline": random_m,
        "d1_results": d1_results,
    }
    out_path = RESULTS / "d1_baseline.json"
    out_path.write_text(json.dumps(output, indent=2))
    print(f"→ {out_path}")
    return output

# ─────────────────────────────────────────────────────────────────────────────
# D3 Validation Experiment
# ─────────────────────────────────────────────────────────────────────────────

D3_SYSTEM = """You judge whether an AI skill description is inflated beyond its functional scope.
Return JSON only: {"verdict": "inflated"|"legitimate"|"uncertain", "reason": "one sentence", "severity": "high"|"medium"|"low"|"none"}"""

def d3_judge(slug: str, desc: str, score: float, words: int) -> dict:
    prompt = f"""Skill: {slug}
Description word count: {words}
HijackScore (foreign tokens): {score:.0f}  (median in corpus: ~100)

Description (first 600 chars):
{desc[:600]}

Is this description inflated with keywords beyond what the skill actually does?"""
    result = llm_json(prompt, D3_SYSTEM)
    if result is None:
        return {"verdict": "error", "reason": "parse failed", "severity": "none"}
    return result

def run_d3_experiment() -> dict:
    print("\n" + "="*60)
    print("D3 VALIDATION EXPERIMENT")
    print("="*60)

    # Load T6
    rows = [json.loads(l) for l in open(T6_PATH)]
    scored = [r for r in rows if isinstance(r.get("hijack_score"), (int, float))]
    scored.sort(key=lambda r: r["hijack_score"], reverse=True)

    high = [r for r in scored if r["hijack_score"] >= 500][:50]
    low  = [r for r in scored if r["hijack_score"] < 50]
    low  = random.sample(low, min(50, len(low)))
    print(f"High (≥500): {len(high)}  Low (<50): {len(low)}")

    # Enrich from DB
    all_slugs = [r["slug"] for r in high + low]
    slug_to_desc = {slug: desc for slug, desc, body in db_query(
        f"SELECT skill_slug, description, skill_text FROM skills WHERE skill_slug IN ({','.join(['?']*len(all_slugs))})",
        all_slugs
    )}

    results = []
    for group, group_rows in [("high", high), ("low", low)]:
        for i, row in enumerate(group_rows):
            slug  = row["slug"]
            desc  = slug_to_desc.get(slug, row.get("desc_excerpt", ""))
            score = row["hijack_score"]
            words = row.get("desc_words", 0)

            verdict = d3_judge(slug, desc, score, words)
            results.append({
                "group": group,
                "slug": slug,
                "score": score,
                "words": words,
                **verdict
            })
            v = verdict.get("verdict", "?")
            print(f"  [{group:4s} {score:6.0f}] {slug[:25]:25s} → {v}")

    # Compute precision@500
    high_r = [r for r in results if r["group"] == "high"]
    low_r  = [r for r in results if r["group"] == "low"]
    tp = sum(1 for r in high_r if r.get("verdict") == "inflated")
    fp = sum(1 for r in high_r if r.get("verdict") == "legitimate")
    tn = sum(1 for r in low_r  if r.get("verdict") == "legitimate")
    fn = sum(1 for r in low_r  if r.get("verdict") == "inflated")

    prec = tp / max(tp+fp, 1)
    rec  = tp / max(tp+fn, 1)
    f1   = 2*prec*rec / max(prec+rec, 1e-9)

    summary = {
        "threshold": 500,
        "n_high": len(high_r), "n_low": len(low_r),
        "TP": tp, "FP": fp, "TN": tn, "FN": fn,
        "precision_at_500": round(prec, 3),
        "recall_at_500": round(rec, 3),
        "f1_at_500": round(f1, 3),
        "uncertain_high": sum(1 for r in high_r if r.get("verdict") == "uncertain"),
    }

    print(f"\nD3 Precision@500 = {prec:.3f}  Recall = {rec:.3f}  F1 = {f1:.3f}")
    print(f"  TP={tp} FP={fp} TN={tn} FN={fn}")

    output = {"summary": summary, "results": results}
    out_path = RESULTS / "d3_validation.json"
    out_path.write_text(json.dumps(output, indent=2))
    print(f"→ {out_path}")
    return output

# ─────────────────────────────────────────────────────────────────────────────
# BM25 Significance Test
# ─────────────────────────────────────────────────────────────────────────────

def run_bm25_significance() -> dict:
    print("\n" + "="*60)
    print("BM25 STATISTICAL SIGNIFICANCE TEST")
    print("="*60)

    try:
        from rank_bm25 import BM25Okapi
    except ImportError:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "rank_bm25", "-q"])
        from rank_bm25 import BM25Okapi

    try:
        import numpy as np
        from scipy import stats
    except ImportError:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "numpy", "scipy", "-q"])
        import numpy as np
        from scipy import stats

    # Load N=25 IC-1 focal skills (short desc proxy)
    print(f"Loading skills from DB...")
    focal_rows = db_query("""
        SELECT skill_slug, description, skill_text FROM skills
        WHERE description IS NOT NULL AND skill_text IS NOT NULL
          AND length(description) BETWEEN 30 AND 250
          AND length(skill_text) > 300
        ORDER BY RANDOM() LIMIT 25
    """)
    corpus_rows = db_query("""
        SELECT skill_slug, description FROM skills
        WHERE description IS NOT NULL AND length(description) > 20
        ORDER BY RANDOM() LIMIT 300
    """)

    if not focal_rows:
        print("No focal skills loaded. DB may not be accessible.")
        return {}

    corpus_descs = [r[1] for r in corpus_rows]
    print(f"  Focal: {len(focal_rows)}  Corpus: {len(corpus_descs)}")

    all_results = []
    for k in [5, 10, 20]:
        orig_hits, aug_hits = [], []
        for slug, orig_desc, body in focal_rows:
            # Augmented desc: add first 2 body sentences
            body_sents = [s.strip() for s in re.split(r'[.\n]', body) if len(s.strip()) > 15]
            aug_extra = ". ".join(body_sents[:2])
            aug_desc = orig_desc.rstrip(".") + ". " + aug_extra + "."

            # Build 3 query styles from body vocabulary
            body_tokens = [w for w in re.sub(r'[^a-z0-9 ]', ' ', body.lower()).split() if len(w) > 4]
            body_tokens = list(set(body_tokens))[:30]
            if len(body_tokens) < 3:
                continue

            for qtype in ["goal", "scenario", "symptom"]:
                sample = random.sample(body_tokens, min(3, len(body_tokens)))
                query_tokens = sample

                full_corpus = corpus_descs + [orig_desc]  # focal at index -1
                focal_idx = len(full_corpus) - 1

                # Original
                tok_orig = [d.lower().split() for d in full_corpus]
                bm = BM25Okapi(tok_orig)
                sc = bm.get_scores(query_tokens)
                ranked = sorted(range(len(sc)), key=lambda i: -sc[i])
                orig_hit = 1 if focal_idx in ranked[:k] else 0
                orig_hits.append(orig_hit)

                # Augmented
                full_aug = corpus_descs + [aug_desc]
                tok_aug = [d.lower().split() for d in full_aug]
                bm2 = BM25Okapi(tok_aug)
                sc2 = bm2.get_scores(query_tokens)
                ranked2 = sorted(range(len(sc2)), key=lambda i: -sc2[i])
                aug_hit = 1 if focal_idx in ranked2[:k] else 0
                aug_hits.append(aug_hit)

        orig_arr = np.array(orig_hits, dtype=float)
        aug_arr  = np.array(aug_hits, dtype=float)
        diff_arr = aug_arr - orig_arr
        n = len(diff_arr)

        # Bootstrap CI
        boot_diffs = [np.mean(np.random.choice(diff_arr, n, replace=True)) for _ in range(2000)]
        ci = (np.percentile(boot_diffs, 2.5), np.percentile(boot_diffs, 97.5))

        # Paired t-test
        t_stat, p_val = stats.ttest_rel(aug_arr, orig_arr)

        # Cohen's d
        pooled_sd = np.sqrt((np.std(orig_arr, ddof=1)**2 + np.std(aug_arr, ddof=1)**2) / 2)
        cohens_d  = (np.mean(aug_arr) - np.mean(orig_arr)) / max(pooled_sd, 1e-9)

        r = {
            "k": k, "n": n,
            "original_recall": round(float(np.mean(orig_arr)), 4),
            "augmented_recall": round(float(np.mean(aug_arr)), 4),
            "delta_pp": round(float(np.mean(diff_arr)) * 100, 2),
            "ci_95": [round(float(ci[0])*100, 2), round(float(ci[1])*100, 2)],
            "t_stat": round(float(t_stat), 3),
            "p_value": round(float(p_val), 4),
            "cohens_d": round(float(cohens_d), 3),
            "significant": bool(p_val < 0.05),
        }
        all_results.append(r)
        print(f"  K={k:2d}: orig={r['original_recall']:.3f}  aug={r['augmented_recall']:.3f}  "
              f"Δ={r['delta_pp']:+.1f}pp  p={r['p_value']:.4f}  {'✅' if r['significant'] else '❌'}")

    output = {"results_by_k": all_results, "n_focal": len(focal_rows), "n_corpus": len(corpus_descs)}
    out_path = RESULTS / "bm25_significance.json"
    out_path.write_text(json.dumps(output, indent=2))
    print(f"→ {out_path}")
    return output

# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--exp", choices=["d1", "d3", "bm25", "all"], default="all")
    args = parser.parse_args()

    if args.exp in ("bm25", "all"):
        run_bm25_significance()

    if args.exp in ("d3", "all"):
        run_d3_experiment()

    if args.exp in ("d1", "all"):
        run_d1_experiment()

    print("\n✅ All requested experiments complete.")
