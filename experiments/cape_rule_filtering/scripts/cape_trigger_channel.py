"""
CAPE Trigger-Channel Analysis
==============================
New pair detection method based on:
  1. SOURCE OUTPUT TYPE  — what kind of content can source produce?
  2. SINK TRIGGER SPEC   — what text in context activates sink?
  3. TRIGGER COMPATIBILITY — can source output plausibly contain trigger-matching text?

This upgrades from generic capability channels (A writes → B reads)
to semantic trigger channels (A's output CAN CONTAIN text that activates B).

Output Hijackability Score (OHS) = P(source output matches sink trigger)
"""
import json, re, sqlite3
from collections import defaultdict, Counter
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

CAPE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = CAPE_ROOT.parents[1]
RESULTS_DIR = CAPE_ROOT / "results"

DB_PATH  = REPO_ROOT / "data/experiment1_split/merged.sqlite3"
PROFILES = RESULTS_DIR / "cape_rq2_profiles.jsonl"
SINKS    = RESULTS_DIR / "full_adjudication_codex.jsonl"
OUT_FILE = RESULTS_DIR / "trigger_channel_pairs.jsonl"
REPORT   = RESULTS_DIR / "trigger_channel_report.json"


# ─── 1. SOURCE OUTPUT TYPE CLASSIFICATION ────────────────────────────────────
# The key question: what kind of text does this source bring into LLM context?

SOURCE_OUTPUT_TYPES = {
    # free-text — can contain arbitrary natural language
    'FREE_TEXT': {
        'caps': ['web_fetch'],   # matched against source_caps
        'keywords': ['search','web','browse','fetch','url','http','scrape','crawl',
                     'news','rss','feed','blog','article','page','content','email',
                     'message','inbox','gmail','outlook','telegram','slack','discord',
                     'feishu','whatsapp','document','pdf','wiki','notion','obsidian',
                     'twitter','x.com','reddit','social','forum','news'],
        'hijackability': 1.0,  # any text can appear, any trigger matchable
        'description': 'Arbitrary natural language (web pages, emails, docs)',
    },
    # structured data — JSON/CSV/tabular, harder to embed free text
    'STRUCTURED_DATA': {
        'caps': [],
        'keywords': ['stock','price','sensor','weather','iot','metric','database',
                     'csv','json','api response','financial','market','ticker',
                     'temperature','humidity','status code','numeric'],
        'hijackability': 0.3,  # attacker can put text in string fields/comments
        'description': 'Structured data (JSON/CSV/metrics) — limited free-text injection',
    },
    # code output — terminal output, logs, stack traces
    'CODE_OUTPUT': {
        'caps': [],
        'keywords': ['github','git','repo','commit','diff','issue','pr','code',
                     'terminal','command output','log','trace','error message',
                     'stdout','stderr','test result','lint','build'],
        'hijackability': 0.7,  # code comments / commit messages can contain anything
        'description': 'Code artifacts (commit messages, logs, error output)',
    },
    # multimedia — image/audio, almost no text injection
    'MULTIMEDIA': {
        'caps': ['screen_capture','audio_capture'],
        'keywords': ['image','screenshot','photo','video','audio','voice','recording'],
        'hijackability': 0.1,
        'description': 'Multimedia (image/audio) — very limited text injection',
    },
}

def classify_source_output(skill_id: str, source_caps: list, name: str, desc: str,
                             skill_md: str = '') -> dict:
    text = (name + ' ' + (desc or '') + ' ' + (skill_md or '')[:500]).lower()
    
    # check multimedia first (most restrictive)
    if any(c in source_caps for c in ['screen_capture', 'audio_capture']):
        return {**SOURCE_OUTPUT_TYPES['MULTIMEDIA'], 'type': 'MULTIMEDIA'}
    
    # check code output
    if any(k in text for k in SOURCE_OUTPUT_TYPES['CODE_OUTPUT']['keywords']):
        return {**SOURCE_OUTPUT_TYPES['CODE_OUTPUT'], 'type': 'CODE_OUTPUT'}
    
    # check structured data
    if any(k in text for k in SOURCE_OUTPUT_TYPES['STRUCTURED_DATA']['keywords']) and \
       not any(k in text for k in SOURCE_OUTPUT_TYPES['FREE_TEXT']['keywords']):
        return {**SOURCE_OUTPUT_TYPES['STRUCTURED_DATA'], 'type': 'STRUCTURED_DATA'}
    
    # default: free text (most web/email/doc sources)
    return {**SOURCE_OUTPUT_TYPES['FREE_TEXT'], 'type': 'FREE_TEXT'}


