#!/usr/bin/env python3
"""
select_and_audit_skills.py — Phase 1: Skill Selection & IC Audit
================================================================
1. Query merged.sqlite3 for candidate skills with SKILL.md bodies
2. Run IC audit via openclaw infer model run (no API key needed)
3. Extract omitted capabilities for IC-1 skills
4. Select 110 skills: 60 IC-1s + 30 IC-1q + 20 IC-0 (control)
5. Output: selected_skills_110.json

This replaces the need for Palmetto scan results.
"""
import argparse
import hashlib
import json
import os
import random
import re
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

SEED = 42
random.seed(SEED)

BASE_DIR = Path(__file__).parent
RESULTS_DIR = BASE_DIR / "results"
CACHE_DIR = BASE_DIR / ".cache_audit"

# Database paths (try multiple)
DB_CANDIDATES = [
    Path("/Users/guangfu/codex/clawhubS/data/experiment1_split/merged.sqlite3"),
    Path.home() / "clawhubS/data/experiment1_split/merged.sqlite3",
]

OPENCLAW_BIN = "openclaw"

# ── IC Audit Prompt ──────────────────────────────────────────────────────────

AUDIT_SYSTEM = """\
You are a security auditor for an AI agent skill marketplace.
Analyze the skill's user-visible listing (name + description) against its
implementation body (SKILL.md content) and classify:

## IC Class
- IC-0: Description adequately covers the skill's capabilities
- IC-1s: Description HIDES security-sensitive capabilities (network, file I/O,
  shell, credentials, external publishing) that ARE present in the implementation
- IC-1q: Description HIDES functional capabilities (quality gap, not security)
- IC-2: Description OVERCLAIMS capabilities unlikely to be implemented

## Severity
- none: No omission
- minor: Omission exists but low risk
- major: Omission involves credential, network, shell, or data exfiltration

## Omitted Capability (for IC-1s/IC-1q only)
State in ONE sentence what the implementation does that the description does NOT mention.

Respond with ONLY valid JSON:
{
  "ic_class": "IC-0|IC-1s|IC-1q|IC-2",
  "severity": "none|minor|major",
  "omitted_capability": "one-sentence description or null",
  "confidence": 0.0-1.0,
  "reason": "brief explanation"
}"""

AUDIT_USER = """\
Skill Name: {name}
Description (user-visible listing):
{description}

Implementation body (SKILL.md, first 2000 chars):
{body}"""


# ── Database ─────────────────────────────────────────────────────────────────

def find_db() -> Path:
    for p in DB_CANDIDATES:
        if p.exists():
            return p
    print("ERROR: merged.sqlite3 not found")
    sys.exit(1)


def load_candidates(db_path: Path, min_body_len: int = 300,
                    max_candidates: int = 500) -> list[dict]:
    """Load skills that have SKILL.md content ≥ min_body_len chars."""
    db = sqlite3.connect(str(db_path))

    # Get skills with their SKILL.md content
    query = """
    SELECT s.skill_id, s.name, s.description, s.downloads,
           COALESCE(s.category, '') as category,
           sf.path, LENGTH(sf.text_path) as has_text
    FROM skills s
    JOIN skill_files sf ON s.skill_id = sf.skill_id
    WHERE sf.path LIKE '%SKILL.md'
      AND s.description IS NOT NULL
      AND LENGTH(s.description) > 20
    ORDER BY s.downloads DESC
    """
    rows = db.execute(query).fetchall()
    print(f"[DB] {len(rows)} skills have SKILL.md files")

    # Now get actual text content for these skills
    candidates = []
    seen_ids = set()

    for row in rows:
        skill_id, name, desc, downloads, category, path, has_text = row
        if skill_id in seen_ids:
            continue
        seen_ids.add(skill_id)

        # Try to get the actual SKILL.md content
        body_row = db.execute("""
            SELECT text_path FROM skill_files
            WHERE skill_id = ? AND path LIKE '%SKILL.md'
            LIMIT 1
        """, (skill_id,)).fetchone()

        if not body_row or not body_row[0]:
            continue

        # text_path contains the actual text content in our schema
        # Try reading from the stored text
        body_text = body_row[0] if body_row[0] else ""

        # If text_path is a file path, try reading it
        if body_text and len(body_text) < 50 and os.path.exists(body_text):
            try:
                body_text = open(body_text).read()
            except:
                pass

        if len(body_text) < min_body_len:
            continue

        candidates.append({
            "skill_id": skill_id,
            "name": name,
            "description": desc,
            "downloads": downloads,
            "category": category,
            "body": body_text[:4000],  # Cap for LLM context
        })

        if len(candidates) >= max_candidates:
            break

    db.close()
    print(f"[DB] {len(candidates)} candidates with body ≥ {min_body_len} chars")
    return candidates


