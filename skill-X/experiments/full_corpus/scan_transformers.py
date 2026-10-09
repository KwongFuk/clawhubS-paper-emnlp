#!/usr/bin/env python3
"""
scan_transformers.py — Full-Corpus LLM Skill Auditor (transformers backend)
=============================================================================
Uses HuggingFace transformers pipeline directly on GPU.
No vLLM needed — runs Qwen2.5-7B-Instruct via AutoModelForCausalLM.

Resume-safe: saves per-batch, skips completed batches.

Usage:
  python3 scan_transformers.py --model Qwen/Qwen2.5-7B-Instruct
  python3 scan_transformers.py --dry-run
"""
import argparse
import json
import os
import re
import sqlite3
import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).parent
RESULTS_DIR = BASE_DIR / "results" / "scan_batches"
RESULTS_DIR.mkdir(exist_ok=True, parents=True)

# DB path resolution
for candidate in [
    Path.home() / "clawhubS/data/experiment1_split/merged.sqlite3",
    BASE_DIR.parents[3] / "data/experiment1_split/merged.sqlite3",
    BASE_DIR.parents[2] / "data/experiment1_split/merged.sqlite3",
]:
    if candidate.exists():
        DB_PATH = candidate
        break
else:
    DB_PATH = None

BATCH_SIZE = 50

# ── Classification Prompt ──────────────────────────────────────────────────────

SYSTEM_PROMPT = """\
You are a security auditor for an AI agent skill marketplace called ClawHub.
Analyze the skill description and classify it.

## Classification
- ic_class: IC-0 (description matches scope) | IC-1s (hides security-sensitive capabilities) | IC-1q (hides functional capabilities) | IC-2 (overclaims capabilities)
- d1_severity: none | minor | major (overclaiming level)
- quality_issues: list of Q1 (missing params), Q2 (vague trigger), Q3 (overclaim), Q4 (missing permissions)
- abuse_signals: list of A1 (impersonation), A2 (concealment), A3 (trust inflation)
- security_sensitive: true/false (mentions network, filesystem, shell, credentials, APIs)
- domains: 2-5 functional domains
- confidence: 0.0-1.0
- reason: one-line explanation

Respond with ONLY valid JSON:
{"ic_class": "IC-0|IC-1s|IC-1q|IC-2", "d1_severity": "none|minor|major", "quality_issues": [], "abuse_signals": [], "security_sensitive": false, "domains": [], "confidence": 0.9, "reason": "..."}"""

USER_TEMPLATE = """\
Skill: {name}
ID: {skill_id}
Category: {category}
Downloads: {downloads:,}
Description: {description}"""


# ── Model Loading ──────────────────────────────────────────────────────────────

def load_model(model_name: str):
    """Load model and tokenizer."""
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    print(f"Loading tokenizer: {model_name}")
    tokenizer = AutoTokenizer.from_pretrained(
        model_name, trust_remote_code=True)

    print(f"Loading model: {model_name}")
    model = AutoModelForCausalLM.from_pretrained(
        model_name,
        torch_dtype=torch.float16,
        device_map="auto",
        trust_remote_code=True,
    )
    model.eval()

    gpu_mem = torch.cuda.memory_allocated() / 1024**3
    print(f"✅ Model loaded. GPU memory: {gpu_mem:.1f} GB")

    return model, tokenizer


def generate(model, tokenizer, messages: list, max_new_tokens: int = 400) -> str:
    """Generate response from chat messages."""
    import torch

    text = tokenizer.apply_chat_template(
        messages, tokenize=False, add_generation_prompt=True)

    inputs = tokenizer(text, return_tensors="pt").to(model.device)

    with torch.no_grad():
        outputs = model.generate(
            **inputs,
            max_new_tokens=max_new_tokens,
            temperature=0.1,
            top_p=0.95,
            do_sample=True,
            pad_token_id=tokenizer.eos_token_id,
        )

    # Decode only the new tokens
    new_tokens = outputs[0][inputs["input_ids"].shape[1]:]
    return tokenizer.decode(new_tokens, skip_special_tokens=True).strip()


def extract_json(text: str) -> dict | None:
    """Extract JSON from LLM response."""
    # Direct parse
    try:
        return json.loads(text)
    except:
        pass
    # Code block
    for chunk in re.split(r'```(?:json)?', text):
        chunk = chunk.strip().rstrip('`').strip()
        if chunk.startswith('{'):
            try:
                return json.loads(chunk)
            except:
                pass
    # Regex
    m = re.search(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group())
        except:
            pass
    return None


# ── Database ───────────────────────────────────────────────────────────────────