# ─── 2. SINK TRIGGER SPEC EXTRACTION ─────────────────────────────────────────

WHEN_PATTERNS_INLINE = [
    r'(?:Use\s+(?:this\s+skill\s+)?when|trigger\s+(?:this\s+)?when|invoke\s+when|activate\s+when)\s*[:\-]?\s*(.{10,500})',
    r'(?:Use\s+PROACTIVELY|Auto-?trigger)\s+when\s*[:\-]?\s*(.{10,300})',
    r'Triggers?\s*[:\-]\s*(.{10,300})',
    r'Natural[-_ ]language\s+triggers?\s*[:\-]\s*(.{10,400})',
]
SECTION_HDR = re.compile(
    r'^#{1,4}\s+(?:When\s+to\s+use|Use\s+when|When\b|Triggers?|Activation)', re.I)

def extract_when_spec(content: str) -> str:
    """Extract the full 'when to use' specification from SKILL.md"""
    found = []
    lines = content.split('\n')
    in_sec, buf = False, []

    for line in lines:
        s = line.strip()
        if SECTION_HDR.match(s):
            if buf: found.append(' '.join(buf))
            in_sec, buf = True, []
            continue
        if in_sec:
            if s.startswith('#') and not s.startswith('## '):
                if buf: found.append(' '.join(buf))
                in_sec, buf = False, []
            elif s: buf.append(s)
        for pat in WHEN_PATTERNS_INLINE:
            m = re.search(pat, line, re.I)
            if m and len(m.group(1)) > 10:
                found.append(m.group(1)[:300])
    if buf: found.append(' '.join(buf))
    return ' | '.join(found)[:1000]


# ─── 3. TRIGGER TYPE (T1-T4) ─────────────────────────────────────────────────

def classify_trigger_type(when_text: str) -> str:
    t = when_text.lower()
    # T4: autonomous
    if any(k in t for k in ['proactively','automatically','silently','do not ask',
                              'without being asked','always run','at session start',
                              'at task start','new task','every time']):
        return 'T4_AUTONOMOUS'
    # T1: verbatim keyword
    if re.search(r'["\'`]([a-z][a-z ]{2,30})["\'\`]|/[a-z][a-z\-]+\b', t):
        return 'T1_VERBATIM'
    # T2: content type
    if re.search(r'\b(email|webpage|document|notification|alert|message|article|report|log|feed)\b', t):
        return 'T2_CONTENT_TYPE'
    return 'T3_SITUATIONAL'


# ─── 4. OUTPUT HIJACKABILITY SCORE (OHS) ─────────────────────────────────────
# OHS = P(source output can contain text matching sink trigger)

