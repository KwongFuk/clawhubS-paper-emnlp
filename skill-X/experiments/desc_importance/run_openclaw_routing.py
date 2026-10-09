"""
OpenClaw Real Routing Experiment — Description Importance Pilot
==============================================================
Uses the REAL OpenClaw CLI (openclaw infer model run) to route queries.
No external API key needed — uses the openai-codex OAuth provider.

Strategy
--------
1. Take the 22 ELIGIBLE installed skills (real SKILL.md files from OpenClaw).
2. For each focal skill, build a routing prompt with all 22 candidates.
3. Vary only that focal skill's description (original / vague / aligned).
   The body of SKILL.md is NOT changed — only the frontmatter description line.
4. Generate queries via openclaw infer model run (GPT-5.3-codex, no key needed).
5. Route queries via the same LLM.
6. Report Recall@1, False-Trigger Rate, Description Sensitivity.

No file patching needed — we build routing prompts in Python
and pass the description text directly. SKILL.md files are NEVER modified.

Output → skill-X/experiments/desc_importance/results_openclaw/
"""

import json
import os
import re
import sys
import subprocess
import hashlib
import random
import time
import argparse
from pathlib import Path

SEED = 42
random.seed(SEED)

REPO_ROOT    = Path(__file__).resolve().parents[3]
RESULTS_DIR  = Path(__file__).parent / "results_openclaw"
CACHE_DIR    = Path(__file__).parent / ".cache_oc"

OPENCLAW_BIN = "openclaw"  # must be in PATH

# ─────────────────────────────────────────────────────────────────────────────
# 1. COLLECT INSTALLED ELIGIBLE SKILLS
# ─────────────────────────────────────────────────────────────────────────────

def collect_installed_skills() -> list[dict]:
    """Use `openclaw skills list --json` and `skills info <name> --json` to get skill data."""
    result = subprocess.run(
        [OPENCLAW_BIN, "skills", "list", "--json"],
        capture_output=True, text=True, timeout=30
    )
    data = json.loads(result.stdout)
    all_skills = data.get("skills", [])
    eligible = [s for s in all_skills if s.get("eligible")]
    print(f"[collect] {len(eligible)} eligible skills")

    enriched = []
    for s in eligible:
        info_result = subprocess.run(
            [OPENCLAW_BIN, "skills", "info", s["name"], "--json"],
            capture_output=True, text=True, timeout=15
        )
        try:
            info = json.loads(info_result.stdout)
        except Exception:
            info = {}

        file_path = info.get("filePath", "")
        body = ""
        if file_path and Path(file_path).exists():
            body = Path(file_path).read_text(encoding="utf-8")

        enriched.append({
            "name"       : s["name"],
            "description": s["description"],
            "file_path"  : file_path,
            "source"     : s.get("source", ""),
            "body"       : body,
        })
        print(f"  ✓ {s['name']} ({file_path.split('/')[-2] if '/' in file_path else '?'})")

    return enriched


# ─────────────────────────────────────────────────────────────────────────────
# 2. OPENCLAW LLM CALL (via openclaw infer model run)
# ─────────────────────────────────────────────────────────────────────────────

def openclaw_llm(prompt: str, cache_key: str | None = None, retries: int = 2) -> str:
    """
    Run openclaw infer model run --prompt ... --json
    Uses Codex OAuth — no OPENAI_API_KEY needed.
    Retries on timeout with increasing wait.
    """
    if cache_key:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cf = CACHE_DIR / f"{cache_key}.txt"
        if cf.exists():
            return cf.read_text().strip()

    last_err = None
    for attempt in range(retries + 1):
        try:
            result = subprocess.run(
                [OPENCLAW_BIN, "infer", "model", "run",
                 "--prompt", prompt,
                 "--json"],
                capture_output=True, text=True, timeout=120
            )
            output = result.stdout.strip()
            # Parse JSON output
            try:
                j = json.loads(output)
                text = j.get("outputs", [{}])[0].get("text", "").strip()
            except Exception:
                lines = [l.strip() for l in output.split("\n") if l.strip()]
                text = lines[-1] if lines else ""
            if cache_key:
                (CACHE_DIR / f"{cache_key}.txt").write_text(text)
            return text
        except subprocess.TimeoutExpired as e:
            last_err = e
            wait = (attempt + 1) * 5
            print(f"  [WARN] LLM timeout (attempt {attempt+1}/{retries+1}), retrying in {wait}s …")
            time.sleep(wait)

    print(f"  [ERROR] All retries exhausted. Returning empty string.")
    if cache_key:
        (CACHE_DIR / f"{cache_key}.txt").write_text("NONE")
    return "NONE"


