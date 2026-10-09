#!/usr/bin/env python3
"""
Routing Experiment: Description Impact Under Name Ambiguity  (v2 — DB-backed)
==============================================================================
Both competitor pools come from the REAL ClawHub database (17K+ skills).
No LLM-generated competitors — scientifically rigorous.

Condition A — Similar slugs
  Distractor pool: skills whose slug shares ≥1 token with the focal slug.
  Example: focal=task-runner → distractors include task-scheduler, build-runner,
           task-automation, script-runner, job-runner, ...
  LLM sees similarly-named skills; must read description to distinguish.

Condition B — Opaque slugs (renamed util-NNN)
  Same distractor pool as Condition A, but ALL slugs renamed to util-NNN codes.
  Names give zero information; routing is purely description-driven.

Both:
  - Focal skill has IC-1 gap (description missing a specific technical capability)
  - Queries are body_trigger queries about the undisclosed capability
  - Compare Recall@1: original vs augmented (original + explicit undisclosed cap sentence)
"""

import json, math, re, sqlite3, subprocess, time, random
from pathlib import Path
from collections import defaultdict, Counter

# ── Config ────────────────────────────────────────────────────────────────────
PILOT_FILE    = Path(__file__).parent / "results/cap_alignment_v2/pilot_150.jsonl"
DB            = "/Users/guangfu/codex/clawhubS/data/skills_sh/skills_sh.sqlite3"
OUTDIR        = Path(__file__).parent / "results/name_ambiguity_v2"
QUERIES_FILE  = OUTDIR / "queries.json"
COMPS_FILE    = OUTDIR / "competitors.json"
AUG_FILE      = OUTDIR / "aug_descs.json"
RESULTS_FILE  = OUTDIR / "results.jsonl"
OUTDIR.mkdir(parents=True, exist_ok=True)

MAX_FOCAL       = 15   # IC-1 focal skills to use
CAPS_PER_SKILL  = 2    # undisclosed caps per skill (top-N specific ones)
LIBRARY_SIZE    = 16   # 1 focal + 15 distractors per routing call
MIN_SLUG_OVERLAP = 1   # min shared slug tokens for Condition A
MAX_SLUG_DISTRACTORS = 20  # how many slug-similar distractors to keep per focal

random.seed(42)

# ── Tiny BM25 ────────────────────────────────────────────────────────────────

def tokenize(text):
    return re.findall(r'\b[a-z][a-z0-9]{1,}\b', text.lower())

class BM25:
    def __init__(self, docs, k1=1.5, b=0.75):
        self.k1, self.b, self.N = k1, b, len(docs)
        self.tok = [tokenize(d) for d in docs]
        self.avgdl = sum(len(t) for t in self.tok) / max(self.N, 1)
        df = Counter(w for t in self.tok for w in set(t))
        self.idf = {w: math.log((self.N - f + 0.5) / (f + 0.5) + 1) for w, f in df.items()}

    def score(self, qts, di):
        tf = Counter(self.tok[di]); dl = len(self.tok[di])
        return sum(self.idf.get(w, 0) * tf[w] * (self.k1 + 1) /
                   (tf[w] + self.k1 * (1 - self.b + self.b * dl / self.avgdl))
                   for w in qts)

    def search(self, q, k=20):
        qt = tokenize(q)
        return sorted(range(self.N), key=lambda i: -self.score(qt, i))[:k]

# ── LLM helpers ──────────────────────────────────────────────────────────────

def sp(t, **kw):
    for k, v in kw.items(): t = t.replace('{' + k + '}', str(v))
    return t

def llm(prompt, retries=3):
    for _ in range(retries):
        try:
            r = subprocess.run(
                ["openclaw", "infer", "model", "run", "--prompt", prompt, "--json"],
                capture_output=True, text=True, timeout=120)
            return json.loads(r.stdout).get("outputs", [{}])[0].get("text", "").strip()
        except subprocess.TimeoutExpired: time.sleep(5)
        except: pass
    return None

def llm_json(prompt):
    txt = llm(prompt)
    if not txt: return None
    for part in txt.split("```"):
        part = part.strip().lstrip("json").strip()
        try: return json.loads(part)
        except: pass
    try: return json.loads(txt)
    except: return None

# ── Step 1: Load IC-1 skills ──────────────────────────────────────────────────

SPEC_PROMPT = """\
Are these phrases SPECIFIC? (tool/API names, file formats, protocol, CLI commands)
Phrases: {caps}
Return JSON: [{{"cap":"...","specific":true/false}}]"""

def filter_specific(caps):
    if not caps: return []
    r = llm_json(sp(SPEC_PROMPT, caps=json.dumps(caps)))
    if not isinstance(r, list): return [c for c in caps if len(c.split()) >= 3]
    return [x["cap"] for x in r if isinstance(x, dict) and x.get("specific")]

