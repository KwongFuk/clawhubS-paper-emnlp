# Shared Source Modules

The graph-first composition line uses these shared package modules from `src/clawhub_analyzer/`.

They remain in `src` because other CLI code, tests, and scripts import them as package modules.

## Hypergraph / Static Mining

- `hypergraph.py`
- `hypergraph_pairs.py`
- `graph2_pairs.py`
- `pair_plausibility.py`
- `toxicity.py`

## C1 / Skill Composition

- `c1_wcr.py`
- `sc_stateful_pairs.py`
- `sc_candidate_universe.py`
- `sc_seed_pool.py`
- `sc_runtime_manifest.py`
- `sc_sampling.py`
- `sc_heuristics.py`

## Runtime Protocol

- `p25r_protocol.py`
