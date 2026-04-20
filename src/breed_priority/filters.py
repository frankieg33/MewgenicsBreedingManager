"""Filter dialog and state for the Breed Priority view.

Standalone module — no imports from mewgenics_manager or breed_priority.
"""

from PySide6.QtWidgets import (
    QDialog, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QScrollArea,
    QCheckBox, QComboBox, QPushButton, QLineEdit, QFrame,
    QGraphicsOpacityEffect,
)
from PySide6.QtCore import Qt, Signal

from .styles import (
    ACTION_BUTTON_PRIMARY_LARGE_STYLE, ACTION_BUTTON_PRIMARY_COMPACT_STYLE,
    ACTION_BUTTON_SECONDARY_LARGE_STYLE, TOGGLE_BUTTON_INACTIVE_COMPACT_STYLE,
    checkbox_style,
)
from .theme import (
    CLR_TEXT_CONTENT_PRIMARY, CLR_TEXT_CONTENT_SECONDARY, CLR_TEXT_LABEL_UI,
    CLR_TEXT_LABEL_COUNT, CLR_TEXT_CONTENT_MUTED,
    CLR_SURFACE_APP_MAIN, CLR_SURFACE_APP_ALT, CLR_SURFACE_PANEL,
    CLR_SURFACE_HEADER, CLR_SURFACE_HEADER_BORDER, CLR_SURFACE_SEPARATOR,
)

# ── Thresholds (must match breed_priority.py TRAIT_*_THRESHOLD) ──────────────
FILTER_TRAIT_LOW  = 0.3
FILTER_TRAIT_HIGH = 0.7

_OP_OPTIONS = ["Less Than", "Equals", "Greater Than"]

# ── Styles ────────────────────────────────────────────────────────────────────
_DLG_STYLE    = f"background:{CLR_SURFACE_APP_MAIN}; color:{CLR_TEXT_CONTENT_PRIMARY};"
_SCROLL_STYLE = f"QScrollArea {{ background:{CLR_SURFACE_APP_MAIN}; border:none; }} QWidget {{ background:{CLR_SURFACE_APP_MAIN}; }}"
_SECTION_LBL  = f"color:{CLR_TEXT_LABEL_COUNT}; font-size:9px; font-weight:bold; letter-spacing:1px; margin-top:2px;"
_ROW_LBL_ON   = f"color:{CLR_TEXT_CONTENT_SECONDARY}; font-size:11px;"
_ROW_LBL_OFF  = f"color:{CLR_TEXT_CONTENT_MUTED}; font-size:11px;"
_BTN_STYLE = ACTION_BUTTON_SECONDARY_LARGE_STYLE  # dialog-level inactive button
_APPLY_BTN_STYLE = ACTION_BUTTON_PRIMARY_LARGE_STYLE  # dialog-level confirm/apply button
_COMBO_STYLE = (
    f"QComboBox {{ background:{CLR_SURFACE_APP_ALT}; color:{CLR_TEXT_CONTENT_SECONDARY}; border:1px solid {CLR_SURFACE_SEPARATOR};"
    " padding:1px 4px; font-size:11px; }"
    "QComboBox::drop-down { border:none; }"
    f"QComboBox QAbstractItemView {{ background:{CLR_SURFACE_APP_ALT}; color:{CLR_TEXT_CONTENT_SECONDARY};"
    f" selection-background-color:#1e3060; border:1px solid {CLR_SURFACE_SEPARATOR}; }}"
)
_CHK_STYLE = checkbox_style(font_size=11, emphasize_checked=True)
_TOG_ON = ACTION_BUTTON_PRIMARY_COMPACT_STYLE  # compact row toggle — On state
_TOG_OFF = TOGGLE_BUTTON_INACTIVE_COMPACT_STYLE  # compact row toggle — Off state


# ── FilterState ───────────────────────────────────────────────────────────────

