#!/usr/bin/env python3
"""Pick 20 ClawHub skills with opaque names but real descriptions + bodies."""
import sqlite3, json, re, sys

DB = "/Users/guangfu/codex/clawhubS/data/skills_sh/skills_sh.sqlite3"
OUT = "/Users/guangfu/codex/clawhubS/skill-X/experiments/desc_importance/clawhub_candidates.json"

db = sqlite3.connect(DB)
cur = db.cursor()

cur.execute("""
  SELECT skill_slug, name, description, length(description) as dl,
         length(skill_text) as bl, total_installs, skill_text
  FROM skills
  WHERE description IS NOT NULL
    AND length(description) BETWEEN 20 AND 200
    AND length(skill_text) > 1200
    AND total_installs >= 10
  ORDER BY total_installs DESC
  LIMIT 5000
""")
rows = cur.fetchall()
print(f"Initial pool: {len(rows)}")

TRANSPARENT = [
    "git", "weather", "notion", "tmux", "docker", "python",
    "react", "node", "aws", "web", "api", "search", "lint",
    "format", "deploy", "test", "debug", "code", "cursor",
    "claude", "ai", "llm", "gpt", "sql", "data", "bash",
    "shell", "file", "read", "write", "markdown", "css", "html",
    "typescript", "javascript", "rust", "java", "swift", "go-lang",
]

clean = []
seen_slugs = set()
for slug, name, desc, dl, bl, inst, body in rows:
    if slug in seen_slugs:
        continue
    # Skip transparent slugs
    if any(w in slug.lower() for w in TRANSPARENT):
        continue
    # Skip garbled descriptions
    if not desc:
        continue
    if desc.strip().startswith(("<", "{", "---", "//", "```")):
        continue
    if "ERROR" in desc or "`" in desc[:20]:
        continue
    # Must look like a real English sentence
    words = desc.split()
    if len(words) < 4:
        continue
    seen_slugs.add(slug)
    clean.append({
        "slug": slug,
        "name": name or slug,
        "description": desc.strip(),
        "desc_len": dl,
        "body_len": bl,
        "installs": inst,
        "body_preview": body[:300] if body else "",
    })

print(f"Clean candidates: {len(clean)}")
print()
for s in clean[:30]:
    print(f"  [{s['installs']:7d}] {s['slug']:40s} | {s['description'][:80]}")

with open(OUT, "w") as f:
    json.dump(clean[:50], f, indent=2, ensure_ascii=False)
print(f"\nSaved top-50 to {OUT}")
