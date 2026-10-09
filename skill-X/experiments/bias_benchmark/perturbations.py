#!/usr/bin/env python3
"""
perturbations.py — 8 metadata perturbation types for EXP-2
============================================================
Following BiasBusters §4.3 + our innovation (P8: IC-1 repair).

Each perturbation returns a modified copy of the skills list.
"""
import copy
import hashlib
import random
import re
import string
from typing import Dict, List, Tuple


def _random_name(length: int = 20) -> str:
    """Generate random alphanumeric name."""
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=length))


def _scramble_text(text: str) -> str:
    """Scramble text by shuffling words while preserving length."""
    words = text.split()
    random.shuffle(words)
    return " ".join(words)


# ──────────────────────────────────────────────────────────────────────────────
# P1: Full Name Scramble
# Replace ALL skill names with random 20-char strings
# ──────────────────────────────────────────────────────────────────────────────
def p1_full_name_scramble(skills: List[Dict]) -> Tuple[List[Dict], str]:
    """Replace all skill names with random strings."""
    result = copy.deepcopy(skills)
    for s in result:
        s["original_name"] = s["name"]
        s["name"] = _random_name()
    return result, "P1_full_name_scramble"


# ──────────────────────────────────────────────────────────────────────────────
# P2: Name Shuffle
# Shuffle skill names within the cluster (swap names between skills)
# ──────────────────────────────────────────────────────────────────────────────
def p2_name_shuffle(skills: List[Dict]) -> Tuple[List[Dict], str]:
    """Shuffle names across skills in the cluster."""
    result = copy.deepcopy(skills)
    names = [s["name"] for s in result]
    random.shuffle(names)
    for s, new_name in zip(result, names):
        s["original_name"] = s["name"]
        s["name"] = new_name
    return result, "P2_name_shuffle"


# ──────────────────────────────────────────────────────────────────────────────
# P3: Single-Tool Name Perturbation
# Only scramble the most-selected skill's name
# ──────────────────────────────────────────────────────────────────────────────
def p3_single_name_perturb(skills: List[Dict], 
                            most_selected_idx: int = 0) -> Tuple[List[Dict], str]:
    """Replace only the most-selected skill's name with random string."""
    result = copy.deepcopy(skills)
    result[most_selected_idx]["original_name"] = result[most_selected_idx]["name"]
    result[most_selected_idx]["name"] = _random_name()
    return result, "P3_single_name_perturb"


# ──────────────────────────────────────────────────────────────────────────────
# P4: Description + Parameter Scramble
# Randomize both descriptions and parameter descriptions
# ──────────────────────────────────────────────────────────────────────────────
def p4_desc_param_scramble(skills: List[Dict]) -> Tuple[List[Dict], str]:
    """Scramble descriptions and parameters for all skills."""
    result = copy.deepcopy(skills)
    for s in result:
        s["original_description"] = s["description"]
        s["description"] = _scramble_text(s["description"])
        if "parameters" in s:
            s["parameters"] = _scramble_text(str(s["parameters"]))
    return result, "P4_desc_param_scramble"


# ──────────────────────────────────────────────────────────────────────────────
# P5: Description-Only Scramble
# Randomize only descriptions (keep names and params intact)
# ──────────────────────────────────────────────────────────────────────────────
def p5_desc_only_scramble(skills: List[Dict]) -> Tuple[List[Dict], str]:
    """Scramble only descriptions for all skills."""
    result = copy.deepcopy(skills)
    for s in result:
        s["original_description"] = s["description"]
        s["description"] = _scramble_text(s["description"])
    return result, "P5_desc_only_scramble"


# ──────────────────────────────────────────────────────────────────────────────
# P6: Targeted Description Scramble
# Only scramble the most-selected skill's description
# ──────────────────────────────────────────────────────────────────────────────
def p6_targeted_desc_scramble(skills: List[Dict],
                               most_selected_idx: int = 0) -> Tuple[List[Dict], str]:
    """Scramble only the most-selected skill's description."""
    result = copy.deepcopy(skills)
    s = result[most_selected_idx]
    s["original_description"] = s["description"]
    s["description"] = _scramble_text(s["description"])
    return result, "P6_targeted_desc_scramble"


