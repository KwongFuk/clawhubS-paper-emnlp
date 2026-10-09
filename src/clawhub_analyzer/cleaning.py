"""Experiment 1 data cleaning pipeline."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from clawhub_analyzer.config import AppConfig
from clawhub_analyzer.db import Database


class DatasetCleaner:
    """Build analysis-ready tables from the raw SQLite store."""

    def __init__(self, config: AppConfig, db: Database):
        self.config = config
        self.db = db

    def clean_all(self, output_dir: str | Path | None = None) -> dict[str, Path]:
        root = Path(output_dir or self.config.output_dir / "cleaned")
        root.mkdir(parents=True, exist_ok=True)
        frames = self.build_frames()
        outputs: dict[str, Path] = {}
        for name, frame in frames.items():
            path = root / f"{name}.csv"
            frame.to_csv(path, index=False)
            outputs[name] = path
        schema_path = root / "dataset_summary.json"
        summary = {
            "rows": {name: int(len(frame)) for name, frame in frames.items()},
            "columns": {name: list(frame.columns) for name, frame in frames.items()},
        }
        schema_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
        outputs["dataset_summary"] = schema_path
        return outputs

    def build_frames(self) -> dict[str, pd.DataFrame]:
        skills_frame = self._build_skills_frame()
        publishers_frame = self._build_publishers_frame(skills_frame)
        search_frame = self._build_search_frame(skills_frame)
        versions_frame = self._build_versions_frame()
        return {
            "skills_cleaned": skills_frame,
            "publishers_cleaned": publishers_frame,
            "search_results_cleaned": search_frame,
            "skill_versions_cleaned": versions_frame,
        }

    def _build_skills_frame(self) -> pd.DataFrame:
        skills = [model.model_dump() for model in self.db.list_skills()]
        hints = [model.model_dump() for model in self.db.list_permission_hints()]
        versions = [model.model_dump() for model in self.db.list_skill_versions()]
        skills_frame = pd.DataFrame(skills)
        hints_frame = pd.DataFrame(hints)
        versions_frame = pd.DataFrame(versions)
        if skills_frame.empty:
            return skills_frame
        skills_frame["owner_handle"] = skills_frame["skill_id"].apply(
            lambda value: value.split("/", 1)[0] if "/" in value else None
        )
        skills_frame["skill_slug"] = skills_frame["skill_id"].apply(
            lambda value: value.split("/", 1)[1] if "/" in value else value
        )
        skills_frame["tag_count"] = skills_frame["tags"].apply(lambda value: len(value or []))
        skills_frame["tags_joined"] = skills_frame["tags"].apply(
            lambda value: "|".join(value) if isinstance(value, list) else ""
        )
        skills_frame["has_install_command"] = skills_frame["install_command"].fillna("").ne("")
        skills_frame["has_scan_status"] = skills_frame["scan_status"].fillna("").ne("")
        skills_frame["is_security_flagged"] = skills_frame["scan_status"].fillna("").str.lower().isin(
            ["malicious", "suspicious", "error", "failed"]
        )
        if not versions_frame.empty:
            version_counts = (
                versions_frame.groupby("skill_id")["version"].nunique().rename("public_version_count")
            )
            skills_frame = skills_frame.merge(
                version_counts,
                how="left",
                left_on="skill_id",
                right_index=True,
            )
        else:
            skills_frame["public_version_count"] = 0
        if not hints_frame.empty:
            hints_frame["raw_evidence_count"] = hints_frame["raw_evidence"].apply(
                lambda value: len(value or [])
            )
            permission_columns = [
                column for column in hints_frame.columns if column.startswith("requires_")
            ]
            hints_frame["permission_flag_count"] = hints_frame[permission_columns].sum(axis=1)
            skills_frame = skills_frame.merge(hints_frame, how="left", on="skill_id")
        for column in skills_frame.columns:
            if column.startswith("requires_"):
                skills_frame[column] = skills_frame[column].astype("boolean").fillna(False).astype(bool)
        skills_frame["permission_flag_count"] = skills_frame["permission_flag_count"].fillna(0).astype(int)
        skills_frame["raw_evidence_count"] = skills_frame["raw_evidence_count"].fillna(0).astype(int)
        skills_frame["stars"] = skills_frame["stars"].fillna(0).astype(int)
        skills_frame["downloads"] = skills_frame["downloads"].fillna(0).astype(int)
        skills_frame["public_version_count"] = skills_frame["public_version_count"].fillna(0).astype(int)
        skills_frame = skills_frame.sort_values(
            ["downloads", "stars", "skill_id"], ascending=[False, False, True]
        ).reset_index(drop=True)
        return skills_frame

    def _build_publishers_frame(self, skills_frame: pd.DataFrame) -> pd.DataFrame:
        publishers = [model.model_dump() for model in self.db.list_publishers()]
        publishers_frame = pd.DataFrame(publishers)
        if publishers_frame.empty:
            return publishers_frame
        if not skills_frame.empty:
            agg = (
                skills_frame.groupby("owner_handle")
                .agg(
                    observed_skill_count=("skill_id", "count"),
                    observed_total_stars=("stars", "sum"),
                    observed_total_downloads=("downloads", "sum"),
                    highlighted_skill_count=("is_highlighted", "sum"),
                    popular_skill_count=("is_popular", "sum"),
                    permission_hit_skill_count=("permission_flag_count", lambda value: int((value > 0).sum())),
                )
                .reset_index()
            )
            publishers_frame = publishers_frame.merge(
                agg,
                how="left",
                left_on="author_handle",
                right_on="owner_handle",
            )
            publishers_frame = publishers_frame.drop(columns=["owner_handle"])
        return publishers_frame.sort_values(
            ["observed_total_downloads", "observed_total_stars", "author_handle"],
            ascending=[False, False, True],
            na_position="last",
        ).reset_index(drop=True)

    def _build_search_frame(self, skills_frame: pd.DataFrame) -> pd.DataFrame:
        search_rows = [model.model_dump() for model in self.db.list_search_results()]
        search_frame = pd.DataFrame(search_rows)
        if search_frame.empty:
            return search_frame
        search_frame["is_top_10"] = search_frame["rank"] <= 10
        if not skills_frame.empty:
            lookup_columns = [
                "skill_id",
                "owner_handle",
                "skill_slug",
                "downloads",
                "stars",
                "is_highlighted",
                "is_popular",
                "permission_flag_count",
            ]
            search_frame = search_frame.merge(
                skills_frame[lookup_columns].drop_duplicates(subset=["skill_id"]),
                how="left",
                on="skill_id",
            )
        return search_frame.sort_values(["query", "rank", "timestamp"]).reset_index(drop=True)

    def _build_versions_frame(self) -> pd.DataFrame:
        versions = [model.model_dump() for model in self.db.list_skill_versions()]
        versions_frame = pd.DataFrame(versions)
        if versions_frame.empty:
            return versions_frame
        versions_frame["has_changelog"] = versions_frame["changelog"].fillna("").ne("")
        versions_frame["has_bundle_url"] = versions_frame["bundle_url"].fillna("").ne("")
        versions_frame["has_scan_status"] = versions_frame["scan_status"].fillna("").ne("")
        return versions_frame.sort_values(["skill_id", "version"], ascending=[True, False]).reset_index(
            drop=True
        )
