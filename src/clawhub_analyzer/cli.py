"""Command line interface."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import typer
from rich.console import Console
from rich.logging import RichHandler
from rich.table import Table

from clawhub_analyzer.analysis import ExperimentAnalyzer
from clawhub_analyzer.cleaning import DatasetCleaner
from clawhub_analyzer.config import AppConfig, load_config
from clawhub_analyzer.crawler import Crawler
from clawhub_analyzer.db import Database
from clawhub_analyzer.experiment1 import run_experiment1
from clawhub_analyzer.exporters import Exporter
from clawhub_analyzer.hypergraph import generate_hypergraph_risk_artifacts
from clawhub_analyzer.hypergraph_pairs import generate_hypergraph_pair_risk_artifacts
from clawhub_analyzer.sc_candidate_universe import generate_sc_candidate_universe_artifacts
from clawhub_analyzer.sc_stateful_pairs import generate_sc_stateful_pair_artifacts
from clawhub_analyzer.sc_seed_pool import generate_sc_seed_pool_artifacts
from clawhub_analyzer.sc_runtime_manifest import generate_sc_runtime_manifest_artifacts
from clawhub_analyzer.sc_sampling import generate_sc_runtime_sampling_artifacts

app = typer.Typer(add_completion=False, no_args_is_help=True)
console = Console()


def _setup_logging(level: str) -> None:
    resolved_level = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(
        level=resolved_level,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(console=console, rich_tracebacks=True)],
        force=True,
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(max(logging.WARNING, resolved_level))


def _load_runtime_config(
    *,
    max_pages: int | None = None,
    use_playwright: bool | None = None,
    dry_run: bool | None = None,
    db_path: Path | None = None,
    output_dir: Path | None = None,
    search_queries: list[str] | None = None,
    search_top_k: int | None = None,
    api_page_size: int | None = None,
    api_index_sort: str | None = None,
) -> AppConfig:
    config = load_config(
        max_pages=max_pages,
        use_playwright=use_playwright,
        dry_run=dry_run,
        db_path=db_path,
        output_dir=output_dir,
        search_queries=search_queries,
        search_top_k=search_top_k,
        api_page_size=api_page_size,
        api_index_sort=api_index_sort,
    )
    _setup_logging(config.log_level)
    return config


def _default_resume_state_path(
    db_path: Path,
    *,
    mode: str,
    shard_index: int,
    shard_count: int,
) -> Path:
    suffix = f".{mode}.shard{shard_index}of{shard_count}.state.json"
    return db_path.with_name(f"{db_path.stem}{suffix}")


@app.command()
def crawl(
    max_pages: int = typer.Option(100, help="Maximum number of skill detail pages to collect."),
    full_index: bool = typer.Option(
        False,
        "--full-index/--no-full-index",
        help="Use the official /api/v1/skills index for full-catalog discovery before HTML detail collection.",
    ),
    index_max_pages: int | None = typer.Option(
        None,
        help="Optional cap for public API index pages when --full-index is enabled.",
    ),
    use_playwright: bool = typer.Option(True, help="Enable Playwright fallback for JS-rendered pages."),
    dry_run: bool = typer.Option(False, help="Do not write to SQLite."),
    db_path: Path | None = typer.Option(None, help="Override SQLite database path."),
    output_dir: Path | None = typer.Option(None, help="Override output directory."),
) -> None:
    config = _load_runtime_config(
        max_pages=max_pages,
        use_playwright=use_playwright,
        dry_run=dry_run,
        db_path=db_path,
        output_dir=output_dir,
    )
    db = Database(config.db_path)

    async def runner() -> None:
        crawler = Crawler(config, db)
        try:
            summary = await crawler.crawl(
                max_pages=max_pages,
                use_full_index=full_index,
                index_max_pages=index_max_pages,
            )
            console.print(
                f"discovered={len(summary.discovered_skill_urls)} collected={len(summary.collected_skills)} "
                f"publishers={len(summary.publisher_urls)} index_pages={summary.index_pages_fetched}"
            )
        finally:
            await crawler.close()
            db.close()

    asyncio.run(runner())


@app.command("collect-home")
def collect_home(
    use_playwright: bool = typer.Option(True, help="Enable Playwright fallback."),
    dry_run: bool = typer.Option(False, help="Do not write to SQLite."),
) -> None:
    config = _load_runtime_config(use_playwright=use_playwright, dry_run=dry_run)
    db = Database(config.db_path)

    async def runner() -> None:
        crawler = Crawler(config, db)
        try:
            result = await crawler.collect_home()
            console.print(
                f"highlighted={len(result.highlighted_skills)} popular={len(result.popular_skills)}"
            )
        finally:
            await crawler.close()
            db.close()

    asyncio.run(runner())


@app.command("collect-browse")
def collect_browse(
    use_playwright: bool = typer.Option(True, help="Enable Playwright fallback."),
    dry_run: bool = typer.Option(False, help="Do not write to SQLite."),
) -> None:
    config = _load_runtime_config(use_playwright=use_playwright, dry_run=dry_run)
    db = Database(config.db_path)

    async def runner() -> None:
        crawler = Crawler(config, db)
        try:
            cards = await crawler.collect_browse()
            console.print(f"browse_cards={len(cards)}")
        finally:
            await crawler.close()
            db.close()

    asyncio.run(runner())


@app.command("collect-index")
def collect_index(
    sort: str = typer.Option("downloads", help="Public API sort: downloads, updated, stars, installs, name."),
    page_size: int = typer.Option(200, help="Public API page size. Current official max is 200."),
    max_pages: int | None = typer.Option(None, help="Limit API pages for testing. Omit for full index."),
    shard_index: int = typer.Option(0, min=0, help="Shard index for distributed crawling."),
    shard_count: int = typer.Option(1, min=1, help="Total shard count for distributed crawling."),
    resume: bool = typer.Option(True, "--resume/--no-resume", help="Resume from saved cursor checkpoint."),
    state_path: Path | None = typer.Option(None, help="Optional cursor checkpoint JSON path."),
    dry_run: bool = typer.Option(False, help="Do not write to SQLite."),
    db_path: Path | None = typer.Option(None, help="Override SQLite database path."),
) -> None:
    config = _load_runtime_config(
        dry_run=dry_run,
        db_path=db_path,
        api_page_size=page_size,
        api_index_sort=sort,
    )
    db = Database(config.db_path)
    resume_state_path = (
        state_path
        if resume and state_path is not None
        else _default_resume_state_path(
            config.db_path,
            mode="index",
            shard_index=shard_index,
            shard_count=shard_count,
        )
        if resume
        else None
    )

    async def runner() -> None:
        crawler = Crawler(config, db)
        try:
            summary = await crawler.collect_index(
                sort=sort,
                page_size=page_size,
                max_pages=max_pages,
                shard_index=shard_index,
                shard_count=shard_count,
                resume_state_path=resume_state_path,
            )
            console.print(
                f"index_pages={summary.pages_fetched} slugs={summary.slugs_discovered} "
                f"skills={summary.skills_upserted} versions={summary.versions_upserted}"
            )
        finally:
            await crawler.close()
            db.close()

    asyncio.run(runner())


@app.command("collect-public-archive")
def collect_public_archive(
    sort: str = typer.Option("downloads", help="Public API sort: downloads, updated, stars, installs, name."),
    page_size: int = typer.Option(200, help="Public API page size. Current official max is 200."),
    max_pages: int | None = typer.Option(None, help="Optional limit for API index pages."),
    max_skills: int | None = typer.Option(None, help="Optional limit for total skills to archive."),
    shard_index: int = typer.Option(0, min=0, help="Shard index for distributed crawling."),
    shard_count: int = typer.Option(1, min=1, help="Total shard count for distributed crawling."),
    include_versions: bool = typer.Option(True, "--versions/--no-versions", help="Collect version history."),
    include_files: bool = typer.Option(True, "--files/--no-files", help="Collect per-version file manifests."),
    include_file_texts: bool = typer.Option(
        True,
        "--file-texts/--no-file-texts",
        help="Fetch publicly readable text files via /api/v1/skills/{slug}/file.",
    ),
    include_html_details: bool = typer.Option(
        False,
        "--html-details/--no-html-details",
        help="Also collect all discovered HTML skill pages for install commands, comments, and visible badges.",
    ),
    max_version_pages: int | None = typer.Option(None, help="Optional cap for paginated version pages per skill."),
    version_page_size: int = typer.Option(100, help="Page size for /versions pagination."),
    max_files_per_version: int | None = typer.Option(
        None,
        help="Optional cap for files fetched per version. Omit for full public manifest coverage.",
    ),
    use_playwright: bool = typer.Option(True, help="Enable Playwright for optional HTML detail collection."),
    resume: bool = typer.Option(True, "--resume/--no-resume", help="Resume from saved cursor checkpoint."),
    state_path: Path | None = typer.Option(None, help="Optional cursor checkpoint JSON path."),
    dry_run: bool = typer.Option(False, help="Do not write to SQLite."),
    db_path: Path | None = typer.Option(None, help="Override SQLite database path."),
) -> None:
    config = _load_runtime_config(
        use_playwright=use_playwright,
        dry_run=dry_run,
        db_path=db_path,
        api_page_size=page_size,
        api_index_sort=sort,
    )
    db = Database(config.db_path)
    resume_state_path = (
        state_path
        if resume and state_path is not None
        else _default_resume_state_path(
            config.db_path,
            mode="archive",
            shard_index=shard_index,
            shard_count=shard_count,
        )
        if resume
        else None
    )

    async def runner() -> None:
        crawler = Crawler(config, db)
        try:
            summary = await crawler.collect_public_archive(
                sort=sort,
                page_size=page_size,
                max_pages=max_pages,
                max_skills=max_skills,
                shard_index=shard_index,
                shard_count=shard_count,
                include_versions=include_versions,
                include_files=include_files,
                include_file_texts=include_file_texts,
                include_html_details=include_html_details,
                max_version_pages=max_version_pages,
                version_page_size=version_page_size,
                max_files_per_version=max_files_per_version,
                resume_state_path=resume_state_path,
            )
            console.print(
                f"index_pages={summary.api_summary.pages_fetched} "
                f"skills={summary.api_summary.skills_processed} "
                f"skipped={summary.api_summary.skills_skipped} "
                f"versions={summary.api_summary.versions_upserted} "
                f"files={summary.api_summary.files_upserted} "
                f"file_texts={summary.api_summary.file_texts_saved} "
                f"html_details={summary.html_skill_pages_collected}"
            )
        finally:
            await crawler.close()
            db.close()

    asyncio.run(runner())


@app.command("collect-search")
def collect_search(
    query: str = typer.Option(..., help="Search keyword."),
    top_k: int = typer.Option(20, help="Keep top-k results."),
    use_playwright: bool = typer.Option(True, help="Enable Playwright fallback."),
    dry_run: bool = typer.Option(False, help="Do not write to SQLite."),
) -> None:
    config = _load_runtime_config(use_playwright=use_playwright, dry_run=dry_run, search_top_k=top_k)
    db = Database(config.db_path)

    async def runner() -> None:
        crawler = Crawler(config, db)
        try:
            results = await crawler.search_collector.collect_query(query, top_k=top_k)
            console.print(f"search_results={len(results)} query={query}")
        finally:
            await crawler.close()
            db.close()

    asyncio.run(runner())


@app.command("collect-skill")
def collect_skill(
    url: str = typer.Option(..., help="Skill detail URL."),
    use_playwright: bool = typer.Option(True, help="Enable Playwright fallback."),
    dry_run: bool = typer.Option(False, help="Do not write to SQLite."),
) -> None:
    config = _load_runtime_config(use_playwright=use_playwright, dry_run=dry_run)
    db = Database(config.db_path)

    async def runner() -> None:
        crawler = Crawler(config, db)
        try:
            bundle = await crawler.skill_collector.collect(url)
            console.print(bundle.skill.skill_id)
        finally:
            await crawler.close()
            db.close()

    asyncio.run(runner())


@app.command("collect-publisher")
def collect_publisher(
    url: str = typer.Option(..., help="Publisher profile URL."),
    use_playwright: bool = typer.Option(True, help="Enable Playwright fallback."),
    dry_run: bool = typer.Option(False, help="Do not write to SQLite."),
) -> None:
    config = _load_runtime_config(use_playwright=use_playwright, dry_run=dry_run)
    db = Database(config.db_path)

    async def runner() -> None:
        crawler = Crawler(config, db)
        try:
            result = await crawler.publisher_collector.collect(url)
            console.print(result.publisher.author_handle)
        finally:
            await crawler.close()
            db.close()

    asyncio.run(runner())


@app.command()
def export(
    format: str = typer.Option("all", help="csv, jsonl, or all"),
    db_path: Path | None = typer.Option(None, help="Override SQLite database path."),
    output_dir: Path | None = typer.Option(None, help="Override output directory."),
) -> None:
    config = _load_runtime_config(db_path=db_path, output_dir=output_dir)
    db = Database(config.db_path)
    exporter = Exporter(config, db)
    try:
        outputs: list[Path] = []
        if format in {"csv", "all"}:
            outputs.extend(
                [
                    exporter.export_skills_csv(),
                    exporter.export_skill_versions_csv(),
                    exporter.export_skill_files_csv(),
                    exporter.export_skill_comments_csv(),
                    exporter.export_publishers_csv(),
                    exporter.export_search_results_csv(),
                    exporter.export_permission_hints_csv(),
                ]
            )
        if format in {"jsonl", "all"}:
            outputs.append(exporter.export_skills_jsonl())
        for path in outputs:
            console.print(str(path))
    finally:
        db.close()


@app.command()
def stats(db_path: Path | None = typer.Option(None, help="Override SQLite database path.")) -> None:
    config = _load_runtime_config(db_path=db_path)
    db = Database(config.db_path)
    try:
        values = db.stats()
        table = Table(title="ClawHub Analyzer Stats")
        table.add_column("Metric")
        table.add_column("Value", justify="right")
        for key, value in values.items():
            table.add_row(key, f"{value}")
        console.print(table)
    finally:
        db.close()


@app.command("experiment1-run")
def experiment1_run(
    max_pages: int = typer.Option(120, help="Maximum number of skill detail pages to collect."),
    search_top_k: int = typer.Option(20, help="Top-k results per search query."),
    full_index: bool = typer.Option(
        False,
        "--full-index/--no-full-index",
        help="Use the official /api/v1/skills index for full-catalog discovery during experiment 1.",
    ),
    index_max_pages: int | None = typer.Option(
        None,
        help="Optional cap for public API index pages when --full-index is enabled.",
    ),
    use_playwright: bool = typer.Option(True, help="Enable Playwright rendering."),
    dry_run: bool = typer.Option(False, help="Do not write to SQLite."),
    db_path: Path | None = typer.Option(None, help="Override SQLite database path."),
    output_dir: Path | None = typer.Option(None, help="Override output directory."),
) -> None:
    config = _load_runtime_config(
        max_pages=max_pages,
        use_playwright=use_playwright,
        dry_run=dry_run,
        db_path=db_path,
        output_dir=output_dir,
        search_top_k=search_top_k,
    )

    async def runner() -> None:
        result = await run_experiment1(
            config,
            max_pages=max_pages,
            use_full_index=full_index,
            index_max_pages=index_max_pages,
        )
        console.print(result)

    asyncio.run(runner())


@app.command("experiment1-clean")
def experiment1_clean(
    db_path: Path | None = typer.Option(None, help="Override SQLite database path."),
    output_dir: Path | None = typer.Option(None, help="Override output directory."),
) -> None:
    config = _load_runtime_config(db_path=db_path, output_dir=output_dir)
    db = Database(config.db_path)
    try:
        cleaner = DatasetCleaner(config, db)
        outputs = cleaner.clean_all()
        for name, path in outputs.items():
            console.print(f"{name}={path}")
    finally:
        db.close()


@app.command("experiment1-report")
def experiment1_report(
    db_path: Path | None = typer.Option(None, help="Override SQLite database path."),
    output_dir: Path | None = typer.Option(None, help="Override output directory."),
) -> None:
    config = _load_runtime_config(db_path=db_path, output_dir=output_dir)
    db = Database(config.db_path)
    try:
        analyzer = ExperimentAnalyzer(config, db)
        outputs = analyzer.generate_report()
        for name, path in outputs.items():
            console.print(f"{name}={path}")
    finally:
        db.close()


@app.command("stage2-hypergraph-report")
def stage2_hypergraph_report(
    db_path: Path = typer.Option(
        Path("data/experiment1_split/merged.sqlite3"),
        help="SQLite database used for the Stage 2 hypergraph analysis.",
    ),
    output_dir: Path = typer.Option(
        Path("data/experiment1_split/analysis"),
        help="Directory where the report and CSV artifacts will be written.",
    ),
    source_limit: int = typer.Option(400, help="Number of top source-capable skills to seed the search."),
    bridge_limit: int = typer.Option(
        80,
        help="Number of bridge candidates retained per source after overlap filtering.",
    ),
    sink_limit: int = typer.Option(
        50,
        help="Number of sink candidates retained per bridge after overlap filtering.",
    ),
    csv_top_k: int = typer.Option(500, help="Number of combinations to export to CSV."),
    report_top_k: int = typer.Option(20, help="Number of combinations to render in the report."),
    skill_top_k: int = typer.Option(50, help="Number of risky skills to keep in the summary outputs."),
) -> None:
    outputs = generate_hypergraph_risk_artifacts(
        db_path=db_path,
        output_dir=output_dir,
        source_limit=source_limit,
        bridge_limit=bridge_limit,
        sink_limit=sink_limit,
        csv_top_k=csv_top_k,
        report_top_k=report_top_k,
        skill_top_k=skill_top_k,
    )
    for name, path in outputs.items():
        console.print(f"{name}={path}")


@app.command("stage2-hypergraph-pair-report")
def stage2_hypergraph_pair_report(
    db_path: Path = typer.Option(
        Path("data/experiment1_split/merged.sqlite3"),
        help="SQLite database used for the Stage 2 pairwise hypergraph analysis.",
    ),
    output_dir: Path = typer.Option(
        Path("data/experiment1_split/analysis_channelcentric_v3"),
        help="Directory where the pairwise report and CSV artifacts will be written.",
    ),
    source_limit: int = typer.Option(400, help="Number of top source-capable skills to seed the search."),
    sink_limit: int = typer.Option(
        80,
        help="Number of sink candidates retained per source after overlap filtering.",
    ),
    csv_top_k: int = typer.Option(500, help="Number of pairs to export to CSV."),
    report_top_k: int = typer.Option(20, help="Number of pairs to render in the report."),
    skill_top_k: int = typer.Option(50, help="Number of risky skills to keep in the summary outputs."),
) -> None:
    outputs = generate_hypergraph_pair_risk_artifacts(
        db_path=db_path,
        output_dir=output_dir,
        source_limit=source_limit,
        sink_limit=sink_limit,
        csv_top_k=csv_top_k,
        report_top_k=report_top_k,
        skill_top_k=skill_top_k,
    )
    for name, path in outputs.items():
        console.print(f"{name}={path}")


@app.command("sc-stateful-pair-report")
def sc_stateful_pair_report(
    db_path: Path = typer.Option(
        Path("data/experiment1_split/merged.sqlite3"),
        help="SQLite database used for SC stateful pair extraction.",
    ),
    input_csv: Path = typer.Option(
        Path("experiments/graph_first_composition/data/analysis_c1_static/c1_cas_pairs.csv"),
        help="Existing SC/CAS pair table used as the input pair universe.",
    ),
    output_dir: Path = typer.Option(
        Path("data/experiment1_split/analysis_sc_stateful"),
        help="Directory where the SC stateful pair artifacts will be written.",
    ),
    report_top_k: int = typer.Option(30, help="Number of top pairs to render in the markdown summary."),
) -> None:
    outputs = generate_sc_stateful_pair_artifacts(
        db_path=db_path,
        input_csv=input_csv,
        output_dir=output_dir,
        top_report_k=report_top_k,
    )
    for name, path in outputs.items():
        console.print(f"{name}={path}")


@app.command("sc-candidate-universe")
def sc_candidate_universe(
    db_path: Path = typer.Option(
        Path("data/experiment1_split/merged.sqlite3"),
        help="SQLite database used for SC candidate-universe extraction.",
    ),
    input_csv: Path = typer.Option(
        Path("experiments/graph_first_composition/data/analysis_runtime_p25r_500/p25r_seed_pool.csv"),
        help="P25R runnable seed pool used as the source runnable universe.",
    ),
    output_dir: Path = typer.Option(
        Path("data/experiment1_split/analysis_sc_stateful"),
        help="Directory where the SC candidate-universe artifacts will be written.",
    ),
) -> None:
    outputs = generate_sc_candidate_universe_artifacts(
        db_path=db_path,
        input_csv=input_csv,
        output_dir=output_dir,
    )
    for name, path in outputs.items():
        console.print(f"{name}={path}")


@app.command("sc-seed-pool")
def sc_seed_pool(
    input_csv: Path = typer.Option(
        Path("data/experiment1_split/analysis_sc_stateful/sc_stateful_pairs.csv"),
        help="SC stateful pair table used as the source candidate universe.",
    ),
    output_dir: Path = typer.Option(
        Path("data/experiment1_split/analysis_sc_stateful"),
        help="Directory where the SC runtime seed-pool artifacts will be written.",
    ),
) -> None:
    outputs = generate_sc_seed_pool_artifacts(
        input_csv=input_csv,
        output_dir=output_dir,
    )
    for name, path in outputs.items():
        console.print(f"{name}={path}")


@app.command("sc-runtime-manifest")
def sc_runtime_manifest(
    input_csv: Path = typer.Option(
        Path("data/experiment1_split/analysis_sc_stateful/sc_runtime_seed_pool.csv"),
        help="SC runtime seed-pool CSV used to build the manifest.",
    ),
    output_dir: Path = typer.Option(
        Path("data/experiment1_split/analysis_sc_stateful"),
        help="Directory where the SC runtime manifest will be written.",
    ),
) -> None:
    outputs = generate_sc_runtime_manifest_artifacts(
        input_csv=input_csv,
        output_dir=output_dir,
    )
    for name, path in outputs.items():
        console.print(f"{name}={path}")


@app.command("sc-sample-runtime-pool")
def sc_sample_runtime_pool(
    input_csv: Path = typer.Option(
        Path("data/experiment1_split/analysis_sc_stateful/sc_candidate_universe_from_p25r.csv"),
        help="SC candidate universe CSV used for adaptive runtime sampling.",
    ),
    output_dir: Path = typer.Option(
        Path("data/experiment1_split/analysis_sc_stateful"),
        help="Directory where the sampled SC runtime pool will be written.",
    ),
    min_cases: int = typer.Option(200, help="Minimum target number of cases."),
    preferred_cases: int = typer.Option(300, help="Preferred target number of cases."),
    max_cases: int = typer.Option(500, help="Maximum cap on cases."),
) -> None:
    outputs = generate_sc_runtime_sampling_artifacts(
        input_csv=input_csv,
        output_dir=output_dir,
        min_cases=min_cases,
        preferred_cases=preferred_cases,
        max_cases=max_cases,
    )
    for name, path in outputs.items():
        console.print(f"{name}={path}")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
