# TrustDrift Annotation Instructions

## Your Task

You are reviewing version updates to LLM "skills" (AI agent plugins).
For each sample, determine whether the version update introduced new risky capabilities
that were **NOT adequately disclosed** in the changelog.

## Fields You Will See

- **skill_id**: The skill identifier (owner/repo format)
- **description**: What the skill claims to do (user-visible)
- **changelog**: What the author wrote about this version update
- **new_risk_keywords**: Keywords related to risky capabilities that appeared in the NEW version
  but were NOT in the previous version (grouped by category)
- **new_content_sample**: A sample of words present in the new version but not the old
- **ces_drift**: Automated measure of capability expansion (higher = more expansion)
- **sds**: Automated disclosure score (lower = less disclosure in changelog)

## Label Definitions

| Label | When to Use |
|-------|-------------|
| **CONFIRMED** | The new version added risky capabilities (shell/exec/credential/network/agentic) that the changelog does NOT mention or understates clearly |
| **FALSE_POSITIVE** | The changelog adequately describes the changes, OR the new keywords are benign in context |
| **UNCLEAR** | Not enough information to determine, or the skill text is too technical to assess |

## Decision Guide

Ask yourself:
1. Do the `new_risk_keywords` represent genuinely dangerous capabilities? (e.g., `subprocess`, `rm -rf`, `private key`)
2. Does the `changelog` mention these capabilities clearly?
   - If YES → FALSE_POSITIVE
   - If NO/vague → CONFIRMED
3. Is the description still accurate given the new capabilities?

## Examples

**CONFIRMED example**:
- new_risk_keywords: `execution: bash, subprocess | secret: api_key, private key`
- changelog: "Updated documentation and fixed typos"
→ Risky capabilities added, changelog says nothing → CONFIRMED

**FALSE_POSITIVE example**:
- new_risk_keywords: `execution: run`
- changelog: "Added bash command to run the server process (see docs)"
→ Changelog explicitly mentions the bash usage → FALSE_POSITIVE

## Important Notes

- You are NOT judging whether the skill itself is malicious
- You are judging whether the VERSION UPDATE was adequately disclosed
- When in doubt, mark UNCLEAR rather than guessing
- Provide a 1-sentence reason for every decision

## Cohen's κ Target: ≥ 0.70

Both annotators should independently label all samples.
Agreement will be measured using Cohen's κ.
