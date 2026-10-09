#!/usr/bin/env python3
"""
BM25 Marketplace Recall Experiment  (Large-Scale)
==================================================
Research question:
  Does description quality (IC-1 gap) causally reduce skill discoverability
  in a realistic marketplace search scenario?

Design:
  • Corpus  : 17K+ real ClawHub skill descriptions (BM25 index)
  • Focal   : ALL 38 IC-1 candidate skills from pilot_150.jsonl
  • Variants: original description vs augmented (+ undisclosed-cap sentence)
  • Queries : 5 types per undisclosed cap  (no specificity filter → max n)
              scenario / symptom / goal / workaround / task
  • Metric  : Recall@K  (K=5, 10, 20)  +  MRR
  • Stats   : Wilson 95% CI, paired McNemar test (fixed vs broke)

Output:
  results/bm25_large/results.jsonl   — per-query result rows
  results/bm25_large/report.json     — aggregated metrics
  results/bm25_large/analysis.md     — paper-ready table + stats
"""

import json, math, re, sqlite3, subprocess, time
from pathlib import Path
from collections import defaultdict, Counter
from itertools import combinations

# ── Config ────────────────────────────────────────────────────────────────────
PILOT_FILE   = Path(__file__).parent / "results/cap_alignment_v2/pilot_150.jsonl"
DB_PATH      = "/Users/guangfu/codex/clawhubS/data/skills_sh/skills_sh.sqlite3"
OUTDIR       = Path(__file__).parent / "results/bm25_large"
QUERIES_FILE = OUTDIR / "queries.json"
AUG_FILE     = OUTDIR / "aug_descs.json"
RESULTS_FILE = OUTDIR / "results.jsonl"
REPORT_FILE  = OUTDIR / "report.json"
ANALYSIS_MD  = OUTDIR / "analysis.md"
OUTDIR.mkdir(parents=True, exist_ok=True)

CAPS_PER_SKILL  = 2   # top-N caps per skill to generate queries for
QUERY_TYPES     = ["scenario", "symptom", "goal", "workaround", "task"]
KS              = [5, 10, 20]   # Recall@K values to report

# ── BM25 ─────────────────────────────────────────────────────────────────────

def tokenize(text: str):
    return re.findall(r'\b[a-z][a-z0-9\-]{1,}\b', text.lower())

class BM25:
    def __init__(self, docs, k1=1.5, b=0.75):
        self.k1, self.b, self.N = k1, b, len(docs)
        self.tok = [tokenize(d) for d in docs]
        self.avgdl = sum(len(t) for t in self.tok) / max(self.N, 1)
        df = Counter(w for t in self.tok for w in set(t))
        self.idf = {w: math.log((self.N - f + 0.5) / (f + 0.5) + 1)
                    for w, f in df.items()}

    def score(self, qt, di):
        tf = Counter(self.tok[di]); dl = len(self.tok[di])
        return sum(self.idf.get(w, 0) * tf[w] * (self.k1 + 1)
                   / (tf[w] + self.k1 * (1 - self.b + self.b * dl / self.avgdl))
                   for w in qt)

    def search(self, query, top_k=20):
        qt = tokenize(query)
        return sorted(range(self.N), key=lambda i: -self.score(qt, i))[:top_k]

# ── LLM helpers ──────────────────────────────────────────────────────────────

def _llm_raw(prompt, retries=3):
    for _ in range(retries):
        try:
            r = subprocess.run(
                ["openclaw", "infer", "model", "run",
                 "--prompt", prompt, "--json"],
                capture_output=True, text=True, timeout=120)
            return json.loads(r.stdout).get("outputs", [{}])[0].get("text", "").strip()
        except subprocess.TimeoutExpired:
            time.sleep(5)
        except Exception:
            pass
    return None

def llm_json(prompt):
    txt = _llm_raw(prompt)
    if not txt: return None
    for part in txt.split("```"):
        part = part.strip().lstrip("json").strip()
        try: return json.loads(part)
        except: pass
    try: return json.loads(txt)
    except: return None

