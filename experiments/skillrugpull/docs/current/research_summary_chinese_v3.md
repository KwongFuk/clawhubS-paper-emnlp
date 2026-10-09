# TrustDrift / CapMismatch 研究进展汇报（中文 v5.0）

> **更新时间**：2026-04-13（晚）　　**投稿目标**：ACM CCS 2026
> **PDF 状态**：已编译 ✅（集群 TeX Live 2026，1.1MB）

---

## 一、研究背景与核心问题

### 背景

随着 LLM agent 生态兴起，「技能市场」（Skill Marketplace）成为主流扩展机制：开发者发布插件，用户一条命令即可扩展 AI 能力。与传统软件包不同，这些技能以**环境授权（ambient authority）**方式运行——持续激活、可读写文件、发邮件、访问密钥——且缺乏 Android 那样的机器可读权限声明。

### 核心问题

> **用户唯一的同意依据是开发者写的自然语言描述。这些描述诚实吗？**

当技能被悄悄更新、加入凭证窃取代码，但 changelog 只写「性能优化」，平台内置扫描器（依赖元数据合规性和 CVE 签名）对此类能力-描述不一致（CDM）**完全无效**。

### 研究定位（v5 稳定版）

系统明确定位为**发布时风险优先级排序（Publish-time Triage）工具**，不是二元恶意检测器：

- CapMismatch 输出**审查优先级排名**，而非阻断决策
- 安全相关性（SR）≠ 恶意（Malicious）
- 核心系统 = SCS + DMR，**确定性、无 LLM、O(N)**
- TDM / SA / AutoDesc 均为**可选扩展或未来方向**

---

## 二、三个研究问题（v5 新增）

本轮最重要的结构改动：论文正式引入三个 RQ，替代之前模糊的"贡献即目标"写法。

| RQ | 问题 | 对应核心结果 |
|----|------|-------------|
| **RQ1** 普遍性与披露质量 | 不同平台上能力-描述不一致有多普遍？开发者是否提供了与实际行为内容一致且有意义的描述？ | IC-1: 75.8%，mean CR=0.042，两平台 >90% |
| **RQ2** 检测与能力分类 | 能否系统识别描述与实际能力之间的差距？哪些能力类别被最频繁、最严重地隐藏？ | SCS+DMR，94.1% recall，CRED/NET 隐藏率最高 |
| **RQ3** 自动披露修复 | 基于静态分析自动生成的能力描述，能否有效弥合披露差距，为开发者和用户提供准确信息？ | AutoDesc，500 skills，CR 0.000→0.943，74.6% 达 IC-0 |

**RQ 设计逻辑**：测量规模（RQ1）→ 系统检测（RQ2）→ 构建性修复（RQ3），形成闭环。

---

## 三、研究框架

### 3.1 IC 差异矩阵（Inconsistency Checking Matrix）

| IC 类型 | 含义 | ClawHub 数量 | 占比 |
|---------|------|-------------|------|
| **IC-1** 完全隐藏 | CR = 0，描述完全不提实际能力 | **13,988** | **75.8%** |
| **IC-2** 选择性隐藏 | 0 < CR < 1，危险能力部分被遗漏 | **2,513** | **13.6%** |
| **TN** 真负例 | C_vis = C_act = Ø | 1,247 | 6.8% |
| **IC-4** 双向错位 | 隐藏 + 过度声明并存 | 538 | 2.9% |
| **IC-0** 准确披露 | CR = CP = 1，完全一致 | 79 | 0.4% |
| **IC-3** 虚假声明 | FC > 0，UD = 0 | 78 | 0.4% |

> → **89.4% 的技能存在某种程度的不一致。**

### 3.1.1 IC 分类详解与真实案例

---

#### 🟢 IC-0：准确披露（Accurate Disclosure，CR = CP = 1）

**定义**：描述声明的能力与代码实际能力完全对齐，既无隐藏也无多说。这是理想状态。

**风险**：无。用户看到的就是技能真正会做的事。

**真实案例（ClawHub: `1kalin/afrexai-crm-updater`）**：
- 描述：*"Manages a local CSV-based CRM with pipeline tracking"*
- C_actual = `[MULTI_STEP]`，C_visible = `[MULTI_STEP]`
- AD（一致）= `[MULTI_STEP]`，UD = `[]`，FC = `[]`
- **CR = 1.0，CDI = 0.0** ✅ 完全准确

