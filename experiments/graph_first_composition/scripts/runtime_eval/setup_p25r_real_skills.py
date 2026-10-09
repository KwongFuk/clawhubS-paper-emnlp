#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import urllib.error
import urllib.request
from pathlib import Path


EXPERIMENT_ROOT = Path(__file__).resolve().parents[2]
PROJECT_ROOT = EXPERIMENT_ROOT.parents[1]
DB_PATH = PROJECT_ROOT / "data/experiment1_split/merged.sqlite3"
MANIFEST_PATH = EXPERIMENT_ROOT / "data/analysis_runtime_p25r_500/p25r_manifest.json"
MATERIALIZE_ROOT = Path("/Users/guangfu/.openclaw/workspace/runtime_skills/p25r_500")
INVENTORY_PATH = EXPERIMENT_ROOT / "data/analysis_runtime_p25r_500/skill_inventory.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", default=str(MANIFEST_PATH))
    parser.add_argument("--dest-root", default=str(MATERIALIZE_ROOT))
    parser.add_argument("--inventory-out", default=str(INVENTORY_PATH))
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def resolve_text_path(raw_path: str | None) -> Path | None:
    if not raw_path:
        return None
    return Path(raw_path) if raw_path.startswith("/") else PROJECT_ROOT / raw_path


def fetch_skill_md(skill_slug: str, version: str) -> str:
    url = f"https://clawhub.ai/api/v1/skills/{skill_slug}/file?path=SKILL.md&version={version}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as response:
        return response.read().decode("utf-8", errors="replace")


def synthesize_skill_md(skill_id: str, name: str, description: str | None) -> str:
    summary = (description or "No description available in the archived metadata.").strip()
    return (
        f"# {name}\n\n"
        "## Purpose\n\n"
        f"- Skill ID: `{skill_id}`\n"
        f"- Summary: {summary}\n\n"
        "## Experimental Note\n\n"
        "This SKILL.md was synthesized from archived metadata because the original file was unavailable "
        "during P25R setup. Treat it as a minimal placeholder for runtime materialization only.\n"
    )


def materialize_skill(
    conn: sqlite3.Connection, skill_id: str, dest_root: Path, overwrite: bool
) -> dict[str, object]:
    skill_row = conn.execute(
        "select current_version, page_url, name, description from skills where skill_id=?",
        (skill_id,),
    ).fetchone()
    if skill_row is None:
        raise RuntimeError(f"skill missing from db: {skill_id}")

    version = skill_row["current_version"]
    slug = skill_id.split("/", 1)[1]
    skill_dir = dest_root / slug
    if skill_dir.exists() and overwrite:
        shutil.rmtree(skill_dir)
    skill_dir.mkdir(parents=True, exist_ok=True)

    copied_files: list[str] = []
    fetched = False
    synthesized = False

    rows = list(
        conn.execute(
            "select path, text_path from skill_files where skill_id=? and version=? order by path",
            (skill_id, version),
        )
    )
    for row in rows:
        src = resolve_text_path(row["text_path"])
        if src is None or not src.exists():
            continue
        dest = skill_dir / row["path"]
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        copied_files.append(row["path"])

    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        skill_md.parent.mkdir(parents=True, exist_ok=True)
        try:
            skill_md.write_text(fetch_skill_md(slug, version), encoding="utf-8")
            fetched = True
        except urllib.error.HTTPError:
            skill_md.write_text(
                synthesize_skill_md(skill_id, skill_row["name"], skill_row["description"]),
                encoding="utf-8",
            )
            synthesized = True
        copied_files.append("SKILL.md")

    origin = {
        "skill_id": skill_id,
        "name": skill_row["name"],
        "version": version,
        "page_url": skill_row["page_url"],
        "materialized_files": copied_files,
        "fetched_skill_md": fetched,
        "synthesized_skill_md": synthesized,
    }
    (skill_dir / "_p25r_origin.json").write_text(json.dumps(origin, indent=2), encoding="utf-8")
    return {
        "skill_id": skill_id,
        "slug": slug,
        "version": version,
        "dest_dir": str(skill_dir),
        "skill_md": str(skill_md),
        "copied_files": copied_files,
        "fetched_skill_md": fetched,
        "synthesized_skill_md": synthesized,
    }


def main() -> int:
    args = parse_args()
    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    dest_root = Path(args.dest_root)
    dest_root.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    skill_ids = sorted(
        {
            skill_id
            for case in manifest["cases"]
            for skill_id in (case["source_skill_id"], case["sink_skill_id"])
        }
    )

    inventory = {"dest_root": str(dest_root), "skills": []}
    for skill_id in skill_ids:
        inventory["skills"].append(materialize_skill(conn, skill_id, dest_root, args.overwrite))

    out = Path(args.inventory_out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(inventory, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"materialized_skill_count": len(inventory["skills"]), "dest_root": str(dest_root)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
