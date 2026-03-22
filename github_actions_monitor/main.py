"""
main.py — Entry point: system tray setup, config management, polling loop.

Run directly:
    python main.py

Build a standalone exe (no console window):
    pyinstaller --noconsole --onefile --name "GH Actions Monitor" main.py

Add to Windows Startup:
    1. Press Win+R, type ``shell:startup``, press Enter.
    2. Create a shortcut to the compiled ``GH Actions Monitor.exe`` there.
       The app will then launch automatically when you log in.
"""

from __future__ import annotations

import configparser
import logging
import os
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pystray
from PIL import Image, ImageDraw, ImageFont

from github_api import AuthError, GitHubAPI, GitHubAPIError, RateLimitError
from notifier import Notifier
from state import RunState, StateManager

# ---------------------------------------------------------------------------
# Data directory — %LOCALAPPDATA%/GitHubActionsMonitor  (or cwd on non-Windows)
# ---------------------------------------------------------------------------

_APP_FOLDER = "GitHubActionsMonitor"


def _get_data_dir() -> Path:
    """Return the platform-appropriate directory for config/data/log files."""
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / _APP_FOLDER
    # Fallback for development on non-Windows hosts
    return Path.home() / ".github_actions_monitor"


DATA_DIR = _get_data_dir()
DATA_DIR.mkdir(parents=True, exist_ok=True)

CONFIG_PATH = DATA_DIR / "config.ini"
LOG_PATH = DATA_DIR / "github_monitor.log"

# ---------------------------------------------------------------------------
# Logging — rotating file, no stdout (no console when built with --noconsole)
# ---------------------------------------------------------------------------


