#!/usr/bin/env python3
"""
run_gemini_p7_expansion.py — Run P7 on remaining 6 clusters with Gemini 2.5 Flash
===================================================================================
We already have P7 on web_search + image_gen (Gemini 3.1 Pro).
This fills the remaining 6 clusters on Gemini 2.5 Flash.

IMPORTANT: Switch openclaw.json to gemini-2.5-flash before running.

Usage (after quota resets ~3:17 PM):
  python3 -c "import json; c=json.load(open('/Users/guangfu/.openclaw/openclaw.json')); c['agents']['defaults']['model']['primary']='google-gemini-cli/gemini-2.5-flash'; json.dump(c,open('/Users/guangfu/.openclaw/openclaw.json','w'),indent=2)"
  nohup python3 run_gemini_p7_expansion.py > results/gemini_p7_expansion.log 2>&1 &
"""
import copy
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from run_expanded_experiments import run_experiment, load_clusters, RESULTS_DIR
from perturbations import p7_desc_transfer

# Only clusters NOT yet tested with P7 on any Gemini model
REMAINING_CLUSTERS = [
    "video_generation", "text_to_speech", "speech_to_text",
    "github_management", "music_generation", "notion_management",
]

EXP3_RANKINGS = {
    "video_generation": {"most": "AI Video Generation", "least": "Prep Video"},
    "text_to_speech": {"most": "ElevenLabs", "least": "Mac TTS"},
    "speech_to_text": {"most": "ElevenLabs STT OpenClaw", "least": "Voicenotes"},
    "github_management": {"most": "GitHub", "least": "Github Issue Creator"},
    "music_generation": {"most": "music-cog", "least": "Song"},
    "notion_management": {"most": "Notion API", "least": "Notion Manager"},
}


def find_idx(skills, name):
    for i, s in enumerate(skills):
        if s["name"] == name:
            return i
    return -1


def main():
    clusters = load_clusters()
    print("=" * 60)
    print("GEMINI P7 EXPANSION: 6 remaining clusters")
    print("=" * 60)

    results = []
    for cluster_name in REMAINING_CLUSTERS:
        if cluster_name not in clusters:
            continue
        skills = clusters[cluster_name]["skills"]
        ranking = EXP3_RANKINGS[cluster_name]
        most_idx = find_idx(skills, ranking["most"])
        least_idx = find_idx(skills, ranking["least"])
        if most_idx < 0 or least_idx < 0:
            print(f"  Skipping {cluster_name}")
            continue

        print(f"\n--- {cluster_name}: {ranking['most']} ↔ {ranking['least']} ---")
        p7_skills, _ = p7_desc_transfer(skills, most_idx, least_idx)
        r = run_experiment("P7_swap_gemini", cluster_name, p7_skills)

        out_path = RESULTS_DIR / f"exp3_gemini25flash_{cluster_name}_P7_swap.json"
        r["model"] = "google-gemini-cli/gemini-2.5-flash"
        with open(out_path, "w") as f:
            json.dump(r, f, indent=2)
        print(f"  ✅ Saved → {out_path.name}")
        results.append(r)

    print("\n" + "=" * 60)
    print("SUMMARY")
    for r in results:
        sel = r["selection_dist"]
        top = max(sel, key=sel.get)
        cluster = r["cluster"]
        least = EXP3_RANKINGS[cluster]["least"]
        reversed_ = (top == least)
        marker = "✅ REVERSED" if reversed_ else "⚠️"
        print(f"  {cluster:20} δ={r['delta_api']:.3f} top={top} {marker}")


if __name__ == "__main__":
    main()