---

#### 🔴 IC-1：完全隐藏（Full Omission，CR = 0）

**定义**：代码里有危险操作，但描述对此**只字不提**（UD > 0，FC = 0，CR = 0）。用户在安装时完全无法知道技能实际在做什么。

**风险**：**最高**。典型供应链注入模式——技能伪装成无害工具，实际暗中访问凭证、发送数据、执行 Shell 命令。占比 **75.8%**，是最主要的安全威胁。

**真实案例（ClawHub: `mainfraame/etrade-pelosi-bot`）**：
- 描述：*"Mirror congressional stock trades with automated broker execution and risk management"*
  （字面意思：自动复制国会议员股票交易）
- C_actual（代码实际有）= `[AUTONOMOUS, BROWSER_CONTROL, CRED_READ, CRED_STORE, CRED_TRANSMIT, CRYPTO_OPS, FILE_DELETE, FILE_READ, FILE_WRITE, MEMORY_WRITE, MULTI_STEP, NET_READ, NET_WRITE, SHELL_EXEC]`
- C_visible（描述声明有）= `[]`（**一个能力都没提**）
- 隐藏了 **14 个能力家族**，包括凭证读取、凭证外传、浏览器控制、文件删除
- **CR = 0.0，CDI = 1.0**

> 用户以为安装的是股票交易助手，实际上技能持有完整的系统控制权和凭证访问权，且描述对此只字未提。

---

#### 🟠 IC-2：选择性隐藏（Partial Omission，0 < CR < 1）

**定义**：描述提到了**部分**能力，但隐藏了其中的危险操作（UD > 0，FC = 0，0 < CR < 1）。开发者有意选择性披露无害功能，省略敏感行为。

**风险**：**中-高**。用户因为看到"有一些描述"产生虚假安全感，反而更难察觉隐藏的高危能力。

**真实案例（ClawHub: `cwyhkyochen-a11y/content-ops`）**：
- 描述：*"Social media content operations automation system with SQLite database. Manage content crawling, curation, publishing, and analytics across platforms..."*
- C_actual = `[AUTONOMOUS, BROWSER_CONTROL, CRED_READ, CRED_STORE, CRED_TRANSMIT, CRYPTO_OPS, FILE_DELETE, FILE_READ, FILE_WRITE, MEMORY_WRITE, MULTI_STEP, NET_READ, NET_WRITE, SHELL_EXEC]`
- C_visible = `[AUTONOMOUS]`（描述只提了"自动化"一项）
- AD（一致）= `[AUTONOMOUS]`，UD（隐藏）= **13 个高危家族**，包括凭证外传、文件删除、浏览器控制
- **CR = 0.071，CDI = 0.929**

> 技能号称是"内容自动化"，披露了"自动化"能力，但隐藏了同时在读取凭证、操控浏览器、删除文件、向外网发送数据。

---

#### 🟡 IC-3：虚假声明（False Claim，FC > 0，UD = 0）

**定义**：描述声明了代码里**根本没有**的能力（FC > 0，UD = 0）。代码实际上什么功能都没实现，但描述给人感觉功能很强大。

**风险**：**中**（欺骗性质不同）——主要是功能欺骗，用户为不存在的功能授权或付费；也可能是未完成技能被提前发布。

**真实案例（ClawHub: `luis2404123/web-scraping-proxy`）**：
- 描述：*"Web scraping with proxy rotation to avoid blocks. Complete scraping methodology with residential proxies, browser automation, anti-detection headers, rate limiting..."*
  （声称有代理轮换、浏览器自动化、反检测头等功能）
- C_actual（代码实际有）= `[]`（**代码里什么能力都没有**）
- C_visible（描述声明有）= `[AUTONOMOUS, BROWSER_CONTROL, NET_READ]`
- FC（虚报）= `[AUTONOMOUS, BROWSER_CONTROL, NET_READ]`，UD = `[]`
- **CR = None（无法计算），CDI = 1.0**

> 描述声称的所有"高级爬虫功能"在代码中一行都不存在。可能是空壳技能，用华丽描述欺骗用户安装。

---

#### 🔴 IC-4：双向错位（Bidirectional Mismatch，UD > 0 且 FC > 0）

