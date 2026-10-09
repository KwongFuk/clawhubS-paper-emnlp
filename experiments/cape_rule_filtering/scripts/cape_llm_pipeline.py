#!/usr/bin/env python3
"""
CAPE LLM-Enhanced Pipeline — V2.0
====================================
Architecture:
    Step 1 (LLM-Extract):  SKILL.md → 32B LLM → structured caps
                           Replaces LSCPS keyword matching
    Step 2 (Rule-Pair):    source_pool × sink_pool → candidate pairs
                           OHS scoring kept (rule-based, fast)
    Step 3 (LLM-Verify):   candidate pairs → LLM → viable attack verdict

Why this split:
  - LLM is best at UNDERSTANDING text semantics (Step 1, 3)
  - Rules are fastest for COMBINATORIAL search (Step 2: 31M pairs)
  - LLM on Step 2 would cost ~$50K / take weeks

Model recommendation:
  - Qwen2.5-32B-Instruct (good at structured JSON output)
  - Or Qwen3-32B (thinking mode for complex cases)
  - Served via vLLM on remote server (already installed)

Usage:
    # Start vLLM server on remote:
    #   python -m vllm.entrypoints.openai.api_server \\
    #       --model Qwen/Qwen2.5-32B-Instruct \\
    #       --port 8000 --tensor-parallel-size 4

    # Run pipeline locally (calls remote API):
    python experiments/cape_rule_filtering/scripts/cape_llm_pipeline.py \\
        --step extract --limit 100
    python experiments/cape_rule_filtering/scripts/cape_llm_pipeline.py \\
        --step pair
    python experiments/cape_rule_filtering/scripts/cape_llm_pipeline.py \\
        --step verify --limit 500
"""
from __future__ import annotations

import argparse, json, re, sqlite3, time, sys
from pathlib import Path
from collections import Counter
from typing import Optional

# ── Config ────────────────────────────────────────────────────────────────────
CAPE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = CAPE_ROOT.parents[1]

DB       = REPO_ROOT / "data/experiment1_split/merged.sqlite3"
R        = CAPE_ROOT / "results"

LLM_PROFILES_OUT  = R / "llm_cap_profiles.jsonl"    # Step 1 output
LLM_PAIRS_OUT     = R / "llm_candidate_pairs.jsonl"  # Step 2 output
LLM_VERDICT_OUT   = R / "llm_final_verdicts.jsonl"   # Step 3 output

# Remote vLLM endpoint
VLLM_URL   = "http://localhost:8000/v1"  # forward via: ssh -L 8000:localhost:8000 p
MODEL_NAME = "Qwen/Qwen2.5-32B-Instruct"
OHS_THRESHOLD = 0.75
BATCH_DELAY   = 0.5   # seconds between LLM calls


# ── Step 1: LLM Capability Extraction ─────────────────────────────────────────
EXTRACT_SYSTEM = """You are a security researcher extracting capability profiles from LLM agent skill files.

Given a SKILL.md file, extract EXACTLY which dangerous capabilities the skill has.
Output ONLY valid JSON, no explanation.

Capability taxonomy:
SOURCE (skill brings dangerous data INTO the agent context):
  web_fetch         — fetches external web content (URLs, pages, APIs)
  credential_read   — reads API keys, tokens, passwords, SSH keys
  email_receive     — reads email/inbox content
  file_read         — reads local files that may contain sensitive data

SINK (skill can perform dangerous actions FROM the agent context):
  shell_exec        — executes shell commands, scripts, subprocesses
  network_send      — sends HTTP requests, webhooks, exfiltrates data
  file_write        — writes to local files (generic)
  agent_memory_write— writes to cross-session persistent memory (HOT/WARM/COLD/BOOT.md)
  boot_hook         — writes to gateway startup hooks (BOOT.md, autostart)
  file_delete       — deletes files
  privilege_escalate— sudo, chmod, escalation
  code_commit       — git push, code changes
  db_write          — writes to databases

CHANNEL (how data flows between skills):
  stdout_pipe       — output piped directly to next skill
  workspace_file    — via shared workspace files
  agent_memory      — via persistent memory (HOT_MEMORY.md etc)
  tmp_file          — via /tmp files

TRIGGER (what activates this skill):
  type: T1_VERBATIM | T2_CONTENT_TYPE | T3_SITUATIONAL | T4_AUTONOMOUS
  T4_AUTONOMOUS: runs proactively/automatically without user request
  T1_VERBATIM: activated by specific keywords or commands
  T3_SITUATIONAL: activated when certain conditions in context are met
  spec: the exact trigger condition (max 100 chars)

IMPORTANT RULES:
- agent_memory_write: assign if skill writes to HOT_MEMORY.md, WARM_MEMORY.md, MEMORY.md,
  or uses "Move to HOT/WARM/COLD", "tier redistribution", "archive to COLD"
- boot_hook: assign if skill writes to BOOT.md or gateway startup
- Be CONSERVATIVE for ambiguous cases — only assign caps with clear evidence
- "organizing memory" alone is NOT sufficient for agent_memory_write; need write evidence
"""

