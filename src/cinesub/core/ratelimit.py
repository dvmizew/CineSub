from __future__ import annotations

import threading
import time


class RateLimiter:
    def __init__(self, rate: float, max_burst: float = 1.0) -> None:
        """Initialize RateLimiter.

        Args:
            rate: Allowed requests per second (e.g. 4.0 for OpenSubtitles, 8.0 for SubDL).
            max_burst: Maximum token capacity for burst traffic.
        """
        self.rate = float(rate)
        self.capacity = float(max_burst)
        self.tokens = float(max_burst)
        self.last_update = time.monotonic()
        self.lock = threading.Lock()
        self.cooldown_until: float = 0.0

    def acquire(self) -> None:
        """Block until a token is available and cooldown expired."""
        while True:
            with self.lock:
                now = time.monotonic()
                # If currently under 429 cooldown
                if now < self.cooldown_until:
                    sleep_time = self.cooldown_until - now
                else:
                    # Replenish tokens
                    elapsed = now - self.last_update
                    self.last_update = now
                    self.tokens = min(self.capacity, self.tokens + (elapsed * self.rate))

                    if self.tokens >= 1.0:
                        self.tokens -= 1.0
                        return

                    # Compute wait duration for 1 token
                    needed = 1.0 - self.tokens
                    sleep_time = needed / self.rate

            time.sleep(max(0.01, sleep_time))

    def trigger_cooldown(self, seconds: float = 5.0) -> None:
        """Pause all worker threads across this service for specified seconds."""
        with self.lock:
            self.cooldown_until = max(self.cooldown_until, time.monotonic() + seconds)
            self.tokens = 0.0


# OpenSubtitles: Official limit is 5 req/sec (login is 1 req/sec). Safe client rate: 4.0 req/sec.
OPENSUBTITLES_LIMITER = RateLimiter(rate=4.0, max_burst=1.0)

# SubDL: Official limit is 600 req/min (10 req/sec). Safe client rate: 8.0 req/sec.
SUBDL_LIMITER = RateLimiter(rate=8.0, max_burst=2.0)

# SubSource: Official limit is 60 req/min (1.0 req/sec). Safe client rate: 1.0 req/sec.
SUBSOURCE_LIMITER = RateLimiter(rate=1.0, max_burst=1.0)

# TMDb: Official guidance accommodates up to ~40-50 req/s. Safe client rate: 4.0 req/sec.
TMDB_LIMITER = RateLimiter(rate=4.0, max_burst=2.0)

# Subs.ro: Safe client rate: 2.0 req/sec.
SUBSRO_LIMITER = RateLimiter(rate=2.0, max_burst=1.0)

# BetaSeries: Safe client rate: 2.0 req/sec.
BETASERIES_LIMITER = RateLimiter(rate=2.0, max_burst=1.0)

# Gestdown: Addic7ed proxy. Safe client rate: 2.0 req/sec.
GESTDOWN_LIMITER = RateLimiter(rate=2.0, max_burst=1.0)

# BSPlayer: Hash-based SOAP API. Safe client rate: 2.0 req/sec.
BSPLAYER_LIMITER = RateLimiter(rate=2.0, max_burst=1.0)

# AnimeTosho: Feed and storage API. Safe client rate: 2.0 req/sec.
ANIMETOSHO_LIMITER = RateLimiter(rate=2.0, max_burst=1.0)

# Assrt.net: Official limit is 20 req/min (0.33 req/sec). Safe client rate: 0.33 req/sec.
ASSRT_LIMITER = RateLimiter(rate=0.33, max_burst=1.0)
