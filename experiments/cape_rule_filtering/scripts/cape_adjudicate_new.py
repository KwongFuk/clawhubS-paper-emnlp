#!/usr/bin/env python3
"""
CAPE V1.1 — 补充评判新发现的 sink skills
==========================================
评判 V1.1 新增的、尚未经过 Codex 评判的 sink candidates。
主要包括: agent_memory_write / boot_hook 类 skills。

Usage:
    .venv/bin/python experiments/cape_rule_filtering/scripts/cape_adjudicate_new.py
"""
from __future__ import annotations
import json, re, sqlite3, subprocess, sys, time
from pathlib import Path
from collections import Counter

sys.path.insert(0, str(Path(__file__).parent))
from cape_filter_config import (
    SINK_SEVERITY, MIN_SINK_SEV, is_sink_candidate, augment_sink_caps,
    TRIGGER_PATTERNS, HIGH_HIJACK_CLASSES
)

CAPE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = CAPE_ROOT.parents[1]
RESULTS_DIR = CAPE_ROOT / "results"

DB       = REPO_ROOT / "data/experiment1_split/merged.sqlite3"
PROFILES = RESULTS_DIR / "cape_rq2_profiles.jsonl"
EXISTING = RESULTS_DIR / "full_adjudication_codex.jsonl"
OUT      = RESULTS_DIR / "cape_v11_new_adjudication.jsonl"
CODEX    = "/opt/homebrew/bin/codex"

BATCH_SIZE    = 5
BATCH_DELAY_S = 45
MAX_RETRIES   = 2

# ── Load existing adjudicated skill IDs ──────────────────────────────────────
already_done = set()
for l in EXISTING.open():
    try:
        already_done.add(json.loads(l)['skill_id'])
    except: pass
print(f"Already adjudicated: {len(already_done)} skills")

# ── Load profiles & find NEW candidates ──────────────────────────────────────
profiles = {json.loads(l)['skill_id']: json.loads(l) for l in PROFILES.open()}
con = sqlite3.connect(DB); con.row_factory = sqlite3.Row

new_candidates = []
for skill_id, p in profiles.items():
    if skill_id in already_done: continue
    row = con.execute(
        "SELECT name, downloads, description FROM skills WHERE skill_id=?", (skill_id,)
    ).fetchone()
    if not row: continue

    frow = con.execute(
        "SELECT text_path FROM skill_files WHERE skill_id=? AND path='SKILL.md' LIMIT 1",
        (skill_id,)
    ).fetchone()
    skill_md = ''
    if frow and frow['text_path']:
        try: skill_md = open(frow['text_path']).read()
        except: pass

    ok, hijack, matched = is_sink_candidate(
        p.get('sink_caps', []), row['description'] or '', skill_md
    )
    if not ok: continue

    # Only include those that have agent_memory_write or boot_hook as NEW caps
    aug = augment_sink_caps(p.get('sink_caps', []), row['description'] or '', skill_md)
    new_caps = set(aug) - set(p.get('sink_caps', []))
    if not (new_caps & {'agent_memory_write', 'boot_hook'}): continue

    new_candidates.append({
        'skill_id':    skill_id,
        'name':        row['name'],
        'downloads':   row['downloads'],
        'description': row['description'] or '',
        'sink_caps':   aug,
        'new_caps':    list(new_caps),
        'hijack':      hijack,
        'matched':     matched,
        'skill_md':    skill_md[:3000],
    })

new_candidates.sort(key=lambda x: x['downloads'], reverse=True)
print(f"New V1.1 candidates to adjudicate: {len(new_candidates)}")
for c in new_candidates[:5]:
    print(f"  DL={c['downloads']:>5,}  {c['name'][:40]}  new={c['new_caps']}")

if not new_candidates:
    print("Nothing new to adjudicate.")
    sys.exit(0)

# ── Codex adjudication prompt ─────────────────────────────────────────────────
PROMPT_TMPL = """You are a security researcher analyzing OpenClaw agent skills for dangerous sink capabilities.

Evaluate each skill for CROSS-SESSION MEMORY POISONING and STARTUP HOOK attack vectors.
These are NEW threat categories (C3/BOOT channel):
- agent_memory_write: skill can write to HOT_MEMORY.md or similar cross-session persistent files
- boot_hook: skill updates BOOT.md or gateway startup hooks

For each skill, output ONLY valid JSON:
{{
  "skill_id": "...",
  "verdict": "CRITICAL|CONFIRMED|PARTIAL|UNLIKELY",
  "attack_type": "one sentence describing the specific attack",
  "exploitable_trigger": "what user action or agent context triggers the dangerous write",
  "channel": "agent_memory_write|boot_hook|both",
  "confidence": 0.0-1.0,
  "rationale": "2-3 sentences"
}}

VERDICT GUIDE:
- CRITICAL: skill explicitly writes to HOT_MEMORY/BOOT.md AND has injectable trigger
- CONFIRMED: skill writes to persistent memory file AND trigger is realistic
- PARTIAL: skill mentions persistent memory but trigger unclear or write is conditional  
- UNLIKELY: skill reads memory only, or write is heavily restricted

SKILLS TO EVALUATE:
{skills_block}

Output one JSON object per skill, separated by newlines.
"""

