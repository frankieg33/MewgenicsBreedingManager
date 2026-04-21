"""ManualScoringView — configurable point-based cat scoring and triage."""

from typing import Optional

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QHeaderView,
    QAbstractItemView, QSplitter, QLineEdit, QSpinBox,
    QTableWidget, QTableWidgetItem, QComboBox, QCheckBox,
    QPushButton, QToolButton, QScrollArea, QFrame, QListWidget,
    QListWidgetItem, QSizePolicy,
)
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QBrush

from save_parser import Cat, ROOM_KEYS
from mewgenics.utils.localization import _tr, ROOM_DISPLAY
from mewgenics.utils.config import _load_ui_state, _save_ui_state, _saved_manual_scoring_auto_calc
from mewgenics.utils.cat_analysis import _cat_base_sum
from mewgenics.utils.calibration import _trait_label_from_value
from mewgenics.utils.abilities import _ability_tip, _mutation_display_name
from mewgenics.utils.trait_ratings import TraitRatings


# ---------------------------------------------------------------------------
# Pure scoring function (no Qt)
# ---------------------------------------------------------------------------

_DEFAULT_CONFIG = {
    "stat_weight": 1,
    "desired_mutations": [],
    "desired_mutation_weights": {},   # mutation_name -> int  (per-mutation override)
    "desired_use_individual": False,
    "desired_default_weight": 1,
    "undesired_mutations": [],
    "undesired_mutation_weights": {},
    "undesired_use_individual": False,
    "undesired_default_weight": -5,
    "desired_disorders": [],
    "desired_disorder_weights": {},
    "desired_disorder_use_individual": False,
    "desired_disorder_default_weight": 1,
    "undesired_disorders": [],
    "undesired_disorder_weights": {},
    "undesired_disorder_use_individual": False,
    "undesired_disorder_default_weight": -5,
    "inbredness_weights": {
        "not": 2, "slightly": 0, "moderately": -1,
        "highly": -10, "extremely": -10,
    },
    "libido_weights": {"high": 1, "average": 0, "low": -10},
    "aggression_weights": {"high": 1, "average": 0, "low": -1},
    "passive_weight": 1,
    "extra_spell_weight": 0,
    "sexuality_weights": {"straight": 0, "bi": -10, "gay": -10},
}


def compute_cat_score(cat, config: dict) -> tuple[int, dict[str, int]]:
    """Return (total_score, breakdown_dict) for a cat given scoring config."""
    breakdown: dict[str, int] = {}

    # Stats
    breakdown["stats"] = config.get("stat_weight", 1) * _cat_base_sum(cat)

    # Desired mutations
    desired = set(config.get("desired_mutations", []))
    use_individual_d = config.get("desired_use_individual", False)
    per_weights_d = config.get("desired_mutation_weights", {})
    default_d = config.get("desired_default_weight", 1)
    d_score = 0
    for m in (cat.mutations or []):
        if m in desired:
            d_score += per_weights_d.get(m, default_d) if use_individual_d else default_d
    breakdown["desired"] = d_score

    # Undesired mutations
    undesired = set(config.get("undesired_mutations", []))
    use_individual_u = config.get("undesired_use_individual", False)
    per_weights_u = config.get("undesired_mutation_weights", {})
    default_u = config.get("undesired_default_weight", -5)
    u_score = 0
    for m in (cat.mutations or []):
        if m in undesired:
            u_score += per_weights_u.get(m, default_u) if use_individual_u else default_u
    breakdown["undesired"] = u_score

    # Desired disorders
    desired_dis = set(config.get("desired_disorders", []))
    use_ind_dd = config.get("desired_disorder_use_individual", False)
    per_w_dd = config.get("desired_disorder_weights", {})
    def_dd = config.get("desired_disorder_default_weight", 1)
    dd_score = 0
    for d in (cat.disorders or []):
        if d in desired_dis:
            dd_score += per_w_dd.get(d, def_dd) if use_ind_dd else def_dd
    breakdown["desired_dis"] = dd_score

    # Undesired disorders
    undesired_dis = set(config.get("undesired_disorders", []))
    use_ind_ud = config.get("undesired_disorder_use_individual", False)
    per_w_ud = config.get("undesired_disorder_weights", {})
    def_ud = config.get("undesired_disorder_default_weight", -5)
    ud_score = 0
    for d in (cat.disorders or []):
        if d in undesired_dis:
            ud_score += per_w_ud.get(d, def_ud) if use_ind_ud else def_ud
    breakdown["undesired_dis"] = ud_score

    # Inbredness
    inb_label = _trait_label_from_value("inbredness", cat.inbredness) if cat.inbredness is not None else ""
    inb_weights = config.get("inbredness_weights", {})
    breakdown["inbredness"] = inb_weights.get(inb_label, 0)

    # Libido
    lib_label = _trait_label_from_value("libido", cat.libido) if cat.libido is not None else ""
    lib_weights = config.get("libido_weights", {})
    breakdown["libido"] = lib_weights.get(lib_label, 0)

    # Aggression
    agg_label = _trait_label_from_value("aggression", cat.aggression) if cat.aggression is not None else ""
    agg_weights = config.get("aggression_weights", {})
    breakdown["aggression"] = agg_weights.get(agg_label, 0)

    # Passives
    breakdown["passives"] = config.get("passive_weight", 1) * len(cat.passive_abilities or [])

    # Extra spells (abilities beyond the first)
    n_abilities = len(cat.abilities or [])
    breakdown["spells"] = config.get("extra_spell_weight", 0) * max(0, n_abilities - 1)

    # Sexuality — ? gender cats can breed with anyone, so sexuality is irrelevant
    if getattr(cat, "gender", "") == "?":
        breakdown["sexuality"] = 0
    else:
        sex_weights = config.get("sexuality_weights", {})
        breakdown["sexuality"] = sex_weights.get(getattr(cat, "sexuality", ""), 0)

    total = sum(breakdown.values())
    return total, breakdown


# ---------------------------------------------------------------------------
# View
# ---------------------------------------------------------------------------

