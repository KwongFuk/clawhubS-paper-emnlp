#!/usr/bin/env python3
"""
build_clusters.py — Construct skill clusters for bias benchmark
================================================================
Following BiasBusters (ICLR 2026) design:
  - 10 clusters of functionally-equivalent skills
  - 5 skills per cluster
  - Each cluster covers one capability domain

Uses ClawHub 18,443 skills with embedding-based similarity + LLM verification.

Output: clusters.json
"""
import json
import sqlite3
import sys
from pathlib import Path

DB_PATH = Path(__file__).resolve().parents[3] / "data/experiment1_split/merged.sqlite3"
OUT_DIR = Path(__file__).parent
OUT_DIR.mkdir(exist_ok=True)

# ──────────────────────────────────────────────────────────────────────────────
# Target cluster definitions — domains where multiple real skills compete
# Each cluster needs 5 skills that can answer the same types of queries
# ──────────────────────────────────────────────────────────────────────────────
CLUSTER_SEEDS = {
    "web_search": {
        "query": """
            SELECT skill_id, name, description FROM skills
            WHERE (LOWER(description) LIKE '%web search%'
                OR LOWER(description) LIKE '%search the web%'
                OR LOWER(description) LIKE '%internet search%'
                OR LOWER(name) LIKE '%search%')
            AND LOWER(description) NOT LIKE '%code search%'
            AND LOWER(description) NOT LIKE '%file search%'
            AND LENGTH(description) > 50
        """,
        "domain": "Search the web for information",
        "min_skills": 5,
    },
    "image_generation": {
        "query": """
            SELECT skill_id, name, description FROM skills
            WHERE (LOWER(description) LIKE '%generate image%'
                OR LOWER(description) LIKE '%image generat%'
                OR LOWER(description) LIKE '%text-to-image%'
                OR LOWER(description) LIKE '%text to image%'
                OR LOWER(description) LIKE '%create image%')
            AND LENGTH(description) > 50
        """,
        "domain": "Generate images from text prompts",
        "min_skills": 5,
    },
    "video_generation": {
        "query": """
            SELECT skill_id, name, description FROM skills
            WHERE (LOWER(description) LIKE '%generate video%'
                OR LOWER(description) LIKE '%video generat%'
                OR LOWER(description) LIKE '%text-to-video%'
                OR LOWER(description) LIKE '%text to video%')
            AND LENGTH(description) > 50
        """,
        "domain": "Generate videos from text or images",
        "min_skills": 5,
    },
    "text_to_speech": {
        "query": """
            SELECT skill_id, name, description FROM skills
            WHERE (LOWER(description) LIKE '%text-to-speech%'
                OR LOWER(description) LIKE '%text to speech%'
                OR LOWER(description) LIKE '%tts%'
                OR LOWER(description) LIKE '%convert text to audio%'
                OR LOWER(description) LIKE '%speech synthesis%')
            AND LENGTH(description) > 50
        """,
        "domain": "Convert text to spoken audio",
        "min_skills": 5,
    },
    "speech_to_text": {
        "query": """
            SELECT skill_id, name, description FROM skills
            WHERE (LOWER(description) LIKE '%speech-to-text%'
                OR LOWER(description) LIKE '%speech to text%'
                OR LOWER(description) LIKE '%transcrib%'
                OR LOWER(description) LIKE '%transcription%')
            AND LOWER(description) NOT LIKE '%subtitle translat%'
            AND LENGTH(description) > 50
        """,
        "domain": "Transcribe audio/speech to text",
        "min_skills": 5,
    },
    "github_management": {
        "query": """
            SELECT skill_id, name, description FROM skills
            WHERE (LOWER(description) LIKE '%github%'
                AND (LOWER(description) LIKE '%issue%'
                    OR LOWER(description) LIKE '%pull request%'
                    OR LOWER(description) LIKE '%repositor%'
                    OR LOWER(description) LIKE '%commit%'))
            AND LENGTH(description) > 50
        """,
        "domain": "Manage GitHub repositories, issues, and PRs",
        "min_skills": 5,
    },
    "memory_system": {
        "query": """
            SELECT skill_id, name, description FROM skills
            WHERE (LOWER(description) LIKE '%memory%'
                AND (LOWER(description) LIKE '%agent%'
                    OR LOWER(description) LIKE '%ai%'
                    OR LOWER(description) LIKE '%persist%'
                    OR LOWER(description) LIKE '%store%'
                    OR LOWER(description) LIKE '%recall%'))
            AND LENGTH(description) > 50
        """,
        "domain": "Persistent memory system for AI agents",
        "min_skills": 5,
    },
    "twitter_x": {
        "query": """
            SELECT skill_id, name, description FROM skills
            WHERE (LOWER(description) LIKE '%twitter%'
                OR LOWER(description) LIKE '%tweet%'
                OR (LOWER(description) LIKE '%x.com%'
                    AND LOWER(description) LIKE '%post%'))
            AND (LOWER(description) LIKE '%post%'
                OR LOWER(description) LIKE '%publish%'
                OR LOWER(description) LIKE '%automat%')
            AND LENGTH(description) > 50
        """,
        "domain": "Post and manage content on Twitter/X",
        "min_skills": 5,
    },
    "music_generation": {
        "query": """
            SELECT skill_id, name, description FROM skills
            WHERE (LOWER(description) LIKE '%music generat%'
                OR LOWER(description) LIKE '%generate music%'
                OR LOWER(description) LIKE '%create music%'
                OR LOWER(description) LIKE '%ai music%'
                OR LOWER(description) LIKE '%compose music%')
            AND LENGTH(description) > 50
        """,
        "domain": "Generate music from text prompts",
        "min_skills": 5,
    },
    "notion_management": {
        "query": """
            SELECT skill_id, name, description FROM skills
            WHERE LOWER(description) LIKE '%notion%'
            AND (LOWER(description) LIKE '%page%'
                OR LOWER(description) LIKE '%database%'
                OR LOWER(description) LIKE '%block%'
                OR LOWER(description) LIKE '%workspace%')
            AND LENGTH(description) > 50
        """,
        "domain": "Manage Notion pages, databases, and blocks",
        "min_skills": 5,
    },
}