class FilterState:
    """Serializable filter configuration for the Breed Priority table."""

    STAT_NAMES = ["STR", "DEX", "CON", "INT", "SPD", "CHA", "LCK"]

    def __init__(self):
        # Age
        self.age_active = False;  self.age_value = 10;   self.age_op = "Less Than"
        # Gender
        self.gender_active  = False; self.gender_not     = False
        self.gender_male    = True;  self.gender_female  = True; self.gender_unknown = True
        # Individual stats
        self.stat_filters = {
            n: {"active": False, "value": 7, "op": "Equals"} for n in self.STAT_NAMES
        }
        # Stat sum
        self.sum_active = False;   self.sum_value = 28;  self.sum_op = "Greater Than"
        # 7-stat count
        self.count7_active = False; self.count7_value = 0; self.count7_op = "Greater Than"
        # Aggro
        self.aggro_active = False;  self.aggro_not  = False
        self.aggro_low    = True;   self.aggro_med  = True;  self.aggro_high = True
        # Libido
        self.libido_active = False; self.libido_not = False
        self.libido_low    = True;  self.libido_med = True;  self.libido_high = True
        # Gene (average in-scope risk %)
        self.gene_active = False;  self.gene_value = 0;  self.gene_op = "Equals"
        # Genetically unique (shortcut: zero average risk)
        self.gene_unique_active = False
        # Children in scope
        self.children_active = False; self.children_value = 4; self.children_op = "Less Than"
        # Total score
        self.score_active = False; self.score_value = 0.0; self.score_op = "Greater Than"
        # Injuries
        self.injuries_active = False
        # Location
        self.location_active = False
        self.location_rooms: set = set()  # set of display name strings to include

    def is_any_active(self) -> bool:
        return any([
            self.age_active, self.gender_active,
            any(sf["active"] for sf in self.stat_filters.values()),
            self.sum_active, self.count7_active,
            self.aggro_active, self.libido_active,
            self.gene_active, self.gene_unique_active,
            self.children_active, self.score_active,
            self.injuries_active, self.location_active,
        ])

    def to_dict(self) -> dict:
        return {
            "age_active": self.age_active, "age_value": self.age_value, "age_op": self.age_op,
            "gender_active": self.gender_active, "gender_not": self.gender_not,
            "gender_male": self.gender_male, "gender_female": self.gender_female,
            "gender_unknown": self.gender_unknown,
            "stat_filters": {k: dict(v) for k, v in self.stat_filters.items()},
            "sum_active": self.sum_active, "sum_value": self.sum_value, "sum_op": self.sum_op,
            "count7_active": self.count7_active, "count7_value": self.count7_value,
            "count7_op": self.count7_op,
            "aggro_active": self.aggro_active, "aggro_not": self.aggro_not,
            "aggro_low": self.aggro_low, "aggro_med": self.aggro_med, "aggro_high": self.aggro_high,
            "libido_active": self.libido_active, "libido_not": self.libido_not,
            "libido_low": self.libido_low, "libido_med": self.libido_med, "libido_high": self.libido_high,
            "gene_active": self.gene_active, "gene_value": self.gene_value, "gene_op": self.gene_op,
            "gene_unique_active": self.gene_unique_active,
            "children_active": self.children_active, "children_value": self.children_value,
            "children_op": self.children_op,
            "score_active": self.score_active, "score_value": self.score_value,
            "score_op": self.score_op,
            "injuries_active": self.injuries_active,
            "location_active": self.location_active,
            "location_rooms": sorted(self.location_rooms),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "FilterState":
        state = cls()
        for k in [
            "age_active", "age_value", "age_op",
            "gender_active", "gender_not", "gender_male", "gender_female", "gender_unknown",
            "sum_active", "sum_value", "sum_op",
            "count7_active", "count7_value", "count7_op",
            "aggro_active", "aggro_not", "aggro_low", "aggro_med", "aggro_high",
            "libido_active", "libido_not", "libido_low", "libido_med", "libido_high",
            "gene_active", "gene_value", "gene_op",
            "gene_unique_active",
            "children_active", "children_value", "children_op",
            "score_active", "score_value", "score_op",
            "injuries_active", "location_active",
        ]:
            if k in d:
                setattr(state, k, d[k])
        if "location_rooms" in d:
            state.location_rooms = set(d["location_rooms"])
        for stat, sf in d.get("stat_filters", {}).items():
            if stat in state.stat_filters:
                state.stat_filters[stat].update(sf)
        return state


# ── Filter application ────────────────────────────────────────────────────────

def _compare(val: float, threshold: float, op: str) -> bool:
    if op == "Less Than":  return val < threshold
    if op == "Equals":     return val == threshold
    return val > threshold  # "Greater Than"


def cat_passes_filter(cat, score_result, ch_in_scope: int, state: FilterState,
                      trait_low: float = FILTER_TRAIT_LOW,
                      trait_high: float = FILTER_TRAIT_HIGH,
                      room_display: dict | None = None) -> bool:
    """Return True if cat should be visible given the active filters."""
    if not state.is_any_active():
        return True
    f = state

    if f.age_active:
        age = getattr(cat, "age", None)
        if age is None or not _compare(float(age), float(f.age_value), f.age_op):
            return False

    if f.gender_active:
        gd = getattr(cat, "gender_display", "?")
        ok = ((gd in ("M", "Male")) and f.gender_male
              or (gd in ("F", "Female")) and f.gender_female
              or (gd == "?") and f.gender_unknown)
        if f.gender_not:
            ok = not ok
        if not ok:
            return False

    for sn, sf in f.stat_filters.items():
        if sf["active"]:
            if not _compare(float(cat.base_stats.get(sn, 0)), float(sf["value"]), sf["op"]):
                return False

    if f.sum_active:
        if not _compare(float(sum(cat.base_stats.values())), float(f.sum_value), f.sum_op):
            return False

    if f.count7_active:
        c7 = sum(1 for v in cat.base_stats.values() if v == 7)
        if not _compare(float(c7), float(f.count7_value), f.count7_op):
            return False

    def _level(val, lo, hi):
        if val is None: return None
        if val >= hi:   return "High"
        if val < lo:    return "Low"
        return "Med"

    if f.aggro_active:
        lvl = _level(cat.aggression, trait_low, trait_high)
        ok = ((lvl == "Low") and f.aggro_low
              or (lvl == "Med") and f.aggro_med
              or (lvl == "High") and f.aggro_high)
        if f.aggro_not:
            ok = not ok
        if not ok:
            return False

    if f.libido_active:
        lvl = _level(cat.libido, trait_low, trait_high)
        ok = ((lvl == "Low") and f.libido_low
              or (lvl == "Med") and f.libido_med
              or (lvl == "High") and f.libido_high)
        if f.libido_not:
            ok = not ok
        if not ok:
            return False

    if f.gene_active:
        if score_result.scope_gene_risk is None:
            return False
        if not _compare(float(score_result.scope_gene_risk),
                        float(f.gene_value), f.gene_op):
            return False

    if f.gene_unique_active:
        if score_result.scope_gene_risk is None or score_result.scope_gene_risk != 0:
            return False

    if f.children_active:
        if not _compare(float(ch_in_scope), float(f.children_value), f.children_op):
            return False

    if f.score_active:
        if not _compare(score_result.total, float(f.score_value), f.score_op):
            return False

    if f.injuries_active:
        _total = getattr(cat, 'total_stats', None)
        _base  = getattr(cat, 'base_stats', {})
        if _total is None:
            return False   # no injury data → exclude when filter active
        _has_inj = any(
            _total.get(sn, _base.get(sn, 0)) < _base.get(sn, 0)
            for sn in _base
        )
        if not _has_inj:
            return False

    if f.location_active and f.location_rooms:
        raw_room = getattr(cat, 'room', None) or ""
        if room_display is not None:
            room_disp = room_display.get(raw_room, raw_room)
        else:
            room_disp = raw_room
        if room_disp not in f.location_rooms:
            return False

    return True


# ── Row widgets ───────────────────────────────────────────────────────────────

class _FilterSpin(QWidget):
    """Compact ▲/▼ spin with an editable value field (matches _WeightSpin style)."""

    valueChanged = Signal(object)

    _BTN = (
        f"QPushButton {{ color:{CLR_TEXT_CONTENT_SECONDARY}; background:#3a3a60; border:1px solid #4a4a80;"
        " font-size:8px; padding:0; }"
        "QPushButton:hover { background:#5050a0; }"
        "QPushButton:pressed { background:#6060c0; }"
    )
    _EDIT = (
        f"QLineEdit {{ color:{CLR_TEXT_CONTENT_SECONDARY}; font-size:10px; background:{CLR_SURFACE_APP_ALT};"
        f" border:1px solid {CLR_SURFACE_SEPARATOR}; border-right:none; padding:0 2px; }}"
        "QLineEdit:focus { border-color:#3a3a7a; }"
    )

    def __init__(self, value, min_val, max_val, step=1, is_float=False):
        super().__init__()
        self._min      = float(min_val)
        self._max      = float(max_val)
        self._step     = float(step)
        self._is_float = is_float
        self._value    = self._clamp(float(value))

        hb = QHBoxLayout(self)
        hb.setContentsMargins(0, 0, 0, 0)
        hb.setSpacing(0)

        self._edit = QLineEdit(self._fmt(self._value))
        self._edit.setFixedWidth(42)
        self._edit.setAlignment(Qt.AlignCenter)
        self._edit.setStyleSheet(self._EDIT)
        self._edit.editingFinished.connect(self._on_edit)
        self._edit.returnPressed.connect(self._on_edit)

        btn_col = QWidget()
        bv = QVBoxLayout(btn_col)
        bv.setContentsMargins(0, 0, 0, 0)
        bv.setSpacing(0)
        up = QPushButton("▲"); up.setFixedSize(18, 11); up.setStyleSheet(self._BTN)
        dn = QPushButton("▼"); dn.setFixedSize(18, 11); dn.setStyleSheet(self._BTN)
        up.clicked.connect(self._inc)
        dn.clicked.connect(self._dec)
        bv.addWidget(up); bv.addWidget(dn)

        hb.addWidget(self._edit)
        hb.addWidget(btn_col)

    def _clamp(self, v: float) -> float:
        v = max(self._min, min(self._max, v))
        return v if self._is_float else float(round(v))

    def _fmt(self, v: float) -> str:
        return f"{v:.1f}" if self._is_float else str(int(v))

    def _set(self, val: float):
        val = self._clamp(val)
        if val != self._value:
            self._value = val
            self._edit.setText(self._fmt(val))
            self.valueChanged.emit(self.value())

    def _inc(self): self._set(self._value + self._step)
    def _dec(self): self._set(self._value - self._step)

    def _on_edit(self):
        try:
            self._set(float(self._edit.text()))
        except ValueError:
            self._edit.setText(self._fmt(self._value))

    def value(self):
        return self._value if self._is_float else int(self._value)

    def setValue(self, val):
        self._value = self._clamp(float(val))
        self._edit.setText(self._fmt(self._value))


class _FilterRow(QWidget):
    """Base: toggle button + label + controls. Subclasses fill controls."""

    ROW_H = 30

    def __init__(self, label_text: str, active: bool):
        super().__init__()
        self.setMinimumHeight(self.ROW_H)
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 1, 0, 1)
        h.setSpacing(6)

        self._tog = QPushButton("●")
        self._tog.setCheckable(True)
        self._tog.setChecked(active)
        self._tog.setFixedSize(22, 20)
        self._tog.setStyleSheet(_TOG_ON if active else _TOG_OFF)
        self._tog.clicked.connect(self._on_toggle)
        h.addWidget(self._tog)

        self._lbl = QLabel(label_text)
        self._lbl.setFixedWidth(108)
        self._lbl.setStyleSheet(_ROW_LBL_ON if active else _ROW_LBL_OFF)
        h.addWidget(self._lbl)

        self._ctrl = QWidget()
        ctrl_h = QHBoxLayout(self._ctrl)
        ctrl_h.setContentsMargins(0, 0, 0, 0)
        ctrl_h.setSpacing(4)
        self._fill_controls(ctrl_h)
        self._opacity = QGraphicsOpacityEffect()
        self._ctrl.setGraphicsEffect(self._opacity)
        h.addWidget(self._ctrl)
        h.addStretch()

        self._update_enabled(active)

    def _fill_controls(self, layout: QHBoxLayout):
        pass  # subclasses implement

    def _on_toggle(self, checked: bool):
        self._tog.setStyleSheet(_TOG_ON if checked else _TOG_OFF)
        self._update_enabled(checked)

    def _update_enabled(self, active: bool):
        self._lbl.setStyleSheet(_ROW_LBL_ON if active else _ROW_LBL_OFF)
        self._opacity.setOpacity(1.0 if active else 0.25)

    def _is_active(self) -> bool:
        return self._tog.isChecked()


