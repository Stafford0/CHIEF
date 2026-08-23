import pytest

from chief.core.rate_limit import SlidingWindowRateLimiter


def test_sliding_window_rate_limiter_recovers_after_window() -> None:
    clock = [10.0]
    limiter = SlidingWindowRateLimiter(2, window_seconds=5, clock=lambda: clock[0])

    assert limiter.check("client").allowed is True
    assert limiter.check("client").allowed is True
    denied = limiter.check("client")
    assert denied.allowed is False
    assert denied.retry_after_seconds == 5

    clock[0] = 15.1
    assert limiter.check("client").allowed is True


def test_rate_limiter_isolates_clients_and_bounds_keys() -> None:
    limiter = SlidingWindowRateLimiter(1, max_keys=1)

    assert limiter.check("first").allowed is True
    assert limiter.check("second").allowed is True
    # The least-recently-used key was evicted, so it receives a fresh budget.
    assert limiter.check("first").allowed is True


@pytest.mark.parametrize(
    ("limit", "window", "keys"),
    [(0, 60, 10), (1, 0, 10), (1, 60, 0)],
)
def test_rate_limiter_rejects_invalid_configuration(limit, window, keys) -> None:
    with pytest.raises(ValueError):
        SlidingWindowRateLimiter(limit, window_seconds=window, max_keys=keys)
