#!/usr/bin/env python3
"""
Dual IC-1 Experiment
====================
Exp A: Routing  — 19 IC-1 skills, 200-skill distractor pool, slug visible
Exp B: Marketplace Search — BM25 over full ClawHub corpus (~17K skills)

Both compare original vs augmented (original + undisclosed cap sentence).
"""

import json, re, sqlite3, subprocess, time, math
from pathlib import Path
from collections import defaultdict

# ── Config ────────────────────────────────────────────────────────────────────
PILOT_FILE   = Path(__file__).parent / "results/cap_alignment_v2/pilot_150.jsonl"
DB           = "/Users/guangfu/codex/clawhubS/data/skills_sh/skills_sh.sqlite3"
OUTDIR       = Path(__file__).parent / "results/dual_exp"
QUERIES_FILE = OUTDIR / "queries_dual.json"
RESULTS_A    = OUTDIR / "results_routing_200.jsonl"
RESULTS_B    = OUTDIR / "results_marketplace.jsonl"
REPORT_FILE  = OUTDIR / "report_dual.json"
OUTDIR.mkdir(parents=True, exist_ok=True)

TARGET_SKILLS    = 25
CAPS_PER_SKILL   = 3
DISTRACTORS_A    = 200   # Exp A: large routing pool
TOP_K_B          = 10    # Exp B: Recall@K for marketplace search

# ── Helpers ──────────────────────────────────────────────────────────────────

def safe_prompt(t, **kw):
    for k, v in kw.items():
        t = t.replace('{' + k + '}', str(v))
    return t

def llm_text(prompt, retries=3):
    for _ in range(retries):
        try:
            r = subprocess.run(
                ["openclaw","infer","model","run","--prompt",prompt,"--json"],
                capture_output=True, text=True, timeout=120)
            return json.loads(r.stdout).get("outputs",[{}])[0].get("text","").strip()
        except subprocess.TimeoutExpired: time.sleep(5)
        except: pass
    return None

def llm_json(prompt):
    text = llm_text(prompt)
    if not text: return None
    for part in text.split("```"):
        part = part.strip().lstrip("json").strip()
        try: return json.loads(part)
        except: pass
    try: return json.loads(text)
    except: return None

# ── BM25 (no external deps) ──────────────────────────────────────────────────

def tokenize(text):
    return re.findall(r'\b[a-z][a-z0-9\-]{1,}\b', text.lower())

class BM25:
    def __init__(self, docs, k1=1.5, b=0.75):
        self.k1, self.b = k1, b
        self.docs = docs
        self.N = len(docs)
        self.tokenized = [tokenize(d) for d in docs]
        self.avgdl = sum(len(t) for t in self.tokenized) / max(self.N, 1)
        self.idf = {}
        from collections import Counter
        df = Counter()
        for t in self.tokenized:
            for w in set(t): df[w] += 1
        for w, f in df.items():
            self.idf[w] = math.log((self.N - f + 0.5) / (f + 0.5) + 1)

    def score(self, query_tokens, doc_idx):
        doc = self.tokenized[doc_idx]
        from collections import Counter
        tf = Counter(doc)
        dl = len(doc)
        s = 0
        for w in query_tokens:
            if w not in self.idf: continue
            f = tf[w]
            s += self.idf[w] * f * (self.k1+1) / (f + self.k1*(1-self.b+self.b*dl/self.avgdl))
        return s

    def search(self, query, top_k=10):
        qt = tokenize(query)
        scores = [(i, self.score(qt, i)) for i in range(self.N)]
        scores.sort(key=lambda x: -x[1])
        return scores[:top_k]

# ── Load IC-1 skills (reuse v3 query file if available) ──────────────────────

SPEC_PROMPT = """\
Are these capability phrases technically specific (contain tool names, APIs, file formats,
framework names, protocols, CLI commands, or similar concrete technical terms)?
Phrases: {caps}
Return JSON list: [{{"cap":"...","specific":true/false}}]"""

def filter_specific(caps):
    if not caps: return []
    r = llm_json(safe_prompt(SPEC_PROMPT, caps=json.dumps(caps)))
    if not isinstance(r, list):
        return [c for c in caps if len(c.split()) >= 3]
    return [x["cap"] for x in r if isinstance(x,dict) and x.get("specific")]

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
            print(f"  ✓ {r['slug']:35s} {sc[:2]}")
        else:
            print(f"  ✗ {r['slug']:35s} (vague)")
    qualified.sort(key=lambda x: -len(x['specific_caps']))
    print(f"\nQualified: {len(qualified)}")
    return qualified[:TARGET_SKILLS]

# ── Load DB ──────────────────────────────────────────────────────────────────

