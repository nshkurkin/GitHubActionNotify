"""
state.py — Run state tracking and seen_runs.json persistence.

Keeps an in-memory mapping of run_id → RunState so the polling loop can
detect transitions without re-notifying on every poll.  The seen set is
persisted to disk so restarts don't flood the user with stale notifications.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger(__name__)


@dataclass
class RunState:
    """Snapshot of a single GitHub Actions workflow run."""

    run_id: int
    repo: str
    workflow_name: str
    status: str          # queued | in_progress | completed
    conclusion: Optional[str]  # success | failure | cancelled | skipped | None
    html_url: str
    event: str           # push | pull_request | workflow_dispatch | …
    created_at: str      # ISO-8601 string
    updated_at: str      # ISO-8601 string
    run_started_at: Optional[str]  # ISO-8601 string or None


class StateManager:
    """
    Manages the lifecycle of known workflow run states.

    Parameters
    ----------
    data_dir:
        Directory where ``seen_runs.json`` is stored (typically
        ``%LOCALAPPDATA%/GitHubActionsMonitor``).
    """

    _FILENAME = "seen_runs.json"

    def __init__(self, data_dir: Path) -> None:
        self._data_dir = data_dir
        self._path = data_dir / self._FILENAME
        self._runs: Dict[int, RunState] = {}
        self._dirty = False
        self._load()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load(self) -> None:
        """Load persisted run states from disk; silently handle missing/corrupt files."""
        if not self._path.exists():
            logger.debug("No seen_runs.json found — starting fresh.")
            return
        try:
            with open(self._path, "r", encoding="utf-8") as fh:
                raw: dict = json.load(fh)
            for key, val in raw.items():
                try:
                    run_id = int(key)
                    self._runs[run_id] = RunState(**val)
                except (TypeError, KeyError) as exc:
                    logger.warning("Skipping malformed run entry %s: %s", key, exc)
            logger.info("Loaded %d seen run(s) from disk.", len(self._runs))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Could not load seen_runs.json: %s", exc)

    def save(self) -> None:
        """Persist all known run states to disk (no-op when nothing has changed)."""
        if not self._dirty:
            return
        try:
            self._data_dir.mkdir(parents=True, exist_ok=True)
            payload = {str(run_id): asdict(state) for run_id, state in self._runs.items()}
            with open(self._path, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2)
            self._dirty = False
        except OSError as exc:
            logger.error("Failed to save seen_runs.json: %s", exc)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get(self, run_id: int) -> Optional[RunState]:
        """Return the last known RunState for *run_id*, or ``None`` if unseen."""
        return self._runs.get(run_id)

    def update(self, state: RunState) -> None:
        """
        Record *state* as the latest known state for its run.

        Call this after a notification has been fired so the transition is not
        repeated on the next poll.  Persistence is deferred: call :meth:`save`
        once at the end of each poll cycle rather than on every update.
        """
        self._runs[state.run_id] = state
        self._dirty = True

    def mark_seen_no_notify(self, state: RunState) -> None:
        """
        Mark a run as already seen **without** triggering a notification.

        Used during startup to absorb runs inside the lookback window so the
        user doesn't receive a flood of notifications for activity that
        happened before the app launched.
        """
        self._runs[state.run_id] = state
        self._dirty = True

    def is_seen(self, run_id: int) -> bool:
        """Return ``True`` if *run_id* has ever been recorded."""
        return run_id in self._runs

    def all_runs(self) -> Dict[int, RunState]:
        """Return a copy of the full in-memory run map."""
        return dict(self._runs)
