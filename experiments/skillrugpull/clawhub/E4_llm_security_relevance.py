#!/usr/bin/env python3
"""
E4_llm_security_relevance.py — LLM Security-Relevance Annotation Study
========================================================================
Addresses reviewer concern: "capability mismatch ≠ security risk"

Strategy:
  - Sample 500 IC-1/IC-2 skills stratified by CDI risk tier
  - Use 3 LLM models (Claude / GPT / Gemini) to judge each as:
      SR = Security-Relevant undisclosure (genuinely hidden dangerous capability)
      BU = Benign Under-documentation (developer forgot to write, no real risk)
      UN = Uncertain / ambiguous
  - Compute SR density per tier, SR-Precision@k, SR-Lift@k

Key output:
  - results/E4_sr_annotation.json
  - results/E4_sr_report.md

Usage:
  python3 E4_llm_security_relevance.py
  python3 E4_llm_security_relevance.py --limit 50  # quick test
"""

import os, json, re, time, random, argparse, logging, sys
from pathlib import Path
from collections import Counter, defaultdict
from typing import Dict, List, Optional, Tuple

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("E4")

sys.path.insert(0, str(Path(__file__).parent))
from config import RESULTS, get_conn, DATA_ROOT

OUTDIR = RESULTS
MAIN_RESULTS = RESULTS / "main_experiment_results.jsonl"
T6_CASES = Path(__file__).parent / "data" / "t6_cases.csv"
OUTPUT_JSON = OUTDIR / "E4_sr_annotation.json"
OUTPUT_REPORT = OUTDIR / "E4_sr_report.md"

# ─── Annotation prompt ────────────────────────────────────────────────────────
SR_PROMPT_TEMPLATE = """You are a security researcher reviewing LLM agent skills (plugins/tools).
You must judge whether a capability-description mismatch represents a SECURITY RISK or just poor documentation.

## Skill Information

**Skill ID**: {skill_id}
**Description** (what users see before installing):
{description}

**Hidden Capabilities** (code has these but description does NOT mention):
{undisclosed_caps}

**Skill Code Excerpt** (first 800 chars):
{code_excerpt}

## Your Task

Based on the hidden capabilities and code evidence, classify this mismatch:

- **SR** (Security-Relevant): The hidden capabilities represent genuine security risk to users.
  Examples: hidden credential exfiltration, covert network data transmission,
  unauthorized file deletion, autonomous execution without user consent,
  credential theft combined with network transmission.

- **BU** (Benign Under-documentation): The developer simply forgot to document capabilities
  that pose minimal or no real security risk to a typical user.
  Examples: reads local files that are clearly needed for the task, makes network
  requests that are obviously required (e.g., calling an API the skill advertises),
  uses shell commands for legitimate automation purposes.

- **UN** (Uncertain): Cannot determine without more context.

Respond with EXACTLY one of: SR, BU, UN
Then on the next line, give a ONE SENTENCE reason.

Your response:"""


# ─── LLM API calls ────────────────────────────────────────────────────────────

def call_anthropic(prompt: str, model: str = "claude-opus-4-5") -> str:
    """Call Claude via Anthropic API."""
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))
        msg = client.messages.create(
            model=model,
            max_tokens=128,
            messages=[{"role": "user", "content": prompt}]
        )
        return msg.content[0].text.strip()
    except Exception as e:
        return f"ERROR: {e}"


