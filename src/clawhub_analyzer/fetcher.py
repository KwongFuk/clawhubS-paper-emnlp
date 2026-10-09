"""HTTP and Playwright fetch helpers."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx
from tenacity import AsyncRetrying, retry_if_exception_type, stop_after_attempt, wait_exponential

from clawhub_analyzer.config import AppConfig
from clawhub_analyzer.rate_limit import AsyncRateLimiter
from clawhub_analyzer.utils import looks_like_app_shell, make_snapshot_path, write_text_file

LOGGER = logging.getLogger(__name__)


class FetchError(RuntimeError):
    """Base fetch error."""


class RetryableFetchError(FetchError):
    """Errors safe to retry."""


class PermanentFetchError(FetchError):
    """Errors that should not be retried."""


class Fetcher:
    """Fetch raw or rendered pages with retry, robots, and snapshots."""

    def __init__(self, config: AppConfig):
        self.config = config
        self.rate_limiter = AsyncRateLimiter(config.requests_per_second)
        self.client = httpx.AsyncClient(
            timeout=config.request_timeout,
            follow_redirects=True,
            headers={"User-Agent": config.user_agent},
        )
        self._robots_parser: RobotFileParser | None = None
        self._robots_loaded = False
        self._playwright_manager = None
        self._browser = None
        self._browser_context = None
        self._playwright_lock = None

    async def close(self) -> None:
        await self.client.aclose()
        if self._browser_context is not None:
            await self._browser_context.close()
            self._browser_context = None
        if self._browser is not None:
            await self._browser.close()
            self._browser = None
        if self._playwright_manager is not None:
            await self._playwright_manager.stop()
            self._playwright_manager = None

    async def _ensure_browser_context(self):
        if self._playwright_lock is None:
            import asyncio

            self._playwright_lock = asyncio.Lock()
        async with self._playwright_lock:
            if self._browser_context is not None:
                return self._browser_context
            try:
                from playwright.async_api import async_playwright
            except ImportError as exc:
                raise RuntimeError("Playwright is required for rendered crawling") from exc
            self._playwright_manager = await async_playwright().start()
            self._browser = await self._playwright_manager.chromium.launch(headless=True)
            self._browser_context = await self._browser.new_context(user_agent=self.config.user_agent)
            return self._browser_context

    async def _load_robots(self) -> None:
        if self._robots_loaded:
            return
        self._robots_loaded = True
        robots_url = urljoin(self.config.base_url, "/robots.txt")
        parser = RobotFileParser()
        try:
            response = await self.client.get(robots_url)
            if response.status_code >= 400:
                LOGGER.warning("robots.txt unavailable at %s (%s)", robots_url, response.status_code)
                self._robots_parser = None
                return
            parser.parse(response.text.splitlines())
            self._robots_parser = parser
        except httpx.HTTPError as exc:
            LOGGER.warning("Failed to load robots.txt from %s: %s", robots_url, exc)
            self._robots_parser = None

    async def _check_robots(self, url: str) -> None:
        if not self.config.robots_aware:
            return
        await self._load_robots()
        if self._robots_parser and not self._robots_parser.can_fetch(self.config.user_agent, url):
            raise PermanentFetchError(f"robots.txt disallows fetching {url}")

    async def _request(self, url: str, *, accept: str = "*/*") -> httpx.Response:
        await self._check_robots(url)
        await self.rate_limiter.wait()
        try:
            response = await self.client.get(url, headers={"Accept": accept})
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise RetryableFetchError(f"Request failed for {url}: {exc}") from exc
        if response.status_code == 404:
            raise PermanentFetchError(f"404 for {url}")
        if response.status_code == 429:
            raise RetryableFetchError(f"429 rate limited for {url}")
        if response.status_code >= 500:
            raise RetryableFetchError(f"{response.status_code} server error for {url}")
        if response.status_code >= 400:
            raise PermanentFetchError(f"{response.status_code} client error for {url}")
        return response

    async def _request_with_retry(self, url: str, *, accept: str = "*/*") -> httpx.Response:
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(self.config.retries),
            wait=wait_exponential(multiplier=1, min=1, max=8),
            retry=retry_if_exception_type(RetryableFetchError),
            reraise=True,
        ):
            with attempt:
                return await self._request(url, accept=accept)
        raise RetryableFetchError(f"Exhausted retries for {url}")

    async def fetch_html(self, url: str) -> str:
        response = await self._request_with_retry(url, accept="text/html,application/xhtml+xml")
        return response.text

    async def fetch_json(self, url: str) -> dict[str, Any]:
        response = await self._request_with_retry(url, accept="application/json")
        return response.json()

    async def fetch_text(self, url: str, *, accept: str = "text/plain,*/*") -> str:
        response = await self._request_with_retry(url, accept=accept)
        return response.text

    async def fetch_page(self, url: str, *, wait_for_selector: str | None = None) -> str:
        html = await self.fetch_html(url)
        if self.config.use_playwright and self.should_render(url, html):
            LOGGER.info("Falling back to Playwright for %s", url)
            return await self.fetch_rendered_html(url, wait_for_selector=wait_for_selector)
        return html

    async def fetch_rendered_html(
        self,
        url: str,
        *,
        wait_for_selector: str | None = None,
        scroll_steps: int = 0,
        scroll_pause_ms: int = 1200,
    ) -> str:
        return await self.render_page(
            url,
            wait_for_selector=wait_for_selector,
            scroll_steps=scroll_steps,
            scroll_pause_ms=scroll_pause_ms,
        )

    async def render_page(
        self,
        url: str,
        *,
        wait_for_selector: str | None = None,
        click_texts: Iterable[str] | None = None,
        scroll_steps: int = 0,
        scroll_pause_ms: int = 1200,
    ) -> str:
        await self._check_robots(url)
        await self.rate_limiter.wait()
        context = await self._ensure_browser_context()
        page = await context.new_page()
        try:
            await page.goto(url, wait_until="networkidle", timeout=self.config.playwright_timeout_ms)
            for text in click_texts or []:
                try:
                    button = page.get_by_role("button", name=text)
                    await button.click(timeout=3_000)
                except Exception:
                    try:
                        target = page.get_by_text(text, exact=True)
                        await target.click(timeout=3_000)
                    except Exception:
                        LOGGER.debug("Playwright click target '%s' not found on %s", text, url)
            if wait_for_selector:
                try:
                    await page.wait_for_selector(wait_for_selector, timeout=self.config.playwright_timeout_ms)
                except Exception:
                    LOGGER.debug("Selector %s not found after render for %s", wait_for_selector, url)
            if scroll_steps > 0:
                await self.scroll_page(page, steps=scroll_steps, pause_ms=scroll_pause_ms)
            return await page.content()
        finally:
            await page.close()

    @staticmethod
    async def scroll_page(page, *, steps: int, pause_ms: int = 1200) -> None:
        previous_height = 0
        stable_rounds = 0
        for _ in range(steps):
            await page.mouse.wheel(0, 7000)
            await page.wait_for_timeout(pause_ms)
            height = await page.evaluate("() => document.body.scrollHeight")
            if height == previous_height:
                stable_rounds += 1
                if stable_rounds >= 2:
                    break
            else:
                stable_rounds = 0
                previous_height = height

    def should_render(self, url: str, html: str) -> bool:
        if looks_like_app_shell(html):
            return True
        path = urlparse(url).path or "/"
        return any(path == route or path.startswith(route.rstrip("/")) for route in self.config.render_routes)

    def save_snapshot(self, url: str, html: str, *, label: str = "pages") -> str | None:
        if not self.config.save_html:
            return None
        root = self.config.snapshot_dir / label
        path: Path = make_snapshot_path(root, url)
        write_text_file(path, html)
        return str(path)
