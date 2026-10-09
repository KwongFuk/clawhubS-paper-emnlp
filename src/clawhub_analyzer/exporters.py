"""Dataset export helpers."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from clawhub_analyzer.config import AppConfig
from clawhub_analyzer.db import Database


class Exporter:
    def __init__(self, config: AppConfig, db: Database):
        self.config = config
        self.db = db

    def export_skills_csv(self, path: str | Path | None = None) -> Path:
        target = Path(path or self.config.output_dir / "skills.csv")
        frame = self._frame(self.db.list_skills())
        frame.to_csv(target, index=False)
        return target

    def export_skills_jsonl(self, path: str | Path | None = None) -> Path:
        target = Path(path or self.config.output_dir / "skills.jsonl")
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("w", encoding="utf-8") as handle:
            for model in self.db.list_skills():
                handle.write(json.dumps(model.model_dump(), ensure_ascii=False) + "\n")
        return target

    def export_publishers_csv(self, path: str | Path | None = None) -> Path:
        target = Path(path or self.config.output_dir / "publishers.csv")
        self._frame(self.db.list_publishers()).to_csv(target, index=False)
        return target

    def export_skill_versions_csv(self, path: str | Path | None = None) -> Path:
        target = Path(path or self.config.output_dir / "skill_versions.csv")
        self._frame(self.db.list_skill_versions()).to_csv(target, index=False)
        return target

    def export_skill_files_csv(self, path: str | Path | None = None) -> Path:
        target = Path(path or self.config.output_dir / "skill_files.csv")
        self._frame(self.db.list_skill_files()).to_csv(target, index=False)
        return target

    def export_skill_comments_csv(self, path: str | Path | None = None) -> Path:
        target = Path(path or self.config.output_dir / "skill_comments.csv")
        self._frame(self.db.list_skill_comments()).to_csv(target, index=False)
        return target

    def export_search_results_csv(self, path: str | Path | None = None) -> Path:
        target = Path(path or self.config.output_dir / "search_results.csv")
        self._frame(self.db.list_search_results()).to_csv(target, index=False)
        return target

    def export_permission_hints_csv(self, path: str | Path | None = None) -> Path:
        target = Path(path or self.config.output_dir / "permission_hints.csv")
        self._frame(self.db.list_permission_hints()).to_csv(target, index=False)
        return target

    @staticmethod
    def _frame(models) -> pd.DataFrame:
        rows = [model.model_dump() for model in models]
        frame = pd.DataFrame(rows)
        for column in ("tags", "raw_evidence", "metadata_os", "metadata_systems"):
            if column in frame.columns:
                frame[column] = frame[column].apply(
                    lambda value: json.dumps(value, ensure_ascii=False) if isinstance(value, list) else value
                )
        return frame
