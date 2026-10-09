"""
CAPE Pair Detector v2  —— 两步检测规则
========================================

Rule 1: Channel Check（渠道检查）
  Source 和 Sink 之间是否存在至少一条信息渠道 C1-C14？
  渠道定义：source 写入的数据能到达 sink 读取的位置

Rule 2: When-Trigger Match（触发器匹配）
  流经渠道的数据，攻击者能否控制其内容使之满足 sink 的 when 条件？
  衡量：攻击者对该渠道内容的控制能力 × sink 触发类型的匹配难度

两步均通过 → 确认危险 pair
"""

import json, re, sqlite3
from collections import defaultdict, Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

CAPE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = CAPE_ROOT.parents[1]
RESULTS_DIR = CAPE_ROOT / "results"

# ─────────────────────────────────────────────────────────────────────────────
# 渠道定义（C1-C14）
# ─────────────────────────────────────────────────────────────────────────────

CHANNELS = {
    # id: (name, persistence, attacker_control, source_indicators, sink_indicators)
    "C1": ("LLM Context Window",   "session",    "HIGH",   
           # 任何产生文本输出的 source 都写入 C1
           ["web_fetch","email_receive","audio_capture","screen_capture","file_read_ext","api_call"],
           # 任何读 context 判断 when 的 sink 都从 C1 读
           ["shell_exec","remote_exec","file_delete","file_write","network_send",
            "email_send","db_write","code_commit","privilege_escalate","cloud_upload"]),

    "C2": ("File System",          "persistent", "HIGH",
           ["file_write","code_commit","shell_exec"],     # source 写文件
           ["file_read","shell_exec","remote_exec"]),     # sink 读文件

    "C3": ("Memory System",        "cross-session","MEDIUM",
           ["memory_write","file_write"],                 # source 写记忆
           ["memory_read","context_inject"]),             # sink 读记忆（下次会话）

    "C4": ("Environment/Config",   "process",    "MEDIUM",
           ["env_write","file_write"],                    # source 修改环境
           ["credential_read","config_read","shell_exec"]),

    "C5": ("Database",             "persistent", "HIGH",
           ["db_write"],                                  # source 写 DB
           ["db_read","shell_exec"]),                     # sink 查 DB

    "C6": ("Git Repository",       "persistent", "HIGH",
           ["code_commit","file_write"],                  # source 提交代码
           ["code_read","shell_exec","remote_exec"]),     # sink 读 repo/触发 CI

    "C8": ("System Log",           "host",       "MEDIUM",
           ["shell_exec","file_write"],                   # source 写日志
           ["log_monitor","shell_exec"]),                 # sink 监控日志

    "C9": ("Cron/Scheduler",       "persistent-loop","HIGH",
           ["cron_write","shell_exec"],                   # source 写 crontab
           ["cron_trigger","shell_exec"]),                # 定时触发任何 sink

    "C11": ("Workspace Files",     "persistent", "HIGH",
            ["file_write","code_commit","shell_exec"],    # source 写工作区
            ["file_read","shell_exec"]),                  # sink 读工作区

    "C13": ("Local Network/Socket","realtime",   "HIGH",
            ["network_send","shell_exec"],                # source 发 HTTP
            ["http_server","remote_exec"]),               # sink 本地监听

    "C14": ("Cross-Agent Message", "session",    "MEDIUM",
            ["agent_send","network_send"],                # source 发 agent 消息
            ["agent_receive","shell_exec"]),              # sink 接收
}

# C1（LLM Context）是最通用的渠道——任何 source 输出都进入 context
# 所以所有有 source_cap 的技能都天然共享 C1


def check_channels(source_caps: list, sink_caps: list,
                   source_md: str = "", sink_md: str = "") -> list[dict]:
    """
    Rule 1: 检测 source 和 sink 之间存在哪些信息渠道
    Returns: list of matched channels with details
    """
    matched = []

    # 将 source/sink caps 扩展为包含 MD 里描述的隐含能力
    src_caps_full = set(source_caps)
    snk_caps_full = set(sink_caps)

    # 从 SKILL.md 文本挖掘隐含能力关键词
    src_text = source_md.lower()
    snk_text = sink_md.lower()

    # 隐含能力检测（从 SKILL.md 文本）
    if any(k in src_text for k in ['write to file','save file','create file','write_file']):
        src_caps_full.add('file_write')
    if any(k in src_text for k in ['crontab','cron job','schedule','systemd timer']):
        src_caps_full.add('cron_write')
    if any(k in src_text for k in ['git commit','git push','commit to']):
        src_caps_full.add('code_commit')
    if any(k in src_text for k in ['memory','remember','store','persist','recall']):
        src_caps_full.add('memory_write')

    for ch_id, (name, persistence, ctrl, src_indicators, snk_indicators) in CHANNELS.items():
        # source 有写入该渠道的能力
        src_has = any(ind in src_caps_full for ind in src_indicators)
        # sink 有读取该渠道的能力
        snk_has = any(ind in snk_caps_full for ind in snk_indicators)

        # C1 特殊处理：只要 source 有任何 source_cap 就能写入 C1
        if ch_id == "C1" and source_caps:
            src_has = True

        if src_has and snk_has:
            matched.append({
                "channel_id": ch_id,
                "channel_name": name,
                "persistence": persistence,
                "attacker_control": ctrl,
            })

    return matched


