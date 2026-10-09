# Bias Benchmark — BiasBusters Scale
> Skill-selection bias measurement in agentic ecosystems (TrustDrift/SkillCalib)

## Overview

This benchmark measures and analyzes skill-selection bias following the methodology
of BiasBusters (ICLR 2026), adapted for the ClawHub skill marketplace.

**Scale**: 10 clusters × 5 skills × 100 queries × 5 rotations × 4 models × 3 runs = **60,000 calls** (EXP-1 alone)

## Quick Start

```bash
# 1. Build clusters from ClawHub data
python build_clusters.py

# 2. Refine to final 5 per cluster (needs OPENAI_API_KEY for LLM, or uses heuristic)
python refine_clusters.py

# 3. Generate pilot queries (no API needed)
python generate_sample_queries.py

# 4. Generate full queries (needs API)
OPENAI_API_KEY=... python generate_queries.py --all --n-queries 100

# 5. Run pilot (10 queries per cluster, ~500 calls)
OPENAI_API_KEY=... python run_bias_experiment.py --all --model gpt-4.1-mini --run 1

# 6. Analyze results
python feature_analysis.py
```

## Experiments

| EXP | Description | Calls | Script |
|-----|-------------|-------|--------|
| EXP-1 | Skill Selection Bias Benchmark | 60,000 | `run_bias_experiment.py` |
| EXP-2 | Description Perturbation (8 types) | 80,000 | `perturbations.py` + runner |
| EXP-3 | Routing Accuracy under Bias | 30,000 | (planned) |
| EXP-4 | Feature-Level Analysis | 0 (post-hoc) | `feature_analysis.py` |
| EXP-5 | Mitigation — Filter-then-Repair | 20,000 | (planned) |
| EXP-6 | End-to-End Agent Validation | 900 sessions | (planned) |

## Bias Metrics (from BiasBusters)

- **δ_API**: API-level total variation distance from uniform selection
- **δ_pos**: Position-level total variation distance from uniform
- **δ_model**: Mean of (δ_API + δ_pos)/2 across all clusters

## Files

```
bias_benchmark/
├── README.md                    # This file
├── build_clusters.py            # Step 1: Find cluster candidates
├── refine_clusters.py           # Step 2: LLM-verify & select final 5
├── generate_sample_queries.py   # Step 3a: Pilot queries (no API)
├── generate_queries.py          # Step 3b: Full query generation
├── run_bias_experiment.py       # Step 4: Multi-model inference runner
├── bias_metrics.py              # Core: TV distance metrics
├── perturbations.py             # EXP-2: 8 perturbation types
├── feature_analysis.py          # EXP-4: Feature regression analysis
├── cluster_candidates.json      # 15 candidates per cluster
├── clusters_final.json          # Final 5 per cluster
├── queries/                     # Generated queries
│   ├── web_search.json
│   ├── image_generation.json
│   └── ...
└── results/                     # Experiment outputs
    └── exp1_<model>_run<N>.json
```

## Models Supported

- GPT-4.1-mini (OpenAI)
- Claude 3.5 Sonnet (Anthropic)
- Gemini 2.5 Flash (Google)
- Qwen3-8B (OpenRouter / local)

## Reference

BiasBusters: Uncovering and Mitigating Tool Selection Bias in LLMs (ICLR 2026)
https://github.com/thierry123454/tool-selection-bias
