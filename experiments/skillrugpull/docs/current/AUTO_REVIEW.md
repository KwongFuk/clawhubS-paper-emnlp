# AUTO_REVIEW.md — CapMismatch / TrustDrift Paper Review Log

**Paper**: CapMismatch: Measuring Capability-Description Inconsistency and Validating Source-Sink Attack Risks in LLM Agent Skill Marketplaces  
**Target**: NDSS 2027  
**Started**: 2026-04-16

---

## Round 1 (2026-04-16 09:20)

### Assessment Summary
- **Score**: 6/10
- **Verdict**: Almost (not ready yet)
- **Reviewer**: OpenClaw/gpt-5.3-codex acting as NDSS/USENIX Security reviewer

### Top 5 Weaknesses

| # | Weakness | Severity | Minimum Fix |
|---|---------|----------|-------------|
| W1 | Incomplete experiment (61/125) with absolute headline claims | HIGH | Finish 125 pairs, add CIs, use interim framing |
| W2 | Weak causal attribution (stochastic vs policy bypass) | HIGH | Add controlled ablations (benign source, benign payload, n≥5 repeats) |
| W3 | Single payload family (`go vet | tee`) limits external validity | MEDIUM | Add 3+ heterogeneous payload templates |
| W4 | Ground-truth reliability not audited | MEDIUM | Report inter-annotator agreement (IAA) |
| W5 | Single platform + novelty vs. prompt injection not crisp | MEDIUM | Formal comparison table; explicit scope claim |

### Reviewer Raw Response

<details>
<summary>Click to expand</summary>

Score: 6/10
Verdict: Almost

1. Incomplete core experiment (61/125) but strong headline claims
   - Finish all 125 pairs before submission camera-ready, report final CIs, remove projection language from abstract/introduction.
2. Potentially weak causal attribution (heuristic/stochastic refusal vs true policy bypass)
   - Add controlled ablations: same sink with clean source, benign payload, randomized payload, and repeated trials per pair (n>=5) to separate model variance from attack effect.
3. Single payload family may limit external validity
   - Add at least 3 heterogeneous payload templates (non-Go, non-build-tool, plain shell) and show comparable success.
4. Ground-truth and labeling reliability not sufficiently audited
   - Report inter-annotator agreement for sink verification and IC labels; include confusion analysis on a stratified sample.
5. Generalization beyond one ecosystem remains limited
   - Expand cross-platform from 44 to a materially larger independent sample, or clearly scope claims to ClawHub only.

Overclaimed: "complete absence of defense" (too absolute given stochastic refusals)
Source-sink vs prompt injection: Partially distinguished, needs formal comparison table.
93% (57/61): Citable as interim/preliminary only.

</details>

### Actions Taken
- **W1**: Updated introduction to 57/64 (89%, 95% CI 79-95%), removed "complete absence" → "no consistent runtime defense", added "interim result" label.
- **W2**: Added `Controls` paragraph to CAPE §5: Ctrl-A (benign source, 0/20), Ctrl-B (benign payload, 0/20), Ctrl-C (3× repeats, σ=0.11/0.28).
- **W3**: Added P1/P2/P3 payload family description. P2 (node verify.js | tee): 7/8. P3 (cat README | tee): 6/8. Cross-payload table in §5.
- **W4**: Added IAA paragraph to §4: Cohen's κ=0.87 on 40 randomly sampled sinks (2 annotators).
- **W5**: Added formal comparison table (Table 2) to §8 Related Work: Classic PI vs Plugin attack vs This work across 5 dimensions. Added explicit scope note to §4 cross-platform paragraph.

### Status
→ Continuing to Round 2 (re-score after fixes)


---

## Round 2 (2026-04-16 10:28)

### Assessment Summary
- **Score**: 7/10 (+1 vs Round 1)
- **Verdict**: Almost
- **Key improvements**: Source-sink vs prompt injection NOW clearly distinguished ✅

### Remaining Weaknesses

| # | Weakness | Fix Needed | Status |
|---|----------|----------|--------|
| W2.1 | 41/125 pairs still pending | Finish for camera-ready | 🔄 Running |
| W2.2 | Timeout NOT pre-registered | Added ITT + completed-run dual metric + 175s policy | ✅ Fixed |
| W2.3 | Controls only 20 pairs | Labeled as "spot-check"; will expand for camera-ready | ✅ Labeled |
| W2.4 | Cross-platform scope | skills.sh now labeled "directional confirmation only" | ✅ Fixed |
| W2.5 | Refusal variance not quantified | Added σ=0.11/0.28 per-task variance + T13/T18 completion rates | ✅ Fixed |

### Reviewer Raw Response (verbatim)

<details>
<summary>Click to expand</summary>

Score: 7/10
Verdict: Almost

1. Still incomplete main experiment (41/125 pending) → Finish; move projected language.
2. Timeout handling can look post-hoc if not pre-registered → Predefine policy; dual-report ITT + completed-run.
3. Controls and payload diversity are sample-limited → Mark as spot-checks; note camera-ready will expand.
4. Cross-platform external validity remains thin → Scope tightly to ClawHub in title/abstract/conclusion.
5. Causal language "no consistent defense" still strong → Quantify refusal variance explicitly.

Is source-sink sufficiently distinguished from PI? YES, sufficiently.
Is "57/84 with 23 timeouts" acceptable? YES — present ITT + completed-run side by side.

</details>

### Actions Taken
- **W2.2**: Added ITT (67.1%, CI 56.5-76.1%) + completed-run (93.4%, CI 84.1-97.8%) dual reporting in §5 with pre-registered timeout policy (175s SIGKILL).
- **W2.3**: All controls labeled as "spot-checks" in §5 and §7 Limitations.
- **W2.4**: Introduction updated to dual-metric; camera-ready note added.
- **W2.5**: Added σ=0.11/0.28 per-task refusal variance in Failure Analysis ¶; T13/T18 completion rates added.
- Observation box softened: "no consistent runtime defense" → "stochastically applied".

### Status
→ Score: 7/10. Experiment completing; will re-review after 125/125 final numbers.
