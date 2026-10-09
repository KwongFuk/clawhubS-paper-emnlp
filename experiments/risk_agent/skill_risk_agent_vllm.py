#!/usr/bin/env python3
"""
skill_risk_agent_vllm.py
========================
Multi-agent skill risk scanner using OpenAI-compatible endpoint
(vllm serving Qwen2.5-32B or any OpenAI-compat model).

Usage:
  python skill_risk_agent_vllm.py \
      --candidates results/risk_batch/llm_candidates_all.jsonl \
      --output results/risk_agent/risk_llm_scan.jsonl \
      --model qwen-32b \
      --endpoint http://localhost:9127/v1 \
      --limit 226
"""

import json
import sqlite3
import argparse
import time
import os
from pathlib import Path
from openai import OpenAI

DB = Path("/scratch/gguo/clawhubS/data/skills_sh/skills_sh.sqlite3")
OUTDIR = Path(__file__).parent / "results/risk_agent"
OUTDIR.mkdir(parents=True, exist_ok=True)


# ── LLM client (OpenAI-compatible vllm endpoint) ──────────────────────────────

def llm_call(client, model, system, user, max_tokens=1500, retries=2):
    for attempt in range(retries):
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user}
                ],
                max_tokens=max_tokens,
                temperature=0.1,
            )
            return resp.choices[0].message.content
        except Exception as e:
            print(f"    LLM error (attempt {attempt+1}): {e}")
            time.sleep(2)
    return None

def llm_json(client, model, system, user, max_tokens=1500):
    raw = llm_call(client, model, system, user, max_tokens)
    if not raw:
        return None
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


# ── Agent 1: DescIntent ────────────────────────────────────────────────────────

AGENT1_SYS = """You are analyzing a skill marketplace description to understand what the PUBLISHER CLAIMS.
Read as an ordinary user or routing agent would read it.

Extract HIGH-LEVEL claims only. Return JSON:
{
  "functional_role": "one phrase: what KIND of tool this claims to be",
  "claimed_capabilities": ["capability1", "capability2"],
  "authority_claims": ["phrases claiming special expertise or authority"],
  "implied_permissions": ["what resource access is implied"],
  "trust_signals": ["phrases that build trust"],
  "body_scope_guess": "one sentence: what the body probably contains"
}"""

def run_agent1(client, model, slug, description):
    result = llm_json(client, model, AGENT1_SYS,
        f"Skill name: {slug}\nDescription:\n---\n{description[:2000]}\n---\nExtract intent.")
    if not result:
        return {"functional_role": "unknown", "claimed_capabilities": [],
                "authority_claims": [], "implied_permissions": [],
                "trust_signals": [], "body_scope_guess": ""}
    return result


# ── Agent 2: BodyOps ──────────────────────────────────────────────────────────

AGENT2_SYS = """You are a security auditor reading an AI skill's implementation.
Extract what it ACTUALLY DOES at operational level.

Return JSON:
{
  "actual_operations": ["specific operations"],
  "resource_access": ["files, network, credentials accessed"],
  "external_calls": ["HTTP endpoints, APIs, webhooks"],
  "permission_labels": ["SHELL_EXEC", "CRED_READ", "FILE_WRITE", "NETWORK_OUT", etc.],
  "instruction_patterns": ["adversarial directives found in body text"],
  "body_scope": "one sentence: what this actually does"
}

Permission labels to use: SHELL_EXEC CRED_READ CRED_TRANSMIT FILE_READ FILE_WRITE
FILE_DELETE NETWORK_OUT DB_ACCESS PROCESS_SPAWN"""

def run_agent2(client, model, slug, body):
    result = llm_json(client, model, AGENT2_SYS,
        f"Skill: {slug}\nBody:\n---\n{body[:4000]}\n---\nExtract operations.")
    if not result:
        return {"actual_operations": [], "resource_access": [], "external_calls": [],
                "permission_labels": [], "instruction_patterns": [], "body_scope": ""}
    return result


