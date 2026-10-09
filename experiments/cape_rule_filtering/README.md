# CAPE Rule Filtering

This is the standalone CAPE experiment line under `experiments/`.

CAPE is the rule/profile-first approach for skill composition risk discovery:
it extracts source/sink/channel capability profiles, applies rule-based pair
filters, adjudicates candidate pairs, and runs E2E / MC validation.

## Layout

- `scripts/`
  CAPE extraction, static filtering, adjudication, pair detection, and cluster /
  vLLM helper scripts.
- `results/`
  Canonical CAPE RQ2 profiles, pair inventories, adjudication outputs, toxic
  pair reports, and summary tables.
- `cape_e2e_experiment/`
  End-to-end runtime validation assets, Codex/MCP task files, SkillSwap
  experiments, and runtime outputs.
- `cape_mc_experiment/`
  Model-controller / Monte-Carlo runtime variants and outputs.
- `paper/`
  CAPE paper draft and related LaTeX assets.
- `paper_sections/`
  CAPE section snippets shared with the broader paper tree.

## Shared Code

Reusable CAPE implementation lives in:

```text
src/clawhub_analyzer/cape/
```

This experiment folder keeps the runnable scripts, generated data, results,
and paper artifacts together.

## Main Entry Points

```bash
python experiments/cape_rule_filtering/scripts/cape_rq2_full_run.py
python experiments/cape_rule_filtering/scripts/cape_toxic_pair_pipeline.py
python experiments/cape_rule_filtering/scripts/cape_pipeline_stats.py
```

Runtime validation entry points are under:

```text
experiments/cape_rule_filtering/cape_e2e_experiment/
experiments/cape_rule_filtering/cape_mc_experiment/
```
