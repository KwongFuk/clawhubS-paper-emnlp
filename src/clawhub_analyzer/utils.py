"""Utility helpers."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote, urljoin, urlparse


NUMBER_SUFFIXES = {
    "k": 1_000,
    "m": 1_000_000,
    "b": 1_000_000_000,
}


def now_utc_iso() -> str:
    return datetime.now(tz=UTC).replace(microsecond=0).isoformat()


def clean_text(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = re.sub(r"\s+", " ", value).strip()
    return cleaned or None


def clean_multiline_text(value: str | None) -> str | None:
    if value is None:
        return None
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in value.splitlines()]
    text = "\n".join(line for line in lines if line)
    return text or None


def normalize_install_command(value: str | None) -> str | None:
    cleaned = clean_multiline_text(value)
    if not cleaned:
        return None
    return re.sub(r"\s+", " ", cleaned).strip()


def parse_abbreviated_number(value: str | int | float | None) -> int | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    text = value.strip().lower().replace(",", "")
    if not text:
        return None
    match = re.fullmatch(r"(-?\d+(?:\.\d+)?)([kmb])?", text)
    if not match:
        digits = re.findall(r"\d+", text)
        return int("".join(digits)) if digits else None
    number = float(match.group(1))
    suffix = match.group(2)
    if suffix:
        number *= NUMBER_SUFFIXES[suffix]
    return int(number)


def ensure_absolute_url(base_url: str, maybe_relative: str | None) -> str | None:
    if not maybe_relative:
        return None
    return urljoin(base_url, maybe_relative)


def coerce_iso_datetime(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value.astimezone(UTC)
        return dt.replace(microsecond=0).isoformat()
    if isinstance(value, (int, float)):
        timestamp = float(value)
        if timestamp > 1_000_000_000_000:
            timestamp /= 1000
        return datetime.fromtimestamp(timestamp, tz=UTC).replace(microsecond=0).isoformat()
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        if raw.isdigit():
            return coerce_iso_datetime(int(raw))
        for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%Y-%m-%d %H:%M:%S"):
            try:
                return datetime.strptime(raw, fmt).replace(tzinfo=UTC).isoformat()
            except ValueError:
                continue
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(UTC).replace(
                microsecond=0
            ).isoformat()
        except ValueError:
            return raw
    return str(value)


def looks_like_app_shell(html: str | None) -> bool:
    if not html:
        return True
    body = html.lower()
    markers = (
        "modulepreload",
        "$_tsr.router",
        "import('/assets/main",
    )
    no_visible_cards = all(token not in body for token in ("skill-card", "skills-row", "section-title"))
    return any(marker in body for marker in markers) and no_visible_cards


def make_snapshot_path(root: Path, url: str, suffix: str = ".html") -> Path:
    parsed = urlparse(url)
    path_part = parsed.path.strip("/") or "home"
    query_part = f"__{quote(parsed.query, safe='')}" if parsed.query else ""
    stem = f"{path_part}{query_part}".replace("/", "__")
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:10]
    safe_stem = re.sub(r"[^a-zA-Z0-9_.-]+", "_", stem)[:120].strip("_") or "page"
    return root / f"{safe_stem}__{digest}{suffix}"


def write_text_file(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def unique_list(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def extract_path_parts(url: str) -> list[str]:
    parsed = urlparse(url)
    return [part for part in parsed.path.split("/") if part]


def infer_skill_identity(page_url: str, author_handle: str | None = None) -> tuple[str | None, str | None]:
    parts = extract_path_parts(page_url)
    if len(parts) >= 2 and parts[0] not in {"u", "skills", "search"}:
        owner = author_handle or parts[0]
        slug = parts[1]
        return owner, slug
    if parts:
        return author_handle, parts[-1]
    return author_handle, None


def make_skill_id(page_url: str, author_handle: str | None = None, slug: str | None = None) -> str:
    owner, inferred_slug = infer_skill_identity(page_url, author_handle=author_handle)
    final_slug = slug or inferred_slug or page_url.rstrip("/").split("/")[-1]
    return f"{owner}/{final_slug}" if owner else final_slug


def merge_optional(new_value: Any, old_value: Any) -> Any:
    return new_value if new_value not in (None, "", [], {}) else old_value


def merge_string_lists(*values: list[str] | None) -> list[str]:
    combined: list[str] = []
    for value in values:
        if value:
            combined.extend(value)
    return unique_list([item for item in combined if item])


def context_snippet(text: str, start: int, end: int, radius: int = 60) -> str:
    left = max(0, start - radius)
    right = min(len(text), end + radius)
    snippet = text[left:right]
    return clean_text(snippet) or ""
