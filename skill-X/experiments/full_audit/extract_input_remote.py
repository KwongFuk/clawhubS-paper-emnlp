#!/usr/bin/env python3
"""Extract ALL skill files — Palmetto, with full path resolution."""
import sqlite3, json, os
from collections import defaultdict

DB_PATH = "/home/gguo/code/github/clawhubS/data/experiment1_split/merged.sqlite3"
OUTPUT = "/scratch/gguo/clawhubS/skill-X/experiments/full_audit/audit_input_full.jsonl"

MAX_FILE_CHARS = 3000
MAX_TOTAL_CHARS = 6000

PRIORITY = {
    'SKILL.md': 0, 'skill.md': 0, 'README.md': 1,
    'index.js': 2, 'index.ts': 2, 'main.py': 2, 'main.js': 2,
    'setup.sh': 3, 'install.sh': 3,
    'skill.yaml': 4, 'skill.json': 4,
    'package.json': 5, 'requirements.txt': 5,
}

# Resolve text_path to disk: try multiple strategies
RELATIVE_BASES = [
    "/home/gguo/code/github/clawhubS",
    "/scratch/gguo/clawhubS",
]
# Prefix remaps for absolute paths stored in DB
ABSOLUTE_REMAPS = [
    ("/home/gguo/code/clawhubS/", "/home/gguo/code/github/clawhubS/"),
    ("/scratch/gguo/clawhub_archive/html_snapshots_shard1/",
     "/scratch/gguo/clawhubS/data/collection_archive/remote_scratch_html_snapshots_shard1/"),
    ("/scratch/gguo/clawhub_archive/html_snapshots/",
     "/scratch/gguo/clawhubS/data/collection_archive/html_snapshots/"),
]

_cache = {}
def resolve(tp):
    if tp in _cache:
        return _cache[tp]
    result = None
    # 1. If absolute path, try direct + remaps
    if tp.startswith("/"):
        if os.path.exists(tp):
            result = tp
        else:
            for old, new in ABSOLUTE_REMAPS:
                if tp.startswith(old):
                    remapped = tp.replace(old, new, 1)
                    if os.path.exists(remapped):
                        result = remapped
                        break
    else:
        # 2. Relative path — try each base
        for base in RELATIVE_BASES:
            full = os.path.join(base, tp)
            if os.path.exists(full):
                result = full
                break
    _cache[tp] = result
    return result

# ── Load ──────────────────────────────────────────────────────────────────────
print(f"[1] Loading from {DB_PATH}...")
db = sqlite3.connect(DB_PATH)
skills = {}
for r in db.execute("SELECT skill_id,name,description,category FROM skills WHERE description IS NOT NULL AND length(description)>10"):
    skills[r[0]] = {"name": r[1], "description": r[2], "category": r[3]}
print(f"  {len(skills)} skills")

print("[2] Loading file records...")
rows = db.execute("""
    SELECT skill_id, path, text_path, version, size
    FROM skill_files WHERE text_path IS NOT NULL AND size>10 AND size<100000
    ORDER BY skill_id, path, version DESC
""").fetchall()
sf = defaultdict(dict)
for sid,path,tp,ver,sz in rows:
    if sid in skills and path not in sf[sid]:
        sf[sid][path] = tp
print(f"  {len(sf)} skills have files")
db.close()

# ── Write ─────────────────────────────────────────────────────────────────────
print("[3] Resolving and writing...")
written = no_files = 0
with open(OUTPUT,"w") as fout:
    for i,sid in enumerate(sorted(sf.keys())):
        fpaths = sf[sid]
        skill = skills[sid]
        resolved = {}
        for fp,tp in fpaths.items():
            d = resolve(tp)
            if d: resolved[fp] = d
        if not resolved:
            no_files += 1; continue
        sp = sorted(resolved.keys(), key=lambda p: PRIORITY.get(p.split("/")[-1],10))
        parts=[]; total=0; fi=[]
        for fp in sp:
            if total>=MAX_TOTAL_CHARS: break
            try:
                with open(resolved[fp],"r",errors="replace") as f: c=f.read()
                if len(c)>MAX_FILE_CHARS: c=c[:MAX_FILE_CHARS]+"\n[...truncated...]"
                rem=MAX_TOTAL_CHARS-total
                if len(c)>rem: c=c[:rem]+"\n[...truncated...]"
                parts.append(f"=== FILE: {fp} ===\n{c}"); total+=len(c); fi.append(fp)
            except: continue
        if not parts:
            no_files+=1; continue
        fout.write(json.dumps({"skill_id":sid,"name":skill["name"],"description":skill["description"],
            "category":skill["category"],"files_included":fi,"n_files_total":len(fpaths),
            "n_files_resolved":len(resolved),"implementation":"\n\n".join(parts)},ensure_ascii=False)+"\n")
        written+=1
        if (i+1)%3000==0: print(f"  [{i+1}] {written} written")

mb=os.path.getsize(OUTPUT)/1024/1024
print(f"\n=== Results ===")
print(f"  Skills: {len(skills)} | Resolved: {written} | Missing: {no_files}")
print(f"  Coverage: {100*written/len(skills):.1f}% | Size: {mb:.1f} MB")
