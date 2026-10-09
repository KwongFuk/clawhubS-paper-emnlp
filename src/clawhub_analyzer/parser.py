"""HTML and JSON parsing."""

from __future__ import annotations

import logging
import re
from typing import Iterable
from urllib.parse import urlparse

from bs4 import BeautifulSoup, Tag

from clawhub_analyzer.models import (
    HomeParseResult,
    PermissionOrSecretHint,
    Publisher,
    PublisherParseResult,
    SearchResult,
    Skill,
    SkillComment,
    SkillCard,
    SkillFile,
    SkillParseBundle,
    SkillVersion,
)
from clawhub_analyzer.utils import (
    clean_multiline_text,
    clean_text,
    coerce_iso_datetime,
    context_snippet,
    ensure_absolute_url,
    make_skill_id,
    normalize_install_command,
    parse_abbreviated_number,
    unique_list,
)

LOGGER = logging.getLogger(__name__)

_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?[kmb]?", re.IGNORECASE)

_HINT_PATTERNS: dict[str, list[str]] = {
    "requires_api_key": [r"\bapi[- ]?key\b", r"\bsecret key\b"],
    "requires_oauth": [r"\boauth\b", r"\bauthoriz(?:e|ation)\b", r"\bsign in with\b"],
    "requires_token": [r"\btoken\b", r"\baccess token\b", r"\bpersonal access token\b"],
    "requires_local_file_access": [
        r"\blocal files?\b",
        r"\bfilesystem\b",
        r"\bread files?\b",
        r"\bwrite files?\b",
        r"\bdocuments on disk\b",
    ],
    "requires_shell_access": [
        r"\bshell\b",
        r"\bterminal\b",
        r"\bcommand line\b",
        r"\bbash\b",
        r"\bzsh\b",
        r"\brun (?:a )?(?:local )?(?:command|script)\b",
        r"\bexecute (?:a )?(?:local )?(?:command|script)\b",
    ],
    "requires_browser_access": [r"\bbrowser\b", r"\bopen tabs?\b", r"\bweb search\b", r"\bsearch the web\b"],
    "requires_github": [
        r"\bgithub(?: api| token| repository| repo| issue| issues| workflow| actions?| releases?)\b",
        r"\bgithub[- ](?:api|repo|issues?|workflow)\b",
        r"\bgithub\.com/[^\s)]+",
    ],
    "requires_slack": [r"\bslack\b"],
    "requires_notion": [r"\bnotion\b"],
    "requires_calendar": [r"\bcalendar\b", r"\bgoogle calendar\b"],
    "requires_email": [r"\bgmail\b", r"\bemail\b", r"\bmailbox\b"],
    "requires_docs": [r"\bgoogle docs\b", r"\bdocs\b", r"\bdocument(s)?\b"],
    "requires_pdf": [r"\bpdf\b"],
}


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


def _text(node: Tag | None) -> str | None:
    if not node:
        return None
    return clean_text(node.get_text(" ", strip=True))


def _strip_handle(value: str | None) -> str | None:
    if not value:
        return None
    return value.strip().lstrip("@")


def _find_stats_text(root: Tag) -> str | None:
    for candidate in root.select(".stat, .skills-row-metrics"):
        text = _text(candidate)
        if text and any(token in text for token in ("⭐", "★", "downloads", "installs")):
            return text
    return None


def _extract_metrics(text: str | None) -> tuple[int | None, int | None]:
    if not text:
        return None, None
    lower = text.lower()
    numbers_raw = _NUMBER_RE.findall(lower)
    numbers = [parse_abbreviated_number(item) for item in numbers_raw]
    numbers = [value for value in numbers if value is not None]
    star_match = re.search(r"[⭐★]\s*(\d+(?:\.\d+)?[kmb]?)", lower)
    stars = parse_abbreviated_number(star_match.group(1)) if star_match else None
    downloads = None
    if stars is not None and numbers:
        if numbers[0] == stars and len(numbers) >= 2:
            downloads = numbers[1]
        elif numbers[0] != stars:
            downloads = numbers[0]
    elif numbers:
        downloads = numbers[0]
        if len(numbers) >= 2:
            stars = numbers[1]
    return stars, downloads


