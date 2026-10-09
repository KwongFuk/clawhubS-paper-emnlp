"""
Description Importance Experiment — Pilot 1
============================================
Research question:
  Does the skill **description** (not the body) determine whether a skill is correctly
  routed?  We fix the skill body and vary only the description, then measure routing
  accuracy with an embedding router and an LLM router.

Three description variants per skill
--------------------------------------
  original           : marketplace/frontmatter description as-is
  vague              : deliberately weakened ("Helps with productivity tasks.")
  capability_aligned : written to expose actual capabilities extracted from the
                       skillrugpull IC analysis

Routing methods
---------------
  R_embed  : cosine similarity between OpenAI text-embedding-3-small(query) and
              text-embedding-3-small(description); pick argmax over 20 candidates
  R_llm    : GPT-4o-mini selects the most relevant skill from descriptions of all
              20 candidates given the query

Query types per skill (4 total)
---------------------------------
  explicit           : directly names the domain / tool
  implicit           : describes need without domain name
  boundary           : looks like this skill but should route to a neighbor
  negative           : completely unrelated

Metrics
-------
  Recall@1 for explicit + implicit (should trigger)
  False-Trigger Rate for boundary + negative (should NOT trigger)
  Description Sensitivity = average |original_score - variant_score|

Output
------
  skill-X/experiments/desc_importance/results/
    pilot_results.json    -- row-per-decision
    summary_table.json    -- grouped aggregates
    report.md             -- human-readable narrative
"""

import json
import os
import sys
import re
import time
import hashlib
import sqlite3
import random
import argparse
from pathlib import Path
from typing import Any

# ── reproducibility ─────────────────────────────────────────────────────────
SEED = 42
random.seed(SEED)

# ── paths ─────────────────────────────────────────────────────────────────
REPO_ROOT   = Path(__file__).resolve().parents[3]          # clawhubS/
DB_PATH     = REPO_ROOT / "data/experiment1_split/merged.sqlite3"
RESULTS_DIR = REPO_ROOT / "data/experiment1_split"
ARCHIVE_API = REPO_ROOT / "data/collection_archive/remote_scratch_html_snapshots_shard1/api_files"
RESULTS_FILE_MAIN = Path(__file__).parent / "results/pilot_results.json"
RESULTS_FILE_SUM  = Path(__file__).parent / "results/summary_table.json"
REPORT_FILE       = Path(__file__).parent / "results/report.md"

IC_JSONL = REPO_ROOT / "experiments/skillrugpull/clawhub/results/main_experiment_results.jsonl"

# ── config ────────────────────────────────────────────────────────────────
N_SKILLS          = 20    # skills in the pilot
N_CANDIDATES      = 20    # library size for each routing decision
QUERIES_PER_SKILL = 8     # 2 positive (explicit+implicit) + 2 boundary + 2 negative + spare
EMBED_MODEL       = "text-embedding-3-small"
LLM_MODEL         = "gpt-4o-mini"
CACHE_DIR         = Path(__file__).parent / ".cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# ── OpenAI client ──────────────────────────────────────────────────────────
try:
    from openai import OpenAI
    client = OpenAI()  # reads OPENAI_API_KEY from environment
except Exception as exc:
    print(f"[ERROR] Cannot create OpenAI client: {exc}")
    print("Set OPENAI_API_KEY before running this script.")
    sys.exit(1)

# ─────────────────────────────────────────────────────────────────────────────
# 1.  LOAD IC ANALYSIS DATA
# ─────────────────────────────────────────────────────────────────────────────

def load_ic_data() -> list[dict]:
    """Load the skillrugpull main experiment results."""
    records = []
    with open(IC_JSONL) as f:
        for line in f:
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    print(f"[load_ic_data] Loaded {len(records):,} records from IC analysis")
    return records


def load_skill_body(skill_id: str) -> str | None:
    """
    Try to load the SKILL.md body from the local archive.
    Archive filenames use the SHORT slug only (no author prefix).
    Pattern: api__v1__skills__<slug>__file__path_3DSKILL.md_<version>__<hash>.md
    Returns the body of the *latest* version found, or None.
    """
    slug = skill_id.split("/")[-1]  # e.g. "triple-layer-memory"
    pattern = f"api__v1__skills__{slug}__file__path_3DSKILL.md_*.md"
    matches = sorted(ARCHIVE_API.glob(pattern))  # sort → take last = newest
    for f in reversed(matches):
        try:
            return f.read_text(encoding="utf-8")
        except Exception:
            continue
    return None


