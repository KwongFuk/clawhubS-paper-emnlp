#!/usr/bin/env python3
"""
IC-1 Routing Recall Experiment  (v3 - clean design)
=====================================================
Lessons learnt from 6 previous versions:

  v4 (only success): gap=40% because:
    - undisclosed cap contained specific tech terms (justfile, PR template)
    - queries targeted that exact missing concept
    - distractor pool was kept reasonable

  v5/v6 (failures): gap~0% because:
    - undisclosed caps were abstract ("YAGNI guidance") → complete desc had no
      new signal for the router
    - distractors were random → routing too easy regardless of description

NEW DESIGN:
  1. Filter   — only IC-1 skills whose undisclosed caps contain tech-specific terms
  2. Generate — 3 realistic query styles per undisclosed cap (no tech jargon in query)
  3. Route    — openclaw real router, same-domain distractors
  4. Compare  — original vs augmented (original + one explicit sentence about undisclosed cap)
  5. Report   — Recall@1 by query style, gap breakdown
"""

import json, re, sqlite3, subprocess, time
from pathlib import Path
from collections import defaultdict

# ── Config ───────────────────────────────────────────────────────────────────
PILOT_FILE   = Path(__file__).parent / "results/cap_alignment_v2/pilot_150.jsonl"
DB           = "/Users/guangfu/codex/clawhubS/data/skills_sh/skills_sh.sqlite3"
OUTDIR       = Path(__file__).parent / "results/routing_gap_v3"
QUERIES_FILE = OUTDIR / "queries_v3.json"
RESULTS_FILE = OUTDIR / "results_v3.jsonl"
OUTDIR.mkdir(parents=True, exist_ok=True)

TARGET_SKILLS = 25          # IC-1 skills to use
CAPS_PER_SKILL = 3          # undisclosed caps per skill
QUERIES_PER_CAP = 3         # scenario, symptom, goal (all natural-language, no jargon)
DISTRACTORS_N  = 15         # same-domain distractors per routing call


# ── Helpers ──────────────────────────────────────────────────────────────────

def safe_prompt(t, **kw):
    for k, v in kw.items():
        t = t.replace('{' + k + '}', str(v))
    return t

def llm_text(prompt: str, retries=3) -> str | None:
    for _ in range(retries):
        try:
            r = subprocess.run(
                ["openclaw", "infer", "model", "run", "--prompt", prompt, "--json"],
                capture_output=True, text=True, timeout=120)
            return json.loads(r.stdout).get("outputs", [{}])[0].get("text", "").strip()
        except subprocess.TimeoutExpired:
            time.sleep(5)
        except Exception:
            pass
    return None

def llm_json(prompt: str) -> dict | list | None:
    text = llm_text(prompt)
    if not text: return None
    for part in text.split("```"):
        part = part.strip().lstrip("json").strip()
        try: return json.loads(part)
        except: pass
    try: return json.loads(text)
    except: return None


# ── Step 1: Filter IC-1 skills with tech-specific undisclosed caps ────────────

SPECIFICITY_PROMPT = """\
You are evaluating whether capability phrases are specific enough to be uniquely queryable.

Capability phrases from an AI skill:
{caps}

For each phrase, decide if it is SPECIFIC (contains a tool name, API, file format,
technology name, protocol, framework, CLI command, or other concrete technical term)
or VAGUE (generic description like "design guidance", "analysis", "optimization").

Return ONLY valid JSON list of objects:
[
  {{"cap": "phrase", "specific": true/false, "reason": "one word why"}},
  ...
]"""

def classify_specificity(caps: list[str]) -> list[str]:
    """Return only the caps judged as 'specific' by LLM."""
    if not caps:
        return []
    result = llm_json(safe_prompt(SPECIFICITY_PROMPT, caps=json.dumps(caps)))
    if not isinstance(result, list):
        # fallback: simple heuristic
        return [c for c in caps if len(c.split()) >= 3 and
                any(w[0].isupper() for w in c.split())]
    return [item["cap"] for item in result
            if isinstance(item, dict) and item.get("specific")]