def load_ic1_skills():
    rows = []
    for line in PILOT_FILE.read_text().strip().split('\n'):
        try:
            r = json.loads(line)
            if 'error' in r or not r.get('only_body'): continue
            rows.append(r)
        except: pass
    print(f"IC-1 candidates: {len(rows)}")
    qualified = []
    for r in rows:
        sc = filter_specific(r['only_body'])
        if sc:
            r['specific_caps'] = sc
            qualified.append(r)
            print(f"  ✓ {r['slug']:35s} specific: {sc[:2]}")
        else:
            print(f"  ✗ {r['slug']:35s} (vague caps)")
    qualified.sort(key=lambda x: -len(x['specific_caps']))
    print(f"\nQualified IC-1 skills: {len(qualified)}")
    return qualified[:MAX_FOCAL]

# ── Step 2: Load full ClawHub DB ──────────────────────────────────────────────

def load_full_db():
    db = sqlite3.connect(DB)
    cur = db.cursor()
    cur.execute("""
        SELECT skill_slug, description FROM skills
        WHERE description IS NOT NULL AND length(description) > 50
    """)
    result = {r[0]: r[1] for r in cur.fetchall()}
    db.close()
    return result

# ── Step 3: Find DB-backed competitors ───────────────────────────────────────

def slug_tokens(slug):
    """Split 'task-runner-v2' → {'task', 'runner', 'v2'}"""
    return set(re.split(r'[-_]', slug.lower()))

def find_slug_similar(focal_slug, all_db, focal_slugs, min_overlap=MIN_SLUG_OVERLAP):
    """
    Condition A: find real skills whose slug shares ≥ min_overlap tokens
    with the focal slug. These look like 'competing' skills in the marketplace
    (e.g. task-runner vs task-automation vs build-runner).
    """
    focal_tokens = slug_tokens(focal_slug)
    # Remove very common tokens that don't carry domain meaning
    STOP_TOKENS = {'v1','v2','v3','a','an','the','my','your',
                   'new','old','lite','pro','plus','mini','max'}
    focal_tokens -= STOP_TOKENS

    scored = []
    for slug, desc in all_db.items():
        if slug == focal_slug or slug in focal_slugs: continue
        tokens = slug_tokens(slug) - STOP_TOKENS
        overlap = len(focal_tokens & tokens)
        if overlap >= min_overlap:
            scored.append((slug, desc, overlap))

    # Sort by overlap desc, then desc length (longer = more specific)
    scored.sort(key=lambda x: (-x[2], -len(x[1])))
    return [{"slug": s, "description": d} for s, d, _ in scored[:MAX_SLUG_DISTRACTORS]]


def find_desc_similar_opaque(focal_slug, focal_desc, all_db, focal_slugs,
                              slug_similar, bm25_index, all_slugs):
    """
    For Condition B: use BM25 desc similarity to find real competitors,
    UNION with the slug-similar ones. Then rename everything to util-NNN.
    Goal: same competitor pool as Condition A, but with opaque names.
    """
    # Start with slug-similar
    pool = {s["slug"]: s for s in slug_similar}

    # Add BM25-similar if we need more
    if len(pool) < LIBRARY_SIZE - 1:
        hits = bm25_index.search(focal_desc, k=30)
        for i in hits:
            s = all_slugs[i]
            if s == focal_slug or s in focal_slugs: continue
            if s not in pool:
                pool[s] = {"slug": s, "description": all_db[s]}
            if len(pool) >= LIBRARY_SIZE - 1: break

    return list(pool.values())[:LIBRARY_SIZE - 1]

# ── Step 4: Generate queries ───────────────────────────────────────────────────

QGEN = """\
Skill slug: {slug}
Current description (may be INCOMPLETE): {description}
Undisclosed capability this skill actually supports: "{cap}"

Generate realistic user queries needing ONLY this undisclosed capability.
Do NOT use the exact technical phrase "{cap}" in any query.
Styles:
  scenario — user describes their work context
  symptom  — user describes the problem/friction they have
  goal     — user states the outcome they want

Also 1 "control" query about something already in the description (baseline).

Return ONLY JSON:
{{"scenario":"...","symptom":"...","goal":"...","control":"..."}}"""

def generate_queries(slug, desc, caps):
    qs = []
    for cap in caps[:CAPS_PER_SKILL]:
        r = llm_json(sp(QGEN, slug=slug, description=desc, cap=cap))
        if not isinstance(r, dict): continue
        for qt in ['scenario', 'symptom', 'goal', 'control']:
            q = r.get(qt, '')
            if isinstance(q, str) and len(q) > 10:
                qs.append({"slug": slug, "cap": cap, "query_type": qt,
                           "query": q, "is_control": qt == "control"})
    return qs

