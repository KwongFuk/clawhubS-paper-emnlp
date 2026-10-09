# Description Importance Experiment — OpenClaw Real Routing

**Router**: openclaw infer model run (gpt-5.3-codex, Codex OAuth)  |  **Skills**: 22  |  **Total decisions**: 528

> **Setup**: Same SKILL.md body for all variants. Only the description text in the routing prompt changes.

## Routing Recall@1 (positive queries — should trigger)

| Description Variant | LLM Recall@1 | Embed Recall@1 |
|--------------------|-------------|----------------|
| **original** | 94.3% | N/A |
| **vague** | 94.3% | N/A |
| **aligned** | 93.2% | N/A |

## False-Trigger Rate (boundary + negative — should NOT trigger)

| Description Variant | LLM FTR | Embed FTR |
|--------------------|---------|-----------|
| **original** | 1.1% | N/A |
| **vague** | 1.1% | N/A |
| **aligned** | 1.1% | N/A |

## Description Sensitivity (Δ% when swapping description, body constant)

| Comparison | LLM Δ% | Embed Δ% |
|-----------|--------|---------|
| original → vague   | 0.0% | None% |
| original → aligned | 1.1% | None% |

## Key Interpretation

- If `aligned recall > original > vague` → **description text drives routing**
- If `aligned FTR < original < vague` → **better descriptions reduce false triggers**
- High sensitivity score → **swapping description significantly changes routing outcomes**

## Skills in Experiment

| Skill | Source | Original Description (truncated) | Aligned Description (truncated) |
|------|--------|--------------------------------|--------------------------------|
| `bear-notes` | openclaw-bundled | Create, search, and manage Bear notes via grizzly CLI.… | Use this skill to manage Bear notes on macOS through th… |
| `blogwatcher` | openclaw-workspace | Monitor blogs and RSS/Atom feeds for updates using the … | Use this skill when the user wants to track RSS/Atom bl… |
| `coding-agent` | openclaw-bundled | Delegate coding tasks to Codex, Claude Code, or Pi agen… | Use this skill to delegate substantial coding work to a… |
| `gh-issues` | openclaw-bundled | Fetch GitHub issues, spawn sub-agents to implement fixe… | Use this skill to orchestrate an end-to-end GitHub issu… |
| `github` | openclaw-bundled | GitHub operations via `gh` CLI: issues, PRs, CI runs, c… | Use this skill for day-to-day GitHub CLI work with `gh`… |
| `healthcheck` | openclaw-bundled | Host security hardening and risk-tolerance configuratio… | Use this skill to assess and harden the host running Op… |
| `mcporter` | openclaw-bundled | Use the mcporter CLI to list, configure, auth, and call… | Use this skill when the user wants direct MCP operation… |
| `node-connect` | openclaw-bundled | Diagnose OpenClaw node connection and pairing failures … | Use this skill to troubleshoot OpenClaw companion app c… |
| `notion` | openclaw-bundled | Notion API for creating and managing pages, databases, … | Use this skill when the user needs direct Notion API wo… |
| `skill-creator` | openclaw-bundled | Create, edit, improve, or audit AgentSkills. Use when c… | Use this skill when the user wants to create a new Agen… |
| `taskflow` | openclaw-bundled | Use when work should span one or more detached tasks bu… | Use this skill when a job must persist across multiple … |
| `taskflow-inbox-triage` | openclaw-bundled | Example TaskFlow authoring pattern for inbox triage. Us… | Use this skill as a concrete TaskFlow pattern for routi… |
| `tmux` | openclaw-bundled | Remote-control tmux sessions for interactive CLIs by se… | Use this skill to control existing tmux sessions on mac… |
| `weather` | openclaw-bundled | Get current weather and forecasts via wttr.in or Open-M… | Use this skill to fetch current weather and short-term … |
| `desktop-control` | openclaw-workspace | Advanced desktop automation with mouse, keyboard, and s… | Use this skill when a task requires direct GUI automati… |
| `Electron` | openclaw-workspace | Build Electron desktop apps with secure architecture an… | Use this skill when designing or reviewing Electron app… |
| `git-helper` | openclaw-workspace | Common git operations as a skill (status, pull, push, b… | Use this skill for routine repository operations throug… |
| `memory-tiering` | openclaw-workspace | Automated multi-tiered memory management (HOT, WARM, CO… | Use this skill to reorganize agent memory across three … |
| `moltguard` | openclaw-workspace | MoltGuard — OpenClaw security guard by OpenGuardrails. … | Use this skill to install and verify the MoltGuard Open… |
| `openclaw-backup` | openclaw-workspace | Backup and restore OpenClaw data. Use when user asks to… | Use this skill to create and restore OpenClaw backups f… |
| `tavily-search` | openclaw-workspace | Web search via Tavily API (alternative to Brave). Use w… | Use this skill to run web searches through Tavily via t… |
| `uptime-kuma` | openclaw-workspace | Interact with Uptime Kuma monitoring server. Use for ch… | Use this skill when you need to manage an Uptime Kuma i… |

*Router: openclaw infer model run (openai-codex/gpt-5.3-codex) — no OPENAI_API_KEY required*