def _extract_installs(text: str | None) -> tuple[int | None, int | None]:
    if not text:
        return None, None
    lower = text.lower()
    current_match = re.search(r"(\d+(?:\.\d+)?[kmb]?)\s+current installs", lower)
    all_time_match = re.search(r"(\d+(?:\.\d+)?[kmb]?)\s+all-time installs", lower)
    installs_match = re.search(r"(\d+(?:\.\d+)?[kmb]?)\s+installs", lower)
    current = parse_abbreviated_number(current_match.group(1)) if current_match else None
    all_time = parse_abbreviated_number(all_time_match.group(1)) if all_time_match else None
    if current is None and all_time is None and installs_match:
        all_time = parse_abbreviated_number(installs_match.group(1))
    return current, all_time


def _parse_skill_card(root: Tag, base_url: str, *, highlighted: bool = False, popular: bool = False) -> SkillCard | None:
    link = root if root.name == "a" and root.get("href") else root.select_one("a[href]")
    href = ensure_absolute_url(base_url, link.get("href")) if link else None
    if not href:
        return None
    name = (
        _text(root.select_one(".skill-card-title"))
        or _text(root.select_one(".skills-row-title span"))
        or _text(root.select_one("h3"))
        or _text(root.select_one("h2"))
    )
    description = _text(root.select_one(".skill-card-summary")) or _text(
        root.select_one(".skills-row-summary")
    )
    author_link = root.select_one(".user-handle")
    author_handle = _strip_handle(_text(author_link))
    author_profile_url = ensure_absolute_url(base_url, author_link.get("href")) if author_link else None
    tags = unique_list([tag for tag in (_text(node) for node in root.select(".tag")) if tag])
    stars, downloads = _extract_metrics(_find_stats_text(root))
    skill_id = make_skill_id(href, author_handle=author_handle)
    return SkillCard(
        skill_id=skill_id,
        name=name,
        page_url=href,
        description=description,
        author_handle=author_handle,
        author_profile_url=author_profile_url,
        stars=stars,
        downloads=downloads,
        tags=tags,
        is_highlighted=highlighted or any(tag.lower() == "highlighted" for tag in tags),
        is_popular=popular,
    )


def _parse_skill_cards(root: Tag | BeautifulSoup, base_url: str, *, highlighted: bool = False, popular: bool = False) -> list[SkillCard]:
    cards: list[SkillCard] = []
    selectors = ["a.skill-card", ".skills-row"]
    nodes: list[Tag] = []
    for selector in selectors:
        nodes.extend(root.select(selector))
    seen: set[str] = set()
    for node in nodes:
        card = _parse_skill_card(node, base_url, highlighted=highlighted, popular=popular)
        if card and card.page_url not in seen:
            seen.add(card.page_url)
            cards.append(card)
    return cards


def parse_homepage(html: str, base_url: str) -> HomeParseResult:
    soup = _soup(html)
    highlighted_cards: list[SkillCard] = []
    popular_cards: list[SkillCard] = []
    for section in soup.select("section"):
        title = _text(section.select_one("h2, h1")) or ""
        lowered = title.lower()
        if "highlighted" in lowered:
            highlighted_cards.extend(_parse_skill_cards(section, base_url, highlighted=True))
        if "popular" in lowered:
            popular_cards.extend(_parse_skill_cards(section, base_url, popular=True))
    return HomeParseResult(
        highlighted_skills=highlighted_cards,
        popular_skills=popular_cards,
    )


def parse_browse_page(html: str, base_url: str) -> list[SkillCard]:
    soup = _soup(html)
    return _parse_skill_cards(soup, base_url)


def parse_search_results(html: str, query: str, base_url: str) -> list[SearchResult]:
    cards = parse_browse_page(html, base_url)
    results: list[SearchResult] = []
    for rank, card in enumerate(cards, start=1):
        results.append(
            SearchResult(
                query=query,
                rank=rank,
                skill_id=card.skill_id,
                skill_name=card.name,
                result_url=card.page_url,
            )
        )
    return results


