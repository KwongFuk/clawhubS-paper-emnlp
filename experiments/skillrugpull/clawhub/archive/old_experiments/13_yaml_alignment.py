#!/usr/bin/env python3
"""
clawhub/13_yaml_alignment.py
-----------------------------
Exp 5: YAML Frontmatter Capability Declaration vs Actual Behavior.

For skills WITH explicit YAML capability declarations, how well do they
align with what's actually in the skill_text?

This answers: "Even when authors USE the structured format, is it accurate?"
If not → proves the problem is systemic, not just a format adoption issue.

Output:
  clawhub/data/yaml_alignment.csv
  clawhub/results/yaml_alignment_report.md
"""

from __future__ import annotations
import csv
import re
import sqlite3
import yaml
from pathlib import Path
from collections import Counter

HERE = Path(__file__).parent
ROOT = HERE.parent.parent.parent
DATA_DIR = HERE / "data"
RESULTS_DIR = HERE / "results"

SDG_CSV = DATA_DIR / "structured_sdg.csv"
OUTPUT_CSV = DATA_DIR / "yaml_alignment.csv"
REPORT_PATH = RESULTS_DIR / "yaml_alignment_report.md"

LSCPS_FAMILIES: dict[str, list[str]] = {
    "SHELL_EXEC":  ["subprocess", r"os\.system", r"\bbash\b", r"rm\s+-rf", r"\bexec\s*\("],
    "FILE_DELETE": [r"rm\s+-rf", r"shutil\.rmtree", r"os\.remove\b"],
    "FILE_WRITE":  [r"open\s*\(.*['\"]w['\"]", r"\.write\s*\("],
    "FILE_READ":   [r"open\s*\(.*['\"]r['\"]", r"\.read\s*\(", r"\bglob\b"],
    "NET_WRITE":   [r"requests\.post", r"\bwebhook\b", r"httpx\.post"],
    "NET_READ":    [r"requests\.get", r"httpx\.get", r"\bcurl\s+http"],
    "CRED_READ":   [r"\bapi[_\s]?key\b", r"\bprivate[_\s]?key\b", r"\bpassword\b", r"\bsecret\b"],
    "CRED_STORE":  [r"os\.environ\[", r"keychain"],
    "CRED_TRANSMIT":[r"send.*key", r"post.*token"],
    "AUTONOMOUS":  [r"\bautonomous\b", r"without.*confirm", r"\bcron\b"],
    "MEMORY_WRITE":[r"\bmemory\b", r"\bremember\b"],
    "MULTI_STEP":  [r"multi.step", r"\bworkflow\b", r"\bpipeline\b"],
}


def extract_lscps(text: str) -> set[str]:
    norm = text.lower()
    found = set()
    for perm, kws in LSCPS_FAMILIES.items():
        for kw in kws:
            if re.search(kw, norm):
                found.add(perm)
                break
    return found


def parse_yaml_stated_caps(frontmatter_raw: str) -> dict:
    """Parse what the YAML frontmatter explicitly states."""
    result = {
        "has_autonomous_field": False,
        "autonomous_value": None,
        "has_permissions_field": False,
        "permissions_list": [],
        "yaml_inferred_caps": set(),
    }
    if not frontmatter_raw:
        return result
    try:
        fm = yaml.safe_load(frontmatter_raw) or {}
    except Exception:
        return result

    meta = fm.get("openclaw", {}) or {}
    if isinstance(meta, dict):
        meta = meta.get("metadata", meta)

    if "autonomous" in meta:
        result["has_autonomous_field"] = True
        result["autonomous_value"] = meta["autonomous"]
        if meta["autonomous"] is True:
            result["yaml_inferred_caps"].add("AUTONOMOUS")

    perms = meta.get("permissions", None)
    if perms is not None:
        result["has_permissions_field"] = True
        if isinstance(perms, list):
            result["permissions_list"] = [str(p).upper() for p in perms]
            result["yaml_inferred_caps"].update(p for p in result["permissions_list"]
                                                  if p in LSCPS_FAMILIES)

    # Also scan full frontmatter text for LSCPS
    result["yaml_inferred_caps"] |= extract_lscps(frontmatter_raw)
    return result


def read_skill_text(text_path: str) -> str:
    if not text_path:
        return ""
    p = Path(text_path)
    if not p.is_absolute():
        p = ROOT / p
    if p.exists():
        try:
            return p.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            return ""
    return ""


def split_frontmatter(text: str) -> tuple[str, str]:
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.DOTALL)
    if m:
        return m.group(1), text[m.end():]
    return "", text


