"""End-to-end Experiment 1 runner."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from clawhub_analyzer.analysis import ExperimentAnalyzer
from clawhub_analyzer.cleaning import DatasetCleaner
from clawhub_analyzer.config import AppConfig
from clawhub_analyzer.crawler import Crawler
from clawhub_analyzer.db import Database
from clawhub_analyzer.exporters import Exporter


async def run_experiment1(
    config: AppConfig,
    *,
    max_pages: int | None = None,
    use_full_index: bool = False,
    index_max_pages: int | None = None,
) -> dict[str, object]:
    db = Database(config.db_path)
    crawler = Crawler(config, db)
    try:
        crawl_summary = await crawler.crawl(
            max_pages=max_pages or config.max_pages,
            use_full_index=use_full_index,
            index_max_pages=index_max_pages,
        )
        exporter = Exporter(config, db)
        raw_exports = {
            "skills_csv": exporter.export_skills_csv(),
            "skills_jsonl": exporter.export_skills_jsonl(),
            "publishers_csv": exporter.export_publishers_csv(),
            "search_results_csv": exporter.export_search_results_csv(),
            "permission_hints_csv": exporter.export_permission_hints_csv(),
        }
        cleaner = DatasetCleaner(config, db)
        cleaned_exports = cleaner.clean_all()
        analyzer = ExperimentAnalyzer(config, db)
        report_exports = analyzer.generate_report()
        return {
            "crawl_summary": asdict(crawl_summary),
            "raw_exports": {key: str(value) for key, value in raw_exports.items()},
            "cleaned_exports": {key: str(value) for key, value in cleaned_exports.items()},
            "report_exports": {key: str(value) for key, value in report_exports.items()},
        }
    finally:
        await crawler.close()
        db.close()


def normalize_output_paths(payload: dict[str, object]) -> dict[str, object]:
    normalized = {}
    for key, value in payload.items():
        if isinstance(value, Path):
            normalized[key] = str(value)
        elif isinstance(value, dict):
            normalized[key] = normalize_output_paths(value)
        else:
            normalized[key] = value
    return normalized
