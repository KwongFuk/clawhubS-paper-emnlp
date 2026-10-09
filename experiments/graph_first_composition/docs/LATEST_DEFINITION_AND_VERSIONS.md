# Latest Definition And Version Map

> Last updated: 2026-04-15  
> Cleanup note: older Stage 2 plans, experiments, and legacy runners were removed from the repository in this cleanup pass. This file now maps only the retained latest materials plus the current proposal line.

## Current Authority

| Scope | Current authority | Notes |
|---|---|---|
| Threat model definition | [THREAT_MODEL.md](/Users/guangfu/codex/clawhubS/experiments/graph_first_composition/docs/THREAT_MODEL.md) | Canonical definitions for Passive Adversary, pair-only analysis, AConC, BCR, CIS, and PH. |
| Separate composition-only article line | [COMPOSITION_ONLY_ARTICLE.md](/Users/guangfu/codex/clawhubS/experiments/graph_first_composition/docs/COMPOSITION_ONLY_ARTICLE.md) | Distinguishes singleton-safe / jointly-unsafe claims from the default resource-contamination mainline. |
| Separate composition-only experiment line | [C1_COMPOSITION_EXPERIMENT.md](/Users/guangfu/codex/clawhubS/experiments/graph_first_composition/docs/C1_COMPOSITION_EXPERIMENT.md) | Defines the proposed end-to-end `C1` experiment from static `CAS` filtering to dynamic co-install / joint-invocation validation. |
| Mainline map | [CURRENT_MAINLINE_AND_ARCHIVE.md](/Users/guangfu/codex/clawhubS/experiments/graph_first_composition/docs/CURRENT_MAINLINE_AND_ARCHIVE.md) | Defines what remains as the active default path. |
| Latest static pair analysis | [bcr_cis_v3_summary.md](/Users/guangfu/codex/clawhubS/experiments/graph_first_composition/data/analysis_bcr_cis_v3/bcr_cis_v3_summary.md) | Current static mainline. |
| Latest static evidence layers | [bcr_cis_v3_evidence_layers.md](/Users/guangfu/codex/clawhubS/experiments/graph_first_composition/data/analysis_bcr_cis_v3/bcr_cis_v3_evidence_layers.md) | Separates the 10k candidate pool from stronger support layers. |
| Latest static code path | [run_bcr_cis_v3.py](/Users/guangfu/codex/clawhubS/experiments/graph_first_composition/scripts/static_filtering/run_bcr_cis_v3.py) | Current canonical Stage 2 static-analysis script. |
| Latest static pair table | [bcr_cis_v3_pairs.csv](/Users/guangfu/codex/clawhubS/experiments/graph_first_composition/data/analysis_bcr_cis_v3/bcr_cis_v3_pairs.csv) | Top-10k `critical×cross-domain` pairs after the current filters. |
| Runtime protocol mainline | [P25R_UNIFIED_PROTOCOL_V4.md](/Users/guangfu/codex/clawhubS/experiments/graph_first_composition/data/analysis_runtime_p25r_500/P25R_UNIFIED_PROTOCOL_V4.md) | Canonical runtime protocol reference for `P25R unified-protocol v4`. |
| Runtime batch overview | [README.md](/Users/guangfu/codex/clawhubS/experiments/graph_first_composition/data/analysis_runtime_p25r_500/README.md) | Unified latest-static runtime batch for `P25R unified-protocol v4`. |
| Runtime result analyzer | [analyze_p25r_results.py](/Users/guangfu/codex/clawhubS/experiments/graph_first_composition/scripts/runtime_eval/analyze_p25r_results.py) | Separates infrastructure failures, no-tool-call negatives, benign execution, and dangerous side effects. |
| Runtime evidence validity status | [RUNTIME_VALIDITY_STATUS_2026-03-27.md](/Users/guangfu/codex/clawhubS/experiments/graph_first_composition/docs/RUNTIME_VALIDITY_STATUS_2026-03-27.md) | Current boundary on what the repository can and cannot claim from the latest runtime outputs. |

## Latest Definitions

| Concept | Current definition | Replaces / clarifies |
|---|---|---|
| Attacker model | `Passive Adversary`: attacker does not publish or modify any skill. | Narrows scope away from active tool poisoning / rug-pull scenarios. |
| Unit of analysis | `pair-only` (`source -> sink`). | Replaces earlier chain-oriented mainlines. |
| Success criterion | `AConC` violation: observable side effects exceed user-authorized effects. | Replaces looser score-centric notions of risk. |
| Separate article criterion | `CO-AConC` violation: forbidden effect appears only under multi-skill composition while matched singleton controls remain safe. | Used only for the separate composition-only line. |
| Stage 1 | `BCR`: binary capability reachability filter. | No score; pass/fail only. |
| Stage 2 | `CIS`: categorical severity + domain-crossing labels. | Derived directly from node labels. |
| Stage 3 | `PH`: prioritization heuristic. | Ordering only; not a risk measure. |

## Retained Version Line

| Layer | Current retained version | Status |
|---|---|---|
| Static analysis | `analysis_bcr_cis_v3` | Current mainline |
| Runtime protocol | `P25R unified-protocol v4` | Current mainline |
| Composition-only protocol | `P27` | Current proposal line |
| Composition-only end-to-end experiment | `C1` | Current proposal line |

## Current Reading Of Key Numbers

- `10,000 critical×cross-domain pairs` refers to the current [bcr_cis_v3_summary.md](/Users/guangfu/codex/clawhubS/experiments/graph_first_composition/data/analysis_bcr_cis_v3/bcr_cis_v3_summary.md) candidate pool.
- `66` refers to the ecologically supported subset described in [bcr_cis_v3_evidence_layers.md](/Users/guangfu/codex/clawhubS/experiments/graph_first_composition/data/analysis_bcr_cis_v3/bcr_cis_v3_evidence_layers.md).
- `3` refers to the higher-confidence support subset described in [bcr_cis_v3_evidence_layers.md](/Users/guangfu/codex/clawhubS/experiments/graph_first_composition/data/analysis_bcr_cis_v3/bcr_cis_v3_evidence_layers.md).

## Recommended Usage

- Cite [THREAT_MODEL.md](/Users/guangfu/codex/clawhubS/experiments/graph_first_composition/docs/THREAT_MODEL.md) for formal definitions.
- Cite [bcr_cis_v3_summary.md](/Users/guangfu/codex/clawhubS/experiments/graph_first_composition/data/analysis_bcr_cis_v3/bcr_cis_v3_summary.md), [bcr_cis_v3_pairs.csv](/Users/guangfu/codex/clawhubS/experiments/graph_first_composition/data/analysis_bcr_cis_v3/bcr_cis_v3_pairs.csv), and [bcr_cis_v3_evidence_layers.md](/Users/guangfu/codex/clawhubS/experiments/graph_first_composition/data/analysis_bcr_cis_v3/bcr_cis_v3_evidence_layers.md) for the current static layer.
- Cite [P25R_UNIFIED_PROTOCOL_V4.md](/Users/guangfu/codex/clawhubS/experiments/graph_first_composition/data/analysis_runtime_p25r_500/P25R_UNIFIED_PROTOCOL_V4.md) and [README.md](/Users/guangfu/codex/clawhubS/experiments/graph_first_composition/data/analysis_runtime_p25r_500/README.md) for the current runtime protocol.
- Cite [RUNTIME_VALIDITY_STATUS_2026-03-27.md](/Users/guangfu/codex/clawhubS/experiments/graph_first_composition/docs/RUNTIME_VALIDITY_STATUS_2026-03-27.md) whenever you state the current runtime evidence boundary.