def load_db(slugs=None):
    db = sqlite3.connect(DB)
    cur = db.cursor()
    if slugs:
        ph = ','.join('?'*len(slugs))
        cur.execute(f"SELECT skill_slug,description FROM skills WHERE skill_slug IN ({ph})", slugs)
    else:
        cur.execute("SELECT skill_slug,description FROM skills WHERE description IS NOT NULL AND length(description)>50")
    result = {r[0]: r[1] for r in cur.fetchall()}
    db.close()
    return result

# ── Generate queries ──────────────────────────────────────────────────────────

QGEN = """\
Skill: {slug}
Incomplete description: {description}
UNDISCLOSED capability: "{cap}"

Generate 3 user queries that need this undisclosed capability.
Do NOT use the exact technical term from the capability in the query.
Query styles:
1. scenario: user describes a real work situation
2. symptom:  user describes a problem they face (that needs this cap)
3. goal:     user states what they want to achieve

Also 1 control query about a DISCLOSED capability already in the description.

Return ONLY JSON: {{"scenario":"...","symptom":"...","goal":"...","control":"..."}}"""

def generate_queries(slug, desc, caps):
    qs = []
    for cap in caps[:CAPS_PER_SKILL]:
        r = llm_json(safe_prompt(QGEN, slug=slug, description=desc, cap=cap))
        if not isinstance(r, dict): continue
        for qt in ['scenario','symptom','goal','control']:
            q = r.get(qt,'')
            if isinstance(q,str) and len(q)>10:
                qs.append({"slug":slug,"cap":cap,"query_type":qt,"query":q,
                           "is_control":qt=='control'})
    return qs

# ── Augment description ──────────────────────────────────────────────────────

AUG = "Append one sentence to this description to mention the missing capability.\n" \
      "Description: {desc}\nMissing: {cap}\nReturn ONLY the full augmented description."

def augment_desc(desc, caps):
    result = desc
    for cap in caps[:CAPS_PER_SKILL]:
        added = llm_text(safe_prompt(AUG, desc=result, cap=cap))
        if added and len(added) > len(result):
            result = added.strip()
    return result

# ── Exp A: Routing with 200-skill pool ───────────────────────────────────────

ROUTE_PROMPT = """\
You are an AI agent skill dispatcher. Given a user message and installed skills,
pick the most appropriate skill. Reply with ONLY the skill slug or "none".

Installed skills:
{skills}

User: "{query}"

Best skill slug:"""

def route_200(query, focal_slug, focal_desc, all_db, slug_pool):
    library = [{"slug": focal_slug, "description": focal_desc}]
    distractors = [(s, all_db[s]) for s in slug_pool if s != focal_slug and s in all_db]
    distractors = distractors[:DISTRACTORS_A]
    for s, d in distractors:
        library.append({"slug": s, "description": d})
    # Format: keep descriptions short to fit in context
    skill_list = "\n".join(f"  {s['slug']}: {s['description'][:120]}" for s in library)
    result = llm_text(safe_prompt(ROUTE_PROMPT, skills=skill_list, query=query))
    if not result: return "error"
    result = result.strip().lower().strip('"\'` ')
    for s in library:
        if s['slug'].lower() in result:
            return s['slug']
    return 'none' if 'none' in result else result.split()[0]

# ── Exp B: Marketplace BM25 search ───────────────────────────────────────────

def run_marketplace(ic1_skills, all_db, augmented_descs, all_queries):
    print("\n" + "="*65)
    print("EXP B: Marketplace BM25 Search")
    print(f"Corpus: {len(all_db)} skills\n")

    # Build two indices: original and augmented
    slugs_list = list(all_db.keys())
    orig_descs  = [all_db[s] for s in slugs_list]
    aug_descs   = [augmented_descs.get(s, all_db[s]) for s in slugs_list]

    print("Building BM25 indices...")
    bm25_orig = BM25(orig_descs)
    bm25_aug  = BM25(aug_descs)
    print("Done.\n")

    body_queries = [q for q in all_queries if not q['is_control']]
    results = []
    for q in body_queries:
        target = q['slug']
        if target not in slugs_list: continue
        target_idx = slugs_list.index(target)
        query_text = q['query']

        # Search with original
        hits_orig = [slugs_list[i] for i,_ in bm25_orig.search(query_text, TOP_K_B)]
        # Search with augmented
        hits_aug  = [slugs_list[i] for i,_ in bm25_aug.search(query_text, TOP_K_B)]

        hit_o = target in hits_orig
        hit_a = target in hits_aug
        rank_o = hits_orig.index(target)+1 if hit_o else None
        rank_a = hits_aug.index(target)+1  if hit_a else None

        results.append({
            "slug": target, "query_type": q['query_type'], "cap": q['cap'],
            "query": q['query'],
            "original_hit": hit_o, "original_rank": rank_o,
            "augmented_hit": hit_a, "augmented_rank": rank_a,
        })
        mark_o = f"✓@{rank_o}" if hit_o else "✗"
        mark_a = f"✓@{rank_a}" if hit_a else "✗"
        print(f"  {target:30s} [{q['query_type']:10s}]  orig={mark_o:6s}  aug={mark_a:6s}  q={query_text[:50]}")

    return results

