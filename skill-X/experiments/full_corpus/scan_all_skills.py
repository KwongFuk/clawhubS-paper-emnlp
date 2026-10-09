#!/usr/bin/env python3
"""
scan_all_skills.py — Full-Corpus LLM Skill Auditor for ClawHub
================================================================
Scans all 18,443 ClawHub skills using an LLM (via OpenAI-compatible API)
to detect:
  - IC-0: Description-capability alignment
  - IC-1: Underdisclosed capabilities (hidden functions)
  - IC-2: Overclaimed capabilities (functional authority claims)
  - D1:   Overclaim severity scoring
  - Q1-Q4: Quality issues
  - A1-A3: Adversarial abuse signals

Designed to run on SLURM with vLLM serving a local model.

Usage:
  # With vLLM server running on same node:
  python3 scan_all_skills.py --api-base http://localhost:8000/v1 --model Qwen/Qwen2.5-32B-Instruct

  # With external API:
  OPENAI_API_KEY=xxx python3 scan_all_skills.py --model gpt-4o-mini

  # Dry run:
  python3 scan_all_skills.py --dry-run

  # Resume from batch N:
  python3 scan_all_skills.py --start-batch 50

Resume-safe: saves per-batch. Skips completed batches automatically.
"""
import argparse
import json
import os
import re
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

BASE_DIR = Path(__file__).parent
RESULTS_DIR = BASE_DIR / "results" / "scan_batches"
RESULTS_DIR.mkdir(exist_ok=True, parents=True)

# Try multiple DB locations
for candidate in [
    BASE_DIR.parents[2] / "data/experiment1_split/merged.sqlite3",  # Palmetto
    Path.home() / "clawhubS/data/experiment1_split/merged.sqlite3",
    BASE_DIR.parents[3] / "data/experiment1_split/merged.sqlite3",  # Local Mac
]:
    if candidate.exists():
        DB_PATH = candidate
        break
else:
    DB_PATH = None

BATCH_SIZE = 50
MAX_WORKERS = 8

# ── LLM Prompt ────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """\
You are a security auditor for an AI agent skill marketplace called ClawHub.
Your job is to analyze skill descriptions and classify them for security risks.

For each skill, analyze the description and provide a structured assessment.

## Classification Schema

### IC Class (Description-Capability Alignment)
- IC-0: Description adequately represents the likely scope of this skill
- IC-1s: Description likely HIDES security-sensitive capabilities (network access, file I/O, shell commands, credential handling) that the skill probably implements based on its category/name
- IC-1q: Description likely HIDES functional capabilities (quality issue, not security)
- IC-2: Description OVERCLAIMS capabilities it's unlikely to actually implement (claims authority, verification, official status without evidence)

### Overclaim Severity (D1)
- none: No overclaiming detected
- minor: Slight exaggeration but plausible
- major: Claims significant authority/official status unlikely to be backed by implementation

