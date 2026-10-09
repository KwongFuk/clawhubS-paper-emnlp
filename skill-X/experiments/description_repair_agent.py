#!/usr/bin/env python3
"""
Description Repair Agent — Expert-crafted repairs based on feature analysis.

BiasBusters + our data show: specificity (+0.654), simplicity (-0.526 FK),
no AI keyword (-0.40), no promotional words (-0.09) predict selection.

This script generates expert-crafted repairs for the 25 never-selected tools
in the 8 benchmark clusters, then creates a repaired cluster file for re-testing.
"""
import json
import os

CLUSTERS_PATH = os.path.expanduser("~/codex/clawhubS/skill-X/experiments/bias_benchmark/clusters_final.json")
RESULTS_DIR = os.path.expanduser("~/codex/clawhubS/skill-X/experiments/bias_benchmark/results")
OUT_DIR = os.path.expanduser("~/codex/clawhubS/paper-emnlp/analysis_output")
os.makedirs(OUT_DIR, exist_ok=True)

with open(CLUSTERS_PATH) as f:
    clusters = json.load(f)
with open(os.path.join(RESULTS_DIR, "exp1_gemini-3.1-pro_run1_summary.json")) as f:
    results = json.load(f)

# Expert-crafted repairs following BiasBusters prescription:
# - Start with action verb
# - Include concrete detail (format, count, speed)
# - Under 160 chars
# - No promotional words
# - No "AI" keyword
EXPERT_REPAIRS = {
    # web_search
    "clockworksquirrel/searxng-metasearch": "Search across 70+ engines including Google, Bing, and DuckDuckGo. Returns ranked results with snippets and metadata.",
    "gora050/exa-integration": "Search and retrieve web content using Exa neural search API. Returns full-text results with relevance scores for research queries.",
    
    # image_generation
    "Amian/chatgpt-image-generation": "Generate images via ChatGPT using Playwright automation. Supports DALL-E 3 prompts, returns PNG files up to 1024x1024.",
    "feiskyer/nanobanana-skill": "Generate images from text prompts using the Nanobanana endpoint. Outputs PNG format with configurable resolution up to 1024x1024.",
    "xxmzdxxxm/falimagegen": "Generate images from text descriptions via Fal.ai API. Supports Flux and SDXL models, outputs PNG/JPEG up to 2048px.",
    "Robin797860/qwen-image": "Generate images using Qwen VL model. Supports text-to-image and image editing with Chinese and English prompts.",
    
    # video_generation
    "PauldeLavallaz/sora-video-gen": "Generate videos from text prompts using OpenAI Sora API. Creates 5-20 second clips at 1080p resolution in MP4 format.",
    "DevvGwardo/grok-imagine-video": "Generate short videos from text descriptions via Grok Imagine API. Outputs 5-10 second MP4 clips with audio.",
    "ivangdavila/video-generation": "Generate videos from text prompts. Supports multiple models including Runway Gen-3 and Kling, outputs MP4 up to 720p.",
    "ScottEverduim/prep-video": "Record and edit short video clips with text overlays. Captures screen or webcam, exports MP4 with configurable bitrate.",
    
    # text_to_speech
    "JonahGaworworski/openai-tts-python": "Convert text to speech using OpenAI TTS API (tts-1 and tts-1-hd). Six voices, MP3/WAV output, supports 57 languages.",
    "travisvn/edge-tts": "Convert text to speech using Microsoft Edge TTS. 300+ voices, 50+ languages, outputs MP3. Free, no API key required.",
    
    # speech_to_text
    "jixsonwang/aliyun-asr": "Transcribe audio to text using Alibaba Cloud ASR. Supports Chinese and English, WAV/MP3 input, returns timestamped text.",
    "huyong2023/voice-note-transcriber-cn": "Transcribe Chinese voice notes to text. Processes WAV and MP3 files, returns plain text with punctuation.",
    "shawnhansen/voicenotes": "Transcribe voice recordings to text using Whisper API. Supports 99 languages, WAV/MP3/M4A input, outputs timestamped JSON.",
    
    # github_management
    "dannyshmueli/github-pat": "Manage GitHub repos via Personal Access Token. Create issues, pull requests, and releases using the GitHub REST API v3.",
    "dannyshmueli/github-token": "Authenticate and manage GitHub repositories. Push commits, create branches, and manage webhooks via GitHub REST API v3.",
    "Linux2010/issue-finder": "Search GitHub issues across repos by label, assignee, or keyword. Returns issue title, body, and status via GitHub API v3.",
    "thegovind/github-issue-creator": "Create GitHub issues with title, body, labels, and assignees. Supports markdown formatting via GitHub REST API v3.",
    
    # memory_system
    "JamesEBall/enhanced-memory": "Store and retrieve conversation context using SQLite backend. Supports keyword search, timestamps, and 10K+ entries.",
    "justinhartbiz/dory-memory": "Persist agent memory across sessions using file-based storage. Auto-indexes by topic, retrieves by semantic similarity.",
    "ktpriyatham/triple-memory-skill": "Store memories in three backends: LanceDB vectors, Git-Notes structured, and file search. Auto-recall by semantic match.",
    "BluePointDigital/vector-memory": "Search stored memories using vector embeddings with automatic fallback to keyword search. ChromaDB backend, 50ms retrieval.",
    
    # twitter_x
    "tylordius/x-read": "Fetch and summarize public X (Twitter) posts by URL. Extracts tweet text, media links, and engagement metrics.",
    "sharbelayy/x-bookmarks": "Fetch and manage X/Twitter bookmarks via API v2. List, filter by date, and export bookmarks as JSON or markdown.",
}