def _setup_logging() -> None:
    """Configure a rotating file logger for the whole application."""
    handler = RotatingFileHandler(
        LOG_PATH, maxBytes=1_000_000, backupCount=2, encoding="utf-8"
    )
    formatter = logging.Formatter(
        "%(asctime)s  %(levelname)-8s  %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    handler.setFormatter(formatter)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)


_setup_logging()
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Default config template
# ---------------------------------------------------------------------------

_DEFAULT_CONFIG = """\
[github]
; Personal Access Token — two options:
;
;   Classic PAT (github.com → Settings → Developer settings → Tokens (classic))
;     Scope needed for PRIVATE repos : repo
;     Scope needed for PUBLIC repos only: public_repo
;     Do NOT add the "workflow" scope — that is for writing workflow files, not reading them.
;
;   Fine-grained PAT (github.com → Settings → Developer settings → Fine-grained tokens)
;     Repository permissions:
;       Actions   → Read-only
;       Metadata  → Read-only  (auto-selected; required for all repo access)
;     No account permissions are needed.
;
token = YOUR_PAT_HERE
username = your-github-username

[repos]
; Comma-separated list of owner/repo pairs, or "all" to auto-discover all your repos (up to 50).
watch = owner/repo1, owner/repo2

[settings]
; How often to poll GitHub, in seconds (minimum 10).
poll_interval_seconds = 30
; Only notify for workflows triggered by a specific event, or "all".
; Valid values: push, pull_request, workflow_dispatch, schedule, all
trigger_filter = all
; On startup, ignore runs older than this many minutes to avoid a flood of old notifications.
lookback_minutes = 60
"""

# ---------------------------------------------------------------------------
# Config dataclass + manager
# ---------------------------------------------------------------------------


class AppConfig:
    """Typed view of config.ini values."""

    def __init__(
        self,
        token: str,
        username: str,
        watch: str,
        poll_interval: int,
        trigger_filter: str,
        lookback_minutes: int,
    ) -> None:
        self.token = token
        self.username = username
        self.watch = watch
        self.poll_interval = poll_interval
        self.trigger_filter = trigger_filter
        self.lookback_minutes = lookback_minutes

    @property
    def is_placeholder(self) -> bool:
        """Return True if the token still has the default placeholder value."""
        return self.token in ("", "YOUR_PAT_HERE")


class ConfigManager:
    """Reads and writes ``config.ini`` located in :data:`DATA_DIR`."""

    def __init__(self, config_path: Path = CONFIG_PATH) -> None:
        self._path = config_path

    @property
    def path(self) -> Path:
        """Absolute path to the config file."""
        return self._path

    def exists(self) -> bool:
        """Return True if the config file exists on disk."""
        return self._path.exists()

    def create_default(self) -> None:
        """Write a stub config.ini to disk so the user has something to edit."""
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(_DEFAULT_CONFIG, encoding="utf-8")
            logger.info("Created default config.ini at %s", self._path)
        except OSError as exc:
            logger.error("Failed to create config.ini: %s", exc)

    def load(self) -> AppConfig:
        """
        Parse config.ini and return an :class:`AppConfig`.

        Missing keys fall back to sensible defaults so the app doesn't crash
        on partial configs.
        """
        parser = configparser.ConfigParser(inline_comment_prefixes=(";", "#"))
        parser.read(self._path, encoding="utf-8")

        token = parser.get("github", "token", fallback="")
        username = parser.get("github", "username", fallback="")
        watch = parser.get("repos", "watch", fallback="")

        try:
            poll_interval = max(10, parser.getint("settings", "poll_interval_seconds", fallback=30))
        except ValueError:
            poll_interval = 30

        trigger_filter = parser.get("settings", "trigger_filter", fallback="all").strip().lower()

        try:
            lookback_minutes = max(0, parser.getint("settings", "lookback_minutes", fallback=60))
        except ValueError:
            lookback_minutes = 60

        return AppConfig(
            token=token.strip(),
            username=username.strip(),
            watch=watch.strip(),
            poll_interval=poll_interval,
            trigger_filter=trigger_filter,
            lookback_minutes=lookback_minutes,
        )

    def open_editor(self) -> None:
        """Open config.ini in Notepad (Windows) or the default text editor."""
        try:
            if sys.platform == "win32":
                subprocess.Popen(["notepad", str(self._path)])
            else:
                # Fallback for development on Linux/macOS
                editor = os.environ.get("EDITOR", "nano")
                subprocess.Popen([editor, str(self._path)])
        except OSError as exc:
            logger.error("Failed to open editor for config: %s", exc)


# ---------------------------------------------------------------------------
# Tray icon image generation
# ---------------------------------------------------------------------------

_ICON_SIZE = 64


def _generate_icon(color: str = "#24292e", badge_color: Optional[str] = None) -> Image.Image:
    """
    Generate a simple 64×64 tray icon using Pillow.

    Draws a filled circle on a transparent background with an optional
    small status badge in the bottom-right corner.

    Parameters
    ----------
    color:
        Hex color for the main circle (default: GitHub dark).
    badge_color:
        If given, a small filled circle of this color is drawn in the
        bottom-right corner (e.g. ``"#28a745"`` for green).

    Returns
    -------
    PIL.Image.Image
        RGBA image suitable for use with pystray.
    """
    size = _ICON_SIZE
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Main circle
    margin = 4
    draw.ellipse(
        [margin, margin, size - margin, size - margin],
        fill=color,
    )

    # White "G" letter in the centre
    try:
        font = ImageFont.truetype("arial.ttf", 28)
    except (OSError, IOError):
        font = ImageFont.load_default()

    text = "GH"
    bbox = draw.textbbox((0, 0), text, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    x = (size - text_w) // 2
    y = (size - text_h) // 2 - 2
    draw.text((x, y), text, fill="white", font=font)

    # Optional status badge
    if badge_color:
        bx, by = size - 18, size - 18
        draw.ellipse([bx, by, bx + 14, by + 14], fill=badge_color, outline="white")

    return img


# Dot prefixes for repo status items in the menu
_DOT_GREEN = "\U0001f7e2"   # 🟢
_DOT_RED = "\U0001f534"     # 🔴
_DOT_GRAY = "\u26aa"        # ⚫ (grey)

# ---------------------------------------------------------------------------
# Duration formatting
# ---------------------------------------------------------------------------


def _format_duration(started_at: Optional[str], updated_at: str) -> str:
    """
    Return a human-readable elapsed time string (e.g. ``"2m 34s"``).

    Parameters
    ----------
    started_at:
        ISO-8601 UTC string of when the run started, or ``None``.
    updated_at:
        ISO-8601 UTC string of the last update time (used as end time).
    """
    if not started_at:
        return "unknown duration"
    try:
        fmt = "%Y-%m-%dT%H:%M:%SZ"
        start = datetime.strptime(started_at, fmt).replace(tzinfo=timezone.utc)
        end = datetime.strptime(updated_at, fmt).replace(tzinfo=timezone.utc)
        delta = int((end - start).total_seconds())
        if delta < 0:
            delta = 0
        minutes, seconds = divmod(delta, 60)
        if minutes:
            return f"{minutes}m {seconds}s"
        return f"{seconds}s"
    except (ValueError, TypeError):
        return "unknown duration"


# ---------------------------------------------------------------------------
# Main application
# ---------------------------------------------------------------------------


class MonitorApp:
    """
    Orchestrates the system tray icon, background polling thread, and
    notification dispatch.

    The application lifecycle is::

        MonitorApp().run()   # blocks until the user clicks Quit

    All long-running work happens on daemon threads so the process exits
    cleanly when ``run()`` returns.
    """

    def __init__(self) -> None:
        self._config_manager = ConfigManager()
        self._notifier = Notifier()
        self._state_manager: Optional[StateManager] = None
        self._api: Optional[GitHubAPI] = None
        self._config: Optional[AppConfig] = None

        # Tray icon state
        self._tray: Optional[pystray.Icon] = None
        self._repo_statuses: Dict[str, Optional[str]] = {}  # repo → "success"|"failure"|None
        self._last_run_urls: Dict[str, str] = {}            # repo → last run HTML URL

        # Polling control
        self._poll_event = threading.Event()
        self._stop_event = threading.Event()
        self._auth_failed = False
        self._rate_limit_until: Optional[datetime] = None
        # Set to True whenever the config is reloaded so the next poll runs as
        # a startup seed (absorb existing runs without notifying) rather than
        # firing notifications for every historical run.
        self._pending_startup_seed = False

        # Ensure config exists; notify if freshly created
        self._first_run = not self._config_manager.exists()
        if self._first_run:
            self._config_manager.create_default()

        self._reload_config(is_initial=True)

    # ------------------------------------------------------------------
    # Config management
    # ------------------------------------------------------------------

    def _reload_config(self, is_initial: bool = False) -> None:
        """
        Load (or reload) config.ini and re-initialise API + state objects.

        Parameters
        ----------
        is_initial:
            True only during ``__init__``.  For every subsequent reload
            (e.g. "Refresh Config" tray action) this is False, which causes
            the next poll to run as a startup seed so historical runs are
            absorbed silently rather than triggering a notification flood.
        """
        logger.info("Loading configuration from %s", self._config_manager.path)
        self._config = self._config_manager.load()
        self._state_manager = StateManager(DATA_DIR)

        if not self._config.is_placeholder:
            self._api = GitHubAPI(self._config.token, self._config.username)
            self._auth_failed = False
            self._rate_limit_until = None
            logger.info("GitHub API client initialised for user '%s'.", self._config.username)
        else:
            self._api = None
            logger.warning("Token is a placeholder — polling is disabled until config is updated.")

        if not is_initial:
            # Signal the polling loop to treat the next poll as a startup seed.
            self._pending_startup_seed = True

    # ------------------------------------------------------------------
    # Tray icon + menu
    # ------------------------------------------------------------------

    def _build_menu(self) -> pystray.Menu:
        """Construct the right-click context menu for the tray icon."""

        def _open_github(_icon: pystray.Icon, _item: pystray.MenuItem) -> None:
            webbrowser.open("https://github.com")

        def _refresh_now(_icon: pystray.Icon, _item: pystray.MenuItem) -> None:
            logger.info("Manual refresh triggered from tray menu.")
            self._poll_event.set()

        def _edit_config(_icon: pystray.Icon, _item: pystray.MenuItem) -> None:
            self._config_manager.open_editor()

        def _refresh_config(_icon: pystray.Icon, _item: pystray.MenuItem) -> None:
            logger.info("Config reload triggered from tray menu.")
            self._reload_config()
            self._update_tray_icon()
            self._poll_event.set()

        def _open_logs(_icon: pystray.Icon, _item: pystray.MenuItem) -> None:
            try:
                if sys.platform == "win32":
                    subprocess.Popen(["notepad", str(LOG_PATH)])
                else:
                    subprocess.Popen([os.environ.get("EDITOR", "nano"), str(LOG_PATH)])
            except OSError as exc:
                logger.error("Failed to open log file: %s", exc)

        def _quit(_icon: pystray.Icon, _item: pystray.MenuItem) -> None:
            logger.info("Quit requested from tray menu.")
            self._stop_event.set()
            _icon.stop()

        # --- Repo items ---
        repo_items: List[pystray.MenuItem] = []
        if self._repo_statuses:
            for repo, conclusion in sorted(self._repo_statuses.items()):
                dot = _DOT_GRAY
                if conclusion == "success":
                    dot = _DOT_GREEN
                elif conclusion in ("failure", "timed_out", "action_required"):
                    dot = _DOT_RED

                repo_name = repo.split("/")[-1] if "/" in repo else repo
                label = f"{dot}  {repo_name}"

                # Capture url in closure properly
                run_url = self._last_run_urls.get(repo, "https://github.com")

                def _make_open_repo(url: str):
                    def _open(_icon: pystray.Icon, _item: pystray.MenuItem) -> None:
                        webbrowser.open(url)
                    return _open

                repo_items.append(
                    pystray.MenuItem(label, _make_open_repo(run_url))
                )
        else:
            repo_items.append(
                pystray.MenuItem("(no repos configured)", None, enabled=False)
            )

        return pystray.Menu(
            pystray.MenuItem("GitHub Actions Monitor", None, enabled=False),
            pystray.Menu.SEPARATOR,
            *repo_items,
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Refresh Now", _refresh_now),
            pystray.MenuItem("Open GitHub Actions", _open_github),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Edit Config", _edit_config),
            pystray.MenuItem("Refresh Config", _refresh_config),
            pystray.MenuItem("Logs", _open_logs),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", _quit),
        )

    def _update_tray_icon(self) -> None:
        """Regenerate the tray icon image and menu based on current status."""
        if self._tray is None:
            return

        # Pick badge colour from overall health
        conclusions = list(self._repo_statuses.values())
        if any(c in ("failure", "timed_out", "action_required") for c in conclusions):
            badge = "#d73a49"  # red
        elif all(c == "success" for c in conclusions) and conclusions:
            badge = "#28a745"  # green
        else:
            badge = None

        self._tray.icon = _generate_icon(badge_color=badge)
        self._tray.menu = self._build_menu()

    def _update_tooltip(self, text: str) -> None:
        """Set the tray icon tooltip text (visible on hover)."""
        if self._tray is not None:
            try:
                self._tray.title = text
            except Exception as exc:
                logger.debug("Could not update tray tooltip: %s", exc)

    # ------------------------------------------------------------------
    # Polling + transition detection
    # ------------------------------------------------------------------

    def _should_notify(self, event: str) -> bool:
        """
        Return True if a run triggered by *event* should produce notifications.

        Controlled by the ``trigger_filter`` config value.
        """
        if self._config is None:
            return False
        tf = self._config.trigger_filter
        if tf == "all":
            return True
        allowed = {e.strip() for e in tf.split(",")}
        return event in allowed

    def _process_run(self, run: dict, repo: str, is_startup: bool) -> None:
        """
        Inspect a single run dict and fire notifications for state transitions.

        Parameters
        ----------
        run:
            Raw GitHub API workflow run object.
        repo:
            Full ``owner/repo`` string.
        is_startup:
            When True, runs are recorded as seen without notifying (absorb
            existing activity on launch).
        """
        assert self._state_manager is not None
        assert self._config is not None

        run_id: int = run["id"]
        status: str = run.get("status", "")
        conclusion: Optional[str] = run.get("conclusion")
        html_url: str = run.get("html_url", "")
        event: str = run.get("event", "")
        created_at: str = run.get("created_at", "")
        updated_at: str = run.get("updated_at", "")
        run_started_at: Optional[str] = run.get("run_started_at")
        workflow_name: str = (
            run.get("name")
            or run.get("display_title")
            or run.get("workflow_id", "")
            or "workflow"
        )

        new_state = RunState(
            run_id=run_id,
            repo=repo,
            workflow_name=str(workflow_name),
            status=status,
            conclusion=conclusion,
            html_url=html_url,
            event=event,
            created_at=created_at,
            updated_at=updated_at,
            run_started_at=run_started_at,
        )

        # Track last run URL per repo for tray menu
        self._last_run_urls[repo] = html_url

        if is_startup:
            self._state_manager.mark_seen_no_notify(new_state)
            return

        prev = self._state_manager.get(run_id)

        if prev is None:
            # Brand-new run — notify about the start
            if self._should_notify(event):
                if status in ("queued", "in_progress"):
                    self._notifier.notify_started(repo, str(workflow_name), html_url)
                elif status == "completed":
                    # Run was already completed before we saw it (fast runs)
                    self._dispatch_completion(repo, str(workflow_name), html_url, conclusion, run_started_at, updated_at)
            self._state_manager.update(new_state)
            return

        # Existing run — check for completion transition
        if prev.status != "completed" and status == "completed":
            if self._should_notify(event):
                self._dispatch_completion(repo, str(workflow_name), html_url, conclusion, run_started_at, updated_at)

        self._state_manager.update(new_state)

    def _dispatch_completion(
        self,
        repo: str,
        workflow: str,
        url: str,
        conclusion: Optional[str],
        run_started_at: Optional[str],
        updated_at: str,
    ) -> None:
        """Fire the appropriate completion notification based on *conclusion*."""
        if conclusion == "success":
            duration = _format_duration(run_started_at, updated_at)
            self._notifier.notify_succeeded(repo, workflow, url, duration)
            self._repo_statuses[repo] = "success"
        elif conclusion in ("failure", "timed_out", "action_required"):
            self._notifier.notify_failed(repo, workflow, url)
            self._repo_statuses[repo] = "failure"
        else:
            # cancelled, skipped, stale, neutral, …
            self._notifier.notify_cancelled(repo, workflow, url)
            # Don't change the repo's success/failure badge for cancellations

    def poll_once(self, is_startup: bool = False) -> None:
        """
        Fetch the latest workflow runs for all configured repos and
        dispatch notifications for any state transitions detected.

        Parameters
        ----------
        is_startup:
            When True the lookback window is applied and runs inside it
            are recorded without notifying (avoids notification floods).
        """
        if self._config is None or self._api is None:
            logger.debug("Polling skipped: no valid config/API client.")
            return

        if self._auth_failed:
            logger.debug("Polling skipped: authentication failed.")
            return

        if self._rate_limit_until is not None:
            if datetime.now(tz=timezone.utc) < self._rate_limit_until:
                logger.debug("Polling skipped: rate limit active until %s.", self._rate_limit_until)
                return
            else:
                logger.info("Rate limit window expired; resuming polling.")
                self._rate_limit_until = None
                self._update_tooltip("GitHub Actions Monitor")

        lookback = self._config.lookback_minutes if is_startup else None

        try:
            repos = self._api.get_repos(self._config.watch)
        except AuthError:
            logger.error("Authentication error fetching repo list.")
            self._auth_failed = True
            self._notifier.notify_auth_error()
            return
        except RateLimitError as exc:
            self._handle_rate_limit(exc)
            return
        except GitHubAPIError as exc:
            logger.warning("Could not fetch repo list: %s", exc)
            return

        if not repos:
            logger.warning("No repositories resolved from watch config: %r", self._config.watch)
            return

        for repo in repos:
            if self._stop_event.is_set():
                break
            try:
                runs = self._api.get_workflow_runs(repo, lookback_minutes=lookback)
            except AuthError:
                logger.error("Authentication error fetching runs for %s.", repo)
                self._auth_failed = True
                self._notifier.notify_auth_error()
                return
            except RateLimitError as exc:
                self._handle_rate_limit(exc)
                return
            except GitHubAPIError as exc:
                logger.warning("Error fetching runs for %s: %s", repo, exc)
                continue

            for run in runs:
                self._process_run(run, repo, is_startup=is_startup)

            # Initialise repo status badge if not yet tracked
            if repo not in self._repo_statuses:
                self._repo_statuses[repo] = None

        if is_startup:
            # Persist the seeded run states in one go
            assert self._state_manager is not None
            self._state_manager.save()
            logger.info("Startup seed complete for %d repo(s).", len(repos))

        self._update_tray_icon()

    def _handle_rate_limit(self, exc: RateLimitError) -> None:
        """Record the rate limit window and surface it to the user."""
        self._rate_limit_until = exc.reset_at
        reset_str = (
            exc.reset_at.astimezone().strftime("%H:%M:%S")
            if exc.reset_at
            else "unknown time"
        )
        logger.warning("Rate limited. Polling paused until %s.", reset_str)
        self._notifier.notify_rate_limited(reset_str)
        self._update_tooltip(f"Rate limited until {reset_str}")

    # ------------------------------------------------------------------
    # Background polling thread
    # ------------------------------------------------------------------

    def _polling_loop(self) -> None:
        """
        Background daemon thread: runs :meth:`poll_once` every
        ``poll_interval`` seconds, or immediately when :attr:`_poll_event`
        is set (e.g. by "Refresh Now" in the menu).
        """
        logger.info("Polling thread started.")
        assert self._config is not None

        # Startup seed — absorb existing runs without notifying
        try:
            self.poll_once(is_startup=True)
        except Exception as exc:  # broad catch to keep thread alive
            logger.exception("Unexpected error during startup poll: %s", exc)

        while not self._stop_event.is_set():
            interval = self._config.poll_interval if self._config else 30
            # Wait for either the interval or a manual refresh trigger
            triggered = self._poll_event.wait(timeout=interval)
            if triggered:
                self._poll_event.clear()
            if self._stop_event.is_set():
                break
            # If the config was reloaded since the last poll, treat this as a
            # startup seed: absorb current run states without notifying.
            is_startup = self._pending_startup_seed
            self._pending_startup_seed = False
            try:
                self.poll_once(is_startup=is_startup)
            except Exception as exc:
                logger.exception("Unexpected error during poll: %s", exc)

        logger.info("Polling thread exiting.")

    def _start_polling_thread(self) -> threading.Thread:
        """Start and return the background polling daemon thread."""
        thread = threading.Thread(
            target=self._polling_loop,
            name="polling-loop",
            daemon=True,
        )
        thread.start()
        return thread

    # ------------------------------------------------------------------
    # Run
    # ------------------------------------------------------------------

    def run(self) -> None:
        """
        Start the system tray icon and the background polling thread.

        Blocks until the user clicks Quit.
        """
        logger.info("GitHub Actions Monitor starting up (data dir: %s).", DATA_DIR)

        # Show first-run notification after the tray icon is ready
        # (scheduled as a one-shot daemon thread so it fires after setup)
        def _deferred_first_run_notify():
            time.sleep(1.5)
            if self._first_run:
                self._notifier.notify_config_missing(str(self._config_manager.path))
            elif self._config and self._config.is_placeholder:
                self._notifier.notify_config_missing(str(self._config_manager.path))

        threading.Thread(target=_deferred_first_run_notify, daemon=True).start()

        initial_icon = _generate_icon()
        self._tray = pystray.Icon(
            name="github_actions_monitor",
            icon=initial_icon,
            title="GitHub Actions Monitor",
            menu=self._build_menu(),
        )

        self._start_polling_thread()

        try:
            self._tray.run()
        except Exception as exc:
            logger.exception("Tray icon crashed: %s", exc)
        finally:
            self._stop_event.set()
            logger.info("GitHub Actions Monitor shut down.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Application entry point."""
    try:
        app = MonitorApp()
        app.run()
    except Exception as exc:
        logging.getLogger(__name__).exception("Fatal error: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
