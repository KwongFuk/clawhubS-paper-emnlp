"""Built-in search queries for experiment 1."""

from __future__ import annotations

DEFAULT_SEARCH_QUERIES = [
    "slack",
    "github",
    "notion",
    "calendar",
    "pdf",
    "search",
    "memory",
    "gmail",
    "docs",
    "agent",
    "install",
    "skill",
]


def get_default_queries() -> list[str]:
    """Return a copy to avoid accidental mutation."""
    return list(DEFAULT_SEARCH_QUERIES)