class _NumericFilterRow(_FilterRow):
    """Toggle + label + spinbox + operator combo."""

    def __init__(self, label_text: str, active: bool, value, op: str,
                 min_val: int = 0, max_val: int = 100, is_float: bool = False):
        self._init_value   = value
        self._init_op      = op
        self._min_val      = min_val
        self._max_val      = max_val
        self._is_float     = is_float
        super().__init__(label_text, active)

    def _fill_controls(self, layout: QHBoxLayout):
        self._spin = _FilterSpin(
            self._init_value, self._min_val, self._max_val,
            step=0.5 if self._is_float else 1,
            is_float=self._is_float,
        )
        layout.addWidget(self._spin)

        self._op = QComboBox()
        self._op.addItems(_OP_OPTIONS)
        self._op.setCurrentText(self._init_op)
        self._op.setFixedWidth(112)
        self._op.setStyleSheet(_COMBO_STYLE)
        layout.addWidget(self._op)

    def get_state(self) -> tuple:
        return self._is_active(), self._spin.value(), self._op.currentText()

    def set_state(self, active: bool, value, op: str):
        self._tog.setChecked(active)
        self._tog.setStyleSheet(_TOG_ON if active else _TOG_OFF)
        self._spin.setValue(value)
        self._op.setCurrentText(op)
        self._update_enabled(active)


