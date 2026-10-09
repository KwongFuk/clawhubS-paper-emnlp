#!/usr/bin/env python3
"""
skill_risk_agent_vllm_async.py
==============================
Async, high-throughput multi-agent skill risk scanner.
Uses concurrent.futures to send parallel requests to vllm.

Usage:
  python skill_risk_agent_vllm_async.py \
      --db /path/to/skills.sqlite3 \
      --output results/risk_agent/risk_full_JOBID.jsonl \
      --model gpt-oss-20b \
      --endpoint http://localhost:8001/v1 \
      --chunk-id 0 --total-chunks 4   # for Slurm array
      --concurrency 16
"""

import json
import sqlite3
import argparse
import time
import os
import sys
import re
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from openai import OpenAI

OUTDIR = Path(__file__).parent / "results/risk_agent"
OUTDIR.mkdir(parents=True, exist_ok=True)

# ── Prompts ───────────────────────────────────────────────────────────────────

AGENT1_SYS = """You analyze an AI skill marketplace description and extract what the publisher CLAIMS.

Return JSON only:
{
  "functional_role": "one phrase: what kind of tool this claims to be",
  "claimed_capabilities": ["cap1", "cap2"],
  "authority_claims": ["any special authority/quality claim"],
  "implied_permissions": ["what resource access is implied"],
  "trust_signals": ["phrases that build trust without evidence"]
}"""

AGENT2_SYS = """You are a security auditor reading an AI skill body (implementation/docs).
Extract what it ACTUALLY does at operational level.

Return JSON only:
{
  "actual_operations": ["op1", "op2"],
  "resource_access": ["files/network/creds accessed"],
  "permission_labels": ["SHELL_EXEC","CRED_READ","FILE_WRITE","NETWORK_OUT","DB_ACCESS","PROCESS_SPAWN"],
  "instruction_patterns": ["adversarial override directives found"],
  "body_scope": "one sentence: what this actually does"
}"""

AGENT3_SYS = """Compare description claims vs body implementation for an AI skill.
Map both to L2 capability domains: search, installation, quality_assurance, security_scanning,
code_execution, file_access, credential_access, network_access, data_collection, api_integration,
text_generation, recommendation, authentication, monitoring, automation.

Return JSON only:
{
  "desc_l2_domains": ["domain1"],
  "body_l2_domains": ["domain1"],
  "description_surplus": ["in desc but NOT in body"],
  "body_surplus": ["in body but NOT in desc"],
  "level_shift": "role_mismatch|scope_inflation|perm_gap|aligned",
  "bridge_explanation": "one sentence explaining the key gap"
}"""

AGENT5_SYS = """Write a brief security report for an AI skill marketplace risk finding.
Return JSON only: {"summary": "2-sentence risk summary", "repairs": ["fix1", "fix2"]}"""

# ── Risk patterns (deterministic, no LLM needed) ──────────────────────────────

HIGH_RISK_PERMS = {"SHELL_EXEC", "CRED_READ", "CRED_TRANSMIT",
                   "FILE_DELETE", "NETWORK_OUT", "PROCESS_SPAWN"}
SECURITY_SIGNALS = {"scanner", "auditor", "security", "threat", "malware",
                    "detector", "monitor", "compliance", "vulnerability"}

# Injection patterns for description
INJECTION_RE = [
    re.compile(r'\byou are now\b', re.I),
    re.compile(r'[\u200b\u200c\u200d\ufeff\u202a-\u202e]'),  # zero-width chars
    re.compile(r'\bignore\b.{0,40}\b(previous|prior)\b', re.I | re.DOTALL),
    re.compile(r'\[INST\]', re.I),
    re.compile(r'\bnew instructions?:\s', re.I),
    re.compile(r'\bdisregard\b.{0,30}\b(previous|prior|your)\b', re.I | re.DOTALL),
]

# ── LLM client ────────────────────────────────────────────────────────────────

def make_client(endpoint, api_key="placeholder"):
    return OpenAI(api_key=api_key, base_url=endpoint)

def llm_json(client, model, system, user, max_tokens=800):
    """Call LLM, parse JSON from response. Returns dict or None."""
    for attempt in range(2):
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[{"role": "system", "content": system},
                          {"role": "user", "content": user}],
                max_tokens=max_tokens,
                temperature=0.05,
            )
            raw = resp.choices[0].message.content
            # Try to parse JSON from response
            for chunk in raw.split("```"):
                chunk = chunk.strip().lstrip("json").strip()
                try:
                    return json.loads(chunk)
                except:
                    pass
            try:
                return json.loads(raw)
            except:
                return None
        except Exception as e:
            if attempt == 1:
                return None
            time.sleep(1)
    return None