### Quality Issues
- Q1: Missing parameter details (desc doesn't mention key inputs/outputs)
- Q2: Vague routing trigger (an agent would struggle to know WHEN to invoke this skill)
- Q3: Functional overclaim (claims more than likely implemented)
- Q4: Missing permission disclosure (likely needs network/file/shell access but doesn't mention it)

### Abuse Signals
- A1: Description collision (sounds like it impersonates a well-known service)
- A2: Capability concealment (strategically vague to hide real purpose)
- A3: Trust inflation (claims endorsement, verification, or official status)

## Response Format
Respond with ONLY valid JSON:
{
  "ic_class": "IC-0|IC-1s|IC-1q|IC-2",
  "d1_severity": "none|minor|major",
  "quality_issues": ["Q1", "Q2", ...],
  "abuse_signals": ["A1", ...],
  "security_sensitive": true/false,
  "domains": ["domain1", "domain2", ...],
  "confidence": 0.0-1.0,
  "reason": "one-line explanation"
}"""

USER_TEMPLATE = """\
Skill Name: {name}
Skill ID: {skill_id}
Category: {category}
Downloads: {downloads:,}
Description:
{description}"""


# ── API Client ─────────────────────────────────────────────────────────────────

class LLMClient:
    def __init__(self, api_base: str, api_key: str, model: str):
        from openai import OpenAI
        self.client = OpenAI(base_url=api_base, api_key=api_key)
        self.model = model
        self.total_calls = 0
        self.total_errors = 0

    def classify(self, skill: dict, max_retries: int = 3) -> dict:
        user_msg = USER_TEMPLATE.format(**skill)
        for attempt in range(max_retries):
            try:
                resp = self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": user_msg},
                    ],
                    temperature=0,
                    max_tokens=400,
                )
                raw = resp.choices[0].message.content.strip()
                self.total_calls += 1

                # Parse JSON from response
                parsed = self._extract_json(raw)
                if parsed:
                    return {"skill_id": skill["skill_id"], "name": skill["name"],
                            "downloads": skill["downloads"], "result": parsed}
            except Exception as e:
                if attempt < max_retries - 1:
                    time.sleep(2 * (attempt + 1))
                else:
                    self.total_errors += 1
                    return {"skill_id": skill["skill_id"], "name": skill["name"],
                            "downloads": skill["downloads"],
                            "result": None, "error": str(e)[:200]}

        self.total_errors += 1
        return {"skill_id": skill["skill_id"], "name": skill["name"],
                "downloads": skill["downloads"],
                "result": None, "error": "parse_failed"}

    @staticmethod
    def _extract_json(text: str) -> dict | None:
        # Try direct parse
        try:
            return json.loads(text)
        except:
            pass
        # Try extracting JSON block
        for chunk in re.split(r'```(?:json)?', text):
            chunk = chunk.strip().rstrip('`').strip()
            if chunk.startswith('{'):
                try:
                    return json.loads(chunk)
                except:
                    pass
        # Try regex extraction
        m = re.search(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group())
            except:
                pass
        return None


# ── Database ───────────────────────────────────────────────────────────────────

def load_all_skills() -> list:
    """Load all ClawHub skills, ordered by downloads (highest first)."""
    db = sqlite3.connect(str(DB_PATH))
    rows = db.execute("""
        SELECT skill_id, name, description,
               COALESCE(downloads, 0) as downloads,
               COALESCE(category, '') as category
        FROM skills
        WHERE description IS NOT NULL AND length(description) > 10
        ORDER BY downloads DESC
    """).fetchall()
    db.close()
    return [{"skill_id": r[0], "name": r[1], "description": r[2],
             "downloads": r[3], "category": r[4]} for r in rows]


# ── Batch Processing ──────────────────────────────────────────────────────────

def process_batch(llm: LLMClient, batch: list, batch_idx: int,
                  max_workers: int) -> dict:
    """Process one batch of skills."""
    results = []
    t0 = time.time()

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(llm.classify, skill): skill for skill in batch}
        for i, future in enumerate(as_completed(futures)):
            r = future.result()
            results.append(r)
            if (i + 1) % 10 == 0:
                ok = sum(1 for x in results if x.get("result"))
                print(f"    [{i+1}/{len(batch)}] ok={ok} err={i+1-ok}", flush=True)

    elapsed = time.time() - t0

    # Quick stats
    ic_counts = {}
    sev_counts = {}
    for r in results:
        d = r.get("result")
        if d:
            ic = d.get("ic_class", "?")
            ic_counts[ic] = ic_counts.get(ic, 0) + 1
            sev = d.get("d1_severity", "?")
            sev_counts[sev] = sev_counts.get(sev, 0) + 1

    return {
        "batch_idx": batch_idx,
        "n_skills": len(batch),
        "n_ok": sum(1 for r in results if r.get("result")),
        "n_errors": sum(1 for r in results if not r.get("result")),
        "elapsed_s": round(elapsed, 1),
        "ic_dist": ic_counts,
        "severity_dist": sev_counts,
        "results": results,
    }


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Full-Corpus Skill Auditor")
    parser.add_argument("--api-base", default="http://localhost:8000/v1",
                       help="vLLM/OpenAI API base URL")
    parser.add_argument("--api-key", default=os.environ.get("OPENAI_API_KEY", "dummy"))
    parser.add_argument("--model", default="Qwen/Qwen2.5-32B-Instruct")
    parser.add_argument("--workers", type=int, default=MAX_WORKERS)
    parser.add_argument("--batch-size", type=int, default=BATCH_SIZE)
    parser.add_argument("--start-batch", type=int, default=0)
    parser.add_argument("--max-batches", type=int, default=9999)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if DB_PATH is None:
        print("ERROR: merged.sqlite3 not found. Check data paths.")
        sys.exit(1)

    print(f"Database: {DB_PATH}")
    skills = load_all_skills()

    # Partition
    batches = [skills[i:i+args.batch_size]
               for i in range(0, len(skills), args.batch_size)]

    # Count completed
    done_count = sum(1 for i in range(len(batches))
                     if (RESULTS_DIR / f"scan_batch_{i:04d}.json").exists())

    print(f"{'='*70}")
    print(f"FULL CORPUS SKILL AUDIT")
    print(f"{'='*70}")
    print(f"  Skills:      {len(skills)}")
    print(f"  Batches:     {len(batches)} (×{args.batch_size})")
    print(f"  Completed:   {done_count}/{len(batches)}")
    print(f"  Remaining:   {len(batches) - done_count}")
    print(f"  Model:       {args.model}")
    print(f"  API:         {args.api_base}")
    print(f"  Workers:     {args.workers}")
    print(f"{'='*70}")

    if args.dry_run:
        print("\n[DRY RUN] Batch status:")
        for i in range(len(batches)):
            f = RESULTS_DIR / f"scan_batch_{i:04d}.json"
            st = "DONE" if f.exists() else "TODO"
            print(f"  Batch {i:4d}: {len(batches[i]):3d} skills [{st}]")
        return

    # Create client
    llm = LLMClient(args.api_base, args.api_key, args.model)

    # Process batches
    processed = 0
    global_ic = {}
    global_sev = {}

    end_batch = min(len(batches), args.start_batch + args.max_batches)
    for batch_idx in range(args.start_batch, end_batch):
        out_file = RESULTS_DIR / f"scan_batch_{batch_idx:04d}.json"

        if out_file.exists():
            # Load existing to aggregate stats
            existing = json.loads(out_file.read_text())
            for k, v in existing.get("ic_dist", {}).items():
                global_ic[k] = global_ic.get(k, 0) + v
            print(f"  ⏩ Batch {batch_idx}: already done", flush=True)
            continue

        batch = batches[batch_idx]
        print(f"\n{'='*60}")
        print(f"Batch {batch_idx}/{len(batches)} | "
              f"Skills: {batch[0]['skill_id'][:25]}..{batch[-1]['skill_id'][:25]}")
        print(f"{'='*60}")

        result = process_batch(llm, batch, batch_idx, args.workers)

        # Save
        out_file.write_text(json.dumps(result, indent=2, ensure_ascii=False))

        # Accumulate
        for k, v in result["ic_dist"].items():
            global_ic[k] = global_ic.get(k, 0) + v
        for k, v in result["severity_dist"].items():
            global_sev[k] = global_sev.get(k, 0) + v

        print(f"  ✅ {result['n_ok']}/{result['n_skills']} ok | "
              f"{result['elapsed_s']:.1f}s | IC: {result['ic_dist']} | "
              f"D1: {result['severity_dist']}")
        print(f"  💾 {out_file.name}")

        processed += 1

        # Save running summary
        summary = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "model": args.model,
            "total_skills": len(skills),
            "batches_done": done_count + processed,
            "batches_total": len(batches),
            "global_ic_dist": global_ic,
            "global_severity_dist": global_sev,
            "total_calls": llm.total_calls,
            "total_errors": llm.total_errors,
        }
        (RESULTS_DIR.parent / "scan_progress.json").write_text(
            json.dumps(summary, indent=2))

    # Final
    print(f"\n{'='*70}")
    print(f"SCAN COMPLETE: {processed} new batches processed")
    print(f"  Global IC distribution: {global_ic}")
    print(f"  Global D1 severity: {global_sev}")
    print(f"  Total LLM calls: {llm.total_calls}")
    print(f"  Total errors: {llm.total_errors}")
    print(f"{'='*70}")


if __name__ == "__main__":
    main()