class _CheckFilterRow(_FilterRow):
    """Toggle + label + Not checkbox + option checkboxes."""

    def __init__(self, label_text: str, active: bool, negate: bool,
                 options: list):  # list of (label_str, checked_bool)
        self._init_negate  = negate
        self._init_options = options
        super().__init__(label_text, active)

    def _fill_controls(self, layout: QHBoxLayout):
        self._not_chk = QCheckBox("Not")
        self._not_chk.setChecked(self._init_negate)
        self._not_chk.setStyleSheet(_CHK_STYLE)
        layout.addWidget(self._not_chk)

        sep = QFrame()
        sep.setFrameShape(QFrame.VLine)
        sep.setStyleSheet(f"color:{CLR_SURFACE_SEPARATOR};")
        sep.setFixedWidth(1)
        layout.addWidget(sep)

        self._opt_chks = []
        for lbl_text, checked in self._init_options:
            chk = QCheckBox(lbl_text)
            chk.setChecked(checked)
            chk.setStyleSheet(_CHK_STYLE)
            layout.addWidget(chk)
            self._opt_chks.append(chk)

    def get_state(self) -> tuple:
        return (self._is_active(),
                self._not_chk.isChecked(),
                [c.isChecked() for c in self._opt_chks])

    def set_state(self, active: bool, negate: bool, option_values: list):
        self._tog.setChecked(active)
        self._tog.setStyleSheet(_TOG_ON if active else _TOG_OFF)
        self._not_chk.setChecked(negate)
        for chk, val in zip(self._opt_chks, option_values):
            chk.setChecked(val)
        self._update_enabled(active)


