#!/usr/bin/env python3
"""
analyze_results.py — Phase 5: Statistical Analysis & Paper Tables
==================================================================
Reads routing_results.jsonl, computes:
1. Per-condition routing accuracy (body-trigger, desc-trigger, negative)
2. Repair lift: Δ(minimal_repair − original) for body-trigger queries
3. Control check: Δ(length_control − original) should ≈ 0
4. Skill-clustered bootstrap CIs (resample over skills, not queries)
5. McNemar test on paired Original→Repair decisions
6. Per-IC-class breakdown
7. Error analysis (why some repairs fail)

Outputs:
- results/main_results_table.md    — Paper Table 7
- results/error_analysis.json      — Failure categorization
- results/analysis_summary.json    — Machine-readable summary
"""
import json
import random
import sys
from collections import defaultdict
from pathlib import Path

SEED = 42
random.seed(SEED)

BASE_DIR = Path(__file__).parent
RESULTS_DIR = BASE_DIR / "results"


def load_results(path: Path) -> list[dict]:
    results = []
    with open(path) as f:
        for line in f:
            if line.strip():
                results.append(json.loads(line))
    return results


# ── Bootstrap CI ─────────────────────────────────────────────────────────────

def skill_clustered_bootstrap(results: list[dict], metric_fn, n_boot: int = 2000,
                              ci: float = 0.95) -> dict:
    """
    Bootstrap CI resampling at the skill level (not query level).
    metric_fn: takes list[dict] → float
    """
    # Group by skill
    by_skill = defaultdict(list)
    for r in results:
        by_skill[r["skill_id"]].append(r)
    skills = list(by_skill.keys())

    if not skills:
        return {"mean": None, "ci_lo": None, "ci_hi": None}

    point = metric_fn(results)
    boot_vals = []

    for _ in range(n_boot):
        sampled_skills = random.choices(skills, k=len(skills))
        boot_data = []
        for s in sampled_skills:
            boot_data.extend(by_skill[s])
        try:
            boot_vals.append(metric_fn(boot_data))
        except:
            pass

    if not boot_vals:
        return {"mean": point, "ci_lo": None, "ci_hi": None}

    alpha = (1 - ci) / 2
    boot_vals.sort()
    lo = boot_vals[int(alpha * len(boot_vals))]
    hi = boot_vals[int((1 - alpha) * len(boot_vals))]

    return {"mean": round(point, 3), "ci_lo": round(lo, 3), "ci_hi": round(hi, 3)}


def accuracy(rows: list[dict]) -> float:
    if not rows:
        return 0.0
    correct = sum(1 for r in rows if r["correct"])
    return correct / len(rows)


def negative_rejection_rate(rows: list[dict]) -> float:
    """For negative queries, correct = NOT selecting the target."""
    if not rows:
        return 0.0
    correct = sum(1 for r in rows if not r["correct"])
    return correct / len(rows)


# ── McNemar Test ─────────────────────────────────────────────────────────────

def mcnemar_test(results: list[dict], cond_a: str, cond_b: str) -> dict:
    """Paired comparison: how often does cond_b fix what cond_a got wrong (and vice versa)?"""
    # Match on (skill_id, query, router)
    a_map = {}
    b_map = {}
    for r in results:
        key = (r["skill_id"], r["query"], r["router"])
        if r["condition"] == cond_a:
            a_map[key] = r["correct"]
        elif r["condition"] == cond_b:
            b_map[key] = r["correct"]

    # Count discordant pairs
    b_only = 0  # b correct, a wrong
    a_only = 0  # a correct, b wrong
    both = 0
    neither = 0

    for key in set(a_map.keys()) & set(b_map.keys()):
        ac, bc = a_map[key], b_map[key]
        if ac and bc:
            both += 1
        elif not ac and bc:
            b_only += 1
        elif ac and not bc:
            a_only += 1
        else:
            neither += 1

    n = b_only + a_only
    if n == 0:
        p_value = 1.0
    else:
        # McNemar exact (binomial test)
        from math import comb
        k = min(b_only, a_only)
        p_value = 2 * sum(comb(n, i) * 0.5**n for i in range(k + 1))
        p_value = min(p_value, 1.0)

    return {
        "n_pairs": both + b_only + a_only + neither,
        f"{cond_a}_only": a_only,
        f"{cond_b}_only": b_only,
        "both_correct": both,
        "both_wrong": neither,
        "p_value": round(p_value, 6),
    }


