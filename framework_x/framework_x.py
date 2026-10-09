#!/usr/bin/env python3
"""
Framework X — Skill Description Integrity Detection
=====================================================
Fixes all methodological problems from T2/T5/T6 experiments:

  D1 (Overclaim)     : LLM alignment matrix, 1 call/skill, evidence-backed
  D2 (Injection)     : Rule-based pattern scan (no LLM, deterministic)
  D3 (Inflation)     : Domain alignment — desc vs body, correct version

Transport: OpenAI-compatible API (openclaw, vLLM on Palmetto, or OpenAI).
           Set OPENAI_BASE_URL + OPENAI_API_KEY (or leave blank for openclaw).

Usage:
  # D1+D2+D3 on 100 random skills
  python3 framework_x.py --n 100

  # Single skill debug
  python3 framework_x.py --slug game-engine --detectors d1 d3

  # Evaluate D1 against T2 ground truth
  python3 framework_x.py --eval d1 --gt_path results/threat_scan/T2_overclaim.jsonl

  # Palmetto: OPENAI_BASE_URL=http://localhost:8000/v1 python3 framework_x.py --n 500
"""

import argparse, json, os, re, sqlite3, statistics, time
from pathlib import Path
from collections import Counter
from typing import Optional

# ── Config ─────────────────────────────────────────────────────────────────────

REPO_ROOT = Path(__file__).resolve().parent.parent
DB_PATH   = REPO_ROOT / "data/skills_sh/skills_sh.sqlite3"
RESULTS   = Path(__file__).parent / "results"
RESULTS.mkdir(exist_ok=True)

# ── OpenAI-compatible client ───────────────────────────────────────────────────

def make_client():
    from openai import OpenAI
    base_url = os.getenv("OPENAI_BASE_URL")   # vLLM endpoint
    api_key  = os.getenv("OPENAI_API_KEY", "dummy")
    if base_url:
        return OpenAI(base_url=base_url, api_key=api_key), os.getenv("OPENAI_MODEL", "gpt-oss-20b")
    # Fallback: openclaw subprocess wrapper
    return None, None

def llm_call(client, model: str, prompt: str, system: str = "",
             max_tokens: int = 512, retries: int = 3) -> Optional[str]:
    """LLM call with retry. Returns raw text or None."""
    if client is None:
        # openclaw subprocess fallback
        import subprocess
        for _ in range(retries):
            try:
                r = subprocess.run(
                    ["openclaw", "infer", "model", "run", "--prompt", prompt, "--json"],
                    capture_output=True, text=True, timeout=120)
                return json.loads(r.stdout).get("outputs",[{}])[0].get("text","").strip()
            except: time.sleep(3)
        return None

    messages = []
    if system: messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    for attempt in range(retries):
        try:
            r = client.chat.completions.create(
                model=model, messages=messages,
                temperature=0, max_tokens=max_tokens)
            return r.choices[0].message.content.strip()
        except Exception as e:
            print(f"    [LLM retry {attempt+1}] {e}")
            time.sleep(2 ** attempt)
    return None

def parse_json(text: str) -> Optional[dict]:
    if not text: return None
    for chunk in re.split(r"```(?:json)?", text):
        chunk = chunk.strip().rstrip("`").strip()
        if chunk.startswith("{") or chunk.startswith("["):
            try: return json.loads(chunk)
            except: pass
    try: return json.loads(text)
    except: return None

# ── D2: Injection Detector (rule-based, no LLM) ────────────────────────────────

D2_PATTERNS = [
    r'\bignore\b.{0,40}\b(previous|prior|above|all)\b.{0,20}\b(instruction|rule|constraint|prompt)',
    r'\bdisregard\b.{0,30}\b(previous|prior|your)\b',
    r'\bforget\b.{0,20}\b(everything|all|what|your)\b',
    r'\byou are now\b',
    r'\bact as\b.{0,20}\b(a |an )?(different|new|real|human)',
    r'\bpretend\b.{0,20}\b(you are|to be)\b',
    r'\byour new (role|persona|identity|instructions?)\b',
    r'\bnew instructions?:\s',
    r'<\s*(system|instructions?|prompt)\s*>',
    r'\[INST\]',
    r'###\s*(System|Instruction|Override)',
    r'\bsend.{0,30}\b(to|via)\b.{0,30}\b(http|webhook|url|endpoint)\b',
    r'\bexfiltrat\b',
    r'[\u200b\u200c\u200d\ufeff\u00ad]',
    r'[\u202a-\u202e]',
]
D2_RES = [(p, re.compile(p, re.IGNORECASE | re.DOTALL)) for p in D2_PATTERNS]