_BREAKDOWN_KEYS = ["stats", "desired", "undesired", "desired_dis", "undesired_dis", "inbredness", "libido", "aggression", "passives", "spells", "sexuality"]
_BREAKDOWN_LABELS = ["Stats", "Desired", "Undes.", "+Dis.", "-Dis.", "Inbred", "Libido", "Aggr", "Passives", "Spells", "Sexuality"]

_COL_NAME = 0
_COL_ROOM = 1
_COL_TOTAL = 2
_COL_FIRST_BREAKDOWN = 3
_NUM_COLS = _COL_FIRST_BREAKDOWN + len(_BREAKDOWN_KEYS)

_HEADER_LABELS = ["Name", "Room", "Score"] + _BREAKDOWN_LABELS


# ---------------------------------------------------------------------------
# Reusable mutation selector widget
# ---------------------------------------------------------------------------

class _MutationSelector(QWidget):
    """Collapsible mutation checklist with blanket or per-mutation weights."""

    def __init__(self, title: str, default_weight: int, parent_view: "ManualScoringView"):
        super().__init__()
        self._parent_view = parent_view
        self._default_weight = default_weight
        self._suppress = False
        # Per-mutation spinbox widgets, keyed by raw mutation name.
        self._per_spins: dict[str, QSpinBox] = {}

        vb = QVBoxLayout(self)
        vb.setContentsMargins(0, 0, 0, 0)
        vb.setSpacing(4)

        # Toggle button
        self._toggle = QToolButton()
        self._toggle.setText(f"{title} (0 selected) \u25BC")
        self._toggle.setCheckable(True)
        self._toggle.toggled.connect(self._on_toggled)
        self._title = title
        vb.addWidget(self._toggle)

        # Collapsible body
        self._body = QWidget()
        body_vb = QVBoxLayout(self._body)
        body_vb.setContentsMargins(0, 0, 0, 0)
        body_vb.setSpacing(4)

        # Weight mode toggle
        mode_row = QHBoxLayout()
        mode_row.setSpacing(6)
        lbl = QLabel("Default weight:")
        lbl.setMinimumWidth(100)
        mode_row.addWidget(lbl)
        self._spin_default = QSpinBox()
        self._spin_default.setRange(-99, 99)
        self._spin_default.setValue(default_weight)
        self._spin_default.valueChanged.connect(self._on_changed)
        mode_row.addWidget(self._spin_default)
        mode_row.addStretch()
        body_vb.addLayout(mode_row)

        self._chk_individual = QCheckBox("Set individual weights")
        self._chk_individual.setToolTip("Enable to assign a custom weight to each item separately")
        self._chk_individual.toggled.connect(self._on_individual_toggled)
        body_vb.addWidget(self._chk_individual)

        # Search
        self._search = QLineEdit()
        self._search.setPlaceholderText("Filter...")
        self._search.textChanged.connect(self._filter_list)
        body_vb.addWidget(self._search)

        # Show filter (All / Checked / Unchecked)
        filter_row = QHBoxLayout()
        filter_row.setSpacing(4)
        filter_row.addWidget(QLabel("Show:"))
        self._filter_mode = "all"
        for mode_label, mode_key in [("All", "all"), ("Checked", "checked"), ("Unchecked", "unchecked")]:
            btn = QPushButton(mode_label)
            btn.setCheckable(True)
            btn.setChecked(mode_key == "all")
            btn.setFixedHeight(22)
            btn.clicked.connect(lambda _, mk=mode_key, b=btn: self._set_filter_mode(mk, b))
            filter_row.addWidget(btn)
            if mode_key == "all":
                self._btn_filter_all = btn
            elif mode_key == "checked":
                self._btn_filter_checked = btn
            else:
                self._btn_filter_unchecked = btn
        filter_row.addStretch()
        body_vb.addLayout(filter_row)

        # Mutation list (items are rows with checkbox + optional per-mutation spinbox)
        self._list = QListWidget()
        self._list.setMinimumHeight(250)
        self._list.setMaximumHeight(400)
        self._list.itemChanged.connect(self._on_item_changed)
        body_vb.addWidget(self._list)

        # Bulk buttons
        btn_row = QHBoxLayout()
        btn_all = QPushButton("Select All")
        btn_all.clicked.connect(lambda: self._set_all(True))
        btn_none = QPushButton("Clear All")
        btn_none.clicked.connect(lambda: self._set_all(False))
        btn_row.addWidget(btn_all)
        btn_row.addWidget(btn_none)
        btn_row.addStretch()
        body_vb.addLayout(btn_row)

        self._body.hide()
        vb.addWidget(self._body)

    # -- Public API --

    def rebuild(self, all_mutations: list[str], prev_checked: set[str],
                prev_weights: dict[str, int]):
        """Rebuild list items from the set of all mutation names."""
        self._suppress = True
        self._list.clear()
        self._per_spins.clear()
        for m in all_mutations:
            display = _mutation_display_name(m)
            tip = _ability_tip(m)
            label = f"{display}  —  {tip}" if tip else display
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, m)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if m in prev_checked else Qt.Unchecked)
            if tip:
                item.setToolTip(tip)
            self._list.addItem(item)
        self._suppress = False
        self._update_toggle_text()

    def get_checked(self) -> list[str]:
        result = []
        for i in range(self._list.count()):
            item = self._list.item(i)
            # When per-mutation mode is active, the built-in checkState may be
            # unreliable (ItemIsUserCheckable flag removed).  Read from the
            # embedded QCheckBox widget instead when present.
            widget = self._list.itemWidget(item)
            if widget is not None:
                chk = widget.findChild(QCheckBox)
                if chk is not None:
                    if chk.isChecked():
                        result.append(item.data(Qt.UserRole))
                    continue
            if item.checkState() == Qt.Checked:
                result.append(item.data(Qt.UserRole))
        return result

    def get_per_weights(self) -> dict[str, int]:
        """Return per-mutation weights for checked mutations."""
        result = {}
        for i in range(self._list.count()):
            item = self._list.item(i)
            if item.checkState() == Qt.Checked:
                name = item.data(Qt.UserRole)
                widget = self._list.itemWidget(item)
                if widget is not None:
                    spin = widget.findChild(QSpinBox)
                    if spin is not None:
                        result[name] = spin.value()
        return result

    def use_individual(self) -> bool:
        return self._chk_individual.isChecked()

    def default_weight(self) -> int:
        return self._spin_default.value()

    def set_state(self, *, use_individual: bool, default_weight: int,
                  per_weights: dict[str, int]):
        """Restore state from saved config."""
        self._suppress = True
        self._spin_default.setValue(default_weight)
        self._chk_individual.setChecked(use_individual)
        self._suppress = False
        # Per-weights are applied after rebuild via _apply_per_weights
        self._saved_per_weights = per_weights

    def apply_per_weights(self):
        """Apply saved per-weights after rebuild has populated items."""
        weights = getattr(self, "_saved_per_weights", {})
        if not weights:
            return
        if self._chk_individual.isChecked():
            self._show_per_spins()
            for i in range(self._list.count()):
                item = self._list.item(i)
                name = item.data(Qt.UserRole)
                if name in weights:
                    widget = self._list.itemWidget(item)
                    if widget is not None:
                        spin = widget.findChild(QSpinBox)
                        if spin is not None:
                            spin.setValue(weights[name])

    def reset(self):
        self._suppress = True
        self._spin_default.setValue(self._default_weight)
        self._chk_individual.setChecked(False)
        for i in range(self._list.count()):
            self._list.item(i).setCheckState(Qt.Unchecked)
        self._suppress = False
        self._hide_per_spins()
        self._update_toggle_text()

    # -- Internals --

    def _on_toggled(self, expanded: bool):
        self._body.setVisible(expanded)
        self._update_toggle_text()

    def _on_individual_toggled(self, checked: bool):
        if checked:
            self._show_per_spins()
        else:
            self._hide_per_spins()
        self._on_changed()

    def _show_per_spins(self):
        """Attach per-mutation QSpinBox widgets to each list item."""
        for i in range(self._list.count()):
            item = self._list.item(i)
            if self._list.itemWidget(item) is not None:
                continue
            w = QWidget()
            row = QHBoxLayout(w)
            row.setContentsMargins(2, 0, 2, 0)
            row.setSpacing(4)
            chk = QCheckBox()
            chk.setChecked(item.checkState() == Qt.Checked)
            chk.toggled.connect(lambda checked, it=item: self._sync_check_from_widget(it, checked))
            row.addWidget(chk)
            spin = QSpinBox()
            spin.setRange(-99, 99)
            spin.setValue(self._spin_default.value())
            spin.setFixedWidth(60)
            spin.valueChanged.connect(self._on_changed)
            row.addWidget(spin)
            full_text = item.text()
            # Show only mutation name; full description goes in tooltip
            name = item.data(Qt.UserRole)
            short_text = _mutation_display_name(name) if name else full_text
            lbl = QLabel(short_text)
            lbl.setStyleSheet("color:#ccc;")
            lbl.setToolTip(item.toolTip() or full_text)
            row.addWidget(lbl, 1)
            self._list.setItemWidget(item, w)
            # Disable the built-in checkbox since the widget checkbox takes over
            item.setFlags(item.flags() & ~Qt.ItemIsUserCheckable)
            item.setText("")
            item.setSizeHint(w.sizeHint())

    def _sync_check_from_widget(self, item: QListWidgetItem, checked: bool):
        """Sync item check state from the embedded QCheckBox."""
        # Temporarily re-enable checkable to set the state
        item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
        item.setCheckState(Qt.Checked if checked else Qt.Unchecked)
        item.setFlags(item.flags() & ~Qt.ItemIsUserCheckable)

    def _hide_per_spins(self):
        """Remove per-mutation QSpinBox widgets and restore built-in checkboxes."""
        for i in range(self._list.count()):
            item = self._list.item(i)
            widget = self._list.itemWidget(item)
            if widget is not None:
                # Reconstruct full display text from raw mutation name
                name = item.data(Qt.UserRole)
                display = _mutation_display_name(name) if name else ""
                tip = _ability_tip(name) if name else ""
                full = f"{display}  \u2014  {tip}" if tip else display
                item.setText(full)
                self._list.removeItemWidget(item)
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)

    def _set_filter_mode(self, mode: str, btn: QPushButton):
        """Switch between All / Checked / Unchecked filter."""
        self._filter_mode = mode
        self._btn_filter_all.setChecked(mode == "all")
        self._btn_filter_checked.setChecked(mode == "checked")
        self._btn_filter_unchecked.setChecked(mode == "unchecked")
        self._filter_list(self._search.text())

    def _is_item_checked(self, item: QListWidgetItem) -> bool:
        """Check whether an item is checked, handling per-mutation widget mode."""
        widget = self._list.itemWidget(item)
        if widget is not None:
            chk = widget.findChild(QCheckBox)
            if chk is not None:
                return chk.isChecked()
        return item.checkState() == Qt.Checked

    def _filter_list(self, text: str):
        text_lower = text.lower()
        for i in range(self._list.count()):
            item = self._list.item(i)
            # Check item text, label widget, and tooltip for search matches
            visible_text = item.text()
            widget = self._list.itemWidget(item)
            if widget is not None:
                lbl = widget.findChild(QLabel)
                if lbl:
                    visible_text = lbl.toolTip() or lbl.text()
            matches_text = text_lower in visible_text.lower()
            # Apply checked/unchecked filter
            if self._filter_mode == "checked":
                matches_filter = self._is_item_checked(item)
            elif self._filter_mode == "unchecked":
                matches_filter = not self._is_item_checked(item)
            else:
                matches_filter = True
            item.setHidden(not (matches_text and matches_filter))

    def _set_all(self, checked: bool):
        self._suppress = True
        state = Qt.Checked if checked else Qt.Unchecked
        for i in range(self._list.count()):
            item = self._list.item(i)
            # If in individual mode, sync via embedded checkbox
            widget = self._list.itemWidget(item)
            if widget is not None:
                chk = widget.findChild(QCheckBox)
                if chk is not None:
                    chk.setChecked(checked)
            else:
                item.setCheckState(state)
        self._suppress = False
        self._update_toggle_text()
        self._on_changed()

    def _on_item_changed(self, _item=None):
        if self._suppress:
            return
        self._update_toggle_text()
        if self._filter_mode != "all":
            self._filter_list(self._search.text())
        self._on_changed()

    def _on_changed(self, _=None):
        if self._suppress:
            return
        self._parent_view._on_config_changed()

    def _count_checked(self) -> int:
        count = 0
        for i in range(self._list.count()):
            item = self._list.item(i)
            widget = self._list.itemWidget(item)
            if widget is not None:
                chk = widget.findChild(QCheckBox)
                if chk is not None:
                    count += chk.isChecked()
                    continue
            count += item.checkState() == Qt.Checked
        return count

    def _update_toggle_text(self):
        n = self._count_checked()
        arrow = "\u25B2" if self._toggle.isChecked() else "\u25BC"
        self._toggle.setText(f"{self._title} ({n} selected) {arrow}")


