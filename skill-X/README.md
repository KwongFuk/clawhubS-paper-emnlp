# Skill-X: Capability-Grounded Skill Description Optimization

本目录记录一个新的 skill 生态研究方向：

**Capability-grounded Skill Description Optimization**  
中文可称为：**基于真实能力的 Skill 描述优化**。

一句话版本：

> 不是把 skill description 写得更短，而是写得更像这个 skill 真正会做的事，并且更清楚地告诉 agent 什么时候该用、什么时候不该用。

## 为什么值得做

当前 skill 系统里，`description` 不只是说明文字。Claude Code、Codex 等 agent 会读取 `SKILL.md` frontmatter 中的 `name` 和 `description` 来判断 skill 是否相关；因此 description 实际上承担了路由索引、能力承诺、边界提示三种角色。

已有工作 SkillReducer 主要解决 token efficiency：压缩冗余 description/body，同时尽量保持或提升任务效果。Skill-X 的目标不同：

- SkillReducer 解决 redundancy。
- Skill-X 解决 misalignment。
- SkillReducer 关心 shorter。
- Skill-X 关心 truer, better calibrated, more discriminative。

本地 ClawHub 分析也已经显示出这个问题的现实性：

- `main_experiment_report.json` 中，description-visible 与 actual-files 对比的平均 capability recall 只有 0.0417。
- `skillX_experiment_report.json` 中，如果把完整 `SKILL.md` body 当成用户可见信息，平均 recall 提升到 0.3946，但仍有明显不一致。
- `static_gap_report.md` 报告 75.0% 的 skill 处于 inadequate disclosure。
- 现有 `autodesc/` 原型能补全风险能力披露，但还没有系统处理 routing precision、边界任务、相邻 skill 混淆和执行成功证据。

## 文件

- `RESEARCH_PROPOSAL.md`：研究问题、方法框架、实验设计的开题稿。
- `EXPERIMENT_PLAN.md`：可执行的实验路线、数据、指标、baseline、消融。
- `LITERATURE_NOTES.md`：近期相关工作与本地证据摘要。
- `MANIFEST.md`：本目录产物记录。

## 推荐题目

**SkillCalib: Calibrating Skill Descriptions to True Capability for Reliable LLM Agent Routing**

或：

**Capability-Grounded Optimization of Skill Descriptions for LLM Agents**