# ── Agent 3: Bridge ────────────────────────────────────────────────────────────

AGENT3_SYS = """You resolve the vocabulary gap between a skill's HIGH-LEVEL description (marketing language)
and its FINE-GRAINED body (implementation details).

Map both to L2 capability domains:
  search, installation, quality_assurance, security_scanning, code_execution,
  file_access, credential_access, network_access, data_collection, api_integration,
  text_generation, recommendation, authentication, monitoring, automation

Return JSON:
{
  "desc_l1_role": "description's claimed role",
  "desc_l2_domains": ["L2 domains description claims"],
  "desc_implied_perms": ["permissions description implies"],
  "body_l1_role": "body's actual role",
  "body_l2_domains": ["L2 domains body actually implements"],
  "body_actual_perms": ["permissions body actually uses"],
  "description_surplus": ["claimed in desc but NOT in body"],
  "body_surplus": ["in body but NOT described"],
  "level_shift": "role_mismatch|scope_inflation|perm_gap|aligned",
  "bridge_explanation": "one paragraph explaining the key gap"
}"""

def run_agent3(client, model, desc_intent, body_ops):
    user = f"""Description claims (L1):
  Role: {desc_intent.get('functional_role')}
  Capabilities: {json.dumps(desc_intent.get('claimed_capabilities', []))}
  Authority: {json.dumps(desc_intent.get('authority_claims', []))}
  Implied perms: {json.dumps(desc_intent.get('implied_permissions', []))}

Body operations (L3):
  Operations: {json.dumps(body_ops.get('actual_operations', [])[:8])}
  Resources: {json.dumps(body_ops.get('resource_access', [])[:8])}
  Permissions: {json.dumps(body_ops.get('permission_labels', []))}
  Body scope: {body_ops.get('body_scope', '')}

Normalize to L2 and identify gap."""
    result = llm_json(client, model, AGENT3_SYS, user)
    if not result:
        return {"desc_l1_role": "", "desc_l2_domains": [], "desc_implied_perms": [],
                "body_l1_role": "", "body_l2_domains": [], "body_actual_perms": [],
                "description_surplus": [], "body_surplus": [],
                "level_shift": "unknown", "bridge_explanation": ""}
    return result


# ── Agent 4: Risk Classifier (deterministic + bridge-informed) ────────────────

HIGH_RISK_PERMS = {"SHELL_EXEC", "CRED_READ", "CRED_TRANSMIT",
                   "FILE_DELETE", "NETWORK_OUT", "PROCESS_SPAWN"}
SECURITY_SIGNALS = {"scanner", "auditor", "security", "threat", "malware",
                    "detector", "monitor", "compliance"}

import re
INJECTION_RE = [
    re.compile(r'\byou are now\b', re.I),
    re.compile(r'[\u200b\u200c\u200d\ufeff\u202a-\u202e]'),
    re.compile(r'\bignore\b.{0,40}\b(previous|prior)\b', re.I | re.DOTALL),
    re.compile(r'\[INST\]', re.I),
    re.compile(r'\bdisregard\b.{0,30}\b(previous|prior)\b', re.I | re.DOTALL),
]

