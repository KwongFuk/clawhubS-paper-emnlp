"""Experiment 1 analysis helpers."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

import pandas as pd

from clawhub_analyzer.config import AppConfig
from clawhub_analyzer.cleaning import DatasetCleaner
from clawhub_analyzer.db import Database
from clawhub_analyzer.utils import now_utc_iso


class ExperimentAnalyzer:
    """Compute descriptive statistics for experiment 1."""

    def __init__(self, config: AppConfig, db: Database):
        self.config = config
        self.db = db
        self.cleaner = DatasetCleaner(config, db)

    def generate_report(self, output_dir: str | Path | None = None) -> dict[str, Path]:
        root = Path(output_dir or self.config.output_dir / "reports")
        root.mkdir(parents=True, exist_ok=True)
        frames = self.cleaner.build_frames()
        summary = self.summarize(frames)
        summary_path = root / "experiment1_summary.json"
        summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
        markdown_path = root / "experiment1_report.md"
        markdown_path.write_text(self._to_markdown(summary), encoding="utf-8")
        return {
            "summary_json": summary_path,
            "report_markdown": markdown_path,
        }

    def summarize(self, frames: dict[str, pd.DataFrame] | None = None) -> dict[str, Any]:
        frames = frames or self.cleaner.build_frames()
        skills = frames["skills_cleaned"]
        publishers = frames["publishers_cleaned"]
        search = frames["search_results_cleaned"]
        versions = frames["skill_versions_cleaned"]
        permission_columns = [column for column in skills.columns if column.startswith("requires_")]

        tag_counter: Counter[str] = Counter()
        if not skills.empty and "tags" in skills.columns:
            for tags in skills["tags"].tolist():
                for tag in tags or []:
                    tag_counter[tag] += 1

        permission_breakdown = {}
        if not skills.empty:
            for column in permission_columns:
                permission_breakdown[column] = int(skills[column].sum())

        scan_distribution = {}
        if not skills.empty:
            scan_distribution = (
                skills["scan_status"]
                .fillna("missing")
                .astype(str)
                .replace({"": "missing"})
                .value_counts()
                .to_dict()
            )

        query_unique_counts = {}
        if not search.empty:
            query_unique_counts = (
                search.groupby("query")["skill_id"].nunique().sort_values(ascending=False).to_dict()
            )

        high_risk_columns = [
            "requires_github",
            "requires_slack",
            "requires_notion",
            "requires_calendar",
            "requires_email",
            "requires_docs",
            "requires_pdf",
            "requires_api_key",
            "requires_oauth",
            "requires_token",
            "requires_local_file_access",
            "requires_shell_access",
            "requires_browser_access",
        ]
        if not skills.empty:
            high_risk_frame = skills[
                ["skill_id", "name", "downloads", "stars", "permission_flag_count", *high_risk_columns]
            ].copy()
            high_risk_frame = high_risk_frame.sort_values(
                ["permission_flag_count", "downloads", "stars"],
                ascending=[False, False, False],
            )
            high_risk_records = high_risk_frame.head(15).to_dict(orient="records")
        else:
            high_risk_records = []

        summary = {
            "generated_at": now_utc_iso(),
            "counts": {
                "skills": int(len(skills)),
                "publishers": int(len(publishers)),
                "search_results": int(len(search)),
                "skill_versions": int(len(versions)),
                "queries": int(search["query"].nunique()) if not search.empty else 0,
            },
            "coverage": {
                "highlighted_skills": int(skills["is_highlighted"].sum()) if not skills.empty else 0,
                "popular_skills": int(skills["is_popular"].sum()) if not skills.empty else 0,
                "with_install_command": int(skills["has_install_command"].sum()) if not skills.empty else 0,
                "with_scan_status": int(skills["has_scan_status"].sum()) if not skills.empty else 0,
                "with_public_versions": int((skills["public_version_count"] > 0).sum()) if not skills.empty else 0,
                "with_permission_hints": int((skills["permission_flag_count"] > 0).sum()) if not skills.empty else 0,
            },
            "descriptive": {
                "avg_stars": round(float(skills["stars"].mean()), 2) if not skills.empty else 0.0,
                "median_stars": round(float(skills["stars"].median()), 2) if not skills.empty else 0.0,
                "avg_downloads": round(float(skills["downloads"].mean()), 2) if not skills.empty else 0.0,
                "median_downloads": round(float(skills["downloads"].median()), 2) if not skills.empty else 0.0,
            },
            "scan_status_distribution": scan_distribution,
            "permission_breakdown": permission_breakdown,
            "top_tags": tag_counter.most_common(15),
            "top_skills_by_downloads": self._top_records(
                skills,
                columns=["skill_id", "name", "downloads", "stars", "permission_flag_count"],
                sort_by=["downloads", "stars"],
                limit=15,
            ),
            "top_skills_by_stars": self._top_records(
                skills,
                columns=["skill_id", "name", "stars", "downloads", "permission_flag_count"],
                sort_by=["stars", "downloads"],
                limit=15,
            ),
            "top_publishers_by_downloads": self._top_records(
                publishers,
                columns=[
                    "author_handle",
                    "display_name",
                    "observed_skill_count",
                    "observed_total_downloads",
                    "observed_total_stars",
                ],
                sort_by=["observed_total_downloads", "observed_total_stars"],
                limit=15,
            ),
            "query_unique_skill_counts": query_unique_counts,
            "high_risk_skill_candidates": high_risk_records,
        }
        return summary

    @staticmethod
    def _top_records(
        frame: pd.DataFrame,
        *,
        columns: list[str],
        sort_by: list[str],
        limit: int,
    ) -> list[dict[str, Any]]:
        if frame.empty:
            return []
        sort_order = [False] * len(sort_by)
        subset = frame[columns].sort_values(sort_by, ascending=sort_order).head(limit)
        records = subset.to_dict(orient="records")
        return records

    @staticmethod
    def _to_markdown(summary: dict[str, Any]) -> str:
        lines = [
            "# Experiment 1 Report",
            "",
            f"Generated at: {summary['generated_at']}",
            "",
            "## Counts",
        ]
        for key, value in summary["counts"].items():
            lines.append(f"- {key}: {value}")
        lines.append("")
        lines.append("## Coverage")
        for key, value in summary["coverage"].items():
            lines.append(f"- {key}: {value}")
        lines.append("")
        lines.append("## Descriptive")
        for key, value in summary["descriptive"].items():
            lines.append(f"- {key}: {value}")
        lines.append("")
        lines.append("## Scan Status Distribution")
        for key, value in summary["scan_status_distribution"].items():
            lines.append(f"- {key}: {value}")
        lines.append("")
        lines.append("## Permission Breakdown")
        for key, value in summary["permission_breakdown"].items():
            lines.append(f"- {key}: {value}")
        lines.append("")
        lines.append("## Top Tags")
        for tag, count in summary["top_tags"]:
            lines.append(f"- {tag}: {count}")
        lines.append("")
        lines.append("## Query Unique Skill Counts")
        for query, count in summary["query_unique_skill_counts"].items():
            lines.append(f"- {query}: {count}")
        return "\n".join(lines) + "\n"
