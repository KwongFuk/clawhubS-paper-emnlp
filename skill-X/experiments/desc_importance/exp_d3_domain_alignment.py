#!/usr/bin/env python3
"""
exp_d3_domain_alignment.py
===========================
CORRECT implementation of D3: Keyword Inflation / Domain Misalignment Detection.

Unlike the broken T6 algorithm (which just counts tokens not in slug name),
this script properly detects keyword inflation by:
  1. Extracting capability domains from description (what desc CLAIMS)
  2. Extracting capability domains from body (what skill ACTUALLY does)
  3. Computing inflation_score = |desc_domains - body_domains| / |desc_domains|

High inflation_score = description claims domains the body doesn't implement.

Run on Palmetto:
  VLLM_BASE_URL=http://localhost:8000/v1 python3 exp_d3_domain_alignment.py
  
Run locally (if API available):
  OPENAI_API_KEY=... python3 exp_d3_domain_alignment.py --openai

Author: TrustDrift research team
"""

import argparse
import json
import os
import re
import sqlite3
import statistics
import time
from pathlib import Path

# ── Config ─────────────────────────────────────────────────────────────────────
REPO_ROOT  = Path(__file__).resolve().parents[3]
DB_PATH    = REPO_ROOT / "data/skills_sh/skills_sh.sqlite3"
RESULTS    = Path(__file__).parent / "results"
RESULTS.mkdir(exist_ok=True)
OUT_PATH   = RESULTS / "d3_domain_alignment.json"

N_SAMPLE   = 50   # 50 balanced skills: 25 suspicious + 25 control
RANDOM_SEED = 42

# ── LLM Client ─────────────────────────────────────────────────────────────────

def make_client(use_openai: bool = False):
    """Create OpenAI-compatible client."""
    from openai import OpenAI
    if use_openai:
        return OpenAI()  # uses OPENAI_API_KEY
    else:
        # vLLM on Palmetto
        base_url = os.getenv("VLLM_BASE_URL", "http://localhost:8000/v1")
        return OpenAI(base_url=base_url, api_key="dummy")

def llm_json(client, model: str, prompt: str, system: str,
             max_retries: int = 3) -> dict | list | None:
    """Call LLM and parse JSON from response."""
    for attempt in range(max_retries):
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user",   "content": prompt},
                ],
                temperature=0,
                max_tokens=512,
            )
            raw = resp.choices[0].message.content.strip()
            # Try JSON block extraction
            for chunk in re.split(r"```(?:json)?", raw):
                chunk = chunk.strip().rstrip("`").strip()
                if chunk.startswith("{") or chunk.startswith("["):
                    try:
                        return json.loads(chunk)
                    except:
                        pass
            return json.loads(raw)
        except json.JSONDecodeError:
            pass
        except Exception as e:
            print(f"    LLM error (attempt {attempt+1}): {e}")
            time.sleep(2)
    return None

# ── Domain Extraction ──────────────────────────────────────────────────────────

DOMAIN_EXTRACT_SYSTEM = """\
You are a capability domain extractor for AI skill documentation.
Given text from an AI skill, extract 3-8 functional capability domains.
Focus on what the skill DOES, not marketing language.

Return JSON only: {"domains": ["domain1", "domain2", ...]}

Good domains: "file I/O", "REST API calls", "data visualization", "SQL queries",
              "PDF generation", "email sending", "GitHub integration"

Bad domains: "assistance", "productivity", "AI-powered", "smart", "easy to use"
"""

def extract_domains(client, model: str, text: str, text_type: str = "text") -> list[str]:
    """Extract capability domains from description or body text."""
    prompt = f"""Extract the functional capability domains from this AI skill {text_type}:

---
{text[:2000]}
---

Return the 3-8 most specific functional domains as a JSON list."""
    
    result = llm_json(client, model, prompt, DOMAIN_EXTRACT_SYSTEM)
    if isinstance(result, dict) and "domains" in result:
        return [d.lower().strip() for d in result["domains"] if isinstance(d, str)]
    elif isinstance(result, list):
        return [d.lower().strip() for d in result if isinstance(d, str)]
    return []

# ── Inflation Score ────────────────────────────────────────────────────────────

