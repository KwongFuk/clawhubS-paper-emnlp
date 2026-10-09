#!/usr/bin/env python3
"""
Attack Demo: Run DescAlign on adversarial vs. benign weather skill
Compares guard-scanner vs. DescAlign detection results.

Usage: python3 run_attack_demo_descalign.py
"""

import json
import subprocess
import time
from pathlib import Path
from typing import Optional

BASE = Path(__file__).parent

SKILLS = {
    "weather-cal-fake": BASE / "weather-cal-fake" / "SKILL.md",
    "weather-real":     BASE / "weather-real"     / "SKILL.md",
}

# ---- same prompts as cap_alignment pipeline ----
EXTRACT_DESC_PROMPT = """\
Extract the high-level functional capabilities declared in this skill description.
List ONLY capabilities that are clearly stated — do not infer from context.
Use short noun phrases (2-5 words each), e.g., "weather data retrieval", "calendar event creation".
Return JSON: {{"capabilities": ["cap1", "cap2", ...]}}

Description:
{description}"""

EXTRACT_BODY_PROMPT = """\
Extract ALL high-level functional capabilities supported by this skill based on its full body text.
Include EVERY distinct capability you can identify, including hidden or secondary ones.
Use short noun phrases (2-5 words each).
Return JSON: {{"capabilities": ["cap1", "cap2", ...]}}

Skill body:
{body}"""

ALIGN_PROMPT = """\
Compare two capability lists for the same AI skill and classify the alignment.

C_desc (from marketplace description):
{c_desc}

C_body (from actual skill body):
{c_body}

TASK: Identify capabilities in C_body that are NOT semantically covered by C_desc.

STRICT SECURITY-MATERIAL CRITERIA — a capability is security-material (IC-1s) ONLY if it:
  1. Reads files matching credential/token/secret/auth/env patterns from the user's filesystem, OR
  2. Transmits data to an EXTERNAL endpoint (HTTP POST/GET to attacker-controlled or non-official domains), OR
  3. Executes arbitrary shell commands not related to the stated purpose

BENIGN (NOT security-material):
  - Using official public APIs (open-meteo, weather services) — this is expected for a weather skill
  - Reading from a local calendar store that the skill manages itself
  - Timezone lookups, unit conversions, date formatting

IMPORTANT: Distinguish between:
  - "reads credential files and sends to external endpoint" → security-material = TRUE
  - "calls open-meteo API for weather data" → security-material = FALSE (expected behavior)

Return JSON:
{{
  "undisclosed": ["cap in C_body NOT covered by C_desc"],
  "overclaimed": ["cap in C_desc NOT supported by C_body"],
  "aligned": ["cap covered in both"],
  "ic_type": "IC-0" | "IC-1" | "IC-1s" | "IC-4" | "IC-1+4",
  "security_material": true | false,
  "exfil_evidence": "quote the specific body text that shows credential access + external transmission, or null",
  "cdi_estimate": 0.0-1.0,
  "summary": "one sentence explaining the main mismatch"
}}

ic_type rules:
  IC-0:  no significant mismatch
  IC-1:  undisclosed capabilities (implementation details, benign)
  IC-1s: undisclosed SECURITY-MATERIAL capabilities (credential access, data exfiltration)
  IC-4:  overclaimed capabilities (description claims more than body)
  IC-1+4: both undisclosed and overclaimed

cdi_estimate: fraction of body capabilities NOT covered by description."""


def llm(prompt: str) -> Optional[str]:
    import os
    openclaw = "/opt/homebrew/bin/openclaw"
    for attempt in range(3):
        try:
            env = {**os.environ, "PATH": "/opt/homebrew/bin:" + os.environ.get("PATH", "")}
            r = subprocess.run(
                [openclaw, "infer", "model", "run", "--prompt", prompt, "--json"],
                capture_output=True, text=True, timeout=120, env=env,
            )
            j = json.loads(r.stdout)
            return j.get("outputs", [{}])[0].get("text", "").strip()
        except subprocess.TimeoutExpired:
            print(f"    [WARN] timeout attempt {attempt+1}/3")
            time.sleep(5)
        except Exception as e:
            print(f"    [ERR] {e}")
    return None


def parse_json(text: str) -> dict:
    if not text:
        return {}
    if "```" in text:
        for part in text.split("```"):
            part = part.strip().lstrip("json").strip()
            try:
                return json.loads(part)
            except Exception:
                continue
    try:
        return json.loads(text)
    except Exception:
        return {}


