# 🔬 CapMismatch 声明-行为不一致检测框架 (v4.1)

> ✅ = 已建成 | 🆕 = 本次新增 | 🔮 = 规划中/Future Work

---

# 第一部分：主线 — 声明-行为不一致检测 (Core Pipeline)

核心命题：**LLM Agent Skill 的描述（C_vis）与实际行为（C_act）是否一致？**

```text
╔══════════════════════════════════════════════════════════════════════════════════════╗
║              主线: 声明-行为不一致检测核心链路 (Core Mismatch Pipeline)                 ║
╚══════════════════════════════════════════════════════════════════════════════════════╝


============================================================================================
[阶段 0: 数据采集与预处理 (Data Collection & Preprocessing)]
============================================================================================
目标: 从 LLM Agent 生态中收集全量 Skill 数据, 构建统一数据库并版本化。

【输入源】
   ✅ ClawHub 生态 ──── 17,738 skills, 含 SKILL.md, description, YAML, 安装量, 版本历史
   ✅ skills.sh 生态 ── 116,208 skills, 同构数据
   ✅ 历史版本快照 ──── 每个 Skill 的全量版本历史记录

【处理流程】
   ✅ 爬虫+解析  ──────────> 统一 SQLite3 (merged.sqlite3)
   │                         表结构: skills(skill_id, description, tags, installs, scan_status, ...)
   │                                 skill_files(skill_id, version, path, text_path, ...)
   │
   ✅ [01] build_version_transitions.py
   │  输入: merged.sqlite3 + SKILL.md 原文件
   │  方法: 对每个 Skill 的连续版本对齐, 构建 (v_prev, v_curr) 配对
   │  输出: version_transitions_enriched.jsonl
   │        每条记录: { pair_id, skill_id, prev_version, curr_version,
   │                    prev_skill_text, curr_skill_text, description, changelog, ... }
   │        规模: 24,200 个版本转换对
   │
   ✅ [02] extract_drift_features.py
      输入: version_transitions_enriched.jsonl
      方法: 对每个版本对计算:
            · CES (Capability Expansion Score) = Σ(新增能力权重) − Σ(移除能力权重)
            · SDS (Self-Disclosure Score) = changelog 中对新增风险的披露覆盖度 [0,1]
            · new_risk_kw_count = v_curr 中新增的高危关键词数
            · text_diff_ratio = 文本变化率
            · 按 6 大能力域分解: ces_drift_execution, ces_drift_secret, ...
      输出: drift_features.csv (6,713 pairs × 13 features)

【产出数据】
   ├── merged.sqlite3 ──────────────> 全量元数据 (阶段 1, 3, 4, 5 共用)
   ├── version_transitions_enriched.jsonl ──> 全量版本对文本 (阶段 1 提取用)
   └── drift_features.csv ─────────> 版本对漂移特征 (阶段 3-C 用)


============================================================================================
[阶段 1: 多维能力提取引擎 (Multi-Dimensional Capability Extractor)]
============================================================================================
目标: 从每个 Skill 的多个数据面精确解析出 C_vis (已声明能力) 与 C_act (实际行为能力)。

标准化能力词表: 12 个 LSCPS 能力家族 (Layered Security-Critical Permission Schema)
   ┌────────────────────────────────────────────────────────────┐
   │ 高危 (权重 ≥ 2.5):                                        │
   │   SHELL_EXEC    — 系统运行命令:subprocess, os.system, bash │
   │   CRED_READ     — 读取凭证: api_key, password, secret     │
   │   CRED_TRANSMIT — 传输凭证: send+key, post+token, exfil   │
   │   FILE_DELETE   — 文件删除: rm -rf, shutil.rmtree         │
   │                                                           │
   │ 中危 (权重 1.5~2.0):                                      │
   │   NET_WRITE     — 外联发送: requests.post, webhook, smtp  │
   │   CRED_STORE    — 存储凭证: os.environ[], keychain        │
   │   AUTONOMOUS    — 自主执行: autonomous, cron, background   │
   │   FILE_WRITE    — 文件写入: open(..,'w'), shutil.copy     │
   │                                                           │
   │ 低危 (权重 0.5~1.0):                                      │
   │   NET_READ      — 外联读取: requests.get, curl http       │
   │   FILE_READ     — 文件读取: open(..,'r'), glob            │
   │   MEMORY_WRITE  — 记忆写入: memory, remember              │
   │   MULTI_STEP    — 多步骤: workflow, pipeline, chain        │
   └────────────────────────────────────────────────────────────┘

=== C_vis 提取层 (用户能看到的声明) ===========================

   ✅ [YAML] YAML Frontmatter 结构化声明
      脚本: 13_yaml_alignment.py
      输入: SKILL.md 的 --- ... --- YAML 头
      方法: 1. pyyaml.safe_load() 解析 frontmatter
            2. 提取 openclaw.metadata.autonomous (bool)
            3. 提取 openclaw.metadata.permissions (list)
            4. 将权限列表映射到 LSCPS 家族
            5. 同时对 frontmatter 原文做 LSCPS 正则扫描
      输出: yaml_inferred_caps: Set[LSCPS]  (如 {AUTONOMOUS, NET_READ})
      数据: yaml_alignment.csv (逐 skill 的 YAML 声明 vs 实际能力)

   ✅ [Desc] 用户描述语义推理
      脚本: 17_llm_semantic_inference.py (Layer 2: infer_description_capabilities)
      输入: skills.description 字段 (自然语言文本)
      方法: 三级语义匹配:
            1. Explicit Match — 直接命中: "execute command", "api key", "webhook"
            2. Implicit Match — 隐含推导: "automate tasks" → SHELL_EXEC
                                          "connect to" → CRED_READ
            3. Negation Filter — 否定排除: "does not execute" → 移除 SHELL_EXEC
               每个 LSCPS 家族都有独立的 explicit/implicit/negation 短语表
      输出: declared_capabilities: Set[LSCPS]  (C_vis)
            explicit_matches, implicit_matches, negated: Set[LSCPS]

   🔮 [Schema] Tool Call JSON Schema (Future Work)
      输入: 给 LLM 看的函数签名 JSON
      方法: 解析参数名/描述, 检测隐藏高危端点
      输出: C_vis (显式声明) + 可能的 C_act (隐藏参数)

=== C_act 提取层 (实际行为能力) ===============================

   ✅ [Code-正向] LSCPS 静态正则 + 上下文感知过滤
      脚本: 17_llm_semantic_inference.py (Layer 1: extract_code_capabilities)
      输入: SKILL.md 全文 (含代码块、指令、文档)
      方法: 1. 对 12 个 LSCPS 家族各自的正则表达式组 (总计 89 条) 进行全文匹配
            2. 命中后, 检查命中位置前后 200 字符的上下文:
               · 是否在 ```example 代码块中 → 排除
               · 是否含 "placeholder", "this is" 注释 → 排除
               · 是否含 "your_api_key", "replace with" → 排除 (CRED_READ)
            3. 通过上下文过滤后, 确认该能力真实存在
      输出: code_capabilities: Set[LSCPS]  (C_act)
      精度: 通过 benign_indicator 排除, 降低误报率

   🆕 [Code-逆向] LLM 猎犬假设 → AST 法官验证
      脚本: 22_hybrid_extractor.py (Track 4)
      输入: SKILL.md 中的 Python 代码块
      方法: 1. LLM (3×7B Ensemble) 通读代码, 提出假设:
               "这段代码可能调用了 shutil.rmtree 实现 FILE_DELETE"
            2. Python ast 模块解析代码, 收集所有 ast.Call 节点
            3. 比对 LLM 假设的函数调用是否存在于 AST 节点中
               · 存在 → ✅ 确认, 加入 C_act
               · 不存在 → ❌ 驳回幻觉, 丢弃
      输出: ast_verified_capabilities: Set[LSCPS]  (补充正则遗漏项)
      优势: 可捕捉正则词库之外的未知 API 调用

   🆕 [Inst] System Prompts / 指令文本分析
      脚本: 22_hybrid_extractor.py (Track 5)
      输入: SKILL.md 中 "System Prompt" / "Instructions" 段落
      方法: 1. 正则定位指令段落 (## System Prompt, ## Instructions 等)
            2. LLM (3×7B Ensemble) 分析指令中被授权的行为:
               "你被指示自动执行文件清理" → AUTONOMOUS + FILE_DELETE
      输出: instruction_capabilities: Set[LSCPS]  (C_act 的意图层)

   🔮 [Deps] 依赖包引入分析 (Future Work)
      方法: AST 提取 import/require → 撞库恶意包名单
      输出: dependency_risk_flags

   🔮 [Env] 环境变量与凭证索取 (Future Work)
      方法: 正则扫描 os.environ.get / process.env
      输出: excessive_secret_requests

=== 能力融合 ================================================

   总 C_vis = YAML ∪ Desc ∪ Schema
   总 C_act = Code正向 ∪ Code逆向(AST验证) ∪ Inst ∪ Deps ∪ Env

【阶段 1 产出数据】
   ├── yaml_alignment.csv ────────> 逐 skill YAML 声明 vs 实际 (阶段 2 输入)
   ├── llm_semantic_inference.json ── 逐 skill C_vis, C_act, CR, CP, RWM (阶段 2/3 输入)
   └── 22_hybrid_extractor 输出 ──> AST 验证后的补充 C_act (阶段 2 输入)


============================================================================================
[阶段 2: 差异矩阵与不一致性分类 (Inconsistency Checking Framework)]
============================================================================================
目标: 对阶段 1 产出的 C_vis 与 C_act, 进行集合差异运算、分类归档与风险打分。

   ✅ [19] ic_framework.py

【输入】
   每个 Skill 的: C_vis = Set[LSCPS], C_act = Set[LSCPS]

【Step 1: 集合差异运算 (Difference Matrix)】

   对每个 LSCPS 能力 c_i ∈ {SHELL_EXEC, CRED_READ, ..., MULTI_STEP}:

    c_i 在 C_act?   c_i 在 C_vis?   判定
    ──────────────  ──────────────  ──────────────────────
        ✓               ✗           UD (Undisclosed) — 有能力但未声明 ⚠
        ✗               ✓           FC (False Claim) — 声明了但没实现
        ✓               ✓           AD (Accurate Disclosure) — 言行一致 ✓
        ✗               ✗           TN (True Negative) — 都没有

【Step 2: IC 分类 (Inconsistency Classification)】

   汇聚所有 c_i 的判定结果, 对整个 Skill 归类:

    ├── IC-1 (Complete Omission)  : C_act 非空, C_vis = ∅ (一个字都没说)
    ├── IC-2 (Partial Concealment): C_act ⊃ C_vis, 且 C_vis ≠ ∅ (说了一部分, 藏了一部分)
    ├── IC-3 (False Advertisement): C_vis ⊃ C_act (吹牛, 声明了没有的能力)
    ├── IC-4 (Bidirectional Error) : C_act ⊄ C_vis 且 C_vis ⊄ C_act (既藏又吹)
    └── IC-0 (Consistent)         : C_act = C_vis (完美一致)

【Step 3: 度量引擎 (Quantitative Metrics)】

   ✅ Capability Recall (CR):
      CR = |C_act ∩ C_vis| / |C_act|      范围 [0,1], 1.0 = 完全透明
      含义: 实际能力中有多大比例被用户知晓

   ✅ Capability Precision (CP):
      CP = |C_act ∩ C_vis| / |C_vis|      范围 [0,1], 1.0 = 没有虚假声明
      含义: 声明的能力中有多大比例是真实的

   ✅ Risk-Weighted Mismatch (RWM):
      RWM = Σ_{c ∈ (C_act \ C_vis)} w_c   +  2.0 × Σ_{c ∈ DeceptiveDenial} w_c
                                                     ↑
                                              Deceptive Denial: 明确否认("无网络")
                                              但 C_act 中存在 → 恶性加倍惩罚

      w_c = 数据驱动权重, 由 16_learn_risk_weights.py 从 T6 真实攻击学习:
         脚本: 16_learn_risk_weights.py
         方法: Logistic Regression (class_weight=balanced)
               训练集: 34 个 T6 恶意样本 + 340 个负样本 (1:10 平衡)
               验证: Leave-One-Out CV, 报告 AUROC
               消融: 单特征 AUROC + 能力共现签名 (Compound Signatures)
         输出: risk_weights.json → 被 17, 18, 19 消费

   ✅ Mismatch 严重度分级:
      CR ≥ 0.7 ──> "adequate" (合规)
      0.3 ≤ CR < 0.7 ──> "partial" (部分缺失)
      CR < 0.3 ──> "inadequate" (严重不充分) ⚠⚠⚠

【阶段 2 产出数据】
   ├── ic_classification.csv ────> 逐 skill: IC 类型, CR, CP, RWM, undisclosed list
   └── risk_weights.json ────────> RWM 学习权重 (供阶段 3 排序用)


============================================================================================
[阶段 3: 筛选防线与优先级排序 (Screening Pipelines & Prioritization)]
============================================================================================
目标: 基于阶段 2 的不一致性度量, 建立出版时 (Publish-Time) 安全拦截防线。

   防线 A ── ✅ SCS 静态能力红标 (Static Capability Screen)
     │
     ├── 脚本: 22_hybrid_extractor.py (Track 1 & Track 2 - Static Pre-scan)
     │    输入: merged.sqlite3 (description + SKILL.md)
     │    方法: 1. 基于确定性正则表达式规则初步扫描 C_code
     │           2. 高速过滤无需大模型介入的明文基线能力
     │    输出: hybrid_extraction_results.json (部分输出)
     │
     ├── 触发条件: C_act 含黑名单高危行为 (SHELL_EXEC + CRED_TRANSMIT + FILE_DELETE)
     │             且 C_vis 完全未提及
     └── 动作: 挂红标 → 立即告警队列

   防线 B ── ✅ DMR 披露偏差排行 (Disclosure Mismatch Ranker)
     │
     ├── 脚本: 22_hybrid_extractor.py & 23_merge_ensemble.py (多模型语义判定)
     │    输入: merged.sqlite3 + version_transitions_enriched.jsonl
     │    方法: 1. Qwen3/GPT-OSS/Gemma4 并行执行 Track 3 (联合语义推理)
     │           2. 对每个模型的 C_vis 和 C_act 结果进行 2/3 多数票表决合并
     │           3. 依据最终的 majority-vote 集合计算 CR, CP, RWM
     │           4. 检测 Deceptive Denial (否认但存在的能力)
     │    输出: ensemble_merged.json (含逐 skill 详细 IC 分类分析)
     │
     ├── 触发条件: CR < 0.3 (inadequate) 且 RWM ≥ 阈值
     │             综合考虑高权重能力 (w_c > 2.0) 是否被隐藏
     └── 动作: 按 RWM 降序排列 → 优先审查队列

   防线 C ── ✅ TDM 时序漂移监控 (Temporal Drift Monitor)
     │
     ├── 脚本: 03_drift_analysis.py
     │    输入: drift_features.csv (来自阶段 0)
     │    方法: 1. 按 CES drift 排序, 识别能力突增版本
     │           2. 交叉验证 SDS (是否在 changelog 中自我披露)
     │           3. 标注 is_candidate = (CES > 0.1 AND SDS < 0.35)
     │    输出: drift_prevalence_report.md
     │
     ├── 脚本: 20_ic_temporal.py
     │    输入: version_transitions_enriched.jsonl
     │    方法: 1. 对每个版本对独立计算 IC 类型 (v_prev vs v_curr)
     │           2. 构建 IC 转换矩阵: IC-0→IC-2 (从合规恶化到藏匿)
     │           3. 识别 Silent Expansion: C_vis 不变但 C_act 增长
     │    输出: IC temporal transition matrix
     │
     ├── 触发条件: C_vis_{v1} ≈ C_vis_{v2} (描述不变)
     │             C_act_{v2} ⊃ C_act_{v1} (代码能力静默扩大)
     │             SDS < 0.35 (changelog 未披露)
     └── 动作: 标记为 Silent Expansion → 升级审查

   ✅ [18] evaluate_detection_pipeline.py
      输入: 上述三条防线的输出 + T6 真实恶意标签
      方法: 1. 联合计算三防线的覆盖度: SCS∪DMR∪TDM
             2. 报告逐层的: Precision, Recall, F1, P@50, P@100
             3. 按 IC 类别分解 T6 检测率
      输出: pipeline_evaluation_report.md + p_at_k_analysis.json

【阶段 3 产出数据】
   ├── ensemble_merged.json ────────> 合并多模型的语义筛查结果 (阶段 4 验证用)
   ├── drift_features.csv ────────> 时序筛查结果 (阶段 4 验证用)
   └── pipeline_evaluation_report.md ── 三防线联合性能 (论文核心表格)


============================================================================================
[阶段 4: 端到端验证 (End-to-End Evaluation)]
============================================================================================
目标: 用真实攻击数据、独立标注者、跨平台迁移三个维度验证系统有效性。

   ── 验证 1: T6 恶意注入召回 (Malicious Ground Truth Detection) ──

     ✅ 脚本: 05_t6_deep_analysis.py
        输入: drift_features.csv + merged.sqlite3
        方法: 识别已被平台封禁/修复的恶意供给链注入版本
              提取 40 个 T6 pair (prev=恶意, curr=修复)
        输出: t6_cases.csv (含 exposure_days, installs, scan_status)

     ✅ 脚本: 19_t6_case_studies.py
        输入: t6_cases.csv + version_transitions_enriched.jsonl
        方法: 选取 5 个代表性 T6 case, 逐一执行:
              1. Attack Surface Analysis — 7 类攻击模式正则匹配
              2. Data Flow Reconstruction — 凭证→外联窃取路径
              3. User Impact Assessment — install × exposure_days
              4. Detection Layer Coverage — L1(正则)/L2(语义)/L3(时序) 各自是否能捕获
        输出: t6_case_studies.md (5 个深度攻击重建, 带代码证据)

     ✅ 脚本: 18_evaluate_detection_pipeline.py
        输入: 三防线输出 + T6 标签 + IC 分类
        方法: 1. Task 1: 恶意检测 — SCS∪DMR 联合评估 vs T6
              2. Task 2: 更新风险检测 — TDM 评估 vs Silent Expansion
              3. Task 3: 按 IC 类别分解 T6 检出率
        结果: SCS∪DMR 召回 = 94.1% (32/34), 平台 Scanner = 0% (0/34)
        输出: pipeline_evaluation_report.md

   ── 验证 2: LLM 语义裁决 (Semantic Adjudication) ──

     ✅ 脚本: 08_llm_annotator.py (本地启发式注释器)
     ✅ 脚本: 08b_claude_native_m1.py (Claude 原生 API)
     ✅ 脚本: 08c_gpt_native_m2.py (GPT 原生 API)
        输入: 高危预警队列 (T5 Double-Suspicious Pool)
        方法: 三个独立模型对同一 Skill 分别判定:
              · "蓄意隐藏高危能力 (Intentional Concealment)"
              · "开发者疏忽未写说明 (Negligent Omission)"
              · 只有 ≥2/3 模型一致时才确认
        输出: llm_annotation_report.md (逐 skill 的判定 + 一致性统计)
        含义: 量化"恶意"vs"无意"的比例, 回答审稿人 "你怎么知道不是开发者懒?"

   ── 验证 3: 跨平台泛化 (Cross-Platform Generalization) ──

     ✅ 脚本: 21_ic_crossplatform.py
        输入: skills.sh 生态数据 (116,208 skills, 独立数据库)
        方法: 1. 将阶段 1-2 的完整管线原样迁移到 skills.sh
              2. 对比两平台的 IC 分布: IC-1/IC-2/IC-3/IC-4 各占多少?
              3. 对比 CR 分布: 两平台的能力披露透明度是否相似?
        结果: ClawHub 与 skills.sh 均 ≈90% 不一致补充
              证明这不是某一平台的孤例, 而是 LLM Agent 生态的系统性问题
        输出: ic_crossplatform_report.md

【阶段 4 最终结论】
   1. 检测有效性: SCS∪DMR 对已知攻击召回率 94.1%, 远超平台 0%
   2. 意图区分: LLM 裁决可分离"恶意" vs "疏忽", 降低误报运维成本
   3. 普遍性: 两大独立生态呈现高度一致的不透明模式, 问题是系统性的
```

---

# 第二部分：辅助分析 (Auxiliary Analytics)

> 以下模块提供补充视角, 丰富论文深度, 但不属于"声明-行为不一致检测"的核心链路。

## 辅助 A: 风险分类学 (Taxonomy)

| 脚本 | 功能 | 产出 |
|------|------|------|
| ✅ `04_taxonomy_analysis.py` | T1-T6 六类威胁分类 | taxonomy_report.md |
| ✅ `15_risk_taxonomy_classification.py` | 15 类细粒度风险分类 | risk_taxonomy.csv |

## 辅助 B: T3 Reassurance Washing (安抚性话术)

| 脚本 | 功能 | 产出 |
|------|------|------|
| ✅ `12_reassurance_washing.py` | 检测"安全/隐私"话术与高危行为的反相关 | reassurance_washing_report.md |

> 关联但非核心: 这是对 C_vis 的**质量**分析（描述是否在"粉饰"），而非直接对 C_vis vs C_act 的比对。

## 辅助 C: T4 累犯作者画像

| 脚本 | 功能 | 产出 |
|------|------|------|
| ✅ `subanalysis_T1_T4_T5T6.py` (Part B) | 重复制造 Silent Expansion 的开发者排行 | Top 20 repeat offenders |

> 关联但非核心: 这是**作者级别**的行为分析，而非单个 Skill 的声明-行为比对。

## 辅助 D: T5 Double-Drift 交叉分析

| 脚本 | 功能 | 产出 |
|------|------|------|
| ✅ `11_double_drift_analysis.py` | SDG + TDD 双维度都异常的 Skill 交集 | double_drift.csv |

> 关联但非核心: 这是两个独立指标的**交叉组合**，不是新的检测维度。

## 辅助 E: T5→T6 因果分析

| 脚本 | 功能 | 产出 |
|------|------|------|
| ✅ `subanalysis_T1_T4_T5T6.py` (Part C) | 2×2 列联表 + Odds Ratio | T5 是否为 T6 前兆 |

> 关联但非核心: 这是**统计因果推断**，服务于论文叙事而非检测系统本身。

## 辅助 F: T1 能力家族分解

| 脚本 | 功能 | 产出 |
|------|------|------|
| ✅ `subanalysis_T1_T4_T5T6.py` (Part A) | 逐 LSCPS 家族的遗漏率分析 | 哪类能力最常被隐藏 |

> 关联但非核心: 这是主线发现的**细粒度分解**，为论文 §5 提供表格数据。

## 辅助 G: 生态五维画像

| 脚本 | 功能 | 产出 |
|------|------|------|
| ✅ `14_ecosystem_analysis.py` | D1 发布者集中度 (Gini)、D2 垂直领域、D3 真实影响、D4 时间趋势、D5 平台防御缺口 | ecosystem_analysis_report.md |

> 关联但非核心: 这是**生态全景统计**，为论文 Discussion 提供宏观背景。

## 辅助 H: ML 检测器基线

| 脚本 | 功能 | 产出 |
|------|------|------|
| ✅ `06_train_clawhub_detector.py` | LR + RF 监督学习检测器 (5-fold CV) | detector_performance.md |
| ✅ `16_learn_risk_weights.py` | 从 T6 学习 RWM 权重 (LogReg + LOO AUROC) | risk_weights.json |

> 关联但非核心: `16` 的权重输出被主线阶段 2 消费；`06` 是独立的 ML 基线对照。

## 辅助 I: 工具脚本

| 脚本 | 功能 |
|------|------|
| ✅ `07_human_annotation.py` | 人工标注基线工具 |
| ✅ `fix_all_reviewer_issues.py` | 审稿人反馈批量修复 |
| ✅ `generate_ic_figures.py` | 学术图表生成 (PNG) |
| ✅ `generate_ic_figures_pdf.py` | 学术图表生成 (PDF) |

---

# 第三部分：资产统计

## 主线 vs 辅助

| 类别 | 脚本数 | 说明 |
|------|--------|------|
| **主线** (阶段0-4) | 15 个 | 数据→提取→DM→筛选→验证 |
| **辅助** (A-I) | 13 个 | 分类学/话术/累犯/交叉/因果/生态/ML/工具 |
| **新增机制** | 2 个 | `22_hybrid_extractor.py` (多模型推理) <br> `23_merge_ensemble.py` (2/3 majority-vote 投票) |
| **规划中** | 3 维度 | Deps / Env / Schema |
