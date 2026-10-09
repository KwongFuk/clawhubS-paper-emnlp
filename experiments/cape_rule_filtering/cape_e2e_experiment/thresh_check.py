#!/usr/bin/env python3
"""Threshold check: how many self-exfil candidates at different download floors."""
import sqlite3, re
from pathlib import Path

DB = Path.home() / "codex/clawhubS/data/skills_sh/skills_sh.sqlite3"
con = sqlite3.connect(str(DB))

DEMO_PAT = re.compile(r'\b(hello.?world|my.first|placeholder|boilerplate|starter.?kit)\b', re.I)

SRC = [re.compile(p, re.I) for p in [
    r'\brequests\.get\b', r'\bfetch\s*\(', r'\bcurl\s+https?', r'\baxios\.get\b',
    r'\btavily\b', r'\bapi[_\s]?key\b', r'\bsecret\b', r'\baccess[_\s]?token\b',
    r'\boauth\b', r'\.env\b', r'\bprocess\.env\b', r'\bgetenv\b',
    r'\bpickle\.load\b', r'\bredis\.get\b', r'\bselenium\b', r'\bplaywright\b',
]]
SINK_CODE = [re.compile(p, re.I) for p in [
    r'\bsubprocess\b', r'\bos\.system\b', r'\beval\s*\(', r'\bshell\s*=\s*True\b',
    r'\bchild_process\b', r'\bexecSync\b', r'\bspawn\s*\(',
    r'\brequests\.post\b', r'\bwebhook\b', r'\baxios\.post\b', r'\bnodemailer\b',
    r'\bos\.remove\b', r'\bos\.unlink\b', r'\bshutil\.rmtree\b', r'\brm\s+-rf\b',
    r'\bfs\.unlinkSync\b', r'\bpickle\b', r'\bredis\b', r'\bcron\b', r'\bschedul\b',
    r'\bdaemon\b',
]]
SINK_DESC = [re.compile(p, re.I) for p in [
    r'\b(run|execute|shell|terminal|command|bash|subprocess)\b',
    r'\b(send|post|publish|webhook|email|notify|upload)\b',
    r'\b(delete|remove|clean\s*up|purge|wipe)\b',
    r'\b(scheduled|background|daemon|automatic|autonomous|cron)\b',
]]

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

for dl_min in [500, 200, 100, 50, 10, 0]:
    cnt = 0
    for skill_id, name, desc, text, install, dl in rows:
        if (dl or 0) < dl_min:
            continue
        if DEMO_PAT.search((name or "") + " " + (desc or "")):
            continue
        has_src = any(p.search(text or "") for p in SRC)
        has_sink_code = any(p.search(text or "") for p in SINK_CODE)
        has_sink_desc = any(p.search(desc or "") for p in SINK_DESC)
        if has_src and has_sink_code and not has_sink_desc:
            cnt += 1
    print(f"  dl >= {dl_min:4d} → {cnt:5d} candidates")
