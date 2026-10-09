# Runtime Validity Status — 2026-03-27

> Scope: current reviewer-facing validity note for the `P25R unified-protocol v4` runtime line.

## Bottom Line

`P25R unified-protocol v4` is the current **runtime protocol mainline**, but the latest completed full run is **not yet usable as runtime evidence**.

The correct reading is:

- the protocol design and manifest construction are current mainline
- the latest completed runtime outputs are currently **infrastructure-limited**
- current runtime negatives must **not** be interpreted as safety evidence

## What Is Valid Today

The repository can currently support the following runtime claims:

1. Real marketplace-derived skill pairs can be mapped into isolated sink-family case templates.
2. The current runtime protocol keeps the literal pair and conservative user task fixed across the strict direct-evidence arms.
3. Runtime outcome analysis now explicitly separates infrastructure-limited trials from usable negatives and usable executions.

## What Is Not Valid Today

The repository cannot currently support the following claims:

1. That the latest 500-case `P25R` batch has already produced a usable direct-`AConC` rate estimate.
2. That the currently completed `P25R` trials are valid evidence of safety or absence of risk.
3. That the current runtime implementation has already satisfied clean per-trial session isolation at execution time.

## Current Confounds

### 1. Infrastructure-limited completed trials

The latest reviewed full run produced completed trials that were dominated by rate-limit / timeout / service-unavailable style failures. These belong to the `infra_error` bucket and do not count as valid negatives.

### 2. Session non-isolation at the OpenClaw agent layer

Current local inspection shows the `p25r` agent reusing a shared session key (`agent:p25r:main`) rather than maintaining clean trial-isolated session state for each runtime turn.

Reviewer implication:

- context can bleed across trials
- token/context growth can distort later turns
- trial independence is weakened

### 3. Session-file lock contention

Current local gateway logs show session lock conflicts on the shared `p25r` session file.

Reviewer implication:

- failed turns may reflect agent-state contention rather than experimental semantics
- repeated runtime failures are partly an execution-environment artifact

### 4. Provider quota / rate-limit exhaustion

Current local session history and gateway logs show repeated provider-side quota or rate-limit failures.

Reviewer implication:

- runtime non-execution cannot currently be read as evidence that the pair would not execute under valid provider availability

## Current Safe Paper Wording

Reviewer-safe wording should be:

- static layer: current evidence supports large-scale structural pair discovery, with stronger static support concentrated in the ecological and higher-confidence subsets
- runtime layer: current repository contains the current `P25R` protocol and manifest, but the latest full run remains infrastructure-limited and does not yet yield usable negative or positive rate estimates

Avoid wording such as:

- “the 500-case runtime batch was negative”
- “the runtime study found no violations”
- “the current runtime results show safety”

## Minimum Requirements Before Claiming Runtime Evidence

Before reporting runtime rates as paper evidence, the implementation should first satisfy all of the following:

1. Per-trial session isolation for the runtime agent.
2. No shared-session lock contention across concurrent or sequential trial turns.
3. Provider availability sufficient to produce usable source and sink executions in smoke runs.
4. At least a small smoke batch showing real lab interaction before scaling back to the full batch.

## Recommended Citation Behavior

When writing the current paper draft:

- cite [P25R_UNIFIED_PROTOCOL_V4.md](/Users/guangfu/codex/clawhubS/experiments/graph_first_composition/data/analysis_runtime_p25r_500/P25R_UNIFIED_PROTOCOL_V4.md) for the runtime design
- cite [README.md](/Users/guangfu/codex/clawhubS/experiments/graph_first_composition/data/analysis_runtime_p25r_500/README.md) for the batch structure
- cite this file for the current evidence-validity boundary
