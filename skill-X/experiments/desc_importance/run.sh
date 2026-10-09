#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Description Importance Experiment — launcher
# Usage:
#   export OPENAI_API_KEY=sk-...
#   bash skill-X/experiments/desc_importance/run.sh
#
# Or pass key inline:
#   OPENAI_API_KEY=sk-... bash skill-X/experiments/desc_importance/run.sh
#
# Options:
#   --n-skills N     number of skills to sample (default: 20)
#   --skip-llm       only run embedding router, no GPT calls for routing
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../../.." && pwd)"

# Load .env if present and OPENAI_API_KEY not set
if [[ -z "${OPENAI_API_KEY:-}" ]]; then
  for env_file in "$REPO_ROOT/.env" "$SCRIPT_DIR/.env"; do
    if [[ -f "$env_file" ]]; then
      echo "[run.sh] Loading $env_file"
      set -a; source "$env_file"; set +a
      break
    fi
  done
fi

if [[ -z "${OPENAI_API_KEY:-}" ]]; then
  echo "[ERROR] OPENAI_API_KEY is not set."
  echo "Export it before running: export OPENAI_API_KEY=sk-..."
  exit 1
fi

echo "============================================================"
echo "  Description Importance Experiment — Pilot 1"
echo "  Model: gpt-4o-mini  |  Embed: text-embedding-3-small"
echo "  Results → skill-X/experiments/desc_importance/results/"
echo "============================================================"

cd "$REPO_ROOT"
python3 skill-X/experiments/desc_importance/run_desc_importance.py "$@"
