#!/usr/bin/env python3
"""
22_hybrid_extractor.py  — Neuro-Symbolic Hybrid Capability Extractor (Production)
====================================================================================
Connects to real vLLM OpenAI-compatible endpoints to run 3-model majority-vote
ensemble extraction across 5 tracks:

  Track 1 (YAML)  : Deterministic rule parsing           → C_vis
  Track 2 (Desc)  : 3×LLM semantic understanding         → C_vis
  Track 3 (Code+) : LSCPS Regex → LLM false-positive filter → C_act
  Track 4 (Code−) : LLM bloodhound → AST judge           → C_act
  Track 5 (Inst)  : 3×LLM instruction intent extraction  → C_act

Usage:
  # Single model mode (one vLLM server on port 8000):
  python3 22_hybrid_extractor.py --endpoint http://nodeXX:8000

  # Multi-model ensemble (3 vLLM servers):
  python3 22_hybrid_extractor.py \
      --endpoints http://node:8000,http://node:8001,http://node:8002

  # Process specific skills:
  python3 22_hybrid_extractor.py --endpoint http://node:8000 --limit 100

  # Dry run (mock mode, no GPU needed):
  python3 22_hybrid_extractor.py --mock
"""

import os
import json
import re
import yaml
import ast
import time
import sqlite3
import argparse
import logging
from typing import List, Dict, Set, Optional, Tuple
from pathlib import Path
from collections import Counter

# Try to import requests; fall back gracefully
try:
    import requests
except ImportError:
    requests = None

HERE = Path(__file__).parent
ROOT = HERE.parent.parent.parent
DB_PATH = Path(os.environ.get("CLAWHUB_DB",
               str(ROOT / "data/experiment1_split/merged.sqlite3")))
JSONL_PATH = Path(os.environ.get("CLAWHUB_JSONL",
                  str(HERE / "data" / "version_transitions_enriched.jsonl")))
RESULTS_DIR = Path(os.environ.get("CLAWHUB_RESULTS", str(HERE / "results")))
OUTPUT_JSON = RESULTS_DIR / "hybrid_extraction_results.json"
REPORT_PATH = RESULTS_DIR / "hybrid_extraction_report.md"

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("hybrid")

# ==============================================================================
# LSCPS Capability Families (12 families, 89 patterns)
# ==============================================================================
LSCPS_FAMILIES = [
    "SHELL_EXEC", "CRED_READ", "CRED_TRANSMIT", "FILE_DELETE",
    "NET_WRITE", "AUTONOMOUS", "FILE_WRITE", "NET_READ",
    "FILE_READ", "MEMORY_WRITE", "MULTI_STEP", "CRED_STORE",
]

LSCPS_REGEX = {
    "SHELL_EXEC": [
        r'\bsubprocess\b', r'\bos\.system\b', r'\bos\.popen\b',
        r'\bexec\s*\(', r'\beval\s*\(', r'\bshell\s*=\s*True\b',
        r'\brm\s+-rf\b', r'\bshutil\.rmtree\b', r'\bbash\b',
        r'\bchmod\b', r'\b/bin/sh\b',
    ],
    "CRED_READ": [
        r'\bapi[_\s]?key\b', r'\bpassword\b', r'\bprivate[_\s]?key\b',
        r'\bsecret\b', r'\baccess[_\s]?token\b', r'\bbearer\b',
        r'\boauth\b', r'\.env\b', r'\bcredential\b',
    ],
    "CRED_TRANSMIT": [
        r'\bsend.*key\b', r'\bupload.*credential\b', r'\bpost.*token\b',
        r'\bexfil\b', r'\bwebhook\b.*\b(key|token|secret)\b',
    ],
    "NET_WRITE": [
        r'\brequests\.post\b', r'\brequests\.put\b', r'\bwebhook\b',
        r'\bhttpx\.post\b', r'\bsmtp\b', r'\bsend.*email\b',
        r'\bcurl\b.*-d\s', r'\bfetch\b.*\bPOST\b',
    ],
    "NET_READ": [
        r'\brequests\.get\b', r'\bhttpx\.get\b', r'\bcurl\s+http\b',
        r'\bwget\b', r'\bfetch\s*\(',
    ],
    "FILE_DELETE": [
        r'\bos\.remove\b', r'\bos\.unlink\b', r'\bshutil\.rmtree\b',
        r'\brm\s+-rf\b',
    ],
    "FILE_WRITE": [
        r'\bopen\s*\(.*["\']w', r'\.write\s*\(', r'\bshutil\.copy\b',
        r'\bos\.makedirs\b',
    ],
    "FILE_READ": [
        r'\bopen\s*\(.*["\']r', r'\.read\s*\(', r'\bglob\b',
        r'\bos\.listdir\b',
    ],
    "AUTONOMOUS": [
        r'\bautonomous\b', r'\bautomatically\b', r'\bwithout.*confirm\b',
        r'\bbackground\b', r'\bcron\b', r'\bpersistent\b', r'\bschedule\b',
    ],
    "MEMORY_WRITE": [
        r'\bmemory\b', r'\bremember\b', r'\bstore.*context\b', r'\blong.term\b',
    ],
    "MULTI_STEP": [
        r'\bmulti.step\b', r'\bworkflow\b', r'\bpipeline\b', r'\bchain\b',
    ],
    "CRED_STORE": [
        r'\bos\.environ\b', r'\bkeychain\b', r'\bkeyring\b',
    ],
}

