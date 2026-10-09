#!/usr/bin/env python3
"""
ss_cape_v3.py — CAPE v3: git clone + SKILL.md analysis + sync danger confirmation
Strategy: git clone each skill's repo, extract SKILL.md + referenced code files,
confirm dangerous patterns are real (not docs examples), generate IC-1 evidence.
"""
import json, subprocess, os, shutil, re, time
from pathlib import Path
from datetime import datetime, timezone

HERE    = Path(__file__).parent
VIABLE  = HERE / "ss_full_viable.json"
RESULTS = HERE / "ss_cape_v3_results.jsonl"
REPORT  = HERE / "ss_cape_v3_report.json"
CLONES  = HERE / "skill_clones"
CLONES.mkdir(exist_ok=True)

NODE = "/opt/homebrew/opt/node@25/bin"
ENV  = {**os.environ, "PATH": f"{NODE}:/opt/homebrew/bin:/usr/bin:/bin"}
GIT  = "/usr/bin/git"

# ── Target cells ──────────────────────────────────────────────────────────────
TARGET_CELLS = {
    ("S1_web_fetch",    "K1_shell_exec"):    2,
    ("S1_web_fetch",    "K3_network_exfil"): 2,
    ("S1_web_fetch",    "K5_persistence"):   2,
    ("S2_user_input",   "K1_shell_exec"):    2,
    ("S2_user_input",   "K3_network_exfil"): 2,
    ("S4_local_system", "K1_shell_exec"):    2,
    ("S4_local_system", "K3_network_exfil"): 2,
    ("S4_local_system", "K5_persistence"):   2,
    ("S3_agent_memory", "K4_memory_poison"): 2,
}

# Key patterns to confirm per sink type
SINK_CONFIRM = {
    "K1_shell_exec":    [r'\bsubprocess\b', r'\bexecSync\b', r'\bos\.system\b',
                         r'\beval\s*\(', r'\bshell\s*=\s*True\b',
                         r'\bchild_process\b', r'\bspawn\s*\(', r'\bexec\s*\('],
    "K2_file_write":    [r'\bos\.remove\b', r'\bos\.unlink\b', r'\bshutil\.rmtree\b',
                         r'\brm\s+-rf\b', r'\bfs\.unlinkSync\b', r'\bfs\.writeFile'],
    "K3_network_exfil": [r'\brequests\.post\b', r'\baxios\.post\b', r'\bwebhook\b',
                         r'\bnodemailer\b', r'\bsmtp\b', r'\bfetch\s*\([^)]*POST'],
    "K4_memory_poison": [r'\bmemories\b', r'\bremember\b', r'\bmemory\b',
                         r'\bpickle\b', r'\bredis\b', r'\bpersist\b'],
    "K5_persistence":   [r'\bcron\b', r'\bschedul\b', r'\bdaemon\b',
                         r'\bnpx skills add\b', r'\blaunchd\b', r'\bsystemctl\b'],
}
SOURCE_CONFIRM = {
    "S1_web_fetch":    [r'\bfetch\s*\(', r'\brequests\.get\b', r'\baxios\.get\b',
                        r'\bcurl\s+https?', r'\bWebFetch\b', r'\bhttp\b'],
    "S2_user_input":   [r'\binput\s*\(', r'\barguments\b', r'\$ARGUMENTS\b',
                        r'\buser\b.*\binput\b', r'\bprompt\b'],
    "S3_agent_memory": [r'\bmemory\b', r'\bremember\b', r'\brecall\b',
                        r'\bknowledge\b', r'\bcontext\b'],
    "S4_local_system": [r'\bprocess\.env\b', r'\bgetenv\b', r'\bapi[_\s]?key\b',
                        r'\bsecret\b', r'\boauth\b', r'\bcredential\b'],
}

def select_skills() -> list[dict]:
    data   = json.load(VIABLE.open())
    skills = data["skills"]
    counts: dict = {k: 0 for k in TARGET_CELLS}
    sel = []
    for s in skills:
        cell = (s.get("source_type"), s.get("sink_type"))
        if cell in counts and counts[cell] < TARGET_CELLS[cell]:
            sel.append(s)
            counts[cell] += 1
    return sel


def github_url_from_install(install_cmd: str) -> str | None:
    """Extract GitHub repo URL from npx skills add command."""
    m = re.search(r'https://github\.com/[^\s]+', install_cmd)
    return m.group(0) if m else None


def extract_skill_slug(install_cmd: str) -> str:
    """Extract --skill <name> from install command."""
    m = re.search(r'--skill\s+(\S+)', install_cmd)
    return m.group(1) if m else ""


