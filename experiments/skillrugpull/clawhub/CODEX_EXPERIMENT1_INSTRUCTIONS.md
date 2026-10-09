# Experiment 1: LLM-as-Annotator (Codex Task)

## Task Description
Run LLM-as-Annotator using Claude API to annotate 160 samples and produce
Cohen's κ inter-annotator agreement score.

## Prerequisites
Make sure `anthropic` package is installed:
```bash
pip3 install anthropic --break-system-packages
```

## Environment Setup
Set your Anthropic API key:
```bash
export ANTHROPIC_API_KEY="sk-ant-..."   # your Claude key
```

## Run the annotator
```bash
cd /Users/guangfu/codex/clawhubS

# Option A: Claude only (one annotator)
ANTHROPIC_API_KEY="sk-ant-..." python3 experiments/skillrugpull/clawhub/08_llm_annotator.py \
  --model1-only \
  --model1 claude-3-5-haiku-20241022 \
  --delay 0.5

# Option B: Claude as annotator 1, GPT-4o-mini as annotator 2 (recommended, gives κ)
ANTHROPIC_API_KEY="sk-ant-..." OPENAI_API_KEY="sk-..." \
  python3 experiments/skillrugpull/clawhub/08_llm_annotator.py \
  --model1 claude-3-5-haiku-20241022 \
  --model2 gpt-4o-mini \
  --delay 0.5
```

## Expected Output
- `experiments/skillrugpull/clawhub/data/llm_annotations.csv` — raw labels
- `experiments/skillrugpull/clawhub/results/llm_annotation_report.md` — κ + stats

## What to check in the report
1. `Cohen's κ` ≥ 0.6 = acceptable for paper
2. `CONFIRMED` rate in 
3. `Precision` = CONFIRMED rate among auto-labeled T1/T2 candidates

## Cost Estimate
- claude-3-5-haiku: ~160 calls × ~$0.001 = **$0.16**
- gpt-4o-mini: ~160 calls × ~$0.0002 = **$0.03**
- Total: **< $0.20**

## If error occurs
Check that `annotation_sheet.csv` exists:
```bash
ls experiments/skillrugpull/clawhub/annotation/annotation_sheet.csv
wc -l experiments/skillrugpull/clawhub/annotation/annotation_sheet.csv
```

The JSONL for full text:
```bash
ls experiments/skillrugpull/clawhub/data/version_transitions_enriched.jsonl
```

If JSONL missing, the annotator will still work but without full SKILL.md diff text—
it will use description + changelog + risk keywords instead.