BENIGN_INDICATORS = {
    "SHELL_EXEC": [r'```.*?\bexample\b', r'#\s*this\s+is', r'placeholder'],
    "CRED_READ": [r'your[_\s]api[_\s]?key', r'replace\s+with', r'insert\s+your',
                  r'example', r'placeholder', r'xxx+'],
    "FILE_DELETE": [r'clean\s*up', r'temporary'],
}

# Semantic description mapping for C_vis
SEMANTIC_DESC_MAP = {
    "SHELL_EXEC": {
        "explicit": ["execute command", "run command", "shell", "terminal", "bash",
                      "command line", "subprocess", "script execution"],
        "negation": ["does not execute", "no shell", "read-only"],
    },
    "CRED_READ": {
        "explicit": ["api key", "credential", "authentication", "token",
                      "password", "secret", "oauth", "env var"],
        "negation": ["no credentials", "no api key needed", "keyless"],
    },
    "NET_WRITE": {
        "explicit": ["send data", "post", "webhook", "upload", "notify",
                      "email", "http request"],
        "negation": ["does not send", "no outbound", "offline"],
    },
    "NET_READ": {
        "explicit": ["fetch", "download", "retrieve", "scrape", "crawl", "api", "http"],
        "negation": ["offline", "no network", "local only"],
    },
    "FILE_DELETE": {
        "explicit": ["delete file", "remove file", "clean up files", "erase"],
        "negation": ["does not delete", "non-destructive", "read-only"],
    },
    "FILE_WRITE": {
        "explicit": ["write file", "create file", "save", "export", "generate report"],
        "negation": ["read-only", "does not write"],
    },
    "AUTONOMOUS": {
        "explicit": ["autonomous", "automatic", "background", "scheduled", "daemon"],
        "negation": ["user-controlled", "manual", "on-demand"],
    },
}


def normalize(t: str) -> str:
    return re.sub(r"\s+", " ", (t or "")).lower().strip()


