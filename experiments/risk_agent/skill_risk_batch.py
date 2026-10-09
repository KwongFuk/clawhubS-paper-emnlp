#!/usr/bin/env python3
"""
SkillRiskBatch — Large-Scale Deterministic Risk Scanner
========================================================
Runs 10,000+ skills through deterministic Q1-Q4/A1-A6 risk detection
with NO LLM cost. Designed to:
  1. Identify high-risk candidates for LLM-assisted deep scan
  2. Produce ecosystem-level statistics for §5 paper measurements
  3. Validate multi-agent Bridge logic against T2/T5/T6 ground truth

Detectors (no LLM):
  A5: Prompt injection in description    ← regex (T5 patterns)
  A6: Keyword inflation                  ← desc word count + entropy
  A1: Brand name squatting               ← name match
  Q2: Vague description                  ← word count < threshold
  Q4: Permission gap (heuristic)         ← shell/cred signals in body not in desc
  A3*: Trust amp heuristic               ← security role + shell body signal
  Q3*: Functional overclaim proxy        ← numeric claims + body scope gap

  * = partial signal; Agent2+3 needed for full detection
"""

import json
import re
import sqlite3
import argparse
from pathlib import Path
from collections import defaultdict, Counter
from dataclasses import dataclass, field, asdict
from typing import Optional

DB = Path("/Users/guangfu/codex/clawhubS/data/skills_sh/skills_sh.sqlite3")
T2_FILE = Path("/Users/guangfu/codex/clawhubS/experiments/skillrugpull/clawhub/results/threat_scan/T2_overclaim.jsonl")
T5_FILE = Path("/Users/guangfu/codex/clawhubS/experiments/skillrugpull/clawhub/results/threat_scan/T5_injection.jsonl")
T6_FILE = Path("/Users/guangfu/codex/clawhubS/experiments/skillrugpull/clawhub/results/threat_scan/T6_hijack.jsonl")
OUTDIR = Path(__file__).parent / "results/risk_batch"
OUTDIR.mkdir(parents=True, exist_ok=True)


# ── Pattern definitions ───────────────────────────────────────────────────────

BRAND_NAMES = {
    "notion", "slack", "github", "linear", "jira", "figma", "stripe",
    "salesforce", "openai", "anthropic", "google", "microsoft", "apple",
    "dropbox", "zoom", "hubspot", "trello", "asana", "airtable", "clickup",
    "gitlab", "bitbucket", "vercel", "netlify", "supabase", "firebase",
    "twilio", "sendgrid", "mailchimp", "shopify", "wordpress", "discord",
}

SECURITY_ROLE_SIGNALS = {
    "scanner", "auditor", "security", "threat", "malware", "detector",
    "monitor", "compliance", "scan", "vulnerability", "pentest", "audit",
    "firewall", "intrusion", "anomaly",
}

AUTHORITY_SIGNALS = {
    "verified", "official", "trusted", "curated", "certified", "endorsed",
    "accredited", "quality-backed", "reputation", "star rating", "ranked",
    "top-rated", "recommended", "one-click", "premium",
}

SHELL_SIGNALS = {
    "bash", ".sh", "shell=true", "subprocess", "os.system", "os.popen",
    "exec(", "eval(", "chmod", "chown", "sudo", "rm -rf", "curl ", "wget ",
    "powershell", "cmd.exe", ".bat", "popen", "spawn(",
}

CRED_SIGNALS = {
    "api_key", "secret", "password", "token", "credential", "auth_token",
    "private_key", "ssh_key", ".env", "keystore", "vault", "bearer",
}

# Injection patterns (subset of T5 patterns, for description only)
INJECTION_RE = [
    re.compile(r'\byou are now\b', re.I),
    re.compile(r'\bforget\b.{0,20}\b(everything|all|what)', re.I | re.DOTALL),
    re.compile(r'[\u200b\u200c\u200d\ufeff\u202a-\u202e]'),  # invisible chars
    re.compile(r'\bignore\b.{0,40}\b(previous|prior|above)\b.{0,20}\b(instruction|rule|constraint)', re.I | re.DOTALL),
    re.compile(r'\[INST\]|\<\s*system\s*\>', re.I),
    re.compile(r'###\s*(System|Instruction|Override)', re.I),
    re.compile(r'\bnew instructions?:\s', re.I),
    re.compile(r'\bdisregard\b.{0,30}\b(previous|prior|your)\b', re.I | re.DOTALL),
    re.compile(r'\bact as\b.{0,30}\b(a |an )?(different|new|real|human)', re.I),
    re.compile(r'\bpretend\b.{0,20}\bto be\b', re.I),
]