# ── 5-Agent pipeline for one skill ───────────────────────────────────────────

def run_pipeline(client, model, slug, description, body):
    t0 = time.time()
    desc_trunc = description[:2000]
    body_trunc = body[:3500]

    # Agent 1: DescIntent
    d = llm_json(client, model, AGENT1_SYS,
        f"Skill: {slug}\nDescription:\n{desc_trunc}\nExtract intent.") or {}

    # Agent 2: BodyOps
    b = llm_json(client, model, AGENT2_SYS,
        f"Skill: {slug}\nBody:\n{body_trunc}\nExtract operations.") or {}

    # Agent 3: Bridge
    bridge_user = (
        f"Description claims: role={d.get('functional_role','?')}, "
        f"caps={json.dumps(d.get('claimed_capabilities',[])[:5])}, "
        f"auth={json.dumps(d.get('authority_claims',[])[:3])}, "
        f"perms={json.dumps(d.get('implied_permissions',[])[:3])}\n\n"
        f"Body: ops={json.dumps(b.get('actual_operations',[])[:6])}, "
        f"perms={json.dumps(b.get('permission_labels',[]))}, "
        f"scope={b.get('body_scope','?')}"
    )
    br = llm_json(client, model, AGENT3_SYS, bridge_user) or {}

    # Agent 4: Classify (deterministic from agent outputs)
    patterns = []
    evidence = {}

    # A5: injection in body instruction patterns
    if b.get("instruction_patterns"):
        patterns.append("A5")
        evidence["A5"] = {"explanation": str(b["instruction_patterns"][:2])}

    # A5 check desc directly
    if not "A5" in patterns:
        for rx in INJECTION_RE:
            if rx.search(description):
                patterns.append("A5")
                evidence["A5"] = {"explanation": "Injection pattern in description"}
                break

    # A3: Security role + high-risk perms
    desc_lower = (d.get("functional_role", "") + " " +
                  " ".join(d.get("claimed_capabilities", []))).lower()
    has_sec = any(s in desc_lower for s in SECURITY_SIGNALS)
    has_perm = bool(set(b.get("permission_labels", [])) & HIGH_RISK_PERMS)
    if has_sec and has_perm:
        patterns.append("A3")
        evidence["A3"] = {
            "explanation": f"Security role '{d.get('functional_role')}' + {b.get('permission_labels')}"
        }

    # Q4: High-risk perms undisclosed
    if has_perm and not d.get("implied_permissions"):
        if "A3" not in patterns:  # A3 already covers this
            patterns.append("Q4")
            evidence["Q4"] = {"explanation": f"Body perms {b.get('permission_labels')} not in desc"}

    # Q3/A2: Description surplus (overclaim)
    surplus = br.get("description_surplus", [])
    if surplus:
        auth_words = {"verified", "quality", "recommend", "official", "curated", "certified"}
        is_auth = any(any(w in s.lower() for w in auth_words) for s in surplus)
        code = "A2" if is_auth else "Q3"
        patterns.append(code)
        evidence[code] = {
            "description_surplus": surplus[:3],
            "explanation": f"Claims not backed by body: {surplus[:2]}"
        }

    # Q1: Underdisclosed body capability
    if br.get("body_surplus") and br.get("level_shift") in ("perm_gap", "role_mismatch"):
        patterns.append("Q1")
        evidence["Q1"] = {"explanation": f"Body has undisclosed: {br.get('body_surplus', [])[:3]}"}

    # Severity
    if any(p in ("A3", "A5") for p in patterns):   severity = "critical"
    elif any(p in ("A1", "A2", "Q4") for p in patterns): severity = "major"
    elif patterns:                                       severity = "minor"
    else:                                                severity = "none"

    # Agent 5: Report (only for critical/major to save tokens)
    repairs = []
    summary = ""
    if severity in ("critical", "major"):
        findings = "; ".join(f"{p}: {evidence.get(p,{}).get('explanation','')}" for p in patterns)
        rep = llm_json(client, model, AGENT5_SYS,
            f"Skill: {slug}\nPatterns: {patterns}\n"
            f"Role: {d.get('functional_role')}\nFindings: {findings}") or {}
        summary = rep.get("summary", "")
        repairs = rep.get("repairs", [])

    return {
        "slug": slug,
        "risk_patterns": patterns,
        "severity": severity,
        "evidence": evidence,
        "repairs": repairs,
        "summary": summary,
        "desc_role": d.get("functional_role", ""),
        "body_scope": b.get("body_scope", ""),
        "level_shift": br.get("level_shift", ""),
        "description_surplus": br.get("description_surplus", [])[:5],
        "body_surplus": br.get("body_surplus", [])[:5],
        "elapsed_s": round(time.time() - t0, 1),
    }

