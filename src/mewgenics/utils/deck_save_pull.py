"""Temporary Steam Deck save-pull helpers for the Breed Priority button."""

from typing import Callable, Optional

from PySide6.QtCore import QObject, QProcess, Signal

TEMP_DECK_SCP_HOST = "deck@172.28.217.240"
TEMP_DECK_SAVE_PATH = (
    "/home/deck/.local/share/Steam/steamapps/compatdata/686060/pfx/drive_c/users/"
    "steamuser/AppData/Roaming/Glaiel Games/Mewgenics/76561197990532520/saves/"
    "steamcampaign01.sav"
)


def deck_pull_source() -> tuple[str, str]:
    """Return temporary Steam Deck source host/path pair for save copy."""
    return TEMP_DECK_SCP_HOST, TEMP_DECK_SAVE_PATH


class DeckSavePullController(QObject):
    """Own the temporary scp process used to copy a Deck save and trigger reload."""
    started = Signal()
    finished = Signal()
    message = Signal(str)
    reloadRequested = Signal()

    def __init__(
        self,
        parent: Optional[QObject],
        *,
        source_host: str,
        source_path: str,
        current_save_provider: Callable[[], Optional[str]],
    ):
        super().__init__(parent)
        self._source_host = source_host
        self._source_path = source_path
        self._current_save_provider = current_save_provider
        self._process: Optional[QProcess] = None

    def pull_and_reload(self):
        destination_path = self._current_save_provider()
        if not destination_path:
            self.message.emit("No current save loaded to overwrite.")
            return
        if self._process is not None:
            self.message.emit("Deck save pull already running...")
            return
        self._start_pull(destination_path)

    def _start_pull(self, destination_path: str):
        proc = QProcess(self)
        proc.setProgram("scp")
        proc.setArguments([f"{self._source_host}:{self._source_path}", destination_path])
        proc.finished.connect(self._on_finished)
        proc.errorOccurred.connect(self._on_error)
        self._process = proc
        self.started.emit()
        self.message.emit("Pulling Steam Deck save...")
        proc.start()

    def _on_finished(self, exit_code: int, exit_status):
        _ = exit_status
        proc = self._process
        self._process = None
        if exit_code == 0:
            self.message.emit("Deck save copied. Reloading...")
            self.reloadRequested.emit()
            self.finished.emit()
            return
        err = self._read_stderr(proc)
        if err:
            self.message.emit(f"Deck save pull failed: {err}")
        else:
            self.message.emit(f"Deck save pull failed (exit {exit_code}).")
        self.finished.emit()

    def _on_error(self, error):
        _ = error
        proc = self._process
        self._process = None
        err = self._read_stderr(proc)
        if err:
            self.message.emit(f"Deck save pull error: {err}")
        else:
            self.message.emit("Deck save pull error. Ensure SSH key auth is set up.")
        self.finished.emit()

    @staticmethod
    def _read_stderr(proc: Optional[QProcess]) -> str:
        if proc is None:
            return ""
        return bytes(proc.readAllStandardError()).decode("utf-8", errors="replace").strip()


def create_temp_deck_save_puller(
    *,
    parent: Optional[QObject],
    current_save_provider: Callable[[], Optional[str]],
) -> DeckSavePullController:
    """Factory that returns a pull controller wired to the current temp source."""
    source_host, source_path = deck_pull_source()
    return DeckSavePullController(
        parent,
        source_host=source_host,
        source_path=source_path,
        current_save_provider=current_save_provider,
    )