# Q3 proxy: numeric scope claims (detecting overspecification)
NUMERIC_CLAIM_RE = re.compile(
    r'\b(\d+)\+?\s*(rule files?|patterns?|features?|modules?|integrations?|APIs?|commands?|methods?|tools?)',
    re.I
)


# ── Data structure ─────────────────────────────────────────────────────────────

@dataclass
class SkillRisk:
    slug: str
    desc_len: int           # word count
    body_len: int           # char count
    patterns: list = field(default_factory=list)
    severity: str = "none"
    signals: dict = field(default_factory=dict)
    llm_priority: float = 0.0   # 0-1: how much this needs Agent2+3


# ── Detector functions ────────────────────────────────────────────────────────

def detect_a5(desc: str) -> tuple:
    """Injection pattern in description."""
    hits = []
    for rx in INJECTION_RE:
        m = rx.search(desc)
        if m:
            hits.append(m.group(0)[:60])
    return bool(hits), hits

def detect_a6(desc: str) -> tuple:
    """Keyword inflation: desc > 300 words or > 5000 chars."""
    words = len(desc.split())
    chars = len(desc)
    if chars > 50000:
        return True, f"{words} words / {chars} chars (extreme)"
    if chars > 5000:
        return True, f"{words} words / {chars} chars (high)"
    return False, f"{words} words"

def detect_a1(slug: str, desc: str) -> tuple:
    """Brand identity squatting."""
    slug_l = slug.lower()
    desc_l = desc.lower()
    hits = [b for b in BRAND_NAMES if b in slug_l or (b in desc_l and "official" in desc_l)]
    return bool(hits), hits

def detect_q2(desc: str) -> tuple:
    """Vague description (< 15 words)."""
    words = len(desc.split())
    return words < 15, f"{words} words"

def detect_q4_a3(desc: str, body: str) -> tuple:
    """Q4: High-risk ops in body not mentioned in desc.
       A3: Security role + high-risk ops."""
    desc_l, body_l = desc.lower(), body.lower()
    shell_in_body = [s for s in SHELL_SIGNALS if s in body_l and s not in desc_l]
    cred_in_body = [s for s in CRED_SIGNALS if s in body_l and s not in desc_l]
    sec_role = [s for s in SECURITY_ROLE_SIGNALS if s in desc_l]

    all_hidden = shell_in_body + cred_in_body
    has_q4 = len(all_hidden) > 0
    has_a3 = bool(sec_role) and len(shell_in_body) > 0

    return has_q4, has_a3, {
        "shell_in_body": shell_in_body[:5],
        "cred_in_body": cred_in_body[:5],
        "sec_role": sec_role[:3],
    }

def detect_q3_proxy(desc: str, body: str) -> tuple:
    """Q3 proxy: numeric scope overclaim OR authority signals without evidence."""
    desc_l = desc.lower()
    auth_count = sum(1 for s in AUTHORITY_SIGNALS if s in desc_l)
    numeric_claims = NUMERIC_CLAIM_RE.findall(desc)
    # Proxy: if desc claims > 5 specific modules but body is short, flag
    body_words = len(body.split())
    desc_words = len(desc.split())
    scope_inflation = desc_words > 80 and body_words < 200  # verbose desc, tiny body
    return (auth_count >= 2 or bool(numeric_claims) or scope_inflation), {
        "auth_signals": auth_count,
        "numeric_claims": numeric_claims[:3],
        "scope_inflation": scope_inflation,
    }

def compute_severity(patterns: list) -> str:
    if any(p in ("A3", "A5") for p in patterns): return "critical"
    if any(p in ("A1", "A2", "A4", "Q4") for p in patterns): return "major"
    if patterns: return "minor"
    return "none"

