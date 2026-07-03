from __future__ import annotations

import asyncio
import logging
import re
import sys
import traceback
from typing import Any

from playwright.sync_api import sync_playwright, Error as PlaywrightError

from tools.base import BaseTool, ToolResult

logger = logging.getLogger(__name__)

class WebScraperTool(BaseTool):
    name: str = "web_scraper_tool"
    description: str = (
        "Use this tool to scrape text content from a specific URL. "
        "This uses a real headless browser (Playwright), so it can read "
        "JavaScript-rendered websites like Reddit, Twitter, or modern SPAs. "
        "Provide the exact URL."
    )

    async def run(self, url: str) -> ToolResult:
        logger.info(f"[WebScraper] Launching headless browser for: {url}")
        
        try:
            # Run the synchronous Playwright code in a separate thread
            clean_text = await asyncio.to_thread(self._sync_scrape, url)
            
            if not clean_text:
                return ToolResult(ok=False, content="Scraping succeeded but no visible text was found.")

            return ToolResult(
                ok=True,
                content=f"Successfully scraped {url}:\n\n{clean_text}",
                data={"url": url, "char_length": len(clean_text)}
            )

        except Exception as e:
            logger.error(f"[WebScraper] Unexpected error: {type(e).__name__} - {e}")
            logger.error(traceback.format_exc())
            return ToolResult(ok=False, content=f"Scraping failed: {type(e).__name__} - {e}")

    def _sync_scrape(self, url: str) -> str:
        """Synchronous Playwright execution meant to run in a thread."""
        # FIX: Force ProactorEventLoop on Windows inside this thread so Playwright can spawn subprocesses
        if sys.platform == 'win32':
            asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

        browser = None
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(
                    headless=True,
                    args=["--disable-gpu", "--no-sandbox", "--disable-dev-shm-usage"]
                )
                page = browser.new_page()
                
                page.set_extra_http_headers({
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                })

                page.goto(url, wait_until="domcontentloaded", timeout=20000)
                page.wait_for_timeout(2000)

                text_content = page.evaluate("""
                    () => {
                        let text = '';
                        document.querySelectorAll('p, h1, h2, h3, h4, span, li, article').forEach(el => {
                            const inner = el.innerText ? el.innerText.trim() : '';
                            if (inner) {
                                text += inner + '\\n';
                            }
                        });
                        return text;
                    }
                """)

                return text_content or ""

        except PlaywrightError as e:
            logger.error(f"[WebScraper] Playwright error: {e}")
            raise
        except Exception as e:
            logger.error(f"[WebScraper] Thread error: {e}")
            raise
        finally:
            if browser:
                try:
                    browser.close()
                except Exception:
                    pass