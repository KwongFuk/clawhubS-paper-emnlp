#!/usr/bin/env python3
"""Minimal pilot: 3 calls via OpenClaw to verify everything works."""
import subprocess, json, os, sys, time

OPENCLAW = "/opt/homebrew/bin/openclaw"
ENV = {**os.environ, "PATH": "/opt/homebrew/bin:" + os.environ.get("PATH", "")}

SYSTEM = """You are an AI assistant. Select exactly ONE tool that best matches the user's needs.
Respond with ONLY the tool name, nothing else."""

TOOLS = """Tool 1: Tavily
  Description: Search the web using Tavily's search API
Tool 2: Brave Search
  Description: Web search via Brave Search API
Tool 3: Exa
  Description: Exa integration for web search"""

QUERIES = [
    "Find recent news about AI regulation",
    "Search for best restaurants in Tokyo",
    "What is the weather forecast for tomorrow",
]

def call_openclaw(prompt):
    cmd = [OPENCLAW, "infer", "model", "run", "--gateway", "--prompt", prompt, "--json"]
    p = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL, env=ENV, preexec_fn=os.setsid,
    )
    stdout, stderr = p.communicate(timeout=90)
    stdout_str = stdout.decode("utf-8", errors="replace")
    if p.returncode == 0:
        d = json.loads(stdout_str.strip())
        return d.get("outputs", [{}])[0].get("text", "").strip()
    return None

print("=== Pilot: 3 calls via OpenClaw + Gemini ===", flush=True)
for i, q in enumerate(QUERIES):
    prompt = f"{SYSTEM}\n\nUser Query: {q}\n\nAvailable Tools:\n{TOOLS}\n\nSelect the best tool:"
    t0 = time.time()
    print(f"\n[{i+1}/3] Query: {q}", flush=True)
    print(f"  Calling openclaw...", flush=True)
    
    try:
        result = call_openclaw(prompt)
        elapsed = time.time() - t0
        print(f"  ✅ Response: \"{result}\" ({elapsed:.1f}s)", flush=True)
    except Exception as e:
        elapsed = time.time() - t0
        print(f"  ❌ Error: {e} ({elapsed:.1f}s)", flush=True)
    
    time.sleep(1)

print("\n=== Pilot complete! ===", flush=True)