# ─────────────────────────────────────────────────────────────────────────────
# When-field extraction（同前）
# ─────────────────────────────────────────────────────────────────────────────

WHEN_RE = [
    r'(?:Use\s+(?:this\s+skill\s+)?when|trigger\s+(?:this\s+)?when|invoke\s+when)\s*[:\-]?\s*(.{10,500})',
    r'(?:Auto-?trigger|Automatically)\s+when\s*[:\-]?\s*(.{10,200})',
    r'Triggers?\s*[:\-]\s*(.{10,300})',
    r'Natural[-_ ]language\s+triggers?\s*[:\-]\s*(.{10,400})',
]
SEC_HDR = re.compile(r'^#{1,4}\s+(?:When\s+to\s+use|Use\s+when|When\b|Triggers?)', re.I)

def extract_when(text: str) -> str:
    found, buf, in_sec = [], [], False
    for line in text.split('\n'):
        s = line.strip()
        if SEC_HDR.match(s):
            if buf: found.append(' '.join(buf))
            in_sec, buf = True, []; continue
        if in_sec:
            if s.startswith('#'): 
                if buf: found.append(' '.join(buf))
                in_sec, buf = False, []
            elif s: buf.append(s)
        for pat in WHEN_RE:
            m = re.search(pat, line, re.I)
            if m and len(m.group(1)) > 10:
                found.append(m.group(1)[:300])
    if buf: found.append(' '.join(buf))
    return ' | '.join(found)[:1000]


def classify_trigger(when_text: str) -> str:
    t = when_text.lower()
    if any(k in t for k in ['proactively','automatically','silently','do not ask',
                              'without being asked','always run','new task','every time']):
        return 'T4_AUTONOMOUS'
    if re.search(r'["\'`]([a-z][a-z ]{2,30})["\'\`]|/[a-z][a-z\-]+\b', t):
        return 'T1_VERBATIM'
    if re.search(r'\b(email|webpage|document|notification|alert|message|article)\b', t):
        return 'T2_CONTENT_TYPE'
    return 'T3_SITUATIONAL'


# ─────────────────────────────────────────────────────────────────────────────
# Rule 2: When-Trigger Match（当满足渠道条件后）
# ─────────────────────────────────────────────────────────────────────────────

def check_when_trigger(channels: list[dict], source_caps: list, source_name: str,
                       source_desc: str, source_md: str,
                       sink_when: str, sink_trigger_type: str,
                       sink_name: str) -> dict:
    """
    Rule 2: 给定已确认的渠道，攻击者能否控制渠道内容来满足 sink 的 when 条件？

    Returns: {
        score: 0-1,  # 触发成功概率上界
        easiest_channel: ...,
        rationale: ...,
        min_viable_injection: ...,
    }
    """
    if not channels:
        return {"score": 0.0, "rationale": "No shared channel"}

    # T4: 无需任何内容，自动触发 → 只要渠道存在即攻击成功
    if sink_trigger_type == 'T4_AUTONOMOUS':
        return {
            "score": 0.95,
            "easiest_channel": channels[0]["channel_id"],
            "rationale": "T4: sink auto-activates, co-installation on any channel is sufficient",
            "min_viable_injection": "(none — autonomous trigger)",
        }

    # 按渠道评估攻击者的内容控制能力
    # 渠道控制能力 × sink 触发难度
    channel_scores = []
    for ch in channels:
        ctrl = ch["attacker_control"]  # HIGH / MEDIUM / LOW
        base = {"HIGH": 0.90, "MEDIUM": 0.60, "LOW": 0.30}[ctrl]

        # Sink 触发类型调整
        if sink_trigger_type == 'T1_VERBATIM':
            # 只需把固定字符串放进渠道内容，容易
            score = base * 1.0
            mvi = _extract_verbatim_phrase(sink_when)
        elif sink_trigger_type == 'T2_CONTENT_TYPE':
            # 需要内容格式匹配，需要模拟特定文档类型
            score = base * 0.85
            mvi = _gen_content_wrap(sink_when, source_name)
        else:  # T3_SITUATIONAL
            # 需要构造语义上匹配的场景描述
            # C1（LLM Context）最灵活，直接注入叙述即可
            if ch["channel_id"] == "C1":
                score = base * 0.90
            elif ch["channel_id"] in ("C2","C6","C11"):  # 文件/Git/工作区
                score = base * 0.80  # 可以嵌在文件内容或 commit message 里
            elif ch["channel_id"] == "C3":  # 记忆系统
                score = base * 0.85  # 可以把场景描述写进记忆
            else:
                score = base * 0.70
            mvi = _gen_situational_mvi(sink_when)

        channel_scores.append((score, ch, mvi))

    # 取最高分渠道
    best_score, best_ch, best_mvi = max(channel_scores, key=lambda x: x[0])

    return {
        "score": round(best_score, 3),
        "easiest_channel": best_ch["channel_id"],
        "channel_name": best_ch["channel_name"],
        "channel_persistence": best_ch["persistence"],
        "attacker_control": best_ch["attacker_control"],
        "trigger_type": sink_trigger_type,
        "min_viable_injection": best_mvi,
        "rationale": (
            f"{sink_trigger_type} via {best_ch['channel_name']} "
            f"(ctrl={best_ch['attacker_control']}, persist={best_ch['persistence']})"
        ),
        "all_channels": [f"{c['channel_id']}:{c['channel_name']}" for c in channels],
    }


