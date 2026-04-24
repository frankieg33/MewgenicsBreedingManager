from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QTableWidget, QTableWidgetItem, QStyledItemDelegate,
    QStyle, QStyleOptionViewItem, QAbstractItemView, QHeaderView,
    QPushButton, QMenu, QToolButton, QSpinBox, QDoubleSpinBox,
    QSplitter,
)
from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QColor, QBrush, QPalette, QFont

from save_parser import (
    Cat, STAT_NAMES, can_breed, kinship_coi, risk_percent, shared_ancestor_counts, _ancestor_depths,
    _inheritance_candidates,
)
from breeding import score_pair, PairFactors
from mewgenics.constants import STAT_COLORS
from mewgenics.models.breeding_cache import BreedingCache
from mewgenics.utils.localization import _tr, ROOM_DISPLAY
from mewgenics.utils.abilities import _mutation_display_name, _ability_tip
from mewgenics.utils.calibration import _trait_label_from_value, _trait_level_color
from mewgenics.utils.tags import _cat_tags, _make_tag_icon
from mewgenics.utils.styling import _enforce_min_font_in_widget_tree, _blend_qcolor


class SafeBreedingView(QWidget):
    """Dedicated view for ranking alive breeding candidates."""
    _QUALITY_COLUMN_COUNT = 21
    _PAIR_JUMP_COL = 0
    _QUALITY_CAT_COL = 1
    _QUALITY_ROOM_COL = 2
    _QUALITY_QUALITY_COL = 3
    _QUALITY_RISK_COL = 4
    _SAFE_CAT_COL = 1
    _SAFE_ROOM_COL = 2
    _SAFE_RISK_COL = 3
    _SAFE_SHARED_COL = 4
    _SAFE_OUTCOME_COL = 5
    _QUALITY_STAT_START = 5
    _QUALITY_SUM_COL = _QUALITY_STAT_START + len(STAT_NAMES)
    _QUALITY_TRAIT_START = _QUALITY_SUM_COL + 1
    _QUALITY_SHARED_COL = _QUALITY_TRAIT_START + 3
    _QUALITY_RECENT_SHARED_COL = _QUALITY_SHARED_COL + 1
    _QUALITY_MUTS_COL = _QUALITY_RECENT_SHARED_COL + 1
    _QUALITY_ABIL_COL = _QUALITY_MUTS_COL + 1
    _QUALITY_NOTES_COL = _QUALITY_ABIL_COL + 1

    class _ColumnPaddingDelegate(QStyledItemDelegate):
        def __init__(self, extra_width: int, left_padding: int = 0, parent=None):
            super().__init__(parent)
            self._extra_width = extra_width
            self._left_padding = left_padding

        def sizeHint(self, option, index):
            s = super().sizeHint(option, index)
            return QSize(s.width() + self._extra_width, s.height())

        def paint(self, painter, option, index):
            if self._left_padding <= 0:
                return super().paint(painter, option, index)

            opt = QStyleOptionViewItem(option)
            self.initStyleOption(opt, index)
            style = opt.widget.style() if opt.widget is not None else QApplication.style()

            text = opt.text
            opt.text = ""
            style.drawControl(QStyle.CE_ItemViewItem, opt, painter, opt.widget)

            text_rect = style.subElementRect(QStyle.SE_ItemViewItemText, opt, opt.widget).adjusted(
                self._left_padding, 0, 0, 0
            )
            if opt.textElideMode != Qt.ElideNone:
                text = opt.fontMetrics.elidedText(text, opt.textElideMode, text_rect.width())

            painter.save()
            if opt.state & QStyle.State_Selected:
                painter.setPen(opt.palette.color(QPalette.HighlightedText))
            else:
                painter.setPen(opt.palette.color(QPalette.Text))
            painter.setFont(opt.font)
            painter.drawText(text_rect, Qt.AlignVCenter | Qt.AlignLeft, text)
            painter.restore()

    @staticmethod
    def _mode_button_style(active: bool, background: str, border: str) -> str:
        if active:
            return (
                "QPushButton { "
                f"background:{background}; color:#f5fbff; border:1px solid {border};"
                " border-radius:4px; padding:4px 10px; font-weight:bold; }"
                f"QPushButton:hover {{ background:{background}; }}"
            )
        return (
            "QPushButton { background:#141428; color:#8f95bd; border:1px solid #2a2a4a;"
            " border-radius:4px; padding:4px 10px; }"
            "QPushButton:hover { background:#1e1e38; color:#ddd; }"
        )

    @staticmethod
    def _pair_jump_button_style() -> str:
        return (
            "QToolButton { background:#1a1a32; color:#aaddff; border:1px solid #2a2a4a;"
            " border-radius:4px; font-size:12px; font-weight:bold; }"
            "QToolButton:hover { background:#252545; }"
            "QToolButton:pressed { background:#10101f; }"
        )

    def _make_pair_jump_button(self, db_key_a: int, db_key_b: int) -> QToolButton:
        jump_btn = QToolButton()
        jump_btn.setText("↗")
        jump_btn.setCursor(Qt.PointingHandCursor)
        jump_btn.setToolTip(_tr(
            "room_optimizer.detail.button.jump_to_pair",
            default="Show these two cats in the Alive Cats view",
        ))
        jump_btn.setAutoRaise(True)
        jump_btn.setFixedSize(24, 22)
        jump_btn.setStyleSheet(self._pair_jump_button_style())
        if self._navigate_to_pair_callback is not None:
            jump_btn.clicked.connect(
                lambda checked=False, ak=db_key_a, bk=db_key_b: self._navigate_to_pair_callback(ak, bk)
            )
        else:
            jump_btn.setEnabled(False)
        return jump_btn

    @staticmethod
    def _filter_toggle_style() -> str:
        return (
            "QPushButton { background:#1a1a32; color:#bbb; border:1px solid #2a2a4a;"
            " border-radius:4px; padding:5px 10px; }"
            "QPushButton:checked { background:#2a3a5a; color:#fff; border:1px solid #4a6a9a; }"
        )

    def _build_filters_pane(self) -> QWidget:
        pane = QWidget()
        v = QVBoxLayout(pane)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)

        title = QLabel("Filters")
        title.setStyleSheet("color:#666; font-size:10px; font-weight:bold;")
        v.addWidget(title)

        self._filter_hide_left_paired_btn = QPushButton("Hide in-love cats on left")
        self._filter_hide_left_paired_btn.setCheckable(True)
        self._filter_hide_left_paired_btn.setStyleSheet(self._filter_toggle_style())
        self._filter_hide_left_paired_btn.toggled.connect(self._on_filter_changed)
        v.addWidget(self._filter_hide_left_paired_btn)

        risk_row = QHBoxLayout()
        risk_row.setSpacing(6)
        risk_row.addWidget(QLabel("Max risk %:"))
        self._filter_max_risk_spin = QSpinBox()
        self._filter_max_risk_spin.setRange(0, 100)
        self._filter_max_risk_spin.setValue(100)
        self._filter_max_risk_spin.valueChanged.connect(self._on_filter_changed)
        risk_row.addWidget(self._filter_max_risk_spin)
        risk_row.addStretch(1)
        v.addLayout(risk_row)

        self._filter_min_quality_row = QWidget()
        qrow = QHBoxLayout(self._filter_min_quality_row)
        qrow.setContentsMargins(0, 0, 0, 0)
        qrow.setSpacing(6)
        qrow.addWidget(QLabel("Min quality:"))
        self._filter_min_quality_spin = QDoubleSpinBox()
        self._filter_min_quality_spin.setRange(-999.0, 999.0)
        self._filter_min_quality_spin.setDecimals(1)
        self._filter_min_quality_spin.setSingleStep(1.0)
        self._filter_min_quality_spin.setValue(-999.0)
        self._filter_min_quality_spin.valueChanged.connect(self._on_filter_changed)
        qrow.addWidget(self._filter_min_quality_spin)
        qrow.addStretch(1)
        v.addWidget(self._filter_min_quality_row)

        muts_label = QLabel("Has any mutation:")
        muts_label.setStyleSheet("QLabel { color:#bbb; }")
        v.addWidget(muts_label)
        self._filter_mutation_search = QLineEdit()
        self._filter_mutation_search.setPlaceholderText("Search mutations…")
        self._filter_mutation_search.textChanged.connect(self._refresh_mutation_filter_visibility)
        v.addWidget(self._filter_mutation_search)
        self._filter_mutation_list = QListWidget()
        self._filter_mutation_list.setMinimumHeight(80)
        self._filter_mutation_list.itemChanged.connect(self._on_mutation_item_changed)
        v.addWidget(self._filter_mutation_list, 1)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)
        self._filter_reset_btn = QPushButton("Reset filters")
        self._filter_reset_btn.clicked.connect(self._reset_filters)
        btn_row.addWidget(self._filter_reset_btn)
        btn_row.addStretch(1)
        v.addLayout(btn_row)

        return pane

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(
            "QWidget { background:#0a0a18; }"
            "QLabel { color:#bbb; }"
            "QListWidget { background:#0d0d1c; color:#ddd; border:1px solid #1e1e38; }"
            "QLineEdit { background:#0d0d1c; color:#ccc; border:1px solid #2a2a4a;"
            " border-radius:4px; padding:4px 8px; }"
            "QTableWidget { background:#101023; color:#ddd; border:1px solid #26264a; }"
            "QHeaderView::section { background:#151532; color:#7d8bb0; border:none; padding:4px; font-weight:bold; }"
        )
        self._cats: list[Cat] = []
        self._alive: list[Cat] = []
        self._by_key: dict[int, Cat] = {}
        self._table_row_cat_keys: list[int] = []
        self._cache: Optional[BreedingCache] = None
        self._quality_mode: bool = False
        self._lover_key_map: dict[int, set[int]] = {}
        self._hater_key_map: dict[int, set[int]] = {}
        self._parent_key_map: dict[int, set[int]] = {}
        self._navigate_to_pair_callback = None

        # Filter state. Defaults match "no filtering".
        self._filter_hide_left_paired: bool = False
        self._filter_hide_matches_paired: bool = False
        self._filter_max_risk: int = 100
        self._filter_min_quality: float = -999.0
        self._filter_required_mutations: set[str] = set()
        self._all_house_mutations: list[str] = []

        root = QHBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(12)

        left = QWidget()
        left.setFixedWidth(320)
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.setSpacing(0)

        self._left_splitter = QSplitter(Qt.Vertical)
        self._left_splitter.setChildrenCollapsible(False)
        self._left_splitter.setHandleWidth(6)

        # ── Filters pane (top) ────────────────────────────────────────
        self._filters_pane = self._build_filters_pane()
        self._left_splitter.addWidget(self._filters_pane)

        # ── Cat selector pane (bottom) ────────────────────────────────
        selector_pane = QWidget()
        sv = QVBoxLayout(selector_pane)
        sv.setContentsMargins(0, 0, 0, 0)
        sv.setSpacing(8)
        self._list_title = QLabel(styleSheet="color:#666; font-size:10px; font-weight:bold;")
        sv.addWidget(self._list_title)
        self._search = QLineEdit()
        sv.addWidget(self._search)
        self._list = QListWidget()
        self._list.setIconSize(QSize(60, 20))
        sv.addWidget(self._list, 1)
        self._left_splitter.addWidget(selector_pane)

        # Filters get a smaller share of the vertical space by default.
        self._left_splitter.setStretchFactor(0, 0)
        self._left_splitter.setStretchFactor(1, 1)
        self._left_splitter.setSizes([260, 520])

        lv.addWidget(self._left_splitter, 1)
        root.addWidget(left)

        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(8)
        self._title = QLabel()
        self._title.setStyleSheet("color:#ddd; font-size:16px; font-weight:bold;")
        self._summary = QLabel("")
        self._summary.setStyleSheet("color:#666; font-size:11px;")
        self._mode_bar = QWidget()
        mode_layout = QHBoxLayout(self._mode_bar)
        mode_layout.setContentsMargins(0, 0, 0, 0)
        mode_layout.setSpacing(6)
        mode_layout.addWidget(QLabel("Mode:"))
        self._quality_btn = QPushButton("Best Pair")
        self._quality_btn.setCheckable(True)
        self._quality_btn.clicked.connect(lambda: self.set_quality_mode(True))
        mode_layout.addWidget(self._quality_btn)
        self._risk_btn = QPushButton("Safe Pair")
        self._risk_btn.setCheckable(True)
        self._risk_btn.clicked.connect(lambda: self.set_quality_mode(False))
        mode_layout.addWidget(self._risk_btn)
        mode_layout.addStretch(1)

        self._filter_hide_matches_btn = QPushButton("Hide in-love matches")
        self._filter_hide_matches_btn.setCheckable(True)
        self._filter_hide_matches_btn.setStyleSheet(self._filter_toggle_style())
        self._filter_hide_matches_btn.toggled.connect(self._on_filter_changed)

        self._table = QTableWidget(0, self._QUALITY_COLUMN_COUNT)
        self._table.setIconSize(QSize(60, 20))
        self._table.verticalHeader().setVisible(False)
        self._table.verticalHeader().setDefaultSectionSize(22)
        self._table.setSelectionMode(QAbstractItemView.SingleSelection)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.setWordWrap(True)
        self._table.setSortingEnabled(False)
        self._table.setContextMenuPolicy(Qt.CustomContextMenu)
        self._table.horizontalHeader().setStretchLastSection(False)
        for col in range(self._QUALITY_COLUMN_COUNT):
            self._table.horizontalHeader().setSectionResizeMode(col, QHeaderView.Interactive)
        self._table.horizontalHeader().setSectionResizeMode(self._PAIR_JUMP_COL, QHeaderView.Fixed)
        self._table.setItemDelegateForColumn(self._QUALITY_CAT_COL, SafeBreedingView._ColumnPaddingDelegate(24, 8, self._table))
        self._table.setColumnWidth(self._PAIR_JUMP_COL, 34)
        self._table.setColumnWidth(self._QUALITY_CAT_COL, 180)
        self._table.setColumnWidth(self._QUALITY_ROOM_COL, 78)
        self._table.setColumnWidth(self._QUALITY_QUALITY_COL, 72)
        self._table.setColumnWidth(self._QUALITY_RISK_COL, 60)
        for col in range(self._QUALITY_STAT_START, self._QUALITY_STAT_START + len(STAT_NAMES)):
            self._table.setColumnWidth(col, 56)
        self._table.setColumnWidth(self._QUALITY_SUM_COL, 60)
        self._table.setColumnWidth(self._QUALITY_TRAIT_START + 0, 70)
        self._table.setColumnWidth(self._QUALITY_TRAIT_START + 1, 60)
        self._table.setColumnWidth(self._QUALITY_TRAIT_START + 2, 74)
        self._table.setColumnWidth(self._QUALITY_SHARED_COL, 84)
        self._table.setColumnWidth(self._QUALITY_RECENT_SHARED_COL, 96)
        self._table.setColumnWidth(self._QUALITY_MUTS_COL, 220)
        self._table.setColumnWidth(self._QUALITY_ABIL_COL, 240)
        self._table.setColumnWidth(self._QUALITY_NOTES_COL, 180)
        self._table.horizontalHeader().setSortIndicatorShown(False)

        rv.addWidget(self._mode_bar)
        rv.addWidget(self._title)
        rv.addWidget(self._summary)
        rv.addWidget(self._filter_hide_matches_btn)
        rv.addWidget(self._table, 1)
        root.addWidget(right, 1)

        self._search.textChanged.connect(self._refresh_list)
        self._list.currentItemChanged.connect(self._on_current_item_changed)
        self._table.cellClicked.connect(self._on_table_row_clicked)
        self._table.customContextMenuRequested.connect(self._show_context_menu)
        self.retranslate_ui()
        self.set_quality_mode(True, refresh=False)
        _enforce_min_font_in_widget_tree(self)

    def retranslate_ui(self):
        self._list_title.setText(_tr("safe_breeding.list_title"))
        self._search.setPlaceholderText(_tr("safe_breeding.search_placeholder"))
        self._update_mode_controls()
        self._apply_mode_headers()
        self._refresh_list()

    def _update_mode_controls(self):
        risk_active = not self._quality_mode
        quality_active = self._quality_mode
        self._risk_btn.blockSignals(True)
        self._quality_btn.blockSignals(True)
        try:
            self._risk_btn.setChecked(risk_active)
            self._quality_btn.setChecked(quality_active)
        finally:
            self._risk_btn.blockSignals(False)
            self._quality_btn.blockSignals(False)
        self._risk_btn.setStyleSheet(self._mode_button_style(risk_active, "#1f5f4a", "#3f8f72"))
        self._quality_btn.setStyleSheet(self._mode_button_style(quality_active, "#2a3a5a", "#4a6a9a"))

    def _apply_mode_headers(self):
        room_label = _tr("safe_breeding.table.room", default="Room")
        if self._quality_mode:
            labels = [
                "",
                _tr("safe_breeding.table.cat", default="Partner"),
                room_label,
                _tr("safe_breeding.table.quality", default="Quality"),
                _tr("safe_breeding.table.risk"),
                *[stat.upper() for stat in STAT_NAMES],
                _tr("table.column.sum"),
                _tr("table.column.aggression"),
                _tr("table.column.libido"),
                _tr("table.column.inbred"),
                _tr("safe_breeding.table.shared_ancestors", default="Shared Anc."),
                _tr("safe_breeding.table.recent_shared_ancestors", default="Recent Shared"),
                _tr("table.column.mutations"),
                _tr("table.column.abilities"),
                _tr("safe_breeding.table.notes", default="Notes"),
            ]
        else:
            labels = [
                "",
                _tr("safe_breeding.table.cat", default="Partner"),
                room_label,
                _tr("safe_breeding.table.risk"),
                _tr("safe_breeding.table.shared_ancestors", default="Shared Anc."),
                _tr("safe_breeding.table.outcome", default="Result"),
            ] + [""] * (self._QUALITY_COLUMN_COUNT - 6)
        self._table.setHorizontalHeaderLabels(labels)
        if self._quality_mode:
            self._table.verticalHeader().setDefaultSectionSize(30)
            self._table.setColumnWidth(self._PAIR_JUMP_COL, 34)
            self._table.setColumnWidth(self._QUALITY_CAT_COL, 168)
            self._table.setColumnWidth(self._QUALITY_ROOM_COL, 78)
            self._table.setColumnWidth(self._QUALITY_QUALITY_COL, 72)
            self._table.setColumnWidth(self._QUALITY_RISK_COL, 60)
            for col in range(self._QUALITY_COLUMN_COUNT):
                self._table.setColumnHidden(col, False)
        else:
            self._table.verticalHeader().setDefaultSectionSize(22)
            self._table.setColumnWidth(self._PAIR_JUMP_COL, 34)
            self._table.setColumnWidth(self._SAFE_CAT_COL, 180)
            self._table.setColumnWidth(self._SAFE_ROOM_COL, 90)
            self._table.setColumnWidth(self._SAFE_RISK_COL, 86)
            self._table.setColumnWidth(self._SAFE_SHARED_COL, 94)
            self._table.setColumnWidth(self._SAFE_OUTCOME_COL, 120)
            for col in range(self._QUALITY_COLUMN_COUNT):
                self._table.setColumnHidden(col, col >= 6)

    @staticmethod
    def _make_room_item(other: Cat, row_bg: QColor | None, row_fg: QColor | None) -> QTableWidgetItem:
        room_display = ROOM_DISPLAY.get(other.room, other.room or "—")
        item = QTableWidgetItem(room_display)
        item.setTextAlignment(Qt.AlignCenter)
        if row_bg is not None:
            item.setBackground(QBrush(row_bg))
            if row_fg is not None:
                item.setForeground(QBrush(row_fg))
        return item

    @staticmethod
    def _metric_item(label: str, bg: QColor, tooltip: str, *, row_bg: QColor | None = None, align=Qt.AlignCenter) -> QTableWidgetItem:
        item = QTableWidgetItem(label)
        item.setTextAlignment(align)
        if row_bg is not None:
            bg = _blend_qcolor(bg, row_bg, 0.35)
        item.setBackground(QBrush(bg))
        item.setForeground(QBrush(QColor(255, 255, 255)))
        item.setToolTip(tooltip)
        return item

    @staticmethod
    def _stat_tint(color: QColor, strength: float = 0.26, lift: int = 16) -> QColor:
        return QColor(
            min(255, int(color.red() * strength) + lift),
            min(255, int(color.green() * strength) + lift),
            min(255, int(color.blue() * strength) + lift),
        )

    @staticmethod
    def _projected_stat_item(stat: str, projection: dict, *, row_bg: QColor | None = None) -> QTableWidgetItem:
        expected_stats = projection.get("expected_stats", {})
        stat_ranges = projection.get("stat_ranges", {})
        lo, hi = stat_ranges.get(stat, (0, 0))
        expected = float(expected_stats.get(stat, hi))
        label = f"{lo}" if lo == hi else f"{lo}-{hi}"
        color_key = max(1, min(20, max(lo, hi)))
        bg = SafeBreedingView._stat_tint(STAT_COLORS.get(color_key, QColor(100, 100, 115)), strength=0.22, lift=18)
        tip = f"Projected {stat}: {lo}-{hi} (expected {expected:.1f})"
        return SafeBreedingView._metric_item(label, bg, tip, row_bg=row_bg)

    @staticmethod
    def _projected_sum_item(projection: dict, expected_sum: float, *, row_bg: QColor | None = None) -> QTableWidgetItem:
        sum_lo, sum_hi = projection.get("sum_range", (0, 0))
        label = f"{sum_lo}" if sum_lo == sum_hi else f"{sum_lo}-{sum_hi}"
        avg_expected = float(projection.get("avg_expected", 0.0))
        seven_plus = float(projection.get("seven_plus_total", 0.0))
        color_key = max(1, min(20, int(round(avg_expected)) or 1))
        bg = SafeBreedingView._stat_tint(STAT_COLORS.get(color_key, QColor(100, 100, 115)), strength=0.20, lift=18)
        tip = f"Sum range: {sum_lo}-{sum_hi} | Avg {avg_expected:.1f} | 7+ {seven_plus:.1f}/7"
        item = SafeBreedingView._metric_item(label, bg, tip, row_bg=row_bg)
        item.setData(Qt.UserRole, float(expected_sum))
        return item

    @staticmethod
    def _projected_trait_item(field: str, value: float, *, row_bg: QColor | None = None) -> QTableWidgetItem:
        label = _trait_label_from_value(field, value) or "unknown"
        bg = _trait_level_color(label)
        tip = f"{field.title()}: {value:.3f} ({label})"
        return SafeBreedingView._metric_item(label, bg, tip, row_bg=row_bg)

    @staticmethod
    def _candidate_summary(
        chips: list[tuple[str, str]],
        *,
        max_items: int = 3,
        title: str = "",
        description_fn=None,
    ) -> tuple[str, str]:
        if not chips:
            return "—", title or "No candidates"

        visible = ", ".join(label for label, _ in chips[:max_items])
        if len(chips) > max_items:
            visible += f" +{len(chips) - max_items}"

        tooltip_lines: list[str] = []
        if title:
            tooltip_lines.append(title)
        for label, source in chips:
            lines = [label]
            if source:
                lines.append(source)
            if description_fn is not None:
                desc = str(description_fn(label) or "").strip()
                if desc:
                    lines.append(desc)
            tooltip_lines.append("\n".join(lines))
        return visible, "\n\n".join(tooltip_lines)

    def set_quality_mode(self, enabled: bool, refresh: bool = True):
        enabled = bool(enabled)
        if self._quality_mode == enabled and refresh:
            self._update_mode_controls()
            self._apply_mode_headers()
            self._filter_min_quality_row.setVisible(self._quality_mode)
            self._refresh_list()
            return
        self._quality_mode = enabled
        self._update_mode_controls()
        self._apply_mode_headers()
        self._filter_min_quality_row.setVisible(self._quality_mode)
        if refresh:
            self._refresh_list()

    def set_navigate_to_pair_callback(self, callback: Callable[[int, int], None] | None):
        self._navigate_to_pair_callback = callback
        current = self._focused_cat()
        if current is not None:
            self._render_for(current)

    def _focused_cat(self) -> Optional[Cat]:
        current = self._list.currentItem()
        if current is None:
            return None
        return self._by_key.get(int(current.data(Qt.UserRole)))

    def _pair_stimulation(self) -> float:
        try:
            from mewgenics.utils.config import _load_app_config
            value = _load_app_config().get("pair_stimulation", 50)
            return float(value if value is not None else 50)
        except Exception:
            return 50.0

    def _show_context_menu(self, pos):
        row = self._table.rowAt(pos.y())
        if row < 0 or row >= len(self._table_row_cat_keys):
            return
        cat = self._by_key.get(self._table_row_cat_keys[row])
        if cat is None:
            return

        menu = QMenu(self)
        open_tree = menu.addAction("Open Family Tree")
        find_best = menu.addAction(_tr("menu.context.find_best_pair", default="Find Best Pair"))
        planner = menu.addAction("Jump to Planner")
        menu.addSeparator()
        toggle_pin = menu.addAction("Toggle Pin")
        toggle_mb = menu.addAction("Toggle Must Breed")
        toggle_block = menu.addAction("Toggle Block")

        window = self.window()
        open_tree.triggered.connect(lambda: getattr(window, "_open_tree_for_cat", lambda _cat: None)(cat))
        find_best.triggered.connect(lambda: getattr(window, "_open_safe_breeding_for_cat", lambda _cat, quality=None: None)(cat, quality=True))
        planner.triggered.connect(lambda: getattr(window, "_open_perfect_planner_for_cat", lambda _cat: None)(cat))
        toggle_pin.triggered.connect(lambda: getattr(window, "_toggle_single_cat_pin", lambda _cat: None)(cat))
        toggle_mb.triggered.connect(lambda: getattr(window, "_toggle_single_cat_must_breed", lambda _cat: None)(cat))
        toggle_block.triggered.connect(lambda: getattr(window, "_toggle_single_cat_blacklist", lambda _cat: None)(cat))

        menu.exec(self._table.viewport().mapToGlobal(pos))

    def set_cats(self, cats: list[Cat]):
        selected_key = None
        cur = self._list.currentItem()
        if cur is not None:
            selected_key = int(cur.data(Qt.UserRole))
        self._cats = cats
        self._alive = sorted([c for c in cats if c.status != "Gone"], key=lambda c: (c.name or "").lower())
        self._by_key = {c.db_key: c for c in self._alive}
        self._lover_key_map = {
            cat.db_key: {
                lover.db_key
                for lover in getattr(cat, "lovers", [])
                if lover is not None and getattr(lover, "db_key", None) is not None and lover is not cat
            }
            for cat in self._alive
        }
        self._hater_key_map = {
            cat.db_key: {
                hater.db_key
                for hater in getattr(cat, "haters", [])
                if hater is not None and getattr(hater, "db_key", None) is not None and hater is not cat
            }
            for cat in self._alive
        }
        self._parent_key_map = {
            cat.db_key: {
                parent.db_key
                for parent in (getattr(cat, "parent_a", None), getattr(cat, "parent_b", None))
                if parent is not None and getattr(parent, "db_key", None) is not None
            }
            for cat in self._alive
        }
        self._rebuild_mutation_filter_list()
        self._refresh_list()
        if selected_key is not None and selected_key in self._by_key:
            self.select_cat(self._by_key[selected_key])
        elif self._list.count():
            self._list.setCurrentRow(0)
        else:
            self._render_for(None)

    def set_cache(self, cache: Optional['BreedingCache']):
        self._cache = cache
        # Re-render the currently selected cat with cached data
        cur = self._list.currentItem()
        if cur is not None:
            self._render_for(self._by_key.get(int(cur.data(Qt.UserRole))))

    def select_cat(self, cat: Optional[Cat]):
        if cat is None or cat.db_key not in self._by_key:
            return
        for i in range(self._list.count()):
            item = self._list.item(i)
            if int(item.data(Qt.UserRole)) == cat.db_key:
                self._list.setCurrentRow(i)
                self._list.scrollToItem(item)
                return

    # ── Filter handlers ─────────────────────────────────────────────
    def _on_filter_changed(self, *_):
        self._filter_hide_left_paired = self._filter_hide_left_paired_btn.isChecked()
        self._filter_hide_matches_paired = self._filter_hide_matches_btn.isChecked()
        self._filter_max_risk = int(self._filter_max_risk_spin.value())
        self._filter_min_quality = float(self._filter_min_quality_spin.value())
        self._refresh_list()
        cur = self._list.currentItem()
        if cur is not None:
            self._render_for(self._by_key.get(int(cur.data(Qt.UserRole))))

    def _on_mutation_item_changed(self, item: QListWidgetItem):
        name = item.data(Qt.UserRole)
        if not isinstance(name, str):
            return
        if item.checkState() == Qt.Checked:
            self._filter_required_mutations.add(name)
        else:
            self._filter_required_mutations.discard(name)
        self._on_filter_changed()

    def _refresh_mutation_filter_visibility(self, *_):
        query = self._filter_mutation_search.text().strip().lower()
        for i in range(self._filter_mutation_list.count()):
            item = self._filter_mutation_list.item(i)
            text = (item.text() or "").lower()
            item.setHidden(bool(query) and query not in text)

    def _reset_filters(self):
        self._filter_hide_left_paired_btn.setChecked(False)
        self._filter_hide_matches_btn.setChecked(False)
        self._filter_max_risk_spin.setValue(100)
        self._filter_min_quality_spin.setValue(-999.0)
        self._filter_mutation_search.clear()
        self._filter_required_mutations.clear()
        self._filter_mutation_list.blockSignals(True)
        try:
            for i in range(self._filter_mutation_list.count()):
                self._filter_mutation_list.item(i).setCheckState(Qt.Unchecked)
        finally:
            self._filter_mutation_list.blockSignals(False)
        self._refresh_mutation_filter_visibility()
        self._on_filter_changed()

    @staticmethod
    def _cat_trait_pool(cat: Cat) -> set[str]:
        """All inheritable trait names on a cat (mutations + passives +
        disorders + defects + abilities). Used by the mutation filter,
        which is broader than just visual mutations."""
        pool: set[str] = set()
        for attr in ("mutations", "passive_abilities", "disorders", "defects", "abilities"):
            for t in (getattr(cat, attr, None) or []):
                if t:
                    pool.add(str(t))
        return pool

    def _has_mutual_lover(self, cat: Cat) -> bool:
        cat_lovers = self._lover_key_map.get(cat.db_key, set())
        for lover_key in cat_lovers:
            if cat.db_key in self._lover_key_map.get(lover_key, set()):
                return True
        return False

    def _has_any_lover(self, cat: Cat) -> bool:
        """True if the cat has any lover, mutual or not.

        Why: once a cat has fallen in love, they can't fall in love again,
        so for filtering purposes any lover marks them as "taken".
        """
        return bool(self._lover_key_map.get(cat.db_key))

    def _rebuild_mutation_filter_list(self):
        """Rebuild the filter checklist from the current alive roster."""
        names = sorted({
            t for cat in self._alive for t in self._cat_trait_pool(cat)
        }, key=lambda s: s.lower())
        self._all_house_mutations = names
        # Drop any selected mutations no longer present in the house.
        self._filter_required_mutations &= set(names)

        self._filter_mutation_list.blockSignals(True)
        try:
            self._filter_mutation_list.clear()
            for name in names:
                item = QListWidgetItem(name)
                item.setData(Qt.UserRole, name)
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                item.setCheckState(Qt.Checked if name in self._filter_required_mutations else Qt.Unchecked)
                self._filter_mutation_list.addItem(item)
        finally:
            self._filter_mutation_list.blockSignals(False)
        self._refresh_mutation_filter_visibility()
        self._highlight_selected_cat_mutations(self._focused_cat())

    def _highlight_selected_cat_mutations(self, cat: Optional[Cat]):
        """Bold + tint traits the selected cat already has.

        Blocks signals while mutating display properties — otherwise
        setText/setFont fire itemChanged, which loops back through
        _on_filter_changed → _refresh_list → setCurrentRow → here.
        """
        cat_muts: set[str] = self._cat_trait_pool(cat) if cat is not None else set()
        bold = QFont()
        bold.setBold(True)
        normal = QFont()
        self._filter_mutation_list.blockSignals(True)
        try:
            for i in range(self._filter_mutation_list.count()):
                item = self._filter_mutation_list.item(i)
                name = item.data(Qt.UserRole)
                if isinstance(name, str) and name in cat_muts:
                    item.setFont(bold)
                    item.setForeground(QBrush(QColor(178, 220, 255)))
                    txt = item.text()
                    if not txt.endswith(" ✓"):
                        item.setText(f"{name} ✓")
                else:
                    item.setFont(normal)
                    item.setForeground(QBrush(QColor(220, 220, 220)))
                    if isinstance(name, str):
                        item.setText(name)
        finally:
            self._filter_mutation_list.blockSignals(False)

    def _refresh_list(self):
        query = self._search.text().strip().lower()
        current_key = None
        cur = self._list.currentItem()
        if cur is not None:
            current_key = int(cur.data(Qt.UserRole))

        self._list.clear()
        for cat in self._alive:
            if query and query not in cat.name.lower():
                continue
            has_lover = self._has_any_lover(cat)
            if self._filter_hide_left_paired and has_lover:
                continue
            room_display = ROOM_DISPLAY.get(cat.room, cat.room or "—")
            heart = " ♥" if has_lover else ""
            cat_age = getattr(cat, "age", None)
            age_str = f" · age {cat_age}" if cat_age is not None else ""
            text = f"{cat.name}{heart}  ({cat.gender_display}){age_str}  · {room_display}"
            if cat.is_blacklisted:
                text += f"  [{_tr('safe_breeding.list.blocked')}]"
            if cat.must_breed:
                text += f"  [{_tr('safe_breeding.list.must')}]"
            item = QListWidgetItem(text)
            item.setData(Qt.UserRole, cat.db_key)
            icon = _make_tag_icon(_cat_tags(cat), dot_size=10, spacing=3)
            if not icon.isNull():
                item.setIcon(icon)
            if cat.is_blacklisted:
                item.setForeground(QBrush(QColor(170, 100, 100)))
            if cat.must_breed:
                item.setForeground(QBrush(QColor(98, 194, 135)))
            self._list.addItem(item)
        if self._list.count() == 0:
            self._render_for(None)
            return
        if current_key is not None:
            for i in range(self._list.count()):
                item = self._list.item(i)
                if int(item.data(Qt.UserRole)) == current_key:
                    self._list.setCurrentRow(i)
                    return
        self._list.setCurrentRow(0)

    def _on_current_item_changed(self, current, previous):
        if current is None:
            self._highlight_selected_cat_mutations(None)
            self._render_for(None)
            return
        cat = self._by_key.get(int(current.data(Qt.UserRole)))
        self._highlight_selected_cat_mutations(cat)
        self._render_for(cat)

    def _on_table_row_clicked(self, row: int, _column: int):
        if row < 0 or row >= len(self._table_row_cat_keys):
            return
        cat = self._by_key.get(self._table_row_cat_keys[row])
        if cat is not None:
            self.select_cat(cat)

    def _render_for(self, cat: Optional[Cat]):
        # This view is a ranking table. Keep sorting disabled so row indices
        # remain stable while we populate all columns for each candidate.
        self._table.setSortingEnabled(False)
        self._table.setRowCount(0)
        self._table_row_cat_keys = []
        if cat is None:
            self._title.setText("Mating Pair Search")
            self._summary.setText(_tr("safe_breeding.summary_empty"))
            self._apply_mode_headers()
            return

        cache = self._cache
        self._title.setText(f"Mating Pair Search for {cat.name}")

        required_muts = self._filter_required_mutations
        max_risk = self._filter_max_risk
        min_quality = self._filter_min_quality

        candidates: list[dict[str, object]] = []
        for other in self._alive:
            if other is cat:
                continue
            ok, _ = can_breed(cat, other)
            if not ok:
                continue
            if self._filter_hide_matches_paired and self._has_any_lover(other):
                continue
            if required_muts:
                if not (self._cat_trait_pool(other) & required_muts):
                    continue
            if cache is not None and cache.ready:
                shared, recent_shared = cache.get_shared(cat, other, recent_depth=3)
                rel = cache.get_risk(cat, other)
            else:
                shared, recent_shared = shared_ancestor_counts(cat, other, recent_depth=3)
                rel = risk_percent(cat, other)
            if rel > max_risk:
                continue
            quality_factors: Optional[PairFactors] = None
            if self._quality_mode:
                quality_factors = score_pair(
                    cat,
                    other,
                    hater_key_map=getattr(self, "_hater_key_map", {}),
                    lover_key_map=getattr(self, "_lover_key_map", {}),
                    parent_key_map=getattr(self, "_parent_key_map", {}),
                    cache=cache,
                    stimulation=self._pair_stimulation(),
                )
                if not quality_factors.compatible:
                    continue
                if quality_factors.quality < min_quality:
                    continue
            closest_recent_gen = 0
            if recent_shared:
                if cache is not None and cache.ready:
                    da = cache.get_ancestor_depths_for(cat)
                    db = cache.get_ancestor_depths_for(other)
                else:
                    da = _ancestor_depths(cat, max_depth=8)
                    db = _ancestor_depths(other, max_depth=8)
                common = set(da.keys()) & set(db.keys())
                recent_levels = [
                    max(da[anc], db[anc])
                    for anc in common
                    if da[anc] <= 3 and db[anc] <= 3
                ]
                closest_recent_gen = min(recent_levels) if recent_levels else 3

            lover_keys = {
                lover.db_key
                for lover in getattr(cat, "lovers", [])
                if lover is not None and getattr(lover, "db_key", None) is not None
            }
            is_loved = other.db_key in lover_keys
            is_mutual_love = is_loved and cat.db_key in {
                lover.db_key
                for lover in getattr(other, "lovers", [])
                if lover is not None and getattr(lover, "db_key", None) is not None
            }

            row_bg = None
            row_fg = None
            if is_mutual_love:
                row_bg = QColor(132, 36, 88)
                row_fg = QColor(246, 229, 239)
            elif is_loved:
                row_bg = QColor(224, 176, 201)
                row_fg = QColor(52, 32, 44)

            notes: list[str] = []
            if shared:
                notes.append(_tr("safe_breeding.notes.shared", default="Shared ancestors: {count}", count=shared))
            if recent_shared:
                notes.append(_tr("safe_breeding.notes.recent_shared", default="Recent shared: {count}", count=recent_shared))
            if is_mutual_love:
                notes.append(_tr("safe_breeding.notes.mutual_lovers", default="Mutual lovers"))
            elif is_loved:
                notes.append(_tr("safe_breeding.notes.loved", default="Lover pair"))

            if self._quality_mode and quality_factors is not None:
                projection = quality_factors.projection
                expected_sum = sum(projection.expected_stats.values())
                trait_values = {
                    "aggression": (getattr(cat, "aggression", 0.0) + getattr(other, "aggression", 0.0)) / 2.0,
                    "libido": (getattr(cat, "libido", 0.0) + getattr(other, "libido", 0.0)) / 2.0,
                    "inbredness": kinship_coi(cat, other),
                }
                stim = self._pair_stimulation()
                active_candidates, _, _ = _inheritance_candidates(
                    list(getattr(cat, "abilities", []) or []),
                    list(getattr(other, "abilities", []) or []),
                    stim,
                    display_fn=_mutation_display_name,
                )
                passive_candidates, _, _ = _inheritance_candidates(
                    list(getattr(cat, "passive_abilities", []) or []),
                    list(getattr(other, "passive_abilities", []) or []),
                    stim,
                    display_fn=_mutation_display_name,
                )
                mutation_candidates, _, _ = _inheritance_candidates(
                    list(getattr(cat, "mutations", []) or []),
                    list(getattr(other, "mutations", []) or []),
                    stim,
                    display_fn=_mutation_display_name,
                )
                if quality_factors.must_breed_bonus:
                    notes.append(_tr("safe_breeding.notes.must_breed", default="Must breed"))
                if quality_factors.lover_bonus:
                    notes.append(_tr("safe_breeding.notes.mutual_lovers", default="Mutual lovers"))
                if quality_factors.trait_bonus:
                    notes.append(_tr("safe_breeding.notes.trait_bonus", default="Trait bonus: {value:.1f}", value=quality_factors.trait_bonus))
                if quality_factors.variance_penalty:
                    notes.append(_tr("safe_breeding.notes.variance_penalty", default="Variance penalty: {value:.1f}", value=quality_factors.variance_penalty))
                candidates.append({
                    "cat": other,
                    "quality": float(quality_factors.quality),
                    "risk": float(rel),
                    "shared": int(shared),
                    "recent_shared": int(recent_shared),
                    "projection": projection,
                    "expected_sum": float(expected_sum),
                    "trait_values": trait_values,
                    "active_candidates": active_candidates,
                    "passive_candidates": passive_candidates,
                    "mutation_candidates": mutation_candidates,
                    "notes": notes,
                    "row_bg": row_bg,
                    "row_fg": row_fg,
                })
            else:
                candidates.append({
                    "cat": other,
                    "risk": float(rel),
                    "shared": int(shared),
                    "closest_recent_gen": int(closest_recent_gen),
                    "notes": notes,
                    "row_bg": row_bg,
                    "row_fg": row_fg,
                })

        if self._quality_mode:
            candidates.sort(key=lambda item: (-float(item["quality"]), float(item["risk"]), (item["cat"].name or "").lower()))
        else:
            candidates.sort(key=lambda item: (float(item["risk"]), int(item["shared"]) * 1000 + int(item["closest_recent_gen"]), (item["cat"].name or "").lower()))

        self._summary.setText(_tr(
            "safe_breeding.summary_quality" if self._quality_mode else "safe_breeding.summary",
            default="Quality-ranked compatible mates: {count}" if self._quality_mode else "Compatible mates: {count}",
            count=len(candidates),
        ))
        self._table.setRowCount(len(candidates))
        for row, item in enumerate(candidates):
            other = item["cat"]
            self._table_row_cat_keys.append(other.db_key)
            row_bg = item.get("row_bg")
            row_fg = item.get("row_fg")
            other_heart = " ♥" if self._has_any_lover(other) else ""
            other_age_val = getattr(other, "age", None)
            other_age = f" · age {other_age_val}" if other_age_val is not None else ""
            name_text = f"{other.name}{other_heart} ({other.gender_display}){other_age}"
            if item.get("quality") is None:
                risk_pct = int(round(float(item["risk"])))
                if risk_pct >= 100:
                    tag, risk_color = _tr("safe_breeding.tag.highly_inbred"), QColor(217, 119, 119)
                elif risk_pct >= 50:
                    tag, risk_color = _tr("safe_breeding.tag.moderately_inbred"), QColor(216, 181, 106)
                elif risk_pct >= 20:
                    tag, risk_color = _tr("safe_breeding.tag.slightly_inbred"), QColor(143, 201, 230)
                else:
                    tag, risk_color = _tr("safe_breeding.tag.not_inbred"), QColor(98, 194, 135)
                name_item = QTableWidgetItem(name_text)
                icon = _make_tag_icon(_cat_tags(other), dot_size=14, spacing=4)
                if not icon.isNull():
                    name_item.setIcon(icon)
                risk_item = QTableWidgetItem(f"{risk_pct}%")
                shared_item = QTableWidgetItem(str(item["shared"]))
                outcome_item = QTableWidgetItem(tag)
                risk_item.setData(Qt.UserRole, risk_pct)
                shared_item.setData(Qt.UserRole, item["shared"])
                for it in (name_item, risk_item, shared_item, outcome_item):
                    it.setTextAlignment(Qt.AlignCenter)
                    if row_bg is not None:
                        it.setBackground(QBrush(row_bg))
                        if row_fg is not None:
                            it.setForeground(QBrush(row_fg))
                outcome_item.setForeground(QBrush(risk_color))
                self._table.setCellWidget(row, self._PAIR_JUMP_COL, self._make_pair_jump_button(cat.db_key, other.db_key))
                self._table.setItem(row, self._SAFE_CAT_COL, name_item)
                self._table.setItem(row, self._SAFE_ROOM_COL, self._make_room_item(other, row_bg, row_fg))
                self._table.setItem(row, self._SAFE_RISK_COL, risk_item)
                self._table.setItem(row, self._SAFE_SHARED_COL, shared_item)
                self._table.setItem(row, self._SAFE_OUTCOME_COL, outcome_item)
                continue

            quality_item = QTableWidgetItem(f"{float(item['quality']):.1f}")
            risk_pct = int(round(float(item["risk"])))
            risk_item = QTableWidgetItem(f"{risk_pct}%")
            projection = item["projection"]
            expected_sum = float(item["expected_sum"])
            notes_item = QTableWidgetItem("; ".join(item.get("notes", [])) or "—")
            if row_bg is not None:
                for it in (quality_item, risk_item, notes_item):
                    it.setBackground(QBrush(row_bg))
                    if row_fg is not None:
                        it.setForeground(QBrush(row_fg))
            icon = _make_tag_icon(_cat_tags(other), dot_size=14, spacing=4)
            name_item = QTableWidgetItem(name_text)
            if not icon.isNull():
                name_item.setIcon(icon)
            for it in (name_item, quality_item, risk_item):
                it.setTextAlignment(Qt.AlignCenter)
            font = quality_item.font()
            font.setBold(True)
            quality_item.setFont(font)
            if row_fg is None:
                quality_item.setForeground(QBrush(QColor(98, 194, 135)))
            risk_item.setData(Qt.UserRole, risk_pct)
            stat_items = [
                self._projected_stat_item(stat, projection, row_bg=row_bg)
                for stat in STAT_NAMES
            ]
            sum_item = self._projected_sum_item(projection, expected_sum, row_bg=row_bg)
            trait_values = item.get("trait_values", {})
            trait_items = [
                self._projected_trait_item(field, float(trait_values.get(field, 0.0)), row_bg=row_bg)
                for field in ("aggression", "libido", "inbredness")
            ]
            shared_item = QTableWidgetItem(str(int(item.get("shared", 0))))
            recent_shared_item = QTableWidgetItem(str(int(item.get("recent_shared", 0))))
            mutation_text, mutation_tip = self._candidate_summary(
                item.get("mutation_candidates", []),
                max_items=3,
                title="Likely mutations",
                description_fn=_ability_tip,
            )
            active_text, active_tip = self._candidate_summary(
                item.get("active_candidates", []),
                max_items=3,
                title="Active abilities",
                description_fn=_ability_tip,
            )
            passive_text, passive_tip = self._candidate_summary(
                item.get("passive_candidates", []),
                max_items=3,
                title="Passive abilities",
                description_fn=_ability_tip,
            )
            ability_parts: list[str] = []
            ability_tooltip_parts: list[str] = []
            if item.get("active_candidates"):
                ability_parts.append(f"A: {active_text}")
                ability_tooltip_parts.append(active_tip)
            if item.get("passive_candidates"):
                ability_parts.append(f"P: {passive_text}")
                ability_tooltip_parts.append(passive_tip)
            ability_item = QTableWidgetItem(" | ".join(ability_parts) if ability_parts else "—")
            mutation_item = QTableWidgetItem(mutation_text)
            mutation_item.setToolTip(mutation_tip if item.get("mutation_candidates") else _tr("safe_breeding.no_mutations.tooltip", default="No likely mutations"))
            ability_item.setToolTip("\n\n".join(ability_tooltip_parts) if ability_tooltip_parts else _tr("safe_breeding.no_abilities.tooltip", default="No inherited abilities"))
            for it in (name_item, quality_item, risk_item, shared_item, recent_shared_item, mutation_item, ability_item):
                if it in (mutation_item, ability_item):
                    it.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
                else:
                    it.setTextAlignment(Qt.AlignCenter)
                if row_bg is not None:
                    it.setBackground(QBrush(row_bg))
                    if row_fg is not None:
                        it.setForeground(QBrush(row_fg))
            quality_item.setData(Qt.UserRole, float(item["quality"]))
            notes_item.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            notes_item.setToolTip("; ".join(item.get("notes", [])) or "—")
            shared_item.setData(Qt.UserRole, int(item.get("shared", 0)))
            recent_shared_item.setData(Qt.UserRole, int(item.get("recent_shared", 0)))
            shared_item.setToolTip(_tr("safe_breeding.table.shared_ancestors", default="Shared ancestors") + f": {int(item.get('shared', 0))}")
            recent_shared_item.setToolTip(_tr("safe_breeding.table.recent_shared_ancestors", default="Recent shared") + f": {int(item.get('recent_shared', 0))}")
            if row_fg is None:
                risk_item.setForeground(QBrush(QColor(216, 181, 106)))
            quality_item.setToolTip(
                _tr(
                    "safe_breeding.quality_tooltip",
                    default="Quality blends offspring stat expectations, risk, and bonus signals.",
                )
            )
            self._table.setCellWidget(row, self._PAIR_JUMP_COL, self._make_pair_jump_button(cat.db_key, other.db_key))
            self._table.setItem(row, self._QUALITY_CAT_COL, name_item)
            self._table.setItem(row, self._QUALITY_ROOM_COL, self._make_room_item(other, row_bg, row_fg))
            self._table.setItem(row, self._QUALITY_QUALITY_COL, quality_item)
            self._table.setItem(row, self._QUALITY_RISK_COL, risk_item)
            for idx, stat_item in enumerate(stat_items, start=self._QUALITY_STAT_START):
                self._table.setItem(row, idx, stat_item)
            self._table.setItem(row, self._QUALITY_SUM_COL, sum_item)
            for idx, trait_item in enumerate(trait_items, start=self._QUALITY_TRAIT_START):
                self._table.setItem(row, idx, trait_item)
            self._table.setItem(row, self._QUALITY_SHARED_COL, shared_item)
            self._table.setItem(row, self._QUALITY_RECENT_SHARED_COL, recent_shared_item)
            self._table.setItem(row, self._QUALITY_MUTS_COL, mutation_item)
            self._table.setItem(row, self._QUALITY_ABIL_COL, ability_item)
            self._table.setItem(row, self._QUALITY_NOTES_COL, notes_item)

        self._apply_mode_headers()
