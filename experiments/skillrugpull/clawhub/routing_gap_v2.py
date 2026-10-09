#!/usr/bin/env python3
"""
IC-1 Routing Gap Experiment (v2)
=================================
Shows that description quality causally impacts routing recall for real user queries.

Pipeline:
  1. Load IC-1 skills from cap_alignment pilot (skills with C_undisclosed ≠ ∅)
  2. For each skill, LLM generates 5 query types per undisclosed capability:
       desc_direct   – query about a DISCLOSED capability (control group)
       body_direct   – explicit query about undisclosed cap
       body_symptom  – symptom/problem framing of undisclosed cap
       body_goal     – goal/outcome framing of undisclosed cap
       body_context  – realistic project-context framing of undisclosed cap
  3. Route each query with original vs complete description
  4. Report Recall by variant × query type

Expected: desc_direct ~100% for both; body_* shows gap of 30-50%

Scale: ~20 IC-1 skills × 3 undisclosed caps × 5 query types × 2 variants = ~600 decisions
"""

import json, re, sqlite3, subprocess, time
from pathlib import Path
from collections import defaultdict

# ── Paths ──────────────────────────────────────────────────────────────────────
PILOT_FILE = Path(__file__).parent / "results/cap_alignment_v2/pilot_50.jsonl"
DB         = "/Users/guangfu/codex/clawhubS/data/skills_sh/skills_sh.sqlite3"
OUTDIR     = Path(__file__).parent / "results/routing_gap_v2"
QUERIES_F  = OUTDIR / "queries_gap.json"
RESULTS_F  = OUTDIR / "results_gap.jsonl"
REPORT_F   = OUTDIR / "report_gap.json"
OUTDIR.mkdir(parents=True, exist_ok=True)

MAX_SKILLS   = 40   # IC-1 skills to use
MIN_UNDISCLOSED = 2 # min C_undisclosed to be useful
CAPS_PER_SKILL  = 3 # undisclosed caps to generate queries for
DISTRACTORS  = 19   # size of distractor pool per routing decision

# ── LLM call ──────────────────────────────────────────────────────────────────

def llm(prompt: str, retries: int = 3) -> str | None:
    for _ in range(retries):
        try:
            r = subprocess.run(
                ["openclaw", "infer", "model", "run", "--prompt", prompt, "--json"],
                capture_output=True, text=True, timeout=120)
            text = json.loads(r.stdout).get("outputs", [{}])[0].get("text", "").strip()
            return text
        except subprocess.TimeoutExpired:
            time.sleep(5)
        except Exception:
            pass
    return None

def llm_json(prompt: str) -> dict | list | None:
    text = llm(prompt)
    if not text: return None
    for part in text.split("```"):
        part = part.strip().lstrip("json").strip()
        try: return json.loads(part)
        except: pass
    try: return json.loads(text)
    except: return None

def safe_prompt(template: str, **kwargs) -> str:
    result = template
    for k, v in kwargs.items():
        result = result.replace('{' + k + '}', str(v))
    return result

# ── Step 1: Load IC-1 skills from pilot ───────────────────────────────────────

def load_ic1_skills() -> list[dict]:
    skills = []
    if not PILOT_FILE.exists():
        raise FileNotFoundError(f"Pilot file not found: {PILOT_FILE}")
    for line in PILOT_FILE.read_text().strip().split('\n'):
        try:
            r = json.loads(line)
            if 'error' in r: continue
            # only_body = capabilities in body but NOT in desc (IC-1 gap)
            undisclosed = r.get('only_body', [])
            if len(undisclosed) >= MIN_UNDISCLOSED:
                r['undisclosed'] = undisclosed   # normalise field name
                skills.append(r)
        except: pass
    # Sort by n undisclosed desc
    skills.sort(key=lambda x: -len(x.get('undisclosed', [])))
    return skills[:MAX_SKILLS]

# ── Step 2: Load full descriptions from DB ─────────────────────────────────────

