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

"""Extended cap scan: 150 new skills appended to pilot_150.jsonl"""
import json, re, sqlite3, subprocess, time
from pathlib import Path

DB     = "/Users/guangfu/codex/clawhubS/data/skills_sh/skills_sh.sqlite3"
OUT    = Path("results/cap_alignment_v2/pilot_150.jsonl")
OUT.parent.mkdir(parents=True, exist_ok=True)

def smart_excerpt(body, target=3000):
    body = re.sub(r'^---[\s\S]*?---\n', '', body, flags=re.MULTILINE).strip()
    body = re.sub(r'```[\s\S]*?```', '', body, flags=re.MULTILINE)
    body = re.sub(r'`[^`]+`', '', body)
    body = re.sub(r'\n{3,}', '\n\n', body)
    lines = body.split('\n')
    SKIP = re.compile(r'(status\s*:|last updated|latest version|prerequisites|^installation$|npm install|pip install|brew install|version\s*\d|compatibility|getting started|requirements\s*$|node\.js|python 3|dotnet tool)', re.IGNORECASE)
    FUNC = re.compile(r'^#+\s+(command|feature|capabilit|usage|operation|overview|what|how|support|core|main|function|key|guide|reference|tool)', re.IGNORECASE)
    start = 0
    for i, line in enumerate(lines[:150]):
        if i > 12 and FUNC.match(line.strip()) and not SKIP.search(line):
            start = i; break
    if start == 0: start = max(0, len(lines) // 7)
    prose = '\n'.join(lines[start:])
    return re.sub(r'\n{3,}', '\n\n', prose)[:target]

JOINT = """\
Extract capability lists from two sources of the same AI skill.
Use UNIFIED vocabulary — use the SAME phrase for the same concept in both lists.

SOURCE A — marketplace description:
{description}

SOURCE B — skill documentation (code stripped):
{excerpt}

Return short noun phrases (2-6 words) for each distinct functional capability.
Return ONLY valid JSON: {{"c_desc": [...], "c_body": [...]}}
SAME phrase for the same real-world function in both lists."""

def llm(prompt):
    for _ in range(3):
        try:
            r = subprocess.run(["openclaw","infer","model","run","--prompt",prompt,"--json"],
                capture_output=True, text=True, timeout=120)
            text = json.loads(r.stdout).get("outputs",[{}])[0].get("text","").strip()
            for part in text.split("```"):
                part = part.strip().lstrip("json").strip()
                try: return json.loads(part)
                except: pass
            return json.loads(text)
        except subprocess.TimeoutExpired: time.sleep(5)
        except: pass
    return None

def safe_p(t, **kw):
    for k,v in kw.items(): t = t.replace('{'+k+'}', str(v))
    return t

def compute_ic(c_desc, c_body):
    shared = set(c_desc)&set(c_body)
    only_desc = set(c_desc)-set(c_body)
    only_body = set(c_body)-set(c_desc)
    cr = len(shared)/max(len(c_body),1)
    cp = 1-len(only_desc)/max(len(c_desc),1)
    cdi = 1-(cr+cp)/2
    ic = ("IC-0" if cr>=1 and cp>=1 else "IC-1+4" if cr<1 and cp<1 else "IC-1" if cr<1 else "IC-4")
    return dict(shared=sorted(shared), only_desc=sorted(only_desc), only_body=sorted(only_body),
                CR=round(cr,3), CP=round(cp,3), CDI=round(cdi,3), ic_type=ic)

# Load done slugs
done = set()
if OUT.exists():
    for l in OUT.read_text().strip().split('\n'):
        try: done.add(json.loads(l)['slug'])
        except: pass
print(f"Already done: {len(done)}")

db = sqlite3.connect(DB)
cur = db.cursor()
cur.execute("""
    SELECT skill_slug, description, skill_text FROM skills
    WHERE description IS NOT NULL AND skill_text IS NOT NULL
      AND description != skill_text
      AND length(description) BETWEEN 80 AND 1200
      AND length(skill_text) BETWEEN 500 AND 30000
      AND description NOT LIKE '%ERROR%'
    ORDER BY RANDOM() LIMIT 600
""")
rows = cur.fetchall(); db.close()

seen, pool = set(done), []
for slug, d, b in rows:
    if slug not in seen: seen.add(slug); pool.append((slug,d,b))
    if len(pool)==150: break

print(f"New skills to scan: {len(pool)}\n")

with open(OUT, 'a') as f:
    for i, (slug, desc, body) in enumerate(pool):
        excerpt = smart_excerpt(body)
        print(f"[{i+1}/{len(pool)}] {slug}  body={len(body)} excerpt={len(excerpt)}")
        result = llm(safe_p(JOINT, description=desc, excerpt=excerpt))
        if not result:
            rec = {"slug":slug,"error":"llm_failed","desc_len":len(desc),"body_len":len(body)}
        else:
            c_desc = result.get("c_desc",[])
            c_body = result.get("c_body",[])
            ic = compute_ic(c_desc, c_body)
            rec = {"slug":slug,"desc_len":len(desc),"body_len":len(body),"excerpt_len":len(excerpt),
                   "n_desc":len(c_desc),"n_body":len(c_body),"c_desc":c_desc,"c_body":c_body,**ic}
            print(f"  IC={ic['ic_type']:8s} CR={ic['CR']:.2f} undisclosed={len(ic['only_body'])} {ic['only_body'][:2]}")
        f.write(json.dumps(rec, ensure_ascii=False)+'\n'); f.flush()
        time.sleep(0.3)

# Summary
valid = []
for l in OUT.read_text().strip().split('\n'):
    try:
        r = json.loads(l)
        if 'error' not in r: valid.append(r)
    except: pass
from collections import Counter
ic_dist = Counter(r['ic_type'] for r in valid)
ic1 = [r for r in valid if r.get('only_body') and len(r['only_body'])>=2]
print(f"\n=== Total valid: {len(valid)} ===")
for ic,n in sorted(ic_dist.items()): print(f"  {ic:8s} {n}")
print(f"\nIC-1 skills (undisclosed>=2): {len(ic1)}")