# ──────────────────────────────────────────────────────────────────────────────
# P7: Description Transfer (Most → Least)
# Swap descriptions between most-selected and least-selected skills
# ──────────────────────────────────────────────────────────────────────────────
def p7_desc_transfer(skills: List[Dict],
                      most_selected_idx: int = 0,
                      least_selected_idx: int = -1) -> Tuple[List[Dict], str]:
    """Swap descriptions between most and least selected skills."""
    result = copy.deepcopy(skills)
    
    # Store originals
    result[most_selected_idx]["original_description"] = result[most_selected_idx]["description"]
    result[least_selected_idx]["original_description"] = result[least_selected_idx]["description"]
    
    # Swap
    tmp = result[most_selected_idx]["description"]
    result[most_selected_idx]["description"] = result[least_selected_idx]["description"]
    result[least_selected_idx]["description"] = tmp
    
    return result, "P7_desc_transfer"


# ──────────────────────────────────────────────────────────────────────────────
# P8: IC-1 Description Repair (OUR INNOVATION)
# Replace original descriptions with SkillCalib-repaired versions
# ──────────────────────────────────────────────────────────────────────────────
def p8_ic1_desc_repair(skills: List[Dict],
                        repaired_descriptions: Dict[str, str]) -> Tuple[List[Dict], str]:
    """Replace descriptions with SkillCalib-repaired versions.
    
    Args:
        skills: Original skills
        repaired_descriptions: {skill_name: repaired_description}
    
    This is our key innovation beyond BiasBusters:
    - P1-P7 are destructive perturbations (scramble/swap)
    - P8 is a constructive repair (calibrate descriptions to match capabilities)
    """
    result = copy.deepcopy(skills)
    for s in result:
        if s["name"] in repaired_descriptions:
            s["original_description"] = s["description"]
            s["description"] = repaired_descriptions[s["name"]]
    return result, "P8_ic1_desc_repair"


# ──────────────────────────────────────────────────────────────────────────────
# P9: Performance Keyword Injection (ATTACK SURFACE)
# Inject speed/efficiency keywords into the least-selected skill
# ──────────────────────────────────────────────────────────────────────────────
def p9_perf_keyword_injection(skills: List[Dict],
                               target_idx: int = -1) -> Tuple[List[Dict], str]:
    """Inject performance keywords into target skill's description."""
    result = copy.deepcopy(skills)
    s = result[target_idx]
    s["original_description"] = s["description"]
    prefix = "Fast, efficient, and lightweight. Low-latency with optimized performance. "
    s["description"] = prefix + s["description"]
    return result, "P9_perf_keyword_injection"


# ──────────────────────────────────────────────────────────────────────────────
# P9-ctrl: Neutral Keyword Injection (CONTROL)
# Same length as P9 but with neutral filler words
# ──────────────────────────────────────────────────────────────────────────────
def p9_ctrl_neutral_injection(skills: List[Dict],
                               target_idx: int = -1) -> Tuple[List[Dict], str]:
    """Inject neutral keywords into target skill (control for P9)."""
    result = copy.deepcopy(skills)
    s = result[target_idx]
    s["original_description"] = s["description"]
    prefix = "Updated, maintained, and documented. Version-controlled with regular releases. "
    s["description"] = prefix + s["description"]
    return result, "P9ctrl_neutral_injection"


# ──────────────────────────────────────────────────────────────────────────────
# P10: Cost/Free Keyword Injection (ATTACK SURFACE)
# Inject cost-related keywords into the least-selected skill
# ──────────────────────────────────────────────────────────────────────────────
def p10_cost_keyword_injection(skills: List[Dict],
                                target_idx: int = -1) -> Tuple[List[Dict], str]:
    """Inject cost/free keywords into target skill's description."""
    result = copy.deepcopy(skills)
    s = result[target_idx]
    s["original_description"] = s["description"]
    prefix = "Free to use, no API key required. Unlimited requests with zero cost. "
    s["description"] = prefix + s["description"]
    return result, "P10_cost_keyword_injection"


