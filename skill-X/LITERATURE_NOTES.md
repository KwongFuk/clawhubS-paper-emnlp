# Literature and Local Evidence Notes

## External Work

### SkillReducer

Source: https://arxiv.org/abs/2603.29919

Key points:

- Studies 55,315 public skills.
- Reports that 26.4% lack routing descriptions and large portions of body/reference content are non-actionable.
- Proposes a two-stage optimization framework: routing-layer description compression/generation and body restructuring with progressive disclosure.
- Reports 48% description compression and 39% body compression while slightly improving functional quality.

Implication for SkillCalib:

- SkillReducer is the closest work, but its primary objective is token efficiency and removing non-essential content.
- SkillCalib should explicitly frame a different objective: calibration of ability claims, boundaries, and contrastive routing behavior.

### SkillsBench

Source: https://www.skillsbench.ai/skillsbench.pdf

Key points:

- Skills are injected into agent-specific directories.
- Claude Code and Codex read `SKILL.md` frontmatter `name` and `description` to determine relevance.
- SkillsBench evaluates agents discovering and using skills without explicit task instructions naming the skill.

Implication:

- Confirms that description is an operational routing interface, not just documentation.
- Supports evaluating description variants by holding body constant and changing frontmatter/routing card.

### SWE-Skills-Bench

Source: https://arxiv.org/abs/2603.15401

Key points:

- Evaluates 49 public SWE skills across about 565 task instances.
- Finds skill injection often has limited benefit: many skills produce zero pass-rate improvement and average gain is small.
- Highlights domain fit, abstraction level, and contextual compatibility.

Implication:

- SkillCalib can test whether poor description/routing calibration is one reason skills fail to help.
- Downstream success must be measured, not just routing trigger rate.

### SkVM / SkillRT-style skill capability profiling

Source: https://arxiv.org/abs/2604.03088

Key points:

- Treats skills as portable reusable units across heterogeneous LLMs and harnesses.
- Decomposes skill requirements into primitive capabilities and measures model-harness support.

Implication:

- Capability profiles are a natural representation for skill optimization.
- SkillCalib uses profiles for description calibration rather than compilation/runtime optimization.

### ToolRet

Source: https://arxiv.org/abs/2503.01763

Key points:

- Tool retrieval from large toolsets is underexplored.
- ToolRet contains 7.6k retrieval tasks and 43k tools.
- General IR models perform poorly on tool retrieval, and retrieval quality affects downstream pass rate.

Implication:

- Skill routing is an IR/retrieval problem under description mismatch.
- Routing evaluation should include retrieval metrics and downstream pass rate.

### ToolDreamer

Source: https://arxiv.org/abs/2510.19791

Key points:

- Existing tool retrievers often rank tools by similarity between user query and tool description.
- User requests are often poorly aligned with tool-description language.
- ToolDreamer uses hypothetical tool descriptions generated from the query to bridge this language gap.

Implication:

- Supports the idea that description language is a bottleneck.
- SkillCalib differs by optimizing the skill-side description to match true capability and boundaries, not only query-side expansion.

## Local Evidence

### Main IC experiment

File: `experiments/skillrugpull/clawhub/results/main_experiment_report.json`

Observed:

- `total_skills`: 18,443
- `mean_CR`: 0.0417
- `mean_CP`: 0.8296
- `mean_CDI`: 0.8943
- `IC-1`: 13,988 rows, 75.84%
- `total_files_read`: 316,675

Interpretation:

- One-line description is extremely low-recall for actual file-level capabilities.
- This is strong empirical motivation for description calibration.

### SkillX ablation

File: `experiments/skillrugpull/clawhub/results/skillX_experiment_report.json`

Observed:

- `mean_CR`: 0.3946 when `SKILL.md` body is treated as visible.
- `mean_CDI`: 0.2999
- IC-1 drops from 75.8% in script 27 to 10.79%.

Interpretation:

- The detailed skill body contains much more capability information than the frontmatter description.
- Description optimization can mine this body/code evidence to improve routing.
- But body-visible recall is still not perfect, so scripts/references/execution traces matter.

### Static disclosure gap

File: `experiments/skillrugpull/clawhub/results/static_gap_report.md`

Observed:

- 75.0% inadequate disclosure.
- 89.8% have at least partial disclosure gaps.
- Common undisclosed capabilities include shell execution, credential read, autonomous behavior, multi-step workflows, and network access.

Interpretation:

- Existing descriptions often understate important capabilities.
- A calibration method should include both normal capabilities and risky side effects.

### Structured SDG

File: `experiments/skillrugpull/clawhub/results/structured_sdg_report.md`

Observed:

- Three-layer extraction finds capabilities in YAML, code blocks, and prose.
- 3,631 skills have code-block capabilities not in prose.
- YAML declarations cover only part of actual capabilities.

Interpretation:

- Capability evidence should be multi-source and structured.
- A simple prose summarizer is insufficient.

### AutoDesc prototype

Files:

- `experiments/skillrugpull/clawhub/autodesc/28_autodesc_generator.py`
- `experiments/skillrugpull/clawhub/autodesc/28b_autodesc_rulebased.py`
- `experiments/skillrugpull/clawhub/results/autodesc/*.jsonl`

Observed:

- Rule-based AutoDesc on 500 IC-1 examples reports mean CR improvement from 0.0 to 0.943.
- LLM AutoDesc on 50 examples reports mean CR improvement from 0.0 to 0.411.

Interpretation:

- Capability-aware rewriting is feasible.
- However, current AutoDesc is primarily a risk/capability disclosure system. It does not yet optimize:
  - routing precision;
  - implicit triggers;
  - neighbor contrast;
  - downstream task success;
  - trace-based success calibration.

This gap is exactly where SkillCalib should sit.

