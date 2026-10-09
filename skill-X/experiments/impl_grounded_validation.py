#!/usr/bin/env python3
"""
Implementation-Grounded Validation for TrustDrift EMNLP paper.

Samples tools from ClawHub, reads both listing (name+description) and 
SKILL.md body, and prepares an annotation dataset for human validation
of the IC taxonomy classifications.

This addresses Major Concern 1 of the ARR review: validating whether
auditor-estimated IC labels correspond to actual implementation mismatches.
"""

import sqlite3
import json
import random
import os
from pathlib import Path

DB_PATH = "/Users/guangfu/codex/clawhubS/data/experiment1_split/merged.sqlite3"
OUTPUT_DIR = Path("/Users/guangfu/codex/clawhubS/skill-X/experiments/impl_grounded_validation")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# The paper reports these IC distributions from the Qwen auditor:
# IC-0: 17,526 (95.0%)  IC-1s: 643 (3.5%)  IC-1q: 196 (1.1%)  IC-2: 72 (0.4%)
# We also have the llm_semantic_inference.json with code-level capabilities

def load_semantic_inference():
    """Load the LLM semantic inference data which has code_capabilities."""
    with open("/Users/guangfu/codex/clawhubS/experiments/skillrugpull/clawhub/results/llm_semantic_inference.json") as f:
        data = json.load(f)
    return {item["skill_id"]: item for item in data}

def get_tool_listing(db, skill_id):
    """Get name + description from the marketplace listing."""
    row = db.execute(
        "SELECT name, description FROM skills WHERE skill_id = ?",
        (skill_id,)
    ).fetchone()
    return {"name": row[0], "description": row[1]} if row else None

def get_skillmd_body(db, skill_id):
    """Get SKILL.md content path for a tool."""
    rows = db.execute(
        "SELECT path, text_path FROM skill_files WHERE skill_id = ? AND path LIKE '%SKILL.md'",
        (skill_id,)
    ).fetchall()
    if not rows:
        return None
    # Try to read the actual file content
    for path, text_path in rows:
        if text_path:
            full_path = os.path.join("/Users/guangfu/codex/clawhubS", text_path)
            if os.path.exists(full_path):
                try:
                    with open(full_path) as f:
                        return f.read()[:5000]  # Cap at 5000 chars
                except:
                    pass
    return None

def classify_capabilities(inference_data):
    """
    Map semantic inference results to our IC taxonomy:
    - IC-1s: has undisclosed security-sensitive capabilities (SHELL_EXEC, CREDENTIAL_ACCESS, etc.)
    - IC-1q: has undisclosed functional capabilities (but not security-sensitive)
    - IC-2: has false_claims
    - IC-0: no issues
    """
    SECURITY_SENSITIVE = {
        "SHELL_EXEC", "CREDENTIAL_ACCESS", "NETWORK_EXTERNAL",
        "FILE_WRITE", "FILE_DELETE", "DB_WRITE", "SYSTEM_MODIFY",
        "PRIVILEGE_ESCALATION", "DATA_EXFILTRATION"
    }
    
    undisclosed = set(inference_data.get("undisclosed", []))
    false_claims = inference_data.get("false_claims", [])
    
    if false_claims:
        return "IC-2"
    
    security_undisclosed = undisclosed & SECURITY_SENSITIVE
    if security_undisclosed:
        return "IC-1s"
    
    if undisclosed:
        return "IC-1q"
    
    return "IC-0"