# ──────────────────────────────────────────────────────────────────────────────
# P11: Brand Alignment Test
# Add vendor brand prefixes to skill names, keep descriptions identical
# ──────────────────────────────────────────────────────────────────────────────
VENDOR_BRANDS = ["OpenAI", "Google", "Anthropic", "Meta", "Open-Source"]

def p11_brand_alignment(skills: List[Dict],
                         domain_word: str = "") -> Tuple[List[Dict], str]:
    """Add vendor brand prefixes to skill names.
    
    Assigns brands in order: OpenAI, Google, Anthropic, Meta, Open-Source.
    All descriptions stay identical to isolate brand effect.
    """
    result = copy.deepcopy(skills)
    for i, s in enumerate(result):
        brand = VENDOR_BRANDS[i % len(VENDOR_BRANDS)]
        s["original_name"] = s["name"]
        s["name"] = f"{brand} {domain_word or s['name']}"
    return result, "P11_brand_alignment"


# ──────────────────────────────────────────────────────────────────────────────
# P12: Anti-Brand Test (reverse assignment)
# Assign "OpenAI" to worst-described, "Google" to 2nd worst, etc.
# ──────────────────────────────────────────────────────────────────────────────
def p12_anti_brand(skills: List[Dict],
                    selection_ranking: List[int] = None,
                    domain_word: str = "") -> Tuple[List[Dict], str]:
    """Assign brand prefixes in REVERSE selection order.
    
    The least-selected skill gets "OpenAI" prefix,
    the 2nd least gets "Google", etc.
    Tests whether brand > description quality.
    """
    result = copy.deepcopy(skills)
    if selection_ranking is None:
        # Default: reverse order (last = OpenAI)
        selection_ranking = list(range(len(skills) - 1, -1, -1))
    
    for rank, idx in enumerate(selection_ranking):
        brand = VENDOR_BRANDS[rank % len(VENDOR_BRANDS)]
        result[idx]["original_name"] = result[idx]["name"]
        result[idx]["name"] = f"{brand} {domain_word or result[idx]['name']}"
    return result, "P12_anti_brand"


# ──────────────────────────────────────────────────────────────────────────────
# P15: Social Proof Injection
# Inject popularity/social proof phrases
# ──────────────────────────────────────────────────────────────────────────────
def p15_social_proof(skills: List[Dict],
                      target_idx: int = -1) -> Tuple[List[Dict], str]:
    """Inject social proof into target skill's description."""
    result = copy.deepcopy(skills)
    s = result[target_idx]
    s["original_description"] = s["description"]
    prefix = "Used by 10M+ developers worldwide. #1 rated in its category. "
    s["description"] = prefix + s["description"]
    return result, "P15_social_proof"


# ──────────────────────────────────────────────────────────────────────────────
# P16: Authority/Endorsement Injection
# Inject official/authority signals
# ──────────────────────────────────────────────────────────────────────────────
def p16_authority_injection(skills: List[Dict],
                             target_idx: int = -1) -> Tuple[List[Dict], str]:
    """Inject authority/endorsement into target skill's description."""
    result = copy.deepcopy(skills)
    s = result[target_idx]
    s["original_description"] = s["description"]
    prefix = "Official integration. Recommended by leading AI research labs. Enterprise-grade. "
    s["description"] = prefix + s["description"]
    return result, "P16_authority_injection"


# ──────────────────────────────────────────────────────────────────────────────
# Batch apply all perturbations
# ──────────────────────────────────────────────────────────────────────────────
ALL_PERTURBATIONS = {
    "P1": p1_full_name_scramble,
    "P2": p2_name_shuffle,
    "P3": p3_single_name_perturb,
    "P4": p4_desc_param_scramble,
    "P5": p5_desc_only_scramble,
    "P6": p6_targeted_desc_scramble,
    "P7": p7_desc_transfer,
    # P8 requires repaired_descriptions, handled separately
    # P9-P16 are targeted perturbations, handled by expanded experiment runner
}


