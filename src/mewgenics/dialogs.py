"""Dialog windows: TagManager, ThresholdPreferences, OptimizerSearchSettings, SaveSelector."""
from __future__ import annotations

import os
import datetime
import platform
import sys
from pathlib import Path
from typing import Optional

from PySide6.QtWidgets import (
    QWidget, QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QScrollArea, QFrame, QGridLayout, QSpinBox, QDoubleSpinBox, QCheckBox,
    QListWidget, QListWidgetItem, QFileDialog, QGroupBox,
    QStackedWidget, QTextBrowser, QDialogButtonBox,
)
from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QColor, QPixmap

from mewgenics.utils.localization import _tr
from mewgenics.utils.config import (
    _save_root_dir,
    _remember_tag_color_history,
    _saved_tag_color_history,
)
from mewgenics.utils.paths import APP_VERSION
from mewgenics.utils.tags import (
    TAG_PRESET_COLORS, _TAG_DEFS, _save_tag_definitions, _next_tag_id,
    _import_tag_image,
)
from mewgenics.utils.thresholds import (
    _normalize_threshold_preferences,
    _load_threshold_preferences,
    _effective_thresholds_for_cats,
)
from mewgenics.utils.optimizer_settings import (
    _normalize_optimizer_search_settings,
    _load_optimizer_search_settings,
    _OPTIMIZER_SEARCH_DEFAULTS,
)

from save_parser import Cat


# ---------------------------------------------------------------------------
# TagManagerDialog
# ---------------------------------------------------------------------------

