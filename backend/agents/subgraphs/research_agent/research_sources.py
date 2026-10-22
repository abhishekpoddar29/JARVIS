from __future__ import annotations

import asyncio
import json
import re
from collections import defaultdict
from datetime import UTC, datetime
from html.parser import HTMLParser
from typing import Any, Iterable
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit
from urllib.request import Request, urlopen

from langchain_community.utilities import GoogleSerperAPIWrapper
from playwright.sync_api import sync_playwright

from security.safe_url import UnsafeUrlError, validate_public_http_url

from .research_state import ResearchSource, SearchTask, SourcePassage


_TRACKING_QUERY_KEYS = {
    "fbclid", "gclid", "mc_cid", "mc_eid", "ref", "ref_src", "source",
}
_TOKEN_RE = re.compile(r"[a-zA-Z0-9]{3,}")
_SITE_RE = re.compile(r"\bsite:[^\s]+", re.IGNORECASE)
_BLOCKED_TEXT = (
    "just a moment", "verify you are human", "access denied", "captcha",
    "enable javascript and cookies", "sign in to continue",
)
_MAX_HTML_BYTES = 6_000_000
_MAX_PDF_BYTES = 24_000_000
_MIN_READABLE_CHARS = 250
_YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")

_REGULATORY_DOMAINS = {
    "sec.gov", "ftc.gov", "justice.gov", "commerce.gov", "federalregister.gov",
    "congress.gov", "supremecourt.gov", "gov.uk", "legislation.gov.uk",
    "europa.eu", "eur-lex.europa.eu", "ec.europa.eu", "competition-policy.ec.europa.eu",
    "oecd.org", "worldbank.org", "imf.org", "who.int", "un.org",
}
_KNOWN_TECHNICAL_DOMAINS = {
    "docs.python.org", "developer.mozilla.org", "learn.microsoft.com",
    "cloud.google.com", "docs.aws.amazon.com", "docs.github.com",
    "kubernetes.io", "docs.anthropic.com", "platform.openai.com",
    "docs.langchain.com", "playwright.dev", "pymupdf.readthedocs.io",
}
_ACADEMIC_DOMAINS = {
    "arxiv.org", "nature.com", "science.org", "sciencedirect.com", "springer.com",
    "acm.org", "ieee.org", "nber.org", "nih.gov", "pubmed.ncbi.nlm.nih.gov",
}
_TIER_1_DOMAINS = {
    "reuters.com", "apnews.com", "bloomberg.com", "ft.com", "wsj.com",
    "bbc.com", "bbc.co.uk", "theguardian.com", "nytimes.com", "economist.com",
}
_SPECIALIST_DOMAINS = {
    "arstechnica.com", "wired.com", "technologyreview.com", "techcrunch.com",
    "lawfaremedia.org", "techpolicy.press", "semianalysis.com", "theregister.com",
}
_INDEPENDENT_ANALYSIS_DOMAINS = {
    "mckinsey.com", "deloitte.com", "pwc.com", "bcg.com", "bain.com",
    "gartner.com", "idc.com", "csis.org", "rand.org", "cfr.org",
    "brookings.edu", "carnegieendowment.org", "chathamhouse.org",
}
_INDUSTRY_ASSOCIATION_DOMAINS = {
    "semi.org", "semiconductors.org", "sia.org", "jeita.or.jp", "wsts.org",
}
_DATABASE_DOMAINS = {
    "crunchbase.com", "pitchbook.com", "tracxn.com", "cbinsights.com",
    "dealroom.co", "privco.com", "companiesmarketcap.com", "startupintros.com",
}
_REFERENCE_DOMAINS = {"wikipedia.org", "britannica.com"}
_SOCIAL_DOMAINS = {
    "reddit.com", "x.com", "twitter.com", "linkedin.com", "facebook.com",
    "instagram.com", "youtube.com", "youtu.be", "tiktok.com", "quora.com",
}
_LOW_VALUE_DOMAINS = {"pinterest.com", "quora.com", "tiktok.com"}


def _canonical_url(url: str) -> str:
    try:
        parts = urlsplit(url.strip())
        if parts.scheme.lower() not in {"http", "https"} or not parts.netloc:
            return ""
        query = [
            (key, value)
            for key, value in parse_qsl(parts.query, keep_blank_values=True)
            if not key.lower().startswith("utm_")
            and key.lower() not in _TRACKING_QUERY_KEYS
        ]
        return urlunsplit(
            (
                parts.scheme.lower(),
                parts.netloc.lower(),
                parts.path.rstrip("/") or "/",
                urlencode(query, doseq=True),
                "",
            )
        )
    except Exception:
        return ""


def _is_safe_public_url(url: str) -> bool:
    try:
        validate_public_http_url(url)
        return True
    except Exception:
        return False


def _domain(url: str) -> str:
    try:
        return urlsplit(url).netloc.lower().removeprefix("www.").split(":", 1)[0]
    except Exception:
        return ""


def _normalize_domain(value: str) -> str:
    raw = value.strip().lower()
    if not raw:
        return ""
    if "://" not in raw:
        raw = f"https://{raw}"
    return _domain(raw).removeprefix("*.")


def _domain_matches(domain: str, expected: str) -> bool:
    expected = _normalize_domain(expected)
    if not expected:
        return False
    if expected.startswith("."):
        return domain.endswith(expected)
    return domain == expected or domain.endswith(f".{expected}")


