# ============================================================
# DEPRECATED — 2026-04-25
# ============================================================
# 方法论问题：此脚本从 skill DESCRIPTION 提取高层能力，从 skill BODY
# 提取细粒度能力，然后做对齐比较（CDI/CR/CP）。
#
# 根本缺陷（Granularity Mismatch）：
#   - 描述是 HIGH-LEVEL（"manages GitHub repositories"）
#   - Body 实现是 FINE-GRAINED（git.clone(), SSH key读写, API调用）
#
# 导致的问题：
#   1. IC-1 虚假高发：几乎所有描述都会被判定为"underdisclosure"，
#      因为描述本来就不该列举每个实现细节。(99.2% 结论不可信)
#   2. CDI/CR/CP 指标失效：分子分母在不同粒度层，无法反映真实对齐度。
#   3. IC 分类矩阵（Description vs Body）本身假设两端对等，实际不成立。
#
# 后续实验应采用以下替代方案之一：
#   - 基于 ABUSE PATTERN 的直接分析（T2/T5/T6 approach）
#   - 路由实验（routing gap）验证实际危害
#   - 人工标注对齐（仅对 body 声明的 high-level 功能做描述核查）
# ============================================================

#!/usr/bin/env python3
"""
Cap Alignment — Batch LLM Scanner (5 skills per LLM call)
==========================================================
Sends 5 skills per prompt → 5x throughput vs single-skill mode.
Supports both platforms via --platform clawhub | skillssh

ClawHub:  2,052 skills → ~3 hours (vs 14h serial)
skills_sh:9,546 skills → ~13 hours (vs 63h serial)

Usage:
  python3 cap_alignment_batch.py --platform clawhub  [--batch 5] [--n 0]
  python3 cap_alignment_batch.py --platform skillssh [--batch 5] [--n 0]
"""

import json, os, re, sqlite3, subprocess, time, threading
from pathlib import Path
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
import argparse

# ── Config ─────────────────────────────────────────────────────────────────────

CLAWHUB_DB  = "/Users/guangfu/codex/clawhubS/data/experiment1_split/local_shard0.sqlite3"
SKILLSSH_DB = "/Users/guangfu/codex/clawhubS/data/skills_sh/skills_sh.sqlite3"
BASE        = "/Users/guangfu/codex/clawhubS/"
BODY_DIRS   = [
    "data/collection_archive/html_snapshots/api_files/",
    "data/collection_archive/runs/2026-03-06_public_archive_partial/html_snapshots/api_files/",
]

WRITE_LOCK = threading.Lock()
LLM_LOCK   = threading.Semaphore(2)   # max 2 concurrent LLM calls

# ── Body loaders ───────────────────────────────────────────────────────────────

def load_clawhub_body(text_path: str) -> str | None:
    fn = text_path.split('/')[-1]
    for d in BODY_DIRS:
        p = BASE + d + fn
        if os.path.exists(p):
            return open(p, encoding='utf-8', errors='ignore').read()
    return None

def load_clawhub(n: int = 0):
    db = sqlite3.connect(CLAWHUB_DB)
    rows = db.execute("""
        SELECT s.skill_id, s.description, sf.text_path
        FROM skills s JOIN skill_files sf ON s.skill_id = sf.skill_id
        WHERE sf.path = 'SKILL.md' AND sf.text_path IS NOT NULL
          AND s.description IS NOT NULL
          AND length(s.description) BETWEEN 30 AND 1500
        ORDER BY s.skill_id
    """).fetchall()
    db.close()
    pool, seen = [], set()
    for skill_id, desc, tp in rows:
        if skill_id in seen: continue
        body = load_clawhub_body(tp)
        if not body or len(body) < 300: continue
        if desc.strip() == body.strip()[:len(desc.strip())]: continue
        seen.add(skill_id); pool.append((skill_id, desc, body))
        if n > 0 and len(pool) >= n: break
    return pool

