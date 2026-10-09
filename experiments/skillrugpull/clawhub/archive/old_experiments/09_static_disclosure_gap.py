#!/usr/bin/env python3
"""
clawhub/09_static_disclosure_gap.py
------------------------------------
Static Disclosure Gap (SDG) Analysis for ClawHub 18K skills.

For each skill: compare description (what the skill claims) vs
latest SKILL.md content (what the skill actually does).

Measures:
  1. LSCPS capability extraction from both layers
  2. Coverage = |declared ∩ actual| / |actual|
  3. Gap = 1 - coverage
  4. Reassurance washing: count of "safe/simple/minimal" words
  5. SDG classification: Adequate / Partial / Inadequate / No-risk

Output:
  clawhub/data/static_disclosure_gap.csv
  clawhub/results/static_gap_report.md
"""

from __future__ import annotations
import csv
import re
import sqlite3
from pathlib import Path
from collections import Counter, defaultdict

HERE = Path(__file__).parent
ROOT = HERE.parent.parent.parent
DB_PATH = ROOT / "data/experiment1_split/merged.sqlite3"
DATA_DIR = HERE / "data"
RESULTS_DIR = HERE / "results"
RESULTS_DIR.mkdir(exist_ok=True)

OUTPUT_CSV = DATA_DIR / "static_disclosure_gap.csv"
REPORT_PATH = RESULTS_DIR / "static_gap_report.md"

# ── LSCPS Capability Keyword Banks ────────────────────────────────────────────
# Each family maps to: (permission_name, keywords)
LSCPS = {
    "SHELL_EXEC":    ["subprocess", "os.system", "bash", "shell", "terminal", "exec(", "eval(", "rm -rf", "command line", "cli", "run command"],
    "FILE_DELETE":   ["rm -rf", "shutil.rmtree", "os.remove", "os.unlink", "delete file"],
    "FILE_WRITE":    ["open.*w", "write(", "writelines", "shutil.copy", "os.makedirs"],
    "FILE_READ":     ["open.*r", "read(", "readlines", "glob", "os.listdir"],
    "NET_WRITE":     ["webhook", "requests.post", "requests.put", "urllib.request", "httpx.post", "curl.*post", "send.*email"],
    "NET_READ":      ["requests.get", "httpx.get", "urllib.get", "fetch(", "curl ", "wget"],
    "CRED_READ":     ["api_key", "api key", "apikey", "private key", "privatekey", "password", "secret", "access token", "bearer token", ".env"],
    "CRED_STORE":    ["os.environ", "keychain", "store.*credential", "save.*token", "write.*key"],
    "AUTONOMOUS":    ["autonomous", "automatically", "without asking", "background", "schedule", "cron", "persistent"],
    "MEMORY_WRITE":  ["memory", "remember", "store.*context", "recall", "long.term"],
    "MULTI_STEP":    ["step by step", "multi.step", "workflow", "pipeline", "chain", "sequence of"],
}

REASSURANCE_WORDS = [
    "safe", "safely", "secure", "securely", "simple", "minimal",
    "read-only", "read only", "no data", "privacy", "private",
    "does not store", "never sends", "local only", "offline",
    "harmless", "lightweight", "non-destructive",
]


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).lower()


def extract_lscps(text: str) -> set[str]:
    """Extract set of LSCPS permissions present in text."""
    norm = normalize(text)
    found = set()
    for perm, keywords in LSCPS.items():
        for kw in keywords:
            if re.search(re.escape(kw), norm):
                found.add(perm)
                break
    return found


def count_reassurance(text: str) -> int:
    norm = normalize(text)
    return sum(1 for w in REASSURANCE_WORDS if w in norm)


def read_skill_text(text_path: str) -> str:
    """Read SKILL.md content from local file."""
    if not text_path:
        return ""
    p = ROOT / text_path
    if p.exists():
        try:
            return p.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            return ""
    return ""


def classify_gap(coverage: float, has_risk: bool) -> str:
    if not has_risk:
        return "no_risk"
    if coverage >= 0.7:
        return "adequate"
    if coverage >= 0.3:
        return "partial"
    return "inadequate"


