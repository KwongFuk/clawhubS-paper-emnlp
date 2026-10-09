#!/usr/bin/env python3
"""
clawhub/19_t6_case_studies.py
------------------------------
Deep Attack Reconstruction for Top 5 T6 Malicious Cases.

For each case:
  1. Attack path reconstruction (code-level: which lines do what)
  2. Data flow analysis (credential read → network exfil)
  3. User impact assessment (installs × exposure time)
  4. Detection layer coverage (L1/L2/L3 analysis)

Output:
  results/t6_case_studies.md   — paper-ready deep case studies

Usage:
  python3 19_t6_case_studies.py
"""

import json
import csv
import re
from pathlib import Path
from collections import OrderedDict

HERE = Path(__file__).parent
DATA_DIR = HERE / "data"
RESULTS_DIR = HERE / "results"

T6_CSV = DATA_DIR / "t6_cases.csv"
JSONL_PATH = DATA_DIR / "version_transitions_enriched.jsonl"
SEMANTIC_JSON = RESULTS_DIR / "ensemble_merged.json"
WEIGHTS_JSON = RESULTS_DIR / "risk_weights.json"
REPORT_PATH = RESULTS_DIR / "t6_case_studies.md"

# Top 5 cases selected for diversity of attack patterns
SELECTED_CASES = [
    {
        "skill_id": "koatora20/guard-scanner",
        "reason": "Longest exposure (9.1 days), 25+ detection patterns, ironic 'security scanner' disguise",
        "attack_type": "Trojanized security tool",
    },
    {
        "skill_id": "ThomasLWang/moltguard",
        "reason": "Hidden phishing file, 54 installs, rm -rf + credential access",
        "attack_type": "Phishing attack vector",
    },
    {
        "skill_id": "satbot-mdk/mdk-agent-wallet",
        "reason": "Crypto wallet + rm -rf + webhook exfiltration",
        "attack_type": "Financial data exfiltration",
    },
    {
        "skill_id": "adboio/agentmail",
        "reason": "Highest installs (155), webhook prompt injection",
        "attack_type": "Prompt injection via email",
    },
    {
        "skill_id": "chuddyrudd/clawsentinel",
        "reason": "Repeated inject/revert pattern (2x T6), persistence strategy",
        "attack_type": "Repeated injection campaign",
    },
]


def normalize(t):
    return re.sub(r"\s+", " ", (t or "")).lower().strip()


# Attack pattern definitions for deep analysis
ATTACK_PATTERNS = OrderedDict([
    ("credential_harvest", {
        "patterns": [r'\bapi[_\s]?key\b', r'\bpassword\b', r'\bprivate[_\s]?key\b',
                     r'\bsecret\b', r'\btoken\b', r'\bcredential\b',
                     r'\bbearer\b', r'\.env\b', r'\boauth\b'],
        "risk_level": "CRITICAL",
        "description": "Credential harvesting / access to secrets",
    }),
    ("network_exfiltration", {
        "patterns": [r'\brequests\.post\b', r'\bwebhook\b', r'\bcurl\b.*-d',
                     r'\burllib\.request\b', r'\bfetch\s*\('],
        "risk_level": "CRITICAL",
        "description": "Outbound data exfiltration",
    }),
    ("shell_execution", {
        "patterns": [r'\bsubprocess\b', r'\bos\.system\b', r'\bexec\s*\(',
                     r'\beval\s*\(', r'\bshell\s*=\s*True\b',
                     r'\brm\s+-rf\b', r'\bchmod\b'],
        "risk_level": "HIGH",
        "description": "Arbitrary command execution",
    }),
    ("file_destruction", {
        "patterns": [r'\brm\s+-rf\b', r'\bshutil\.rmtree\b', r'\bos\.remove\b',
                     r'\bdelete\b'],
        "risk_level": "HIGH",
        "description": "File system destruction",
    }),
    ("persistence_mechanism", {
        "patterns": [r'\bcrontab\b', r'\bcron\b', r'\bstartup\b',
                     r'\blaunchagent\b', r'\bsystemd\b', r'\bautostart\b'],
        "risk_level": "HIGH",
        "description": "Persistence / auto-execution",
    }),
    ("obfuscation", {
        "patterns": [r'\bbase64\b.*\bdecode\b', r'\bfrom.*code.*import\b',
                     r'\bstring\.fromcharcode\b', r'\bchar\s*\(\b',
                     r'\bdynamic\s*import\b'],
        "risk_level": "CRITICAL",
        "description": "Code obfuscation / anti-analysis",
    }),
    ("download_execute", {
        "patterns": [r'\bwget\b', r'\bcurl\b.*-[oO]\b',
                     r'\bdownload.*exec\b', r'\bpip\s+install\b'],
        "risk_level": "HIGH",
        "description": "Download-and-execute chain",
    }),
])


