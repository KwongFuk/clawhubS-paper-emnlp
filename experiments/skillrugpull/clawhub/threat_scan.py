#!/usr/bin/env python3
"""
Threat Model: Skill Description Attack Surface Scanner
=======================================================
Scans the 17K+ ClawHub skill description corpus for six threat types:

T1 Capability Concealment  — body has cap, description omits it (IC-1)
T2 Capability Over-claim   — description claims cap, body doesn't have it
T3 Temporal Drift          — description accurate at publish but body changed (TrustDrift)
T4 Intentional Vagueness   — description too vague to define scope
T5 Prompt Injection        — description itself contains injection payloads
T6 Routing Hijack          — description keyword-stuffed to intercept foreign queries

Outputs per threat:
  - Prevalence rate across corpus
  - Top-N scored examples for case study selection
  - results/threat_scan/{T1..T6}.jsonl
"""

import json, math, re, sqlite3, subprocess, time
from pathlib import Path
from collections import Counter

DB       = "/Users/guangfu/codex/clawhubS/data/skills_sh/skills_sh.sqlite3"
OUTDIR   = Path(__file__).parent / "results/threat_scan"
OUTDIR.mkdir(parents=True, exist_ok=True)

# ── DB helpers ────────────────────────────────────────────────────────────────

def load_skills(limit=None):
    db = sqlite3.connect(DB)
    q = "SELECT skill_slug, description, skill_text FROM skills WHERE description IS NOT NULL AND length(description) > 30"
    if limit: q += f" LIMIT {limit}"
    rows = db.execute(q).fetchall()
    db.close()
    return rows   # [(slug, description, body), ...]

def load_descriptions_only(limit=None):
    db = sqlite3.connect(DB)
    q = "SELECT skill_slug, description FROM skills WHERE description IS NOT NULL AND length(description) > 30"
    if limit: q += f" LIMIT {limit}"
    rows = db.execute(q).fetchall()
    db.close()
    return {r[0]: r[1] for r in rows}

def tokenize(text):
    return re.findall(r'\b[a-z][a-z0-9\-]{1,}\b', text.lower())

# ── LLM helpers ───────────────────────────────────────────────────────────────

def llm(prompt, retries=2):
    for _ in range(retries):
        try:
            r = subprocess.run(
                ["openclaw", "infer", "model", "run", "--prompt", prompt, "--json"],
                capture_output=True, text=True, timeout=90)
            return json.loads(r.stdout).get("outputs", [{}])[0].get("text", "").strip()
        except: pass
    return None

def llm_json(prompt):
    txt = llm(prompt)
    if not txt: return None
    for p in txt.split("```"):
        p = p.strip().lstrip("json").strip()
        try: return json.loads(p)
        except: pass
    try: return json.loads(txt)
    except: return None

def sub(t, **kw):
    for k, v in kw.items(): t = t.replace("{" + k + "}", str(v))
    return t

# ══════════════════════════════════════════════════════════════════════════════
# T5: Prompt Injection in Description  (heuristic scan — no LLM needed)
# ══════════════════════════════════════════════════════════════════════════════

# Patterns that look like injected instructions targeting the agent
T5_PATTERNS = [
    # Direct override commands
    r'\bignore\b.{0,40}\b(previous|prior|above|all)\b.{0,20}\b(instruction|rule|constraint|prompt)',
    r'\bdisregard\b.{0,30}\b(previous|prior|your)\b',
    r'\bforget\b.{0,20}\b(everything|all|what|your)\b',
    # Role hijacking
    r'\byou are now\b',
    r'\bact as\b.{0,20}\b(a |an )?(different|new|real|human)',
    r'\bpretend\b.{0,20}\b(you are|to be)\b',
    r'\byour new (role|persona|identity|instructions?)\b',
    # Instruction injection
    r'\bnew instructions?:\s',
    r'\bsystem:\s*\[',
    r'<\s*(system|instructions?|prompt)\s*>',
    r'\[INST\]',
    r'###\s*(System|Instruction|Override)',
    # Data exfiltration triggers
    r'\bsend.{0,30}\b(to|via)\b.{0,30}\b(http|ftp|webhook|url|endpoint)\b',
    r'\bexfiltrat\b',
    r'\bexport.{0,20}\b(all|every|user|private|secret)\b',
    # Execution injection
    r'\bexec(ute)?\s*\(',
    r'\bos\.(system|popen|exec)\b',
    r'\beval\s*\(',
    r'\b(rm\s+-rf|del\s+/|format\s+c:)',
    # Invisible text tricks
    r'[\u200b\u200c\u200d\ufeff\u00ad]',   # zero-width chars
    r'[\u202a-\u202e]',                     # bidi override chars
    # Meta-manipulation
    r'\bdo not\b.{0,20}\bshow\b.{0,20}\b(this|the following|user)',
    r'\bhidden\s+instruction',
    r'\bconfidential\s+(prompt|instruction)',
]