def parse_skill_detail_from_api(payload: dict, page_url: str, base_url: str) -> SkillParseBundle:
    skill_data = payload.get("skill", {})
    owner = payload.get("owner", {})
    latest_version = payload.get("latestVersion", {})
    moderation = payload.get("moderation") or {}
    metadata = payload.get("metadata") or {}
    author_handle = owner.get("handle")
    skill_id = make_skill_id(page_url, author_handle=author_handle, slug=skill_data.get("slug"))
    scan_status = None
    if moderation.get("isMalwareBlocked"):
        scan_status = "Malicious"
    elif moderation.get("isSuspicious"):
        scan_status = "Suspicious"
    scan_report_url = None
    sha256_hash = latest_version.get("sha256hash")
    if sha256_hash:
        scan_report_url = f"https://www.virustotal.com/gui/file/{sha256_hash}"
    stats = skill_data.get("stats", {}) or {}
    skill = Skill(
        skill_id=skill_id,
        name=skill_data.get("displayName"),
        page_url=page_url,
        description=skill_data.get("summary"),
        author_handle=author_handle,
        author_profile_url=f"{base_url}/u/{author_handle}" if author_handle else None,
        stars=stats.get("stars"),
        downloads=stats.get("downloads"),
        installs_current=stats.get("installsCurrent"),
        installs_all_time=stats.get("installsAllTime"),
        comments_count=stats.get("comments"),
        versions_count=stats.get("versions"),
        is_suspicious=bool(moderation.get("isSuspicious")),
        is_malware_blocked=bool(moderation.get("isMalwareBlocked")),
        current_version=latest_version.get("version"),
        created_at=coerce_iso_datetime(skill_data.get("createdAt")),
        updated_at=coerce_iso_datetime(skill_data.get("updatedAt")),
        scan_status=scan_status,
        scan_report_url=scan_report_url,
        category=metadata.get("category"),
        tags=list((skill_data.get("tags") or {}).keys()),
        metadata_os=metadata.get("os") or [],
        metadata_systems=metadata.get("systems") or [],
    )
    versions: list[SkillVersion] = []
    if latest_version.get("version"):
        versions.append(
            SkillVersion(
                skill_id=skill_id,
                version=latest_version["version"],
                release_time=coerce_iso_datetime(latest_version.get("createdAt")),
                changelog=clean_multiline_text(latest_version.get("changelog")),
                scan_status=scan_status,
                scan_report_url=scan_report_url,
                bundle_hash=sha256_hash,
                files_count=len(latest_version.get("files") or []) or None,
            )
        )
    publisher = None
    if author_handle:
        publisher = Publisher(
            author_handle=author_handle,
            profile_url=f"{base_url}/u/{author_handle}",
            display_name=owner.get("displayName"),
        )
    return SkillParseBundle(skill=skill, versions=versions, publisher=publisher, metadata=payload)


def parse_version_detail_from_api(payload: dict, skill_id: str, base_url: str) -> tuple[SkillVersion | None, list[SkillFile]]:
    version_root = payload.get("version") or {}
    skill_root = payload.get("skill") or {}
    version_name = version_root.get("version")
    if not version_name:
        return None, []
    security = version_root.get("security") or {}
    status = security.get("status")
    mapped_status = {
        "clean": "Benign",
        "suspicious": "Suspicious",
        "malicious": "Malicious",
        "pending": "Pending",
        "error": "Error",
    }.get(str(status).lower(), status)
    version = SkillVersion(
        skill_id=skill_id,
        version=str(version_name),
        release_time=coerce_iso_datetime(version_root.get("createdAt")),
        changelog=clean_multiline_text(version_root.get("changelog")),
        scan_status=mapped_status,
        security_has_warnings=bool(security.get("hasWarnings")),
        security_checked_at=coerce_iso_datetime(security.get("checkedAt")),
        security_model=security.get("model"),
        files_count=len(version_root.get("files") or []) or None,
        bundle_url=(
            f"{base_url}/api/v1/download?slug={skill_root.get('slug')}&version={version_name}"
            if skill_root.get("slug")
            else None
        ),
    )
    files = [
        SkillFile(
            skill_id=skill_id,
            version=str(version_name),
            path=str(file_root.get("path") or ""),
            size=file_root.get("size"),
            sha256=file_root.get("sha256"),
            content_type=file_root.get("contentType"),
        )
        for file_root in (version_root.get("files") or [])
        if file_root.get("path")
    ]
    return version, files


