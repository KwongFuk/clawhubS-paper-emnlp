#!/usr/bin/env python3
"""
run_full_codex_replication.py
=============================
Full replication of ALL experiments on openai-codex/gpt-5.4-mini.
This gives us multi-model validation (Gemini 2.5 Flash + GPT-5.4-mini).

Experiments:
  Phase 1: P7 expanded — description swap on 8 clusters (200 calls)
  Phase 2: H1 — keyword injection on 8 clusters (600 calls)
  Phase 3: H2 — brand preference on 4 clusters (200 calls)
  Phase 4: H5H6 — social proof + authority on 4 clusters (200 calls)

Total: ~1200 calls on GPT-5.4-mini

Usage:
  nohup python3 run_full_codex_replication.py > results/codex_replication.log 2>&1 &
"""
import copy
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from run_expanded_experiments import run_experiment, load_clusters, RESULTS_DIR
from perturbations import p7_desc_transfer

MODEL_TAG = "gpt54mini"

# EXP-3 baseline rankings (from Gemini 2.5 Flash P9ctrl results)
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

ALL_CLUSTERS = [
    "web_search", "image_generation", "video_generation", "text_to_speech",
    "speech_to_text", "github_management", "music_generation", "notion_management",
]
H2_CLUSTERS = ["web_search", "image_generation", "text_to_speech", "video_generation"]


def find_idx(skills, name):
    for i, s in enumerate(skills):
        if s["name"] == name:
            return i
    return -1


def run_p7_phase(clusters_data):
    """Phase 1: P7 description swap on all 8 clusters."""
    print("\n" + "#" * 60)
    print("# PHASE 1: P7 Description Swap (8 clusters)")
    print("#" * 60)

    results = []
    for cluster_name in ALL_CLUSTERS:
        if cluster_name not in clusters_data:
            continue
        skills = clusters_data[cluster_name]["skills"]
        ranking = EXP3_RANKINGS.get(cluster_name, {})
        most_name = ranking.get("most", skills[0]["name"])
        least_name = ranking.get("least", skills[-1]["name"])
        most_idx = find_idx(skills, most_name)
        least_idx = find_idx(skills, least_name)
        if most_idx < 0 or least_idx < 0:
            print(f"  Skipping {cluster_name}: can't find skills")
            continue

        print(f"\n  [{cluster_name}] Swap: {most_name} ↔ {least_name}")
        p7_skills, _ = p7_desc_transfer(skills, most_idx, least_idx)

        tag = f"P7_swap_{MODEL_TAG}"
        r = run_experiment(tag, cluster_name, p7_skills)

        # Save with model-tagged filename
        out_path = RESULTS_DIR / f"exp4_{MODEL_TAG}_{cluster_name}_P7_swap.json"
        r["model"] = "openai-codex/gpt-5.4-mini"
        with open(out_path, "w") as f:
            json.dump(r, f, indent=2)
        print(f"  ✅ Saved → {out_path}")
        results.append(r)

    # P7 summary
    print("\n  P7 Summary:")
    for r in results:
        sel = r["selection_dist"]
        top = max(sel, key=sel.get)
        cluster = r["cluster"]
        original_least = EXP3_RANKINGS.get(cluster, {}).get("least", "?")
        reversed_ = (top == original_least)
        marker = "✅ REVERSED" if reversed_ else "⚠️"
        print(f"    {cluster:20} δ={r['delta_api']:.3f} top={top} {marker}")


def run_h1_phase(clusters_data):
    """Phase 2: H1 keyword injection on all 8 clusters."""
    print("\n" + "#" * 60)
    print("# PHASE 2: H1 Keyword Injection (8 clusters)")
    print("#" * 60)

    runner = Path(__file__).parent / "run_expanded_experiments.py"
    for cluster_name in ALL_CLUSTERS:
        print(f"\n  Running H1 on {cluster_name}...")
        log_file = RESULTS_DIR / f"exp4_{MODEL_TAG}_{cluster_name}_H1.log"
        proc = subprocess.run(
            [sys.executable, str(runner), "--hypothesis", "H1", "--cluster", cluster_name],
            stdout=open(log_file, "w"), stderr=subprocess.STDOUT,
        )
        # Rename output files to include model tag
        for f in RESULTS_DIR.glob(f"exp3_{cluster_name}_P*.json"):
            new_name = f.name.replace("exp3_", f"exp4_{MODEL_TAG}_")
            target = RESULTS_DIR / new_name
            if not target.exists():
                f.rename(target)
                print(f"    Renamed → {new_name}")
        time.sleep(2)


