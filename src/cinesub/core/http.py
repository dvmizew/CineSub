from __future__ import annotations

import atexit

import httpx

from cinesub.core.constants import DEFAULT_TIMEOUT, USER_AGENT

SESSION = httpx.Client(
    transport=httpx.HTTPTransport(
        http2=True,
        retries=3,
        limits=httpx.Limits(
            max_connections=50,
            max_keepalive_connections=15,
            keepalive_expiry=30.0,
        ),
    ),
    timeout=httpx.Timeout(
        connect=4.0,
        read=max(float(DEFAULT_TIMEOUT), 25.0),
        write=15.0,
        pool=10.0,
    ),
    follow_redirects=True,
    headers={"User-Agent": USER_AGENT},
)

atexit.register(SESSION.close)
