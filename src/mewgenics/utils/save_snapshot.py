"""Snapshot the live .sav to a temp file before reading.

The Mewgenics game rewrites the save while it runs; our readers used to
open the live file directly which contributed to shared-access crashes
of the game on Windows (issue #94). Copying the file first removes all
co-access between the game and this app.
"""
import logging
import os
import shutil
import tempfile
import time
from contextlib import contextmanager

logger = logging.getLogger("mewgenics.snapshot")

# WAL / SHM / journal sidecars SQLite may produce. Present mostly for
# Mewgenics' main save; harmless when absent.
_SIDECAR_SUFFIXES = ("-wal", "-shm", "-journal")


def copy_save(path: str, tmp_dir: str) -> tuple[str, int]:
    """Copy `path` (and any SQLite sidecars) into `tmp_dir`.

    Returns (destination_path, bytes_copied).
    """
    base = os.path.basename(path)
    dst = os.path.join(tmp_dir, base)
    shutil.copy2(path, dst)
    bytes_copied = os.path.getsize(dst)
    for suffix in _SIDECAR_SUFFIXES:
        src_side = path + suffix
        if os.path.exists(src_side):
            try:
                shutil.copy2(src_side, dst + suffix)
                bytes_copied += os.path.getsize(dst + suffix)
            except OSError as exc:
                logger.warning("could not copy sidecar %s: %s", src_side, exc)
    return dst, bytes_copied


@contextmanager
def snapshot(path: str):
    """Context manager yielding a snapshot path; removes the temp dir on exit.

    Logs size + duration at INFO so uploaded user logs contain a concrete
    trace of every save read — essential for diagnosing issue #94-style
    reports where the root cause isn't reproducible locally.
    """
    tmp_dir = tempfile.mkdtemp(prefix="mew_save_")
    try:
        t0 = time.monotonic()
        dst, bytes_copied = copy_save(path, tmp_dir)
        copy_s = time.monotonic() - t0
        logger.info(
            "save snapshot path=%s bytes=%d copy_s=%.3f",
            path, bytes_copied, copy_s,
        )
        yield dst
    finally:
        try:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        except Exception as exc:  # noqa: BLE001 — best-effort cleanup
            logger.warning("snapshot cleanup failed for %s: %s", tmp_dir, exc)