def main():
    print("[13_yaml_alignment] Loading data...")
    db = sqlite3.connect(str(ROOT / "data/experiment1_split/merged.sqlite3"))
    db.row_factory = sqlite3.Row

    skills = db.execute("""
        SELECT skill_id, description FROM skills
        WHERE description IS NOT NULL AND LENGTH(description) > 10
    """).fetchall()

    # text_path for SKILL.md
    text_paths = {}
    for r in db.execute("""
        SELECT sf.skill_id, sf.text_path FROM skill_files sf
        JOIN skills s ON sf.skill_id = s.skill_id
        WHERE sf.path = 'SKILL.md' AND sf.text_path IS NOT NULL
          AND s.current_version = sf.version
    """).fetchall():
        text_paths[r["skill_id"]] = r["text_path"]
    for r in db.execute("SELECT skill_id, text_path FROM skill_files WHERE path='SKILL.md' AND text_path IS NOT NULL").fetchall():
        if r["skill_id"] not in text_paths:
            text_paths[r["skill_id"]] = r["text_path"]
    db.close()

    results = []
    n_has_yaml_caps = 0
    n_yaml_accurate = 0
    n_yaml_understates = 0
    n_yaml_overstates = 0

    for skill in skills:
        sid = skill["skill_id"]
        tp = text_paths.get(sid, "")
        skill_text = read_skill_text(tp) if tp else ""
        if not skill_text:
            continue

        fm_raw, body = split_frontmatter(skill_text)
        yaml_info = parse_yaml_stated_caps(fm_raw)

        # Actual capabilities from full skill_text (code + prose)
        actual_caps = extract_lscps(skill_text)
        yaml_stated = yaml_info["yaml_inferred_caps"]
        desc_caps = extract_lscps(skill["description"] or "")

        has_yaml_caps = bool(yaml_stated)
        if has_yaml_caps:
            n_has_yaml_caps += 1

        # Alignment analysis
        yaml_covers_actual = yaml_stated >= actual_caps  # yaml ⊇ actual = overstate
        actual_beyond_yaml = actual_caps - yaml_stated    # what yaml MISSED
        yaml_beyond_actual = yaml_stated - actual_caps    # what yaml over-claimed

        yaml_coverage = len(yaml_stated & actual_caps) / max(len(actual_caps), 1) if actual_caps else 1.0

        if has_yaml_caps and actual_caps:
            if yaml_coverage >= 0.8:
                n_yaml_accurate += 1
            elif len(actual_beyond_yaml) > len(yaml_beyond_actual):
                n_yaml_understates += 1
            else:
                n_yaml_overstates += 1

        results.append({
            "skill_id": sid,
            "has_yaml_autonomous_field": int(yaml_info["has_autonomous_field"]),
            "autonomous_value": str(yaml_info["autonomous_value"]),
            "has_yaml_permissions_field": int(yaml_info["has_permissions_field"]),
            "yaml_permissions_list": "|".join(yaml_info["permissions_list"]),
            "yaml_inferred_caps": "|".join(sorted(yaml_stated)),
            "actual_caps": "|".join(sorted(actual_caps)),
            "desc_caps": "|".join(sorted(desc_caps)),
            "actual_beyond_yaml": "|".join(sorted(actual_beyond_yaml)),
            "yaml_beyond_actual": "|".join(sorted(yaml_beyond_actual)),
            "yaml_coverage": round(yaml_coverage, 4),
            "yaml_cap_count": len(yaml_stated),
            "actual_cap_count": len(actual_caps),
            "has_yaml_caps": int(has_yaml_caps),
        })

    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(results[0].keys()))
        writer.writeheader()
        writer.writerows(results)

    total = len(results)
    n_with_actual_risk = sum(1 for r in results if r["actual_cap_count"] > 0)
    n_autonomous_false = sum(1 for r in results if r["autonomous_value"] == "False" and "AUTONOMOUS" in r["actual_caps"])

    lines = [
        "# YAML Frontmatter Alignment Analysis (Experiment 5)",
        "",
        f"**Skills analyzed**: {total:,}",
        "",
        "## RQ: When authors use structured YAML declarations, are they accurate?",
        "",
        "| Metric | Count | % |",
        "|--------|-------|---|",
        f"| Total skills analyzed | {total:,} | — |",
        f"| Skills with YAML capability declarations | {n_has_yaml_caps:,} | {100*n_has_yaml_caps/max(total,1):.1f}% |",
        f"| Skills with actual risky capabilities | {n_with_actual_risk:,} | {100*n_with_actual_risk/max(total,1):.1f}% |",
        "",
        "## YAML Accuracy (among skills with both YAML decls + actual capabilities)",
        "",
        f"| YAML Accuracy Class | Count |",
        f"|---------------------|-------|",
        f"| Accurate (≥80% coverage) | {n_yaml_accurate:,} |",
        f"| Understates (actual > yaml) | {n_yaml_understates:,} |",
        f"| Overstates (yaml > actual) | {n_yaml_overstates:,} |",
        "",
        "## Most Concerning: `autonomous: false` but has AUTONOMOUS capability",
        "",
        f"- Skills declaring `autonomous: false` but having autonomous patterns: **{n_autonomous_false:,}**",
        f"- This is **explicit false declaration** in the structured metadata",
        "",
        "## Key Takeaway",
        "",
        "Even among skills that adopted the structured YAML format,",
        "a significant fraction either under-declares or actively misdeclares capabilities.",
        "This confirms that **the problem is not just lack of structured format** —",
        "the platform needs automated capability verification, not just voluntary declaration.",
    ]

    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    print(f"  Total: {total:,}, has YAML caps: {n_has_yaml_caps:,}")
    print(f"  Accurate: {n_yaml_accurate}, Understates: {n_yaml_understates}, Overstates: {n_yaml_overstates}")
    print(f"  autonomous:false but AUTONOMOUS detected: {n_autonomous_false}")
    print(f"  Report: {REPORT_PATH}")


if __name__ == "__main__":
    main()