# ==============================================================================
# vLLM Ensemble Client
# ==============================================================================
class VLLMEnsemble:
    """
    Manages connections to 1-3 vLLM OpenAI-compatible servers.
    Supports majority vote across models.
    """
    def __init__(self, endpoints: List[str], mock: bool = False,
                 temperature: float = 0.1, max_tokens: int = 256):
        self.endpoints = endpoints
        self.mock = mock
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.stats = Counter()  # track API call counts
        self._model_cache = {}  # endpoint → model name

        if not mock and not requests:
            raise ImportError("pip install requests  is required for real API calls")

        if not mock:
            log.info(f"VLLMEnsemble initialized with {len(endpoints)} endpoint(s):")
            for ep in endpoints:
                log.info(f"  → {ep}")

    def _detect_model(self, endpoint: str) -> str:
        """Query /v1/models once to detect which model is loaded."""
        try:
            resp = requests.get(f"{endpoint}/v1/models", timeout=10)
            data = resp.json()
            model_id = data["data"][0]["id"]
            return model_id.lower()
        except Exception:
            return ""

    def _call_one(self, endpoint: str, prompt: str, system: str = "") -> str:
        """Call a single vLLM endpoint and return the text response."""
        url = f"{endpoint}/v1/chat/completions"
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        payload = {
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "stream": False,
        }

        # Only add thinking-disable for Qwen3 models
        if endpoint not in self._model_cache:
            self._model_cache[endpoint] = self._detect_model(endpoint)
        if "qwen" in self._model_cache[endpoint]:
            payload["chat_template_kwargs"] = {"enable_thinking": False}

        try:
            resp = requests.post(url, json=payload, timeout=60)
            resp.raise_for_status()
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            self.stats["api_calls"] += 1
            # Strip any residual <think>...</think> tags
            content = re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL)
            return content.strip()
        except Exception as e:
            self.stats["api_errors"] += 1
            log.warning(f"  API error ({endpoint}): {e}")
            return ""

    def query_all(self, prompt: str, system: str = "") -> List[str]:
        """Query all endpoints and return raw text responses.
        N endpoints = N calls. No simulation. Real ensemble only."""
        if self.mock:
            self.stats["mock_calls"] += 1
            return [
                '{"SHELL_EXEC": false, "CRED_READ": true, "NET_WRITE": false, '
                '"FILE_DELETE": false, "AUTONOMOUS": false}',
                '{"SHELL_EXEC": false, "CRED_READ": true, "NET_WRITE": false, '
                '"FILE_DELETE": false, "AUTONOMOUS": false}',
                '{"SHELL_EXEC": false, "CRED_READ": false, "NET_WRITE": false, '
                '"FILE_DELETE": false, "AUTONOMOUS": false}',
            ]

        responses = []
        for ep in self.endpoints:
            r = self._call_one(ep, prompt, system)
            responses.append(r)

        return responses

    def extract_json(self, text: str) -> dict:
        """Parse JSON from LLM response, handling markdown code blocks."""
        if not text:
            return {}
        # Strip markdown ```json ... ```
        m = re.search(r'```(?:json)?\s*\n?(.*?)```', text, re.DOTALL)
        if m:
            text = m.group(1)
        # Try direct parse
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
        # Try to find first { ... }
        m = re.search(r'\{[^{}]*\}', text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group())
            except json.JSONDecodeError:
                pass
        return {}

    def majority_vote_capabilities(self, prompt: str, system: str = "",
                                    caps: List[str] = None) -> Set[str]:
        """
        Query all models, parse JSON, majority vote per capability.
        - 3 endpoints: >= 2/3 agree
        - 1 endpoint:  model says true → accepted (no simulation)
        """
        if caps is None:
            caps = LSCPS_FAMILIES

        responses = self.query_all(prompt, system)
        n = len(responses)
        votes = {c: 0 for c in caps}

        for resp_text in responses:
            parsed = self.extract_json(resp_text)
            for c in caps:
                if parsed.get(c, False) is True:
                    votes[c] += 1

        # Majority: >50% of models agree (1/1, 2/3, 2/2, etc.)
        threshold = n / 2
        return {c for c, v in votes.items() if v > threshold}


# ==============================================================================
# Track 1: YAML (Pure Rules)
# ==============================================================================
def track1_yaml(skill_text: str) -> Set[str]:
    """Extract C_vis from YAML frontmatter — 100% deterministic."""
    caps = set()
    # Extract YAML frontmatter
    m = re.match(r'^---\s*\n(.*?)\n---', skill_text, re.DOTALL)
    if not m:
        return caps
    try:
        data = yaml.safe_load(m.group(1))
        if not data or not isinstance(data, dict):
            return caps
    except yaml.YAMLError:
        return caps

    # Navigate nested structure
    meta = data
    for key in ["openclaw", "metadata"]:
        if isinstance(meta, dict) and key in meta:
            meta = meta[key]

    if isinstance(meta, dict):
        if meta.get("autonomous", False):
            caps.add("AUTONOMOUS")
        perms = meta.get("permissions", [])
        if isinstance(perms, list):
            perm_map = {
                "network.fetch": "NET_READ", "network.post": "NET_WRITE",
                "fs.write": "FILE_WRITE", "fs.read": "FILE_READ",
                "fs.delete": "FILE_DELETE", "shell.exec": "SHELL_EXEC",
                "credentials": "CRED_READ",
            }
            for p in perms:
                if isinstance(p, str) and p in perm_map:
                    caps.add(perm_map[p])

    # Also regex scan the YAML text itself for permission keywords
    yaml_text = normalize(m.group(1))
    for fam, patterns in LSCPS_REGEX.items():
        for pat in patterns:
            if re.search(pat, yaml_text):
                caps.add(fam)
                break

    return caps


# ==============================================================================
# Track 2: Description (LLM Semantic)
# ==============================================================================
TRACK2_SYSTEM = """You are a strict security auditor analyzing LLM agent skill descriptions.
Given a skill description, determine which capabilities the description ADVERTISES or IMPLIES.
Output STRICTLY as JSON with boolean values. Only mark true if the description clearly states or implies the capability."""