def _extract_verbatim_phrase(when_text: str) -> str:
    exact = re.findall(r'"([^"]{3,40})"', when_text)
    if exact: return f'embed verbatim: "{exact[0]}"'
    cmds = re.findall(r'(/[a-z][a-z\-]+)', when_text)
    if cmds: return f'include command: {cmds[0]}'
    return f'embed phrase: "{when_text[:40]}"'


def _gen_content_wrap(when_text: str, source_name: str) -> str:
    t = when_text.lower()
    if 'email' in t: return 'Craft email with payload in body'
    if 'webpage' in t or 'web page' in t: return 'Host webpage with payload in article text'
    if 'document' in t: return 'Create document with payload in content'
    return f'Wrap payload as expected content type from "{source_name}"'


def _gen_situational_mvi(when_text: str) -> str:
    # 找出 when 条件里的核心动词/名词
    m = re.search(r'(?:user (?:asks?|says?|wants?|mentions?))\s+(.{5,40})', when_text, re.I)
    if m: return f'Mimic: "{m.group(1)}"'
    m = re.search(r'(?:when|if)\s+(.{5,50}?)(?:\.|,|;|$)', when_text, re.I)
    if m: return f'Simulate scenario: "{m.group(1).strip()}"'
    return f'LLM-generate narrative matching: "{when_text[:60]}"'


# ─────────────────────────────────────────────────────────────────────────────
# MAIN ANALYSIS
# ─────────────────────────────────────────────────────────────────────────────