def _in_domain_set(domain: str, domains: Iterable[str]) -> bool:
    return any(_domain_matches(domain, expected) for expected in domains)


def _looks_regulatory(domain: str) -> bool:
    return (
        domain.endswith(".gov")
        or ".gov." in domain
        or domain.endswith(".gouv.fr")
        or domain.endswith(".bund.de")
        or _in_domain_set(domain, _REGULATORY_DOMAINS)
    )


def _page_type(url: str) -> str:
    domain = _domain(url)
    path = urlsplit(url).path.lower().rstrip("/")
    if domain.startswith(("forum.", "forums.", "community.")) or any(
        token in path for token in ("/forum", "/forums", "/community", "/discussion", "/discussions")
    ):
        return "forum"
    if path.endswith(".pdf"):
        return "pdf"
    if path in {"", "/", "/news", "/news/latest", "/latest", "/blog", "/press"}:
        return "listing"
    if any(token in path for token in ("/search", "/tag/", "/category/", "/archive")):
        return "listing"
    if any(token in path for token in ("/docs", "/documentation", "/reference", "/system-card")):
        return "documentation"
    return "article"


def _looks_technical(url: str) -> bool:
    domain = _domain(url)
    page_type = _page_type(url)
    if page_type == "forum":
        return False
    return (
        page_type == "documentation"
        or domain.startswith(("docs.", "developer.", "developers.", "documentation."))
    )

def _classify_source(url: str, task: SearchTask) -> str:
    """Classify the actual publisher, never the role the search hoped to find."""

    domain = _domain(url)
    page_type = _page_type(url)
    role = str(task.get("search_role", "discovery"))
    preferred = [
        normalized
        for item in task.get("preferred_domains", [])
        if (normalized := _normalize_domain(str(item)))
    ]
    preferred_match = any(_domain_matches(domain, item) for item in preferred)

    if page_type == "forum" or _in_domain_set(domain, _SOCIAL_DOMAINS):
        return "social_or_forum"
    if _in_domain_set(domain, _REFERENCE_DOMAINS):
        return "reference_only"
    if _in_domain_set(domain, _DATABASE_DOMAINS):
        return "database_estimate"
    if _in_domain_set(domain, _ACADEMIC_DOMAINS) or domain.endswith(".edu"):
        return "academic_primary"
    if _looks_regulatory(domain):
        return "primary_regulatory"
    if _in_domain_set(domain, _TIER_1_DOMAINS):
        return "independent_tier_1"
    if _in_domain_set(domain, _SPECIALIST_DOMAINS | _INDEPENDENT_ANALYSIS_DOMAINS | _INDUSTRY_ASSOCIATION_DOMAINS):
        return "independent_specialist"
    if _looks_technical(url) and (
        preferred_match or _in_domain_set(domain, _KNOWN_TECHNICAL_DOMAINS)
    ):
        return "primary_technical"

    if preferred_match:
        if role == "independent":
            return "independent_specialist"
        if role == "reference":
            return "reference_only"
        if role == "regulatory":
            return "primary_regulatory" if _looks_regulatory(domain) else "unknown"
        if role == "technical":
            return "primary_technical"
        if role == "primary":
            return "primary_official"
    return "unknown"


def _role_suitability(source_role: str, search_role: str) -> float:
    mapping = {
        "primary": {
            "primary_official": 6.0,
            "primary_technical": 5.0,
            "primary_regulatory": 4.0,
        },
        "regulatory": {"primary_regulatory": 7.0},
        "technical": {
            "primary_technical": 7.0,
            "academic_primary": 5.0,
            "primary_official": 3.0,
        },
        "independent": {
            "independent_tier_1": 7.0,
            "independent_specialist": 4.5,
            "academic_primary": 4.0,
        },
        "reference": {"reference_only": 5.0, "primary_official": 2.0},
        "discovery": {
            "primary_official": 4.0,
            "primary_regulatory": 4.0,
            "primary_technical": 4.0,
            "academic_primary": 4.0,
            "independent_tier_1": 4.0,
            "independent_specialist": 2.5,
            "reference_only": 1.5,
            "database_estimate": 1.0,
        },
    }
    return mapping.get(search_role, {}).get(source_role, -1.0)


def _quality_tier(source_role: str, fetch_status: str = "pending") -> str:
    if fetch_status == "snippet_only":
        return "low"
    if source_role in {
        "primary_official", "primary_regulatory", "primary_technical",
        "academic_primary", "independent_tier_1",
    }:
        return "high"
    if source_role == "independent_specialist":
        return "medium"
    return "low"


def _temporal_score(
    *,
    publication_date: str,
    title: str,
    snippet: str,
    task: SearchTask,
) -> float:
    """Reward sources whose age matches the current research phase.

    Orientation is allowed to use older foundational material. Verification and gap
    phases prefer recent material for changing facts, without banning undated primary
    documents that may still be authoritative.
    """

    phase = str(task.get("phase", "")).strip().lower()
    time_mode = str(task.get("time_mode", "")).strip().lower()
    text = f"{publication_date} {title} {snippet}"
    years = [int(match.group(0)) for match in _YEAR_RE.finditer(text)]
    newest_year = max(years) if years else None
    current_year = datetime.now(UTC).year

    if time_mode in {"latest"} or phase in {"verification", "gaps"}:
        if newest_year is None:
            score = -0.35
        else:
            age = current_year - newest_year
            if age <= 0:
                score = 2.5
            elif age == 1:
                score = 1.5
            elif age == 2:
                score = 0.5
            elif age >= 4:
                score = -2.5
            else:
                score = -0.5
        if re.search(r"\b(latest|current|updated|today|recent)\b", text, re.IGNORECASE):
            score += 0.5
        return score

    if time_mode in {"foundational", "historical"} or phase == "orientation":
        score = 0.0
        if re.search(r"\b(history|historical|origin|foundation|overview|introduction|principles?)\b", text, re.IGNORECASE):
            score += 1.0
        # Do not penalize an old primary source merely because it is old when the task
        # explicitly seeks origin, interpretation, or historical development.
        return score

    return 0.0


