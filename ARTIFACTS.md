# Artifact Scope and Limitations

## Included in the repository

- Python package source and command-line tooling.
- Unit tests.
- Active and historical first-party experiment scripts and documentation.
- Compatibility entry points expected by the test suite.
- EMNLP paper source, rendered figures, and the available PDF.

## Included in the GitHub Release

- EMNLP paper PDF and self-contained LaTeX source bundle.
- Aggregate ecosystem-quality statistics.
- Aggregate repair-impact and routing summaries.
- Checksums and a machine-readable manifest.

## Intentionally excluded

- Personal assistant configuration and memory files.
- API keys, credentials, environment files, caches, logs, and virtual environments.
- Nested third-party Git repositories and downloaded skill clones.
- Raw skill-level IC-1s/risk labels or identifiable flagged examples.
- Large intermediate model outputs and redundant experiment caches.

## Missing upstream corpus

The original 80,000+ downloaded skill corpus and the databases referenced as
`data/experiment1_split/merged.sqlite3` and `data/skills_sh/skills_sh.sqlite3`
were not present in the workspace when this release was prepared. Derived
aggregate results remain, but they are not a substitute for the raw corpus.

## Reproducibility notes

Some historical scripts contain absolute paths for the original workstation or
cluster. They are preserved to document the executed workflow. Porting them to
a new machine requires replacing those paths and supplying the missing raw
marketplace snapshot or an equivalent new collection.
