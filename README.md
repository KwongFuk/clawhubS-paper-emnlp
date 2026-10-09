# ClawHub Skill Ecosystem Research Artifacts

This repository contains the source code, tests, experiment drivers, and paper
sources for research on listing adequacy, routing, risk signals, and skill
composition in LLM-agent marketplaces.

The EMNLP paper draft is in [`paper-emnlp/`](paper-emnlp/). The main research
lines are organized under [`experiments/`](experiments/):

- `skillrugpull/`: capability-description mismatch and listing-quality studies.
- `graph_first_composition/`: graph-first composition screening and runtime evaluation.
- `cape_rule_filtering/`: CAPE profile/rule-based filtering experiments.
- `risk_agent/`: deterministic and LLM-assisted risk analysis.

The newer description and routing experiments are under [`skill-X/`](skill-X/).
Reusable crawler and analysis modules are under [`src/clawhub_analyzer/`](src/clawhub_analyzer/).
Historical first-party experiment scripts are retained in their original
`archive/` paths for provenance; generated data and third-party clones are not.

## Installation

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -U pip
python -m pip install -r requirements.txt
```

For the broader experiment suite:

```bash
python -m pip install -r requirements-experiments.txt
```

## Tests

```bash
python -m pytest -q
```

## Data availability

Large raw marketplace snapshots are not stored in this repository. The local
workspace used to prepare this release no longer contained the original
80,000+ downloaded skill corpus, so this release does not claim to include it.

The accompanying GitHub Release contains the paper and aggregate, public-safe
artifacts that were still available. It intentionally excludes private machine
configuration, caches, third-party repository clones, raw per-skill risk labels,
and records that could identify skills flagged by automated screening.

Several historical experiment scripts retain machine-specific path constants.
Adjust those paths or environment variables before rerunning them on another
system. See [`ARTIFACTS.md`](ARTIFACTS.md) for scope and limitations.

## Ethical use

The code is intended for academic analysis of publicly available marketplace
data. Do not use it to access private resources, bypass platform controls, or
publish unverified accusations about individual skill authors.

## License

MIT. See [`LICENSE`](LICENSE).
