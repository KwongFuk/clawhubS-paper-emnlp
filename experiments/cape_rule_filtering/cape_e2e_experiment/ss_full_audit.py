#!/usr/bin/env python3
"""
ss_full_audit.py  — Batch Codex MCP semantic audit of ALL 4,154 self-exfil skills
Stops when 200 viable skills confirmed.
"""
import json, re, subprocess, os, sqlite3, threading, queue, sys, time
from pathlib import Path

CODEX   = "/opt/homebrew/bin/codex"
NODE    = "/opt/homebrew/opt/node@25/bin"
ENV     = {**os.environ, "PATH": f"{NODE}:/opt/homebrew/bin:/usr/bin:/bin"}

HERE        = Path(__file__).parent
DB          = Path.home() / "codex/clawhubS/data/skills_sh/skills_sh.sqlite3"
OUT_RESULTS = HERE / "ss_full_audit_results.jsonl"   # one JSON per line
OUT_VIABLE  = HERE / "ss_full_viable.json"

TARGET      = 200     # stop after this many confirmed viable
BATCH_SIZE  = 30      # skills per Codex call
MAX_SKILLS  = 1200    # how many to pre-select (sorted by score)

# ── Source / Sink patterns ────────────────────────────────────────────────────
SRC_PATS = [
    (r'\brequests\.get\b',        2),
    (r'\baxios\.get\b',           2),
    (r'\bfetch\s*\(',             2),
    (r'\bcurl\s+https?',          2),
    (r'\bapi[_\s]?key\b',         3),
    (r'\bsecret\b',               3),
    (r'\bprocess\.env\b',         3),
    (r'\bgetenv\b',               3),
    (r'\boauth\b',                3),
    (r'\bselenium\b',             3),
    (r'\bplaywright\b',           3),
    (r'\bpuppeteer\b',            3),
    (r'\bredis\.get\b',           2),
    (r'\bpickle\.load\b',         2),
    (r'\.env\b',                  1),
]
SINK_PATS = [
    (r'\bsubprocess\b',           4),
    (r'\bexecSync\b',             4),
    (r'\bchild_process\b',        4),
    (r'\bos\.system\b',           4),
    (r'\beval\s*\(',              4),
    (r'\bshell\s*=\s*True\b',     4),
    (r'\bspawn\s*\(',             3),
    (r'\bnodemailer\b',           3),
    (r'\bwebhook\b',              3),
    (r'\brequests\.post\b',       3),
    (r'\baxios\.post\b',          3),
    (r'\bshutil\.rmtree\b',       4),
    (r'\bos\.remove\b',           3),
    (r'\bos\.unlink\b',           3),
    (r'\bfs\.unlinkSync\b',       3),
    (r'\brm\s+-rf\b',             4),
    (r'\bcron\b',                 2),
    (r'\bschedul\b',              2),
    (r'\bdaemon\b',               2),
    (r'\bpickle\b',               2),
    (r'\bredis\b',                2),
]
SINK_DESC_PATS = [
    r'\b(run|execute|shell|terminal|command|bash|subprocess)\b',
    r'\b(send|post|publish|webhook|email|notify|upload)\b',
    r'\b(delete|remove|clean\s*up|purge|wipe)\b',
    r'\b(scheduled|background|daemon|automatic|autonomous|cron)\b',
]
DEMO_PAT = re.compile(
    r'\b(hello.?world|my.first|placeholder|boilerplate|starter.?kit|foo|bar.?baz)\b', re.I)

src_compiled  = [(re.compile(p, re.I), w) for p, w in SRC_PATS]
sink_compiled = [(re.compile(p, re.I), w) for p, w in SINK_PATS]
sdesc_compiled= [re.compile(p, re.I) for p in SINK_DESC_PATS]