# ─────────────────────────────────────────────────────────────────────────────
# 2.  SELECT 20 PILOT SKILLS
# ─────────────────────────────────────────────────────────────────────────────

def _build_archive_slug_index() -> dict[str, Path]:
    """Build slug → newest SKILL.md path index from archive (computed once)."""
    index: dict[str, Path] = {}
    for f in ARCHIVE_API.glob("api__v1__skills__*__file__path_3DSKILL.md_*.md"):
        parts = f.name.split("__")
        if len(parts) >= 4:
            slug = parts[3]
            # keep newest (sort lexicographically; version string in filename)
            if slug not in index or str(f) > str(index[slug]):
                index[slug] = f
    return index


_ARCHIVE_INDEX: dict[str, Path] | None = None


def load_skill_body(skill_id: str) -> str | None:
    """
    Load the newest SKILL.md body from the local archive for this skill.
    Archive filenames use the SHORT slug only (no author prefix).
    """
    global _ARCHIVE_INDEX
    if _ARCHIVE_INDEX is None:
        print("[load_skill_body] Building archive index …")
        _ARCHIVE_INDEX = _build_archive_slug_index()
        print(f"[load_skill_body] Index size: {len(_ARCHIVE_INDEX)} slugs")

    slug = skill_id.split("/")[-1]
    path = _ARCHIVE_INDEX.get(slug)
    if path is None:
        return None
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return None


def select_pilot_skills(ic_records: list[dict], n: int = N_SKILLS) -> list[dict]:
    """
    Pick n skills that:
      - have a non-trivial English/mixed description (> 20 chars)
      - have c_actual capabilities (so we know what they really do)
      - have SKILL.md body retrievable on disk
      - span different ic_types
    """
    # Pre-build archive index so it's only done once
    global _ARCHIVE_INDEX
    if _ARCHIVE_INDEX is None:
        _ARCHIVE_INDEX = _build_archive_slug_index()

    candidates = [
        r for r in ic_records
        if r.get("description") and len(r["description"]) > 20
        and r.get("c_actual")
        and r.get("ic_type") in ("IC-1", "IC-2", "IC-4", "TN")
        and r["skill_id"].split("/")[-1] in _ARCHIVE_INDEX
    ]
    print(f"[select_pilot_skills] {len(candidates)} candidates with body on disk")

    # bias toward IC-1 (high capability gap) — 60 % of sample
    ic1  = [r for r in candidates if r["ic_type"] == "IC-1"]
    rest = [r for r in candidates if r["ic_type"] != "IC-1"]
    random.shuffle(ic1)
    random.shuffle(rest)

    pool = ic1[: int(n * 0.6)] + rest[: n - int(n * 0.6)]
    random.shuffle(pool)
    pool = pool[:n]

    selected = []
    for r in pool:
        body = load_skill_body(r["skill_id"])
        if body:
            r = dict(r)  # don't mutate original
            r["_body"] = body
            selected.append(r)

    print(f"[select_pilot_skills] Selected {len(selected)} skills")
    for s in selected:
        print(f"  {s['skill_id']}  ic={s['ic_type']}  caps={s['c_actual']}")
    return selected


# ─────────────────────────────────────────────────────────────────────────────
# 3.  DESCRIPTION VARIANTS
# ─────────────────────────────────────────────────────────────────────────────

_VAGUE_TEMPLATES = [
    "Helps with productivity tasks.",
    "Assists with common workflow operations.",
    "A general-purpose skill for various tasks.",
    "Use when you need help with everyday tasks.",
    "Provides support for multiple operations.",
]

def make_vague_description(skill_id: str) -> str:
    """Return a deliberately vague description (deterministic per skill)."""
    idx = int(hashlib.md5(skill_id.encode()).hexdigest(), 16) % len(_VAGUE_TEMPLATES)
    return _VAGUE_TEMPLATES[idx]