def load_ic1_skills() -> list[dict]:
    """Load IC-1 skills from pilot, filter to those with specific undisclosed caps."""
    candidates = []
    for line in PILOT_FILE.read_text().strip().split('\n'):
        try:
            r = json.loads(line)
            if 'error' in r: continue
            if not r.get('only_body'): continue
            candidates.append(r)
        except: pass

    print(f"IC-1 candidates (only_body≠∅): {len(candidates)}")
    print("Classifying specificity of undisclosed caps...\n")

    qualified = []
    for r in candidates:
        specific_caps = classify_specificity(r['only_body'])
        if len(specific_caps) >= 1:
            r['specific_undisclosed'] = specific_caps
            qualified.append(r)
            print(f"  ✓ {r['slug']:35s}  specific caps: {specific_caps[:2]}")
        else:
            print(f"  ✗ {r['slug']:35s}  all vague: {r['only_body'][:2]}")

    # Sort by number of specific undisclosed caps
    qualified.sort(key=lambda x: -len(x['specific_undisclosed']))
    print(f"\nQualified IC-1 skills with specific caps: {len(qualified)}")
    return qualified[:TARGET_SKILLS]


# ── Step 2: Load descriptions from DB ────────────────────────────────────────

def load_db_info(slugs: list[str]) -> dict:
    db = sqlite3.connect(DB)
    cur = db.cursor()
    ph = ','.join('?' * len(slugs))
    cur.execute(f"SELECT skill_slug, description, skill_text FROM skills WHERE skill_slug IN ({ph})", slugs)
    result = {r[0]: {'desc': r[1], 'body': r[2]} for r in cur.fetchall()}
    db.close()
    return result


# ── Step 3: Generate realistic queries ───────────────────────────────────────

QUERY_GEN_PROMPT = """\
You are generating realistic user queries for an AI skill routing test.

Skill slug: {slug}
Current description (INCOMPLETE — missing some capabilities):
  {description}

The skill actually supports this undisclosed capability:
  UNDISCLOSED CAP: "{undisclosed_cap}"

Generate exactly 3 realistic user queries that would naturally invoke this undisclosed
capability. The queries should:
- Sound like real user messages, first person, 15-40 words each
- NOT repeat the exact technical term from the capability phrase (use natural language)
- Be specific enough that only a tool with this exact capability would be appropriate
- Cover 3 angles:
    1. "scenario" — user describes a real work situation needing this capability
    2. "symptom"  — user describes a problem they are experiencing (hints at the need)
    3. "goal"     — user states what they want to accomplish in plain language

Also generate 1 "control" query about a DISCLOSED capability already in the description.

Return ONLY valid JSON:
{{
    "scenario": "...",
    "symptom":  "...",
    "goal":     "...",
    "control":  "..."
}}"""

def generate_skill_queries(slug: str, desc: str, specific_caps: list[str]) -> list[dict]:
    queries = []
    for cap in specific_caps[:CAPS_PER_SKILL]:
        result = llm_json(safe_prompt(QUERY_GEN_PROMPT,
                                      slug=slug, description=desc, undisclosed_cap=cap))
        if not isinstance(result, dict):
            print(f"    [WARN] query gen failed for cap '{cap}'")
            continue
        for qtype in ['scenario', 'symptom', 'goal', 'control']:
            q = result.get(qtype, '')
            if isinstance(q, str) and len(q) > 10:
                queries.append({
                    "slug": slug,
                    "undisclosed_cap": cap,
                    "query_type": qtype,
                    "query": q,
                    "is_control": qtype == "control",
                    "is_body_trigger": qtype != "control",
                })
    return queries


# ── Step 4: Build augmented description ──────────────────────────────────────

AUGMENT_PROMPT = """\
Append a single clear sentence to this skill description to mention the missing capability.
Do NOT change the existing text. Add exactly one natural sentence at the end.

Description: {description}
Missing capability to add: {cap}

Return ONLY the final description (original + the new sentence). No explanation."""