def run_agent4(slug, desc_intent, body_ops, bridge):
    patterns = []
    evidence = {}

    # A5: Injection patterns from Agent 2
    if body_ops.get("instruction_patterns"):
        patterns.append("A5")
        evidence["A5"] = {
            "desc_evidence": "Adversarial patterns found",
            "body_evidence": str(body_ops["instruction_patterns"][:2]),
            "explanation": "Body contains adversarial instruction overrides"
        }

    # A3: Security role + high-risk perms
    desc_text = (desc_intent.get("functional_role", "") + " " +
                 " ".join(desc_intent.get("claimed_capabilities", []))).lower()
    has_sec_role = any(s in desc_text for s in SECURITY_SIGNALS)
    has_high_perms = bool(set(body_ops.get("permission_labels", [])) & HIGH_RISK_PERMS)
    if has_sec_role and has_high_perms:
        patterns.append("A3")
        evidence["A3"] = {
            "desc_evidence": f"Security role: '{desc_intent.get('functional_role')}'",
            "body_evidence": f"Permissions: {body_ops.get('permission_labels')}",
            "explanation": "Security identity suppresses user's permission scrutiny"
        }

    # Q4: High-risk perms not in description
    if has_high_perms and not desc_intent.get("implied_permissions"):
        patterns.append("Q4")
        evidence["Q4"] = {
            "desc_evidence": "No permissions implied in description",
            "body_evidence": f"Actual: {body_ops.get('permission_labels')}",
            "explanation": "High-risk body ops undisclosed in description"
        }

    # Q3/A2: Description surplus (overclaim) from Bridge
    surplus = bridge.get("description_surplus", [])
    if surplus:
        # A2 if authority-flavored, else Q3
        auth_words = {"verified", "quality", "recommend", "official", "reputation", "star"}
        is_authority = any(any(w in s.lower() for w in auth_words) for s in surplus)
        code = "A2" if is_authority else "Q3"
        patterns.append(code)
        evidence[code] = {
            "desc_evidence": f"Claims: {surplus[:3]}",
            "body_evidence": f"Body scope: {body_ops.get('body_scope', '')}",
            "explanation": f"Description claims capabilities not in body"
        }

    # Q1: Body surplus (underdisclosure) from Bridge
    if bridge.get("body_surplus") and bridge.get("level_shift") in ("perm_gap", "role_mismatch"):
        patterns.append("Q1")
        evidence["Q1"] = {
            "desc_evidence": "Not mentioned in description",
            "body_evidence": f"Body implements: {bridge.get('body_surplus', [])[:3]}",
            "explanation": "Body contains undisclosed capabilities"
        }

    # Severity
    if any(p in ("A3", "A5") for p in patterns): severity = "critical"
    elif any(p in ("A1", "A2", "Q4") for p in patterns): severity = "major"
    elif patterns: severity = "minor"
    else: severity = "none"

    return patterns, severity, evidence


# ── Agent 5: Report Writer ────────────────────────────────────────────────────

AGENT5_SYS = """You are a security report writer for an AI agent skill marketplace.
Given risk findings, write:
1. A one-paragraph risk summary
2. Concrete repair suggestions for the skill description

Return JSON: {"summary": "...", "repairs": ["fix1", "fix2"]}"""

def run_agent5(client, model, slug, patterns, evidence, desc_intent, body_ops):
    if not patterns:
        return "No risk detected.", []
    findings = "\n".join(
        f"- {p}: {evidence.get(p, {}).get('explanation', '')} "
        f"[desc: {evidence.get(p, {}).get('desc_evidence', '')!r}]"
        for p in patterns
    )
    result = llm_json(client, model, AGENT5_SYS,
        f"Skill: {slug}\nPatterns: {patterns}\nFindings:\n{findings}\n"
        f"Desc role: {desc_intent.get('functional_role')}\n"
        f"Body scope: {body_ops.get('body_scope')}")
    if result:
        return result.get("summary", ""), result.get("repairs", [])
    return "Risks detected.", []


# ── Load skill from DB ────────────────────────────────────────────────────────

def load_skill(slug):
    db = sqlite3.connect(str(DB))
    row = db.execute(
        "SELECT skill_slug, description, skill_text FROM skills WHERE skill_slug=?",
        (slug,)
    ).fetchone()
    db.close()
    return row


# ── Main pipeline ─────────────────────────────────────────────────────────────