def sub(t, **kw):
    for k, v in kw.items(): t = t.replace("{" + k + "}", str(v))
    return t

# ── Load IC-1 skills (ALL candidates, no specificity filter) ─────────────────

def load_ic1_skills():
    rows = []
    for line in PILOT_FILE.read_text().strip().split("\n"):
        try:
            r = json.loads(line)
            if "error" in r or not r.get("only_body"): continue
            rows.append(r)
        except: pass
    print(f"IC-1 candidates loaded: {len(rows)}")
    return rows

# ── Load ClawHub DB ───────────────────────────────────────────────────────────

def load_db():
    db = sqlite3.connect(DB_PATH)
    cur = db.cursor()
    cur.execute("""SELECT skill_slug, description FROM skills
                   WHERE description IS NOT NULL AND length(description) > 50""")
    result = {r[0]: r[1] for r in cur.fetchall()}
    db.close()
    return result

# ── Generate queries ──────────────────────────────────────────────────────────

QGEN_PROMPT = """\
Skill slug: {slug}
Description (INCOMPLETE — missing some capabilities): {desc}
One UNDISCLOSED capability this skill actually supports: "{cap}"

Generate realistic user queries for each of these 5 styles.
Each query should express a need that REQUIRES the undisclosed capability.
Do NOT mention the exact technical term "{cap}" in the queries.
Be natural — write as a real user would type into a search box.

Styles:
  scenario    — user describes a real work situation where they need this
  symptom     — user describes pain/friction caused by lacking this capability
  goal        — user says what outcome they want to achieve
  workaround  — user describes a clunky workaround they're doing and wants a better way
  task        — user states a concrete task they need to execute

Also one "control" query about a capability clearly stated in the description.

Return ONLY JSON (7 keys):
{{
  "scenario":   "...",
  "symptom":    "...",
  "goal":       "...",
  "workaround": "...",
  "task":       "...",
  "control":    "..."
}}"""

def generate_queries(slug, desc, caps):
    queries = []
    for cap in caps[:CAPS_PER_SKILL]:
        r = llm_json(sub(QGEN_PROMPT, slug=slug, desc=desc[:800], cap=cap))
        if not isinstance(r, dict): continue
        for qt in QUERY_TYPES + ["control"]:
            q = r.get(qt, "")
            if isinstance(q, str) and len(q) > 10:
                queries.append({
                    "slug": slug, "cap": cap,
                    "query_type": qt, "query": q,
                    "is_control": qt == "control"
                })
    return queries

# ── Augment descriptions ──────────────────────────────────────────────────────

AUG_PROMPT = """\
Append exactly ONE sentence to this description to explicitly mention the missing capability.
The sentence should be concise, factual, and sound like a real description.
Return ONLY the full augmented description (original + new sentence), nothing else.

Original description:
{desc}

Missing capability to add: {cap}"""

def augment_desc(desc, caps):
    result = desc
    for cap in caps[:CAPS_PER_SKILL]:
        aug = _llm_raw(sub(AUG_PROMPT, desc=result[:1000], cap=cap))
        if aug and len(aug) > len(result):
            result = aug.strip()
    return result

# ── Statistics ────────────────────────────────────────────────────────────────

def wilson_ci(k, n, z=1.96):
    """Wilson score 95% CI for a proportion k/n."""
    if n == 0: return (0.0, 0.0)
    p = k / n
    denom = 1 + z**2 / n
    center = (p + z**2 / (2*n)) / denom
    pm = z * math.sqrt(p*(1-p)/n + z**2/(4*n**2)) / denom
    return (max(0, center - pm), min(1, center + pm))

def mcnemar_p(fixed, broke):
    """Approximate McNemar p-value (continuity corrected)."""
    b, c = fixed, broke
    if b + c == 0: return 1.0
    chi2 = (abs(b - c) - 1.0)**2 / (b + c)
    # Approximate p from chi2 (1 df) using survival function approximation
    # Simple: use erfc
    x = math.sqrt(chi2 / 2)
    p = math.erfc(x)
    return round(p, 4)