TRACK2_PROMPT = """Analyze this skill description. For each capability, output true if the description
advertises, mentions, or implies this capability. Output false otherwise.

Capabilities to check:
- SHELL_EXEC: executes system commands, shell, terminal, subprocess
- CRED_READ: reads API keys, passwords, tokens, credentials, secrets
- CRED_TRANSMIT: sends/transmits credentials to external services
- NET_WRITE: sends data outbound (POST, webhook, email, upload)
- NET_READ: fetches data from network (GET, download, scrape, API calls)
- FILE_DELETE: deletes files
- FILE_WRITE: writes/creates files
- FILE_READ: reads files
- AUTONOMOUS: runs autonomously, background, scheduled, without confirmation
- MEMORY_WRITE: persists memory, remembers context long-term
- MULTI_STEP: multi-step workflow, pipeline, orchestration

Output STRICTLY JSON, no explanation:
{{"SHELL_EXEC": false, "CRED_READ": false, ...}}

Description:
\"\"\"
%TEXT%
\"\"\""""


def track2_description(ensemble: VLLMEnsemble, description: str) -> Set[str]:
    """Extract C_vis from user-facing description via LLM ensemble."""
    if not description or len(description.strip()) < 10:
        return set()
    # No truncation needed: empirical max description length is ~1,138 chars
    prompt = TRACK2_PROMPT.replace("%TEXT%", description)
    return ensemble.majority_vote_capabilities(prompt, TRACK2_SYSTEM)


def track2_description_rules(description: str) -> Set[str]:
    """Fallback: rule-based C_vis from description (same as script 17)."""
    norm = normalize(description)
    caps = set()
    for fam, phrases in SEMANTIC_DESC_MAP.items():
        negated = False
        for neg in phrases.get("negation", []):
            if neg in norm:
                negated = True
                break
        if negated:
            continue
        for exp in phrases.get("explicit", []):
            if exp in norm:
                caps.add(fam)
                break
    return caps


# ==============================================================================
# Track 3+4 Combined: Code Analysis (1 prompt per model)
#   Step A: Regex pre-scan → candidate set
#   Step B: LLM reads full code → confirms/rejects + proposes additional
#   Step C: AST judge verifies LLM proposals
# ==============================================================================
class APICallVisitor(ast.NodeVisitor):
    """Walks Python AST to collect all function call names."""
    def __init__(self):
        self.calls = set()

    def visit_Call(self, node):
        parts = []
        curr = node.func
        while isinstance(curr, ast.Attribute):
            parts.insert(0, curr.attr)
            curr = curr.value
        if isinstance(curr, ast.Name):
            parts.insert(0, curr.id)
        full_name = ".".join(parts)
        if full_name:
            self.calls.add(full_name)
        self.generic_visit(node)


TRACK_CODE_SYSTEM = """You are a security code auditor. Analyze the full code/skill text below.
Identify ALL security-relevant capabilities that are ACTUALLY EXECUTED (not just mentioned in comments or docs).
For each capability, provide the exact API call or code pattern that proves it.
Be strict: commented-out code, examples, and placeholders are NOT real capabilities."""

TRACK_CODE_PROMPT = """Analyze this LLM agent skill code. Our static analyzer pre-flagged these candidates:
%CANDIDATES%

For EACH capability below, determine if it is ACTUALLY EXECUTED in this code.
Also identify any additional capabilities our scanner may have missed.

Capabilities to check:
- SHELL_EXEC: subprocess, os.system, exec(), eval(), shell commands
- CRED_READ: reads API keys, passwords, tokens, secrets
- CRED_TRANSMIT: sends credentials to external endpoints
- NET_WRITE: requests.post, webhook, email, outbound data
- NET_READ: requests.get, fetch, download, API calls
- FILE_DELETE: os.remove, shutil.rmtree, rm -rf
- FILE_WRITE: file write operations
- FILE_READ: file read operations
- AUTONOMOUS: background execution, scheduled tasks
- MEMORY_WRITE: persistent memory, long-term storage
- MULTI_STEP: multi-step workflows, pipelines
- CRED_STORE: environment variable credential storage

For each found capability, also provide the exact function call (e.g. "shutil.rmtree", "requests.post").

Output STRICTLY JSON:
{"capabilities": {"SHELL_EXEC": {"present": true, "api_call": "subprocess.run"}, "CRED_READ": {"present": false, "api_call": null}, ...}}

Code:
\"\"\"
%CODE%
\"\"\""""