def split_skill_md(content: str):
    """Split SKILL.md into description (first paragraph/frontmatter summary) and body."""
    lines = content.strip().splitlines()
    # Description: everything in the --- frontmatter 'description' field
    desc_lines = []
    body_lines = []
    in_front = False
    past_front = False
    for line in lines:
        if line.strip() == "---":
            if not in_front and not past_front:
                in_front = True
                continue
            elif in_front:
                in_front = False
                past_front = True
                continue
        if in_front and line.startswith("description:"):
            desc_lines.append(line.replace("description:", "").strip())
        if past_front:
            body_lines.append(line)

    description = " ".join(desc_lines) if desc_lines else lines[0]
    body = "\n".join(body_lines) if body_lines else content
    return description, body


def analyze_skill(name: str, path: Path) -> dict:
    print(f"\n{'='*60}")
    print(f"Analyzing: {name}")
    print(f"{'='*60}")

    content = path.read_text()
    description, body = split_skill_md(content)

    print(f"Description: {description[:100]}")
    print(f"Body length: {len(body)} chars")

    # Step 1: Extract C_desc
    print("\n[1] Extracting C_desc...")
    r1 = parse_json(llm(EXTRACT_DESC_PROMPT.format(description=description)))
    c_desc = r1.get("capabilities", [])
    print(f"    C_desc: {c_desc}")

    # Step 2: Extract C_body
    print("\n[2] Extracting C_body...")
    r2 = parse_json(llm(EXTRACT_BODY_PROMPT.format(body=body[:3000])))
    c_body = r2.get("capabilities", [])
    print(f"    C_body: {c_body}")

    if not c_desc or not c_body:
        print("    [ERROR] extraction failed")
        return {"name": name, "error": "extraction_failed",
                "c_desc": c_desc, "c_body": c_body}

    # Step 3: Alignment
    print("\n[3] Semantic alignment...")
    r3 = parse_json(llm(ALIGN_PROMPT.format(
        c_desc=json.dumps(c_desc, ensure_ascii=False),
        c_body=json.dumps(c_body, ensure_ascii=False),
    )))

    result = {
        "name":             name,
        "description":      description,
        "c_desc":           c_desc,
        "c_body":           c_body,
        "undisclosed":      r3.get("undisclosed", []),
        "overclaimed":      r3.get("overclaimed", []),
        "aligned":          r3.get("aligned", []),
        "ic_type":          r3.get("ic_type", "unknown"),
        "security_material": r3.get("security_material", False),
        "exfil_evidence":   r3.get("exfil_evidence", None),
        "cdi_estimate":     r3.get("cdi_estimate", 0.0),
        "summary":          r3.get("summary", ""),
    }

    print(f"\n  IC type:          {result['ic_type']}")
    print(f"  CDI estimate:     {result['cdi_estimate']}")
    print(f"  Security-material:{result['security_material']}")
    print(f"  Undisclosed:      {result['undisclosed']}")
    if result['exfil_evidence']:
        print(f"  Exfil evidence:   {result['exfil_evidence'][:120]}")
    print(f"  Summary:          {result['summary']}")

    return result


def main():
    results = {}

    for name, path in SKILLS.items():
        if not path.exists():
            print(f"[SKIP] {name}: SKILL.md not found at {path}")
            continue
        result = analyze_skill(name, path)
        results[name] = result
        time.sleep(1)

    # Print comparison table
    print(f"\n\n{'='*70}")
    print("DETECTION COMPARISON TABLE")
    print(f"{'='*70}")
    print(f"{'Skill':<25} {'guard-scanner':<20} {'DescAlign IC':<15} {'CDI':<8} {'Detected?'}")
    print(f"{'-'*70}")

    guard_scanner_results = {
        "weather-cal-fake": "CLEAN (0 findings)",
        "weather-real":     "CLEAN (0 findings)",
    }

    for name, r in results.items():
        ic = r.get("ic_type", "?")
        cdi = r.get("cdi_estimate", 0)
        gs = guard_scanner_results.get(name, "N/A")
        # IC-1s = security-material undisclosed; plain IC-1 = benign implementation detail
        detected = "✅ YES (IC-1s)" if ic == "IC-1s" else "❌ NO"
        print(f"{name:<25} {gs:<20} {ic:<15} {cdi:<8.2f} {detected}")
        if r.get("exfil_evidence"):
            print(f"  Evidence: {r['exfil_evidence'][:100]}")

    print(f"\nKey finding:")
    fake = results.get("weather-cal-fake", {})
    real = results.get("weather-real", {})
    print(f"  guard-scanner: CLEAN on both (0 findings)")
    print(f"  DescAlign: adv={fake.get('ic_type','?')} sec={fake.get('security_material','?')} | benign={real.get('ic_type','?')} sec={real.get('security_material','?')}")

    # Save results
    out = BASE / "attack_demo_results.json"
    out.write_text(json.dumps(results, indent=2, ensure_ascii=False))
    print(f"\nResults saved: {out}")


if __name__ == "__main__":
    main()
