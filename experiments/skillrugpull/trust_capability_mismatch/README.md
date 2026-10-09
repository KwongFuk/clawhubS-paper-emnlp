# Trust Capability Mismatch / SkillRugPull

This directory is the canonical home for the skills.sh trust-drift and
capability-description mismatch pipeline.

For the newer ClawHub IC implementation that still shares files with CAPE, see
`CLAWHUB_IC_INDEX.md`.

Early archived trust-drift code and outputs are preserved under
`legacy_trust_drift/`.

**Research Question**: In the LLM skill marketplace, do skills exhibit *trust drift* — where capability, permission, or behavioral exposure expands across version updates without adequate disclosure in changelogs or metadata? And can we detect these "SkillRugPull" updates from marketplace-visible signals alone?

## Core Claims

- **C1 (Prevalence)**: A non-trivial fraction of skill updates involve significant capability/permission expansion.
- **C2 (Under-disclosure)**: A substantial portion of high-risk expansions are not adequately reflected in changelogs or descriptions.
- **C3 (Detection)**: Version-diff + disclosure-gap features can effectively identify SkillRugPull candidates before review.

## Research Questions

| RQ | Question |
|----|----------|
| RQ1 | How prevalent is risk expansion in skill version updates? |
| RQ2 | How often is high-risk expansion under-disclosed in changelogs/descriptions? |
| RQ3 | What types of updates most often form SkillRugPull patterns? |
| RQ4 | Can we build a pre-audit detector using marketplace-visible signals? |

## Data Sources

Primary: `data/skills_sh/skills_sh.sqlite3`
- `skills` — 116K skills: description, summary_text, skill_text, scan_status
- `permission_secret_hints` — per-skill permission flags (shell, token, api_key, etc.)
- `skill_versions` — version list per skill (minimal; no changelog in current snapshot)

## Two-Track Approach

1. **Static snapshot analysis** (immediately available): Detect risk-disclosure gap using current text content vs permission hints. Treats each skill as a frozen snapshot.
2. **Live version tracking** (future): Crawl per-version changelogs and descriptions to build real version transition datasets.

## Key Definitions

### Capability Expansion Score (CES)
```
CES = Σ wᵢ · fᵢ
```
where `fᵢ` are counts of capability-expanding patterns (shell, exec, external API, token/secret references, network access, file I/O, autonomous agent patterns) weighted by risk.

### Permission Expansion Score (PES)
```
PES = Σ αⱼ · pⱼ
```
where `pⱼ` are permission hint flags from `permission_secret_hints`.

### Semantic Disclosure Score (SDS)
```
SDS ∈ [0, 1]  (higher = more adequate disclosure)
```
Measures: risky-change coverage in description, absence of vague filler phrases, description-to-capability alignment.

### Disclosure Gap Score (DGS)
```
DGS = λ₁·CES + λ₂·PES − λ₃·SDS
```

### SkillRugPull Candidate
```
SkillRugPull = (RiskExpansion is HIGH) AND (DisclosureAdequacy is LOW)
```

## Script Execution Order

```bash
cd /path/to/clawhubS

# 1. Build transition dataset
python experiments/skillrugpull/trust_capability_mismatch/01_build_transitions.py

# 2. Extract features
python experiments/skillrugpull/trust_capability_mismatch/02_extract_features.py

# 3. Compute semantic drift
python experiments/skillrugpull/trust_capability_mismatch/03_semantic_drift.py

# 4. Score disclosure adequacy
python experiments/skillrugpull/trust_capability_mismatch/04_disclosure_adequacy.py

# 5. Ecosystem prevalence analysis
python experiments/skillrugpull/trust_capability_mismatch/05_prevalence_analysis.py

# 6. Risk vs disclosure plot
python experiments/skillrugpull/trust_capability_mismatch/06_risk_vs_disclosure.py

# 7. Category breakdown
python experiments/skillrugpull/trust_capability_mismatch/07_category_breakdown.py

# 8. Build unified feature matrix
python experiments/skillrugpull/trust_capability_mismatch/08_build_feature_matrix.py

# 9. Train detector
python experiments/skillrugpull/trust_capability_mismatch/09_train_detector.py

# 10. Generate case studies
python experiments/skillrugpull/trust_capability_mismatch/10_case_studies.py

# Annotation
python experiments/skillrugpull/trust_capability_mismatch/annotation/sampling.py
python experiments/skillrugpull/trust_capability_mismatch/annotation/generate_annotation_sheets.py
```

## Output Files

| File | Description |
|------|-------------|
| `data/transitions.csv` | Version transition pairs |
| `data/feature_matrix.csv` | Full feature matrix |
| `data/snapshots_features.csv` | Per-skill snapshot features |
| `results/prevalence_report.md` | RQ1 answer |
| `results/disclosure_report.md` | RQ2 answer |
| `results/category_breakdown.md` | RQ3 answer |
| `results/detector_performance.md` | RQ4 answer |
| `results/case_studies.md` | Top SkillRugPull candidates |
| `annotation/annotation_sample_500.csv` | Human annotation sheet |

## Risk Family Taxonomy

| Family | Keywords |
|--------|----------|
| **Execution** | shell, exec, subprocess, terminal, command, bash, sh, powershell |
| **Secret** | token, api_key, secret, credential, auth, oauth, bearer |
| **Network** | requests, fetch, http, websocket, remote, webhook, curl |
| **File system** | read file, write file, upload, download, temp file, local storage |
| **External action** | send email, post message, create PR, deploy, purchase, schedule |
| **Autonomous agent** | plan, chain, tool use, autonomous, memory, loop, delegate |