def compute_inflation(desc_domains: list[str], body_domains: list[str]) -> dict:
    """
    Compute domain inflation metrics.
    
    inflation_score = |desc_only domains| / |desc_domains|
    High = description claims domains the body doesn't implement.
    """
    desc_set = set(desc_domains)
    body_set  = set(body_domains)
    
    desc_only = desc_set - body_set   # claimed but not implemented
    body_only  = body_set - desc_set  # implemented but not described (IC-1!)
    shared     = desc_set & body_set
    
    inflation = len(desc_only) / max(len(desc_set), 1)
    concealment = len(body_only) / max(len(body_set), 1)
    
    return {
        "desc_domains": sorted(desc_domains),
        "body_domains": sorted(body_domains),
        "desc_only": sorted(desc_only),   # INFLATION: over-claimed
        "body_only": sorted(body_only),   # IC-1: concealed
        "shared": sorted(shared),
        "inflation_score": round(inflation, 3),
        "concealment_score": round(concealment, 3),  # bonus: detects IC-1 too
    }

# ── Database ───────────────────────────────────────────────────────────────────

def load_skills_for_d3(n_suspicious: int = 25, n_control: int = 25) -> tuple[list, list]:
    """
    Load two groups of skills:
    - suspicious: high T6 score AND desc/body can be separated
    - control: low T6 score AND desc/body match
    """
    import random
    random.seed(RANDOM_SEED)
    
    db = sqlite3.connect(str(DB_PATH))
    
    # Load T6 scores for ranking
    T6_PATH = REPO_ROOT / "experiments/skillrugpull/clawhub/results/threat_scan/T6_hijack.jsonl"
    t6_scores = {}
    if T6_PATH.exists():
        for line in open(T6_PATH):
            r = json.loads(line)
            t6_scores[r["slug"]] = r.get("hijack_score", 0)
    
    # Suspicious group: skills where desc is MUCH shorter than body
    # (high chance of desc claiming less than body → IC-1, or desc claiming more → D3)
    # We want desc_len/body_len ratio < 0.3 (desc is brief, body is rich)
    # OR desc_len > 2000 AND has many non-body tokens
    
    candidates = db.execute("""
        SELECT skill_slug, description, skill_text
        FROM skills
        WHERE description IS NOT NULL
          AND skill_text IS NOT NULL
          AND length(description) BETWEEN 100 AND 3000
          AND length(skill_text) > 500
          AND scan_status = 'complete'
        ORDER BY RANDOM()
        LIMIT 500
    """).fetchall()
    
    db.close()
    
    # Separate into suspicious and control based on T6 score
    high_t6 = [(s, d, b) for s, d, b in candidates if t6_scores.get(s, 0) >= 200]
    low_t6  = [(s, d, b) for s, d, b in candidates if t6_scores.get(s, 0) < 20]
    
    suspicious = random.sample(high_t6, min(n_suspicious, len(high_t6)))
    control    = random.sample(low_t6,  min(n_control,    len(low_t6)))
    
    print(f"Suspicious pool: {len(high_t6)} → sampling {len(suspicious)}")
    print(f"Control pool:    {len(low_t6)}  → sampling {len(control)}")
    
    return suspicious, control

# ── Main Experiment ────────────────────────────────────────────────────────────