def load_skillssh(n: int = 0):
    db = sqlite3.connect(SKILLSSH_DB)
    q = """
        SELECT skill_slug, description, skill_text FROM skills
        WHERE description IS NOT NULL AND skill_text IS NOT NULL
          AND description != skill_text
          AND length(description) BETWEEN 50 AND 1500
          AND length(skill_text) > 500
          AND description NOT LIKE '%ERROR%'
        ORDER BY skill_slug
    """
    if n > 0: q += f" LIMIT {n}"
    rows = db.execute(q).fetchall()
    db.close()
    return [(r[0], r[1], r[2]) for r in rows]

# ── Section extractor ─────────────────────────────────────────────────────────

def extract_sections(body: str, max_chars: int = 1500) -> str:
    if not body: return ""
    USE_WHEN = re.compile(r'(use when|trigger when|when to use|user asks?)', re.I)
    lines = body.split('\n')
    sections, cur_h, cur_l = [], None, []
    for line in lines:
        if re.match(r'^#{1,4}\s', line):
            if cur_l and cur_h: sections.append((cur_h, '\n'.join(cur_l)))
            cur_h, cur_l = line.strip(), []
        else: cur_l.append(line)
    if cur_l and cur_h: sections.append((cur_h, '\n'.join(cur_l)))

    def score(h, c):
        s = 0
        hl = h.lower()
        if any(k in hl for k in ['when to use','trigger','features','capabilities']): s += 10
        if any(k in hl for k in ['what','does','overview','functions','usage']): s += 5
        s += min(c.count('\n- ') + c.count('\n* '), 5)
        s += 3 if USE_WHEN.search(c) else 0
        return s

    scored = sorted(sections, key=lambda x: -score(*x))
    parts, total = [], 0
    for h, c in scored:
        chunk = f"{h}\n{c.strip()}\n"
        if total + len(chunk) <= max_chars: parts.append(chunk); total += len(chunk)
        elif total == 0: parts.append(chunk[:max_chars]); break
    return '\n'.join(parts) if parts else body[:max_chars]

# ── Batch prompt (5 skills per call) ──────────────────────────────────────────

BATCH_PROMPT_HEADER = """\
Analyze the following {n} AI skills. For EACH skill, build a capability alignment matrix.

For each skill:
1. Extract 3-5 HIGH-LEVEL capability domains FROM THE BODY (what it actually does)
2. Extract 2-4 HIGH-LEVEL claims FROM THE DESCRIPTION (what it claims to do)
3. Classify into: MATCH / IC1_HIDDEN / IC4_OVERCLAIM

"""

SKILL_TEMPLATE = """\
--- SKILL {i}: {skill_id} ---
DESCRIPTION: {desc}
BODY EXCERPT: {body}

"""

BATCH_PROMPT_FOOTER = """\
Return a JSON ARRAY with exactly {n} objects in order:
[
  {
    "skill_id": "SKILL-ID-HERE",
    "C_body": ["domain1", "domain2", ...],
    "C_desc": ["claim1", "claim2", ...],
    "matrix": {
      "match":         ["in both"],
      "IC1_hidden":    ["in body not desc"],
      "IC4_overclaim": ["in desc not body"]
    }
  },
  ...
]
Return ONLY the JSON array, no other text."""

def build_batch_prompt(batch: list) -> str:
    n = len(batch)
    header = BATCH_PROMPT_HEADER.replace("{n}", str(n))
    blocks  = ""
    for i, (sid, desc, body) in enumerate(batch, 1):
        excerpt = extract_sections(body, 600)   # shorter per skill in batch
        block = (f"--- SKILL {i}: {sid} ---\n"
                 f"DESCRIPTION: {desc[:350]}\n"
                 f"BODY EXCERPT: {excerpt}\n\n")
        blocks += block
    footer = BATCH_PROMPT_FOOTER.replace("{n}", str(n))
    return header + blocks + footer

# ── LLM ───────────────────────────────────────────────────────────────────────

