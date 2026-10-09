#!/usr/bin/env python3
"""
run_d1_full.py — D1 Overclaim + IC Proxy Classification for 18,443 ClawHub Skills
==================================================================================
Uses LLM to analyze each skill's description for:
  1. Authority/trust claims (overclaim signals)
  2. Capability scope estimation
  3. IC proxy classification (desc-only mode)

Two modes:
  --mode proxy:   Uses SSH tunnel proxy (sub2api on Palmetto)
  --mode openclaw: Uses local OpenClaw gateway (Gemini)
  --mode direct:  Uses OpenAI API directly

Resume-safe: saves results per batch, skips completed batches.

Usage:
  # On Palmetto via proxy:
  python3 run_d1_full.py --mode proxy --base-url http://localhost:18082/v1 --model gpt-4o-mini

  # Local via OpenClaw:
  python3 run_d1_full.py --mode openclaw

  # Direct API:
  OPENAI_API_KEY=xxx python3 run_d1_full.py --mode direct --model gpt-4o-mini
"""
import argparse
import json
import os
import sqlite3
import subprocess
import signal
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

BASE_DIR = Path(__file__).parent
RESULTS_DIR = BASE_DIR / "results" / "d1_batches"
RESULTS_DIR.mkdir(exist_ok=True, parents=True)

DB_PATH = Path(__file__).resolve().parents[3] / "data/experiment1_split/merged.sqlite3"

BATCH_SIZE = 100  # Skills per batch file
MAX_WORKERS = 16  # Parallel LLM calls

# ── D1 System Prompt ───────────────────────────────────────────────────────────
D1_SYSTEM = """\
You are a skill description auditor for an AI agent marketplace.
Analyze the given skill description for overclaiming and capability scope.

Evaluate:
1. AUTHORITY CLAIMS: Does the description claim the skill verifies, recommends,
   ranks, curates, certifies, or acts as a quality arbiter? (yes/no)
2. OFFICIAL STATUS: Does it claim official, verified, or endorsed status? (yes/no)
3. OVERCLAIM SEVERITY: How much does the description claim beyond what a
   typical skill of this type would implement?
   - none: Description matches expected scope
   - minor: Slight exaggeration but reasonable
   - major: Claims significant authority/capability unlikely to be implemented
4. CAPABILITY DOMAINS: List 2-5 functional domains the description claims
   (e.g., "web search", "code review", "file management")
5. SECURITY SENSITIVITY: Does the description mention any security-sensitive
   operations (network, filesystem, shell, credentials, APIs)? (yes/no)
6. DESCRIPTION QUALITY: Is the description sufficient to understand what
   the skill does? (adequate/vague/misleading)

Respond in JSON only:
{"authority": bool, "official": bool, "severity": "none|minor|major",
 "domains": ["domain1", ...], "security_sensitive": bool,
 "quality": "adequate|vague|misleading", "reason": "brief explanation"}"""

D1_USER_TEMPLATE = """\
Skill: {name}
ID: {skill_id}
Description: {description}
Category: {category}
Downloads: {downloads}"""


# ── LLM Backends ───────────────────────────────────────────────────────────────

def call_openai(client, model: str, system: str, user: str) -> dict | None:
    """Call OpenAI-compatible API."""
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0,
            max_tokens=300,
            response_format={"type": "json_object"},
        )
        raw = resp.choices[0].message.content.strip()
        return json.loads(raw)
    except json.JSONDecodeError:
        # Try extracting JSON from response
        import re
        m = re.search(r'\{.*\}', raw, re.DOTALL)
        if m:
            return json.loads(m.group())
        return None
    except Exception as e:
        return None


