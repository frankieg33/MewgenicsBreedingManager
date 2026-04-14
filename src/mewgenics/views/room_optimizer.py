"""Room Optimizer views extracted from mewgenics_manager.py."""

import hashlib
import html
import json
from typing import Optional

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QToolButton, QTableWidget, QTableWidgetItem,
    QHeaderView, QAbstractItemView, QSplitter, QFrame,
    QScrollArea, QSizePolicy, QLineEdit, QTextBrowser, QTabWidget,
)
from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QColor, QBrush

from save_parser import Cat, STAT_NAMES, FurnitureRoomSummary

from mewgenics.constants import (
    STAT_COLORS, PAIR_COLORS,
    _room_color, _room_tint, _room_key_from_display,
    COL_BL, COL_PIN,
)
from mewgenics.utils.localization import _tr, ROOM_DISPLAY
from mewgenics.utils.abilities import _planner_trait_name_html, _planner_trait_weight
from mewgenics.utils.config import (
    _saved_optimizer_flag, _set_optimizer_flag,
    _saved_room_optimizer_auto_recalc,
)
from mewgenics.utils.optimizer_settings import (
    _saved_optimizer_search_temperature, _saved_optimizer_search_neighbors,
    _save_room_priority_config,
)
from mewgenics.utils.planner_state import (
    MUTATION_CLASS_MODES,
    _load_planner_state_value, _save_planner_state_value,
    _mutation_class_label,
    _normalize_mutation_mode_profiles,
    _planner_import_traits_summary,
)
from mewgenics.utils.tags import _make_tag_icon
from mewgenics.utils.calibration import _trait_level_color
from mewgenics.utils.styling import _enforce_min_font_in_widget_tree
from mewgenics.models.breeding_cache import BreedingCache
from mewgenics.models.cat_table_model import _SortByUserRoleItem
from mewgenics.workers.optimizer_worker import RoomOptimizerWorker
from mewgenics.panels.room_priority import RoomPriorityPanel


