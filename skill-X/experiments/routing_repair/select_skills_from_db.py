#!/usr/bin/env python3
"""
select_skills_from_db.py — Phase 1: Skill Selection from DB Permission Data
=============================================================================
Uses the permission_secret_hints table to identify GROUND-TRUTH IC-1 skills:
skills whose description does NOT mention capabilities that the manifest
DOES declare (api_key, shell, file, browser, oauth, github, etc.).

This is more reliable than LLM-based IC audit because:
1. Permission hints come from the skill manifest, not LLM inference
2. Description matching is keyword-based (high precision)
3. No API calls needed for skill selection

Output: selected_skills.json with 110 skills (60 IC-1s + 30 IC-1q + 20 IC-0)
"""
import json
import random
import re
import sqlite3
import sys
from pathlib import Path

SEED = 42
random.seed(SEED)

BASE_DIR = Path(__file__).parent
RESULTS_DIR = BASE_DIR / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = Path("/Users/guangfu/codex/clawhubS/data/experiment1_split/merged.sqlite3")

# ── Permission-to-Description Keywords ───────────────────────────────────────
# If a skill declares a permission but the description does NOT contain
# any of these keywords, it's a description-capability mismatch (IC-1s).

PERM_KEYWORDS = {
    "requires_api_key": {
        "keywords": ["api key", "api_key", "apikey", "api token",
                     "requires.*key", "authentication.*key",
                     "token", "credentials"],
        "capability": "requires API key/token authentication",
        "security": True,
    },
    "requires_shell_access": {
        "keywords": ["shell", "command line", "terminal", "subprocess",
                     "execute.*command", "run.*command", "cli",
                     "bash", "system command"],
        "capability": "executes shell commands",
        "security": True,
    },
    "requires_local_file_access": {
        "keywords": ["file system", "filesystem", "local file",
                     "read.*file", "write.*file", "disk",
                     "directory", "folder"],
        "capability": "accesses local file system",
        "security": True,
    },
    "requires_browser_access": {
        "keywords": ["browser", "headless", "web scraping",
                     "chromium", "playwright", "puppeteer",
                     "selenium", "web automation"],
        "capability": "launches browser automation",
        "security": True,
    },
    "requires_oauth": {
        "keywords": ["oauth", "authorization", "login flow",
                     "user authentication", "sign.?in"],
        "capability": "initiates OAuth authorization flow",
        "security": True,
    },
    "requires_github": {
        "keywords": ["github", "git hub", "gh cli", "repository",
                     "pull request", "issue", "commit"],
        "capability": "accesses GitHub API",
        "security": False,  # IC-1q unless combined with shell
    },
    "requires_slack": {
        "keywords": ["slack", "workspace", "channel", "message"],
        "capability": "sends messages via Slack",
        "security": False,
    },
    "requires_notion": {
        "keywords": ["notion", "notion api", "notion database"],
        "capability": "accesses Notion workspaces",
        "security": False,
    },
    "requires_email": {
        "keywords": ["email", "e-mail", "smtp", "mail",
                     "inbox", "send.*message"],
        "capability": "sends or reads email",
        "security": True,
    },
}


def description_mentions_capability(description: str, perm_name: str) -> bool:
    """Check if description mentions the capability keywords."""
    desc_lower = description.lower()
    keywords = PERM_KEYWORDS[perm_name]["keywords"]
    for kw in keywords:
        if re.search(kw, desc_lower):
            return True
    return False


def classify_skill(skill: dict) -> dict:
    """
    Classify a skill based on permission hints vs description.
    Returns skill dict with ic_class, severity, and omitted_capabilities.
    """
    desc = skill["description"]
    omitted = []  # List of (perm_name, capability_text, is_security)

    for perm_name, perm_info in PERM_KEYWORDS.items():
        if skill.get(perm_name) and not description_mentions_capability(desc, perm_name):
            omitted.append({
                "permission": perm_name,
                "capability": perm_info["capability"],
                "security": perm_info["security"],
            })

    if not omitted:
        # No mismatches found
        return {
            **skill,
            "ic_class": "IC-0",
            "severity": "none",
            "omitted_capabilities": [],
            "n_omissions": 0,
        }

    # Has omissions — classify severity
    security_omissions = [o for o in omitted if o["security"]]
    functional_omissions = [o for o in omitted if not o["security"]]

    if security_omissions:
        ic_class = "IC-1s"
        severity = "major" if len(security_omissions) >= 2 else "minor"
        # Upgrade to major if shell + file/network
        has_shell = any(o["permission"] == "requires_shell_access"
                       for o in security_omissions)
        has_file = any(o["permission"] == "requires_local_file_access"
                      for o in security_omissions)
        has_api = any(o["permission"] == "requires_api_key"
                     for o in security_omissions)
        if has_shell and (has_file or has_api):
            severity = "major"
    else:
        ic_class = "IC-1q"
        severity = "minor"

    # Build omitted capability summary
    omitted_text = "; ".join(o["capability"] for o in omitted)

    return {
        **skill,
        "ic_class": ic_class,
        "severity": severity,
        "omitted_capabilities": omitted,
        "omitted_summary": omitted_text,
        "n_omissions": len(omitted),
    }


