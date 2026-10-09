# Graph-First Composition Experiments

This folder is the canonical home for the C1 / hypergraph skill-composition attack line.

In paper language, this is the `graph-first` / `ecosystem-first` method: start from the skill ecosystem graph, statically mine plausible source-sink compositions, then use runtime experiments or benchmark cases to validate whether individually benign skills become risky together. The target is close to CAPE, but the method is different: CAPE is rule/profile-first, while this line is graph/hypergraph-first.

## Layout

- `data/analysis_bcr_cis_v3/`
  Latest BCR+CIS v3 hypergraph static screening output.
- `data/analysis_c1_static/`
  Latest C1 static composition-only screening output, including CAS/WCR candidate pools.
- `data/analysis_runtime_p25r_500/`
  Latest P25R runtime protocol, manifest, seed pool, and runtime summaries.
- `scripts/static_filtering/`
  Static graph/hypergraph mining scripts: BCR+CIS, C1 CAS, C1 WCR, evidence layers, and legacy helper analyses.
- `scripts/runtime_eval/`
  P25R runtime materialization, manifest construction, pair evaluation, and result analysis scripts.
- `benchmark/skillcompbench/`
  SkillCompBench schema and seed cases for composition-only benchmark tasks.
- `docs/`
  Threat model, C1/P27 notes, stage-2 reports, validity notes, and reviewer-facing summaries.
- `SRC_MODULES.md`
  Index of shared `src/clawhub_analyzer` modules used by this experiment line.

## Shared Inputs Kept Outside

The raw merged skill database is still shared at:

```text
data/experiment1_split/merged.sqlite3
```

The Python package modules remain in:

```text
src/clawhub_analyzer/
```

Those are shared infrastructure, so this folder stores the graph-first experiment line without duplicating the raw database or moving package code out of `src`.

## Main Pipeline

```bash
# 1. Build static hypergraph source-sink pairs.
python3 experiments/graph_first_composition/scripts/static_filtering/run_bcr_cis_v3.py

# 2. Add C1 composition-only static filters.
python3 experiments/graph_first_composition/scripts/static_filtering/run_c1_cas_analysis.py
python3 experiments/graph_first_composition/scripts/static_filtering/run_c1_wcr_experiment.py

# 3. Build runtime seed pool and P25R manifest.
python3 experiments/graph_first_composition/scripts/runtime_eval/build_p25r_seed_pool.py
python3 experiments/graph_first_composition/scripts/runtime_eval/build_p25r_manifest.py
```

Old top-level `scripts/...` entry points were moved to
`experiments/graph_first_composition/scripts/compat/` as compatibility wrappers.
