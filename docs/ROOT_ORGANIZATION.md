# Root Organization Notes

This note records the cleanup of top-level scripts and loose files.

## Keep At Repository Root

- `README.md`
- `pyproject.toml`
- `requirements.txt`
- `.env.example`
- `skills-lock.json`
- `src/`
- `tests/`
- `.venv/`
- `.agents/`
- `_archive/`

## Moved

- Top-level script entry points were split by task:
  - `data/collection_archive/scripts/`
  - `experiments/graph_first_composition/scripts/`
  - `experiments/cape_rule_filtering/scripts/`
  - `experiments/skillrugpull/scripts/`
- `REVIEW_STATE.json` moved to:
  `experiments/skillrugpull/run_logs/review_state/REVIEW_STATE_20260416T143200Z.json`
- Stage 1 graph-first docs moved to:
  `experiments/graph_first_composition/docs/stage1/`
- Research reference PDF moved to:
  `experiments/graph_first_composition/docs/references/`
- Collection logs and old DB backup moved to:
  - `data/collection_archive/runs/legacy_logs/`
  - `data/collection_archive/runs/legacy_backups/`

## Deleted

Generated clutter, empty directories, and compatibility stubs that are no
longer needed were deleted:

- `.pytest_cache/`
- `tests/__pycache__/`
- `src/clawhub_analyzer/__pycache__/`
- empty `trash/`
- empty `docs/operations/`
- empty `data/skills_sh/analysis_cleaned_pairs_20260322/`
- `scripts/README.md`
- `benchmark/README.md`
- `benchmark/skillcompbench/README.md`
- `docs/research/README.md`
