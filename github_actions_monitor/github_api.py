"""
github_api.py — All GitHub REST API interactions.

Wraps the GitHub v3 REST API with typed exceptions so the rest of the
application can react to auth failures, rate limits, and missing repos
without inspecting raw HTTP responses.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------


class GitHubAPIError(Exception):
    """Base class for all GitHub API errors raised by this module."""


class AuthError(GitHubAPIError):
    """Raised when the GitHub API returns 401 Unauthorized."""


class RateLimitError(GitHubAPIError):
    """
    Raised when the GitHub API returns 403 or 429 (rate limited).

    Attributes
    ----------
    reset_at:
        UTC datetime at which the rate limit resets, if available.
    """

    def __init__(self, message: str, reset_at: Optional[datetime] = None) -> None:
        super().__init__(message)
        self.reset_at = reset_at


class RepoNotFoundError(GitHubAPIError):
    """Raised when a repo returns 404 or the token lacks access."""


# ---------------------------------------------------------------------------
# API client
# ---------------------------------------------------------------------------


class GitHubAPI:
    """
    Thin wrapper around the GitHub REST API v3.

    Parameters
    ----------
    token:
        Personal Access Token (classic or fine-grained) with
        ``repo`` and ``workflow`` read scopes.
    username:
        GitHub username, used only for "all repos" discovery fallback.
    """

    _BASE = "https://api.github.com"

    def __init__(self, token: str, username: str) -> None:
        self._username = username
        self._session = requests.Session()
        self._session.headers.update(
            {
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            }
        )
        # ETag-based conditional request caches: keyed by (path, sorted-params).
        self._etag_cache: Dict[str, str] = {}
        self._response_cache: Dict[str, Any] = {}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get(self, path: str, params: Optional[dict] = None) -> dict | list:
        """
        Perform a GET request and return the parsed JSON body.

        Uses HTTP conditional requests (ETag / If-None-Match) to avoid
        re-downloading unchanged responses.  A 304 Not Modified response
        returns the previously cached body at no bandwidth cost.

        Raises typed exceptions for 401, 403/429, and 404 responses.
        All other non-2xx responses raise :class:`GitHubAPIError`.
        """
        url = f"{self._BASE}{path}"
        cache_key = path + str(sorted(params.items()) if params else "")
        headers: dict = {}
        if cache_key in self._etag_cache:
            headers["If-None-Match"] = self._etag_cache[cache_key]

        try:
            response = self._session.get(url, params=params, headers=headers, timeout=15)
        except requests.RequestException as exc:
            raise GitHubAPIError(f"Network error: {exc}") from exc

        if response.status_code == 304:
            logger.debug("304 Not Modified for %s — reusing cached response.", path)
            return self._response_cache[cache_key]

        self._raise_for_status(response)
        data = response.json()

        etag = response.headers.get("ETag")
        if etag:
            self._etag_cache[cache_key] = etag
            self._response_cache[cache_key] = data

        return data

    @staticmethod
    def _raise_for_status(response: requests.Response) -> None:
        """Convert HTTP error responses into typed exceptions."""
        if response.status_code == 401:
            raise AuthError(
                "GitHub API returned 401 — check your Personal Access Token."
            )

        if response.status_code in (403, 429):
            reset_ts = response.headers.get("X-RateLimit-Reset")
            reset_at: Optional[datetime] = None
            if reset_ts:
                try:
                    reset_at = datetime.fromtimestamp(int(reset_ts), tz=timezone.utc)
                except ValueError:
                    pass
            raise RateLimitError(
                f"GitHub API rate limit exceeded (HTTP {response.status_code}).",
                reset_at=reset_at,
            )

        if response.status_code == 404:
            raise RepoNotFoundError(
                f"Resource not found or token lacks access: {response.url}"
            )

        if not response.ok:
            raise GitHubAPIError(
                f"Unexpected HTTP {response.status_code} from {response.url}: "
                f"{response.text[:200]}"
            )

    # ------------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------------

    def get_repos(self, watch_config: str) -> List[str]:
        """
        Resolve the ``watch`` config value to a list of ``owner/repo`` strings.

        Parameters
        ----------
        watch_config:
            Either ``"all"`` to auto-discover up to 50 repos for the
            authenticated user, or a comma-separated list like
            ``"owner/repo1, owner/repo2"``.

        Returns
        -------
        list[str]
            Unique, stripped ``owner/repo`` strings.
        """
        if watch_config.strip().lower() == "all":
            return self._discover_repos()

        repos = [r.strip() for r in watch_config.split(",") if r.strip()]
        return repos

    def _discover_repos(self) -> List[str]:
        """Return up to 50 repos visible to the authenticated token."""
        data = self._get(
            "/user/repos",
            params={"type": "all", "per_page": 50, "sort": "pushed"},
        )
        repos = [item["full_name"] for item in data if isinstance(item, dict)]
        logger.info("Auto-discovered %d repo(s).", len(repos))
        return repos

    def get_workflow_runs(
        self,
        repo: str,
        lookback_minutes: Optional[int] = None,
    ) -> List[dict]:
        """
        Fetch recent workflow runs for *repo*.

        Parameters
        ----------
        repo:
            Full ``owner/repo`` string.
        lookback_minutes:
            When set, only runs created within this many minutes in the past
            are returned.  Pass ``None`` to skip time filtering.

        Returns
        -------
        list[dict]
            Raw workflow run objects from the GitHub API, newest first.
        """
        params: dict = {"per_page": 50}

        if lookback_minutes is not None:
            since = _utcnow_minus_minutes(lookback_minutes)
            params["created"] = f">={since}"

        try:
            data = self._get(f"/repos/{repo}/actions/runs", params=params)
        except RepoNotFoundError:
            logger.warning("Repo %s not found or not accessible — skipping.", repo)
            return []

        if not isinstance(data, dict):
            logger.warning("Unexpected response shape for %s runs.", repo)
            return []

        runs: List[dict] = data.get("workflow_runs", [])
        logger.debug("Fetched %d run(s) for %s.", len(runs), repo)
        return runs

    def get_workflow_name(self, repo: str, workflow_id: int) -> str:
        """
        Resolve a workflow ID to its human-readable name.

        Falls back to the stringified ID if the lookup fails.
        """
        try:
            data = self._get(f"/repos/{repo}/actions/workflows/{workflow_id}")
            return data.get("name", str(workflow_id))  # type: ignore[union-attr]
        except GitHubAPIError:
            return str(workflow_id)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utcnow_minus_minutes(minutes: int) -> str:
    """Return an ISO-8601 UTC timestamp *minutes* before now (no microseconds)."""
    from datetime import timedelta

    dt = datetime.now(tz=timezone.utc) - timedelta(minutes=minutes)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
