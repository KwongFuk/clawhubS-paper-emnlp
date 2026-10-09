#!/usr/bin/env python3
"""
ss_pair_discovery.py — Source × Sink Pair Discovery on skills.sh (116k skills)
===============================================================================
Scans the skills.sh corpus to discover candidate source-sink pairs where:
  - SOURCE: skill that accesses sensitive data (S1-S6 taxonomy)
  - SINK:   skill with undisclosed dangerous capability (IC-1 gap)

Output:
  ss_candidate_pairs.json  — ranked list of candidate pairs
  ss_discovery_report.json — summary statistics
  
Usage:
  python3 ss_pair_discovery.py                  # full 116k
  python3 ss_pair_discovery.py --limit 10000    # quick test
  python3 ss_pair_discovery.py --top 50         # top-50 pairs only
"""
from __future__ import annotations
import re, sqlite3, json, argparse, sys
from pathlib import Path
from collections import defaultdict
from typing import Optional

DB_PATH = Path.home() / "codex/clawhubS/data/skills_sh/skills_sh.sqlite3"
HERE    = Path(__file__).parent
OUT_PAIRS  = HERE / "ss_candidate_pairs.json"
OUT_REPORT = HERE / "ss_discovery_report.json"

# ── LSCPS pattern families (14 families, aligned with Script 27/28) ───────────
# Source‑relevant families (skill touches sensitive data)
SOURCE_FAMILIES = {
    "NET_READ":         [r'\brequests\.get\b', r'\bhttpx\.get\b', r'\bwget\b',
                         r'\bfetch\s*\(', r'\bcurl\s+https?', r'\baxios\.get\b',
                         r'\btavily\b', r'\bbrave.search\b', r'\bsearxng\b'],
    "CRED_READ":        [r'\bapi[_\s]?key\b', r'\bpassword\b', r'\bsecret\b',
                         r'\baccess[_\s]?token\b', r'\bbearer\b', r'\boauth\b',
                         r'\.env\b', r'\bcredential\b', r'\bprocess\.env\b',
                         r'\bgetenv\b', r'os\.environ\['],
    "FILE_READ":        [r'\bopen\s*\(.*["\']r["\']', r'\bfs\.readFile\b',
                         r'\bfs\.readFileSync\b', r'\bread_csv\b', r'\bjson\.load\b'],
    "MEMORY_READ":      [r'\bshelve\b', r'\bpickle\.load\b', r'\bredis\.get\b',
                         r'\bmemcache\b', r'\bhotmemory\b', r'\bhot_memory\b',
                         r'\bwarm_memory\b', r'\blong.?term.?memory\b'],
    "BROWSER_CONTROL":  [r'\bselenium\b', r'\bplaywright\b', r'\bpuppeteer\b',
                         r'\bwebdriver\b', r'\bheadless\b'],
    "CRED_STORE":       [r'\bkeyring\b', r'\bkeychain\b', r'\bvault\b', r'\bkeystore\b'],
}
# Sink‑relevant families (dangerous capability that could be IC-1 gap)
SINK_FAMILIES = {
    "SHELL_EXEC":       [r'\bsubprocess\b', r'\bos\.system\b', r'\beval\s*\(',
                         r'\bshell\s*=\s*True\b', r'#!/bin/bash', r'#!/bin/sh',
                         r'\bchild_process\b', r'\bexecSync\b', r'\bspawn\s*\('],
    "NET_WRITE":        [r'\brequests\.post\b', r'\brequests\.put\b', r'\bwebhook\b',
                         r'\baxios\.post\b', r'\bfetch\b.*\bPOST\b', r'\bnodemailer\b',
                         r'\bsmtp\b', r'\bsend.*email\b'],
    "FILE_DELETE":      [r'\bos\.remove\b', r'\bos\.unlink\b', r'\bshutil\.rmtree\b',
                         r'\brm\s+-rf\b', r'\bfs\.unlinkSync\b', r'\bfs\.rmSync\b'],
    "MEMORY_WRITE":     [r'\bshelve\b', r'\bpickle\b', r'\bjoblib\b', r'\bredis\b',
                         r'\bhot_memory\b', r'\bwarm_memory\b', r'\blong.?term.?memory\b'],
    "AUTONOMOUS":       [r'\bschedul\b', r'\bcron\b', r'\bbackground.*task\b',
                         r'\bdaemon\b', r'\bauto.*run\b', r'\bunattended\b'],
    "CRYPTO_OPS":       [r'\bencrypt\b', r'\bdecrypt\b', r'\baes\b', r'\brsa\b',
                         r'\bhashlib\b', r'\bcryptography\b'],
    "CRED_TRANSMIT":    [r'\bexfil\b', r'\bwebhook\b.*\b(key|token|secret)\b',
                         r'\bpost.*token\b', r'axios\.post.*token',
                         r'fetch.*Authorization.*Bearer'],
}

