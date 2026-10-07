from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor

from cinesub.core.ratelimit import RateLimiter


def test_rate_limiter_acquire() -> None:
    """Test token acquisition."""
    limiter = RateLimiter(rate=100.0, max_burst=5.0)
    start = time.monotonic()
    for _ in range(5):
        limiter.acquire()
    elapsed = time.monotonic() - start
    assert elapsed < 0.1


def test_rate_limiter_cooldown() -> None:
    """Test 429 cooldown backoff."""
    limiter = RateLimiter(rate=100.0, max_burst=5.0)
    limiter.trigger_cooldown(0.2)
    start = time.monotonic()
    limiter.acquire()
    elapsed = time.monotonic() - start
    assert elapsed >= 0.18


def test_rate_limiter_multithreaded() -> None:
    """Test concurrent thread safety."""
    limiter = RateLimiter(rate=50.0, max_burst=10.0)

    def worker() -> None:
        for _ in range(5):
            limiter.acquire()

    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(worker) for _ in range(4)]
        for f in futures:
            f.result()


def test_rate_limiter_circuit_breaker() -> None:
    """Test circuit breaker availability and unreachability cooldown."""
    limiter = RateLimiter(rate=10.0, max_burst=2.0)
    assert limiter.is_available is True

    limiter.mark_unreachable(0.2)
    assert limiter.is_available is False

    time.sleep(0.22)
    assert limiter.is_available is True
