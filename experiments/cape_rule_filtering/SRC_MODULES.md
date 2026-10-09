# CAPE Source Modules

CAPE uses shared library code from the repository `src/` tree instead of
duplicating it inside the experiment folder.

## Shared Package

```text
src/clawhub_analyzer/cape/
```

This package contains the reusable profile extraction, capability taxonomy, and
toxic-pair utilities used by the CAPE scripts.

## Experiment-Owned Files

```text
experiments/cape_rule_filtering/scripts/
experiments/cape_rule_filtering/cape_e2e_experiment/
experiments/cape_rule_filtering/cape_mc_experiment/
experiments/cape_rule_filtering/results/
```

These directories are the canonical CAPE experiment artifacts.