**定义**：同时存在隐藏（UD > 0）和虚假声明（FC > 0）。描述说了一些代码没做的事，代码又做了一些描述没说的事。最难被发现的模式。

**风险**：**复合高危**。虚假声明部分可能是"烟雾弹"——用无害的功能声明转移注意力，掩盖真实隐藏的恶意行为。

**真实案例（ClawHub: `0x7466/kimai-time-tracking`）**：
- 描述：*"Complete Kimai time-tracking API integration. Manage timesheets, customers, projects, activities, teams, invoices and exports via REST API..."*
  （声称是 REST API 时间追踪集成，暗示只读网络数据）
- C_actual（代码实际有）= `[CRED_READ, CRED_STORE, FILE_READ, FILE_WRITE, MULTI_STEP, SHELL_EXEC]`
- C_visible（描述声明有）= `[NET_READ]`
- UD（隐藏）= `[CRED_READ, CRED_STORE, FILE_READ, FILE_WRITE, MULTI_STEP, SHELL_EXEC]`（**6 个危险能力未披露**）
- FC（虚报）= `[NET_READ]`（描述暗示只读网络，实际代码无此操作）
- **CR = 0.0，CDI = 1.0**

> 描述让用户以为是简单的 REST API 集成（只读），实际上代码在读写凭证、操作文件、执行 Shell 命令。"NET_READ"是诱导性虚假描述，掩盖了比读取 API 危险得多的实际行为。

---

### 3.2 核心度量

```
CR(s) = |C_desc ∩ C_code| / |C_code|   # 能力召回率
CP(s) = |C_desc ∩ C_code| / |C_desc|   # 能力精确率
CDI(s) = 1 − CR(s)                      # 不一致指数
```

| 指标 | 均值 |
|------|------|
| mean CR | **0.042**（描述平均只覆盖实际能力 4.2%）|
| mean CP | **0.830** |
| mean CDI | **0.894** |

### 3.3 LSCPS 提取范围（v5 澄清稳定版）

- **全仓库（full system）**：SKILL.md + Python/JS/Shell + 依赖 manifest + 配置文件，**300+ 正则模式**
- **SCS alone**：仅 SKILL.md 内代码块，**89 核心模式**（B3 基线用此）
- **主实验**：全仓库 300+ 模式

### 3.4 检测流水线（v5 系统边界）

```
[SCS] 全仓库 300+ 正则 → C_act + CES 分        ← 核心系统 (Steps 1-3)
[DMR] 对比 C_code/描述 → CR / CDI，生成排名      ← 核心系统 (Steps 1-3)
─────────────────────────────────────────────────
[TDM] 跨版本检测（可选，exploratory）            ← 可选扩展
[SA]  五模型 LLM 集成（可选，future work）        ← 可选扩展
─────────────────────────────────────────────────
[AutoDesc] LLM 自动生成准确描述（未来方向）        ← Discussion 节
```

> **v5 关键改动**：SA 不再是主流程 Step 4，不再称"objective final arbiter"。核心系统 = Steps 1-3，确定性，无 LLM。

---

## 四、实验结果

### 主实验（N = 18,443）

| 指标 | 值 |
|------|----|
| IC-1 完全隐藏（CR=0） | **75.8%**（13,988 skills）|
| IC-2 选择性隐藏（0<CR<1） | **13.6%**（2,513 skills）|
| IC-4 双向错位（UD>0 且 FC>0） | 2.9%（538 skills）|
| IC-3 虚假声明（FC>0，UD=0） | 0.4%（78 skills）|
| IC-0 准确披露（CR=CP=1） | 0.4%（79 skills）|
| TN 真负例（C_vis=C_act=Ø） | 6.8%（1,247 skills）|
| **不一致总计（IC-1+2+4）** | **92.3%** |
| mean CR | **0.042** |
| mean CDI | **0.894** |
| T6 34 skills 平台扫描器检出率 | **0%** |
| SCS∪DMR T6 recall | **94.1%**（95% CI: [80.9%, 98.4%]）|
| P@200 Lift | **16.3×** 随机基线 |

---

### R1：规则鲁棒性（8 种正则配置）

即使最严格设置（Strict），IC-1 仍达 **32.5%**，证明核心发现对参数变化稳健。

---

### 跨平台验证：skills.sh（116,208 skills，Script 28，2026-04-14 新跑）