class _BoolFilterRow(_FilterRow):
    """Toggle + label only — no additional controls.

    Active = show only cats where the condition is True (e.g. 'Has Injuries').
    """

    def _fill_controls(self, layout: QHBoxLayout):
        pass  # no extra controls needed

    def get_state(self) -> bool:
        return self._is_active()

    def set_state(self, active: bool):
        self._tog.setChecked(active)
        self._tog.setStyleSheet(_TOG_ON if active else _TOG_OFF)
        self._update_enabled(active)


class _LocationFilterRow(QWidget):
    """Toggle + 'Location' label + per-room checkboxes."""

    ROW_H = 30

    def __init__(self, active: bool, rooms: list, selected: set):
        super().__init__()
        self._rooms = rooms
        self.setMinimumHeight(self.ROW_H)

        h = QHBoxLayout(self)
        h.setContentsMargins(0, 1, 0, 1)
        h.setSpacing(6)

        self._tog = QPushButton("●")
        self._tog.setCheckable(True)
        self._tog.setChecked(active)
        self._tog.setFixedSize(22, 20)
        self._tog.setStyleSheet(_TOG_ON if active else _TOG_OFF)
        self._tog.clicked.connect(self._on_toggle)
        h.addWidget(self._tog)

        self._lbl = QLabel("Location")
        self._lbl.setFixedWidth(108)
        self._lbl.setStyleSheet(_ROW_LBL_ON if active else _ROW_LBL_OFF)
        h.addWidget(self._lbl)

        self._ctrl = QWidget()
        ctrl_h = QHBoxLayout(self._ctrl)
        ctrl_h.setContentsMargins(0, 0, 0, 0)
        ctrl_h.setSpacing(6)

        self._room_chks: list[tuple[str, QCheckBox]] = []
        for room in rooms:
            chk = QCheckBox(room)
            chk.setChecked(room in selected)
            chk.setStyleSheet(_CHK_STYLE)
            ctrl_h.addWidget(chk)
            self._room_chks.append((room, chk))

        self._opacity = QGraphicsOpacityEffect()
        self._ctrl.setGraphicsEffect(self._opacity)
        h.addWidget(self._ctrl)
        h.addStretch()

        self._update_enabled(active)

    def _on_toggle(self, checked: bool):
        self._tog.setStyleSheet(_TOG_ON if checked else _TOG_OFF)
        self._update_enabled(checked)

    def _update_enabled(self, active: bool):
        self._lbl.setStyleSheet(_ROW_LBL_ON if active else _ROW_LBL_OFF)
        self._opacity.setOpacity(1.0 if active else 0.25)

    def get_state(self) -> tuple:
        active = self._tog.isChecked()
        selected = {room for room, chk in self._room_chks if chk.isChecked()}
        return active, selected

    def set_state(self, active: bool, selected: set):
        self._tog.setChecked(active)
        self._tog.setStyleSheet(_TOG_ON if active else _TOG_OFF)
        self._update_enabled(active)
        for room, chk in self._room_chks:
            chk.setChecked(room in selected)


