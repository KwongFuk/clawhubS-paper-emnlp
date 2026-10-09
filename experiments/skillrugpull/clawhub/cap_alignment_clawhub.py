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
Cap Alignment — ClawHub Platform
=================================
Reads descriptions from local_shard0.sqlite3,
resolves SKILL.md body from collection_archive directories,
runs the same v3 high-level domain matrix analysis.

  python3 cap_alignment_clawhub.py [--workers 4] [--n 0]
  --n 0 = all 2,052 analyzable skills
"""

import json, os, re, sqlite3, subprocess, sys, time, threading
from pathlib import Path
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
import argparse

CLAWHUB_DB = "/Users/guangfu/codex/clawhubS/data/experiment1_split/local_shard0.sqlite3"
BASE       = "/Users/guangfu/codex/clawhubS/"
BODY_DIRS  = [
    "data/collection_archive/html_snapshots/api_files/",
    "data/collection_archive/runs/2026-03-06_public_archive_partial/html_snapshots/api_files/",
]
OUTDIR  = Path(__file__).parent / "results/cap_alignment_clawhub"
OUTFILE = OUTDIR / "results.jsonl"
REPORT  = OUTDIR / "summary.json"
OUTDIR.mkdir(parents=True, exist_ok=True)

WRITE_LOCK = threading.Lock()
LLM_LOCK   = threading.Semaphore(2)

# ── Body loader ────────────────────────────────────────────────────────────────

def resolve_body(text_path: str) -> str | None:
    fn = text_path.split('/')[-1]
    for d in BODY_DIRS:
        p = BASE + d + fn
        if os.path.exists(p):
            return open(p, encoding='utf-8', errors='ignore').read()
    return None

# ── Load dataset ───────────────────────────────────────────────────────────────

def load_clawhub(n: int = 0):
    db = sqlite3.connect(CLAWHUB_DB)
    rows = db.execute("""
        SELECT s.skill_id, s.description, sf.text_path
        FROM skills s
        JOIN skill_files sf ON s.skill_id = sf.skill_id
        WHERE sf.path = 'SKILL.md'
          AND sf.text_path IS NOT NULL
          AND s.description IS NOT NULL
          AND length(s.description) BETWEEN 30 AND 1500
        ORDER BY s.skill_id
    """).fetchall()
    db.close()

    pool = []
    seen = set()
    for skill_id, desc, text_path in rows:
        if skill_id in seen: continue
        body = resolve_body(text_path)
        if not body or len(body) < 300: continue
        if desc.strip() == body.strip()[:len(desc.strip())]: continue
        seen.add(skill_id)
        pool.append((skill_id, desc, body))
        if n > 0 and len(pool) >= n: break
    return pool

# ── Section parser (same as v3) ────────────────────────────────────────────────

def extract_sections(body: str, max_chars: int = 2000) -> str:
    if not body: return ""
    USE_WHEN = re.compile(
        r'(use when|trigger when|when (to use|you need)|user (asks?|says?))',
        re.IGNORECASE)
    lines = body.split('\n')
    sections, current_header, current_lines = [], None, []
    for line in lines:
        if re.match(r'^#{1,4}\s', line):
            if current_lines and current_header:
                sections.append((current_header, '\n'.join(current_lines)))
            current_header, current_lines = line.strip(), []
        else:
            current_lines.append(line)
    if current_lines and current_header:
        sections.append((current_header, '\n'.join(current_lines)))

    def score(header, content):
        h = header.lower()
        s = 0
        if any(k in h for k in ['when to use','trigger','features','capabilities']): s += 10
        if any(k in h for k in ['what','does','overview','functions','usage']): s += 5
        s += min(content.count('\n- ') + content.count('\n* '), 5)
        s += 3 if USE_WHEN.search(content) else 0
        return s

    scored = sorted(sections, key=lambda x: -score(x[0], x[1]))
    parts, total = [], 0
    for header, content in scored:
        chunk = f"{header}\n{content.strip()}\n"
        if total + len(chunk) <= max_chars:
            parts.append(chunk); total += len(chunk)
        elif total == 0:
            parts.append(chunk[:max_chars]); break
    return '\n'.join(parts) if parts else body[:max_chars]

# ── Prompt ─────────────────────────────────────────────────────────────────────

PROMPT = """\
You are auditing an AI skill for description-capability alignment.