> ⚠️ **v5 重要更新**：旧版 skills.sh 结果（Script 12，2026-04-02）用旧方法，本次用**与 ClawHub 完全相同的 LSCPS 14族 IC 矩阵**重新分析，结果与旧估算有重大差异。

| IC 类型 | skills.sh 数量 | skills.sh % | ClawHub % | 对比 |
|---------|--------------|-------------|-----------|------|
| IC-1 完全隐藏 | 686 | **0.6%** | 75.8% | skills.sh 极少 |
| IC-2 选择性隐藏 | 290 | **0.2%** | 13.6% | skills.sh 极少 |
| **IC-4 双向错位** | 24,577 | **21.1%** | 2.9% | **skills.sh 显著更多** |
| **IC-3 虚假声明** | 80,027 | **68.9%** | 0.4% | **skills.sh 主要问题** |
| IC-0 准确披露 | 1,985 | 1.7% | 0.4% | |
| TN 真负例 | 8,643 | 7.4% | 6.8% | |
| **不一致总计（IC-1+2+4）** | 25,553 | **22.0%** | 92.3% | |
| **mean CR** | — | **0.818** | **0.042** | 完全相反方向 |
| **mean CDI** | — | **0.121** | **0.894** | |

#### 🔍 关键发现：两个平台的不一致方向截然相反

**ClawHub 问题**：描述说的太少（IC-1主导，75.8%）——代码有大量能力但描述完全不提

**skills.sh 问题**：描述说的太多（IC-3主导，68.9%）——描述声明了代码根本没实现的能力（虚假声明）

这个对比说明：
1. 不一致问题在两个平台都广泛存在，但**方向相反**
2. ClawHub 是"实际能力被隐藏的风险"（供应链注入典型场景）
3. skills.sh 是"描述可信度问题"（功能欺骗、未完成技能发布）
4. 两种模式对用户同等有害：一个隐藏危险，一个误导信任

> **论文叙述建议**：不再笼统地说"两平台均 >90% 不一致"（旧数据），而是改为："两平台均存在系统性能力-描述不一致，但方向相反——ClawHub 以欠披露（IC-1/2）为主（89.4%），skills.sh 以虚假声明（IC-3/4）为主（90.0%）——揭示了技能市场生态的两种截然不同的披露失败模式。"

| 方法 | MRR | R@200 | P@200 Lift | T6 中位排名 |
|------|-----|-------|-----------|-----------|
| 平台扫描器 | 0 | 0 | 0× | 18,443 |
| B1 词汇重叠 | 0.0021 | 0 | 0× | 11,042 |
| B2 TF-IDF | 0.0019 | 0 | 0× | 12,529 |
| B3 单文件（仅 SKILL.md）| 0.0022 | 0.029 | 0× | 10,669 |
| **SCS+DMR（本文）** | **0.0167** | **0.176** | **16.3×** | **1,276** |

---

### R3：完整系统 Triage（N=18,443，T6=34，基础率 ≈0.18%）

top-200 覆盖 17.6% T6，提供与平台扫描器**正交**的信号（描述-代码不一致 vs. 签名匹配）。

---

### R4：多视角收敛验证

713 个隐藏能力实例被代码正则（View A）和依赖包推断（View B）独立标记——无人工标注，反驳规则库偏差批评。

---

### E4：安全相关性研究（500 skills，κ=0.81）

| 指标 | 值 |
|------|-----|
| SR 总体率 | **53.8%**（269/500 满足三条 SR 条件）|
| 高 CDI 层（CDI=1）SR 率 | **100%** |
| 低 CDI 层（CDI<0.5）SR 率 | 2% |
| SR-Precision@200 | **100%**（top-200 全部为 SR mismatch）|

> 注："Security-relevant" ≠ "malicious"。SR=100%@200 意味队列顶部每项都值得审查，不是说全部恶意。中间层（CDI∈[0.6,0.9]）方差较大，CDI-SR 是**趋势相关**，非严格单调。

---

### AutoDesc 实验（RQ3，500 IC-1 skills）

| 指标 | 修复前 | 修复后 |
|------|--------|--------|
| Mean CR | 0.000 | **0.943** |
| Mean CDI | 1.000 | 0.044 |
| CR≥0.95 达成率 | 0% | **74.6%** |

