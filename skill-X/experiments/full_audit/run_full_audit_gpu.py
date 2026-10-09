#!/usr/bin/env python3
"""
Full-Scale Implementation-Grounded Audit (Palmetto GPU Version)
================================================================
Compares each skill's listing (name + description) against its actual
SKILL.md implementation to identify concrete disclosure gaps.

Runs on Palmetto HPC using vLLM-served Qwen2.5-7B-Instruct.

Usage:
    # Step 1: Start vLLM server (in separate terminal)
    python3 -m vllm.entrypoints.openai.api_server \
        --model Qwen/Qwen2.5-7B-Instruct \
        --port 8000 --max-model-len 4096

    # Step 2: Run audit
    python3 run_full_audit_gpu.py --workers 16
"""

import sqlite3
import json
import os
import sys
import re
import time
import hashlib
import argparse
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

# ── Paths ──────────────────────────────────────────────────────────────────────

DB_PATH = os.environ.get(
    "AUDIT_DB",
    "/Users/guangfu/codex/clawhubS/data/experiment1_split/merged.sqlite3"
)
SKILLMD_DIRS = [
    os.path.join(os.path.dirname(DB_PATH), "..", "collection_archive/html_snapshots/api_files"),
    os.path.join(os.path.dirname(DB_PATH), "..", "collection_archive/remote_scratch_html_snapshots/api_files"),
    os.path.join(os.path.dirname(DB_PATH), "..", "collection_archive/remote_scratch_html_snapshots_shard1/api_files"),
]
# Resolve relative paths
SKILLMD_DIRS = [os.path.realpath(d) for d in SKILLMD_DIRS]

OUTPUT_DIR = Path(os.environ.get("AUDIT_OUTPUT", "full_audit_output"))
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
CACHE_DIR = OUTPUT_DIR / "cache"
CACHE_DIR.mkdir(exist_ok=True)
RESULTS_FILE = OUTPUT_DIR / "full_audit_results.jsonl"
PROGRESS_FILE = OUTPUT_DIR / "progress.json"

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

### Security-relevant operations (for IC-1s detection)
SHELL_EXEC, CREDENTIAL_ACCESS, NETWORK_EXTERNAL, FILE_WRITE, FILE_DELETE,
DB_WRITE, SYSTEM_MODIFY

## Response Format
Respond with ONLY valid JSON (no markdown, no explanation):
{
  "ic_class": "IC-0|IC-1s|IC-1q|IC-2",
  "undisclosed_capabilities": ["cap1", "cap2"],
  "overclaimed_capabilities": ["cap1"],
  "security_operations": ["SHELL_EXEC"],
  "confidence": 0.0-1.0,
  "reason": "one-line explanation"
}"""

USER_TEMPLATE = """\
LISTING (what users see before installation):
  Name: {name}
  Description: {description}