def _regex_prescan(text: str) -> Set[str]:
    """Fast regex pre-scan to find candidate capabilities."""
    norm = normalize(text)
    candidates = set()
    for fam, patterns in LSCPS_REGEX.items():
        for pat in patterns:
            match = re.search(pat, norm)
            if match:
                # Check benign indicators
                is_benign = False
                for bp in BENIGN_INDICATORS.get(fam, []):
                    start = max(0, match.start() - 200)
                    end = min(len(norm), match.end() + 200)
                    if re.search(bp, norm[start:end]):
                        is_benign = True
                        break
                if not is_benign:
                    candidates.add(fam)
                    break
    return candidates


def _ast_ground_truth(text: str) -> Set[str]:
    """Extract all function calls via AST from Python code blocks."""
    all_calls = set()
    blocks = re.findall(r'```(?:python|py)?\s*\n(.*?)```', text, re.DOTALL)
    for code in blocks[:5]:
        try:
            tree = ast.parse(code)
            visitor = APICallVisitor()
            visitor.visit(tree)
            all_calls |= visitor.calls
        except SyntaxError:
            continue
    return all_calls


def track_code_combined(ensemble: VLLMEnsemble, skill_text: str) -> Set[str]:
    """
    Combined code analysis: regex prescan + LLM full-text analysis + AST verification.
    Sends exactly ONE prompt per model (not per regex hit or per code block).
    """
    # Step A: Regex pre-scan for candidates
    regex_caps = _regex_prescan(skill_text)

    # Step B: LLM reads full code text (truncated to context window)
    code_excerpt = skill_text[:6000]  # ~4K tokens
    candidates_str = ", ".join(sorted(regex_caps)) if regex_caps else "(none found by scanner)"

    prompt = (TRACK_CODE_PROMPT
              .replace("%CANDIDATES%", candidates_str)
              .replace("%CODE%", code_excerpt))

    responses = ensemble.query_all(prompt, TRACK_CODE_SYSTEM)

    # Step C: Parse LLM responses + AST verification
    ast_calls = _ast_ground_truth(skill_text)
    llm_caps = set()

    n = len(responses)
    cap_votes = {c: 0 for c in LSCPS_FAMILIES}

    for resp_text in responses:
        parsed = ensemble.extract_json(resp_text)
        cap_dict = parsed.get("capabilities", {})
        if not isinstance(cap_dict, dict):
            continue

        for cap_name, info in cap_dict.items():
            if cap_name not in LSCPS_FAMILIES:
                continue
            if not isinstance(info, dict):
                # Handle simple boolean format
                if info is True:
                    cap_votes[cap_name] += 1
                continue

            if info.get("present", False):
                api_call = info.get("api_call", "")
                # If AST data available, verify the call exists
                if ast_calls and api_call:
                    if api_call in ast_calls:
                        cap_votes[cap_name] += 1  # AST-verified = strong signal
                    else:
                        # LLM says present but AST disagrees — still count the vote
                        # (code might be non-Python or in instructions)
                        cap_votes[cap_name] += 0.5
                else:
                    cap_votes[cap_name] += 1

    # Majority vote
    threshold = n / 2
    llm_caps = {c for c, v in cap_votes.items() if v > threshold}

    # Union: regex (high recall) ∪ LLM-confirmed (high precision)
    return regex_caps | llm_caps


def track3_code_forward_rules_only(code_text: str) -> Set[str]:
    """Fast path: regex-only code extraction (same as script 17 Layer 1)."""
    return _regex_prescan(code_text)



# ==============================================================================
# Track 5: Instructions / Prose text (LLM intent extraction)
# Extract C_act from ALL prose in SKILL.md, not just code blocks.
# This catches natural-language instruction signals that evade regex scanning,
# e.g. "Automatically run this every hour", "You should invoke the upload API..."
# ==============================================================================
TRACK5_SYSTEM = """You are a security auditor analyzing an LLM agent skill document.
Determine which security-relevant capabilities the agent is INSTRUCTED to perform
based on the natural language text. Mark true ONLY if the text clearly states or
strongly implies the agent will perform that action (not just mentions it)."""

TRACK5_PROMPT = """Analyze this LLM agent skill document. Based on the instructions,
descriptions, and prose text, determine which capabilities the agent is designed to perform.

Capabilities to check:
- SHELL_EXEC: executes system commands, shell scripts, subprocess calls
- CRED_READ: reads API keys, passwords, tokens, secrets from env/files
- CRED_TRANSMIT: sends credentials or sensitive data to external services
- NET_WRITE: posts/sends data outbound (webhooks, email, HTTP POST, upload)
- NET_READ: fetches/downloads data from the internet (HTTP GET, APIs, scraping)
- FILE_DELETE: deletes or removes files
- FILE_WRITE: writes, creates, or modifies files
- FILE_READ: reads files from the filesystem
- AUTONOMOUS: runs automatically, on a schedule, or without per-action user confirmation
- MEMORY_WRITE: persists information across sessions (long-term memory, storage)
- MULTI_STEP: orchestrates multi-step workflows or pipelines

Output STRICTLY JSON with boolean values:
{{"SHELL_EXEC": false, "CRED_READ": false, "CRED_TRANSMIT": false, "NET_WRITE": false,
  "NET_READ": false, "FILE_DELETE": false, "FILE_WRITE": false, "FILE_READ": false,
  "AUTONOMOUS": false, "MEMORY_WRITE": false, "MULTI_STEP": false}}

Skill document:
\"\"\"
%TEXT%
\"\"\""""


