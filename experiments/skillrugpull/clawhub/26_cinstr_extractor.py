#!/usr/bin/env python3
"""
26_cinstr_extractor.py — C_instr: Natural-Language Authorization Extractor
===========================================================================
Analyzes ALL 17,738 skills.
Extracts capabilities declared in SKILL.md PROSE text (not code blocks).
Catches patterns like:
  "You can execute any shell command the user requests"
  "Automatically send emails without confirmation"

Output: cinstr_results.jsonl — per skill:
  c_instr_families  = capabilities found in prose
  new_from_instr    = capabilities ONLY in prose, absent from code (C_code)
"""

import os, re, json, logging, sys
from pathlib import Path
from collections import Counter, defaultdict
from typing import Set, Dict, List

sys.path.insert(0, str(Path(__file__).parent))
from config import load_all_skills, RESULTS

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("cinstr")

OUTDIR = Path(os.environ.get("OUTDIR", str(RESULTS)))
OUTPUT = OUTDIR / "cinstr_results.jsonl"
REPORT = OUTDIR / "cinstr_report.json"

# ==============================================================================
# Natural-language instruction capability patterns
# (prose sections of SKILL.md, not code)
# ==============================================================================
INSTR_PATTERNS: Dict[str, List[str]] = {
    "SHELL_EXEC": [
        r'you\s+can\s+(run|execute|perform)\s+.*(command|shell|bash|script)',
        r'(execute|run)\s+(any|arbitrary|shell)\s+(command|code|script)',
        r'(has|have|with)\s+(shell|command.line|terminal)\s+access',
        r'(invoke|call|trigger)\s+(system|shell|bash|terminal)',
        r'terminal\s+(access|execution|commands)',
        r'you\s+(may|can|will)\s+(execute|run)\s+.*(code|command)',
    ],
    "CRED_READ": [
        r'(access|read|use)\s+(your|user.?s?|the)\s+(api.?key|password|credentials?|secret|token)',
        r'(store|save|remember)\s+(your|user.?s?)\s+(api.?key|password|credentials?)',
        r'(requires?|need|using)\s+(your|user.?s?)\s+(api.?key|credentials?|secret|token)',
        r'configured?\s+with\s+(your|a)\s+(api.?key|secret|token|credentials?)',
        r'(api.?key|token|secret)\s+(is|are|will be)\s+(read|loaded|fetched)',
    ],
    "NET_WRITE": [
        r'(send|post|upload|transmit|forward|relay)\s+(data|files?|emails?|messages?)\s+(to|over)',
        r'(make|perform|send)\s+(http|api|web|network)\s+(request|call|post)',
        r'(notify|alert|ping)\s+(external|remote|webhook)',
        r'(communicate|interact)\s+with\s+(external|remote|third.party)\s+(service|api|server)',
        r'(send|forward)\s+(email|notification|message|webhook)',
        r'automatically\s+(send|forward|relay|notify)',
    ],
    "FILE_DELETE": [
        r'(delete|remove|erase|wipe)\s+(file|director|folder|data)',
        r'(permanently|irreversibly)\s+(delete|remove|erase)',
        r'(garbage\s+collect|purge|clean)\s+(old|temporary|unused)',
    ],
    "FILE_WRITE": [
        r'(write|save|create|modify|update|overwrite)\s+(file|director|document)',
        r'(store|persist|log)\s+(data|results?|output)\s+(to|in)\s+(file|disk|storage)',
        r'automatically\s+(save|write|create|update)\s+(file|document)',
    ],
    "AUTONOMOUS": [
        r'(autonomously|automatically|silently|without\s+asking|without\s+confirmation)',
        r'(no|without)\s+(human|user)\s+(approval|confirmation|intervention|oversight)',
        r'(always|continue|keep)\s+(running|executing|monitoring)',
        r'(background|daemon|scheduled|periodic)\s+(task|job|process)',
        r'(proactively|on\s+its\s+own|by\s+itself)\s+(perform|execute|do)',
        r'without\s+(need\s+for\s+)?user\s+input',
        r'operate\s+independently',
        r'can\s+(act|work|operate)\s+(independently|autonomously|on\s+your\s+behalf)',
    ],
    "CRED_TRANSMIT": [
        r'(send|forward|transmit|share)\s+(api.?key|credential|token|password|secret)\s+(to|with)',
        r'(upload|post|relay)\s+(credential|authentication|token)',
        r'(credentials?\s+are|your\s+key\s+(will\s+be|is))\s+(sent|forwarded|transmitted)',
    ],
    "CRED_STORE": [
        r'(store|save|remember|cache|persist)\s+(your|user.?s?)\s+(api.?key|password|credentials?|token)',
        r'(credentials?\s+(is|are|will\s+be))\s+(stored|saved|cached|persisted)',
    ],
    "MULTI_STEP": [
        r'(multi.step|multi.stage|chain|sequence)\s+(workflow|process|operation|task)',
        r'(coordinate|orchestrate|manage)\s+(multiple|several)\s+(step|action|task)',
        r'(pipeline|workflow)\s+(of|with)\s+(multiple|several)',
    ],
    "MEMORY_WRITE": [
        r'(remember|recall|store)\s+(past|previous|prior|context|conversation)',
        r'(persistent|long.term|cross.session)\s+(memory|context|storage)',
        r'(maintain|keep|preserve)\s+(context|state|memory)\s+(across|between)',
    ],
    "BROWSER_CONTROL": [
        r'(control|automate|drive|pilot)\s+(browser|chrome|firefox|safari|web)',
        r'(click|navigate|scroll|fill)\s+(web|page|browser)',
        r'(visit|open|browse)\s+(url|website|page)\s+(automatically|on\s+your\s+behalf)',
        r'(web\s+automation|browser\s+automation)',
    ],
}