def load_descriptions(slugs: list[str]) -> dict[str, dict]:
    db = sqlite3.connect(DB)
    cur = db.cursor()
    placeholders = ','.join('?' * len(slugs))
    cur.execute(f"""
        SELECT skill_slug, description, skill_text
        FROM skills
        WHERE skill_slug IN ({placeholders})
    """, slugs)
    result = {row[0]: {'description': row[1], 'body': row[2]}
              for row in cur.fetchall()}
    db.close()
    return result

# ── Step 3: Generate queries ───────────────────────────────────────────────────

QUERY_GEN_PROMPT = """\
Generate routing queries for an AI skill named "{slug}".

Skill description (original, may be incomplete):
{description}

DISCLOSED capabilities (mentioned in description):
{c_desc}

UNDISCLOSED capability to query about: "{undisclosed_cap}"
(This is a capability the skill actually has but the description doesn't mention.)

ALSO disclosed sample (for control query): "{disclosed_cap}"

Generate exactly 5 queries as realistic user messages. A user would type these into
a chat interface to get help. Make them natural and varied — not keyword searches.

Query types:
1. desc_direct   – asks about the DISCLOSED capability "{disclosed_cap}" (control)
2. body_direct   – directly asks for help with "{undisclosed_cap}"
3. body_symptom  – describes a problem/symptom that requires "{undisclosed_cap}" to solve
4. body_goal     – describes a goal/outcome that needs "{undisclosed_cap}"
5. body_context  – describes a realistic project scenario where "{undisclosed_cap}" is needed

Rules:
- body_* queries must NOT use the exact phrase from the skill name or description
- Each query 15-40 words, first person, realistic user language
- No overlap between queries

Return ONLY valid JSON:
{{
    "desc_direct":   "...",
    "body_direct":   "...",
    "body_symptom":  "...",
    "body_goal":     "...",
    "body_context":  "..."
}}"""

def generate_queries_for_skill(slug: str, description: str,
                                c_desc: list, undisclosed_caps: list) -> list[dict]:
    queries = []
    disclosed_cap = c_desc[0] if c_desc else "core functionality"
    for cap in undisclosed_caps[:CAPS_PER_SKILL]:
        result = llm_json(safe_prompt(
            QUERY_GEN_PROMPT,
            slug=slug,
            description=description,
            c_desc=json.dumps(c_desc),
            undisclosed_cap=cap,
            disclosed_cap=disclosed_cap,
        ))
        if not result or not isinstance(result, dict):
            print(f"    [WARN] query gen failed for cap: {cap}")
            continue
        for qtype, q in result.items():
            if isinstance(q, str) and len(q) > 10:
                queries.append({
                    "slug": slug,
                    "undisclosed_cap": cap,
                    "query_type": qtype,
                    "query": q,
                    # expected: body_* should fail on original, succeed on complete
                    "should_trigger": True,
                    "is_control": qtype == "desc_direct",
                    "is_body_trigger": qtype.startswith("body_"),
                })
    return queries

# ── Step 4: Build complete description ────────────────────────────────────────

COMPLETE_DESC_PROMPT = """\
Rewrite this skill description to include ALL the listed capabilities.
Keep the same tone and length (add 1-2 sentences if needed).
Do NOT change existing correct information.

Original description:
{description}

Capabilities to ADD (currently undisclosed):
{undisclosed}

Return ONLY the improved description text, no JSON, no explanation."""

def make_complete_description(description: str, undisclosed: list) -> str:
    result = llm(safe_prompt(
        COMPLETE_DESC_PROMPT,
        description=description,
        undisclosed=json.dumps(undisclosed),
    ))
    return result.strip() if result else description

# ── Step 5: Build routing library ─────────────────────────────────────────────

def build_library(focal_slug: str, focal_desc: str, all_slugs: list[str],
                   db_descs: dict) -> list[dict]:
    """focal skill + DISTRACTORS distractors from other skills."""
    library = [{"slug": focal_slug, "description": focal_desc}]
    distractors = [{"slug": s, "description": db_descs[s]['description']}
                   for s in all_slugs if s != focal_slug][:DISTRACTORS]
    return library + distractors

# ── Step 6: Route one query ────────────────────────────────────────────────────

