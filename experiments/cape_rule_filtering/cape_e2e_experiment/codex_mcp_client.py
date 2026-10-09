#!/usr/bin/env python3
"""
codex_mcp_client.py — Direct JSON-RPC client for Codex MCP server (stdio)
Sends batch security audit prompts and saves results.
"""
import json, subprocess, sys, time, os
from pathlib import Path

CODEX = "/opt/homebrew/bin/codex"
NODE  = "/opt/homebrew/opt/node@25/bin"
ENV   = {**os.environ, "PATH": f"{NODE}:/opt/homebrew/bin:/usr/bin:/bin"}

def call_codex_mcp(prompt: str, model: str = "gpt-5.4",
                   reasoning_effort: str = "high", timeout: int = 180) -> str:
    """
    Calls `codex mcp-server` via stdio, sends one MCP request, returns reply text.
    Uses JSON-RPC over stdin/stdout (stdio transport).
    """
    # Build MCP initialize + tools/call sequence
    def msg(id_, method, params=None):
        obj = {"jsonrpc": "2.0", "id": id_, "method": method}
        if params:
            obj["params"] = params
        return json.dumps(obj) + "\n"

    # 1. Initialize
    # 2. Call the 'codex' tool  
    messages = [
        msg(1, "initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "cape-audit", "version": "1.0"}
        }),
        msg(2, "notifications/initialized", None)[:1],  # notification (no id)
        msg(3, "tools/call", {
            "name": "codex",
            "arguments": {
                "prompt": prompt,
                "config": {
                    "model": model,
                    "model_reasoning_effort": reasoning_effort
                }
            }
        })
    ]
    # notifications have no id
    notif = json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}) + "\n"

    stdin_data = (
        msg(1, "initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "cape-audit", "version": "1.0"}
        })
        + notif
        + msg(3, "tools/call", {
            "name": "codex",
            "arguments": {
                "prompt": prompt,
                "config": {
                    "model": model,
                    "model_reasoning_effort": reasoning_effort
                }
            }
        })
    )

    proc = subprocess.run(
        [CODEX, "mcp-server"],
        input=stdin_data,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=ENV
    )

    raw = proc.stdout + proc.stderr
    # Parse each line as JSON-RPC response
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            if obj.get("id") == 3:
                # This is the tools/call response
                result = obj.get("result", {})
                content = result.get("content", [])
                for c in content:
                    if c.get("type") == "text":
                        return c["text"]
                return json.dumps(result)
        except Exception:
            pass
    return raw[-2000:] if raw else "NO OUTPUT"


HERE = Path(__file__).parent

def main():
    print("Testing Codex MCP server connection...")
    reply = call_codex_mcp("Reply with exactly: {\"status\": \"ok\", \"model\": \"ready\"}", 
                           model="gpt-5.4", reasoning_effort="low", timeout=60)
    print(f"Test reply: {reply[:200]}")

if __name__ == "__main__":
    main()
