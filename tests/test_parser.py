from clawhub_analyzer.parser import (
    extract_permission_hints,
    parse_search_results,
    parse_skill_comments,
    parse_skill_detail,
    parse_skill_detail_from_api,
    parse_version_detail_from_api,
)


DETAIL_HTML = """
<html>
  <body>
    <main class="section">
      <div class="card skill-hero">
        <div class="skill-hero-top">
          <div class="skill-hero-header">
            <div class="skill-hero-title">
              <div class="skill-hero-title-row">
                <h1 class="section-title">Slack Ops</h1>
              </div>
              <p class="section-subtitle">Manage Slack channels and sync Gmail alerts with calendar summaries.</p>
              <div class="stat">⭐ 32 · 19.6k · 145 current installs · 233 all-time installs</div>
              <div class="stat"><a class="user-handle" href="/u/researcher">@researcher</a></div>
              <div class="scan-results-panel">
                <div class="scan-result-row">
                  <div class="scan-result-status scan-status-clean">Benign</div>
                  <a class="scan-result-link" href="https://virustotal.example/report/abc">View report</a>
                </div>
              </div>
            </div>
            <div class="skill-hero-cta">
              <div class="skill-version-pill">
                <span class="skill-version-label">Current version</span>
                <strong>v1.4.2</strong>
              </div>
            </div>
          </div>
        </div>
        <div class="skill-tag-row">
          <span class="tag">Highlighted</span>
          <span class="tag">Slack</span>
          <span class="tag">Email</span>
        </div>
        <div class="skill-panel">
          <h3 class="section-title">Install</h3>
          <div class="skill-panel-body">
            <code>npx clawhub@latest install slack-ops</code>
          </div>
        </div>
        <div class="comment-item">
          <strong>@alice</strong>
          <div class="comment-body-text">Needs Slack OAuth and a browser session.</div>
        </div>
      </div>
    </main>
  </body>
</html>
"""


SEARCH_HTML = """
<html>
  <body>
    <a class="skill-card" href="/alice/slack-helper">
      <div class="skill-card-tags">
        <div class="tag">Highlighted</div>
      </div>
      <h3 class="skill-card-title">Slack Helper</h3>
      <p class="skill-card-summary">Search Slack and manage reactions.</p>
      <div class="skill-card-footer">
        <span class="user-handle" href="/u/alice">@alice</span>
        <div class="stat">⭐ 12 · 8.2k</div>
      </div>
    </a>
    <a class="skill-card" href="/bob/github-triage">
      <h3 class="skill-card-title">GitHub Triage</h3>
      <p class="skill-card-summary">Handle GitHub issues with a PAT.</p>
      <div class="skill-card-footer">
        <span class="user-handle" href="/u/bob">@bob</span>
        <div class="stat">⭐ 7 · 3.4k</div>
      </div>
    </a>
  </body>
</html>
"""


DETAIL_API_PAYLOAD = {
    "skill": {
        "slug": "slack-ops",
        "displayName": "Slack Ops",
        "summary": "Manage Slack channels and sync Gmail alerts with calendar summaries.",
        "tags": {"latest": "1.4.2", "Slack": True, "Email": True},
        "stats": {
            "stars": 32,
            "downloads": 19600,
            "comments": 46,
            "installsCurrent": 145,
            "installsAllTime": 233,
            "versions": 12,
        },
        "createdAt": 1770000000000,
        "updatedAt": 1770100000000,
    },
    "latestVersion": {
        "version": "1.4.2",
        "createdAt": 1770100000000,
        "changelog": "Initial stable release",
        "sha256hash": "deadbeef",
    },
    "owner": {
        "handle": "researcher",
        "displayName": "Researcher",
        "userId": "users:123",
    },
    "moderation": {
        "isSuspicious": True,
        "isMalwareBlocked": False,
    },
    "metadata": {
        "os": ["macos", "linux"],
        "systems": ["slack", "gmail"],
        "category": "productivity",
    },
}