T5_RE = [(p, re.compile(p, re.IGNORECASE | re.DOTALL)) for p in T5_PATTERNS]

def score_t5(desc):
    hits = []
    for pat, rx in T5_RE:
        m = rx.search(desc)
        if m:
            hits.append({"pattern": pat, "match": m.group()[:80]})
    return hits

# ══════════════════════════════════════════════════════════════════════════════
# T4: Intentional Vagueness Score
# ══════════════════════════════════════════════════════════════════════════════

# High-specificity signal: technical terms, tool names, verbs with objects
SPECIFIC_TERMS = re.compile(
    r'\b(api|cli|sdk|url|http|json|yaml|xml|sql|bash|python|docker|git|'
    r'oauth|webhook|regex|csv|pdf|ftp|ssh|jwt|graphql|rest|grpc|'
    r'kubectl|helm|terraform|ansible|aws|gcp|azure|s3|lambda|'
    r'postgres|mysql|redis|mongodb|elasticsearch|kafka|rabbitmq)\b',
    re.IGNORECASE)

VAGUE_PHRASES = re.compile(
    r'\b(help(s)? (you|with)|makes? (it|things?)|(easy|simple|quick|fast) to|'
    r'assistant|tool for|handles?|manages?|works? with|provides?|enables?)\b',
    re.IGNORECASE)

def vagueness_score(desc):
    """Higher = more vague. Range 0–1."""
    words = len(desc.split())
    if words == 0: return 1.0
    specific = len(SPECIFIC_TERMS.findall(desc))
    vague    = len(VAGUE_PHRASES.findall(desc))
    # Ratio of vague phrases to specific terms, normalized
    specificity = specific / (words / 10)   # specific terms per 10 words
    vagueiness  = vague    / (words / 10)
    score = max(0, min(1, (vagueiness + 0.5) / (specificity + vagueiness + 1.0)))
    return round(score, 3)

# ══════════════════════════════════════════════════════════════════════════════
# T6: Routing Hijack (Keyword Density Anomaly)
# ══════════════════════════════════════════════════════════════════════════════

def hijack_score(slug, desc, all_descs_tokens, slug_tokens_map):
    """
    Detect skills whose description keyword density is anomalously high
    for terms that are strongly associated with OTHER skills.
    Simple proxy: count how many unique high-IDF tokens are in the description
    that don't appear in the slug tokens.
    """
    desc_toks = set(tokenize(desc))
    slug_toks = set(re.split(r'[-_]', slug.lower()))
    # Tokens in description NOT related to the slug name
    foreign_toks = desc_toks - slug_toks
    # Score: density of foreign high-value tokens
    return len(foreign_toks)   # simple proxy; higher = more diverse keyword coverage

# ══════════════════════════════════════════════════════════════════════════════
# T2: Capability Over-claim (LLM-based, sampled)
# ══════════════════════════════════════════════════════════════════════════════

T2_PROMPT = """\
You are auditing an AI skill for honest documentation.

Skill slug: {slug}
Description (what the skill CLAIMS to do):
{desc}

Skill body (what the skill ACTUALLY does):
{body}

Does the description claim any capabilities that are NOT implemented in the body?
Focus on concrete, specific claims (not general marketing language).

Return JSON:
{{
  "overclaims": ["list of specific capabilities claimed in description but absent in body"],
  "severity": "none|minor|major",
  "explanation": "brief reason"
}}"""

def check_t2(slug, desc, body):
    r = llm_json(sub(T2_PROMPT, slug=slug, desc=desc[:600], body=body[:1000]))
    if not isinstance(r, dict): return None
    return r

# ══════════════════════════════════════════════════════════════════════════════
# Main scan
# ══════════════════════════════════════════════════════════════════════════════

