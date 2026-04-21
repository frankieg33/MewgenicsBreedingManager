"""QuickRoomRefreshWorker: fast room assignment refresh without full parse."""
import logging
import sqlite3

from PySide6.QtCore import QThread, Signal

from save_parser import _get_house_info, _get_adventure_keys
from mewgenics.utils.retry import retry_transient
from mewgenics.utils.save_snapshot import snapshot


logger = logging.getLogger("mewgenics.room_refresh")


class QuickRoomRefreshWorker(QThread):
    """Fast path: re-reads only house_state/adventure_state to update room assignments.

    If the set of cat keys in the DB has changed (birth/death), emits needs_full_reload
    instead so the caller can fall back to a full SaveLoadWorker parse.

    A *generation* token is passed through and re-emitted alongside the
    result.  MainWindow compares that token against its current generation
    to discard signals from superseded workers — preventing a stale
    room_patch from clobbering freshly-loaded state.
    """
    # (generation, patch dict)  db_key → (room, status)
    room_patch = Signal(int, object)
    needs_full_reload = Signal(int)

    def __init__(self, path: str, expected_keys: set, generation: int = 0, parent=None):
        super().__init__(parent)
        self._path = path
        self._expected_keys = expected_keys
        self._generation = generation

    def _read_state_from(self, src_path: str):
        """Open `src_path` read-only and return (live_keys, house, adv).

        Raises the underlying sqlite3 exception if the file can't be opened
        or queried — retry_transient handles the partial-write window.
        """
        conn = None
        try:
            conn = sqlite3.connect(f"file:{src_path}?mode=ro", uri=True)
            live_keys = {row[0] for row in conn.execute("SELECT key FROM cats").fetchall()}
            house = _get_house_info(conn)
            adv = _get_adventure_keys(conn)
            return live_keys, house, adv
        finally:
            if conn is not None:
                # close() can raise if the connection is already broken
                # by the error we're unwinding — swallow so we don't mask
                # the real exception.
                try:
                    conn.close()
                except Exception:  # noqa: BLE001
                    pass

    def run(self):
        try:
            if self.isInterruptionRequested():
                return
            # Snapshot the save to a temp copy first to avoid co-accessing
            # the live file while the game is running (issue #94).
            with snapshot(self._path) as snap_path:
                live_keys, house, adv = retry_transient(
                    lambda: self._read_state_from(snap_path)
                )
            if self.isInterruptionRequested():
                return
            if live_keys != self._expected_keys:
                self.needs_full_reload.emit(self._generation)
                return
            patch: dict[int, tuple[str, str]] = {}
            for key in live_keys:
                if key in adv:
                    patch[key] = ("Adventure", "Adventure")
                elif key in house:
                    patch[key] = (house[key], "In House")
                else:
                    patch[key] = ("", "Gone")
            if self.isInterruptionRequested():
                return
            self.room_patch.emit(self._generation, patch)
        except Exception:
            logger.exception("quick room refresh failed; falling back to full reload")
            self.needs_full_reload.emit(self._generation)
