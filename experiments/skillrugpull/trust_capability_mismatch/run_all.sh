#!/usr/bin/env bash
# run_all.sh — Execute the full SkillRugPull pipeline end-to-end
# Usage: bash experiments/skillrugpull/trust_capability_mismatch/run_all.sh
# Run from project root: /Users/guangfu/codex/clawhubS

set -e
cd "$(dirname "$0")/../../.."

PYTHON="${PYTHON:-python3}"
EXP="experiments/skillrugpull/trust_capability_mismatch"

echo "========================================"
echo " SkillRugPull Experiment Pipeline"
echo "========================================"
echo ""

step() {
  echo "── Step $1: $2 ──"
}

step 1 "Build transitions (version pairs)"
$PYTHON $EXP/01_build_transitions.py

step 2 "Extract features (CES, PES, SDS, DGS, RPS)"
$PYTHON $EXP/02_extract_features.py

step 3 "Compute semantic drift (description vs skill_text)"
$PYTHON $EXP/03_semantic_drift.py

step 4 "Score disclosure adequacy"
$PYTHON $EXP/04_disclosure_adequacy.py

step 5 "Prevalence analysis"
$PYTHON $EXP/05_prevalence_analysis.py

step 6 "Risk vs disclosure visualization"
$PYTHON $EXP/06_risk_vs_disclosure.py

step 7 "Category breakdown"
$PYTHON $EXP/07_category_breakdown.py

step 8 "Build unified feature matrix"
$PYTHON $EXP/08_build_feature_matrix.py

step 9 "Train detector (LR + RF + XGBoost baselines)"
$PYTHON $EXP/09_train_detector.py

step 10 "Generate case studies"
$PYTHON $EXP/10_case_studies.py

step 11 "skills.sh declaration mismatch supplement"
$PYTHON $EXP/12_skillssh_declaration_mismatch.py

step "A" "Annotation: stratified sampling"
$PYTHON $EXP/annotation/sampling.py

step "B" "Annotation: generate enriched sheets"
$PYTHON $EXP/annotation/generate_annotation_sheets.py

echo ""
echo "========================================"
echo " All done! Results in: $EXP/results/"
echo "========================================"
echo ""
echo "Key outputs:"
echo "  $EXP/results/prevalence_report.md"
echo "  $EXP/results/disclosure_report.md"
echo "  $EXP/results/category_breakdown.md"
echo "  $EXP/results/detector_performance.md"
echo "  $EXP/results/case_studies.md"
echo "  $EXP/results/skillssh_declaration_mismatch.md"
echo "  $EXP/annotation/annotation_sheets_enriched.csv"
echo ""
