import asyncio

import pytest

from chief.core.request_limits import RequestBodyLimitMiddleware


def test_chunked_body_cannot_bypass_request_limit() -> None:
    requests = iter(
        [
            {"type": "http.request", "body": b"123", "more_body": True},
            {"type": "http.request", "body": b"456", "more_body": False},
        ]
    )
    responses = []

    async def receive():
        return next(requests)

    async def send(message):
        responses.append(message)

    async def read_body(_scope, receive_body, _send):
        await receive_body()
        await receive_body()

    middleware = RequestBodyLimitMiddleware(read_body, max_body_bytes=5)
    asyncio.run(
        middleware(
            {"type": "http", "headers": []},
            receive,
            send,
        )
    )

    assert responses[0]["status"] == 413


def test_request_limit_rejects_invalid_budget() -> None:
    with pytest.raises(ValueError):
        RequestBodyLimitMiddleware(lambda *_: None, max_body_bytes=0)