def recall_at_k(hits_list, k):
    """hits_list: list of booleans (True if rank <= k)."""
    return sum(hits_list) / max(len(hits_list), 1)

def mrr(ranks):
    """Mean Reciprocal Rank. ranks: list of int or None."""
    valid = [1/r for r in ranks if r is not None]
    return sum(valid) / max(len(ranks), 1)

# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    print("=" * 65)
    print("BM25 MARKETPLACE RECALL EXPERIMENT  (Large-Scale)")
    print("=" * 65 + "\n")

    # ── Load data ─────────────────────────────────────────────────────────
    ic1 = load_ic1_skills()
    focal_slugs = {sk["slug"] for sk in ic1}

    print("Loading ClawHub DB...")
    all_db = load_db()
    all_slugs = list(all_db.keys())
    print(f"  {len(all_db)} skills\n")

    # Filter focal skills that have descriptions
    ic1 = [sk for sk in ic1 if sk["slug"] in all_db]
    print(f"Focal skills with DB descriptions: {len(ic1)}\n")

    # ── Queries ───────────────────────────────────────────────────────────
    if QUERIES_FILE.exists() and QUERIES_FILE.stat().st_size > 10:
        print(f"Loading queries from {QUERIES_FILE.name}...")
        queries = json.loads(QUERIES_FILE.read_text())
        print(f"  {len(queries)} queries\n")
    else:
        print("── Generating queries ──")
        queries = []
        for sk in ic1:
            slug = sk["slug"]
            desc = all_db[slug]
            caps = sk["only_body"][:CAPS_PER_SKILL]
            qs = generate_queries(slug, desc, caps)
            queries.extend(qs)
            n_body = sum(1 for q in qs if not q["is_control"])
            print(f"  {slug}: {n_body} body + {len(qs)-n_body} ctrl queries")
            time.sleep(0.3)
        QUERIES_FILE.write_text(json.dumps(queries, indent=2, ensure_ascii=False))
        print(f"\n{len(queries)} queries saved\n")

    # ── Augmented descriptions ────────────────────────────────────────────
    if AUG_FILE.exists():
        aug_descs = json.loads(AUG_FILE.read_text())
        print(f"Loaded {len(aug_descs)} augmented descriptions\n")
    else:
        print("── Augmenting descriptions ──")
        aug_descs = {}
        for sk in ic1:
            slug = sk["slug"]
            aug = augment_desc(all_db[slug], sk["only_body"])
            aug_descs[slug] = aug
            print(f"  {slug}: {len(all_db[slug])} → {len(aug)} chars")
            time.sleep(0.3)
        AUG_FILE.write_text(json.dumps(aug_descs, indent=2, ensure_ascii=False))
        print()

    # ── Build BM25 indices ────────────────────────────────────────────────
    print("Building BM25 indices (original + augmented) ...")
    orig_descs = [all_db[s] for s in all_slugs]
    aug_descs_list = [aug_descs.get(s, all_db[s]) for s in all_slugs]
    bm25_orig = BM25(orig_descs)
    bm25_aug  = BM25(aug_descs_list)
    slug_idx  = {s: i for i, s in enumerate(all_slugs)}
    print(f"  Done. Corpus size: {len(all_slugs)}\n")

    # ── Run search ────────────────────────────────────────────────────────
    print("── Running BM25 Recall Evaluation ──")
    results = []
    K_MAX = max(KS)

    for i, q in enumerate(queries):
        slug = q["slug"]
        if slug not in slug_idx: continue
        target_idx = slug_idx[slug]
        query_text = q["query"]

        # Search with original descriptions
        hits_o = bm25_orig.search(query_text, K_MAX)
        # Search with augmented descriptions
        hits_a = bm25_aug.search(query_text, K_MAX)

        rank_o = (hits_o.index(target_idx) + 1) if target_idx in hits_o else None
        rank_a = (hits_a.index(target_idx) + 1) if target_idx in hits_a else None

        rec = {
            "slug":        slug,
            "query_type":  q["query_type"],
            "cap":         q["cap"],
            "query":       q["query"],
            "is_control":  q["is_control"],
            "rank_orig":   rank_o,
            "rank_aug":    rank_a,
            **{f"hit_orig_k{k}": (rank_o is not None and rank_o <= k) for k in KS},
            **{f"hit_aug_k{k}":  (rank_a is not None and rank_a <= k)  for k in KS},
        }
        results.append(rec)
        mark_o = f"@{rank_o}" if rank_o else "✗"
        mark_a = f"@{rank_a}" if rank_a else "✗"
        if (i+1) % 10 == 0 or i < 5:
            print(f"  [{i+1:4d}/{len(queries)}] {slug:30s} {q['query_type']:12s}  "
                  f"orig={mark_o:5s}  aug={mark_a:5s}")

    with open(RESULTS_FILE, "w") as f:
        for r in results:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"\n→ {len(results)} results saved\n")

    # ── Aggregate & Report ────────────────────────────────────────────────
    body = [r for r in results if not r["is_control"]]
    ctrl = [r for r in results if r["is_control"]]
    print("=" * 65)
    print("RESULTS\n")

    report = {}

    for k in KS:
        ok = f"hit_orig_k{k}"; ak = f"hit_aug_k{k}"
        print(f"─── Recall@{k} ────────────── (body-trigger queries only, n={len(body)}) ────")
        print(f"  {'query_type':12s}  {'original':>9s} {'[95%CI]':14s}  "
              f"{'augmented':>9s} {'[95%CI]':14s}  {'gap':>7s}  McNemar-p")

        qt_report = {}
        for qt in QUERY_TYPES:
            rows = [r for r in body if r["query_type"] == qt]
            if not rows: continue
            n = len(rows)
            o_hits = [r[ok] for r in rows]
            a_hits = [r[ak] for r in rows]
            ro = sum(o_hits) / n * 100
            ra = sum(a_hits) / n * 100
            lo, ho = wilson_ci(sum(o_hits), n)
            la, ha = wilson_ci(sum(a_hits), n)

            fixed = sum(1 for o, a in zip(o_hits, a_hits) if not o and a)
            broke  = sum(1 for o, a in zip(o_hits, a_hits) if o and not a)
            p = mcnemar_p(fixed, broke)
            sig = "**" if p < 0.05 else ("*" if p < 0.10 else "  ")

            print(f"  {qt:12s}  {ro:8.1f}% [{lo*100:.0f}-{ho*100:.0f}%]  "
                  f"{ra:8.1f}% [{la*100:.0f}-{ha*100:.0f}%]  "
                  f"{ra-ro:+6.1f}%  p={p:.3f}{sig}")
            qt_report[qt] = {"orig": ro, "aug": ra, "gap": ra-ro,
                             "fixed": fixed, "broke": broke, "p": p, "n": n}

        # Aggregate across all query types
        all_o = [r[ok] for r in body]
        all_a = [r[ak] for r in body]
        n_all = len(body)
        ro_all = sum(all_o) / n_all * 100
        ra_all = sum(all_a) / n_all * 100
        lo, ho = wilson_ci(sum(all_o), n_all)
        la, ha = wilson_ci(sum(all_a), n_all)
        fixed_all = sum(1 for o,a in zip(all_o, all_a) if not o and a)
        broke_all  = sum(1 for o,a in zip(all_o, all_a) if o and not a)
        p_all = mcnemar_p(fixed_all, broke_all)
        sig = "**" if p_all < 0.05 else ("*" if p_all < 0.10 else "  ")
        print(f"  {'ALL':12s}  {ro_all:8.1f}% [{lo*100:.0f}-{ho*100:.0f}%]  "
              f"{ra_all:8.1f}% [{la*100:.0f}-{ha*100:.0f}%]  "
              f"{ra_all-ro_all:+6.1f}%  p={p_all:.3f}{sig}  "
              f"(fixed={fixed_all} broke={broke_all})")
        print()
        report[f"recall@{k}"] = {"by_query_type": qt_report,
                                   "all": {"orig": ro_all, "aug": ra_all,
                                           "gap": ra_all-ro_all, "p": p_all,
                                           "fixed": fixed_all, "broke": broke_all}}

    # MRR
    print("─── MRR (body-trigger) ──────────────────────────────────────────")
    mrr_o = mrr([r["rank_orig"] for r in body])
    mrr_a = mrr([r["rank_aug"]  for r in body])
    print(f"  original : {mrr_o:.3f}")
    print(f"  augmented: {mrr_a:.3f}  (Δ={mrr_a-mrr_o:+.3f})")
    report["mrr"] = {"orig": mrr_o, "aug": mrr_a, "delta": mrr_a - mrr_o}

    # Control (sanity check)
    print(f"\n─── Control queries (sanity check, n={len(ctrl)}) ──────────────────")
    for k in [10]:
        ok = f"hit_orig_k{k}"; ak = f"hit_aug_k{k}"
        ro = sum(r[ok] for r in ctrl) / max(len(ctrl),1) * 100
        ra = sum(r[ak] for r in ctrl) / max(len(ctrl),1) * 100
        print(f"  Recall@{k}  original={ro:.1f}%  augmented={ra:.1f}%  (ctrl should be high for both)")

    REPORT_FILE.write_text(json.dumps(report, indent=2))

    # ── Write paper-ready analysis.md ────────────────────────────────────
    write_analysis_md(report, len(body), len(ctrl))
    print(f"\n→ Report : {REPORT_FILE}")
    print(f"→ Analysis: {ANALYSIS_MD}")

