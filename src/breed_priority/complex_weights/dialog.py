"""Complex Weights dialogs — management list and single-CW editor.

ComplexWeightsDialog: non-modal window listing all CWs with toggle/edit/delete.
_CWEditorDialog: modal dialog for creating or editing a single ComplexWeight.
_TraitSelectDialog: modal checklist for picking traits in a condition.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QScrollArea, QFrame,
    QPushButton, QLineEdit, QDoubleSpinBox, QSpinBox,
    QComboBox, QCheckBox, QListWidget, QListWidgetItem,
    QSizePolicy,
)
from PySide6.QtCore import Qt, Signal

from .model import (
    ComplexWeight, Condition,
    FIELD_GENDER, FIELD_LIBIDO, FIELD_AGGRESSION, FIELD_SEXUALITY,
    FIELD_STAT_SUM, FIELD_AGE, FIELD_GENE_RISK, FIELD_GENE_UNIQUE,
    FIELD_SCORE, FIELD_TRAIT, FIELD_STAT_PREFIX,
    CATEGORICAL_VALUES,
    NUMERIC_OPS, CATEGORICAL_OPS, TRAIT_OPS,
    OP_EQ, OP_DISPLAY,
    TRAIT_MODE_ANY,
    LOGIC_AND, LOGIC_OR,
    build_field_options,
)
from ..styles import (
    ACTION_BUTTON_PRIMARY_STYLE, ACTION_BUTTON_SECONDARY_STYLE,
    ACTION_BUTTON_SECONDARY_LARGE_STYLE,
    PRIORITY_COMBO_STYLE,
)
from ..theme import (
    CLR_SURFACE_APP_MAIN, CLR_SURFACE_APP_ALT,
    CLR_SURFACE_SEPARATOR, CLR_SURFACE_HEADER, CLR_SURFACE_HEADER_BORDER,
    CLR_TEXT_CONTENT_PRIMARY, CLR_TEXT_CONTENT_SECONDARY,
    CLR_TEXT_LABEL_UI, CLR_TEXT_LABEL_COUNT,
    CLR_VALUE_POS, CLR_VALUE_NEG, CLR_VALUE_NEUTRAL,
    CLR_BG_DEEP,
)


# ── Common styling helpers ────────────────────────────────────────────────────

_DLG_BASE_STYLE = (
    f"background:{CLR_SURFACE_APP_MAIN}; color:{CLR_TEXT_CONTENT_PRIMARY};"
)
_COMBO_STYLE = (
    PRIORITY_COMBO_STYLE
    + f" QComboBox {{ background:{CLR_SURFACE_APP_ALT}; color:{CLR_TEXT_CONTENT_PRIMARY};"
    f"   border:1px solid {CLR_SURFACE_SEPARATOR}; padding:1px 4px; }}"
)
_SPIN_STYLE = (
    f"QDoubleSpinBox, QSpinBox {{"
    f"  background:{CLR_SURFACE_APP_ALT}; color:{CLR_TEXT_CONTENT_PRIMARY};"
    f"  border:1px solid {CLR_SURFACE_SEPARATOR}; padding:1px 4px; }}"
)
_LINE_STYLE = (
    f"QLineEdit {{"
    f"  background:{CLR_SURFACE_APP_ALT}; color:{CLR_TEXT_CONTENT_PRIMARY};"
    f"  border:1px solid {CLR_SURFACE_SEPARATOR}; padding:2px 6px; }}"
)


# ── Trait select dialog ───────────────────────────────────────────────────────

class _TraitSelectDialog(QDialog):
    """Modal checklist for selecting traits to use in a condition."""

    def __init__(self, parent, all_traits: list, selected: list):
        super().__init__(parent)
        self.setWindowTitle("Select Traits")
        self.setModal(True)
        self.setMinimumSize(320, 460)
        self.setStyleSheet(_DLG_BASE_STYLE)
        self._result: list = list(selected)

        vb = QVBoxLayout(self)
        vb.setContentsMargins(12, 12, 12, 12)
        vb.setSpacing(8)

        search = QLineEdit()
        search.setPlaceholderText("Filter traits…")
        search.setStyleSheet(_LINE_STYLE)
        search.textChanged.connect(self._filter)
        vb.addWidget(search)

        self._list = QListWidget()
        self._list.setStyleSheet(
            f"QListWidget {{ background:{CLR_SURFACE_APP_ALT};"
            f"  border:1px solid {CLR_SURFACE_SEPARATOR}; color:{CLR_TEXT_CONTENT_PRIMARY}; }}"
            "QListWidget::item { padding:2px 6px; }"
        )
        for trait in sorted(all_traits):
            item = QListWidgetItem(trait)
            item.setCheckState(Qt.Checked if trait in selected else Qt.Unchecked)
            self._list.addItem(item)
        vb.addWidget(self._list, stretch=1)

        row = QHBoxLayout()
        row.addStretch()
        cancel = QPushButton("Cancel")
        cancel.setStyleSheet(ACTION_BUTTON_SECONDARY_STYLE)
        cancel.clicked.connect(self.reject)
        ok = QPushButton("OK")
        ok.setStyleSheet(ACTION_BUTTON_PRIMARY_STYLE)
        ok.clicked.connect(self._on_ok)
        row.addWidget(cancel)
        row.addWidget(ok)
        vb.addLayout(row)

    def _filter(self, text: str):
        lower = text.lower()
        for i in range(self._list.count()):
            item = self._list.item(i)
            item.setHidden(lower not in item.text().lower())

    def _on_ok(self):
        self._result = [
            self._list.item(i).text()
            for i in range(self._list.count())
            if self._list.item(i).checkState() == Qt.Checked
        ]
        self.accept()

    def selected_traits(self) -> list:
        return self._result


# ── Condition row widget ──────────────────────────────────────────────────────

class _ConditionRow(QWidget):
    """A single condition row: [Field] [Operator] [Value] [Remove]."""

    removed = Signal()

    _BOOL_OPTIONS = [("True", True), ("False", False)]

    def __init__(self, parent, condition: Condition, all_traits: list, stat_names: list):
        super().__init__(parent)
        self._all_traits = all_traits
        self._stat_names = stat_names
        self._field_options = build_field_options(stat_names)
        self._trait_selection: list = []
        self._trait_label: QLabel | None = None
        self._value_widget = None

        self.setStyleSheet(f"background:{CLR_SURFACE_APP_ALT}; border-radius:2px;")

        row = QHBoxLayout(self)
        row.setContentsMargins(4, 3, 4, 3)
        row.setSpacing(4)

        self._field_cb = QComboBox()
        self._field_cb.setStyleSheet(_COMBO_STYLE)
        self._field_cb.setFixedWidth(136)
        for label, key in self._field_options:
            self._field_cb.addItem(label, key)
        row.addWidget(self._field_cb)

        self._op_cb = QComboBox()
        self._op_cb.setStyleSheet(_COMBO_STYLE)
        self._op_cb.setFixedWidth(76)
        row.addWidget(self._op_cb)

        self._val_container = QWidget()
        self._val_container.setStyleSheet("background:transparent;")
        self._val_layout = QHBoxLayout(self._val_container)
        self._val_layout.setContentsMargins(0, 0, 0, 0)
        self._val_layout.setSpacing(4)
        row.addWidget(self._val_container, stretch=1)

        rm = QPushButton("✕")
        rm.setFixedWidth(22)
        rm.setStyleSheet(ACTION_BUTTON_SECONDARY_STYLE)
        rm.clicked.connect(self.removed)
        row.addWidget(rm)

        self._field_cb.currentIndexChanged.connect(self._on_field_changed)
        self._load(condition)

    def _on_field_changed(self):
        self._rebuild_for_field(self._field_cb.currentData())

    def _clear_value(self):
        while self._val_layout.count():
            item = self._val_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._value_widget = None
        self._trait_label = None

    def _rebuild_for_field(self, field_key: str):
        self._op_cb.blockSignals(True)
        self._op_cb.clear()
        self._clear_value()

        if field_key in CATEGORICAL_VALUES:
            self._op_cb.addItem("==", OP_EQ)
            self._op_cb.addItem("!=", "!=")
            cb = QComboBox()
            cb.setStyleSheet(_COMBO_STYLE)
            for label, val in CATEGORICAL_VALUES[field_key]:
                cb.addItem(label, val)
            self._val_layout.addWidget(cb)
            self._value_widget = cb

        elif field_key == FIELD_GENE_UNIQUE:
            self._op_cb.addItem("is", OP_EQ)
            cb = QComboBox()
            cb.setStyleSheet(_COMBO_STYLE)
            for label, val in self._BOOL_OPTIONS:
                cb.addItem(label, val)
            self._val_layout.addWidget(cb)
            self._value_widget = cb

        elif field_key == FIELD_TRAIT:
            for mode in TRAIT_OPS:
                self._op_cb.addItem(OP_DISPLAY.get(mode, mode), mode)
            self._trait_selection = []
            btn = QPushButton("Select…")
            btn.setStyleSheet(ACTION_BUTTON_SECONDARY_STYLE)
            btn.clicked.connect(self._open_trait_picker)
            self._trait_label = QLabel("(none)")
            self._trait_label.setStyleSheet(
                f"color:{CLR_TEXT_LABEL_COUNT}; font-size:10px;"
            )
            self._val_layout.addWidget(btn)
            self._val_layout.addWidget(self._trait_label)

        elif field_key in (FIELD_AGE, FIELD_STAT_SUM) or field_key.startswith(FIELD_STAT_PREFIX):
            for op in NUMERIC_OPS:
                self._op_cb.addItem(OP_DISPLAY.get(op, op), op)
            spin = QSpinBox()
            spin.setRange(0, 9999)
            spin.setFixedWidth(72)
            spin.setStyleSheet(_SPIN_STYLE)
            self._val_layout.addWidget(spin)
            self._value_widget = spin

        else:  # FIELD_GENE_RISK, FIELD_SCORE
            for op in NUMERIC_OPS:
                self._op_cb.addItem(OP_DISPLAY.get(op, op), op)
            spin = QDoubleSpinBox()
            spin.setRange(-9999.0, 9999.0)
            spin.setDecimals(1)
            spin.setFixedWidth(80)
            spin.setStyleSheet(_SPIN_STYLE)
            self._val_layout.addWidget(spin)
            self._value_widget = spin

        self._op_cb.blockSignals(False)

    def _open_trait_picker(self):
        dlg = _TraitSelectDialog(self, self._all_traits, self._trait_selection)
        if dlg.exec():
            self._trait_selection = dlg.selected_traits()
        n = len(self._trait_selection)
        if self._trait_label:
            self._trait_label.setText(f"({n} selected)" if n else "(none)")

    def _load(self, cond: Condition):
        # Set field combobox
        for i, (_, key) in enumerate(self._field_options):
            if key == cond.field:
                self._field_cb.setCurrentIndex(i)
                break
        self._rebuild_for_field(cond.field)

        # Set operator
        for i in range(self._op_cb.count()):
            if self._op_cb.itemData(i) == cond.operator:
                self._op_cb.setCurrentIndex(i)
                break

        # Set value
        if cond.field == FIELD_TRAIT:
            self._trait_selection = list(cond.value) if isinstance(cond.value, list) else []
            n = len(self._trait_selection)
            if self._trait_label:
                self._trait_label.setText(f"({n} selected)" if n else "(none)")
        elif cond.field == FIELD_GENE_UNIQUE:
            if self._value_widget:
                idx = 0 if cond.value else 1
                self._value_widget.setCurrentIndex(idx)
        elif cond.field in CATEGORICAL_VALUES:
            if self._value_widget:
                for i in range(self._value_widget.count()):
                    if self._value_widget.itemData(i) == cond.value:
                        self._value_widget.setCurrentIndex(i)
                        break
        else:
            if self._value_widget:
                try:
                    self._value_widget.setValue(float(cond.value or 0))
                except (TypeError, ValueError):
                    pass

    def get_condition(self) -> Condition:
        """Build a Condition from current widget state."""
        field_key = self._field_cb.currentData()
        op = self._op_cb.currentData()

        if field_key == FIELD_TRAIT:
            value = list(self._trait_selection)
        elif field_key == FIELD_GENE_UNIQUE:
            value = self._value_widget.currentData() if self._value_widget else True
        elif field_key in CATEGORICAL_VALUES:
            value = self._value_widget.currentData() if self._value_widget else ""
        else:
            value = self._value_widget.value() if self._value_widget else 0

        return Condition(field=field_key, operator=op, value=value)


# ── Single CW editor dialog ───────────────────────────────────────────────────

class _CWEditorDialog(QDialog):
    """Modal dialog for creating or editing a single ComplexWeight."""

    def __init__(self, parent, cw: ComplexWeight | None, all_traits: list, stat_names: list):
        super().__init__(parent)
        self.setWindowTitle("Edit Complex Weight" if cw else "New Complex Weight")
        self.setModal(True)
        self.setMinimumWidth(560)
        self.setStyleSheet(_DLG_BASE_STYLE)
        self._all_traits = all_traits
        self._stat_names = stat_names
        self._rows: list[_ConditionRow] = []
        self._saved_cw: ComplexWeight | None = None

        vb = QVBoxLayout(self)
        vb.setContentsMargins(14, 14, 14, 14)
        vb.setSpacing(8)

        # Name
        name_row = QHBoxLayout()
        name_row.addWidget(QLabel("Name:"))
        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText("Complex Weight name…")
        self._name_edit.setStyleSheet(_LINE_STYLE)
        name_row.addWidget(self._name_edit, stretch=1)
        vb.addLayout(name_row)

        # Score delta + logic on the same row
        params_row = QHBoxLayout()
        params_row.addWidget(QLabel("Score delta:"))
        self._delta_spin = QDoubleSpinBox()
        self._delta_spin.setRange(-999.0, 999.0)
        self._delta_spin.setDecimals(1)
        self._delta_spin.setSingleStep(0.5)
        self._delta_spin.setFixedWidth(88)
        self._delta_spin.setStyleSheet(_SPIN_STYLE)
        params_row.addWidget(self._delta_spin)
        params_row.addSpacing(12)
        params_row.addWidget(QLabel("Logic:"))
        self._logic_cb = QComboBox()
        self._logic_cb.setStyleSheet(_COMBO_STYLE)
        self._logic_cb.addItem("AND — all conditions must match", LOGIC_AND)
        self._logic_cb.addItem("OR — any condition must match",  LOGIC_OR)
        params_row.addWidget(self._logic_cb)
        params_row.addStretch()
        vb.addLayout(params_row)

        # Separator + conditions label
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet(f"color:{CLR_SURFACE_SEPARATOR};")
        vb.addWidget(sep)

        cond_hdr = QHBoxLayout()
        cond_hdr.addWidget(QLabel("Conditions"))
        cond_hdr.addStretch()
        add_btn = QPushButton("+ Add Condition")
        add_btn.setStyleSheet(ACTION_BUTTON_SECONDARY_STYLE)
        add_btn.clicked.connect(self._add_empty)
        cond_hdr.addWidget(add_btn)
        vb.addLayout(cond_hdr)

        # Scrollable condition list
        self._cond_widget = QWidget()
        self._cond_widget.setStyleSheet(f"background:{CLR_SURFACE_APP_MAIN};")
        self._cond_vb = QVBoxLayout(self._cond_widget)
        self._cond_vb.setContentsMargins(0, 0, 0, 0)
        self._cond_vb.setSpacing(3)
        self._cond_vb.addStretch()

        scroll = QScrollArea()
        scroll.setWidget(self._cond_widget)
        scroll.setWidgetResizable(True)
        scroll.setMinimumHeight(120)
        scroll.setMaximumHeight(300)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet(
            f"QScrollArea {{ border:1px solid {CLR_SURFACE_SEPARATOR};"
            f" background:{CLR_SURFACE_APP_MAIN}; }}"
        )
        vb.addWidget(scroll)

        # Bottom buttons
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel = QPushButton("Cancel")
        cancel.setStyleSheet(ACTION_BUTTON_SECONDARY_STYLE)
        cancel.clicked.connect(self.reject)
        save = QPushButton("Save")
        save.setStyleSheet(ACTION_BUTTON_PRIMARY_STYLE)
        save.clicked.connect(self._on_save)
        btn_row.addWidget(cancel)
        btn_row.addWidget(save)
        vb.addLayout(btn_row)

        # Populate from existing CW or add one blank condition
        if cw:
            self._name_edit.setText(cw.name)
            self._delta_spin.setValue(cw.delta)
            for i in range(self._logic_cb.count()):
                if self._logic_cb.itemData(i) == cw.logic:
                    self._logic_cb.setCurrentIndex(i)
                    break
            for cond in cw.conditions:
                self._add_row(cond)
        else:
            self._add_empty()

    def _add_empty(self):
        self._add_row(Condition(field=FIELD_GENDER, operator=OP_EQ, value="m"))

    def _add_row(self, cond: Condition):
        row = _ConditionRow(self._cond_widget, cond, self._all_traits, self._stat_names)
        row.removed.connect(lambda r=row: self._remove_row(r))
        self._rows.append(row)
        # Insert before the trailing stretch
        self._cond_vb.insertWidget(self._cond_vb.count() - 1, row)

    def _remove_row(self, row: _ConditionRow):
        self._rows.remove(row)
        row.setParent(None)
        row.deleteLater()

    def _on_save(self):
        name = self._name_edit.text().strip()
        if not name:
            self._name_edit.setFocus()
            return
        self._saved_cw = ComplexWeight(
            name=name,
            delta=self._delta_spin.value(),
            logic=self._logic_cb.currentData(),
            conditions=[r.get_condition() for r in self._rows],
            enabled=True,
        )
        self.accept()

    def result_cw(self) -> ComplexWeight | None:
        return self._saved_cw


# ── Management dialog ─────────────────────────────────────────────────────────

class ComplexWeightsDialog(QDialog):
    """Non-modal window listing all Complex Weights with toggle/edit/delete."""

    cw_changed = Signal()

    def __init__(self, parent, complex_weights: list, all_traits: list, stat_names: list):
        super().__init__(parent)
        self.setWindowTitle("Complex Weights")
        self.setWindowFlags(self.windowFlags() | Qt.Tool)
        self.setAttribute(Qt.WA_DeleteOnClose, False)
        self.setMinimumWidth(480)
        self.setMinimumHeight(280)
        self.setStyleSheet(_DLG_BASE_STYLE)

        self._cws = complex_weights   # reference to view's list — modified in-place
        self._all_traits = all_traits
        self._stat_names = stat_names

        vb = QVBoxLayout(self)
        vb.setContentsMargins(12, 12, 12, 12)
        vb.setSpacing(8)

        # Header
        hdr = QLabel("Complex Weights")
        hdr.setStyleSheet(
            f"color:{CLR_TEXT_CONTENT_PRIMARY}; font-size:14px; font-weight:bold;"
        )
        vb.addWidget(hdr)

        hint = QLabel(
            "Each Complex Weight adds a score delta when its conditions match. "
            "Enabled weights create a new column in the scoring table."
        )
        hint.setStyleSheet(f"color:{CLR_TEXT_CONTENT_SECONDARY}; font-size:10px;")
        hint.setWordWrap(True)
        vb.addWidget(hint)

        # Scrollable list of CW rows
        self._list_widget = QWidget()
        self._list_widget.setStyleSheet(f"background:{CLR_SURFACE_APP_MAIN};")
        self._list_vb = QVBoxLayout(self._list_widget)
        self._list_vb.setContentsMargins(0, 0, 0, 0)
        self._list_vb.setSpacing(4)

        scroll = QScrollArea()
        scroll.setWidget(self._list_widget)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet(
            f"QScrollArea {{ border:1px solid {CLR_SURFACE_SEPARATOR}; }}"
        )
        vb.addWidget(scroll, stretch=1)

        # Add button
        add_btn = QPushButton("+ Add Complex Weight")
        add_btn.setStyleSheet(ACTION_BUTTON_SECONDARY_LARGE_STYLE)
        add_btn.clicked.connect(self._on_add)
        vb.addWidget(add_btn)

        self._rebuild()

    # ── Row management ────────────────────────────────────────────────────────

    def _rebuild(self):
        while self._list_vb.count():
            item = self._list_vb.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not self._cws:
            empty_lbl = QLabel("No complex weights yet. Click '+ Add' to create one.")
            empty_lbl.setStyleSheet(f"color:{CLR_TEXT_LABEL_COUNT}; font-size:10px;")
            empty_lbl.setAlignment(Qt.AlignCenter)
            self._list_vb.addWidget(empty_lbl)
        else:
            for idx, cw in enumerate(self._cws):
                self._list_vb.addWidget(self._make_row(idx, cw))

        self._list_vb.addStretch()

    def _make_row(self, idx: int, cw: ComplexWeight) -> QWidget:
        row = QWidget()
        row.setStyleSheet(
            f"background:{CLR_SURFACE_APP_ALT}; border-radius:3px;"
            f" border:1px solid {CLR_SURFACE_SEPARATOR};"
        )
        hb = QHBoxLayout(row)
        hb.setContentsMargins(8, 4, 8, 4)
        hb.setSpacing(6)

        chk = QCheckBox()
        chk.setChecked(cw.enabled)
        chk.stateChanged.connect(lambda s, i=idx: self._on_toggle(i, bool(s)))
        hb.addWidget(chk)

        n = len(cw.conditions)
        sign = "+" if cw.delta >= 0 else ""
        delta_clr = CLR_VALUE_POS if cw.delta > 0 else CLR_VALUE_NEG if cw.delta < 0 else CLR_VALUE_NEUTRAL
        dim_clr = CLR_TEXT_LABEL_COUNT
        lbl = QLabel(
            f"<b style='color:{CLR_TEXT_CONTENT_PRIMARY}'>{cw.name}</b>"
            f"  <span style='color:{delta_clr}'>{sign}{cw.delta:.1f} pts</span>"
            f"  <span style='color:{dim_clr}'>{cw.logic} · "
            f"{n} condition{'s' if n != 1 else ''}</span>"
        )
        lbl.setTextFormat(Qt.RichText)
        lbl.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        hb.addWidget(lbl, stretch=1)

        edit_btn = QPushButton("Edit")
        edit_btn.setStyleSheet(ACTION_BUTTON_SECONDARY_STYLE)
        edit_btn.setFixedWidth(44)
        edit_btn.clicked.connect(lambda _, i=idx: self._on_edit(i))
        hb.addWidget(edit_btn)

        del_btn = QPushButton("✕")
        del_btn.setFixedWidth(22)
        del_btn.setStyleSheet(ACTION_BUTTON_SECONDARY_STYLE)
        del_btn.clicked.connect(lambda _, i=idx: self._on_delete(i))
        hb.addWidget(del_btn)

        return row

    # ── Action handlers ───────────────────────────────────────────────────────

    def _on_add(self):
        dlg = _CWEditorDialog(self, None, self._all_traits, self._stat_names)
        if dlg.exec():
            new_cw = dlg.result_cw()
            if new_cw:
                self._cws.append(new_cw)
                self._rebuild()
                self.cw_changed.emit()

    def _on_edit(self, idx: int):
        dlg = _CWEditorDialog(self, self._cws[idx], self._all_traits, self._stat_names)
        if dlg.exec():
            updated = dlg.result_cw()
            if updated:
                updated.enabled = self._cws[idx].enabled
                self._cws[idx] = updated
                self._rebuild()
                self.cw_changed.emit()

    def _on_delete(self, idx: int):
        del self._cws[idx]
        self._rebuild()
        self.cw_changed.emit()

    def _on_toggle(self, idx: int, enabled: bool):
        self._cws[idx].enabled = enabled
        self.cw_changed.emit()

    def refresh_traits(self, all_traits: list):
        """Update available trait list (call when cats reload while dialog is open)."""
        self._all_traits = all_traits