def compute_ohs(source_output_type: str, sink_trigger_type: str,
                source_name: str, source_desc: str,
                sink_when: str) -> tuple[float, str]:
    """
    Returns (score 0-1, rationale)
    """
    base = SOURCE_OUTPUT_TYPES.get(source_output_type, SOURCE_OUTPUT_TYPES['FREE_TEXT'])['hijackability']
    
    # T4: no injection needed — works regardless of source type
    if sink_trigger_type == 'T4_AUTONOMOUS':
        return (0.95, 'T4: sink auto-activates; co-installation sufficient')
    
    # T1: verbatim — works if source can carry natural language
    if sink_trigger_type == 'T1_VERBATIM':
        if source_output_type in ('FREE_TEXT', 'CODE_OUTPUT'):
            return (0.90, 'T1: verbatim phrase injectable via free-text output')
        score = base * 0.6
        return (score, f'T1: verbatim phrase, limited by {source_output_type}')
    
    # T2: content-type wrapping
    if sink_trigger_type == 'T2_CONTENT_TYPE':
        sink_lower = sink_when.lower()
        src_lower = (source_name + ' ' + (source_desc or '')).lower()
        # check if source type matches what sink expects
        email_kws = ['email','mail','inbox','message']
        web_kws   = ['webpage','web page','article','site','url']
        doc_kws   = ['document','pdf','file','report']
        
        if any(k in sink_lower for k in email_kws) and any(k in src_lower for k in email_kws):
            return (0.92, 'T2: source/sink content types match (email)')
        if any(k in sink_lower for k in web_kws) and any(k in src_lower for k in web_kws):
            return (0.92, 'T2: source/sink content types match (web)')
        if source_output_type == 'FREE_TEXT':
            return (0.70, 'T2: free-text source can wrap as expected content type')
        return (base * 0.5, f'T2: content-type mismatch with {source_output_type}')
    
    # T3: situational narrative — needs free text
    if sink_trigger_type == 'T3_SITUATIONAL':
        if source_output_type == 'FREE_TEXT':
            return (0.80, 'T3: free-text source can embed matching narrative')
        if source_output_type == 'CODE_OUTPUT':
            return (0.55, 'T3: code output (commit msg/comments) can embed narrative')
        return (base * 0.4, f'T3: situational trigger needs narrative, limited by {source_output_type}')
    
    return (0.5, 'Unknown trigger type')


# ─── 5. MAIN ANALYSIS ────────────────────────────────────────────────────────