class TagColorDialog(QDialog):
    """Pick a tag color using either hex or RGB inputs."""

    def __init__(self, parent=None, initial_color: str = "#555555", title: str = "Tag Color"):
        super().__init__(parent)
        self.setModal(True)
        self.setWindowTitle(title)
        self.setMinimumWidth(460)
        self.setStyleSheet(
            "QDialog { background:#1a1a32; color:#ddd; }"
            "QLabel { color:#ddd; }"
            "QLineEdit { background:#101024; color:#ddd; border:1px solid #2a2a4a;"
            " padding:4px 8px; border-radius:4px; }"
            "QSpinBox { background:#101024; color:#ddd; border:1px solid #2a2a4a;"
            " padding:3px 6px; border-radius:4px; }"
            "QGroupBox { color:#f1f1f9; border:1px solid #34345a; border-radius:6px;"
            " margin-top:10px; padding-top:10px; }"
            "QGroupBox::title { subcontrol-origin: margin; left:10px; padding:0 4px; }"
            "QDialogButtonBox QPushButton { background:#252545; color:#ddd; border:1px solid #3d3d68;"
            " border-radius:4px; padding:6px 12px; }"
            "QDialogButtonBox QPushButton:hover { background:#34345f; }"
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        intro = QLabel("Set a tag color using RGB or hex. The preview updates live, and your recent colors stay available below.")
        intro.setWordWrap(True)
        intro.setStyleSheet("color:#aeb0d2;")
        root.addWidget(intro)

        self._color = QColor(initial_color)
        if not self._color.isValid():
            self._color = QColor("#555555")
        self._updating = False
        self._palette_buttons: list[tuple[QPushButton, str]] = []

        palette_group = QGroupBox("Palette")
        palette_layout = QVBoxLayout(palette_group)
        palette_layout.setContentsMargins(10, 12, 10, 10)
        palette_layout.setSpacing(10)

        recent_label = QLabel("Recent Colors")
        recent_label.setStyleSheet("color:#9aa0c7; font-size:11px; font-weight:bold;")
        palette_layout.addWidget(recent_label)

        recent_colors = _saved_tag_color_history(limit=12)
        recent_grid = QGridLayout()
        recent_grid.setContentsMargins(0, 0, 0, 0)
        recent_grid.setHorizontalSpacing(6)
        recent_grid.setVerticalSpacing(6)
        if recent_colors:
            self._add_palette_swatches(recent_grid, recent_colors, columns=6)
            palette_layout.addLayout(recent_grid)
        else:
            recent_empty = QLabel("No saved colors yet. Confirm a color once and it will appear here.")
            recent_empty.setWordWrap(True)
            recent_empty.setStyleSheet("color:#7f84a8; font-style:italic;")
            palette_layout.addWidget(recent_empty)

        preset_label = QLabel("Preset Colors")
        preset_label.setStyleSheet("color:#9aa0c7; font-size:11px; font-weight:bold;")
        palette_layout.addWidget(preset_label)

        preset_grid = QGridLayout()
        preset_grid.setContentsMargins(0, 0, 0, 0)
        preset_grid.setHorizontalSpacing(6)
        preset_grid.setVerticalSpacing(6)
        self._add_palette_swatches(preset_grid, TAG_PRESET_COLORS, columns=4)
        palette_layout.addLayout(preset_grid)
        root.addWidget(palette_group)

        preview_group = QGroupBox("Preview")
        preview_layout = QVBoxLayout(preview_group)
        preview_layout.setContentsMargins(10, 12, 10, 10)
        preview_layout.setSpacing(8)
        self._preview_label = QLabel()
        self._preview_label.setAlignment(Qt.AlignCenter)
        self._preview_label.setMinimumHeight(56)
        self._preview_label.setWordWrap(True)
        preview_layout.addWidget(self._preview_label)
        root.addWidget(preview_group)

        form_group = QGroupBox("Color Values")
        form_layout = QGridLayout(form_group)
        form_layout.setHorizontalSpacing(10)
        form_layout.setVerticalSpacing(8)

        self._hex_input = QLineEdit()
        self._hex_input.setPlaceholderText("#RRGGBB")
        self._hex_input.textEdited.connect(self._on_hex_edited)
        form_layout.addWidget(QLabel("Hex"), 0, 0)
        form_layout.addWidget(self._hex_input, 0, 1, 1, 3)

        self._red_spin = QSpinBox()
        self._green_spin = QSpinBox()
        self._blue_spin = QSpinBox()
        for spin in (self._red_spin, self._green_spin, self._blue_spin):
            spin.setRange(0, 255)
            spin.valueChanged.connect(self._on_rgb_changed)

        form_layout.addWidget(QLabel("R"), 1, 0)
        form_layout.addWidget(self._red_spin, 1, 1)
        form_layout.addWidget(QLabel("G"), 1, 2)
        form_layout.addWidget(self._green_spin, 1, 3)
        form_layout.addWidget(QLabel("B"), 2, 0)
        form_layout.addWidget(self._blue_spin, 2, 1)

        self._status_label = QLabel("")
        self._status_label.setWordWrap(True)
        self._status_label.setStyleSheet("color:#9aa0c7;")
        form_layout.addWidget(self._status_label, 3, 0, 1, 4)

        root.addWidget(form_group)

        button_row = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        button_row.accepted.connect(self.accept)
        button_row.rejected.connect(self.reject)
        root.addWidget(button_row)

        self._set_color(self._color)

    def _make_palette_button(self, color: str) -> QPushButton:
        btn = QPushButton()
        btn.setFixedSize(24, 24)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setToolTip(color.upper())
        btn.setAccessibleName(color.upper())
        btn.setFocusPolicy(Qt.NoFocus)
        btn.clicked.connect(lambda checked=False, c=color: self._set_color(QColor(c)))
        self._palette_buttons.append((btn, color))
        return btn

    def _add_palette_swatches(self, layout: QGridLayout, colors: list[str], columns: int):
        for index, color in enumerate(colors):
            btn = self._make_palette_button(color)
            layout.addWidget(btn, index // columns, index % columns)

    def _update_palette_button_styles(self):
        current = self._color.name().lower()
        for btn, color in self._palette_buttons:
            swatch = QColor(color)
            if not swatch.isValid():
                continue
            selected = swatch.name().lower() == current
            border = "#ffffff" if selected else "#2f3254"
            width = "2px" if selected else "1px"
            btn.setStyleSheet(
                f"QPushButton {{ background:{swatch.name()}; border:{width} solid {border};"
                f" border-radius:5px; }}"
                f"QPushButton:hover {{ border-color:#ffffff; }}"
            )

    @staticmethod
    def _hex_to_color(text: str) -> QColor | None:
        cleaned = str(text or "").strip()
        if not cleaned:
            return None
        if cleaned.lower().startswith("0x"):
            cleaned = cleaned[2:]
        if cleaned.startswith("#"):
            cleaned = cleaned[1:]
        if len(cleaned) == 3:
            cleaned = "".join(ch * 2 for ch in cleaned)
        if len(cleaned) != 6:
            return None
        try:
            int(cleaned, 16)
        except ValueError:
            return None
        color = QColor(f"#{cleaned}")
        return color if color.isValid() else None

    def _set_color(self, color: QColor):
        if not color.isValid():
            return
        self._color = QColor(color)
        self._updating = True
        try:
            self._hex_input.setText(self._color.name())
            self._red_spin.setValue(self._color.red())
            self._green_spin.setValue(self._color.green())
            self._blue_spin.setValue(self._color.blue())
        finally:
            self._updating = False
        self._refresh_preview(valid=True)
        self._update_palette_button_styles()

    def _refresh_preview(self, valid: bool):
        color_name = self._color.name()
        rgb_text = f"RGB {self._color.red()}, {self._color.green()}, {self._color.blue()}"
        fg = "#111111" if self._color.lightness() >= 140 else "#f6f6f6"
        self._preview_label.setText(f"{color_name.upper()}\n{rgb_text}")
        self._preview_label.setStyleSheet(
            f"background:{color_name}; color:{fg}; border:1px solid #3d3d68; "
            "border-radius:6px; padding:12px; font-weight:bold;"
        )
        if valid:
            self._hex_input.setStyleSheet(
                "QLineEdit { background:#101024; color:#ddd; border:1px solid #2a2a4a;"
                " padding:4px 8px; border-radius:4px; }"
            )
            self._status_label.setText("Enter values in either field to update the preview.")
            self._status_label.setStyleSheet("color:#9aa0c7;")
        else:
            self._hex_input.setStyleSheet(
                "QLineEdit { background:#101024; color:#ddd; border:1px solid #7a3f3f;"
                " padding:4px 8px; border-radius:4px; }"
            )
            self._status_label.setText(
                "Hex must be a 3- or 6-digit value such as #E74C3C. The current color stays unchanged until you enter a valid value."
            )
            self._status_label.setStyleSheet("color:#f0b0b0;")

    def _on_hex_edited(self, text: str):
        if self._updating:
            return
        color = self._hex_to_color(text)
        if color is None:
            self._refresh_preview(valid=False)
            return
        self._set_color(color)

    def _on_rgb_changed(self, _value: int):
        if self._updating:
            return
        color = QColor(self._red_spin.value(), self._green_spin.value(), self._blue_spin.value())
        if color.isValid():
            self._set_color(color)

    def selected_color(self) -> QColor:
        return QColor(self._color)

    def selected_hex(self) -> str:
        return self._color.name()


class TagManagerDialog(QDialog):
    """Dialog for creating, editing, and deleting tag definitions."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Manage Tags")
        self.setMinimumWidth(620)
        self.setStyleSheet(
            "QDialog { background:#1a1a32; color:#ddd; }"
            "QLabel { color:#ddd; }"
            "QGroupBox { color:#f1f1f9; border:1px solid #34345a; border-radius:6px; margin-top:10px; padding-top:10px; }"
            "QGroupBox::title { subcontrol-origin: margin; left:10px; padding:0 4px; }"
            "QLineEdit { background:#101024; color:#ddd; border:1px solid #2a2a4a;"
            " padding:4px 8px; border-radius:4px; }"
        )
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        intro = QLabel(
            "Create and edit your tag palette. Images are copied into the app's tag asset folder and shown as previews."
        )
        intro.setWordWrap(True)
        intro.setStyleSheet("color:#aeb0d2;")
        layout.addWidget(intro)

        # Tag list area
        list_group = QGroupBox("Existing Tags")
        list_layout = QVBoxLayout(list_group)
        list_layout.setContentsMargins(10, 12, 10, 10)
        list_layout.setSpacing(8)

        self._list_widget = QWidget()
        self._list_layout = QVBoxLayout(self._list_widget)
        self._list_layout.setContentsMargins(0, 0, 0, 0)
        self._list_layout.setSpacing(6)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self._list_widget)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setMaximumHeight(300)
        scroll.setStyleSheet("QScrollArea { border:none; background:transparent; }")
        list_layout.addWidget(scroll)
        layout.addWidget(list_group)

        # Add new tag section
        add_group = QGroupBox("Create Tag")
        add_layout = QVBoxLayout(add_group)
        add_layout.setContentsMargins(10, 12, 10, 10)
        add_layout.setSpacing(8)

        name_row = QHBoxLayout()
        name_row.setSpacing(8)

        self._name_input = QLineEdit()
        self._name_input.setPlaceholderText("New tag name...")
        self._name_input.setMaxLength(20)
        name_row.addWidget(self._name_input, 1)
        add_layout.addLayout(name_row)

        color_row = QHBoxLayout()
        color_row.setSpacing(6)
        self._selected_color = TAG_PRESET_COLORS[0]
        self._color_btns = []
        for color in TAG_PRESET_COLORS:
            btn = QPushButton()
            btn.setFixedSize(22, 22)
            btn.setStyleSheet(
                f"QPushButton {{ background:{color}; border:2px solid transparent;"
                f" border-radius:11px; }}"
                f"QPushButton:hover {{ border-color:#fff; }}"
            )
            btn.clicked.connect(lambda checked=False, c=color: self._select_color(c))
            self._color_btns.append((btn, color))
            color_row.addWidget(btn)

        self._custom_color_btn = QPushButton("Custom Color")
        self._custom_color_btn.setStyleSheet(
            "QPushButton { background:#252545; color:#ddd; border:1px solid #3d3d68;"
            " border-radius:4px; padding:4px 10px; }"
            "QPushButton:hover { background:#34345f; }"
        )
        self._custom_color_btn.clicked.connect(self._pick_custom_color)
        color_row.addWidget(self._custom_color_btn)
        color_row.addStretch(1)
        add_layout.addLayout(color_row)

        image_row = QHBoxLayout()
        image_row.setSpacing(8)
        image_row.addWidget(QLabel("Image:"))
        self._image_preview = QLabel("None")
        self._image_preview.setAlignment(Qt.AlignCenter)
        self._image_preview.setFixedSize(36, 36)
        self._image_preview.setStyleSheet(
            "QLabel { background:#101024; color:#9aa0c7; border:1px solid #2a2a4a;"
            " border-radius:4px; font-size:9px; }"
        )
        image_row.addWidget(self._image_preview)
        self._image_path_label = QLabel("None")
        self._image_path_label.setStyleSheet("color:#9aa0c7;")
        self._image_path_label.setWordWrap(False)
        image_row.addWidget(self._image_path_label, 1)
        self._pick_image_btn = QPushButton("Choose Image…")
        self._pick_image_btn.setStyleSheet(
            "QPushButton { background:#252545; color:#ddd; border:1px solid #3d3d68;"
            " border-radius:4px; padding:4px 10px; }"
            "QPushButton:hover { background:#34345f; }"
        )
        self._pick_image_btn.clicked.connect(self._pick_new_tag_image)
        image_row.addWidget(self._pick_image_btn)
        self._clear_image_btn = QPushButton("Clear")
        self._clear_image_btn.setStyleSheet(
            "QPushButton { background:#2d2020; color:#f0b0b0; border:1px solid #5a3434;"
            " border-radius:4px; padding:4px 10px; }"
            "QPushButton:hover { background:#4a2a2a; }"
        )
        self._clear_image_btn.clicked.connect(self._clear_new_tag_image)
        image_row.addWidget(self._clear_image_btn)
        add_layout.addLayout(image_row)

        self._selected_image_path = ""
        self._update_image_preview(self._image_preview, "")

        add_btn = QPushButton("Add Tag")
        add_btn.setMinimumHeight(30)
        add_btn.setMinimumWidth(108)
        add_btn.setStyleSheet(
            "QPushButton { background:#2a4a2a; color:#d6f0d6; font-size:12px; font-weight:bold;"
            " border:1px solid #4a7a4a; border-radius:4px; padding:4px 12px; }"
            "QPushButton:hover { background:#3a6a3a; }"
        )
        add_btn.clicked.connect(self._add_tag)
        add_layout.addWidget(add_btn, alignment=Qt.AlignRight)

        layout.addWidget(add_group)
        self._update_color_selection()
        self._rebuild_list()

        # Close button
        close_btn = QPushButton("Close")
        close_btn.setStyleSheet(
            "QPushButton { background:#252545; color:#aaa; padding:6px 16px;"
            " border:none; border-radius:4px; }"
            "QPushButton:hover { background:#353565; color:#ddd; }"
        )
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn, alignment=Qt.AlignRight)

    def _select_color(self, color: str):
        self._selected_color = color
        _remember_tag_color_history(color)
        self._selected_image_path = self._selected_image_path or ""
        self._update_color_selection()

    def _update_color_selection(self):
        for btn, color in self._color_btns:
            if color == self._selected_color:
                btn.setStyleSheet(
                    f"QPushButton {{ background:{color}; border:2px solid #fff;"
                    f" border-radius:11px; }}"
                )
            else:
                btn.setStyleSheet(
                    f"QPushButton {{ background:{color}; border:2px solid transparent;"
                    f" border-radius:11px; }}"
                    f"QPushButton:hover {{ border-color:#fff; }}"
                )
        if self._selected_color in TAG_PRESET_COLORS:
            self._custom_color_btn.setStyleSheet(
                "QPushButton { background:#252545; color:#ddd; border:1px solid #3d3d68;"
                " border-radius:4px; padding:4px 10px; }"
                "QPushButton:hover { background:#34345f; }"
            )
        else:
            custom_color = QColor(self._selected_color)
            if custom_color.isValid():
                fg = "#111111" if custom_color.lightness() >= 140 else "#f6f6f6"
                self._custom_color_btn.setStyleSheet(
                    f"QPushButton {{ background:{custom_color.name()}; color:{fg}; border:1px solid #ffffff66;"
                    " border-radius:4px; padding:4px 10px; font-weight:bold; }}"
                    f"QPushButton:hover {{ border-color:#fff; }}"
                )
            else:
                self._custom_color_btn.setStyleSheet(
                    "QPushButton { background:#252545; color:#ddd; border:1px solid #3d3d68;"
                    " border-radius:4px; padding:4px 10px; }"
                    "QPushButton:hover { background:#34345f; }"
                )

    def _update_image_preview(self, label: QLabel, path: str, empty_text: str = "None"):
        label.setAlignment(Qt.AlignCenter)
        label.setStyleSheet(
            "QLabel { background:#101024; color:#9aa0c7; border:1px solid #2a2a4a;"
            " border-radius:4px; font-size:9px; }"
        )
        clean = str(path or "").strip()
        if clean:
            pix = QPixmap(clean)
            if not pix.isNull():
                _dpr = self.devicePixelRatioF()
                _ls = label.size()
                _target = QSize(int(_ls.width() * _dpr), int(_ls.height() * _dpr))
                pix = pix.scaled(
                    _target,
                    Qt.KeepAspectRatio,
                    Qt.SmoothTransformation,
                )
                pix.setDevicePixelRatio(_dpr)
                label.setPixmap(
                    pix
                )
                label.setText("")
                label.setToolTip(os.path.basename(clean))
                return
        label.setPixmap(QPixmap())
        label.setText(empty_text)
        label.setToolTip(empty_text)

    def _open_color_dialog(self, initial_color: str, title: str) -> str | None:
        dlg = TagColorDialog(self, initial_color=initial_color, title=title)
        if dlg.exec() != QDialog.Accepted:
            return None
        return dlg.selected_hex()

    def _pick_custom_color(self):
        color = self._open_color_dialog(self._selected_color, "Custom Tag Color")
        if not color:
            return
        self._selected_color = color
        _remember_tag_color_history(color)
        self._update_color_selection()

    def _pick_new_tag_image(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Choose tag image",
            str(Path.home()),
            "Images (*.png *.jpg *.jpeg *.bmp *.gif *.webp);;All Files (*.*)",
        )
        if path:
            copied = _import_tag_image(path)
            self._selected_image_path = copied or path
            self._update_image_preview(self._image_preview, self._selected_image_path)
            self._image_path_label.setText(os.path.basename(self._selected_image_path))
            self._image_path_label.setToolTip(os.path.basename(self._selected_image_path))
        else:
            self._selected_image_path = ""
            self._update_image_preview(self._image_preview, "")
            self._image_path_label.setText("None")
            self._image_path_label.setToolTip("None")

    def _clear_new_tag_image(self):
        self._selected_image_path = ""
        self._update_image_preview(self._image_preview, "")
        self._image_path_label.setText("None")
        self._image_path_label.setToolTip("None")

    def _add_tag(self):
        name = self._name_input.text().strip()
        _remember_tag_color_history(self._selected_color)
        tag_id = _next_tag_id()
        _TAG_DEFS.append({
            "id": tag_id,
            "name": name,
            "color": self._selected_color,
            "image_path": self._selected_image_path,
        })
        _save_tag_definitions()
        self._name_input.clear()
        self._clear_new_tag_image()
        self._rebuild_list()

    def _delete_tag(self, tag_id: str):
        _TAG_DEFS[:] = [td for td in _TAG_DEFS if td["id"] != tag_id]
        _save_tag_definitions()
        mw = self.parent()
        if hasattr(mw, '_cats'):
            for cat in mw._cats:
                current = list(getattr(cat, 'tags', None) or [])
                if tag_id in current:
                    current.remove(tag_id)
                    cat.tags = current
        self._rebuild_list()

    def _rename_tag(self, tag_id: str, new_name: str):
        for td in _TAG_DEFS:
            if td["id"] == tag_id:
                td["name"] = new_name.strip()
                break
        _save_tag_definitions()

    def _recolor_tag(self, tag_id: str, new_color: str):
        _remember_tag_color_history(new_color)
        for td in _TAG_DEFS:
            if td["id"] == tag_id:
                td["color"] = new_color
                break
        _save_tag_definitions()
        self._rebuild_list()

    def _set_tag_image(self, tag_id: str, image_path: str):
        for td in _TAG_DEFS:
            if td["id"] == tag_id:
                td["image_path"] = _import_tag_image(image_path, tag_id) if image_path else ""
                break
        _save_tag_definitions()
        self._rebuild_list()

    def _rebuild_list(self):
        while self._list_layout.count():
            item = self._list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not _TAG_DEFS:
            empty = QLabel("No tags defined yet")
            empty.setStyleSheet("color:#666; font-style:italic; padding:10px;")
            empty.setAlignment(Qt.AlignCenter)
            self._list_layout.addWidget(empty)
        else:
            for td in _TAG_DEFS:
                row = QWidget()
                rl = QHBoxLayout(row)
                rl.setContentsMargins(4, 2, 4, 2)
                rl.setSpacing(8)

                swatch = QPushButton()
                swatch.setFixedSize(20, 20)
                swatch.setStyleSheet(
                    f"QPushButton {{ background:{td['color']}; border:none; border-radius:10px; }}"
                    f"QPushButton:hover {{ border:2px solid #fff; }}"
                )
                tag_id = td["id"]
                swatch.clicked.connect(lambda checked, tid=tag_id: self._show_color_picker(tid))
                rl.addWidget(swatch)

                preview = QLabel("None")
                preview.setAlignment(Qt.AlignCenter)
                preview.setFixedSize(32, 32)
                preview.setStyleSheet(
                    "QLabel { background:#101024; color:#9aa0c7; border:1px solid #2a2a4a;"
                    " border-radius:4px; font-size:8px; }"
                )
                self._update_image_preview(preview, str(td.get("image_path", "") or ""), "None")
                preview.setToolTip(os.path.basename(str(td.get("image_path", "") or "")) or "No image")
                rl.addWidget(preview)

                name_edit = QLineEdit(td["name"])
                name_edit.setMaxLength(20)
                name_edit.setPlaceholderText("Tag name")
                name_edit.setStyleSheet(
                    "QLineEdit { background:transparent; color:#ddd; border:none;"
                    " border-bottom:1px solid #2a2a4a; padding:2px 4px; font-size:12px; }"
                    "QLineEdit:focus { border-bottom-color:#5a5a8a; }"
                )
                name_edit.editingFinished.connect(
                    lambda tid=tag_id, le=name_edit: self._rename_tag(tid, le.text())
                )
                rl.addWidget(name_edit, 1)

                image_label = QLabel(os.path.basename(str(td.get("image_path", "") or "")) or "No image")
                image_label.setStyleSheet("color:#9aa0c7; font-size:11px;")
                image_label.setFixedWidth(150)
                image_label.setToolTip(os.path.basename(str(td.get("image_path", "") or "")) or "No image")
                rl.addWidget(image_label)

                img_btn = QPushButton("Image…")
                img_btn.setFixedWidth(70)
                img_btn.setStyleSheet(
                    "QPushButton { background:#252545; color:#ddd; border:1px solid #3d3d68;"
                    " border-radius:4px; padding:3px 8px; font-size:11px; }"
                    "QPushButton:hover { background:#34345f; }"
                )
                img_btn.clicked.connect(lambda checked=False, tid=tag_id: self._show_image_picker(tid))
                rl.addWidget(img_btn)

                clear_img_btn = QPushButton("Clear")
                clear_img_btn.setFixedWidth(60)
                clear_img_btn.setStyleSheet(
                    "QPushButton { background:#2d2020; color:#f0b0b0; border:1px solid #5a3434;"
                    " border-radius:4px; padding:3px 8px; font-size:11px; }"
                    "QPushButton:hover { background:#4a2a2a; }"
                )
                clear_img_btn.clicked.connect(lambda checked=False, tid=tag_id: self._set_tag_image(tid, ""))
                rl.addWidget(clear_img_btn)

                del_btn = QPushButton("x")
                del_btn.setFixedSize(22, 22)
                del_btn.setStyleSheet(
                    "QPushButton { background:transparent; color:#855; font-size:12px;"
                    " font-weight:bold; border:1px solid #433; border-radius:11px; }"
                    "QPushButton:hover { background:#4a2020; color:#f88; border-color:#855; }"
                )
                del_btn.clicked.connect(lambda checked, tid=tag_id: self._delete_tag(tid))
                rl.addWidget(del_btn)

                self._list_layout.addWidget(row)

        self._list_layout.addStretch()

    def _show_color_picker(self, tag_id: str):
        popup = QDialog(self)
        popup.setWindowTitle("Pick Color")
        popup.setFixedWidth(280)
        popup.setStyleSheet("QDialog { background:#1a1a32; }")
        grid = QGridLayout(popup)
        grid.setSpacing(6)
        for i, color in enumerate(TAG_PRESET_COLORS):
            btn = QPushButton()
            btn.setFixedSize(30, 30)
            btn.setStyleSheet(
                f"QPushButton {{ background:{color}; border:2px solid transparent;"
                f" border-radius:15px; }}"
                f"QPushButton:hover {{ border-color:#fff; }}"
            )
            btn.clicked.connect(lambda checked=False, c=color: (self._recolor_tag(tag_id, c), popup.accept()))
            grid.addWidget(btn, i // 4, i % 4)

        custom = QPushButton("Custom Color")
        custom.setStyleSheet(
            "QPushButton { background:#252545; color:#ddd; border:1px solid #3d3d68;"
            " border-radius:4px; padding:5px 10px; }"
            "QPushButton:hover { background:#34345f; }"
        )
        custom.clicked.connect(lambda: self._pick_color_from_dialog(tag_id, popup))
        grid.addWidget(custom, len(TAG_PRESET_COLORS) // 4 + 1, 0, 1, 4)
        popup.exec()

    def _pick_color_from_dialog(self, tag_id: str, popup: QDialog):
        color = self._open_color_dialog(_tag_color(tag_id), "Edit Tag Color")
        if not color:
            return
        self._recolor_tag(tag_id, color)
        popup.accept()

    def _show_image_picker(self, tag_id: str):
        current = ""
        for td in _TAG_DEFS:
            if td["id"] == tag_id:
                current = str(td.get("image_path", "") or "")
                break
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Choose tag image",
            str(Path(current).expanduser().parent) if current else str(Path.home()),
            "Images (*.png *.jpg *.jpeg *.bmp *.gif *.webp);;All Files (*.*)",
        )
        if path:
            self._set_tag_image(tag_id, path)


# ---------------------------------------------------------------------------
# About / onboarding / changelog dialogs
# ---------------------------------------------------------------------------

class AboutDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setModal(True)
        self.setWindowTitle("About Mewgenics Breeding Manager")
        self.setMinimumWidth(560)
        self.setStyleSheet(
            "QDialog { background:#0d0d1c; }"
            "QLabel { color:#ddd; }"
            "QTextBrowser { background:#101023; color:#ddd; border:1px solid #26264a;"
            " border-radius:6px; padding:12px; }"
            "QPushButton { background:#1a1a32; color:#aaa; border:1px solid #2a2a4a;"
            " border-radius:4px; padding:6px 12px; }"
            "QPushButton:hover { background:#252545; color:#ddd; }"
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        title = QLabel(f"Mewgenics Breeding Manager v{APP_VERSION}")
        title.setStyleSheet("color:#f0f0ff; font-size:18px; font-weight:bold;")
        root.addWidget(title)

        body = QTextBrowser()
        body.setOpenExternalLinks(True)
        import PySide6

        pyside_version = getattr(PySide6, "__version__", "unknown")
        body.setHtml(
            f"""
            <div style="line-height:1.45;">
              <p>A desktop companion for breeding analysis, room planning, mutation inspection, and save-file organization.</p>
              <ul>
                <li><b>App version:</b> {APP_VERSION}</li>
                <li><b>Python:</b> {platform.python_version()}</li>
                <li><b>PySide6:</b> {pyside_version}</li>
              </ul>
              <p><a href="https://github.com/frankieg33/MewgenicsBreedingManager">Project on GitHub</a></p>
            </div>
            """
        )
        root.addWidget(body, 1)

        button_row = QHBoxLayout()
        button_row.addStretch(1)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        button_row.addWidget(close_btn)
        root.addLayout(button_row)


class WhatsNewDialog(QDialog):
    def __init__(self, parent=None, version: str = APP_VERSION, highlights: list[str] | None = None):
        super().__init__(parent)
        self.setModal(True)
        self.setWindowTitle(f"What's New in v{version}")
        self.setMinimumWidth(620)
        self.setStyleSheet(
            "QDialog { background:#0d0d1c; }"
            "QLabel { color:#ddd; }"
            "QTextBrowser { background:#101023; color:#ddd; border:1px solid #26264a;"
            " border-radius:6px; padding:12px; }"
            "QPushButton { background:#1f5f4a; color:#f2f7f3; border:1px solid #3f8f72;"
            " border-radius:4px; padding:6px 12px; }"
            "QPushButton:hover { background:#26735a; }"
        )

        default_highlights = highlights or [
            "Fixed Configure Rooms layout (capacity, room type, stimulation) bleeding between save files — room priority config now lives only in the per-save sidecar instead of being mirrored globally (issue #68).",
            "Room Optimizer can now route kittens (age < 2) into fallback rooms instead of wasting breeding-room capacity on cats that can't breed yet. Eternal-youth cats are still placed in the best breeding room (issue #70).",
            "Room Optimizer can now avoid placing cats with desired mutations into high-Evolution rooms, and cats with desired disorders into high-Health rooms (which would otherwise override or cure those traits). Opt in via the new Avoid Trait Loss toggle (issue #71).",
        ]

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        title = QLabel(f"What's New in v{version}")
        title.setStyleSheet("color:#f0f0ff; font-size:18px; font-weight:bold;")
        root.addWidget(title)

        body = QTextBrowser()
        body.setOpenExternalLinks(True)
        bullets = "".join(f"<li>{item}</li>" for item in default_highlights)
        body.setHtml(
            f"""
            <div style="line-height:1.5;">
              <p>This release fixes Configure Rooms settings bleeding between save files and adds two opt-in Room Optimizer improvements: kitten routing to fallback rooms and trait-loss avoidance for desired mutations/disorders.</p>
              <ul>{bullets}</ul>
              <p><a href="https://github.com/frankieg33/MewgenicsBreedingManager/releases">View releases on GitHub</a></p>
            </div>
            """
        )
        root.addWidget(body, 1)

        button_row = QHBoxLayout()
        button_row.addStretch(1)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        button_row.addWidget(close_btn)
        root.addLayout(button_row)


class OnboardingDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setModal(True)
        self.setWindowTitle("Getting Started")
        self.setMinimumWidth(680)
        self.setStyleSheet(
            "QDialog { background:#0d0d1c; }"
            "QLabel { color:#ddd; }"
            "QTextBrowser { background:#101023; color:#ddd; border:1px solid #26264a;"
            " border-radius:6px; padding:12px; }"
            "QPushButton { background:#1a1a32; color:#aaa; border:1px solid #2a2a4a;"
            " border-radius:4px; padding:6px 12px; }"
            "QPushButton:hover { background:#252545; color:#ddd; }"
            "QPushButton:disabled { color:#555; background:#141428; }"
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        title = QLabel("Welcome to Mewgenics Breeding Manager")
        title.setStyleSheet("color:#f0f0ff; font-size:18px; font-weight:bold;")
        root.addWidget(title)

        self._stack = QStackedWidget()
        self._stack.addWidget(self._make_page(
            "1. Load a save",
            f"""
            <p>Start with <b>File > Open Save</b> or your configured default save.</p>
            <p>The save root currently points to:</p>
            <p><code>{_save_root_dir()}</code></p>
            """,
        ))
        self._stack.addWidget(self._make_page(
            "2. Sidebar shortcuts",
            """
            <p>The left sidebar jumps between the most useful views:</p>
            <ul>
              <li>Room optimizer and Perfect Planner</li>
              <li>Mating Pair Search and Breeding Partners</li>
              <li>Family Tree and other info views</li>
            </ul>
            """,
        ))
        self._stack.addWidget(self._make_page(
            "3. Roster workflow",
            """
            <p>Use the main roster to sort, filter, tag, and inspect cats.</p>
            <ul>
              <li>Right-click rows for quick actions.</li>
              <li>The Tags button opens the tag manager.</li>
              <li>The status bar version label opens release notes.</li>
            </ul>
            """,
        ))
        self._stack.addWidget(self._make_page(
            "4. Help and accessibility",
            """
            <p>The Settings menu now includes accessibility presets and saved UI scale controls.</p>
            <p>The Help menu gives you this walkthrough again, What&apos;s New, and About.</p>
            <p>Cat sprites require shape assets &mdash; these are extracted automatically from
            <code>DefinedShapes.zip</code> or the game&apos;s <code>resources.gpak</code> on first launch.</p>
            """,
        ))
        self._stack.addWidget(self._make_page(
            "5. Cat Sprites",
            """
            <p>The Family Tree view can render in-game cat portraits using sprite data
            from the game&apos;s <code>resources.gpak</code>.</p>
            <ul>
              <li>On first launch, shape assets are extracted automatically (~3 s from bundled ZIP, ~25 s from GPAK).</li>
              <li>Rendered thumbnails are cached on disk for instant loading on subsequent opens.</li>
              <li>Toggle <b>Show Cat Images</b> in the Family Tree view to enable portraits.</li>
            </ul>
            """,
        ))
        root.addWidget(self._stack, 1)

        self._page_label = QLabel("")
        self._page_label.setStyleSheet("color:#8f95bd; font-size:11px;")
        root.addWidget(self._page_label)

        button_row = QHBoxLayout()
        self._back_btn = QPushButton("Back")
        self._back_btn.clicked.connect(self._previous_page)
        button_row.addWidget(self._back_btn)
        button_row.addStretch(1)
        self._next_btn = QPushButton("Next")
        self._next_btn.clicked.connect(self._next_page)
        button_row.addWidget(self._next_btn)
        self._finish_btn = QPushButton("Finish")
        self._finish_btn.clicked.connect(self.accept)
        button_row.addWidget(self._finish_btn)
        root.addLayout(button_row)

        self._stack.currentChanged.connect(self._update_controls)
        self._update_controls(0)

    def _make_page(self, title: str, html_body: str) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(10)
        heading = QLabel(title)
        heading.setStyleSheet("color:#f0f0ff; font-size:16px; font-weight:bold;")
        layout.addWidget(heading)
        body = QTextBrowser()
        body.setOpenExternalLinks(True)
        body.setHtml(f"<div style='line-height:1.5;'>{html_body}</div>")
        layout.addWidget(body, 1)
        return page

    def _update_controls(self, index: int):
        total = self._stack.count()
        self._page_label.setText(f"Page {index + 1} of {total}")
        self._back_btn.setEnabled(index > 0)
        if index >= total - 1:
            self._next_btn.setEnabled(False)
            self._finish_btn.setDefault(True)
        else:
            self._next_btn.setEnabled(True)
            self._finish_btn.setDefault(False)

    def _previous_page(self):
        self._stack.setCurrentIndex(max(0, self._stack.currentIndex() - 1))

    def _next_page(self):
        next_index = self._stack.currentIndex() + 1
        if next_index >= self._stack.count():
            self.accept()
            return
        self._stack.setCurrentIndex(next_index)


# ---------------------------------------------------------------------------
# ThresholdPreferencesDialog
# ---------------------------------------------------------------------------

class ThresholdPreferencesDialog(QDialog):
    def __init__(self, parent=None, prefs: dict | None = None, cats: list[Cat] | None = None):
        super().__init__(parent)
        self.setModal(True)
        self.setWindowTitle(_tr("thresholds.title", default="Donation / Exceptional Thresholds"))
        self.setMinimumWidth(520)
        self.setStyleSheet(
            "QDialog { background:#0a0a18; }"
            "QLabel { color:#cfcfe0; }"
            "QPushButton { background:#1a1a32; color:#aaa; border:1px solid #2a2a4a; "
            "border-radius:4px; padding:6px 12px; font-size:11px; }"
            "QPushButton:hover { background:#252545; color:#ddd; }"
            "QCheckBox { color:#d8d8e8; }"
            "QSpinBox, QDoubleSpinBox { background:#0d0d1c; color:#ddd; border:1px solid #2a2a4a; "
            "border-radius:4px; padding:3px 6px; }"
        )

        self._cats = list(cats or [])
        self._prefs = _normalize_threshold_preferences(prefs or _load_threshold_preferences())

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        desc = QLabel(_tr(
            "thresholds.description",
            default="Edit the donation and exceptional thresholds used by the sidebar filters."
        ))
        desc.setWordWrap(True)
        desc.setStyleSheet("font-size:12px; color:#a8a8c0;")
        root.addWidget(desc)

        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(8)

        self._exceptional_spin = QSpinBox()
        self._exceptional_spin.setRange(0, 999)
        self._exceptional_spin.setValue(self._prefs["exceptional_sum_threshold"])
        self._exceptional_spin.valueChanged.connect(self._update_preview)

        self._donation_spin = QSpinBox()
        self._donation_spin.setRange(0, 999)
        self._donation_spin.setValue(self._prefs["donation_sum_threshold"])
        self._donation_spin.valueChanged.connect(self._update_preview)

        self._top_stat_spin = QSpinBox()
        self._top_stat_spin.setRange(0, 20)
        self._top_stat_spin.setValue(self._prefs["donation_max_top_stat"])
        self._top_stat_spin.valueChanged.connect(self._update_preview)

        self._planner_trait_check = QCheckBox(_tr(
            "thresholds.planner_trait_toggle",
            default="Count cats missing selected mutation/ability traits as donation candidates",
        ))
        self._planner_trait_check.setChecked(bool(self._prefs["donation_missing_planner_traits"]))
        self._planner_trait_check.setToolTip(_tr(
            "thresholds.planner_trait_toggle.tooltip",
            default="When enabled, cats that do not carry any selected mutation or ability traits will count as donation candidates.",
        ))
        self._planner_trait_check.toggled.connect(self._update_preview)

        self._adaptive_check = QCheckBox(_tr(
            "thresholds.adaptive_toggle",
            default="Adjust thresholds from the living-cat average",
        ))
        self._adaptive_check.setChecked(self._prefs["adaptive_enabled"])
        self._adaptive_check.toggled.connect(self._update_preview)

        self._reference_spin = QDoubleSpinBox()
        self._reference_spin.setRange(0.0, 99.0)
        self._reference_spin.setDecimals(1)
        self._reference_spin.setSingleStep(0.5)
        self._reference_spin.setValue(float(self._prefs["adaptive_reference_avg_sum"]))
        self._reference_spin.valueChanged.connect(self._update_preview)

        self._curve_spin = QDoubleSpinBox()
        self._curve_spin.setRange(0.0, 5.0)
        self._curve_spin.setDecimals(2)
        self._curve_spin.setSingleStep(0.1)
        self._curve_spin.setValue(float(self._prefs["adaptive_curve_strength"]))
        self._curve_spin.valueChanged.connect(self._update_preview)

        grid.addWidget(QLabel(_tr("thresholds.exceptional", default="Exceptional threshold")), 0, 0)
        grid.addWidget(self._exceptional_spin, 0, 1)
        grid.addWidget(QLabel(_tr("thresholds.donation", default="Donation threshold")), 1, 0)
        grid.addWidget(self._donation_spin, 1, 1)
        grid.addWidget(QLabel(_tr("thresholds.donation_top_stat", default="Donation max top stat")), 2, 0)
        grid.addWidget(self._top_stat_spin, 2, 1)
        grid.addWidget(self._planner_trait_check, 3, 0, 1, 2)
        grid.addWidget(self._adaptive_check, 4, 0, 1, 2)
        grid.addWidget(QLabel(_tr("thresholds.reference_average", default="Reference living average")), 5, 0)
        grid.addWidget(self._reference_spin, 5, 1)
        grid.addWidget(QLabel(_tr("thresholds.curve_strength", default="Curve strength")), 6, 0)
        grid.addWidget(self._curve_spin, 6, 1)
        root.addLayout(grid)

        self._current_avg_label = QLabel()
        self._current_avg_label.setWordWrap(True)
        self._current_avg_label.setStyleSheet("color:#9ea4c6;")
        root.addWidget(self._current_avg_label)

        self._preview_label = QLabel()
        self._preview_label.setWordWrap(True)
        self._preview_label.setStyleSheet("color:#d8d8e8; font-weight:bold;")
        root.addWidget(self._preview_label)

        button_row = QHBoxLayout()
        button_row.addStretch(1)
        cancel_btn = QPushButton(_tr("common.cancel", default="Cancel"))
        cancel_btn.clicked.connect(self.reject)
        ok_btn = QPushButton(_tr("common.ok", default="OK"))
        ok_btn.clicked.connect(self.accept)
        button_row.addWidget(cancel_btn)
        button_row.addWidget(ok_btn)
        root.addLayout(button_row)

        self._adaptive_check.toggled.connect(self._update_adaptive_controls)
        self._update_adaptive_controls(self._adaptive_check.isChecked())
        self._update_preview()

    def _update_adaptive_controls(self, enabled: bool):
        self._reference_spin.setEnabled(enabled)
        self._curve_spin.setEnabled(enabled)

    def _sync_exceptional_floor(self):
        if self._exceptional_spin.value() < self._donation_spin.value():
            self._exceptional_spin.blockSignals(True)
            try:
                self._exceptional_spin.setValue(self._donation_spin.value())
            finally:
                self._exceptional_spin.blockSignals(False)

    def _collect_preferences(self) -> dict:
        return {
            "exceptional_sum_threshold": int(self._exceptional_spin.value()),
            "donation_sum_threshold": int(self._donation_spin.value()),
            "donation_max_top_stat": int(self._top_stat_spin.value()),
            "donation_missing_planner_traits": bool(self._planner_trait_check.isChecked()),
            "adaptive_enabled": bool(self._adaptive_check.isChecked()),
            "adaptive_reference_avg_sum": float(self._reference_spin.value()),
            "adaptive_curve_strength": float(self._curve_spin.value()),
        }

    def _update_preview(self, *_args):
        self._sync_exceptional_floor()
        prefs = _normalize_threshold_preferences(self._collect_preferences())
        exceptional, donation, top_stat, avg_sum = _effective_thresholds_for_cats(prefs, self._cats)
        if self._cats:
            self._current_avg_label.setText(
                _tr(
                    "thresholds.current_average",
                    default="Living cats average base sum: {avg:.1f}",
                    avg=avg_sum,
                )
            )
        else:
            self._current_avg_label.setText(
                _tr(
                    "thresholds.no_save_preview",
                    default="Load a save to preview the curve; the values below will still be saved.",
                )
            )
        if prefs["adaptive_enabled"] and self._cats:
            preview_text = _tr(
                "thresholds.preview",
                default="Effective now: Exceptional >= {exceptional}, Donation <= {donation}, Donation top stat <= {top_stat}",
                exceptional=exceptional,
                donation=donation,
                top_stat=top_stat,
            )
        elif prefs["adaptive_enabled"]:
            preview_text = _tr(
                "thresholds.preview_no_save",
                default="Adaptive mode is on, but there is no save loaded yet.",
            )
        else:
            preview_text = _tr(
                "thresholds.preview_fixed",
                default="Fixed thresholds: Exceptional >= {exceptional}, Donation <= {donation}, Donation top stat <= {top_stat}",
                exceptional=exceptional,
                donation=donation,
                top_stat=top_stat,
            )
        if prefs.get("donation_missing_planner_traits"):
            preview_text += _tr(
                "thresholds.preview.planner_trait_note",
                default=" Cats missing the selected mutation/ability traits will count as donation candidates if they are still under the stat floor.",
            )
        self._preview_label.setText(preview_text)

    def preferences(self) -> dict:
        return _normalize_threshold_preferences(self._collect_preferences())


# ---------------------------------------------------------------------------
# SharedOptimizerSearchSettingsDialog
# ---------------------------------------------------------------------------

class SharedOptimizerSearchSettingsDialog(QDialog):
    def __init__(self, parent=None, settings: dict | None = None):
        super().__init__(parent)
        self.setModal(True)
        self.setWindowTitle(_tr(
            "menu.settings.optimizer_search_settings.title",
            default="Shared Optimizer Search Settings",
        ))
        self.setMinimumWidth(460)
        self.setStyleSheet(
            "QDialog { background:#0a0a18; }"
            "QLabel { color:#cfcfe0; }"
            "QPushButton { background:#1a1a32; color:#aaa; border:1px solid #2a2a4a; "
            "border-radius:4px; padding:6px 12px; font-size:11px; }"
            "QPushButton:hover { background:#252545; color:#ddd; }"
            "QSpinBox, QDoubleSpinBox { background:#0d0d1c; color:#ddd; border:1px solid #2a2a4a; "
            "border-radius:4px; padding:3px 6px; }"
        )

        self._settings = _normalize_optimizer_search_settings(settings or _load_optimizer_search_settings())

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        desc = QLabel(_tr(
            "menu.settings.optimizer_search_settings.description",
            default="These values control the simulated annealing search used by the room optimizer and Perfect 7 planner.",
        ))
        desc.setWordWrap(True)
        desc.setStyleSheet("font-size:12px; color:#a8a8c0;")
        root.addWidget(desc)

        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(8)

        self._temperature_spin = QDoubleSpinBox()
        self._temperature_spin.setRange(0.0, 1000.0)
        self._temperature_spin.setDecimals(1)
        self._temperature_spin.setSingleStep(0.5)
        self._temperature_spin.setValue(float(self._settings["temperature"]))

        self._neighbors_spin = QSpinBox()
        self._neighbors_spin.setRange(1, 5000)
        self._neighbors_spin.setSingleStep(8)
        self._neighbors_spin.setValue(int(self._settings["neighbors"]))

        grid.addWidget(QLabel(_tr("room_optimizer.sa_temperature", default="Temperature:")), 0, 0)
        grid.addWidget(self._temperature_spin, 0, 1)
        _temp_default = QLabel(f"default: {_OPTIMIZER_SEARCH_DEFAULTS['temperature']:.1f}")
        _temp_default.setStyleSheet("color:#5a607a; font-size:11px;")
        grid.addWidget(_temp_default, 0, 2)
        grid.addWidget(QLabel(_tr("room_optimizer.sa_neighbors", default="Neighbors:")), 1, 0)
        grid.addWidget(self._neighbors_spin, 1, 1)
        _neighbors_default = QLabel(f"default: {_OPTIMIZER_SEARCH_DEFAULTS['neighbors']}")
        _neighbors_default.setStyleSheet("color:#5a607a; font-size:11px;")
        grid.addWidget(_neighbors_default, 1, 2)
        root.addLayout(grid)

        note = QLabel(_tr(
            "menu.settings.optimizer_search_settings.note",
            default="Changes take effect the next time either planner runs.",
        ))
        note.setWordWrap(True)
        note.setStyleSheet("color:#9ea4c6;")
        root.addWidget(note)

        button_row = QHBoxLayout()
        button_row.addStretch(1)
        cancel_btn = QPushButton(_tr("common.cancel", default="Cancel"))
        cancel_btn.clicked.connect(self.reject)
        ok_btn = QPushButton(_tr("common.ok", default="OK"))
        ok_btn.clicked.connect(self.accept)
        button_row.addWidget(cancel_btn)
        button_row.addWidget(ok_btn)
        root.addLayout(button_row)

    def preferences(self) -> dict:
        return _normalize_optimizer_search_settings({
            "temperature": float(self._temperature_spin.value()),
            "neighbors": int(self._neighbors_spin.value()),
        })


# ---------------------------------------------------------------------------
# SaveSelectorDialog
# ---------------------------------------------------------------------------

class SaveSelectorDialog(QDialog):
    """Startup dialog for picking which save file to load."""

    def __init__(self, saves: list[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"{_tr('app.title')} \u2014 {_tr('save_picker.title')}")
        self.setFixedSize(520, 360)
        self.setStyleSheet(
            "QDialog { background:#0d0d1c; }"
            "QLabel { color:#ccc; }"
            "QListWidget { background:#101023; color:#ddd; border:1px solid #26264a;"
            " font-size:13px; }"
            "QListWidget::item { padding:6px; }"
            "QListWidget::item:selected { background:#1e3060; }"
            "QPushButton { background:#1f5f4a; color:#f2f7f3; border:1px solid #3f8f72;"
            " border-radius:4px; padding:8px 20px; font-size:12px; font-weight:bold; }"
            "QPushButton:hover { background:#26735a; }"
            "QPushButton:disabled { background:#1a1a32; color:#555; border-color:#2a2a4a; }"
        )
        self._selected_path: Optional[str] = None

        vb = QVBoxLayout(self)
        vb.setContentsMargins(16, 16, 16, 16)
        vb.setSpacing(12)

        title = QLabel(_tr("save_picker.title"))
        title.setStyleSheet("color:#ddd; font-size:16px; font-weight:bold;")
        vb.addWidget(title)

        self._list = QListWidget()
        self._list.setIconSize(QSize(60, 20))
        for path in saves:
            name = os.path.basename(path)
            folder = os.path.basename(os.path.dirname(os.path.dirname(path)))
            mtime = os.path.getmtime(path)
            ts = datetime.datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")
            item = QListWidgetItem(f"{name}  ({folder})  \u2014  {ts}")
            item.setData(Qt.UserRole, path)
            self._list.addItem(item)
        self._list.setCurrentRow(0)
        self._list.itemDoubleClicked.connect(lambda _: self._accept())
        vb.addWidget(self._list, 1)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._open_btn = QPushButton(_tr("save_picker.open"))
        self._open_btn.clicked.connect(self._accept)
        self._open_btn.setEnabled(len(saves) > 0)
        btn_row.addWidget(self._open_btn)

        browse_btn = QPushButton(_tr("save_picker.browse"))
        browse_btn.setStyleSheet(
            "QPushButton { background:#1a1a32; color:#aaa; border:1px solid #2a2a4a; }"
            "QPushButton:hover { background:#252545; color:#ddd; }"
        )
        browse_btn.clicked.connect(self._browse)
        btn_row.addWidget(browse_btn)
        vb.addLayout(btn_row)

    def _accept(self):
        cur = self._list.currentItem()
        if cur is not None:
            self._selected_path = cur.data(Qt.UserRole)
            self.accept()

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            _tr("dialog.open_save.title"),
            str(Path.home()),
            _tr("dialog.open_save.filter"),
        )
        if path:
            self._selected_path = path
            self.accept()

    @property
    def selected_path(self) -> Optional[str]:
        return self._selected_path