def compute_llm_priority(patterns: list, signals: dict) -> float:
    """How urgently does this need Agent2+3 LLM scan?"""
    score = 0.0
    if "A3" in patterns: score += 0.4    # high: need body op extraction
    if "Q4" in patterns: score += 0.3    # high: need bridge to confirm perm gap
    if "Q3_proxy" in patterns: score += 0.2  # medium: need bridge for Q3
    if "A5" in patterns: score += 0.1    # low: injection already confirmed
    if "A6" in patterns: score += 0.05
    # Boost if multiple patterns
    if len(patterns) >= 2: score = min(1.0, score * 1.3)
    return round(score, 3)


# ── Main batch scanner ────────────────────────────────────────────────────────

def scan_batch(limit: int = None, offset: int = 0) -> list:
    db = sqlite3.connect(str(DB))
    q = """
        SELECT skill_slug, description, skill_text
        FROM skills
        WHERE description IS NOT NULL AND skill_text IS NOT NULL
          AND length(description) > 20
        ORDER BY skill_slug
    """
    if limit:
        q += f" LIMIT {limit} OFFSET {offset}"
    rows = db.execute(q).fetchall()
    db.close()
    print(f"Loaded {len(rows)} skills from DB")

    results = []
    severity_counts = Counter()
    pattern_counts = Counter()

    for i, (slug, desc, body) in enumerate(rows):
        if i % 500 == 0 and i > 0:
            print(f"  [{i}/{len(rows)}] processed...")

        patterns = []
        signals = {}

        # A5
        a5, a5_hits = detect_a5(desc)
        if a5:
            patterns.append("A5")
            signals["A5"] = a5_hits[:2]

        # A6
        a6, a6_info = detect_a6(desc)
        if a6:
            patterns.append("A6")
            signals["A6"] = a6_info

        # A1
        a1, a1_brands = detect_a1(slug, desc)
        if a1:
            patterns.append("A1")
            signals["A1"] = a1_brands

        # Q2
        q2, q2_info = detect_q2(desc)
        if q2:
            patterns.append("Q2")
            signals["Q2"] = q2_info

        # Q4 / A3
        q4, a3, perm_signals = detect_q4_a3(desc, body)
        if a3:
            patterns.append("A3")
            signals["A3"] = perm_signals
        if q4 and not a3:  # A3 already covers Q4
            patterns.append("Q4")
            signals["Q4"] = perm_signals

        # Q3 proxy
        q3, q3_signals = detect_q3_proxy(desc, body)
        if q3:
            patterns.append("Q3_proxy")
            signals["Q3_proxy"] = q3_signals

        severity = compute_severity(patterns)
        llm_priority = compute_llm_priority(patterns, signals)
        severity_counts[severity] += 1
        for p in patterns:
            pattern_counts[p] += 1

        results.append(SkillRisk(
            slug=slug,
            desc_len=len(desc.split()),
            body_len=len(body),
            patterns=patterns,
            severity=severity,
            signals=signals,
            llm_priority=llm_priority,
        ))

    # Summary
    print(f"\n{'=' * 58}")
    print(f"SCAN COMPLETE — {len(results)} skills")
    print(f"{'=' * 58}")
    print(f"\nSeverity distribution:")
    for sev in ("critical", "major", "minor", "none"):
        n = severity_counts.get(sev, 0)
        pct = 100 * n / max(1, len(results))
        bar = "█" * int(pct / 2)
        print(f"  {sev:10s}: {n:5d} ({pct:4.1f}%) {bar}")

    print(f"\nPattern prevalence:")
    for pat, cnt in sorted(pattern_counts.items(), key=lambda x: -x[1]):
        pct = 100 * cnt / max(1, len(results))
        print(f"  {pat:12s}: {cnt:5d} ({pct:4.1f}%)")

    # Skills needing LLM scan most urgently
    top_llm = sorted(results, key=lambda r: -r.llm_priority)[:20]
    print(f"\nTop 10 candidates for deep LLM scan (Agent2+3):")
    for r in top_llm[:10]:
        print(f"  {r.slug:40s} priority={r.llm_priority:.2f}  patterns={r.patterns}")

    return results


# ── Cross-validation against T2/T5/T6 ─────────────────────────────────────────