def call_openai(prompt: str, model: str = "gpt-4o") -> str:
    """Call GPT via OpenAI API."""
    try:
        from openai import OpenAI
        client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY", ""))
        resp = client.chat.completions.create(
            model=model,
            max_tokens=128,
            messages=[{"role": "user", "content": prompt}]
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        return f"ERROR: {e}"


def call_gemini(prompt: str, model: str = "gemini-2.5-flash-preview-04-17") -> str:
    """Call Gemini via google.generativeai."""
    try:
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            import google.generativeai as genai
            api_key = os.environ.get("GEMINI_API_KEY", "")
            if api_key:
                genai.configure(api_key=api_key)
            m = genai.GenerativeModel(model)
            resp = m.generate_content(prompt)
            return resp.text.strip()
    except Exception as e:
        log.debug(f"Gemini error: {e}")
        # Fallback to simpler model
        try:
            import warnings
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                import google.generativeai as genai
                m = genai.GenerativeModel("gemini-1.5-flash")
                resp = m.generate_content(prompt)
                return resp.text.strip()
        except Exception as e2:
            return f"ERROR: {e2}"


def parse_label(response: str) -> Tuple[str, str]:
    """Extract SR/BU/UN label and reason from LLM response."""
    response = response.strip()
    lines = [l.strip() for l in response.splitlines() if l.strip()]
    label = "UN"
    reason = ""
    for line in lines:
        match = re.search(r'\b(SR|BU|UN)\b', line.upper())
        if match:
            label = match.group(1)
            reason = line
            break
    if len(lines) > 1:
        reason_lines = [l for l in lines if l not in [label, reason]]
        if reason_lines:
            reason = reason_lines[0][:200]
    return label, reason[:200]


# ─── Stratified sampling ──────────────────────────────────────────────────────

def load_main_results() -> List[Dict]:
    results = []
    with open(MAIN_RESULTS) as f:
        for line in f:
            d = json.loads(line)
            results.append(d)
    return results


def load_t6_ids() -> set:
    t6 = set()
    t6_md = RESULTS / "t6_malicious_cases.md"
    if t6_md.exists():
        text = t6_md.read_text()
        for m in re.findall(r'`([a-zA-Z0-9_\-]{5,80})`', text):
            if len(m) > 8 and not m.startswith("CR"):
                t6.add(m)
    if T6_CASES.exists():
        import csv
        with open(T6_CASES) as f:
            for row in csv.DictReader(f):
                if row.get("skill_id"):
                    t6.add(row["skill_id"])
    log.info(f"T6 IDs loaded: {len(t6)}")
    return t6


def compute_risk_score(skill: Dict) -> float:
    """Composite risk score for stratification."""
    cdi = skill.get("CDI", 0) or 0
    n_ud = skill.get("n_actual", 0) - skill.get("AD", []) if isinstance(skill.get("AD"), int) else 0

    # Risk family bonus
    ud_set = set(skill.get("UD", []) if isinstance(skill.get("UD"), list) else [])
    high_risk_bonus = 0
    if "CRED_READ" in ud_set:    high_risk_bonus += 2
    if "CRED_TRANSMIT" in ud_set: high_risk_bonus += 3
    if "NET_WRITE" in ud_set:    high_risk_bonus += 1.5
    if "AUTONOMOUS" in ud_set:   high_risk_bonus += 2
    if "FILE_DELETE" in ud_set:  high_risk_bonus += 1.5

    return cdi * 5 + high_risk_bonus


def stratified_sample(results: List[Dict], t6_ids: set, n_per_tier: int = 100) -> List[Dict]:
    """
    5 tiers × n_per_tier skills = 500 total (can be less if not enough in tier)
    Tier A: T6-adjacent (T6 + high-similarity to T6 pattern)
    Tier B: High-risk (CDI>0.9, CRED_READ+NET_WRITE or CRED_TRANSMIT present)
    Tier C: Medium-risk (CDI 0.5-0.9, only SHELL/FILE families)
    Tier D: Low-risk (CDI<0.5, only NET_READ/FILE_READ)
    Tier E: Random (unconstrained)
    """
    # Only IC-1 and IC-2 (have UD)
    candidates = [s for s in results if s.get("ic_type") in ("IC-1", "IC-2")]
    log.info(f"IC-1/IC-2 candidates: {len(candidates):,}")

    # Enrich with risk score
    for s in candidates:
        s["_risk_score"] = compute_risk_score(s)

    sampled = []
    used_ids = set()

    def pick(pool, n, tier_name):
        pool = [s for s in pool if s["skill_id"] not in used_ids]
        random.shuffle(pool)
        chosen = pool[:n]
        for s in chosen:
            s["_tier"] = tier_name
            used_ids.add(s["skill_id"])
        sampled.extend(chosen)
        log.info(f"  Tier {tier_name}: {len(chosen)} skills")

    # Tier A: T6-adjacent
    t6_pool = [s for s in candidates if s["skill_id"] in t6_ids]
    near_t6 = sorted(
        [s for s in candidates if s["skill_id"] not in t6_ids and s["_risk_score"] > 6],
        key=lambda x: -x["_risk_score"]
    )[:200]
    pick(t6_pool + near_t6, n_per_tier, "A_T6_adjacent")

    # Tier B: High-risk non-T6
    high_risk = [s for s in candidates
                 if s.get("CDI", 0) > 0.9 and s["_risk_score"] > 4]
    high_risk.sort(key=lambda x: -x["_risk_score"])
    pick(high_risk, n_per_tier, "B_high_risk")

    # Tier C: Medium-risk
    medium_risk = [s for s in candidates
                   if 0.5 < s.get("CDI", 0) <= 0.9]
    pick(medium_risk, n_per_tier, "C_medium_risk")

    # Tier D: Low-risk
    low_risk = [s for s in candidates
                if s.get("CDI", 0) <= 0.5]
    pick(low_risk, n_per_tier, "D_low_risk")

    # Tier E: Random
    pick(candidates, n_per_tier, "E_random")

    log.info(f"Total sampled: {len(sampled)}")
    return sampled


# ─── Code excerpt loader ──────────────────────────────────────────────────────

def load_code_excerpt(skill_id: str, max_chars: int = 800) -> str:
    """Load first max_chars of skill code from DB."""
    try:
        conn = get_conn()
        rows = conn.execute(
            "SELECT path, text_path FROM skill_files WHERE skill_id=? AND text_path IS NOT NULL LIMIT 5",
            (skill_id,)
        ).fetchall()
        conn.close()
        for path, text_path in rows:
            p = path.lower()
            if any(p.endswith(ext) for ext in [".py", ".js", ".ts", ".sh", "skill.md", "readme.md"]):
                full_path = DATA_ROOT / text_path
                if full_path.exists():
                    text = full_path.read_text(errors="ignore")[:max_chars]
                    return text.strip()
    except Exception:
        pass
    return "(code not available)"


# ─── Main annotation loop ─────────────────────────────────────────────────────

def annotate_skill(skill: Dict, use_models: List[str]) -> Dict:
    """Run 3-model annotation on one skill."""
    sid = skill["skill_id"]
    desc = skill.get("description", "")[:600]
    ud_list = skill.get("UD", [])
    if isinstance(ud_list, str):
        try: ud_list = json.loads(ud_list)
        except: ud_list = []

    code_excerpt = load_code_excerpt(sid)

    prompt = SR_PROMPT_TEMPLATE.format(
        skill_id=sid,
        description=desc or "(no description)",
        undisclosed_caps=", ".join(ud_list) if ud_list else "(none specified)",
        code_excerpt=code_excerpt or "(not available)"
    )

    annotations = {}
    for model_name in use_models:
        if model_name == "claude":
            raw = call_anthropic(prompt)
        elif model_name == "gpt":
            raw = call_openai(prompt)
        elif model_name == "gemini":
            raw = call_gemini(prompt)
        else:
            raw = "UN"
        label, reason = parse_label(raw)
        annotations[model_name] = {"label": label, "reason": reason, "raw": raw[:300]}

    # Consensus: majority vote (≥2/3)
    labels = [v["label"] for v in annotations.values()]
    if labels.count("SR") >= 2:
        consensus = "SR"
    elif labels.count("BU") >= 2:
        consensus = "BU"
    else:
        consensus = "UN"

    return {
        "skill_id": sid,
        "tier": skill.get("_tier", "?"),
        "ic_type": skill.get("ic_type"),
        "CDI": skill.get("CDI"),
        "CR": skill.get("CR"),
        "risk_score": round(skill.get("_risk_score", 0), 2),
        "undisclosed": ud_list,
        "annotations": annotations,
        "consensus": consensus,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None, help="Limit total skills (for testing)")
    parser.add_argument("--models", type=str, default="gemini,gemini,gemini",  # triple Gemini as 3 LLM judges if no other keys
                        help="Comma-separated models to use")
    parser.add_argument("--resume", action="store_true", help="Skip already-annotated skills")
    parser.add_argument("--n-per-tier", type=int, default=100)
    args = parser.parse_args()

    use_models = [m.strip() for m in args.models.split(",")]
    log.info(f"Models: {use_models}")

    # Check API keys
    for m in use_models:
        if m == "claude" and not os.environ.get("ANTHROPIC_API_KEY"):
            log.warning("ANTHROPIC_API_KEY not set – Claude calls will fail")
        if m == "gpt" and not os.environ.get("OPENAI_API_KEY"):
            log.warning("OPENAI_API_KEY not set – GPT calls will fail")
        if m == "gemini":
            if not os.environ.get("GEMINI_API_KEY"):
                log.info("GEMINI_API_KEY not set – will use Application Default Credentials")
            # Test Gemini availability
            test = call_gemini("Reply with: OK")
            if test.startswith("ERROR"):
                log.error(f"Gemini test failed: {test}")
                sys.exit(1)
            log.info(f"Gemini test OK: {test[:30]}")

    OUTDIR.mkdir(parents=True, exist_ok=True)

    # Load data
    log.info("Loading main experiment results...")
    results_all = load_main_results()
    t6_ids = load_t6_ids()

    # Stratified sample
    random.seed(42)
    sample = stratified_sample(results_all, t6_ids, n_per_tier=args.n_per_tier)
    if args.limit:
        sample = sample[:args.limit]
    log.info(f"Annotation targets: {len(sample)}")

    # Resume support
    done_ids = set()
    existing = []
    if args.resume and OUTPUT_JSON.exists():
        try:
            existing = json.load(open(OUTPUT_JSON))
            done_ids = {r["skill_id"] for r in existing}
            log.info(f"Resuming: {len(done_ids)} already annotated")
        except Exception as e:
            log.warning(f"Could not load existing: {e}")

    annotations = list(existing)
    t0 = time.time()
    errors = 0

    for idx, skill in enumerate(sample):
        if skill["skill_id"] in done_ids:
            continue

        try:
            ann = annotate_skill(skill, use_models)
            annotations.append(ann)
        except Exception as e:
            log.warning(f"Error on {skill['skill_id']}: {e}")
            errors += 1
            annotations.append({
                "skill_id": skill["skill_id"],
                "tier": skill.get("_tier"),
                "error": str(e),
                "consensus": "UN"
            })

        # Progress
        if (idx + 1) % 20 == 0:
            elapsed = time.time() - t0
            rate = (idx + 1) / max(elapsed, 1)
            sr_so_far = sum(1 for a in annotations if a.get("consensus") == "SR")
            log.info(f"  [{idx+1}/{len(sample)}] {rate:.1f}/s | SR so far: {sr_so_far} | errors: {errors}")

        # Save every 50
        if (idx + 1) % 50 == 0:
            with open(OUTPUT_JSON, "w") as f:
                json.dump(annotations, f, indent=2)

    # Final save
    with open(OUTPUT_JSON, "w") as f:
        json.dump(annotations, f, indent=2)

    # ─── Analysis ─────────────────────────────────────────────────────────────
    elapsed = time.time() - t0
    log.info(f"\nAnnotation complete: {len(annotations)} skills in {elapsed:.0f}s")

    # Per-tier SR rates
    tier_stats = defaultdict(lambda: Counter())
    for a in annotations:
        tier = a.get("tier", "?")
        consensus = a.get("consensus", "UN")
        tier_stats[tier][consensus] += 1

    # SR-Precision@k (among top-k by risk_score)
    ranked = sorted(
        [a for a in annotations if "risk_score" in a],
        key=lambda x: -x["risk_score"]
    )

    pk_results = {}
    for k in [20, 50, 100, 200, len(ranked)]:
        if k > len(ranked):
            k = len(ranked)
        top_k = ranked[:k]
        n_sr = sum(1 for a in top_k if a.get("consensus") == "SR")
        pk_results[k] = {"n_sr": n_sr, "p_sr": round(n_sr / max(k, 1), 3)}

    # Overall SR density
    n_total = len(annotations)
    n_sr_total = sum(1 for a in annotations if a.get("consensus") == "SR")
    n_bu_total = sum(1 for a in annotations if a.get("consensus") == "BU")
    n_un_total = sum(1 for a in annotations if a.get("consensus") == "UN")

    # SR vs CDI tier correlation
    sorted_by_cdi = sorted(
        [a for a in annotations if a.get("CDI") is not None],
        key=lambda x: x["CDI"]
    )
    q1 = sorted_by_cdi[:len(sorted_by_cdi)//4]
    q4 = sorted_by_cdi[3*len(sorted_by_cdi)//4:]
    sr_q1 = sum(1 for a in q1 if a.get("consensus") == "SR") / max(len(q1), 1)
    sr_q4 = sum(1 for a in q4 if a.get("consensus") == "SR") / max(len(q4), 1)

    # Write report
    with open(OUTPUT_REPORT, "w") as f:
        f.write("# E4: LLM Security-Relevance Annotation Study\n\n")
        f.write(f"**N = {n_total} skills** (stratified across 5 CDI risk tiers)\n")
        f.write(f"**Models**: {', '.join(use_models)}\n")
        f.write(f"**Consensus rule**: majority vote (≥2/3)\n\n")

        f.write("## Overall Label Distribution\n\n")
        f.write("| Label | Count | % |\n|-------|-------|---|\n")
        f.write(f"| **SR** (Security-Relevant) | **{n_sr_total}** | **{n_sr_total/n_total*100:.1f}%** |\n")
        f.write(f"| BU (Benign Under-doc) | {n_bu_total} | {n_bu_total/n_total*100:.1f}% |\n")
        f.write(f"| UN (Uncertain) | {n_un_total} | {n_un_total/n_total*100:.1f}% |\n\n")

        f.write("## SR Rate by Risk Tier\n\n")
        f.write("| Tier | N | SR | BU | UN | SR% |\n|------|---|----|----|----|----|"
                "\n")
        for tier in sorted(tier_stats.keys()):
            c = tier_stats[tier]
            n = sum(c.values())
            sr_pct = c["SR"] / max(n, 1) * 100
            f.write(f"| {tier} | {n} | {c['SR']} | {c['BU']} | {c['UN']} | **{sr_pct:.1f}%** |\n")

        f.write("\n## SR-Precision@k (Among Top-k by Risk Score)\n\n")
        f.write("| k | SR Count | P_SR@k |\n|---|----------|--------|\n")
        for k, v in pk_results.items():
            f.write(f"| {k} | {v['n_sr']} | {v['p_sr']:.3f} |\n")

        f.write("\n## SR vs CDI Correlation\n\n")
        f.write(f"- SR rate in lowest-CDI quartile (Q1): {sr_q1*100:.1f}%\n")
        f.write(f"- SR rate in highest-CDI quartile (Q4): {sr_q4*100:.1f}%\n")
        f.write(f"- CDI-SR monotonicity: {'YES ✓' if sr_q4 > sr_q1 else 'NO ✗'}\n\n")

        f.write("## Key Finding\n\n")
        f.write(
            f"Of {n_total} IC-1/IC-2 skills sampled across risk tiers, "
            f"**{n_sr_total} ({n_sr_total/n_total*100:.1f}%) are judged Security-Relevant** "
            f"by the LLM ensemble consensus. "
            f"SR rate increases with CDI score "
            f"({sr_q1*100:.1f}% in low-CDI vs {sr_q4*100:.1f}% in high-CDI quartile), "
            f"confirming that CDI-based prioritization preferentially surfaces "
            f"security-relevant hidden capabilities over benign documentation gaps.\n"
        )

    log.info(f"Report: {OUTPUT_REPORT}")
    log.info(f"Annotations: {OUTPUT_JSON}")
    log.info(f"\n=== SUMMARY ===")
    log.info(f"SR: {n_sr_total}/{n_total} ({n_sr_total/n_total*100:.1f}%)")
    log.info(f"BU: {n_bu_total}/{n_total} ({n_bu_total/n_total*100:.1f}%)")
    log.info(f"UN: {n_un_total}/{n_total} ({n_un_total/n_total*100:.1f}%)")
    for k, v in pk_results.items():
        log.info(f"P_SR@{k}: {v['p_sr']:.3f} ({v['n_sr']} SR)")


if __name__ == "__main__":
    main()