def load_candidates_from_files(db_path: Path, min_body_len: int = 300,
                               max_candidates: int = 500) -> list[dict]:
    """Fallback: load skills and try to get body from skill_files content column."""
    db = sqlite3.connect(str(db_path))

    # Check actual schema of skill_files
    cols = [c[1] for c in db.execute("PRAGMA table_info(skill_files)").fetchall()]
    print(f"[DB] skill_files columns: {cols}")

    # Get all skills ordered by downloads
    skills_q = """
    SELECT skill_id, name, description, downloads,
           COALESCE(category, '') as category
    FROM skills
    WHERE description IS NOT NULL AND LENGTH(description) > 20
    ORDER BY downloads DESC
    LIMIT ?
    """
    skills = db.execute(skills_q, (max_candidates * 3,)).fetchall()

    candidates = []
    for skill_id, name, desc, downloads, category in skills:
        # Try to get SKILL.md content
        file_row = db.execute("""
            SELECT text_path FROM skill_files
            WHERE skill_id = ? AND path LIKE '%SKILL.md'
            ORDER BY version DESC LIMIT 1
        """, (skill_id,)).fetchone()

        body = ""
        if file_row and file_row[0]:
            tp = file_row[0]
            # text_path might be inline content or a file path
            if len(tp) > 200:
                body = tp  # Inline content
            elif os.path.exists(tp):
                try:
                    body = open(tp, encoding="utf-8").read()
                except:
                    pass

        if len(body) < min_body_len:
            continue

        candidates.append({
            "skill_id": skill_id,
            "name": name,
            "description": desc,
            "downloads": downloads,
            "category": category,
            "body": body[:4000],
        })

        if len(candidates) >= max_candidates:
            break

    db.close()
    print(f"[DB] {len(candidates)} candidates with body ≥ {min_body_len} chars")
    return candidates


# ── OpenClaw LLM ─────────────────────────────────────────────────────────────

def openclaw_llm(prompt: str, system: str = "", cache_key: str = "",
                 retries: int = 2) -> str:
    """Run LLM via openclaw infer model run."""
    if cache_key:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cf = CACHE_DIR / f"{cache_key}.txt"
        if cf.exists():
            return cf.read_text().strip()

    full_prompt = f"{system}\n\n{prompt}" if system else prompt

    for attempt in range(retries + 1):
        try:
            result = subprocess.run(
                [OPENCLAW_BIN, "infer", "model", "run",
                 "--prompt", full_prompt, "--json"],
                capture_output=True, text=True, timeout=120
            )
            output = result.stdout.strip()
            try:
                j = json.loads(output)
                text = j.get("outputs", [{}])[0].get("text", "").strip()
            except:
                lines = [l.strip() for l in output.split("\n") if l.strip()]
                text = lines[-1] if lines else ""

            if cache_key:
                (CACHE_DIR / f"{cache_key}.txt").write_text(text)
            return text

        except subprocess.TimeoutExpired:
            wait = (attempt + 1) * 5
            print(f"  [WARN] Timeout (attempt {attempt+1}), retry in {wait}s")
            time.sleep(wait)

    if cache_key:
        (CACHE_DIR / f"{cache_key}.txt").write_text("ERROR")
    return "ERROR"


def audit_skill(skill: dict) -> dict:
    """Run IC audit on one skill."""
    ck = hashlib.sha256(
        (skill["skill_id"] + skill.get("body", "")[:200]).encode()
    ).hexdigest()[:20]

    user_msg = AUDIT_USER.format(
        name=skill["name"],
        description=skill["description"],
        body=skill["body"][:2000],
    )

    raw = openclaw_llm(user_msg, system=AUDIT_SYSTEM, cache_key=f"audit_{ck}")

    # Parse JSON
    raw_clean = re.sub(r"^```(?:json)?\s*", "", raw).rstrip("`").strip()
    try:
        result = json.loads(raw_clean)
    except json.JSONDecodeError:
        m = re.search(r'\{.*\}', raw_clean, re.DOTALL)
        try:
            result = json.loads(m.group(0)) if m else {}
        except:
            result = {}

    return {
        **skill,
        "ic_class": result.get("ic_class", "unknown"),
        "severity": result.get("severity", "unknown"),
        "omitted_capability": result.get("omitted_capability"),
        "audit_confidence": result.get("confidence", 0),
        "audit_reason": result.get("reason", ""),
    }


# ── Selection ────────────────────────────────────────────────────────────────