def build_augmented_desc(desc: str, specific_caps: list[str]) -> str:
    """Add one sentence per specific cap to the description."""
    result = desc
    for cap in specific_caps[:CAPS_PER_SKILL]:
        added = llm_text(safe_prompt(AUGMENT_PROMPT, description=result, cap=cap))
        if added and len(added) > len(result):
            result = added.strip()
    return result


# ── Step 5: Build same-domain distractor library ─────────────────────────────

def build_distractor_library(focal_slug: str, focal_desc: str,
                              all_db: dict, all_slugs: list[str]) -> list[dict]:
    """
    Library = focal skill + same-domain distractors.
    Same-domain: we pick skills whose description shares keywords with focal.
    """
    focal_words = set(re.findall(r'\b\w{4,}\b', focal_desc.lower()))
    scored = []
    for slug, info in all_db.items():
        if slug == focal_slug: continue
        words = set(re.findall(r'\b\w{4,}\b', info['desc'].lower()))
        overlap = len(focal_words & words)
        scored.append((slug, overlap, info['desc']))
    scored.sort(key=lambda x: -x[1])
    distractors = [{'slug': s, 'description': d} for s, _, d in scored[:DISTRACTORS_N]]

    library = [{'slug': focal_slug, 'description': focal_desc}] + distractors
    return library


# ── Step 6: Route ─────────────────────────────────────────────────────────────

ROUTE_PROMPT = """\
You are an AI agent skill dispatcher.

Available skills:
{skill_list}

User message: "{query}"

Select the most appropriate skill slug to handle this request.
If no skill is relevant, respond "none".
Reply with ONLY the skill slug or "none". No explanation."""

