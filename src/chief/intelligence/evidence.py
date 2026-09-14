from __future__ import annotations

import html
import json
import re
from collections.abc import Callable
from html.parser import HTMLParser
from typing import Protocol
from urllib import error, request
from urllib.parse import urlencode, urljoin

from pydantic import BaseModel, ConfigDict, Field

from chief.browser.research import (
    BrowserLink,
    BrowserPageEvidence,
    BrowserResearchService,
    BrowserUrlPolicy,
    PolicyHttpFetcher,
)

_BRAVE_WEB_SEARCH_URL = "https://api.search.brave.com/res/v1/web/search"
_ALLOWED_FRESHNESS = frozenset({"pd", "pw", "pm", "py"})
_TAGS = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"\s+")


class SearchUnavailable(RuntimeError):
    """Search discovery is not currently configured."""


class SearchResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    title: str = Field(max_length=1_000)
    url: str = Field(max_length=4_000)
    description: str = Field(default="", max_length=4_000)
    provider: str
    trust: str = "untrusted_external"


class EvidencePage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_id: str
    url: str
    title: str
    text: str
    truncated: bool
    trust: str = "untrusted_external"


class EvidenceFailure(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    url: str
    error: str = Field(max_length=2_000)


class ReconEvidenceBundle(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    query: str = ""
    search_provider: str | None = None
    search_available: bool = False
    search_results: list[SearchResult] = Field(default_factory=list)
    pages: list[EvidencePage] = Field(default_factory=list)
    failures: list[EvidenceFailure] = Field(default_factory=list)


class SearchProvider(Protocol):
    @property
    def name(self) -> str: ...

    def available(self) -> bool: ...

    def search(
        self,
        query: str,
        *,
        count: int,
        freshness: str | None = None,
    ) -> list[SearchResult]: ...


def _clean_text(value: str, *, limit: int) -> str:
    cleaned = html.unescape(_TAGS.sub(" ", value or ""))
    return _SPACE.sub(" ", cleaned).strip()[:limit]


class BraveSearchProvider:
    """Bounded, read-only Brave Web Search adapter."""

    def __init__(
        self,
        token_provider: Callable[[], str | None],
        *,
        timeout_seconds: float = 15,
        max_response_bytes: int = 2_000_000,
        policy: BrowserUrlPolicy | None = None,
    ) -> None:
        if not 1 <= timeout_seconds <= 120:
            raise ValueError("Search timeout must be between 1 and 120 seconds.")
        if not 10_000 <= max_response_bytes <= 10_000_000:
            raise ValueError("Search response limit is outside the supported range.")
        self.token_provider = token_provider
        self.timeout_seconds = timeout_seconds
        self.max_response_bytes = max_response_bytes
        self.policy = policy or BrowserUrlPolicy()

    @property
    def name(self) -> str:
        return "brave"

    def available(self) -> bool:
        return bool(self.token_provider())

    @staticmethod
    def _validate_query(query: str) -> str:
        value = query.strip()
        if not value:
            raise ValueError("Search query cannot be blank.")
        if len(value) > 600 or len(value.split()) > 75:
            raise ValueError("Search query exceeds Brave's 600-character or 75-word limit.")
        return value

    def search(
        self,
        query: str,
        *,
        count: int,
        freshness: str | None = None,
    ) -> list[SearchResult]:
        query = self._validate_query(query)
        if not 1 <= count <= 20:
            raise ValueError("Search result count must be between 1 and 20.")
        if freshness is not None and freshness not in _ALLOWED_FRESHNESS:
            raise ValueError("Freshness must be one of pd, pw, pm, or py.")
        token = self.token_provider()
        if not token:
            raise SearchUnavailable(
                "Web search is not configured. Add CHIEF_BRAVE_SEARCH_API_KEY to CHIEF's secret vault."
            )

        params: dict[str, str | int] = {
            "q": query,
            "count": count,
            "safesearch": "moderate",
            "extra_snippets": "true",
        }
        if freshness is not None:
            params["freshness"] = freshness
        http_request = request.Request(
            f"{_BRAVE_WEB_SEARCH_URL}?{urlencode(params)}",
            headers={
                "Accept": "application/json",
                "Accept-Encoding": "identity",
                "User-Agent": "CHIEF-RECON/1.0",
                "X-Subscription-Token": token,
            },
            method="GET",
        )
        try:
            with request.urlopen(http_request, timeout=self.timeout_seconds) as response:
                raw = response.read(self.max_response_bytes + 1)
                if len(raw) > self.max_response_bytes:
                    raise RuntimeError("Search response exceeded CHIEF's configured size limit.")
        except error.HTTPError as exc:
            raise RuntimeError(f"Brave Search returned HTTP {exc.code}.") from exc
        except error.URLError as exc:
            raise RuntimeError("Brave Search could not be reached.") from exc

        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("Brave Search returned an invalid JSON response.") from exc
        rows = payload.get("web", {}).get("results", []) if isinstance(payload, dict) else []
        results: list[SearchResult] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            url = row.get("url")
            if not isinstance(url, str):
                continue
            try:
                safe_url = self.policy.validate(url)
            except (PermissionError, RuntimeError, ValueError):
                continue
            title = _clean_text(str(row.get("title") or ""), limit=1_000)
            description = _clean_text(str(row.get("description") or ""), limit=4_000)
            results.append(
                SearchResult(
                    title=title,
                    url=safe_url,
                    description=description,
                    provider=self.name,
                )
            )
            if len(results) >= count:
                break
        return results


class _StaticHtmlParser(HTMLParser):
    def __init__(self, *, base_url: str, max_links: int) -> None:
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.max_links = max_links
        self.in_title = False
        self.skip_depth = 0
        self.title_parts: list[str] = []
        self.text_parts: list[str] = []
        self.links: list[BrowserLink] = []
        self._active_href: str | None = None
        self._active_label: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        normalized = tag.casefold()
        if normalized in {"script", "style", "noscript", "svg", "template"}:
            self.skip_depth += 1
            return
        if self.skip_depth:
            return
        if normalized == "title":
            self.in_title = True
        if normalized == "a" and len(self.links) < self.max_links:
            href = next((value for key, value in attrs if key.casefold() == "href"), None)
            if href:
                self._active_href = urljoin(self.base_url, href)
                self._active_label = []

    def handle_endtag(self, tag: str) -> None:
        normalized = tag.casefold()
        if normalized in {"script", "style", "noscript", "svg", "template"} and self.skip_depth:
            self.skip_depth -= 1
            return
        if self.skip_depth:
            return
        if normalized == "title":
            self.in_title = False
        if normalized == "a" and self._active_href is not None:
            label = _SPACE.sub(" ", " ".join(self._active_label)).strip()[:500]
            self.links.append(BrowserLink(text=label, url=self._active_href))
            self._active_href = None
            self._active_label = []

    def handle_data(self, data: str) -> None:
        if self.skip_depth:
            return
        if self.in_title:
            self.title_parts.append(data)
        self.text_parts.append(data)
        if self._active_href is not None:
            self._active_label.append(data)


class StaticHtmlReadOnlyDriver:
    """Dependency-free HTML evidence reader using CHIEF's existing SSRF policy."""

    def __init__(
        self,
        *,
        policy: BrowserUrlPolicy | None = None,
        fetcher: PolicyHttpFetcher | None = None,
    ) -> None:
        self.policy = policy or BrowserUrlPolicy()
        self.fetcher = fetcher or PolicyHttpFetcher(self.policy)

    def read(
        self,
        url: str,
        *,
        timeout_ms: int,
        max_chars: int,
        max_links: int,
    ) -> BrowserPageEvidence:
        document = self.fetcher.fetch(
            url,
            timeout_ms=timeout_ms,
            max_bytes=max(500_000, min(max_chars * 8, 4_000_000)),
        )
        parser = _StaticHtmlParser(base_url=document.final_url, max_links=max_links)
        parser.feed(document.html)
        body = _SPACE.sub(" ", " ".join(parser.text_parts)).strip()
        title = _SPACE.sub(" ", " ".join(parser.title_parts)).strip()[:1_000]
        links: list[BrowserLink] = []
        for link in parser.links:
            try:
                self.policy.validate(link.url)
            except (PermissionError, RuntimeError, ValueError):
                continue
            links.append(link)
            if len(links) >= max_links:
                break
        return BrowserPageEvidence(
            url=url,
            final_url=document.final_url,
            title=title,
            text=body[:max_chars],
            links=tuple(links),
            truncated=len(body) > max_chars,
        )


class ReconEvidenceService:
    """Read-only discovery plus bounded page collection for RECON."""

    def __init__(
        self,
        *,
        browser_service: BrowserResearchService,
        search_provider: SearchProvider | None = None,
        max_pages: int = 8,
    ) -> None:
        if not 1 <= max_pages <= 20:
            raise ValueError("RECON evidence page limit must be between 1 and 20.")
        self.browser_service = browser_service
        self.search_provider = search_provider
        self.max_pages = max_pages

    @property
    def search_available(self) -> bool:
        return self.search_provider is not None and self.search_provider.available()

    @property
    def search_provider_name(self) -> str | None:
        return self.search_provider.name if self.search_provider is not None else None

    def gather(
        self,
        *,
        query: str = "",
        seed_urls: list[str] | None = None,
        max_results: int = 5,
        freshness: str | None = None,
    ) -> ReconEvidenceBundle:
        query = query.strip()
        seeds = list(dict.fromkeys(url.strip() for url in (seed_urls or []) if url.strip()))
        if not query and not seeds:
            raise ValueError("RECON evidence requires a search query or at least one seed URL.")
        if not 1 <= max_results <= 10:
            raise ValueError("RECON max_results must be between 1 and 10.")
        if len(seeds) > self.max_pages:
            raise ValueError(f"RECON seed URL count cannot exceed {self.max_pages}.")

        results: list[SearchResult] = []
        if query:
            if self.search_provider is None or not self.search_provider.available():
                if not seeds:
                    raise SearchUnavailable(
                        "Search discovery is not configured and no seed URLs were supplied."
                    )
            else:
                results = self.search_provider.search(
                    query,
                    count=max_results,
                    freshness=freshness,
                )

        urls = seeds + [result.url for result in results]
        deduped = list(dict.fromkeys(urls))[: self.max_pages]
        pages: list[EvidencePage] = []
        failures: list[EvidenceFailure] = []
        for url in deduped:
            try:
                page = self.browser_service.read_pages([url])[0]
            except (PermissionError, RuntimeError, ValueError) as exc:
                failures.append(EvidenceFailure(url=url, error=str(exc)))
                continue
            pages.append(
                EvidencePage(
                    source_id=f"S{len(pages) + 1}",
                    url=page.final_url,
                    title=page.title,
                    text=page.text,
                    truncated=page.truncated,
                    trust=page.trust,
                )
            )
        return ReconEvidenceBundle(
            query=query,
            search_provider=self.search_provider_name if query else None,
            search_available=self.search_available,
            search_results=results,
            pages=pages,
            failures=failures,
        )


def build_recon_evidence_service(
    token_provider: Callable[[], str | None],
) -> ReconEvidenceService:
    policy = BrowserUrlPolicy()
    browser = BrowserResearchService(
        StaticHtmlReadOnlyDriver(policy=policy),
        policy=policy,
        timeout_ms=20_000,
        max_chars=20_000,
        max_links=50,
        max_pages=8,
    )
    return ReconEvidenceService(
        browser_service=browser,
        search_provider=BraveSearchProvider(token_provider, policy=policy),
        max_pages=8,
    )