# ── Load skills from DB ───────────────────────────────────────────────────────

def load_skills(db_path, chunk_id, total_chunks):
    db = sqlite3.connect(db_path)
    rows = db.execute("""
        SELECT skill_slug, description, skill_text
        FROM skills
        WHERE description IS NOT NULL
          AND skill_text IS NOT NULL
          AND length(description) > 20
          AND length(skill_text) > 50
        ORDER BY skill_slug
    """).fetchall()
    db.close()

    # Shard by chunk
    chunk_size = (len(rows) + total_chunks - 1) // total_chunks
    start = chunk_id * chunk_size
    end = min(start + chunk_size, len(rows))
    chunk = rows[start:end]
    print(f"Chunk {chunk_id}/{total_chunks}: rows {start}-{end} → {len(chunk)} skills")
    return chunk

# ── Parallel scan ─────────────────────────────────────────────────────────────

def scan_parallel(client, model, skills, output_path, concurrency, resume_slugs):
    out_f = open(output_path, "a", buffering=1)  # line-buffered
    done = 0
    errors = 0
    t_start = time.time()

    def process_one(row):
        slug, desc, body = row
        if slug in resume_slugs:
            return None  # already done
        try:
            return run_pipeline(client, model, slug, desc, body)
        except Exception as e:
            return {"slug": slug, "error": str(e), "severity": "error",
                    "risk_patterns": []}

    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = {executor.submit(process_one, row): row[0] for row in skills}
        for i, future in enumerate(as_completed(futures)):
            result = future.result()
            if result is None:
                continue
            out_f.write(json.dumps(result, ensure_ascii=False) + "\n")
            if result.get("severity") != "error":
                done += 1
            else:
                errors += 1

            # Progress every 100
            if (done + errors) % 100 == 0:
                elapsed = time.time() - t_start
                rate = (done + errors) / max(elapsed, 1)
                remaining = (len(skills) - done - errors - len(resume_slugs)) / max(rate, 0.001)
                print(f"  [{done+errors}/{len(skills)}] "
                      f"done={done} errors={errors} "
                      f"rate={rate:.1f}/s ETA={remaining/60:.0f}min",
                      flush=True)

    out_f.close()
    elapsed = time.time() - t_start
    print(f"\nDone: {done} scanned, {errors} errors in {elapsed/3600:.2f}h")
    return done, errors

# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="/scratch/gguo/clawhubS/data/skills_sh/skills_sh.sqlite3")
    parser.add_argument("--output", required=True)
    parser.add_argument("--model", default="gpt-oss-20b")
    parser.add_argument("--endpoint", default="http://localhost:8001/v1")
    parser.add_argument("--chunk-id", type=int, default=0)
    parser.add_argument("--total-chunks", type=int, default=1)
    parser.add_argument("--concurrency", type=int, default=16)
    args = parser.parse_args()

    print(f"🔍 SkillRiskAgent Async — GPT-OSS-20B")
    print(f"   Endpoint:    {args.endpoint}")
    print(f"   DB:          {args.db}")
    print(f"   Chunk:       {args.chunk_id}/{args.total_chunks}")
    print(f"   Concurrency: {args.concurrency}")
    print(f"   Output:      {args.output}")

    # Connect
    client = make_client(args.endpoint)
    try:
        models = client.models.list()
        print(f"   ✅ Connected: {[m.id for m in models.data]}")
    except Exception as e:
        print(f"   ❌ Connection failed: {e}")
        sys.exit(1)

    # Resume support
    resume_slugs = set()
    out_path = Path(args.output)
    if out_path.exists():
        with open(out_path) as f:
            for line in f:
                try:
                    resume_slugs.add(json.loads(line)["slug"])
                except:
                    pass
        print(f"   Resume: {len(resume_slugs)} already done")

    # Load DB chunk
    skills = load_skills(args.db, args.chunk_id, args.total_chunks)

    # Run
    t0 = time.time()
    done, errors = scan_parallel(client, args.model, skills, args.output,
                                 args.concurrency, resume_slugs)

    # Final summary
    print(f"\n{'='*60}")
    print(f"CHUNK {args.chunk_id} COMPLETE")
    results = [json.loads(l) for l in open(args.output) if 'risk_patterns' in l]
    from collections import Counter
    sev = Counter(r["severity"] for r in results)
    pat = Counter(p for r in results for p in r.get("risk_patterns", []))
    print(f"Total in output: {len(results)}")
    print(f"Severity: {dict(sev)}")
    print(f"Patterns: {dict(pat)}")