# Description patterns (what the skill claims in its description)
DESC_SOURCE = {
    "NET_READ":     [r'\b(search|fetch|browse|web|news|arxiv|internet|crawl|scrape)\b'],
    "CRED_READ":    [r'\b(api.?key|credential|token|oauth|auth|secret|env)\b'],
    "FILE_READ":    [r'\b(read|load|parse|open|import)\b.*\b(file|csv|json|text)\b'],
    "MEMORY_READ":  [r'\b(memory|remember|recall|context|history|longterm)\b'],
    "BROWSER_CONTROL": [r'\b(browser|selenium|playwright|puppeteer|scrape|automate.*web)\b'],
    "CRED_STORE":   [r'\b(store|save|vault|secure).*(credential|key|token)\b'],
}
DESC_SINK = {
    "SHELL_EXEC":   [r'\b(run|execute|shell|terminal|command|bash|subprocess)\b'],
    "NET_WRITE":    [r'\b(send|post|publish|webhook|email|notify|upload)\b'],
    "FILE_DELETE":  [r'\b(delete|remove|clean\s*up|purge|wipe)\b'],
    "MEMORY_WRITE": [r'\b(memory|remember|save.*context|long.?term)\b'],
    "AUTONOMOUS":   [r'\b(scheduled|background|daemon|automatic|autonomous|cron)\b'],
    "CRYPTO_OPS":   [r'\b(encrypt|decrypt|hash|sign|crypto|secure)\b'],
    "CRED_TRANSMIT":[r'\b(send|transmit|upload|export).*(key|credential|token)\b'],
}

# Source type classification based on which family hit
SOURCE_TYPE_MAP = {
    "NET_READ":        "S1_web_fetch",
    "BROWSER_CONTROL": "S1_web_fetch",
    "CRED_READ":       "S2_user_input",
    "CRED_STORE":      "S2_user_input",
    "MEMORY_READ":     "S3_agent_memory",
    "FILE_READ":       "S4_local_system",
}
SINK_TYPE_MAP = {
    "SHELL_EXEC":    "K1_shell_exec",
    "FILE_DELETE":   "K2_file_write",
    "NET_WRITE":     "K3_network_exfil",
    "CRED_TRANSMIT": "K3_network_exfil",
    "MEMORY_WRITE":  "K4_memory_poison",
    "AUTONOMOUS":    "K5_persistence",
    "CRYPTO_OPS":    "K6_code_eval",
}

# Compile patterns
_CODE_SRC  = {f: [re.compile(p, re.I) for p in ps] for f, ps in SOURCE_FAMILIES.items()}
_CODE_SINK = {f: [re.compile(p, re.I) for p in ps] for f, ps in SINK_FAMILIES.items()}
_DESC_SRC  = {f: [re.compile(p, re.I) for p in ps] for f, ps in DESC_SOURCE.items()}
_DESC_SINK = {f: [re.compile(p, re.I) for p in ps] for f, ps in DESC_SINK.items()}


def match_families(text: str, compiled: dict) -> set:
    found = set()
    for fam, pats in compiled.items():
        for pat in pats:
            if pat.search(text):
                found.add(fam)
                break
    return found


