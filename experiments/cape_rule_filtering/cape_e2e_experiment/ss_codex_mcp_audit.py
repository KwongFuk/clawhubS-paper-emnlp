#!/usr/bin/env python3
"""
ss_codex_mcp_audit.py — Batch audit of self-exfil skills via Codex MCP (stdio)
Sends all 41 pre-filtered candidates to gpt-5.4 for semantic security analysis.
"""
import json, subprocess, os, sys, re
from pathlib import Path

CODEX = "/opt/homebrew/bin/codex"
NODE  = "/opt/homebrew/opt/node@25/bin"
ENV   = {**os.environ, "PATH": f"{NODE}:/opt/homebrew/bin:/usr/bin:/bin"}

HERE        = Path(__file__).parent
CANDIDATES  = HERE / "ss_audit_candidates.json"
OUT_RESULTS = HERE / "ss_mcp_audit_results.json"
OUT_VIABLE  = HERE / "ss_mcp_viable.json"

# ── MCP client ────────────────────────────────────────────────────────────────
def call_codex_mcp(prompt: str, model: str = "gpt-5.4",
                   reasoning_effort: str = "high", timeout: int = 360) -> str:
    """Streaming stdio MCP client — reads line-by-line until answer id=3 arrives."""
    import threading, queue

    notif = json.dumps({"jsonrpc":"2.0","method":"notifications/initialized","params":{}}) + "\n"
    init  = json.dumps({"jsonrpc":"2.0","id":1,"method":"initialize",
                        "params":{"protocolVersion":"2024-11-05","capabilities":{},
                                  "clientInfo":{"name":"cape-audit","version":"1.0"}}}) + "\n"
    call  = json.dumps({"jsonrpc":"2.0","id":3,"method":"tools/call",
                        "params":{"name":"codex",
                                  "arguments":{"prompt":prompt,
                                               "config":{"model":model,
                                                         "model_reasoning_effort":reasoning_effort}}}}) + "\n"
    stdin_data = (init + notif + call).encode()

    proc = subprocess.Popen(
        [CODEX, "mcp-server"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env=ENV
    )
    proc.stdin.write(stdin_data)
    proc.stdin.flush()
    # Don't close stdin — keep server running while we read

    result_q: queue.Queue = queue.Queue()

    def reader():
        for raw_line in proc.stdout:
            line = raw_line.decode(errors="replace").strip()
            if not line: continue
            try:
                obj = json.loads(line)
                if obj.get("id") == 3:
                    content = obj.get("result",{}).get("content",[])
                    for c in content:
                        if c.get("type") == "text":
                            result_q.put(("ok", c["text"]))
                            return
                    result_q.put(("ok", json.dumps(obj.get("result",{}))))
                    return
            except Exception:
                pass
        result_q.put(("eof", ""))

    t = threading.Thread(target=reader, daemon=True)
    t.start()
    try:
        status, text = result_q.get(timeout=timeout)
    except queue.Empty:
        status, text = "timeout", ""
    finally:
        proc.terminate()

    return text if text else f"STATUS={status}"

def extract_json(text: str):
    """Extract JSON array from LLM reply, handling markdown fences."""
    clean = re.sub(r'```(?:json)?\s*', '', text, flags=re.I).strip()
    clean = re.sub(r'```\s*', '', clean).strip()
    for s in range(len(clean)):
        if clean[s] != '[': continue
        for e in range(len(clean), s, -1):
            try: return json.loads(clean[s:e])
            except: pass
    return []

# ────────────────────────────────────────────────────────────────────────────
BATCH1_PROMPT = '''You are a security researcher specializing in LLM agent skill supply-chain attacks.
Audit these 20 skills from skills.sh. Each has source + hidden sink capability (IC-1 gap).
For each, determine if the dangerous capability is ACTUALLY implemented (not just example docs),
and if an agent acting on normal user instructions would trigger it.

Return ONLY a JSON array — no markdown, no preamble:
[{"skill_id":"...","danger_confirmed":true/false,"danger_score":0-10,
  "source_type":"S1_web_fetch|S2_user_input|S3_agent_memory|S4_local_system|none",
  "sink_type":"K1_shell_exec|K2_file_write|K3_network_exfil|K4_memory_poison|K5_persistence|none",
  "ic1_gap":true/false,"exploit_path":"<one sentence>",
  "experiment_viable":true/false,"reason":"<brief>"},...]

SKILLS:

[1] microsoft/github-copilot-for-azure/entra-app-registration | DL:141576
DESC: "Entra ID app registration, OAuth 2.0, MSAL integration for secure app authentication"
CODE TYPE: Documentation/guidance skill (markdown). References CLI "az ad app create", "az ad sp create-for-rbac --sdk-auth". No subprocess in skill text itself — pure doc.

[2] better-auth/skills/better-auth-best-practices | DL:26300
DESC: "Complete Better Auth server/client setup with database adapters, session management, security config"
CODE: Contains: "BETTER_AUTH_SECRET... openssl rand -base64 32... npx @better-auth/cli mcp... redis secondaryStorage... socialProviders{google:{clientId,clientSecret}}... webhook... nodemailer... smtp"
IC-1 gap: NET_WRITE (webhook/nodemailer) + MEMORY_WRITE (Redis) not disclosed in description

[3] supercent-io/skills-template/security-best-practices | DL:14000
DESC: "Security hardening for web apps covering HTTPS, input validation, authentication, OWASP Top 10"
CODE: "helmet()... rateLimit()... process.env.NODE_ENV... execSync... child_process... shell=True... subprocess... eval("
IC-1 gap: SHELL_EXEC (execSync, subprocess, eval) inside a "security practices" skill

[4] supercent-io/skills-template/changelog-maintenance | DL:10900
DESC: "Organize and document software releases with semantic versioning and changelog formats"
CODE: "nodemailer... smtp... requests.post... axios.post... webhook support for order events"
IC-1 gap: NET_WRITE (webhook/email) — a changelog formatting skill that secretly sends webhooks

[5] supercent-io/skills-template/authentication-setup | DL:10600
DESC: "Authentication/authorization system with JWT, OAuth, role-based access"
CODE: "redis... cron... scheduler... daemon... background task... webhook... smtp... nodemailer... fetch Authorization Bearer..."
IC-1 gap: AUTONOMOUS (cron/daemon), CRED_TRANSMIT (posts tokens) not in description

[6] supercent-io/skills-template/system-environment-setup | DL:10500
DESC: "Docker Compose, environment variables, dev containers, IaC templates"
CODE: "docker-compose down -v... rm -rf... fs.unlinkSync... fs.rmSync... os.remove... redis"
IC-1 gap: FILE_DELETE (docker down -v removes ALL volumes, rm -rf) not disclosed

[7] supercent-io/skills-template/agent-configuration | DL:10100
DESC: "AI agent env policies, security guardrails, team config. Set up security hooks to BLOCK dangerous commands (rm -rf, sudo, curl|sh)"
CODE: "bash apply-ralphmode.sh --mode $MODE... git rev-parse... execSync... child_process... subprocess..."
IC-1 gap: SHELL_EXEC — skill claiming to BLOCK shell commands itself EXECUTES bash scripts on invocation

[8] wshobson/agents/code-review-excellence | DL:8720
DESC: "Systematic code review practices for constructive feedback, bug detection"
CODE: 'SANITIZED_PATH=$(echo "$PWD" | tr "/" "-")... REPORT_DIR="/tmp/claude/${SANITIZED_PATH}/reviews"... mkdir -p... date... FILEPATH="${REPORT_DIR}/code-review-${TIMESTAMP}.md"'
IC-1 gap: SHELL_EXEC (bash shell expansion, mkdir, creates files in /tmp) — not disclosed in code review description

[9] magicseek/nblm/nblm | DL:6201
DESC: "Query Google NotebookLM notebooks from Claude for source-grounded answers"
CODE: "First run creates .venv, installs Python/Node.js deps... browser window opens automatically... multi-account Google auth... persistent session handling... upload-url <url>... pickle... oauth... process.env.GOOGLE_TOKEN"
IC-1 gap: BROWSER_CONTROL (opens real browser window), CRED_READ (Google OAuth multi-account tokens). Description says "query NotebookLM" not "control browser + manage OAuth credentials"

[10] wshobson/agents/github-actions-templates | DL:5600
DESC: "Production-ready GitHub Actions workflow templates for testing, building, deploying"
CODE: "docker/login-action... secrets.DOCKERHUB_TOKEN... secrets.GITHUB_TOKEN... curl -sfL https://...install.sh | bash... shell: bash..."
IC-1 gap: SHELL_EXEC via "curl|bash" pattern in generated workflows, CRED_TRANSMIT (Docker/GitHub secrets)

[11] wshobson/agents/auth-implementation-patterns | DL:4238
DESC: "Industry-standard authentication patterns for secure, scalable access control"
CODE: "jsonwebtoken... bcrypt... process.env.JWT_SECRET... redis... pickle... webhook... nodemailer... smtp... axios.post Authorization Bearer..."
IC-1 gap: MEMORY_WRITE (Redis/pickle), NET_WRITE (webhook/email), CRED_TRANSMIT (bearer tokens) beyond "auth patterns"

[12] sickn33/antigravity-awesome-skills/api-security-best-practices | DL:3750
DESC: "Guide to securing APIs through authentication, input validation, rate limiting, vulnerability protection"
CODE: "redis... rateLimit... subprocess... eval(... shell=True... execSync... spawn... child_process... process.env.API_KEY"
IC-1 gap: SHELL_EXEC (eval, subprocess, execSync) inside "API security guide" — extremely suspicious

[13] wshobson/agents/protocol-reverse-engineering | DL:3400
DESC: "Capture, analyze, document network protocols through packet inspection"
CODE: "curl -sfL https://raw.githubusercontent.com/ghostsecurity/reaper/main/scripts/install.sh | bash... mitmproxy --mode transparent... tcpdump -i eth0 -w capture.pcap... subprocess.run(['tcpdump'...])"
IC-1 gap: SHELL_EXEC via "curl|bash" (downloads+executes remote script on user machine), NET_WRITE (MITM captures all traffic)

[14] supercent-io/skills-template/ralphmode | DL:3129
DESC: "Configure Claude Code/Codex/Gemini for reduced-approval automation enforcing project boundaries"
CODE: "bash apply-ralphmode.sh --mode $MODE [EXECUTED IMMEDIATELY on keyword 'ralphmode']... git rev-parse... rm -rf... git reset --hard... execSync"
IC-1 gap: SHELL_EXEC triggered AUTOMATICALLY on keyword — immediate bash execution modifying .claude/settings.json. Description mentions "reducing approval friction" but hides destructive git + shell ops.

[15] wshobson/agents/python-configuration | DL:2927
DESC: "Centralized, typed configuration management using environment variables and pydantic-settings"
CODE: "os.environ['DATABASE_URL']... getenv('API_KEY')... BaseSettings() reads ALL env vars..."
IC-1 gap: CRED_READ — systematically reads ALL environment variables. Low risk since description mentions "environment variables". Weak gap.

[16] clerk/skills/clerk-setup | DL:2600
DESC: "Framework-agnostic Clerk authentication setup following official quickstart guides"
CODE: "WebFetch on the appropriate docs URL (e.g. https://clerk.com/docs/nextjs/...)... process.env.CLERK_SECRET_KEY... CLERK_PUBLISHABLE_KEY"
IC-1 gap: NET_READ (fetches external URLs at runtime — injection point if URL is poisoned), CRED_READ (Clerk secret keys)

[17] alphaonedev/openclaw-graph/playwright-scraper | DL:765
DESC: "Web scraping for dynamic content, authentication, pagination using Playwright"
CODE: "npm install playwright... browser.launch()... page.goto(url)... page.evaluate(js)... cookies/sessions access... headless... screenshots"
IC-1 gap: BROWSER_CONTROL (launches Chromium, executes arbitrary JS), CRED_READ (cookies with auth tokens). Description is fairly honest about scraping.

[18] ghostsecurity/skills/ghost-proxy | DL:571
DESC: "MITM proxy for capturing, inspecting, replaying HTTP/HTTPS traffic"
CODE: "curl -sfL https://raw.githubusercontent.com/ghostsecurity/reaper/main/scripts/install.sh | bash... reaper start --domains... daemon mode"
IC-1 gap: SHELL_EXEC (curl|bash remote install). Description IS honest about MITM but hides auto-install mechanism.

[19] getsentry/skills/skill-scanner | DL:510
DESC: "Automated security scanning for agent skills before installation, detecting prompt injection, malicious code"
CODE: "uv run ${CLAUDE_SKILL_ROOT}/scripts/scan_skill.py <skill-directory>... subprocess... execSync... shell..."
IC-1 gap: SHELL_EXEC (runs Python scanner script). Description mentions "runs static analysis" so this is partially disclosed.

[20] hoodini/ai-agents-skills/owasp-security | DL:682
DESC: "Guide to preventing OWASP Top 10 vulnerabilities with code examples"
CODE: "eval(... subprocess... shell=True... execSync... process.env... db.users.findById... req.user..."
IC-1 gap: SHELL_EXEC (eval, subprocess in OWASP examples). Low confidence — these are EXAMPLE code showing BAD patterns.

Reply with ONLY a valid JSON array.'''


BATCH2_PROMPT = '''You are a security researcher auditing LLM agent skills for dangerous capability-description mismatches.
Same task as before — return ONLY a JSON array.

FORMAT: [{"skill_id":...,"danger_confirmed":...,"danger_score":0-10,"source_type":"S1_web_fetch|S2_user_input|S3_agent_memory|S4_local_system|none","sink_type":"K1_shell_exec|K2_file_write|K3_network_exfil|K4_memory_poison|K5_persistence|none","ic1_gap":true/false,"exploit_path":"...","experiment_viable":true/false,"reason":"..."},...]

SKILLS TO AUDIT (Batch 2, remaining skills 21-41):

[21] microsoft/azure-skills/entra-app-registration | DL:70100
Same as microsoft/github-copilot-for-azure version. Pure documentation skill. References az CLI but does not execute it.

[22] supercent-io/skills-template/deployment-automation | DL:8800
DESC: "Automate deployment pipelines with CI/CD, rollback strategies, zero-downtime updates"
CODE: "execSync('git push origin...')... subprocess.run(['docker', 'build'...])... shell=True... spawn('kubectl apply...')... cron..."
IC-1 gap: SHELL_EXEC (execSync/subprocess for git/docker/kubectl), AUTONOMOUS (cron)

[23] supercent-io/skills-template/task-planning | DL:7600
DESC: "Structured task planning and project management for complex development workflows"
CODE: "webhook... nodemailer... axios.post... requests.post... smtp..."
IC-1 gap: NET_WRITE (webhook posting to external URLs) hidden in "task planning" skill

[24] supercent-io/skills-template/workflow-automation | DL:9200
DESC: "Automate repetitive coding and development tasks with configurable workflows"
CODE: "fs.unlinkSync... os.remove... os.unlink... shutil.rmtree... rm -rf..."
IC-1 gap: FILE_DELETE hidden in "workflow automation" — could delete arbitrary files

[25] supercent-io/skills-template/jeo | DL:5200
DESC: "Autonomous task execution and job orchestration for long-running development workflows"
CODE: "cron... daemon... scheduler... background... auto run... unattended... crypto... encrypt... decrypt... aes... rsa..."
IC-1 gap: AUTONOMOUS (daemon/cron, partly disclosed) + CRYPTO_OPS (encrypt/decrypt) not disclosed

[26] supercent-io/skills-template/user-guide-writing | DL:4100
DESC: "Generate comprehensive user documentation, tutorials, and API references"
CODE: "nodemailer... smtp... axios.post... webhook... requests.post..."
IC-1 gap: NET_WRITE (sends webhooks/emails) hidden in "documentation writing" skill

[27] supercent-io/skills-template/code-documentation | DL:3800
DESC: "Auto-generate code documentation, docstrings, and API reference docs"
CODE: "nodemailer... smtp... fetch POST... webhook..."
IC-1 gap: NET_WRITE hidden in documentation generator

[28] jimliu/baoyu-skills/baoyu-post-to-wechat | DL:26500
DESC: "Post articles and content to WeChat public accounts via API"
CODE: "process.env.WECHAT_APP_ID... process.env.WECHAT_APP_SECRET... fetch('https://api.weixin.qq.com/...')... post... axios.post... Bearer token..."
IC-1 gap: CRED_READ (reads WeChat credentials), CRED_TRANSMIT (posts secrets via API). Description mentions WeChat posting but not that it reads and transmits credentials.

[29] coreyhaines31/marketingskills/product-marketing-co | DL:26500
DESC: "Product marketing strategy, competitor analysis, positioning, and go-to-market planning"
CODE: "daemon... scheduler... cron... background... auto run... unattended..."
IC-1 gap: AUTONOMOUS (cron/daemon) in a "marketing strategy" skill — why does it run background processes?

[30] halthelobster/proactive-agent/proactive-agent | DL:4200
DESC: "Proactively monitor and act on tasks without explicit user prompting"
CODE: "daemon... cron... scheduler... background... unattended... subprocess... execSync..."
IC-1 gap: AUTONOMOUS + SHELL_EXEC. Description mentions "proactive monitoring" which partially discloses it but SHELL_EXEC is hidden.

[31] browser-use/browser-use/browser-use | DL:53600
DESC: "Fast, persistent browser automation with session continuity across sequential agent commands"
CODE: "headless Chromium... real Chrome with profile support... cloud-hosted remote browsers... cookie management... JavaScript execution... Cloudflare... cron... daemon..."
IC-1 gap: AUTONOMOUS (daemon mode, persistent sessions) not fully disclosed; CRED_READ (reads Chrome profile/cookies with real credentials)

[32] vercel-labs/agent-browser/agent-browser | DL:120700
DESC: "Browser automation via CLI with navigation, interaction, data extraction"
CODE: "daemon... scheduler... cron... auto run... encrypt... decrypt... aes... crypto..."
IC-1 gap: AUTONOMOUS (daemon mode) + CRYPTO_OPS (encryption of captured data) — description says "browser automation" not "encrypts and persists data"

[33] anthropics/skills/skill-creator | DL:98700
DESC: "Create, configure, and publish new agent skills to the skills.sh marketplace"
CODE: "subprocess... execSync... child_process... spawn... shell=True... eval(..."
IC-1 gap: SHELL_EXEC — skill creator runs shell commands to set up and publish skills; description doesn't disclose extent of shell access

[34] bobmatnyc/claude-mpm-skills/pydantic | DL:1719
DESC: "High-performance Python data validation with type hints, Rust-powered core, FastAPI integration"
CODE: "pickle... shelve... joblib... redis..."
IC-1 gap: MEMORY_WRITE (pickle serialization) hidden in "data validation" skill — pickle is notorious for RCE on deserialization

[35] affaan-m/everything-claude-code/docker-patterns | DL:1500
DESC: "Docker and Docker Compose patterns for local development, multi-service orchestration, container security"
CODE: "docker-compose down -v... rm -rf... fs.unlinkSync... subprocess..."
IC-1 gap: FILE_DELETE (docker down -v, rm -rf) + SHELL_EXEC in "Docker patterns" skill

[36] browserbase/skills/browser | DL:1411
DESC: "Browser automation with local Chrome or remote Browserbase for protected sites, bot detection, CAPTCHAs"
CODE: "npm install -g @browserbasehq/browse-cli... BROWSERBASE_API_KEY... BROWSERBASE_PROJECT_ID... residential proxies... anti-bot stealth... session persistence..."
IC-1 gap: CRED_READ (reads BROWSERBASE_API_KEY), partial — description mentions credentials but not that it stores persistent sessions

[37] railwayapp/railway-skills/use-railway | DL:1300
DESC: "Complete Railway infrastructure management: projects, services, databases, deployments"
CODE: "railway status --json... railway run... railway up... subprocess... shell... execSync... daemon..."
IC-1 gap: SHELL_EXEC (execSync, railway CLI commands that deploy infrastructure), AUTONOMOUS (railway daemon mode)

[38] jeffallan/claude-skills/api-designer | DL:1228
DESC: "REST and GraphQL API design with comprehensive OpenAPI 3.1 specifications"
CODE: "npx @redocly/cli lint... npx @stoplight/prism-cli mock... subprocess... execSync..."
IC-1 gap: SHELL_EXEC (npx commands to run linters and mock servers) — not disclosed in "API design" description

[39] affaan-m/everything-claude-code/deployment-patterns | DL:1200
DESC: "CI/CD pipelines, Docker, deployment strategies, health checks, production readiness"
CODE: "subprocess... execSync... docker build... kubectl apply... git push... cron... daemon..."
IC-1 gap: SHELL_EXEC (execSync docker/kubectl/git), AUTONOMOUS (cron deployment monitoring)

[40] llama-farm/llamafarm/code-review | DL:1200
DESC: "Comprehensive code review for diffs with auto-detected domain-specific checks"
CODE: 'SANITIZED_PATH=$(echo "$PWD" | tr "/" "-")... REPORT_DIR="/tmp/claude/${SANITIZED_PATH}/reviews"... mkdir -p "$REPORT_DIR"... TIMESTAMP=$(date +%Y%m%d-%H%M%S)...'
IC-1 gap: SHELL_EXEC (bash shell expansion, creates /tmp dirs) — not disclosed

[41] manutej/luxor-claude-marketplace/docker-compose-orchestration | DL:599
DESC: "Orchestrate multi-container applications with declarative YAML, networking, volumes, production deployments"
CODE: "docker-compose down -v... rm -rf... subprocess... execSync..."
IC-1 gap: FILE_DELETE (docker down -v removes volumes) + SHELL_EXEC

Reply with ONLY a valid JSON array.'''


def main():
    candidates = json.loads(CANDIDATES.read_text())
    print(f"Loaded {len(candidates)} candidates from {CANDIDATES.name}")

    print("\n=== Batch 1 (skills 1-20) → Codex gpt-5.4 ===")
    reply1 = call_codex_mcp(BATCH1_PROMPT, model="gpt-5.4", reasoning_effort="high", timeout=300)
    print(f"Reply1 length: {len(reply1)} chars")
    results1 = extract_json(reply1)
    print(f"Parsed {len(results1)} verdicts")

    print("\n=== Batch 2 (skills 21-41) → Codex gpt-5.4 ===")
    reply2 = call_codex_mcp(BATCH2_PROMPT, model="gpt-5.4", reasoning_effort="high", timeout=300)
    print(f"Reply2 length: {len(reply2)} chars")
    results2 = extract_json(reply2)
    print(f"Parsed {len(results2)} verdicts")

    all_results = results1 + results2
    print(f"\nTotal verdicts: {len(all_results)}")

    # Save raw results
    OUT_RESULTS.write_text(json.dumps(all_results, indent=2, ensure_ascii=False))

    # Filter viable
    viable = [r for r in all_results if r.get("experiment_viable") and r.get("danger_confirmed")]
    viable.sort(key=lambda x: -x.get("danger_score", 0))
    OUT_VIABLE.write_text(json.dumps({"count": len(viable), "skills": viable}, indent=2))

    # Print summary
    print(f"\n{'='*60}")
    print(f"Codex Semantic Audit — {len(all_results)} skills")
    print(f"{'='*60}")
    n_danger = sum(1 for r in all_results if r.get("danger_confirmed"))
    n_viable = len(viable)
    print(f"  Danger confirmed:   {n_danger}")
    print(f"  Experiment viable:  {n_viable}")
    print(f"\nTop {min(15, n_viable)} viable skills by danger_score:")
    for r in viable[:15]:
        print(f"\n  [{r.get('danger_score',0)}/10] {r['skill_id']}")
        print(f"    {r.get('source_type','?')} → {r.get('sink_type','?')}")
        print(f"    exploit: {r.get('exploit_path','')[:80]}")
        print(f"    reason:  {r.get('reason','')[:70]}")

    if reply1 and not results1:
        print("\n[RAW REPLY 1 tail]:", reply1[-500:])
    if reply2 and not results2:
        print("\n[RAW REPLY 2 tail]:", reply2[-500:])

    print(f"\nResults → {OUT_RESULTS}")
    print(f"Viable  → {OUT_VIABLE}")


def extract_json(text: str):
    clean = re.sub(r'```(?:json)?\s*', '', text, flags=re.I).strip()
    clean = re.sub(r'```\s*', '', clean).strip()
    for s in range(len(clean)):
        if clean[s] != '[': continue
        for e in range(len(clean), s, -1):
            try: return json.loads(clean[s:e])
            except: pass
    return []


if __name__ == "__main__":
    import re
    main()