def _extract_install_command(soup: BeautifulSoup) -> str | None:
    for heading in soup.select(".skill-panel h3, .card h2"):
        title = (_text(heading) or "").lower()
        if "install" in title:
            panel = heading.find_parent(class_="skill-panel") or heading.find_parent(class_="card")
            if panel:
                code = _text(panel.select_one("code")) or _text(panel.select_one("pre"))
                return normalize_install_command(code)
    code = _text(soup.select_one("pre.hero-install-code"))
    return normalize_install_command(code)


def parse_version_history(html: str, skill_id: str) -> list[SkillVersion]:
    soup = _soup(html)
    versions: list[SkillVersion] = []
    for row in soup.select(".version-row"):
        info = row.select_one(".version-info")
        heading_text = _text(info.select_one("div")) if info else None
        version_match = re.search(r"v([0-9][^ ·]*)", heading_text or "")
        version = version_match.group(1) if version_match else None
        if not version:
            continue
        release_time = None
        if heading_text:
            parts = heading_text.split("·")
            if len(parts) >= 2:
                release_time = coerce_iso_datetime(parts[1].strip())
        changelog = None
        if info:
            blocks = info.find_all("div", recursive=False)
            if len(blocks) >= 2:
                changelog = clean_multiline_text(blocks[1].get_text("\n", strip=True))
        status_text = _text(row.select_one(".version-scan-badge .scan-status-clean")) or _text(
            row.select_one(".version-scan-badge span")
        )
        report_link = row.select_one(".version-scan-link")
        zip_link = row.select_one(".version-zip")
        versions.append(
            SkillVersion(
                skill_id=skill_id,
                version=version,
                release_time=release_time,
                changelog=changelog,
                bundle_url=zip_link.get("href") if zip_link else None,
                scan_status=status_text,
                scan_report_url=report_link.get("href") if report_link else None,
            )
        )
    return versions


def _extract_signal_text(soup: BeautifulSoup) -> str | None:
    parts: list[str] = []
    selectors = [
        ".skill-hero-title",
        ".skill-panel",
        ".bundle-card",
        ".tab-card .tab-body",
        ".hero-install-code",
    ]
    for selector in selectors:
        for node in soup.select(selector):
            text = clean_multiline_text(node.get_text("\n", strip=True))
            if text:
                parts.append(text)
    if not parts:
        main = soup.select_one("main.section")
        if main:
            return clean_multiline_text(main.get_text("\n", strip=True))
    return clean_multiline_text("\n".join(parts))


