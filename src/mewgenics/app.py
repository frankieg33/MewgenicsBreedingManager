"""Application entry point: QApplication setup, save selector, and MainWindow launch."""
import sys
import os
import logging
from typing import Optional

from PySide6.QtWidgets import (
    QApplication, QDialog, QMessageBox, QWidget,
    QVBoxLayout, QLabel, QProgressBar,
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette, QFont

from mewgenics.utils.paths import APP_VERSION
from mewgenics.utils.config import _saved_default_save, _saved_last_save, find_save_files
from mewgenics.utils.game_data import _GPAK_PATH
from mewgenics.utils.shape_extractor import ensure_defined_shapes
from mewgenics.utils.logging_setup import setup_logging, install_excepthooks
from mewgenics.dialogs import SaveSelectorDialog
from mewgenics.main_window import MainWindow, _ensure_gpak_path_interactive

logger = logging.getLogger("mewgenics")


class _SplashScreen(QWidget):
    """Dark-themed startup splash — shown while the app initialises."""

    def __init__(self):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground, False)
        self.setFixedSize(420, 200)
        self.setStyleSheet("background:#0a0a18;")

        vb = QVBoxLayout(self)
        vb.setAlignment(Qt.AlignCenter)
        vb.setSpacing(6)

        title = QLabel("Mewgenics Breeding Manager")
        title.setAlignment(Qt.AlignCenter)
        title_font = QFont()
        title_font.setPointSize(16)
        title_font.setBold(True)
        title.setFont(title_font)
        title.setStyleSheet("color:#d0d0e8;")
        vb.addWidget(title)

        ver = QLabel(f"v{APP_VERSION}")
        ver.setAlignment(Qt.AlignCenter)
        ver.setStyleSheet("color:#556; font-size:11px;")
        vb.addWidget(ver)

        vb.addSpacing(12)

        self._status = QLabel("Starting...")
        self._status.setAlignment(Qt.AlignCenter)
        self._status.setStyleSheet("color:#889; font-size:11px;")
        vb.addWidget(self._status)

        bar = QProgressBar()
        bar.setFixedWidth(300)
        bar.setFixedHeight(6)
        bar.setRange(0, 0)  # indeterminate pulse
        bar.setTextVisible(False)
        bar.setStyleSheet(
            "QProgressBar { background:#1a1a32; border:1px solid #2a2a4a; border-radius:3px; }"
            "QProgressBar::chunk { background:#3f8f72; border-radius:2px; }"
        )
        vb.addWidget(bar, 0, Qt.AlignCenter)

        # Centre on screen
        screen = QApplication.primaryScreen()
        if screen:
            geo = screen.availableGeometry()
            self.move(
                geo.x() + (geo.width() - self.width()) // 2,
                geo.y() + (geo.height() - self.height()) // 2,
            )

    def set_status(self, text: str):
        self._status.setText(text)
        QApplication.processEvents()


def main():
    # Configure rotating file logging and install global exception hooks.
    # Must happen before we touch Qt so crashes during QApplication setup
    # are still captured.
    setup_logging()

    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    # Install crash hooks now that QApplication exists — the main-thread
    # hook needs it to show the crash dialog. The getter returns the
    # MainWindow once it has been created, or None before that.
    _main_window_ref: dict[str, object] = {"win": None}
    install_excepthooks(dialog_parent_getter=lambda: _main_window_ref.get("win"))

    pal = QPalette()
    pal.setColor(QPalette.Window,          QColor(13,  13,  28))
    pal.setColor(QPalette.WindowText,      QColor(220, 220, 230))
    pal.setColor(QPalette.Base,            QColor(18,  18,  36))
    pal.setColor(QPalette.AlternateBase,   QColor(20,  20,  40))
    pal.setColor(QPalette.Text,            QColor(220, 220, 230))
    pal.setColor(QPalette.Button,          QColor(22,  22,  46))
    pal.setColor(QPalette.ButtonText,      QColor(200, 200, 210))
    pal.setColor(QPalette.Highlight,       QColor(30,  48, 100))
    pal.setColor(QPalette.HighlightedText, QColor(255, 255, 255))
    pal.setColor(QPalette.ToolTipBase,     QColor(20,  20,  40))
    pal.setColor(QPalette.ToolTipText,     QColor(220, 220, 230))
    app.setPalette(pal)

    # Keep Qt initialized before showing dialogs on some Linux setups.
    from PySide6 import QtWidgets
    QtWidgets.QMessageBox()

    # Show splash screen immediately so the user sees something.
    splash = _SplashScreen()
    splash.show()
    QApplication.processEvents()

    if not _GPAK_PATH:
        splash.hide()
        QMessageBox.information(
            None,
            "Locate Mewgenics",
            "Ability and mutation descriptions need the game's resources.gpak.\n"
            "If the app needs you to browse for it, the chooser will start from your configured save root first.",
        )
        _ensure_gpak_path_interactive()
        splash.show()
        QApplication.processEvents()

    # Extract DefinedShape PNGs from the bundled ZIP if the cache is empty.
    splash.set_status("Loading cat assets...")

    def _on_shapes_progress(current, total):
        splash.set_status(f"Extracting cat sprites... {current}/{total}")

    ensure_defined_shapes(progress_callback=_on_shapes_progress)

    # Prefer explicit default save, then most recently loaded save, then show selector.
    initial_save: Optional[str] = _saved_default_save() or _saved_last_save()

    if initial_save is None:
        splash.hide()
        saves = find_save_files()
        dlg = SaveSelectorDialog(saves)
        if dlg.exec() == QDialog.Accepted:
            initial_save = dlg.selected_path
        else:
            return 0

    splash.set_status("Building interface...")
    win = MainWindow(initial_save=initial_save, use_saved_default=False)
    _main_window_ref["win"] = win

    # Show the main window behind the splash. The splash stays on top
    # until the save finishes loading so the user never sees a blank window.
    win.show()

    if initial_save:
        splash.set_status("Loading save file...")
        splash.raise_()  # keep above the main window

        def _dismiss_splash():
            splash.close()
        win.startup_save_load_finished.connect(_dismiss_splash)
    else:
        splash.close()

    return app.exec()