# ── Step 5: Augment descriptions ──────────────────────────────────────────────

AUG_P = "Append ONE sentence to mention the missing capability. Return ONLY the full description.\nDescription: {desc}\nMissing: {cap}"

def augment(desc, caps):
    result = desc
    for cap in caps[:CAPS_PER_SKILL]:
        added = llm(sp(AUG_P, desc=result, cap=cap))
        if added and len(added) > len(result): result = added.strip()
    return result

# ── Step 6: Route ─────────────────────────────────────────────────────────────

ROUTE_P = """\
You are an AI skill dispatcher. Select the best skill for this user request.
Reply with ONLY the skill slug or "none".

Available skills:
{skills}

User: "{query}"

Best skill:"""

def route(query, library):
    skill_list = "\n".join(f"  {s['slug']}: {s['description'][:130]}" for s in library)
    r = llm(sp(ROUTE_P, skills=skill_list, query=query))
    if not r: return "error"
    r = r.strip().lower().strip('"\'` ')
    for sk in library:
        if sk['slug'].lower() in r: return sk['slug']
    return 'none' if 'none' in r else r.split()[0]

# ── Step 7: Evaluate one routing call ─────────────────────────────────────────

def eval_one(query, focal_slug, focal_desc, distractor_pool, condition, all_db):
    # Build library: focal + distractors (pad to LIBRARY_SIZE)
    library = [{"slug": focal_slug, "description": focal_desc}]
    pool = list(distractor_pool)
    random.shuffle(pool)
    library += pool[:LIBRARY_SIZE - 1]
    random.shuffle(library)
    target = focal_slug

    if condition == "B":
        # Anonymize ALL slugs → util-NNN
        codes = [f"util-{i+1:03d}" for i in range(len(library))]
        random.shuffle(codes)
        code_map = {lib["slug"]: codes[i] for i, lib in enumerate(library)}
        library = [{"slug": code_map[lib["slug"]], "description": lib["description"]}
                   for lib in library]
        target = code_map[focal_slug]

    chosen = route(query, library)
    return chosen == target

# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    print("=" * 65)
    print("ROUTING UNDER NAME AMBIGUITY  (DB-backed competitors)")
    print("A: similar slug tokens | B: util-NNN opaque names")
    print("=" * 65 + "\n")

    # ── Load ──────────────────────────────────────────────────────────────────
    ic1_skills = load_ic1_skills()
    focal_slugs = {sk["slug"] for sk in ic1_skills}

    print("\nLoading full ClawHub DB...")
    all_db = load_full_db()
    all_slugs = list(all_db.keys())
    print(f"  {len(all_db)} skills")

    print("Building BM25 index...")
    bm25 = BM25([all_db[s] for s in all_slugs])
    print("  done\n")

    # ── Find real DB competitors ───────────────────────────────────────────────
    if COMPS_FILE.exists():
        print(f"Loading competitors from {COMPS_FILE.name}...")
        competitors_map = json.loads(COMPS_FILE.read_text())
    else:
        print("── Finding DB competitors ──")
        competitors_map = {}
        for sk in ic1_skills:
            slug = sk["slug"]
            if slug not in all_db: continue
            desc = all_db[slug]
            slug_sim = find_slug_similar(slug, all_db, focal_slugs)
            all_sim  = find_desc_similar_opaque(slug, desc, all_db, focal_slugs,
                                                 slug_sim, bm25, all_slugs)
            competitors_map[slug] = {
                "slug_similar": slug_sim,    # for Condition A
                "desc_similar": all_sim,     # for Condition B
            }
            print(f"  {slug:35s}  slug-sim={len(slug_sim)}  desc-sim={len(all_sim)}")
            for c in slug_sim[:3]:
                print(f"    A: {c['slug']}: {c['description'][:70]}")
        COMPS_FILE.write_text(json.dumps(competitors_map, indent=2))
        print()

    # ── Queries ───────────────────────────────────────────────────────────────
    if QUERIES_FILE.exists() and QUERIES_FILE.stat().st_size > 10:
        print(f"Loading queries from {QUERIES_FILE.name}...")
        all_queries = json.loads(QUERIES_FILE.read_text())
        print(f"  {len(all_queries)} queries\n")
    else:
        print("── Generating queries ──")
        all_queries = []
        for sk in ic1_skills:
            slug = sk["slug"]
            if slug not in all_db: continue
            qs = generate_queries(slug, all_db[slug], sk["specific_caps"])
            all_queries.extend(qs)
            print(f"  {slug}: {len(qs)} queries")
        QUERIES_FILE.write_text(json.dumps(all_queries, indent=2, ensure_ascii=False))
        print(f"\n{len(all_queries)} queries saved\n")

    # ── Augmented descriptions ────────────────────────────────────────────────
    if AUG_FILE.exists():
        aug_descs = json.loads(AUG_FILE.read_text())
        print(f"Loaded {len(aug_descs)} augmented descriptions")
    else:
        print("── Augmenting descriptions ──")
        aug_descs = {}
        for sk in ic1_skills:
            slug = sk["slug"]
            if slug not in all_db: continue
            aug_descs[slug] = augment(all_db[slug], sk["specific_caps"])
            print(f"  {slug}: {len(all_db[slug])} → {len(aug_descs[slug])} chars")
        AUG_FILE.write_text(json.dumps(aug_descs, indent=2, ensure_ascii=False))
    print()

    # ── Routing experiment ────────────────────────────────────────────────────
    print(f"── Running routing ──")
    done_keys = set()
    all_results = []
    if RESULTS_FILE.exists():
        for line in RESULTS_FILE.read_text().strip().split('\n'):
            try:
                r = json.loads(line)
                done_keys.add((r["slug"], r["condition"], r["variant"], r["query"][:30]))
                all_results.append(r)
            except: pass
    print(f"Already done: {len(done_keys)}")

    total = len(all_queries) * 2 * 2
    done_n = len(done_keys)

    with open(RESULTS_FILE, "a") as f:
        for q in all_queries:
            slug = q["slug"]
            if slug not in all_db or slug not in aug_descs: continue
            comps_info = competitors_map.get(slug, {})

            for condition in ["A", "B"]:
                pool = (comps_info.get("slug_similar", []) if condition == "A"
                        else comps_info.get("desc_similar", []))

                for variant, desc in [("original",   all_db[slug]),
                                       ("augmented", aug_descs[slug])]:

                    key = (slug, condition, variant, q["query"][:30])
                    if key in done_keys: continue

                    correct = eval_one(q["query"], slug, desc, pool, condition, all_db)
                    done_n += 1

                    rec = {"slug": slug, "condition": condition, "variant": variant,
                           "query_type": q["query_type"], "cap": q["cap"],
                           "query": q["query"], "correct": correct,
                           "is_control": q["is_control"]}
                    all_results.append(rec); done_keys.add(key)
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n"); f.flush()

                    mark = "✓" if correct else "✗"
                    print(f"  [{done_n:4d}/{total}] {slug:25s} [cond-{condition}] "
                          f"[{variant:9s}] {q['query_type']:10s} {mark}")
                    time.sleep(0.2)

    # ── Report ────────────────────────────────────────────────────────────────
    print("\n" + "=" * 65)
    print("RESULTS\n")

    for cond, label in [("A", "A — slug-similar (similar names, real DB skills)"),
                        ("B", "B — util-NNN (opaque names,  real DB skills)")]:
        print(f"── Condition {label} ──")
        qt_v = defaultdict(lambda: defaultdict(lambda: {"c": 0, "t": 0}))
        for r in all_results:
            if r["condition"] != cond: continue
            qt_v[r["query_type"]][r["variant"]]["t"] += 1
            if r["correct"]: qt_v[r["query_type"]][r["variant"]]["c"] += 1

        print(f"  {'type':12s}  {'original':>9s}  {'augmented':>9s}  {'gap':>7s}   n")
        for qt in ["control", "scenario", "symptom", "goal"]:
            o = qt_v[qt]["original"]; a = qt_v[qt]["augmented"]
            if not o["t"]: continue
            oa = o["c"] / o["t"] * 100; aa = a["c"] / a["t"] * 100
            tag = "← KEY" if qt != "control" else "(ctrl)"
            print(f"  {qt:12s}  {oa:8.1f}%  {aa:8.1f}%  {aa-oa:+6.1f}%  "
                  f"{o['t']:3d} {tag}")

        pairs = defaultdict(dict)
        for r in all_results:
            if r["condition"] != cond or r["is_control"]: continue
            pairs[(r["slug"], r["query"][:30])][r["variant"]] = r["correct"]
        fixed = sum(1 for p in pairs.values() if not p.get("original") and p.get("augmented"))
        broke = sum(1 for p in pairs.values() if p.get("original") and not p.get("augmented"))
        print(f"\n  Body-trigger paired: fixed={fixed}  broke={broke}  net={fixed-broke:+d}\n")

    # Body trigger overall gap
    print("── Overall body-trigger Recall@1 ──")
    for cond in ["A", "B"]:
        for variant in ["original", "augmented"]:
            rows = [r for r in all_results
                    if r["condition"] == cond and r["variant"] == variant
                    and not r["is_control"]]
            if rows:
                recall = sum(r["correct"] for r in rows) / len(rows) * 100
                print(f"  Cond-{cond} [{variant:9s}]: {recall:.1f}%  (n={len(rows)})")


if __name__ == "__main__":
    main()