VERSION_API_PAYLOAD = {
    "skill": {
        "slug": "slack-ops",
        "displayName": "Slack Ops",
    },
    "version": {
        "version": "1.4.1",
        "createdAt": 1770100000000,
        "changelog": "Adds GitHub token support",
        "security": {
            "status": "clean",
            "hasWarnings": True,
            "checkedAt": 1770200000000,
            "model": "vt-v1",
        },
        "files": [
            {
                "path": "SKILL.md",
                "size": 1024,
                "sha256": "abc123",
                "contentType": "text/markdown",
            }
        ],
    },
}


def test_parse_skill_detail() -> None:
    bundle = parse_skill_detail(
        DETAIL_HTML,
        "https://clawhub.ai/researcher/slack-ops",
        "https://clawhub.ai",
    )
    assert bundle.skill.skill_id == "researcher/slack-ops"
    assert bundle.skill.name == "Slack Ops"
    assert bundle.skill.author_handle == "researcher"
    assert bundle.skill.stars == 32
    assert bundle.skill.downloads == 19_600
    assert bundle.skill.current_version == "1.4.2"
    assert bundle.skill.install_command == "npx clawhub@latest install slack-ops"
    assert bundle.skill.scan_status == "Benign"
    assert bundle.skill.scan_report_url == "https://virustotal.example/report/abc"
    assert "Slack" in bundle.skill.tags
    assert bundle.skill.installs_current == 145
    assert bundle.skill.installs_all_time == 233
    assert bundle.skill.is_highlighted is True


def test_parse_search_results() -> None:
    results = parse_search_results(SEARCH_HTML, "slack", "https://clawhub.ai")
    assert len(results) == 2
    assert results[0].rank == 1
    assert results[0].skill_id == "alice/slack-helper"
    assert results[0].skill_name == "Slack Helper"
    assert results[1].skill_id == "bob/github-triage"


def test_extract_permission_hints() -> None:
    hint = extract_permission_hints(
        "researcher/slack-ops",
        [
            "Requires Slack OAuth and a Gmail API key.",
            "Can browse the web in a browser and read local files.",
            "Uses GitHub token rotation for issue sync.",
        ],
    )
    assert hint.requires_slack is True
    assert hint.requires_oauth is True
    assert hint.requires_email is True
    assert hint.requires_api_key is True
    assert hint.requires_browser_access is True
    assert hint.requires_local_file_access is True
    assert hint.requires_github is True
    assert hint.requires_token is True
    assert hint.raw_evidence


def test_parse_skill_detail_from_api() -> None:
    bundle = parse_skill_detail_from_api(
        DETAIL_API_PAYLOAD,
        "https://clawhub.ai/researcher/slack-ops",
        "https://clawhub.ai",
    )
    assert bundle.skill.skill_id == "researcher/slack-ops"
    assert bundle.skill.author_handle == "researcher"
    assert bundle.skill.current_version == "1.4.2"
    assert bundle.skill.downloads == 19600
    assert bundle.skill.comments_count == 46
    assert bundle.skill.versions_count == 12
    assert bundle.skill.category == "productivity"
    assert bundle.skill.metadata_os == ["macos", "linux"]
    assert bundle.skill.metadata_systems == ["slack", "gmail"]
    assert bundle.versions[0].version == "1.4.2"
    assert bundle.publisher is not None
    assert bundle.publisher.author_handle == "researcher"


def test_parse_version_detail_from_api() -> None:
    version, files = parse_version_detail_from_api(
        VERSION_API_PAYLOAD,
        "researcher/slack-ops",
        "https://clawhub.ai",
    )
    assert version is not None
    assert version.version == "1.4.1"
    assert version.scan_status == "Benign"
    assert version.security_has_warnings is True
    assert version.security_model == "vt-v1"
    assert version.files_count == 1
    assert version.bundle_url == "https://clawhub.ai/api/v1/download?slug=slack-ops&version=1.4.1"
    assert files[0].path == "SKILL.md"
    assert files[0].content_type == "text/markdown"


def test_parse_skill_comments() -> None:
    comments = parse_skill_comments(DETAIL_HTML, "researcher/slack-ops")
    assert len(comments) == 1
    assert comments[0].author_handle == "alice"
    assert "Slack OAuth" in (comments[0].body or "")