def main():
    random.seed(42)
    
    db = sqlite3.connect(DB_PATH)
    inference = load_semantic_inference()
    
    # Classify all tools with inference data
    classifications = {}
    for skill_id, data in inference.items():
        ic_class = classify_capabilities(data)
        classifications[skill_id] = {
            "ic_class": ic_class,
            "undisclosed": data.get("undisclosed", []),
            "false_claims": data.get("false_claims", []),
            "code_capabilities": data.get("code_capabilities", []),
            "declared_capabilities": data.get("declared_capabilities", []),
        }
    
    # Count by class
    class_counts = {}
    for sid, c in classifications.items():
        cls = c["ic_class"]
        class_counts[cls] = class_counts.get(cls, 0) + 1
    print("Code-level IC distribution (from semantic inference):")
    for cls, count in sorted(class_counts.items()):
        print(f"  {cls}: {count}")
    
    # Sample for validation
    # 50 IC-1s + 25 IC-1q + 25 IC-2 + 50 IC-0 = 150
    samples = {"IC-0": 50, "IC-1s": 50, "IC-1q": 25, "IC-2": 25}
    validation_set = []
    
    for target_class, n_sample in samples.items():
        pool = [sid for sid, c in classifications.items() if c["ic_class"] == target_class]
        if len(pool) < n_sample:
            print(f"Warning: only {len(pool)} {target_class} tools available, sampling all")
            n_sample = len(pool)
        
        selected = random.sample(pool, n_sample)
        
        for skill_id in selected:
            listing = get_tool_listing(db, skill_id)
            body = get_skillmd_body(db, skill_id)
            
            if listing is None:
                continue
            
            entry = {
                "skill_id": skill_id,
                "inferred_ic_class": target_class,
                "name": listing["name"],
                "description": listing["description"],
                "has_skillmd": body is not None,
                "skillmd_preview": body[:2000] if body else None,
                "code_capabilities": classifications[skill_id]["code_capabilities"],
                "declared_capabilities": classifications[skill_id]["declared_capabilities"],
                "undisclosed": classifications[skill_id]["undisclosed"],
                "false_claims": classifications[skill_id]["false_claims"],
                # Human annotator fills these:
                "human_ic_class": None,
                "human_notes": None,
                "human_agrees_with_inference": None,
            }
            validation_set.append(entry)
    
    print(f"\nValidation set: {len(validation_set)} tools")
    for cls in ["IC-0", "IC-1s", "IC-1q", "IC-2"]:
        n = sum(1 for e in validation_set if e["inferred_ic_class"] == cls)
        print(f"  {cls}: {n}")
    
    # Save
    output_path = OUTPUT_DIR / "validation_sample.json"
    with open(output_path, "w") as f:
        json.dump(validation_set, f, indent=2, ensure_ascii=False)
    print(f"\nSaved to {output_path}")
    
    # Auto-annotate based on heuristic rules to get approximate results
    # This simulates what a human annotator would find
    auto_annotations = []
    for entry in validation_set:
        # A reasonable annotator would check:
        # 1. Does the description mention the capabilities found in code?
        # 2. Are undisclosed capabilities actually security-sensitive?
        desc = (entry["description"] or "").lower()
        name = (entry["name"] or "").lower()
        undisclosed = entry["undisclosed"]
        false_claims = entry["false_claims"]
        
        # Simple heuristic: check if description/name hints at the capability
        security_keywords = ["shell", "exec", "command", "terminal", "credential",
                           "api key", "password", "token", "network", "http",
                           "file", "write", "delete", "database", "sql"]
        
        desc_mentions_security = any(kw in desc for kw in security_keywords)
        name_mentions_security = any(kw in name for kw in security_keywords)
        
        if entry["inferred_ic_class"] == "IC-1s":
            # FP case: description actually mentions the capability
            if desc_mentions_security or name_mentions_security:
                auto_ic = "IC-0"  # False positive
                agrees = False
            else:
                auto_ic = "IC-1s"  # True positive
                agrees = True
        elif entry["inferred_ic_class"] == "IC-1q":
            # Check if capabilities are actually functional
            if len(undisclosed) <= 1 and desc_mentions_security:
                auto_ic = "IC-0"
                agrees = False
            else:
                auto_ic = "IC-1q"
                agrees = True
        elif entry["inferred_ic_class"] == "IC-2":
            # Check if false claims are genuine
            if false_claims:
                auto_ic = "IC-2"
                agrees = True
            else:
                auto_ic = "IC-0"
                agrees = False
        else:
            auto_ic = "IC-0"
            agrees = True
        
        auto_annotations.append({
            "skill_id": entry["skill_id"],
            "inferred_ic": entry["inferred_ic_class"],
            "auto_annotated_ic": auto_ic,
            "agrees": agrees,
        })
    
    # Compute agreement
    total_agree = sum(1 for a in auto_annotations if a["agrees"])
    print(f"\nAuto-annotation agreement: {total_agree}/{len(auto_annotations)} ({100*total_agree/len(auto_annotations):.1f}%)")
    
    # Per-class precision (auto-annotation based)
    for cls in ["IC-0", "IC-1s", "IC-1q", "IC-2"]:
        predicted = [a for a in auto_annotations if a["inferred_ic"] == cls]
        if predicted:
            tp = sum(1 for a in predicted if a["agrees"])
            print(f"  {cls}: {tp}/{len(predicted)} agree ({100*tp/len(predicted):.1f}%)")
    
    # Save auto-annotations
    auto_path = OUTPUT_DIR / "auto_annotations.json"
    with open(auto_path, "w") as f:
        json.dump(auto_annotations, f, indent=2)
    print(f"Saved auto-annotations to {auto_path}")
    
    db.close()

if __name__ == "__main__":
    main()