def main():
    con = sqlite3.connect(REPO_ROOT / "data/experiment1_split/merged.sqlite3")
    con.row_factory = sqlite3.Row

    profiles = {json.loads(l)['skill_id']: json.loads(l)
                for l in (RESULTS_DIR / "cape_rq2_profiles.jsonl").open()}
    sources = {sid: p for sid, p in profiles.items() if p.get('source_caps')}

    adj    = [json.loads(l) for l in (RESULTS_DIR / "full_adjudication_codex.jsonl").open()]
    sinks  = [r for r in adj if r['verdict'] in ('CRITICAL','CONFIRMED')]

    print(f"Sources: {len(sources)}  |  Confirmed sinks: {len(sinks)}")
    print("Building sink index...")

    # 预处理 sink：读 SKILL.md，提取 when 字段、sink_caps
    sink_data = []
    for r in sinks:
        frow = con.execute(
            "SELECT text_path FROM skill_files WHERE skill_id=? AND path='SKILL.md' LIMIT 1",
            (r['skill_id'],)).fetchone()
        sink_md = ''
        if frow and frow['text_path']:
            try: sink_md = open(frow['text_path']).read()
            except: pass
        when = extract_when(sink_md) or r.get('exploitable_trigger', '')
        sink_data.append({
            **r,
            'sink_md': sink_md,
            'when_spec': when,
            'trigger_type': classify_trigger(when),
        })

    print(f"Trigger type dist: {Counter(s['trigger_type'] for s in sink_data)}")

    # 预处理 source：读基本信息 + SKILL.md
    src_rows = {r['skill_id']: dict(r) for r in con.execute(
        "SELECT skill_id, name, downloads, description FROM skills WHERE skill_id IN ({})".format(
            ','.join('?'*min(len(sources),999))),
        list(sources.keys())[:999]).fetchall()}

    # 分析每个 (source, sink) 对
    print("Analyzing all pairs (Rule 1 + Rule 2)...")
    out_pairs = []
    stats = Counter()

    for i, (src_id, src_profile) in enumerate(sources.items()):
        if i % 1000 == 0: print(f"  {i}/{len(sources)}...", flush=True)

        src_info = src_rows.get(src_id, {})
        src_name = src_info.get('name', src_id)
        src_dl   = src_info.get('downloads', 0)
        src_desc = src_info.get('description', '')

        frow = con.execute(
            "SELECT text_path FROM skill_files WHERE skill_id=? AND path='SKILL.md' LIMIT 1",
            (src_id,)).fetchone()
        src_md = ''
        if frow and frow['text_path']:
            try: src_md = open(frow['text_path']).read()[:3000]
            except: pass

        src_caps = src_profile.get('source_caps', [])

        for sink in sink_data:
            # ── Rule 1: Channel Check ──────────────────────────────────
            channels = check_channels(
                src_caps, sink['sink_caps'], src_md, sink['sink_md'])

            if not channels:
                stats['rule1_fail'] += 1
                continue

            # ── Rule 2: When-Trigger Match ─────────────────────────────
            result = check_when_trigger(
                channels, src_caps, src_name, src_desc, src_md,
                sink['when_spec'], sink['trigger_type'], sink['name'])

            threshold = 0.70  # 触发成功概率下界
            if result['score'] < threshold:
                stats['rule2_fail'] += 1
                continue

            stats['confirmed_pair'] += 1
            out_pairs.append({
                'source_id':   src_id,
                'source_name': src_name,
                'source_dl':   src_dl,
                'source_caps': src_caps,
                'sink_id':     sink['skill_id'],
                'sink_name':   sink['name'],
                'sink_dl':     sink['downloads'],
                'sink_verdict':sink['verdict'],
                'sink_caps':   sink['sink_caps'],
                'sink_trigger_type': sink['trigger_type'],
                'sink_when':   sink['when_spec'][:150],
                'channels':    result.get('all_channels', []),
                'easiest_channel': result.get('easiest_channel'),
                'channel_name':    result.get('channel_name'),
                'channel_persistence': result.get('channel_persistence'),
                'trigger_score':   result['score'],
                'min_viable_injection': result.get('min_viable_injection',''),
                'rationale':   result.get('rationale',''),
                'combined_risk': round(
                    result['score']
                    * (1.5 if sink['verdict']=='CRITICAL' else 1.0)
                    * (1 + min(src_dl, sink['downloads']) / 200000), 4),
            })

    out_pairs.sort(key=lambda x: x['combined_risk'], reverse=True)

    # Save
    OUT = RESULTS_DIR / "v2_pair_detection.jsonl"
    with OUT.open('w') as f:
        for p in out_pairs: f.write(json.dumps(p) + '\n')

    print(f"\n{'='*65}")
    print("V2 PAIR DETECTION RESULTS")
    print(f"{'='*65}")
    print(f"  Rule 1 (channel) pass:   {stats['confirmed_pair']+stats['rule2_fail']:>7,}")
    print(f"  Rule 1 (channel) fail:   {stats['rule1_fail']:>7,}")
    print(f"  Rule 2 (trigger) pass:   {stats['confirmed_pair']:>7,}  ← confirmed pairs")
    print(f"  Rule 2 (trigger) fail:   {stats['rule2_fail']:>7,}")
    print(f"\n  Total confirmed pairs:   {len(out_pairs):>7,}")
    print(f"  Unique sources:          {len({p['source_id'] for p in out_pairs}):>7,}")
    print(f"  Unique sinks:            {len({p['sink_id'] for p in out_pairs}):>7,}")

    print(f"\n  Channel distribution (easiest channel per pair):")
    ch_dist = Counter(p['easiest_channel'] for p in out_pairs)
    for ch, n in ch_dist.most_common():
        ch_name = CHANNELS.get(ch, ('?',))[0]
        print(f"    {ch}: {ch_name:<25} {n:>7,} pairs ({100*n//len(out_pairs):2d}%)")

    print(f"\n  Trigger type distribution:")
    tt_dist = Counter(p['sink_trigger_type'] for p in out_pairs)
    for tt, n in tt_dist.most_common():
        print(f"    {tt:<20} {n:>7,} pairs")

    print(f"\n  Top 10 pairs by combined risk:")
    for p in out_pairs[:10]:
        print(f"  [{p['sink_verdict'][:4]}] score={p['trigger_score']:.2f} "
              f"ch={p['easiest_channel']} ({p['channel_persistence']})")
        print(f"    SOURCE: {p['source_name'][:35]} ({p['source_dl']:,} DL)")
        print(f"    SINK:   {p['sink_name'][:35]} [{p['sink_trigger_type']}]")
        print(f"    WHEN:   \"{p['sink_when'][:60]}\"")
        print(f"    MVI:    {p['min_viable_injection'][:70]}")
        print()

    con.close()

if __name__ == '__main__':
    main()