def analyze_attack_surface(text):
    """Deep analysis of attack patterns in skill text."""
    norm = normalize(text)
    findings = []

    for attack_name, spec in ATTACK_PATTERNS.items():
        matches = []
        for pat in spec["patterns"]:
            for m in re.finditer(pat, norm):
                # Get context (50 chars before and after)
                start = max(0, m.start() - 50)
                end = min(len(norm), m.end() + 50)
                context = norm[start:end].strip()
                matches.append({
                    "match": m.group(),
                    "context": f"...{context}...",
                    "position": m.start(),
                })

        if matches:
            findings.append({
                "category": attack_name,
                "risk_level": spec["risk_level"],
                "description": spec["description"],
                "match_count": len(matches),
                "examples": matches[:3],  # Top 3 examples
            })

    return findings


def reconstruct_data_flow(findings):
    """Determine the data flow path from findings."""
    has_cred = any(f["category"] == "credential_harvest" for f in findings)
    has_exfil = any(f["category"] == "network_exfiltration" for f in findings)
    has_shell = any(f["category"] == "shell_execution" for f in findings)
    has_destruct = any(f["category"] == "file_destruction" for f in findings)
    has_persist = any(f["category"] == "persistence_mechanism" for f in findings)
    has_download = any(f["category"] == "download_execute" for f in findings)

    flows = []
    if has_cred and has_exfil:
        flows.append("**Credential Exfiltration**: Read credentials → Send to external endpoint")
    if has_cred and has_shell:
        flows.append("**Credential Abuse**: Read credentials → Execute commands with elevated access")
    if has_shell and has_destruct:
        flows.append("**Destructive Execution**: Execute shell → Delete files/data")
    if has_download and has_shell:
        flows.append("**Remote Code Execution**: Download payload → Execute locally")
    if has_persist and has_shell:
        flows.append("**Persistent Backdoor**: Install auto-execution → Maintain shell access")
    if has_cred and not has_exfil and not has_shell:
        flows.append("**Credential Exposure**: Credentials accessible but no immediate exfil path detected")

    if not flows:
        flows.append("**Unknown**: Attack pattern detected but data flow unclear")

    return flows


def check_detection_coverage(findings, description):
    """Check which detection layers would catch this attack."""
    norm_desc = normalize(description)

    # L1: Does static regex catch it?
    l1_catches = len(findings) > 0
    l1_high_risk = any(f["risk_level"] == "CRITICAL" for f in findings)

    # L2: Is any risk capability disclosed in description?
    l2_undisclosed_count = 0
    l2_disclosed_count = 0
    disclosure_terms = {
        "credential_harvest": ["credential", "api key", "token", "password", "auth"],
        "network_exfiltration": ["send", "webhook", "post", "upload", "transmit"],
        "shell_execution": ["execute", "shell", "command", "bash", "terminal"],
        "file_destruction": ["delete", "remove", "clean"],
        "persistence_mechanism": ["schedule", "cron", "automatic", "persistent"],
    }

    for f in findings:
        terms = disclosure_terms.get(f["category"], [])
        if any(t in norm_desc for t in terms):
            l2_disclosed_count += 1
        else:
            l2_undisclosed_count += 1

    l2_catches = l2_undisclosed_count > 0  # Semantic layer flags undisclosed

    # L3: Would temporal detection catch it?
    # T6 are fix transitions (prev=malicious), so L3 catches the FORWARD drift
    l3_catches = l1_high_risk  # If L1 sees high risk in prev version

    return {
        "L1_static": l1_catches,
        "L1_high_risk": l1_high_risk,
        "L2_semantic": l2_catches,
        "L2_undisclosed": l2_undisclosed_count,
        "L2_disclosed": l2_disclosed_count,
        "L3_temporal": l3_catches,
        "platform_scanner": False,  # 0/40 caught by platform
    }


