# 第三阶段（Stage 3）报告：Trust Drift / Semantic Rug Pull

本报告回答以下研究问题：

> **RQ3：技能在不触发重新审查的情况下，能否通过语义漂移改变 agent 的安全行为？**

这里关注的不是 binary-level drift，而是：

- `SKILL.md`
- `README.md`
- `agents/openai.yaml`
- `skill.json / skill.yaml / clawhub.json / _meta.json`
- 权限说明、动作说明、trust framing、carrier 描述

这些 **agent-facing artifacts** 在版本间发生变化，从而悄悄改变 agent 看到的“技能语义”。

---

## 1. 数据来源

- 主数据库：
  - [merged.sqlite3](/Users/guangfu/codex/clawhubS/data/experiment1_split/merged.sqlite3)
- 自动分析输出：
  - [trust_drift_report.md](/Users/guangfu/codex/clawhubS/experiments/skillrugpull/trust_capability_mismatch/legacy_trust_drift/results/trust_drift_report.md)
  - [trust_drift_candidates.csv](/Users/guangfu/codex/clawhubS/experiments/skillrugpull/trust_capability_mismatch/legacy_trust_drift/results/trust_drift_candidates.csv)
  - [trust_drift_proxy_report.md](/Users/guangfu/codex/clawhubS/experiments/skillrugpull/trust_capability_mismatch/legacy_trust_drift/results/trust_drift_proxy_report.md)
  - [trust_drift_proxy_candidates.csv](/Users/guangfu/codex/clawhubS/experiments/skillrugpull/trust_capability_mismatch/legacy_trust_drift/results/trust_drift_proxy_candidates.csv)
- 分析脚本：
  - [analyze_trust_drift.py](/Users/guangfu/codex/clawhubS/experiments/skillrugpull/trust_capability_mismatch/legacy_trust_drift/scripts/analyze_trust_drift.py)
- 规则与算法：
  - [trust_drift.py](/Users/guangfu/codex/clawhubS/experiments/skillrugpull/trust_capability_mismatch/legacy_trust_drift/src/trust_drift.py)

---

## 2. 我们实际检测了什么

对每个多版本 skill，我们按相邻版本做 transition 分析，重点比较：

1. **agent-facing 文件是否变化**
2. **权限相关词是否扩张**
3. **动作相关词是否扩张**
4. **trust framing 是否增强**
5. **covert / stateful 语义是否增强**
6. **scan/security signal 是否变坏**
7. 在上述变化发生时，**代码改动是否相对少**
8. changelog 是否为空或过于泛化

因此，这个检测更接近：

> **semantic trust drift detector**

而不是普通代码 diff。

---

## 3. 整体结果

### 3.1 版本规模

- `skills_with_multiple_versions`: `7144`
- `version_transitions`: `25068`

这说明数据库已经足够支持版本级语义漂移研究，不是只有零散少量版本。

### 3.2 agent-facing 漂移覆盖面

- `transitions_with_agent_facing_change`: `20082`

也就是：

- 约 `80.1%` 的版本迁移，都涉及至少一个 `SKILL.md / README / agent config / manifest` 之类的 agent-facing 文件变化

这本身就是一个很强的结论：

> **版本升级时，技能对 agent 呈现出来的语义层内容，变化非常普遍。**

### 3.3 安全语义扩张

- `transitions_with_permission_expansion`: `1094`
- `transitions_with_action_expansion`: `1256`
- `transitions_with_trust_expansion`: `672`
- `transitions_with_covert_expansion`: `703`
- `transitions_with_scan_worsening`: `4956`

换算成 transition 占比：

- permission 扩张：约 `4.4%`
- action 扩张：约 `5.0%`
- trust framing 扩张：约 `2.7%`
- covert/stateful 扩张：约 `2.8%`
- scan/security 信号变坏：约 `19.8%`

这些数字说明：

- 权限和动作语义不是静态的
- trust framing 也会变化
- 而且相当一部分版本迁移伴随 security posture 的恶化

### 3.4 候选数量

- `semantic_drift_candidates`: `5507`
- `rug_pull_candidates`: `5218`

占比大约是：

- semantic drift：`22.0%` 的 version transitions
- rug-pull 候选：`20.8%` 的 version transitions

如果只在发生 agent-facing 变化的 transition 里看：

- semantic drift 候选约占 `27.4%`
- rug pull 候选约占 `26.0%`

这意味着：

> **在 agent-facing 文件发生变化的版本迁移里，大约每四次就有一次呈现出值得警惕的安全语义漂移信号。**

---

## 4. 这说明数据库里“有没有这个问题”？

可以给出一个明确结论：

> **有。当前数据库中已经存在大量 `Trust Drift / Semantic Rug Pull` 的证据。**

但需要精确表达它的含义：

- 这里的“证据”是：
  - 版本间 agent-facing 语义变化
  - 权限/动作/trust/covert 词汇扩张
  - 安全信号变坏
  - 且很多情况下代码改动不大或 changelog 很弱

- 这里还**不是**：
  - 对每个候选都证明真实用户已被攻击
  - 对每个候选都证明 agent 行为已经实际被带偏

也就是说，目前可以证明：

> **ClawHub 里确实存在大量“名字没变，但 agent-facing 语义在版本间明显漂移”的技能更新。**

---

## 5. 代表性候选

从当前 top 候选里，可以看到典型模式：