def route(query: str, library: list[dict]) -> str:
    skill_list = "\n".join(f"  {s['slug']}: {s['description'][:160]}" for s in library)
    result = llm_text(safe_prompt(ROUTE_PROMPT, skill_list=skill_list, query=query))
    if not result: return "error"
    result = result.strip().lower().strip('"\'` ')
    for s in library:
        if s['slug'].lower() in result:
            return s['slug']
    if 'none' in result: return 'none'
    return result.split()[0] if result else "error"


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("=" * 65)
    print("IC-1 Routing Recall Experiment  (v3)")
    print("=" * 65, "\n")

    # ── 1. Select IC-1 skills ─────────────────────────────────────────────────
    ic1_skills = load_ic1_skills()
    if not ic1_skills:
        print("No qualifying IC-1 skills found. Run cap_pilot first.")
        return

    all_slugs = [s['slug'] for s in ic1_skills]
    db_info   = load_db_info(all_slugs)

    # ── 2. Generate queries ───────────────────────────────────────────────────
    if QUERIES_FILE.exists() and QUERIES_FILE.stat().st_size > 10:
        print(f"\nLoading queries from {QUERIES_FILE.name}...")
        all_queries = json.loads(QUERIES_FILE.read_text())
        print(f"  {len(all_queries)} queries loaded")
    else:
        print("\n── Generating queries ──")
        all_queries = []
        for skill in ic1_skills:
            slug = skill['slug']
            if slug not in db_info: continue
            desc = db_info[slug]['desc']
            caps = skill['specific_undisclosed']
            print(f"  {slug}  ({len(caps)} specific caps)")
            qs = generate_skill_queries(slug, desc, caps)
            all_queries.extend(qs)
            print(f"    → {len(qs)} queries")
            time.sleep(0.3)
        QUERIES_FILE.write_text(json.dumps(all_queries, indent=2, ensure_ascii=False))
        print(f"\n{len(all_queries)} queries saved → {QUERIES_FILE.name}")

    # ── 3. Build augmented descriptions ──────────────────────────────────────
    print("\n── Building augmented descriptions ──")
    augmented: dict[str, str] = {}
    for skill in ic1_skills:
        slug = skill['slug']
        if slug not in db_info: continue
        orig = db_info[slug]['desc']
        caps = skill['specific_undisclosed']
        aug  = build_augmented_desc(orig, caps)
        augmented[slug] = aug
        print(f"  {slug}: {len(orig)} → {len(aug)} chars")
        time.sleep(0.2)

    # ── 4. Run routing experiment ─────────────────────────────────────────────
    print("\n── Running routing experiment ──")
    done_keys = set()
    all_results = []
    if RESULTS_FILE.exists():
        for line in RESULTS_FILE.read_text().strip().split('\n'):
            try:
                r = json.loads(line)
                done_keys.add((r['slug'], r['variant'], r['query'][:30]))
                all_results.append(r)
            except: pass
    print(f"Already done: {len(done_keys)}")

    total = len(all_queries) * 2
    done_n = len(done_keys)

    with open(RESULTS_FILE, 'a') as f:
        for q_rec in all_queries:
            slug = q_rec['slug']
            if slug not in db_info or slug not in augmented:
                continue

            for variant, desc in [('original',  db_info[slug]['desc']),
                                   ('augmented', augmented[slug])]:
                key = (slug, variant, q_rec['query'][:30])
                if key in done_keys:
                    continue

                library = build_distractor_library(slug, desc, db_info, all_slugs)
                chosen  = route(q_rec['query'], library)
                correct = (chosen == slug)
                done_n += 1

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
                all_results.append(rec); done_keys.add(key)
                f.write(json.dumps(rec, ensure_ascii=False) + '\n'); f.flush()

                mark = '✓' if correct else '✗'
                print(f"  [{done_n:4d}/{total}] {slug:30s} [{variant:9s}] "
                      f"{q_rec['query_type']:10s} {mark}  chosen={chosen}")

    # ── 5. Report ─────────────────────────────────────────────────────────────
    print("\n" + "=" * 65)
    print("RESULTS\n")

    qt_v = defaultdict(lambda: defaultdict(lambda: {'c': 0, 't': 0}))
    for r in all_results:
        qt_v[r['query_type']][r['variant']]['t'] += 1
        if r['correct']: qt_v[r['query_type']][r['variant']]['c'] += 1

    print(f"{'Query type':12s}  {'original':>9s}  {'augmented':>9s}  {'gap':>8s}    n")
    print("-" * 55)
    for qt in ['control', 'scenario', 'symptom', 'goal']:
        o = qt_v[qt]['original'];  a = qt_v[qt]['augmented']
        if not o['t']: continue
        oa = o['c']/o['t']*100;   aa = a['c']/a['t']*100
        marker = "← KEY" if qt != 'control' else "(ctrl)"
        print(f"  {qt:10s}  {oa:8.1f}%  {aa:8.1f}%  {aa-oa:+7.1f}%  {o['t']:4d} {marker}")

    # Paired analysis
    pairs = defaultdict(dict)
    for r in all_results:
        pairs[(r['slug'], r['query'][:30])][r['variant']] = r['correct']
    only_original_fixed = [(k, v) for k, v in pairs.items()
                           if not v.get('original') and v.get('augmented')]
    only_augmented_broke = [(k, v) for k, v in pairs.items()
                            if v.get('original') and not v.get('augmented')]
    both_fail = [(k, v) for k, v in pairs.items()
                 if not v.get('original') and not v.get('augmented')]

    print(f"\nPaired analysis (n={len(pairs)} query pairs):")
    print(f"  augmented fixed a failure (orig✗→aug✓): {len(only_original_fixed)}")
    print(f"  augmented broke a success (orig✓→aug✗): {len(only_augmented_broke)}")
    print(f"  Both fail:                               {len(both_fail)}")

    # Body trigger overall
    body_rows = [r for r in all_results if r['is_body_trigger']]
    for v in ['original', 'augmented']:
        rows_v = [r for r in body_rows if r['variant'] == v]
        correct = sum(r['correct'] for r in rows_v)
        print(f"\n  body_trigger Recall [{v:9s}]: "
              f"{correct}/{len(rows_v)} = {correct/max(len(rows_v),1)*100:.1f}%")

    if any_fixed := len(only_original_fixed):
        print(f"\n  ✓ Augmented description FIXED {any_fixed} routing failures!")
        for (slug, q_prefix), _ in only_original_fixed[:3]:
            print(f"    {slug}: '{q_prefix}...'")


if __name__ == "__main__":
    main()