# ── Report ────────────────────────────────────────────────────────────────────

def report(results_a, results_b):
    print("\n" + "="*65)
    print("FINAL REPORT\n")

    print("── Exp A: Routing (200-skill pool) ──────────────────────────")
    qt_v = defaultdict(lambda: defaultdict(lambda: {'c':0,'t':0}))
    for r in results_a:
        qt_v[r['query_type']][r['variant']]['t'] += 1
        if r['correct']: qt_v[r['query_type']][r['variant']]['c'] += 1

    print(f"  {'query_type':12s}  {'original':>9s}  {'augmented':>9s}  {'gap':>7s}")
    for qt in ['control','scenario','symptom','goal']:
        o = qt_v[qt]['original']; a = qt_v[qt]['augmented']
        if not o['t']: continue
        oa=o['c']/o['t']*100; aa=a['c']/a['t']*100
        print(f"  {qt:12s}  {oa:8.1f}%  {aa:8.1f}%  {aa-oa:+6.1f}%  (n={o['t']})")

    # Paired
    pairs = defaultdict(dict)
    for r in results_a:
        pairs[(r['slug'],r['query'][:30])][r['variant']]=r['correct']
    fixed = sum(1 for p in pairs.values() if not p.get('original') and p.get('augmented'))
    broke = sum(1 for p in pairs.values() if p.get('original') and not p.get('augmented'))
    print(f"\n  Paired: fixed={fixed}  broke={broke}  net={fixed-broke:+d}")

    print("\n── Exp B: Marketplace BM25 Recall@10 ────────────────────────")
    qt_b = defaultdict(lambda: {'ho':0,'ha':0,'t':0})
    for r in results_b:
        qt_b[r['query_type']]['t'] += 1
        if r['original_hit']: qt_b[r['query_type']]['ho'] += 1
        if r['augmented_hit']: qt_b[r['query_type']]['ha'] += 1

    print(f"  {'query_type':12s}  {'original':>9s}  {'augmented':>9s}  {'gap':>7s}")
    for qt in ['scenario','symptom','goal']:
        s = qt_b[qt]
        if not s['t']: continue
        ro=s['ho']/s['t']*100; ra=s['ha']/s['t']*100
        print(f"  {qt:12s}  {ro:8.1f}%  {ra:8.1f}%  {ra-ro:+6.1f}%  (n={s['t']})")

    fixed_b = sum(1 for r in results_b if not r['original_hit'] and r['augmented_hit'])
    broke_b  = sum(1 for r in results_b if r['original_hit'] and not r['augmented_hit'])
    print(f"\n  Paired: fixed={fixed_b}  broke={broke_b}  net={fixed_b-broke_b:+d}")

    return {"routing_200": {qt: {
                "original": f"{qt_v[qt]['original']['c']/max(qt_v[qt]['original']['t'],1)*100:.1f}%",
                "augmented": f"{qt_v[qt]['augmented']['c']/max(qt_v[qt]['augmented']['t'],1)*100:.1f}%"
            } for qt in ['control','scenario','symptom','goal']},
            "marketplace_bm25_recall10": {qt: {
                "original": f"{qt_b[qt]['ho']/max(qt_b[qt]['t'],1)*100:.1f}%",
                "augmented": f"{qt_b[qt]['ha']/max(qt_b[qt]['t'],1)*100:.1f}%",
                "gap": f"{(qt_b[qt]['ha']-qt_b[qt]['ho'])/max(qt_b[qt]['t'],1)*100:+.1f}%"
            } for qt in ['scenario','symptom','goal']}}

# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    print("="*65)
    print("DUAL IC-1 EXPERIMENT")
    print("Exp A: Routing 200-skill pool | Exp B: Marketplace BM25")
    print("="*65+"\n")

    # ── Load skills ────────────────────────────────────────────────────────
    ic1_skills = load_ic1_skills()
    focal_slugs = [s['slug'] for s in ic1_skills]

    # ── Load focal descriptions ────────────────────────────────────────────
    focal_db = load_db(focal_slugs)

    # ── Load full corpus for distractors (Exp A) + marketplace (Exp B) ────
    print(f"\nLoading full ClawHub corpus...")
    all_db = load_db()  # all skills
    print(f"  {len(all_db)} skills in corpus")

    # Pool for 200-skill routing (exclude focal skills, take random 200)
    import random; random.seed(42)
    non_focal = [s for s in all_db if s not in focal_slugs]
    random.shuffle(non_focal)
    distractor_pool = non_focal[:DISTRACTORS_A + 50]  # a bit extra

    # ── Load or generate queries ───────────────────────────────────────────
    if QUERIES_FILE.exists() and QUERIES_FILE.stat().st_size > 10:
        print(f"\nLoading queries from {QUERIES_FILE.name}...")
        all_queries = json.loads(QUERIES_FILE.read_text())
        print(f"  {len(all_queries)} queries")
    else:
        print("\n── Generating queries ──")
        all_queries = []
        for skill in ic1_skills:
            slug = skill['slug']
            if slug not in focal_db: continue
            qs = generate_queries(slug, focal_db[slug], skill['specific_caps'])
            all_queries.extend(qs)
            print(f"  {slug}: {len(qs)} queries")
        QUERIES_FILE.write_text(json.dumps(all_queries, indent=2, ensure_ascii=False))
        print(f"{len(all_queries)} queries saved")

    # ── Build augmented descriptions ───────────────────────────────────────
    print("\n── Building augmented descriptions ──")
    aug_file = OUTDIR / "augmented_descs.json"
    if aug_file.exists():
        augmented = json.loads(aug_file.read_text())
        print(f"  Loaded {len(augmented)} augmented descs")
    else:
        augmented = {}
        for skill in ic1_skills:
            slug = skill['slug']
            if slug not in focal_db: continue
            aug = augment_desc(focal_db[slug], skill['specific_caps'])
            augmented[slug] = aug
            print(f"  {slug}: {len(focal_db[slug])} → {len(aug)} chars")
        aug_file.write_text(json.dumps(augmented, indent=2, ensure_ascii=False))

    # ══ EXP B: Marketplace BM25 (fast, no LLM) ════════════════════════════
    # Build augmented corpus: replace focal skill descriptions with augmented
    aug_corpus_db = dict(all_db)
    for slug, aug_desc in augmented.items():
        aug_corpus_db[slug] = aug_desc

    b_results = run_marketplace(ic1_skills, all_db, aug_corpus_db, all_queries)
    # Save
    with open(RESULTS_B, 'w') as f:
        for r in b_results:
            f.write(json.dumps(r, ensure_ascii=False)+'\n')
    print(f"\n→ Exp B results saved: {len(b_results)} decisions")

    # ══ EXP A: Routing with 200-skill pool ════════════════════════════════
    print("\n" + "="*65)
    print("EXP A: Routing (200-skill distractor pool)")
    print(f"Total decisions: {len(all_queries)*2}")

    done_keys = set()
    a_results = []
    if RESULTS_A.exists():
        for line in RESULTS_A.read_text().strip().split('\n'):
            try:
                r = json.loads(line)
                done_keys.add((r['slug'], r['variant'], r['query'][:30]))
                a_results.append(r)
            except: pass
    print(f"Already done: {len(done_keys)}\n")

    total = len(all_queries) * 2
    done_n = len(done_keys)

    with open(RESULTS_A, 'a') as f:
        for q_rec in all_queries:
            slug = q_rec['slug']
            if slug not in focal_db or slug not in augmented: continue

            for variant, desc in [('original', focal_db[slug]),
                                   ('augmented', augmented[slug])]:
                key = (slug, variant, q_rec['query'][:30])
                if key in done_keys: continue

                chosen = route_200(q_rec['query'], slug, desc, all_db, distractor_pool)
                correct = (chosen == slug)
                done_n += 1

                rec = {"slug":slug, "variant":variant,
                       "query_type":q_rec['query_type'], "cap":q_rec['cap'],
                       "query":q_rec['query'], "chosen":chosen,
                       "correct":correct, "is_control":q_rec['is_control']}
                a_results.append(rec); done_keys.add(key)
                f.write(json.dumps(rec, ensure_ascii=False)+'\n'); f.flush()

                mark = '✓' if correct else '✗'
                print(f"  [{done_n:4d}/{total}] {slug:28s} [{variant:9s}] "
                      f"{q_rec['query_type']:10s} {mark}  chosen={chosen}")

    # ══ Report ════════════════════════════════════════════════════════════
    rep = report(a_results, b_results)
    REPORT_FILE.write_text(json.dumps(rep, indent=2, ensure_ascii=False))
    print(f"\n→ Report: {REPORT_FILE}")

if __name__ == "__main__":
    main()