IMPLEMENTATION (SKILL.md content):
{skillmd_body}"""

# ── SKILL.md resolution ───────────────────────────────────────────────────────

def build_skillmd_index():
    """Build skill_name -> latest SKILL.md path mapping."""
    index = {}
    pattern = re.compile(
        r"api__v1__skills__(.+?)__file__path_3DSKILL\.md"
        r"_26version_3D(.+?)__([a-f0-9]+)\.md$"
    )
    for dirpath in SKILLMD_DIRS:
        if not os.path.isdir(dirpath):
            print(f"  [SKIP] {dirpath}")
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
                index[skill_name].append({"path": full_path, "version": version})

    resolved = {}
    for skill_name, entries in index.items():
        entries.sort(key=lambda e: e["version"], reverse=True)
        resolved[skill_name] = entries[0]["path"]
    return resolved


def read_skillmd(path, max_chars=3500):
    """Read SKILL.md, return structured content."""
    try:
        with open(path, "r", errors="replace") as f:
            content = f.read()
        if len(content) > max_chars:
            content = content[:max_chars] + "\n[... truncated ...]"
        return content
    except:
        return None


# ── LLM client (OpenAI-compatible, for vLLM) ─────────────────────────────────

class LLMClient:
    def __init__(self, api_base, api_key, model):
        from openai import OpenAI
        self.client = OpenAI(base_url=api_base, api_key=api_key)
        self.model = model

    def classify(self, name, description, skillmd_body, cache_key=""):
        if cache_key:
            cf = CACHE_DIR / f"{cache_key}.json"
            if cf.exists():
                try:
                    return json.loads(cf.read_text())
                except:
                    pass

        prompt = USER_TEMPLATE.format(
            name=name,
            description=description or "(no description)",
            skillmd_body=skillmd_body,
        )

        try:
            resp = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.1,
                max_tokens=300,
            )
            text = resp.choices[0].message.content.strip()

            # Clean markdown wrappers
            text = re.sub(r"^```json\s*", "", text)
            text = re.sub(r"\s*```$", "", text)
            
            parsed = json.loads(text)
            if cache_key:
                (CACHE_DIR / f"{cache_key}.json").write_text(
                    json.dumps(parsed, ensure_ascii=False)
                )
            return parsed
        except json.JSONDecodeError:
            return {"error": "json_parse_error", "raw": text[:200]}
        except Exception as e:
            return {"error": str(e)}


# ── Main ───────────────────────────────────────────────────────────────────────

def load_progress():
    if PROGRESS_FILE.exists():
        return set(json.loads(PROGRESS_FILE.read_text()))
    return set()

def save_progress(done_ids):
    PROGRESS_FILE.write_text(json.dumps(sorted(done_ids)))


def main():
    parser = argparse.ArgumentParser(
        description="Full-Scale Implementation-Grounded Audit (GPU)"
    )
    parser.add_argument("--api-base", default="http://localhost:8000/v1")
    parser.add_argument("--api-key", default="dummy")
    parser.add_argument("--model", default="Qwen/Qwen2.5-7B-Instruct")
    parser.add_argument("--workers", type=int, default=16)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    print("=" * 60)
    print("Full-Scale Implementation-Grounded Audit (GPU)")
    print("=" * 60)
    print(f"  Model:   {args.model}")
    print(f"  API:     {args.api_base}")
    print(f"  Workers: {args.workers}")
    print(f"  Output:  {RESULTS_FILE}")

    # Step 1: Build index
    print("\n[1/4] Building SKILL.md file index...")
    skillmd_index = build_skillmd_index()
    print(f"  Found {len(skillmd_index)} unique skill names")

    # Step 2: Load skills
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
    print(f"  Loaded {len(skills)} skills")

    # Step 3: Match
    matched = []
    for skill in skills:
        skill_name = skill["skill_id"].split("/")[-1] if "/" in skill["skill_id"] else skill["skill_id"]
        if skill_name in skillmd_index:
            matched.append((skill, skillmd_index[skill_name]))
    print(f"  Matched {len(matched)} skills to SKILL.md files")

    if args.dry_run:
        print("\n[DRY RUN] Exiting.")
        return

    # Step 4: Process
    done_ids = load_progress()
    todo = [(s, p) for s, p in matched if s["skill_id"] not in done_ids]
    if args.limit > 0:
        todo = todo[:args.limit]
    print(f"\n[3/4] Processing {len(todo)} skills...")
    print(f"  Already done: {len(done_ids)}")

    llm = LLMClient(args.api_base, args.api_key, args.model)
    start = time.time()
    completed = 0
    errors = 0

    with open(RESULTS_FILE, "a") as fout:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            def do_one(skill, path):
                body = read_skillmd(path)
                if not body or len(body) < 50:
                    return None
                cache_key = hashlib.md5(skill["skill_id"].encode()).hexdigest()
                result = llm.classify(
                    skill["name"], skill["description"], body, cache_key
                )
                if result:
                    result["skill_id"] = skill["skill_id"]
                    result["name"] = skill["name"]
                    result["desc_len"] = len(skill["description"] or "")
                    result["body_len"] = len(body)
                return result

            futures = {
                pool.submit(do_one, s, p): s for s, p in todo
            }

            for future in as_completed(futures):
                skill = futures[future]
                try:
                    result = future.result()
                    if result and "error" not in result:
                        fout.write(json.dumps(result, ensure_ascii=False) + "\n")
                        fout.flush()
                        done_ids.add(skill["skill_id"])
                        completed += 1
                    else:
                        errors += 1
                except Exception as e:
                    errors += 1

                total = completed + errors
                if total % 100 == 0:
                    elapsed = time.time() - start
                    rate = total / elapsed if elapsed > 0 else 0
                    eta = (len(todo) - total) / rate / 60 if rate > 0 else 0
                    print(f"  [{total}/{len(todo)}] {completed} ok, "
                          f"{errors} err, {rate:.1f}/s, ETA {eta:.0f}min")
                    save_progress(done_ids)

    save_progress(done_ids)
    elapsed = time.time() - start

    print(f"\n[4/4] Done!")
    print(f"  Completed: {completed}, Errors: {errors}")
    print(f"  Time: {elapsed/60:.1f} min")

    # Stats
    ic_counts = {}
    with open(RESULTS_FILE) as f:
        for line in f:
            try:
                r = json.loads(line)
                ic = r.get("ic_class", "unknown")
                ic_counts[ic] = ic_counts.get(ic, 0) + 1
            except:
                pass
    print("\n--- IC Distribution ---")
    total_ic = sum(ic_counts.values())
    for ic in sorted(ic_counts):
        pct = 100 * ic_counts[ic] / total_ic if total_ic else 0
        print(f"  {ic}: {ic_counts[ic]} ({pct:.1f}%)")


if __name__ == "__main__":
    main()