| Skill | Prev -> Curr | Agent-facing Changed | Code Changed | Permission + | Action + | Trust + | Covert + | Scan Worse | Generic/Empty Changelog |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |
| `TensusDS/turing-pyramid` | `1.8.1 -> 1.8.2` | 1 | 0 | 7 | 9 | 2 | 4 | no | no |
| `saesak/openclaw-skill-gastown` | `0.1.3 -> 0.1.4` | 2 | 0 | 5 | 7 | 2 | 4 | yes | yes |
| `OBrezhniev/verified-agent-identity` | `0.0.3 -> 0.0.4` | 1 | 0 | 6 | 6 | 3 | 3 | no | no |
| `davidbenjaminnovotny/moltslist` | `1.0.0 -> 1.0.1` | 1 | 0 | 5 | 8 | 3 | 1 | yes | yes |
| `thomasansems/n8n` | `1.0.2 -> 2.0.0` | 2 | 3 | 7 | 4 | 3 | 1 | no | yes |
| `anajuliabit/memoclaw` | `1.0.1 -> 1.7.0` | 2 | 0 | 2 | 5 | 3 | 3 | no | yes |
| `SinghCoder/openclaw-structured-memory` | `1.0.0 -> 2.0.0` | 1 | 0 | 5 | 4 | 0 | 2 | no | yes |

这些候选共同呈现出几个你论文里最需要的模式：

1. **agent-facing 文件确实变了**
2. **权限/动作/trust/covert 语义在扩张**
3. **代码变动未必同步大**
4. **changelog 常常不足以解释这些变化**

这正是 semantic rug pull 的核心形态。

---

## 6. 为什么这很像 TOCTOU in agent semantics

这个问题的核心不是：

- “代码有没有被换成恶意代码”

而是：

- 用户或平台最初批准的是某个“skill identity”
- 后续使用时，agent 实际读到的：
  - 描述
  - prompt
  - usage
  - capability statement
  - trust framing
  已经悄悄变化

这就是：

> **TOCTOU（检查时 vs 使用时）在 agent-facing semantic layer 的重演。**

检查时：

- 用户看到的是某个版本下的 skill 语义

使用时：

- agent 消费的却是更新后的 skill 语义

如果平台或用户的审批锚定在“名字 / 品牌 / skill identity”，而不是锚定在“版本化后的语义快照”，那么这种漂移就可以绕过一次性信任决策。

---

## 7. 当前结论的强度

### 现在已经能比较稳地说

1. `semantic trust drift` 在当前数据库里不是罕见现象
2. 多版本 skill 中，agent-facing 文件变化非常普遍
3. 相当比例的迁移呈现出权限、动作或 trust 语义扩张
4. `rug pull` 风格候选在当前数据里已经大量存在

### 现在还不能直接说

1. 每个候选都已经真正改变了 OpenClaw 的执行行为
2. 每个候选都能绕过真实平台审查
3. 每个候选都代表恶意意图

因此，这一阶段的结论应表述为：

> **当前数据库已经显示出大量版本级 semantic trust drift 候选，说明 ClawHub skill 生态存在现实的“语义 rug pull”风险面。**

---

## 7.5 更强但仍保守的代理证据

为了进一步回答更强的三个问题：

1. 哪些候选更可能已经改变 OpenClaw 的实际决策或执行行为？
2. 哪些候选更可能在披露不足的情况下滑过常规人工审查？
3. 哪些候选更接近“带有误导/恶意意图”的 semantic rug pull？

我们在基础漂移检测之上，又增加了一层 **proxy analysis**，见：

- [trust_drift_proxy_report.md](/Users/guangfu/codex/clawhubS/experiments/skillrugpull/trust_capability_mismatch/legacy_trust_drift/results/trust_drift_proxy_report.md)
- [trust_drift_proxy_candidates.csv](/Users/guangfu/codex/clawhubS/experiments/skillrugpull/trust_capability_mismatch/legacy_trust_drift/results/trust_drift_proxy_candidates.csv)

这里的三个代理定义是：

- `behavior_change_proxy`
  - 更强调 agent-facing 文件变化叠加权限/动作/covert 语义扩张
- `review_gap_proxy`
  - 更强调语义扩张与 changelog 披露不足之间的缺口
- `malicious_intent_proxy`
  - 更强调 trust framing、隐蔽性语义、scan 变坏与披露不足共同出现

这层分析仍然 **不是最终事实认定**，但它能把候选从“广义 drift”进一步压缩成“更值得做 OpenClaw old-vs-new 对照实验”的高价值样本。

---

## 8. 下一步最值得做什么

为了把这个问题从 measurement 推进到更强的安全论证，下一步建议：

1. **人工校验 top candidates**
   - 逐个看 `SKILL.md / README / agent config` 的实际 diff
2. **把当前版本与旧版本装到本地 OpenClaw**
   - 测试 agent selection / approval / execution 是否变化
3. **做 approval bypass case study**
   - 用户批准旧版本，但升级后 agent 行为是否更激进
4. **检查 changelog disclosure gap**
   - 看语义变化是否被 changelog 正常披露

---

## 9. 总结

当前数据库可以支持一个相对稳健的结论：

> **Trust Drift / Semantic Rug Pull 在 ClawHub 中已经不是理论上的假设，而是一个可以从版本历史中直接观察到的大规模现象。**

更具体地说：

- `7144` 个 skill 至少有多个版本
- `25068` 次版本迁移可分析
- `20082` 次迁移涉及 agent-facing 文件变化
- `5507` 次迁移表现出安全语义漂移候选
- `5218` 次迁移进一步符合 rug-pull 候选条件

因此，若后续在 OpenClaw 受控环境中进一步验证这些 top candidates 对 agent 行为的影响，`RQ3` 是完全有机会成立的。