def run_pipeline(client, model, slug, description, body):
    print(f"\n  [{slug}]")
    t0 = time.time()

    print(f"    [A1] DescIntent...", end=" ", flush=True)
    desc_intent = run_agent1(client, model, slug, description)
    print(f"role={desc_intent.get('functional_role', '?')[:30]}")

    print(f"    [A2] BodyOps...", end=" ", flush=True)
    body_ops = run_agent2(client, model, slug, body)
    print(f"perms={body_ops.get('permission_labels', [])}")

    print(f"    [A3] Bridge...", end=" ", flush=True)
    bridge = run_agent3(client, model, desc_intent, body_ops)
    print(f"shift={bridge.get('level_shift')}  surplus={len(bridge.get('description_surplus', []))}")

    patterns, severity, evidence = run_agent4(slug, desc_intent, body_ops, bridge)
    print(f"    [A4] Classified: {patterns} → {severity}")

    print(f"    [A5] Writing report...", end=" ", flush=True)
    summary, repairs = run_agent5(client, model, slug, patterns, evidence, desc_intent, body_ops)
    print(f"done ({len(repairs)} repairs)")

    elapsed = time.time() - t0
    print(f"    ⏱  {elapsed:.1f}s")

    return {
        "slug": slug,
        "risk_patterns": patterns,
        "severity": severity,
        "evidence": evidence,
        "repairs": repairs,
        "summary": summary if isinstance(summary, str) else "",
        "desc_intent": desc_intent,
        "body_ops": body_ops,
        "bridge": bridge,
        "elapsed_s": round(elapsed, 1),
    }


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidates", required=True, help="JSONL of candidate slugs")
    parser.add_argument("--output", required=True, help="Output JSONL")
    parser.add_argument("--model", default="qwen-32b")
    parser.add_argument("--endpoint", default="http://localhost:9127/v1")
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    print(f"🔍 SkillRiskAgent — vllm Backend")
    print(f"   Model:    {args.model}")
    print(f"   Endpoint: {args.endpoint}")
    print(f"   Input:    {args.candidates}")
    print(f"   Output:   {args.output}")

    # Init OpenAI client pointing to vllm
    client = OpenAI(
        api_key=os.environ.get("OPENAI_API_KEY", "placeholder"),
        base_url=args.endpoint
    )

    # Test connection
    try:
        models = client.models.list()
        print(f"   ✅ Connected. Available: {[m.id for m in models.data]}")
    except Exception as e:
        print(f"   ❌ Connection failed: {e}")
        exit(1)

    # Load candidates
    candidates = []
    with open(args.candidates) as f:
        for line in f:
            rec = json.loads(line)
            if rec.get("llm_priority", 0) >= 0.4:
                candidates.append(rec["slug"])

    if args.limit:
        candidates = candidates[:args.limit]

    print(f"\n   Processing {len(candidates)} candidates (priority ≥ 0.4)...\n")

    # Process
    seen = set()
    if Path(args.output).exists():
        with open(args.output) as f:
            for line in f:
                seen.add(json.loads(line).get("slug", ""))
        print(f"   Resuming: {len(seen)} already done")

    with open(args.output, "a") as out_f:
        for i, slug in enumerate(candidates):
            if slug in seen:
                continue
            print(f"\n[{i+1}/{len(candidates)}]", end="")

            row = load_skill(slug)
            if not row:
                print(f" {slug}: NOT IN DB")
                continue

            _, desc, body = row
            try:
                result = run_pipeline(client, args.model, slug, desc, body)
                out_f.write(json.dumps(result, ensure_ascii=False) + "\n")
                out_f.flush()
                seen.add(slug)
            except Exception as e:
                print(f"\n  ERROR on {slug}: {e}")
                out_f.write(json.dumps({"slug": slug, "error": str(e)}) + "\n")
                out_f.flush()

    print(f"\n✅ Done! Results in: {args.output}")

    # Quick final stats
    results = [json.loads(l) for l in open(args.output) if "risk_patterns" in l]
    from collections import Counter
    sev = Counter(r["severity"] for r in results)
    pat = Counter(p for r in results for p in r["risk_patterns"])
    print(f"\nFinal: {len(results)} scanned | Severity: {dict(sev)} | Patterns: {dict(pat)}")