def detect_d2(desc: str) -> dict:
    hits = []
    for pat, rx in D2_RES:
        m = rx.search(desc)
        if m: hits.append({"pattern": pat[:60], "match": m.group()[:80]})
    return {"slug": None, "d2_hits": hits, "d2_flagged": len(hits) > 0,
            "d2_n_patterns": len(hits)}

# ── SKILL.md parser: split frontmatter description from body ─────────────────────

def parse_skill_md(raw_content: str) -> tuple[str, str]:
    """
    Parse a SKILL.md file into (description, body).

    Returns:
        description : user-written description from YAML frontmatter
                      (the routing signal — what the marketplace shows)
        body        : full markdown body after the closing '---'
                      (the complete implementation spec)

    Handles:
        - Standard frontmatter: --- ... description: '...' ... ---
        - No frontmatter: description='', body=full content
        - Multi-file skills: caller concatenates files before passing in
    """
    if not raw_content:
        return "", ""

    # Detect YAML frontmatter block
    if raw_content.startswith('---'):
        end = raw_content.find('\n---', 3)
        if end != -1:
            frontmatter = raw_content[3:end].strip()
            body = raw_content[end + 4:].strip()

            # Extract description from frontmatter
            desc = ""
            for line in frontmatter.splitlines():
                line = line.strip()
                if line.startswith('description:'):
                    val = line[len('description:'):].strip()
                    # Remove surrounding quotes
                    desc = val.strip('"\'')
                    break
            return desc, body

    # No frontmatter — return empty description, full content as body
    return "", raw_content


def load_skill_files(skill_text: str, extra_files: list[str] = None) -> str:
    """
    Build complete skill body for analysis.
    Stage 1: primary skill_text (from DB or main SKILL.md)
    Stage 2: concatenate any extra files (tools/, scripts/, etc.)

    This ensures ALL skill content is analysed, not just the first file.
    """
    parts = [skill_text or ""]
    if extra_files:
        for content in extra_files:
            if content and content.strip():
                parts.append("\n\n---\n\n" + content.strip())
    return "\n".join(p for p in parts if p)


# ── Body section parser (shared by D1 + D3) ────────────────────────────────────

FUNC_HEADERS = re.compile(
    r'^#{1,3}\s*(when to use|features?|capabilities?|what (it|this) does?|'
    r'commands?|trigger|usage|workflow|functions?|actions?|overview|purpose)',
    re.IGNORECASE | re.MULTILINE)

def extract_body_excerpt(body: str, max_chars: int = 2500) -> str:
    """
    Extract capability-dense sections from full SKILL.md body.
    Strategy:
      1. Parse markdown headers, score by capability relevance
      2. Take top-scoring sections up to max_chars
      3. Always prepend first 400 chars (title + overview)
      4. Fallback: first max_chars if no structure
    """
    if not body: return ""
    lines = body.split('\n')
    sections, cur_h, cur_l = [], None, []
    for line in lines:
        if re.match(r'^#{1,4}\s', line):
            if cur_l and cur_h: sections.append((cur_h, '\n'.join(cur_l)))
            cur_h, cur_l = line.strip(), []
        else:
            cur_l.append(line)
    if cur_l and cur_h: sections.append((cur_h, '\n'.join(cur_l)))

    def score(h, c):
        h = h.lower()
        s  = 10 if any(k in h for k in ['when to use','trigger','use when']) else 0
        s += 8  if any(k in h for k in ['features','capabilities','functions']) else 0
        s += 5  if any(k in h for k in ['what','does','overview','purpose']) else 0
        s += min(c.count('\n- ') + c.count('\n* '), 5)
        return s

    scored = sorted(sections, key=lambda x: -score(x[0], x[1]))
    parts, total = [], 0
    for h, c in scored:
        chunk = f"{h}\n{c.strip()}\n"
        if total + len(chunk) <= max_chars:
            parts.append(chunk); total += len(chunk)
        elif not parts:
            parts.append(chunk[:max_chars]); break
    return '\n'.join(parts) if parts else body[:max_chars]