def llm(prompt: str, retries: int = 3) -> str | None:
    for attempt in range(retries):
        with LLM_LOCK:
            try:
                r = subprocess.run(
                    ["openclaw", "infer", "model", "run", "--prompt", prompt, "--json"],
                    capture_output=True, text=True, timeout=300)
                txt = json.loads(r.stdout).get("outputs", [{}])[0].get("text","").strip()
                if txt: return txt
            except: pass
        time.sleep(3 + attempt * 5)
    return None

def parse_json_list(text: str) -> list | None:
    if not text: return None
    # Try to find JSON array in output
    for p in text.split("```"):
        p = p.strip().lstrip("json").strip()
        if p.startswith("["):
            try: return json.loads(p)
            except: pass
    # Try full text
    if text.strip().startswith("["):
        try: return json.loads(text.strip())
        except: pass
    # Try to find [...] in middle of text
    m = re.search(r'\[[\s\S]*\]', text)
    if m:
        try: return json.loads(m.group())
        except: pass
    return None

# ── IC metrics ─────────────────────────────────────────────────────────────────

def compute_ic(r: dict) -> dict:
    mx = r.get("matrix", {})
    cb = r.get("C_body", [])
    cd = r.get("C_desc", [])
    n_match    = len(mx.get("match", []))
    n_hidden   = len(mx.get("IC1_hidden", []))
    n_overclaim= len(mx.get("IC4_overclaim", []))
    n_body = max(len(cb), n_match + n_hidden, 1)
    n_desc = max(len(cd), n_match + n_overclaim, 1)
    cr  = n_match / n_body
    cp  = n_match / n_desc
    cdi = 1.0 - (cr + cp) / 2.0
    has1 = n_hidden > 0;  has4 = n_overclaim > 0
    ic = ("IC-1+4" if has1 and has4 else
          "IC-1"   if has1 else
          "IC-4"   if has4 else "IC-0")
    return {"n_body": n_body, "n_desc": n_desc, "n_match": n_match,
            "n_hidden": n_hidden, "n_overclaim": n_overclaim,
            "CR": round(cr,4), "CP": round(cp,4), "CDI": round(cdi,4),
            "ic_type": ic}

# ── Process one batch ──────────────────────────────────────────────────────────

def process_batch(batch: list) -> list:
    prompt = build_batch_prompt(batch)
    raw = llm(prompt)
    parsed = parse_json_list(raw)

    if not parsed or len(parsed) == 0:
        # Fallback: mark all as errors
        return [{"skill_id": sid, "desc_len": len(desc), "body_len": len(body),
                 "error": "batch_parse_failed", "raw": (raw or "")[:200]}
                for sid, desc, body in batch]

    results = []
    for i, (sid, desc, body) in enumerate(batch):
        if i < len(parsed):
            r = parsed[i]
            if not isinstance(r, dict) or "matrix" not in r:
                results.append({"skill_id": sid, "error": "bad_item",
                                 "desc_len": len(desc), "body_len": len(body)})
            else:
                r["skill_id"] = sid
                r["desc_len"] = len(desc)
                r["body_len"] = len(body)
                ic = compute_ic(r)
                results.append({**r, **ic})
        else:
            results.append({"skill_id": sid, "error": "missing_in_response",
                             "desc_len": len(desc), "body_len": len(body)})
    return results

# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--platform", choices=["clawhub","skillssh"], required=True)
    ap.add_argument("--batch",   type=int, default=5)
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--n",       type=int, default=0)
    args = ap.parse_args()

    OUTDIR = Path(__file__).parent / f"results/cap_batch_{args.platform}"
    OUTDIR.mkdir(parents=True, exist_ok=True)
    OUTFILE = OUTDIR / "results.jsonl"
    REPORT  = OUTDIR / "summary.json"

    # Load data
    print(f"Loading {args.platform} dataset...")
    if args.platform == "clawhub":
        pool = load_clawhub(args.n)
    else:
        pool = load_skillssh(args.n)
    print(f"Total: {len(pool):,} skills | batch={args.batch} | workers={args.workers}")

    # Resume
    done = set()
    if OUTFILE.exists():
        for line in OUTFILE.read_text().strip().split('\n'):
            try:
                r = json.loads(line)
                done.add(r.get("skill_id") or r.get("slug",""))
            except: pass
    id_key = "skill_id" if args.platform == "clawhub" else "slug"

    todo = [(sid, d, b) for sid, d, b in pool
            if (sid not in done)]
    print(f"Already done: {len(done):,} | Remaining: {len(todo):,}")

    # Make batches
    batches = [todo[i:i+args.batch] for i in range(0, len(todo), args.batch)]
    print(f"Batches: {len(batches)} × {args.batch} skills\n")

    counts = Counter()
    t0 = time.time()
    n_done = 0

    with open(OUTFILE, "a", buffering=1) as fout:
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            futures = {ex.submit(process_batch, b): b for b in batches}
            for fi, fut in enumerate(as_completed(futures), 1):
                batch_results = fut.result()
                n_done += len(batch_results)

                with WRITE_LOCK:
                    for r in batch_results:
                        r_out = dict(r)
                        if "slug" not in r_out and "skill_id" in r_out:
                            r_out["slug"] = r_out["skill_id"]
                        fout.write(json.dumps(r_out, ensure_ascii=False) + "\n")

                for r in batch_results:
                    ic = r.get("ic_type", "ERR")
                    counts[ic] += 1

                elapsed = time.time() - t0
                rate_skill = n_done / elapsed * 60
                eta_h = (len(todo) - n_done) / max(rate_skill/60, 0.01) / 3600
                last_id = batch_results[-1].get("skill_id","?") if batch_results else "?"

                if fi % 5 == 0 or fi <= 3:
                    print(f"[batch {fi:4d}/{len(batches)}] {last_id:35s} "
                          f"| {rate_skill:.1f} skills/min  ETA={eta_h:.1f}h "
                          f"| IC-0={counts.get('IC-0',0)} "
                          f"IC-1={counts.get('IC-1',0)} "
                          f"IC-4={counts.get('IC-4',0)} "
                          f"IC-1+4={counts.get('IC-1+4',0)}")

    # Final summary
    all_r = [json.loads(l) for l in OUTFILE.read_text().strip().split('\n') if l]
    valid = [r for r in all_r if "error" not in r]
    print(f"\n{'='*65}\nFINAL ({args.platform}): {len(valid):,} valid / {len(all_r):,} total\n")
    ic_cnt = Counter(r["ic_type"] for r in valid)
    for t, n in sorted(ic_cnt.items()):
        pct = n/max(len(valid),1)*100
        print(f"  {t:8s}  {n:5d}  {pct:5.1f}%  {'█'*int(pct/2)}")
    avg = lambda k: sum(r.get(k,0) for r in valid)/max(len(valid),1)
    print(f"\n  IC-1 rate : {(ic_cnt.get('IC-1',0)+ic_cnt.get('IC-1+4',0))/max(len(valid),1)*100:.1f}%")
    print(f"  IC-4 rate : {(ic_cnt.get('IC-4',0)+ic_cnt.get('IC-1+4',0))/max(len(valid),1)*100:.1f}%")
    print(f"  mean CR   : {avg('CR'):.3f}   CP: {avg('CP'):.3f}   CDI: {avg('CDI'):.3f}")
    summary = {
        "platform": args.platform, "n_valid": len(valid), "n_total": len(all_r),
        "ic_distribution": dict(ic_cnt),
        "ic1_rate": (ic_cnt.get("IC-1",0)+ic_cnt.get("IC-1+4",0))/max(len(valid),1),
        "ic4_rate": (ic_cnt.get("IC-4",0)+ic_cnt.get("IC-1+4",0))/max(len(valid),1),
        "mean_CR": round(avg("CR"),3), "mean_CP": round(avg("CP"),3),
        "mean_CDI": round(avg("CDI"),3),
        "avg_n_hidden":   round(avg("n_hidden"),3),
        "avg_n_overclaim":round(avg("n_overclaim"),3),
    }
    REPORT.write_text(json.dumps(summary, indent=2, ensure_ascii=False))
    print(f"\n→ Results : {OUTFILE}\n→ Report  : {REPORT}")

if __name__ == "__main__":
    main()