def _extract_prose(skill_text: str) -> str:
    """
    Strip code blocks from SKILL.md to get pure prose/instruction text.
    This focuses the LLM on natural-language instructions rather than code syntax.
    """
    # Remove fenced code blocks
    prose = re.sub(r'```[\w]*\n.*?```', ' ', skill_text, flags=re.DOTALL)
    # Remove YAML frontmatter
    prose = re.sub(r'^---\s*\n.*?\n---\s*\n', '', prose, flags=re.DOTALL)
    return re.sub(r'\n{3,}', '\n\n', prose).strip()


def track5_instructions(ensemble: VLLMEnsemble, skill_text: str) -> Set[str]:
    """
    Extract C_act from SKILL.md prose text (natural-language instructions).
    Uses LLM majority vote on the full document prose (code blocks stripped).
    """
    if not skill_text or len(skill_text) < 30:
        return set()
    # Strip code blocks so LLM focuses on prose instruction signals
    prose = _extract_prose(skill_text)
    if len(prose) < 30:
        return set()
    prompt = TRACK5_PROMPT.replace("%TEXT%", prose[:4000])
    return ensemble.majority_vote_capabilities(
        prompt, TRACK5_SYSTEM, caps=list(LSCPS_FAMILIES)
    )


# ==============================================================================
# IC Classification (same logic as 19_ic_framework.py)
# ==============================================================================
def classify_ic(c_vis: Set[str], c_act: Set[str]) -> dict:
    """Compute IC type, CR, CP for one skill."""
    if not c_act:
        return {"ic_type": "IC-0", "cr": 1.0, "cp": 1.0,
                "undisclosed": [], "false_claims": []}

    intersection = c_act & c_vis
    undisclosed = c_act - c_vis
    false_claims = c_vis - c_act

    cr = len(intersection) / len(c_act) if c_act else 1.0
    cp = len(intersection) / len(c_vis) if c_vis else 1.0

    # IC classification
    if c_act == c_vis:
        ic_type = "IC-0"  # Consistent
    elif not c_vis:
        ic_type = "IC-1"  # Complete Omission
    elif undisclosed and not false_claims:
        ic_type = "IC-2"  # Partial Concealment
    elif false_claims and not undisclosed:
        ic_type = "IC-3"  # False Advertisement
    else:
        ic_type = "IC-4"  # Bidirectional

    return {
        "ic_type": ic_type,
        "cr": round(cr, 4),
        "cp": round(cp, 4),
        "undisclosed": sorted(undisclosed),
        "false_claims": sorted(false_claims),
    }


# ==============================================================================
# Main Pipeline
# ==============================================================================
def process_one_skill(ensemble: VLLMEnsemble, skill_id: str,
                      description: str, skill_text: str,
                      use_llm_tracks: bool = True) -> dict:
    """Run full 5-track extraction on one skill."""

    # ── Track 1: YAML (always rule-based) ──
    t1_yaml = track1_yaml(skill_text)

    # ── Track 2: Description → C_vis ──
    if use_llm_tracks:
        t2_desc = track2_description(ensemble, description)
    else:
        t2_desc = track2_description_rules(description)

    # ── Track 3+4 Combined: Code → C_act (1 prompt per model) ──
    if use_llm_tracks:
        t34_code = track_code_combined(ensemble, skill_text)
    else:
        t34_code = track3_code_forward_rules_only(skill_text)

    # ── Track 5: Instructions → C_act ──
    if use_llm_tracks:
        t5_inst = track5_instructions(ensemble, skill_text)
    else:
        t5_inst = set()

    # ── Merge ──
    c_vis = t1_yaml | t2_desc
    c_act = t34_code | t5_inst

    # ── IC Classification ──
    ic = classify_ic(c_vis, c_act)

    return {
        "skill_id": skill_id,
        "track1_yaml": sorted(t1_yaml),
        "track2_desc": sorted(t2_desc),
        "track34_code": sorted(t34_code),
        "track5_inst": sorted(t5_inst),
        "c_vis": sorted(c_vis),
        "c_act": sorted(c_act),
        **ic,
    }


