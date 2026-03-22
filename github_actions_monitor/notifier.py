"""
notifier.py — Windows toast notification logic.

Wraps plyer's notification API so the rest of the application can fire
structured, consistently-formatted toasts without knowing the underlying
library.  All notification messages are logged as well so the log file
provides a full audit trail even when toasts are missed.

Note on click-to-open behaviour
--------------------------------
plyer does not reliably support click callbacks on Windows toast
notifications.  For "click to open" behaviour the tray menu provides a
per-repo "Open last run" option.  The notification message for failures
explicitly says "(see tray menu)" so users know where to go.
"""

from __future__ import annotations

import logging
import webbrowser
from typing import Optional

logger = logging.getLogger(__name__)

_APP_NAME = "GitHub Actions Monitor"

# Attempt to import plyer; if unavailable the notifier degrades gracefully
# and only logs messages (useful for headless CI environments).
try:
    from plyer import notification as _plyer_notify

    _PLYER_AVAILABLE = True
except ImportError:  # pragma: no cover
    _PLYER_AVAILABLE = False
    logger.warning("plyer is not installed — toast notifications disabled.")


def _toast(title: str, message: str, timeout: int = 8) -> None:
    """
    Fire a Windows toast notification and log it.

    Parameters
    ----------
    title:
        Bold heading shown at the top of the toast.
    message:
        Body text of the toast.
    timeout:
        How many seconds before the toast auto-dismisses (best-effort;
        Windows may override this).
    """
    logger.info("NOTIFY  %s | %s", title, message)
    if not _PLYER_AVAILABLE:
        return
    try:
        _plyer_notify.notify(
            app_name=_APP_NAME,
            title=title,
            message=message,
            timeout=timeout,
        )
    except Exception as exc:  # plyer can raise on misconfigured systems
        logger.warning("Toast notification failed: %s", exc)


class Notifier:
    """
    High-level notification dispatcher for GitHub Actions events.

    All public methods accept human-friendly strings that have already
    been formatted by the caller (e.g. duration, repo/workflow names).
    """

    # ------------------------------------------------------------------
    # Workflow lifecycle events
    # ------------------------------------------------------------------

    def notify_started(self, repo: str, workflow: str, url: str) -> None:
        """
        Fire a toast when a workflow run is first detected in queued or
        in_progress state.

        Parameters
        ----------
        repo:
            Short repo name (e.g. ``owner/my-repo``).
        workflow:
            Workflow display name (e.g. ``CI``).
        url:
            HTML URL of the workflow run on GitHub.
        """
        title = f"\U0001f504 Run started"
        message = f"{repo} / {workflow} — run started"
        _toast(title, message, timeout=5)

    def notify_succeeded(
        self, repo: str, workflow: str, url: str, duration: str
    ) -> None:
        """
        Fire a toast when a workflow run completes with conclusion ``success``.

        Parameters
        ----------
        repo:
            Short repo name.
        workflow:
            Workflow display name.
        url:
            HTML URL of the run.
        duration:
            Human-readable elapsed time (e.g. ``"2m 34s"``).
        """
        title = "\u2705 Workflow passed"
        message = f"{repo} / {workflow} — passed in {duration}"
        _toast(title, message, timeout=8)

    def notify_failed(self, repo: str, workflow: str, url: str) -> None:
        """
        Fire a toast when a workflow run completes with conclusion ``failure``.

        The message directs the user to the tray menu to open the run URL
        because plyer click-callbacks are not reliable on Windows.

        Parameters
        ----------
        repo:
            Short repo name.
        workflow:
            Workflow display name.
        url:
            HTML URL of the run (used for logging; opened via tray menu).
        """
        title = "\u274c Workflow FAILED"
        message = f"{repo} / {workflow} — FAILED (open via tray menu)"
        _toast(title, message, timeout=12)

    def notify_cancelled(self, repo: str, workflow: str, url: str) -> None:
        """
        Fire a toast when a workflow run completes with conclusion
        ``cancelled`` or ``skipped``.

        Parameters
        ----------
        repo:
            Short repo name.
        workflow:
            Workflow display name.
        url:
            HTML URL of the run.
        """
        title = "\u26a0\ufe0f Workflow cancelled"
        message = f"{repo} / {workflow} — cancelled"
        _toast(title, message, timeout=6)

    # ------------------------------------------------------------------
    # Application-level events
    # ------------------------------------------------------------------

    def notify_config_missing(self, config_path: str) -> None:
        """
        Inform the user that a stub config.ini was created and needs editing.

        Parameters
        ----------
        config_path:
            Absolute path to the newly-created ``config.ini``.
        """
        title = f"{_APP_NAME} — setup required"
        message = (
            f"A default config was created at:\n{config_path}\n"
            "Please fill in your GitHub token and repositories, then "
            "right-click the tray icon and choose 'Refresh Config'."
        )
        _toast(title, message, timeout=20)

    def notify_auth_error(self) -> None:
        """Inform the user that the GitHub token is invalid or missing."""
        title = f"{_APP_NAME} — authentication error"
        message = (
            "GitHub returned 401 Unauthorized.\n"
            "Please update your token in config.ini and choose 'Refresh Config'."
        )
        _toast(title, message, timeout=15)

    def notify_rate_limited(self, reset_time_str: str) -> None:
        """
        Inform the user that polling is paused due to rate limiting.

        Parameters
        ----------
        reset_time_str:
            Human-readable local time string when the rate limit resets.
        """
        title = f"{_APP_NAME} — rate limited"
        message = f"GitHub API rate limit hit. Polling resumes at {reset_time_str}."
        _toast(title, message, timeout=10)

    # ------------------------------------------------------------------
    # Utility
    # ------------------------------------------------------------------

    @staticmethod
    def open_url(url: str) -> None:
        """Open *url* in the default system browser."""
        logger.info("Opening URL in browser: %s", url)
        try:
            webbrowser.open(url)
        except Exception as exc:
            logger.error("Failed to open browser: %s", exc)
