#!/usr/bin/env python3
"""
Full-Scale Implementation-Grounded Audit
=========================================
Compares each skill's listing (name + description) against its actual
SKILL.md implementation to identify concrete disclosure gaps.

For every skill in merged.sqlite3 that has a readable SKILL.md body,
this script sends BOTH the listing and the implementation to an LLM
and produces an implementation-grounded IC classification.

Usage:
    python3 run_full_audit.py --model gemini-2.5-flash --workers 8
    python3 run_full_audit.py --model gpt-5.4-mini --workers 4
"""

import sqlite3
import json
import os
import sys
import re
import subprocess
import argparse
import hashlib
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

# ── Paths ──────────────────────────────────────────────────────────────────────

DB_PATH = "/Users/guangfu/codex/clawhubS/data/experiment1_split/merged.sqlite3"
SKILLMD_DIRS = [
    "/Users/guangfu/codex/clawhubS/data/collection_archive/html_snapshots/api_files",
    "/Users/guangfu/codex/clawhubS/data/collection_archive/remote_scratch_html_snapshots/api_files",
    "/Users/guangfu/codex/clawhubS/data/collection_archive/remote_scratch_html_snapshots_shard1/api_files",
]
OUTPUT_DIR = Path("/Users/guangfu/codex/clawhubS/skill-X/experiments/full_audit")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
CACHE_DIR = OUTPUT_DIR / "cache"
CACHE_DIR.mkdir(exist_ok=True)
RESULTS_FILE = OUTPUT_DIR / "full_audit_results.jsonl"
PROGRESS_FILE = OUTPUT_DIR / "progress.json"

OPENCLAW_BIN = "/opt/homebrew/bin/openclaw"

# ── Prompt ─────────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """\
You are auditing an AI agent skill marketplace. For each skill, you receive:
1. The LISTING — what users see before installation (name + description)
2. The IMPLEMENTATION — the actual SKILL.md file content

Your task: compare the listing against the implementation and classify whether
the listing adequately represents what the skill actually does.

## Classification

### IC Class (Implementation-Grounded)
- IC-0: Listing adequately represents the implementation's actual capabilities
- IC-1s: Implementation contains security-relevant operations NOT disclosed
  in the listing (e.g., shell execution, credential access, network calls,
  file system writes, database modification)
- IC-1q: Implementation has functional capabilities NOT mentioned in listing
  (quality gap, not security-relevant)
- IC-2: Listing claims capabilities the implementation does NOT actually have

### Security-relevant operations (for IC-1s)
SHELL_EXEC, CREDENTIAL_ACCESS, NETWORK_EXTERNAL, FILE_WRITE, FILE_DELETE,
DB_WRITE, SYSTEM_MODIFY, PRIVILEGE_ESCALATION, DATA_EXFILTRATION

## Response Format
Respond with ONLY valid JSON:
{
  "ic_class": "IC-0|IC-1s|IC-1q|IC-2",
  "undisclosed_capabilities": ["cap1", "cap2"],
  "overclaimed_capabilities": ["cap1"],
  "security_operations": ["SHELL_EXEC", ...],
  "confidence": 0.0-1.0,
  "reason": "one-line explanation of the classification"
}"""

USER_TEMPLATE = """\
LISTING (what users see before installation):
  Name: {name}
  Description: {description}

