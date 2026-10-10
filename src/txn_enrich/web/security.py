"""Access control and deployment options for the web UI.

Locally nothing is required. When the server is exposed (Render, Fly, ...) it spends your API
key for anyone who can reach it, so a password is mandatory: see `check_exposure`.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable, Mapping, Optional

LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
TOKEN_TTL = 12 * 3600  # seconds a login stays valid
MAX_FAILS, FAIL_WINDOW = 10, 60  # wrong passwords allowed per rolling minute (all clients)
MIN_PASSWORD = 8


def _csv(value: Optional[str]) -> tuple[str, ...]:
    return tuple(p.strip() for p in (value or "").split(",") if p.strip())


def _int(value: Optional[str], default: int, lo: int = 0) -> int:
    try:
        return max(lo, int(value)) if value not in (None, "") else default
    except ValueError:
        return default


@dataclass(frozen=True)
class WebOptions:
    password: str = ""  # empty = no login (local use only)
    allowed_origins: tuple[str, ...] = ()  # browser origins allowed to call the API (CORS)
    allowed_hosts: tuple[str, ...] = ()  # extra Host headers accepted ("*" = any)
    max_rows: int = 1000
    max_searches: int = 500  # hard ceiling on "max web searches" a client may request
    max_running: int = 0  # simultaneous runs; 0 = unlimited

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None) -> "WebOptions":
        env = os.environ if env is None else env
        return cls(
            password=env.get("TXN_ACCESS_PASSWORD", ""),
            allowed_origins=tuple(o.rstrip("/") for o in _csv(env.get("TXN_ALLOWED_ORIGINS"))),
            allowed_hosts=_csv(env.get("TXN_ALLOWED_HOSTS")),
            max_rows=_int(env.get("TXN_MAX_ROWS"), 1000, 1),
            max_searches=_int(env.get("TXN_MAX_SEARCHES"), 500, 0),
            max_running=_int(env.get("TXN_MAX_RUNNING"), 0, 0),
        )


def check_exposure(host: str, options: WebOptions) -> Optional[str]:
    """Error message when the server would be reachable by others without a real password."""
    if host in LOCAL_HOSTS or len(options.password) >= MIN_PASSWORD:
        return None
    return (
        f"Refusing to listen on {host} without TXN_ACCESS_PASSWORD "
        f"(at least {MIN_PASSWORD} characters): anyone who can reach the server would spend "
        "your API key."
    )


class RateLimited(Exception):
    pass


class Auth:
    """Shared-password login that hands out signed, expiring tokens (stateless)."""

    def __init__(self, password: str, clock: Callable[[], float] = time.time):
        self.password = password
        self._clock = clock
        self._key = hashlib.sha256(b"txn-token:" + password.encode()).digest()
        self._fails: deque[float] = deque()

    @property
    def required(self) -> bool:
        return bool(self.password)

    def _sign(self, expires: int) -> str:
        return hmac.new(self._key, str(expires).encode(), hashlib.sha256).hexdigest()

    def issue(self, ttl: int = TOKEN_TTL) -> str:
        expires = int(self._clock()) + ttl
        return f"{expires}.{self._sign(expires)}"

    def valid(self, token: str) -> bool:
        if not self.required:
            return True
        expires_text, _, signature = (token or "").partition(".")
        try:
            expires = int(expires_text)
        except ValueError:
            return False
        if expires < self._clock():
            return False
        return hmac.compare_digest(signature.encode(), self._sign(expires).encode())

    def login(self, given: str) -> str:
        """Token for the right password. PermissionError if wrong, RateLimited if locked out."""
        now = self._clock()
        while self._fails and now - self._fails[0] > FAIL_WINDOW:
            self._fails.popleft()
        if len(self._fails) >= MAX_FAILS:
            raise RateLimited()
        if self.required and not hmac.compare_digest(given.encode(), self.password.encode()):
            self._fails.append(now)
            raise PermissionError("wrong password")
        return self.issue()