def run_experiment(client, model: str) -> dict:
    print("\n" + "="*65)
    print("D3 DOMAIN ALIGNMENT EXPERIMENT (Correct Version)")
    print("="*65)
    
    suspicious_skills, control_skills = load_skills_for_d3(25, 25)
    
    all_results = []
    
    for group, skills in [("suspicious", suspicious_skills), ("control", control_skills)]:
        print(f"\n── {group.upper()} GROUP ({len(skills)} skills) ──")
        
        for i, (slug, desc, body) in enumerate(skills):
            print(f"  [{i+1:2d}] {slug[:30]:30s}", end="", flush=True)
            
            # Extract domains from both desc and body
            desc_doms = extract_domains(client, model, desc, "description")
            body_doms = extract_domains(client, model, body, "body")
            
            # Compute inflation
            metrics = compute_inflation(desc_doms, body_doms)
            
            result = {
                "group": group,
                "slug": slug,
                "desc_len": len(desc),
                "body_len": len(body),
                **metrics,
            }
            all_results.append(result)
            
            inf = metrics["inflation_score"]
            con = metrics["concealment_score"]
            verdict = "INFLATED" if inf > 0.5 else ("ALIGNED" if inf < 0.3 else "PARTIAL")
            print(f" inf={inf:.2f} con={con:.2f} → {verdict}")
            
            # Small delay to avoid rate limiting
            time.sleep(0.5)
    
    # ── Compute Precision/Recall ───────────────────────────────────────────────
    # Treat suspicious group as "positive" (should be inflated)
    # Treat control group as "negative" (should be aligned)
    
    INFLATION_THRESHOLD = 0.4  # tune this
    
    suspicious_r = [r for r in all_results if r["group"] == "suspicious"]
    control_r    = [r for r in all_results if r["group"] == "control"]
    
    tp = sum(1 for r in suspicious_r if r["inflation_score"] >= INFLATION_THRESHOLD)
    fp = sum(1 for r in control_r    if r["inflation_score"] >= INFLATION_THRESHOLD)
    tn = sum(1 for r in control_r    if r["inflation_score"] <  INFLATION_THRESHOLD)
    fn = sum(1 for r in suspicious_r if r["inflation_score"] <  INFLATION_THRESHOLD)
    
    prec = tp / max(tp+fp, 1)
    rec  = tp / max(tp+fn, 1)
    f1   = 2*prec*rec / max(prec+rec, 1e-9)
    
    # Distribution stats
    susp_scores = [r["inflation_score"] for r in suspicious_r]
    ctrl_scores  = [r["inflation_score"] for r in control_r]
    
    # Mann-Whitney test
    try:
        from scipy import stats
        mw = stats.mannwhitneyu(susp_scores, ctrl_scores, alternative='greater')
        mw_p = float(mw.pvalue)
        mw_sig = mw_p < 0.05
    except:
        mw_p = None
        mw_sig = False
    
    print(f"\n{'='*65}")
    print(f"D3 DOMAIN ALIGNMENT RESULTS (threshold={INFLATION_THRESHOLD})")
    print(f"{'='*65}")
    print(f"  TP={tp} FP={fp} TN={tn} FN={fn}")
    print(f"  Precision: {prec:.3f}")
    print(f"  Recall:    {rec:.3f}")
    print(f"  F1:        {f1:.3f}")
    print(f"  Suspicious inflation: median={statistics.median(susp_scores):.3f}")
    print(f"  Control inflation:    median={statistics.median(ctrl_scores):.3f}")
    if mw_p:
        print(f"  Mann-Whitney p={mw_p:.4f} ({'✅ significant' if mw_sig else '❌ not significant'})")
    
    # Compare with IC-1 detection (concealment_score)
    ic1_tp = sum(1 for r in suspicious_r if r["concealment_score"] >= 0.4)
    print(f"\n  Bonus: IC-1 signals in suspicious group: {ic1_tp}/25 ({ic1_tp/25*100:.0f}%)")
    
    # Save results
    output = {
        "experiment": "D3 Domain Alignment (Correct Version)",
        "model": model,
        "inflation_threshold": INFLATION_THRESHOLD,
        "n_suspicious": len(suspicious_r),
        "n_control": len(control_r),
        "precision": round(prec, 3),
        "recall": round(rec, 3),
        "f1": round(f1, 3),
        "tp": int(tp), "fp": int(fp), "tn": int(tn), "fn": int(fn),
        "suspicious_inflation_median": round(statistics.median(susp_scores), 3),
        "control_inflation_median": round(statistics.median(ctrl_scores), 3),
        "mannwhitney_p": mw_p,
        "significant": mw_sig,
        "results": all_results,
    }
    OUT_PATH.write_text(json.dumps(output, indent=2))
    print(f"\n→ {OUT_PATH}")
    return output

# ── Entry point ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="D3 Domain Alignment Experiment")
    parser.add_argument("--openai", action="store_true", help="Use OpenAI API instead of vLLM")
    parser.add_argument("--model",  default="gpt-oss-20b", help="Model ID")
    args = parser.parse_args()
    
    if args.openai:
        model = "gpt-4o-mini"
    else:
        model = args.model
    
    print(f"Using model: {model}")
    client = make_client(args.openai)
    
    result = run_experiment(client, model)
    print(f"\n✅ D3 domain alignment experiment complete.")
    print(f"   F1={result['f1']:.3f}  P={result['precision']:.3f}  R={result['recall']:.3f}")
    if result.get("mannwhitney_p"):
        print(f"   Mann-Whitney p={result['mannwhitney_p']:.4f}")