EXTRACT_USER_TMPL = """SKILL.md content:
---
{skill_md}
---

Extract capability profile. Output JSON:
{{
  "skill_id": "{skill_id}",
  "source_caps": [],
  "sink_caps": [],
  "channel_caps": [],
  "trigger": {{
    "type": "T1_VERBATIM|T2_CONTENT_TYPE|T3_SITUATIONAL|T4_AUTONOMOUS",
    "spec": "..."
  }},
  "evidence": {{
    "source": "quote from SKILL.md supporting source caps",
    "sink": "quote from SKILL.md supporting sink caps",
    "trigger": "quote from SKILL.md supporting trigger type"
  }},
  "confidence": 0.0-1.0
}}"""


# ── Step 3: LLM Attack Chain Verification ─────────────────────────────────────
VERIFY_SYSTEM = """You are a security researcher verifying whether a specific skill composition
creates an exploitable attack chain in an LLM agent system.

You will be given two skills (source + sink) and asked to judge:
1. Can attacker-controlled content entering via the SOURCE skill realistically
   flow to the SINK skill's dangerous operation?
2. Is there a realistic trigger condition that doesn't require explicit user cooperation?
3. Would a typical user notice this happening?

Output ONLY valid JSON."""

VERIFY_USER_TMPL = """SOURCE SKILL:
  Name: {src_name}
  Source capabilities: {src_caps}
  SKILL.md: {src_md}

SINK SKILL:
  Name: {snk_name}
  Sink capabilities: {snk_caps}
  Trigger: {snk_trigger}
  SKILL.md: {snk_md}

Proposed attack chain: {attack_chain}

Judge this attack chain. Output JSON:
{{
  "skill_pair": "{src_id} × {snk_id}",
  "verdict": "CRITICAL|CONFIRMED|PARTIAL|UNLIKELY",
  "attack_viable": true/false,
  "stealthiness": "HIGH|MEDIUM|LOW",
  "trigger_realistic": true/false,
  "attacker_control_needed": "none|minimal|moderate|extensive",
  "attack_narrative": "2-sentence attack story",
  "missing_conditions": "what would be needed to make this work"
}}"""


