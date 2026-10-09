#!/usr/bin/env python3
"""
23_merge_ensemble.py — Merge 3-Model Extraction Results with Majority Vote
===========================================================================
Takes the per-model extraction results from 22_hybrid_extractor.py and
produces a single merged result using majority vote (≥2/3 models agree).

Usage (6,912 partial — legacy):
  python3 23_merge_ensemble.py \
    --model1 /scratch/gguo/results_qwen/hybrid_extraction_results.json \
    --model2 /scratch/gguo/results_mistral/hybrid_extraction_results.json \
    --model3 /scratch/gguo/results_llama/hybrid_extraction_results.json \
    --output results/ensemble_merged_partial.json

Usage (17,738 full — preferred):
  python3 23_merge_ensemble.py \
    --model1 /scratch/gguo/results_qwen_full/hybrid_extraction_results.json \
    --model2 /scratch/gguo/results_mistral_full/hybrid_extraction_results.json \
    --model3 /scratch/gguo/results_llama_full/hybrid_extraction_results.json \
    --output results/ensemble_merged.json
"""

import json
import argparse
import logging
from pathlib import Path
from collections import Counter
from typing import List, Dict, Set

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("merge")

LSCPS_FAMILIES = [
    "SHELL_EXEC", "CRED_READ", "CRED_TRANSMIT", "FILE_DELETE",
    "NET_WRITE", "AUTONOMOUS", "FILE_WRITE", "NET_READ",
    "FILE_READ", "MEMORY_WRITE", "MULTI_STEP", "CRED_STORE",
]


def classify_ic(c_vis: Set[str], c_act: Set[str]) -> dict:
    if not c_act:
        return {"ic_type": "IC-0", "cr": 1.0, "cp": 1.0,
                "undisclosed": [], "false_claims": []}
    intersection = c_act & c_vis
    undisclosed = c_act - c_vis
    false_claims = c_vis - c_act
    cr = len(intersection) / len(c_act) if c_act else 1.0
    cp = len(intersection) / len(c_vis) if c_vis else 1.0
    if c_act == c_vis:
        ic_type = "IC-0"
    elif not c_vis:
        ic_type = "IC-1"
    elif undisclosed and not false_claims:
        ic_type = "IC-2"
    elif false_claims and not undisclosed:
        ic_type = "IC-3"
    else:
        ic_type = "IC-4"
    return {"ic_type": ic_type, "cr": round(cr, 4), "cp": round(cp, 4),
            "undisclosed": sorted(undisclosed), "false_claims": sorted(false_claims)}


def majority_vote(cap_lists: List[List[str]], threshold: int = 2) -> List[str]:
    """Return capabilities that appear in >= threshold model results."""
    counter = Counter()
    for caps in cap_lists:
        for c in caps:
            counter[c] += 1
    return sorted([c for c, count in counter.items() if count >= threshold])


