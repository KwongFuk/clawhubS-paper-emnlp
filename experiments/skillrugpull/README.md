# SkillRugPull / Trust Experiments

This directory is now the top-level organizer for trust-drift and
capability-description mismatch work.

## Layout

- `trust_capability_mismatch/`
  Canonical skills.sh trust mismatch pipeline. Contains scripts, data, results,
  annotation files, and the runnable `run_all.sh`.
- `clawhub/`
  ClawHub IC / capability-description inconsistency implementation and related
  results. CAPE materials were moved out to `../cape_rule_filtering/`.
- `paper/`
  Main TrustDrift / CapMismatch paper source, compiled PDFs, figures, and paper archive.
- `docs/`
  Current and archived project notes, summaries, review logs, and handoff docs.
- `run_logs/`
  Historical auto-loop logs for skills.sh / SC research runs.
- `scripts/`
  SkillRugPull-specific helper scripts that used to live under `scripts/archive`.

## Main Trust Pipeline

```bash
bash experiments/skillrugpull/trust_capability_mismatch/run_all.sh
```

## Latest ClawHub IC Pointers

See:

```text
experiments/skillrugpull/trust_capability_mismatch/CLAWHUB_IC_INDEX.md
```

## Paper

```bash
cd experiments/skillrugpull/paper
make
```