# ─────────────────────────────────────────────────────────────────────────────
# 3. DESCRIPTION VARIANTS
# ─────────────────────────────────────────────────────────────────────────────

_VAGUE_POOL = [
    "Helps with productivity tasks.",
    "Assists with common workflow operations.",
    "A general-purpose skill for various tasks.",
    "Use when you need help with everyday tasks.",
    "Provides support for multiple operations.",
    "Useful for managing your workflow.",
]


def make_vague(skill_name: str) -> str:
    idx = int(hashlib.md5(skill_name.encode()).hexdigest(), 16) % len(_VAGUE_POOL)
    return _VAGUE_POOL[idx]


def make_aligned(skill: dict) -> str:
    """Generate capability-aligned description via openclaw LLM (openai-codex, no key)."""
    ck = hashlib.sha256((skill["name"] + skill.get("body", "")[:200]).encode()).hexdigest()[:20]
    body_excerpt = skill.get("body", "")[:1200]
    original_desc = skill["description"]

    prompt = f"""You are writing a routing card for an AI skill. The skill is: {skill['name']}

Original marketplace description (may be vague or inaccurate):
{original_desc}

First 1200 chars of SKILL.md:
{body_excerpt}

Write a capability-aligned description (2-4 sentences) that:
1. Accurately states what the skill CAN do based on its implementation
2. States trigger conditions (when to use it)
3. States at least one boundary (what it CANNOT do or when NOT to use it)
4. Uses concrete, specific language — no marketing fluff

Output ONLY the description text, no headings, no quotes."""

    return openclaw_llm(prompt, cache_key=f"aligned_{ck}")


# ─────────────────────────────────────────────────────────────────────────────
# 4. QUERY GENERATION
# ─────────────────────────────────────────────────────────────────────────────

def generate_queries(skill: dict) -> list[dict]:
    """Generate 8 routing test queries for a skill via openclaw LLM."""
    ck = hashlib.sha256(skill["name"].encode()).hexdigest()[:20]
    cf = CACHE_DIR / f"queries_oc_{ck}.json"
    if cf.exists():
        return json.loads(cf.read_text())

    body_excerpt = skill.get("body", "")[:800]
    prompt = f"""Generate exactly 8 test queries to evaluate skill routing for: {skill['name']}
Skill description: {skill['description']}
SKILL.md excerpt: {body_excerpt[:500]}

Output a JSON array of 8 objects. Each object:
  "query": the user request string (1-2 sentences)
  "query_type": one of [explicit, implicit, boundary, negative]
  "should_trigger": true or false

Rules (exactly 2 of each type):
- explicit (should_trigger=true): user clearly asks for what THIS skill does
- implicit (should_trigger=true): user describes the need without naming this skill
- boundary (should_trigger=false): looks like this skill but needs a DIFFERENT skill
- negative (should_trigger=false): completely unrelated to this skill

Output ONLY the JSON array, no markdown."""

    raw = openclaw_llm(prompt, cache_key=f"queries_oc_{ck}")
    raw = re.sub(r"^```(?:json)?\s*", "", raw).rstrip("`").strip()
    try:
        queries = json.loads(raw)
    except json.JSONDecodeError:
        # Try to extract JSON array from response
        m = re.search(r"\[.*\]", raw, re.DOTALL)
        queries = json.loads(m.group(0)) if m else []

    cf.write_text(json.dumps(queries, ensure_ascii=False, indent=2))
    return queries


# ─────────────────────────────────────────────────────────────────────────────
# 5. ROUTING VIA OPENCLAW LLM
# ─────────────────────────────────────────────────────────────────────────────

MAX_DESC_IN_ROUTING = 200  # chars per description in the routing prompt