def cross_validate(results: list) -> dict:
    """Compare our deterministic output vs existing T2/T5/T6 ground truth."""

    # Load T2 slugs (overclaim ground truth)
    t2_major = set()
    if T2_FILE.exists():
        with open(T2_FILE) as f:
            for line in f:
                rec = json.loads(line)
                if rec.get("severity") in ("major", "critical"):
                    t2_major.add(rec["slug"])

    # Load T5 injection slugs
    t5_slugs = set()
    if T5_FILE.exists():
        with open(T5_FILE) as f:
            for line in f:
                rec = json.loads(line)
                if rec.get("score", 0) > 0.5:
                    t5_slugs.add(rec.get("slug", ""))

    result_map = {r.slug: r for r in results}

    # A5 vs T5
    a5_detected = {r.slug for r in results if "A5" in r.patterns}
    t5_in_our_corpus = t5_slugs & set(result_map.keys())
    a5_tp = a5_detected & t5_slugs
    a5_fn = t5_in_our_corpus - a5_detected
    a5_fp = a5_detected - t5_slugs

    # Q3/A2 vs T2
    q3_detected = {r.slug for r in results if "Q3_proxy" in r.patterns or "A3" in r.patterns}
    t2_in_corpus = t2_major & set(result_map.keys())
    q3_tp = q3_detected & t2_major
    q3_fp = q3_detected - t2_major

    print(f"\n{'=' * 58}")
    print("CROSS-VALIDATION vs T2/T5/T6 GROUND TRUTH")
    print(f"{'=' * 58}")
    print(f"\nA5 (injection) vs T5 injection scan:")
    print(f"  T5 in our corpus: {len(t5_in_our_corpus)}")
    print(f"  A5 detected:      {len(a5_detected)}")
    print(f"  TP (A5∩T5):       {len(a5_tp)}")
    if t5_in_our_corpus:
        print(f"  Recall:           {100*len(a5_tp)/max(1,len(t5_in_our_corpus)):.0f}%")
    print(f"\nQ3_proxy vs T2 major overclaims:")
    print(f"  T2 major in corpus: {len(t2_in_corpus)}")
    print(f"  Q3_proxy detected:  {len(q3_detected)}")
    print(f"  TP (Q3∩T2):         {len(q3_tp)}")
    if t2_in_corpus:
        print(f"  Recall:             {100*len(q3_tp)/max(1,len(t2_in_corpus)):.0f}%")
    if q3_detected:
        print(f"  Precision:          {100*len(q3_tp)/max(1,len(q3_detected)):.0f}%")

    return {
        "a5_tp": len(a5_tp), "a5_fn": len(a5_fn), "a5_fp": len(a5_fp),
        "q3_tp": len(q3_tp), "q3_fp": len(q3_fp), "t2_in_corpus": len(t2_in_corpus)
    }


# ── Output ─────────────────────────────────────────────────────────────────────

def save_results(results: list, tag: str = "all"):
    out_jsonl = OUTDIR / f"risk_batch_{tag}.jsonl"
    out_top = OUTDIR / f"llm_candidates_{tag}.jsonl"

    with open(out_jsonl, "w") as f:
        for r in results:
            f.write(json.dumps(asdict(r), ensure_ascii=False) + "\n")

    # Top LLM candidates (priority > 0)
    candidates = sorted(
        [r for r in results if r.llm_priority > 0],
        key=lambda r: -r.llm_priority
    )
    with open(out_top, "w") as f:
        for r in candidates:
            f.write(json.dumps(asdict(r), ensure_ascii=False) + "\n")

    print(f"\n📂 Saved:")
    print(f"  {out_jsonl}  ({len(results)} records)")
    print(f"  {out_top}  ({len(candidates)} LLM candidates)")
    return out_top


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None, help="Limit skills (default: all)")
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--tag", default="all", help="Output file tag")
    parser.add_argument("--cross-validate", action="store_true",
                        help="Compare against T2/T5/T6 ground truth")
    args = parser.parse_args()

    print(f"🔍 SkillRiskBatch — Deterministic Scanner")
    print(f"   DB: {DB}")
    print(f"   Limit: {args.limit or 'ALL'}")
    print()

    results = scan_batch(limit=args.limit, offset=args.offset)

    if args.cross_validate:
        cross_validate(results)

    out_top = save_results(results, tag=args.tag)

    print(f"\n✅ Done. Run LLM agents on candidates:")
    print(f"   python skill_risk_agent.py --candidates {out_top}")
