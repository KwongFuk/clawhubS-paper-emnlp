# ClawHub IC Index

The latest ClawHub capability-description inconsistency work still lives in
`../clawhub/` because that workspace shares configuration, data, and results
with CAPE experiments. This index keeps the trust/IC pieces discoverable without
breaking the shared CAPE setup.

## Core IC Scripts

- `../clawhub/27_main_experiment.py`
  Main ClawHub TrustDrift / IC experiment over the full corpus.
- `../clawhub/28_skillssh_main_experiment.py`
  skills.sh cross-platform validation with the same IC framework.
- `../clawhub/29_skillX_experiment.py`
  SkillX validation.
- `../clawhub/20_ic_temporal.py`
  Temporal IC analysis.
- `../clawhub/21_ic_crossplatform.py`
  Cross-platform IC classification.
- `../clawhub/26_cinstr_extractor.py`
  Capability-instruction extraction support.

## Review / Robustness Scripts

- `../clawhub/R1_robustness.py`
- `../clawhub/R2_baselines.py`
- `../clawhub/R3_ranking_eval.py`
- `../clawhub/R4_multiview.py`
- `../clawhub/E1_evasion_analysis.py`
- `../clawhub/E2_temporal_evolution.py`
- `../clawhub/E3_category_breakdown.py`
- `../clawhub/E4_annotate_heuristic.py`
- `../clawhub/E4_llm_security_relevance.py`
- `../clawhub/E6_triage_sr_lift.py`
- `../clawhub/E7_rule_vs_llm_consistency.py`

## Key Data Outputs

- `../clawhub/data/ic_classification.csv`
- `../clawhub/data/ic_crossplatform.csv`
- `../clawhub/data/ic_temporal.csv`
- `../clawhub/data/static_disclosure_gap.csv`
- `../clawhub/data/structured_sdg.csv`
- `../clawhub/data/yaml_alignment.csv`
- `../clawhub/data/taxonomy_classified.csv`
- `../clawhub/data/version_transitions.csv`
- `../clawhub/data/version_transitions_enriched.jsonl`

## Key Result Outputs

- `../clawhub/results/main_experiment_report.json`
- `../clawhub/results/main_experiment_results.jsonl`
- `../clawhub/results/skillssh_main_experiment_report.json`
- `../clawhub/results/skillssh_main_experiment_results.jsonl`
- `../clawhub/results/static_gap_report.md`
- `../clawhub/results/yaml_alignment_report.md`
- `../clawhub/results/structured_sdg_report.md`
- `../clawhub/results/temporal_ic_evolution_report.md`
- `../clawhub/results/ic_crossplatform_report.md`
- `../clawhub/results/R1_robustness_summary.md`
- `../clawhub/results/R2_baseline_table.md`
- `../clawhub/results/R3_ranking_table.md`
- `../clawhub/results/R4_multiview_report.md`
- `../clawhub/results/E7_consistency_report.md`

## Boundary

Files in `../clawhub/` whose names start with `cape_`, plus
`cape_e2e_experiment/`, `cape_mc_experiment/`, `rq2_toxic_pairs.py`, and
`victim_runner.py`, belong to the CAPE / dynamic composition line rather than
the trust capability-description mismatch line.
