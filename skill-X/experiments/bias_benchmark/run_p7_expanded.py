#!/usr/bin/env python3
"""
run_p7_expanded.py — Run P7 (description swap) on ALL 8 clusters
================================================================

P7 swaps descriptions between the most-selected and least-selected skill
in each cluster. This is the key causal experiment proving that selection
tracks description content, not skill name.

Previously only run on 2 clusters; this expands to all 8 for robustness.

Run when quota resets:
  nohup python3 run_p7_expanded.py > results/p7_expanded.log 2>&1 &
"""
import copy
import json
import sys
from pathlib import Path

# Import the run_experiment function from the main runner
sys.path.insert(0, str(Path(__file__).parent))
from run_expanded_experiments import (
    run_experiment, load_clusters, RESULTS_DIR
)
from perturbations import p7_desc_transfer

# Baseline most/least from EXP-1 (Gemini 3.1 Pro)
BASELINE_RANKINGS = {
    "web_search": {"most": "Tavily", "most_idx": 0, "least": "Exa", "least_idx": 4},
    "image_generation": {"most": "Flux Image", "most_idx": 1, "least": "Qwen Image", "least_idx": 4},
    "video_generation": {"most": "Google Veo", "most_idx": 3, "least": "Prep Video", "least_idx": 4},
    "text_to_speech": {"most": "Mac TTS", "most_idx": 4, "least": "Edge TTS", "least_idx": 2},
    "speech_to_text": {"most": "Speech To Text", "most_idx": 0, "least": "Voicenotes", "least_idx": 4},
    "github_management": {"most": "GitHub", "most_idx": 0, "least": "Github Issue Creator", "least_idx": 4},
    "music_generation": {"most": "music-cog", "most_idx": 0, "least": "Song", "least_idx": 4},
    "notion_management": {"most": "Notion API", "most_idx": 0, "least": "Notion Manager", "least_idx": 4},
}

# Use EXP-3 results (P9ctrl) as the better baseline for Gemini 2.5 Flash
EXP3_RANKINGS = {
    "web_search": {"most": "Tavily", "least": "Exa"},
    "image_generation": {"most": "Nanobanana Skill", "least": "Qwen Image"},
    "video_generation": {"most": "AI Video Generation", "least": "Prep Video"},
    "text_to_speech": {"most": "ElevenLabs", "least": "Mac TTS"},
    "speech_to_text": {"most": "ElevenLabs STT OpenClaw", "least": "Voicenotes"},
    "github_management": {"most": "GitHub", "least": "Github Issue Creator"},
    "music_generation": {"most": "music-cog", "least": "Song"},
    "notion_management": {"most": "Notion API", "least": "Notion Manager"},
}


def find_idx(skills, name):
    """Find index of skill by name."""
    for i, s in enumerate(skills):
        if s["name"] == name:
            return i
    return -1


def main():
    clusters = load_clusters()
    
    print("=" * 60)
    print("P7 EXPANDED: Description Swap on All 8 Clusters")
    print("=" * 60)
    
    all_results = []
    
    for cluster_name in ["web_search", "image_generation", "video_generation",
                         "text_to_speech", "speech_to_text", "github_management",
                         "music_generation", "notion_management"]:
        if cluster_name not in clusters:
            print(f"  Skipping {cluster_name} (not in clusters)")
            continue
        
        skills = clusters[cluster_name]["skills"]
        ranking = EXP3_RANKINGS.get(cluster_name, BASELINE_RANKINGS.get(cluster_name, {}))
        
        most_name = ranking.get("most", skills[0]["name"])
        least_name = ranking.get("least", skills[-1]["name"])
        
        most_idx = find_idx(skills, most_name)
        least_idx = find_idx(skills, least_name)
        
        if most_idx < 0 or least_idx < 0:
            print(f"  Skipping {cluster_name}: can't find {most_name} or {least_name}")
            continue
        
        print(f"\n--- {cluster_name} ---")
        print(f"  Swapping: {most_name} (most) ↔ {least_name} (least)")
        
        # Apply P7 perturbation
        p7_skills, _ = p7_desc_transfer(skills, most_selected_idx=most_idx,
                                         least_selected_idx=least_idx)
        
        result = run_experiment(f"P7_swap", cluster_name, p7_skills)
        all_results.append(result)
    
    # Summary
    print("\n" + "=" * 60)
    print("P7 EXPANDED SUMMARY")
    print("=" * 60)
    
    reversed_count = 0
    for r in all_results:
        cluster = r["cluster"]
        sel = r["selection_dist"]
        top = max(sel, key=sel.get)
        
        ranking = EXP3_RANKINGS.get(cluster, {})
        original_most = ranking.get("most", "?")
        original_least = ranking.get("least", "?")
        
        # Did the least-selected skill become dominant after getting the best description?
        reversed_ = (top == original_least)
        marker = "✅ REVERSED" if reversed_ else "⚠️ NOT REVERSED"
        if reversed_:
            reversed_count += 1
        
        print(f"  {cluster:20} δ={r['delta_api']:.3f}  top={top:25}  {marker}")
    
    print(f"\n  Reversal rate: {reversed_count}/{len(all_results)} = {reversed_count/max(len(all_results),1):.0%}")
    print(f"  (Expected: high reversal proves description > name)")


if __name__ == "__main__":
    main()