# ── D1 + D3 combined prompt (ONE LLM call per skill) ──────────────────────────
# This is the correct version of cap_alignment_v3.py, using OpenAI client.

ALIGN_SYSTEM = "You are a precise AI skill auditor. Return only valid JSON, no explanation."

ALIGN_PROMPT = """\
Audit this AI skill for description–capability alignment.

DESCRIPTION (what the skill CLAIMS):
{desc}

SKILL BODY EXCERPT (what the skill ACTUALLY does):
{excerpt}

Instructions:
1. Extract 3-6 HIGH-LEVEL functional domains from the BODY (broad, e.g. "REST API calls").
2. Extract 2-5 HIGH-LEVEL claims from the DESCRIPTION (explicit claims only, no marketing).
3. Classify into the alignment matrix.

Return ONLY this JSON:
{{
  "C_body": ["domain1", ...],
  "C_desc": ["claim1", ...],
  "matrix": {{
    "match":         ["in both body and description"],
    "IC1_hidden":    ["in body but NOT mentioned in description"],
    "IC4_overclaim": ["in description but NOT supported by body"]
  }},
  "notes": "one sentence summary"
}}"""

def detect_d1_d3(client, model: str, slug: str, desc: str, body: str) -> dict:
    """
    D1 (Overclaim) + D3 (Domain Inflation) in one LLM call.
    D1 signal = IC4_overclaim list (desc claims body doesn't have)
    D3 signal = inflation_ratio = len(IC4_overclaim) / len(C_desc)
    IC1 signal (bonus) = hidden capabilities
    """
    # Use full description (typically 300-800 chars, well within context)
    # For very long descriptions (>1200 chars), keep first 1200 chars
    desc_input = desc if len(desc) <= 1200 else desc[:1200]
    excerpt = extract_body_excerpt(body, max_chars=2500)
    prompt  = ALIGN_PROMPT.format(desc=desc_input, excerpt=excerpt)
    raw     = llm_call(client, model, prompt, ALIGN_SYSTEM, max_tokens=600)
    result  = parse_json(raw)

    base = {"slug": slug, "desc_len": len(desc), "body_len": len(body)}
    if not result or "matrix" not in result:
        return {**base, "error": "parse_failed", "raw": (raw or "")[:200]}

    c_body  = result.get("C_body", [])
    c_desc  = result.get("C_desc", [])
    matrix  = result.get("matrix", {})

    n_match     = len(matrix.get("match", []))
    n_hidden    = len(matrix.get("IC1_hidden", []))
    n_overclaim = len(matrix.get("IC4_overclaim", []))
    n_body      = max(len(c_body), n_match + n_hidden, 1)
    n_desc      = max(len(c_desc), n_match + n_overclaim, 1)

    cr  = n_match / n_body          # body coverage by desc
    cp  = n_match / n_desc          # desc precision
    cdi = 1.0 - (cr + cp) / 2.0    # mismatch index

    # IC type
    has_ic1 = n_hidden > 0
    has_ic4 = n_overclaim > 0
    ic_type = ("IC-0"   if not has_ic1 and not has_ic4 else
               "IC-1"   if has_ic1 and not has_ic4 else
               "IC-4"   if not has_ic1 and has_ic4 else "IC-1+4")

    # D1: severity from overclaim count
    d1_severity = ("none"  if n_overclaim == 0 else
                   "minor" if n_overclaim <= 1 else "major")

    # D3: inflation ratio
    d3_inflation = round(n_overclaim / n_desc, 3) if n_desc > 0 else 0.0

    return {
        **base,
        "C_body": c_body, "C_desc": c_desc,
        "matrix": matrix, "notes": result.get("notes", ""),
        "n_body": n_body, "n_desc": n_desc,
        "n_match": n_match, "n_hidden": n_hidden, "n_overclaim": n_overclaim,
        "CR": round(cr, 4), "CP": round(cp, 4), "CDI": round(cdi, 4),
        "ic_type": ic_type,
        # D1 signal
        "d1_severity": d1_severity,
        "d1_overclaimed": matrix.get("IC4_overclaim", []),
        # D3 signal
        "d3_inflation_ratio": d3_inflation,
        "d3_flagged": d3_inflation >= 0.4,
        # IC-1 bonus signal
        "ic1_hidden": matrix.get("IC1_hidden", []),
    }