def _candidate_score(
    *,
    title: str,
    snippet: str,
    publication_date: str,
    url: str,
    query: str,
    task: SearchTask,
    source_role: str,
) -> float:
    clean_query = _SITE_RE.sub("", query)
    query_terms = set(_TOKEN_RE.findall(clean_query.lower()))
    haystack = f"{title} {snippet}".lower()
    overlap = sum(1 for term in query_terms if term in haystack)
    domain = _domain(url)
    page_type = _page_type(url)

    score = min(overlap, 10) * 0.75
    score += _role_suitability(source_role, str(task.get("search_role", "discovery")))
    score += _temporal_score(
        publication_date=publication_date,
        title=title,
        snippet=snippet,
        task=task,
    )
    score += 0.5 if url.startswith("https://") else 0.0
    if any(_domain_matches(domain, item) for item in task.get("preferred_domains", [])):
        score += 4.0
    if any(_domain_matches(domain, item) for item in task.get("excluded_domains", [])):
        score -= 20.0
    if _in_domain_set(domain, _LOW_VALUE_DOMAINS):
        score -= 6.0
    if source_role == "unknown" and task.get("search_role") in {"primary", "regulatory", "technical"}:
        score -= 4.0
    if source_role == "reference_only" and task.get("search_role") not in {"reference", "discovery"}:
        score -= 5.0
    if source_role == "database_estimate" and task.get("search_role") != "discovery":
        score -= 2.5
    if source_role == "social_or_forum":
        score -= 6.0
    if page_type == "listing":
        score -= 3.0
    return round(score, 3)

def _normalize_text(text: str) -> str:
    lines: list[str] = []
    seen: set[str] = set()
    for line in text.splitlines():
        clean = re.sub(r"\s+", " ", line).strip()
        key = clean.casefold()
        if len(clean) < 35 or key in seen:
            continue
        seen.add(key)
        lines.append(clean)
    return "\n".join(lines)


def _focus_terms(source: ResearchSource) -> set[str]:
    focus = " ".join(
        [
            str(source.get("question", "")),
            str(source.get("query", "")),
            str(source.get("claim_type", "")),
            *[str(item) for item in source.get("focus_questions", [])],
        ]
    )
    return set(_TOKEN_RE.findall(_SITE_RE.sub("", focus).lower()))


def _select_passages(
    chunks: list[tuple[int | None, str]],
    source: ResearchSource,
    max_chars: int,
) -> list[SourcePassage]:
    terms = _focus_terms(source)
    candidates: list[tuple[float, int, int | None, str]] = []

    for index, (page, raw) in enumerate(chunks):
        for paragraph in _normalize_text(raw).splitlines():
            pieces = [paragraph]
            if len(paragraph) > 1600:
                sentences = re.split(r"(?<=[.!?])\s+", paragraph)
                pieces = []
                current = ""
                for sentence in sentences:
                    candidate = f"{current} {sentence}".strip()
                    if current and len(candidate) > 1400:
                        pieces.append(current)
                        current = sentence
                    else:
                        current = candidate
                if current:
                    pieces.append(current)

            for piece in pieces:
                lower = piece.lower()
                overlap = sum(1 for term in terms if term in lower)
                score = overlap * 4.0
                if 80 <= len(piece) <= 1200:
                    score += 2.0
                if re.search(r"\b\d{4}\b|\$\s?\d|\b\d+(?:\.\d+)?%", piece):
                    score += 1.0

                phase = str(source.get("phase", "")).lower()
                time_mode = str(source.get("time_mode", "")).lower()
                years = [int(match.group(0)) for match in _YEAR_RE.finditer(piece)]
                if time_mode == "latest" or phase in {"verification", "gaps"}:
                    current_year = datetime.now(UTC).year
                    if current_year in years:
                        score += 2.0
                    elif current_year - 1 in years:
                        score += 1.0
                    elif years and max(years) <= current_year - 4:
                        score -= 1.25
                    if re.search(r"\b(as of|current|latest|updated|recent)\b", piece, re.IGNORECASE):
                        score += 0.75
                elif time_mode in {"foundational", "historical"} or phase == "orientation":
                    if re.search(r"\b(origin|history|historical|first|introduced|developed|foundation|interpretation)\b", piece, re.IGNORECASE):
                        score += 0.75

                candidates.append((score, -index, page, piece))

    selected: list[SourcePassage] = []
    used: set[str] = set()
    total = 0
    source_id = str(source.get("id", "S"))

    for _, _, page, paragraph in sorted(candidates, reverse=True):
        key = paragraph.casefold()
        if key in used:
            continue
        used.add(key)
        passage = SourcePassage(
            id=f"{source_id}-P{len(selected) + 1}",
            text=paragraph,
        )
        if page is not None:
            passage["page"] = page
        selected.append(passage)
        total += len(paragraph)
        if total >= max_chars or len(selected) >= 10:
            break

    return selected