def make_capability_aligned_description(record: dict) -> str:
    """
    Generate a capability-aligned description via GPT-4o-mini.
    Uses the original description + c_actual capabilities + SKILL.md body snippet.
    """
    cache_key = hashlib.sha256(
        (record["skill_id"] + json.dumps(record.get("c_actual", []))).encode()
    ).hexdigest()[:16]
    cache_file = CACHE_DIR / f"aligned_{cache_key}.txt"
    if cache_file.exists():
        return cache_file.read_text()

    body_snippet = record.get("_body", "")[:1500]  # first 1500 chars of SKILL.md
    capabilities = record.get("c_actual", [])
    original_desc = record.get("description", "")

    prompt = f"""You are writing a routing card for an AI skill in a marketplace.
The skill is: {record['skill_id']}

Original marketplace description (may be vague or inaccurate):
{original_desc}

Actual capabilities detected from the skill's implementation:
{', '.join(capabilities)}

First 1500 characters of SKILL.md body:
{body_snippet}

Write a capability-aligned description (2-4 sentences) that:
1. Accurately states what the skill can do (based on actual capabilities)
2. States when to use it (trigger conditions)
3. States what it CANNOT do or its boundaries
4. Uses concrete verbs/nouns, not marketing language

Output ONLY the description text, no headings, no bullets."""

    response = client.chat.completions.create(
        model=LLM_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3,
        max_tokens=200,
    )
    desc = response.choices[0].message.content.strip()
    cache_file.write_text(desc)
    return desc


# ─────────────────────────────────────────────────────────────────────────────
# 4.  QUERY GENERATION
# ─────────────────────────────────────────────────────────────────────────────

def generate_queries(record: dict) -> list[dict]:
    """
    Generate 8 queries for a skill via GPT-4o-mini (cached).
    Returns list of {query, query_type, gold_skill_id}
    """
    cache_key = hashlib.sha256(record["skill_id"].encode()).hexdigest()[:16]
    cache_file = CACHE_DIR / f"queries_{cache_key}.json"
    if cache_file.exists():
        return json.loads(cache_file.read_text())

    body_snippet = record.get("_body", "")[:1500]
    capabilities = record.get("c_actual", [])
    skill_name   = record["skill_id"].split("/")[-1]

    prompt = f"""You are building a routing benchmark for the skill: {record['skill_id']}
Skill description: {record['description']}
Actual capabilities: {', '.join(capabilities)}
SKILL.md excerpt: {body_snippet[:800]}

Generate exactly 8 test queries in JSON array format. Each object must have:
  "query": the user request string
  "query_type": one of [explicit, implicit, boundary, negative]
  "should_trigger": true if this skill SHOULD handle the query, false if it should NOT

Rules:
- 2 x explicit: user clearly asks for what this skill does (should_trigger=true)
- 2 x implicit: user describes the need indirectly without skill name (should_trigger=true)
- 2 x boundary: query looks LIKE this skill but should be handled by a different skill (should_trigger=false)
- 2 x negative: completely unrelated query (should_trigger=false)

Output ONLY valid JSON array, no markdown wrapping."""

    response = client.chat.completions.create(
        model=LLM_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.5,
        max_tokens=600,
    )
    raw = response.choices[0].message.content.strip()
    # Strip possible markdown code fences
    raw = re.sub(r"^```(?:json)?\s*", "", raw).rstrip("`").strip()
    try:
        queries = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"  [WARN] Query parse error for {record['skill_id']}: {e}")
        queries = []

    # Attach gold label
    for q in queries:
        q["gold_skill_id"] = record["skill_id"] if q.get("should_trigger") else None

    cache_file.write_text(json.dumps(queries, ensure_ascii=False, indent=2))
    return queries


# ─────────────────────────────────────────────────────────────────────────────
# 5.  EMBEDDING + LLM ROUTING
# ─────────────────────────────────────────────────────────────────────────────

_embed_cache: dict[str, list[float]] = {}

def embed(text: str) -> list[float]:
    """Embed text with OpenAI text-embedding-3-small (in-memory cache)."""
    key = hashlib.sha256(text.encode()).hexdigest()
    if key in _embed_cache:
        return _embed_cache[key]
    cache_file = CACHE_DIR / f"emb_{key[:24]}.json"
    if cache_file.exists():
        vec = json.loads(cache_file.read_text())
        _embed_cache[key] = vec
        return vec
    response = client.embeddings.create(model=EMBED_MODEL, input=text)
    vec = response.data[0].embedding
    _embed_cache[key] = vec
    cache_file.write_text(json.dumps(vec))
    return vec