def write_analysis_md(report, n_body, n_ctrl):
    k = 10
    r10 = report.get("recall@10", {})
    all_r = r10.get("all", {})
    rows = r10.get("by_query_type", {})

    lines = [
        "# BM25 Marketplace Recall Experiment — Results",
        "",
        f"> **n_body** = {n_body} body-trigger queries | **n_ctrl** = {n_ctrl} control queries",
        f"> Corpus: 17K+ real ClawHub skills (BM25, k1=1.5, b=0.75)",
        "",
        "## Recall@10 by Query Type (body-trigger only)",
        "",
        "| Query type | Original | Augmented | Gap | McNemar p |",
        "|------------|----------|-----------|-----|-----------|",
    ]
    for qt in QUERY_TYPES:
        if qt not in rows: continue
        d = rows[qt]
        sig = "**" if d["p"] < 0.05 else ("*" if d["p"] < 0.10 else "")
        lines.append(
            f"| {qt} | {d['orig']:.1f}% | {d['aug']:.1f}% "
            f"| {d['gap']:+.1f}% | p={d['p']:.3f}{sig} |")
    d = all_r
    sig = "**" if d.get("p",1) < 0.05 else ("*" if d.get("p",1) < 0.10 else "")
    lines += [
        f"| **ALL** | **{d.get('orig',0):.1f}%** | **{d.get('aug',0):.1f}%** "
        f"| **{d.get('gap',0):+.1f}%** | p={d.get('p',1):.3f}{sig} |",
        "",
        f"Paired analysis: augmented fixed **{d.get('fixed',0)}** failures, "
        f"broke **{d.get('broke',0)}**, net={d.get('fixed',0)-d.get('broke',0):+d}",
        "",
        "## MRR",
        "",
        f"| | MRR |",
        f"|---|---|",
        f"| Original | {report.get('mrr',{}).get('orig',0):.3f} |",
        f"| Augmented | {report.get('mrr',{}).get('aug',0):.3f} |",
        f"| Δ | {report.get('mrr',{}).get('delta',0):+.3f} |",
        "",
        "## Recall@K Summary",
        "",
        "| K | Original | Augmented | Gap |",
        "|---|----------|-----------|-----|",
    ]
    for k in [5, 10, 20]:
        rk = report.get(f"recall@{k}", {}).get("all", {})
        lines.append(f"| {k} | {rk.get('orig',0):.1f}% | {rk.get('aug',0):.1f}% | {rk.get('gap',0):+.1f}% |")

    ANALYSIS_MD.write_text("\n".join(lines) + "\n")

if __name__ == "__main__":
    main()
