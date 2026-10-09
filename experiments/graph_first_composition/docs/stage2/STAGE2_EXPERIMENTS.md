# 第二阶段（Stage 2）运行时实验主线

本文件只保留当前仍在仓库中的 runtime 主线与当前 proposal line。

当前默认层次：

- 运行时协议主线：`P25R unified-protocol v4`
- 当前运行时证据边界：latest full run is infrastructure-limited

## 1. 当前默认 runtime 主线

### 1.1 P25R unified-protocol v4

- [P25R_UNIFIED_PROTOCOL_V4.md](/Users/guangfu/codex/clawhubS/experiments/graph_first_composition/data/analysis_runtime_p25r_500/P25R_UNIFIED_PROTOCOL_V4.md)
- [README.md](/Users/guangfu/codex/clawhubS/experiments/graph_first_composition/data/analysis_runtime_p25r_500/README.md)
- [p25r_manifest.json](/Users/guangfu/codex/clawhubS/experiments/graph_first_composition/data/analysis_runtime_p25r_500/p25r_manifest.json)
- [p25r_seed_pool_summary.md](/Users/guangfu/codex/clawhubS/experiments/graph_first_composition/data/analysis_runtime_p25r_500/p25r_seed_pool_summary.md)
- [p25r_selection_summary.md](/Users/guangfu/codex/clawhubS/experiments/graph_first_composition/data/analysis_runtime_p25r_500/p25r_selection_summary.md)
- [setup_p25r_real_skills.py](/Users/guangfu/codex/clawhubS/experiments/graph_first_composition/scripts/runtime_eval/setup_p25r_real_skills.py)
- [run_p25r_pair_eval.py](/Users/guangfu/codex/clawhubS/experiments/graph_first_composition/scripts/runtime_eval/run_p25r_pair_eval.py)
- [analyze_p25r_results.py](/Users/guangfu/codex/clawhubS/experiments/graph_first_composition/scripts/runtime_eval/analyze_p25r_results.py)

当前定位：

- 当前仓库默认的 threat-model-aligned literal skill-pair runtime protocol
- 使用 unified seed pool 与 500-case manifest
- 在固定 literal pair 上比较 `authorized_baseline / same_pair_control / contaminated_resource / guarded_contaminated / relay_trigger`
- 只把 direct `contaminated_resource` 正例视为 strict direct `AConC` evidence

### 1.2 当前 runtime 证据边界

- [RUNTIME_VALIDITY_STATUS_2026-03-27.md](/Users/guangfu/codex/clawhubS/experiments/graph_first_composition/docs/RUNTIME_VALIDITY_STATUS_2026-03-27.md)

当前定位：

- 用来明确当前 full run 还不能当作 usable rate evidence
- 解释为什么 completed runtime negatives 不能被写成“安全”
- 记录当前 reviewer 最可能追问的执行 confounds

## 2. 当前保留的 proposal line

### 2.1 P27（proposed）

- [P27_COMPOSITION_ONLY_PROTOCOL.md](/Users/guangfu/codex/clawhubS/experiments/graph_first_composition/docs/P27_COMPOSITION_ONLY_PROTOCOL.md)

### 2.2 C1（proposed）

- [C1_COMPOSITION_EXPERIMENT.md](/Users/guangfu/codex/clawhubS/experiments/graph_first_composition/docs/C1_COMPOSITION_EXPERIMENT.md)

这条线用于“单技能安全、组合后失效”的 composition-only 主张，和当前默认 `P25R` 主线分开。

## 3. 当前写作和复现口径

- 静态主线：`analysis_bcr_cis_v3`
- runtime 协议主线：`P25R unified-protocol v4`
- runtime 证据边界：latest full run remains infrastructure-limited
- 新方案线：`P27` 与 `C1`