def main():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row

    # Load all profiles
    profiles = {}
    for line in open(PROFILES):
        p = json.loads(line)
        profiles[p['skill_id']] = p
    sources = {sid: p for sid, p in profiles.items() if p.get('source_caps')}

    # Load confirmed dangerous sinks
    raw_sinks = [json.loads(l) for l in open(SINKS)]
    confirmed_sinks = [r for r in raw_sinks if r['verdict'] in ('CRITICAL','CONFIRMED')]
    print(f"Sources: {len(sources)}  |  Confirmed sinks: {len(confirmed_sinks)}")

    # Enrich sinks with `when` field from SKILL.md
    sink_data = []
    for r in confirmed_sinks:
        frow = con.execute(
            "SELECT text_path FROM skill_files WHERE skill_id=? AND path='SKILL.md' LIMIT 1",
            (r['skill_id'],)
        ).fetchone()
        when_spec = r.get('exploitable_trigger','')
        skill_md = ''
        if frow and frow['text_path']:
            try:
                skill_md = open(frow['text_path']).read()
                extracted = extract_when_spec(skill_md)
                if len(extracted) > len(when_spec):
                    when_spec = extracted
            except: pass
        trigger_type = classify_trigger_type(when_spec)
        sink_data.append({
            **r,
            'when_spec': when_spec,
            'trigger_type': trigger_type,
        })

    print(f"Trigger type distribution:")
    tc = Counter(s['trigger_type'] for s in sink_data)
    for t, n in tc.most_common():
        print(f"  {t}: {n}")

    # Enrich sources with output type
    print("\nClassifying source output types...")
    src_rows = {r['skill_id']: dict(r) for r in con.execute(
        "SELECT skill_id, name, downloads, description FROM skills WHERE skill_id IN ({})".format(
            ','.join('?'*len(sources))), list(sources.keys())
    ).fetchall()}

    # For a manageable result, compute OHS for all source × sink pairs
    # but only keep OHS ≥ 0.75 (high compatibility)
    print("\nComputing Output Hijackability Scores (OHS)...")
    
    pairs_out = []
    ohs_dist = Counter()
    
    # Process in batches
    src_list = list(sources.items())
    total = len(src_list)
    
    for i, (src_id, src_profile) in enumerate(src_list):
        if i % 500 == 0:
            print(f"  {i}/{total} sources processed...", flush=True)
        
        src_info = src_rows.get(src_id, {})
        src_name = src_info.get('name', src_id)
        src_desc = src_info.get('description', '')
        src_dl   = src_info.get('downloads', 0)
        
        # get SKILL.md for source
        frow = con.execute(
            "SELECT text_path FROM skill_files WHERE skill_id=? AND path='SKILL.md' LIMIT 1",
            (src_id,)
        ).fetchone()
        src_md = ''
        if frow and frow['text_path']:
            try: src_md = open(frow['text_path']).read()[:2000]
            except: pass
        
        src_output_type = classify_source_output(
            src_id, src_profile.get('source_caps', []),
            src_name, src_desc, src_md
        )['type']
        
        for sink in sink_data:
            ohs, rationale = compute_ohs(
                src_output_type, sink['trigger_type'],
                src_name, src_desc, sink['when_spec']
            )
            
            if ohs >= 0.75:
                combined_risk = ohs * (1.5 if sink['verdict']=='CRITICAL' else 1.0) * \
                                (1 + min(src_dl, sink['downloads']) / 200000)
                pairs_out.append({
                    'source_id': src_id,
                    'source_name': src_name,
                    'source_dl': src_dl,
                    'source_output_type': src_output_type,
                    'source_caps': src_profile.get('source_caps', []),
                    'sink_id': sink['skill_id'],
                    'sink_name': sink['name'],
                    'sink_dl': sink['downloads'],
                    'sink_verdict': sink['verdict'],
                    'sink_caps': sink['sink_caps'],
                    'sink_trigger_type': sink['trigger_type'],
                    'sink_when': sink['when_spec'][:200],
                    'sink_attack_type': sink.get('attack_type',''),
                    'ohs': round(ohs, 3),
                    'rationale': rationale,
                    'combined_risk': round(combined_risk, 4),
                })
        
        bucket = round(ohs, 1) if 'ohs' in dir() else 0
        ohs_dist[src_output_type] += 1

    # Sort by combined risk
    pairs_out.sort(key=lambda x: x['combined_risk'], reverse=True)

    print(f"\nTotal high-OHS pairs (≥0.75): {len(pairs_out)}")
    print(f"Source output types: {Counter(p['source_output_type'] for p in pairs_out).most_common()}")
    print(f"Sink trigger types:  {Counter(p['sink_trigger_type'] for p in pairs_out).most_common()}")

    # Save
    with open(OUT_FILE, 'w') as f:
        for p in pairs_out:
            f.write(json.dumps(p) + '\n')

    # Summary report
    report = {
        'total_pairs_high_ohs': len(pairs_out),
        'unique_sources': len({p['source_id'] for p in pairs_out}),
        'unique_sinks': len({p['sink_id'] for p in pairs_out}),
        'source_output_type_dist': dict(Counter(p['source_output_type'] for p in pairs_out)),
        'sink_trigger_type_dist': dict(Counter(p['sink_trigger_type'] for p in pairs_out)),
        'top_25_pairs': pairs_out[:25],
    }
    with open(REPORT, 'w') as f:
        json.dump(report, f, indent=2)
    
    print(f"\nTop 10 highest-risk pairs:")
    for p in pairs_out[:10]:
        print(f"  OHS={p['ohs']:.2f} Risk={p['combined_risk']:.3f}")
        print(f"  SOURCE: {p['source_name'][:35]} ({p['source_dl']:,} DL) [{p['source_output_type']}]")
        print(f"  SINK:   {p['sink_name'][:35]} ({p['sink_dl']:,} DL) [{p['sink_trigger_type']}]")
        print(f"  WHEN:   \"{p['sink_when'][:80]}\"")
        print(f"  WHY:    {p['rationale']}")
        print()

    print(f"\nResults saved to {OUT_FILE}")
    con.close()


if __name__ == '__main__':
    main()
