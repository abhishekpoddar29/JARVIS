from __future__ import annotations

import asyncio
import logging
import sys
import traceback
from typing import Any
from urllib.parse import urlparse

from config import get_settings
from security.safe_url import UnsafeUrlError, validate_public_http_url
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from tools.base import BaseTool, ToolResult


logger = logging.getLogger(__name__)
settings = get_settings()


MAX_LINKS = 100
PAGE_TIMEOUT_MS = 30_000

# SEC prefers a declared automated user agent.
# Replace the contact email with your real project/contact email later.
DECLARED_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36 "
    "JarvisResearchBot/0.1 contact:local@example.com"
)


class WebScraperTool(BaseTool):
    name: str = "web_scraper_tool"

    description: str = (
        "Use this tool to scrape readable text content and links from a "
        "specific URL. This uses a real headless browser with Playwright, "
        "so it can read JavaScript-rendered websites like Reddit, Twitter, "
        "or modern SPAs. Provide the exact URL."
    )

    async def run(self, url: str) -> ToolResult:
        url = url.strip()

        if not settings.enable_web_scraper:
            return ToolResult(
                ok=False,
                content="Web scraping is disabled by ENABLE_WEB_SCRAPER.",
                data={"url": url},
            )

        logger.info(
            "[WebScraper] Launching headless browser for: %s",
            url,
        )

        try:
            scrape_data = await asyncio.to_thread(
                self._sync_scrape,
                url,
            )

            text = scrape_data.get("text", "")
            links = scrape_data.get("links", [])

            if not text.strip():
                return ToolResult(
                    ok=False,
                    content=(
                        "Scraping succeeded but no visible text was found."
                    ),
                    data={
                        "url": url,
                        "text": "",
                        "links": links,
                        "char_length": 0,
                    },
                )

            return ToolResult(
                ok=True,
                content=f"Successfully scraped {url}.",
                data={
                    "url": url,
                    "text": text,
                    "links": links,
                    "char_length": len(text),
                },
            )

        except Exception as exc:
            logger.error(
                "[WebScraper] Unexpected error: %s - %s",
                type(exc).__name__,
                exc,
            )
            logger.error(traceback.format_exc())

            return ToolResult(
                ok=False,
                content=(
                    f"Scraping failed: {type(exc).__name__} - {exc}"
                ),
                data={
                    "url": url,
                    "text": "",
                    "links": [],
                    "char_length": 0,
                    "error": str(exc),
                },
            )

    def _sync_scrape(
        self,
        url: str,
    ) -> dict[str, Any]:
        """
        Synchronous Playwright execution meant to run in a thread.

        Returns:
            {
                "text": str,
                "links": list[dict[str, str]]
            }
        """
        self._validate_url(url)

        if self._is_pdf_url(url):
            raise ValueError(
                "Skipping PDF file. Use a PDF reader/downloader tool instead."
            )

        if sys.platform == "win32":
            asyncio.set_event_loop_policy(
                asyncio.WindowsProactorEventLoopPolicy()
            )

        browser = None

        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(
                    headless=True,
                    args=[
                        "--disable-gpu",
                        "--no-sandbox",
                        "--disable-dev-shm-usage",
                    ],
                )

                context = browser.new_context(
                    user_agent=DECLARED_USER_AGENT,
                    viewport={
                        "width": 1366,
                        "height": 768,
                    },
                    java_script_enabled=True,
                )

                context.set_extra_http_headers({
                    "Accept-Language": "en-US,en;q=0.9",
                    "From": "local@example.com",
                })

                page = context.new_page()
                page.set_default_timeout(PAGE_TIMEOUT_MS)
                page.set_default_navigation_timeout(PAGE_TIMEOUT_MS)

                response = page.goto(
                    url,
                    wait_until="domcontentloaded",
                    timeout=PAGE_TIMEOUT_MS,
                )
                try:
                    validate_public_http_url(page.url)
                except UnsafeUrlError as exc:
                    raise ValueError(f"Redirected to an unsafe URL: {exc}") from exc

                page.wait_for_timeout(2_000)

                status = response.status if response is not None else None

                text_content = page.evaluate("""
                    () => {
                        if (!document.body) return "";
                        return document.body.innerText || "";
                    }
                """)

                links = page.evaluate("""
                    () => {
                        const anchors = Array.from(
                            document.querySelectorAll("a[href]")
                        );

                        return anchors.map((anchor) => ({
                            text: (anchor.innerText || "").trim(),
                            href: anchor.href || ""
                        }));
                    }
                """)

                cleaned_text = self._clean_text(text_content or "")
                cleaned_links = self._clean_links(links)

                return {
                    "text": cleaned_text,
                    "links": cleaned_links,
                    "status": status,
                }

        except PlaywrightTimeoutError as exc:
            logger.error("[WebScraper] Timeout error: %s", exc)
            raise

        except PlaywrightError as exc:
            logger.error("[WebScraper] Playwright error: %s", exc)
            raise

        except Exception as exc:
            logger.error("[WebScraper] Thread error: %s", exc)
            raise

        finally:
            if browser is not None:
                try:
                    browser.close()
                except Exception:
                    pass

    def _validate_url(
        self,
        url: str,
    ) -> None:
        parsed = urlparse(url)

        if parsed.scheme not in {"http", "https"}:
            raise ValueError(
                "URL must start with http:// or https://."
            )

        if not parsed.netloc:
            raise ValueError(
                "URL must include a valid domain."
            )

        try:
            validate_public_http_url(url)
        except UnsafeUrlError as exc:
            raise ValueError(str(exc)) from exc

    def _is_pdf_url(
        self,
        url: str,
    ) -> bool:
        lowered = url.lower()
        return lowered.endswith(".pdf") or ".pdf" in lowered

    def _clean_text(
        self,
        text: str,
    ) -> str:
        lines = []

        for line in text.splitlines():
            cleaned = " ".join(line.split())

            if cleaned:
                lines.append(cleaned)

        return "\n".join(lines).strip()

    def _clean_links(
        self,
        links: list[dict[str, str]],
    ) -> list[dict[str, str]]:
        cleaned_links: list[dict[str, str]] = []
        seen: set[str] = set()

        for link in links:
            href = str(link.get("href", "")).strip()
            text = " ".join(
                str(link.get("text", "")).split()
            )

            if not href:
                continue

            if href in seen:
                continue

            seen.add(href)

            cleaned_links.append({
                "text": text,
                "href": href,
            })

            if len(cleaned_links) >= MAX_LINKS:
                break

        return cleaned_links
