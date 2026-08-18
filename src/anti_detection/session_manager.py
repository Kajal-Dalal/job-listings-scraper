"""
Session and identity management.

Features:
- Separate identity (headers + cookies) per scrape job
- Referrer header chain to simulate natural browsing
- Cookie jar management per identity
- Connection keep-alive management
- Session reuse within a scrape run
"""
import uuid
from typing import Dict, Optional

import httpx

from src.anti_detection.user_agent_rotator import UserAgentRotator
from src.monitoring.logger import get_logger

log = get_logger(__name__)


class BrowserIdentity:
    """
    A single browser identity: one set of headers and one cookie jar,
    representing a single browser session.
    """

    def __init__(self, identity_id: str, headers: Dict[str, str]):
        self.identity_id = identity_id
        self.headers = headers
        self.referrer: Optional[str] = None
        self._cookie_jar: Dict[str, str] = {}

    def set_referrer(self, referrer_url: str) -> None:
        """Simulate navigating from a previous page."""
        self.referrer = referrer_url

    def get_request_headers(self, url: Optional[str] = None) -> Dict[str, str]:
        """
        Build the final request headers for a given URL,
        including referrer if set.
        """
        headers = dict(self.headers)
        if self.referrer:
            headers["Referer"] = self.referrer
        return headers

    def update_cookies(self, cookies: Dict[str, str]) -> None:
        """Merge new cookies into the jar."""
        self._cookie_jar.update(cookies)


class SessionManager:
    """
    Creates and manages browser identities per scrape job.

    Each scrape run should create a new identity to avoid
    linking separate scrape sessions via cookies or headers.
    """

    def __init__(self, ua_rotator: Optional[UserAgentRotator] = None):
        self._ua_rotator = ua_rotator or UserAgentRotator()
        self._active_sessions: Dict[str, httpx.AsyncClient] = {}
        self._identities: Dict[str, BrowserIdentity] = {}

    def create_identity(self, domain: Optional[str] = None) -> BrowserIdentity:
        """
        Create a new browser identity with a fresh UA and matching headers.

        Args:
            domain: Optional domain for UA diversity tracking

        Returns:
            BrowserIdentity with unique ID and headers
        """
        identity_id = str(uuid.uuid4())
        headers = self._ua_rotator.get(domain=domain)
        identity = BrowserIdentity(identity_id=identity_id, headers=headers)
        self._identities[identity_id] = identity
        log.debug(
            "identity_created",
            identity_id=identity_id,
            ua=headers.get("User-Agent", "unknown")[:60],
        )
        return identity

    def create_client(
        self,
        identity: BrowserIdentity,
        proxy_url: Optional[str] = None,
        timeout: float = 30.0,
        follow_redirects: bool = True,
    ) -> httpx.AsyncClient:
        """
        Create an httpx AsyncClient for the given identity.

        Args:
            identity:          The browser identity to use
            proxy_url:         Optional proxy URL (http:// or socks5://)
            timeout:           Request timeout in seconds
            follow_redirects:  Whether to follow HTTP redirects

        Returns:
            Configured httpx.AsyncClient (caller must use as async context manager)
        """
        headers = identity.get_request_headers()

        client_kwargs = {
            "headers": headers,
            "timeout": httpx.Timeout(timeout),
            "follow_redirects": follow_redirects,
            "http2": True,  # Use HTTP/2 when available (matches real browsers)
        }

        if proxy_url:
            client_kwargs["proxy"] = proxy_url

        client = httpx.AsyncClient(**client_kwargs)
        self._active_sessions[identity.identity_id] = client
        return client

    async def close_session(self, identity_id: str) -> None:
        """Close and remove a session by identity ID."""
        client = self._active_sessions.pop(identity_id, None)
        if client:
            await client.aclose()
            log.debug("session_closed", identity_id=identity_id)

    async def close_all(self) -> None:
        """Close all active sessions."""
        for identity_id, client in list(self._active_sessions.items()):
            try:
                await client.aclose()
            except Exception:
                pass
        self._active_sessions.clear()
        self._identities.clear()
        log.info("all_sessions_closed")

    @property
    def active_session_count(self) -> int:
        return len(self._active_sessions)