def apply_all_perturbations(
    skills: List[Dict],
    most_selected_idx: int = 0,
    least_selected_idx: int = -1,
    repaired_descriptions: Dict[str, str] = None,
    seed: int = 42,
) -> Dict[str, Tuple[List[Dict], str]]:
    """Apply all 8 perturbation types and return results.
    
    Args:
        skills: Original cluster skills
        most_selected_idx: Index of most-selected skill (from baseline)
        least_selected_idx: Index of least-selected skill
        repaired_descriptions: SkillCalib repaired descriptions for P8
        seed: Random seed for reproducibility
    
    Returns:
        {perturbation_id: (modified_skills, perturbation_name)}
    """
    results = {}
    
    random.seed(seed)
    results["P1"] = p1_full_name_scramble(skills)
    
    random.seed(seed)
    results["P2"] = p2_name_shuffle(skills)
    
    random.seed(seed)
    results["P3"] = p3_single_name_perturb(skills, most_selected_idx)
    
    random.seed(seed)
    results["P4"] = p4_desc_param_scramble(skills)
    
    random.seed(seed)
    results["P5"] = p5_desc_only_scramble(skills)
    
    random.seed(seed)
    results["P6"] = p6_targeted_desc_scramble(skills, most_selected_idx)
    
    random.seed(seed)
    results["P7"] = p7_desc_transfer(skills, most_selected_idx, least_selected_idx)
    
    if repaired_descriptions:
        results["P8"] = p8_ic1_desc_repair(skills, repaired_descriptions)
    
    return results


# ── Self-test ──────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=== Perturbation Self-Test (P1–P16) ===\n")
    
    test_skills = [
        {"name": "SearchPro", "description": "Professional web search engine with AI-powered results ranking"},
        {"name": "WebFind", "description": "Fast web search for finding information online"},
        {"name": "QueryBot", "description": "Search the internet with natural language queries"},
        {"name": "InfoSeek", "description": "Intelligent search assistant for research"},
        {"name": "NetSearch", "description": "Comprehensive web search with filtering options"},
    ]
    
    # Test P1-P8
    perturbations = apply_all_perturbations(
        test_skills,
        most_selected_idx=0,
        least_selected_idx=4,
        repaired_descriptions={
            "SearchPro": "Web search with AI ranking. Handles general queries, news, academic papers.",
        },
    )
    
    for pid, (modified, label) in perturbations.items():
        print(f"\n[{pid}] {label}")
        for orig, mod in zip(test_skills, modified):
            name_changed = orig["name"] != mod["name"]
            desc_changed = orig["description"] != mod["description"]
            changes = []
            if name_changed: changes.append(f"name: {orig['name']} → {mod['name']}")
            if desc_changed: changes.append(f"desc changed")
            if changes:
                print(f"  {orig['name']}: {', '.join(changes)}")
            else:
                print(f"  {orig['name']}: unchanged")
    
    # Test P9-P16
    print("\n\n=== New Perturbations (P9-P16) ===")
    
    new_tests = [
        ("P9", p9_perf_keyword_injection(test_skills, target_idx=4)),
        ("P9ctrl", p9_ctrl_neutral_injection(test_skills, target_idx=4)),
        ("P10", p10_cost_keyword_injection(test_skills, target_idx=4)),
        ("P11", p11_brand_alignment(test_skills, domain_word="Search")),
        ("P12", p12_anti_brand(test_skills, domain_word="Search")),
        ("P15", p15_social_proof(test_skills, target_idx=4)),
        ("P16", p16_authority_injection(test_skills, target_idx=4)),
    ]
    
    for pid, (modified, label) in new_tests:
        print(f"\n[{pid}] {label}")
        for orig, mod in zip(test_skills, modified):
            name_changed = orig["name"] != mod.get("name", orig["name"])
            desc_changed = orig["description"] != mod.get("description", orig["description"])
            changes = []
            if name_changed: changes.append(f"name: → {mod['name']}")
            if desc_changed: changes.append(f"desc: {mod['description'][:60]}...")
            if changes:
                print(f"  {orig['name']}: {', '.join(changes)}")
            else:
                print(f"  {orig['name']}: unchanged")
    
    print("\n✅ All perturbations generated successfully!")

