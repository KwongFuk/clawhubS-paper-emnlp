"""
config.py — Unified corpus configuration for all TrustDrift experiments.
=========================================================================
SINGLE SOURCE OF TRUTH: always uses the full 17,738-skill DB corpus.
Never use JSONL (6,912 skills) as primary data source.

Usage in any script:
    from config import DB, DATA_ROOT, RESULTS, load_all_skills
"""

import os, sqlite3, logging
from pathlib import Path
from typing import Dict, List, Optional, Tuple

log = logging.getLogger("config")

# ── Paths ──────────────────────────────────────────────────────────────────
HERE      = Path(__file__).parent
ROOT      = HERE.parent.parent.parent          # clawhubS/

# DB: full 17,738-skill corpus
DB        = Path(os.environ.get("CLAWHUB_DB",
              str(ROOT / "data/experiment1_split/merged.sqlite3")))

# DATA_ROOT: base for text_path in skill_files table
# text_path format: "data/experiment1_split/html_snapshots_local/..."
# so DATA_ROOT must be clawhubS/ root
DATA_ROOT = Path(os.environ.get("DATA_ROOT", str(ROOT)))

# Results output directory
RESULTS   = Path(os.environ.get("OUTDIR",
              str(HERE / "results")))

# Reference JSONL (version transitions, 6,912 unique skills)
# Use ONLY for temporal/transition analysis, NOT as primary corpus
TRANS_JSONL = Path(os.environ.get("CLAWHUB_JSONL",
                str(HERE / "data/version_transitions_enriched.jsonl")))


# ── Full corpus loader ──────────────────────────────────────────────────────

def load_all_skills(
    limit: int = 0,
    load_skill_md: bool = True,
    load_all_files: bool = False,
) -> List[Dict]:
    """
    Load ALL 17,738 skills from the DB with optional SKILL.md content.

    Args:
        limit:          If >0, limit number of skills (for testing).
        load_skill_md:  If True, read latest SKILL.md text from disk.
        load_all_files: If True, also return list of all file paths per skill.

    Returns:
        List of dicts with keys:
            skill_id, name, description, skill_text,
            stars, downloads, author_handle,
            [file_paths] if load_all_files
    """
    if not DB.exists():
        raise FileNotFoundError(f"DB not found: {DB}")

    conn = sqlite3.connect(str(DB))

    # Fetch skills + latest SKILL.md text_path in one query
    query = """
        SELECT
            s.skill_id,
            s.name,
            s.description,
            s.stars,
            s.downloads,
            s.author_handle,
            sf.text_path
        FROM skills s
        LEFT JOIN (
            SELECT skill_id, text_path,
                   ROW_NUMBER() OVER (
                       PARTITION BY skill_id
                       ORDER BY version DESC
                   ) AS rn
            FROM skill_files
            WHERE path = 'SKILL.md'
        ) sf ON s.skill_id = sf.skill_id AND sf.rn = 1
        WHERE s.description IS NOT NULL
          AND length(s.description) > 10
    """
    if limit:
        query += f" LIMIT {limit}"

    rows = conn.execute(query).fetchall()
    log.info(f"Loaded {len(rows)} skills from DB (limit={limit or 'none'})")

    skills = []
    missing = 0
    for skill_id, name, description, stars, downloads, author, text_path in rows:
        skill_text = ""
        if load_skill_md and text_path:
            fp = DATA_ROOT / text_path
            try:
                skill_text = fp.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                missing += 1

        entry = {
            "skill_id":    skill_id or "",
            "name":        name or "",
            "description": description or "",
            "skill_text":  skill_text,
            "stars":       stars or 0,
            "downloads":   downloads or 0,
            "author":      author or "",
        }

        if load_all_files:
            file_rows = conn.execute(
                "SELECT path, text_path FROM skill_files WHERE skill_id=?",
                (skill_id,)
            ).fetchall()
            entry["file_paths"] = [(p, tp) for p, tp in file_rows if tp]

        skills.append(entry)

    conn.close()
    if missing:
        log.warning(f"  {missing}/{len(rows)} skills missing SKILL.md on disk")
    else:
        log.info(f"  All SKILL.md files loaded successfully")

    return skills


def read_file(text_path: str) -> Optional[str]:
    """Read a skill file given its text_path from the DB."""
    fp = DATA_ROOT / text_path
    try:
        return fp.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return None


def get_conn() -> sqlite3.Connection:
    """Get a DB connection."""
    return sqlite3.connect(str(DB))


# ── Quick sanity check ──────────────────────────────────────────────────────
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(message)s")
    print(f"DB:        {DB} (exists={DB.exists()})")
    print(f"DATA_ROOT: {DATA_ROOT}")
    print(f"RESULTS:   {RESULTS}")

    skills = load_all_skills(limit=5, load_skill_md=True)
    print(f"\nLoaded {len(skills)} skills (test limit=5)")
    for s in skills[:3]:
        print(f"  {s['skill_id']}: desc={len(s['description'])}ch, "
              f"skill_md={len(s['skill_text'])}ch")