# ── Evaluation: D1 against T2 ground truth ────────────────────────────────────

def evaluate_d1(gt_path: Path, pred_results: list) -> dict:
    """
    Compare D1 predictions against T2 LLM ground truth.
    Reports P/R/F1 + majority baseline + bootstrap 95% CI.
    """
    import random
    gt = {json.loads(l)["slug"]: json.loads(l).get("severity","none")
          for l in gt_path.read_text().strip().split('\n') if l}
    pred_map = {r["slug"]: r.get("d1_severity","none")
                for r in pred_results if "error" not in r}

    common = [s for s in gt if s in pred_map]
    if not common:
        return {"error": "no overlap between gt and predictions"}

    y_true = [1 if gt[s]=="major" else 0 for s in common]
    y_pred = [1 if pred_map[s]=="major" else 0 for s in common]

    def f1(yt, yp):
        tp = sum(a==1 and b==1 for a,b in zip(yt,yp))
        fp = sum(a==0 and b==1 for a,b in zip(yt,yp))
        fn = sum(a==1 and b==0 for a,b in zip(yt,yp))
        p = tp/max(tp+fp,1); r = tp/max(tp+fn,1)
        return 2*p*r/max(p+r,1e-9), p, r

    f1_score, prec, rec = f1(y_true, y_pred)

    # Majority baseline
    maj = [1]*len(y_true)
    f1_maj, _, _ = f1(y_true, maj)

    # Bootstrap CI
    random.seed(42)
    boot = []
    for _ in range(2000):
        idx = [random.randint(0, len(common)-1) for _ in common]
        yt2 = [y_true[i] for i in idx]
        yp2 = [y_pred[i] for i in idx]
        s, _, _ = f1(yt2, yp2)
        boot.append(s)
    boot.sort()
    ci_lo, ci_hi = boot[50], boot[1949]

    return {
        "n": len(common), "precision": round(prec,3),
        "recall": round(rec,3), "f1": round(f1_score,3),
        "f1_majority_baseline": round(f1_maj,3),
        "f1_advantage": round(f1_score - f1_maj, 3),
        "bootstrap_ci_95": [round(ci_lo,3), round(ci_hi,3)],
    }

# ── Database loader ────────────────────────────────────────────────────────────

def run_on_text(client, model: str, slug: str,
                description: str, skill_text: str,
                extra_files: list[str] = None) -> dict:
    """
    Run all detectors on provided text.
    No DB access needed — use for live skills, custom input, or testing.

    Args:
        slug:        Identifier string
        description: User-written frontmatter description (routing signal)
                     If empty, will be auto-extracted from skill_text frontmatter
        skill_text:  Full SKILL.md content (primary file)
        extra_files: Additional file contents (tools/, scripts/, etc.)
                     — staged analysis to cover ALL skill files

    Returns:
        dict with D1 (overclaim) + D2 (injection) + D3 (inflation) signals
    """
    # Auto-extract description from frontmatter if not provided
    if not description:
        description, skill_text = parse_skill_md(skill_text)

    # Build complete body from all files
    full_body = load_skill_files(skill_text, extra_files)

    rec = {"slug": slug, "desc_len": len(description), "body_len": len(full_body),
           "desc_source": "frontmatter" if description else "none"}
    d2 = detect_d2(description)
    rec.update({k: v for k, v in d2.items() if k != "slug"})
    d13 = detect_d1_d3(client, model, slug, description, full_body)
    rec.update({k: v for k, v in d13.items()
                if k not in ("slug", "desc_len", "body_len")})
    return rec


