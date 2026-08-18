"""Shared HTTP session (v3.10 performance).

Every API call previously created a brand-new connection: a fresh TCP handshake
plus TLS negotiation per request. In bulk runs (hundreds of devices) and in the
Messaging poll loop (every few seconds, forever) that overhead dominates.

A single pooled Session keeps connections alive and reuses them, which cuts
per-request latency substantially and stops the app from churning sockets.

Usage: `from .http import session` then `session.post(...)` exactly as with
`requests.post(...)`. Drop-in.
"""
import requests
from requests.adapters import HTTPAdapter


def _build_session():
    s = requests.Session()
    # Pool enough connections for bulk loops without unbounded growth.
    adapter = HTTPAdapter(pool_connections=10, pool_maxsize=20, max_retries=0)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    s.headers.update({"User-Agent": "FleetX-Toolkit"})
    return s


# Module-level singleton — safe to share across threads (requests.Session is
# thread-safe for separate requests; we never mutate it after construction).
session = _build_session()


def reset_session():
    """Rebuild the pool (e.g. after a network change). Rarely needed."""
    global session
    try:
        session.close()
    except Exception:
        pass
    session = _build_session()
    return session