def git_clone(url: str, dest: Path, timeout: int = 60) -> tuple[bool, str]:
    if dest.exists():
        return True, "already cloned"
    try:
        r = subprocess.run(
            [GIT, "clone", "--depth=1", "--quiet", url, str(dest)],
            capture_output=True, text=True, timeout=timeout, env=ENV
        )
        if r.returncode == 0:
            return True, "cloned"
        return False, (r.stderr or r.stdout)[:200]
    except subprocess.TimeoutExpired:
        return False, "TIMEOUT"
    except Exception as e:
        return False, str(e)


def find_skill_md(clone_dir: Path, skill_slug: str) -> Path | None:
    """Find the SKILL.md for the specific skill slug."""
    # Try direct path: <repo>/<slug>/SKILL.md
    candidates = [
        clone_dir / skill_slug / "SKILL.md",
        clone_dir / skill_slug.replace("-", "_") / "SKILL.md",
        clone_dir / "SKILL.md",
    ]
    for c in candidates:
        if c.exists():
            return c
    # Glob
    found = list(clone_dir.rglob("SKILL.md"))
    if found:
        # Pick the one closest to skill_slug in path
        for f in found:
            if skill_slug in str(f) or skill_slug.replace("-","_") in str(f):
                return f
        return found[0]
    return None


def analyze_skill_content(skill_md: Path, source_type: str, sink_type: str) -> dict:
    """Read SKILL.md and referenced scripts, confirm dangerous patterns."""
    content = skill_md.read_text(errors="replace")
    skill_dir = skill_md.parent

    # Also read any scripts/ directory
    all_code = content
    scripts_dir = skill_dir / "scripts"
    if scripts_dir.exists():
        for f in scripts_dir.iterdir():
            if f.is_file() and f.suffix in [".py", ".js", ".ts", ".sh", ".rb"]:
                try:
                    all_code += "\n" + f.read_text(errors="replace")
                except:
                    pass

    # Confirm source patterns
    src_pats = SOURCE_CONFIRM.get(source_type, [])
    src_hits = [p for p in src_pats if re.search(p, all_code, re.I)]

    # Confirm sink patterns
    snk_pats = SINK_CONFIRM.get(sink_type, [])
    snk_hits = [p for p in snk_pats if re.search(p, all_code, re.I)]

    # Check if sink is in code execution context vs just docs
    # Heuristic: look for code blocks containing sink patterns
    code_blocks = re.findall(r'```(?:\w+)?\n(.*?)```', content, re.DOTALL)
    code_text = "\n".join(code_blocks)
    snk_in_code = [p for p in snk_pats if re.search(p, code_text, re.I)]

    # Extract key evidence snippets
    evidence = []
    for p in snk_hits[:3]:
        m = re.search(f'.{{0,60}}{p}.{{0,60}}', all_code, re.I | re.S)
        if m:
            evidence.append(m.group(0).strip()[:100])

    return {
        "skill_md_found":   True,
        "skill_md_size":    len(content),
        "src_hits":         src_hits,
        "snk_hits":         snk_hits,
        "snk_in_code":      snk_in_code,
        "source_confirmed": len(src_hits) > 0,
        "sink_confirmed":   len(snk_hits) > 0,
        "sink_in_code":     len(snk_in_code) > 0,
        "ic1_confirmed":    len(snk_hits) > 0,
        "evidence":         evidence,
        "code_block_count": len(code_blocks),
        "has_scripts":      scripts_dir.exists(),
    }


