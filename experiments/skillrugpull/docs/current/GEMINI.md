# TrustDrift / CapMismatch — GEMINI.md
# Project configuration for Antigravity + ARIS workflows

## Project

- **Name**: TrustDrift (CapMismatch)
- **Target venue**: USENIX Security '26
- **Paper directory**: `experiments/skillrugpull/paper/`
- **Main tex**: `experiments/skillrugpull/paper/main.tex`
- **Sections**: `experiments/skillrugpull/paper/sections/`
- **Figures**: `experiments/skillrugpull/paper/figures/`

## Experiment Results

- **Main results**: `experiments/skillrugpull/clawhub/results/main_experiment_results.jsonl`
- **Summary report**: `experiments/skillrugpull/clawhub/results/main_experiment_report.json`
- **Dataset**: `experiments/skillrugpull/clawhub/merged.sqlite3` (18,443 skills)
- **Core script**: `experiments/skillrugpull/clawhub/27_main_experiment.py`

## Key Numbers (from 27_main_experiment.py, full corpus)

- N = 18,443 ClawHub skills
- IC-1 (Full Omission): 75.8%
- IC-2 (Partial Omission): 13.6%
- IC-4 (Bidirectional): 2.9%
- IC-3 (False Claim): 0.4%
- IC-0 (Accurate): 0.4%
- Mean CR = 0.042
- Mean CP = 0.830
- Mean CDI = 0.894
- T6 recall = 94.1% (34 malicious skills, 40 version pairs)
- Platform scanner recall = 0%
- Cross-platform (skills.sh): 90.9% inconsistency

## Paper Status

- All TBD: **0** (fully filled)
- Stale numbers: **0**
- LLM ensemble: **future work** (not in current paper)
- Figures: regenerated from Script 27 real data

## Methodology Note (for reviewers)

LSCPS rule-based extraction is a **lower bound** on Capability Recall.
CR = 0.042 is an upper bound on true disclosure rates.
LLM semantic adjudication is deferred to future work.

## ARIS Workflow Entry Points

To run the auto paper improvement loop:
```
Read .agents/skills/auto-paper-improvement-loop/SKILL.md and run the improvement loop.
Paper: experiments/skillrugpull/paper/main.tex, sections: experiments/skillrugpull/paper/sections/, figures: experiments/skillrugpull/paper/figures/
Target venue: USENIX Security '26
Key claims to validate: IC-1=75.8%, T6 recall=94.1%, CR=0.042
```

To run the auto review loop:
```
Read .agents/skills/auto-review-loop/SKILL.md and review this security paper.
Paper: experiments/skillrugpull/paper/main.tex
Topic: capability-description inconsistency in LLM agent skill marketplaces
```

To analyze experiment results:
```
Read .agents/skills/analyze-results/SKILL.md 
Results file: experiments/skillrugpull/clawhub/results/main_experiment_report.json
```