def main():
    if not DB_PATH.exists():
        print(f"ERROR: Database not found at {DB_PATH}")
        sys.exit(1)

    db = sqlite3.connect(str(DB_PATH))
    cur = db.cursor()

    clusters = {}
    total_skills = 0

    for cluster_name, spec in CLUSTER_SEEDS.items():
        cur.execute(spec["query"])
        rows = cur.fetchall()

        candidates = []
        for skill_id, name, desc in rows:
            candidates.append({
                "skill_id": skill_id,
                "name": name,
                "description": desc[:500] if desc else "",
                "desc_len": len(desc) if desc else 0,
            })

        # Sort by description length (prefer informative skills) and deduplicate
        seen_names = set()
        deduped = []
        for c in sorted(candidates, key=lambda x: -x["desc_len"]):
            name_lower = c["name"].lower().strip()
            if name_lower not in seen_names:
                seen_names.add(name_lower)
                deduped.append(c)

        # Take top 5 (or more for later LLM filtering)
        selected = deduped[:min(15, len(deduped))]  # Keep 15 for LLM-based refinement

        clusters[cluster_name] = {
            "domain": spec["domain"],
            "total_candidates": len(deduped),
            "selected_for_review": len(selected),
            "skills": selected,
        }
        total_skills += len(selected)

        print(f"[{cluster_name}] {len(deduped)} candidates → {len(selected)} selected")
        for s in selected[:5]:
            print(f"  - {s['name']}: {s['description'][:80]}...")

    db.close()

    # Save
    out_path = OUT_DIR / "cluster_candidates.json"
    out_path.write_text(json.dumps(clusters, indent=2, ensure_ascii=False))
    print(f"\n✅ Saved {len(clusters)} clusters ({total_skills} skills) → {out_path}")

    # Print summary table
    print("\n" + "=" * 60)
    print(f"{'Cluster':<25} {'Candidates':>10} {'Selected':>10}")
    print("-" * 60)
    for name, data in clusters.items():
        print(f"{name:<25} {data['total_candidates']:>10} {data['selected_for_review']:>10}")
    print("=" * 60)
    print(f"\nNext step: Run refine_clusters.py to LLM-verify functional equivalence")
    print(f"           and select final 5 per cluster")


if __name__ == "__main__":
    main()