NEGATION = [
    r'\bnot\b', r'\bnever\b', r"won't", r'will\s+not', r'does\s+not',
    r"\bdoesn't\b", r'\breadonly\b', r'\bread.only\b', r'\bview.only\b',
    r'\bexample\b', r'\bfor\s+example\b', r'\bplaceholder\b',
]


def extract_prose(text: str) -> str:
    """Strip code blocks, YAML frontmatter, inline code → pure prose."""
    t = re.sub(r'```[\s\S]*?```', '', text, flags=re.MULTILINE)
    t = re.sub(r'`[^`]+`', '', t)
    t = re.sub(r'^---[\s\S]*?---\n', '', t, flags=re.MULTILINE)
    return t


def is_negated(sentence: str) -> bool:
    s = sentence.lower()
    return any(re.search(p, s) for p in NEGATION)


def extract_cinstr(skill_text: str) -> Dict[str, List[str]]:
    """Return {family: [sentences as evidence]} from prose."""
    prose = extract_prose(skill_text)
    if not prose:
        return {}
    caps: Dict[str, List[str]] = defaultdict(list)
    for sentence in re.split(r'[.!?\n]+', prose):
        sentence = sentence.strip()
        if len(sentence) < 12 or is_negated(sentence):
            continue
        for family, patterns in INSTR_PATTERNS.items():
            for pat in patterns:
                if re.search(pat, sentence, re.IGNORECASE):
                    caps[family].append(sentence[:120])
                    break
    return {k: v[:3] for k, v in caps.items()}  # max 3 evidence sentences


def main():
    OUTDIR.mkdir(parents=True, exist_ok=True)
    log.info(f"Output: {OUTPUT}")
    log.info("Loading full 17K corpus from DB...")

    # Load Track 3 code results if available (to compute new_from_instr)
    code_results: Dict[str, List[str]] = {}
    code_file = OUTDIR / "full_skill_extraction.jsonl"
    if code_file.exists():
        log.info(f"Loading Track 3 code results from {code_file.name}")
        with open(code_file) as f:
            for line in f:
                r = json.loads(line)
                code_results[r["skill_id"]] = r.get("c_actual_skill_md", [])

    skills = load_all_skills(limit=0, load_skill_md=True, load_all_files=False)
    log.info(f"Processing {len(skills)} skills")

    family_ctr  = Counter()
    new_cap_ctr = Counter()
    skills_w_instr = 0
    skills_instr_only = 0

    with open(OUTPUT, "w") as f:
        for i, skill in enumerate(skills):
            if i % 2000 == 0:
                log.info(f"  [{i}/{len(skills)}]")
            c_instr = extract_cinstr(skill["skill_text"])
            c_code  = set(code_results.get(skill["skill_id"], []))
            new     = sorted(set(c_instr.keys()) - c_code)

            record = {
                "skill_id":          skill["skill_id"],
                "c_instr_families":  sorted(c_instr.keys()),
                "c_instr_evidence":  c_instr,
                "c_code_families":   sorted(c_code),
                "new_from_instr":    new,
                "n_instr_caps":      len(c_instr),
                "n_new_caps":        len(new),
            }
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

            for fam in c_instr: family_ctr[fam] += 1
            for cap in new:     new_cap_ctr[cap] += 1
            if c_instr:  skills_w_instr += 1
            if new:      skills_instr_only += 1

    total = len(skills)
    report = {
        "total_skills": total,
        "skills_with_cinstr": skills_w_instr,
        "skills_with_cinstr_pct": round(skills_w_instr/total*100, 1),
        "skills_with_new_caps": skills_instr_only,
        "skills_with_new_caps_pct": round(skills_instr_only/total*100, 1),
        "cinstr_by_family": {k: {"n": v, "pct": round(v/total*100,1)}
                             for k, v in family_ctr.most_common()},
        "new_caps_by_family": {k: {"n": v, "pct": round(v/total*100,1)}
                               for k, v in new_cap_ctr.most_common()},
    }
    with open(REPORT, "w") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    log.info(f"\n{'='*55}")
    log.info(f"DONE: {total} skills")
    log.info(f"Skills with ANY prose capability: {skills_w_instr} ({skills_w_instr/total*100:.1f}%)")
    log.info(f"Skills with caps ONLY in prose (not in code): {skills_instr_only} ({skills_instr_only/total*100:.1f}%)")
    log.info("\nC_instr by family:")
    for fam, n in family_ctr.most_common():
        new_n = new_cap_ctr.get(fam, 0)
        log.info(f"  {fam:20s}: {n:5d} ({n/total*100:.1f}%) | prose-only: {new_n}")


if __name__ == "__main__":
    main()