def load_and_classify_all() -> list[dict]:
    """Load all skills with permission hints and classify them."""
    db = sqlite3.connect(str(DB_PATH))

    rows = db.execute("""
        SELECT s.skill_id, s.name, s.description,
               COALESCE(s.downloads, 0) as downloads,
               COALESCE(s.category, '') as category,
               p.requires_api_key, p.requires_oauth, p.requires_token,
               p.requires_local_file_access, p.requires_shell_access,
               p.requires_browser_access, p.requires_github,
               p.requires_slack, p.requires_notion,
               p.requires_calendar, p.requires_email
        FROM skills s
        JOIN permission_secret_hints p ON s.skill_id = p.skill_id
        WHERE s.description IS NOT NULL AND LENGTH(s.description) > 20
        ORDER BY s.downloads DESC
    """).fetchall()
    db.close()

    print(f"[DB] {len(rows)} skills with permission hints")

    classified = []
    for r in rows:
        skill = {
            "skill_id": r[0],
            "name": r[1],
            "description": r[2],
            "downloads": r[3],
            "category": r[4],
            "requires_api_key": bool(r[5]),
            "requires_oauth": bool(r[6]),
            "requires_token": bool(r[7]),
            "requires_local_file_access": bool(r[8]),
            "requires_shell_access": bool(r[9]),
            "requires_browser_access": bool(r[10]),
            "requires_github": bool(r[11]),
            "requires_slack": bool(r[12]),
            "requires_notion": bool(r[13]),
            "requires_calendar": bool(r[14]),
            "requires_email": bool(r[15]),
        }
        classified.append(classify_skill(skill))

    return classified


def select_balanced(classified: list[dict],
                    n_ic1s: int = 60, n_ic1q: int = 30, n_ic0: int = 20,
                    ) -> list[dict]:
    """Select a balanced set of skills for the benchmark."""

    ic1s = [s for s in classified if s["ic_class"] == "IC-1s"]
    ic1q = [s for s in classified if s["ic_class"] == "IC-1q"]
    ic0  = [s for s in classified if s["ic_class"] == "IC-0"]

    print(f"\n[Pool] IC-1s: {len(ic1s)}, IC-1q: {len(ic1q)}, IC-0: {len(ic0)}")

    # Sort by downloads (higher = more representative) then n_omissions
    ic1s.sort(key=lambda s: (s.get("severity") == "major", s["downloads"]),
              reverse=True)
    ic1q.sort(key=lambda s: (s["downloads"],), reverse=True)
    ic0.sort(key=lambda s: (s["downloads"],), reverse=True)

    # Stratify IC-1s by omission type
    # Want diversity: shell, file, api_key, browser, oauth, email
    ic1s_by_perm = {}
    for s in ic1s:
        for o in s.get("omitted_capabilities", []):
            perm = o["permission"]
            if perm not in ic1s_by_perm:
                ic1s_by_perm[perm] = []
            ic1s_by_perm[perm].append(s)

    print(f"[IC-1s by permission type]:")
    for perm, skills in sorted(ic1s_by_perm.items()):
        print(f"  {perm}: {len(skills)}")

    # Round-robin selection from each permission type
    selected_ic1s = []
    seen_ids = set()
    perm_order = sorted(ic1s_by_perm.keys(),
                       key=lambda p: PERM_KEYWORDS.get(p, {}).get("security", False),
                       reverse=True)

    while len(selected_ic1s) < n_ic1s:
        added = False
        for perm in perm_order:
            for s in ic1s_by_perm[perm]:
                if s["skill_id"] not in seen_ids:
                    selected_ic1s.append(s)
                    seen_ids.add(s["skill_id"])
                    added = True
                    break
            if len(selected_ic1s) >= n_ic1s:
                break
        if not added:
            break

    # IC-1q: top by downloads
    selected_ic1q = [s for s in ic1q if s["skill_id"] not in seen_ids][:n_ic1q]
    for s in selected_ic1q:
        seen_ids.add(s["skill_id"])

    # IC-0: random sample from top downloads (to avoid bias)
    ic0_pool = [s for s in ic0 if s["skill_id"] not in seen_ids]
    # Take from top 200 by downloads, then random sample
    ic0_top = ic0_pool[:200]
    random.shuffle(ic0_top)
    selected_ic0 = ic0_top[:n_ic0]

    selected = selected_ic1s + selected_ic1q + selected_ic0
    return selected


