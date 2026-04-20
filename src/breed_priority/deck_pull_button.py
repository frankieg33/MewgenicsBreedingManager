"""Breed Priority — temporary Steam Deck save pull button helper."""

from typing import Callable, Optional

from PySide6.QtWidgets import QPushButton

PULL_DECK_SAVE_LABEL = "Pull Deck Save (Temp)"


class PullDeckSaveButton(QPushButton):
    def __init__(self, style: str, parent: Optional[object] = None):
        super().__init__(PULL_DECK_SAVE_LABEL, parent)
        self._callback: Optional[Callable[[], None]] = None
        self.setStyleSheet(style)
        self.setFixedHeight(22)
        self.setToolTip(
            "Temporary helper: copy Steam Deck save over SSH and reload current save."
        )
        self.clicked.connect(self._on_clicked)

    def set_callback(self, callback: Optional[Callable[[], None]]):
        self._callback = callback

    def set_busy(self, busy: bool):
        if busy:
            self.setEnabled(False)
            self.setText("Pulling...")
        else:
            self.setEnabled(True)
            self.setText(PULL_DECK_SAVE_LABEL)

    def _on_clicked(self):
        if callable(self._callback):
            self._callback()


def create_pull_deck_save_button(
    *,
    style: str,
    parent: Optional[object] = None,
) -> PullDeckSaveButton:
    """Factory for the temporary Steam Deck save pull button."""
    return PullDeckSaveButton(style, parent=parent)
