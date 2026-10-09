# SkillCalib: 面向可靠路由的 Skill 真实能力校准描述优化

## 研究问题

LLM agent 的 skill description 不是普通文档摘要，而是 agent 路由系统的第一层索引。一个 skill 是否会被加载，往往取决于 `SKILL.md` frontmatter 中短短一行 `description` 是否让 agent 判断它和用户任务相关。

现有 skill description 常见三类失真：

1. **低估能力**：skill 明明支持某类任务，但 description 写得过窄，导致该触发时不触发。
2. **高估能力**：description 写得太泛或太强，导致不该触发时误触发，或触发后无法完成任务。
3. **边界不清**：description 只说“能做什么”，没有说明“不能做什么”或“和邻近 skill 有何不同”，导致 skill library 中相互抢 query。

因此，本项目研究：

> 给定一个 skill 的实现内容、参考材料、历史执行证据和相邻 skill 信息，如何自动生成或修正一个与真实能力校准的 description，使其提升 skill routing 的召回率、精确率和下游成功率，同时抑制能力过度宣称。

这个问题可以称为 **Capability-grounded Skill Description Optimization**。

## 与 SkillReducer 的区别

SkillReducer 的核心目标是降低 token 成本：压缩冗余 description/body，并用 progressive disclosure 降低上下文负担。它的主要问题意识是“skill 太长、太散、太占 token”。

SkillCalib 的核心目标是描述校准：让 description 的能力承诺与 skill 的真实能力一致，并在 skill library 中更可区分。它的问题意识是“description 不一定真实、不一定有边界、不一定利于正确路由”。

可以用一句话区分：

> SkillReducer optimizes description redundancy; SkillCalib optimizes description truthfulness and routing calibration.

这使 SkillCalib 不只是 SkillReducer 的延续，而是一个不同目标函数下的 skill governance / routing reliability 问题。

## 问题形式化

给定 skill library `S = {s_1, ..., s_n}`。每个 skill `s` 包含：

- `m_s`：原始 marketplace metadata，包括 name、description、tags。
- `b_s`：`SKILL.md` body。
- `r_s`：references、scripts、配置文件、依赖说明等静态证据。
- `t_s`：历史执行 traces，包括用户 query、是否触发、是否成功、失败原因。
- `N_s`：相邻或容易混淆的 skills。

目标是生成新的 description `d'_s`，满足：

- **Coverage**：覆盖 skill 真实能完成的核心任务。
- **Precision**：不宣称 skill 实际做不到或低成功率的能力。
- **Boundary clarity**：说明适用条件、依赖、限制和不适用任务。
- **Contrastiveness**：相对邻近 skills 更可区分，减少路由混淆。
- **Utility**：提升路由和下游执行成功，而不是只提升文字指标。

## 方法概览

SkillCalib 分为四个模块。

### 1. Capability Profile Construction

先从真实证据构建 skill 的能力画像，而不是直接重写原始 description。

能力证据包括：

- frontmatter description、tags、requires、permissions。
- `SKILL.md` body 中的 use-when、workflow、commands、error handling。
- scripts、package files、references 中出现的 API、文件格式、工具依赖。
- 已有静态能力抽取结果，例如本 repo 的 `C_actual` / `C_visible` / IC taxonomy。
- 可选执行证据：query -> selected skill -> success/failure -> error type。

能力画像建议使用结构化 schema：

```yaml
skill_id:
core_tasks:
  - task: "fetch and summarize X/Twitter bookmarks"
    evidence: ["SKILL.md:workflow", "scripts/fetch_bookmarks_api.py"]
    confidence: 0.88
    success_rate: 0.76
triggers:
  explicit: ["check my bookmarks", "bookmark digest"]
  implicit: ["turn saved tweets into action items"]
preconditions:
  - "bird CLI or X API token is configured"
boundaries:
  - "does not post tweets unless a separate posting workflow is present"
  - "does not bypass X authentication"
side_effects:
  - "reads local auth tokens"
  - "writes local bookmark state"
neighbor_confusions:
  - neighbor: "x-poster"
    difference: "this skill reads/analyzes bookmarks; neighbor posts content"
```

### 2. Capability-Aligned Description Generation

生成目标不是“短摘要”，而是一个可路由、可校准的能力说明。

推荐 description 模板：

```text
Use when [task/trigger conditions]. This skill can [core capability], using [required tools/APIs/data].
It is appropriate when [preconditions/implicit cases]. It does not [boundary/non-goals].
Prefer it over [neighbor skill type] when [contrastive distinction].
```

对于 marketplace 一行 description，可以压缩成 1-3 句；对于 agent-internal routing card，可以保留更结构化版本。这样可以同时支持人类可读和 agent 可路由。

### 3. Capability Consistency Verification

生成后需要验证，而不是只看文本好不好。

验证分四类：

- **Coverage check**：真实能力是否被 description 足够覆盖。
- **Overclaim check**：description 是否宣称了缺乏证据或低成功率的能力。
- **Boundary check**：高风险、低成功率、依赖未满足、相邻 skill 任务是否被明确排除。
- **Evidence entailment check**：每个能力句是否能追溯到 profile 中的 evidence。

核心指标可以定义为：

```text
Description Calibration Error (DCE)
  = average over claimed capability bins | claimed_confidence - observed_success_rate |
```

如果没有历史 execution traces，可以先用静态证据置信度和 probe execution 成功率近似；有 traces 后再切换到真实成功率。

### 4. Contrastive Description Optimization

description 不是独立存在的，而是在 skill library 中竞争路由。因此要加入相邻 skill 的对比信号。