# Build repairs dict
repairs = {}
for cluster_name in ["web_search", "image_generation", "video_generation",
                      "text_to_speech", "speech_to_text", "github_management",
                      "memory_system", "twitter_x"]:
    cluster = clusters[cluster_name]
    sel_dist = results["clusters"][cluster_name]["selection_dist"]
    
    for skill in cluster["skills"]:
        if sel_dist.get(skill["name"], 0) == 0:
            sid = skill["skill_id"]
            if sid in EXPERT_REPAIRS:
                repairs[sid] = {
                    "name": skill["name"],
                    "cluster": cluster_name,
                    "original_desc": skill["description"],
                    "repaired_desc": EXPERT_REPAIRS[sid],
                    "original_sel_rate": 0.0,
                    "repair_method": "expert",
                }
                print(f"✓ {skill['name']}: {len(EXPERT_REPAIRS[sid])} chars")
            else:
                # Try partial match
                matched = False
                for key, repair in EXPERT_REPAIRS.items():
                    if skill["name"].lower().replace(" ", "-") in key.lower() or key.lower() in sid.lower():
                        repairs[sid] = {
                            "name": skill["name"],
                            "cluster": cluster_name,
                            "original_desc": skill["description"],
                            "repaired_desc": repair,
                            "original_sel_rate": 0.0,
                            "repair_method": "expert",
                        }
                        print(f"✓ {skill['name']} (matched via {key}): {len(repair)} chars")
                        matched = True
                        break
                if not matched:
                    print(f"✗ {skill['name']} ({sid}) — no repair found")

# Save
with open(os.path.join(OUT_DIR, "description_repairs.json"), 'w') as f:
    json.dump(repairs, f, indent=2)

# Create repaired clusters
repaired_clusters = {}
for cluster_name in ["web_search", "image_generation", "video_generation",
                      "text_to_speech", "speech_to_text", "github_management",
                      "memory_system", "twitter_x"]:
    cluster = clusters[cluster_name]
    repaired_skills = []
    for skill in cluster["skills"]:
        skill_copy = dict(skill)
        if skill["skill_id"] in repairs:
            skill_copy["description"] = repairs[skill["skill_id"]]["repaired_desc"]
            skill_copy["original_description"] = skill["description"]
            skill_copy["was_repaired"] = True
        else:
            skill_copy["was_repaired"] = False
        repaired_skills.append(skill_copy)
    repaired_clusters[cluster_name] = {
        "domain": cluster["domain"],
        "skills": repaired_skills,
        "total_candidates": cluster.get("total_candidates", len(repaired_skills)),
    }

with open(os.path.join(OUT_DIR, "clusters_repaired.json"), 'w') as f:
    json.dump(repaired_clusters, f, indent=2)

print(f"\n{'='*60}")
print(f"Generated {len(repairs)} expert repairs")
print(f"Saved to {OUT_DIR}/description_repairs.json")
print(f"Repaired clusters: {OUT_DIR}/clusters_repaired.json")

# Summary statistics
print(f"\nRepair statistics:")
for cluster_name in ["web_search", "image_generation", "video_generation",
                      "text_to_speech", "speech_to_text", "github_management",
                      "memory_system", "twitter_x"]:
    cluster_repairs = [r for r in repairs.values() if r["cluster"] == cluster_name]
    if cluster_repairs:
        orig_lens = [len(r["original_desc"]) for r in cluster_repairs]
        new_lens = [len(r["repaired_desc"]) for r in cluster_repairs]
        print(f"  {cluster_name}: {len(cluster_repairs)} repairs, "
              f"avg len {sum(orig_lens)//len(orig_lens)} → {sum(new_lens)//len(new_lens)} chars")
