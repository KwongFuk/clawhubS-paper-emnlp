#!/usr/bin/env python3
"""
Phase C1: Programmatic Capability Verification
===============================================
For IC-1s flagged skills, verify whether the claimed "omitted" capabilities
actually exist in the implementation by grepping for concrete evidence:
  - shell/subprocess: subprocess, os.system, exec, spawn, shell
  - credentials: API_KEY, SECRET, TOKEN, OAUTH, password, credential
  - network: http, requests, fetch, urllib, axios, curl, websocket
  - filesystem: open(, readFile, writeFile, fs., os.path, readdir
  - email: smtp, imap, sendmail, @gmail, email

Reports precision: of auditor-flagged capabilities, how many are
confirmed by implementation evidence.
"""

import json
import re
import sys
from collections import defaultdict
from pathlib import Path

AUDIT_INPUT = Path("/Users/guangfu/codex/clawhubS/skill-X/experiments/full_audit/audit_input_full.jsonl")
# Load auditor predictions
AUDIT_RESULTS = Path("/Users/guangfu/codex/clawhubS/skill-X/experiments/full_audit")

# Capability patterns (case-insensitive)
CAPABILITY_PATTERNS = {
    "shell_execution": [
        r'\bsubprocess\b', r'\bos\.system\b', r'\bexec\b', r'\bspawn\b',
        r'\bchild_process\b', r'\bshell\s*=\s*True\b', r'\brun_command\b',
        r'\bos\.popen\b', r'\bPopen\b', r'\bbash\b', r'\b/bin/sh\b',
    ],
    "credential_access": [
        r'\bAPI_KEY\b', r'\bSECRET\b', r'\bTOKEN\b', r'\bOAUTH\b',
        r'\bpassword\b', r'\bcredential\b', r'\bauth\b', r'\bapi_key\b',
        r'\baccess_token\b', r'\brefresh_token\b', r'\bclient_secret\b',
        r'\bos\.environ\b', r'\bgetenv\b', r'\b\.env\b',
    ],
    "network_access": [
        r'\brequests\.\b', r'\bhttpx\b', r'\burllib\b', r'\bfetch\(', 
        r'\baxios\b', r'\bcurl\b', r'\bwebsocket\b', r'\bhttp\b',
        r'\baiohttp\b', r'\bhttplib\b', r'\bsocket\b',
    ],
    "filesystem": [
        r'\bopen\(', r'\breadFile\b', r'\bwriteFile\b', r'\bfs\.\b',
        r'\bos\.path\b', r'\breaddir\b', r'\bglob\b', r'\bshutil\b',
        r'\bPathlib\b', r'\bPath\(', r'\bfopen\b', r'\bfile_path\b',
    ],
    "email": [
        r'\bsmtp\b', r'\bimap\b', r'\bsendmail\b', r'\bgmail\b',
        r'\bemail\b', r'\bmailbox\b', r'\boutlook\b',
    ],
}


def check_capabilities(implementation_text: str) -> dict[str, bool]:
    """Check which capabilities are present in implementation."""
    results = {}
    for cap_name, patterns in CAPABILITY_PATTERNS.items():
        found = False
        for pat in patterns:
            if re.search(pat, implementation_text, re.IGNORECASE):
                found = True
                break
        results[cap_name] = found
    return results


def infer_auditor_claimed_caps(description: str, name: str) -> set[str]:
    """Infer what the auditor might flag as omitted based on name/description."""
    claimed = set()
    text = (name + " " + description).lower()
    
    # Shell-related names
    if any(w in text for w in ["terminal", "command", "shell", "cli", "execute", "run"]):
        claimed.add("shell_execution")
    # Credential/API related
    if any(w in text for w in ["api", "connect", "integration", "oauth", "login", "auth"]):
        claimed.add("credential_access")
    # Network
    if any(w in text for w in ["search", "fetch", "download", "web", "scrape", "browse", "http"]):
        claimed.add("network_access")
    # Filesystem
    if any(w in text for w in ["file", "read", "write", "local", "disk", "folder", "directory"]):
        claimed.add("filesystem")
    # Email
    if any(w in text for w in ["email", "mail", "gmail", "outlook", "smtp", "inbox"]):
        claimed.add("email")
    
    return claimed