def main():
    parser = argparse.ArgumentParser(
        description="Merge 3-5 model extraction results with majority vote")
    # Required: 3 core models
    parser.add_argument("--model1", required=True, help="Qwen3-8B results JSON")
    parser.add_argument("--model2", required=True, help="Mistral-7B-v0.3 results JSON")
    parser.add_argument("--model3", required=True, help="Llama-3.1-8B results JSON")
    # Optional: additional models for 4- or 5-model ensemble
    parser.add_argument("--model4", default=None, help="Gemma-4-E4B results JSON (optional)")
    parser.add_argument("--model5", default=None, help="GPT-OSS-20B results JSON (optional)")
    parser.add_argument("--output", default="ensemble_merged.json")
    parser.add_argument("--report", default="ensemble_report.md")
    args = parser.parse_args()

    # Build model list (skip None entries)
    raw_paths = [args.model1, args.model2, args.model3, args.model4, args.model5]
    raw_names = ["Qwen3-8B", "Mistral-7B-v0.3", "Llama-3.1-8B",
                 "Gemma-4-E4B", "GPT-OSS-20B"]
    model_paths = [(n, p) for n, p in zip(raw_names, raw_paths) if p is not None]
    model_names = [n for n, _ in model_paths]
    n_models = len(model_paths)
    # Majority vote threshold: >50% of models
    mv_threshold = n_models // 2 + 1
    log.info(f"Ensemble: {n_models} models, majority vote >= {mv_threshold}")

    # Load all result files
    results = {}
    for i, (name, path) in enumerate(model_paths):
        log.info(f"Loading {name} from {path}")
        with open(path) as f:
            data = json.load(f)
        for record in data:
            sid = record["skill_id"]
            if sid not in results:
                results[sid] = {"models": [None] * n_models}
            results[sid]["models"][i] = record
        log.info(f"  Loaded {len(data)} skills")

    # Merge with majority vote
    merged = []
    ic_counts = Counter()
    agreement_stats = {"full_agree": 0, "two_agree": 0, "no_agree": 0}

    for sid, info in sorted(results.items()):
        models = info["models"]
        available = [m for m in models if m is not None]
        n_models = len(available)

        if n_models == 0:
            continue

        # Merge C_vis: majority vote across models
        c_vis_lists = [m.get("c_vis", []) for m in available]
        c_vis_merged = set(majority_vote(c_vis_lists, threshold=max(n_models // 2 + 1, 1)))

        # Merge C_act: majority vote across models
        c_act_lists = [m.get("c_act", []) for m in available]
        c_act_merged = set(majority_vote(c_act_lists, threshold=max(n_models // 2 + 1, 1)))

        # Also keep track34_code per model for analysis
        code_lists = [m.get("track34_code", m.get("track3_code", [])) for m in available]

        # IC Classification on merged sets
        ic = classify_ic(c_vis_merged, c_act_merged)

        # Agreement analysis
        ic_types = [m.get("ic_type", "") for m in available]
        if len(set(ic_types)) == 1:
            agreement_stats["full_agree"] += 1
        elif any(ic_types.count(t) >= 2 for t in set(ic_types)):
            agreement_stats["two_agree"] += 1
        else:
            agreement_stats["no_agree"] += 1

        record = {
            "skill_id": sid,
            "n_models": n_models,
            "c_vis": sorted(c_vis_merged),
            "c_act": sorted(c_act_merged),
            "per_model_c_vis": c_vis_lists,
            "per_model_c_act": c_act_lists,
            "per_model_ic": ic_types,
            **ic,
        }
        merged.append(record)
        ic_counts[ic["ic_type"]] += 1

    # Save merged results
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(merged, f, indent=2)
    log.info(f"Merged results: {len(merged)} skills → {output_path}")

    # Compute stats
    import numpy as np
    cr_vals = [r["cr"] for r in merged if r["c_act"]]
    cr_arr = np.array(cr_vals) if cr_vals else np.array([1.0])
    n = len(merged)

    # Generate report
    lines = [
        "# 3-Model Ensemble Extraction Report",
        "",
        f"**Models**: {', '.join(model_names)}",
        f"**Skills merged**: {n:,}",
        "",
        "## IC Distribution (Majority Vote)",
        "",
        "| IC Type | Count | % | Description |",
        "|---------|-------|---|-------------|",
    ]
    ic_desc = {
        "IC-0": "Consistent",
        "IC-1": "Complete Omission",
        "IC-2": "Partial Concealment",
        "IC-3": "False Advertisement",
        "IC-4": "Bidirectional Error",
    }
    for ic in ["IC-0", "IC-1", "IC-2", "IC-3", "IC-4"]:
        cnt = ic_counts.get(ic, 0)
        lines.append(f"| {ic} | {cnt:,} | {100*cnt/max(n,1):.1f}% | {ic_desc[ic]} |")

    lines += [
        "",
        "## Capability Recall",
        "",
        f"- Mean CR: {cr_arr.mean():.3f}",
        f"- Median CR: {float(np.median(cr_arr)):.3f}",
        f"- CR < 0.3 (inadequate): {int((cr_arr < 0.3).sum()):,} ({100*(cr_arr<0.3).mean():.1f}%)",
        "",
        "## Model Agreement",
        "",
        f"- 3/3 models agree on IC type: {agreement_stats['full_agree']:,} ({100*agreement_stats['full_agree']/max(n,1):.1f}%)",
        f"- 2/3 models agree: {agreement_stats['two_agree']:,} ({100*agreement_stats['two_agree']/max(n,1):.1f}%)",
        f"- No majority: {agreement_stats['no_agree']:,} ({100*agreement_stats['no_agree']/max(n,1):.1f}%)",
    ]

    report_path = Path(args.report)
    with open(report_path, "w") as f:
        f.write("\n".join(lines) + "\n")
    log.info(f"Report: {report_path}")

    # Summary
    log.info(f"\n{'='*60}")
    log.info(f"  IC: {dict(ic_counts)}")
    log.info(f"  Mean CR: {cr_arr.mean():.3f}")
    log.info(f"  Agreement: {agreement_stats}")
    log.info(f"{'='*60}")


if __name__ == "__main__":
    main()