def main():
    print("=" * 70)
    print("ROUTING REPAIR BENCHMARK — Phase 1: Skill Selection")
    print("=" * 70)

    if not DB_PATH.exists():
        print(f"ERROR: Database not found at {DB_PATH}")
        sys.exit(1)

    # 1. Load and classify
    print(f"\n[Phase 1.1] Loading and classifying skills...")
    classified = load_and_classify_all()

    # Distribution
    ic_dist = {}
    for s in classified:
        ic = s["ic_class"]
        ic_dist[ic] = ic_dist.get(ic, 0) + 1
    print(f"\n[Classification] IC distribution:")
    for ic, count in sorted(ic_dist.items()):
        print(f"  {ic}: {count} ({count/len(classified)*100:.1f}%)")

    # Severity distribution for IC-1s
    ic1s = [s for s in classified if s["ic_class"] == "IC-1s"]
    sev_dist = {}
    for s in ic1s:
        sev = s["severity"]
        sev_dist[sev] = sev_dist.get(sev, 0) + 1
    print(f"\n[IC-1s Severity]: {sev_dist}")

    # Save full classification
    # Strip permission fields for smaller output
    slim = []
    for s in classified:
        slim.append({
            "skill_id": s["skill_id"],
            "name": s["name"],
            "description": s["description"][:200],
            "downloads": s["downloads"],
            "ic_class": s["ic_class"],
            "severity": s["severity"],
            "n_omissions": s["n_omissions"],
            "omitted_summary": s.get("omitted_summary", ""),
        })
    with open(RESULTS_DIR / "classification_all.json", "w") as f:
        json.dump(slim, f, indent=2, ensure_ascii=False)

    # 2. Select balanced set
    print(f"\n[Phase 1.2] Selecting balanced benchmark set...")
    selected = select_balanced(classified)

    print(f"\n{'='*70}")
    print(f"SELECTION COMPLETE: {len(selected)} skills")
    print(f"{'='*70}")

    sel_dist = {}
    for s in selected:
        ic = s["ic_class"]
        sel_dist[ic] = sel_dist.get(ic, 0) + 1
    print(f"  IC distribution: {sel_dist}")

    # Omission type distribution
    omit_types = {}
    for s in selected:
        for o in s.get("omitted_capabilities", []):
            p = o["permission"]
            omit_types[p] = omit_types.get(p, 0) + 1
    print(f"  Omission types: {omit_types}")

    # Save selected (full details for downstream)
    output = []
    for s in selected:
        output.append({
            "skill_id": s["skill_id"],
            "name": s["name"],
            "description": s["description"],
            "downloads": s["downloads"],
            "category": s["category"],
            "ic_class": s["ic_class"],
            "severity": s["severity"],
            "omitted_capabilities": s.get("omitted_capabilities", []),
            "omitted_summary": s.get("omitted_summary", ""),
            "n_omissions": s["n_omissions"],
        })
    with open(RESULTS_DIR / "selected_skills.json", "w") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    # Save summary table
    print(f"\n[Top 10 IC-1s selected]:")
    for s in [x for x in selected if x["ic_class"] == "IC-1s"][:10]:
        print(f"  {s['name']}: {s['omitted_summary']}")
        print(f"    desc: {s['description'][:80]}...")

    print(f"\n  Output: {RESULTS_DIR / 'selected_skills.json'}")


if __name__ == "__main__":
    main()