ROUTE_PROMPT = """\
You are an AI agent routing dispatcher. Given a user query and a list of available skills,
select the most appropriate skill to handle the query.
If no skill is relevant, respond with "none".

Available skills:
{skill_list}

User query: "{query}"

Respond with ONLY the skill slug (e.g., "task-runner") or "none". No explanation."""

def route_query(query: str, library: list[dict]) -> str:
    skill_list = "\n".join(
        f'- {s["slug"]}: {s["description"][:150]}' for s in library)
    result = llm(safe_prompt(ROUTE_PROMPT, skill_list=skill_list, query=query))
    if not result: return "error"
    # Extract skill slug from response
    result = result.strip().lower().strip('"\'`')
    for skill in library:
        if skill['slug'].lower() in result:
            return skill['slug']
    if 'none' in result: return 'none'
    return result

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("=== IC-1 Routing Gap Experiment v2 ===\n")

    # Load IC-1 skills
    ic1_skills = load_ic1_skills()
    print(f"IC-1 skills loaded: {len(ic1_skills)}")
    for s in ic1_skills[:5]:
        print(f"  {s['slug']:40s}  undisclosed={len(s['undisclosed'])}  {s['undisclosed'][:2]}")

    all_slugs = [s['slug'] for s in ic1_skills]
    db_descs  = load_descriptions(all_slugs)
    print(f"\nDB descriptions loaded: {len(db_descs)}/{len(all_slugs)}")

    # ── Step A: Generate queries ──────────────────────────────────────────────
    if QUERIES_F.exists():
        print(f"\nLoading existing queries from {QUERIES_F.name}...")
        all_queries = json.loads(QUERIES_F.read_text())
        print(f"  {len(all_queries)} queries loaded")
    else:
        print("\n── Generating queries ──")
        all_queries = []
        for skill in ic1_skills:
            slug = skill['slug']
            if slug not in db_descs:
                print(f"  [SKIP] {slug}: not in DB")
                continue
            desc = db_descs[slug]['description']
            c_desc = skill.get('c_desc', [])
            undisclosed = skill.get('undisclosed', [])
            print(f"  {slug}  ({len(undisclosed)} undisclosed, using {min(len(undisclosed), CAPS_PER_SKILL)})")
            qs = generate_queries_for_skill(slug, desc, c_desc, undisclosed)
            all_queries.extend(qs)
            print(f"    → {len(qs)} queries generated")
            time.sleep(0.3)
        QUERIES_F.write_text(json.dumps(all_queries, indent=2, ensure_ascii=False))
        print(f"\n→ {len(all_queries)} queries saved to {QUERIES_F.name}")

    # ── Step B: Build complete descriptions ───────────────────────────────────
    print("\n── Building complete descriptions ──")
    complete_descs: dict[str, str] = {}
    for skill in ic1_skills:
        slug = skill['slug']
        if slug not in db_descs: continue
        orig = db_descs[slug]['description']
        undisclosed = skill.get('undisclosed', [])
        print(f"  {slug}:", end=' ', flush=True)
        complete_descs[slug] = make_complete_description(orig, undisclosed)
        print(f"{len(orig)} → {len(complete_descs[slug])} chars")
        time.sleep(0.2)

    # ── Step C: Run routing experiment ────────────────────────────────────────
    print("\n── Running routing experiment ──")
    done_keys = set()
    existing_results = []
    if RESULTS_F.exists():
        for line in RESULTS_F.read_text().strip().split('\n'):
            try:
                r = json.loads(line)
                done_keys.add((r['slug'], r['variant'], r['query_type'],
                               r['undisclosed_cap'], r['query'][:30]))
                existing_results.append(r)
            except: pass
    print(f"Already done: {len(done_keys)} decisions")

    total = len(all_queries) * 2  # original + complete
    done_count = len(done_keys)

    with open(RESULTS_F, 'a') as out_f:
        for qi, q_rec in enumerate(all_queries):
            slug = q_rec['slug']
            if slug not in db_descs or slug not in complete_descs:
                continue

            for variant, desc_fn in [
                ('original', db_descs[slug]['description']),
                ('complete', complete_descs[slug]),
            ]:
                key = (slug, variant, q_rec['query_type'],
                       q_rec['undisclosed_cap'], q_rec['query'][:30])
                if key in done_keys:
                    continue

                # Build library for this skill
                library = build_library(slug, desc_fn, all_slugs, db_descs)

                chosen = route_query(q_rec['query'], library)
                correct = (chosen == slug)
                done_count += 1

                rec = {
                    "slug":           slug,
                    "variant":        variant,
                    "query_type":     q_rec['query_type'],
                    "undisclosed_cap":q_rec['undisclosed_cap'],
                    "query":          q_rec['query'],
                    "chosen":         chosen,
                    "correct":        correct,
                    "is_control":     q_rec['is_control'],
                    "is_body_trigger":q_rec['is_body_trigger'],
                }
                existing_results.append(rec)
                out_f.write(json.dumps(rec, ensure_ascii=False) + '\n')
                out_f.flush()

                mark = '✓' if correct else '✗'
                print(f"  [{done_count}/{total}] {slug:30s} [{variant:8s}] "
                      f"{q_rec['query_type']:15s} {mark}  chosen={chosen}")
                time.sleep(0.2)

    # ── Step D: Report ────────────────────────────────────────────────────────
    results = existing_results
    print("\n" + "=" * 70)
    print("RESULTS\n")

    # By variant × query_type
    qt_v = defaultdict(lambda: defaultdict(lambda: {'c': 0, 't': 0}))
    for r in results:
        qt_v[r['query_type']][r['variant']]['t'] += 1
        if r['correct']: qt_v[r['query_type']][r['variant']]['c'] += 1

    print(f"{'Query type':20s}  {'original':>10s}  {'complete':>10s}  {'gap':>8s}")
    print("-" * 58)
    for qt in ['desc_direct', 'body_direct', 'body_symptom', 'body_goal', 'body_context']:
        orig = qt_v[qt]['original']
        comp = qt_v[qt]['complete']
        if not orig['t']: continue
        oa = orig['c'] / max(orig['t'], 1) * 100
        ca = comp['c'] / max(comp['t'], 1) * 100
        print(f"  {qt:18s}  {oa:9.1f}%  {ca:9.1f}%  {ca-oa:+7.1f}%")

    # Overall body_trigger impact
    body_orig = sum(r['correct'] for r in results if r['is_body_trigger'] and r['variant']=='original')
    body_orig_t = sum(1 for r in results if r['is_body_trigger'] and r['variant']=='original')
    body_comp = sum(r['correct'] for r in results if r['is_body_trigger'] and r['variant']=='complete')
    body_comp_t = sum(1 for r in results if r['is_body_trigger'] and r['variant']=='complete')

    if body_orig_t:
        print(f"\n  body_trigger TOTAL  {body_orig/body_orig_t*100:.1f}%  →  {body_comp/body_comp_t*100:.1f}%"
              f"  gap={body_comp/body_comp_t*100 - body_orig/body_orig_t*100:+.1f}%")

    # Save report
    report = {
        "by_query_type": {
            qt: {
                "original": f"{qt_v[qt]['original']['c']/max(qt_v[qt]['original']['t'],1)*100:.1f}%",
                "complete": f"{qt_v[qt]['complete']['c']/max(qt_v[qt]['complete']['t'],1)*100:.1f}%",
                "gap": f"{qt_v[qt]['complete']['c']/max(qt_v[qt]['complete']['t'],1)*100 - qt_v[qt]['original']['c']/max(qt_v[qt]['original']['t'],1)*100:+.1f}%",
                "n": qt_v[qt]['original']['t'],
            } for qt in ['desc_direct','body_direct','body_symptom','body_goal','body_context']
        },
        "body_trigger_recall": {
            "original": round(body_orig/max(body_orig_t,1)*100, 1),
            "complete": round(body_comp/max(body_comp_t,1)*100, 1),
            "gap":      round(body_comp/max(body_comp_t,1)*100 - body_orig/max(body_orig_t,1)*100, 1),
            "n": body_orig_t,
        }
    }
    REPORT_F.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    print(f"\n→ Results: {RESULTS_F}")
    print(f"→ Report:  {REPORT_F}")

if __name__ == "__main__":
    main()