DESCRIPTION (what the skill CLAIMS to do):
{desc}

SKILL BODY EXCERPT (what the skill ACTUALLY does):
{body_excerpt}

Task — build an alignment matrix:

Step 1. Extract 3-6 HIGH-LEVEL capability domains from the BODY.
        Use broad domain names (e.g. "task execution", "API integration").
Step 2. Extract 2-5 HIGH-LEVEL capability claims from the DESCRIPTION.
        Only include things explicitly stated (ignore vague marketing language).
Step 3. Classify into exactly one cell:
  - MATCH        : capability is in BOTH body and description
  - IC1_HIDDEN   : capability is in body but NOT mentioned in description
  - IC4_OVERCLAIM: claim is in description but NOT supported by body

Return ONLY valid JSON:
{{
  "C_body": ["domain1", ...],
  "C_desc": ["claim1", ...],
  "matrix": {{
    "match":        ["..."],
    "IC1_hidden":   ["..."],
    "IC4_overclaim":["..."]
  }},
  "notes": "1-sentence summary"
}}"""

# ── LLM ───────────────────────────────────────────────────────────────────────

def llm(prompt: str, retries: int = 3) -> str | None:
    for attempt in range(retries):
        with LLM_LOCK:
            try:
                r = subprocess.run(
                    ["openclaw", "infer", "model", "run", "--prompt", prompt, "--json"],
                    capture_output=True, text=True, timeout=150)
                txt = json.loads(r.stdout).get("outputs", [{}])[0].get("text", "").strip()
                if txt: return txt
            except: pass
        time.sleep(2 + attempt * 3)
    return None

def parse_json(text: str) -> dict | None:
    if not text: return None
    for part in text.split("```"):
        part = part.strip().lstrip("json").strip()
        try: return json.loads(part)
        except: pass
    try: return json.loads(text)
    except: return None

# ── IC metrics ─────────────────────────────────────────────────────────────────

def compute_ic(matrix, c_body, c_desc):
    n_match     = len(matrix.get("match", []))
    n_hidden    = len(matrix.get("IC1_hidden", []))
    n_overclaim = len(matrix.get("IC4_overclaim", []))
    n_body      = max(len(c_body), n_match + n_hidden, 1)
    n_desc      = max(len(c_desc), n_match + n_overclaim, 1)
    cr  = n_match / n_body
    cp  = n_match / n_desc
    cdi = 1.0 - (cr + cp) / 2.0
    has_ic1 = n_hidden > 0
    has_ic4 = n_overclaim > 0
    if not has_ic1 and not has_ic4:  ic = "IC-0"
    elif has_ic1 and not has_ic4:    ic = "IC-1"
    elif not has_ic1 and has_ic4:    ic = "IC-4"
    else:                             ic = "IC-1+4"
    return {"n_body": n_body, "n_desc": n_desc, "n_match": n_match,
            "n_hidden": n_hidden, "n_overclaim": n_overclaim,
            "CR": round(cr,4), "CP": round(cp,4), "CDI": round(cdi,4), "ic_type": ic}

# ── Process one skill ──────────────────────────────────────────────────────────

def process(skill_id: str, desc: str, body: str) -> dict:
    base = {"skill_id": skill_id, "desc_len": len(desc), "body_len": len(body)}
    excerpt = extract_sections(body, 2000)
    prompt = PROMPT.replace("{desc}", desc[:800]).replace("{body_excerpt}", excerpt)
    raw = llm(prompt)
    r = parse_json(raw)
    if not r or "matrix" not in r:
        return {**base, "error": "parse_failed", "raw": (raw or "")[:200]}
    c_body = r.get("C_body", [])
    c_desc = r.get("C_desc", [])
    matrix = r.get("matrix", {})
    ic = compute_ic(matrix, c_body, c_desc)
    return {**base, "C_body": c_body, "C_desc": c_desc,
            "matrix": matrix, "notes": r.get("notes",""),
            "body_excerpt_used": len(excerpt), **ic}

# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--n",       type=int, default=0)
    args = ap.parse_args()

    print("Loading ClawHub dataset...")
    pool = load_clawhub(args.n)
    print(f"Total analyzable: {len(pool):,} skills | workers={args.workers}")

    done = set()
    if OUTFILE.exists():
        for line in OUTFILE.read_text().strip().split('\n'):
            try: done.add(json.loads(line)["skill_id"])
            except: pass
    todo = [(sid, d, b) for sid, d, b in pool if sid not in done]
    print(f"Already done: {len(done):,} | Remaining: {len(todo):,}\n")

    counts = Counter()
    t0 = time.time()

    with open(OUTFILE, "a", buffering=1) as fout:
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            futures = {ex.submit(process, sid, desc, body): sid
                       for sid, desc, body in todo}
            for i, fut in enumerate(as_completed(futures), 1):
                sid = futures[fut]
                try:    result = fut.result()
                except Exception as e: result = {"skill_id": sid, "error": str(e)}

                with WRITE_LOCK:
                    fout.write(json.dumps(result, ensure_ascii=False) + "\n")

                ic = result.get("ic_type","ERR")
                counts[ic] += 1
                elapsed = time.time() - t0
                rate = i / elapsed * 60
                eta_h = (len(todo) - i) / max(rate/60, 0.01) / 3600

                if i % 10 == 0 or i <= 5:
                    print(f"[{i:4d}/{len(todo)}] {sid:40s} {ic:8s} "
                          f"| {rate:.1f}/min ETA={eta_h:.1f}h "
                          f"| IC-0={counts.get('IC-0',0)} "
                          f"IC-1={counts.get('IC-1',0)} "
                          f"IC-4={counts.get('IC-4',0)} "
                          f"IC-1+4={counts.get('IC-1+4',0)}")

    # Final summary
    all_r  = [json.loads(l) for l in OUTFILE.read_text().strip().split('\n') if l]
    valid  = [r for r in all_r if "error" not in r]
    print(f"\n{'='*65}\nFINAL: {len(valid):,} valid / {len(all_r):,} total\n")
    ic_cnt = Counter(r["ic_type"] for r in valid)
    for t, n in sorted(ic_cnt.items()):
        pct = n/max(len(valid),1)*100
        print(f"  {t:8s}  {n:5d}  {pct:5.1f}%  {'█'*int(pct/2)}")
    avg = lambda k: sum(r.get(k,0) for r in valid)/max(len(valid),1)
    print(f"\n  IC-1 rate: {(ic_cnt.get('IC-1',0)+ic_cnt.get('IC-1+4',0))/max(len(valid),1)*100:.1f}%")
    print(f"  IC-4 rate: {(ic_cnt.get('IC-4',0)+ic_cnt.get('IC-1+4',0))/max(len(valid),1)*100:.1f}%")
    print(f"  mean CR: {avg('CR'):.3f}  CP: {avg('CP'):.3f}  CDI: {avg('CDI'):.3f}")
    summary = {
        "platform": "clawhub", "n_valid": len(valid), "n_total": len(all_r),
        "ic_distribution": dict(ic_cnt),
        "ic1_rate": (ic_cnt.get("IC-1",0)+ic_cnt.get("IC-1+4",0))/max(len(valid),1),
        "ic4_rate": (ic_cnt.get("IC-4",0)+ic_cnt.get("IC-1+4",0))/max(len(valid),1),
        "mean_CR":  round(avg("CR"),3), "mean_CP": round(avg("CP"),3),
        "mean_CDI": round(avg("CDI"),3),
        "avg_n_hidden":   round(avg("n_hidden"),3),
        "avg_n_overclaim":round(avg("n_overclaim"),3),
    }
    REPORT.write_text(json.dumps(summary, indent=2, ensure_ascii=False))
    print(f"\n→ Results: {OUTFILE}\n→ Report:  {REPORT}")

if __name__ == "__main__":
    main()