def load_all_skills() -> list:
    db = sqlite3.connect(str(DB_PATH))
    rows = db.execute("""
        SELECT skill_id, name, description,
               COALESCE(downloads, 0), COALESCE(category, '')
        FROM skills
        WHERE description IS NOT NULL AND length(description) > 10
        ORDER BY downloads DESC
    """).fetchall()
    db.close()
    return [{"skill_id": r[0], "name": r[1], "description": r[2],
             "downloads": r[3], "category": r[4]} for r in rows]


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="Qwen/Qwen2.5-7B-Instruct")
    parser.add_argument("--batch-size", type=int, default=BATCH_SIZE)
    parser.add_argument("--start-batch", type=int, default=0)
    parser.add_argument("--max-batches", type=int, default=9999)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if DB_PATH is None:
        print("ERROR: merged.sqlite3 not found")
        sys.exit(1)

    skills = load_all_skills()
    batches = [skills[i:i+args.batch_size]
               for i in range(0, len(skills), args.batch_size)]

    done = sum(1 for i in range(len(batches))
               if (RESULTS_DIR / f"scan_batch_{i:04d}.json").exists())

    print(f"{'='*70}")
    print(f"FULL CORPUS SKILL AUDIT (transformers)")
    print(f"{'='*70}")
    print(f"  Skills:    {len(skills)}")
    print(f"  Batches:   {len(batches)} (×{args.batch_size})")
    print(f"  Completed: {done}/{len(batches)}")
    print(f"  Model:     {args.model}")
    print(f"{'='*70}")

    if args.dry_run:
        for i in range(len(batches)):
            st = "DONE" if (RESULTS_DIR / f"scan_batch_{i:04d}.json").exists() else "TODO"
            print(f"  Batch {i:4d}: {len(batches[i]):3d} skills [{st}]")
        return

    # Load model
    model, tokenizer = load_model(args.model)

    # Process
    total_calls = 0
    total_errors = 0
    global_ic = {}
    global_sev = {}

    end_batch = min(len(batches), args.start_batch + args.max_batches)
    for batch_idx in range(args.start_batch, end_batch):
        out_file = RESULTS_DIR / f"scan_batch_{batch_idx:04d}.json"

        if out_file.exists():
            existing = json.loads(out_file.read_text())
            for k, v in existing.get("ic_dist", {}).items():
                global_ic[k] = global_ic.get(k, 0) + v
            continue

        batch = batches[batch_idx]
        print(f"\n{'='*60}")
        print(f"Batch {batch_idx}/{len(batches)} ({len(batch)} skills)")
        print(f"{'='*60}")

        results = []
        batch_errors = 0
        t0 = time.time()

        for i, skill in enumerate(batch):
            messages = [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": USER_TEMPLATE.format(**skill)},
            ]

            try:
                raw = generate(model, tokenizer, messages)
                parsed = extract_json(raw)
                result = {
                    "skill_id": skill["skill_id"],
                    "name": skill["name"],
                    "downloads": skill["downloads"],
                    "result": parsed,
                }
                if parsed is None:
                    result["error"] = "parse_failed"
                    result["raw"] = raw[:500]
                    batch_errors += 1
            except Exception as e:
                result = {
                    "skill_id": skill["skill_id"],
                    "name": skill["name"],
                    "downloads": skill["downloads"],
                    "result": None,
                    "error": str(e)[:200],
                }
                batch_errors += 1

            results.append(result)
            total_calls += 1

            if (i + 1) % 10 == 0:
                elapsed = time.time() - t0
                rate = (i + 1) / elapsed
                print(f"  [{i+1}/{len(batch)}] "
                      f"ok={i+1-batch_errors} err={batch_errors} "
                      f"rate={rate:.1f}/s ETA={((len(batch)-i-1)/rate):.0f}s",
                      flush=True)

        elapsed = time.time() - t0

        # Stats
        ic_counts = {}
        sev_counts = {}
        for r in results:
            d = r.get("result")
            if d:
                ic = d.get("ic_class", "?")
                ic_counts[ic] = ic_counts.get(ic, 0) + 1
                sev = d.get("d1_severity", "?")
                sev_counts[sev] = sev_counts.get(sev, 0) + 1

        batch_data = {
            "batch_idx": batch_idx,
            "n_skills": len(batch),
            "n_ok": len(batch) - batch_errors,
            "n_errors": batch_errors,
            "elapsed_s": round(elapsed, 1),
            "rate_per_s": round(len(batch) / elapsed, 2) if elapsed > 0 else 0,
            "ic_dist": ic_counts,
            "severity_dist": sev_counts,
            "results": results,
        }
        out_file.write_text(json.dumps(batch_data, indent=2, ensure_ascii=False))

        for k, v in ic_counts.items():
            global_ic[k] = global_ic.get(k, 0) + v
        for k, v in sev_counts.items():
            global_sev[k] = global_sev.get(k, 0) + v

        total_errors += batch_errors

        print(f"  ✅ {len(batch)-batch_errors}/{len(batch)} ok | "
              f"{elapsed:.1f}s ({len(batch)/elapsed:.1f}/s) | "
              f"IC: {ic_counts} | D1: {sev_counts}")

        # Running summary
        summary = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "model": args.model,
            "total_skills": len(skills),
            "batches_done": batch_idx + 1,
            "batches_total": len(batches),
            "global_ic_dist": global_ic,
            "global_severity_dist": global_sev,
            "total_calls": total_calls,
            "total_errors": total_errors,
        }
        (RESULTS_DIR.parent / "scan_progress.json").write_text(
            json.dumps(summary, indent=2))

    print(f"\n{'='*70}")
    print(f"SCAN COMPLETE")
    print(f"  IC: {global_ic}")
    print(f"  D1: {global_sev}")
    print(f"  Calls: {total_calls}, Errors: {total_errors}")
    print(f"{'='*70}")


if __name__ == "__main__":
    main()
