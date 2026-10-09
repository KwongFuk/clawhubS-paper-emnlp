# Experiment Plan

## Goal

验证一个核心 claim：

> Capability-grounded and contrastive description optimization makes skill routing more accurate and downstream execution more reliable than original, compressed, or generic summarized descriptions.

## Data

### D1. ClawHub corpus

Primary local sources:

- `data/experiment1_split/merged.sqlite3`
- `experiments/skillrugpull/clawhub/results/main_experiment_results.jsonl`
- `experiments/skillrugpull/clawhub/results/skillX_experiment_results.jsonl`
- `experiments/skillrugpull/clawhub/results/autodesc/*.jsonl`

Use this dataset for static capability profiles, mismatch prevalence, description generation, and routing-only evaluation.

Important cleanup before paper:

- Freeze one canonical snapshot count. Current local reports mention both 17,738 and 18,443 because of different pipeline filters/snapshots.
- Record exact script commit/hash or archive timestamp.
- Deduplicate skill versions if the evaluation is per-current-skill rather than per-skill-row.

### D2. Runnable skill subset

Select 30-100 skills with:

- available `SKILL.md`;
- scripts or CLI commands;
- setup that does not require private credentials;
- deterministic or checkable output.

Use this subset for downstream success.

### D3. SkillBench / SkillsBench-style tasks

Use external or local benchmark patterns where skills are injected and agents must discover them autonomously. The local `SkillCompBench` skeleton is security/composition oriented, but its case schema can inspire fixed-composition and search-mode separation.

## Capability Profile Schema

For each skill:

```json
{
  "skill_id": "",
  "original_description": "",
  "capabilities": [
    {
      "label": "NET_READ",
      "task_phrase": "fetch data from external APIs",
      "objects": ["URLs", "REST APIs"],
      "evidence": [
        {"file": "SKILL.md", "snippet": "..."},
        {"file": "scripts/fetch.py", "snippet": "requests.get(...)"}
      ],
      "confidence": 0.0,
      "observed_success_rate": null,
      "risk": "low"
    }
  ],
  "triggers": {
    "explicit": [],
    "implicit": []
  },
  "preconditions": [],
  "boundaries": [],
  "neighbors": []
}
```

Static-only pilot can populate `observed_success_rate = null`; execution-enhanced runs should fill it from traces.

## Description Variants

Evaluate these description variants for the same skill:

1. `original`: marketplace/frontmatter description.
2. `skillreducer_style`: compressed or generated routing description optimized for brevity.
3. `summary`: generic LLM summary of `SKILL.md`.
4. `autodesc_rule`: existing template-based capability disclosure baseline.
5. `profile_only`: generated from capability profile without neighbors.
6. `profile_contrastive`: generated from profile plus hard-neighbor distinctions.
7. `profile_trace_contrastive`: full method with execution traces when available.

## Query Set

For each skill, generate or annotate:

- **Positive explicit**: user names the domain/tool directly.
- **Positive implicit**: user describes the need without naming the skill.
- **Boundary near-miss**: related domain but should route to another skill.
- **Overclaim probe**: asks for a capability original description implies but evidence does not support.
- **No-skill query**: adjacent to the ecosystem but no available skill should be used.

Example:

```json
{
  "skill_id": "x-bookmarks",
  "query_type": "boundary_near_miss",
  "query": "Draft and post a thread based on my latest bookmarked tweets.",
  "gold": "do_not_route_to_x_bookmarks_unless_posting_skill_available",
  "neighbor": "x-poster"
}
```

## Routing Evaluation

Routers:

- `R_embed`: embedding similarity over descriptions.
- `R_llm`: LLM chooses relevant skills from top-k candidate descriptions.
- `R_agent`: real harness simulation if feasible, copying skills into agent-specific skill directory and observing selected skill.

Metrics:

- Recall@1 / Recall@3 / Recall@5 on positive queries.
- Precision@1 / Precision@3 on mixed query pool.
- Hard-negative confusion rate.
- Abstention accuracy on no-skill queries.
- Boundary false trigger rate.
- Mean description length and token cost.