def route_llm(query: str, library: list[dict], desc_key: str,
               hide_name: bool = False) -> str:
    """
    OpenClaw LLM selects the best skill.
    library items: {"name": ..., desc_key: ...}

    hide_name=False  → "1. [weather] description…"  (name visible — LLM can cheat)
    hide_name=True   → "1. description…"            (description-only, like embedding router)

    When hide_name=True the LLM must return an integer ("1", "3", …) which we
    map back to the skill name.  This exactly simulates embedding-based routing
    where routing is done purely on description text without identifier leakage.
    """
    mode = "desc_only" if hide_name else "with_name"
    ck = hashlib.sha256(
        (query + desc_key + mode + ",".join(s["name"] for s in library)).encode()
    ).hexdigest()[:24]

    if hide_name:
        # Description text only — no name exposed
        numbered = "\n".join(
            f"{i+1}. {s[desc_key][:MAX_DESC_IN_ROUTING]}"
            for i, s in enumerate(library)
        )
        prompt = f"""You are a skill router. Select the most relevant skill for the user request below.
You are shown descriptions only—NO skill names.

User request: {query}

Skill descriptions:
{numbered}

Reply with ONLY the number of the best matching description, e.g. "3".
If none are relevant, reply with "0"."""

        raw = openclaw_llm(prompt, cache_key=f"route_oc_{ck}")
        # Parse number
        m = re.search(r"\b(\d+)\b", raw.strip())
        if m:
            idx = int(m.group(1))
            if 1 <= idx <= len(library):
                return library[idx - 1]["name"]
        return "NONE"
    else:
        numbered = "\n".join(
            f"{i+1}. [{s['name']}] {s[desc_key][:MAX_DESC_IN_ROUTING]}"
            for i, s in enumerate(library)
        )
        prompt = f"""You are a skill router for an AI agent. Given the user request, select the most relevant skill.

User request: {query}

Available skills:
{numbered}

Reply with ONLY the skill name in square brackets, e.g. [weather]
If none are relevant, reply with [NONE]."""

        answer = openclaw_llm(prompt, cache_key=f"route_oc_{ck}")
        m = re.search(r"\[([^\]]+)\]", answer)
        if m:
            return m.group(1)
        words = answer.strip().split()
        return words[0] if words else "NONE"


# ─────────────────────────────────────────────────────────────────────────────
# 6. EMBEDDING ROUTER (via openclaw infer embedding or cosine on openai embed)
# ─────────────────────────────────────────────────────────────────────────────

_emb_cache: dict[str, list[float]] = {}


def embed_via_openclaw(text: str) -> list[float] | None:
    """
    Try: openclaw infer embedding create --text ... --provider openai --json
    If openai fails, fall back to local provider.
    """
    ck = hashlib.sha256(text.encode()).hexdigest()[:24]
    cf = CACHE_DIR / f"emb_oc_{ck}.json"
    if cf.exists():
        return json.loads(cf.read_text())

    for provider in ["openai", "local"]:
        result = subprocess.run(
            [OPENCLAW_BIN, "infer", "embedding", "create",
             "--text", text, "--provider", provider, "--json"],
            capture_output=True, text=True, timeout=30
        )
        try:
            j = json.loads(result.stdout)
            vec = j[0]["embedding"]
            cf.write_text(json.dumps(vec))
            return vec
        except Exception:
            continue
    return None


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na  = sum(x * x for x in a) ** 0.5
    nb  = sum(x * x for x in b) ** 0.5
    return dot / (na * nb) if na and nb else 0.0


def route_embed(query: str, library: list[dict], desc_key: str) -> str | None:
    """Embedding cosine routing. Returns skill name or None if embeddings unavailable."""
    qvec = embed_via_openclaw(query)
    if qvec is None:
        return None
    best_name, best_score = None, -1.0
    for skill in library:
        dvec = embed_via_openclaw(skill[desc_key])
        if dvec is None:
            continue
        score = cosine(qvec, dvec)
        if score > best_score:
            best_score = score
            best_name = skill["name"]
    return best_name


# ─────────────────────────────────────────────────────────────────────────────
# 7. MAIN EXPERIMENT LOOP
# ─────────────────────────────────────────────────────────────────────────────

