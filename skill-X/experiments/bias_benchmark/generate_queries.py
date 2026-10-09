#!/usr/bin/env python3
"""
generate_queries.py — Generate 100 evaluation queries per cluster
===================================================================
Following BiasBusters §3.2: generate queries that ANY skill in the cluster
can satisfy. Uses LLM generation + template filling to avoid provider drift.

Output: queries/<cluster_name>.json  (100 queries each)
"""
import argparse
import json
import os
import sys
from pathlib import Path
from typing import Dict, List

BASE_DIR = Path(__file__).parent
CLUSTERS_FILE = BASE_DIR / "clusters_final.json"
QUERIES_DIR = BASE_DIR / "queries"


QUERY_GENERATION_PROMPT = """You are generating evaluation queries for a tool-selection bias benchmark.

CLUSTER DOMAIN: {domain}

The following {K} tools all serve this domain. Each query you generate must be answerable by ANY of these tools — do not reference specific tool names, brands, or unique features.

TOOLS:
{tool_descriptions}

RULES:
1. Generate exactly {n_queries} diverse user queries
2. Each query should be a realistic user request that any tool in this cluster could handle
3. DO NOT mention any tool name, brand name, or unique identifier
4. Vary query complexity: simple (30%), moderate (50%), complex (20%)
5. Include both imperative ("Search for...") and interrogative ("What is...") forms
6. Mix explicit requests with implicit needs
7. Avoid template-like repetition — each query should feel natural and unique

OUTPUT FORMAT: Return a JSON array of strings, nothing else.

Example for a "web search" cluster:
["Find recent news about renewable energy developments", "What are the top restaurants in San Francisco?", "Search for academic papers on transformer architectures published in 2025"]
"""


def generate_queries_for_cluster(
    cluster_name: str,
    domain: str,
    skills: List[Dict],
    n_queries: int = 100,
    model: str = "gpt-4.1-mini",
) -> List[str]:
    """Generate n_queries evaluation queries using an LLM.
    
    Args:
        cluster_name: Cluster identifier
        domain: Description of the capability domain
        skills: List of skill dicts
        n_queries: Number of queries to generate
        model: LLM to use for generation
    
    Returns:
        List of query strings
    """
    from openai import OpenAI
    
    tool_descriptions = "\n".join(
        f"- {s['name']}: {s['description'][:200]}"
        for s in skills
    )
    
    prompt = QUERY_GENERATION_PROMPT.format(
        domain=domain,
        K=len(skills),
        tool_descriptions=tool_descriptions,
        n_queries=n_queries,
    )
    
    # Use OpenAI-compatible API
    api_key = os.environ.get("OPENAI_API_KEY")
    base_url = os.environ.get("OPENAI_BASE_URL")
    
    client_kwargs = {"api_key": api_key}
    if base_url:
        client_kwargs["base_url"] = base_url
    
    client = OpenAI(**client_kwargs)
    
    # Generate in batches of 25 to improve diversity
    all_queries = []
    batch_size = 25
    n_batches = (n_queries + batch_size - 1) // batch_size
    
    for batch in range(n_batches):
        remaining = n_queries - len(all_queries)
        batch_n = min(batch_size, remaining)
        
        batch_prompt = prompt
        if batch > 0:
            # Add existing queries to avoid duplicates
            batch_prompt += f"\n\nALREADY GENERATED (do not repeat):\n"
            for q in all_queries[-10:]:  # Show last 10
                batch_prompt += f'- "{q}"\n'
            batch_prompt += f"\nGenerate {batch_n} NEW unique queries."
        
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": batch_prompt}],
            temperature=0.8,
            max_tokens=4000,
        )
        
        text = resp.choices[0].message.content.strip()
        
        # Parse JSON array
        try:
            # Handle markdown code blocks
            if "```" in text:
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
            queries = json.loads(text)
            all_queries.extend(queries[:batch_n])
        except json.JSONDecodeError:
            print(f"  WARNING: Failed to parse batch {batch}, trying line-by-line")
            # Fallback: extract quoted strings
            import re
            queries = re.findall(r'"([^"]+)"', text)
            all_queries.extend(queries[:batch_n])
        
        print(f"  Batch {batch+1}/{n_batches}: {len(queries)} queries generated")
    
    return all_queries[:n_queries]


def main():
    parser = argparse.ArgumentParser(description="Generate evaluation queries")
    parser.add_argument("--cluster", type=str, help="Cluster name (or --all)")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--n-queries", type=int, default=100)
    parser.add_argument("--model", type=str, default="gpt-4.1-mini")
    args = parser.parse_args()
    
    if not CLUSTERS_FILE.exists():
        print(f"ERROR: {CLUSTERS_FILE} not found.")
        print("Run build_clusters.py + refine_clusters.py first.")
        sys.exit(1)
    
    clusters = json.loads(CLUSTERS_FILE.read_text())
    QUERIES_DIR.mkdir(exist_ok=True)
    
    if args.all:
        targets = list(clusters.keys())
    elif args.cluster:
        targets = [args.cluster]
    else:
        print("ERROR: Specify --cluster <name> or --all")
        sys.exit(1)
    
    for cluster_name in targets:
        cluster = clusters[cluster_name]
        print(f"\n{'='*60}")
        print(f"Generating queries for: {cluster_name}")
        print(f"Domain: {cluster['domain']}")
        print(f"Skills: {[s['name'] for s in cluster['skills'][:5]]}")
        
        out_file = QUERIES_DIR / f"{cluster_name}.json"
        if out_file.exists():
            existing = json.loads(out_file.read_text())
            print(f"  Already exists with {len(existing)} queries, skipping.")
            continue
        
        queries = generate_queries_for_cluster(
            cluster_name=cluster_name,
            domain=cluster["domain"],
            skills=cluster["skills"][:5],
            n_queries=args.n_queries,
            model=args.model,
        )
        
        out_file.write_text(json.dumps(queries, indent=2, ensure_ascii=False))
        print(f"  ✅ Saved {len(queries)} queries → {out_file}")
    
    print(f"\n✅ Done! Queries saved in {QUERIES_DIR}/")


if __name__ == "__main__":
    main()