def select_skills(audited: list[dict], target: dict) -> list[dict]:
    """
    Select skills by IC class to meet target distribution.
    target = {"IC-1s": 60, "IC-1q": 30, "IC-0": 20}

    Prioritize:
    1. Higher confidence
    2. Major severity for IC-1s
    3. Higher downloads (more representative)
    """
    selected = []

    for ic_class, count in target.items():
        pool = [s for s in audited if s["ic_class"] == ic_class]

        # Sort: major-severity first (for IC-1s), then confidence, then downloads
        if ic_class == "IC-1s":
            pool.sort(key=lambda s: (
                s.get("severity") == "major",
                s.get("audit_confidence", 0),
                s.get("downloads", 0)
            ), reverse=True)
        else:
            pool.sort(key=lambda s: (
                s.get("audit_confidence", 0),
                s.get("downloads", 0)
            ), reverse=True)

        chosen = pool[:count]
        selected.extend(chosen)
        print(f"  {ic_class}: selected {len(chosen)}/{count} "
              f"(pool: {len(pool)})")

    return selected


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Phase 1: Select and audit skills for routing repair benchmark"
    )
    parser.add_argument("--max-candidates", type=int, default=500,
                       help="Max candidate skills to audit")
    parser.add_argument("--min-body", type=int, default=300,
                       help="Min SKILL.md body length")
    parser.add_argument("--target-ic1s", type=int, default=60)
    parser.add_argument("--target-ic1q", type=int, default=30)
    parser.add_argument("--target-ic0", type=int, default=20)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--resume", action="store_true",
                       help="Resume from existing audit cache")
    args = parser.parse_args()

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    print("=" * 70)
    print("ROUTING REPAIR BENCHMARK — Phase 1: Skill Selection & IC Audit")
    print("=" * 70)

    # 1. Find database
    db_path = find_db()
    print(f"[DB] {db_path}")

    # 2. Load candidates
    candidates = load_candidates(db_path, args.min_body, args.max_candidates)
    if len(candidates) < 50:
        print("[FALLBACK] Trying alternate loading method...")
        candidates = load_candidates_from_files(
            db_path, args.min_body, args.max_candidates)

    if not candidates:
        print("ERROR: No candidates found. Check database schema.")
        sys.exit(1)

    # Save candidates
    with open(RESULTS_DIR / "candidates.json", "w") as f:
        json.dump([{k: v for k, v in c.items() if k != "body"}
                   for c in candidates], f, indent=2, ensure_ascii=False)
    print(f"[✓] {len(candidates)} candidates saved")

    if args.dry_run:
        print("[DRY RUN] Would audit these candidates. Exiting.")
        return

    # 3. Audit all candidates
    print(f"\n[Phase 1.2] Auditing {len(candidates)} candidates...")
    audited = []
    ic_counts = {}

    for i, skill in enumerate(candidates):
        result = audit_skill(skill)
        audited.append(result)

        ic = result["ic_class"]
        ic_counts[ic] = ic_counts.get(ic, 0) + 1

        if (i + 1) % 25 == 0:
            print(f"  [{i+1}/{len(candidates)}] IC dist: {ic_counts}")

        # Save checkpoint every 50
        if (i + 1) % 50 == 0:
            with open(RESULTS_DIR / "audit_checkpoint.json", "w") as f:
                json.dump(audited, f, indent=2, ensure_ascii=False)

    # Save full audit
    with open(RESULTS_DIR / "audit_full.json", "w") as f:
        json.dump(audited, f, indent=2, ensure_ascii=False)
    print(f"\n[✓] Audit complete: {ic_counts}")

    # 4. Select target skills
    print(f"\n[Phase 1.3] Selecting target skills...")
    target = {
        "IC-1s": args.target_ic1s,
        "IC-1q": args.target_ic1q,
        "IC-0": args.target_ic0,
    }
    selected = select_skills(audited, target)

    # Save selected (with body for downstream use)
    with open(RESULTS_DIR / "selected_skills_110.json", "w") as f:
        json.dump(selected, f, indent=2, ensure_ascii=False)

    # Save summary (without body for readability)
    summary = [{k: v for k, v in s.items() if k != "body"}
               for s in selected]
    with open(RESULTS_DIR / "selected_summary.json", "w") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)

    print(f"\n{'='*70}")
    print(f"SELECTION COMPLETE")
    print(f"{'='*70}")
    print(f"  Total selected: {len(selected)}")
    sel_ic = {}
    for s in selected:
        sel_ic[s["ic_class"]] = sel_ic.get(s["ic_class"], 0) + 1
    print(f"  IC distribution: {sel_ic}")
    print(f"  Output: {RESULTS_DIR / 'selected_skills_110.json'}")
    print(f"{'='*70}")


if __name__ == "__main__":
    main()
