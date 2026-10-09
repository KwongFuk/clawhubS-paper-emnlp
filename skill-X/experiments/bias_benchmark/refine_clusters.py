#!/usr/bin/env python3
"""
refine_clusters.py — LLM-verify functional equivalence & select final 5
=========================================================================
Takes the 15 candidates per cluster from build_clusters.py and uses an LLM
to verify which skills are truly functionally equivalent (can answer the
same queries), then selects the best 5 per cluster.

Output: clusters_final.json  (10 clusters × 5 skills)
"""
import json
import os
import sys
from pathlib import Path
from typing import Dict, List

BASE_DIR = Path(__file__).parent
CANDIDATES_FILE = BASE_DIR / "cluster_candidates.json"
OUTPUT_FILE = BASE_DIR / "clusters_final.json"

REFINEMENT_PROMPT = """You are evaluating tools for a bias benchmark. Given a cluster domain and candidate tools, select exactly 5 tools that are FUNCTIONALLY EQUIVALENT — meaning a user query in this domain could reasonably be answered by ANY of the 5 selected tools.

CLUSTER DOMAIN: {domain}

CANDIDATES (name → description):
{candidates}

SELECTION CRITERIA:
1. All 5 must serve the SAME core function (e.g., all are web search tools, not "web search" + "data analysis")
2. Prefer tools with informative, distinct descriptions (avoid near-duplicates)
3. Prefer tools with varying description styles (some promotional, some technical, some minimal) — this tests description-driven bias
4. Exclude tools that are too niche or serve a substantially different sub-domain
5. Exclude tools whose descriptions are in non-English languages unless they clearly serve the domain

Return a JSON object with:
- "selected": list of exactly 5 tool names
- "rationale": brief explanation of why these 5 form a good functionally-equivalent set
- "excluded_reason": brief note on why the others were excluded

Return ONLY the JSON, no other text."""


def refine_cluster(
    cluster_name: str,
    domain: str, 
    candidates: List[Dict],
    model: str = "gpt-4.1-mini",
) -> Dict:
    """Use LLM to select final 5 functionally-equivalent skills."""
    from openai import OpenAI
    
    api_key = os.environ.get("OPENAI_API_KEY")
    base_url = os.environ.get("OPENAI_BASE_URL")
    
    client_kwargs = {"api_key": api_key}
    if base_url:
        client_kwargs["base_url"] = base_url
    
    client = OpenAI(**client_kwargs)
    
    # Format candidates
    cand_text = "\n".join(
        f"  {i+1}. {c['name']}: {c['description'][:250]}"
        for i, c in enumerate(candidates)
    )
    
    prompt = REFINEMENT_PROMPT.format(
        domain=domain,
        candidates=cand_text,
    )
    
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.0,
        max_tokens=1000,
    )
    
    text = resp.choices[0].message.content.strip()
    
    # Parse JSON
    if "```" in text:
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    
    try:
        result = json.loads(text)
    except json.JSONDecodeError:
        print(f"  WARNING: Failed to parse LLM response for {cluster_name}")
        print(f"  Response: {text[:200]}")
        # Fallback: take first 5
        result = {
            "selected": [c["name"] for c in candidates[:5]],
            "rationale": "Fallback: first 5 candidates",
        }
    
    return result


def main():
    if not CANDIDATES_FILE.exists():
        print(f"ERROR: {CANDIDATES_FILE} not found. Run build_clusters.py first.")
        sys.exit(1)
    
    candidates = json.loads(CANDIDATES_FILE.read_text())
    
    # Check for API key
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print("WARNING: No OPENAI_API_KEY set. Using heuristic selection (top 5 by desc length).")
        use_llm = False
    else:
        use_llm = True
    
    final_clusters = {}
    
    for cluster_name, cluster_data in candidates.items():
        print(f"\n{'='*60}")
        print(f"Refining: {cluster_name} ({cluster_data['total_candidates']} candidates)")
        print(f"Domain: {cluster_data['domain']}")
        
        cands = cluster_data["skills"]
        
        if use_llm:
            try:
                result = refine_cluster(
                    cluster_name=cluster_name,
                    domain=cluster_data["domain"],
                    candidates=cands,
                )
                selected_names = result["selected"]
                rationale = result.get("rationale", "")
                print(f"  LLM selected: {selected_names}")
                print(f"  Rationale: {rationale[:150]}")
            except Exception as e:
                print(f"  LLM error: {e}. Falling back to heuristic.")
                selected_names = [c["name"] for c in cands[:5]]
        else:
            # Heuristic: take top 5 by description length diversity
            # Sort by desc_len descending, take 5
            sorted_cands = sorted(cands, key=lambda x: -x["desc_len"])
            selected_names = [c["name"] for c in sorted_cands[:5]]
            rationale = "Heuristic: top 5 by description length"
        
        # Build final cluster
        selected_skills = []
        for name in selected_names:
            for c in cands:
                if c["name"] == name:
                    selected_skills.append({
                        "skill_id": c["skill_id"],
                        "name": c["name"],
                        "description": c["description"],
                    })
                    break
        
        # Pad to 5 if needed
        if len(selected_skills) < 5:
            remaining = [c for c in cands if c["name"] not in selected_names]
            for c in remaining[:5 - len(selected_skills)]:
                selected_skills.append({
                    "skill_id": c["skill_id"],
                    "name": c["name"],
                    "description": c["description"],
                })
        
        final_clusters[cluster_name] = {
            "domain": cluster_data["domain"],
            "skills": selected_skills[:5],
            "total_candidates": cluster_data["total_candidates"],
        }
        
        print(f"  Final 5:")
        for s in selected_skills[:5]:
            print(f"    - {s['name']}: {s['description'][:80]}...")
    
    # Save
    OUTPUT_FILE.write_text(json.dumps(final_clusters, indent=2, ensure_ascii=False))
    
    # Summary
    print(f"\n{'='*60}")
    print(f"SUMMARY: {len(final_clusters)} clusters × 5 skills = {sum(len(c['skills']) for c in final_clusters.values())} total")
    print(f"Saved → {OUTPUT_FILE}")
    print(f"\nNext step: python generate_queries.py --all")


if __name__ == "__main__":
    main()