# ── LLM Client ────────────────────────────────────────────────────────────────
def call_llm(system: str, user: str, url: str = VLLM_URL,
             model: str = MODEL_NAME, temperature: float = 0.1,
             max_tokens: int = 1024) -> Optional[str]:
    """Call vLLM OpenAI-compatible API."""
    try:
        import urllib.request
        payload = json.dumps({
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user",   "content": user},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }).encode()
        req = urllib.request.Request(
            f"{url}/chat/completions",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = json.loads(resp.read())
            return body["choices"][0]["message"]["content"]
    except Exception as e:
        print(f"  LLM call failed: {e}")
        return None


def parse_json_from_llm(text: str) -> Optional[dict]:
    """Extract first valid JSON object from LLM response."""
    if not text: return None
    # Try direct parse first
    try: return json.loads(text.strip())
    except: pass
    # Extract JSON block
    for m in re.finditer(r'\{.*?\}', text, re.DOTALL):
        try: return json.loads(m.group())
        except: continue
    return None


# ── Step 1: Extract ───────────────────────────────────────────────────────────
def run_step1_extract(limit: int = 0):
    """LLM capability extraction for all skills."""
    con = sqlite3.connect(DB); con.row_factory = sqlite3.Row

    # Load already done
    done = set()
    if LLM_PROFILES_OUT.exists():
        done = {json.loads(l)['skill_id'] for l in LLM_PROFILES_OUT.open()}
    print(f"Already extracted: {len(done)}")

    rows = con.execute(
        "SELECT skill_id, name, downloads, description FROM skills ORDER BY downloads DESC"
    ).fetchall()
    if limit: rows = rows[:limit]

    processed = errors = 0
    with LLM_PROFILES_OUT.open('a') as fout:
        for row in rows:
            sid = row['skill_id']
            if sid in done: continue

            # Get SKILL.md
            frow = con.execute(
                "SELECT text_path FROM skill_files WHERE skill_id=? AND path='SKILL.md' LIMIT 1",
                (sid,)
            ).fetchone()
            skill_md = ''
            if frow and frow['text_path']:
                try: skill_md = open(frow['text_path']).read()[:3000]
                except: pass

            if not skill_md and not row['description']:
                continue

            content = skill_md or row['description']
            prompt  = EXTRACT_USER_TMPL.format(skill_id=sid, skill_md=content[:2500])
            resp    = call_llm(EXTRACT_SYSTEM, prompt)
            parsed  = parse_json_from_llm(resp) if resp else None

            if parsed:
                parsed['skill_id']   = sid
                parsed['name']       = row['name']
                parsed['downloads']  = row['downloads']
                parsed['llm_model']  = MODEL_NAME
                fout.write(json.dumps(parsed, ensure_ascii=False) + '\n')
                fout.flush()
                processed += 1
                print(f"  [{processed}] {sid[:45]} | "
                      f"src={parsed.get('source_caps',[])} | "
                      f"snk={parsed.get('sink_caps',[])} | "
                      f"conf={parsed.get('confidence',0):.2f}")
            else:
                errors += 1

            time.sleep(BATCH_DELAY)

    print(f"\n✅ Step 1 done. Processed: {processed}, Errors: {errors}")


# ── Step 2: Rule-based Pair Search (unchanged logic, fast) ───────────────────
def run_step2_pair():
    """Rule-based pair enumeration from LLM profiles. Same OHS logic."""
    if not LLM_PROFILES_OUT.exists():
        print("Run step1 first."); return

    sys.path.insert(0, str(Path(__file__).parent))
    from cape_filter_config import OHS_THRESHOLD

    profiles = {json.loads(l)['skill_id']: json.loads(l)
                for l in LLM_PROFILES_OUT.open()}
    sources = {sid: p for sid, p in profiles.items() if p.get('source_caps')}
    sinks   = {sid: p for sid, p in profiles.items() if p.get('sink_caps')}

    # Dangerous sink caps (same threshold logic)
    DANGEROUS_SINK_CAPS = {
        'shell_exec', 'network_send', 'script_eval', 'remote_exec',
        'privilege_escalate', 'cloud_upload', 'code_commit', 'file_delete',
        'agent_memory_write', 'boot_hook',  # ← V1.1 additions
    }
    confirmed_sinks = {
        sid: p for sid, p in sinks.items()
        if any(c in DANGEROUS_SINK_CAPS for c in p.get('sink_caps', []))
    }
    print(f"LLM-extracted sources: {len(sources)}")
    print(f"LLM-confirmed sinks:   {len(confirmed_sinks)}")

    # OHS scoring (same formula as cape_trigger_channel.py)
    FREE_TEXT_SOURCES = {'web_fetch', 'email_receive'}

    def ohs(src_caps, snk_trigger_type):
        if snk_trigger_type == 'T4_AUTONOMOUS': return 0.95
        if snk_trigger_type == 'T1_VERBATIM':
            return 0.90 if any(c in FREE_TEXT_SOURCES for c in src_caps) else 0.60
        if snk_trigger_type == 'T3_SITUATIONAL':
            return 0.80 if any(c in FREE_TEXT_SOURCES for c in src_caps) else 0.55
        return 0.70

    pairs = []
    for src_id, src_p in sources.items():
        for snk_id, snk_p in confirmed_sinks.items():
            if src_id == snk_id: continue
            trigger_type = snk_p.get('trigger', {}).get('type', 'T3_SITUATIONAL')
            score = ohs(src_p.get('source_caps', []), trigger_type)
            if score < OHS_THRESHOLD: continue

            pairs.append({
                'source_id':   src_id,
                'source_name': src_p.get('name', src_id),
                'source_dl':   src_p.get('downloads', 0),
                'source_caps': src_p.get('source_caps', []),
                'sink_id':     snk_id,
                'sink_name':   snk_p.get('name', snk_id),
                'sink_dl':     snk_p.get('downloads', 0),
                'sink_caps':   snk_p.get('sink_caps', []),
                'sink_trigger_type': trigger_type,
                'ohs':         score,
                'attack_chain': (
                    f"[{','.join(src_p.get('source_caps',[]))}]"
                    f" → [{','.join(snk_p.get('sink_caps',[]))}]"
                ),
            })

    pairs.sort(key=lambda x: x['ohs'] * (x['source_dl'] + x['sink_dl']), reverse=True)
    with LLM_PAIRS_OUT.open('w') as f:
        for p in pairs: f.write(json.dumps(p, ensure_ascii=False) + '\n')

    print(f"\n✅ Step 2 done. Candidate pairs: {len(pairs):,}")
    trig_dist = Counter(p['sink_trigger_type'] for p in pairs)
    for t, n in trig_dist.most_common():
        print(f"  {t}: {n:,}")


# ── Step 3: LLM Final Verification ───────────────────────────────────────────
def run_step3_verify(limit: int = 500):
    """LLM verification of top candidate pairs."""
    if not LLM_PAIRS_OUT.exists():
        print("Run step2 first."); return

    con = sqlite3.connect(DB); con.row_factory = sqlite3.Row

    done = set()
    if LLM_VERDICT_OUT.exists():
        done = {json.loads(l)['skill_pair'] for l in LLM_VERDICT_OUT.open()}

    pairs = [json.loads(l) for l in LLM_PAIRS_OUT.open()]
    if limit: pairs = pairs[:limit]
    pairs = [p for p in pairs if f"{p['source_id']} × {p['sink_id']}" not in done]
    print(f"Pairs to verify: {len(pairs)}")

    def get_skill_md(skill_id):
        frow = con.execute(
            "SELECT text_path FROM skill_files WHERE skill_id=? AND path='SKILL.md' LIMIT 1",
            (skill_id,)
        ).fetchone()
        if frow and frow['text_path']:
            try: return open(frow['text_path']).read()[:1500]
            except: pass
        return ''

    processed = 0
    with LLM_VERDICT_OUT.open('a') as fout:
        for p in pairs:
            prompt = VERIFY_USER_TMPL.format(
                src_id=p['source_id'],
                src_name=p['source_name'],
                src_caps=p['source_caps'],
                src_md=get_skill_md(p['source_id'])[:800],
                snk_id=p['sink_id'],
                snk_name=p['sink_name'],
                snk_caps=p['sink_caps'],
                snk_trigger=p['sink_trigger_type'],
                snk_md=get_skill_md(p['sink_id'])[:800],
                attack_chain=p['attack_chain'],
            )
            resp   = call_llm(VERIFY_SYSTEM, prompt, max_tokens=512)
            parsed = parse_json_from_llm(resp) if resp else None

            if parsed:
                parsed['ohs'] = p['ohs']
                parsed['source_dl'] = p['source_dl']
                parsed['sink_dl']   = p['sink_dl']
                fout.write(json.dumps(parsed, ensure_ascii=False) + '\n')
                fout.flush()
                processed += 1
                print(f"  [{processed}] {p['source_name'][:25]} × {p['sink_name'][:25]}"
                      f" → {parsed.get('verdict','?')} "
                      f"(viable={parsed.get('attack_viable','?')})")

            time.sleep(BATCH_DELAY)

    print(f"\n✅ Step 3 done. Verified: {processed}")
    if LLM_VERDICT_OUT.exists():
        verdicts = [json.loads(l) for l in LLM_VERDICT_OUT.open()]
        dist = Counter(v['verdict'] for v in verdicts)
        print("Verdict distribution:", dict(dist))
        dangerous = [v for v in verdicts if v.get('attack_viable')]
        print(f"Viable attacks confirmed: {len(dangerous)}")


# ── CLI ───────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='CAPE LLM Pipeline V2.0')
    parser.add_argument('--step', choices=['extract', 'pair', 'verify', 'all'],
                        required=True)
    parser.add_argument('--limit', type=int, default=0,
                        help='Limit number of items to process (0=all)')
    parser.add_argument('--vllm-url', default=VLLM_URL,
                        help=f'vLLM API endpoint (default: {VLLM_URL})')
    parser.add_argument('--model', default=MODEL_NAME)
    args = parser.parse_args()

    VLLM_URL   = args.vllm_url
    MODEL_NAME = args.model

    if args.step in ('extract', 'all'):
        run_step1_extract(args.limit)
    if args.step in ('pair', 'all'):
        run_step2_pair()
    if args.step in ('verify', 'all'):
        run_step3_verify(args.limit or 500)
