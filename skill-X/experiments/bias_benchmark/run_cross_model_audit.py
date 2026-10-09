#!/usr/bin/env python3
"""
run_cross_model_audit.py — Multi-Model Auditor Cross-Validation (A2)
=====================================================================
Addresses ARR W1: "single LLM auditor" weakness.

Runs the SAME IC audit prompt on a random 2,000-skill subset using
GPT-5.4-mini via openclaw CLI, then compares with existing Qwen2.5-7B results.

Usage:
  PYTHONUNBUFFERED=1 nohup python3 run_cross_model_audit.py \
    > results/cross_model_audit.log 2>&1 &
"""
import json
import os
import random
import re
import sqlite3
import subprocess
import time
from pathlib import Path

RESULTS_DIR = Path(__file__).parent / "results"
DB_PATH = Path(__file__).parents[3] / "data/experiment1_split/merged.sqlite3"

SAMPLE_SIZE = 2000
SEED = 42

OPENCLAW_BIN = "/opt/homebrew/bin/openclaw"
OPENCLAW_ENV = {**os.environ, "PATH": "/opt/homebrew/bin:" + os.environ.get("PATH", "")}
BASE_DIR = Path.home() / "codex" / "clawhubS"

SYSTEM_PROMPT = """\
You are a security auditor for an AI agent skill marketplace called ClawHub.
Analyze the skill description and classify it.

## IC Class
- IC-0: Description adequately represents scope
- IC-1s: Likely HIDES security-sensitive capabilities (network, file I/O, shell, credentials)
- IC-1q: Likely HIDES functional capabilities (quality issue)
- IC-2: OVERCLAIMS capabilities unlikely to implement

## Severity: none | minor | major

Respond with ONLY valid JSON:
{"ic_class": "IC-0|IC-1s|IC-1q|IC-2", "d1_severity": "none|minor|major",
 "quality_issues": [], "abuse_signals": [], "security_sensitive": false,
 "confidence": 0.0, "reason": "brief"}"""

USER_TEMPLATE = """\
Skill: {name}
ID: {skill_id}
Description: {description}
Category: {category}
Downloads: {downloads}"""


def call_openclaw(prompt: str, retries: int = 3) -> str:
    """Call model via openclaw gateway."""
    for attempt in range(retries):
        try:
            cmd = [OPENCLAW_BIN, "infer", "model", "run",
                   "--gateway", "--prompt", prompt, "--json"]
            p = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL, env=OPENCLAW_ENV,
                cwd=str(BASE_DIR), preexec_fn=os.setsid)
            stdout, stderr = p.communicate(timeout=90)
            
            if p.returncode == 0:
                text = stdout.decode("utf-8", errors="replace")
                try:
                    d = json.loads(text.strip())
                    return d.get("outputs", [{}])[0].get("text", "").strip()
                except json.JSONDecodeError:
                    m = re.search(r'\{.*\}', text, re.DOTALL)
                    if m:
                        d = json.loads(m.group())
                        return d.get("outputs", [{}])[0].get("text", "").strip()
            
            stderr_text = stderr.decode("utf-8", errors="replace")
            if "exhausted" in stderr_text or "quota" in stderr_text:
                m = re.search(r'reset after (\d+)m', stderr_text)
                wait = int(m.group(1)) * 60 + 10 if m else 60
                print(f"  [QUOTA] Waiting {wait}s...", flush=True)
                time.sleep(wait)
            elif attempt < retries - 1:
                time.sleep(3)
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(3)
            else:
                return f"ERROR: {e}"
    return "ERROR: max retries"


def sample_skills():
    db = sqlite3.connect(str(DB_PATH))
    rows = db.execute("""
        SELECT skill_id, name, description,
               COALESCE(downloads, 0) as downloads,
               COALESCE(category, '') as category
        FROM skills WHERE description IS NOT NULL AND length(description) > 10
        ORDER BY skill_id
    """).fetchall()
    db.close()
    skills = [{"skill_id": r[0], "name": r[1], "description": r[2],
               "downloads": r[3], "category": r[4]} for r in rows]
    random.seed(SEED)
    return random.sample(skills, min(SAMPLE_SIZE, len(skills)))


def audit_one(skill):
    user_msg = USER_TEMPLATE.format(**skill)
    prompt = f"{SYSTEM_PROMPT}\n\n{user_msg}"
    
    text = call_openclaw(prompt)
    if text.startswith("ERROR"):
        return {"skill_id": skill["skill_id"], "name": skill["name"],
                "ic_class": "ERROR", "error": text}
    
    try:
        # Parse JSON from response
        text = text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0]
        # Find JSON object
        m = re.search(r'\{[^{}]*\}', text, re.DOTALL)
        if m:
            result = json.loads(m.group())
        else:
            result = json.loads(text)
        result["skill_id"] = skill["skill_id"]
        result["name"] = skill["name"]
        return result
    except Exception as e:
        return {"skill_id": skill["skill_id"], "name": skill["name"],
                "ic_class": "ERROR", "error": f"Parse: {e}", "raw": text[:200]}


def main():
    print("=" * 70, flush=True)
    print("CROSS-MODEL AUDITOR VALIDATION (A2)", flush=True)
    print(f"Model: openai-codex/gpt-5.4-mini (via openclaw)", flush=True)
    print(f"Sample: {SAMPLE_SIZE} skills (seed={SEED})", flush=True)
    print("=" * 70, flush=True)
    
    skills = sample_skills()
    print(f"Sampled {len(skills)} skills", flush=True)
    
    out_path = RESULTS_DIR / "exp5_cross_model_audit_gpt54mini.json"
    
    # Resume support
    done_ids = set()
    results = []
    if out_path.exists():
        old = json.loads(out_path.read_text())
        # Keep non-error results
        results = [r for r in old if r.get("ic_class") != "ERROR"]
        done_ids = {r["skill_id"] for r in results}
        print(f"Resuming: {len(results)} good, skipping {len(done_ids)}", flush=True)
    
    remaining = [s for s in skills if s["skill_id"] not in done_ids]
    print(f"Remaining: {len(remaining)}", flush=True)
    
    ic_counts = {}
    for r in results:
        ic = r.get("ic_class", "ERROR")
        ic_counts[ic] = ic_counts.get(ic, 0) + 1
    
    for i, skill in enumerate(remaining):
        result = audit_one(skill)
        results.append(result)
        
        ic = result.get("ic_class", "ERROR")
        ic_counts[ic] = ic_counts.get(ic, 0) + 1
        
        total = len(done_ids) + i + 1
        if total % 25 == 0:
            with open(out_path, "w") as f:
                json.dump(results, f)
            print(f"[{total}/{len(skills)}] {skill['name'][:30]:30} → {ic}", flush=True)
            print(f"  IC: {dict(ic_counts)}", flush=True)
    
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2)
    
    print("\n" + "=" * 70, flush=True)
    print("FINAL IC DISTRIBUTION (GPT-5.4-mini)", flush=True)
    total = len(results)
    for ic in ["IC-0", "IC-1s", "IC-1q", "IC-2", "ERROR"]:
        n = ic_counts.get(ic, 0)
        pct = n / total * 100 if total else 0
        print(f"  {ic:8} {n:6} ({pct:.1f}%)", flush=True)
    print(f"Saved: {out_path}", flush=True)


if __name__ == "__main__":
    main()