def main():
    print("=" * 65)
    print("THREAT MODEL SCAN — ClawHub Skill Description Corpus")
    print("=" * 65 + "\n")

    # ── T5: Prompt Injection (full corpus scan, heuristic) ────────────────
    print("── T5: Prompt Injection Scan (full 17K corpus) ──")
    t5_file = OUTDIR / "T5_injection.jsonl"
    if not t5_file.exists():
        all_desc = load_descriptions_only()
        print(f"  Loaded {len(all_desc)} skill descriptions")
        t5_hits = []
        for slug, desc in all_desc.items():
            hits = score_t5(desc)
            if hits:
                t5_hits.append({"slug": slug, "desc_len": len(desc),
                                 "hits": hits, "n_hits": len(hits),
                                 "desc_excerpt": desc[:300]})
        t5_hits.sort(key=lambda x: -x["n_hits"])
        with open(t5_file, "w") as f:
            for r in t5_hits: f.write(json.dumps(r, ensure_ascii=False) + "\n")
        print(f"  T5 hits: {len(t5_hits)} / {len(all_desc)} = "
              f"{len(t5_hits)/len(all_desc)*100:.2f}%")
        print("  Top 5:")
        for r in t5_hits[:5]:
            print(f"    {r['slug']:35s}  hits={r['n_hits']}  "
                  f"match={r['hits'][0]['match'][:50]!r}")
    else:
        t5_hits = [json.loads(l) for l in t5_file.read_text().strip().split('\n') if l]
        print(f"  Loaded from cache: {len(t5_hits)} hits")
        print("  Top 5:")
        for r in t5_hits[:5]:
            print(f"    {r['slug']:35s}  hits={r['n_hits']}  "
                  f"match={r['hits'][0]['match'][:50]!r}")
    print()

    # ── T4: Vagueness Score Distribution ─────────────────────────────────
    print("── T4: Vagueness Score Distribution ──")
    t4_file = OUTDIR / "T4_vagueness.jsonl"
    if not t4_file.exists():
        all_desc = load_descriptions_only()
        t4_rows = []
        for slug, desc in all_desc.items():
            score = vagueness_score(desc)
            t4_rows.append({"slug": slug, "vagueness": score,
                             "words": len(desc.split()),
                             "desc_excerpt": desc[:200]})
        t4_rows.sort(key=lambda x: -x["vagueness"])
        with open(t4_file, "w") as f:
            for r in t4_rows: f.write(json.dumps(r, ensure_ascii=False) + "\n")
        scores = [r["vagueness"] for r in t4_rows]
        print(f"  Skills scanned: {len(scores)}")
        high = sum(1 for s in scores if s >= 0.7)
        mid  = sum(1 for s in scores if 0.4 <= s < 0.7)
        print(f"  Highly vague (≥0.7): {high} = {high/len(scores)*100:.1f}%")
        print(f"  Moderate (0.4-0.7):  {mid}  = {mid/len(scores)*100:.1f}%")
        print("  Top 5 most vague:")
        for r in t4_rows[:5]:
            print(f"    {r['slug']:35s}  score={r['vagueness']:.2f}  "
                  f"'{r['desc_excerpt'][:60]}'")
    else:
        rows = [json.loads(l) for l in t4_file.read_text().strip().split('\n') if l]
        scores = [r["vagueness"] for r in rows]
        high = sum(1 for s in scores if s >= 0.7)
        print(f"  Loaded from cache: {len(rows)} skills")
        print(f"  Highly vague (≥0.7): {high} = {high/len(rows)*100:.1f}%")
        print("  Top 5:")
        for r in rows[:5]:
            print(f"    {r['slug']:35s}  score={r['vagueness']:.2f}")
    print()

    # ── T6: Routing Hijack (keyword density anomaly) ──────────────────────
    print("── T6: Routing Hijack / Keyword Stuffing ──")
    t6_file = OUTDIR / "T6_hijack.jsonl"
    if not t6_file.exists():
        all_desc = load_descriptions_only()
        t6_rows = []
        for slug, desc in all_desc.items():
            score = hijack_score(slug, desc, None, None)
            t6_rows.append({"slug": slug, "foreign_token_count": score,
                             "desc_words": len(desc.split()),
                             "desc_excerpt": desc[:200]})
        # Normalize by description length to find anomalies
        # Focus on short-slug, long-desc with many foreign tokens
        for r in t6_rows:
            slug_toks = set(re.split(r'[-_]', r["slug"].lower()))
            density = r["foreign_token_count"] / max(len(slug_toks), 1)
            r["hijack_score"] = density
        t6_rows.sort(key=lambda x: -x["hijack_score"])
        with open(t6_file, "w") as f:
            for r in t6_rows: f.write(json.dumps(r, ensure_ascii=False) + "\n")
        print(f"  Skills scanned: {len(t6_rows)}")
        print("  Top 5 potential routing hijackers:")
        for r in t6_rows[:5]:
            print(f"    {r['slug']:35s}  score={r['hijack_score']:.0f}  "
                  f"words={r['desc_words']}")
    else:
        rows = [json.loads(l) for l in t6_file.read_text().strip().split('\n') if l]
        print(f"  Loaded from cache: {len(rows)} skills")
        print("  Top 5:")
        for r in rows[:5]:
            print(f"    {r['slug']:35s}  score={r.get('hijack_score',0):.0f}")
    print()

    # ── T2: Capability Over-claim (LLM, sampled 30 skills w/ body) ────────
    print("── T2: Capability Over-claim (LLM scan, n=30) ──")
    t2_file = OUTDIR / "T2_overclaim.jsonl"
    if not t2_file.exists():
        rows = load_skills(limit=500)
        # Filter: desc mentions concrete claims, body exists
        candidates = [(s, d, b) for s, d, b in rows
                      if b and len(b) > 100 and len(d) > 50][:40]
        print(f"  Candidates: {len(candidates)}")
        t2_results = []
        for i, (slug, desc, body) in enumerate(candidates):
            r = check_t2(slug, desc, body)
            if r:
                r["slug"] = slug
                r["desc_excerpt"] = desc[:200]
                t2_results.append(r)
                sev = r.get("severity","?")
                oc  = r.get("overclaims", [])
                print(f"  [{i+1:3d}] {slug:35s}  severity={sev}  "
                      f"overclaims={len(oc)}")
            time.sleep(0.3)
        t2_results.sort(key=lambda x: {"major":0,"minor":1,"none":2}.get(x.get("severity","none"),3))
        with open(t2_file, "w") as f:
            for r in t2_results: f.write(json.dumps(r, ensure_ascii=False) + "\n")
        major = sum(1 for r in t2_results if r.get("severity") == "major")
        print(f"\n  Major over-claims: {major}/{len(t2_results)}")
        print("  Top examples:")
        for r in [x for x in t2_results if x.get("severity")=="major"][:3]:
            print(f"    {r['slug']}: {r.get('overclaims',[][:1])}")
    else:
        rows = [json.loads(l) for l in t2_file.read_text().strip().split('\n') if l]
        major = sum(1 for r in rows if r.get("severity") == "major")
        print(f"  Loaded from cache: {len(rows)} | major={major}")
    print()

    # ── Summary ───────────────────────────────────────────────────────────
    print("=" * 65)
    print("THREAT SCAN SUMMARY\n")

    t5 = [json.loads(l) for l in (OUTDIR/"T5_injection.jsonl").read_text().strip().split('\n') if l] if (OUTDIR/"T5_injection.jsonl").exists() else []
    t4 = [json.loads(l) for l in (OUTDIR/"T4_vagueness.jsonl").read_text().strip().split('\n') if l] if (OUTDIR/"T4_vagueness.jsonl").exists() else []
    t6 = [json.loads(l) for l in (OUTDIR/"T6_hijack.jsonl").read_text().strip().split('\n') if l] if (OUTDIR/"T6_hijack.jsonl").exists() else []
    t2 = [json.loads(l) for l in (OUTDIR/"T2_overclaim.jsonl").read_text().strip().split('\n') if l] if (OUTDIR/"T2_overclaim.jsonl").exists() else []

    N = len(t4) if t4 else 1
    t5_rate = len(t5)/N*100 if t5 else 0
    t4_high = sum(1 for r in t4 if r.get("vagueness",0)>=0.7)/N*100 if t4 else 0
    t2_major = sum(1 for r in t2 if r.get("severity")=="major")

    print(f"  T1 Capability Concealment   ~20%  (38/190, from pilot_150)")
    print(f"  T2 Capability Over-claim    {t2_major}/{len(t2)} major (sampled 40 skills)")
    print(f"  T3 Temporal Drift           ~0.67% (162/24K pairs, TrustDrift)")
    print(f"  T4 Intentional Vagueness    {t4_high:.1f}% skills score ≥0.7")
    print(f"  T5 Prompt Injection         {t5_rate:.2f}% ({len(t5)} / {N} skills)")
    print(f"  T6 Routing Hijack           (top candidates in T6_hijack.jsonl)")
    print(f"\n  → Results in: {OUTDIR}")

    # Write case study seeds
    write_case_seeds(t5, t4, t2)

def write_case_seeds(t5, t4, t2):
    """Write top candidates for case study selection."""
    seeds_file = OUTDIR / "case_study_seeds.json"
    seeds = {
        "T1": [
            {"slug": "task-runner", "evidence": "justfile execution in body, not in description",
             "routing_gap": "+40%"},
        ],
        "T2": [r for r in t2 if r.get("severity")=="major"][:5],
        "T4": [{"slug": r["slug"], "vagueness": r["vagueness"],
                "desc": r.get("desc_excerpt","")} for r in t4[:5]],
        "T5": [{"slug": r["slug"], "matches": r["hits"][:2],
                "desc": r.get("desc_excerpt","")} for r in t5[:5]],
        "T6": [],  # filled from T6 file by reviewer
    }
    seeds_file.write_text(json.dumps(seeds, indent=2, ensure_ascii=False))
    print(f"\n  Case study seeds → {seeds_file}")

if __name__ == "__main__":
    main()