def main():
    print("[19_t6_case_studies] Deep attack reconstruction for T6 cases")

    # Load T6 data
    t6_data = {}
    if T6_CSV.exists():
        with open(T6_CSV, newline='', encoding='utf-8') as f:
            for r in csv.DictReader(f):
                sid = r['skill_id']
                if sid not in t6_data:
                    t6_data[sid] = r

    # Load full text
    pair_texts = {}
    with open(JSONL_PATH, encoding='utf-8') as f:
        for line in f:
            p = json.loads(line)
            sid = p.get('skill_id', '')
            pair_texts[sid] = {
                'prev_text': p.get('prev_skill_text', ''),
                'curr_text': p.get('curr_skill_text', ''),
                'pair_id': p['pair_id'],
                'changelog': p.get('changelog', ''),
                'description': p.get('description', ''),
            }

    # Load semantic results
    semantic_by_skill = {}
    if SEMANTIC_JSON.exists():
        with open(SEMANTIC_JSON) as f:
            for entry in json.load(f):
                semantic_by_skill[entry['skill_id']] = entry

    lines = [
        "# T6 Ground Truth: Deep Attack Case Studies",
        "",
        "> These 5 cases are selected from 40 confirmed malicious version transitions",
        "> for diversity of attack patterns, severity, and user impact.",
        "> Each case demonstrates a different attack vector that the platform scanner missed (0/40).",
        "",
    ]

    for i, case in enumerate(SELECTED_CASES, 1):
        sid = case["skill_id"]
        t6 = t6_data.get(sid, {})
        texts = pair_texts.get(sid, {})
        sem = semantic_by_skill.get(sid, {})

        prev_text = texts.get('prev_text', '')
        description = texts.get('description', t6.get('description', ''))
        changelog_excerpt = t6.get('changelog_excerpt', texts.get('changelog', ''))[:300]

        # Deep analysis
        findings = analyze_attack_surface(prev_text)
        data_flows = reconstruct_data_flow(findings)
        detection = check_detection_coverage(findings, description)

        # Impact metrics
        installs = int(t6.get('total_installs', 0) or 0)
        exposure = t6.get('exposure_days', 'unknown')
        try:
            exposure_float = float(exposure)
            impact_score = installs * exposure_float
            exposure_str = f"{exposure_float:.1f} days"
        except (ValueError, TypeError):
            impact_score = 0
            exposure_str = "unknown"

        lines += [
            f"## Case {i}: `{sid}`",
            "",
            f"**Attack Type**: {case['attack_type']}",
            f"**Why Selected**: {case['reason']}",
            "",
            "### Version Transition",
            "",
            f"| Metric | Value |",
            f"|--------|-------|",
            f"| Previous (malicious) version | `{t6.get('prev_version', '?')}` |",
            f"| Fix version | `{t6.get('curr_version', '?')}` |",
            f"| Exposure window | {exposure_str} |",
            f"| Total installs | {installs} |",
            f"| Platform scanner | ❌ **Missed** |",
            f"| Scan status | `{t6.get('scan_status', 'unknown')}` |",
            "",
            "### Changelog (Fix Version)",
            "",
            f"> {changelog_excerpt}",
            "",
        ]

        # Attack surface analysis
        if findings:
            lines += [
                "### Attack Surface Analysis",
                "",
                "| Category | Risk Level | Matches | Description |",
                "|----------|------------|---------|-------------|",
            ]
            for f in findings:
                lines.append(
                    f"| `{f['category']}` | {f['risk_level']} | "
                    f"{f['match_count']} | {f['description']} |"
                )

            # Show specific code evidence
            lines += ["", "### Code Evidence (Malicious Version)", ""]
            for f in findings[:4]:
                for ex in f["examples"][:2]:
                    lines.append(
                        f"- **{f['category']}**: `{ex['match']}` "
                        f"→ `{ex['context'][:80]}`"
                    )
            lines.append("")

        # Data flow
        if data_flows:
            lines += ["### Reconstructed Data Flow", ""]
            for flow in data_flows:
                lines.append(f"- {flow}")
            lines.append("")

        # Detection coverage
        lines += [
            "### Detection Layer Coverage",
            "",
            "| Layer | Catches? | Details |",
            "|-------|----------|---------|",
            f"| Platform Scanner | ❌ | 0% detection rate across all 40 T6 cases |",
            f"| L1: Static Regex | {'✅' if detection['L1_static'] else '❌'} | "
            f"{'High-risk patterns detected' if detection['L1_high_risk'] else 'Low-risk only'} |",
            f"| L2: Semantic (CR) | {'✅' if detection['L2_semantic'] else '❌'} | "
            f"{detection['L2_undisclosed']} undisclosed / {detection['L2_disclosed']} disclosed caps |",
            f"| L3: Temporal | {'✅' if detection['L3_temporal'] else '❌'} | "
            f"{'Detects in forward-drift scan' if detection['L3_temporal'] else 'Fix transition (no forward drift)'} |",
        ]

        # Semantic mismatch
        cr = sem.get('capability_recall', 'N/A')
        rwm = sem.get('rwm_score', 'N/A')
        if cr != 'N/A':
            lines += [
                "",
                f"**Capability Recall (CR)**: {cr:.3f} | "
                f"**Risk-Weighted Mismatch (RWM)**: {rwm:.2f}",
            ]

        lines += [
            "",
            "### User Impact Assessment",
            "",
            f"- **Directly exposed users**: {installs} (installed during malicious window)",
            f"- **Exposure duration**: {exposure_str}",
            f"- **Impact severity**: {'CRITICAL' if any(f['risk_level'] == 'CRITICAL' for f in findings) else 'HIGH'}",
            f"- **Attack sophistication**: {'High (compound attack)' if len(findings) >= 3 else 'Medium (single vector)'}",
            "",
            "---",
            "",
        ]

    # Summary table
    lines += [
        "## Summary: Detection Layer Effectiveness Across Cases",
        "",
        "| Case | Skill | Platform | L1 | L2 | L3 | Attack Type |",
        "|------|-------|----------|-----|-----|-----|-------------|",
    ]

    for i, case in enumerate(SELECTED_CASES, 1):
        sid = case["skill_id"]
        texts = pair_texts.get(sid, {})
        prev_text = texts.get('prev_text', '')
        desc = texts.get('description', '')
        findings = analyze_attack_surface(prev_text)
        det = check_detection_coverage(findings, desc)
        lines.append(
            f"| {i} | `{sid.split('/')[-1]}` | ❌ | "
            f"{'✅' if det['L1_static'] else '❌'} | "
            f"{'✅' if det['L2_semantic'] else '❌'} | "
            f"{'✅' if det['L3_temporal'] else '❌'} | "
            f"{case['attack_type']} |"
        )

    lines += [
        "",
        "## Implications for Defense",
        "",
        "1. **Platform scanners are insufficient**: 0/40 T6 cases caught",
        "2. **Static regex provides high recall**: catches most code-level danger signals",
        "3. **Semantic inference is essential for triage**: distinguishes malicious from legitimate use",
        "4. **Multi-layer defense is required**: no single layer catches all attack patterns",
        "5. **Repeated attackers exist**: some skills show inject-revert-inject patterns",
    ]

    with open(REPORT_PATH, "w") as f:
        f.write("\n".join(lines) + "\n")

    print(f"  Generated {len(SELECTED_CASES)} deep case studies")
    print(f"  Report: {REPORT_PATH}")


if __name__ == "__main__":
    main()