def main():
    print(f"[09_static_disclosure_gap] Connecting to DB: {DB_PATH}")
    db = sqlite3.connect(str(DB_PATH))
    db.row_factory = sqlite3.Row

    # Get all skills with description
    skills = db.execute("""
        SELECT skill_id, description, category, installs_all_time,
               installs_current, is_malware_blocked, scan_status,
               is_suspicious, stars, versions_count
        FROM skills
        WHERE description IS NOT NULL AND LENGTH(description) > 10
    """).fetchall()

    print(f"  Skills with description: {len(skills)}")

    # Get latest SKILL.md text_path for each skill
    # Use current_version to match the latest version
    skill_text_paths = {}
    rows = db.execute("""
        SELECT sf.skill_id, sf.version, sf.text_path
        FROM skill_files sf
        JOIN skills s ON sf.skill_id = s.skill_id
        WHERE sf.path = 'SKILL.md'
          AND sf.text_path IS NOT NULL
          AND s.current_version = sf.version
    """).fetchall()
    for r in rows:
        skill_text_paths[r["skill_id"]] = r["text_path"]

    # Fallback: if current_version not matched, take any SKILL.md
    all_skillmd = db.execute("""
        SELECT skill_id, text_path
        FROM skill_files
        WHERE path = 'SKILL.md' AND text_path IS NOT NULL
    """).fetchall()
    for r in all_skillmd:
        if r["skill_id"] not in skill_text_paths:
            skill_text_paths[r["skill_id"]] = r["text_path"]

    print(f"  Skills with SKILL.md text: {len(skill_text_paths)}")
    db.close()

    # ── Analyze each skill ────────────────────────────────────────────────────
    results = []
    gap_classes = Counter()

    for i, skill in enumerate(skills):
        sid = skill["skill_id"]
        description = skill["description"] or ""
        text_path = skill_text_paths.get(sid, "")
        skill_text = read_skill_text(text_path) if text_path else ""

        if not skill_text:
            continue  # Skip skills without SKILL.md locally

        # Extract capabilities
        actual_caps = extract_lscps(skill_text)
        declared_caps = extract_lscps(description)
        undisclosed_caps = actual_caps - declared_caps

        # Metrics
        has_risk = len(actual_caps) > 0
        coverage = len(declared_caps & actual_caps) / max(len(actual_caps), 1) if has_risk else 1.0
        gap_score = 1.0 - coverage
        reassurance_count = count_reassurance(description)
        # Reassurance washing: has reassurance words BUT has high gap
        is_reassurance_washing = reassurance_count > 0 and gap_score >= 0.5 and has_risk

        gap_class = classify_gap(coverage, has_risk)
        gap_classes[gap_class] += 1

        results.append({
            "skill_id": sid,
            "description_len": len(description),
            "skill_text_len": len(skill_text),
            "actual_caps": "|".join(sorted(actual_caps)),
            "declared_caps": "|".join(sorted(declared_caps)),
            "undisclosed_caps": "|".join(sorted(undisclosed_caps)),
            "actual_cap_count": len(actual_caps),
            "declared_cap_count": len(declared_caps),
            "undisclosed_cap_count": len(undisclosed_caps),
            "coverage": round(coverage, 4),
            "gap_score": round(gap_score, 4),
            "reassurance_count": reassurance_count,
            "is_reassurance_washing": int(is_reassurance_washing),
            "gap_class": gap_class,
            "installs_all_time": skill["installs_all_time"] or 0,
            "installs_current": skill["installs_current"] or 0,
            "stars": skill["stars"] or 0,
            "versions_count": skill["versions_count"] or 0,
            "category": skill["category"] or "unknown",
            "is_suspicious": skill["is_suspicious"] or 0,
            "scan_status": skill["scan_status"] or "unknown",
        })

        if (i + 1) % 2000 == 0:
            print(f"  Processed {i+1}/{len(skills)}...")

    # Write CSV
    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(results[0].keys()))
        writer.writeheader()
        writer.writerows(results)

    # ── Statistics ────────────────────────────────────────────────────────────
    total = len(results)
    adequate = gap_classes["adequate"]
    partial = gap_classes["partial"]
    inadequate = gap_classes["inadequate"]
    no_risk = gap_classes["no_risk"]
    washing = sum(1 for r in results if r["is_reassurance_washing"])

    # High-risk + high-install
    hi_gap_hi_install = [r for r in results
                          if r["gap_class"] == "inadequate" and r["installs_all_time"] > 50]

    # Family breakdown
    family_undisclosed = Counter()
    for r in results:
        for cap in r["undisclosed_caps"].split("|"):
            if cap:
                family_undisclosed[cap] += 1

    # ── Write Report ──────────────────────────────────────────────────────────
    lines = [
        "# ClawHub Static Disclosure Gap (SDG) Analysis",
        "",
        f"**Dataset**: ClawHub | **Skills analyzed**: {total:,}",
        "",
        "## RQ1-S: How prevalent is the static disclosure gap?",
        "",
        "| SDG Class | Count | % | Definition |",
        "|-----------|-------|---|------------|",
        f"| No-risk skills | {no_risk:,} | {100*no_risk/total:.1f}% | description and skill_text have no risky caps |",
        f"| Adequate disclosure | {adequate:,} | {100*adequate/total:.1f}% | ≥70% of actual caps mentioned in description |",
        f"| Partial disclosure | {partial:,} | {100*partial/total:.1f}% | 30-70% coverage |",
        f"| **Inadequate disclosure** | **{inadequate:,}** | **{100*inadequate/total:.1f}%** | <30% coverage — high gap |",
        "",
        f"- **Reassurance washing** (has 'safe/simple' words but high gap): **{washing:,}** ({100*washing/total:.1f}%)",
        f"- **High-gap + high-install** (inadequate + >50 installs): **{len(hi_gap_hi_install):,}**",
        "",
        "## RQ2-S: Which capability families are most undisclosed?",
        "",
        "| LSCPS Permission | Undisclosed Count | % of analyzed |",
        "|------------------|-------------------|----------------|",
    ]
    for perm, cnt in family_undisclosed.most_common():
        lines.append(f"| {perm} | {cnt:,} | {100*cnt/total:.1f}% |")

    lines += [
        "",
        "## RQ3-S: Do high-install skills have more disclosure gaps?",
        "",
    ]
    # Install bucket analysis
    buckets = [(500, "≥500"), (100, "100-499"), (10, "10-99"), (0, "1-9")]
    lines.append("| Install Range | Total | Inadequate | Rate |")
    lines.append("|---------------|-------|------------|------|")
    for threshold, label in buckets:
        bucket_skills = [r for r in results if r["installs_all_time"] >= threshold]
        bucket_bad = [r for r in bucket_skills if r["gap_class"] == "inadequate"]
        if bucket_skills:
            lines.append(f"| {label} | {len(bucket_skills):,} | {len(bucket_bad):,} | {100*len(bucket_bad)/len(bucket_skills):.1f}% |")

    lines += [
        "",
        "## Key Findings",
        "",
        f"- **{100*(inadequate+partial)/total:.1f}%** of ClawHub skills have at least partial disclosure gaps",
        f"- **{100*inadequate/total:.1f}%** have severely inadequate disclosure (coverage < 30%)",
        f"- **{100*washing/total:.1f}%** use reassurance language while having high capability gaps",
        f"  → These are the most concerning: actively mislead users about safety",
        "",
        "> **Definition**: A skill with Inadequate SDG has detectable risky capabilities",
        "> (shell execution, credential handling, network exfiltration, etc.) in its SKILL.md",
        "> but its description covers fewer than 30% of these capabilities.",
    ]

    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    print(f"\n  Total analyzed: {total:,}")
    print(f"  No-risk: {no_risk:,} ({100*no_risk/total:.1f}%)")
    print(f"  Adequate: {adequate:,} ({100*adequate/total:.1f}%)")
    print(f"  Partial: {partial:,} ({100*partial/total:.1f}%)")
    print(f"  Inadequate: {inadequate:,} ({100*inadequate/total:.1f}%)")
    print(f"  Reassurance washing: {washing:,} ({100*washing/total:.1f}%)")
    print(f"  Report: {REPORT_PATH}")


if __name__ == "__main__":
    main()
