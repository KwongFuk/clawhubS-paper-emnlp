# Reviewer Issues And Fixes — 2026-03-27

> Scope: paper-level and experiment-level reviewer issues identified in the current repository, with fix status.

## A. Issues Fixed In This Pass

### A1. Mainline documentation was inconsistent about the current runtime line

Problem:

- some top-level Stage 2 documents still described `P25` or `P25.2` as the current runtime mainline
- other authority documents had already moved to `P25R unified-protocol v4`

Reviewer risk:

- version confusion
- apparent method drift
- easy criticism that the paper has no stable current mainline

Fix:

- unified the top-level Stage 2 documents around `analysis_bcr_cis_v3` + `P25R unified-protocol v4`
- moved `P25` / `P25.2` / `P23` / `P24` / `P26` into explicit archive lineage language

Key files:

- [CURRENT_MAINLINE_AND_ARCHIVE.md](/Users/guangfu/codex/clawhubS/experiments/graph_first_composition/docs/CURRENT_MAINLINE_AND_ARCHIVE.md)
- [STAGE2_REPORT.md](/Users/guangfu/codex/clawhubS/experiments/graph_first_composition/docs/stage2/STAGE2_REPORT.md)
- [STAGE2_EXPERIMENTS.md](/Users/guangfu/codex/clawhubS/experiments/graph_first_composition/docs/stage2/STAGE2_EXPERIMENTS.md)
- [STAGES_INDEX.md](/Users/guangfu/codex/clawhubS/experiments/graph_first_composition/docs/STAGES_INDEX.md)

### A2. The paper-facing docs did not clearly separate runtime protocol mainline from runtime evidence usability

Problem:

- the repository already had a current runtime protocol line
- but the current full run was still infra-limited
- this distinction was not explicit enough in the main reading path

Reviewer risk:

- a reviewer may read “current runtime mainline” as “current runtime evidence is already valid”
- current negatives could be over-read as evidence of safety

Fix:

- added an explicit runtime validity boundary document
- linked it from the threat model, version map, Stage 2 report, Stage 2 experiments, and the runtime batch README

Key files:

- [RUNTIME_VALIDITY_STATUS_2026-03-27.md](/Users/guangfu/codex/clawhubS/experiments/graph_first_composition/docs/RUNTIME_VALIDITY_STATUS_2026-03-27.md)
- [THREAT_MODEL.md](/Users/guangfu/codex/clawhubS/experiments/graph_first_composition/docs/THREAT_MODEL.md)
- [LATEST_DEFINITION_AND_VERSIONS.md](/Users/guangfu/codex/clawhubS/experiments/graph_first_composition/docs/LATEST_DEFINITION_AND_VERSIONS.md)
- [README.md](/Users/guangfu/codex/clawhubS/experiments/graph_first_composition/data/analysis_runtime_p25r_500/README.md)

### A3. Static evidence strength was too easy to overstate

Problem:

- readers could still collapse the full 10k candidate pool into one undifferentiated “evidence” layer

Reviewer risk:

- overclaiming static precision
- weak separation between candidate discovery and stronger support

Fix:

- formalized evidence-layer usage in the top-level docs
- current Stage 2 report now explicitly instructs paper writing to use evidence layers

Key files:

- [bcr_cis_v3_evidence_layers.md](/Users/guangfu/codex/clawhubS/experiments/graph_first_composition/data/analysis_bcr_cis_v3/bcr_cis_v3_evidence_layers.md)
- [STAGE2_REPORT.md](/Users/guangfu/codex/clawhubS/experiments/graph_first_composition/docs/stage2/STAGE2_REPORT.md)

### A4. Runtime validity requirements were implicit rather than explicit

Problem:

- the protocol docs described the semantic design, but did not clearly say that session isolation and provider availability are preconditions for valid runtime evidence

Reviewer risk:

- a reviewer can argue that the runtime implementation assumptions were underspecified

Fix:

- added explicit execution-validity requirements to the canonical runtime protocol doc and batch README

Key files:

- [P25R_UNIFIED_PROTOCOL_V4.md](/Users/guangfu/codex/clawhubS/experiments/graph_first_composition/data/analysis_runtime_p25r_500/P25R_UNIFIED_PROTOCOL_V4.md)
- [README.md](/Users/guangfu/codex/clawhubS/experiments/graph_first_composition/data/analysis_runtime_p25r_500/README.md)

## B. Issues Still Open After This Pass

### B1. Current full `P25R` run is still infrastructure-limited

Current status:

- latest completed runtime outputs still do not provide a usable direct-`AConC` rate estimate

Why still open:

- this requires a fresh rerun after environment repair, not only documentation cleanup

### B2. The `p25r` OpenClaw agent currently reuses shared session state

Current status:

- local inspection shows reuse of `agent:p25r:main`

Reviewer risk:

- trial independence is weakened
- context bleed and token growth can confound later trials

Why still open:

- this is an execution-environment / OpenClaw session-isolation issue, not a paper-only wording problem

### B3. Session-file lock contention exists in the current OpenClaw runtime path

Current status:

- local gateway logs show lock contention on the shared `p25r` session file

Reviewer risk:

- runtime failures may partly reflect environment contention rather than semantic failure

Why still open:

- this requires execution-environment repair and rerun

### B4. Provider quota / rate-limit exhaustion remains active

Current status:

- local gateway/session history still shows repeated provider-side quota or rate-limit failures

Reviewer risk:

- runtime non-execution is not interpretable as pair non-executability

Why still open:

- this requires provider availability restoration before a valid smoke rerun

## C. Reviewer-Safe Current Claim Set

The current repository can support the following paper-level claim set:

1. The static pipeline has converged to `BCR -> CIS -> PH`, with explicit evidence layering.
2. The repository contains a threat-model-aligned current runtime protocol (`P25R unified-protocol v4`) and a 500-case manifest derived from a latest-static seed pool.
3. The current runtime analyzer correctly separates infrastructure-limited trials from usable negatives and usable executions.
4. The latest full runtime run is still infrastructure-limited, so the repository should not yet report a final runtime violation rate.