def read_skill_files_for_prompt(skill_id: str, skill_md: str) -> str:
    return skill_md[:2000] if skill_md else '(no SKILL.md)'


def build_prompt(batch: list[dict]) -> str:
    blocks = []
    for i, s in enumerate(batch, 1):
        blocks.append(
            f"[{i}] skill_id: {s['skill_id']}\n"
            f"    name: {s['name']}\n"
            f"    description: {s['description'][:200]}\n"
            f"    detected_new_caps: {s['new_caps']}\n"
            f"    SKILL.md excerpt:\n{s['skill_md'][:800]}\n"
        )
    return PROMPT_TMPL.format(skills_block='\n'.join(blocks))


def parse_verdicts(text: str, batch: list[dict]) -> list[dict]:
    results = []
    # Extract all JSON objects
    for m in re.finditer(r'\{[^{}]+\}', text, re.DOTALL):
        try:
            obj = json.loads(m.group())
            if 'verdict' in obj and 'skill_id' in obj:
                results.append(obj)
        except: pass

    # If fewer results than batch, fill with PARTIAL
    found_ids = {r['skill_id'] for r in results}
    for s in batch:
        if s['skill_id'] not in found_ids:
            results.append({
                'skill_id':   s['skill_id'],
                'verdict':    'PARTIAL',
                'attack_type': 'Parse failure — defaulted to PARTIAL',
                'exploitable_trigger': s.get('matched', ['unknown'])[0] if s.get('matched') else 'unknown',
                'channel':    s['new_caps'][0] if s['new_caps'] else 'agent_memory_write',
                'confidence': 0.3,
                'rationale':  'Could not parse Codex response for this skill.',
            })
    return results


def run_codex(prompt: str) -> str:
    for attempt in range(MAX_RETRIES):
        try:
            result = subprocess.run(
                [CODEX, "exec", "--quiet", "--full-auto", prompt],
                capture_output=True, text=True, timeout=120
            )
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout
        except subprocess.TimeoutExpired:
            print(f"  Timeout attempt {attempt+1}")
        time.sleep(5)
    return ''


# ── Run batches ───────────────────────────────────────────────────────────────
results_all = []
batches = [new_candidates[i:i+BATCH_SIZE] for i in range(0, len(new_candidates), BATCH_SIZE)]

print(f"\nRunning {len(batches)} Codex batches ({BATCH_SIZE} skills each)...")
OUT.parent.mkdir(exist_ok=True)

# Check if some already done in this run
done_this_run = set()
if OUT.exists():
    for l in OUT.open():
        try:
            done_this_run.add(json.loads(l)['skill_id'])
        except: pass
print(f"Already done in this run: {len(done_this_run)}")

with OUT.open('a') as fout:
    for bi, batch in enumerate(batches):
        batch_filtered = [s for s in batch if s['skill_id'] not in done_this_run]
        if not batch_filtered:
            print(f"  Batch {bi+1}/{len(batches)}: all done, skip")
            continue

        print(f"\n  Batch {bi+1}/{len(batches)}: {[s['name'][:20] for s in batch_filtered]}")
        prompt = build_prompt(batch_filtered)
        response = run_codex(prompt)

        if not response:
            print(f"  ⚠ No response, marking as PARTIAL")
            verdicts = parse_verdicts('', batch_filtered)
        else:
            verdicts = parse_verdicts(response, batch_filtered)

        for v in verdicts:
            # Enrich with metadata
            orig = next((s for s in batch_filtered if s['skill_id'] == v.get('skill_id','')), {})
            v['downloads'] = orig.get('downloads', 0)
            v['new_caps']  = orig.get('new_caps', [])
            v['name']      = orig.get('name', v.get('skill_id',''))
            fout.write(json.dumps(v, ensure_ascii=False) + '\n')
            fout.flush()
            results_all.append(v)
            print(f"  → {v['skill_id'][:40]}: {v['verdict']} ({v.get('confidence',0):.1f})")

        if bi < len(batches) - 1:
            print(f"  Waiting {BATCH_DELAY_S}s...")
            time.sleep(BATCH_DELAY_S)

print(f"\n✅ Done. Results saved to {OUT}")
verdicts_dist = Counter(r['verdict'] for r in results_all)
print(f"Verdict distribution: {dict(verdicts_dist)}")
