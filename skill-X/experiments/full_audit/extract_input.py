#!/usr/bin/env python3
"""
Extract ALL skill files (SKILL.md + code + scripts + configs) per skill.
Produces audit_input_full.jsonl where each entry has:
- listing: name + description (high-level, user-facing)
- implementation_files: dict of {path: content} for ALL readable files

This is the input for the full implementation-grounded audit.
"""
import sqlite3, json, os, re
from collections import defaultdict

DB_PATH = "/Users/guangfu/codex/clawhubS/data/experiment1_split/merged.sqlite3"
BASE = "/Users/guangfu/codex/clawhubS"
OUTPUT = "/Users/guangfu/codex/clawhubS/skill-X/experiments/full_audit/audit_input_full.jsonl"

ARCHIVE_DIRS = [
    "data/collection_archive/html_snapshots/api_files",
    "data/collection_archive/remote_scratch_html_snapshots/api_files",
    "data/collection_archive/remote_scratch_html_snapshots_shard1/api_files",
]

# Max chars per file, and max total chars per skill
MAX_FILE_CHARS = 3000
MAX_TOTAL_CHARS = 6000  # Keep total prompt manageable for 7B model

# ── Step 1: Build filename → disk path index ──────────────────────────────────
print("[1] Building filename index from disk...")
fname_to_path = {}
for d in ARCHIVE_DIRS:
    full_d = os.path.join(BASE, d)
    if not os.path.isdir(full_d):
        continue
    for f in os.listdir(full_d):
        fname_to_path[f] = os.path.join(full_d, f)
print(f"  Indexed {len(fname_to_path)} files on disk")

# ── Step 2: Load skills ───────────────────────────────────────────────────────
print("[2] Loading skills from DB...")
db = sqlite3.connect(DB_PATH)

skills = {}
for row in db.execute("""
    SELECT skill_id, name, description, category
    FROM skills WHERE description IS NOT NULL AND length(description) > 10
"""):
    skills[row[0]] = {"name": row[1], "description": row[2], "category": row[3]}
print(f"  {len(skills)} skills with descriptions")

# ── Step 3: Get ALL files per skill, resolve to disk ──────────────────────────
print("[3] Resolving skill files...")

# Priority order for files (most important first)
PRIORITY = {
    'SKILL.md': 0,
    'skill.md': 0,
    'README.md': 1,
    'index.js': 2, 'index.ts': 2, 'main.py': 2, 'main.js': 2,
    'setup.sh': 3, 'install.sh': 3,
    'skill.yaml': 4, 'skill.json': 4,
    'package.json': 5, 'requirements.txt': 5,
}

# Group files by skill_id, dedupe by (skill_id, path), keep latest version
rows = db.execute("""
    SELECT skill_id, path, text_path, version, size, content_type
    FROM skill_files
    WHERE text_path IS NOT NULL AND size > 10 AND size < 100000
    ORDER BY skill_id, path, version DESC
""").fetchall()

skill_files = defaultdict(dict)  # skill_id -> {path: text_path}
for sid, path, tp, ver, size, ctype in rows:
    if sid not in skills:
        continue
    if path not in skill_files[sid]:  # First = latest version
        skill_files[sid][path] = tp

print(f"  {len(skill_files)} skills have file records")
db.close()

# ── Step 4: Read files and build JSONL ────────────────────────────────────────
print("[4] Reading files and writing JSONL...")
written = 0
no_files_on_disk = 0

with open(OUTPUT, "w") as fout:
    for sid in sorted(skill_files.keys()):
        file_paths = skill_files[sid]
        skill = skills[sid]
        
        # Resolve and read files, sorted by priority
        resolved_files = {}
        for fpath, tp in file_paths.items():
            fname = tp.split("/")[-1]
            disk_path = fname_to_path.get(fname)
            if disk_path and os.path.exists(disk_path):
                resolved_files[fpath] = disk_path
        
        if not resolved_files:
            no_files_on_disk += 1
            continue
        
        # Sort by priority, read files up to budget
        sorted_paths = sorted(
            resolved_files.keys(),
            key=lambda p: PRIORITY.get(p.split("/")[-1], 10)
        )
        
        impl_text_parts = []
        total_chars = 0
        files_included = []
        
        for fpath in sorted_paths:
            if total_chars >= MAX_TOTAL_CHARS:
                break
            try:
                with open(resolved_files[fpath], "r", errors="replace") as f:
                    content = f.read()
                # Truncate individual file
                if len(content) > MAX_FILE_CHARS:
                    content = content[:MAX_FILE_CHARS] + "\n[... truncated ...]"
                
                remaining = MAX_TOTAL_CHARS - total_chars
                if len(content) > remaining:
                    content = content[:remaining] + "\n[... truncated ...]"
                
                impl_text_parts.append(f"=== FILE: {fpath} ===\n{content}")
                total_chars += len(content)
                files_included.append(fpath)
            except:
                continue
        
        if not impl_text_parts:
            no_files_on_disk += 1
            continue
        
        implementation_text = "\n\n".join(impl_text_parts)
        
        entry = {
            "skill_id": sid,
            "name": skill["name"],
            "description": skill["description"],
            "category": skill["category"],
            "files_included": files_included,
            "n_files_total": len(file_paths),
            "n_files_resolved": len(resolved_files),
            "implementation": implementation_text,
        }
        fout.write(json.dumps(entry, ensure_ascii=False) + "\n")
        written += 1

size_mb = os.path.getsize(OUTPUT) / 1024 / 1024
print(f"\n=== Results ===")
print(f"  Total skills:           {len(skills)}")
print(f"  With files on disk:     {written}")
print(f"  No files on disk:       {no_files_on_disk}")
print(f"  Coverage:               {written}/{len(skills)} = {100*written/len(skills):.1f}%")
print(f"  Output size:            {size_mb:.1f} MB")
