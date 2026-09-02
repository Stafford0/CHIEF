from __future__ import annotations

import base64
from datetime import UTC, datetime

import pytest

from chief.browser.capture import RawScreenshot, ScreenshotCaptureService
from chief.browser.research import BrowserUrlPolicy

NOW = datetime(2026, 9, 2, 3, 15, tzinfo=UTC)


class FakeScreenshotDriver:
    def __init__(self, *, final_url: str = "https://example.com/final", data: bytes = b"png-data"):
        self.final_url = final_url
        self.data = data
        self.calls = 0

    def capture(self, url: str, *, timeout_ms: int, width: int, height: int) -> RawScreenshot:
        self.calls += 1
        return RawScreenshot(
            final_url=self.final_url,
            title="Example",
            png_bytes=self.data,
            width=width,
            height=height,
        )


def _policy() -> BrowserUrlPolicy:
    return BrowserUrlPolicy(resolver=lambda _host: ["93.184.216.34"])


def test_screenshot_capture_returns_digest_expiry_and_untrusted_label() -> None:
    driver = FakeScreenshotDriver()
    service = ScreenshotCaptureService(driver, policy=_policy(), ttl_seconds=300)

    evidence = service.capture("https://example.com", now=NOW)

    assert evidence.receipt.persisted is False
    assert evidence.receipt.trust == "untrusted_external"
    assert evidence.receipt.expires_at > evidence.receipt.captured_at
    assert evidence.receipt.byte_count == len(b"png-data")
    assert base64.b64decode(evidence.png_base64) == b"png-data"
    assert len(evidence.receipt.content_digest) == 64


def test_screenshot_capture_rechecks_redirect_target() -> None:
    driver = FakeScreenshotDriver(final_url="http://127.0.0.1/private")
    service = ScreenshotCaptureService(driver, policy=_policy())

    with pytest.raises(PermissionError, match="Private or local"):
        service.capture("https://example.com", now=NOW)


def test_screenshot_capture_enforces_in_memory_size_limit() -> None:
    driver = FakeScreenshotDriver(data=b"x" * 100_001)
    service = ScreenshotCaptureService(driver, policy=_policy(), max_bytes=100_000)

    with pytest.raises(RuntimeError, match="in-memory evidence limit"):
        service.capture("https://example.com", now=NOW)