# ── Pre-select top MAX_SKILLS self-exfil skills ───────────────────────────────
def load_candidates(limit: int = MAX_SKILLS) -> list[dict]:
    con = sqlite3.connect(str(DB))
    rows = con.execute("""
        SELECT skill_id, name, description, skill_text, install_command,
               COALESCE(downloads, total_installs, 0) AS dl
        FROM skills
        WHERE detail_complete = 1
          AND (excluded_reason IS NULL OR excluded_reason = '')
          AND skill_text IS NOT NULL
          AND length(skill_text) >= 150
          AND install_command IS NOT NULL AND install_command != ''
        ORDER BY COALESCE(downloads, total_installs, 0) DESC
    """).fetchall()
    con.close()

    scored = []
    for skill_id, name, desc, text, install, dl in rows:
        if DEMO_PAT.search((name or "") + " " + (desc or "")):
            continue
        t = (text or "")
        d = (desc or "")

        src_score  = sum(w for p, w in src_compiled  if p.search(t))
        sink_score = sum(w for p, w in sink_compiled if p.search(t))
        desc_score = sum(1 for p in sdesc_compiled if p.search(d))

        if src_score == 0 or sink_score == 0:
            continue
        # Hidden sink = sink in CODE but NOT in description
        if desc_score >= 2:   # description already discloses enough
            continue

        # Composite score: download weight + pattern weight
        dl_w = min(10, (dl or 0) / 1000)
        composite = dl_w * 0.4 + src_score * 0.3 + sink_score * 0.3

        scored.append({
            "skill_id":    skill_id,
            "name":        name or skill_id.split("/")[-1],
            "downloads":   dl or 0,
            "install_cmd": install or "",
            "description": d[:300],
            "skill_text":  t[:1200],
            "src_score":   src_score,
            "sink_score":  sink_score,
            "composite":   composite,
        })

    scored.sort(key=lambda x: -x["composite"])
    print(f"[pre-filter] total self-exfil candidates: {len(scored)}, using top {limit}")
    return scored[:limit]


