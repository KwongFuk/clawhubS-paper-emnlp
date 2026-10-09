# Current Mainline

> Last updated: 2026-04-15  
> Cleanup status: archived Stage 2 plans, experiments, and legacy runners were physically removed from this repository in this cleanup pass.

## 1. Current Active Mainline

### 1.1 Canonical definitions

- [THREAT_MODEL.md](/Users/guangfu/codex/clawhubS/experiments/graph_first_composition/docs/THREAT_MODEL.md)
- [LATEST_DEFINITION_AND_VERSIONS.md](/Users/guangfu/codex/clawhubS/experiments/graph_first_composition/docs/LATEST_DEFINITION_AND_VERSIONS.md)

Current authority:

- attacker model: `Passive Adversary`
- unit of analysis: `pair-only`
- static pipeline: `BCR -> CIS -> PH`
- success criterion: `AConC`

Separate current proposal line:

- [COMPOSITION_ONLY_ARTICLE.md](/Users/guangfu/codex/clawhubS/experiments/graph_first_composition/docs/COMPOSITION_ONLY_ARTICLE.md)
- [P27_COMPOSITION_ONLY_PROTOCOL.md](/Users/guangfu/codex/clawhubS/experiments/graph_first_composition/docs/P27_COMPOSITION_ONLY_PROTOCOL.md)
- [C1_COMPOSITION_EXPERIMENT.md](/Users/guangfu/codex/clawhubS/experiments/graph_first_composition/docs/C1_COMPOSITION_EXPERIMENT.md)

These remain intentionally separate from the default `P25R` mainline.

### 1.2 Active static-analysis code and outputs

- [run_bcr_cis_v3.py](/Users/guangfu/codex/clawhubS/experiments/graph_first_composition/scripts/static_filtering/run_bcr_cis_v3.py)
- [hypergraph.py](/Users/guangfu/codex/clawhubS/src/clawhub_analyzer/hypergraph.py)
- [bcr_cis_v3_summary.md](/Users/guangfu/codex/clawhubS/experiments/graph_first_composition/data/analysis_bcr_cis_v3/bcr_cis_v3_summary.md)
- [bcr_cis_v3_pairs.csv](/Users/guangfu/codex/clawhubS/experiments/graph_first_composition/data/analysis_bcr_cis_v3/bcr_cis_v3_pairs.csv)
- [bcr_cis_v3_evidence_layers.md](/Users/guangfu/codex/clawhubS/experiments/graph_first_composition/data/analysis_bcr_cis_v3/bcr_cis_v3_evidence_layers.md)

### 1.3 Active runtime protocol line

- [P25R_UNIFIED_PROTOCOL_V4.md](/Users/guangfu/codex/clawhubS/experiments/graph_first_composition/data/analysis_runtime_p25r_500/P25R_UNIFIED_PROTOCOL_V4.md)
- [README.md](/Users/guangfu/codex/clawhubS/experiments/graph_first_composition/data/analysis_runtime_p25r_500/README.md)
- [p25r_manifest.json](/Users/guangfu/codex/clawhubS/experiments/graph_first_composition/data/analysis_runtime_p25r_500/p25r_manifest.json)
- [build_p25r_seed_pool.py](/Users/guangfu/codex/clawhubS/experiments/graph_first_composition/scripts/runtime_eval/build_p25r_seed_pool.py)
- [build_p25r_manifest.py](/Users/guangfu/codex/clawhubS/experiments/graph_first_composition/scripts/runtime_eval/build_p25r_manifest.py)
- [setup_p25r_real_skills.py](/Users/guangfu/codex/clawhubS/experiments/graph_first_composition/scripts/runtime_eval/setup_p25r_real_skills.py)
- [run_p25r_pair_eval.py](/Users/guangfu/codex/clawhubS/experiments/graph_first_composition/scripts/runtime_eval/run_p25r_pair_eval.py)
- [analyze_p25r_results.py](/Users/guangfu/codex/clawhubS/experiments/graph_first_composition/scripts/runtime_eval/analyze_p25r_results.py)

### 1.4 Active runtime evidence boundary

- [RUNTIME_VALIDITY_STATUS_2026-03-27.md](/Users/guangfu/codex/clawhubS/experiments/graph_first_composition/docs/RUNTIME_VALIDITY_STATUS_2026-03-27.md)

## 2. What Was Removed

The cleanup pass removed older Stage 2 materials that had already been superseded:

- historical static outputs before `analysis_bcr_cis_v3`
- historical runtime lines before `P25R unified-protocol v4`
- the archived `P26` protocol line and its local experiment outputs
- legacy Stage 2 runner scripts tied to those removed lines
- outdated research-plan documents that no longer matched the current repository state

## 3. Default Reading Order

1. Read [THREAT_MODEL.md](/Users/guangfu/codex/clawhubS/experiments/graph_first_composition/docs/THREAT_MODEL.md).
2. Read [bcr_cis_v3_summary.md](/Users/guangfu/codex/clawhubS/experiments/graph_first_composition/data/analysis_bcr_cis_v3/bcr_cis_v3_summary.md) and [bcr_cis_v3_evidence_layers.md](/Users/guangfu/codex/clawhubS/experiments/graph_first_composition/data/analysis_bcr_cis_v3/bcr_cis_v3_evidence_layers.md).
3. Read [P25R_UNIFIED_PROTOCOL_V4.md](/Users/guangfu/codex/clawhubS/experiments/graph_first_composition/data/analysis_runtime_p25r_500/P25R_UNIFIED_PROTOCOL_V4.md), [README.md](/Users/guangfu/codex/clawhubS/experiments/graph_first_composition/data/analysis_runtime_p25r_500/README.md), and [p25r_manifest.json](/Users/guangfu/codex/clawhubS/experiments/graph_first_composition/data/analysis_runtime_p25r_500/p25r_manifest.json).
4. Read [RUNTIME_VALIDITY_STATUS_2026-03-27.md](/Users/guangfu/codex/clawhubS/experiments/graph_first_composition/docs/RUNTIME_VALIDITY_STATUS_2026-03-27.md) before making runtime claims.
5. Read [COMPOSITION_ONLY_ARTICLE.md](/Users/guangfu/codex/clawhubS/experiments/graph_first_composition/docs/COMPOSITION_ONLY_ARTICLE.md), [P27_COMPOSITION_ONLY_PROTOCOL.md](/Users/guangfu/codex/clawhubS/experiments/graph_first_composition/docs/P27_COMPOSITION_ONLY_PROTOCOL.md), and [C1_COMPOSITION_EXPERIMENT.md](/Users/guangfu/codex/clawhubS/experiments/graph_first_composition/docs/C1_COMPOSITION_EXPERIMENT.md) only when working on the separate composition-only proposal line.