## Calibration Evaluation

Extract claims from generated descriptions into capability labels and task phrases.

Metrics:

```text
Capability Coverage = |claimed ∩ supported| / |supported|
Overclaim Rate      = |claimed - supported| / |claimed|
Boundary Coverage   = boundaries_mentioned / boundaries_required
DCE                 = mean_b |claim_confidence_b - observed_success_b|
```

When no execution traces exist:

- use static confidence as a weak substitute;
- report it separately as Static-DCE;
- avoid claiming true success calibration until traces are collected.

## Downstream Evaluation

For runnable subset:

1. Install or stage skills in isolated workspace.
2. Run the same user task under different description variants.
3. Hold `SKILL.md` body constant; only change frontmatter description/routing card.
4. Use deterministic checkers where possible.

Metrics:

- task success rate;
- correct skill selected;
- unnecessary skill load;
- setup/tool errors;
- number of turns;
- token cost;
- failure category.

## Ablations

- Remove boundaries from generated description.
- Remove contrastive neighbor block.
- Remove execution evidence.
- Use only original body, no scripts/references.
- Vary description length budget: 30, 60, 120, 200 tokens.
- Test library size: 20, 100, 500, 2,000 candidate skills.

## Baseline Expectations

Expected patterns:

- Original descriptions have high under-disclosure and boundary ambiguity.
- Pure AutoDesc improves capability coverage but may hurt routing precision because it emphasizes generic risk capabilities like shell, credentials, network.
- Generic summary improves human readability but may not reduce hard-neighbor confusion.
- SkillReducer-style compression reduces tokens but can preserve or amplify pre-existing misalignment if the source description is wrong.
- Full SkillCalib should improve positive recall while reducing boundary false triggers.

## Pilot Order

### Pilot 1: Static routing benchmark

- Sample 500 IC-1/IC-2 skills with at least two hidden capability families.
- Build static profiles from existing JSONL outputs.
- Generate `profile_only` and `profile_contrastive` descriptions.
- Evaluate with `R_embed` and `R_llm`.

Success signal:

- +10% relative Recall@3 over original.
- -20% hard-negative confusion rate.
- Overclaim rate below generic LLM summary.

### Pilot 2: AutoDesc vs SkillCalib

- Compare existing `autodesc_rule` to `profile_contrastive`.
- Focus on whether security-style disclosure helps or hurts routing.

Success signal:

- SkillCalib keeps coverage close to AutoDesc but improves precision and boundary handling.

### Pilot 3: Execution-enhanced subset

- Pick 30 runnable skills.
- Generate 3 tasks per skill plus 2 boundary tasks.
- Run agent harness with original vs SkillCalib descriptions.

Success signal:

- higher correct skill selection;
- fewer wrong-skill executions;
- measurable downstream success lift.

## Implementation Notes

Likely new scripts:

- `skill-X/scripts/build_profiles.py`
- `skill-X/scripts/generate_descriptions.py`
- `skill-X/scripts/build_query_sets.py`
- `skill-X/scripts/evaluate_routing.py`
- `skill-X/scripts/analyze_calibration.py`

Reuse existing code where possible:

- capability extraction from `experiments/skillrugpull/clawhub/27_main_experiment.py`;
- `SKILL.md` vs code-only ablation from `29_skillX_experiment.py`;
- AutoDesc baselines from `experiments/skillrugpull/clawhub/autodesc/`;
- CAPE profile utilities under `src/clawhub_analyzer/cape/`.

## Risks

- Static extraction noise may make generated descriptions overfit false capabilities. Mitigation: evidence confidence, LLM adjudication, small human sample.
- Generated boundaries may become too verbose for frontmatter. Mitigation: separate short marketplace description from internal routing card.
- Trace data may be sparse. Mitigation: start static-only, then add probe tasks and synthetic traces.
- Routing gains may not translate to downstream success. Mitigation: include end-to-end runnable subset early.