> AutoDesc 定位为**Discussion / Future Direction**，不参与当前主评估。

---

## 五、v5 本轮结构修复汇总

这是本 session 最重要的一轮改动，全部属于"不加新东西，只把结构改对"。

| 改动 | 旧版（v4）| 新版（v5）|
|------|---------|---------|
| **摘要** | 技术细节堆砌（CP/ResultFilesRead 等）| **4 句话**：问题→方法→两条大发现→边界声明 |
| **贡献条数** | 4 条（含 temporal 单独列）| **3 条**（按参考论文 Alexa/Google 风格）|
| **RQ** | 无 | **RQ1/RQ2/RQ3** 正式写入论文 |
| **贡献风格** | 学术叙述 | 参考语音 app privacy 论文风格：先方法，再发现，用"Surprisingly"标重点 |
| **SA 定位** | Step 4 主流程 / "objective final arbiter" | **Optional extension**，不参与当前评估 |
| **Figure 1 caption** | 暗示 SA 是必要组件 | **明确：TDM/SA 为 optional，当前评估仅 SCS+DMR** |
| **AutoDesc 标题** | §4.13 平级小节 | **Discussion: Toward ...** + 斜体免责行 |
| **Temporal 定位** | 检测结果 | 开头 **Exploratory note** 框，proxy labels |

---

## 六、当前论文状态（v5）

| 章节 | 行数 | 状态 |
|------|------|------|
| §1 Introduction | ~145 | ✅（含 3 RQ + 3 条贡献）|
| §2 Background | 172 | ✅ |
| §3 Taxonomy (IC框架) | ~300 | ✅（C_act 定义修正；SA Optional note）|
| §4 Static Analysis | 347 | ✅ |
| §5 Temporal Analysis | ~160 | ✅（Exploratory note）|
| §6 Ground Truth | 217 | ✅ |
| §7 Detection | ~940 | ✅（E4+AutoDesc Discussion；5 处硬伤修复）|
| §8 Related Work | 77 | ✅ |
| §9 Conclusion | 65 | ✅ |
| Appendix | 231 | ✅ |
| **总计** | **~2,450 行** | **✅ 零 TBD，零 Fatal 编译错误** |

**PDF**：`experiments/skillrugpull/paper/main.pdf`（1.1 MB，13:09 最后更新）

---

## 七、关键结论

| # | 结论 | 数据 |
|---|------|------|
| 1 | 平台扫描器对 34 个确认恶意技能检出率 **0%** | T6 实验 |
| 2 | **89.4%** 的技能存在能力-描述不一致 | 主实验 |
| 3 | mean CR = **0.042**，描述平均只覆盖实际能力 4.2% | 主实验 |
| 4 | top-200 triage 提供 **16.3×** 随机基线提升 | R3 |
| 5 | **53.8%** 的 IC mismatch 满足安全相关性（SR）判据 | E4，κ=0.81 |
| 6 | AutoDesc 规则基线：Mean CR 0.000→**0.943** | RQ3 实验 |

---

## 八、下一步（第二轮改进）

| 优先级 | 任务 |
|--------|------|
| 🔴 高 | **Ranking ablation table**（CES/CR/CDI/RWM/composite 分解）|
| 🔴 高 | Bandit/Semgrep 改为 qualitative comparison（删掉估算数字）|
| 🟡 中 | 把贡献按新风格写进论文（已有草稿，待执行）|
| 🟡 中 | E4 在正文标记为主要卖点（提前到 §3 摘要 / 引言）|
| 🟢 低 | 语义裁决全量跑（需集群 vLLM 环境）|
| 🟢 低 | 再做一轮 auto-review-loop |

---

## 九、局限性

| 局限 | 说明 |
|------|------|
| T6 样本量有限 | 34 skills，95% CI 较宽 |
| Triage 绝对精确率低 | P@200=1.6%（高 lift 但绝对数字低）|
| 规则库是保守下界 | CR=0.042 是上界，真实披露率可能更低 |
| Temporal 为探索性 | Proxy labels，不独立 |
| AutoDesc LLM 版未评估 | 当前只有规则模板基线 |
| 仅两个平台 | LangChain Hub、GPT Store 等待验证 |

---

*本文档由 Antigravity 于 2026-04-13 更新，取代旧版 v4.0。投稿目标：ACM CCS 2026。*