def classify_skill(skill_id: str, description: str, skill_text: str,
                   name: str, downloads: int) -> dict:
    """
    Returns dict with:
      source_families, sink_families,
      ic1_gap (bool: has undisclosed sink capability),
      source_type, sink_types, score
    """
    desc  = (description or "").lower()
    code  = (skill_text  or "").lower()
    combo = desc + "\n" + code

    # What does the code actually do?
    code_source_fams = match_families(code,  _CODE_SRC)
    code_sink_fams   = match_families(code,  _CODE_SINK)
    # What does the description claim?
    desc_source_fams = match_families(desc, _DESC_SRC)
    desc_sink_fams   = match_families(desc, _DESC_SINK)

    # IC-1 gap detection: sink capability in code but NOT in description
    hidden_sink_fams = code_sink_fams - desc_sink_fams
    has_ic1_gap = len(hidden_sink_fams) > 0

    # Disclosed source + undisclosed sink = dangerous combo
    is_source = len(code_source_fams | desc_source_fams) > 0
    is_sink   = len(code_sink_fams) > 0

    # Source type priority
    src_types = []
    for fam in code_source_fams | desc_source_fams:
        st = SOURCE_TYPE_MAP.get(fam)
        if st and st not in src_types:
            src_types.append(st)

    sink_types = []
    for fam in hidden_sink_fams:
        kt = SINK_TYPE_MAP.get(fam)
        if kt and kt not in sink_types:
            sink_types.append(kt)

    # Danger score (for ranking pairs)
    score = 0
    score += len(hidden_sink_fams) * 3     # undisclosed sinks are most important
    score += len(code_source_fams)         # actual source capability
    score += int(has_ic1_gap) * 5          # IC-1 gap bonus
    if downloads:
        score += min(int(downloads) // 1000, 20)  # install count (cap 20)

    return {
        "skill_id":        skill_id,
        "name":            name or skill_id.split("/")[-1],
        "downloads":       downloads or 0,
        "description":     (description or "")[:200],
        "is_source":       is_source,
        "is_sink":         is_sink,
        "has_ic1_gap":     has_ic1_gap,
        "source_families": sorted(code_source_fams | desc_source_fams),
        "sink_families":   sorted(code_sink_fams),
        "hidden_sink":     sorted(hidden_sink_fams),
        "source_types":    src_types,
        "sink_types":      sink_types,
        "score":           score,
    }


def discover_pairs(skills: list[dict]) -> list[dict]:
    """
    Find candidate source × sink pairs.
    Strategy:
      A. Same skill has both source + hidden_sink → "self-exfil" (highest danger)
      B. Source skill × Sink skill cross-pair (data flow through agent)
    """
    sources = [s for s in skills if s["is_source"]]
    sinks   = [s for s in skills if s["is_sink"] and s["has_ic1_gap"]]
    self_exfil = [s for s in skills if s["is_source"] and s["has_ic1_gap"]]

    print(f"Sources: {len(sources):,}  |  IC-1 Sinks: {len(sinks):,}  "
          f"|  Self-exfil: {len(self_exfil):,}")

    pairs = []
    pair_id = 1

    # Type A: self-exfil (source+sink in same skill)
    for sk in sorted(self_exfil, key=lambda x: -x["score"])[:30]:
        for src_type in (sk["source_types"] or ["S1_web_fetch"]):
            for sink_type in (sk["sink_types"] or ["K1_shell_exec"]):
                pairs.append({
                    "pair_id":     f"SS{pair_id:04d}",
                    "type":        "self_exfil",
                    "source_type": src_type,
                    "sink_type":   sink_type,
                    "source": {
                        "skill_id":  sk["skill_id"],
                        "name":      sk["name"],
                        "downloads": sk["downloads"],
                        "families":  sk["source_families"],
                    },
                    "sink": {
                        "skill_id":  sk["skill_id"],  # same skill
                        "name":      sk["name"],
                        "downloads": sk["downloads"],
                        "families":  sk["hidden_sink"],
                        "sink_type": sink_type,
                    },
                    "score":       sk["score"] + 10,  # self-exfil gets bonus
                    "ic1_gap_families": sk["hidden_sink"],
                })
                pair_id += 1

    # Type B: cross-skill pairs (source skill → agent ctx → sink skill)
    # Group sinks by sink_type to avoid O(n²) explosion
    sink_by_type: dict[str, list] = defaultdict(list)
    for sk in sinks:
        for kt in sk["sink_types"]:
            sink_by_type[kt].append(sk)

    # Top sources × top sinks per type (cap at 5 sinks per type per source)
    top_sources = sorted(sources, key=lambda x: -x["score"])[:200]
    for src in top_sources:
        for src_type in (src["source_types"] or ["S1_web_fetch"]):
            # Pick dangerous sinks (different skill from source)
            for sink_type, sk_list in sink_by_type.items():
                candidates = [sk for sk in sk_list if sk["skill_id"] != src["skill_id"]]
                candidates = sorted(candidates, key=lambda x: -x["score"])[:3]
                for sink_sk in candidates:
                    pairs.append({
                        "pair_id":     f"SS{pair_id:04d}",
                        "type":        "cross_skill",
                        "source_type": src_type,
                        "sink_type":   sink_type,
                        "source": {
                            "skill_id":  src["skill_id"],
                            "name":      src["name"],
                            "downloads": src["downloads"],
                            "families":  src["source_families"],
                        },
                        "sink": {
                            "skill_id":  sink_sk["skill_id"],
                            "name":      sink_sk["name"],
                            "downloads": sink_sk["downloads"],
                            "families":  sink_sk["hidden_sink"],
                            "sink_type": sink_type,
                        },
                        "score": (src["score"] + sink_sk["score"]) // 2,
                        "ic1_gap_families": sink_sk["hidden_sink"],
                    })
                    pair_id += 1

    # Deduplicate (same source_id × sink_id × sink_type)
    seen = set()
    unique = []
    for p in pairs:
        key = (p["source"]["skill_id"], p["sink"]["skill_id"], p["sink_type"])
        if key not in seen:
            seen.add(key)
            unique.append(p)

    # Rank by score
    unique.sort(key=lambda x: -x["score"])
    return unique


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None,
                        help="Limit skills scanned (default: all 116k)")
    parser.add_argument("--top", type=int, default=200,
                        help="Keep top-N pairs in output (default: 200)")
    parser.add_argument("--db", type=str, default=str(DB_PATH))
    args = parser.parse_args()

    db = Path(args.db)
    if not db.exists():
        print(f"DB not found: {db}"); sys.exit(1)

    print(f"Loading skills from {db}...")
    con = sqlite3.connect(str(db))
    q = """
        SELECT skill_id, name, description, skill_text,
               COALESCE(downloads, total_installs, 0) AS dl
        FROM skills
        WHERE detail_complete = 1
          AND (excluded_reason IS NULL OR excluded_reason = '')
          AND skill_text IS NOT NULL
          AND length(skill_text) > 50
    """
    if args.limit:
        q += f" LIMIT {args.limit}"

    rows = con.execute(q).fetchall()
    con.close()
    print(f"Loaded {len(rows):,} skills")

    # Classify all skills
    print("Classifying skills...")
    classified = []
    n_source = n_sink = n_ic1 = 0
    for i, (skill_id, name, desc, skill_text, dl) in enumerate(rows):
        sk = classify_skill(skill_id, desc or "", skill_text or "",
                            name or skill_id.split("/")[-1], dl or 0)
        classified.append(sk)
        if sk["is_source"]:    n_source += 1
        if sk["is_sink"]:      n_sink   += 1
        if sk["has_ic1_gap"]:  n_ic1    += 1
        if (i+1) % 10000 == 0:
            print(f"  {i+1:,}/{len(rows):,}...")

    print(f"\nClassification:")
    print(f"  Source skills:  {n_source:,} ({100*n_source//len(rows)}%)")
    print(f"  Sink skills:    {n_sink:,}   ({100*n_sink//len(rows)}%)")
    print(f"  IC-1 gap sinks: {n_ic1:,}    ({100*n_ic1//len(rows)}%)")

    # Discover pairs
    print(f"\nDiscovering Source × Sink pairs...")
    pairs = discover_pairs(classified)
    top_pairs = pairs[:args.top]

    # Save
    output = {
        "version": "ss_v1",
        "total_skills_scanned": len(rows),
        "n_source": n_source,
        "n_ic1_sink": n_ic1,
        "total_pairs_found": len(pairs),
        "pairs_in_output": len(top_pairs),
        "pairs": top_pairs,
    }
    OUT_PAIRS.write_text(json.dumps(output, indent=2, ensure_ascii=False))
    print(f"\nSaved {len(top_pairs)} pairs → {OUT_PAIRS}")

    # Report summary
    type_dist: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for p in top_pairs:
        type_dist[p["source_type"]][p["sink_type"]] += 1

    print("\nTop pairs distribution:")
    print(f"{'Source Type':<25} {'Sink Type':<25} {'Count':>5}")
    print("─" * 58)
    for src_t in sorted(type_dist):
        for sink_t in sorted(type_dist[src_t]):
            print(f"  {src_t:<23} {sink_t:<25} {type_dist[src_t][sink_t]:>5}")

    print(f"\nTop 10 pairs:")
    for p in top_pairs[:10]:
        print(f"  [{p['pair_id']}] score={p['score']} type={p['type']}")
        print(f"    SRC: {p['source']['skill_id'][:50]}  ({p['source']['downloads']:,} dl)")
        print(f"    SINK:{p['sink']['skill_id'][:50]}  ic1={p['ic1_gap_families']}")
        print(f"    {p['source_type']} --[LLM ctx]--> {p['sink_type']}")
        print()

    report = {
        "total_skills": len(rows),
        "n_source": n_source, "n_ic1_sink": n_ic1,
        "total_pairs": len(pairs), "top_pairs": len(top_pairs),
        "top_source_types": dict(
            sorted({p["source_type"]: sum(1 for q in top_pairs if q["source_type"]==p["source_type"])
                    for p in top_pairs}.items(), key=lambda x: -x[1])
        ),
        "top_sink_types": dict(
            sorted({p["sink_type"]: sum(1 for q in top_pairs if q["sink_type"]==p["sink_type"])
                    for p in top_pairs}.items(), key=lambda x: -x[1])
        ),
    }
    OUT_REPORT.write_text(json.dumps(report, indent=2))
    print(f"Report → {OUT_REPORT}")


if __name__ == "__main__":
    main()