# ── FilterDialog ──────────────────────────────────────────────────────────────

class FilterDialog(QDialog):
    """Popup window to configure all Breed Priority filters."""

    def __init__(self, parent, initial_state: FilterState,
                 available_rooms: list | None = None):
        super().__init__(parent)
        self.setWindowTitle("Breed Priority — Filters")
        self.setModal(True)
        self.setStyleSheet(_DLG_STYLE)
        self.resize(460, 620)
        self._state = initial_state
        self._available_rooms = available_rooms or []
        self._applied: FilterState | None = None
        self._build_ui()

    def applied_state(self) -> "FilterState | None":
        """Non-None only when Apply was clicked."""
        return self._applied

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Header ────────────────────────────────────────────────────────────
        hdr = QWidget()
        hdr.setStyleSheet(f"background:{CLR_SURFACE_HEADER}; border-bottom:1px solid {CLR_SURFACE_HEADER_BORDER};")
        hdr.setFixedHeight(42)
        hh = QHBoxLayout(hdr)
        hh.setContentsMargins(14, 0, 14, 0)
        title = QLabel("Filters")
        title.setStyleSheet(f"color:{CLR_TEXT_CONTENT_PRIMARY}; font-size:14px; font-weight:bold;")
        hh.addWidget(title)
        hh.addStretch()
        reset_btn = QPushButton("Reset All")
        reset_btn.setStyleSheet(_BTN_STYLE)
        reset_btn.clicked.connect(self._reset_all)
        hh.addWidget(reset_btn)
        root.addWidget(hdr)

        # ── Scrollable content ────────────────────────────────────────────────
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(_SCROLL_STYLE)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        content = QWidget()
        content.setStyleSheet(f"background:{CLR_SURFACE_PANEL};")
        cv = QVBoxLayout(content)
        cv.setContentsMargins(14, 10, 14, 10)
        cv.setSpacing(3)
        scroll.setWidget(content)
        root.addWidget(scroll)

        f = self._state

        def _sep():
            s = QFrame()
            s.setFrameShape(QFrame.HLine)
            s.setStyleSheet(f"color:{CLR_SURFACE_SEPARATOR}; margin:2px 0;")
            cv.addWidget(s)

        def _section(text: str):
            lbl = QLabel(text)
            lbl.setStyleSheet(_SECTION_LBL)
            cv.addWidget(lbl)

        # ── Age ───────────────────────────────────────────────────────────────
        self._age_row = _NumericFilterRow(
            "Age", f.age_active, f.age_value, f.age_op,
            min_val=0, max_val=100, is_float=False)
        cv.addWidget(self._age_row)

        _sep()

        # ── Gender ────────────────────────────────────────────────────────────
        self._gender_row = _CheckFilterRow(
            "Gender", f.gender_active, f.gender_not,
            [("M", f.gender_male), ("F", f.gender_female), ("?", f.gender_unknown)])
        cv.addWidget(self._gender_row)

        _sep()
        _section("ATTRIBUTES")

        # ── Stats ─────────────────────────────────────────────────────────────
        self._stat_rows: dict[str, _NumericFilterRow] = {}
        for sn in FilterState.STAT_NAMES:
            sf = f.stat_filters[sn]
            row = _NumericFilterRow(
                sn, sf["active"], sf["value"], sf["op"],
                min_val=0, max_val=7, is_float=False)
            cv.addWidget(row)
            self._stat_rows[sn] = row

        _sep()

        # ── Sum ───────────────────────────────────────────────────────────────
        self._sum_row = _NumericFilterRow(
            "Sum", f.sum_active, f.sum_value, f.sum_op,
            min_val=0, max_val=49, is_float=False)
        cv.addWidget(self._sum_row)

        # ── 7-stat count ──────────────────────────────────────────────────────
        self._count7_row = _NumericFilterRow(
            "# of 7 Stats", f.count7_active, f.count7_value, f.count7_op,
            min_val=0, max_val=7, is_float=False)
        cv.addWidget(self._count7_row)

        _sep()
        _section("PERSONALITY")

        # ── Aggro ─────────────────────────────────────────────────────────────
        self._aggro_row = _CheckFilterRow(
            "Aggro", f.aggro_active, f.aggro_not,
            [("Low", f.aggro_low), ("Med", f.aggro_med), ("High", f.aggro_high)])
        cv.addWidget(self._aggro_row)

        # ── Libido ────────────────────────────────────────────────────────────
        self._libido_row = _CheckFilterRow(
            "Libido", f.libido_active, f.libido_not,
            [("Low", f.libido_low), ("Med", f.libido_med), ("High", f.libido_high)])
        cv.addWidget(self._libido_row)

        _sep()
        _section("GENETICS")

        # ── Gene ──────────────────────────────────────────────────────────────
        self._gene_row = _NumericFilterRow(
            "Gene Risk %", f.gene_active, f.gene_value, f.gene_op,
            min_val=0, max_val=100, is_float=False)
        cv.addWidget(self._gene_row)

        # ── Genetically Unique ────────────────────────────────────────────────
        self._gene_unique_row = _BoolFilterRow("Genetically Unique", f.gene_unique_active)
        cv.addWidget(self._gene_unique_row)

        # ── Children ──────────────────────────────────────────────────────────
        self._children_row = _NumericFilterRow(
            "Children", f.children_active, f.children_value, f.children_op,
            min_val=0, max_val=100, is_float=False)
        cv.addWidget(self._children_row)

        # ── Score ─────────────────────────────────────────────────────────────
        self._score_row = _NumericFilterRow(
            "Score", f.score_active, f.score_value, f.score_op,
            min_val=-100, max_val=100, is_float=True)
        cv.addWidget(self._score_row)

        _sep()
        _section("STATUS")

        # ── Injuries ──────────────────────────────────────────────────────────
        self._injuries_row = _BoolFilterRow("Has Injuries", f.injuries_active)
        cv.addWidget(self._injuries_row)

        # ── Location ──────────────────────────────────────────────────────────
        if self._available_rooms:
            _sep()
            _section("LOCATION")
            self._loc_row = _LocationFilterRow(
                f.location_active, self._available_rooms, f.location_rooms)
            cv.addWidget(self._loc_row)
        else:
            self._loc_row = None

        cv.addStretch()

        # ── Footer ────────────────────────────────────────────────────────────
        ftr = QWidget()
        ftr.setStyleSheet(f"background:{CLR_SURFACE_HEADER}; border-top:1px solid {CLR_SURFACE_HEADER_BORDER};")
        ftr.setFixedHeight(46)
        fh = QHBoxLayout(ftr)
        fh.setContentsMargins(14, 0, 14, 0)
        fh.setSpacing(8)
        fh.addStretch()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setStyleSheet(_BTN_STYLE)
        cancel_btn.clicked.connect(self.reject)
        fh.addWidget(cancel_btn)
        apply_btn = QPushButton("Apply")
        apply_btn.setStyleSheet(_APPLY_BTN_STYLE)
        apply_btn.clicked.connect(self._apply)
        fh.addWidget(apply_btn)
        root.addWidget(ftr)

    # ── Actions ───────────────────────────────────────────────────────────────

    def _reset_all(self):
        from PySide6.QtWidgets import QMessageBox
        mb = QMessageBox(self)
        mb.setWindowTitle("Reset Filters")
        mb.setText("Reset all filters to defaults?")
        mb.setStandardButtons(QMessageBox.Ok | QMessageBox.Cancel)
        mb.setDefaultButton(QMessageBox.Cancel)
        mb.setStyleSheet(_DLG_STYLE)
        if mb.exec() != QMessageBox.Ok:
            return
        d = FilterState()
        self._age_row.set_state(d.age_active, d.age_value, d.age_op)
        self._gender_row.set_state(d.gender_active, d.gender_not,
                                   [d.gender_male, d.gender_female, d.gender_unknown])
        for sn, row in self._stat_rows.items():
            sf = d.stat_filters[sn]
            row.set_state(sf["active"], sf["value"], sf["op"])
        self._sum_row.set_state(d.sum_active, d.sum_value, d.sum_op)
        self._count7_row.set_state(d.count7_active, d.count7_value, d.count7_op)
        self._aggro_row.set_state(d.aggro_active, d.aggro_not,
                                  [d.aggro_low, d.aggro_med, d.aggro_high])
        self._libido_row.set_state(d.libido_active, d.libido_not,
                                   [d.libido_low, d.libido_med, d.libido_high])
        self._gene_row.set_state(d.gene_active, d.gene_value, d.gene_op)
        self._gene_unique_row.set_state(d.gene_unique_active)
        self._children_row.set_state(d.children_active, d.children_value, d.children_op)
        self._score_row.set_state(d.score_active, d.score_value, d.score_op)
        self._injuries_row.set_state(d.injuries_active)
        if self._loc_row is not None:
            self._loc_row.set_state(d.location_active, set())

    def _apply(self):
        f = FilterState()
        f.age_active, f.age_value, f.age_op         = self._age_row.get_state()
        f.gender_active, f.gender_not, gvals         = self._gender_row.get_state()
        f.gender_male, f.gender_female, f.gender_unknown = gvals
        for sn, row in self._stat_rows.items():
            active, value, op = row.get_state()
            f.stat_filters[sn] = {"active": active, "value": value, "op": op}
        f.sum_active, f.sum_value, f.sum_op          = self._sum_row.get_state()
        f.count7_active, f.count7_value, f.count7_op = self._count7_row.get_state()
        f.aggro_active, f.aggro_not, avals           = self._aggro_row.get_state()
        f.aggro_low, f.aggro_med, f.aggro_high        = avals
        f.libido_active, f.libido_not, lvals         = self._libido_row.get_state()
        f.libido_low, f.libido_med, f.libido_high     = lvals
        f.gene_active, f.gene_value, f.gene_op       = self._gene_row.get_state()
        f.gene_unique_active                          = self._gene_unique_row.get_state()
        f.children_active, f.children_value, f.children_op = self._children_row.get_state()
        f.score_active, f.score_value, f.score_op    = self._score_row.get_state()
        f.injuries_active                             = self._injuries_row.get_state()
        if self._loc_row is not None:
            f.location_active, f.location_rooms = self._loc_row.get_state()
        self._applied = f
        self.accept()