def _apply_passages(source: ResearchSource, passages: list[SourcePassage]) -> bool:
    if not passages:
        return False
    source["passages"] = passages
    source["excerpt"] = "\n\n".join(
        f"[{item['id']}] {item['text']}" for item in passages
    )
    return True


class _ReadableHTMLParser(HTMLParser):
    SKIP = {"script", "style", "noscript", "nav", "footer", "header", "aside", "svg", "form"}
    BLOCK = {"p", "div", "section", "article", "main", "li", "h1", "h2", "h3", "h4", "blockquote", "br", "tr"}
    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.skip_depth = 0
        self.content_depth = 0
        self.body_depth = 0
        self.title_depth = 0
        self.frames: list[tuple[str, bool, bool, bool, bool]] = []
        self.main_chunks: list[str] = []
        self.body_chunks: list[str] = []
        self.title = ""
        self.canonical = ""
        self.metadata: dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        attrs_map = {str(k).lower(): str(v or "") for k, v in attrs}
        starts_skip = tag in self.SKIP
        starts_body = tag == "body"
        starts_content = tag in {"article", "main"} or attrs_map.get("role", "").lower() == "main"
        starts_title = tag == "title"

        if starts_skip:
            self.skip_depth += 1
        if starts_body:
            self.body_depth += 1
        if starts_content:
            self.content_depth += 1
        if starts_title:
            self.title_depth += 1

        if tag not in self.VOID:
            self.frames.append((tag, starts_skip, starts_body, starts_content, starts_title))

        if tag == "link" and "canonical" in attrs_map.get("rel", "").lower():
            self.canonical = attrs_map.get("href", "").strip()
        if tag == "meta":
            key = (
                attrs_map.get("property")
                or attrs_map.get("name")
                or attrs_map.get("itemprop")
            ).strip().lower()
            value = attrs_map.get("content", "").strip()
            if key and value and key not in self.metadata:
                self.metadata[key] = value
        if tag in self.BLOCK:
            self._append("\n")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag.lower() not in self.VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in self.BLOCK:
            self._append("\n")

        match_index = next(
            (index for index in range(len(self.frames) - 1, -1, -1) if self.frames[index][0] == tag),
            None,
        )
        if match_index is None:
            return

        closing = self.frames[match_index:]
        del self.frames[match_index:]
        for _, starts_skip, starts_body, starts_content, starts_title in reversed(closing):
            if starts_title and self.title_depth:
                self.title_depth -= 1
            if starts_content and self.content_depth:
                self.content_depth -= 1
            if starts_body and self.body_depth:
                self.body_depth -= 1
            if starts_skip and self.skip_depth:
                self.skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self.skip_depth:
            return
        value = data.strip()
        if not value:
            return
        if self.title_depth:
            self.title = f"{self.title} {value}".strip()
        self._append(value)

    def _append(self, value: str) -> None:
        if self.skip_depth:
            return
        if self.body_depth:
            self.body_chunks.append(value)
        if self.content_depth:
            self.main_chunks.append(value)

    def readable_text(self) -> str:
        main = " ".join(self.main_chunks)
        body = " ".join(self.body_chunks)
        return main if len(main) >= _MIN_READABLE_CHARS else body


def _metadata_from_json_ld(values: list[str]) -> dict[str, str]:
    output: dict[str, str] = {}

    def walk(value: Any) -> None:
        if isinstance(value, list):
            for item in value:
                walk(item)
            return
        if not isinstance(value, dict):
            return
        for key, item in value.items():
            lower = str(key).lower()
            if lower in {"datepublished", "datemodified"} and item and "publication_date" not in output:
                output["publication_date"] = str(item)
            elif lower == "author" and "author" not in output:
                if isinstance(item, dict):
                    output["author"] = str(item.get("name", ""))
                elif isinstance(item, list):
                    output["author"] = ", ".join(
                        str(entry.get("name", "")) if isinstance(entry, dict) else str(entry)
                        for entry in item
                    )
                else:
                    output["author"] = str(item)
            elif lower == "publisher" and "publisher" not in output:
                output["publisher"] = str(item.get("name", "")) if isinstance(item, dict) else str(item)
            walk(item)

    for raw in values[:12]:
        try:
            walk(json.loads(raw))
        except Exception:
            continue
    return output


def _is_blocked(title: str, text: str) -> bool:
    sample = f"{title} {text[:1200]}".lower()
    return any(marker in sample for marker in _BLOCKED_TEXT)


def _extract_pdf(source: ResearchSource, payload: bytes, excerpt_chars: int) -> bool:
    try:
        try:
            import pymupdf  # type: ignore
        except ImportError:
            import fitz as pymupdf  # type: ignore
    except ImportError:
        source["error"] = "PyMuPDF is not installed; PDF could not be extracted."
        source["fetch_status"] = "snippet_only"
        return False

    try:
        document = pymupdf.open(stream=payload, filetype="pdf")
        chunks: list[tuple[int | None, str]] = []
        for page_number, page in enumerate(document, start=1):
            if page_number > 100:
                break
            text = page.get_text("text", sort=True)
            if text and text.strip():
                chunks.append((page_number, text))
        metadata = document.metadata or {}
        document.close()

        passages = _select_passages(chunks, source, excerpt_chars)
        if not _apply_passages(source, passages):
            source["error"] = "PDF contained no readable text."
            source["fetch_status"] = "snippet_only"
            return False

        source["publication_date"] = str(
            source.get("publication_date") or metadata.get("creationDate") or ""
        )[:100]
        source["author"] = str(metadata.get("author") or "")[:200]
        source["publisher"] = str(metadata.get("producer") or "")[:200]
        source["retrieval_method"] = "pdf"
        source["fetch_status"] = "pdf_fetched"
        source["error"] = ""
        return True
    except Exception as exc:
        source["error"] = f"PDF extraction failed: {exc}"[:350]
        source["fetch_status"] = "snippet_only"
        return False