def parse_skill_detail(html: str, page_url: str, base_url: str) -> SkillParseBundle:
    soup = _soup(html)
    title = _text(soup.select_one("h1.section-title"))
    description = _text(soup.select_one(".skill-hero p.section-subtitle")) or _text(
        soup.select_one("p.section-subtitle")
    )
    author_link = soup.select_one(".user-handle")
    author_handle = _strip_handle(_text(author_link))
    author_profile_url = ensure_absolute_url(base_url, author_link.get("href")) if author_link else None
    skill_id = make_skill_id(page_url, author_handle=author_handle)
    stats_text = None
    for stat in soup.select(".skill-hero .stat"):
        text = _text(stat)
        if text and "⭐" in text:
            stats_text = text
            break
    stars, downloads = _extract_metrics(stats_text)
    installs_current, installs_all_time = _extract_installs(stats_text)
    version_text = _text(soup.select_one(".skill-version-pill strong"))
    current_version = version_text.lstrip("v") if version_text else None
    tags = unique_list([tag for tag in (_text(node) for node in soup.select(".tag")) if tag])
    scan_status = _text(soup.select_one(".scan-result-status"))
    scan_report = ensure_absolute_url(base_url, soup.select_one(".scan-result-link").get("href")) if soup.select_one(".scan-result-link") else None
    install_command = _extract_install_command(soup)
    category = "plugin_bundle" if any("plugin bundle" in tag.lower() for tag in tags) else None
    skill = Skill(
        skill_id=skill_id,
        name=title,
        page_url=page_url,
        description=description,
        author_handle=author_handle,
        author_profile_url=author_profile_url,
        stars=stars,
        downloads=downloads,
        installs_current=installs_current,
        installs_all_time=installs_all_time,
        is_highlighted=any((tag or "").lower() == "highlighted" for tag in tags),
        is_official=any((tag or "").lower() == "official" for tag in tags),
        is_deprecated=any((tag or "").lower() == "deprecated" for tag in tags),
        install_command=install_command,
        current_version=current_version,
        scan_status=scan_status,
        scan_report_url=scan_report,
        category=category,
        tags=tags,
    )
    raw_text = _extract_signal_text(soup)
    permission_hint = extract_permission_hints(skill_id, [description or "", raw_text or "", install_command or ""])
    versions = parse_version_history(html, skill_id)
    publisher = None
    if author_handle and author_profile_url:
        publisher = Publisher(
            author_handle=author_handle,
            profile_url=author_profile_url,
            display_name=None,
        )
    return SkillParseBundle(
        skill=skill,
        versions=versions,
        permission_hint=permission_hint,
        publisher=publisher,
        raw_text=raw_text,
    )


def parse_skill_comments(html: str, skill_id: str) -> list[SkillComment]:
    soup = _soup(html)
    comments: list[SkillComment] = []
    for index, node in enumerate(soup.select(".comment-item"), start=1):
        author = _strip_handle(_text(node.select_one("strong")))
        body = _text(node.select_one(".comment-body-text")) or _text(node)
        comments.append(
            SkillComment(
                skill_id=skill_id,
                comment_index=index,
                author_handle=author,
                body=body,
            )
        )
    return comments


def parse_publisher_page(html: str, page_url: str, base_url: str) -> PublisherParseResult:
    soup = _soup(html)
    display_name = _text(soup.select_one(".settings-name"))
    handle = _strip_handle(_text(soup.select_one(".settings-handle")))
    if not handle:
        path = [part for part in urlparse(page_url).path.split("/") if part]
        handle = path[-1] if path else "unknown"
    bio = _text(soup.select_one(".settings-profile-body p:not(.settings-handle)")) or _text(
        soup.select_one(".settings-bio")
    )
    published_heading = next(
        (heading for heading in soup.select("h2.section-title") if "published" in (_text(heading) or "").lower()),
        None,
    )
    published_root = soup
    if published_heading:
        candidate = published_heading.find_next("div", class_="grid")
        if isinstance(candidate, Tag):
            published_root = candidate
    published_cards = _parse_skill_cards(
        published_root,
        base_url,
    )
    publisher = Publisher(
        author_handle=handle,
        profile_url=page_url,
        display_name=display_name,
        bio=bio,
        num_skills=len(published_cards),
        total_stars=sum(card.stars or 0 for card in published_cards) or None,
        total_downloads=sum(card.downloads or 0 for card in published_cards) or None,
    )
    return PublisherParseResult(
        publisher=publisher,
        published_skill_urls=[card.page_url for card in published_cards],
    )


def extract_permission_hints(skill_id: str, texts: Iterable[str]) -> PermissionOrSecretHint:
    combined = "\n".join(text for text in texts if text)
    evidence: dict[str, list[str]] = {field: [] for field in _HINT_PATTERNS}
    for field, patterns in _HINT_PATTERNS.items():
        for pattern in patterns:
            for match in re.finditer(pattern, combined, flags=re.IGNORECASE):
                snippet = context_snippet(combined, match.start(), match.end())
                if snippet and snippet not in evidence[field]:
                    evidence[field].append(snippet)
    raw_evidence = unique_list([snippet for values in evidence.values() for snippet in values][:20])
    return PermissionOrSecretHint(
        skill_id=skill_id,
        raw_evidence=raw_evidence,
        **{field: bool(values) for field, values in evidence.items()},
    )
