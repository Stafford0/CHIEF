from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urljoin, urlparse


@dataclass(frozen=True, slots=True)
class BrowserLink:
    text: str
    url: str


@dataclass(frozen=True, slots=True)
class BrowserPageEvidence:
    url: str
    final_url: str
    title: str
    text: str
    links: tuple[BrowserLink, ...]
    truncated: bool
    trust: str = "untrusted_external"


class BrowserDriver(Protocol):
    def read(self, url: str, *, timeout_ms: int, max_chars: int, max_links: int) -> BrowserPageEvidence: ...


Resolver = Callable[[str], Iterable[str]]


def _default_resolver(host: str) -> Iterable[str]:
    return {item[4][0] for item in socket.getaddrinfo(host, None)}


class BrowserUrlPolicy:
    """Reject non-web URLs and hosts that resolve into private/local address space."""

    def __init__(self, *, resolver: Resolver = _default_resolver) -> None:
        self.resolver = resolver

    @staticmethod
    def _unsafe_address(value: str) -> bool:
        address = ipaddress.ip_address(value)
        return any(
            (
                address.is_private,
                address.is_loopback,
                address.is_link_local,
                address.is_multicast,
                address.is_reserved,
                address.is_unspecified,
            )
        )

    def validate(self, url: str) -> str:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            raise PermissionError("Browser research permits only http and https URLs.")
        if parsed.username is not None or parsed.password is not None:
            raise PermissionError("URLs containing credentials are not permitted.")
        host = (parsed.hostname or "").strip().casefold()
        if not host:
            raise ValueError("Browser URL must include a hostname.")
        if host == "localhost" or host.endswith((".localhost", ".local")):
            raise PermissionError("Local hostnames are not permitted for browser research.")
        try:
            if self._unsafe_address(host):
                raise PermissionError("Private or local IP addresses are not permitted.")
            return url
        except ValueError:
            pass
        try:
            addresses = tuple(self.resolver(host))
        except OSError as exc:
            raise RuntimeError(f"Could not resolve browser target host '{host}'.") from exc
        if not addresses:
            raise RuntimeError(f"Browser target host '{host}' resolved to no addresses.")
        for address in addresses:
            try:
                if self._unsafe_address(address):
                    raise PermissionError(
                        f"Browser target '{host}' resolves to private/local address {address}."
                    )
            except ValueError as exc:
                raise RuntimeError(f"Resolver returned invalid address '{address}'.") from exc
        return url


class PlaywrightReadOnlyDriver:
    """Ephemeral Chromium reader with policy enforcement on every network request."""

    def __init__(
        self,
        *,
        headless: bool = True,
        policy: BrowserUrlPolicy | None = None,
    ) -> None:
        self.headless = headless
        self.policy = policy or BrowserUrlPolicy()

    def read(
        self,
        url: str,
        *,
        timeout_ms: int,
        max_chars: int,
        max_links: int,
    ) -> BrowserPageEvidence:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise RuntimeError(
                "Browser research requires the optional 'browser' dependency and Chromium install."
            ) from exc

        self.policy.validate(url)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=self.headless)
            context = browser.new_context(
                accept_downloads=False,
                service_workers="block",
                java_script_enabled=True,
            )
            page = context.new_page()

            def guard(route) -> None:
                try:
                    self.policy.validate(route.request.url)
                except (PermissionError, RuntimeError, ValueError):
                    route.abort("blockedbyclient")
                    return
                route.continue_()

            context.route("**/*", guard)
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            self.policy.validate(page.url)
            title = page.title()
            body = page.locator("body").inner_text(timeout=timeout_ms)
            truncated = len(body) > max_chars
            text = body[:max_chars]
            links: list[BrowserLink] = []
            for locator in page.locator("a").all()[:max_links]:
                href = locator.get_attribute("href")
                if not href:
                    continue
                resolved = urljoin(page.url, href)
                try:
                    self.policy.validate(resolved)
                except (PermissionError, RuntimeError, ValueError):
                    continue
                label = locator.inner_text(timeout=timeout_ms).strip()[:500]
                links.append(BrowserLink(text=label, url=resolved))
            final_url = page.url
            context.close()
            browser.close()
        return BrowserPageEvidence(
            url=url,
            final_url=final_url,
            title=title[:1000],
            text=text,
            links=tuple(links),
            truncated=truncated,
        )


class BrowserResearchService:
    """Policy-enforced, read-only page evidence collection."""

    def __init__(
        self,
        driver: BrowserDriver,
        *,
        policy: BrowserUrlPolicy | None = None,
        timeout_ms: int = 20_000,
        max_chars: int = 100_000,
        max_links: int = 200,
        max_pages: int = 10,
    ) -> None:
        if not 1_000 <= timeout_ms <= 120_000:
            raise ValueError("Browser timeout must be between 1,000 and 120,000 ms.")
        if not 1_000 <= max_chars <= 2_000_000:
            raise ValueError("Browser text limit must be between 1,000 and 2,000,000 characters.")
        if not 0 <= max_links <= 5_000:
            raise ValueError("Browser link limit must be between 0 and 5,000.")
        if not 1 <= max_pages <= 100:
            raise ValueError("Browser page limit must be between 1 and 100.")
        self.driver = driver
        self.policy = policy or BrowserUrlPolicy()
        self.timeout_ms = timeout_ms
        self.max_chars = max_chars
        self.max_links = max_links
        self.max_pages = max_pages

    def read_pages(self, urls: list[str]) -> list[BrowserPageEvidence]:
        if not urls:
            raise ValueError("At least one browser URL is required.")
        if len(urls) > self.max_pages:
            raise ValueError(f"Browser request exceeds the {self.max_pages}-page limit.")
        evidence: list[BrowserPageEvidence] = []
        for raw_url in urls:
            url = self.policy.validate(raw_url)
            page = self.driver.read(
                url,
                timeout_ms=self.timeout_ms,
                max_chars=self.max_chars,
                max_links=self.max_links,
            )
            self.policy.validate(page.final_url)
            evidence.append(page)
        return evidence