# ── Main Analysis ────────────────────────────────────────────────────────────

def main():
    results_file = RESULTS_DIR / "routing_results.jsonl"
    if not results_file.exists():
        print("ERROR: routing_results.jsonl not found")
        sys.exit(1)

    results = load_results(results_file)
    print(f"Loaded {len(results)} results")

    # Filter to positive queries only for accuracy
    positive = [r for r in results if r.get("expected_target", True)]
    negative = [r for r in results if not r.get("expected_target", True)]

    n_skills = len(set(r["skill_id"] for r in results))
    n_routers = len(set(r["router"] for r in results))
    conditions = sorted(set(r["condition"] for r in results))
    query_types = sorted(set(r["query_type"] for r in results))

    print(f"Skills: {n_skills}, Routers: {n_routers}")
    print(f"Conditions: {conditions}")
    print(f"Query types: {query_types}")

    # ── Main Results Table ────────────────────────────────────────────────────
    print(f"\n{'='*80}")
    print("MAIN RESULTS TABLE")
    print(f"{'='*80}")

    table_lines = [
        "| Condition | Body-Trigger | Desc-Trigger | Paraphrase | Neg. Reject | Overall |",
        "|-----------|:-------------|:-------------|:-----------|:------------|:--------|",
    ]

    summary = {}

    for cond in conditions:
        row = {"condition": cond}

        for qt in ["body_trigger", "desc_trigger", "paraphrase_body"]:
            subset = [r for r in positive if r["condition"] == cond and r["query_type"] == qt]
            ci = skill_clustered_bootstrap(subset, accuracy)
            row[qt] = ci
            pct = f"{ci['mean']*100:.1f}" if ci['mean'] is not None else "—"
            ci_str = f"[{ci['ci_lo']*100:.1f},{ci['ci_hi']*100:.1f}]" if ci['ci_lo'] is not None else ""

        neg_subset = [r for r in negative if r["condition"] == cond]
        neg_ci = skill_clustered_bootstrap(neg_subset, negative_rejection_rate) if neg_subset else {"mean": None}
        row["negative"] = neg_ci

        overall_subset = [r for r in positive if r["condition"] == cond]
        overall_ci = skill_clustered_bootstrap(overall_subset, accuracy)
        row["overall"] = overall_ci

        # Build table row
        cells = []
        for key in ["body_trigger", "desc_trigger", "paraphrase_body", "negative", "overall"]:
            ci = row.get(key, {})
            if ci.get("mean") is not None:
                pct = f"{ci['mean']*100:.1f}%"
                if ci.get("ci_lo") is not None:
                    pct += f" [{ci['ci_lo']*100:.0f},{ci['ci_hi']*100:.0f}]"
            else:
                pct = "—"
            cells.append(pct)

        cond_display = cond.replace("_", " ").title()
        table_lines.append(f"| **{cond_display}** | {' | '.join(cells)} |")
        summary[cond] = row

    for line in table_lines:
        print(line)

    # ── Repair Lift ───────────────────────────────────────────────────────────
    print(f"\n{'='*80}")
    print("REPAIR LIFT (Δ body-trigger accuracy)")
    print(f"{'='*80}")

    for repair_cond in ["minimal_repair", "length_control", "structured_repair"]:
        orig_bt = [r for r in positive if r["condition"] == "original" and r["query_type"] == "body_trigger"]
        repair_bt = [r for r in positive if r["condition"] == repair_cond and r["query_type"] == "body_trigger"]

        if orig_bt and repair_bt:
            orig_acc = accuracy(orig_bt)
            repair_acc = accuracy(repair_bt)
            delta = repair_acc - orig_acc
            print(f"  {repair_cond:25s}: {orig_acc*100:.1f}% → {repair_acc*100:.1f}% (Δ={delta*100:+.1f}pp)")

    # ── McNemar Tests ─────────────────────────────────────────────────────────
    print(f"\n{'='*80}")
    print("McNEMAR PAIRED TESTS (body-trigger)")
    print(f"{'='*80}")

    bt_results = [r for r in results if r["query_type"] == "body_trigger"]
    for repair_cond in ["minimal_repair", "length_control", "structured_repair"]:
        mc = mcnemar_test(bt_results, "original", repair_cond)
        print(f"  Original → {repair_cond:25s}: "
              f"repair_fixes={mc.get(f'{repair_cond}_only',0)}, "
              f"repair_breaks={mc.get('original_only',0)}, "
              f"p={mc['p_value']:.4f}")

    # ── Per-IC-Class Breakdown ────────────────────────────────────────────────
    print(f"\n{'='*80}")
    print("PER IC-CLASS BREAKDOWN (body-trigger accuracy)")
    print(f"{'='*80}")

    for ic in ["IC-1s", "IC-1q", "IC-0"]:
        print(f"\n  [{ic}]")
        for cond in conditions:
            subset = [r for r in positive if r["ic_class"] == ic
                     and r["condition"] == cond and r["query_type"] == "body_trigger"]
            if subset:
                acc = accuracy(subset)
                print(f"    {cond:25s}: {acc*100:.1f}% (n={len(subset)})")

    # ── Error Analysis ────────────────────────────────────────────────────────
    print(f"\n{'='*80}")
    print("ERROR ANALYSIS")
    print(f"{'='*80}")

    # Count empty/error responses
    empty = sum(1 for r in results if r.get("chosen_idx", -1) == -1)
    print(f"  Empty responses: {empty}/{len(results)} ({empty/len(results)*100:.1f}%)")

    # Failures where repair didn't help
    repair_bt = [r for r in positive if r["condition"] == "minimal_repair"
                and r["query_type"] == "body_trigger" and not r["correct"]]
    print(f"  Repair failures (body-trigger): {len(repair_bt)}")

    # By skill — which skills are hardest?
    skill_acc = defaultdict(lambda: {"correct": 0, "total": 0})
    for r in positive:
        if r["condition"] == "minimal_repair" and r["query_type"] == "body_trigger":
            skill_acc[r["skill_name"]]["total"] += 1
            if r["correct"]:
                skill_acc[r["skill_name"]]["correct"] += 1

    hardest = sorted(skill_acc.items(), key=lambda x: x[1]["correct"]/max(x[1]["total"],1))
    print(f"\n  Hardest skills (repair body-trigger):")
    for name, counts in hardest[:10]:
        pct = counts["correct"]/counts["total"]*100 if counts["total"] else 0
        print(f"    {name:40s}: {counts['correct']}/{counts['total']} ({pct:.0f}%)")

    # ── Save Outputs ──────────────────────────────────────────────────────────
    # Main results as markdown
    with open(RESULTS_DIR / "main_results_table.md", "w") as f:
        f.write("# Large-Scale Routing Repair Benchmark — Main Results\n\n")
        f.write(f"**Skills**: {n_skills} | **Routers**: {n_routers} | "
                f"**Total decisions**: {len(results)}\n\n")
        for line in table_lines:
            f.write(line + "\n")

    # Machine-readable summary
    analysis = {
        "n_skills": n_skills,
        "n_routers": n_routers,
        "n_results": len(results),
        "empty_rate": round(empty / len(results), 4),
        "conditions": {},
    }
    for cond, row in summary.items():
        analysis["conditions"][cond] = {
            k: v for k, v in row.items() if k != "condition"
        }
    with open(RESULTS_DIR / "analysis_summary.json", "w") as f:
        json.dump(analysis, f, indent=2)

    print(f"\n{'='*80}")
    print("ANALYSIS COMPLETE")
    print(f"  Table: {RESULTS_DIR / 'main_results_table.md'}")
    print(f"  Summary: {RESULTS_DIR / 'analysis_summary.json'}")
    print(f"{'='*80}")


if __name__ == "__main__":
    main()
