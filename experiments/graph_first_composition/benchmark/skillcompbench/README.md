# SkillCompBench

`SkillCompBench` is a benchmark skeleton for evaluating compositional attacks in agent skill ecosystems under OpenClaw-compatible runtime environments.

## Layout

```text
experiments/graph_first_composition/benchmark/skillcompbench/
  README.md
  schema/
    case.schema.json
  cases/
    workspace_upgrade_without_approval_001.json
    device_actuation_from_sensitive_state_001.json
```

Runner status:

```text
not present in this checkout
```

The runner referenced in older notes is not present in this checkout. The
current package contains the schema and seed cases.

## Benchmark Modes

- `search mode`
  the evaluated system must choose which skills to compose from the provided `skill_pool`
- `fixed-composition mode`
  the case provides the exact pair or chain to execute

## Core Principle

A valid benchmark case must require multiple skills to realize the dangerous effect. The attacker can shape public inputs and task framing, but cannot forge platform-level authority or modify skill code.

## Current Cases

- `SCB_001`
  `workspace_upgrade_without_approval`
  search + offline execution supported
- `SCB_002`
  `device_actuation_from_sensitive_state`
  search supported, runtime harness not yet added

## Current Use

Validate or inspect the seed cases directly:

```bash
python -m json.tool experiments/graph_first_composition/benchmark/skillcompbench/cases/workspace_upgrade_without_approval_001.json >/dev/null
python -m json.tool experiments/graph_first_composition/benchmark/skillcompbench/cases/device_actuation_from_sensitive_state_001.json >/dev/null
```
