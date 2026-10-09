#!/usr/bin/env python3
"""
generate_sample_queries.py — Create offline sample queries for pilot testing
=============================================================================
Generates 10 hand-crafted queries per cluster for dry-run testing.
No API key required.
"""
import json
from pathlib import Path

BASE_DIR = Path(__file__).parent
QUERIES_DIR = BASE_DIR / "queries"
QUERIES_DIR.mkdir(exist_ok=True)

# Hand-crafted pilot queries for each cluster (10 per cluster)
PILOT_QUERIES = {
    "web_search": [
        "Find recent news about artificial intelligence regulation in the EU",
        "What are the top programming languages in 2026?",
        "Search for information about climate change impacts on agriculture",
        "Look up the latest research on quantum computing",
        "Find reviews for the best noise-canceling headphones",
        "What is the current population of Tokyo?",
        "Search for tutorials on building REST APIs with Python",
        "Find information about upcoming SpaceX launches",
        "What are the side effects of common cold medications?",
        "Search for open source alternatives to popular productivity tools",
    ],
    "image_generation": [
        "Create a photorealistic image of a futuristic city at sunset",
        "Generate a watercolor painting of a serene mountain landscape",
        "Make an illustration of a robot reading a book in a library",
        "Create a product photo of a sleek smartwatch on a marble surface",
        "Generate a cartoon character of a friendly dragon",
        "Create an abstract art piece with vibrant blue and orange colors",
        "Make a professional headshot-style portrait of a business person",
        "Generate a pixel art scene of a space station",
        "Create an image of a cozy coffee shop interior on a rainy day",
        "Make a minimalist logo design for a tech startup called NovaStar",
    ],
    "video_generation": [
        "Create a 5-second video of waves crashing on a beach at sunset",
        "Generate a drone shot flying over a mountain forest",
        "Make a video of a coffee cup with steam rising in slow motion",
        "Create an animated logo reveal with particle effects",
        "Generate a time-lapse of clouds moving over a city skyline",
        "Make a product showcase video of a smartphone rotating on display",
        "Create a short video of aurora borealis dancing in the night sky",
        "Generate a cinematic shot of a car driving through neon-lit streets",
        "Make a video of rain drops falling on a window with city lights behind",
        "Create an underwater video scene with tropical fish and coral reefs",
    ],
    "text_to_speech": [
        "Convert this text to speech: Welcome to our podcast, today we discuss AI",
        "Read this announcement aloud: The office will be closed on Friday",
        "Generate audio narration for: Once upon a time in a distant galaxy",
        "Speak this weather report: Today will be sunny with highs near 75",
        "Create an audio version of this quote: The only limit is your imagination",
        "Read aloud: Chapter one. The old house stood at the end of the lane.",
        "Convert to speech: Your order has been confirmed and will arrive tomorrow",
        "Generate spoken audio: Ladies and gentlemen, welcome aboard flight 42",
        "Read this message: Happy birthday! Wishing you all the best today",
        "Speak this tutorial step: First, open the settings menu and click preferences",
    ],
    "speech_to_text": [
        "Transcribe this audio recording from my meeting",
        "Convert the voice memo I recorded yesterday to text",
        "I have a podcast episode that needs transcription",
        "Transcribe this interview recording with timestamps",
        "Turn this voice note into written text",
        "I need to transcribe a lecture recording for my notes",
        "Convert this phone call recording to text format",
        "Transcribe the audio from this video file",
        "I recorded a brainstorming session, please transcribe it",
        "Turn this voice message into readable text with speaker labels",
    ],
    "github_management": [
        "Create a new issue on our repository about the login page bug",
        "List all open pull requests in the main branch",
        "Show me the recent commits on the develop branch",
        "Check if there are any failing CI checks on the latest PR",
        "Create a new branch called feature/user-dashboard",
        "Review the changes in pull request number 42",
        "Add a comment to issue 15 with the bug reproduction steps",
        "List all repositories in our organization",
        "Close issue 23 with a resolution comment",
        "Show the diff between main and the staging branch",
    ],
    "memory_system": [
        "Remember that the user prefers dark mode in all applications",
        "Store this: the project deadline is March 15, 2027",
        "Save this preference: user likes Python over JavaScript",
        "Remember that we discussed the API redesign yesterday",
        "Store this fact: the database password was rotated last Tuesday",
        "Remember my home address is 123 Oak Street",
        "Save this context: the client wants weekly progress reports",
        "Store this note: user is allergic to shellfish",
        "Remember that the meeting was moved to Thursday at 3pm",
        "Save this learning: the batch size of 32 gave the best results",
    ],
    "twitter_x": [
        "Post a tweet about our new product launch",
        "Schedule a thread about AI trends for tomorrow morning",
        "Check my latest mentions and notifications",
        "Compose a tweet announcing our conference talk",
        "Post a reply to the viral thread about tech layoffs",
        "Draft a tweet thread about the benefits of open source",
        "Schedule a tweet with this image for peak engagement time",
        "Check trending topics related to artificial intelligence",
        "Post a quote tweet of the CEO's announcement",
        "Create a Twitter poll asking about favorite programming languages",
    ],
    "music_generation": [
        "Create an upbeat electronic track for a YouTube intro",
        "Generate a calm lo-fi beat for studying",
        "Make a cinematic orchestral score for a short film",
        "Create a jazz piano piece for a coffee shop playlist",
        "Generate a rock guitar riff for a podcast intro",
        "Make ambient background music for a meditation app",
        "Create a hip-hop beat with heavy bass and snare",
        "Generate a classical piano sonata in C minor",
        "Make a cheerful acoustic guitar track for a travel vlog",
        "Create an 8-bit chiptune for a retro video game",
    ],
    "notion_management": [
        "Create a new page in my workspace called Q3 Planning",
        "Add a task to my project database with due date next Friday",
        "Search for all pages tagged with 'meeting-notes'",
        "Update the status of task 42 to 'In Progress'",
        "Create a new database for tracking customer feedback",
        "List all pages modified in the last week",
        "Add a new row to the budget tracking database",
        "Move the design review page to the Archive section",
        "Query the project database for all overdue tasks",
        "Create a template page for weekly standup notes",
    ],
}


def main():
    for cluster_name, queries in PILOT_QUERIES.items():
        out_file = QUERIES_DIR / f"{cluster_name}.json"
        out_file.write_text(json.dumps(queries, indent=2, ensure_ascii=False))
        print(f"[{cluster_name}] {len(queries)} pilot queries → {out_file}")
    
    total = sum(len(q) for q in PILOT_QUERIES.values())
    print(f"\n✅ Generated {total} pilot queries across {len(PILOT_QUERIES)} clusters")
    print(f"   Pilot benchmark: {total * 5} calls per model (with rotations)")
    print(f"\nFor full 100-query generation, run:")
    print(f"  OPENAI_API_KEY=... python generate_queries.py --all --n-queries 100")


if __name__ == "__main__":
    main()