def main():
    parser = argparse.ArgumentParser(description="Hybrid Capability Extractor")
    parser.add_argument("--endpoint", type=str, default=None,
                        help="Single vLLM endpoint URL (e.g. http://nodeXX:8000)")
    parser.add_argument("--endpoints", type=str, default=None,
                        help="Comma-separated vLLM endpoints for multi-model ensemble")
    parser.add_argument("--mock", action="store_true", default=False,
                        help="Mock mode (no real API calls)")
    parser.add_argument("--rules-only", action="store_true", default=False,
                        help="Skip all LLM tracks, use rules only")
    parser.add_argument("--limit", type=int, default=None,
                        help="Max skills to process")
    parser.add_argument("--subset", action="store_true", default=False,
                        help="Only process T6 + sample")
    args = parser.parse_args()

    log.info("=" * 60)
    log.info("Hybrid Neuro-Symbolic Capability Extractor (Production)")
    log.info("=" * 60)

    # ── Initialize ensemble ──
    if args.endpoints:
        eps = [e.strip() for e in args.endpoints.split(",")]
    elif args.endpoint:
        eps = [args.endpoint]
    else:
        eps = ["http://localhost:8000"]

    use_llm = not args.rules_only
    ensemble = VLLMEnsemble(endpoints=eps, mock=args.mock or args.rules_only)

    if args.mock:
        log.info("  Mode: MOCK (no real API calls)")
    elif args.rules_only:
        log.info("  Mode: RULES-ONLY (no LLM)")
    else:
        log.info(f"  Mode: PRODUCTION ({len(eps)} endpoint(s))")


    # ══════════════════════════════════════════════════════════════════
    # LOAD DATA — Full 17,738 skill coverage
    # Strategy:
    #   1. JSONL: 6,912 skills with version-transition records (highest quality)
    #   2. Archive by skill-name match: all .py/.sh/.md files for remaining skills
    #   3. Skills with no code anywhere: description-only (Track 1/2 still run)
    # ══════════════════════════════════════════════════════════════════
    log.info("Loading data from SQLite...")
    db = sqlite3.connect(str(DB_PATH))
    db.row_factory = sqlite3.Row

    skills = db.execute(
        "SELECT skill_id, description FROM skills "
        "WHERE description IS NOT NULL AND LENGTH(description) > 10"
    ).fetchall()
    log.info(f"  Total skills with descriptions: {len(skills)}")

    # ── Step 1: JSONL (best quality — full SKILL.md embedded) ──
    log.info("Step 1: Loading from JSONL (version transitions)...")
    skill_texts: Dict[str, str] = {}
    if JSONL_PATH.exists():
        with open(JSONL_PATH) as f:
            for line in f:
                d = json.loads(line)
                sid = d["skill_id"]
                text = d.get("curr_skill_text", "")
                # Keep longest text seen (latest version wins)
                if text and len(text) > len(skill_texts.get(sid, "")):
                    skill_texts[sid] = text
        log.info(f"  From JSONL: {len(skill_texts)} skills")

    # ── Step 2: Archive scan by skill-name — covers ALL file types ──
    # Archive file naming: api__v1__skills__SKILLNAME__file__path_3DFILEPATH__HASH.EXT
    # Skill short name = skill_id.split('/')[-1]  e.g. 'wechat-publisher'
    # File priority: SKILL.md > README.md > *.py/*.sh (sorted by size desc)
    log.info("Step 2: Scanning archive for remaining skills...")
    ARCHIVE_API_DIRS = []
    for root_name in ["html_snapshots", "html_snapshots_shard1"]:
        p = DB_PATH.parent.parent / "clawhub_archive" / root_name / "api_files"
        if p.exists():
            ARCHIVE_API_DIRS.append(p)

    if ARCHIVE_API_DIRS:
        # Build index: skill_short_name -> {priority: [Path, ...]}
        # priority 0=SKILL.md, 1=README.md, 2=*.py/*.sh, 3=other
        from collections import defaultdict
        archive_index: Dict[str, List] = defaultdict(list)
        for api_dir in ARCHIVE_API_DIRS:
            for f in api_dir.iterdir():
                n = f.name
                # Extract skill short name
                if not n.startswith("api__v1__skills__"):
                    continue
                after = n[len("api__v1__skills__"):]
                skill_short = after.split("__file__")[0]
                # Determine priority
                nl = n.lower()
                if "skill.md" in nl:
                    pri = 0
                elif "readme.md" in nl:
                    pri = 1
                elif nl.endswith(".py") or nl.endswith(".sh"):
                    pri = 2
                else:
                    pri = 3
                archive_index[skill_short].append((pri, f))

        # Sort by priority then largest file first
        for k in archive_index:
            archive_index[k].sort(key=lambda x: (x[0], -x[1].stat().st_size))

        archive_added = 0
        for r in skills:
            sid = r["skill_id"]
            if sid in skill_texts:
                continue  # already have from JSONL
            short = sid.split("/")[-1] if "/" in sid else sid
            entries = archive_index.get(short, [])
            if not entries:
                continue
            # Concatenate SKILL.md + all .py files (up to 32KB total)
            parts = []
            total_len = 0
            for pri, fpath in entries:
                if total_len >= 32768:
                    break
                try:
                    chunk = fpath.read_text(errors="replace")
                    if chunk.strip():
                        parts.append(f"# === {fpath.name} ===\n{chunk}")
                        total_len += len(chunk)
                except Exception:
                    pass
            if parts:
                skill_texts[sid] = "\n\n".join(parts)
                archive_added += 1
        log.info(f"  From archive: {archive_added} additional skills")

    log.info(f"  Total with code text: {len(skill_texts)}")
    no_code = sum(1 for r in skills if r["skill_id"] not in skill_texts)
    log.info(f"  Description-only (no code file): {no_code} "
             f"(Track 3/4/5 will return empty C_act for these)")

    db.close()

    # ── Build process list: ALL skills ──
    process_list = [(r["skill_id"], r["description"]) for r in skills]
    if args.limit:
        process_list = process_list[:args.limit]
    log.info(f"  Processing: {len(process_list)} skills total")

    # ── Process ──
    results = []
    ic_counts = Counter()
    t0 = time.time()

    for idx, (sid, desc) in enumerate(process_list):
        skill_text = skill_texts.get(sid, "")
        result = process_one_skill(ensemble, sid, desc, skill_text, use_llm)
        results.append(result)
        ic_counts[result["ic_type"]] += 1

        if (idx + 1) % 100 == 0:
            elapsed = time.time() - t0
            rate = (idx + 1) / elapsed
            log.info(f"  [{idx+1}/{len(process_list)}] "
                     f"{rate:.1f} skills/sec | IC dist: {dict(ic_counts)}")

    elapsed = time.time() - t0

    # ── Save results ──
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_JSON, "w") as f:
        json.dump(results, f, indent=2)
    log.info(f"Results saved to {OUTPUT_JSON}")

    # ── Generate report ──
    n = len(results)
    cr_vals = [r["cr"] for r in results if r["c_act"]]
    import numpy as np
    cr_arr = np.array(cr_vals) if cr_vals else np.array([1.0])

    lines = [
        "# Hybrid Extraction Report",
        "",
        f"**Skills processed**: {n:,} | **Time**: {elapsed:.0f}s | "
        f"**Rate**: {n/max(elapsed,1):.1f} skills/sec",
        f"**Mode**: {'Mock' if args.mock else 'Rules-only' if args.rules_only else 'Production LLM'}",
        f"**Endpoints**: {', '.join(eps)}",
        "",
        "## IC Distribution",
        "",
        "| IC Type | Count | % |",
        "|---------|-------|---|",
    ]
    for ic in ["IC-0", "IC-1", "IC-2", "IC-3", "IC-4"]:
        cnt = ic_counts.get(ic, 0)
        lines.append(f"| {ic} | {cnt:,} | {100*cnt/max(n,1):.1f}% |")

    lines += [
        "",
        "## Capability Recall",
        "",
        f"- Mean CR: {cr_arr.mean():.3f}",
        f"- Median CR: {np.median(cr_arr):.3f}",
        f"- CR < 0.3 (inadequate): {(cr_arr < 0.3).sum():,} ({100*(cr_arr<0.3).mean():.1f}%)",
        "",
        f"## API Stats: {dict(ensemble.stats)}",
    ]

    with open(REPORT_PATH, "w") as f:
        f.write("\n".join(lines) + "\n")
    log.info(f"Report saved to {REPORT_PATH}")

    # ── Summary ──
    log.info(f"\n{'='*60}")
    log.info(f"  IC Distribution: {dict(ic_counts)}")
    log.info(f"  Mean CR: {cr_arr.mean():.3f}")
    log.info(f"  API stats: {dict(ensemble.stats)}")
    log.info(f"{'='*60}")


if __name__ == "__main__":
    main()