class RoomOptimizerStatPrioritiesPanel(QWidget):
    """Editor for class-specific stat priorities used by the room optimizer."""

    def __init__(self, on_priority_changed, parent=None):
        super().__init__(parent)
        self._on_priority_changed = on_priority_changed
        self._profiles: dict[str, dict] = _normalize_mutation_mode_profiles({})
        self._mode_sections: dict[str, dict] = {}
        self.setStyleSheet(
            "QWidget { background:#0a0a18; }"
            "QLabel { color:#bbb; }"
            "QPushButton { background:#1a1a32; color:#ddd; border:1px solid #2a2a4a; border-radius:4px; padding:4px 10px; font-size:11px; }"
            "QPushButton:hover { background:#252545; }"
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(8)

        header = QHBoxLayout()
        self._title = QLabel("Class Stat Priorities")
        self._title.setStyleSheet("color:#ddd; font-size:16px; font-weight:bold;")
        header.addWidget(self._title)
        header.addStretch(1)
        root.addLayout(header)

        self._summary = QLabel("Adjust the class stat orders here. Each room mode uses its own list.")
        self._summary.setStyleSheet("color:#8d8da8; font-size:11px;")
        self._summary.setWordWrap(True)
        root.addWidget(self._summary)

        self._list_widget = QWidget()
        self._list_layout = QHBoxLayout(self._list_widget)
        self._list_layout.setContentsMargins(0, 0, 0, 0)
        self._list_layout.setSpacing(8)
        self._list_layout.setAlignment(Qt.AlignTop)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet("QScrollArea { border:none; background:transparent; }")
        scroll.setWidget(self._list_widget)
        root.addWidget(scroll, 1)

        self._empty = QLabel("Connect the mutation planner to edit class priorities.")
        self._empty.setStyleSheet("color:#666; font-size:11px;")
        self._empty.setWordWrap(True)
        root.addWidget(self._empty)

        self.refresh_profiles(self._profiles)

    def _order_for_mode(self, mode: str) -> list[str]:
        return list((self._profiles.get(mode, {}) or {}).get("stat_priority", []) or [])

    def _trait_count_for_mode(self, mode: str) -> int:
        return len((self._profiles.get(mode, {}) or {}).get("traits", []) or [])

    def _trait_names_for_mode(self, mode: str) -> list[str]:
        """Return the display names of the selected mutation traits for a mode."""
        traits = (self._profiles.get(mode, {}) or {}).get("traits", []) or []
        return [str(t.get("display") or t.get("key") or "?") for t in traits if isinstance(t, dict)]

    def _clear_card_layout(self, layout: QVBoxLayout):
        while layout.count():
            child = layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

    def _rebuild_mode_card(self, mode: str):
        section = self._mode_sections.get(mode)
        if not section:
            return
        list_layout = section["list_layout"]
        self._clear_card_layout(list_layout)

        order = self._order_for_mode(mode)
        trait_names = self._trait_names_for_mode(mode)
        if trait_names:
            summary_text = ", ".join(trait_names)
        else:
            summary_text = "No mutations selected."
        section["summary"].setText(summary_text)

        for index, stat in enumerate(order):
            row = QWidget()
            row.setStyleSheet("QWidget { background:#101023; border:1px solid #26264a; border-radius:5px; }")
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(8, 6, 8, 6)
            row_layout.setSpacing(8)

            rank = QLabel(f"{index + 1}.")
            rank.setStyleSheet("color:#8fb8a0; font-size:11px; font-weight:bold;")
            row_layout.addWidget(rank)

            stat_label = QLabel(stat)
            stat_label.setStyleSheet("color:#ddd; font-size:12px; font-weight:bold;")
            row_layout.addWidget(stat_label, 1)

            up_btn = QPushButton("↑")
            up_btn.setFixedSize(24, 24)
            up_btn.setEnabled(index > 0)
            up_btn.clicked.connect(lambda _=False, mm=mode, ii=index: self._move_stat(mm, ii, -1))
            row_layout.addWidget(up_btn)

            down_btn = QPushButton("↓")
            down_btn.setFixedSize(24, 24)
            down_btn.setEnabled(index < len(order) - 1)
            down_btn.clicked.connect(lambda _=False, mm=mode, ii=index: self._move_stat(mm, ii, 1))
            row_layout.addWidget(down_btn)

            list_layout.addWidget(row)

        list_layout.addStretch(1)

    def _rebuild_list(self):
        while self._list_layout.count():
            child = self._list_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()
        self._mode_sections.clear()
        has_any = False
        for mode in MUTATION_CLASS_MODES:
            card = QFrame()
            card.setStyleSheet("QFrame { background:#0f0f22; border:1px solid #26264a; border-radius:6px; }")
            card.setMinimumWidth(235)
            card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(10, 8, 10, 8)
            card_layout.setSpacing(6)

            header = QHBoxLayout()
            title = QLabel(_mutation_class_label(mode))
            title.setStyleSheet("color:#8fb8a0; font-size:13px; font-weight:bold;")
            header.addWidget(title)
            header.addStretch(1)
            reset_btn = QPushButton("Reset to Recommended")
            reset_btn.clicked.connect(lambda _=False, mm=mode: self._reset_mode(mm))
            header.addWidget(reset_btn)
            card_layout.addLayout(header)

            summary = QLabel("")
            summary.setStyleSheet("color:#8d8da8; font-size:11px;")
            summary.setWordWrap(True)
            card_layout.addWidget(summary)

            list_widget = QWidget()
            list_layout = QVBoxLayout(list_widget)
            list_layout.setContentsMargins(0, 0, 0, 0)
            list_layout.setSpacing(4)
            card_layout.addWidget(list_widget)

            self._mode_sections[mode] = {
                "summary": summary,
                "list_layout": list_layout,
                "reset_btn": reset_btn,
            }
            self._rebuild_mode_card(mode)
            self._list_layout.addWidget(card)
            has_any = has_any or bool(self._order_for_mode(mode))

        self._list_layout.addStretch(1)
        self._empty.setVisible(not has_any)

    def _move_stat(self, mode: str, index: int, direction: int):
        order = self._order_for_mode(mode)
        new_index = index + direction
        if not (0 <= index < len(order) and 0 <= new_index < len(order)):
            return
        order[index], order[new_index] = order[new_index], order[index]
        self._profiles.setdefault(mode, {})["stat_priority"] = order
        self._on_priority_changed(mode, order)
        self._rebuild_mode_card(mode)

    def _reset_mode(self, mode: str):
        default_order = _normalize_mutation_mode_profiles({}).get(mode, {}).get("stat_priority", [])
        self._profiles.setdefault(mode, {})["stat_priority"] = list(default_order)
        self._on_priority_changed(mode, list(default_order))
        self._rebuild_mode_card(mode)

    def refresh_profiles(self, mode_profiles: dict[str, dict], *, selected_mode: str = "best_pairs"):
        self._profiles = _normalize_mutation_mode_profiles(mode_profiles)
        self._rebuild_list()

    def retranslate_ui(self):
        self._title.setText(_tr("room_optimizer.tab.stat_priorities", default="Class Stats"))
        self._summary.setText(_tr(
            "room_optimizer.stat_priorities.summary",
            default="Adjust the class stat orders here. Each room mode uses its own list.",
        ))
        self._empty.setText(_tr("room_optimizer.stat_priorities.empty", default="Connect the mutation planner to edit class priorities."))
        for section in self._mode_sections.values():
            section["reset_btn"].setText(_tr("mutation_planner.reset_stat_priority", default="Reset to Recommended"))
        self._rebuild_list()


class RoomOptimizerView(QWidget):
    """View for optimizing cat room distribution to maximize breeding outcomes."""

    @staticmethod
    def _set_toggle_button_label(btn: QPushButton, label_key: str):
        defaults = {
            "room_optimizer.toggle.minimize_variance": "Minimize Variance",
            "room_optimizer.toggle.avoid_lovers": "Avoid Lovers",
            "room_optimizer.toggle.prefer_low_aggression": "Prefer Low Aggression",
            "room_optimizer.toggle.prefer_high_libido": "Prefer High Libido",
            "room_optimizer.toggle.maximize_throughput": "Maximize Throughput",
            "room_optimizer.toggle.ignore_stat_priority": "Ignore Class Stat Priorities",
            "room_optimizer.toggle.send_kittens_to_fallback": "Kittens to Fallback",
            "room_optimizer.toggle.avoid_trait_loss": "Avoid Trait Loss",
            "room_optimizer.toggle.use_sa": "More Depth",
        }
        state = _tr("common.on", default="On") if btn.isChecked() else _tr("common.off", default="Off")
        btn.setText(f"{_tr(label_key, default=defaults.get(label_key, label_key))}: {state}")

    @staticmethod
    def _bind_persistent_toggle(btn: QPushButton, label_key: str, key: str):
        RoomOptimizerView._set_toggle_button_label(btn, label_key)
        btn.toggled.connect(lambda checked: _set_optimizer_flag(key, checked))
        btn.toggled.connect(lambda _: RoomOptimizerView._set_toggle_button_label(btn, label_key))

    @staticmethod
    def _planner_mode_profiles_total_traits(mode_profiles: dict[str, dict]) -> int:
        return sum(len((mode_profiles.get(mode, {}) or {}).get("traits", []) or []) for mode in MUTATION_CLASS_MODES)

    @staticmethod
    def _planner_mode_profiles_summary(mode_profiles: dict[str, dict], active_mode: str) -> str:
        total_traits = RoomOptimizerView._planner_mode_profiles_total_traits(mode_profiles)
        active_label = _mutation_class_label(active_mode)
        configured_modes = [
            mode for mode in MUTATION_CLASS_MODES
            if (mode_profiles.get(mode, {}) or {}).get("traits")
        ]
        return f"{len(configured_modes)}/4 trees, {total_traits} traits, active: {active_label}"

    @staticmethod
    def _planner_mode_profiles_tooltip(mode_profiles: dict[str, dict], active_mode: str, *, empty_text: str) -> str:
        total_traits = RoomOptimizerView._planner_mode_profiles_total_traits(mode_profiles)
        if total_traits <= 0:
            return empty_text
        lines = [
            "Imported mutation class trees:",
            f"Active tree: {_mutation_class_label(active_mode)}",
        ]
        for mode in MUTATION_CLASS_MODES:
            profile = mode_profiles.get(mode, {}) or {}
            traits = list(profile.get("traits", []) or [])
            stat_priority = list(profile.get("stat_priority", []) or [])
            summary = _planner_import_traits_summary(traits) if traits else "No traits selected"
            priority = " > ".join(stat_priority[:4]) + ("..." if len(stat_priority) > 4 else "")
            lines.append(f"- {_mutation_class_label(mode)}: {summary}")
            lines.append(f"  Stats: {priority or 'None'}")
        return "\n".join(lines)

    def _set_mode_button_text(self, enabled: bool):
        key = "room_optimizer.mode_family" if enabled else "room_optimizer.mode_pair"
        self._mode_toggle_btn.setText(_tr(key))
        self._mode_toggle_btn.setToolTip(_tr("room_optimizer.mode_tooltip"))

    @staticmethod
    def _style_room_action_button(btn: QPushButton, background: str, border: str, hover_background: str):
        btn.setCheckable(False)
        btn.setMinimumWidth(110)
        btn.setStyleSheet(
            "QPushButton { "
            f"background:{background}; color:#f1f1f1; border:1px solid {border}; "
            "border-radius:4px; padding:4px 10px; font-size:11px; font-weight:bold; }"
            f"QPushButton:hover {{ background:{hover_background}; }}"
            "QPushButton:pressed { background:#1a1a1a; }"
        )

    @staticmethod
    def _style_import_planner_button(btn: QPushButton, active: bool = False):
        if active:
            btn.setStyleSheet(
                "QPushButton { background:#2a3a5a; color:#aaddff; border:1px solid #4a6a9a; "
                "border-radius:4px; padding:6px 12px 6px 10px; font-size:11px; text-align:left; }"
                "QPushButton:hover { background:#3a4a6a; color:#ddd; }"
            )
        else:
            btn.setStyleSheet(
                "QPushButton { background:#2a2a5a; color:#bbbbee; border:1px solid #4a4a8a; "
                "border-radius:4px; padding:6px 12px 6px 10px; font-size:11px; text-align:left; }"
                "QPushButton:hover { background:#3a3a6a; color:#ddd; }"
            )

    def _set_room_action_button_texts(self):
        self._must_breed_action_btn.setText(_tr("bulk.toggle_must_breed"))
        self._must_breed_action_btn.setToolTip(_tr("bulk.toggle_must_breed.tooltip"))
        self._breeding_block_action_btn.setText(_tr("bulk.toggle_breeding_block"))
        self._breeding_block_action_btn.setToolTip(_tr("bulk.toggle_breeding_block.tooltip"))
        self._pin_action_btn.setText(_tr("bulk.toggle_pin", default="Toggle Pin"))
        self._pin_action_btn.setToolTip(_tr("bulk.toggle_pin.tooltip", default="Toggle pin for selected cats"))

    def _current_room_data(self) -> Optional[dict]:
        selected_ranges = self._table.selectedRanges()
        if not selected_ranges:
            return None
        row = selected_ranges[0].topRow()
        room_item = self._table.item(row, 0)
        if room_item is None:
            return None
        data = room_item.data(Qt.UserRole)
        return data if isinstance(data, dict) else None

    def _room_cats_from_data(self, data: Optional[dict]) -> list[Cat]:
        if not data:
            return []
        cat_keys: list[int] = []
        for key in data.get("cat_keys", []) or []:
            try:
                cat_keys.append(int(key))
            except (TypeError, ValueError):
                continue
        if not cat_keys and data.get("room") == "Excluded":
            for row in data.get("excluded_cat_rows", []) or []:
                try:
                    cat_keys.append(int(row.get("db_key")))
                except (TypeError, ValueError):
                    continue
        if not cat_keys:
            wanted_names = {
                str(name).split(" (", 1)[0]
                for name in (data.get("cats", []) or [])
                if name
            }
            if not wanted_names:
                return []
            return [cat for cat in self._cats if cat.name in wanted_names]
        lookup = getattr(self, "_cat_lookup", None) or {cat.db_key: cat for cat in self._cats}
        seen: set[int] = set()
        cats: list[Cat] = []
        for key in cat_keys:
            if key in seen:
                continue
            seen.add(key)
            cat = lookup.get(key)
            if cat is not None:
                cats.append(cat)
        return cats

    def _refresh_main_model(self):
        mw = self.window()
        source_model = getattr(mw, "_source_model", None)
        if source_model is None or source_model.rowCount() == 0:
            return
        top_left = source_model.index(0, COL_BL)
        bottom_right = source_model.index(max(0, source_model.rowCount() - 1), COL_PIN)
        source_model.dataChanged.emit(
            top_left,
            bottom_right,
            [Qt.DisplayRole, Qt.CheckStateRole, Qt.ToolTipRole],
        )
        source_model.blacklistChanged.emit()

    def _apply_room_action(self, action: str):
        cats = self._room_cats_from_data(self._current_room_data())
        mw = self.window()
        status_bar = mw.statusBar() if hasattr(mw, "statusBar") else None
        if not cats:
            if status_bar is not None:
                status_bar.showMessage("Select a room first, then click a room action.")
            return

        changed = 0
        for cat in cats:
            if action == "must_breed":
                cat.must_breed = not cat.must_breed
                if cat.must_breed:
                    cat.is_blacklisted = False
            elif action == "breeding_block":
                cat.is_blacklisted = not cat.is_blacklisted
                if cat.is_blacklisted:
                    cat.must_breed = False
            elif action == "pin":
                cat.is_pinned = not cat.is_pinned
            changed += 1

        self._refresh_main_model()
        self._refresh_room_action_buttons()

        if action == "must_breed":
            if status_bar is not None:
                status_bar.showMessage(_tr("bulk.status.toggled_must_breed", default="Toggled must breed for {count} selected cats", count=changed))
        elif action == "breeding_block":
            if status_bar is not None:
                status_bar.showMessage(_tr("bulk.status.toggled_breeding_block", default="Toggled breeding block for {count} selected cats", count=changed))
        else:
            if status_bar is not None:
                status_bar.showMessage(_tr("bulk.status.toggled_pin", default="Toggled pin for {count} selected cats", count=changed))

    def _refresh_room_action_buttons(self):
        cats = self._room_cats_from_data(self._current_room_data())
        enabled = bool(cats)
        for btn in (self._must_breed_action_btn, self._breeding_block_action_btn, self._pin_action_btn):
            btn.setEnabled(enabled)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(
            "QWidget { background:#0a0a18; }"
            "QLabel { color:#bbb; }"
            "QTableWidget { background:#101023; color:#ddd; border:1px solid #26264a; }"
            "QHeaderView::section { background:#151532; color:#7d8bb0; border:none; padding:4px; font-weight:bold; }"
            "QPushButton { background:#1a1a32; color:#aaa; border:1px solid #2a2a4a; "
            "border-radius:4px; padding:6px 12px; font-size:11px; }"
            "QPushButton:hover { background:#252545; color:#ddd; }"
        )
        self._cats: list[Cat] = []
        self._cache: Optional[BreedingCache] = None
        self._optimizer_worker: Optional[RoomOptimizerWorker] = None
        self._auto_recalculate = _saved_room_optimizer_auto_recalc()
        self._planner_view: Optional['MutationDisorderPlannerView'] = None
        self._planner_traits: list[dict] = []
        self._planner_mode_profiles: dict[str, dict] = _normalize_mutation_mode_profiles({})
        self._planner_selected_mode = "best_pairs"
        self._available_rooms: list[str] = list(ROOM_DISPLAY.keys())
        self._room_summaries: dict[str, FurnitureRoomSummary] = {}
        self._save_path: Optional[str] = None
        self._session_state: dict = _load_planner_state_value("room_optimizer_state", {})
        self._restoring_session_state = False
        self._pending_initial_restore_run = False
        self._pending_cache_recalc = False
        self._pending_cache_recalc_sa = False
        self._selected_room_data: Optional[dict] = None

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        # Header
        header = QHBoxLayout()
        self._title = QLabel()
        self._title.setStyleSheet("color:#ddd; font-size:18px; font-weight:bold;")
        self._summary = QLabel("")
        self._summary.setStyleSheet("color:#666; font-size:11px;")
        self._summary.setWordWrap(True)
        self._summary.setMaximumHeight(50)
        self._summary.setAlignment(Qt.AlignRight | Qt.AlignTop)
        header.addWidget(self._title)
        header.addWidget(self._summary, 1)  # stretch=1 to fill space
        root.addLayout(header)

        self._top_actions = QWidget()
        self._top_actions.setStyleSheet("background:transparent;")
        self._top_actions_layout = QHBoxLayout(self._top_actions)
        self._top_actions_layout.setContentsMargins(0, 0, 0, 0)
        self._top_actions_layout.setSpacing(8)
        self._top_actions_layout.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        root.addWidget(self._top_actions)

        # Room priority panel
        self._room_priority_panel = RoomPriorityPanel()
        self._room_priority_panel.setStyleSheet("background:transparent;")
        self._room_priority_panel.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._configure_rooms_tab = QWidget()
        self._configure_rooms_tab.setStyleSheet("background:#0a0a18;")
        configure_rooms_layout = QVBoxLayout(self._configure_rooms_tab)
        configure_rooms_layout.setContentsMargins(0, 0, 0, 0)
        configure_rooms_layout.setSpacing(8)
        configure_rooms_layout.addWidget(self._room_priority_panel, 1)
        self._stat_priorities_tab = RoomOptimizerStatPrioritiesPanel(self._on_optimizer_stat_priority_changed)
        self._setup_tab = QWidget()
        self._setup_tab.setStyleSheet("background:#0a0a18;")
        self._setup_tab_layout = QVBoxLayout(self._setup_tab)
        self._setup_tab_layout.setContentsMargins(0, 0, 0, 0)
        self._setup_tab_layout.setSpacing(8)

        self._setup_splitter = QSplitter(Qt.Horizontal)
        self._setup_splitter.setObjectName("room_optimizer_setup_splitter")
        self._setup_splitter.setChildrenCollapsible(False)
        self._setup_splitter.setStyleSheet("QSplitter::handle:horizontal { background:#1e1e38; }")
        self._setup_tab_layout.addWidget(self._setup_splitter, 1)

        controls_wrap = QScrollArea()
        controls_wrap.setWidgetResizable(True)
        controls_wrap.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        controls_wrap.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        controls_wrap.setFrameShape(QFrame.NoFrame)
        controls_wrap.setStyleSheet("QScrollArea { border:none; background:transparent; }")

        controls_box = QWidget()
        self._setup_controls_layout = QVBoxLayout(controls_box)
        self._setup_controls_layout.setSpacing(8)
        self._setup_controls_layout.setContentsMargins(0, 0, 0, 0)
        controls_wrap.setWidget(controls_box)

        self._import_planner_btn = QPushButton()
        self._import_planner_btn.setToolTip("")
        self._style_import_planner_button(self._import_planner_btn, active=False)
        self._import_planner_btn.clicked.connect(self._import_from_planner)

        self._setup_stats_row = QWidget()
        self._setup_stats_row.setStyleSheet("background:transparent;")
        self._setup_stats_row_layout = QHBoxLayout(self._setup_stats_row)
        self._setup_stats_row_layout.setContentsMargins(0, 0, 0, 0)
        self._setup_stats_row_layout.setSpacing(10)

        self._min_stats_box = QWidget()
        self._min_stats_box.setStyleSheet("background:transparent;")
        self._min_stats_box_layout = QHBoxLayout(self._min_stats_box)
        self._min_stats_box_layout.setContentsMargins(0, 0, 0, 0)
        self._min_stats_box_layout.setSpacing(6)
        self._min_stats_label = QLabel()
        self._min_stats_label.setStyleSheet("color:#888; font-size:11px;")
        self._min_stats_box_layout.addWidget(self._min_stats_label)
        self._min_stats_input = QLineEdit()
        self._min_stats_input.setPlaceholderText("")
        self._min_stats_input.setFixedWidth(60)
        self._min_stats_input.setStyleSheet(
            "QLineEdit { background:#0d0d1c; color:#ccc; border:1px solid #2a2a4a;"
            " border-radius:4px; padding:4px 8px; }"
        )
        self._min_stats_input.textChanged.connect(lambda _: self._save_session_state())
        self._min_stats_box_layout.addWidget(self._min_stats_input)
        self._setup_stats_row_layout.addWidget(self._min_stats_box)

        self._max_risk_box = QWidget()
        self._max_risk_box.setStyleSheet("background:transparent;")
        self._max_risk_box_layout = QHBoxLayout(self._max_risk_box)
        self._max_risk_box_layout.setContentsMargins(0, 0, 0, 0)
        self._max_risk_box_layout.setSpacing(6)
        self._max_risk_label = QLabel()
        self._max_risk_label.setStyleSheet("color:#888; font-size:11px;")
        self._max_risk_box_layout.addWidget(self._max_risk_label)
        self._max_risk_input = QLineEdit()
        self._max_risk_input.setPlaceholderText("")
        self._max_risk_input.setFixedWidth(60)
        self._max_risk_input.setStyleSheet(
            "QLineEdit { background:#0d0d1c; color:#ccc; border:1px solid #2a2a4a;"
            " border-radius:4px; padding:4px 8px; }"
        )
        self._max_risk_input.textChanged.connect(lambda _: self._save_session_state())
        self._max_risk_box_layout.addWidget(self._max_risk_input)
        self._setup_stats_row_layout.addWidget(self._max_risk_box)
        self._setup_controls_layout.addWidget(self._setup_stats_row)

        self._shared_search_note = QLabel(_tr(
            "menu.settings.optimizer_search_settings.summary",
            default="Shared annealing settings live in Settings and apply to both planners.",
        ))
        self._shared_search_note.setStyleSheet("color:#8d8da8; font-size:11px;")
        self._shared_search_note.setWordWrap(True)
        self._setup_controls_layout.addWidget(self._shared_search_note)

        self._mode_toggle_btn = QPushButton()
        self._mode_toggle_btn.setCheckable(True)
        self._mode_toggle_btn.setChecked(False)
        self._mode_toggle_btn.setToolTip("")
        self._mode_toggle_btn.setStyleSheet(
            "QPushButton { background:#1a1a32; color:#aaa; border:1px solid #2a2a4a; "
            "border-radius:4px; padding:6px 12px; font-size:11px; }"
            "QPushButton:checked { background:#3a2f54; color:#ddd; border:1px solid #6a5a9a; }"
            "QPushButton:hover { background:#252545; color:#ddd; }"
        )
        self._mode_toggle_btn.toggled.connect(self._on_optimizer_mode_toggled)
        self._mode_toggle_btn.toggled.connect(lambda _: self._save_session_state())
        self._setup_controls_layout.addWidget(self._mode_toggle_btn)

        self._minimize_variance_checkbox = QPushButton()
        self._minimize_variance_checkbox.setCheckable(True)
        self._minimize_variance_checkbox.setChecked(_saved_optimizer_flag("minimize_variance", True))
        self._minimize_variance_checkbox.setStyleSheet(
            "QPushButton { background:#1a1a32; color:#aaa; border:1px solid #2a2a4a; "
            "border-radius:4px; padding:6px 12px; font-size:11px; }"
            "QPushButton:checked { background:#2a4a5a; color:#ddd; border:1px solid #4a6a7a; }"
            "QPushButton:hover { background:#252545; color:#ddd; }"
        )
        self._bind_persistent_toggle(self._minimize_variance_checkbox, "room_optimizer.toggle.minimize_variance", "minimize_variance")
        self._minimize_variance_checkbox.toggled.connect(lambda _: self._save_session_state())
        self._setup_controls_layout.addWidget(self._minimize_variance_checkbox)

        self._avoid_lovers_checkbox = QPushButton()
        self._avoid_lovers_checkbox.setCheckable(True)
        self._avoid_lovers_checkbox.setChecked(_saved_optimizer_flag("avoid_lovers", True))
        self._avoid_lovers_checkbox.setToolTip(_tr("room_optimizer.tooltip.avoid_lovers"))
        self._avoid_lovers_checkbox.setStyleSheet(
            "QPushButton { background:#1a1a32; color:#aaa; border:1px solid #2a2a4a; "
            "border-radius:4px; padding:6px 12px; font-size:11px; }"
            "QPushButton:checked { background:#5a3a2a; color:#ddd; border:1px solid #8a5a4a; }"
            "QPushButton:hover { background:#252545; color:#ddd; }"
        )
        self._bind_persistent_toggle(self._avoid_lovers_checkbox, "room_optimizer.toggle.avoid_lovers", "avoid_lovers")
        self._avoid_lovers_checkbox.toggled.connect(lambda _: self._save_session_state())
        self._setup_controls_layout.addWidget(self._avoid_lovers_checkbox)

        self._prefer_low_aggression_checkbox = QPushButton()
        self._prefer_low_aggression_checkbox.setCheckable(True)
        self._prefer_low_aggression_checkbox.setChecked(_saved_optimizer_flag("prefer_low_aggression", True))
        self._prefer_low_aggression_checkbox.setToolTip(_tr("room_optimizer.tooltip.prefer_low_aggression"))
        self._prefer_low_aggression_checkbox.setStyleSheet(
            "QPushButton { background:#1a1a32; color:#aaa; border:1px solid #2a2a4a; "
            "border-radius:4px; padding:6px 12px; font-size:11px; }"
            "QPushButton:checked { background:#4a2a2a; color:#ddd; border:1px solid #7a4a4a; }"
            "QPushButton:hover { background:#252545; color:#ddd; }"
        )
        self._bind_persistent_toggle(self._prefer_low_aggression_checkbox, "room_optimizer.toggle.prefer_low_aggression", "prefer_low_aggression")
        self._prefer_low_aggression_checkbox.toggled.connect(lambda _: self._save_session_state())
        self._setup_controls_layout.addWidget(self._prefer_low_aggression_checkbox)

        self._prefer_high_libido_checkbox = QPushButton()
        self._prefer_high_libido_checkbox.setCheckable(True)
        self._prefer_high_libido_checkbox.setChecked(_saved_optimizer_flag("prefer_high_libido", True))
        self._prefer_high_libido_checkbox.setToolTip(_tr("room_optimizer.tooltip.prefer_high_libido"))
        self._prefer_high_libido_checkbox.setStyleSheet(
            "QPushButton { background:#1a1a32; color:#aaa; border:1px solid #2a2a4a; "
            "border-radius:4px; padding:6px 12px; font-size:11px; }"
            "QPushButton:checked { background:#2a4a36; color:#ddd; border:1px solid #4a7a5a; }"
            "QPushButton:hover { background:#252545; color:#ddd; }"
        )
        self._bind_persistent_toggle(self._prefer_high_libido_checkbox, "room_optimizer.toggle.prefer_high_libido", "prefer_high_libido")
        self._prefer_high_libido_checkbox.toggled.connect(lambda _: self._save_session_state())
        self._setup_controls_layout.addWidget(self._prefer_high_libido_checkbox)

        self._maximize_throughput_checkbox = QPushButton()
        self._maximize_throughput_checkbox.setCheckable(True)
        self._maximize_throughput_checkbox.setChecked(_saved_optimizer_flag("maximize_throughput", False))
        self._maximize_throughput_checkbox.setToolTip(_tr("room_optimizer.tooltip.maximize_throughput"))
        self._maximize_throughput_checkbox.setStyleSheet(
            "QPushButton { background:#1a1a32; color:#aaa; border:1px solid #2a2a4a; "
            "border-radius:4px; padding:6px 12px; font-size:11px; }"
            "QPushButton:checked { background:#304a2a; color:#e6f6dd; border:1px solid #5b8750; }"
            "QPushButton:hover { background:#252545; color:#ddd; }"
        )
        self._bind_persistent_toggle(
            self._maximize_throughput_checkbox,
            "room_optimizer.toggle.maximize_throughput",
            "maximize_throughput",
        )
        self._maximize_throughput_checkbox.toggled.connect(lambda _: self._save_session_state())
        self._setup_controls_layout.addWidget(self._maximize_throughput_checkbox)

        # "Ignore Class Stat Priorities" — skips the per-mode stat priority
        # scoring bonus entirely. Useful when chasing all-7s, where no stat
        # is more valuable than any other.
        self._ignore_stat_priority_checkbox = QPushButton()
        self._ignore_stat_priority_checkbox.setCheckable(True)
        self._ignore_stat_priority_checkbox.setChecked(_saved_optimizer_flag("ignore_stat_priority", False))
        self._ignore_stat_priority_checkbox.setToolTip(
            _tr(
                "room_optimizer.tooltip.ignore_stat_priority",
                default="Disable class stat prioritization when scoring pairs. Use this when chasing all-7 stat lines.",
            )
        )
        self._ignore_stat_priority_checkbox.setStyleSheet(
            "QPushButton { background:#1a1a32; color:#aaa; border:1px solid #2a2a4a; "
            "border-radius:4px; padding:6px 12px; font-size:11px; }"
            "QPushButton:checked { background:#4a3a1f; color:#f6e2c1; border:1px solid #8a6f3a; }"
            "QPushButton:hover { background:#252545; color:#ddd; }"
        )
        self._bind_persistent_toggle(
            self._ignore_stat_priority_checkbox,
            "room_optimizer.toggle.ignore_stat_priority",
            "ignore_stat_priority",
        )
        self._ignore_stat_priority_checkbox.toggled.connect(lambda _: self._save_session_state())
        self._setup_controls_layout.addWidget(self._ignore_stat_priority_checkbox)

        # Kittens can't breed in-game (day 0 / day 1). When enabled, the
        # optimizer routes them to fallback rooms instead of wasting
        # breeding-room capacity on them.
        self._send_kittens_checkbox = QPushButton()
        self._send_kittens_checkbox.setCheckable(True)
        self._send_kittens_checkbox.setChecked(_saved_optimizer_flag("send_kittens_to_fallback", False))
        self._send_kittens_checkbox.setToolTip(
            _tr(
                "room_optimizer.tooltip.send_kittens_to_fallback",
                default="Route kittens (age 0-1) to fallback rooms since they can't breed yet. Eternal-youth cats are unaffected.",
            )
        )
        self._send_kittens_checkbox.setStyleSheet(
            "QPushButton { background:#1a1a32; color:#aaa; border:1px solid #2a2a4a; "
            "border-radius:4px; padding:6px 12px; font-size:11px; }"
            "QPushButton:checked { background:#4a3a1f; color:#f6e2c1; border:1px solid #8a6f3a; }"
            "QPushButton:hover { background:#252545; color:#ddd; }"
        )
        self._bind_persistent_toggle(
            self._send_kittens_checkbox,
            "room_optimizer.toggle.send_kittens_to_fallback",
            "send_kittens_to_fallback",
        )
        self._send_kittens_checkbox.toggled.connect(lambda _: self._save_session_state())
        self._setup_controls_layout.addWidget(self._send_kittens_checkbox)

        # Avoid placing cats carrying desired mutations into high-Evolution
        # rooms, and cats carrying desired disorders into high-Health rooms —
        # those room effects can strip the desired traits away.
        self._avoid_trait_loss_checkbox = QPushButton()
        self._avoid_trait_loss_checkbox.setCheckable(True)
        self._avoid_trait_loss_checkbox.setChecked(_saved_optimizer_flag("avoid_trait_loss", False))
        self._avoid_trait_loss_checkbox.setToolTip(
            _tr(
                "room_optimizer.tooltip.avoid_trait_loss",
                default="Avoid placing cats with desired mutations into high-Evolution rooms, and cats with desired disorders into high-Health rooms (those effects can strip the traits).",
            )
        )
        self._avoid_trait_loss_checkbox.setStyleSheet(
            "QPushButton { background:#1a1a32; color:#aaa; border:1px solid #2a2a4a; "
            "border-radius:4px; padding:6px 12px; font-size:11px; }"
            "QPushButton:checked { background:#4a3a1f; color:#f6e2c1; border:1px solid #8a6f3a; }"
            "QPushButton:hover { background:#252545; color:#ddd; }"
        )
        self._bind_persistent_toggle(
            self._avoid_trait_loss_checkbox,
            "room_optimizer.toggle.avoid_trait_loss",
            "avoid_trait_loss",
        )
        self._avoid_trait_loss_checkbox.toggled.connect(lambda _: self._save_session_state())
        self._setup_controls_layout.addWidget(self._avoid_trait_loss_checkbox)

        self._setup_controls_layout.addStretch(1)

        self._setup_info_panel = QWidget()
        self._setup_info_panel.setStyleSheet("background:transparent;")
        self._setup_info_panel_layout = QVBoxLayout(self._setup_info_panel)
        self._setup_info_panel_layout.setContentsMargins(0, 0, 0, 0)
        self._setup_info_panel_layout.setSpacing(8)

        self._setup_info_title = QLabel()
        self._setup_info_title.setStyleSheet("color:#ddd; font-size:14px; font-weight:bold;")
        self._setup_info_title.setWordWrap(True)
        self._setup_info_panel_layout.addWidget(self._setup_info_title)

        self._setup_info_subtitle = QLabel("")
        self._setup_info_subtitle.setStyleSheet("color:#8d8da8; font-size:11px;")
        self._setup_info_subtitle.setWordWrap(True)
        self._setup_info_panel_layout.addWidget(self._setup_info_subtitle)

        self._setup_info_browser = QTextBrowser()
        self._setup_info_browser.setOpenExternalLinks(False)
        self._setup_info_browser.setFocusPolicy(Qt.NoFocus)
        self._setup_info_browser.setFrameShape(QFrame.NoFrame)
        self._setup_info_browser.setStyleSheet(
            "QTextBrowser { background:#0d0d1c; color:#ddd; border:1px solid #26264a; "
            "border-radius:6px; padding:10px; }"
            "QTextBrowser h2 { color:#f0f0ff; margin-top: 4px; margin-bottom: 8px; }"
            "QTextBrowser h3 { color:#c9d6ff; margin-top: 10px; margin-bottom: 4px; }"
            "QTextBrowser ul { margin-left: 18px; }"
            "QTextBrowser li { margin-bottom: 6px; }"
            "QTextBrowser p { margin-top: 4px; margin-bottom: 8px; }"
            "QTextBrowser .muted { color:#8d8da8; }"
        )
        self._setup_info_panel_layout.addWidget(self._setup_info_browser, 1)

        self._optimize_btn = QPushButton()
        self._optimize_btn.clicked.connect(lambda: self._calculate_optimal_distribution(use_sa=self._deep_optimize_btn.isChecked()))
        self._optimize_btn.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self._optimize_btn.setStyleSheet(
            "QPushButton { background:#1f5f4a; color:#f2f7f3; border:1px solid #3f8f72; "
            "border-radius:4px; padding:6px 14px; font-size:11px; font-weight:bold; }"
            "QPushButton:hover { background:#26735a; }"
            "QPushButton:pressed { background:#184b3a; }"
        )

        self._deep_optimize_btn = QPushButton()
        self._deep_optimize_btn.setCheckable(True)
        self._deep_optimize_btn.setChecked(_saved_optimizer_flag("use_sa", False))
        self._deep_optimize_btn.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self._deep_optimize_btn.setStyleSheet(
            "QPushButton { background:#2a2a5a; color:#bbbbee; border:1px solid #4a4a8a; "
            "border-radius:4px; padding:6px 12px; font-size:11px; font-weight:bold; }"
            "QPushButton:hover { background:#3a3a6a; color:#ddd; }"
            "QPushButton:checked { background:#3a5a3a; color:#aaffaa; border:1px solid #4a8a4a; }"
            "QPushButton:disabled { background:#1a1a32; color:#555; border-color:#2a2a4a; }"
        )
        self._bind_persistent_toggle(self._deep_optimize_btn, "room_optimizer.toggle.use_sa", "use_sa")
        self._deep_optimize_btn.toggled.connect(lambda _: self._save_session_state())

        self._cancel_btn = QPushButton()
        self._cancel_btn.setText(_tr("room_optimizer.cancel_btn", default="Cancel"))
        self._cancel_btn.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self._cancel_btn.setStyleSheet(
            "QPushButton { background:#5a1f1f; color:#f7f2f2; border:1px solid #8f3f3f; "
            "border-radius:4px; padding:6px 14px; font-size:11px; font-weight:bold; }"
            "QPushButton:hover { background:#732626; }"
            "QPushButton:pressed { background:#4b1818; }"
        )
        self._cancel_btn.clicked.connect(self._cancel_optimization)
        self._cancel_btn.setVisible(False)

        self._import_planner_btn.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self._top_actions_layout.addWidget(self._optimize_btn)
        self._top_actions_layout.addWidget(self._cancel_btn)
        self._top_actions_layout.addWidget(self._deep_optimize_btn)
        self._top_actions_layout.addWidget(self._import_planner_btn)
        self._top_actions_layout.addStretch(1)
        self._setup_splitter.addWidget(controls_wrap)
        self._setup_splitter.addWidget(self._setup_info_panel)
        self._setup_splitter.setStretchFactor(0, 3)
        self._setup_splitter.setStretchFactor(1, 2)
        self._setup_splitter.setSizes([540, 360])

        room_actions_box = QWidget()
        room_actions = QHBoxLayout(room_actions_box)
        room_actions.setContentsMargins(0, 0, 0, 0)
        room_actions.setSpacing(8)

        self._must_breed_action_btn = QPushButton()
        RoomOptimizerView._style_room_action_button(
            self._must_breed_action_btn,
            "#3b355f",
            "#5d58a0",
            "#49417a",
        )
        self._must_breed_action_btn.clicked.connect(lambda: self._apply_room_action("must_breed"))
        room_actions.addWidget(self._must_breed_action_btn)

        self._breeding_block_action_btn = QPushButton()
        RoomOptimizerView._style_room_action_button(
            self._breeding_block_action_btn,
            "#5a2d22",
            "#8b4c3e",
            "#6c382a",
        )
        self._breeding_block_action_btn.clicked.connect(lambda: self._apply_room_action("breeding_block"))
        room_actions.addWidget(self._breeding_block_action_btn)

        self._pin_action_btn = QPushButton()
        RoomOptimizerView._style_room_action_button(
            self._pin_action_btn,
            "#2a3a2a",
            "#4a6a4a",
            "#3a4a3a",
        )
        self._pin_action_btn.setMinimumWidth(90)
        self._pin_action_btn.clicked.connect(lambda: self._apply_room_action("pin"))
        room_actions.addWidget(self._pin_action_btn)

        room_actions.addStretch()
        root.addWidget(room_actions_box)
        room_actions_box.hide()

        # Splitter to hold table and details pane
        self._splitter = QSplitter(Qt.Vertical)
        self._splitter.setObjectName("room_optimizer_splitter")
        self._splitter.setStyleSheet("QSplitter::handle:vertical { background:#1e1e38; }")

        # Results table
        self._table = QTableWidget(0, 7)
        self._table.setIconSize(QSize(60, 20))
        self._table.setHorizontalHeaderLabels([
            _tr("room_optimizer.table.room"),
            _tr("room_optimizer.table.type", default="Type"),
            _tr("room_optimizer.table.cats"),
            _tr("room_optimizer.table.expected_pairs"),
            _tr("room_optimizer.table.avg_stats"),
            _tr("room_optimizer.table.risk"),
            _tr("room_optimizer.table.details"),
        ])
        self._set_room_action_button_texts()
        if hasattr(self, "_details_pane") and self._details_pane is not None:
            self._details_pane.retranslate_ui()
        if hasattr(self, "_cat_locator") and self._cat_locator is not None:
            self._cat_locator.retranslate_ui()
        if hasattr(self, "_stat_priorities_tab") and self._stat_priorities_tab is not None:
            self._stat_priorities_tab.retranslate_ui()
        self._table.verticalHeader().setVisible(False)
        self._table.verticalHeader().setDefaultSectionSize(28)
        self._table.verticalHeader().setMinimumSectionSize(24)
        self._table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.setSortingEnabled(False)

        hh = self._table.horizontalHeader()
        hh.setStretchLastSection(True)
        hh.setSectionResizeMode(0, QHeaderView.Interactive)
        hh.setSectionResizeMode(1, QHeaderView.Interactive)
        hh.setSectionResizeMode(2, QHeaderView.Interactive)
        hh.setSectionResizeMode(3, QHeaderView.Interactive)
        hh.setSectionResizeMode(4, QHeaderView.Interactive)
        hh.setSectionResizeMode(5, QHeaderView.Interactive)
        hh.setSectionResizeMode(6, QHeaderView.Stretch)
        self._table.setColumnWidth(0, 140)
        self._table.setColumnWidth(1, 90)
        self._table.setColumnWidth(2, 290)
        self._table.setColumnWidth(3, 96)
        self._table.setColumnWidth(4, 88)
        self._table.setColumnWidth(5, 72)
        self._table.setStyleSheet(
            self._table.styleSheet()
            + "QTableWidget::item { padding:4px 8px; }"
            + "QHeaderView::section { padding:5px 8px; }"
        )
        self._table.itemSelectionChanged.connect(self._on_table_selection_changed)

        self._splitter.addWidget(self._table)

        # Bottom tabs: Configure Rooms, Setup, Breeding Pairs, Cat Locator
        self._bottom_tabs = QTabWidget()
        self._bottom_tabs.setStyleSheet(
            "QTabWidget::pane { border:1px solid #1e1e38; background:#0a0a18; }"
            "QTabBar::tab { background:#14142a; color:#888; padding:6px 14px; border:1px solid #1e1e38;"
            " border-bottom:none; margin-right:2px; font-size:11px; }"
            "QTabBar::tab:selected { background:#1a1a36; color:#ddd; font-weight:bold; }"
            "QTabBar::tab:hover { background:#1e1e3a; color:#bbb; }"
        )

        # Tab 0: Configure Rooms
        self._bottom_tabs.addTab(self._configure_rooms_tab, _tr("room_optimizer.tab.configure_rooms"))

        # Tab 1: Class Stats
        self._bottom_tabs.addTab(self._stat_priorities_tab, _tr("room_optimizer.tab.stat_priorities", default="Class Stats"))

        # Tab 2: Setup
        self._bottom_tabs.addTab(self._setup_tab, _tr("room_optimizer.tab.setup"))

        # Tab 3: Breeding Pairs (existing detail panel)
        self._details_pane = RoomOptimizerDetailPanel()
        self._details_pane.set_mode_profiles(self._planner_mode_profiles)
        self._details_pane.set_navigate_to_cat_callback(self._navigate_to_cat_from_breeding_pairs)
        self._bottom_tabs.addTab(self._details_pane, _tr("room_optimizer.tab.breeding_pairs"))

        # Tab 4: Cat Locator
        self._cat_locator = RoomOptimizerCatLocator()
        self._bottom_tabs.addTab(self._cat_locator, _tr("room_optimizer.tab.cat_locator"))
        self._bottom_tabs.setCurrentIndex(3)
        self._bottom_tabs.currentChanged.connect(lambda _: self._save_session_state())

        self._splitter.addWidget(self._bottom_tabs)
        self._splitter.setSizes([180, 420])

        root.addWidget(self._splitter, 1)

        _enforce_min_font_in_widget_tree(self)
        self.retranslate_ui()
        self._restore_session_state()
        self._pending_initial_restore_run = bool(self._session_state.get("has_run", False))
        self._refresh_room_action_buttons()

    def _on_optimizer_mode_toggled(self, enabled: bool):
        self._set_mode_button_text(enabled)
        self._minimize_variance_checkbox.setChecked(False if enabled else _saved_optimizer_flag("minimize_variance", True))
        self._minimize_variance_checkbox.setEnabled(not enabled)
        self._minimize_variance_checkbox.setToolTip("" if not enabled else _tr("room_optimizer.tooltip.variance"))
        if hasattr(self, "_deep_optimize_btn"):
            self._deep_optimize_btn.setEnabled(True)
            self._deep_optimize_btn.setToolTip(
                _tr("room_optimizer.more_depth_tooltip", default="Use simulated annealing for a slower, deeper search.")
            )
        if hasattr(self, "_maximize_throughput_checkbox"):
            self._maximize_throughput_checkbox.setEnabled(not enabled)
        self._save_session_state()

    def _on_table_selection_changed(self):
        selected_ranges = self._table.selectedRanges()
        if not selected_ranges:
            self._selected_room_data = None
            self._details_pane.show_room(None)
            self._refresh_room_action_buttons()
            return

        row = selected_ranges[0].topRow()
        room_item = self._table.item(row, 0)
        if room_item:
            details_data = room_item.data(Qt.UserRole)
            self._selected_room_data = details_data if isinstance(details_data, dict) else None
            self._details_pane.show_room(self._selected_room_data)
            self._refresh_room_action_buttons()
        else:
            self._selected_room_data = None
            self._details_pane.show_room(None)
            self._refresh_room_action_buttons()

    def set_cats(self, cats: list[Cat], excluded_keys: set[int] = None):
        self._cats = cats
        self._cat_lookup = {cat.db_key: cat for cat in cats}
        # Combine explicit excluded_keys with blacklisted cats
        blacklisted_keys = {c.db_key for c in cats if c.is_blacklisted}
        self._excluded_keys = (excluded_keys or set()) | blacklisted_keys
        alive_count = len([c for c in cats if c.status != 'Gone'])
        excluded_count = len([c for c in cats if c.status != 'Gone' and c.db_key in self._excluded_keys])
        if excluded_count > 0:
            self._summary.setText(_tr("room_optimizer.summary.with_excluded",
                                       alive=alive_count, excluded=excluded_count))
        else:
            self._summary.setText(_tr("room_optimizer.summary.no_excluded",
                                       alive=alive_count))
        self._restore_session_state()
        self._on_planner_traits_changed()
        alive_count = len([c for c in self._cats if c.status != "Gone"])
        if self._session_state.get("has_run") and alive_count >= 2:
            cached = self._load_cached_results()
            if cached is not None:
                self._on_optimizer_result(cached)
                self._pending_initial_restore_run = False
                return
        if self._pending_initial_restore_run and alive_count >= 2:
            self._pending_initial_restore_run = False
            use_sa = bool(self._session_state.get("use_sa", False))
            if self._cache is None:
                self._pending_cache_recalc = True
                self._pending_cache_recalc_sa = use_sa
            else:
                self._calculate_optimal_distribution(use_sa=use_sa)
        elif self._auto_recalculate and self._session_state.get("has_run") and alive_count >= 2:
            use_sa = bool(self._session_state.get("use_sa", False))
            if self._cache is None:
                self._pending_cache_recalc = True
                self._pending_cache_recalc_sa = use_sa
            else:
                self._calculate_optimal_distribution(use_sa=use_sa)

    def set_available_rooms(self, rooms: list[str]):
        ordered = [room for room in ROOM_DISPLAY.keys() if room in set(rooms)]
        self._available_rooms = ordered or list(ROOM_DISPLAY.keys())
        if hasattr(self, "_room_priority_panel") and self._room_priority_panel is not None:
            self._room_priority_panel.set_available_rooms(self._available_rooms)

    def get_available_rooms(self) -> list[str]:
        return list(self._available_rooms)

    def set_navigate_to_pair_callback(self, callback):
        self._details_pane.set_navigate_to_pair_callback(callback)

    def set_room_summaries(self, summaries: list[FurnitureRoomSummary] | dict[str, FurnitureRoomSummary]):
        if isinstance(summaries, dict):
            self._room_summaries = {
                room: summary
                for room, summary in summaries.items()
                if room and isinstance(summary, FurnitureRoomSummary)
            }
        else:
            self._room_summaries = {
                summary.room: summary
                for summary in summaries
                if isinstance(summary, FurnitureRoomSummary) and summary.room
            }
        self._room_priority_panel.set_room_summaries(self._room_summaries)

    @property
    def room_priority_panel(self):
        return self._room_priority_panel

    @property
    def cat_locator(self):
        return self._cat_locator

    @property
    def save_path(self) -> Optional[str]:
        return self._save_path

    def save_session_state(self, **kwargs):
        self._save_session_state(**kwargs)

    def on_planner_traits_changed(self):
        self._on_planner_traits_changed()

    def get_room_config(self) -> list[dict]:
        return self._room_priority_panel.get_config()

    def _navigate_to_cat_from_breeding_pairs(self, cat_name_formatted: str):
        """Navigate to a cat by its formatted name (e.g. 'Fluffy (Female)')."""
        # Extract the cat name part (before the gender)
        cat_name = cat_name_formatted.split(" (")[0] if " (" in cat_name_formatted else cat_name_formatted

        # Find the cat by name
        for cat in self._cats:
            if cat.name == cat_name:
                # Call the cat locator's callback if available
                if self._cat_locator._navigate_to_cat_callback:
                    self._cat_locator._navigate_to_cat_callback(cat.db_key)
                return

    def set_cache(self, cache: Optional['BreedingCache']):
        self._cache = cache
        if cache is not None and self._pending_cache_recalc:
            self._pending_cache_recalc = False
            use_sa = self._pending_cache_recalc_sa
            self._pending_cache_recalc_sa = False
            self._calculate_optimal_distribution(use_sa=use_sa)

    def set_auto_recalculate(self, enabled: bool):
        self._auto_recalculate = bool(enabled)

    def set_save_path(self, save_path: Optional[str], *, refresh_existing: bool = True):
        self._save_path = save_path
        self._room_priority_panel.set_save_path(save_path)
        self._restore_session_state()
        self._pending_initial_restore_run = bool(self._session_state.get("has_run", False))
        self._pending_cache_recalc = False
        if refresh_existing and self._cats:
            self.set_cats(self._cats, self._excluded_keys)
            return
        self._on_planner_traits_changed()

    def set_planner_view(self, planner: 'MutationDisorderPlannerView'):
        if self._planner_view is not None and hasattr(self._planner_view, "traitsChanged"):
            try:
                self._planner_view.traitsChanged.disconnect(self._on_planner_traits_changed)
            except (TypeError, RuntimeError):
                pass
        self._planner_view = planner
        if self._planner_view is not None and hasattr(self._planner_view, "traitsChanged"):
            try:
                self._planner_view.traitsChanged.connect(self._on_planner_traits_changed)
            except (TypeError, RuntimeError):
                pass
        self._on_planner_traits_changed()

    def _persist_mode_profiles_fallback(self):
        if not self._save_path:
            return
        state = _load_planner_state_value("mutation_planner_state", {}, self._save_path)
        if not isinstance(state, dict):
            state = {}
        state["selected_mode"] = self._planner_selected_mode
        state["mode_profiles"] = self._planner_mode_profiles
        state["selected_traits"] = list(
            (self._planner_mode_profiles.get(self._planner_selected_mode, {}) or {}).get("traits", []) or []
        )
        _save_planner_state_value("mutation_planner_state", state, self._save_path)

    def _on_optimizer_stat_priority_changed(self, mode: str, order: list[str]):
        mode_key = str(mode or "").strip().lower()
        if mode_key not in MUTATION_CLASS_MODES:
            return
        self._planner_mode_profiles = _normalize_mutation_mode_profiles(self._planner_mode_profiles)
        self._planner_mode_profiles.setdefault(mode_key, {})["stat_priority"] = list(order)
        if self._planner_view is not None and hasattr(self._planner_view, "set_stat_priority_for_mode"):
            self._planner_view.set_stat_priority_for_mode(mode_key, order, save=True, notify=True)
        else:
            self._persist_mode_profiles_fallback()
            if self.isVisible() and self._session_state.get("has_run") and len([c for c in self._cats if c.status != "Gone"]) >= 2:
                self._calculate_optimal_distribution(use_sa=bool(self._session_state.get("use_sa", False)))

    def _on_planner_traits_changed(self):
        self._planner_traits = self._planner_view.get_selected_traits() if self._planner_view is not None else []
        mode_profiles = {}
        if self._planner_view is not None and hasattr(self._planner_view, "get_mode_profiles"):
            mode_profiles = self._planner_view.get_mode_profiles()
        selected_mode = "best_pairs"
        if self._planner_view is not None and hasattr(self._planner_view, "get_selected_mode"):
            try:
                selected_mode = str(self._planner_view.get_selected_mode() or "best_pairs").strip().lower()
            except Exception:
                selected_mode = "best_pairs"
        self._planner_selected_mode = selected_mode if selected_mode in MUTATION_CLASS_MODES else "best_pairs"
        self._planner_mode_profiles = _normalize_mutation_mode_profiles(mode_profiles, legacy_traits=self._planner_traits)
        if hasattr(self, "_details_pane") and self._details_pane is not None:
            self._details_pane.set_mode_profiles(self._planner_mode_profiles)
        if hasattr(self, "_stat_priorities_tab") and self._stat_priorities_tab is not None:
            self._stat_priorities_tab.refresh_profiles(self._planner_mode_profiles, selected_mode=self._planner_selected_mode)
        if self._planner_mode_profiles_total_traits(self._planner_mode_profiles) <= 0:
            self._import_planner_btn.setText(_tr("room_optimizer.import_none", default="No Mutations Imported"))
            self._import_planner_btn.setToolTip(self._import_planner_button_tooltip())
            self._style_import_planner_button(self._import_planner_btn, active=False)
            return
        self._import_from_planner()

    def _import_planner_button_tooltip(self) -> str:
        return self._planner_mode_profiles_tooltip(
            self._planner_mode_profiles,
            self._planner_selected_mode,
            empty_text=_tr("room_optimizer.import_none_tooltip"),
        )

    def _session_state_payload(self, *, has_run: Optional[bool] = None, use_sa: Optional[bool] = None) -> dict:
        state = dict(self._session_state) if isinstance(self._session_state, dict) else {}
        state.update({
            "min_stats": self._min_stats_input.text().strip(),
            "max_risk": self._max_risk_input.text().strip(),
            "mode_family": bool(self._mode_toggle_btn.isChecked()),
            "minimize_variance": bool(self._minimize_variance_checkbox.isChecked()),
            "avoid_lovers": bool(self._avoid_lovers_checkbox.isChecked()),
            "prefer_low_aggression": bool(self._prefer_low_aggression_checkbox.isChecked()),
            "prefer_high_libido": bool(self._prefer_high_libido_checkbox.isChecked()),
            "maximize_throughput": bool(self._maximize_throughput_checkbox.isChecked()),
            "ignore_stat_priority": bool(self._ignore_stat_priority_checkbox.isChecked()),
            "send_kittens_to_fallback": bool(self._send_kittens_checkbox.isChecked()) if hasattr(self, "_send_kittens_checkbox") else False,
            "avoid_trait_loss": bool(self._avoid_trait_loss_checkbox.isChecked()) if hasattr(self, "_avoid_trait_loss_checkbox") else False,
            "bottom_tab_index": int(self._bottom_tabs.currentIndex()) if hasattr(self, "_bottom_tabs") else 2,
        })
        if use_sa is not None:
            state["use_sa"] = bool(use_sa)
        else:
            state["use_sa"] = bool(state.get("use_sa", False))
        if has_run is not None:
            state["has_run"] = bool(has_run)
        else:
            state["has_run"] = bool(state.get("has_run", False))
        return state

    def _save_session_state(self, *, has_run: Optional[bool] = None, use_sa: Optional[bool] = None):
        if getattr(self, "_restoring_session_state", False):
            return
        self._session_state = self._session_state_payload(has_run=has_run, use_sa=use_sa)
        _save_planner_state_value("room_optimizer_state", self._session_state, self._save_path)

    # ── Result caching ──────────────────────────────────────────────────

    @staticmethod
    def _cats_fingerprint(cats: list[Cat]) -> str:
        keys = sorted(c.db_key for c in cats if c.status != "Gone")
        raw = json.dumps(keys, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    def _save_cached_results(self, result: dict):
        if not self._save_path:
            return
        payload = {
            "fingerprint": self._cats_fingerprint(self._cats),
            "result": result,
        }
        _save_planner_state_value("room_optimizer_results", payload, self._save_path)

    def _load_cached_results(self) -> Optional[dict]:
        if not self._save_path or not self._cats:
            return None
        payload = _load_planner_state_value("room_optimizer_results", None, self._save_path)
        if not isinstance(payload, dict):
            return None
        if payload.get("fingerprint") != self._cats_fingerprint(self._cats):
            return None
        result = payload.get("result")
        return result if isinstance(result, dict) and "room_rows" in result else None

    def _clear_cached_results(self):
        if self._save_path:
            _save_planner_state_value("room_optimizer_results", None, self._save_path)

    def _restore_session_state(self):
        state = _load_planner_state_value("room_optimizer_state", {}, self._save_path)
        if not isinstance(state, dict):
            state = {}
        self._session_state = state
        self._restoring_session_state = True
        try:
            self._min_stats_input.setText(str(state.get("min_stats", "") or ""))
            self._max_risk_input.setText(str(state.get("max_risk", "") or ""))
            mode_family = bool(state.get("mode_family", False))
            self._mode_toggle_btn.setChecked(mode_family)
            if not mode_family:
                self._minimize_variance_checkbox.setChecked(bool(state.get("minimize_variance", self._minimize_variance_checkbox.isChecked())))
            else:
                self._minimize_variance_checkbox.setChecked(False)
            self._avoid_lovers_checkbox.setChecked(bool(state.get("avoid_lovers", self._avoid_lovers_checkbox.isChecked())))
            self._prefer_low_aggression_checkbox.setChecked(bool(state.get("prefer_low_aggression", self._prefer_low_aggression_checkbox.isChecked())))
            self._prefer_high_libido_checkbox.setChecked(bool(state.get("prefer_high_libido", self._prefer_high_libido_checkbox.isChecked())))
            self._maximize_throughput_checkbox.setChecked(bool(state.get("maximize_throughput", self._maximize_throughput_checkbox.isChecked())))
            self._ignore_stat_priority_checkbox.setChecked(bool(state.get("ignore_stat_priority", self._ignore_stat_priority_checkbox.isChecked())))
            if hasattr(self, "_send_kittens_checkbox"):
                self._send_kittens_checkbox.setChecked(bool(state.get("send_kittens_to_fallback", self._send_kittens_checkbox.isChecked())))
            if hasattr(self, "_avoid_trait_loss_checkbox"):
                self._avoid_trait_loss_checkbox.setChecked(bool(state.get("avoid_trait_loss", self._avoid_trait_loss_checkbox.isChecked())))
            self._deep_optimize_btn.setChecked(bool(state.get("use_sa", False)))
            if hasattr(self, "_bottom_tabs"):
                tab_index = state.get("bottom_tab_index", self._bottom_tabs.currentIndex())
                try:
                    self._bottom_tabs.setCurrentIndex(max(0, min(self._bottom_tabs.count() - 1, int(tab_index))))
                except (TypeError, ValueError):
                    self._bottom_tabs.setCurrentIndex(3)
        finally:
            self._restoring_session_state = False
        if self._save_path is not None:
            # Make the restored state durable immediately once we're bound to a save.
            self._save_session_state()
            _save_room_priority_config(self._room_priority_panel.get_config(), self._save_path)
        if self._planner_view is not None:
            self._planner_traits = self._planner_view.get_selected_traits()
            if hasattr(self._planner_view, "get_mode_profiles"):
                self._planner_mode_profiles = _normalize_mutation_mode_profiles(
                    self._planner_view.get_mode_profiles(),
                    legacy_traits=self._planner_traits,
                )
                if hasattr(self, "_details_pane") and self._details_pane is not None:
                    self._details_pane.set_mode_profiles(self._planner_mode_profiles)
            if hasattr(self._planner_view, "get_selected_mode"):
                try:
                    mode = str(self._planner_view.get_selected_mode() or "best_pairs").strip().lower()
                    self._planner_selected_mode = mode if mode in MUTATION_CLASS_MODES else "best_pairs"
                except Exception:
                    self._planner_selected_mode = "best_pairs"
        return bool(state.get("has_run", False))

    def reset_to_defaults(self):
        """Restore the room optimizer controls to their built-in defaults."""
        self._session_state = {}
        self._restoring_session_state = True
        try:
            self._min_stats_input.setText("")
            self._max_risk_input.setText("")
            self._mode_toggle_btn.setChecked(False)
            self._minimize_variance_checkbox.setChecked(True)
            self._avoid_lovers_checkbox.setChecked(True)
            self._prefer_low_aggression_checkbox.setChecked(True)
            self._prefer_high_libido_checkbox.setChecked(True)
            self._maximize_throughput_checkbox.setChecked(False)
            self._ignore_stat_priority_checkbox.setChecked(False)
            if hasattr(self, "_send_kittens_checkbox"):
                self._send_kittens_checkbox.setChecked(False)
            if hasattr(self, "_avoid_trait_loss_checkbox"):
                self._avoid_trait_loss_checkbox.setChecked(False)
            self._deep_optimize_btn.setChecked(False)
            if hasattr(self, "_bottom_tabs"):
                self._bottom_tabs.setCurrentIndex(3)
            self._room_priority_panel.reset_to_defaults()
        finally:
            self._restoring_session_state = False
        self._pending_initial_restore_run = False
        self.retranslate_ui()
        self._save_session_state(has_run=False, use_sa=False)

    def _import_from_planner(self):
        if self._planner_view is None:
            return
        self._planner_traits = self._planner_view.get_selected_traits()
        if hasattr(self._planner_view, "get_mode_profiles"):
            self._planner_mode_profiles = _normalize_mutation_mode_profiles(
                self._planner_view.get_mode_profiles(),
                legacy_traits=self._planner_traits,
            )
            if hasattr(self, "_details_pane") and self._details_pane is not None:
                self._details_pane.set_mode_profiles(self._planner_mode_profiles)
        if hasattr(self._planner_view, "get_selected_mode"):
            try:
                mode = str(self._planner_view.get_selected_mode() or "best_pairs").strip().lower()
                self._planner_selected_mode = mode if mode in MUTATION_CLASS_MODES else "best_pairs"
            except Exception:
                self._planner_selected_mode = "best_pairs"
        if self._planner_mode_profiles_total_traits(self._planner_mode_profiles) <= 0:
            self._import_planner_btn.setText(_tr("room_optimizer.import_none", default="No Mutations Imported"))
            self._import_planner_btn.setToolTip(self._import_planner_button_tooltip())
            self._style_import_planner_button(self._import_planner_btn, active=False)
            return
        summary = self._planner_mode_profiles_summary(self._planner_mode_profiles, self._planner_selected_mode)
        self._import_planner_btn.setText(_tr("room_optimizer.imported", summary=summary))
        self._import_planner_btn.setToolTip(self._import_planner_button_tooltip())
        self._style_import_planner_button(self._import_planner_btn, active=True)

    def _build_setup_info_html(self) -> str:
        def row(title: str, body: str) -> str:
            return (
                "<tr>"
                f"<td>{html.escape(title)}</td>"
                f"<td>{html.escape(body)}</td>"
                "</tr>"
            )

        entries = [
            row(
                _tr("room_optimizer.min_stats"),
                "Filter out cats below this base-stat total.",
            ),
            row(
                _tr("room_optimizer.max_risk"),
                "Set the highest inbreeding risk the optimizer will allow.",
            ),
            row(
                _tr("room_optimizer.import_planner", default="Import Mutation Planner"),
                "Load traits from the breeding planner before you optimize.",
            ),
            row(
                _tr("room_optimizer.optimize_btn"),
                "Run the optimizer once using the current room and scoring settings.",
            ),
            row(
                _tr("room_optimizer.more_depth_calculation", default="More Depth Calculation"),
                "Run a slower simulated-annealing search for a deeper pass. Available in both Pair Quality and Family Separation modes.",
            ),
            row(
                _tr("menu.settings.optimizer_search_settings", default="Optimizer Search Settings"),
                "Open Settings to adjust the shared temperature and neighbor sampling values used by both planners.",
            ),
            row(
                "Optimizer Mode",
                "Switch between Pair Quality and Family Separation scoring.",
            ),
            row(
                _tr("room_optimizer.toggle.minimize_variance"),
                "Favor more even room pair counts. Only meaningful in Pair Quality mode.",
            ),
            row(
                _tr("room_optimizer.toggle.avoid_lovers"),
                "Keep mutual lovers in the same room.",
            ),
            row(
                _tr("room_optimizer.toggle.prefer_low_aggression"),
                "Prefer cats with lower aggression scores.",
            ),
            row(
                _tr("room_optimizer.toggle.prefer_high_libido"),
                "Prefer cats with higher libido scores.",
            ),
            row(
                _tr("room_optimizer.toggle.maximize_throughput"),
                "Favor layouts with the most simultaneous valid pairs.",
            ),
        ]
        return (
            "<style>"
            "table { border-collapse: collapse; width: 100%; }"
            "th, td { border: 1px solid #3a3a5f; padding: 4px 8px; vertical-align: top; }"
            "th { background: #1a1a38; color: #c9d6ff; text-align: left; }"
            "td { color: #ddd; }"
            "td:first-child { width: 34%; font-weight: bold; color: #f0f0ff; white-space: nowrap; }"
            "td:last-child { width: 66%; }"
            "</style>"
            "<table>"
            "<thead><tr><th>Optimizer options</th><th>Description</th></tr></thead>"
            "<tbody>"
            f"{''.join(entries)}"
            "</tbody></table>"
        )

    def retranslate_ui(self):
        self._title.setText(_tr("room_optimizer.title"))
        self._summary.setText(_tr("room_optimizer.summary_empty"))
        self._min_stats_label.setText(_tr("room_optimizer.min_stats"))
        self._min_stats_input.setPlaceholderText(_tr("room_optimizer.placeholder.min_stats"))
        self._max_risk_label.setText(_tr("room_optimizer.max_risk"))
        self._max_risk_input.setPlaceholderText(_tr("room_optimizer.placeholder.max_risk"))
        self._min_stats_label.setToolTip(_tr("room_optimizer.min_stats_tooltip", default="Minimum base-stat total a cat must meet to be considered."))
        self._min_stats_input.setToolTip(_tr("room_optimizer.min_stats_tooltip", default="Minimum base-stat total a cat must meet to be considered."))
        self._max_risk_label.setToolTip(_tr("room_optimizer.max_risk_tooltip", default="Highest inbreeding risk percentage the optimizer will accept."))
        self._max_risk_input.setToolTip(_tr("room_optimizer.max_risk_tooltip", default="Highest inbreeding risk percentage the optimizer will accept."))
        self._optimize_btn.setToolTip(
            _tr(
                "room_optimizer.optimize_btn_tooltip",
                default="Run the optimizer once using the current room and scoring settings.",
            )
        )
        self._optimize_btn.setText(_tr("room_optimizer.optimize_btn"))
        self._cancel_btn.setText(_tr("room_optimizer.cancel_btn", default="Cancel"))
        self._set_mode_button_text(self._mode_toggle_btn.isChecked())
        RoomOptimizerView._set_toggle_button_label(self._deep_optimize_btn, "room_optimizer.toggle.use_sa")
        self._deep_optimize_btn.setEnabled(True)
        self._deep_optimize_btn.setToolTip(
            _tr("room_optimizer.more_depth_tooltip", default="Use simulated annealing for a slower, deeper search.")
        )
        self._minimize_variance_checkbox.setEnabled(not self._mode_toggle_btn.isChecked())
        self._minimize_variance_checkbox.setToolTip(
            "" if not self._mode_toggle_btn.isChecked() else _tr("room_optimizer.tooltip.variance")
        )
        self._maximize_throughput_checkbox.setEnabled(not self._mode_toggle_btn.isChecked())
        if self._planner_mode_profiles_total_traits(self._planner_mode_profiles) > 0 and self._planner_view is not None:
            self._import_from_planner()
        else:
            self._import_planner_btn.setText(_tr("room_optimizer.import_none", default="No Mutations Imported"))
            self._import_planner_btn.setToolTip(self._import_planner_button_tooltip())
            self._style_import_planner_button(self._import_planner_btn, active=False)
        # Refresh toggle button labels
        RoomOptimizerView._set_toggle_button_label(self._minimize_variance_checkbox, "room_optimizer.toggle.minimize_variance")
        RoomOptimizerView._set_toggle_button_label(self._avoid_lovers_checkbox, "room_optimizer.toggle.avoid_lovers")
        RoomOptimizerView._set_toggle_button_label(self._prefer_low_aggression_checkbox, "room_optimizer.toggle.prefer_low_aggression")
        RoomOptimizerView._set_toggle_button_label(self._prefer_high_libido_checkbox, "room_optimizer.toggle.prefer_high_libido")
        RoomOptimizerView._set_toggle_button_label(self._maximize_throughput_checkbox, "room_optimizer.toggle.maximize_throughput")
        self._maximize_throughput_checkbox.setToolTip(_tr("room_optimizer.tooltip.maximize_throughput"))
        if hasattr(self, "_ignore_stat_priority_checkbox"):
            RoomOptimizerView._set_toggle_button_label(self._ignore_stat_priority_checkbox, "room_optimizer.toggle.ignore_stat_priority")
            self._ignore_stat_priority_checkbox.setToolTip(
                _tr(
                    "room_optimizer.tooltip.ignore_stat_priority",
                    default="Disable class stat prioritization when scoring pairs. Use this when chasing all-7 stat lines.",
                )
            )
        if hasattr(self, "_send_kittens_checkbox"):
            RoomOptimizerView._set_toggle_button_label(self._send_kittens_checkbox, "room_optimizer.toggle.send_kittens_to_fallback")
            self._send_kittens_checkbox.setToolTip(
                _tr(
                    "room_optimizer.tooltip.send_kittens_to_fallback",
                    default="Route kittens (age 0-1) to fallback rooms since they can't breed yet. Eternal-youth cats are unaffected.",
                )
            )
        if hasattr(self, "_avoid_trait_loss_checkbox"):
            RoomOptimizerView._set_toggle_button_label(self._avoid_trait_loss_checkbox, "room_optimizer.toggle.avoid_trait_loss")
            self._avoid_trait_loss_checkbox.setToolTip(
                _tr(
                    "room_optimizer.tooltip.avoid_trait_loss",
                    default="Avoid placing cats with desired mutations into high-Evolution rooms, and cats with desired disorders into high-Health rooms (those effects can strip the traits).",
                )
            )
        if hasattr(self, "_shared_search_note"):
            self._shared_search_note.setText(_tr(
                "menu.settings.optimizer_search_settings.summary",
                default="Shared annealing settings live in Settings and apply to both planners.",
            ))
        self._import_planner_btn.setToolTip(self._import_planner_button_tooltip())
        self._setup_info_title.setText(_tr("room_optimizer.setup_info.title", default="Optimizer Setup Guide"))
        self._setup_info_subtitle.setText(
            _tr(
                "room_optimizer.setup_info.subtitle",
                default="The controls on the left shape how room layouts are scored before you calculate.",
            )
        )
        self._setup_info_browser.setHtml(self._build_setup_info_html())
        # Refresh tab titles
        self._bottom_tabs.setTabText(0, _tr("room_optimizer.tab.configure_rooms"))
        self._bottom_tabs.setTabText(1, _tr("room_optimizer.tab.stat_priorities", default="Class Stats"))
        self._bottom_tabs.setTabText(2, _tr("room_optimizer.tab.setup"))
        self._bottom_tabs.setTabText(3, _tr("room_optimizer.tab.breeding_pairs"))
        self._bottom_tabs.setTabText(4, _tr("room_optimizer.tab.cat_locator"))
        self._table.setHorizontalHeaderLabels([
            _tr("room_optimizer.table.room"),
            _tr("room_optimizer.table.type", default="Type"),
            _tr("room_optimizer.table.cats"),
            _tr("room_optimizer.table.expected_pairs"),
            _tr("room_optimizer.table.avg_stats"),
            _tr("room_optimizer.table.risk"),
            _tr("room_optimizer.table.details"),
        ])

    def _calculate_optimal_distribution(self, use_sa: bool = False):
        """Kick off background optimizer worker."""
        self._pending_cache_recalc = False
        if self._optimizer_worker is not None and self._optimizer_worker.isRunning():
            return  # already running

        min_stats = 0
        try:
            if self._min_stats_input.text().strip():
                min_stats = int(self._min_stats_input.text().strip())
        except ValueError:
            pass

        max_risk = 10.0
        try:
            if self._max_risk_input.text().strip():
                max_risk = float(self._max_risk_input.text().strip())
        except ValueError:
            pass

        sa_temperature = _saved_optimizer_search_temperature()
        sa_neighbors = _saved_optimizer_search_neighbors()
        maximize_throughput = bool(self._maximize_throughput_checkbox.isChecked()) if hasattr(self, "_maximize_throughput_checkbox") else False
        ignore_stat_priority = bool(self._ignore_stat_priority_checkbox.isChecked()) if hasattr(self, "_ignore_stat_priority_checkbox") else False
        send_kittens_to_fallback = bool(self._send_kittens_checkbox.isChecked()) if hasattr(self, "_send_kittens_checkbox") else False
        avoid_trait_loss = bool(self._avoid_trait_loss_checkbox.isChecked()) if hasattr(self, "_avoid_trait_loss_checkbox") else False
        mode_family = self._mode_toggle_btn.isChecked()

        params = {
            "min_stats": min_stats,
            "max_risk": max_risk,
            "minimize_variance": self._minimize_variance_checkbox.isChecked(),
            "avoid_lovers": self._avoid_lovers_checkbox.isChecked(),
            "prefer_low_aggression": self._prefer_low_aggression_checkbox.isChecked(),
            "prefer_high_libido": self._prefer_high_libido_checkbox.isChecked(),
            "maximize_throughput": maximize_throughput and not mode_family,
            "ignore_stat_priority": ignore_stat_priority,
            "send_kittens_to_fallback": send_kittens_to_fallback,
            "avoid_trait_loss": avoid_trait_loss,
            "sa_temperature": sa_temperature,
            "sa_neighbors": sa_neighbors,
            "mode_family": mode_family,
            "use_sa": use_sa,
            "planner_traits": list(self._planner_traits),
            "mode_profiles": self._planner_mode_profiles,
            "available_rooms": list(getattr(self, "_available_rooms", [])),
            "room_config": self._room_priority_panel.get_config(),
            "room_stats": dict(self._room_summaries),
        }
        self._save_session_state(has_run=True, use_sa=use_sa)

        self._optimize_btn.setEnabled(False)
        self._cancel_btn.setVisible(True)
        self._summary.setText(_tr("room_optimizer.status.calculating"))

        worker = RoomOptimizerWorker(
            self._cats,
            getattr(self, "_excluded_keys", set()),
            self._cache,
            params,
            parent=self,
        )
        worker.finished.connect(self._on_optimizer_result)
        self._optimizer_worker = worker
        worker.start()

    def _cancel_optimization(self):
        """Request cancellation of the running optimizer worker."""
        if self._optimizer_worker is not None and self._optimizer_worker.isRunning():
            self._optimizer_worker.requestInterruption()
            self._summary.setText(_tr("room_optimizer.status.cancelling", default="Cancelling…"))
            self._cancel_btn.setEnabled(False)

    def _on_optimizer_result(self, result: dict):
        self._optimizer_worker = None
        self._optimize_btn.setEnabled(True)
        self._cancel_btn.setVisible(False)
        self._cancel_btn.setEnabled(True)

        if result.get("cancelled"):
            self._summary.setText(_tr("room_optimizer.status.cancelled", default="Optimization cancelled."))
            return

        if "error" in result:
            self._table.setRowCount(0)
            self._selected_room_data = None
            self._refresh_room_action_buttons()
            self._summary.setText(_tr("room_optimizer.status.error", message=result["error"]))
            return

        room_rows = result["room_rows"]
        locator_data = result["locator_data"]
        excluded_rows = result["excluded_rows"]
        mode_family = result["mode_family"]
        min_stats = result["min_stats"]
        max_risk = result["max_risk"]
        minimize_variance = result["minimize_variance"]
        avoid_lovers = result["avoid_lovers"]
        prefer_low_aggression = result["prefer_low_aggression"]
        prefer_high_libido = result["prefer_high_libido"]
        maximize_throughput = result.get("maximize_throughput", False)
        sa_temperature = float(result.get("sa_temperature", 0.0) or 0.0)
        sa_neighbors = int(result.get("sa_neighbors", 0) or 0)
        use_sa = result.get("use_sa", False)

        self._cat_locator.show_assignments(locator_data)

        # Prevent Qt from reshuffling rows while we are still inserting items.
        # If sorting stays on here, the room labels and cat lists can get split
        # across different rows as the table keeps resorting itself.
        sorting_was_enabled = self._table.isSortingEnabled()
        header = self._table.horizontalHeader()
        sort_column = header.sortIndicatorSection()
        sort_order = header.sortIndicatorOrder()
        self._table.setSortingEnabled(False)

        self._table.setRowCount(0)
        self._selected_room_data = None
        self._details_pane.show_room(None)
        self._refresh_room_action_buttons()

        row_idx = 0
        total_pairs = 0
        total_assigned = 0

        for room_data in room_rows:
            room_label = room_data["room_label"]
            room_key = room_data.get("room")
            is_fallback = bool(room_data.get("is_fallback"))
            room_mode = str(room_data.get("room_mode") or ("fallback" if is_fallback else "best_pairs")).strip().lower()
            room_mode_label = str(room_data.get("room_mode_label") or _mutation_class_label(room_mode))
            cat_names = room_data["cat_names"]
            cat_keys = room_data.get("cat_keys", [])
            room_pairs = room_data["pairs"]
            avg_stats = room_data["avg_stats"]
            avg_risk = room_data["avg_risk"]
            room_capacity = room_data.get("capacity")
            room_stim = room_data.get("base_stim")

            best_pairs_count = room_data.get("best_pairs_count", len(room_pairs))
            total_assigned += len(cat_names)
            total_pairs += best_pairs_count

            self._table.insertRow(row_idx)
            room_color = _room_color(room_key)
            room_bg = _room_tint(room_key, strength=0.16, lift=14)

            room_item = QTableWidgetItem(room_label)
            room_item.setTextAlignment(Qt.AlignCenter)
            room_item.setForeground(QBrush(room_color))
            room_item.setBackground(QBrush(room_bg))
            room_item.setToolTip(
                f"Capacity: {'∞' if room_capacity in (None, 0) else int(room_capacity)}\n"
                f"Base stimulation: {float(room_stim or 0.0):.0f}"
            )

            type_item = QTableWidgetItem(
                _tr("room_optimizer.table.fallback", default="Fallback")
                if is_fallback
                else room_mode_label
            )
            type_item.setTextAlignment(Qt.AlignCenter)
            type_item.setForeground(QBrush(QColor(208, 208, 224) if is_fallback else QColor(147, 224, 160)))
            type_item.setBackground(QBrush(room_bg))

            cats_item = QTableWidgetItem(", ".join(cat_names) or "—")
            cats_item.setBackground(QBrush(room_bg))

            pairs_item = QTableWidgetItem(str(best_pairs_count))
            pairs_item.setTextAlignment(Qt.AlignCenter)
            pairs_item.setBackground(QBrush(room_bg))

            stats_item = QTableWidgetItem(f"{avg_stats:.1f}")
            stats_item.setTextAlignment(Qt.AlignCenter)
            stats_item.setBackground(QBrush(room_bg))
            if avg_stats >= 200:
                stats_item.setForeground(QBrush(QColor(98, 194, 135)))
            elif avg_stats >= 150:
                stats_item.setForeground(QBrush(QColor(143, 201, 230)))
            else:
                stats_item.setForeground(QBrush(QColor(190, 145, 40)))

            risk_item = QTableWidgetItem(f"{avg_risk:.0f}%")
            risk_item.setTextAlignment(Qt.AlignCenter)
            risk_item.setBackground(QBrush(room_bg))
            if avg_risk >= 50:
                risk_item.setForeground(QBrush(QColor(217, 119, 119)))
            elif avg_risk >= 20:
                risk_item.setForeground(QBrush(QColor(216, 181, 106)))
            else:
                risk_item.setForeground(QBrush(QColor(98, 194, 135)))

            details_lines = []
            for p in room_pairs[:3]:
                details_lines.append(
                    f"{p['cat_a']} × {p['cat_b']} "
                    f"(stats: {p['avg_stats']:.0f}, risk: {p['risk']:.0f}%)"
                )
            if len(room_pairs) > 3:
                details_lines.append(f"... and {len(room_pairs) - 3} more")
            details_item = QTableWidgetItem("; ".join(details_lines) or "—")
            details_item.setBackground(QBrush(room_bg))

            room_item.setData(Qt.UserRole, {
                "room": room_label,
                "cats": cat_names,
                "cat_keys": cat_keys,
                "room_mode": room_mode,
                "room_mode_label": room_mode_label,
                "total_pairs": best_pairs_count,
                "avg_stats": avg_stats,
                "avg_risk": avg_risk,
                "excluded_cats": [],
                "pairs": room_pairs,
            })

            self._table.setItem(row_idx, 0, room_item)
            self._table.setItem(row_idx, 1, type_item)
            self._table.setItem(row_idx, 2, cats_item)
            self._table.setItem(row_idx, 3, pairs_item)
            self._table.setItem(row_idx, 4, stats_item)
            self._table.setItem(row_idx, 5, risk_item)
            self._table.setItem(row_idx, 6, details_item)
            row_idx += 1

        if excluded_rows:
            excluded_names = [r["name"] for r in excluded_rows]
            excluded_keys = [r.get("db_key") for r in excluded_rows if r.get("db_key") is not None]
            self._table.insertRow(row_idx)
            excluded_room_item = QTableWidgetItem("Excluded")
            excluded_room_item.setTextAlignment(Qt.AlignCenter)
            excluded_room_item.setForeground(QBrush(QColor(170, 120, 120)))
            excluded_room_item.setData(Qt.UserRole, {
                "room": "Excluded",
                "cats": excluded_names,
                "cat_keys": excluded_keys,
                "total_pairs": 0,
                "avg_stats": 0.0,
                "avg_risk": 0.0,
                "excluded_cats": excluded_names,
                "excluded_cat_rows": excluded_rows,
                "pairs": [],
            })
            self._table.setItem(row_idx, 0, excluded_room_item)
            excluded_type_item = QTableWidgetItem("—")
            excluded_type_item.setTextAlignment(Qt.AlignCenter)
            excluded_type_item.setForeground(QBrush(QColor(120, 120, 130)))
            self._table.setItem(row_idx, 1, excluded_type_item)
            self._table.setItem(row_idx, 2, QTableWidgetItem(f"{len(excluded_rows)} excluded cats"))
            for col in (3, 4, 5):
                dash = QTableWidgetItem("—")
                dash.setTextAlignment(Qt.AlignCenter)
                self._table.setItem(row_idx, col, dash)
            self._table.setItem(row_idx, 6, QTableWidgetItem("Excluded from optimizer breeding calculations"))
            row_idx += 1

        filter_info = [f"mode: {'family separation' if mode_family else 'pair quality'}"]
        filter_info.append(f"depth: {'SA' if use_sa else 'greedy'}")
        if min_stats > 0:
            filter_info.append(f"min stats: {min_stats}")
        if max_risk < 100:
            filter_info.append(f"max risk: {max_risk}%")
        if (not mode_family) and minimize_variance:
            filter_info.append("variance: on")
        if prefer_low_aggression:
            filter_info.append("prefer low aggression")
        if prefer_high_libido:
            filter_info.append("prefer high libido")
        if maximize_throughput and not mode_family:
            filter_info.append("maximize throughput")
        if use_sa:
            filter_info.append(f"temp: {sa_temperature:g}")
            filter_info.append(f"neighbors: {sa_neighbors}")
        if avoid_lovers:
            filter_info.append("keep lovers together")
        filter_str = f"  |  Filters: {', '.join(filter_info)}" if filter_info else ""

        self._summary.setText(
            f"Optimized {total_assigned} cats into {len(room_rows)} rooms  |  "
            f"{total_pairs} total breeding pairs{filter_str}"
        )

        if sorting_was_enabled:
            self._table.setSortingEnabled(True)
            if sort_column is not None and sort_column >= 0:
                self._table.sortByColumn(sort_column, sort_order)
        else:
            self._table.setSortingEnabled(False)

        # Cache the result for cross-session restore
        self._save_cached_results(result)


class RoomOptimizerCatLocator(QWidget):
    """Shows all cats with their current location vs assigned room, sorted by room priority."""

    COL_CAT = 0
    COL_AGE = 1
    COL_CURRENT = 2
    COL_MOVE_TO = 3
    COL_ACTION = 4

    def __init__(self):
        super().__init__()
        self.setStyleSheet("background:#0a0a18;")
        self._navigate_to_cat_callback = None
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 8, 10, 8)
        root.setSpacing(6)

        self._summary = QLabel(_tr("room_optimizer.locator.summary.empty"))
        self._summary.setStyleSheet("color:#888; font-size:11px;")
        root.addWidget(self._summary)

        self._table = QTableWidget(0, 5)
        self._table.setIconSize(QSize(60, 20))
        self._table.setHorizontalHeaderLabels([
            _tr("room_optimizer.locator.table.cat"),
            _tr("room_optimizer.locator.table.age"),
            _tr("room_optimizer.locator.table.currently_in"),
            _tr("room_optimizer.locator.table.move_to"),
            _tr("room_optimizer.locator.table.action"),
        ])
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionMode(QAbstractItemView.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.setFocusPolicy(Qt.NoFocus)
        self._table.setMouseTracking(True)
        self._table.cellClicked.connect(self._on_cat_clicked)
        self._table.cellEntered.connect(lambda r, c: self._table.setCursor(
            Qt.PointingHandCursor if c == self.COL_CAT else Qt.ArrowCursor
        ))
        self._table.setSortingEnabled(True)
        self._table.setAlternatingRowColors(True)
        hh = self._table.horizontalHeader()
        hh.setSectionResizeMode(self.COL_CAT, QHeaderView.Interactive)
        hh.setSectionResizeMode(self.COL_AGE, QHeaderView.Interactive)
        hh.setSectionResizeMode(self.COL_CURRENT, QHeaderView.Interactive)
        hh.setSectionResizeMode(self.COL_MOVE_TO, QHeaderView.Interactive)
        hh.setSectionResizeMode(self.COL_ACTION, QHeaderView.Interactive)
        self._table.setColumnWidth(self.COL_CAT, 220)
        self._table.setColumnWidth(self.COL_AGE, 45)
        self._table.setColumnWidth(self.COL_CURRENT, 140)
        self._table.setColumnWidth(self.COL_MOVE_TO, 140)
        self._table.setColumnWidth(self.COL_ACTION, 65)
        self._table.setStyleSheet("""
            QTableWidget {
                background:#0d0d1c; alternate-background-color:#131326;
                color:#ddd; border:1px solid #26264a; font-size:12px;
            }
            QTableWidget::item { padding:3px 6px; }
            QHeaderView::section {
                background:#16213e; color:#888; padding:5px 4px;
                border:none; border-bottom:1px solid #1e1e38;
                border-right:1px solid #16213e; font-size:11px; font-weight:bold;
            }
        """)
        root.addWidget(self._table, 1)

    def set_navigate_to_cat_callback(self, callback):
        self._navigate_to_cat_callback = callback

    @staticmethod
    def _pair_color(room_order: float | int) -> QColor:
        try:
            rank = max(1, int(float(room_order or 0)) + 1)
        except (TypeError, ValueError):
            rank = 1
        return PAIR_COLORS[(rank - 1) % len(PAIR_COLORS)]

    @staticmethod
    def _pair_tint(color: QColor, strength: float = 0.28, lift: int = 18) -> QColor:
        return QColor(
            min(255, int(color.red() * strength) + lift),
            min(255, int(color.green() * strength) + lift),
            min(255, int(color.blue() * strength) + lift),
        )

    def show_assignments(self, all_assignments: list[dict]):
        """
        all_assignments: list of dicts with keys:
            name, gender_display, age, current_room, assigned_room, room_order, needs_move
        Sorted by room_order (Priority 1 first, Fallback last).
        """
        # Sort by assigned room priority, then by name within each room
        all_assignments.sort(key=lambda d: (d.get("room_order", 999), (d["name"] or "").lower()))

        self._table.setSortingEnabled(False)
        self._table.setRowCount(len(all_assignments))

        moves_needed = 0
        for row, info in enumerate(all_assignments):
            heart = " ♥" if info.get("has_lover") else ""
            name_item = QTableWidgetItem(f"{info['name']}{heart} ({info['gender_display']})")
            name_item.setData(Qt.UserRole, info.get("db_key"))
            icon = _make_tag_icon(info.get("tags", []))
            if not icon.isNull():
                name_item.setIcon(icon)
            name_item.setForeground(QColor("#5b9bd5"))
            name_item.setToolTip(_tr("room_optimizer.locator.tooltip.jump_to_cat"))

            age_val = info.get("age")
            if isinstance(age_val, (int, float)):
                age_item = _SortByUserRoleItem(f"{age_val:.2f}" if isinstance(age_val, float) else str(age_val))
                age_item.setData(Qt.UserRole, float(age_val))
            else:
                age_item = _SortByUserRoleItem(str(age_val) if age_val is not None else "?")
                age_item.setData(Qt.UserRole, 0.0)
            age_item.setTextAlignment(Qt.AlignCenter)

            current_item = QTableWidgetItem(info["current_room"])

            assigned_item = _SortByUserRoleItem(info["assigned_room"])
            # Store room_order so sorting this column keeps room priority order
            assigned_item.setData(Qt.UserRole, info.get("room_order", 999))

            row_room_key = info.get("current_room_key") or _room_key_from_display(info.get("current_room"))
            row_bg = _room_tint(row_room_key, strength=0.18, lift=14)
            if row_room_key is None:
                row_bg = self._pair_tint(self._pair_color(info.get("room_order", row)), strength=0.18, lift=14)
            for it in (name_item, age_item, current_item):
                it.setBackground(QBrush(row_bg))

            move_room_key = info.get("assigned_room_key") or _room_key_from_display(info.get("assigned_room"))
            if move_room_key is not None:
                move_color = _room_color(move_room_key)
                move_bg = _room_tint(move_room_key, strength=0.24, lift=18)
                assigned_item.setBackground(QBrush(move_bg))
                assigned_item.setForeground(QBrush(move_color))
            else:
                move_color = self._pair_color(info.get("room_order", row))
                move_bg = self._pair_tint(move_color, strength=0.36, lift=22)
                assigned_item.setBackground(QBrush(move_bg))
                assigned_item.setForeground(QBrush(move_color))

            needs_move = info.get("needs_move", False)
            if needs_move:
                moves_needed += 1
                action_item = QTableWidgetItem(_tr("room_optimizer.locator.action.move"))
                action_item.setTextAlignment(Qt.AlignCenter)
                action_item.setForeground(QBrush(QColor(216, 181, 106)))
                action_item.setBackground(QBrush(row_bg))
            else:
                action_item = QTableWidgetItem(_tr("room_optimizer.locator.action.ok"))
                action_item.setTextAlignment(Qt.AlignCenter)
                action_item.setForeground(QBrush(QColor(98, 194, 135)))
                action_item.setBackground(QBrush(row_bg))

            self._table.setItem(row, self.COL_CAT, name_item)
            self._table.setItem(row, self.COL_AGE, age_item)
            self._table.setItem(row, self.COL_CURRENT, current_item)
            self._table.setItem(row, self.COL_MOVE_TO, assigned_item)
            self._table.setItem(row, self.COL_ACTION, action_item)

        self._table.setSortingEnabled(True)
        # Default sort: by Move To column (room priority order)
        self._table.sortByColumn(self.COL_MOVE_TO, Qt.AscendingOrder)

        total = len(all_assignments)
        stay = total - moves_needed
        self._summary.setText(
            _tr("room_optimizer.locator.summary.with_counts", total=total, moves=moves_needed, stay=stay)
        )

    def retranslate_ui(self):
        self._table.setHorizontalHeaderLabels([
            _tr("room_optimizer.locator.table.cat"),
            _tr("room_optimizer.locator.table.age"),
            _tr("room_optimizer.locator.table.currently_in"),
            _tr("room_optimizer.locator.table.move_to"),
            _tr("room_optimizer.locator.table.action"),
        ])
        if self._table.rowCount() == 0:
            self._summary.setText(_tr("room_optimizer.locator.summary.empty"))

    def _on_cat_clicked(self, row: int, col: int):
        if col != self.COL_CAT:
            return
        item = self._table.item(row, col)
        if item is None:
            return
        db_key = item.data(Qt.UserRole)
        if db_key is not None and self._navigate_to_cat_callback is not None:
            self._navigate_to_cat_callback(db_key)

    def clear(self):
        self._table.setRowCount(0)
        self._summary.setText(_tr("room_optimizer.locator.summary.empty"))


class RoomOptimizerDetailPanel(QWidget):
    def __init__(self):
        super().__init__()
        self.setStyleSheet("background:#0a0a18; border-top:1px solid #1e1e38;")
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 10, 14, 10)
        root.setSpacing(8)

        # Header with summary label and best pairs toggle
        hdr = QHBoxLayout()
        hdr.setSpacing(8)
        self._summary = QLabel(_tr("room_optimizer.detail.summary.select_room"))
        self._summary.setStyleSheet("color:#aaa; font-size:12px;")
        self._summary.setWordWrap(True)
        hdr.addWidget(self._summary, 1)

        self._best_pairs_btn = QPushButton(_tr("room_optimizer.detail.toggle.all_pairs"))
        self._best_pairs_btn.setCheckable(True)
        self._best_pairs_btn.setChecked(False)
        self._best_pairs_btn.setMinimumWidth(90)
        self._best_pairs_btn.setStyleSheet(
            "QPushButton { background:#1e1e38; color:#ccc; border:1px solid #2a2a4a; padding:4px;"
            "             font-size:11px; border-radius:3px; }"
            "QPushButton:hover { background:#252555; }"
            "QPushButton:checked { background:#3a5a7a; color:#fff; }"
        )
        self._best_pairs_btn.setToolTip(_tr("room_optimizer.detail.toggle.tooltip"))
        self._best_pairs_btn.clicked.connect(self._on_toggle_best_pairs)
        hdr.addWidget(self._best_pairs_btn)

        root.addLayout(hdr)

        self._current_data: Optional[dict] = None
        self._mode_profiles: dict[str, dict] = _normalize_mutation_mode_profiles({})
        self._navigate_to_cat_callback = None  # Callback to navigate to a cat by name
        self._navigate_to_pair_callback = None  # Callback to navigate to a cat pair

        self._pairs_table = QTableWidget(0, 16)
        self._pairs_table.setHorizontalHeaderLabels([
            "",
            _tr("room_optimizer.detail.table.cat_a"),
            _tr("room_optimizer.detail.table.cat_b"),
            "\u2665",
            "STR", "DEX", "CON", "INT", "SPD", "CHA", "LCK",
            _tr("room_optimizer.detail.table.sum"),
            _tr("room_optimizer.detail.table.avg"),
            _tr("room_optimizer.detail.table.risk"),
            _tr("room_optimizer.detail.table.rank"),
            _tr("room_optimizer.detail.table.mutations", default="Mutations"),
        ])
        self._pairs_table.verticalHeader().setVisible(False)
        self._pairs_table.setSelectionMode(QAbstractItemView.NoSelection)
        self._pairs_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._pairs_table.setFocusPolicy(Qt.NoFocus)
        self._pairs_table.setWordWrap(False)
        self._pairs_table.setAlternatingRowColors(True)
        hh = self._pairs_table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Fixed)
        hh.setSectionResizeMode(1, QHeaderView.Interactive)
        hh.setSectionResizeMode(2, QHeaderView.Interactive)
        for col in range(3, 15):
            hh.setSectionResizeMode(col, QHeaderView.Interactive)
        hh.setSectionResizeMode(15, QHeaderView.Stretch)
        self._pairs_table.setColumnWidth(0, 34)
        self._pairs_table.setColumnWidth(1, 120)
        self._pairs_table.setColumnWidth(2, 120)
        self._pairs_table.setColumnWidth(3, 24)
        for col in range(4, 11):
            self._pairs_table.setColumnWidth(col, 40)
        self._pairs_table.setColumnWidth(11, 60)
        self._pairs_table.setColumnWidth(12, 50)
        self._pairs_table.setColumnWidth(13, 75)
        self._pairs_table.setColumnWidth(14, 50)
        self._pairs_table.setStyleSheet("""
            QTableWidget {
                background:#0d0d1c; alternate-background-color:#131326;
                color:#ddd; border:1px solid #26264a; font-size:12px;
            }
            QTableWidget::item { padding:3px 4px; }
            QHeaderView::section {
                background:#16213e; color:#888; padding:5px 4px;
                border:none; border-bottom:1px solid #1e1e38;
                border-right:1px solid #16213e; font-size:11px; font-weight:bold;
            }
        """)
        self._pairs_table.itemClicked.connect(self._on_pair_cell_clicked)
        root.addWidget(self._pairs_table, 1)

        self._excluded_table = QTableWidget(0, 12)
        self._excluded_table.setHorizontalHeaderLabels([
            _tr("room_optimizer.detail.excluded.cat"), "STR", "DEX", "CON", "INT", "SPD", "CHA", "LCK",
            _tr("room_optimizer.detail.excluded.sum"),
            _tr("room_optimizer.detail.excluded.agg"),
            _tr("room_optimizer.detail.excluded.lib"),
            _tr("room_optimizer.detail.excluded.inbred"),
        ])
        self._excluded_table.verticalHeader().setVisible(False)
        self._excluded_table.setSelectionMode(QAbstractItemView.NoSelection)
        self._excluded_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._excluded_table.setFocusPolicy(Qt.NoFocus)
        self._excluded_table.setAlternatingRowColors(True)
        self._excluded_table.hide()
        ex_hh = self._excluded_table.horizontalHeader()
        ex_hh.setSectionResizeMode(0, QHeaderView.Stretch)
        for col in range(1, 9):
            ex_hh.setSectionResizeMode(col, QHeaderView.Interactive)
        for col in range(1, 8):
            self._excluded_table.setColumnWidth(col, 50)
        self._excluded_table.setColumnWidth(8, 60)
        for col in range(9, 12):
            self._excluded_table.setColumnWidth(col, 60)
            ex_hh.setSectionResizeMode(col, QHeaderView.Interactive)
        self._excluded_table.setStyleSheet("""
            QTableWidget {
                background:#0d0d1c; alternate-background-color:#131326;
                color:#ddd; border:1px solid #26264a; font-size:12px;
            }
            QTableWidget::item { padding:3px 4px; }
            QHeaderView::section {
                background:#16213e; color:#888; padding:5px 4px;
                border:none; border-bottom:1px solid #1e1e38;
                border-right:1px solid #16213e; font-size:11px; font-weight:bold;
            }
        """)
        root.addWidget(self._excluded_table, 1)

    def retranslate_ui(self):
        self._best_pairs_btn.setText(
            _tr("room_optimizer.detail.toggle.best_pairs")
            if self._best_pairs_btn.isChecked()
            else _tr("room_optimizer.detail.toggle.all_pairs")
        )
        self._best_pairs_btn.setToolTip(_tr("room_optimizer.detail.toggle.tooltip"))
        self._pairs_table.setHorizontalHeaderLabels([
            "",
            _tr("room_optimizer.detail.table.cat_a"),
            _tr("room_optimizer.detail.table.cat_b"),
            "\u2665",
            "STR", "DEX", "CON", "INT", "SPD", "CHA", "LCK",
            _tr("room_optimizer.detail.table.sum"),
            _tr("room_optimizer.detail.table.avg"),
            _tr("room_optimizer.detail.table.risk"),
            _tr("room_optimizer.detail.table.rank"),
            _tr("room_optimizer.detail.table.mutations", default="Mutations"),
        ])
        self._excluded_table.setHorizontalHeaderLabels([
            _tr("room_optimizer.detail.excluded.cat"), "STR", "DEX", "CON", "INT", "SPD", "CHA", "LCK",
            _tr("room_optimizer.detail.excluded.sum"),
            _tr("room_optimizer.detail.excluded.agg"),
            _tr("room_optimizer.detail.excluded.lib"),
            _tr("room_optimizer.detail.excluded.inbred"),
        ])

    def set_navigate_to_cat_callback(self, callback):
        self._navigate_to_cat_callback = callback

    def set_navigate_to_pair_callback(self, callback):
        self._navigate_to_pair_callback = callback

    def set_mode_profiles(self, mode_profiles: dict[str, dict]):
        self._mode_profiles = _normalize_mutation_mode_profiles(mode_profiles)
        if self._current_data:
            self.show_room(self._current_data)

    def _on_pair_cell_clicked(self, item):
        """Handle clicks on cat names to navigate to the cat in the main view."""
        col = self._pairs_table.column(item)
        # Only handle clicks on Cat A (column 1) or Cat B (column 2)
        if col not in (1, 2):
            return

        cat_name = item.text().replace(" \u2665", "")
        if not cat_name or not self._navigate_to_cat_callback:
            return

        # Call the navigate callback with the cat name
        self._navigate_to_cat_callback(cat_name)

    def _on_toggle_best_pairs(self):
        """Re-render pairs table based on toggle state."""
        checked = self._best_pairs_btn.isChecked()
        self._best_pairs_btn.setText(
            _tr("room_optimizer.detail.toggle.best_pairs")
            if checked
            else _tr("room_optimizer.detail.toggle.all_pairs")
        )
        if self._current_data:
            self.show_room(self._current_data)

    @staticmethod
    def _apply_best_pairs_filter(pairs: list[dict]) -> list[dict]:
        """Greedy non-overlapping pair selection. Lover pairs take priority."""
        # Sort lover pairs first so they get picked before rank-based pairs
        sorted_pairs = sorted(enumerate(pairs), key=lambda ip: (not ip[1].get("is_lovers"), ip[0]))
        sorted_pairs = [p for _, p in sorted_pairs]
        used = set()
        result = []
        for pair in sorted_pairs:
            a, b = pair["cat_a"], pair["cat_b"]
            if a not in used and b not in used:
                result.append(pair)
                used.add(a)
                used.add(b)
        # Re-sort by original rank for display
        result.sort(key=lambda p: p.get("_original_rank", 0))
        return result

    @staticmethod
    def _range_background(lo: int, hi: int) -> QColor:
        base = STAT_COLORS.get(max(lo, hi), QColor(100, 100, 115))
        if lo != hi:
            return QColor(
                min(255, int(base.red() * 0.55) + 22),
                min(255, int(base.green() * 0.55) + 22),
                min(255, int(base.blue() * 0.55) + 22),
            )
        return QColor(
            min(255, int(base.red() * 0.7) + 18),
            min(255, int(base.green() * 0.7) + 18),
            min(255, int(base.blue() * 0.7) + 18),
        )

    @staticmethod
    def _pair_color(room_order: int) -> QColor:
        rank = max(1, int(room_order or 1))
        return PAIR_COLORS[(rank - 1) % len(PAIR_COLORS)]

    @staticmethod
    def _pair_tint(color: QColor, strength: float = 0.28, lift: int = 18) -> QColor:
        return QColor(
            min(255, int(color.red() * strength) + lift),
            min(255, int(color.green() * strength) + lift),
            min(255, int(color.blue() * strength) + lift),
        )

    def show_room(self, data: Optional[dict]):
        if not data:
            self._summary.setText(_tr("room_optimizer.detail.summary.select_room"))
            self._summary.setToolTip("")
            self._pairs_table.setRowCount(0)
            self._pairs_table.show()
            self._excluded_table.hide()
            return

        self._current_data = data

        room = data.get("room", _tr("common.unknown", default="Unknown"))
        cats = data.get("cats", [])
        cat_keys = list(data.get("cat_keys", []) or [])
        total_pairs = int(data.get("total_pairs", 0))
        avg_stats = float(data.get("avg_stats", 0))
        avg_risk = float(data.get("avg_risk", 0))
        pairs = data.get("pairs", [])
        excluded_cats = data.get("excluded_cats", [])
        excluded_cat_rows = data.get("excluded_cat_rows", [])
        room_mode = str(data.get("room_mode") or ("fallback" if data.get("is_fallback") else "best_pairs")).strip().lower()
        room_traits = list((self._mode_profiles.get(room_mode, {}) or {}).get("traits", []) or [])
        room_cat_lookup = {}
        for name, key in zip(cats, cat_keys):
            try:
                room_cat_lookup[str(name).replace(" \u2665", "").split(" (", 1)[0]] = int(key)
            except (TypeError, ValueError):
                continue

        def _pair_keys(pair: dict) -> tuple[Optional[int], Optional[int]]:
            a_key = pair.get("cat_a_db_key")
            b_key = pair.get("cat_b_db_key")
            if a_key is not None and b_key is not None:
                try:
                    return int(a_key), int(b_key)
                except (TypeError, ValueError):
                    pass
            a_name = str(pair.get("cat_a", "")).replace(" \u2665", "").split(" (", 1)[0]
            b_name = str(pair.get("cat_b", "")).replace(" \u2665", "").split(" (", 1)[0]
            return room_cat_lookup.get(a_name), room_cat_lookup.get(b_name)

        if room == "Excluded":
            self._pairs_table.hide()
            self._excluded_table.show()
            self._summary.setText(
                _tr("room_optimizer.detail.summary.excluded", count=len(excluded_cat_rows))
            )
            self._summary.setToolTip(_tr("room_optimizer.detail.summary.excluded_tooltip"))
            self._excluded_table.setRowCount(len(excluded_cat_rows))
            for row_idx, cat_row in enumerate(excluded_cat_rows):
                name_item = QTableWidgetItem(cat_row["name"])
                icon = _make_tag_icon(cat_row.get("tags", []))
                if not icon.isNull():
                    name_item.setIcon(icon)
                self._excluded_table.setItem(row_idx, 0, name_item)
                for stat_col, stat in enumerate(STAT_NAMES, start=1):
                    value = int(cat_row["stats"].get(stat, 0))
                    item = QTableWidgetItem(str(value))
                    item.setTextAlignment(Qt.AlignCenter)
                    item.setBackground(QBrush(STAT_COLORS.get(value, QColor(100, 100, 115))))
                    self._excluded_table.setItem(row_idx, stat_col, item)
                sum_item = QTableWidgetItem(str(int(cat_row["sum"])))
                sum_item.setTextAlignment(Qt.AlignCenter)
                self._excluded_table.setItem(row_idx, 8, sum_item)
                for trait_col, trait_key in enumerate(("aggression", "libido", "inbredness"), start=9):
                    trait_text = cat_row["traits"][trait_key]
                    trait_display = trait_text.replace("average", "avg")
                    trait_item = QTableWidgetItem(trait_display)
                    trait_item.setTextAlignment(Qt.AlignCenter)
                    trait_item.setBackground(QBrush(_trait_level_color(trait_text)))
                    self._excluded_table.setItem(row_idx, trait_col, trait_item)
            return

        self._pairs_table.show()
        self._excluded_table.hide()

        def _compact_names(names: list[str], limit: int = 8) -> str:
            if len(names) <= limit:
                return ", ".join(names)
            shown = ", ".join(names[:limit])
            return f"{shown}, ... (+{len(names) - limit} more)"

        cats_text = _compact_names(cats)
        self._summary.setText(
            _tr(
                "room_optimizer.detail.summary.room",
                room=room,
                pairs=total_pairs,
                avg=f"{avg_stats:.1f}",
                risk=f"{avg_risk:.0f}",
            )
        )
        self._summary.setToolTip(
            _tr("room_optimizer.detail.summary.cats", cats=", ".join(cats)) if cats else ""
        )

        # Preserve original rank before filtering
        for i, pair in enumerate(pairs, 1):
            pair["_original_rank"] = i

        # Apply best pairs filter if enabled
        if self._best_pairs_btn.isChecked():
            pairs = self._apply_best_pairs_filter(pairs)

        self._pairs_table.setRowCount(len(pairs))
        for i, pair in enumerate(pairs, 1):
            jump_btn = QToolButton()
            jump_btn.setText("↗")
            jump_btn.setCursor(Qt.PointingHandCursor)
            jump_btn.setToolTip(_tr(
                "room_optimizer.detail.button.jump_to_pair",
                default="Show these two cats in the Alive Cats view",
            ))
            jump_btn.setAutoRaise(True)
            jump_btn.setFixedSize(24, 22)
            jump_btn.setStyleSheet(
                "QToolButton { background:#1a1a32; color:#aaddff; border:1px solid #2a2a4a;"
                " border-radius:4px; font-size:12px; font-weight:bold; }"
                "QToolButton:hover { background:#252545; }"
                "QToolButton:pressed { background:#10101f; }"
            )
            a_key, b_key = _pair_keys(pair)
            if self._navigate_to_pair_callback and a_key is not None and b_key is not None:
                jump_btn.clicked.connect(lambda checked=False, ak=a_key, bk=b_key: self._navigate_to_pair_callback(ak, bk))
            else:
                jump_btn.setEnabled(False)
            self._pairs_table.setCellWidget(i - 1, 0, jump_btn)

            # Cat A and B items with hyperlink styling
            cat_a_text = pair['cat_a']
            cat_b_text = pair['cat_b']
            if pair.get("cat_a_has_lover"):
                cat_a_text += " \u2665"
            if pair.get("cat_b_has_lover"):
                cat_b_text += " \u2665"
            cat_a_item = QTableWidgetItem(cat_a_text)
            cat_b_item = QTableWidgetItem(cat_b_text)
            # Style as hyperlinks
            hyperlink_color = QColor(0x5b9bd5)  # Blue
            cat_a_item.setForeground(QBrush(hyperlink_color))
            cat_b_item.setForeground(QBrush(hyperlink_color))
            font = cat_a_item.font()
            font.setUnderline(True)
            cat_a_item.setFont(font)
            cat_b_item.setFont(font)
            cat_a_item.setToolTip(_tr("room_optimizer.locator.tooltip.jump_to_cat"))
            cat_b_item.setToolTip(_tr("room_optimizer.locator.tooltip.jump_to_cat"))
            sum_lo, sum_hi = pair.get("sum_range", (0, 0))
            sum_item = QTableWidgetItem(f"{sum_lo}-{sum_hi}")
            sum_item.setToolTip(
                _tr("room_optimizer.detail.tooltip.sum_range", lo=sum_lo, hi=sum_hi)
            )
            avg_item = QTableWidgetItem(f"{pair['avg_stats']:.1f}")
            stat_ranges = pair.get("stat_ranges", {})
            stat_items = []
            for stat in STAT_NAMES:
                lo, hi = stat_ranges.get(stat, (0, 0))
                item = QTableWidgetItem(f"{lo}-{hi}")
                item.setToolTip(_tr("room_optimizer.detail.tooltip.stat_range", stat=stat.upper(), lo=lo, hi=hi))
                item.setBackground(QBrush(self._range_background(lo, hi)))
                stat_items.append(item)
            risk_item = QTableWidgetItem(f"{pair['risk']:.0f}%")
            rank_item = QTableWidgetItem(str(pair.get("_original_rank", i)))

            for item in stat_items:
                item.setTextAlignment(Qt.AlignCenter)
            sum_item.setTextAlignment(Qt.AlignCenter)
            avg_item.setTextAlignment(Qt.AlignCenter)
            risk_item.setTextAlignment(Qt.AlignCenter)
            rank_item.setTextAlignment(Qt.AlignCenter)
            sum_item.setBackground(QBrush(self._range_background(sum_lo // len(STAT_NAMES), sum_hi // len(STAT_NAMES))))
            avg_item.setBackground(QBrush(self._range_background(int(pair['avg_stats']), int(pair['avg_stats']))))

            risk = float(pair["risk"])
            if risk >= 50:
                risk_item.setForeground(QBrush(QColor(217, 119, 119)))
            elif risk >= 20:
                risk_item.setForeground(QBrush(QColor(216, 181, 106)))
            else:
                risk_item.setForeground(QBrush(QColor(98, 194, 135)))

            self._pairs_table.setItem(i - 1, 1, cat_a_item)
            self._pairs_table.setItem(i - 1, 2, cat_b_item)
            # Lovers indicator column
            lover_item = QTableWidgetItem("\u2665" if pair.get("is_lovers") else "")
            lover_item.setTextAlignment(Qt.AlignCenter)
            if pair.get("is_lovers"):
                lover_item.setForeground(QBrush(QColor(220, 100, 120)))
                lover_item.setToolTip("Mutual lovers")
            self._pairs_table.setItem(i - 1, 3, lover_item)
            for j, item in enumerate(stat_items, 4):
                self._pairs_table.setItem(i - 1, j, item)
            self._pairs_table.setItem(i - 1, 11, sum_item)
            self._pairs_table.setItem(i - 1, 12, avg_item)
            self._pairs_table.setItem(i - 1, 13, risk_item)
            self._pairs_table.setItem(i - 1, 14, rank_item)

            mutations = pair.get("mutations") or []
            if mutations:
                shown = [
                    f"{_planner_trait_name_html(name, _planner_trait_weight(room_traits, display=name))} "
                    f"<span style=\"color:#9aa6ba;\">{prob * 100:.0f}%</span>"
                    for name, prob in mutations[:4]
                ]
                cell_text = ", ".join(shown)
                if len(mutations) > 4:
                    cell_text += f" <span style=\"color:#7d8bb0;\">(+{len(mutations) - 4})</span>"
                tooltip_lines = [f"{name}: {prob * 100:.0f}%" for name, prob in mutations]
                mut_label = QLabel(cell_text)
                mut_label.setTextFormat(Qt.RichText)
                mut_label.setWordWrap(True)
                mut_label.setStyleSheet("background:transparent; color:#ddd; padding:2px 4px;")
                mut_label.setToolTip("\n".join(tooltip_lines))
            else:
                mut_label = QLabel("—")
                mut_label.setStyleSheet("background:transparent; color:#777; padding:2px 4px;")
            self._pairs_table.setCellWidget(i - 1, 15, mut_label)
        self._pairs_table.resizeRowsToContents()