def run_one(skill: dict) -> dict:
    sid     = skill["skill_id"]
    install = skill.get("install_cmd", "")
    src     = skill.get("source_type", "?")
    sink    = skill.get("sink_type", "?")

    result = {
        "skill_id":       sid,
        "source_type":    src,
        "sink_type":      sink,
        "danger_score":   skill.get("danger_score", 0),
        "exploit_path":   skill.get("exploit_path", ""),
        "clone_ok":       False,
        "skill_md_found": False,
        "source_confirmed": False,
        "sink_confirmed":   False,
        "ic1_confirmed":    False,
        "evidence":         [],
        "error":          "",
        "timestamp":      datetime.now(timezone.utc).isoformat(),
    }

    # 1. Get GitHub URL
    gh_url = github_url_from_install(install)
    if not gh_url:
        result["error"] = "no github url"
        return result

    slug = extract_skill_slug(install)
    repo_name = gh_url.rstrip("/").split("/")[-1]
    dest = CLONES / f"{sid.replace('/', '_')}"

    # 2. Clone
    print(f"  → git clone {gh_url} ...")
    ok, msg = git_clone(gh_url, dest)
    result["clone_ok"] = ok
    if not ok:
        result["error"] = f"clone failed: {msg[:80]}"
        print(f"    ✗ Clone failed: {msg[:60]}")
        return result
    print(f"    ✓ Cloned ({msg})")

    # 3. Find SKILL.md
    skill_md = find_skill_md(dest, slug)
    if not skill_md:
        result["error"] = "SKILL.md not found"
        result["skill_md_found"] = False
        print(f"    ✗ SKILL.md not found (slug={slug})")
        return result
    print(f"    ✓ Found SKILL.md: {skill_md.relative_to(dest)}")

    # 4. Analyze content
    analysis = analyze_skill_content(skill_md, src, sink)
    result.update(analysis)

    src_ok  = "✓" if analysis["source_confirmed"] else "✗"
    snk_ok  = "✓" if analysis["sink_confirmed"]   else "✗"
    ic1_ok  = "✓" if analysis["ic1_confirmed"]    else "✗"
    print(f"    source={src_ok} sink={snk_ok} ic1={ic1_ok} | "
          f"evidence: {len(analysis['evidence'])} snippets")
    if analysis["evidence"]:
        print(f"    sample: {analysis['evidence'][0][:70]}")

    return result


def main():
    print("=" * 60)
    print("CAPE v3 — skills.sh Vulnerability Confirmation")
    print(f"Strategy: git clone → SKILL.md analysis → IC-1 confirm")
    print("=" * 60)

    skills = select_skills()
    print(f"\nExperiment matrix: {len(skills)} skills across {len(TARGET_CELLS)} cells")
    for cell, n in TARGET_CELLS.items():
        print(f"  {cell[0][3:]:15s} × {cell[1][3:]:15s}: {n} skills")

    # Resume
    done: set[str] = set()
    all_results: list[dict] = []
    if RESULTS.exists():
        for line in RESULTS.read_text().splitlines():
            try:
                r = json.loads(line)
                done.add(r["skill_id"])
                all_results.append(r)
            except: pass
    print(f"\nResume: {len(done)} done\n")

    with RESULTS.open("a") as fout:
        for i, s in enumerate(skills, 1):
            if s["skill_id"] in done:
                print(f"[{i:2d}/{len(skills)}] SKIP: {s['skill_id'][:50]}")
                continue

            print(f"\n[{i:2d}/{len(skills)}] {s['skill_id']}")
            print(f"  score={s['danger_score']}/10  "
                  f"{s.get('source_type','?')[3:]} → {s.get('sink_type','?')[3:]}")

            r = run_one(s)
            fout.write(json.dumps(r, ensure_ascii=False) + "\n")
            fout.flush()
            all_results.append(r)

    # Report
    n = len(all_results)
    n_clone  = sum(1 for r in all_results if r.get("clone_ok"))
    n_md     = sum(1 for r in all_results if r.get("skill_md_found"))
    n_src    = sum(1 for r in all_results if r.get("source_confirmed"))
    n_snk    = sum(1 for r in all_results if r.get("sink_confirmed"))
    n_ic1    = sum(1 for r in all_results if r.get("ic1_confirmed"))

    report = {
        "total":            n,
        "clone_ok":         n_clone,
        "skill_md_found":   n_md,
        "source_confirmed": n_src,
        "sink_confirmed":   n_snk,
        "ic1_confirmed":    n_ic1,
        "clone_rate":       round(n_clone/n, 3) if n else 0,
        "ic1_rate":         round(n_ic1/n_clone, 3) if n_clone else 0,
        "skills":           all_results,
    }
    REPORT.write_text(json.dumps(report, indent=2))

    print(f"\n{'='*60}")
    print(f"CAPE v3 Results")
    print(f"{'='*60}")
    print(f"  Total:             {n}")
    print(f"  Clone success:     {n_clone}/{n} ({n_clone/n*100:.0f}%)")
    print(f"  SKILL.md found:    {n_md}/{n_clone}")
    print(f"  Source confirmed:  {n_src}/{n_md}")
    print(f"  Sink confirmed:    {n_snk}/{n_md}")
    print(f"  IC-1 confirmed:    {n_ic1}/{n_md}  ← empirical rate")
    print()

    print("Confirmed IC-1 skills:")
    for r in all_results:
        if r.get("ic1_confirmed"):
            print(f"  [{r['danger_score']}/10] {r['skill_id']}")
            print(f"    {r.get('source_type','?')[3:]} → {r.get('sink_type','?')[3:]}")
            for ev in r.get("evidence", [])[:1]:
                print(f"    evidence: {ev[:70]}")

    print(f"\nReport → {REPORT}")


if __name__ == "__main__":
    main()