对每个 skill 找 hard neighbors：

- embedding 相似但能力不同的 skill。
- 同领域但操作对象不同的 skill。
- 同工具但权限/副作用不同的 skill。
- 历史上经常互相误触发的 skill。

然后构造 contrastive routing loss：

```text
For positive query q+ of skill s and hard-negative neighbor h:
  score(router(q+, d'_s)) > score(router(q+, d'_h)) + margin

For boundary query q- that belongs to neighbor h:
  score(router(q-, d'_s)) < score(router(q-, d'_h)) - margin
```

这一步是 SkillCalib 的关键差异：不只是“写得真实”，还要“在库里写得可区分”。

## 核心贡献

### C1. 从 capability mismatch 到 description calibration

已有本地工作能检测 description 与实际能力不一致。SkillCalib 往前走一步：不仅指出 mismatch，还自动生成可路由、可验证、带边界的新 description。

### C2. 引入 Description Calibration Error

把 description 看成能力承诺，度量它和真实成功能力之间的差距。这个指标比单纯 trigger rate 更贴近问题本质。

### C3. 引入 contrastive skill description optimization

把 description 优化放回整个 skill library，而不是逐个 skill 独立重写。目标是减少邻近 skill 之间的抢占和误触发。

### C4. 利用成功/失败执行证据更新描述

静态内容只能说明“写了什么”和“代码可能做什么”。执行 traces 能说明“真的会不会”。持续校准可以把 SkillCalib 从一次性重写扩展为 self-calibrating skills。

## 实验设计总览

### RQ1: 真实 skill 生态中 description misalignment 有多普遍？

数据：

- 本地 ClawHub corpus：`main_experiment_report.json` 报告 18,443 skill rows、316,675 files read。
- 注意：部分旧报告使用 17,738 作为 canonical DB count，正式论文前需要 freeze 一个统一 snapshot。

指标：

- capability recall / precision。
- IC-1/IC-2/IC-3/IC-4 分布。
- risky vs non-risky capability gap。
- description length 与 mismatch 的关系。

### RQ2: SkillCalib 是否提升 routing recall 与 precision？

构造 benchmark：

- Positive queries：该 skill 应该触发。
- Boundary queries：领域相近但不该触发。
- Overclaim probes：原 description 容易误导但 skill 做不好。
- Implicit queries：用户没有显式说出 skill 域，但 skill 实际适合。

比较：

- Original description。
- SkillReducer-style compressed/generated description。
- Generic summarization baseline。
- Existing AutoDesc / rule-based disclosure baseline。
- SkillCalib without contrastive optimization。
- Full SkillCalib。

指标：

- Routing Recall@k。
- Routing Precision@k。
- Hard-negative confusion rate。
- No-skill abstention accuracy。

### RQ3: SkillCalib 是否提升触发后的任务成功率？

在可执行 skill 子集上运行 end-to-end 任务：

- 选择 30-100 个有明确 scripts / CLI / references 的 skill。
- 每个 skill 构造 3-5 个任务。
- 使用同一个 agent harness，只替换 description。

指标：

- task success rate。
- wrong skill invocation rate。
- tool/setup error rate。
- token cost。
- time-to-success。

### RQ4: execution evidence 是否比静态证据更能校准 description？

比较三种 profile：

- Static-only：body + references + code。
- Trace-only：历史 query 与成功/失败。
- Static + trace：合并证据。

重点看：

- 能否减少 overclaim。
- 能否发现 description 没写但经常成功的隐式能力。
- 能否把低成功率能力降级成 boundary 或 caveat。

### RQ5: contrastive optimization 是否减少相邻 skill 混淆？

构造 hard-neighbor set：

- 同 category。
- description embedding top-k。
- capability overlap high but side-effect/precondition differs。

指标：

- pairwise confusion rate。
- top-1/top-3 neighbor displacement。
- false activation on boundary tasks。

## 最小可行版本

第一阶段不需要完整 execution traces，可以用现有 ClawHub 静态数据做强 pilot：

1. 从 `main_experiment_results.jsonl` 选 500 个 IC-1/IC-2 skill。
2. 复用 `C_actual`、`C_visible`、UD/FC/AD 作为初始 capability profile。
3. 用三种方法生成 description：
   - rule-based AutoDesc；
   - LLM profile-to-description；
   - LLM + contrastive neighbor prompt。
4. 自动生成 positive / boundary / overclaim / implicit queries。
5. 用 embedding router 和 LLM router 评测 routing recall/precision。
6. 对 30 个可运行 skill 做小规模 end-to-end 验证。

这个 pilot 可以直接证明：问题不仅是“description 没披露风险能力”，而是“description 改写会不会真的改善 skill routing 和任务成功”。

## 预期论文叙事

论文主线可以这样写：

1. Skill description 是 agent routing 的关键接口。
2. 真实 skill 生态中 description 与实际能力大规模失配。
3. 现有压缩方法优化 token，但没有显式优化能力真实性、边界和区分性。
4. SkillCalib 从静态证据和执行证据构建 capability profile。
5. SkillCalib 生成可验证、对比式、能力校准的 description。
6. 它同时提升 routing recall、precision、downstream success，并降低 overclaim。

## 论文标题候选

- **SkillCalib: Calibrating Skill Descriptions to True Capability for Reliable LLM Agent Routing**
- **Capability-Grounded Optimization of Skill Descriptions for LLM Agents**
- **From Shorter to Truer: Calibrating Agent Skill Descriptions with Capability Evidence**
- **Do Agent Skill Descriptions Tell the Truth? Capability-Grounded Description Calibration for Skill Routing**