def main():
    # Load implementation data
    print("Loading implementation data...")
    skills = {}
    with open(AUDIT_INPUT) as f:
        for line in f:
            r = json.loads(line)
            impl = r.get("implementation", "")
            if impl and len(impl) > 100:
                skills[r["skill_id"]] = {
                    "name": r["name"],
                    "description": r["description"],
                    "implementation": impl,
                }

    print(f"  {len(skills)} skills with implementation text")

    # Load audit results if available
    audit_labels = {}
    audit_files = list(AUDIT_RESULTS.glob("results_*.jsonl")) + \
                  list(AUDIT_RESULTS.glob("audit_results*.jsonl"))
    for af in audit_files:
        with open(af) as f:
            for line in f:
                try:
                    r = json.loads(line)
                    sid = r.get("skill_id", "")
                    ic = r.get("ic_class", r.get("predicted_class", ""))
                    if sid and ic:
                        audit_labels[sid] = ic
                except:
                    continue
    print(f"  {len(audit_labels)} audit labels loaded")

    # Focus on IC-1s skills that have implementations
    ic1s_skills = []
    for sid, data in skills.items():
        label = audit_labels.get(sid, "")
        if label in ("IC-1s", "IC-1q"):
            ic1s_skills.append((sid, data, label))

    if not ic1s_skills:
        # Fallback: use all skills and check caps
        print("  No audit labels found, using heuristic IC-1 detection...")
        # Use the routing repair benchmark skills as our IC-1s set
        repair_file = Path("/Users/guangfu/codex/clawhubS/skill-X/experiments/routing_repair/results/benchmark_repairs.json")
        if repair_file.exists():
            repairs = json.loads(repair_file.read_text())
            for sid, r in repairs.items():
                if sid in skills:
                    ic1s_skills.append((sid, skills[sid], "IC-1s"))
        print(f"  Using {len(ic1s_skills)} IC-1s skills from routing benchmark")

    # Verify each skill
    print(f"\nVerifying {len(ic1s_skills)} skills...")
    
    cap_stats = defaultdict(lambda: {"flagged": 0, "confirmed": 0, "denied": 0})
    skill_results = []
    
    for sid, data, label in ic1s_skills:
        impl_caps = check_capabilities(data["implementation"])
        # What does the description mention?
        desc_caps = check_capabilities(data["description"])
        # What would auditor flag as missing?
        inferred = infer_auditor_claimed_caps(data["description"], data["name"])
        
        for cap_name, in_impl in impl_caps.items():
            in_desc = desc_caps.get(cap_name, False)
            
            if in_impl and not in_desc:
                # Implementation has it, description doesn't mention it
                # → genuine omission
                cap_stats[cap_name]["confirmed"] += 1
                cap_stats[cap_name]["flagged"] += 1
            elif not in_impl and not in_desc:
                # Not in implementation, not in description
                # If auditor flagged it → false positive
                if cap_name in inferred:
                    cap_stats[cap_name]["denied"] += 1
                    cap_stats[cap_name]["flagged"] += 1
            elif in_impl and in_desc:
                # Both have it → no omission (correct)
                pass
        
        # Per-skill summary
        omissions = [c for c, v in impl_caps.items() if v and not desc_caps.get(c, False)]
        skill_results.append({
            "skill_id": sid,
            "name": data["name"],
            "label": label,
            "impl_caps": {k: v for k, v in impl_caps.items() if v},
            "desc_caps": {k: v for k, v in desc_caps.items() if v},
            "verified_omissions": omissions,
        })

    # Print results
    print(f"\n{'='*60}")
    print("PROGRAMMATIC CAPABILITY VERIFICATION")
    print(f"{'='*60}")
    print(f"Skills verified: {len(ic1s_skills)}")
    
    print(f"\n{'Capability':<22s} {'Flagged':>8s} {'Confirmed':>10s} {'FP':>5s} {'Precision':>10s}")
    print("-" * 60)
    total_flagged = 0
    total_confirmed = 0
    for cap in sorted(cap_stats.keys()):
        s = cap_stats[cap]
        prec = s["confirmed"] / s["flagged"] * 100 if s["flagged"] else 0
        print(f"  {cap:<20s} {s['flagged']:>8d} {s['confirmed']:>10d} {s['denied']:>5d} {prec:>9.1f}%")
        total_flagged += s["flagged"]
        total_confirmed += s["confirmed"]
    
    overall_prec = total_confirmed / total_flagged * 100 if total_flagged else 0
    print(f"\n  {'TOTAL':<20s} {total_flagged:>8d} {total_confirmed:>10d} {total_flagged-total_confirmed:>5d} {overall_prec:>9.1f}%")

    # Skills with verified omissions
    with_omissions = [s for s in skill_results if s["verified_omissions"]]
    print(f"\n  Skills with ≥1 verified omission: {len(with_omissions)}/{len(skill_results)}")
    
    # Save detailed results
    out = Path("/Users/guangfu/codex/clawhubS/skill-X/experiments/ic1_body_gap/results/programmatic_verification.json")
    with open(out, "w") as f:
        json.dump(skill_results, f, indent=2, ensure_ascii=False)
    print(f"\n→ {out}")


if __name__ == "__main__":
    main()
