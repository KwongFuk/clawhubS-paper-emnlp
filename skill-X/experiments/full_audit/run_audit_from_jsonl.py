#!/usr/bin/env python3
"""
Full-Scale Implementation-Grounded Audit — All Files Version
=============================================================
For each skill, compares the HIGH-LEVEL listing (name + description)
against ALL implementation files (SKILL.md, code, scripts, configs).

Input: audit_input_full.jsonl (extracted by extract_input.py)
Output: full_audit_results.jsonl

Each entry contains:
  - listing: name + description (what users see before install)
  - implementation: concatenated content of SKILL.md + code + scripts + configs
"""

import json, os, re, sys, time, argparse
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

# ── Prompt ─────────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """\
You are auditing an AI agent skill marketplace. For each skill, you receive:
1. LISTING — the high-level name and description users see before installation
2. IMPLEMENTATION — the actual files (SKILL.md, code, scripts, configs)

The listing is intentionally high-level. Your job: identify concrete capabilities
present in the implementation that are NOT disclosed (even implicitly) in the
listing. Focus especially on security-relevant operations.

## IC Class (Implementation-Grounded)
- IC-0: Listing adequately represents the implementation's actual scope
- IC-1s: Implementation contains security-relevant operations NOT disclosed
  in listing. Security-relevant = shell execution, credential/API-key access,
  network calls to external services, file system writes/deletes, database
  writes, system modification, privilege escalation
- IC-1q: Implementation has non-trivial functional capabilities NOT mentioned
  in listing (quality gap, not security-relevant)
- IC-2: Listing claims capabilities the implementation does NOT actually have

## Response — ONLY valid JSON, no markdown:
{
  "ic_class": "IC-0|IC-1s|IC-1q|IC-2",
  "undisclosed_capabilities": ["list of capabilities in impl but not in listing"],
  "overclaimed_capabilities": ["list of capabilities in listing but not in impl"],
  "security_operations": ["SHELL_EXEC","CREDENTIAL_ACCESS","NETWORK_EXTERNAL","FILE_WRITE","FILE_DELETE","DB_WRITE","SYSTEM_MODIFY"],
  "confidence": 0.0-1.0,
  "reason": "one-line explanation"
}"""

USER_TEMPLATE = """\
LISTING (what users see before installation):
  Name: {name}
  Description: {description}

IMPLEMENTATION FILES:
{implementation}"""

# ── LLM client ────────────────────────────────────────────────────────────────

class LLMClient:
    def __init__(self, api_base, api_key, model):
        from openai import OpenAI
        self.client = OpenAI(base_url=api_base, api_key=api_key)
        self.model = model

    def classify(self, name, description, implementation):
        prompt = USER_TEMPLATE.format(
            name=name,
            description=description or "(no description)",
            implementation=implementation,
        )
        for attempt in range(3):
            try:
                resp = self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": prompt},
                    ],
                    temperature=0.1,
                    max_tokens=400,
                )
                text = resp.choices[0].message.content.strip()
                text = re.sub(r"^```json\s*", "", text)
                text = re.sub(r"\s*```$", "", text)
                return json.loads(text)
            except json.JSONDecodeError:
                if attempt == 2:
                    return {"error": "json_parse", "raw": text[:200]}
            except Exception as e:
                if attempt == 2:
                    return {"error": str(e)}
                time.sleep(1)
        return {"error": "max_retries"}


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", default="full_audit_results.jsonl")
    parser.add_argument("--api-base", default="http://localhost:8000/v1")
    parser.add_argument("--api-key", default="dummy")
    parser.add_argument("--model", default="Qwen/Qwen2.5-7B-Instruct")
    parser.add_argument("--workers", type=int, default=16)
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    print("=" * 60)
    print("Full Implementation-Grounded Audit (All Files)")
    print("=" * 60)
    print(f"  Input:   {args.input}")
    print(f"  Output:  {args.output}")
    print(f"  Model:   {args.model}")
    print(f"  Workers: {args.workers}")

    # Load input
    entries = []
    with open(args.input) as f:
        for line in f:
            entries.append(json.loads(line))
    print(f"  Loaded {len(entries)} skills")

    # Progress tracking
    progress_file = args.output + ".progress"
    done_ids = set()
    if os.path.exists(progress_file):
        done_ids = set(json.loads(open(progress_file).read()))

    todo = [e for e in entries if e["skill_id"] not in done_ids]
    if args.limit > 0:
        todo = todo[:args.limit]
    print(f"  Already done: {len(done_ids)}")
    print(f"  To process: {len(todo)}")

    llm = LLMClient(args.api_base, args.api_key, args.model)
    start = time.time()
    completed = errors = 0

    with open(args.output, "a") as fout:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            def do_one(entry):
                result = llm.classify(
                    entry["name"],
                    entry["description"],
                    entry["implementation"],
                )
                if result:
                    result["skill_id"] = entry["skill_id"]
                    result["name"] = entry["name"]
                    result["n_files"] = len(entry.get("files_included", []))
                    result["files"] = entry.get("files_included", [])
                return result

            futures = {pool.submit(do_one, e): e for e in todo}

            for future in as_completed(futures):
                entry = futures[future]
                try:
                    result = future.result()
                    if result and "error" not in result:
                        fout.write(json.dumps(result, ensure_ascii=False) + "\n")
                        fout.flush()
                        done_ids.add(entry["skill_id"])
                        completed += 1
                    else:
                        errors += 1
                        if result:
                            fout.write(json.dumps({
                                "skill_id": entry["skill_id"], **result
                            }, ensure_ascii=False) + "\n")
                except Exception as e:
                    errors += 1

                total = completed + errors
                if total % 100 == 0:
                    elapsed = time.time() - start
                    rate = total / elapsed if elapsed > 0 else 0
                    eta = (len(todo) - total) / rate / 60 if rate > 0 else 0
                    print(f"  [{total}/{len(todo)}] {completed} ok, "
                          f"{errors} err, {rate:.1f}/s, ETA {eta:.0f}min")
                    with open(progress_file, "w") as pf:
                        pf.write(json.dumps(sorted(done_ids)))

    with open(progress_file, "w") as pf:
        pf.write(json.dumps(sorted(done_ids)))

    elapsed = time.time() - start
    print(f"\nDone! {completed} ok, {errors} err, {elapsed/60:.1f} min")

    # Stats
    ic_counts = {}
    with open(args.output) as f:
        for line in f:
            try:
                r = json.loads(line)
                ic = r.get("ic_class", "unknown")
                ic_counts[ic] = ic_counts.get(ic, 0) + 1
            except:
                pass
    print("\n--- IC Distribution (Implementation-Grounded, All Files) ---")
    total_ic = sum(ic_counts.values())
    for ic in sorted(ic_counts):
        pct = 100 * ic_counts[ic] / total_ic if total_ic else 0
        print(f"  {ic}: {ic_counts[ic]} ({pct:.1f}%)")


if __name__ == "__main__":
    main()