def cosine_sim(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na  = sum(x * x for x in a) ** 0.5
    nb  = sum(x * x for x in b) ** 0.5
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def route_embed(query: str, library: list[dict], desc_key: str) -> str:
    """Return skill_id of highest cosine similarity skill."""
    qvec = embed(query)
    best_id, best_score = None, -1.0
    for skill in library:
        dvec = embed(skill[desc_key])
        score = cosine_sim(qvec, dvec)
        if score > best_score:
            best_score = score
            best_id = skill["skill_id"]
    return best_id


def route_llm(query: str, library: list[dict], desc_key: str) -> str:
    """LLM chooses the best skill from a numbered list of descriptions."""
    cache_key = hashlib.sha256(
        (query + desc_key + json.dumps([s["skill_id"] for s in library])).encode()
    ).hexdigest()[:20]
    cache_file = CACHE_DIR / f"llm_route_{cache_key}.txt"
    if cache_file.exists():
        return cache_file.read_text().strip()

    numbered = "\n".join(
        f"{i+1}. [{s['skill_id']}] {s[desc_key]}"
        for i, s in enumerate(library)
    )
    prompt = f"""You are a skill router for an AI agent. Given the user request below, select the SINGLE most relevant skill from the list.

User request: {query}

Skill candidates:
{numbered}

Reply with ONLY the skill_id of the best match (the text in square brackets).
If none are relevant, reply with: NONE"""

    response = client.chat.completions.create(
        model=LLM_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.0,
        max_tokens=80,
    )
    answer = response.choices[0].message.content.strip()
    # extract skill_id from brackets if LLM included them
    m = re.search(r"\[([^\]]+)\]", answer)
    result = m.group(1) if m else answer.split()[0] if answer.split() else "NONE"
    cache_file.write_text(result)
    return result


# ─────────────────────────────────────────────────────────────────────────────
# 6.  MAIN EXPERIMENT LOOP
# ─────────────────────────────────────────────────────────────────────────────

def run_experiment(skills: list[dict]) -> list[dict]:
    """Run routing evaluation across all skills, descriptions, and routers."""
    results = []

    # Build description variants for each skill first (cache-friendly)
    print("\n[Phase 1] Building description variants …")
    for skill in skills:
        skill["desc_original"]  = skill["description"]
        skill["desc_vague"]     = make_vague_description(skill["skill_id"])
        print(f"  → generating aligned desc for {skill['skill_id']} …")
        skill["desc_aligned"]   = make_capability_aligned_description(skill)

    # Build query sets
    print("\n[Phase 2] Generating query sets …")
    for skill in skills:
        print(f"  → generating queries for {skill['skill_id']} …")
        skill["_queries"] = generate_queries(skill)

    # Build the routing library (20 candidates drawn from pilot skills)
    # Each decision: the correct skill + 19 distractors
    print("\n[Phase 3] Running routing evaluations …")
    skill_ids = [s["skill_id"] for s in skills]

    for focal in skills:
        distractors = [s for s in skills if s["skill_id"] != focal["skill_id"]]
        # ensure library has exactly N_CANDIDATES (may be < 20 if pilot is small)
        library_pool = [focal] + distractors

        for q in focal.get("_queries", []):
            for variant in ["original", "vague", "aligned"]:
                desc_key = f"desc_{variant}"

                # Build library view for this variant
                lib_view = [
                    {"skill_id": s["skill_id"], desc_key: s[desc_key]}
                    for s in library_pool
                ]

                # Embedding router
                embed_choice = route_embed(q["query"], lib_view, desc_key)

                # LLM router
                llm_choice = route_llm(q["query"], lib_view, desc_key)

                gold = q.get("gold_skill_id")  # None means "should NOT trigger"
                should_trigger = q.get("should_trigger", False)

                # Correctness logic
                if should_trigger:
                    embed_correct = (embed_choice == focal["skill_id"])
                    llm_correct   = (llm_choice   == focal["skill_id"])
                else:
                    # should NOT trigger focal skill
                    embed_correct = (embed_choice != focal["skill_id"])
                    llm_correct   = (llm_choice   != focal["skill_id"])

                row = {
                    "skill_id"       : focal["skill_id"],
                    "ic_type"        : focal["ic_type"],
                    "c_actual"       : focal.get("c_actual", []),
                    "query"          : q["query"],
                    "query_type"     : q.get("query_type"),
                    "should_trigger" : should_trigger,
                    "gold_skill_id"  : gold,
                    "desc_variant"   : variant,
                    "desc_original"  : focal["desc_original"],
                    "desc_used"      : focal[desc_key],
                    "embed_choice"   : embed_choice,
                    "llm_choice"     : llm_choice,
                    "embed_correct"  : embed_correct,
                    "llm_correct"    : llm_correct,
                }
                results.append(row)

        print(f"  ✓ {focal['skill_id']} — {len(focal.get('_queries',[]))} queries × 3 variants × 2 routers")

    return results


# ─────────────────────────────────────────────────────────────────────────────
# 7.  METRICS + REPORTING
# ─────────────────────────────────────────────────────────────────────────────

def compute_metrics(results: list[dict]) -> dict[str, Any]:
    """Compute per-variant metrics."""
    variants = ["original", "vague", "aligned"]
    metrics  = {}

    for v in variants:
        rows = [r for r in results if r["desc_variant"] == v]
        pos  = [r for r in rows if r["should_trigger"]]
        neg  = [r for r in rows if not r["should_trigger"]]

        def pct(correct_key, subset):
            if not subset:
                return None
            return round(sum(r[correct_key] for r in subset) / len(subset) * 100, 2)

        metrics[v] = {
            "n_total"              : len(rows),
            "n_positive"           : len(pos),
            "n_negative"           : len(neg),
            # Recall = fraction of should-trigger queries routed correctly
            "embed_recall"         : pct("embed_correct", pos),
            "llm_recall"           : pct("llm_correct",   pos),
            # Specificity = fraction of should-not-trigger queries correctly NOT chosen
            "embed_specificity"    : pct("embed_correct", neg),
            "llm_specificity"      : pct("llm_correct",   neg),
            # False trigger rate = 1 - specificity
            "embed_false_trigger"  : round(100 - (pct("embed_correct", neg) or 0), 2),
            "llm_false_trigger"    : round(100 - (pct("llm_correct",   neg) or 0), 2),
        }

    # Description sensitivity: for positive queries compare original vs others
    def sensitivity(results, router_key, v1, v2):
        """Average |correct_v1 - correct_v2| across queries (per skill)."""
        skill_ids = list({r["skill_id"] for r in results})
        diffs = []
        for sid in skill_ids:
            pos_v1 = [r for r in results if r["skill_id"] == sid and r["desc_variant"] == v1 and r["should_trigger"]]
            pos_v2 = [r for r in results if r["skill_id"] == sid and r["desc_variant"] == v2 and r["should_trigger"]]
            if not pos_v1 or not pos_v2:
                continue
            acc1 = sum(r[router_key] for r in pos_v1) / len(pos_v1)
            acc2 = sum(r[router_key] for r in pos_v2) / len(pos_v2)
            diffs.append(abs(acc1 - acc2))
        return round(sum(diffs) / len(diffs) * 100, 2) if diffs else None

    metrics["sensitivity"] = {
        "embed_original_vs_vague"   : sensitivity(results, "embed_correct", "original", "vague"),
        "embed_original_vs_aligned" : sensitivity(results, "embed_correct", "original", "aligned"),
        "llm_original_vs_vague"     : sensitivity(results, "llm_correct",   "original", "vague"),
        "llm_original_vs_aligned"   : sensitivity(results, "llm_correct",   "original", "aligned"),
    }

    return metrics


def make_report(metrics: dict, skills: list[dict]) -> str:
    """Generate a markdown report."""
    m = metrics
    sens = m.get("sensitivity", {})

    lines = [
        "# Description Importance Pilot — Results",
        "",
        f"**Skills evaluated:** {len(skills)}  |  **Queries per skill:** {QUERIES_PER_SKILL}  |  **Total routing decisions:** {m.get('original',{}).get('n_total', '?') * 3}",
        "",
        "## Routing Recall (positive queries — should trigger)",
        "",
        "| Description Variant | Embed Recall@1 | LLM Recall@1 |",
        "|--------------------|----------------|--------------|",
    ]
    for v in ["original", "vague", "aligned"]:
        vm = m.get(v, {})
        lines.append(f"| {v:<19} | {vm.get('embed_recall','N/A'):>14} | {vm.get('llm_recall','N/A'):>12} |")

    lines += [
        "",
        "## False-Trigger Rate (negative / boundary queries — should NOT trigger)",
        "",
        "| Description Variant | Embed FTR | LLM FTR |",
        "|--------------------|-----------|---------|",
    ]
    for v in ["original", "vague", "aligned"]:
        vm = m.get(v, {})
        lines.append(f"| {v:<19} | {vm.get('embed_false_trigger','N/A'):>9} | {vm.get('llm_false_trigger','N/A'):>7} |")

    lines += [
        "",
        "## Description Sensitivity (Δ% in routing accuracy from changing description)",
        "",
        "| Comparison | Embed Δ% | LLM Δ% |",
        "|-----------|----------|--------|",
        f"| original → vague   | {sens.get('embed_original_vs_vague','N/A'):>8} | {sens.get('llm_original_vs_vague','N/A'):>6} |",
        f"| original → aligned | {sens.get('embed_original_vs_aligned','N/A'):>8} | {sens.get('llm_original_vs_aligned','N/A'):>6} |",
        "",
        "## Key Finding",
        "",
        "If aligned > original > vague on Recall AND aligned < vague on FTR,",
        "this confirms **description is the dominant routing signal**.",
        "(The skill body is held constant across all three variants.)",
        "",
        "## Skills in Pilot",
        "",
        "| skill_id | ic_type | c_actual | original desc (truncated) |",
        "|---------|---------|---------|--------------------------|",
    ]
    for s in skills:
        desc_short = s.get("description", "")[:60].replace("|", "｜")
        caps = ",".join(s.get("c_actual", []))
        lines.append(f"| `{s['skill_id']}` | {s['ic_type']} | {caps} | {desc_short}… |")

    lines += ["", f"*Generated by run_desc_importance.py — model={LLM_MODEL}, embed={EMBED_MODEL}*"]
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# 8.  ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Description Importance Experiment")
    parser.add_argument("--n-skills",    type=int, default=N_SKILLS,
                        help=f"Number of skills to evaluate (default {N_SKILLS})")
    parser.add_argument("--skip-llm",   action="store_true",
                        help="Skip LLM router (embedding only, cheaper)")
    args = parser.parse_args()

    out_dir = Path(__file__).parent / "results"
    out_dir.mkdir(parents=True, exist_ok=True)

    # ── Load data ──────────────────────────────────────────────────────────
    print("=" * 60)
    print("Skill Description Importance Experiment — Pilot 1")
    print("=" * 60)
    ic_records = load_ic_data()
    skills = select_pilot_skills(ic_records, n=args.n_skills)

    if not skills:
        print("[ERROR] No skills found with body on disk. Check archive path.")
        sys.exit(1)

    # ── Run experiment ──────────────────────────────────────────────────────
    raw_results = run_experiment(skills)

    # Save raw
    with open(out_dir / "pilot_results.json", "w", encoding="utf-8") as f:
        json.dump(raw_results, f, ensure_ascii=False, indent=2)
    print(f"\n[✓] Raw results saved → {out_dir / 'pilot_results.json'}  ({len(raw_results)} rows)")

    # ── Metrics ─────────────────────────────────────────────────────────────
    metrics = compute_metrics(raw_results)
    with open(out_dir / "summary_table.json", "w", encoding="utf-8") as f:
        json.dump(metrics, f, indent=2)

    # ── Report ──────────────────────────────────────────────────────────────
    report_md = make_report(metrics, skills)
    with open(out_dir / "report.md", "w", encoding="utf-8") as f:
        f.write(report_md)

    # Print summary to terminal
    print("\n" + "=" * 60)
    print("RESULTS SUMMARY")
    print("=" * 60)
    for v in ["original", "vague", "aligned"]:
        vm = metrics.get(v, {})
        print(f"\n[{v.upper()}]")
        print(f"  Embed Recall@1    : {vm.get('embed_recall')}%")
        print(f"  LLM   Recall@1    : {vm.get('llm_recall')}%")
        print(f"  Embed False-Trigger: {vm.get('embed_false_trigger')}%")
        print(f"  LLM   False-Trigger: {vm.get('llm_false_trigger')}%")

    sens = metrics.get("sensitivity", {})
    print("\n[DESCRIPTION SENSITIVITY]")
    print(f"  Embed: original→vague   {sens.get('embed_original_vs_vague')}%")
    print(f"  Embed: original→aligned {sens.get('embed_original_vs_aligned')}%")
    print(f"  LLM:   original→vague   {sens.get('llm_original_vs_vague')}%")
    print(f"  LLM:   original→aligned {sens.get('llm_original_vs_aligned')}%")

    print(f"\n[✓] Full report → {out_dir / 'report.md'}")


if __name__ == "__main__":
    main()
