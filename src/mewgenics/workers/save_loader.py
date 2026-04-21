"""SaveLoadWorker: parses a save file off the main thread."""
import logging
import os
import time

from PySide6.QtCore import QThread, Signal

from save_parser import parse_save
from mewgenics.utils.cat_persistence import (
    _load_blacklist, _load_must_breed, _load_pinned, _load_tags,
    _load_not_adventured,
)
from mewgenics.utils.calibration import _load_gender_overrides, _apply_calibration
from mewgenics.utils.retry import retry_transient, TRANSIENT_EXCEPTIONS
from mewgenics.utils.save_snapshot import snapshot


logger = logging.getLogger("mewgenics.save_loader")


class SaveLoadWorker(QThread):
    """Parses a save file off the main thread so the UI stays responsive."""
    status = Signal(str)  # status text updates
    finished_load = Signal(object)  # emits dict with parsed results
    # (error_repr, is_transient) — is_transient distinguishes partial-write
    # races (worth a self-heal retry) from real errors (don't retry-loop).
    failed = Signal(str, bool)

    def __init__(self, path: str, parent=None):
        super().__init__(parent)
        self._path = path

    def run(self):
        try:
            if self.isInterruptionRequested():
                return
            try:
                live_size = os.path.getsize(self._path)
                live_mtime = os.path.getmtime(self._path)
            except OSError as exc:
                logger.warning("stat failed on %s: %s", self._path, exc)
                live_size = -1
                live_mtime = 0.0
            logger.info(
                "save load start path=%s size=%d mtime=%.3f",
                self._path, live_size, live_mtime,
            )
            self.status.emit("Snapshotting save file…")
            # Snapshot to temp so we never hold a handle to the live save
            # while the game is running (issue #94). retry_transient
            # handles the atomic-rename window when the game writes
            # mid-copy.
            with snapshot(self._path) as snapshot_path:
                if self.isInterruptionRequested():
                    return
                self.status.emit("Parsing save file…")
                t_parse = time.monotonic()
                save = retry_transient(lambda: parse_save(snapshot_path))
                parse_s = time.monotonic() - t_parse
                logger.info("save parse ok parse_s=%.3f", parse_s)
                if self.isInterruptionRequested():
                    return
                cats, errors, unlocked_house_rooms = save
                logger.info(
                    "save load summary cats=%d errors=%d",
                    len(cats), len(errors),
                )
                self.status.emit("Loading blacklist & overrides…")
                # Sidecar loads key off the *live* save path, not the snapshot.
                _load_blacklist(self._path, cats)
                _load_must_breed(self._path, cats)
                _load_pinned(self._path, cats)
                _load_tags(self._path, cats)
                _load_not_adventured(self._path, cats)
                applied_overrides, override_rows = _load_gender_overrides(self._path, cats)
                cal_explicit, cal_token, cal_rows = _apply_calibration(self._path, cats)
                if self.isInterruptionRequested():
                    return
                self.finished_load.emit({
                    "cats": cats,
                    "errors": errors,
                    "unlocked_house_rooms": unlocked_house_rooms,
                    "accessible_cats": save.accessible_cats,
                    "furniture": save.furniture,
                    "furniture_by_room": save.furniture_by_room,
                    "pedigree_coi_memos": save.pedigree_coi_memos,
                    "applied_overrides": applied_overrides,
                    "override_rows": override_rows,
                    "cal_explicit": cal_explicit,
                    "cal_token": cal_token,
                    "cal_rows": cal_rows,
                })
        except Exception as exc:  # noqa: BLE001 — last line of defence before QThread dies silently
            # Without this, an unhandled exception would kill the QThread,
            # `finished_load` would never fire, the loading overlay would
            # stay up, and `_save_load_worker` on MainWindow would remain
            # non-None — so every subsequent file-change event would
            # cascade into the old terminate() path.
            logger.exception("save load failed for %s", self._path)
            self.failed.emit(repr(exc), isinstance(exc, TRANSIENT_EXCEPTIONS))