def run_experiment(skills: list[dict]) -> list[dict]:
    results = []
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    # Phase 1: description variants (all cached after first run)
    print("\n[Phase 1] Generating description variants …")
    for s in skills:
        s["desc_original"] = s["description"]
        s["desc_vague"]    = make_vague(s["name"])
        print(f"  → aligned desc for [{s['name']}] …")
        s["desc_aligned"]  = make_aligned(s)

    # Phase 2: query sets (all cached after first run)
    print("\n[Phase 2] Generating query sets …")
    for s in skills:
        print(f"  → queries for [{s['name']}] …")
        s["_queries"] = generate_queries(s)

    # Phase 3: routing eval — TWO modes
    #   mode A: with_name  → LLM sees [skill_name] + description  (baseline, may cheat via name)
    #   mode B: desc_only  → LLM sees description text only        (clean description signal)
    for mode_label, hide_name in [("with_name", False), ("desc_only", True)]:
        print(f"\n[Phase 3 / {mode_label}] Routing evaluation …")
        for focal in skills:
            library_pool = skills

            for q in focal.get("_queries", []):
                should_trigger = q.get("should_trigger", False)

                for variant in ["original", "vague", "aligned"]:
                    dk = f"desc_{variant}"
                    lib_view = [{"name": s["name"], dk: s[dk]} for s in library_pool]

                    llm_choice = route_llm(q["query"], lib_view, dk, hide_name=hide_name)
                    llm_correct = (
                        (llm_choice == focal["name"]) if should_trigger
                        else (llm_choice != focal["name"])
                    )

                    results.append({
                        "skill_name"    : focal["name"],
                        "skill_source"  : focal.get("source", ""),
                        "query"         : q["query"],
                        "query_type"    : q.get("query_type"),
                        "should_trigger": should_trigger,
                        "router_mode"   : mode_label,
                        "desc_variant"  : variant,
                        "desc_original" : focal["desc_original"],
                        "desc_used"     : focal[dk],
                        "llm_choice"    : llm_choice,
                        "llm_correct"   : llm_correct,
                    })

            n_queries = len(focal.get("_queries", []))
            print(f"  ✓ {focal['name']} — {n_queries} q × 3 variants")

    return results


# ─────────────────────────────────────────────────────────────────────────────
# 8. METRICS + REPORT
# ─────────────────────────────────────────────────────────────────────────────

def compute_metrics(results: list[dict]) -> dict:
    """Compute metrics stratified by router_mode × desc_variant."""
    metrics: dict = {}

    def safe_pct(subset, key="llm_correct"):
        vals = [r[key] for r in subset if r.get(key) is not None]
        return round(sum(vals) / len(vals) * 100, 1) if vals else None

    def sensitivity(rows, v1, v2):
        skills = list({r["skill_name"] for r in rows})
        diffs = []
        for sn in skills:
            p1 = [r for r in rows if r["skill_name"]==sn and r["desc_variant"]==v1 and r["should_trigger"]]
            p2 = [r for r in rows if r["skill_name"]==sn and r["desc_variant"]==v2 and r["should_trigger"]]
            a1 = safe_pct(p1)
            a2 = safe_pct(p2)
            if a1 is not None and a2 is not None:
                diffs.append(abs(a1 - a2))
        return round(sum(diffs)/len(diffs), 1) if diffs else None

    for mode in ["with_name", "desc_only"]:
        mode_rows = [r for r in results if r.get("router_mode") == mode]
        metrics[mode] = {}
        for v in ["original", "vague", "aligned"]:
            rows = [r for r in mode_rows if r["desc_variant"] == v]
            pos  = [r for r in rows if     r["should_trigger"]]
            neg  = [r for r in rows if not r["should_trigger"]]
            ftr_val = safe_pct(neg)
            metrics[mode][v] = {
                "n"       : len(rows),
                "recall"  : safe_pct(pos),
                "ftr"     : round(100 - ftr_val, 1) if ftr_val is not None else None,
            }
        metrics[mode]["sensitivity"] = {
            "orig_vs_vague"  : sensitivity(mode_rows, "original", "vague"),
            "orig_vs_aligned": sensitivity(mode_rows, "original", "aligned"),
        }

    return metrics


