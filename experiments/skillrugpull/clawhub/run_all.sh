#!/usr/bin/env bash
# clawhub/run_all.sh — Run full ClawHub version drift pipeline
# Usage: bash experiments/skillrugpull/clawhub/run_all.sh
# From project root: /Users/guangfu/codex/clawhubS

set -e
cd "$(dirname "$0")/../../.."

PYTHON="${PYTHON:-python3}"
EXP="experiments/skillrugpull/clawhub"

echo "================================================"
echo " ClawHub Trust Drift Pipeline (Full)"
echo " Dataset: data/experiment1_split/merged.sqlite3"
echo "================================================"
echo ""

step() {
  echo ""
  echo "── Step $1: $2 ──"
}

# ── Phase 1: Core Pipeline ──────────────────────────────────────────
step 1 "Build version transitions"
$PYTHON $EXP/01_build_version_transitions.py

step 2 "Extract drift features (CES, SDS, RPS)"
$PYTHON $EXP/02_extract_drift_features.py

step 3 "Drift prevalence analysis (RQ1-T to RQ3-T)"
$PYTHON $EXP/03_drift_analysis.py

step 4 "Taxonomy classification (T1-T6)"
$PYTHON $EXP/04_taxonomy_analysis.py

# ── Phase 2: Deep Analysis ──────────────────────────────────────────
step 5 "T6 malicious case forensic analysis"
$PYTHON $EXP/05_t6_deep_analysis.py

step 6 "Train drift detector (Logistic Regression + Random Forest)"
$PYTHON $EXP/06_train_clawhub_detector.py

# ── Phase 3: Annotation ────────────────────────────────────────────
step 7 "Generate human annotation sheets"
$PYTHON $EXP/07_human_annotation.py

# NOTE: Script 08 (LLM annotator) requires API keys:
#   export ANTHROPIC_API_KEY=... OPENAI_API_KEY=...
# Uncomment to run:
# step 8 "LLM-as-Annotator (requires API keys)"
# $PYTHON $EXP/08_llm_annotator.py

# ── Phase 4: Static Analysis ───────────────────────────────────────
step 9 "Static disclosure gap analysis"
$PYTHON $EXP/09_static_disclosure_gap.py

step 10 "Structured SDG analysis (three-layer)"
$PYTHON $EXP/10_structured_sdg_analysis.py

step 11 "Double drift joint analysis (SDG ∩ TDD)"
$PYTHON $EXP/11_double_drift_analysis.py

step 12 "Reassurance washing analysis"
$PYTHON $EXP/12_reassurance_washing.py

step 13 "YAML frontmatter alignment"
$PYTHON $EXP/13_yaml_alignment.py

step 14 "Ecosystem-level analysis (5 dimensions)"
$PYTHON $EXP/14_ecosystem_analysis.py

step 15 "Risk taxonomy classification (paper figure)"
$PYTHON $EXP/15_risk_taxonomy_classification.py

# ── Phase 5: Sub-analysis ──────────────────────────────────────────
step "S" "Sub-analysis: T1 omission, T4 repeat offenders, T5→T6 correlation"
$PYTHON $EXP/subanalysis_T1_T4_T5T6.py

# ── Phase 6: Detection System (Core Paper Contribution) ───────────
step 16 "Learn data-driven risk weights from T6 ground truth"
$PYTHON $EXP/16_learn_risk_weights.py

step 8 "LLM-as-Annotator (M1: Claude local engine)"
$PYTHON $EXP/08_llm_annotator.py --run-m1

step 17 "Semantic capability inference (Layer 2)"
$PYTHON $EXP/17_llm_semantic_inference.py

step 18 "Three-layer detection pipeline evaluation (Task 1/2/3)"
$PYTHON $EXP/18_evaluate_detection_pipeline.py

step 19 "T6 deep attack case studies"
$PYTHON $EXP/19_t6_case_studies.py

echo ""
echo "================================================"
echo " Done! All results in: $EXP/results/"
echo "================================================"
echo ""
echo "Key outputs:"
echo "  — Measurement —"
echo "  $EXP/results/drift_prevalence_report.md      — RQ1-T to RQ3-T"
echo "  $EXP/results/taxonomy_report.md              — T1-T6 classification"
echo "  $EXP/results/t6_malicious_cases.md           — Forensic analysis"
echo "  $EXP/results/static_gap_report.md            — Static SDG"
echo "  $EXP/results/structured_sdg_report.md        — Three-layer SDG"
echo "  $EXP/results/double_drift_report.md          — SDG ∩ TDD"
echo "  $EXP/results/reassurance_washing_report.md   — Safety theater"
echo "  $EXP/results/ecosystem_analysis_report.md    — 5D ecosystem"
echo "  — Detection System (Paper Core) —"
echo "  $EXP/results/risk_weights.json               — Learned RWM weights"
echo "  $EXP/results/risk_weights_report.md           — Weight ablation"
echo "  $EXP/results/semantic_inference_report.md     — CR/CP/RWM analysis"
echo "  $EXP/results/pipeline_evaluation_report.md   — Task 1/2/3 P/R/F1"
echo "  $EXP/results/llm_annotation_report.md        — LLM annotator κ"
echo "  $EXP/results/t6_case_studies.md              — 5 deep case studies"
echo ""