IMPLEMENTATION (SKILL.md content, may include frontmatter + instructions):
{skillmd_body}"""

# ── SKILL.md file resolution ──────────────────────────────────────────────────

def build_skillmd_index():
    """Build a mapping from skill_name -> list of SKILL.md file paths."""
    index = {}
    pattern = re.compile(
        r"api__v1__skills__(.+?)__file__path_3DSKILL\.md"
        r"_26version_3D(.+?)__([a-f0-9]+)\.md$"
    )
    
    for dirpath in SKILLMD_DIRS:
        if not os.path.exists(dirpath):
            continue
        for fname in os.listdir(dirpath):
            if "SKILL.md" not in fname:
                continue
            m = pattern.match(fname)
            if m:
                skill_name = m.group(1)
                version = m.group(2)
                full_path = os.path.join(dirpath, fname)
                if skill_name not in index:
                    index[skill_name] = []
                index[skill_name].append({
                    "path": full_path,
                    "version": version,
                })
    
    # For each skill, keep only the latest version
    resolved = {}
    for skill_name, entries in index.items():
        # Sort by version string (simple lexicographic, works for semver)
        entries.sort(key=lambda e: e["version"], reverse=True)
        resolved[skill_name] = entries[0]["path"]
    
    return resolved


def read_skillmd(path, max_chars=4000):
    """Read SKILL.md content, truncating to max_chars."""
    try:
        with open(path, "r", errors="replace") as f:
            content = f.read()
        # Strip YAML frontmatter for the body
        if content.startswith("---"):
            end = content.find("---", 3)
            if end > 0:
                # Keep frontmatter for context, but prioritize body
                frontmatter = content[3:end].strip()
                body = content[end+3:].strip()
                # If body is very long, truncate but keep frontmatter
                if len(body) > max_chars - 500:
                    body = body[:max_chars - 500] + "\n[... truncated ...]"
                return f"--- FRONTMATTER ---\n{frontmatter}\n--- BODY ---\n{body}"
        return content[:max_chars]
    except Exception as e:
        return None


# ── LLM call via openclaw ─────────────────────────────────────────────────────

def llm_call(prompt, model, cache_key=""):
    """Call LLM via openclaw infer."""
    if cache_key:
        cf = CACHE_DIR / f"{cache_key}.json"
        if cf.exists():
            try:
                return json.loads(cf.read_text())
            except:
                pass
    
    full_prompt = f"{SYSTEM_PROMPT}\n\n{prompt}"
    cmd = [OPENCLAW_BIN, "infer", "model", "run",
           "--prompt", full_prompt, "--json"]
    if model:
        cmd.extend(["--model", model])
    
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=60
        )
        text = result.stdout.strip()
        if not text:
            return None
        
        # Parse JSON from response
        # Sometimes the LLM wraps in ```json ... ```
        text = re.sub(r"^```json\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
        
        parsed = json.loads(text)
        
        if cache_key:
            (CACHE_DIR / f"{cache_key}.json").write_text(json.dumps(parsed))
        
        return parsed
    except (subprocess.TimeoutExpired, json.JSONDecodeError, Exception) as e:
        return {"error": str(e)}


# ── Main ───────────────────────────────────────────────────────────────────────

def load_progress():
    """Load set of already-processed skill_ids."""
    if PROGRESS_FILE.exists():
        return set(json.loads(PROGRESS_FILE.read_text()))
    return set()

def save_progress(done_ids):
    PROGRESS_FILE.write_text(json.dumps(list(done_ids)))

def process_skill(skill, skillmd_path, model):
    """Process a single skill."""
    body = read_skillmd(skillmd_path)
    if not body or len(body) < 50:
        return None
    
    prompt = USER_TEMPLATE.format(
        name=skill["name"],
        description=skill["description"] or "(no description)",
        skillmd_body=body,
    )
    
    cache_key = hashlib.md5(
        f"{skill['skill_id']}:{model}".encode()
    ).hexdigest()
    
    result = llm_call(prompt, model, cache_key)
    if result is None:
        return None
    
    return {
        "skill_id": skill["skill_id"],
        "name": skill["name"],
        "description_len": len(skill["description"] or ""),
        "skillmd_len": len(body),
        **result,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Full-Scale Implementation-Grounded Audit"
    )
    parser.add_argument("--model", default="gemini-2.5-flash",
                        help="LLM model to use")
    parser.add_argument("--workers", type=int, default=6,
                        help="Parallel workers")
    parser.add_argument("--limit", type=int, default=0,
                        help="Max skills to process (0=all)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Just count matches, don't call LLM")
    args = parser.parse_args()
    
    print("=" * 60)
    print("Full-Scale Implementation-Grounded Audit")
    print("=" * 60)
    
    # Step 1: Build SKILL.md index
    print("\n[1/4] Building SKILL.md file index...")
    skillmd_index = build_skillmd_index()
    print(f"  Found {len(skillmd_index)} unique skill names with SKILL.md files")
    
    # Step 2: Load skills from DB
    print("\n[2/4] Loading skills from database...")
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    rows = db.execute("""
        SELECT skill_id, name, description, category
        FROM skills
        WHERE description IS NOT NULL AND length(description) > 10
    """).fetchall()
    skills = [dict(r) for r in rows]
    db.close()
    print(f"  Loaded {len(skills)} skills with descriptions")
    
    # Step 3: Match skills to SKILL.md files
    # skill_id format: "author/skill-name", SKILL.md index key is just "skill-name"
    matched = []
    for skill in skills:
        sid = skill["skill_id"]
        # Try exact match on the part after /
        skill_name = sid.split("/")[-1] if "/" in sid else sid
        if skill_name in skillmd_index:
            matched.append((skill, skillmd_index[skill_name]))
    
    print(f"  Matched {len(matched)} skills to SKILL.md files")
    
    if args.dry_run:
        print("\n[DRY RUN] Would process these skills. Exiting.")
        return
    
    # Step 4: Process
    done_ids = load_progress()
    todo = [(s, p) for s, p in matched if s["skill_id"] not in done_ids]
    if args.limit > 0:
        todo = todo[:args.limit]
    
    print(f"\n[3/4] Processing {len(todo)} skills (model={args.model}, "
          f"workers={args.workers})")
    print(f"  Already done: {len(done_ids)}")
    print(f"  Results file: {RESULTS_FILE}")
    
    start = time.time()
    completed = 0
    errors = 0
    
    with open(RESULTS_FILE, "a") as fout:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = {
                pool.submit(process_skill, skill, path, args.model): skill
                for skill, path in todo
            }
            
            for future in as_completed(futures):
                skill = futures[future]
                try:
                    result = future.result()
                    if result:
                        fout.write(json.dumps(result, ensure_ascii=False) + "\n")
                        fout.flush()
                        done_ids.add(skill["skill_id"])
                        completed += 1
                    else:
                        errors += 1
                except Exception as e:
                    errors += 1
                    print(f"  ERROR {skill['skill_id']}: {e}")
                
                total = completed + errors
                if total % 50 == 0:
                    elapsed = time.time() - start
                    rate = total / elapsed if elapsed > 0 else 0
                    eta = (len(todo) - total) / rate / 60 if rate > 0 else 0
                    print(f"  [{total}/{len(todo)}] "
                          f"{completed} ok, {errors} err, "
                          f"{rate:.1f}/s, ETA {eta:.0f}min")
                    save_progress(done_ids)
    
    save_progress(done_ids)
    elapsed = time.time() - start
    
    print(f"\n[4/4] Done!")
    print(f"  Completed: {completed}")
    print(f"  Errors: {errors}")
    print(f"  Time: {elapsed/60:.1f} min")
    
    # Quick stats
    print("\n--- Quick IC Distribution ---")
    ic_counts = {}
    with open(RESULTS_FILE) as f:
        for line in f:
            try:
                r = json.loads(line)
                ic = r.get("ic_class", "unknown")
                ic_counts[ic] = ic_counts.get(ic, 0) + 1
            except:
                pass
    for ic in sorted(ic_counts):
        print(f"  {ic}: {ic_counts[ic]}")


if __name__ == "__main__":
    main()