def build_report(metrics: dict, skills: list[dict], results: list[dict]) -> str:
    n_skills = len(skills)
    n_total  = len(results)

    lines = [
        "# Description Importance Experiment — OpenClaw Real Routing",
        "",
        f"**Router**: openclaw infer model run (gpt-5.3-codex, Codex OAuth)  |  **Skills**: {n_skills}  |  **Total decisions**: {n_total}",
        "",
        "> **Two routing modes compared:**",
        "> - `with_name`: prompt shows `[skill_name] description` — LLM can route by name alone (baseline)",
        "> - `desc_only`: prompt shows description text ONLY — equivalent to embedding router, description is the sole signal",
        "",
    ]

    for mode_label, mode_key in [("with_name (name visible — LLM may shortcut)", "with_name"),
                                   ("desc_only  (description only — true routing signal)", "desc_only")]:
        mm = metrics.get(mode_key, {})
        lines += [
            f"## Mode: `{mode_label}`",
            "",
            "| Variant | Recall@1 | FTR | Sens orig→vague | Sens orig→aligned |",
            "|---------|----------|-----|----------------|-------------------|",
        ]
        sv = mm.get("sensitivity", {})
        for v in ["original", "vague", "aligned"]:
            vm = mm.get(v, {})
            lines.append(
                f"| **{v}** | {vm.get('recall')}% | {vm.get('ftr')}% |"
                f" {sv.get('orig_vs_vague')}% | {sv.get('orig_vs_aligned')}% |"
                if v == "original" else
                f"| **{v}** | {vm.get('recall')}% | {vm.get('ftr')}% | — | — |"
            )
        lines += [""]

    lines += [
        "## Key Interpretation",
        "",
        "- `desc_only` sensitivity **>> 0** → description text drives routing",
        "- `with_name` sensitivity ≈ 0, `desc_only` >> 0 → **name was masking description effect**",
        "- `desc_only` aligned recall > original > vague → capability-grounded descriptions improve routing",
        "",
        "## Skills in Experiment",
        "",
        "| Skill | Source | Original Description (first 60 chars) | Aligned Description (first 60 chars) |",
        "|------|--------|--------------------------------------|--------------------------------------|",
    ]
    for s in skills:
        od = s.get("desc_original", s.get("description", ""))[:60].replace("|", "｜")
        ad = s.get("desc_aligned", "")[:60].replace("|", "｜")
        lines.append(f"| `{s['name']}` | {s['source']} | {od}… | {ad}… |")

    lines += ["", f"*Router: openclaw infer model run (openai-codex/gpt-5.3-codex) — no OPENAI_API_KEY required*"]
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# 9. ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-embed", action="store_true",
                        help="Skip embedding router (LLM only)")
    parser.add_argument("--sample", type=int, default=0,
                        help="Use only N skills (0 = all eligible)")
    args = parser.parse_args()

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    print("=" * 60)
    print("Description Importance Experiment — OpenClaw Real Routing")
    print("=" * 60)

    # Verify openclaw is available
    ver = subprocess.run([OPENCLAW_BIN, "--version"], capture_output=True, text=True, timeout=10)
    if ver.returncode != 0:
        print(f"[ERROR] openclaw not found at: {OPENCLAW_BIN}")
        sys.exit(1)
    print(f"[openclaw] {ver.stdout.strip()}")

    skills = collect_installed_skills()
    if args.sample and args.sample < len(skills):
        random.shuffle(skills)
        skills = skills[:args.sample]
        print(f"[sample] Using {len(skills)} skills")

    raw_results = run_experiment(skills)

    # Save raw results
    out = RESULTS_DIR / "pilot_results_openclaw.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(raw_results, f, ensure_ascii=False, indent=2)
    print(f"\n[✓] Raw results → {out}  ({len(raw_results)} rows)")

    # Metrics
    metrics = compute_metrics(raw_results)
    with open(RESULTS_DIR / "summary_openclaw.json", "w") as f:
        json.dump(metrics, f, indent=2)

    # Report
    report = build_report(metrics, skills, raw_results)
    with open(RESULTS_DIR / "report_openclaw.md", "w") as f:
        f.write(report)

    # Terminal summary
    print("\n" + "=" * 60)
    print("RESULTS SUMMARY")
    print("=" * 60)
    for v in ["original", "vague", "aligned"]:
        vm = metrics.get(v, {})
        print(f"\n[{v.upper()}]")
        print(f"  LLM Recall@1    : {vm.get('llm_recall')}%")
        print(f"  LLM FTR         : {vm.get('llm_ftr')}%")
        if vm.get("emb_recall") is not None:
            print(f"  Embed Recall@1  : {vm.get('emb_recall')}%")
            print(f"  Embed FTR       : {vm.get('emb_ftr')}%")

    sens = metrics.get("sensitivity", {})
    print("\n[SENSITIVITY — Δ% from description swap, body constant]")
    print(f"  LLM  original→vague   : {sens.get('llm_orig_vs_vague')}%")
    print(f"  LLM  original→aligned : {sens.get('llm_orig_vs_aligned')}%")
    print(f"\n[✓] Report → {RESULTS_DIR / 'report_openclaw.md'}")


if __name__ == "__main__":
    main()
