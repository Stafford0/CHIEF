from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from chief.browser.research import BrowserDocumentFetcher, BrowserUrlPolicy, PolicyHttpFetcher


@dataclass(frozen=True, slots=True)
class RawScreenshot:
    final_url: str
    title: str
    png_bytes: bytes
    width: int
    height: int


class ScreenshotDriver(Protocol):
    def capture(self, url: str, *, timeout_ms: int, width: int, height: int) -> RawScreenshot: ...


class ScreenshotReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    url: str
    final_url: str
    title: str
    content_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    byte_count: int = Field(ge=1)
    width: int = Field(ge=1)
    height: int = Field(ge=1)
    captured_at: datetime
    expires_at: datetime
    trust: str = "untrusted_external"
    persisted: bool = False


class ScreenshotEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    receipt: ScreenshotReceipt
    png_base64: str


class PlaywrightScreenshotDriver:
    """Capture one viewport in an ephemeral browser without writing it to disk."""

    def __init__(
        self,
        *,
        policy: BrowserUrlPolicy | None = None,
        headless: bool = True,
        fetcher: BrowserDocumentFetcher | None = None,
    ) -> None:
        self.policy = policy or BrowserUrlPolicy()
        self.headless = headless
        self.fetcher = fetcher or PolicyHttpFetcher(self.policy)

    def capture(self, url: str, *, timeout_ms: int, width: int, height: int) -> RawScreenshot:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise RuntimeError(
                "Screenshot capture requires the optional 'browser' dependency and Chromium install."
            ) from exc

        document = self.fetcher.fetch(url, timeout_ms=timeout_ms, max_bytes=8_000_000)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=self.headless)
            context = browser.new_context(
                accept_downloads=False,
                service_workers="block",
                java_script_enabled=False,
                viewport={"width": width, "height": height},
            )
            context.route("**/*", lambda route: route.abort("blockedbyclient"))
            page = context.new_page()
            page.set_content(document.html, wait_until="domcontentloaded", timeout=timeout_ms)
            title = page.title()[:1_000]
            png_bytes = page.screenshot(type="png", full_page=False, animations="disabled")
            context.close()
            browser.close()
        return RawScreenshot(
            final_url=document.final_url,
            title=title,
            png_bytes=png_bytes,
            width=width,
            height=height,
        )


class ScreenshotCaptureService:
    """Bound and label screenshot bytes as short-lived untrusted evidence."""

    def __init__(
        self,
        driver: ScreenshotDriver,
        *,
        policy: BrowserUrlPolicy | None = None,
        timeout_ms: int = 20_000,
        width: int = 1_440,
        height: int = 900,
        max_bytes: int = 2_000_000,
        ttl_seconds: int = 300,
    ) -> None:
        if not 1_000 <= timeout_ms <= 120_000:
            raise ValueError("Screenshot timeout must be between 1,000 and 120,000 ms")
        if not 320 <= width <= 3_840 or not 240 <= height <= 2_160:
            raise ValueError("Screenshot viewport is outside the supported bounds")
        if not 100_000 <= max_bytes <= 10_000_000:
            raise ValueError("Screenshot byte limit must be between 100 KB and 10 MB")
        if not 30 <= ttl_seconds <= 3_600:
            raise ValueError("Screenshot expiry must be between 30 and 3,600 seconds")
        self.driver = driver
        self.policy = policy or BrowserUrlPolicy()
        self.timeout_ms = timeout_ms
        self.width = width
        self.height = height
        self.max_bytes = max_bytes
        self.ttl = timedelta(seconds=ttl_seconds)

    def capture(self, url: str, *, now: datetime | None = None) -> ScreenshotEvidence:
        safe_url = self.policy.validate(url)
        raw = self.driver.capture(
            safe_url,
            timeout_ms=self.timeout_ms,
            width=self.width,
            height=self.height,
        )
        self.policy.validate(raw.final_url)
        if not raw.png_bytes:
            raise RuntimeError("Screenshot driver returned empty image data")
        if len(raw.png_bytes) > self.max_bytes:
            raise RuntimeError(
                f"Screenshot exceeds the {self.max_bytes}-byte in-memory evidence limit"
            )
        captured_at = (now or datetime.now(UTC)).astimezone(UTC)
        receipt = ScreenshotReceipt(
            url=safe_url,
            final_url=raw.final_url,
            title=raw.title,
            content_digest=hashlib.sha256(raw.png_bytes).hexdigest(),
            byte_count=len(raw.png_bytes),
            width=raw.width,
            height=raw.height,
            captured_at=captured_at,
            expires_at=captured_at + self.ttl,
        )
        return ScreenshotEvidence(
            receipt=receipt,
            png_base64=base64.b64encode(raw.png_bytes).decode("ascii"),
        )
