# 第二阶段（Stage 2）最新主线报告

> Last updated: 2026-04-15  
> Cleanup status: 旧方案、旧实验和旧 runner 已从仓库移除，只保留当前主线与当前 proposal line。  
> Mainline map: [CURRENT_MAINLINE_AND_ARCHIVE.md](/Users/guangfu/codex/clawhubS/experiments/graph_first_composition/docs/CURRENT_MAINLINE_AND_ARCHIVE.md)

## 1. 当前主线

Stage 2 当前默认主线收敛为三部分：

- 静态分析主线：`analysis_bcr_cis_v3`
- 运行时协议主线：`P25R unified-protocol v4`
- 运行时证据边界：当前 full run 仍属 infrastructure-limited

## 2. 当前主产物

### 2.1 静态组合发现

- [bcr_cis_v3_summary.md](/Users/guangfu/codex/clawhubS/experiments/graph_first_composition/data/analysis_bcr_cis_v3/bcr_cis_v3_summary.md)
- [bcr_cis_v3_pairs.csv](/Users/guangfu/codex/clawhubS/experiments/graph_first_composition/data/analysis_bcr_cis_v3/bcr_cis_v3_pairs.csv)
- [bcr_cis_v3_evidence_layers.md](/Users/guangfu/codex/clawhubS/experiments/graph_first_composition/data/analysis_bcr_cis_v3/bcr_cis_v3_evidence_layers.md)
- [run_bcr_cis_v3.py](/Users/guangfu/codex/clawhubS/experiments/graph_first_composition/scripts/static_filtering/run_bcr_cis_v3.py)

当前 reviewer-safe 静态口径：

- `10,000` 对应 high-recall candidate pool
- `66` 对应 ecological support
- `3` 对应 higher-confidence support

### 2.2 运行时协议主线

- [P25R_UNIFIED_PROTOCOL_V4.md](/Users/guangfu/codex/clawhubS/experiments/graph_first_composition/data/analysis_runtime_p25r_500/P25R_UNIFIED_PROTOCOL_V4.md)
- [README.md](/Users/guangfu/codex/clawhubS/experiments/graph_first_composition/data/analysis_runtime_p25r_500/README.md)
- [p25r_manifest.json](/Users/guangfu/codex/clawhubS/experiments/graph_first_composition/data/analysis_runtime_p25r_500/p25r_manifest.json)
- [setup_p25r_real_skills.py](/Users/guangfu/codex/clawhubS/experiments/graph_first_composition/scripts/runtime_eval/setup_p25r_real_skills.py)
- [run_p25r_pair_eval.py](/Users/guangfu/codex/clawhubS/experiments/graph_first_composition/scripts/runtime_eval/run_p25r_pair_eval.py)
- [analyze_p25r_results.py](/Users/guangfu/codex/clawhubS/experiments/graph_first_composition/scripts/runtime_eval/analyze_p25r_results.py)

### 2.3 当前运行时证据边界

- [RUNTIME_VALIDITY_STATUS_2026-03-27.md](/Users/guangfu/codex/clawhubS/experiments/graph_first_composition/docs/RUNTIME_VALIDITY_STATUS_2026-03-27.md)

当前真实状态：

- 最新 full run 仍主要被 `rate limit / timeout / service unavailable` 类失败污染
- 当前 completed negatives 不能解释为安全证据
- 当前 runtime 结果更适合作为“协议已成型、但执行环境仍需清障”的证据

## 3. 当前保留的新方案线

- [COMPOSITION_ONLY_ARTICLE.md](/Users/guangfu/codex/clawhubS/experiments/graph_first_composition/docs/COMPOSITION_ONLY_ARTICLE.md)
- [P27_COMPOSITION_ONLY_PROTOCOL.md](/Users/guangfu/codex/clawhubS/experiments/graph_first_composition/docs/P27_COMPOSITION_ONLY_PROTOCOL.md)
- [C1_COMPOSITION_EXPERIMENT.md](/Users/guangfu/codex/clawhubS/experiments/graph_first_composition/docs/C1_COMPOSITION_EXPERIMENT.md)

这条线保留为当前 proposal line，不替代默认 `P25R` 主线。

## 4. 当前最稳的 Stage 2 结论

1. 当前仓库的静态主线是 `analysis_bcr_cis_v3`。
2. 当前仓库的 runtime 主线是 `P25R unified-protocol v4`。
3. 当前 full runtime run 仍主要反映执行环境限制，而不是 pair 安全性本身。
4. 当前最稳健的 runtime 表述应是 protocol validity 与 case-mapping capability，而不是最终 violation rate。