def run_h2_phase(clusters_data):
    """Phase 3: H2 brand experiments on 4 clusters."""
    print("\n" + "#" * 60)
    print("# PHASE 3: H2 Brand Preference (4 clusters)")
    print("#" * 60)

    runner = Path(__file__).parent / "run_expanded_experiments.py"
    for cluster_name in H2_CLUSTERS:
        print(f"\n  Running H2 on {cluster_name}...")
        log_file = RESULTS_DIR / f"exp4_{MODEL_TAG}_{cluster_name}_H2.log"
        proc = subprocess.run(
            [sys.executable, str(runner), "--hypothesis", "H2", "--cluster", cluster_name],
            stdout=open(log_file, "w"), stderr=subprocess.STDOUT,
        )
        for f in RESULTS_DIR.glob(f"exp3_{cluster_name}_P1*.json"):
            new_name = f.name.replace("exp3_", f"exp4_{MODEL_TAG}_")
            target = RESULTS_DIR / new_name
            if not target.exists():
                f.rename(target)
                print(f"    Renamed → {new_name}")
        time.sleep(2)


def run_h5h6_phase(clusters_data):
    """Phase 4: H5H6 social proof + authority on 4 clusters."""
    print("\n" + "#" * 60)
    print("# PHASE 4: H5H6 Social Proof + Authority (4 clusters)")
    print("#" * 60)

    runner = Path(__file__).parent / "run_expanded_experiments.py"
    for cluster_name in H2_CLUSTERS:
        print(f"\n  Running H5H6 on {cluster_name}...")
        log_file = RESULTS_DIR / f"exp4_{MODEL_TAG}_{cluster_name}_H5H6.log"
        proc = subprocess.run(
            [sys.executable, str(runner), "--hypothesis", "H5H6", "--cluster", cluster_name],
            stdout=open(log_file, "w"), stderr=subprocess.STDOUT,
        )
        for f in RESULTS_DIR.glob(f"exp3_{cluster_name}_P1*.json"):
            new_name = f.name.replace("exp3_", f"exp4_{MODEL_TAG}_")
            target = RESULTS_DIR / new_name
            if not target.exists():
                f.rename(target)
                print(f"    Renamed → {new_name}")
        time.sleep(2)


def print_final_summary():
    """Print summary of all Codex results."""
    print("\n" + "=" * 60)
    print("FINAL CODEX REPLICATION SUMMARY")
    print("=" * 60)

    for f in sorted(RESULTS_DIR.glob(f"exp4_{MODEL_TAG}_*.json")):
        try:
            d = json.loads(f.read_text())
            sel = d.get("selection_dist", {})
            if sel:
                top = max(sel, key=sel.get)
                print(f"  {d.get('cluster','?'):20} {d.get('condition','?'):18} "
                      f"δ={d.get('delta_api',0):.3f} top={top}({sel[top]:.0%})")
        except Exception:
            pass

    # Count
    n_files = len(list(RESULTS_DIR.glob(f"exp4_{MODEL_TAG}_*.json")))
    print(f"\n  Total result files: {n_files}")


def main():
    print("=" * 60)
    print(f"FULL CODEX REPLICATION (model: openai-codex/gpt-5.4-mini)")
    print("=" * 60)

    clusters_data = load_clusters()

    run_p7_phase(clusters_data)
    run_h1_phase(clusters_data)
    run_h2_phase(clusters_data)
    run_h5h6_phase(clusters_data)

    print_final_summary()

    print("\n" + "=" * 60)
    print("ALL CODEX REPLICATION EXPERIMENTS COMPLETE!")
    print("=" * 60)


if __name__ == "__main__":
    main()