def call_openclaw(prompt: str) -> str | None:
    """Call via OpenClaw gateway."""
    OPENCLAW_BIN = os.environ.get("OPENCLAW_BIN", "/opt/homebrew/bin/openclaw")
    OPENCLAW_ENV = {**os.environ, "PATH": "/opt/homebrew/bin:" + os.environ.get("PATH", "")}
    try:
        p = subprocess.Popen(
            [OPENCLAW_BIN, "infer", "model", "run", "--gateway",
             "--prompt", prompt, "--json"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL, env=OPENCLAW_ENV,
            cwd=str(BASE_DIR), preexec_fn=os.setsid,
        )
        stdout, _ = p.communicate(timeout=60)
        if p.returncode == 0 and stdout:
            d = json.loads(stdout.decode("utf-8", errors="replace").strip())
            text = d.get("outputs", [{}])[0].get("text", "").strip()
            if text:
                return text
    except subprocess.TimeoutExpired:
        os.killpg(os.getpgid(p.pid), signal.SIGKILL)
        p.wait()
    except Exception:
        pass
    return None


# ── Main Pipeline ──────────────────────────────────────────────────────────────

def process_skill(skill: dict, client, model: str, mode: str) -> dict:
    """Process a single skill through D1."""
    user_prompt = D1_USER_TEMPLATE.format(**skill)

    result = {"skill_id": skill["skill_id"], "name": skill["name"],
              "downloads": skill["downloads"]}

    if mode == "openclaw":
        full_prompt = f"{D1_SYSTEM}\n\n{user_prompt}"
        raw = call_openclaw(full_prompt)
        if raw:
            try:
                import re
                m = re.search(r'\{.*\}', raw, re.DOTALL)
                if m:
                    result["d1"] = json.loads(m.group())
                else:
                    result["d1"] = json.loads(raw)
            except:
                result["d1"] = None
                result["error"] = "parse_error"
        else:
            result["d1"] = None
            result["error"] = "no_response"
    else:
        d1 = call_openai(client, model, D1_SYSTEM, user_prompt)
        result["d1"] = d1
        if d1 is None:
            result["error"] = "api_error"

    return result


def main():
    parser = argparse.ArgumentParser(description="D1 Full Corpus Classification")
    parser.add_argument("--mode", choices=["proxy", "openclaw", "direct"],
                       default="direct", help="LLM backend mode")
    parser.add_argument("--base-url", type=str,
                       help="Base URL for proxy/direct mode (e.g., http://localhost:18082/v1)")
    parser.add_argument("--model", type=str, default="gpt-4o-mini",
                       help="Model name for API calls")
    parser.add_argument("--workers", type=int, default=MAX_WORKERS,
                       help="Number of parallel workers")
    parser.add_argument("--batch-size", type=int, default=BATCH_SIZE)
    parser.add_argument("--start-batch", type=int, default=0,
                       help="Start from this batch number")
    parser.add_argument("--max-batches", type=int, default=999,
                       help="Maximum number of batches to process")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    # Load skills
    db = sqlite3.connect(str(DB_PATH))
    rows = db.execute("""
        SELECT skill_id, name, description, downloads, category
        FROM skills
        WHERE description IS NOT NULL AND length(description) > 10
        ORDER BY downloads DESC
    """).fetchall()
    db.close()

    skills = [{"skill_id": r[0], "name": r[1], "description": r[2],
               "downloads": r[3] or 0, "category": r[4] or ""}
              for r in rows]

    # Partition into batches
    batches = []
    for i in range(0, len(skills), args.batch_size):
        batches.append(skills[i:i + args.batch_size])

    total_batches = len(batches)
    print(f"Total skills: {len(skills)}")
    print(f"Batch size: {args.batch_size}")
    print(f"Total batches: {total_batches}")
    print(f"Mode: {args.mode}")
    print(f"Model: {args.model}")
    print(f"Workers: {args.workers}")

    if args.dry_run:
        print("\n[DRY RUN] Would process batches:")
        for i, batch in enumerate(batches):
            out_f = RESULTS_DIR / f"d1_batch_{i:04d}.json"
            status = "DONE" if out_f.exists() else "TODO"
            print(f"  Batch {i:4d}: {len(batch)} skills [{status}]")
        done = sum(1 for i in range(total_batches)
                   if (RESULTS_DIR / f"d1_batch_{i:04d}.json").exists())
        print(f"\n  Done: {done}/{total_batches}")
        return

    # Setup client
    client = None
    if args.mode in ("proxy", "direct"):
        from openai import OpenAI
        kwargs = {}
        if args.mode == "proxy":
            kwargs["base_url"] = args.base_url or "http://localhost:18082/v1"
            kwargs["api_key"] = "dummy"
        else:
            kwargs["api_key"] = os.environ.get("OPENAI_API_KEY")
            if args.base_url:
                kwargs["base_url"] = args.base_url
        client = OpenAI(**kwargs)

    # Process batches
    processed = 0
    for batch_idx in range(args.start_batch, min(total_batches, args.start_batch + args.max_batches)):
        batch = batches[batch_idx]
        out_file = RESULTS_DIR / f"d1_batch_{batch_idx:04d}.json"

        if out_file.exists():
            print(f"\n⏩ Batch {batch_idx}/{total_batches}: already done")
            continue

        print(f"\n{'='*60}")
        print(f"Batch {batch_idx}/{total_batches} ({len(batch)} skills)")
        print(f"{'='*60}")

        results = []
        errors = 0
        t0 = time.time()

        if args.mode == "openclaw":
            # Sequential for OpenClaw
            for i, skill in enumerate(batch):
                r = process_skill(skill, None, args.model, args.mode)
                results.append(r)
                if r.get("error"):
                    errors += 1
                if (i + 1) % 10 == 0:
                    print(f"  [{i+1}/{len(batch)}] errors={errors}", flush=True)
                time.sleep(1)
        else:
            # Parallel for API
            with ThreadPoolExecutor(max_workers=args.workers) as executor:
                futures = {
                    executor.submit(process_skill, skill, client, args.model, args.mode): skill
                    for skill in batch
                }
                for i, future in enumerate(as_completed(futures)):
                    r = future.result()
                    results.append(r)
                    if r.get("error"):
                        errors += 1
                    if (i + 1) % 20 == 0:
                        print(f"  [{i+1}/{len(batch)}] errors={errors}", flush=True)

        elapsed = time.time() - t0

        # Save batch
        batch_data = {
            "batch_idx": batch_idx,
            "n_skills": len(batch),
            "n_errors": errors,
            "elapsed_s": round(elapsed, 1),
            "model": args.model,
            "mode": args.mode,
            "results": results,
        }
        out_file.write_text(json.dumps(batch_data, indent=2, ensure_ascii=False))

        # Quick stats
        severities = {}
        for r in results:
            d1 = r.get("d1")
            if d1:
                sev = d1.get("severity", "unknown")
                severities[sev] = severities.get(sev, 0) + 1

        print(f"  ✅ {len(results)} done, {errors} errors, {elapsed:.1f}s")
        print(f"  Severity: {severities}")
        print(f"  💾 {out_file.name}")

        processed += 1

    print(f"\n✅ Processed {processed} batches")


if __name__ == "__main__":
    main()