def _fetch_static(source: ResearchSource, excerpt_chars: int) -> str:
    try:
        validate_public_http_url(str(source.get("url", "")))
    except UnsafeUrlError as exc:
        source["error"] = f"Unsafe URL blocked: {exc}"[:350]
        return "terminal"

    request = Request(
        str(source.get("url", "")),
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/pdf;q=0.9,*/*;q=0.7",
        },
    )

    try:
        with urlopen(request, timeout=12) as response:
            status = int(response.getcode() or 0)
            final_url = str(response.geturl() or source.get("url", ""))
            try:
                validate_public_http_url(final_url)
            except UnsafeUrlError as exc:
                source["error"] = f"Unsafe redirect blocked: {exc}"[:350]
                return "terminal"
            content_type = str(response.headers.get_content_type() or "").lower()
            max_bytes = _MAX_PDF_BYTES if "pdf" in content_type else _MAX_HTML_BYTES
            payload = response.read(max_bytes + 1)

        source["http_status"] = status
        source["final_url"] = _canonical_url(final_url) or final_url
        source["domain"] = _domain(final_url) or str(source.get("domain", ""))
        source["content_type"] = content_type

        if len(payload) > max_bytes:
            source["error"] = "Response exceeded the research download limit."
            return "terminal"
        if status >= 400:
            source["error"] = f"HTTP status {status}."
            return "fallback"

        is_pdf = "pdf" in content_type or urlsplit(final_url).path.lower().endswith(".pdf")
        if is_pdf:
            _extract_pdf(source, payload, excerpt_chars)
            return "terminal"

        if not ("html" in content_type or "text" in content_type or not content_type):
            source["error"] = f"Unsupported content type: {content_type or 'unknown'}."
            return "terminal"

        html = payload.decode("utf-8", errors="replace")
        parser = _ReadableHTMLParser()
        parser.feed(html)
        text = parser.readable_text()
        if _is_blocked(parser.title, text) or len(text) < _MIN_READABLE_CHARS:
            source["error"] = "Static page was blocked, incomplete, or JavaScript-dependent."
            return "fallback"

        passages = _select_passages([(None, text)], source, excerpt_chars)
        if not _apply_passages(source, passages):
            source["error"] = "No relevant readable passages were extracted."
            return "fallback"

        source["title"] = (parser.title or str(source.get("title", "")))[:300]
        if parser.canonical:
            source["canonical_url"] = _canonical_url(urljoin(final_url, parser.canonical))
        metadata = parser.metadata
        source["publication_date"] = str(
            source.get("publication_date")
            or metadata.get("article:published_time")
            or metadata.get("datepublished")
            or metadata.get("date")
            or ""
        )[:100]
        source["author"] = str(metadata.get("author") or "")[:200]
        source["publisher"] = str(
            metadata.get("og:site_name") or metadata.get("publisher") or ""
        )[:200]
        source["retrieval_method"] = "http_html"
        source["fetch_status"] = "fetched"
        source["error"] = ""
        return "success"
    except Exception as exc:
        source["error"] = f"Static fetch failed: {exc}"[:350]
        return "fallback"


def _metadata_value(page: Any, selectors: list[str], attribute: str = "content") -> str:
    for selector in selectors:
        try:
            locator = page.locator(selector).first
            if locator.count():
                value = locator.get_attribute(attribute)
                if value:
                    return str(value).strip()
        except Exception:
            continue
    return ""


def _playwright_text(page: Any) -> str:
    selectors = ["article", "main", "[role='main']"]
    candidates: list[str] = []
    for selector in selectors:
        try:
            locator = page.locator(selector)
            for index in range(min(locator.count(), 3)):
                text = locator.nth(index).inner_text(timeout=2500)
                if text and len(text.strip()) >= _MIN_READABLE_CHARS:
                    candidates.append(text)
        except Exception:
            continue
    if candidates:
        return max(candidates, key=len)
    try:
        return page.locator("body").inner_text(timeout=4000)
    except Exception:
        return ""


def _fetch_with_playwright(context: Any, source: ResearchSource, excerpt_chars: int) -> None:
    page = context.new_page()
    try:
        validate_public_http_url(str(source.get("url", "")))
        response = page.goto(
            str(source.get("url", "")),
            wait_until="domcontentloaded",
            timeout=15_000,
        )
        validate_public_http_url(page.url)
        try:
            page.wait_for_load_state("networkidle", timeout=1800)
        except Exception:
            page.wait_for_timeout(450)

        source["final_url"] = _canonical_url(page.url) or page.url
        source["domain"] = _domain(page.url) or str(source.get("domain", ""))
        if response is not None:
            source["http_status"] = int(response.status)
            source["content_type"] = str(response.headers.get("content-type", "")).split(";", 1)[0]

        title = page.title().strip()
        text = _playwright_text(page)
        if _is_blocked(title, text) or len(text) < _MIN_READABLE_CHARS:
            raise RuntimeError("Page was blocked, paywalled, or contained insufficient readable content.")

        passages = _select_passages([(None, text)], source, excerpt_chars)
        if not _apply_passages(source, passages):
            raise RuntimeError("No relevant passages were extracted from the rendered page.")

        source["title"] = (title or str(source.get("title", "")))[:300]
        canonical = _metadata_value(page, ["link[rel='canonical']"], "href")
        if canonical:
            source["canonical_url"] = _canonical_url(urljoin(page.url, canonical))
        source["publication_date"] = str(
            source.get("publication_date")
            or _metadata_value(
                page,
                [
                    "meta[property='article:published_time']",
                    "meta[name='article:published_time']",
                    "meta[name='date']",
                    "meta[itemprop='datePublished']",
                ],
            )
            or _metadata_value(page, ["time[datetime]"], "datetime")
        )[:100]
        source["author"] = _metadata_value(
            page,
            ["meta[name='author']", "meta[property='article:author']"],
        )[:200]
        source["publisher"] = _metadata_value(
            page,
            ["meta[property='og:site_name']", "meta[name='publisher']"],
        )[:200]

        try:
            json_ld = page.locator("script[type='application/ld+json']").all_text_contents()
            metadata = _metadata_from_json_ld(json_ld)
            source["publication_date"] = str(source.get("publication_date") or metadata.get("publication_date") or "")[:100]
            source["author"] = str(source.get("author") or metadata.get("author") or "")[:200]
            source["publisher"] = str(source.get("publisher") or metadata.get("publisher") or "")[:200]
        except Exception:
            pass

        source["retrieval_method"] = "playwright"
        source["fetch_status"] = "fetched"
        source["error"] = ""
    except Exception as exc:
        source["fetch_status"] = "snippet_only"
        source["retrieval_method"] = "search_snippet"
        source["passages"] = []
        source["excerpt"] = str(source.get("snippet", ""))
        source["error"] = str(exc)[:350]
    finally:
        page.close()


def _finalize_quality(source: ResearchSource) -> None:
    fetch_status = str(source.get("fetch_status", "snippet_only"))
    final_task = SearchTask(
        search_role=str(source.get("search_role", "discovery")),
        preferred_domains=list(source.get("preferred_domains", [])),
        excluded_domains=list(source.get("excluded_domains", [])),
        phase=str(source.get("phase", "")),
        time_mode=str(source.get("time_mode", "")),
    )
    final_url = str(source.get("canonical_url") or source.get("final_url") or source.get("url") or "")
    source_role = _classify_source(final_url, final_task)
    final_domain = _domain(final_url)
    page_type = _page_type(final_url)
    excluded = list(source.get("excluded_domains", []))
    excluded_redirect = any(_domain_matches(final_domain, item) for item in excluded)

    source["domain"] = final_domain
    source["page_type"] = page_type
    source["source_role"] = "unknown" if excluded_redirect else source_role
    source_role = str(source["source_role"])
    source["quality_tier"] = _quality_tier(source_role, fetch_status)

    score = float(source.get("rank_score", 0.0))
    if excluded_redirect:
        score -= 20.0
        source["error"] = "Final URL redirected to an excluded domain."
    if fetch_status in {"fetched", "pdf_fetched"}:
        score += 2.0
    else:
        score -= 3.0
    if page_type == "listing":
        score -= 3.0
        if source["quality_tier"] == "high":
            source["quality_tier"] = "medium"
    if page_type == "forum":
        source["quality_tier"] = "low"
        score -= 6.0
    source["quality_score"] = round(score, 3)

class ResearchSourceClient:
    def __init__(self, serper_api_key: str) -> None:
        if not serper_api_key:
            raise RuntimeError("SERPER_API_KEY is not configured.")
        self._serper = GoogleSerperAPIWrapper(serper_api_key=serper_api_key)

    async def search_and_fetch(
        self,
        *,
        tasks: list[SearchTask],
        existing_urls: set[str],
        start_index: int,
        candidates_per_task: int,
        fetch_limit: int,
        backfill_limit: int,
        excerpt_chars: int,
    ) -> tuple[list[ResearchSource], list[str]]:
        expanded = self._expand_tasks(tasks)
        raw_batches = await asyncio.gather(
            *(asyncio.to_thread(self._search_one, query) for _, query in expanded)
        )

        candidate_by_url: dict[str, ResearchSource] = {}
        existing = set(existing_urls)
        executed_queries: list[str] = []

        for (task, effective_query), raw_results in zip(expanded, raw_batches):
            executed_queries.append(effective_query)
            for item in raw_results[:candidates_per_task]:
                url = _canonical_url(str(item.get("link", "")).strip())
                if not url or not _is_safe_public_url(url) or url in existing:
                    continue
                title = str(item.get("title", "")).strip()
                if not title:
                    continue
                snippet = str(item.get("snippet", "")).strip()
                source_role = _classify_source(url, task)
                publication_date = str(item.get("date", "")).strip()
                score = _candidate_score(
                    title=title,
                    snippet=snippet,
                    publication_date=publication_date,
                    url=url,
                    query=effective_query,
                    task=task,
                    source_role=source_role,
                )
                if score < 1.5:
                    continue

                question_id = str(task.get("question_id", ""))
                question = str(task.get("question", ""))
                previous = candidate_by_url.get(url)
                related = list(previous.get("related_question_ids", [])) if previous else []
                focuses = list(previous.get("focus_questions", [])) if previous else []
                if question_id and question_id not in related:
                    related.append(question_id)
                if question and question not in focuses:
                    focuses.append(question)

                if previous is not None and score <= float(previous.get("rank_score", 0.0)):
                    previous["related_question_ids"] = related
                    previous["focus_questions"] = focuses
                    continue

                candidate_by_url[url] = ResearchSource(
                    id="",
                    task_id=str(task.get("id", "")),
                    question_id=question_id,
                    question=question,
                    related_question_ids=related or ([question_id] if question_id else []),
                    focus_questions=focuses or ([question] if question else []),
                    priority=(
                        "must"
                        if str(task.get("priority", "should")) == "must"
                        or (previous is not None and str(previous.get("priority", "should")) == "must")
                        else "should"
                    ),
                    claim_type=str(task.get("claim_type", "other")),
                    search_role=str(task.get("search_role", "discovery")),
                    query=effective_query,
                    preferred_domains=list(task.get("preferred_domains", [])),
                    excluded_domains=list(task.get("excluded_domains", [])),
                    phase=str(task.get("phase", "")),
                    time_mode=str(task.get("time_mode", "")),
                    title=title,
                    url=url,
                    final_url=url,
                    canonical_url="",
                    domain=_domain(url),
                    snippet=snippet,
                    excerpt=snippet,
                    passages=[],
                    publication_date=publication_date,
                    author="",
                    publisher="",
                    page_type=_page_type(url),
                    source_role=source_role,
                    quality_tier=_quality_tier(source_role),
                    quality_score=score,
                    rank_score=score,
                    retrieval_method="pending",
                    fetch_status="pending",
                    http_status=0,
                    content_type="",
                    error="",
                )

        candidates = list(candidate_by_url.values())
        initial = self._select_candidates(candidates, fetch_limit)
        initial_urls = {str(source.get("url", "")) for source in initial}
        remaining = [source for source in candidates if str(source.get("url", "")) not in initial_urls]

        self._assign_ids(initial, start_index)
        if initial:
            await asyncio.to_thread(self._fetch_many_sync, initial, excerpt_chars)

        backfill = self._select_backfill(
            tasks=tasks,
            attempted=initial,
            remaining=remaining,
            limit=backfill_limit,
        )
        self._assign_ids(backfill, start_index + len(initial))
        if backfill:
            await asyncio.to_thread(self._fetch_many_sync, backfill, excerpt_chars)

        attempted = [*initial, *backfill]
        self._reindex_sources(attempted, start_index)
        return attempted, list(dict.fromkeys(executed_queries))

    def _search_one(self, query: str) -> list[dict[str, Any]]:
        try:
            response = self._serper.results(query)
            organic = response.get("organic", [])
            return organic if isinstance(organic, list) else []
        except Exception:
            return []

    @staticmethod
    def _expand_tasks(tasks: list[SearchTask]) -> list[tuple[SearchTask, str]]:
        expanded: list[tuple[SearchTask, str]] = []
        seen: set[str] = set()
        for task in tasks:
            base_query = str(task.get("query", "")).strip()
            if not base_query:
                continue
            role = str(task.get("search_role", "discovery"))
            domains = [
                domain
                for item in task.get("preferred_domains", [])
                if (domain := _normalize_domain(str(item)))
            ]
            if "site:" in base_query.lower() or not domains or role == "discovery":
                variants = [base_query]
            elif role == "independent":
                variants = [*[f"site:{domain} {base_query}" for domain in domains[:2]], base_query]
            else:
                variants = [f"site:{domain} {base_query}" for domain in domains[:3]]

            for query in variants:
                key = query.casefold()
                if key in seen:
                    continue
                seen.add(key)
                expanded.append((task, query))
        return expanded

    @staticmethod
    def _select_candidates(candidates: list[ResearchSource], limit: int) -> list[ResearchSource]:
        if not candidates or limit <= 0:
            return []

        selected: list[ResearchSource] = []
        selected_urls: set[str] = set()
        domain_counts: dict[str, int] = defaultdict(int)

        def domain_cap(source: ResearchSource) -> int:
            role = str(source.get("source_role", "unknown"))
            if role.startswith("primary_"):
                return 3
            if role == "academic_primary":
                return 3
            if role in {"independent_tier_1", "independent_specialist"}:
                return 2
            return 1

        def add(source: ResearchSource) -> bool:
            url = str(source.get("url", ""))
            domain = str(source.get("domain", ""))
            if not url or url in selected_urls or domain_counts[domain] >= domain_cap(source):
                return False
            selected.append(source)
            selected_urls.add(url)
            domain_counts[domain] += 1
            return True

        grouped: dict[str, list[ResearchSource]] = defaultdict(list)
        for source in candidates:
            for question_id in list(source.get("related_question_ids", [])) or [str(source.get("question_id", ""))]:
                if question_id:
                    grouped[question_id].append(source)

        for question_id in sorted(
            grouped,
            key=lambda qid: (0 if any(item.get("priority") == "must" for item in grouped[qid]) else 1, qid),
        ):
            group = sorted(grouped[question_id], key=lambda item: float(item.get("rank_score", 0.0)), reverse=True)
            for desired in (
                {"primary_official", "primary_regulatory", "primary_technical", "academic_primary"},
                {"independent_tier_1", "independent_specialist", "academic_primary"},
            ):
                match = next(
                    (
                        item for item in group
                        if item.get("source_role") in desired
                        and item.get("page_type") not in {"forum", "listing"}
                        and float(item.get("rank_score", 0.0)) >= 2.5
                    ),
                    None,
                )
                if match:
                    add(match)
                if len(selected) >= limit:
                    return selected

        for source in sorted(candidates, key=lambda item: float(item.get("rank_score", 0.0)), reverse=True):
            if float(source.get("rank_score", 0.0)) < 2.5:
                continue
            if source.get("page_type") == "forum":
                continue
            if source.get("page_type") == "listing" and source.get("search_role") not in {"discovery", "reference"}:
                continue
            add(source)
            if len(selected) >= limit:
                break
        return selected

    @staticmethod
    def _task_satisfied(task: SearchTask, sources: list[ResearchSource]) -> bool:
        question_id = str(task.get("question_id", ""))
        search_role = str(task.get("search_role", "discovery"))
        for source in sources:
            related = list(source.get("related_question_ids", [])) or [str(source.get("question_id", ""))]
            if question_id not in related:
                continue
            if source.get("fetch_status") not in {"fetched", "pdf_fetched"} or not source.get("passages"):
                continue
            if source.get("quality_tier") == "low":
                continue
            if _role_suitability(str(source.get("source_role", "unknown")), search_role) >= 3.0:
                return True
        return False

    @classmethod
    def _select_backfill(
        cls,
        *,
        tasks: list[SearchTask],
        attempted: list[ResearchSource],
        remaining: list[ResearchSource],
        limit: int,
    ) -> list[ResearchSource]:
        if limit <= 0:
            return []
        chosen: list[ResearchSource] = []
        chosen_urls: set[str] = set()
        successful_domains = {
            str(source.get("domain", ""))
            for source in attempted
            if source.get("fetch_status") in {"fetched", "pdf_fetched"}
        }
        failed_domain_counts: dict[str, int] = defaultdict(int)
        for source in attempted:
            if source.get("fetch_status") not in {"fetched", "pdf_fetched"}:
                failed_domain_counts[str(source.get("domain", ""))] += 1

        must_tasks = [task for task in tasks if str(task.get("priority", "must")) == "must"]
        for task in must_tasks:
            if cls._task_satisfied(task, [*attempted, *chosen]):
                continue
            question_id = str(task.get("question_id", ""))
            role = str(task.get("search_role", "discovery"))
            candidates = [
                source for source in remaining
                if str(source.get("url", "")) not in chosen_urls
                and question_id in (list(source.get("related_question_ids", [])) or [str(source.get("question_id", ""))])
                and _role_suitability(str(source.get("source_role", "unknown")), role) >= 2.0
                and source.get("page_type") not in {"forum", "listing"}
            ]
            # Prefer a new domain and avoid repeatedly selecting a domain that already
            # failed twice in the same pass when alternatives exist.
            candidates.sort(
                key=lambda source: (
                    failed_domain_counts.get(str(source.get("domain", "")), 0) < 2,
                    str(source.get("domain", "")) not in successful_domains,
                    -failed_domain_counts.get(str(source.get("domain", "")), 0),
                    float(source.get("rank_score", 0.0)),
                ),
                reverse=True,
            )
            if candidates:
                selected = candidates[0]
                chosen.append(selected)
                chosen_urls.add(str(selected.get("url", "")))
                successful_domains.add(str(selected.get("domain", "")))
            if len(chosen) >= limit:
                break
        return chosen

    @staticmethod
    def _assign_ids(sources: list[ResearchSource], start_index: int) -> None:
        for offset, source in enumerate(sources, start=start_index):
            source["id"] = f"S{offset}"

    @staticmethod
    def _reindex_sources(sources: list[ResearchSource], start_index: int) -> None:
        for offset, source in enumerate(sources, start=start_index):
            source_id = f"S{offset}"
            source["id"] = source_id
            for passage_index, passage in enumerate(source.get("passages", []), start=1):
                passage["id"] = f"{source_id}-P{passage_index}"

    @staticmethod
    def _fetch_many_sync(sources: list[ResearchSource], excerpt_chars: int) -> None:
        playwright_needed: list[ResearchSource] = []
        for source in sources:
            outcome = _fetch_static(source, excerpt_chars)
            if outcome == "fallback":
                playwright_needed.append(source)

        if playwright_needed:
            try:
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch(
                        headless=True,
                        args=["--disable-dev-shm-usage"],
                    )
                    context = browser.new_context(
                        user_agent=(
                            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"
                        ),
                        locale="en-US",
                        ignore_https_errors=True,
                    )
                    try:
                        for source in playwright_needed:
                            _fetch_with_playwright(context, source, excerpt_chars)
                    finally:
                        context.close()
                        browser.close()
            except Exception as exc:
                for source in playwright_needed:
                    source["fetch_status"] = "snippet_only"
                    source["retrieval_method"] = "search_snippet"
                    source["passages"] = []
                    source["excerpt"] = str(source.get("snippet", ""))
                    source["error"] = f"Playwright unavailable: {exc}"[:350]

        for source in sources:
            if source.get("fetch_status") == "pending":
                source["fetch_status"] = "snippet_only"
                source["retrieval_method"] = "search_snippet"
                source["passages"] = []
                source["excerpt"] = str(source.get("snippet", ""))
            _finalize_quality(source)