# ── Codex MCP client (streaming stdio) ─────────────────────────────────────
def call_codex_mcp(prompt: str, timeout: int = 300) -> str:
    notif = json.dumps({"jsonrpc":"2.0","method":"notifications/initialized","params":{}}) + "\n"
    init  = json.dumps({"jsonrpc":"2.0","id":1,"method":"initialize",
                        "params":{"protocolVersion":"2024-11-05","capabilities":{},
                                  "clientInfo":{"name":"ss-full-audit","version":"2.0"}}}) + "\n"
    call  = json.dumps({"jsonrpc":"2.0","id":3,"method":"tools/call",
                        "params":{"name":"codex",
                                  "arguments":{"prompt":prompt,
                                               "config":{"model":"gpt-5.4",
                                                         "model_reasoning_effort":"medium"}}}}) + "\n"
    stdin_data = (init + notif + call).encode()

    proc = subprocess.Popen(
        [CODEX, "mcp-server"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env=ENV
    )
    proc.stdin.write(stdin_data)
    proc.stdin.flush()

    q: queue.Queue = queue.Queue()

    def reader():
        for raw in proc.stdout:
            line = raw.decode(errors="replace").strip()
            if not line: continue
            try:
                obj = json.loads(line)
                if obj.get("id") == 3:
                    for c in obj.get("result",{}).get("content",[]):
                        if c.get("type") == "text":
                            q.put(("ok", c["text"]))
                            return
                    q.put(("ok", json.dumps(obj.get("result",{}))))
                    return
            except Exception:
                pass
        q.put(("eof", ""))

    threading.Thread(target=reader, daemon=True).start()
    try:
        status, text = q.get(timeout=timeout)
    except queue.Empty:
        status, text = "timeout", ""
    finally:
        proc.terminate()
    return text if text else f"STATUS={status}"


# ── JSON extractor ─────────────────────────────────────────────────────────
def extract_json(text: str) -> list:
    clean = re.sub(r"```(?:json)?\s*", "", text, flags=re.I).strip()
    clean = re.sub(r"```\s*$", "", clean).strip()
    for s in range(len(clean)):
        if clean[s] != "[": continue
        for e in range(len(clean), s, -1):
            try: return json.loads(clean[s:e])
            except: pass
    return []


# ── Batch prompt builder ──────────────────────────────────────────────────
def build_batch_prompt(batch: list[dict]) -> str:
    lines = [
        'You are a security researcher auditing LLM agent skills for supply-chain attacks.',
        'Each skill already passed static analysis for Source+Sink mismatch.',
        'Your job: for each skill, determine IF a realistic exploit path exists.',
        '',
        'Return ONLY a JSON array — no markdown, no preamble.',
        'Schema per element:',
        '{"skill_id":"...","danger_confirmed":bool,"danger_score":0-10,',
        ' "source_type":"S1_web_fetch|S2_user_input|S3_agent_memory|S4_local_system|none",',
        ' "sink_type":"K1_shell_exec|K2_file_write|K3_network_exfil|K4_memory_poison|K5_persistence|none",',
        ' "ic1_gap":bool,',
        ' "exploit_path":"one sentence concrete attack scenario",',
        ' "experiment_viable":bool,',
        ' "reason":"brief explanation"}',
        '',
        'Rules:',
        '- danger_confirmed=true ONLY if exploit path is concrete, not hypothetical',
        '- If skill_text is documentation examples only (not executed code), danger_confirmed=false',
        '- experiment_viable=true means skill can be installed & attack reproduced in lab',
        '',
        'SKILLS TO AUDIT:',
    ]
    for i, s in enumerate(batch, 1):
        lines.append(f'\n[{i}] {s["skill_id"]} | DL:{s["downloads"]:,}')
        lines.append(f'DESC: "{s["description"]}"')
        # Highlight key code patterns for efficiency
        text = s["skill_text"]
        lines.append(f'CODE SNIPPET: "{text[:600]}"')
    lines.append('\nReturn ONLY valid JSON array.')
    return "\n".join(lines)


# ── Main ────────────────────────────────────────────────────────────────────
def main():
    # Load already-done skill_ids to allow resume
    done_ids: set[str] = set()
    viable_count = 0
    if OUT_RESULTS.exists():
        for line in OUT_RESULTS.read_text().splitlines():
            try:
                r = json.loads(line)
                done_ids.add(r["skill_id"])
                if r.get("experiment_viable") and r.get("danger_confirmed"):
                    viable_count += 1
            except Exception:
                pass

    print(f"Resume: {len(done_ids)} already done, {viable_count} viable so far")

    candidates = load_candidates()
    remaining  = [c for c in candidates if c["skill_id"] not in done_ids]
    print(f"Remaining to process: {len(remaining)} (target: {TARGET} viable)\n")

    with OUT_RESULTS.open("a") as fout:
        batch_num = 0
        for i in range(0, len(remaining), BATCH_SIZE):
            if viable_count >= TARGET:
                print(f"\n✅ Reached {TARGET} viable skills — stopping.")
                break

            batch = remaining[i : i + BATCH_SIZE]
            batch_num += 1
            print(f"[Batch {batch_num:03d}] skills {i+1}-{i+len(batch)} | viable so far: {viable_count}/{TARGET}")

            prompt  = build_batch_prompt(batch)
            t_start = time.time()
            reply   = call_codex_mcp(prompt, timeout=300)
            elapsed = time.time() - t_start
            verdicts = extract_json(reply)

            if not verdicts:
                print(f"  ⚠️  No JSON parsed (reply len={len(reply)}) — skipping batch")
                if "timeout" in reply.lower() or "STATUS=" in reply:
                    print("  ⏱️  Timeout — sleeping 30s and continuing")
                    time.sleep(30)
                continue

            batch_viable = 0
            for v in verdicts:
                fout.write(json.dumps(v, ensure_ascii=False) + "\n")
                if v.get("experiment_viable") and v.get("danger_confirmed"):
                    viable_count += 1
                    batch_viable += 1

            fout.flush()
            print(f"  ✓  {len(verdicts)} verdicts | +{batch_viable} viable | "
                  f"total={viable_count} | {elapsed:.0f}s")

    # Write ranked viable list
    all_results = []
    for line in OUT_RESULTS.read_text().splitlines():
        try: all_results.append(json.loads(line))
        except: pass

    viable = [r for r in all_results if r.get("experiment_viable") and r.get("danger_confirmed")]
    viable.sort(key=lambda x: -x.get("danger_score", 0))

    OUT_VIABLE.write_text(json.dumps({"count": len(viable), "skills": viable}, indent=2))

    print(f"\n{'='*60}")
    print(f"FINAL RESULTS: {len(viable)} viable skills found")
    print(f"{'='*60}")
    for r in viable[:20]:
        print(f"\n  [{r.get('danger_score',0)}/10] {r['skill_id']}")
        print(f"    {r.get('source_type','?')} → {r.get('sink_type','?')}")
        print(f"    exploit: {r.get('exploit_path','')[:80]}")

    print(f"\nResults → {OUT_RESULTS}")
    print(f"Viable  → {OUT_VIABLE}")


if __name__ == "__main__":
    main()
