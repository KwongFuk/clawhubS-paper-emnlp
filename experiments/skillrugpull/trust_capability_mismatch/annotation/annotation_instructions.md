# SkillRugPull Annotation Instructions

## Overview
You are annotating LLM agent skills for the **SkillRugPull** study.
We are identifying skills where the user-visible description **understates** the actual
capabilities — creating a trust gap between what the user approved and what runs.

## Your Task (per skill)

### Column: `label_risk_expansion`
Read the **skill_text_excerpt** (what the skill actually does). Then rate:

- **0** = No significant expansion — description matches capabilities
- **1** = Minor expansion — some undisclosed capabilities but low risk
- **2** = Significant expansion — shell/exec/token/network/external-action capabilities in skill_text but NOT in description

**Tip**: Look at `undisclosed_keywords` column for pre-computed mismatches.

### Column: `label_disclosure`
Read the **description_excerpt** (what users see). Rate adequacy:

- **0** = Inadequate — description conceals or ignores major risk-relevant capabilities
- **1** = Partial — some risk mentioned but key ones omitted
- **2** = Adequate — description fairly represents skill's capabilities

### Column: `label_rugpull`
Final binary label:

- **0** = Not a SkillRugPull candidate
- **1** = SkillRugPull candidate (assign 1 if label_risk_expansion=2 AND label_disclosure=0)

### Column: `annotator_notes`
Optional free-text notes about why you labeled it as you did.

## What to Look For

| Risk Signal | Example keywords |
|-------------|-----------------|
| Shell execution | `shell`, `bash`, `subprocess`, `exec(`, `os.system` |
| Credential access | `api_key`, `token`, `secret`, `credential`, `oauth` |
| Network requests | `requests.get`, `fetch(`, `webhook`, `external api` |
| File system | `write file`, `upload`, `download`, `open(` |
| External actions | `send email`, `create PR`, `deploy`, `browser automation` |
| Autonomous agent | `autonomous`, `plan and execute`, `memory`, `loop until` |

## Inter-annotator Agreement
After annotation, Cohen's κ will be computed between annotators.
Disagreements on `label_rugpull` items will be arbitrated by a third reviewer.

## Questions?
If a skill's description or skill_text is empty, mark `label_risk_expansion=0`,
`label_disclosure=2`, `label_rugpull=0`, and note in `annotator_notes`.