# ---------------------------------------------------------------------------
# Main view
# ---------------------------------------------------------------------------

class ManualScoringView(QWidget):
    """Configurable point-based cat scoring and triage view."""

    _UI_STATE_KEY = "manual_scoring_state"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(
            "QWidget { background:#0a0a18; }"
            "QLabel { color:#bbb; }"
            "QLineEdit { background:#0d0d1c; color:#ccc; border:1px solid #2a2a4a;"
            " border-radius:4px; padding:4px 8px; }"
            "QSpinBox { background:#0d0d1c; color:#ccc; border:1px solid #2a2a4a;"
            " border-radius:4px; padding:2px 6px; min-width:55px; }"
            "QComboBox { background:#0d0d1c; color:#ccc; border:1px solid #2a2a4a;"
            " border-radius:4px; padding:4px 8px; }"
            "QComboBox QAbstractItemView { background:#101023; color:#ddd; }"
            "QTableWidget { background:#101023; color:#ddd; border:1px solid #26264a; }"
            "QHeaderView::section { background:#151532; color:#7d8bb0; border:none;"
            " padding:4px; font-weight:bold; }"
            "QCheckBox { color:#bbb; spacing:6px; }"
            "QListWidget { background:#0d0d1c; color:#ccc; border:1px solid #2a2a4a;"
            " border-radius:4px; }"
            "QPushButton { background:#1a1a32; color:#aaa; border:1px solid #2a2a4a;"
            " border-radius:4px; padding:4px 10px; }"
            "QPushButton:hover { background:#252545; color:#ddd; }"
            "QScrollArea { background:transparent; border:none; }"
            "QToolButton { color:#8899cc; border:none; text-align:left; padding:2px 0; }"
        )

        self._cats: list[Cat] = []
        self._alive: list[Cat] = []
        self._config: dict = dict(_DEFAULT_CONFIG)
        self._session_state: dict = _load_ui_state(self._UI_STATE_KEY)
        self._suppress_recompute = False
        self._trait_ratings: Optional[TraitRatings] = None
        self._auto_calc = _saved_manual_scoring_auto_calc()
        self._results_stale = False

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        self._splitter = QSplitter(Qt.Horizontal)
        root.addWidget(self._splitter, 1)

        # --- Left panel: config ---
        self._config_panel = self._build_config_panel()
        self._splitter.addWidget(self._config_panel)

        # --- Right panel: table ---
        self._table_panel = self._build_table_panel()
        self._splitter.addWidget(self._table_panel)

        self._splitter.setSizes([380, 800])
        self._splitter.setStretchFactor(0, 0)
        self._splitter.setStretchFactor(1, 1)

        self._restore_session_state()

    # ---- Config panel construction ----------------------------------------

    def _build_config_panel(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setMaximumWidth(460)

        container = QWidget()
        vb = QVBoxLayout(container)
        vb.setContentsMargins(12, 12, 12, 12)
        vb.setSpacing(10)

        title = QLabel("Simple Scoring")
        title.setStyleSheet("color:#f0f0ff; font-size:17px; font-weight:bold;")
        vb.addWidget(title)

        self._subtitle = QLabel("")
        self._subtitle.setStyleSheet("color:#667; font-size:13px;")
        vb.addWidget(self._subtitle)

        # --- Profile ---
        profile_row = QHBoxLayout()
        profile_row.setSpacing(6)
        profile_lbl = QLabel("Profile:")
        profile_lbl.setMinimumWidth(60)
        profile_row.addWidget(profile_lbl)
        self._profile_combo = QComboBox()
        for i in range(1, 6):
            self._profile_combo.addItem(f"Profile {i}", i)
        self._profile_combo.currentIndexChanged.connect(self._on_profile_changed)
        profile_row.addWidget(self._profile_combo, 1)
        profile_row.addStretch()
        vb.addLayout(profile_row)

        # --- Stats ---
        vb.addWidget(self._section_label("Stats"))
        row, self._spin_stat = self._weight_row("Per stat point", 1)
        vb.addLayout(row)

        # --- Desired mutations ---
        vb.addWidget(self._section_label("Mutations"))
        self._desired_selector = _MutationSelector("Desired Mutations", 1, self)
        vb.addWidget(self._desired_selector)

        # --- Undesired mutations ---
        self._undesired_selector = _MutationSelector("Undesirable Mutations", -5, self)
        vb.addWidget(self._undesired_selector)

        # --- Disorders ---
        vb.addWidget(self._section_label("Disorders"))
        self._desired_disorder_selector = _MutationSelector("Desired Disorders", 1, self)
        vb.addWidget(self._desired_disorder_selector)
        self._undesired_disorder_selector = _MutationSelector("Undesirable Disorders", -5, self)
        vb.addWidget(self._undesired_disorder_selector)

        # --- Inbredness ---
        vb.addWidget(self._section_label("Inbredness"))
        self._spin_inbredness: dict[str, QSpinBox] = {}
        for tier, default in [("not", 2), ("slightly", 0), ("moderately", -1), ("highly", -10), ("extremely", -10)]:
            row, spin = self._weight_row(tier.capitalize(), default)
            self._spin_inbredness[tier] = spin
            vb.addLayout(row)

        # --- Libido ---
        vb.addWidget(self._section_label("Libido"))
        self._spin_libido: dict[str, QSpinBox] = {}
        for tier, default in [("high", 1), ("average", 0), ("low", -10)]:
            row, spin = self._weight_row(tier.capitalize(), default)
            self._spin_libido[tier] = spin
            vb.addLayout(row)

        # --- Aggression ---
        vb.addWidget(self._section_label("Aggression"))
        self._spin_aggression: dict[str, QSpinBox] = {}
        for tier, default in [("high", 1), ("average", 0), ("low", -1)]:
            row, spin = self._weight_row(tier.capitalize(), default)
            self._spin_aggression[tier] = spin
            vb.addLayout(row)

        # --- Passives ---
        vb.addWidget(self._section_label("Passives"))
        row, self._spin_passive = self._weight_row("Per passive ability", 1)
        vb.addLayout(row)

        # --- Extra spells ---
        vb.addWidget(self._section_label("Extra Spells"))
        row, self._spin_spell = self._weight_row("Per extra spell", 0)
        vb.addLayout(row)

        # --- Sexuality ---
        vb.addWidget(self._section_label("Sexuality"))
        self._spin_sexuality: dict[str, QSpinBox] = {}
        for tier, default in [("straight", 0), ("bi", -10), ("gay", -10)]:
            row, spin = self._weight_row(tier.capitalize(), default)
            self._spin_sexuality[tier] = spin
            vb.addLayout(row)

        # --- Reset ---
        vb.addSpacing(12)
        reset_btn = QPushButton("Reset Defaults")
        reset_btn.clicked.connect(self._reset_defaults)
        vb.addWidget(reset_btn)

        vb.addStretch(1)
        scroll.setWidget(container)
        return scroll

    # ---- Table panel construction -----------------------------------------

    def _build_table_panel(self) -> QWidget:
        panel = QWidget()
        vb = QVBoxLayout(panel)
        vb.setContentsMargins(8, 8, 8, 8)
        vb.setSpacing(6)

        # Toolbar
        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)

        toolbar.addWidget(QLabel("Room:"))
        self._room_combo = QComboBox()
        self._room_combo.setMinimumWidth(140)
        self._room_combo.currentIndexChanged.connect(self._on_filter_changed)
        toolbar.addWidget(self._room_combo)

        self._chk_in_house = QCheckBox("In House only")
        self._chk_in_house.setChecked(True)
        self._chk_in_house.toggled.connect(self._on_filter_changed)
        toolbar.addWidget(self._chk_in_house)

        self._calculate_btn = QPushButton("Calculate")
        self._calculate_btn.setToolTip("Run scoring for all cats with current config.")
        self._calculate_btn.clicked.connect(self._recompute)
        toolbar.addWidget(self._calculate_btn)
        self._auto_calc_chk = QCheckBox("Auto Calc")
        self._auto_calc_chk.setToolTip("Automatically recalculate scores when config changes.")
        self._auto_calc_chk.setChecked(self._auto_calc)
        self._auto_calc_chk.toggled.connect(self._on_auto_calc_toggled)
        toolbar.addWidget(self._auto_calc_chk)

        toolbar.addStretch()

        toolbar.addWidget(QLabel("Highlight below:"))
        self._spin_threshold = QSpinBox()
        self._spin_threshold.setRange(-999, 999)
        self._spin_threshold.setValue(12)
        self._spin_threshold.valueChanged.connect(self._apply_threshold_highlight)
        toolbar.addWidget(self._spin_threshold)

        vb.addLayout(toolbar)

        # Table
        self._table = QTableWidget()
        self._table.setColumnCount(_NUM_COLS)
        self._table.setHorizontalHeaderLabels(_HEADER_LABELS)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.setSortingEnabled(True)
        self._table.verticalHeader().setVisible(False)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.setAlternatingRowColors(True)
        self._table.setStyleSheet(
            self._table.styleSheet()
            + "QTableWidget { alternate-background-color:#0c0c20; }"
        )

        hdr = self._table.horizontalHeader()
        hdr.setSectionResizeMode(_COL_NAME, QHeaderView.Interactive)
        hdr.setSectionResizeMode(_COL_ROOM, QHeaderView.Interactive)
        self._table.setColumnWidth(_COL_NAME, 160)
        self._table.setColumnWidth(_COL_ROOM, 80)
        self._table.setColumnWidth(_COL_TOTAL, 60)
        for i in range(_COL_FIRST_BREAKDOWN, _NUM_COLS):
            self._table.setColumnWidth(i, 65)

        vb.addWidget(self._table, 1)

        self._status_label = QLabel("")
        self._status_label.setStyleSheet("color:#667; font-size:13px;")
        vb.addWidget(self._status_label)

        return panel

    # ---- Helpers ----------------------------------------------------------

    @staticmethod
    def _section_label(text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet("color:#8899cc; font-size:14px; font-weight:bold; margin-top:6px;")
        return lbl

    def _weight_row(self, label: str, default: int) -> tuple[QHBoxLayout, QSpinBox]:
        row = QHBoxLayout()
        row.setSpacing(8)
        lbl = QLabel(label)
        lbl.setMinimumWidth(120)
        row.addWidget(lbl)
        spin = QSpinBox()
        spin.setRange(-99, 99)
        spin.setValue(default)
        spin.valueChanged.connect(self._on_config_changed)
        row.addWidget(spin)
        row.addStretch()
        return row, spin

    # ---- Mutation catalog rebuild -----------------------------------------

    def _rebuild_mutation_catalogs(self):
        """Rebuild mutation and disorder selectors from current cats."""
        all_mutations = sorted({m for c in self._alive for m in (c.mutations or [])})
        all_disorders = sorted({d for c in self._alive for d in (c.disorders or [])})

        # Snapshot prev selections before any rebuild — apply_per_weights can
        # trigger _on_config_changed which would overwrite self._config with
        # an incomplete read (later selectors haven't been rebuilt yet).
        prev_desired = set(self._config.get("desired_mutations", []))
        prev_desired_weights = self._config.get("desired_mutation_weights", {})
        prev_undesired = set(self._config.get("undesired_mutations", []))
        prev_undesired_weights = self._config.get("undesired_mutation_weights", {})
        prev_desired_dis = set(self._config.get("desired_disorders", []))
        prev_desired_dis_weights = self._config.get("desired_disorder_weights", {})
        prev_undesired_dis = set(self._config.get("undesired_disorders", []))
        prev_undesired_dis_weights = self._config.get("undesired_disorder_weights", {})

        # Suppress config reads during rebuild so apply_per_weights signals
        # don't clobber later selectors before they're rebuilt.
        self._suppress_recompute = True
        self._desired_selector.rebuild(all_mutations, prev_desired, prev_desired_weights)
        self._desired_selector.apply_per_weights()
        self._undesired_selector.rebuild(all_mutations, prev_undesired, prev_undesired_weights)
        self._undesired_selector.apply_per_weights()
        self._desired_disorder_selector.rebuild(all_disorders, prev_desired_dis, prev_desired_dis_weights)
        self._desired_disorder_selector.apply_per_weights()
        self._undesired_disorder_selector.rebuild(all_disorders, prev_undesired_dis, prev_undesired_dis_weights)
        self._undesired_disorder_selector.apply_per_weights()
        self._suppress_recompute = False

    # ---- Config reading ---------------------------------------------------

    def _read_config(self) -> dict:
        """Read all widget values into a config dict."""
        return {
            "stat_weight": self._spin_stat.value(),
            "desired_mutations": self._desired_selector.get_checked(),
            "desired_mutation_weights": self._desired_selector.get_per_weights(),
            "desired_use_individual": self._desired_selector.use_individual(),
            "desired_default_weight": self._desired_selector.default_weight(),
            "undesired_mutations": self._undesired_selector.get_checked(),
            "undesired_mutation_weights": self._undesired_selector.get_per_weights(),
            "undesired_use_individual": self._undesired_selector.use_individual(),
            "undesired_default_weight": self._undesired_selector.default_weight(),
            "desired_disorders": self._desired_disorder_selector.get_checked(),
            "desired_disorder_weights": self._desired_disorder_selector.get_per_weights(),
            "desired_disorder_use_individual": self._desired_disorder_selector.use_individual(),
            "desired_disorder_default_weight": self._desired_disorder_selector.default_weight(),
            "undesired_disorders": self._undesired_disorder_selector.get_checked(),
            "undesired_disorder_weights": self._undesired_disorder_selector.get_per_weights(),
            "undesired_disorder_use_individual": self._undesired_disorder_selector.use_individual(),
            "undesired_disorder_default_weight": self._undesired_disorder_selector.default_weight(),
            "inbredness_weights": {k: s.value() for k, s in self._spin_inbredness.items()},
            "libido_weights": {k: s.value() for k, s in self._spin_libido.items()},
            "aggression_weights": {k: s.value() for k, s in self._spin_aggression.items()},
            "passive_weight": self._spin_passive.value(),
            "extra_spell_weight": self._spin_spell.value(),
            "sexuality_weights": {k: s.value() for k, s in self._spin_sexuality.items()},
        }

    def _on_config_changed(self, _value=None):
        if self._suppress_recompute:
            return
        self._config = self._read_config()
        if self._auto_calc:
            self._recompute()
        else:
            self._mark_stale()

    def _on_filter_changed(self, _=None):
        if self._suppress_recompute:
            return
        if self._auto_calc:
            self._recompute()
        else:
            self._mark_stale()

    def _mark_stale(self):
        self._results_stale = True
        self._status_label.setText("Scores are out of date. Click Calculate to recompute.")
        self._calculate_btn.setEnabled(bool(self._alive))

    def _on_auto_calc_toggled(self, checked: bool):
        from mewgenics.utils.config import _set_manual_scoring_auto_calc
        self._auto_calc = bool(checked)
        _set_manual_scoring_auto_calc(self._auto_calc)
        if self._auto_calc and self._results_stale and self._alive:
            self._recompute()

    def set_auto_recalculate(self, enabled: bool):
        self._auto_calc = bool(enabled)
        self._auto_calc_chk.blockSignals(True)
        self._auto_calc_chk.setChecked(self._auto_calc)
        self._auto_calc_chk.blockSignals(False)

    # ---- Data reception ---------------------------------------------------

    def set_cats(self, cats: list[Cat]):
        self._cats = cats or []
        self._alive = [c for c in self._cats if c.status != "Gone"]
        self._subtitle.setText(f"{len(self._alive)} cats alive, {len(self._cats)} total")
        self._populate_room_filter()
        self._rebuild_mutation_catalogs()
        self._recompute()

    # ---- Room filter ------------------------------------------------------

    def _populate_room_filter(self):
        prev = self._room_combo.currentData()
        self._room_combo.blockSignals(True)
        self._room_combo.clear()
        self._room_combo.addItem("All Rooms", "")
        occupied = sorted(
            {c.room for c in self._alive if c.room},
            key=lambda r: list(ROOM_KEYS).index(r) if r in ROOM_KEYS else 99,
        )
        for rk in occupied:
            display = ROOM_DISPLAY.get(rk, rk)
            count = sum(1 for c in self._alive if c.room == rk)
            self._room_combo.addItem(f"{display}  ({count})", rk)
        if prev:
            idx = self._room_combo.findData(prev)
            if idx >= 0:
                self._room_combo.setCurrentIndex(idx)
        self._room_combo.blockSignals(False)

    # ---- Recompute and display --------------------------------------------

    def _recompute(self, _=None):
        self._results_stale = False
        room_filter = self._room_combo.currentData() or ""
        in_house_only = self._chk_in_house.isChecked()

        cats = self._alive if in_house_only else self._cats
        if room_filter:
            cats = [c for c in cats if c.room == room_filter]

        scored: list[tuple[Cat, int, dict[str, int]]] = []
        for cat in cats:
            total, breakdown = compute_cat_score(cat, self._config)
            scored.append((cat, total, breakdown))

        scored.sort(key=lambda x: x[1], reverse=True)

        self._table.setSortingEnabled(False)
        self._table.setRowCount(len(scored))

        for row, (cat, total, breakdown) in enumerate(scored):
            name_item = QTableWidgetItem(cat.name)
            name_item.setData(Qt.UserRole, cat.db_key)
            self._table.setItem(row, _COL_NAME, name_item)

            room_display = ROOM_DISPLAY.get(cat.room, cat.room or "\u2014")
            self._table.setItem(row, _COL_ROOM, QTableWidgetItem(room_display))

            total_item = _NumericItem(str(total))
            total_item.setData(Qt.UserRole, total)
            total_item.setTextAlignment(Qt.AlignCenter)
            self._table.setItem(row, _COL_TOTAL, total_item)

            for ci, key in enumerate(_BREAKDOWN_KEYS):
                val = breakdown.get(key, 0)
                item = _NumericItem(str(val))
                item.setData(Qt.UserRole, val)
                item.setTextAlignment(Qt.AlignCenter)
                if val > 0:
                    item.setForeground(QBrush(QColor(120, 200, 130)))
                elif val < 0:
                    item.setForeground(QBrush(QColor(220, 100, 100)))
                self._table.setItem(row, _COL_FIRST_BREAKDOWN + ci, item)

        self._table.setSortingEnabled(True)
        self._apply_threshold_highlight()
        self._status_label.setText(f"{len(scored)} cats displayed")

    def _apply_threshold_highlight(self, _=None):
        threshold = self._spin_threshold.value()
        red_bg = QBrush(QColor(80, 20, 20))
        clear_bg = QBrush()
        for row in range(self._table.rowCount()):
            total_item = self._table.item(row, _COL_TOTAL)
            if total_item is None:
                continue
            val = total_item.data(Qt.UserRole)
            bg = red_bg if isinstance(val, int) and val < threshold else clear_bg
            for col in range(self._table.columnCount()):
                item = self._table.item(row, col)
                if item is not None:
                    item.setBackground(bg)

    # ---- Reset defaults ---------------------------------------------------

    def _reset_defaults(self):
        self._suppress_recompute = True
        self._spin_stat.setValue(_DEFAULT_CONFIG["stat_weight"])
        self._desired_selector.reset()
        self._undesired_selector.reset()
        self._desired_disorder_selector.reset()
        self._undesired_disorder_selector.reset()
        for k, v in _DEFAULT_CONFIG["inbredness_weights"].items():
            self._spin_inbredness[k].setValue(v)
        for k, v in _DEFAULT_CONFIG["libido_weights"].items():
            self._spin_libido[k].setValue(v)
        for k, v in _DEFAULT_CONFIG["aggression_weights"].items():
            self._spin_aggression[k].setValue(v)
        self._spin_passive.setValue(_DEFAULT_CONFIG["passive_weight"])
        self._spin_spell.setValue(_DEFAULT_CONFIG["extra_spell_weight"])
        for k, v in _DEFAULT_CONFIG["sexuality_weights"].items():
            self._spin_sexuality[k].setValue(v)
        self._spin_threshold.setValue(12)
        self._suppress_recompute = False
        self._config = self._read_config()
        self._recompute()

    # ---- Shared trait ratings ------------------------------------------------

    def set_trait_ratings(self, tr: TraitRatings):
        """Receive shared TraitRatings instance from MainWindow."""
        self._trait_ratings = tr
        # Sync profile combo
        self._suppress_recompute = True
        idx = self._profile_combo.findData(tr.active_profile)
        if idx >= 0:
            self._profile_combo.setCurrentIndex(idx)
        self._suppress_recompute = False
        # Restore state from active profile
        manual_weights = tr.get_manual_weights()
        if manual_weights:
            self._restore_from_manual_weights(manual_weights)

    def _on_profile_changed(self):
        if self._suppress_recompute or self._trait_ratings is None:
            return
        slot = self._profile_combo.currentData()
        if slot is None:
            return
        # Save current manual weights before switching
        self._trait_ratings.set_manual_weights(self._read_config())
        self._trait_ratings.switch_profile(slot)
        self._trait_ratings.save()
        # Load new profile's manual weights
        manual_weights = self._trait_ratings.get_manual_weights()
        if manual_weights:
            self._restore_from_manual_weights(manual_weights)
        self._recompute()

    def _restore_from_manual_weights(self, s: dict):
        """Restore widget state from a manual_weights dict."""
        self._suppress_recompute = True
        self._spin_stat.setValue(s.get("stat_weight", _DEFAULT_CONFIG["stat_weight"]))
        self._desired_selector.set_state(
            use_individual=s.get("desired_use_individual", False),
            default_weight=s.get("desired_default_weight", 1),
            per_weights=s.get("desired_mutation_weights", {}),
        )
        self._undesired_selector.set_state(
            use_individual=s.get("undesired_use_individual", False),
            default_weight=s.get("undesired_default_weight", -5),
            per_weights=s.get("undesired_mutation_weights", {}),
        )
        self._desired_disorder_selector.set_state(
            use_individual=s.get("desired_disorder_use_individual", False),
            default_weight=s.get("desired_disorder_default_weight", 1),
            per_weights=s.get("desired_disorder_weights", {}),
        )
        self._undesired_disorder_selector.set_state(
            use_individual=s.get("undesired_disorder_use_individual", False),
            default_weight=s.get("undesired_disorder_default_weight", -5),
            per_weights=s.get("undesired_disorder_weights", {}),
        )
        for k, spin in self._spin_inbredness.items():
            spin.setValue(s.get("inbredness_weights", _DEFAULT_CONFIG["inbredness_weights"]).get(k, 0))
        for k, spin in self._spin_libido.items():
            spin.setValue(s.get("libido_weights", _DEFAULT_CONFIG["libido_weights"]).get(k, 0))
        for k, spin in self._spin_aggression.items():
            spin.setValue(s.get("aggression_weights", _DEFAULT_CONFIG["aggression_weights"]).get(k, 0))
        self._spin_passive.setValue(s.get("passive_weight", _DEFAULT_CONFIG["passive_weight"]))
        self._spin_spell.setValue(s.get("extra_spell_weight", _DEFAULT_CONFIG["extra_spell_weight"]))
        for k, spin in self._spin_sexuality.items():
            spin.setValue(s.get("sexuality_weights", _DEFAULT_CONFIG["sexuality_weights"]).get(k, 0))
        self._suppress_recompute = False
        # Preserve saved mutation/disorder checked lists — _read_config returns
        # empty lists when the selectors have no items (before set_cats rebuilds them).
        saved_checked = {
            k: self._config.get(k, [])
            for k in ("desired_mutations", "undesired_mutations",
                       "desired_disorders", "undesired_disorders",
                       "desired_mutation_weights", "undesired_mutation_weights",
                       "desired_disorder_weights", "undesired_disorder_weights")
        }
        self._config = self._read_config()
        self._config.update(saved_checked)

    # ---- Session state persistence ----------------------------------------

    def save_session_state(self):
        if self._trait_ratings is not None:
            self._trait_ratings.set_manual_weights(self._read_config())
            self._trait_ratings.save()
        # Always save UI state (threshold, room filter, splitter, sort) to app config
        state = self._read_config()
        state["threshold"] = self._spin_threshold.value()
        state["room_filter"] = self._room_combo.currentData() or ""
        state["in_house_only"] = self._chk_in_house.isChecked()
        state["splitter_sizes"] = self._splitter.sizes()
        hdr = self._table.horizontalHeader()
        state["sort_column"] = hdr.sortIndicatorSection()
        state["sort_order"] = hdr.sortIndicatorOrder().value
        _save_ui_state(self._UI_STATE_KEY, state)

    def _restore_session_state(self):
        s = self._session_state
        if not s:
            return

        self._suppress_recompute = True

        self._spin_stat.setValue(s.get("stat_weight", _DEFAULT_CONFIG["stat_weight"]))

        # Restore mutation/disorder selector state (items rebuilt later in set_cats)
        self._desired_selector.set_state(
            use_individual=s.get("desired_use_individual", False),
            default_weight=s.get("desired_default_weight", 1),
            per_weights=s.get("desired_mutation_weights", {}),
        )
        self._undesired_selector.set_state(
            use_individual=s.get("undesired_use_individual", False),
            default_weight=s.get("undesired_default_weight", -5),
            per_weights=s.get("undesired_mutation_weights", {}),
        )
        self._desired_disorder_selector.set_state(
            use_individual=s.get("desired_disorder_use_individual", False),
            default_weight=s.get("desired_disorder_default_weight", 1),
            per_weights=s.get("desired_disorder_weights", {}),
        )
        self._undesired_disorder_selector.set_state(
            use_individual=s.get("undesired_disorder_use_individual", False),
            default_weight=s.get("undesired_disorder_default_weight", -5),
            per_weights=s.get("undesired_disorder_weights", {}),
        )

        for k, spin in self._spin_inbredness.items():
            spin.setValue(s.get("inbredness_weights", _DEFAULT_CONFIG["inbredness_weights"]).get(k, 0))
        for k, spin in self._spin_libido.items():
            spin.setValue(s.get("libido_weights", _DEFAULT_CONFIG["libido_weights"]).get(k, 0))
        for k, spin in self._spin_aggression.items():
            spin.setValue(s.get("aggression_weights", _DEFAULT_CONFIG["aggression_weights"]).get(k, 0))

        self._spin_passive.setValue(s.get("passive_weight", _DEFAULT_CONFIG["passive_weight"]))
        self._spin_spell.setValue(s.get("extra_spell_weight", _DEFAULT_CONFIG["extra_spell_weight"]))

        for k, spin in self._spin_sexuality.items():
            spin.setValue(s.get("sexuality_weights", _DEFAULT_CONFIG["sexuality_weights"]).get(k, 0))

        self._spin_threshold.setValue(s.get("threshold", 12))
        self._chk_in_house.setChecked(s.get("in_house_only", True))

        sizes = s.get("splitter_sizes")
        if sizes and len(sizes) == 2:
            self._splitter.setSizes(sizes)

        self._suppress_recompute = False

        # Build initial config — mutation/disorder lists restored later in set_cats
        self._config = self._read_config()
        self._config["desired_mutations"] = s.get("desired_mutations", [])
        self._config["desired_mutation_weights"] = s.get("desired_mutation_weights", {})
        self._config["undesired_mutations"] = s.get("undesired_mutations", [])
        self._config["undesired_mutation_weights"] = s.get("undesired_mutation_weights", {})
        self._config["desired_disorders"] = s.get("desired_disorders", [])
        self._config["desired_disorder_weights"] = s.get("desired_disorder_weights", {})
        self._config["undesired_disorders"] = s.get("undesired_disorders", [])
        self._config["undesired_disorder_weights"] = s.get("undesired_disorder_weights", {})

    def _restore_sort_order(self):
        """Called after table is populated to restore sort column/order."""
        s = self._session_state
        col = s.get("sort_column", _COL_TOTAL)
        order = Qt.SortOrder(s.get("sort_order", int(Qt.DescendingOrder)))
        self._table.sortByColumn(col, order)


class _NumericItem(QTableWidgetItem):
    """Table item that sorts numerically by UserRole data."""

    def __lt__(self, other):
        a = self.data(Qt.UserRole)
        b = other.data(Qt.UserRole) if other else None
        if isinstance(a, (int, float)) and isinstance(b, (int, float)):
            return a < b
        return super().__lt__(other)