def load_skills(n: int = 100, slugs: list = None) -> list:
    """
    Load skills from DB, correctly parsing SKILL.md frontmatter.

    Returns list of (slug, user_description, full_body) tuples where:
      user_description = frontmatter 'description:' field (routing signal)
      full_body        = complete body after frontmatter (all implementation content)

    Skills with no parseable user description are still included,
    with desc_source='none' recorded in output.
    """
    db = sqlite3.connect(str(DB_PATH))
    if slugs:
        ph = ",".join("?" * len(slugs))
        rows = db.execute(
            f"SELECT skill_slug, skill_text FROM skills "
            f"WHERE skill_slug IN ({ph})", slugs).fetchall()
    else:
        rows = db.execute("""
            SELECT skill_slug, skill_text FROM skills
            WHERE skill_text IS NOT NULL
              AND length(skill_text) > 300
              AND scan_status = 'complete'
            ORDER BY RANDOM() LIMIT ?""", (n * 4,)).fetchall()
    db.close()

    seen, pool = set(), []
    for slug, raw in rows:
        if slug in seen or not raw:
            continue
        seen.add(slug)
        # Parse frontmatter to get user-written description
        user_desc, body = parse_skill_md(raw)
        # Require a non-trivial description and body
        if len(user_desc) >= 20 and len(body) >= 200:
            pool.append((slug, user_desc, body))
        if len(pool) == n:
            break
    return pool


def load_skills_db(n: int = 100, slugs: list = None) -> list:
    """
    Load skills from DB using pre-split fields:
      description = marketplace short description (routing signal, scraped from webpage)
      skill_text  = complete SKILL.md body (full implementation content)

    Use this for skills_sh / ClawHub where the DB already stores them separately.
    Use load_skills() for local SKILL.md files with YAML frontmatter.
    """
    db = sqlite3.connect(str(DB_PATH))
    if slugs:
        ph = ",".join("?" * len(slugs))
        rows = db.execute(
            f"SELECT skill_slug, description, skill_text FROM skills "
            f"WHERE skill_slug IN ({ph})", slugs).fetchall()
    else:
        rows = db.execute("""
            SELECT skill_slug, description, skill_text FROM skills
            WHERE description IS NOT NULL
              AND skill_text IS NOT NULL
              AND description != skill_text
              AND length(description) BETWEEN 30 AND 3000
              AND length(skill_text) > length(description)
            ORDER BY RANDOM() LIMIT ?""", (n * 3,)).fetchall()
    db.close()

    seen, pool = set(), []
    for slug, desc, body in rows:
        if slug in seen or not desc or not body:
            continue
        seen.add(slug)
        # Skip if description is just a copy of the body start
        if body.strip().startswith(desc.strip()[:50]):
            continue
        pool.append((slug, desc.strip(), body.strip()))
        if len(pool) == n:
            break
    return pool

# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="Framework X")
    ap.add_argument("--n",          type=int,  default=50)
    ap.add_argument("--slug",       type=str,  default=None)
    ap.add_argument("--detectors",  nargs="+", default=["d1","d2","d3"],
                    choices=["d1","d2","d3"])
    ap.add_argument("--eval",       type=str,  default=None,
                    help="Evaluate detector (e.g. d1)")
    ap.add_argument("--gt_path",    type=str,  default=None,
                    help="Ground truth JSONL for evaluation")
    ap.add_argument("--out",        type=str,  default=None)
    args = ap.parse_args()

    client, model = make_client()
    print(f"Transport: {'OpenAI-compatible at ' + os.getenv('OPENAI_BASE_URL','') if client else 'openclaw subprocess'}")

    # Load skills
    slugs = [args.slug] if args.slug else None
    skills = load_skills(n=args.n, slugs=slugs)
    print(f"Skills loaded: {len(skills)}\n")

    results = []
    out_path = Path(args.out) if args.out else RESULTS / "framework_x_results.jsonl"

    # Resume
    done = set()
    if out_path.exists():
        for line in out_path.read_text().strip().split('\n'):
            try: done.add(json.loads(line)["slug"])
            except: pass

    todo = [(s,d,b) for s,d,b in skills if s not in done]
    print(f"Already done: {len(done)} | Remaining: {len(todo)}\n")

    with open(out_path, "a") as fout:
        for i, (slug, desc, body) in enumerate(todo):
            print(f"[{i+1:4d}/{len(todo)}] {slug}", end="  ")
            rec = {"slug": slug, "desc_len": len(desc), "body_len": len(body)}

            # D2 (always — no LLM)
            if "d2" in args.detectors:
                d2 = detect_d2(desc)
                rec.update({k: v for k,v in d2.items() if k != "slug"})

            # D1 + D3 (one LLM call)
            if "d1" in args.detectors or "d3" in args.detectors:
                d13 = detect_d1_d3(client, model, slug, desc, body)
                rec.update({k: v for k,v in d13.items()
                            if k not in ("slug","desc_len","body_len")})

            results.append(rec)
            fout.write(json.dumps(rec, ensure_ascii=False) + "\n")
            fout.flush()

            if "error" in rec:
                print(f"ERROR: {rec['error']}")
            else:
                parts = []
                if "d1_severity" in rec: parts.append(f"D1={rec['d1_severity']}")
                if "d2_flagged"  in rec: parts.append(f"D2={'⚠' if rec['d2_flagged'] else 'ok'}")
                if "d3_flagged"  in rec: parts.append(f"D3={'⚠' if rec['d3_flagged'] else 'ok'} inf={rec.get('d3_inflation_ratio',0):.2f}")
                if "ic_type"     in rec: parts.append(f"IC={rec['ic_type']}")
                print(" | ".join(parts))

            time.sleep(0.2)

    # ── Summary ───────────────────────────────────────────────────────────────
    all_r = [json.loads(l) for l in out_path.read_text().strip().split('\n') if l]
    valid = [r for r in all_r if "error" not in r]
    print(f"\n{'='*60}")
    print(f"Framework X Results: {len(valid)} valid / {len(all_r)} total\n")

    if valid:
        ic_dist = Counter(r.get("ic_type","?") for r in valid)
        print("IC Distribution:")
        for ic, n in sorted(ic_dist.items()):
            bar = "█" * int(n/len(valid)*40)
            print(f"  {ic:8s}  {n:4d}  ({n/len(valid)*100:.1f}%)  {bar}")

        d1_dist = Counter(r.get("d1_severity","?") for r in valid)
        print(f"\nD1 Severity: {dict(d1_dist)}")
        d2_flag = sum(1 for r in valid if r.get("d2_flagged"))
        print(f"D2 Flagged:  {d2_flag}/{len(valid)} ({d2_flag/len(valid)*100:.1f}%)")
        d3_flag = sum(1 for r in valid if r.get("d3_flagged"))
        print(f"D3 Flagged:  {d3_flag}/{len(valid)} ({d3_flag/len(valid)*100:.1f}%)")

        avgs = {k: round(sum(r.get(k,0) for r in valid)/len(valid),3)
                for k in ["CR","CP","CDI","n_hidden","n_overclaim","d3_inflation_ratio"]}
        print(f"\nAverages: CR={avgs['CR']} CP={avgs['CP']} CDI={avgs['CDI']}")
        print(f"          IC1_hidden/skill={avgs['n_hidden']}  IC4_overclaim/skill={avgs['n_overclaim']}")
        print(f"          D3 inflation_ratio={avgs['d3_inflation_ratio']}")

    # Evaluation mode
    if args.eval == "d1" and args.gt_path:
        print(f"\n── D1 Evaluation vs ground truth ──")
        ev = evaluate_d1(Path(args.gt_path), valid)
        for k, v in ev.items():
            print(f"  {k}: {v}")

    summary = {
        "n_valid": len(valid), "n_total": len(all_r),
        "ic_distribution": dict(ic_dist) if valid else {},
        "d1_severity": dict(d1_dist) if valid else {},
        "d2_flagged": d2_flag if valid else 0,
        "d3_flagged": d3_flag if valid else 0,
    }
    (RESULTS / "framework_x_summary.json").write_text(json.dumps(summary, indent=2))
    print(f"\n→ Results:  {out_path}")
    print(f"→ Summary:  {RESULTS/'framework_x_summary.json'}")

if __name__ == "__main__":
    main()
