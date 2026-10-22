'''
from __future__ import annotations

import asyncio
import json
import re
import urllib.request
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from html.parser import HTMLParser
from typing import Any, Callable, Awaitable
from urllib.error import URLError

from langchain_community.utilities import GoogleSerperAPIWrapper

from agents.gemini_client import GeminiClientError, generate_reply
from config import get_settings
from security.safe_url import validate_public_http_url

settings = get_settings()


# ── Enums & constants ──────────────────────────────────────────────────────────

class ResearchDepth(StrEnum):
    QUICK = "quick"
    DEEP = "deep"


DEEP_TRIGGERS = {
    "research", "deep dive", "analyze", "analyse",
    "investigate", "full report", "deep research",
    "in depth", "in-depth", "comprehensive",
}

MAX_QUERIES_QUICK = 2
MAX_QUERIES_DEEP = 5
MAX_RESULTS_PER_QUERY = 5
FETCH_TOP_N_QUICK = 3
FETCH_TOP_N_DEEP = 6
FETCH_TIMEOUT = 8
MAX_CONTENT_CHARS = 4000

# F14: Critic Loop settings
MAX_REVIEW_ITERATIONS = 2

_SKIP_DOMAINS = {
    "youtube.com", "youtu.be", "twitter.com", "x.com",
    "instagram.com", "facebook.com", "tiktok.com",
    "reddit.com", "linkedin.com",
}


# ── Data models ────────────────────────────────────────────────────────────────

@dataclass
class SearchResult:
    title: str
    link: str
    snippet: str
    query: str
    rank_score: float = 0.0
    full_content: str = ""

@dataclass
class ReviewResult:
    is_sufficient: bool
    reasoning: str
    next_queries: list[str]

@dataclass
class ResearchReport:
    topic: str
    depth: ResearchDepth
    markdown: str
    sources: list[SearchResult]
    queries_used: list[str]
    iterations: int = 1
    created_at: str = field(
        default_factory=lambda: datetime.now(UTC).isoformat()
    )

@dataclass
class ResearchContext:
    topic: str
    report_summary: str
    queries_used: list[str]
    sources: list[dict[str, str]]
    created_at: str


# ── Lightweight HTML text & code extractor ───────────────────────────────────

class _TextExtractor(HTMLParser):
    SKIP_TAGS = {"script", "style", "nav", "footer", "header", "aside", "noscript"}

    def __init__(self):
        super().__init__()
        self._skip = 0
        self.chunks: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() in self.SKIP_TAGS:
            self._skip += 1

    def handle_endtag(self, tag):
        if tag.lower() in self.SKIP_TAGS and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip:
            text = data.strip()
            if text:
                self.chunks.append(text)

def _extract_text_and_code(html: str) -> str:
    """
    Extracts visible text AND code snippets from <pre>/<code> tags.
    This ensures the Tech Lead gets actual HTML/CSS/JS examples to learn from.
    """
    # 1. Extract code blocks using regex (before stripping tags)
    code_matches = re.findall(r'<(?:pre|code)[^>]*>(.*?)</(?:pre|code)>', html, re.DOTALL | re.IGNORECASE)
    
    def _clean_html_tags(text):
        # Remove inner HTML tags
        text = re.sub(r'<[^>]+>', '', text)
        # Decode basic HTML entities
        text = text.replace('&lt;', '<').replace('&gt;', '>').replace('&amp;', '&').replace('&quot;', '"')
        return text.strip()

    code_blocks = [_clean_html_tags(m) for m in code_matches if _clean_html_tags(m)]
    
    # 2. Extract normal visible text (ignoring scripts/styles)
    parser = _TextExtractor()
    try:
        parser.feed(html)
    except Exception:
        pass
    raw_text = " ".join(parser.chunks)
    raw_text = re.sub(r"\s+", " ", raw_text).strip()
    
    # 3. Combine them
    if code_blocks:
        code_section = "\n\n--- EXTRACTED CODE EXAMPLES ---\n"
        for i, block in enumerate(code_blocks[:5]): # Limit to 5 blocks to save context
            code_section += f"\n```code\n{block}\n```\n"
        return raw_text + code_section
    
    return raw_text

def _should_skip(url: str) -> bool:
    try:
        validate_public_http_url(url)
    except Exception:
        return True

    for domain in _SKIP_DOMAINS:
        if domain in url:
            return True
    return False


# ── Prompts ────────────────────────────────────────────────────────────────────

_QUERY_GEN_PROMPT = """\
You are a research strategist. Given a research topic and optional prior context,
generate {n} distinct, targeted search queries that together give broad and deep
coverage of the topic. Always include the current year (2026) in queries about
recent events, funding, leadership, or model releases.

Queries should cover:
- Core definition / overview
- Recent news / developments 2026
- Key players / companies / people (current as of 2026)
- Data, statistics, funding (latest figures)
- Criticism, risks, controversies
- UI/UX patterns, design systems, and code examples (if applicable)

Respond ONLY with a JSON array of strings. No preamble, no markdown fences.
Example: ["query one 2026", "query two latest", "query three current"]
"""

_RANK_PROMPT = """\
You are a source quality evaluator. Given a list of search results, score each
from 0.0 to 1.0 based on:
- Source credibility (official sites, known outlets > random blogs)
- Relevance to the research topic
- Recency signals in snippet

Respond ONLY with a JSON array of floats in the same order as input.
No preamble, no markdown fences. Example: [0.9, 0.7, 0.4]
"""

# F14: The Critic Prompt
_REVIEW_PROMPT = """\
You are a meticulous research reviewer. You are given a research topic and a summary of scraped content.
Determine if the provided content is sufficient to write a comprehensive report.

1. If the content covers the core topic, recent developments, and key data, mark as sufficient.
2. If there are GLARING GAPS (e.g., no statistics, missing key people, no recent news), mark as insufficient.

Respond ONLY with a JSON object with this exact schema:
{
  "is_sufficient": boolean,
  "reasoning": "Brief explanation of what is missing or why it is sufficient.",
  "next_queries": ["query1", "query2"] // If insufficient, provide 2-3 new search queries to fill the gaps. Empty array if sufficient.
}
No markdown fences. No preamble.
"""

_QUICK_REPORT_PROMPT = """\
You are a research analyst. Write a concise research summary (~400-600 words) in
Markdown. Use these sections:
## Overview
## Key Facts
## Recent Developments
## Code & Design Patterns (if any code blocks were provided in the sources, include them here verbatim)
## Sources

STRICT GROUNDING RULES — NO EXCEPTIONS:
- Every claim must come directly from the provided source content below.
- If the source content does not explicitly state a fact, write "Insufficient data."
- NEVER use your training data to fill gaps.
- If sources contradict each other, create a sub-section under Key Facts called "Conflicting Reports".
- If the source content includes ```code``` blocks, you MUST preserve them exactly as written.

Cite sources inline as [Source N](url).
"""

_DEEP_REPORT_PROMPT = """\
You are a senior research analyst. Write a comprehensive research report
(2000-4000 words) in Markdown with these sections:

## Executive Summary
## Overview
## Recent Developments (last 30 days)
## Key Players / Companies / People
## Data & Statistics
## Code & Design Architecture (if any code blocks were provided in the sources, include them here verbatim)
## Strategic Analysis
## Risks & Challenges
## Future Outlook
## Sources

STRICT GROUNDING RULES — NO EXCEPTIONS:
- Every single claim must be directly traceable to the provided source content.
- If source content does not explicitly state a fact, write "Insufficient data available."
- NEVER use training knowledge to fill gaps — not for executives, funding, dates, or model names.
- If sources contradict each other (e.g., different valuation numbers, conflicting timelines), you MUST create a subsection called "## Conflicting Reports" and explain both sides.
- When in doubt, omit rather than guess.
- If the source content includes ```code``` blocks, you MUST preserve them exactly as written so the engineering team can use them.

Cite sources inline as [Source N](url).
"""

_FOLLOWUP_PROMPT = """\
You are continuing a research session. The user previously researched: {topic}

Prior context summary:
{summary}

Now answer the follow-up question using prior context AND new search results.

STRICT GROUNDING RULES:
- Only state facts present in the provided source content or prior context.
- Write "Insufficient data" if sources don't cover something.
- If the source content includes ```code``` blocks, you MUST preserve them exactly as written.

Write in Markdown. Cite sources as [Source N](url).
"""


# ── Core agent ─────────────────────────────────────────────────────────────────

class ResearchAgent:

    def __init__(self) -> None:
        if not settings.serper_api_key:
            raise RuntimeError("SERPER_API_KEY not configured")
        if not settings.gemini_api_key:
            raise RuntimeError("GEMINI_API_KEY not configured")

        self._serper = GoogleSerperAPIWrapper(
            serper_api_key=settings.serper_api_key
        )

    # ── Helper to emit progress to WebSocket ──────────────────────────────────
    async def _emit(self, callback: Callable[[str], Awaitable[None]] | None, message: str):
        if callback:
            try:
                await callback(message)
            except Exception:
                pass # Never crash research due to UI emit failure

    # ── Public API ─────────────────────────────────────────────────────────────

    async def run(
        self,
        topic: str,
        depth: ResearchDepth = ResearchDepth.QUICK,
        prior_context: ResearchContext | None = None,
        progress_callback: Callable[[str], Awaitable[None]] | None = None,
    ) -> ResearchReport:
        n_queries = MAX_QUERIES_DEEP if depth == ResearchDepth.DEEP else MAX_QUERIES_QUICK
        fetch_n = FETCH_TOP_N_DEEP if depth == ResearchDepth.DEEP else FETCH_TOP_N_QUICK

        await self._emit(progress_callback, f"Generating initial search strategy for {topic}...")
        queries = await self._generate_queries(topic, n_queries, prior_context)
        
        all_ranked_results: list[SearchResult] = []
        seen_links: set[str] = set()
        iterations_run = 1

        # F14: The Critic Loop
        for i in range(MAX_REVIEW_ITERATIONS):
            iterations_run = i + 1
            await self._emit(progress_callback, f"Iteration {iterations_run}: Searching the web...")
            raw_results = await self._parallel_search(queries)
            
            await self._emit(progress_callback, f"Iteration {iterations_run}: Ranking and filtering sources...")
            ranked = await self._rank_results(topic, raw_results)
            
            # Filter out already seen links
            new_ranked = [r for r in ranked if r.link not in seen_links]
            for r in new_ranked:
                seen_links.add(r.link)
                all_ranked_results.append(r)

            await self._emit(progress_callback, f"Iteration {iterations_run}: Scraping top {fetch_n} web pages...")
            await self._fetch_page_content(all_ranked_results, fetch_n)

            if i < MAX_REVIEW_ITERATIONS - 1:
                await self._emit(progress_callback, f"Iteration {iterations_run}: Analyzing gathered intelligence...")
                review = await self._review_context(topic, all_ranked_results)
                
                if review.is_sufficient:
                    await self._emit(progress_callback, "Research is comprehensive. Proceeding to synthesis.")
                    break
                else:
                    await self._emit(progress_callback, f"Gaps found: {review.reasoning}. Refining search...")
                    queries = review.next_queries
                    if not queries:
                        break # Safety if LLM returns empty queries
            else:
                await self._emit(progress_callback, "Max research iterations reached. Proceeding to synthesis.")

        await self._emit(progress_callback, "Synthesizing final markdown report...")
        report_md = await self._build_report(topic, depth, all_ranked_results, prior_context)

        return ResearchReport(
            topic=topic,
            depth=depth,
            markdown=report_md,
            sources=all_ranked_results,
            queries_used=queries,
            iterations=iterations_run
        )

    async def run_followup(
        self,
        question: str,
        prior_context: ResearchContext,
        progress_callback: Callable[[str], Awaitable[None]] | None = None,
    ) -> ResearchReport:
        await self._emit(progress_callback, "Generating follow-up search queries...")
        queries = await self._generate_queries(question, MAX_QUERIES_QUICK, prior_context)
        
        await self._emit(progress_callback, "Searching the web for new information...")
        raw_results = await self._parallel_search(queries)
        ranked = await self._rank_results(question, raw_results)
        
        await self._emit(progress_callback, "Scraping top sources...")
        await self._fetch_page_content(ranked, FETCH_TOP_N_QUICK)

        await self._emit(progress_callback, "Synthesizing follow-up report...")
        report_md = await self._build_followup_report(question, ranked, prior_context)

        return ResearchReport(
            topic=question,
            depth=ResearchDepth.QUICK,
            markdown=report_md,
            sources=ranked,
            queries_used=queries,
        )

    # ── Step 1: Query generation ───────────────────────────────────────────────

    async def _generate_queries(
        self,
        topic: str,
        n: int,
        prior_context: ResearchContext | None,
    ) -> list[str]:
        context_block = ""
        if prior_context:
            context_block = (
                f"\nPrior research topic: {prior_context.topic}\n"
                f"Prior queries used: {prior_context.queries_used}\n"
                "Generate queries that complement, not duplicate, prior research.\n"
            )

        user_msg = (
            f"Research topic: {topic}\n"
            f"{context_block}"
            f"Generate exactly {n} search queries. Include '2026' in queries about "
            f"recent events, leadership, funding, or releases."
        )

        loop = asyncio.get_running_loop()
        try:
            reply = await loop.run_in_executor(
                None,
                lambda: generate_reply(
                    api_key=settings.gemini_api_key,
                    model=settings.gemini_model,
                    user_message=user_msg,
                    system_instruction=_QUERY_GEN_PROMPT.format(n=n),
                    temperature=0.4,
                    max_output_tokens=512,
                ),
            )
            text = reply.text.strip()
            if "```" in text:
                parts = text.split("```")
                for part in parts:
                    part = part.strip()
                    if part.startswith("json"):
                        part = part[4:].strip()
                    try:
                        parsed = json.loads(part)
                        if isinstance(parsed, list) and parsed:
                            return [str(q) for q in parsed[:n]]
                    except Exception:
                        continue
            else:
                queries = json.loads(text)
                if isinstance(queries, list) and queries:
                    return [str(q) for q in queries[:n]]
        except Exception as exc:
            print(f"[QUERY GEN] Failed: {exc}")

        fallback = [
            f"{topic} company overview",
            f"{topic} latest news 2026",
            f"{topic} funding valuation 2026",
            f"{topic} products technology",
            f"{topic} CEO leadership team 2026",
        ][:n]
        return fallback

    # ── Step 2: Parallel search ────────────────────────────────────────────────

    async def _parallel_search(self, queries: list[str]) -> list[SearchResult]:
        loop = asyncio.get_running_loop()

        async def _search_one(query: str) -> list[SearchResult]:
            try:
                raw = await loop.run_in_executor(
                    None, lambda: self._serper.results(query)
                )
                organic = raw.get("organic", [])[:MAX_RESULTS_PER_QUERY]
                return [
                    SearchResult(
                        title=item.get("title", ""),
                        link=item.get("link", ""),
                        snippet=item.get("snippet", ""),
                        query=query,
                    )
                    for item in organic
                    if item.get("title") and item.get("link")
                ]
            except Exception:
                return []

        nested = await asyncio.gather(*[_search_one(q) for q in queries])
        return [r for batch in nested for r in batch]

    # ── Step 3: Rank & deduplicate ─────────────────────────────────────────────

    async def _rank_results(
        self,
        topic: str,
        results: list[SearchResult],
    ) -> list[SearchResult]:
        if not results:
            return []

        unique: list[SearchResult] = []
        for r in results:
            if r.link not in {u.link for u in unique}:
                unique.append(r)

        candidates = unique[:15]
        snippets_json = json.dumps(
            [{"title": r.title, "snippet": r.snippet, "link": r.link}
             for r in candidates]
        )

        loop = asyncio.get_running_loop()
        try:
            reply = await loop.run_in_executor(
                None,
                lambda: generate_reply(
                    api_key=settings.gemini_api_key,
                    model=settings.gemini_model,
                    user_message=f"Topic: {topic}\n\nResults:\n{snippets_json}",
                    system_instruction=_RANK_PROMPT,
                    temperature=0.1,
                    max_output_tokens=256,
                ),
            )
            scores = json.loads(reply.text)
            if isinstance(scores, list) and len(scores) == len(candidates):
                for result, score in zip(candidates, scores):
                    result.rank_score = float(score)
                candidates.sort(key=lambda r: r.rank_score, reverse=True)
        except (json.JSONDecodeError, GeminiClientError, ValueError):
            pass

        return candidates

    # ── Step 4: Fetch full page content ───────────────────────────────────────

    async def _fetch_page_content(
        self,
        results: list[SearchResult],
        fetch_n: int,
    ) -> None:
        loop = asyncio.get_running_loop()
        to_fetch = [r for r in results[:fetch_n] if not _should_skip(r.link)]

        async def _fetch_one(result: SearchResult) -> None:
            try:
                req = urllib.request.Request(
                    result.link,
                    headers={
                        "User-Agent": (
                            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/120.0.0.0 Safari/537.36"
                        )
                    },
                )
                html = await loop.run_in_executor(
                    None,
                    lambda: urllib.request.urlopen(req, timeout=FETCH_TIMEOUT)
                    .read()
                    .decode("utf-8", errors="replace"),
                )
                # FIX: Use the new text+code extractor
                text = _extract_text_and_code(html)
                result.full_content = text[:MAX_CONTENT_CHARS]
            except Exception:
                result.full_content = ""

        await asyncio.gather(*[_fetch_one(r) for r in to_fetch])

    # ── F14 Step 5: The Critic Loop ───────────────────────────────────────────

    async def _review_context(
        self,
        topic: str,
        results: list[SearchResult],
    ) -> ReviewResult:
        """Evaluates if scraped content is sufficient. If not, generates new queries."""
        # Only feed the critic the top results that actually have content to save tokens
        valid_results = [r for r in results if r.full_content]
        if not valid_results:
            return ReviewResult(is_sufficient=False, reasoning="No content was scraped.", next_queries=[f"{topic} overview 2026"])

        # Compact the content for the critic prompt (use first 1000 chars per source)
        compact_sources = "\n".join([
            f"[Source {i+1}] {r.title}\nContent: {r.full_content[:1000]}..."
            for i, r in enumerate(valid_results[:5])
        ])

        user_msg = f"Research Topic: {topic}\n\nScraped Content Summary:\n{compact_sources}"
        
        loop = asyncio.get_running_loop()
        try:
            reply = await loop.run_in_executor(
                None,
                lambda: generate_reply(
                    api_key=settings.gemini_api_key,
                    model=settings.gemini_model,
                    user_message=user_msg,
                    system_instruction=_REVIEW_PROMPT,
                    temperature=0.2,
                    max_output_tokens=512,
                ),
            )
            text = reply.text.strip()
            if text.startswith("```json"): text = text[7:-3].strip()
            
            data = json.loads(text)
            return ReviewResult(
                is_sufficient=bool(data.get("is_sufficient", False)),
                reasoning=data.get("reasoning", "No reasoning provided."),
                next_queries=data.get("next_queries", [])
            )
        except Exception as exc:
            print(f"[REVIEWER] Failed: {exc}")
            # Fail safe: assume sufficient to avoid infinite loops
            return ReviewResult(is_sufficient=True, reasoning="Reviewer failed, proceeding with available data.", next_queries=[])

    # ── Step 6: Full report ───────────────────────────────────────────────────

    async def _build_report(
        self,
        topic: str,
        depth: ResearchDepth,
        results: list[SearchResult],
        prior_context: ResearchContext | None,
    ) -> str:
        sources_block = self._format_sources_for_prompt(results)
        prior_block = ""
        if prior_context:
            prior_block = (
                f"\nPrior research context on '{prior_context.topic}':\n"
                f"{prior_context.report_summary}\n\n"
            )

        user_msg = (
            f"Research topic: {topic}\n"
            f"Current date: {datetime.now(UTC).strftime('%B %d, %Y')}\n"
            f"{prior_block}"
            f"Source content:\n{sources_block}"
        )

        system_prompt = _DEEP_REPORT_PROMPT if depth == ResearchDepth.DEEP else _QUICK_REPORT_PROMPT
        max_tokens = 8192 if depth == ResearchDepth.DEEP else 2048

        loop = asyncio.get_running_loop()
        reply = await loop.run_in_executor(
            None,
            lambda: generate_reply(
                api_key=settings.gemini_api_key,
                model=settings.gemini_model,
                user_message=user_msg,
                system_instruction=system_prompt,
                temperature=0.3,
                max_output_tokens=max_tokens,
            ),
        )
        return reply.text

    # ── Step 7: Follow-up report ──────────────────────────────────────────────

    async def _build_followup_report(
        self,
        question: str,
        results: list[SearchResult],
        prior_context: ResearchContext,
    ) -> str:
        sources_block = self._format_sources_for_prompt(results)
        user_msg = (
            f"Follow-up question: {question}\n"
            f"Current date: {datetime.now(UTC).strftime('%B %d, %Y')}\n\n"
            f"New source content:\n{sources_block}"
        )

        system_prompt = _FOLLOWUP_PROMPT.format(
            topic=prior_context.topic,
            summary=prior_context.report_summary,
        )

        loop = asyncio.get_running_loop()
        reply = await loop.run_in_executor(
            None,
            lambda: generate_reply(
                api_key=settings.gemini_api_key,
                model=settings.gemini_model,
                user_message=user_msg,
                system_instruction=system_prompt,
                temperature=0.3,
                max_output_tokens=2048,
            ),
        )
        return reply.text

    # ── Helpers ────────────────────────────────────────────────────────────────

    @staticmethod
    def _format_sources_for_prompt(results: list[SearchResult]) -> str:
        lines = []
        for idx, r in enumerate(results, start=1):
            content = r.full_content if r.full_content else r.snippet
            lines.append(
                f"[Source {idx}] {r.title}\n"
                f"URL: {r.link}\n"
                f"Content: {content}\n"
            )
        return "\n".join(lines)


# ── Depth detector ─────────────────────────────────────────────────────────────

def detect_depth(message: str) -> ResearchDepth:
    lower = message.lower()
    if any(trigger in lower for trigger in DEEP_TRIGGERS):
        return ResearchDepth.DEEP
    return ResearchDepth.QUICK


# ── Context helpers ────────────────────────────────────────────────────────────

def report_to_context(report: ResearchReport) -> ResearchContext:
    words = report.markdown.split()
    summary = " ".join(words[:300])
    return ResearchContext(
        topic=report.topic,
        report_summary=summary,
        queries_used=report.queries_used,
        sources=[
            {"title": s.title, "link": s.link}
            for s in report.sources[:10]
        ],
        created_at=report.created_at,
    )

def context_to_dict(ctx: ResearchContext) -> dict[str, Any]:
    return {
        "topic": ctx.topic,
        "report_summary": ctx.report_summary,
        "queries_used": ctx.queries_used,
        "sources": ctx.sources,
        "created_at": ctx.created_at,
    }

def context_from_dict(d: dict[str, Any]) -> ResearchContext:
    return ResearchContext(
        topic=d["topic"],
        report_summary=d["report_summary"],
        queries_used=d["queries_used"],
        sources=d["sources"],
        created_at=d["created_at"],
    )
    '''
