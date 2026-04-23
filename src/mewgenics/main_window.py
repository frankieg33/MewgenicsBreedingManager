"""MainWindow: primary application window for Mewgenics Breeding Manager."""
import re
import csv
import os
from pathlib import Path
from typing import Optional

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QTableView, QPushButton, QLabel, QFileDialog, QHeaderView,
    QAbstractItemView, QSplitter, QDialog, QScrollArea,
    QLineEdit,
    QMessageBox, QProgressBar, QMenu,
)
from PySide6.QtCore import (
    Qt, QEvent, QModelIndex, QItemSelection, QItemSelectionModel,
    QFileSystemWatcher, QThread, QTimer, QSize, QByteArray,
)
from PySide6.QtGui import (
    QColor, QBrush, QAction, QActionGroup, QFont, QKeySequence,
    QPainter, QPixmap, QIcon,
)

from save_parser import (
    Cat, FurnitureDefinition, FurnitureRoomSummary,
    build_furniture_room_summaries,
    STAT_NAMES, _is_hater_pair, ROOM_KEYS,
)

from mewgenics.constants import (
    COL_NAME, COL_TAGS, COL_AGE, COL_GEN, COL_ROOM, COL_STAT, COL_ADV, COL_BL, COL_MB, COL_PIN,
    STAT_COLS, COL_SUM, COL_AGG, COL_LIB, COL_INBRD, COL_SEXUALITY,
    COL_RELNS, COL_REL, COL_ABIL, COL_MUTS, COL_GEN_DEPTH, COL_SRC,
    _W_STATUS, _W_STAT, _W_GEN, _W_RELNS, _W_REL, _W_TRAIT, _W_TRAIT_NARROW,
    _ZOOM_MIN, _ZOOM_MAX, _ZOOM_STEP,
    _NAME_STYLE, _META_STYLE,
)
from mewgenics.utils.paths import (
    APPDATA_SAVE_DIR, APPDATA_CONFIG_DIR, APP_VERSION, _breeding_cache_path,
)
from mewgenics.utils.config import (
    _save_root_dir, _saved_default_save, _set_default_save,
    _save_current_view, _load_current_view,
    _set_save_dir, find_save_files,
    _saved_room_optimizer_auto_recalc, _set_room_optimizer_auto_recalc,
    _saved_manual_scoring_auto_calc, _set_manual_scoring_auto_calc,
    _save_splitter_state, _bind_splitter_persistence,
    _saved_zoom_percent, _set_zoom_percent,
    _saved_font_size_offset, _set_font_size_offset_config,
    _saved_last_seen_version, _set_last_seen_version,
    _saved_accessibility_preset, _set_accessibility_preset,
    _saved_total_stats_display, _set_total_stats_display,
    _saved_stat_icon_mode, _set_stat_icon_mode,
    _saved_roster_visual_mode, _set_roster_visual_mode,
    _gpak_search_start_dir,
    _candidate_gpak_paths,
    _set_last_save,
    _save_window_geometry, _load_window_geometry,
)
from mewgenics.utils.localization import (
    _SUPPORTED_LANGUAGES, ROOM_DISPLAY, COLUMNS,
    _saved_language, _set_saved_language,
    _set_current_language, _current_language, _tr,
    _language_label, _font_size_offset_label,
    _refresh_localized_constants,
)
from mewgenics.utils.tags import (
    _TAG_DEFS, _TAG_ICON_CACHE, _TAG_PIX_CACHE, _cat_tags,
    _make_tag_icon,
)
from mewgenics.utils.thresholds import (
    _load_threshold_preferences, _save_threshold_preferences,
    _apply_threshold_preferences, _current_threshold_summary,
    _set_donation_planner_traits,
)
from mewgenics.utils.optimizer_settings import (
    _OPTIMIZER_SEARCH_DEFAULTS,
    _load_optimizer_search_settings, _save_optimizer_search_settings,
    _save_room_priority_config,
)
from mewgenics.utils.calibration import (
    _trait_label_from_value, _apply_calibration,
)
from mewgenics.utils.cat_persistence import (
    _save_blacklist, _save_must_breed, _save_pinned, _save_tags,
    _save_not_adventured,
)
from mewgenics.utils.planner_state import _planner_import_traits_summary
from mewgenics.utils.cat_analysis import (
    _is_exceptional_breeder, _is_donation_candidate,
)
from mewgenics.utils.game_data import (
    _set_gpak_path, _GPAK_PATH, _FURNITURE_DATA,
)
from mewgenics.utils.styling import (
    _ACCESSIBILITY_MIN_FONT_PX, _ACCESSIBILITY_MIN_FONT_PT,
    _enforce_min_font_in_widget_tree, _apply_font_offset_to_tree,
    _hsep, _sidebar_btn, _high_contrast_stylesheet,
)
from mewgenics.models.breeding_cache import (
    BreedingCache, BreedingCacheWorker,
    _breeding_cache_fingerprint, _breeding_save_signature,
)
from mewgenics.models.cat_table_model import (
    TagStripDelegate, CatTableModel, VisualIconDelegate,
    clear_cat_sprite_cache, clear_mutation_part_cache,
)
from mewgenics.models.room_filter_model import RoomFilterModel
from mewgenics.workers.save_loader import SaveLoadWorker
from mewgenics.workers.room_refresh import QuickRoomRefreshWorker

from mewgenics.dialogs import (
    TagManagerDialog,
    ThresholdPreferencesDialog,
    SharedOptimizerSearchSettingsDialog,
    SaveSelectorDialog,
    AboutDialog,
    OnboardingDialog,
    WhatsNewDialog,
)
from mewgenics.panels.cat_detail import CatDetailPanel

from mewgenics.views.family_tree import FamilyTreeBrowserView
from mewgenics.views.safe_breeding import SafeBreedingView
from mewgenics.views.breeding_partners import BreedingPartnersView
from mewgenics.views.room_optimizer import RoomOptimizerView
from mewgenics.views.perfect_planner import PerfectCatPlannerView
from mewgenics.views.calibration import CalibrationView
from mewgenics.views.mutation_planner import MutationDisorderPlannerView
from mewgenics.views.furniture import FurnitureView
from mewgenics.views.manual_scoring import ManualScoringView
from mewgenics.utils.trait_ratings import TraitRatings

from breed_priority import BreedPriorityView
from mewgenics.utils.abilities import _mutation_display_name, _ability_tip
from mewgenics.utils.paths import _scoring_path


class MainWindow(QMainWindow):
    # Max consecutive self-heal retries after a transient save-load failure.
    # After this, we stop and wait for a fresh fileChanged event (or manual
    # reload) rather than spinning on a permanently-broken save.
    _SAVE_LOAD_RETRY_CAP = 3

    @staticmethod
    def _set_bulk_toggle_label(btn: QPushButton, label: str, enabled: bool):
        btn.setText(_tr("bulk.label_template", label=label, state=_tr("common.on" if enabled else "common.off")))

    @staticmethod
    def _style_room_action_button(btn: QPushButton, background: str, border: str, hover_background: str, width: int = 110):
        btn.setCheckable(False)
        btn.setMinimumWidth(width)
        btn.setStyleSheet(
            "QPushButton { "
            f"background:{background}; color:#f1f1f1; border:1px solid {border}; "
            "border-radius:4px; padding:4px 10px; font-size:11px; font-weight:bold; }"
            f"QPushButton:hover {{ background:{hover_background}; }}"
            "QPushButton:pressed { background:#1a1a1a; }"
        )

    def _set_room_action_button_texts(self):
        self._room_must_breed_btn.setText(_tr("bulk.toggle_must_breed"))
        self._room_must_breed_btn.setToolTip(_tr("bulk.toggle_must_breed.tooltip"))
        self._room_breeding_block_btn.setText(_tr("bulk.toggle_breeding_block"))
        self._room_breeding_block_btn.setToolTip(_tr("bulk.toggle_breeding_block.tooltip"))
        self._room_pin_btn.setText(_tr("bulk.toggle_pin", default="Toggle Pin"))
        self._room_pin_btn.setToolTip(_tr("bulk.toggle_pin.tooltip", default="Toggle pin for selected cats"))

    def _room_view_target_cats(self, room_key=None) -> list[Cat]:
        if room_key in (None, "__all__"):
            return self._selected_cats()
        return self._visible_filtered_cats()

    def _active_room_key(self):
        if self._active_btn is not None:
            for key, btn in self._room_btns.items():
                if btn is self._active_btn:
                    return key
        return None

    def _toggle_room_view_boolean(self, attr: str, room_key=None) -> int:
        cats = self._room_view_target_cats(room_key)
        mw_status = self.statusBar()
        if not cats:
            if room_key in (None, "__all__"):
                mw_status.showMessage("Select cats first, then click a room action.")
            else:
                mw_status.showMessage("No cats in the current room view needed a change.")
            return 0

        current = [bool(getattr(cat, attr, False)) for cat in cats]
        target_state = not all(current)
        changed = 0
        for cat in cats:
            if attr == "is_pinned":
                if cat.is_pinned == target_state:
                    continue
                cat.is_pinned = target_state
                changed += 1
                continue
            if attr == "must_breed":
                if cat.must_breed == target_state:
                    continue
                cat.must_breed = target_state
                if target_state:
                    cat.is_blacklisted = False
                changed += 1
                continue
            if attr == "is_blacklisted":
                if cat.is_blacklisted == target_state and (not target_state or not cat.must_breed):
                    continue
                cat.is_blacklisted = target_state
                if target_state:
                    cat.must_breed = False
                changed += 1

        if changed == 0:
            mw_status.showMessage("No cats in view needed a change.")
            return 0
        self._emit_bulk_toggle_refresh()
        return changed

    def _toggle_room_must_breed(self, room_key=None):
        changed = self._toggle_room_view_boolean("must_breed", room_key)
        if changed:
            self.statusBar().showMessage(_tr("bulk.status.toggled_must_breed", default="Toggled must breed for {count} selected cats", count=changed))

    def _toggle_room_breeding_block(self, room_key=None):
        changed = self._toggle_room_view_boolean("is_blacklisted", room_key)
        if changed:
            self.statusBar().showMessage(_tr("bulk.status.toggled_breeding_block", default="Toggled breeding block for {count} selected cats", count=changed))

    def _toggle_room_pin(self, room_key=None):
        changed = self._toggle_room_view_boolean("is_pinned", room_key)
        if changed:
            self.statusBar().showMessage(_tr("bulk.status.toggled_pin", default="Toggled pin for {count} selected cats", count=changed))

    def __init__(self, initial_save: Optional[str] = None, use_saved_default: bool = True):
        super().__init__()
        _set_current_language(_saved_language())
        _refresh_localized_constants()
        self.setWindowTitle(_tr("app.title"))
        self.resize(1440, 900)
        saved_geometry = _load_window_geometry()
        if saved_geometry:
            try:
                self.restoreGeometry(QByteArray.fromBase64(saved_geometry.encode("ascii")))
            except Exception:
                pass

        self._current_save = None
        self._cats: list[Cat] = []
        self._furniture = []
        self._furniture_by_room = {}
        self._room_summaries: dict[str, FurnitureRoomSummary] = {}
        self._available_house_rooms: list[str] = list(ROOM_KEYS)
        self._furniture_data: dict[str, FurnitureDefinition] = dict(_FURNITURE_DATA)
        self._room_btns: dict = {}
        self._active_btn = None
        # Navigation history for mouse back/forward buttons. Each entry is
        # a dict describing the view (+ table filter + selection). `_back_stack`
        # holds snapshots taken BEFORE a navigation action; `_forward_stack`
        # is rebuilt when the user navigates anywhere new. `_nav_suppress`
        # blocks recursive pushes while we restore a state.
        self._nav_back_stack: list[dict] = []
        self._nav_forward_stack: list[dict] = []
        self._nav_suppress: bool = False
        self._show_lineage: bool = False
        self._pair_detail_override: bool = False
        self._pedigree_coi_memos: dict[tuple[int, int], float] = {}
        self._tree_view: Optional[FamilyTreeBrowserView] = None
        self._safe_breeding_view: Optional[SafeBreedingView] = None
        self._breeding_partners_view: Optional[BreedingPartnersView] = None
        self._room_optimizer_view: Optional[RoomOptimizerView] = None
        self._perfect_planner_view: Optional[PerfectCatPlannerView] = None
        self._calibration_view: Optional[CalibrationView] = None
        self._mutation_planner_view: Optional['MutationDisorderPlannerView'] = None
        self._furniture_view: Optional[FurnitureView] = None
        self._manual_scoring_view: Optional[ManualScoringView] = None
        self._breed_priority_view: Optional[BreedPriorityView] = None
        self._trait_ratings: Optional[TraitRatings] = None
        self._cats_generation: int = 0
        self._view_generation: dict[str, int] = {}
        self._breeding_cache: Optional[BreedingCache] = None
        self._cache_worker: Optional[BreedingCacheWorker] = None
        self._save_load_worker: Optional[SaveLoadWorker] = None
        # Consecutive self-heal attempts triggered by `_on_save_load_failed`.
        # Capped so a permanently broken save can't loop forever.
        self._save_load_retries: int = 0
        self._quick_refresh_worker: Optional[QuickRoomRefreshWorker] = None
        # Stale-signal discriminator for `_on_room_patch`.  See
        # `_start_quick_room_refresh` for why `quit()/wait()` alone
        # cannot prevent a previous worker's queued signal from
        # clobbering freshly-loaded state.
        self._quick_refresh_generation: int = 0
        self._prev_parent_keys: dict[int, tuple] = {}
        self._accessible_cat_keys: set[int] = set()
        self._fight_club_layout_active: bool = False
        self._fight_club_prev_total_stats: bool = _saved_total_stats_display()
        self._fight_club_hidden_state: dict[int, bool] = {}
        self._zoom_percent: int = _saved_zoom_percent()
        self._font_size_offset: int = _saved_font_size_offset()   # pt offset applied on top of zoom
        self._accessibility_preset: str = _saved_accessibility_preset()
        self._safe_breeding_quality_mode: bool = True
        self._startup_dialogs_shown: bool = False
        self._default_app_stylesheet: str = QApplication.instance().styleSheet()
        self._base_font: QFont = QApplication.instance().font()
        self._base_sidebar_width = 190
        self._base_header_height = 46
        self._base_search_width = 180
        self._base_col_widths = {
            COL_TAGS: 122,
            COL_NAME: 160,
            COL_GEN: _W_GEN,
            COL_STAT: _W_STATUS,
            COL_ADV: 72,
            COL_BL: 34,
            COL_MB: 34,
            COL_PIN: 34,
            COL_SUM: 38,
            COL_ABIL: 180,
            COL_MUTS: 155,
            COL_RELNS: _W_RELNS,
            COL_REL: _W_REL,
            COL_AGE: 34,
            COL_AGG: _W_TRAIT_NARROW,
            COL_LIB: _W_TRAIT_NARROW,
            COL_INBRD: _W_TRAIT_NARROW,
            COL_SEXUALITY: _W_TRAIT,
            **{c: _W_STAT for c in STAT_COLS},
        }

        self._build_ui()
        self._build_menu()
        # Route mouse back/forward button presses through our navigation
        # history. Installed at app level so it catches clicks on any
        # child widget regardless of focus.
        QApplication.instance().installEventFilter(self)
        self._source_model.set_show_total_stats(_saved_total_stats_display())
        self._source_model.set_show_stat_icons(_saved_stat_icon_mode())
        self._apply_accessibility_style(self._accessibility_preset)
        self._apply_zoom()
        # Restore roster visual-mode choice. Must happen after the table
        # has been built and delegates installed.
        self._apply_roster_visual_mode(_saved_roster_visual_mode())

        # Progress bar for breeding cache computation
        self._cache_progress = QProgressBar()
        self._cache_progress.setFixedWidth(200)
        self._cache_progress.setFixedHeight(16)
        self._cache_progress.setTextVisible(True)
        self._cache_progress.setFormat(_tr("loading.cache.computing"))
        self._cache_progress.setStyleSheet(
            "QProgressBar { background:#1a1a32; border:1px solid #2a2a4a; border-radius:4px; color:#aaa; font-size:10px; }"
            "QProgressBar::chunk { background:#3f8f72; border-radius:3px; }"
        )
        self._cache_progress.hide()
        self.statusBar().addPermanentWidget(self._cache_progress)

        self._watcher = QFileSystemWatcher(self)
        self._watcher.fileChanged.connect(self._on_file_changed_raw)
        # Debounce file-watcher events.  The game often writes the save
        # in a burst (multiple fileChanged events within a few ms), and
        # each one used to start its own QuickRoomRefreshWorker, racing
        # with the previous one still running — a major contributor to
        # the ~10% crash rate reported against v5.4.8.  Coalesce bursts
        # into a single refresh 250 ms after the last event.
        self._file_change_timer = QTimer(self)
        self._file_change_timer.setSingleShot(True)
        self._file_change_timer.setInterval(250)
        self._file_change_timer.timeout.connect(self._on_file_changed_debounced)
        self._pending_changed_path: Optional[str] = None

        # Use initial_save if provided; otherwise only auto-load the saved default when allowed.
        save_to_load = initial_save if initial_save else (_saved_default_save() if use_saved_default else None)
        if save_to_load:
            # Defer load_save to after the window is shown so the UI appears instantly.
            QTimer.singleShot(0, lambda: self.load_save(save_to_load))

    # ── Menu ──────────────────────────────────────────────────────────────

    def _build_menu(self):
        self.menuBar().clear()
        fm = self.menuBar().addMenu(_tr("menu.file"))

        oa = QAction(_tr("menu.file.open_save"), self)
        oa.setShortcut("Ctrl+O")
        oa.triggered.connect(self._open_file)
        fm.addAction(oa)

        # Recent Saves submenu
        self._recent_saves_menu = fm.addMenu(_tr("menu.file.recent_saves"))
        self._recent_save_actions: list[QAction] = []
        self._refresh_recent_save_actions()

        fm.addSeparator()

        # Default Save submenu
        self._default_save_menu = fm.addMenu(_tr("menu.file.default_save"))
        self._set_default_save_action = QAction(_tr("menu.file.default_save.set_current"), self)
        self._set_default_save_action.triggered.connect(self._set_current_as_default)
        self._set_default_save_action.setEnabled(False)
        self._default_save_menu.addAction(self._set_default_save_action)

        self._clear_default_save_action = QAction(_tr("menu.file.default_save.clear"), self)
        self._clear_default_save_action.triggered.connect(self._clear_default_save)
        self._clear_default_save_action.setEnabled(False)
        self._default_save_menu.addAction(self._clear_default_save_action)

        fm.addSeparator()

        ra = QAction(_tr("menu.file.reload"), self)
        ra.setShortcut("F5")
        ra.triggered.connect(self._reload)
        fm.addAction(ra)

        recalc = QAction(_tr("menu.file.recalculate_breeding_data"), self)
        recalc.setShortcut("Ctrl+F5")
        recalc.setToolTip(_tr("menu.file.recalculate_breeding_data.tooltip"))
        recalc.triggered.connect(lambda: self._start_breeding_cache(self._cats, force_full=True) if self._cats else None)
        fm.addAction(recalc)

        clear_cache = QAction(_tr("menu.file.clear_breeding_cache"), self)
        clear_cache.setToolTip(_tr("menu.file.clear_breeding_cache.tooltip"))
        clear_cache.triggered.connect(self._clear_breeding_cache)
        fm.addAction(clear_cache)

        fm.addSeparator()

        export_action = QAction(_tr("menu.file.export_cats", default="Export Cats…"), self)
        export_action.setShortcut("Ctrl+E")
        export_action.triggered.connect(self._export_cats)
        fm.addAction(export_action)

        fm.addSeparator()

        exit_action = QAction(_tr("menu.file.exit"), self)
        exit_action.setShortcut("Alt+F4")
        exit_action.triggered.connect(self.close)
        fm.addAction(exit_action)

        # ── View menu ─────────────────────────────────────────────────────
        vm = self.menuBar().addMenu(_tr("menu.view", default="View"))

        self._lineage_action = QAction(_tr("menu.settings.show_lineage"), self)
        self._lineage_action.setCheckable(True)
        self._lineage_action.setChecked(self._show_lineage)
        self._lineage_action.triggered.connect(self._toggle_lineage)
        vm.addAction(self._lineage_action)

        self._room_optimizer_auto_recalc_action = QAction(_tr("menu.settings.room_optimizer_auto_recalc", default="Auto Recalculate Room Optimizer"), self)
        self._room_optimizer_auto_recalc_action.setCheckable(True)
        self._room_optimizer_auto_recalc_action.setChecked(_saved_room_optimizer_auto_recalc())
        self._room_optimizer_auto_recalc_action.toggled.connect(self._toggle_room_optimizer_auto_recalc)
        vm.addAction(self._room_optimizer_auto_recalc_action)

        self._manual_scoring_auto_calc_action = QAction(_tr("menu.settings.manual_scoring_auto_calc", default="Auto Recalculate Manual Scoring"), self)
        self._manual_scoring_auto_calc_action.setCheckable(True)
        self._manual_scoring_auto_calc_action.setChecked(_saved_manual_scoring_auto_calc())
        self._manual_scoring_auto_calc_action.toggled.connect(self._toggle_manual_scoring_auto_calc)
        vm.addAction(self._manual_scoring_auto_calc_action)

        vm.addSeparator()

        self._roster_display_menu = vm.addMenu(_tr("menu.settings.roster_display", default="Roster Display"))
        self._total_stats_action = QAction(_tr("menu.settings.show_total_stats", default="Show Total Stats"), self)
        self._total_stats_action.setCheckable(True)
        self._total_stats_action.setShortcut("Ctrl+T")
        self._total_stats_action.setChecked(_saved_total_stats_display())
        self._total_stats_action.triggered.connect(self._toggle_total_stats_display)
        self._roster_display_menu.addAction(self._total_stats_action)

        self._stat_icons_action = QAction(_tr("menu.settings.show_stat_icons", default="Stat Icons"), self)
        self._stat_icons_action.setCheckable(True)
        self._stat_icons_action.setChecked(_saved_stat_icon_mode())
        self._stat_icons_action.triggered.connect(self._toggle_stat_icon_mode)
        self._roster_display_menu.addAction(self._stat_icons_action)

        self._visual_mode_action = QAction(
            _tr("menu.settings.roster_visual_mode", default="Visual Mode (larger rows, sprites & icons)"),
            self,
        )
        self._visual_mode_action.setCheckable(True)
        self._visual_mode_action.setChecked(_saved_roster_visual_mode())
        self._visual_mode_action.triggered.connect(self._toggle_roster_visual_mode)
        self._roster_display_menu.addAction(self._visual_mode_action)

        vm.addSeparator()

        zoom_in = QAction(_tr("menu.settings.zoom_in"), self)
        zoom_in_keys = QKeySequence.keyBindings(QKeySequence.StandardKey.ZoomIn)
        if not zoom_in_keys:
            zoom_in_keys = []
        for seq in (QKeySequence("Ctrl+="), QKeySequence("Ctrl++")):
            if seq not in zoom_in_keys:
                zoom_in_keys.append(seq)
        zoom_in.setShortcuts(zoom_in_keys)
        zoom_in.triggered.connect(lambda: self._change_zoom(+1))
        vm.addAction(zoom_in)

        zoom_out = QAction(_tr("menu.settings.zoom_out"), self)
        zoom_out_keys = QKeySequence.keyBindings(QKeySequence.StandardKey.ZoomOut)
        if not zoom_out_keys:
            zoom_out_keys = []
        if QKeySequence("Ctrl+-") not in zoom_out_keys:
            zoom_out_keys.append(QKeySequence("Ctrl+-"))
        zoom_out.setShortcuts(zoom_out_keys)
        zoom_out.triggered.connect(lambda: self._change_zoom(-1))
        vm.addAction(zoom_out)

        zoom_reset = QAction(_tr("menu.settings.reset_zoom"), self)
        zoom_reset.setShortcut("Ctrl+0")
        zoom_reset.triggered.connect(self._reset_zoom)
        vm.addAction(zoom_reset)

        self._zoom_info_action = QAction("", self)
        self._zoom_info_action.setEnabled(False)
        vm.addAction(self._zoom_info_action)
        self._update_zoom_info_action()

        vm.addSeparator()

        fs_in = QAction(_tr("menu.settings.increase_font_size"), self)
        fs_in.setShortcut("Ctrl+]")
        fs_in.triggered.connect(lambda: self._change_font_size(+1))
        vm.addAction(fs_in)

        fs_out = QAction(_tr("menu.settings.decrease_font_size"), self)
        fs_out.setShortcut("Ctrl+[")
        fs_out.triggered.connect(lambda: self._change_font_size(-1))
        vm.addAction(fs_out)

        fs_reset = QAction(_tr("menu.settings.reset_font_size"), self)
        fs_reset.setShortcut("Ctrl+\\")
        fs_reset.triggered.connect(lambda: self._set_font_size_offset(0))
        vm.addAction(fs_reset)

        self._font_size_info_action = QAction("", self)
        self._font_size_info_action.setEnabled(False)
        vm.addAction(self._font_size_info_action)
        self._update_font_size_info_action()

        vm.addSeparator()

        self._accessibility_menu = vm.addMenu(_tr("menu.settings.accessibility", default="Accessibility"))
        self._accessibility_group = QActionGroup(self)
        self._accessibility_group.setExclusive(True)
        self._accessibility_actions: dict[str, QAction] = {}
        for preset in ("Default", "Comfort", "High Contrast", "Large Table"):
            action = QAction(preset, self)
            action.setCheckable(True)
            action.setChecked(preset == self._accessibility_preset)
            action.triggered.connect(lambda checked=False, name=preset: self._apply_accessibility_preset(name))
            self._accessibility_group.addAction(action)
            self._accessibility_menu.addAction(action)
            self._accessibility_actions[preset] = action

        vm.addSeparator()

        self._reset_ui_settings_action = QAction(_tr("menu.settings.reset_ui_defaults"), self)
        self._reset_ui_settings_action.triggered.connect(self._reset_ui_settings_to_defaults)
        vm.addAction(self._reset_ui_settings_action)

        # ── Settings menu ────────────────────────────────────────────────
        sm = self.menuBar().addMenu(_tr("menu.settings"))

        locations_action = QAction(_tr("menu.settings.locations"), self)
        locations_action.triggered.connect(self._open_locations_dialog)
        sm.addAction(locations_action)

        self._thresholds_action = QAction(_tr("menu.settings.thresholds", default="Donation / Exceptional Thresholds…"), self)
        self._thresholds_action.triggered.connect(self._open_threshold_preferences_dialog)
        sm.addAction(self._thresholds_action)

        self._optimizer_search_settings_action = QAction(
            _tr("menu.settings.optimizer_search_settings", default="Optimizer Search Settings…"),
            self,
        )
        self._optimizer_search_settings_action.triggered.connect(self._open_optimizer_search_settings_dialog)
        sm.addAction(self._optimizer_search_settings_action)

        sm.addSeparator()

        self._language_menu = sm.addMenu(_tr("language.menu"))
        self._language_group = QActionGroup(self)
        self._language_group.setExclusive(True)
        for language in _SUPPORTED_LANGUAGES:
            action = QAction(_language_label(language), self)
            action.setCheckable(True)
            action.setChecked(language == _current_language())
            action.triggered.connect(lambda checked=False, lang=language: self._change_language(lang))
            self._language_group.addAction(action)
            self._language_menu.addAction(action)

        hm = self.menuBar().addMenu(_tr("menu.help", default="Help"))
        self._getting_started_action = QAction(_tr("menu.help.getting_started", default="Getting Started"), self)
        self._getting_started_action.triggered.connect(self._show_onboarding_dialog)
        hm.addAction(self._getting_started_action)

        self._whats_new_action = QAction(_tr("menu.help.whats_new", default="What's New"), self)
        self._whats_new_action.triggered.connect(self._show_whats_new_dialog)
        hm.addAction(self._whats_new_action)

        hm.addSeparator()
        self._about_action = QAction(_tr("menu.help.about", default="About"), self)
        self._about_action.triggered.connect(self._show_about_dialog)
        hm.addAction(self._about_action)

    def _refresh_recent_save_actions(self):
        if not hasattr(self, "_recent_saves_menu"):
            return
        self._recent_saves_menu.clear()
        self._recent_save_actions = []

        saves = find_save_files()
        if not saves:
            action = QAction(_tr("menu.file.no_saves_found", path=_save_root_dir()), self)
            action.setEnabled(False)
            self._recent_saves_menu.addAction(action)
            self._recent_save_actions.append(action)
            return

        for path in saves[:10]:
            action = QAction(os.path.basename(path), self)
            action.setToolTip(path)
            action.triggered.connect(lambda _, p=path: self.load_save(p))
            self._recent_saves_menu.addAction(action)
            self._recent_save_actions.append(action)

    def _open_locations_dialog(self):
        dlg = QDialog(self)
        dlg.setWindowTitle(_tr("dialog.locations.title"))
        dlg.setModal(True)
        layout = QVBoxLayout(dlg)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        game_title = QLabel(_tr("dialog.locations.game_install"))
        game_title.setStyleSheet(_NAME_STYLE)
        game_path_label = QLabel()
        game_path_label.setWordWrap(True)
        game_path_label.setStyleSheet(_META_STYLE)

        save_title = QLabel(_tr("dialog.locations.save_root"))
        save_title.setStyleSheet(_NAME_STYLE)
        save_path_label = QLabel()
        save_path_label.setWordWrap(True)
        save_path_label.setStyleSheet(_META_STYLE)

        note_label = QLabel(_tr("dialog.locations.note", path=APPDATA_SAVE_DIR))
        note_label.setWordWrap(True)
        note_label.setStyleSheet(_META_STYLE)

        def _refresh_labels():
            game_path_label.setText(_GPAK_PATH or _tr("common.not_found"))
            save_path_label.setText(_save_root_dir())

        def _choose_game_dir():
            start_dir = os.path.dirname(_GPAK_PATH) if _GPAK_PATH else _gpak_search_start_dir()
            chosen_dir = QFileDialog.getExistingDirectory(
                dlg,
                _tr("dialog.locations.select_game_folder"),
                start_dir,
            )
            if not chosen_dir:
                return
            gpak_path = os.path.join(chosen_dir, "resources.gpak")
            if not os.path.exists(gpak_path):
                QMessageBox.warning(
                    dlg,
                    _tr("dialog.locations.resources_not_found.title"),
                    _tr("dialog.locations.resources_not_found.body"),
                )
                return
            _set_gpak_path(gpak_path)
            _refresh_labels()
            if self._current_save:
                self.load_save(self._current_save)
            self.statusBar().showMessage(_tr("status.using_game_data", path=gpak_path))

        def _choose_save_dir():
            chosen_dir = QFileDialog.getExistingDirectory(
                dlg,
                _tr("dialog.locations.select_save_root"),
                _save_root_dir(),
            )
            if not chosen_dir:
                return
            _set_save_dir(chosen_dir)
            _refresh_labels()
            self._refresh_recent_save_actions()
            self.statusBar().showMessage(_tr("status.using_save_root", path=chosen_dir))

        game_btn = QPushButton(_tr("dialog.locations.change_game_folder"))
        game_btn.clicked.connect(_choose_game_dir)
        save_btn = QPushButton(_tr("dialog.locations.change_save_root"))
        save_btn.clicked.connect(_choose_save_dir)

        layout.addWidget(game_title)
        layout.addWidget(game_path_label)
        layout.addWidget(game_btn)
        layout.addSpacing(8)
        layout.addWidget(save_title)
        layout.addWidget(save_path_label)
        layout.addWidget(save_btn)
        layout.addSpacing(8)
        layout.addWidget(note_label)

        close_btn = QPushButton(_tr("common.close"))
        close_btn.clicked.connect(dlg.accept)
        layout.addWidget(close_btn, alignment=Qt.AlignRight)

        _refresh_labels()
        dlg.resize(640, 260)
        dlg.exec()

    def _open_threshold_preferences_dialog(self):
        dlg = ThresholdPreferencesDialog(self, _load_threshold_preferences(), self._cats)
        if dlg.exec() != QDialog.Accepted:
            return
        prefs = dlg.preferences()
        _save_threshold_preferences(prefs)
        self._refresh_threshold_runtime(self._cats)
        room_key = None
        if self._active_btn is not None:
            for key, btn in self._room_btns.items():
                if btn is self._active_btn:
                    room_key = key
                    break
        self._refresh_threshold_sensitive_ui(room_key)
        self.statusBar().showMessage(
            _tr("status.thresholds_saved", default="Threshold preferences saved")
        )

    def _open_optimizer_search_settings_dialog(self):
        dlg = SharedOptimizerSearchSettingsDialog(self, _load_optimizer_search_settings())
        if dlg.exec() != QDialog.Accepted:
            return
        settings = dlg.preferences()
        _save_optimizer_search_settings(settings)
        self.statusBar().showMessage(
            _tr("status.optimizer_search_settings_saved", default="Optimizer search settings saved")
        )

    def _show_about_dialog(self):
        AboutDialog(self).exec()

    def _show_whats_new_dialog(self, mark_seen: bool = True):
        dlg = WhatsNewDialog(self, APP_VERSION)
        dlg.exec()
        if mark_seen:
            _set_last_seen_version(APP_VERSION)

    def _show_onboarding_dialog(self, mark_seen: bool = False):
        OnboardingDialog(self).exec()
        if mark_seen:
            _set_last_seen_version(APP_VERSION)

    def _maybe_show_startup_dialogs(self):
        if self._startup_dialogs_shown:
            return
        self._startup_dialogs_shown = True
        last_seen = _saved_last_seen_version()
        if not last_seen:
            self._show_onboarding_dialog(mark_seen=True)
            return
        if last_seen != APP_VERSION:
            self._show_whats_new_dialog(mark_seen=True)

    def _apply_accessibility_style(self, preset: str):
        app = QApplication.instance()
        if preset == "High Contrast":
            app.setStyleSheet(_high_contrast_stylesheet())
        else:
            app.setStyleSheet(self._default_app_stylesheet)

    def _refresh_accessibility_action_checks(self):
        for name, action in getattr(self, "_accessibility_actions", {}).items():
            action.blockSignals(True)
            try:
                action.setChecked(name == self._accessibility_preset)
            finally:
                action.blockSignals(False)

    def _apply_accessibility_preset(self, preset_name: str, persist: bool = True):
        presets = {
            "Default": {"zoom": 100, "font": 0},
            "Comfort": {"zoom": 110, "font": 2},
            "High Contrast": {"zoom": 100, "font": 1},
            "Large Table": {"zoom": 100, "font": 3},
        }
        preset_name = preset_name if preset_name in presets else "Default"
        self._accessibility_preset = preset_name
        self._apply_accessibility_style(preset_name)
        preset = presets[preset_name]
        if persist:
            _set_accessibility_preset(preset_name)
            self._set_zoom(preset["zoom"])
            self._set_font_size_offset(preset["font"])
        self._refresh_accessibility_action_checks()

    def _toggle_total_stats_display(self, checked: bool):
        enabled = bool(checked)
        _set_total_stats_display(enabled)
        if hasattr(self, "_source_model"):
            self._source_model.set_show_total_stats(enabled)
        self._update_header(self._current_room_key())
        self.statusBar().showMessage(
            _tr("status.total_stats_display", default="Roster total-stat display {state}", state=_tr("common.on", default="on") if enabled else _tr("common.off", default="off"))
        )

    def _toggle_stat_icon_mode(self, checked: bool):
        enabled = bool(checked)
        _set_stat_icon_mode(enabled)
        if hasattr(self, "_source_model"):
            self._source_model.set_show_stat_icons(enabled)
        if self._detail and self._detail.current_cats:
            self._detail.show_cats(self._detail.current_cats)
        self.statusBar().showMessage(
            _tr("status.stat_icons_display", default="Roster stat icons {state}", state=_tr("common.on", default="on") if enabled else _tr("common.off", default="off"))
        )

    # Row height (px) used when roster visual mode is on. Chosen to be
    # roughly 2.25x the default compact row height so sprites and ability
    # icons are large enough to read at a glance.
    _VISUAL_ROW_HEIGHT = 70
    _VISUAL_SPRITE_SIZE = 62
    _VISUAL_ABIL_COL_WIDTH = 360
    _VISUAL_MUTS_COL_WIDTH = 300
    _VISUAL_NAME_COL_WIDTH = 240

    def _toggle_roster_visual_mode(self, checked: bool):
        enabled = bool(checked)
        _set_roster_visual_mode(enabled)
        self._apply_roster_visual_mode(enabled)
        self.statusBar().showMessage(
            _tr(
                "status.roster_visual_mode",
                default="Roster visual mode {state}",
                state=_tr("common.on", default="on") if enabled else _tr("common.off", default="off"),
            )
        )

    def _apply_roster_visual_mode(self, enabled: bool):
        """Push visual-mode state into the model, row height, and columns."""
        if not hasattr(self, "_source_model"):
            return
        self._source_model.set_visual_mode(enabled, sprite_size=self._VISUAL_SPRITE_SIZE)
        if hasattr(self, "_table"):
            vh = self._table.verticalHeader()
            if enabled:
                vh.setDefaultSectionSize(self._scaled(self._VISUAL_ROW_HEIGHT))
                self._table.setIconSize(QSize(self._VISUAL_SPRITE_SIZE, self._VISUAL_SPRITE_SIZE))
                # Widen name / abilities / mutations so icons have room.
                self._table.setColumnWidth(COL_NAME, max(self._table.columnWidth(COL_NAME), self._VISUAL_NAME_COL_WIDTH))
                self._table.setColumnWidth(COL_ABIL, max(self._table.columnWidth(COL_ABIL), self._VISUAL_ABIL_COL_WIDTH))
                self._table.setColumnWidth(COL_MUTS, max(self._table.columnWidth(COL_MUTS), self._VISUAL_MUTS_COL_WIDTH))
            else:
                vh.setDefaultSectionSize(self._scaled(24))
                self._table.setIconSize(QSize(16, 16))
                # Restore column widths to their base defaults.
                if hasattr(self, "_base_col_widths"):
                    for col in (COL_NAME, COL_ABIL, COL_MUTS):
                        if col in self._base_col_widths:
                            self._table.setColumnWidth(col, self._base_col_widths[col])
            self._table.viewport().update()
        clear_cat_sprite_cache()
        clear_mutation_part_cache()

    # ── Layout ────────────────────────────────────────────────────────────

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        rl = QHBoxLayout(central)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(0)

        hs = QSplitter(Qt.Horizontal)
        hs.setObjectName("main_window_sidebar_splitter")
        self._sidebar_splitter = hs
        rl.addWidget(hs)
        hs.addWidget(self._build_sidebar())
        hs.addWidget(self._build_content())
        hs.setStretchFactor(0, 0)
        hs.setStretchFactor(1, 1)
        hs.setSizes([190, 1250])
        _enforce_min_font_in_widget_tree(central)
        # Snapshot all stylesheet font sizes before any offset is applied,
        # so _apply_font_offset_to_tree always scales from the true originals.
        _apply_font_offset_to_tree(central, 0)
        _bind_splitter_persistence(self)
        self._restore_roster_table_defaults()

    def _restore_roster_table_defaults(self):
        if not hasattr(self, "_table") or self._table is None or self._table.model() is None:
            return
        col_count = self._table.model().columnCount()
        for col in range(col_count):
            self._table.setColumnHidden(col, col in (COL_GEN_DEPTH, COL_SRC))
        self._pin_roster_special_columns()

    def _pin_roster_special_columns(self):
        if not hasattr(self, "_table") or self._table is None or self._table.model() is None:
            return
        header = self._table.horizontalHeader()
        col_count = self._table.model().columnCount()
        if COL_TAGS < col_count:
            try:
                tags_visual = header.visualIndex(COL_TAGS)
                if tags_visual >= 0 and tags_visual != 0:
                    header.moveSection(tags_visual, 0)
            except Exception:
                pass
        if COL_ADV < col_count:
            try:
                adv_visual = header.visualIndex(COL_ADV)
                if adv_visual >= 0 and adv_visual != header.count() - 1:
                    header.moveSection(adv_visual, header.count() - 1)
            except Exception:
                pass

    # ── Sidebar ────────────────────────────────────────────────────────────

    def _build_sidebar(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setFixedWidth(self._base_sidebar_width)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setStyleSheet(
            "QScrollArea { background:#14142a; border:none; }"
            "QScrollBar:vertical { background:#14142a; width:6px; }"
            "QScrollBar::handle:vertical { background:#2a2a4a; border-radius:3px; }"
            "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height:0; }"
        )
        w = QWidget()
        self._sidebar = w
        w.setStyleSheet("background:#14142a;")
        vb = QVBoxLayout(w)
        vb.setContentsMargins(8, 14, 8, 12)
        vb.setSpacing(2)

        def sl(text):
            l = QLabel(text)
            # letter-spacing is not supported by Qt QSS — apply via QFont
            # to avoid "Could not parse stylesheet" warnings.
            l.setStyleSheet("color:#444; font-size:10px; font-weight:bold;"
                            " padding:8px 4px 4px 4px;")
            f = l.font()
            f.setLetterSpacing(QFont.AbsoluteSpacing, 1.0)
            l.setFont(f)
            return l

        self._filters_section_label = sl(_tr("sidebar.section.filters"))
        vb.addWidget(self._filters_section_label)
        self._btn_everyone = _sidebar_btn(_tr("sidebar.button.all_cats"))
        self._btn_everyone.clicked.connect(
            lambda: self._filter("__all__", self._btn_everyone))
        vb.addWidget(self._btn_everyone)
        self._room_btns["__all__"] = self._btn_everyone

        self._btn_all = _sidebar_btn(_tr("sidebar.button.alive_cats"))
        self._btn_all.setChecked(True)
        self._active_btn = self._btn_all
        self._btn_all.clicked.connect(lambda: self._filter(None, self._btn_all))
        vb.addWidget(self._btn_all)
        self._room_btns[None] = self._btn_all

        self._btn_exceptional = _sidebar_btn("")
        self._btn_exceptional.setToolTip("")
        self._btn_exceptional.clicked.connect(
            lambda: self._filter("__exceptional__", self._btn_exceptional)
        )
        vb.addWidget(self._btn_exceptional)
        self._room_btns["__exceptional__"] = self._btn_exceptional

        self._btn_donation = _sidebar_btn("")
        self._btn_donation.setToolTip("")
        self._btn_donation.clicked.connect(
            lambda: self._filter("__donation__", self._btn_donation)
        )
        vb.addWidget(self._btn_donation)
        self._room_btns["__donation__"] = self._btn_donation

        self._btn_fight_club = _sidebar_btn(_tr("sidebar.button.fight_club", default="Fight Club"))
        self._btn_fight_club.clicked.connect(lambda: self._filter("__fight_club__", self._btn_fight_club))
        vb.addWidget(self._btn_fight_club)
        self._room_btns["__fight_club__"] = self._btn_fight_club

        vb.addWidget(_hsep())
        self._sorting_section_label = sl(_tr("sidebar.section.cat_sorting", default="CAT SCORING"))
        vb.addWidget(self._sorting_section_label)
        self._btn_manual_scoring = _sidebar_btn(_tr("sidebar.button.manual_scoring", default="Simple Scoring"))
        self._btn_manual_scoring.clicked.connect(self._open_manual_scoring_view)
        vb.addWidget(self._btn_manual_scoring)
        self._btn_breed_priority = _sidebar_btn(_tr("sidebar.button.breed_priority", default="Detailed Scoring"))
        self._btn_breed_priority.clicked.connect(self._open_breed_priority_view)
        vb.addWidget(self._btn_breed_priority)

        vb.addWidget(_hsep())
        self._breeding_section_label = sl(_tr("sidebar.section.breeding"))
        vb.addWidget(self._breeding_section_label)
        self._btn_room_optimizer = _sidebar_btn(_tr("sidebar.button.room_optimizer"))
        self._btn_room_optimizer.clicked.connect(self._open_room_optimizer)
        vb.addWidget(self._btn_room_optimizer)
        self._btn_perfect_planner = _sidebar_btn(_tr("sidebar.button.perfect_7_planner"))
        self._btn_perfect_planner.clicked.connect(self._open_perfect_planner_view)
        vb.addWidget(self._btn_perfect_planner)
        self._btn_mutation_planner = _sidebar_btn(_tr("sidebar.button.mutation_planner"))
        self._btn_mutation_planner.clicked.connect(self._open_mutation_planner_view)
        vb.addWidget(self._btn_mutation_planner)
        self._btn_safe_breeding_view = _sidebar_btn(_tr("sidebar.button.mating_pair_search", default="Mating Pair Search"))
        self._btn_safe_breeding_view.clicked.connect(self._open_safe_breeding_view)
        vb.addWidget(self._btn_safe_breeding_view)
        self._btn_breeding_partners_view = _sidebar_btn(_tr("sidebar.button.breeding_partners"))
        self._btn_breeding_partners_view.clicked.connect(self._open_breeding_partners_view)
        vb.addWidget(self._btn_breeding_partners_view)

        vb.addWidget(_hsep())
        self._info_section_label = sl(_tr("sidebar.section.info"))
        vb.addWidget(self._info_section_label)
        self._btn_tree_view = _sidebar_btn(_tr("sidebar.button.family_tree_view"))
        self._btn_tree_view.clicked.connect(self._open_tree_browser)
        vb.addWidget(self._btn_tree_view)
        self._btn_furniture_view = _sidebar_btn(_tr("sidebar.button.furniture", default="Furniture"))
        self._btn_furniture_view.clicked.connect(self._open_furniture_view)
        vb.addWidget(self._btn_furniture_view)
        self._btn_calibration = _sidebar_btn(_tr("sidebar.button.calibration"))
        self._btn_calibration.clicked.connect(self._open_calibration_view)
        vb.addWidget(self._btn_calibration)

        vb.addWidget(_hsep())
        self._rooms_section_label = sl(_tr("sidebar.section.rooms"))
        vb.addWidget(self._rooms_section_label)
        self._rooms_vb = QVBoxLayout(); self._rooms_vb.setSpacing(2)
        vb.addLayout(self._rooms_vb)
        vb.addWidget(_hsep())

        self._other_section_label = sl(_tr("sidebar.section.other"))
        vb.addWidget(self._other_section_label)
        self._btn_adventure = _sidebar_btn(_tr("sidebar.button.on_adventure"))
        self._btn_gone      = _sidebar_btn(_tr("sidebar.button.gone"))
        self._btn_adventure.clicked.connect(
            lambda: self._filter("__adventure__", self._btn_adventure))
        self._btn_gone.clicked.connect(
            lambda: self._filter("__gone__", self._btn_gone))
        vb.addWidget(self._btn_adventure)
        vb.addWidget(self._btn_gone)
        self._room_btns["__adventure__"] = self._btn_adventure
        self._room_btns["__gone__"]      = self._btn_gone

        vb.addStretch()

        self._version_lbl = QLabel(f'<a href="whats-new://{APP_VERSION}">v{APP_VERSION}</a>')
        self._version_lbl.setTextFormat(Qt.RichText)
        self._version_lbl.setTextInteractionFlags(Qt.TextBrowserInteraction)
        self._version_lbl.setOpenExternalLinks(False)
        self._version_lbl.linkActivated.connect(lambda _link: self._show_whats_new_dialog())
        self._version_lbl.setStyleSheet("color:#666; font-size:10px; padding:0 4px 2px 4px;")
        self._version_lbl.setToolTip(f"Click to view release notes for {APP_VERSION}")
        vb.addWidget(self._version_lbl)

        self._save_lbl = QLabel(_tr("sidebar.no_save_loaded"))
        self._save_lbl.setStyleSheet("color:#444; font-size:10px;")
        self._save_lbl.setWordWrap(True)
        vb.addWidget(self._save_lbl)

        self._reload_btn = QPushButton(_tr("sidebar.button.reload"))
        self._reload_btn.setStyleSheet("QPushButton { color:#888; background:#1a1a32;"
                         " border:1px solid #2a2a4a; padding:7px;"
                         " border-radius:4px; font-size:11px; }"
                         "QPushButton:hover { background:#222244; }")
        self._reload_btn.clicked.connect(self._reload)
        vb.addWidget(self._reload_btn)
        self._refresh_filter_button_counts()
        scroll.setWidget(w)
        return scroll

    def _rebuild_room_buttons(self, cats: list[Cat]):
        # Capture the active room key BEFORE destroying buttons so we can
        # repoint `_active_btn` at the replacement for the same room. Without
        # this rescue, `_active_btn` keeps pointing at a deleted C++ widget,
        # and the next `_filter()` call raises RuntimeError on setChecked()
        # mid-handler — leaving the clicked room button highlighted but the
        # actual filter unchanged. See the "menu tab won't switch after an
        # in-game day" regression.
        active_room_key = self._active_room_key()
        while self._rooms_vb.count():
            item = self._rooms_vb.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        # Drop stale room entries — rooms that no longer exist (e.g., last
        # cat moved out of Attic) would otherwise leak deleted-widget
        # references in `_room_btns` forever.  Preserve permanent filter
        # entries (None, "__all__", "__exceptional__", …) so that
        # _active_room_key(), _current_room_key(), and nav restore keep
        # working after a rebuild.
        if active_room_key is not None and active_room_key in self._room_btns:
            # Null out first so `_active_btn` doesn't briefly point at a
            # dangling entry while we rebuild.
            self._active_btn = None
        _PERMANENT_KEYS = {
            None, "__all__", "__exceptional__", "__donation__",
            "__fight_club__", "__adventure__", "__gone__",
        }
        stale = [k for k in self._room_btns if k not in _PERMANENT_KEYS]
        for k in stale:
            del self._room_btns[k]
        _ROOM_ORDER = {
            "Attic": 0,
            "Floor2_Large": 1, "Floor2_Small": 2,
            "Floor1_Large": 3, "Floor1_Small": 4,
        }
        rooms = sorted(
            {c.room for c in cats if c.status == "In House" and c.room},
            key=lambda r: _ROOM_ORDER.get(r, 99),
        )
        for room in rooms:
            count = sum(1 for c in cats if c.room == room)
            display = ROOM_DISPLAY.get(room, room)
            btn = _sidebar_btn(f"{display}  ({count})")
            btn.clicked.connect(lambda _, r=room, b=btn: self._filter(r, b))
            self._rooms_vb.addWidget(btn)
            self._room_btns[room] = btn
        # Repoint `_active_btn` at the rebuilt button for the previously
        # active room (if that room still exists after the refresh).
        if active_room_key is not None and active_room_key in self._room_btns:
            self._active_btn = self._room_btns[active_room_key]
            self._active_btn.setChecked(True)

    def _refresh_filter_button_counts(self):
        total = len(self._cats)
        alive = sum(1 for c in self._cats if c.status != "Gone")
        exceptional = sum(1 for c in self._cats if c.status != "Gone" and _is_exceptional_breeder(c))
        donation = sum(1 for c in self._cats if c.status != "Gone" and _is_donation_candidate(c))
        fight_club = sum(
            1 for c in self._cats
            if c.status != "Gone"
            and not c.has_adventured
            and c.db_key in self._accessible_cat_keys
        )
        adv = sum(1 for c in self._cats if c.status == "Adventure")
        gone = sum(1 for c in self._cats if c.status == "Gone")

        self._btn_everyone.setText(f"{_tr('sidebar.button.all_cats')}  ({total})" if total else _tr("sidebar.button.all_cats"))
        self._btn_all.setText(f"{_tr('sidebar.button.alive_cats')}  ({alive})" if total else _tr("sidebar.button.alive_cats"))
        self._btn_exceptional.setText(f"{_tr('sidebar.button.exceptional')}  ({exceptional})")
        self._btn_donation.setText(f"{_tr('sidebar.button.donation_candidates')}  ({donation})")
        if hasattr(self, "_btn_fight_club"):
            self._btn_fight_club.setText(
                f"{_tr('sidebar.button.fight_club', default='Fight Club')}  ({fight_club})"
                if total else _tr("sidebar.button.fight_club", default="Fight Club")
            )
        self._btn_adventure.setText(f"{_tr('sidebar.button.on_adventure')}  ({adv})" if total else _tr("sidebar.button.on_adventure"))
        self._btn_gone.setText(f"{_tr('sidebar.button.gone')}  ({gone})" if total else _tr("sidebar.button.gone"))
        self._btn_room_optimizer.setText(_tr("sidebar.button.room_optimizer"))
        self._btn_perfect_planner.setText(_tr("sidebar.button.perfect_7_planner"))
        self._btn_mutation_planner.setText(_tr("sidebar.button.mutation_planner"))
        self._btn_safe_breeding_view.setText(_tr("sidebar.button.mating_pair_search", default="Mating Pair Search"))
        self._btn_breeding_partners_view.setText(_tr("sidebar.button.breeding_partners"))
        self._btn_tree_view.setText(_tr("sidebar.button.family_tree_view"))
        self._btn_calibration.setText(_tr("sidebar.button.calibration"))
        self._btn_furniture_view.setText(_tr("sidebar.button.furniture", default="Furniture"))
        self._update_threshold_button_copy()

    def _update_threshold_button_copy(self):
        if not hasattr(self, "_btn_exceptional") or not hasattr(self, "_btn_donation"):
            return
        summary = _current_threshold_summary(self._cats)
        exceptional = summary["exceptional"]
        donation = summary["donation"]
        top_stat = summary["top_stat"]
        avg_sum = summary["avg_sum"]
        base_exceptional = summary["base_exceptional"]
        base_donation = summary["base_donation"]
        adaptive = summary["adaptive_enabled"]
        planner_traits = self._mutation_planner_view.get_selected_traits() if self._mutation_planner_view is not None else []
        mutation_ability_traits = [t for t in planner_traits if t.get("category") in {"mutation", "ability"}]
        planner_note = ""
        if summary.get("donation_missing_planner_traits"):
            if mutation_ability_traits:
                planner_note = (
                    " Donation candidates are cats missing selected mutation/ability traits and still under the stat floor"
                    f" ({_planner_import_traits_summary(mutation_ability_traits)})."
                )
            else:
                planner_note = (
                    " Donation candidates are cats missing selected mutation/ability traits and still under the stat floor."
                )
        if adaptive:
            self._btn_exceptional.setToolTip(
                "Exceptional breeders follow the living-cat average curve: "
                f"base {base_exceptional}, reference avg {summary['adaptive_reference_avg_sum']:.1f}, "
                f"curve {summary['adaptive_curve_strength']:.2f}, current avg {avg_sum:.1f} -> {exceptional}."
            )
            self._btn_donation.setToolTip(
                "Donation candidates follow the living-cat average curve: "
                f"base {base_donation}, reference avg {summary['adaptive_reference_avg_sum']:.1f}, "
                f"curve {summary['adaptive_curve_strength']:.2f}, current avg {avg_sum:.1f} -> {donation}, "
                f"top stat cap {top_stat}." + planner_note
            )
        else:
            self._btn_exceptional.setToolTip(
                f"Exceptional breeders: base stat sum >= {exceptional}."
            )
            self._btn_donation.setToolTip(
                "Donation candidates use documented heuristics: "
                f"base stat sum <= {donation}, "
                f"top stat <= {top_stat}, and/or high aggression." + planner_note
            )

    def _refresh_threshold_runtime(self, cats: list[Cat] | None = None):
        _apply_threshold_preferences(_load_threshold_preferences(), cats if cats is not None else self._cats)

    def _sync_donation_planner_traits(self):
        traits = self._mutation_planner_view.get_selected_traits() if self._mutation_planner_view is not None else []
        _set_donation_planner_traits(traits)
        room_key = None
        if self._active_btn is not None:
            for key, btn in self._room_btns.items():
                if btn is self._active_btn:
                    room_key = key
                    break
        self._refresh_threshold_sensitive_ui(room_key)
        self._update_threshold_button_copy()

    def _refresh_threshold_sensitive_ui(self, room_key=None):
        if hasattr(self, "_proxy_model"):
            self._proxy_model.invalidate()
        self._refresh_filter_button_counts()
        self._refresh_bulk_view_buttons(room_key)
        self._update_count()

    def _sync_room_config_views(self):
        if self._room_optimizer_view is None or self._perfect_planner_view is None:
            return
        self._perfect_planner_view.sync_from_room_config(
            self._room_optimizer_view.get_room_config(),
            available_rooms=self._room_optimizer_view.get_available_rooms(),
        )

    def _retranslate_ui(self):
        current_room_key = next((key for key, btn in self._room_btns.items() if btn is self._active_btn), None)
        _refresh_localized_constants()
        self._build_menu()
        if getattr(self, "_fight_club_layout_active", False):
            if current_room_key == "__fight_club__":
                self._apply_fight_club_layout(True, force=True)
            else:
                self._apply_fight_club_layout(False, force=True)
        self._filters_section_label.setText(_tr("sidebar.section.filters"))
        if hasattr(self, "_sorting_section_label"):
            self._sorting_section_label.setText(_tr("sidebar.section.cat_sorting", default="CAT SCORING"))
        self._breeding_section_label.setText(_tr("sidebar.section.breeding"))
        self._info_section_label.setText(_tr("sidebar.section.info"))
        self._rooms_section_label.setText(_tr("sidebar.section.rooms"))
        self._other_section_label.setText(_tr("sidebar.section.other"))
        self._reload_btn.setText(_tr("sidebar.button.reload"))
        self._save_lbl.setText(os.path.basename(self._current_save) if self._current_save else _tr("sidebar.no_save_loaded"))
        self._search.setPlaceholderText(_tr("header.search_placeholder"))
        self._fight_club_abilities_filter.setPlaceholderText(_tr("header.filter.abilities", default="Abilities"))
        self._fight_club_mutations_filter.setPlaceholderText(_tr("header.filter.mutations", default="Mutations"))
        self._loading_label.setText(_tr("loading.save_file"))
        self._cache_progress.setFormat(_tr("loading.cache.computing"))
        self._refresh_filter_button_counts()
        self._rebuild_room_buttons(self._cats)
        if current_room_key in self._room_btns:
            self._active_btn = self._room_btns[current_room_key]
            self._active_btn.setChecked(True)
        self._update_header(current_room_key)
        self._update_count()
        self._refresh_bulk_view_buttons()
        if hasattr(self, "_source_model") and self._source_model is not None:
            self._source_model.headerDataChanged.emit(Qt.Horizontal, 0, len(COLUMNS) - 1)
        if self._safe_breeding_view is not None:
            self._safe_breeding_view.retranslate_ui()
        if self._breeding_partners_view is not None:
            self._breeding_partners_view.retranslate_ui()
        if self._room_optimizer_view is not None:
            self._room_optimizer_view.retranslate_ui()
        if self._perfect_planner_view is not None:
            self._perfect_planner_view.retranslate_ui()
        if hasattr(self, "_mutation_planner_view") and self._mutation_planner_view is not None:
            self._mutation_planner_view.retranslate_ui()
        if self._calibration_view is not None:
            self._calibration_view.retranslate_ui()
        if self._furniture_view is not None:
            self._furniture_view.retranslate_ui()
        if hasattr(self, "_thresholds_action"):
            self._thresholds_action.setText(_tr("menu.settings.thresholds", default="Donation / Exceptional Thresholds…"))
        if hasattr(self, "_optimizer_search_settings_action"):
            self._optimizer_search_settings_action.setText(
                _tr("menu.settings.optimizer_search_settings", default="Optimizer Search Settings…")
            )
        if hasattr(self, "_reset_ui_settings_action"):
            self._reset_ui_settings_action.setText(_tr("menu.settings.reset_ui_defaults"))
        if hasattr(self, "_room_optimizer_auto_recalc_action"):
            self._room_optimizer_auto_recalc_action.setText(_tr("menu.settings.room_optimizer_auto_recalc", default="Auto Recalculate Room Optimizer"))
        if hasattr(self, "_manual_scoring_auto_calc_action"):
            self._manual_scoring_auto_calc_action.setText(_tr("menu.settings.manual_scoring_auto_calc", default="Auto Recalculate Manual Scoring"))

    def _change_language(self, language: str):
        if language not in _SUPPORTED_LANGUAGES or language == _current_language():
            return
        _set_saved_language(language)
        _set_current_language(language)
        self._retranslate_ui()
        current_title = _language_label(language)
        self.setWindowTitle(_tr("app.title_with_save", name=os.path.basename(self._current_save)) if self._current_save else _tr("app.title"))
        self.statusBar().showMessage(_tr("status.language_changed", language=current_title))

    # ── Content ────────────────────────────────────────────────────────────

    def _build_content(self) -> QWidget:
        w  = QWidget()
        vb = QVBoxLayout(w)
        vb.setContentsMargins(0, 0, 0, 0)
        vb.setSpacing(0)

        # Header
        hdr = QWidget()
        self._header = hdr
        hdr.setStyleSheet("background:#16213e; border-bottom:1px solid #1e1e38;")
        hdr.setFixedHeight(self._base_header_height)
        hb = QHBoxLayout(hdr); hb.setContentsMargins(14, 0, 14, 0)
        self._header_lbl = QLabel(_tr("header.filter.all_cats"))
        self._header_lbl.setStyleSheet("color:#eee; font-size:15px; font-weight:bold;")
        self._mode_badge_lbl = QLabel(_tr("header.badge.total_stats", default="TOTAL STATS"))
        self._mode_badge_lbl.setVisible(False)
        self._mode_badge_lbl.setStyleSheet(
            "QLabel { color:#ffe8a3; background:#5a4516; border:1px solid #9f7b2c;"
            " border-radius:10px; padding:2px 8px; font-size:10px; font-weight:bold; }"
        )
        # letter-spacing isn't a Qt QSS property — apply via QFont instead.
        _badge_font = self._mode_badge_lbl.font()
        _badge_font.setLetterSpacing(QFont.AbsoluteSpacing, 0.8)
        self._mode_badge_lbl.setFont(_badge_font)
        self._count_lbl = QLabel("")
        self._count_lbl.setStyleSheet("color:#555; font-size:12px; padding-left:8px;")
        self._summary_lbl = QLabel("")
        self._summary_lbl.setStyleSheet("color:#4a7a9a; font-size:11px;")
        self._bulk_blacklist_btn = QPushButton()
        self._bulk_blacklist_btn.setCheckable(True)
        self._bulk_blacklist_btn.setMinimumWidth(130)
        self._bulk_blacklist_btn.setStyleSheet(
            "QPushButton { background:#5a2d22; color:#f1dfda; border:1px solid #8b4c3e;"
            " border-radius:4px; padding:4px 10px; font-size:11px; font-weight:bold; }"
            "QPushButton:hover { background:#6c382a; }"
            "QPushButton:pressed { background:#4c241b; }"
            "QPushButton:checked { background:#7a3626; border:1px solid #b35b48; }"
        )
        self._set_bulk_toggle_label(self._bulk_blacklist_btn, _tr("bulk.breeding_block"), False)
        self._bulk_blacklist_btn.clicked.connect(self._toggle_blacklist_filtered_cats)
        self._bulk_must_breed_btn = QPushButton()
        self._bulk_must_breed_btn.setCheckable(True)
        self._bulk_must_breed_btn.setMinimumWidth(110)
        self._bulk_must_breed_btn.setStyleSheet(
            "QPushButton { background:#3b355f; color:#ece8fb; border:1px solid #5d58a0;"
            " border-radius:4px; padding:4px 10px; font-size:11px; font-weight:bold; }"
            "QPushButton:hover { background:#49417a; }"
            "QPushButton:pressed { background:#312c4f; }"
            "QPushButton:checked { background:#514890; border:1px solid #7d73c7; }"
        )
        self._set_bulk_toggle_label(self._bulk_must_breed_btn, _tr("bulk.must_breed"), False)
        self._bulk_must_breed_btn.clicked.connect(self._toggle_must_breed_filtered_cats)
        bulk_container = QWidget()
        self._bulk_actions_layout = QHBoxLayout(bulk_container)
        self._bulk_actions_layout.setContentsMargins(0, 0, 0, 0)
        self._bulk_actions_layout.setSpacing(8)
        self._bulk_pin_btn = QPushButton()
        self._bulk_pin_btn.setCheckable(True)
        self._bulk_pin_btn.setMinimumWidth(90)
        self._bulk_pin_btn.setStyleSheet(
            "QPushButton { background:#2a3a2a; color:#c8dcc8; border:1px solid #4a6a4a;"
            " border-radius:4px; padding:4px 10px; font-size:11px; font-weight:bold; }"
            "QPushButton:hover { background:#3a4a3a; }"
            "QPushButton:pressed { background:#1e2e1e; }"
            "QPushButton:checked { background:#3a5a3a; border:1px solid #5a8a5a; }")
        self._set_bulk_toggle_label(self._bulk_pin_btn, _tr("bulk.pin", default="Pin"), False)
        self._bulk_pin_btn.clicked.connect(self._toggle_pin_filtered_cats)
        self._bulk_actions_layout.addWidget(self._bulk_must_breed_btn)
        self._bulk_actions_layout.addWidget(self._bulk_blacklist_btn)
        self._bulk_actions_layout.addWidget(self._bulk_pin_btn)

        self._room_actions_box = QWidget()
        room_actions = QHBoxLayout(self._room_actions_box)
        room_actions.setContentsMargins(0, 0, 0, 0)
        room_actions.setSpacing(8)

        self._room_must_breed_btn = QPushButton()
        self._style_room_action_button(self._room_must_breed_btn, "#3b355f", "#5d58a0", "#49417a")
        self._room_must_breed_btn.clicked.connect(lambda: self._toggle_room_must_breed(self._active_room_key()))
        room_actions.addWidget(self._room_must_breed_btn)

        self._room_breeding_block_btn = QPushButton()
        self._style_room_action_button(self._room_breeding_block_btn, "#5a2d22", "#8b4c3e", "#6c382a")
        self._room_breeding_block_btn.clicked.connect(lambda: self._toggle_room_breeding_block(self._active_room_key()))
        room_actions.addWidget(self._room_breeding_block_btn)

        self._room_pin_btn = QPushButton()
        self._style_room_action_button(self._room_pin_btn, "#2a3a2a", "#4a6a4a", "#3a4a3a", width=90)
        self._room_pin_btn.clicked.connect(lambda: self._toggle_room_pin(self._active_room_key()))
        room_actions.addWidget(self._room_pin_btn)

        room_actions.addStretch()
        self._set_room_action_button_texts()
        self._search = QLineEdit()
        self._search.setPlaceholderText(_tr("header.search_placeholder"))
        self._search.setClearButtonEnabled(True)
        self._search.setFixedWidth(self._base_search_width)
        self._search.setStyleSheet(
            "QLineEdit { background:#0d0d1c; color:#ccc; border:1px solid #2a2a4a;"
            " border-radius:4px; padding:3px 8px; font-size:12px; }"
            "QLineEdit:focus { border-color:#3a3a7a; }")
        self._fight_club_abilities_filter = QLineEdit()
        self._fight_club_abilities_filter.setPlaceholderText(_tr("header.filter.abilities", default="Abilities"))
        self._fight_club_abilities_filter.setClearButtonEnabled(True)
        self._fight_club_abilities_filter.setFixedWidth(132)
        self._fight_club_abilities_filter.setVisible(False)
        self._fight_club_abilities_filter.setStyleSheet(
            "QLineEdit { background:#0d0d1c; color:#ccc; border:1px solid #2a2a4a;"
            " border-radius:4px; padding:3px 8px; font-size:12px; }"
            "QLineEdit:focus { border-color:#3a3a7a; }")
        self._fight_club_mutations_filter = QLineEdit()
        self._fight_club_mutations_filter.setPlaceholderText(_tr("header.filter.mutations", default="Mutations"))
        self._fight_club_mutations_filter.setClearButtonEnabled(True)
        self._fight_club_mutations_filter.setFixedWidth(132)
        self._fight_club_mutations_filter.setVisible(False)
        self._fight_club_mutations_filter.setStyleSheet(
            "QLineEdit { background:#0d0d1c; color:#ccc; border:1px solid #2a2a4a;"
            " border-radius:4px; padding:3px 8px; font-size:12px; }"
            "QLineEdit:focus { border-color:#3a3a7a; }")
        self._pin_toggle = QPushButton(_tr("header.pin_toggle", default="📌"))
        self._pin_toggle.setCheckable(True)
        self._pin_toggle.setToolTip(_tr("header.pin_toggle_tooltip", default="Show only pinned cats"))
        self._pin_toggle.setStyleSheet(
            "QPushButton { background:#1a1a32; color:#888; border:1px solid #2a2a4a;"
            " border-radius:4px; padding:3px 8px; font-size:12px; min-width:28px; }"
            "QPushButton:hover { background:#222244; }"
            "QPushButton:checked { background:#2a2a5a; color:#eee; border-color:#4a4a8a; }")
        self._pin_toggle.toggled.connect(self._on_pin_toggle)

        self._tags_btn = QPushButton("Tags")
        self._tags_btn.setToolTip("Apply tags to selected cats")
        self._tags_btn.setStyleSheet(
            "QPushButton { background:#1a1a32; color:#aaa; border:1px solid #2a2a4a;"
            " border-radius:4px; padding:3px 10px; font-size:11px; font-weight:bold; }"
            "QPushButton:hover { background:#252545; color:#ddd; }"
            "QPushButton::menu-indicator { image:none; }")
        self._tags_btn.clicked.connect(self._show_tags_menu)

        hb.addWidget(self._header_lbl)
        hb.addWidget(self._mode_badge_lbl)
        hb.addWidget(self._count_lbl)
        hb.addStretch()
        hb.addWidget(self._room_actions_box)
        hb.addSpacing(8)
        hb.addWidget(bulk_container)
        hb.addSpacing(10)
        hb.addWidget(self._tags_btn)
        hb.addSpacing(4)
        hb.addWidget(self._pin_toggle)
        hb.addSpacing(4)
        hb.addWidget(self._search)
        hb.addSpacing(6)
        hb.addWidget(self._fight_club_abilities_filter)
        hb.addSpacing(4)
        hb.addWidget(self._fight_club_mutations_filter)
        hb.addSpacing(12)
        hb.addWidget(self._summary_lbl)
        vb.addWidget(hdr)

        # Vertical splitter: table on top, detail panel on bottom (user-resizable)
        vs = QSplitter(Qt.Vertical)
        vs.setObjectName("main_window_detail_splitter")
        vs.setHandleWidth(4)
        vs.setStyleSheet("QSplitter::handle:vertical { background:#1e1e38; }")
        self._detail_splitter = vs
        self._table_view_container = vs
        vb.addWidget(vs)

        # Table
        self._source_model = CatTableModel()
        self._source_model.blacklistChanged.connect(self._on_blacklist_changed)
        self._proxy_model  = RoomFilterModel()
        self._proxy_model.setSourceModel(self._source_model)
        self._proxy_model.set_accessible_cats(set())
        self._proxy_model.modelReset.connect(self._update_count)
        self._proxy_model.rowsInserted.connect(self._update_count)
        self._proxy_model.rowsRemoved.connect(self._update_count)

        self._table = QTableView()
        self._table.setModel(self._proxy_model)
        self._table.setProperty("_keep_adv_ready_last", True)
        self._table.setProperty("_keep_tags_first", True)
        self._table.setProperty("_min_tags_width", 122)
        self._table.setProperty("_table_state_version", 2)
        self._table.setSortingEnabled(True)
        self._table.sortByColumn(COL_NAME, Qt.AscendingOrder)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setVisible(False)
        self._table.setShowGrid(False)
        self._table.setWordWrap(False)
        # Checkbox columns are toggled explicitly in _on_table_clicked.
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        hh = self._table.horizontalHeader()
        hh.setStretchLastSection(False)  # we control stretch manually

        # Name: interactive so the user can resize it; not Stretch so it
        # doesn't eat the blank space that should sit at the right edge.
        hh.setSectionResizeMode(COL_TAGS, QHeaderView.Interactive)
        self._table.setColumnWidth(COL_TAGS, self._base_col_widths[COL_TAGS])

        hh.setSectionResizeMode(COL_NAME, QHeaderView.Interactive)
        self._table.setColumnWidth(COL_NAME, self._base_col_widths[COL_NAME])
        self._tag_strip_delegate = TagStripDelegate(self._table)
        self._table.setItemDelegateForColumn(COL_TAGS, self._tag_strip_delegate)

        # Visual-mode delegates: these forward to the default text
        # rendering in compact mode, and paint icons in visual mode.
        self._abilities_visual_delegate = VisualIconDelegate("abilities", self._table)
        self._mutations_visual_delegate = VisualIconDelegate("mutations", self._table)
        self._table.setItemDelegateForColumn(COL_ABIL, self._abilities_visual_delegate)
        self._table.setItemDelegateForColumn(COL_MUTS, self._mutations_visual_delegate)

        # Room: size to content so it adapts to room name length
        hh.setSectionResizeMode(COL_ROOM, QHeaderView.ResizeToContents)

        # Narrow columns keep today's defaults but can now be widened for translated text.
        for col, width in [
            (COL_GEN, _W_GEN),
            (COL_STAT, _W_STATUS),
            (COL_ADV, 72),
            (COL_BL, 34),
            (COL_MB, 34),
            (COL_PIN, 34),
            (COL_SUM, 38),
            (COL_AGG, _W_TRAIT_NARROW),
            (COL_LIB, _W_TRAIT_NARROW),
            (COL_INBRD, _W_TRAIT_NARROW),
            (COL_SEXUALITY, _W_TRAIT),
        ] + [(c, _W_STAT) for c in STAT_COLS]:
            hh.setSectionResizeMode(col, QHeaderView.Interactive)
            self._table.setColumnWidth(col, width)

        # Abilities: interactive — user drags to taste
        hh.setSectionResizeMode(COL_ABIL, QHeaderView.Interactive)
        self._table.setColumnWidth(COL_ABIL, self._base_col_widths[COL_ABIL])

        # Mutations: interactive
        hh.setSectionResizeMode(COL_MUTS, QHeaderView.Interactive)
        self._table.setColumnWidth(COL_MUTS, self._base_col_widths[COL_MUTS])

        # Relations: interactive
        hh.setSectionResizeMode(COL_RELNS, QHeaderView.Interactive)
        self._table.setColumnWidth(COL_RELNS, self._base_col_widths[COL_RELNS])

        # Narrow auxiliary columns keep their defaults but can be widened manually.
        hh.setSectionResizeMode(COL_REL, QHeaderView.Interactive)
        self._table.setColumnWidth(COL_REL, self._base_col_widths[COL_REL])

        hh.setSectionResizeMode(COL_AGE, QHeaderView.Interactive)
        self._table.setColumnWidth(COL_AGE, self._base_col_widths[COL_AGE])

        hh.setSectionResizeMode(COL_GEN_DEPTH, QHeaderView.Interactive)
        self._table.setColumnWidth(COL_GEN_DEPTH, _W_GEN)
        self._table.setColumnHidden(COL_GEN_DEPTH, True)

        # Source: Stretch — absorbs blank space, hidden by default (behind lineage toggle)
        hh.setSectionResizeMode(COL_SRC, QHeaderView.Stretch)
        self._table.setColumnHidden(COL_SRC, True)
        self._table.setContextMenuPolicy(Qt.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._on_table_context_menu)

        self._table.setStyleSheet("""
            QTableView {
                background:#0d0d1c; alternate-background-color:#131326;
                color:#ddd; border:none; font-size:12px;
                selection-background-color:#1e3060;
            }
            QTableView::item { padding:3px 4px; }
            QTableView::item:selected { color:#fff; }
            QHeaderView::section {
                background:#16213e; color:#888; padding:5px 4px;
                border:none; border-bottom:1px solid #1e1e38;
                border-right:1px solid #2a2a50;
                font-size:11px; font-weight:bold;
            }
            QScrollBar:vertical { background:#0d0d1c; width:10px; }
            QScrollBar::handle:vertical {
                background:#252545; border-radius:5px; min-height:20px;
            }
        """)

        self._table.selectionModel().selectionChanged.connect(self._on_selection)
        self._table.clicked.connect(self._on_table_clicked)
        self._search.textChanged.connect(self._proxy_model.set_name_filter)
        self._search.textChanged.connect(self._update_count)
        self._search.textChanged.connect(lambda _: self._refresh_bulk_view_buttons())
        self._fight_club_abilities_filter.textChanged.connect(self._proxy_model.set_abilities_filter)
        self._fight_club_abilities_filter.textChanged.connect(self._update_count)
        self._fight_club_abilities_filter.textChanged.connect(lambda _: self._refresh_bulk_view_buttons())
        self._fight_club_mutations_filter.textChanged.connect(self._proxy_model.set_mutations_filter)
        self._fight_club_mutations_filter.textChanged.connect(self._update_count)
        self._fight_club_mutations_filter.textChanged.connect(lambda _: self._refresh_bulk_view_buttons())
        vs.addWidget(self._table)

        # Detail panel
        self._detail = CatDetailPanel()
        vs.addWidget(self._detail)
        vs.setStretchFactor(0, 1)
        vs.setStretchFactor(1, 0)

        # Build all secondary views eagerly so every tab is ready when
        # the user clicks it — no freeze on first navigation.
        self._content_vb = vb
        self._build_all_views()

        # Loading overlay — shown during background save parse, dismissed before UI population
        self._loading_overlay = QWidget(w)
        self._loading_overlay.setStyleSheet("background:#0a0a18;")
        lo_vb = QVBoxLayout(self._loading_overlay)
        lo_vb.setAlignment(Qt.AlignCenter)
        self._loading_label = QLabel(_tr("loading.save_file"))
        self._loading_label.setStyleSheet("color:#aaa; font-size:15px; font-weight:bold;")
        self._loading_label.setAlignment(Qt.AlignCenter)
        self._loading_bar = QProgressBar()
        self._loading_bar.setFixedWidth(320)
        self._loading_bar.setFixedHeight(16)
        self._loading_bar.setRange(0, 0)  # indeterminate pulse
        self._loading_bar.setTextVisible(False)
        self._loading_bar.setStyleSheet(
            "QProgressBar { background:#1a1a32; border:1px solid #2a2a4a; border-radius:4px; }"
            "QProgressBar::chunk { background:#3f8f72; border-radius:3px; }"
        )
        lo_vb.addWidget(self._loading_label)
        lo_vb.addSpacing(10)
        lo_vb.addWidget(self._loading_bar, 0, Qt.AlignCenter)
        self._loading_overlay.hide()

        return w

    # ── Selection → detail ────────────────────────────────────────────────

    def _on_selection(self):
        rows = list({
            self._proxy_model.mapToSource(idx).row()
            for idx in self._table.selectionModel().selectedRows()
        })
        cats = [c for r in rows[:2] if (c := self._source_model.cat_at(r)) is not None]
        room_key = self._current_room_key()
        if room_key == "__fight_club__" and len(cats) > 1:
            cats = cats[:1]
        elif len(cats) == 2 and _is_hater_pair(cats[0], cats[1]) and not self._pair_detail_override:
            cats = cats[:1]
        was_collapsed = self._detail.maximumHeight() == 0
        self._detail.show_cats(cats)
        if cats and was_collapsed:
            total   = self._detail_splitter.height()
            panel_h = 200 if len(cats) == 1 else 300
            self._detail_splitter.setSizes([max(10, total - panel_h), panel_h])

        # Highlight compatibility: dim incompatible cats when 1 is selected.
        # Fight Club isn't a breeding view, so skip the dimming there.
        focus = cats[0] if len(cats) == 1 else None
        is_fight_club = room_key == "__fight_club__" or getattr(self, "_fight_club_layout_active", False)
        table_focus = None if is_fight_club else focus
        self._source_model.set_focus_cat(table_focus)
        if self._tree_view is not None and self._tree_view.isVisible() and focus is not None:
            self._tree_view.select_cat(focus)
        if self._safe_breeding_view is not None and self._safe_breeding_view.isVisible() and focus is not None:
            self._safe_breeding_view.select_cat(focus)

    def _on_table_clicked(self, proxy_index: QModelIndex):
        if not proxy_index.isValid() or proxy_index.column() not in (COL_BL, COL_MB, COL_PIN):
            return
        src_index = self._proxy_model.mapToSource(proxy_index)
        if not src_index.isValid():
            return
        current = self._source_model.data(src_index, Qt.CheckStateRole)
        next_state = Qt.Unchecked if current == Qt.Checked else Qt.Checked
        if self._source_model.setData(src_index, next_state, Qt.CheckStateRole):
            self._on_selection()

    def _on_table_context_menu(self, pos):
        index = self._table.indexAt(pos)
        selection_model = self._table.selectionModel()
        if index.isValid() and selection_model is not None and not selection_model.isSelected(index):
            self._table.clearSelection()
            self._table.selectRow(index.row())
        cats = self._selected_cats()
        if not cats and index.isValid():
            src_index = self._proxy_model.mapToSource(index)
            if src_index.isValid():
                cat = self._source_model.cat_at(src_index.row())
                cats = [cat] if cat is not None else []
        if not cats:
            return
        cat = cats[0]

        menu = QMenu(self)
        find_best = menu.addAction(_tr("menu.context.find_best_pair", default="Find Best Pair"))
        open_tree = menu.addAction(_tr("menu.context.open_tree", default="Open Family Tree"))
        jump_planner = menu.addAction(_tr("menu.context.jump_planner", default="Jump to Planner"))
        menu.addSeparator()
        toggle_pin = menu.addAction(_tr("menu.context.toggle_pin", default="Toggle Pin"))
        toggle_mb = menu.addAction(_tr("menu.context.toggle_must_breed", default="Toggle Must Breed"))
        toggle_block = menu.addAction(_tr("menu.context.toggle_block", default="Toggle Block"))
        toggle_not_adv = menu.addAction(_tr("menu.context.toggle_not_adventured", default="Toggle Not Adventured"))

        find_best.triggered.connect(lambda: self._open_safe_breeding_for_cat(cat, quality=True))
        open_tree.triggered.connect(lambda: self._open_tree_for_cat(cat))
        jump_planner.triggered.connect(lambda: self._open_perfect_planner_for_cat(cat))
        toggle_pin.triggered.connect(self._toggle_pin_filtered_cats)
        toggle_mb.triggered.connect(self._toggle_must_breed_filtered_cats)
        toggle_block.triggered.connect(self._toggle_blacklist_filtered_cats)
        toggle_not_adv.triggered.connect(self._toggle_not_adventured_filtered_cats)

        # ── Tag submenu ──
        menu.addSeparator()
        tag_menu = menu.addMenu(_tr("menu.context.tag_submenu", default="Tag"))
        self._populate_tag_context_submenu(tag_menu, cats)

        menu.exec(self._table.viewport().mapToGlobal(pos))

    def _populate_tag_context_submenu(self, tag_menu: QMenu, cats: list):
        """Fill the RMB 'Tag' submenu with toggle actions for each defined tag."""
        tag_menu.setStyleSheet(
            "QMenu { background:#1a1a32; color:#ddd; border:1px solid #2a2a4a; padding:4px; }"
            "QMenu::item { padding:4px 16px; }"
            "QMenu::item:selected { background:#252545; }"
            "QMenu::separator { height:1px; background:#2a2a4a; margin:4px 8px; }"
        )
        if not _TAG_DEFS:
            empty = tag_menu.addAction(_tr("menu.context.tag_no_defs", default="No tags defined…"))
            empty.triggered.connect(self._open_tag_manager)
            return

        for td in _TAG_DEFS:
            tid = td["id"]
            label = td.get("name") or "\u25CF"
            all_have = all(tid in _cat_tags(c) for c in cats)
            icon = _make_tag_icon([tid], dot_size=12)
            action = tag_menu.addAction(icon, label)
            action.setCheckable(True)
            action.setChecked(all_have)
            action.triggered.connect(
                lambda checked, tag_id=tid: self._apply_tag_to_selection(tag_id, checked)
            )

        tag_menu.addSeparator()
        clear = tag_menu.addAction(_tr("menu.context.tag_clear", default="Clear tags"))
        clear.setEnabled(bool(cats))
        clear.triggered.connect(self._clear_tags_from_selection)
        tag_menu.addSeparator()
        manage = tag_menu.addAction(_tr("menu.context.tag_manage", default="Manage Tags\u2026"))
        manage.triggered.connect(self._open_tag_manager)

    # ── Filtering ──────────────────────────────────────────────────────────

    def _filter(self, room_key, btn: QPushButton):
        self._push_nav_history()
        if not getattr(self, "_save_view_disabled", False):
            _save_current_view("table")
        self._show_table_view()
        if self._active_btn and self._active_btn is not btn:
            self._active_btn.setChecked(False)
        btn.setChecked(True)
        self._active_btn = btn
        self._proxy_model.set_room(room_key)

        # Set multi-column sort for donation candidates and exceptional breeders
        if room_key == "__fight_club__":
            self._proxy_model.set_sort_columns([
                (COL_SUM, Qt.DescendingOrder),
                (COL_NAME, Qt.AscendingOrder),
            ])
        elif room_key in ("__donation__", "__exceptional__"):
            self._proxy_model.set_sort_columns([
                (COL_ROOM, Qt.AscendingOrder),
                (COL_AGE, Qt.AscendingOrder),
                (COL_NAME, Qt.AscendingOrder),
            ])
        else:
            self._proxy_model.set_sort_columns([])

        self._apply_fight_club_layout(room_key == "__fight_club__")
        if room_key != "__fight_club__":
            self._restore_roster_table_defaults()
        self._refresh_bulk_view_buttons(room_key)
        self._update_header(room_key)
        self._update_count()
        self._detail.show_cats([])
        self._source_model.set_focus_cat(None)

    def _visible_filtered_cats(self) -> list[Cat]:
        cats: list[Cat] = []
        for row in range(self._proxy_model.rowCount()):
            src_idx = self._proxy_model.mapToSource(self._proxy_model.index(row, 0))
            if not src_idx.isValid():
                continue
            cat = self._source_model.cat_at(src_idx.row())
            if cat is not None:
                cats.append(cat)
        return cats

    def _selected_cats(self) -> list[Cat]:
        cats: list[Cat] = []
        for idx in self._table.selectionModel().selectedRows():
            src_idx = self._proxy_model.mapToSource(idx)
            if not src_idx.isValid():
                continue
            cat = self._source_model.cat_at(src_idx.row())
            if cat is not None:
                cats.append(cat)
        return cats

    def _refresh_bulk_view_buttons(self, room_key=None):
        if room_key is None and self._active_btn is not None:
            for key, btn in self._room_btns.items():
                if btn is self._active_btn:
                    room_key = key
                    break
        fight_club_view = room_key == "__fight_club__"
        room_visible = room_key in (None, "__all__") or room_key in ROOM_DISPLAY
        bulk_visible = room_key in ("__donation__", "__exceptional__")
        donation_view = room_key == "__donation__"
        exceptional_view = room_key == "__exceptional__"
        alive_view = room_key is None
        if hasattr(self, "_bulk_actions_layout"):
            while self._bulk_actions_layout.count():
                item = self._bulk_actions_layout.takeAt(0)
                if item.widget():
                    item.widget().setParent(None)
            if bulk_visible and donation_view:
                self._bulk_actions_layout.addWidget(self._bulk_blacklist_btn)
                self._bulk_actions_layout.addWidget(self._bulk_must_breed_btn)
            elif bulk_visible:
                self._bulk_actions_layout.addWidget(self._bulk_must_breed_btn)
                self._bulk_actions_layout.addWidget(self._bulk_blacklist_btn)
            elif fight_club_view:
                self._bulk_actions_layout.addWidget(self._bulk_pin_btn)
            if bulk_visible:
                self._bulk_actions_layout.addWidget(self._bulk_pin_btn)
        if hasattr(self, "_bulk_blacklist_btn"):
            self._bulk_blacklist_btn.setVisible(bulk_visible)
        if hasattr(self, "_bulk_must_breed_btn"):
            self._bulk_must_breed_btn.setVisible(bulk_visible)
        if hasattr(self, "_bulk_pin_btn"):
            self._bulk_pin_btn.setVisible(bulk_visible or fight_club_view)
        if hasattr(self, "_room_actions_box"):
            self._room_actions_box.setVisible(room_visible)
        if not (bulk_visible or room_visible or fight_club_view):
            return
        if room_visible:
            self._set_room_action_button_texts()
            return
        if fight_club_view:
            self._bulk_pin_btn.blockSignals(True)
            self._bulk_pin_btn.setChecked(False)
            self._bulk_pin_btn.setCheckable(False)
            self._bulk_pin_btn.setEnabled(True)
            self._bulk_pin_btn.setText(_tr("bulk.toggle_pin", default="Toggle Pin"))
            self._bulk_pin_btn.setToolTip(_tr("bulk.toggle_pin.tooltip", default="Toggle pin on selected cats"))
            self._bulk_pin_btn.blockSignals(False)
            return
        if alive_view:
            self._bulk_blacklist_btn.blockSignals(True)
            try:
                self._bulk_blacklist_btn.setCheckable(False)
                self._bulk_blacklist_btn.setText(_tr("bulk.toggle_breeding_block"))
                self._bulk_blacklist_btn.setEnabled(True)
                self._bulk_blacklist_btn.setToolTip(_tr("bulk.toggle_breeding_block.tooltip"))
            finally:
                self._bulk_blacklist_btn.blockSignals(False)
            self._bulk_must_breed_btn.blockSignals(True)
            try:
                self._bulk_must_breed_btn.setCheckable(False)
                self._bulk_must_breed_btn.setText(_tr("bulk.toggle_must_breed"))
                self._bulk_must_breed_btn.setEnabled(True)
                self._bulk_must_breed_btn.setToolTip(_tr("bulk.toggle_must_breed.tooltip"))
            finally:
                self._bulk_must_breed_btn.blockSignals(False)
            self._bulk_pin_btn.blockSignals(True)
            try:
                self._bulk_pin_btn.setCheckable(False)
                self._bulk_pin_btn.setText(_tr("bulk.toggle_pin", default="Toggle Pin"))
                self._bulk_pin_btn.setEnabled(True)
                self._bulk_pin_btn.setToolTip(_tr("bulk.toggle_pin.tooltip", default="Toggle pin for selected cats"))
            finally:
                self._bulk_pin_btn.blockSignals(False)
            return
        cats = self._visible_filtered_cats()
        all_blocked = bool(cats) and all(cat.is_blacklisted for cat in cats)
        all_must_breed = bool(cats) and all(cat.must_breed for cat in cats)
        self._bulk_blacklist_btn.setCheckable(True)
        self._bulk_blacklist_btn.blockSignals(True)
        if exceptional_view:
            any_blocked = any(cat.is_blacklisted for cat in cats)
            self._bulk_blacklist_btn.setChecked(False)
            self._bulk_blacklist_btn.setEnabled(any_blocked)
            self._bulk_blacklist_btn.setText(_tr("bulk.clear_breeding_block"))
            self._bulk_blacklist_btn.setToolTip(_tr("bulk.clear_breeding_block.tooltip"))
        else:
            self._bulk_blacklist_btn.setChecked(all_blocked)
            self._bulk_blacklist_btn.setEnabled(True)
            self._set_bulk_toggle_label(self._bulk_blacklist_btn, _tr("bulk.breeding_block"), all_blocked)
            self._bulk_blacklist_btn.setToolTip("")
        self._bulk_blacklist_btn.blockSignals(False)
        self._bulk_must_breed_btn.setCheckable(True)
        self._bulk_must_breed_btn.blockSignals(True)
        if donation_view:
            any_must_breed = any(cat.must_breed for cat in cats)
            self._bulk_must_breed_btn.setChecked(False)
            self._bulk_must_breed_btn.setEnabled(any_must_breed)
            self._bulk_must_breed_btn.setText(_tr("bulk.clear_must_breed"))
            self._bulk_must_breed_btn.setToolTip(_tr("bulk.clear_must_breed.tooltip"))
        else:
            self._bulk_must_breed_btn.setChecked(all_must_breed)
            self._bulk_must_breed_btn.setEnabled(True)
            self._set_bulk_toggle_label(self._bulk_must_breed_btn, _tr("bulk.must_breed"), all_must_breed)
            self._bulk_must_breed_btn.setToolTip("")
        self._bulk_must_breed_btn.blockSignals(False)
        all_pinned = bool(cats) and all(cat.is_pinned for cat in cats)
        self._bulk_pin_btn.setCheckable(True)
        self._bulk_pin_btn.blockSignals(True)
        self._bulk_pin_btn.setChecked(all_pinned)
        self._bulk_pin_btn.setEnabled(True)
        self._set_bulk_toggle_label(self._bulk_pin_btn, _tr("bulk.pin", default="Pin"), all_pinned)
        self._bulk_pin_btn.setToolTip("")
        self._bulk_pin_btn.blockSignals(False)

    def _toggle_blacklist_filtered_cats(self):
        room_key = self._active_room_key()
        alive_view = room_key is None
        exceptional_view = room_key == "__exceptional__"
        if alive_view:
            cats = self._selected_cats()
            if not cats:
                self.statusBar().showMessage(_tr("bulk.status.select_toggle_breeding_block", default="Select cats first, then click Toggle Breeding Block"))
                return
            changed = 0
            for cat in cats:
                cat.is_blacklisted = not cat.is_blacklisted
                if cat.is_blacklisted:
                    cat.must_breed = False
                changed += 1
            self._emit_bulk_toggle_refresh()
            self.statusBar().showMessage(_tr("bulk.status.toggled_breeding_block", default="Toggled breeding block for {count} selected cats", count=changed))
            return
        target_state = False if exceptional_view else self._bulk_blacklist_btn.isChecked()
        changed = 0
        for cat in self._visible_filtered_cats():
            if cat.is_blacklisted == target_state and (not target_state or not cat.must_breed):
                continue
            cat.is_blacklisted = target_state
            if target_state:
                cat.must_breed = False
            changed += 1
        self._refresh_bulk_view_buttons()
        if changed == 0:
            self.statusBar().showMessage(_tr("bulk.status.no_breeding_block_change", default="No cats in view needed a breeding-block change"))
            return
        self._emit_bulk_toggle_refresh()
        if exceptional_view:
            self.statusBar().showMessage(_tr("bulk.status.cleared_breeding_block_exceptional", default="Cleared breeding block for {count} cats in the current exceptional view", count=changed))
        else:
            state_text = _tr("common.on", default="on") if target_state else _tr("common.off", default="off")
            self.statusBar().showMessage(_tr("bulk.status.turned_breeding_block", default="Turned breeding block {state} for {count} cats in the current view", state=state_text, count=changed))

    def _toggle_not_adventured_filtered_cats(self):
        cats = self._selected_cats()
        if not cats:
            self.statusBar().showMessage(_tr("bulk.status.select_toggle_not_adventured", default="Select cats first, then click Toggle Not Adventured"))
            return
        changed = 0
        for cat in cats:
            cat.not_adventured_override = not getattr(cat, "not_adventured_override", False)
            changed += 1
        if self._current_save:
            _save_not_adventured(self._current_save, self._cats)
        self._emit_bulk_toggle_refresh()
        self.statusBar().showMessage(_tr("bulk.status.toggled_not_adventured", default="Toggled not-adventured override for {count} selected cats", count=changed))

    def _toggle_must_breed_filtered_cats(self):
        room_key = self._active_room_key()
        alive_view = room_key is None
        donation_view = room_key == "__donation__"
        if alive_view:
            cats = self._selected_cats()
            if not cats:
                self.statusBar().showMessage(_tr("bulk.status.select_toggle_must_breed", default="Select cats first, then click Toggle Must Breed"))
                return
            changed = 0
            for cat in cats:
                cat.must_breed = not cat.must_breed
                if cat.must_breed:
                    cat.is_blacklisted = False
                changed += 1
            self._emit_bulk_toggle_refresh()
            self.statusBar().showMessage(_tr("bulk.status.toggled_must_breed", default="Toggled must breed for {count} selected cats", count=changed))
            return
        target_state = False if donation_view else self._bulk_must_breed_btn.isChecked()
        changed = 0
        for cat in self._visible_filtered_cats():
            if cat.must_breed == target_state and (not target_state or not cat.is_blacklisted):
                continue
            cat.must_breed = target_state
            if target_state:
                cat.is_blacklisted = False
            changed += 1
        self._refresh_bulk_view_buttons()
        if changed == 0:
            self.statusBar().showMessage(_tr("bulk.status.no_must_breed_change", default="No cats in view needed a must-breed change"))
            return
        self._emit_bulk_toggle_refresh()
        if donation_view:
            self.statusBar().showMessage(_tr("bulk.status.cleared_must_breed_donation", default="Cleared Must Breed for {count} cats in the current donation-candidates view", count=changed))
        else:
            state_text = _tr("common.on", default="on") if target_state else _tr("common.off", default="off")
            self.statusBar().showMessage(_tr("bulk.status.turned_must_breed", default="Turned must breed {state} for {count} cats in the current view", state=state_text, count=changed))

    def _toggle_pin_filtered_cats(self):
        room_key = self._active_room_key()
        alive_view = room_key is None
        fight_club_view = room_key == "__fight_club__"
        if alive_view or fight_club_view:
            cats = self._selected_cats()
            if not cats:
                self.statusBar().showMessage(_tr("bulk.status.select_toggle_pin", default="Select cats first, then click Toggle Pin"))
                return
            changed = 0
            for cat in cats:
                cat.is_pinned = not cat.is_pinned
                changed += 1
            self._emit_bulk_toggle_refresh()
            self.statusBar().showMessage(_tr("bulk.status.toggled_pin", default="Toggled pin for {count} selected cats", count=changed))
            return
        target_state = self._bulk_pin_btn.isChecked()
        changed = 0
        for cat in self._visible_filtered_cats():
            if cat.is_pinned == target_state:
                continue
            cat.is_pinned = target_state
            changed += 1
        self._refresh_bulk_view_buttons()
        if changed == 0:
            self.statusBar().showMessage(_tr("bulk.status.no_pin_change", default="No cats in view needed a pin change"))
            return
        self._emit_bulk_toggle_refresh()
        state_text = _tr("common.on", default="on") if target_state else _tr("common.off", default="off")
        self.statusBar().showMessage(_tr("bulk.status.turned_pin", default="Turned pin {state} for {count} cats in the current view", state=state_text, count=changed))

    def _emit_bulk_toggle_refresh(self):
        if self._source_model.rowCount() == 0:
            return
        top_left = self._source_model.index(0, COL_BL)
        bottom_right = self._source_model.index(max(0, self._source_model.rowCount() - 1), COL_PIN)
        self._source_model.dataChanged.emit(
            top_left,
            bottom_right,
            [Qt.DisplayRole, Qt.CheckStateRole, Qt.ToolTipRole],
        )
        self._proxy_model.invalidate()
        self._source_model.blacklistChanged.emit()
        self._update_count()
        self._refresh_bulk_view_buttons()

    def _blacklist_filtered_cats(self):
        changed = 0
        for row in range(self._proxy_model.rowCount()):
            proxy_idx = self._proxy_model.index(row, COL_BL)
            if not proxy_idx.isValid():
                continue
            src_idx = self._proxy_model.mapToSource(proxy_idx)
            if not src_idx.isValid():
                continue
            cat = self._source_model.cat_at(src_idx.row())
            if cat is None or cat.is_blacklisted:
                continue
            cat.is_blacklisted = True
            changed += 1
        if changed == 0:
            self.statusBar().showMessage(_tr("bulk.status.no_additional_blacklist", default="No additional cats in view were added to the breeding blacklist"))
            return

        top_left = self._source_model.index(0, COL_BL)
        bottom_right = self._source_model.index(max(0, self._source_model.rowCount() - 1), COL_BL)
        self._source_model.dataChanged.emit(
            top_left,
            bottom_right,
            [Qt.DisplayRole, Qt.CheckStateRole, Qt.ToolTipRole],
        )
        self._source_model.blacklistChanged.emit()
        self._update_count()
        self.statusBar().showMessage(_tr("bulk.status.excluded_donation", default="Excluded {count} cats in the current donation-candidates view from breeding", count=changed))

    def _clear_must_breed_filtered_cats(self):
        changed = 0
        for row in range(self._proxy_model.rowCount()):
            proxy_idx = self._proxy_model.index(row, COL_MB)
            if not proxy_idx.isValid():
                continue
            src_idx = self._proxy_model.mapToSource(proxy_idx)
            if not src_idx.isValid():
                continue
            cat = self._source_model.cat_at(src_idx.row())
            if cat is None or not cat.must_breed:
                continue
            cat.must_breed = False
            changed += 1
        if changed == 0:
            self.statusBar().showMessage("No cats in view had Must Breed set")
            return

        top_left = self._source_model.index(0, COL_MB)
        bottom_right = self._source_model.index(max(0, self._source_model.rowCount() - 1), COL_MB)
        self._source_model.dataChanged.emit(
            top_left,
            bottom_right,
            [Qt.DisplayRole, Qt.CheckStateRole, Qt.ToolTipRole],
        )
        self._source_model.blacklistChanged.emit()
        self._update_count()
        self.statusBar().showMessage(f"Cleared Must Breed for {changed} cats in the current donation-candidates view")

    # ── View data generation tracking ─────────────────────────────────

    def _bump_cats_generation(self):
        """Increment the generation counter whenever cat data changes."""
        self._cats_generation += 1

    def _set_view_cats_if_needed(self, view_key: str, view, cats):
        """Push cats to *view* only when data has changed since the last push."""
        if self._view_generation.get(view_key) == self._cats_generation:
            return  # already up-to-date
        view.set_cats(cats)
        self._view_generation[view_key] = self._cats_generation

    # ── Deferred view construction ───────────────────────────────────
    #
    # Secondary views are built lazily so the window appears fast.
    # Each _ensure_*() method constructs the view if it is still None,
    # adds it to the content layout, wires signals, and pushes cat data.
    # The idle chain (_deferred_build_views) calls them in priority
    # order after the save finishes loading.

    def _push_cats_to_view_if_loaded(self, view_key: str, view):
        """Push current cat data to a freshly-built view if cats are loaded."""
        if self._cats and view is not None:
            view.set_cats(self._cats)
            self._view_generation[view_key] = self._cats_generation

    def _ensure_room_optimizer_view(self):
        if self._room_optimizer_view is not None:
            return
        self._room_optimizer_view = RoomOptimizerView(self)
        self._room_optimizer_view.hide()
        self._content_vb.addWidget(self._room_optimizer_view, 1)
        self._room_optimizer_view.room_priority_panel.configChanged.connect(self._sync_room_config_views)
        self._room_optimizer_view.cat_locator.set_navigate_to_cat_callback(self._navigate_to_cat)
        self._room_optimizer_view.set_navigate_to_pair_callback(self._navigate_to_cat_pair)
        # Wire to mutation planner if it's already built
        if self._mutation_planner_view is not None:
            self._room_optimizer_view.set_planner_view(self._mutation_planner_view)
        self._push_cats_to_view_if_loaded("room_optimizer", self._room_optimizer_view)

    def _ensure_mutation_planner_view(self):
        if self._mutation_planner_view is not None:
            return
        self._mutation_planner_view = MutationDisorderPlannerView(self)
        self._mutation_planner_view.hide()
        self._content_vb.addWidget(self._mutation_planner_view, 1)
        self._mutation_planner_view.traitsChanged.connect(self._sync_donation_planner_traits)
        self._mutation_planner_view.set_navigate_to_cat_callback(self._navigate_to_cat)
        # Wire to room optimizer if it was built first (normal order)
        if self._room_optimizer_view is not None:
            self._room_optimizer_view.set_planner_view(self._mutation_planner_view)
        self._push_cats_to_view_if_loaded("mutation_planner", self._mutation_planner_view)

    def _ensure_manual_scoring_view(self):
        if self._manual_scoring_view is not None:
            return
        self._manual_scoring_view = ManualScoringView(self)
        self._manual_scoring_view.hide()
        self._content_vb.addWidget(self._manual_scoring_view, 1)
        self._manual_scoring_view._auto_calc_chk.toggled.connect(self._sync_manual_scoring_auto_calc_action)
        self._push_cats_to_view_if_loaded("manual_scoring", self._manual_scoring_view)

    def _sync_manual_scoring_auto_calc_action(self, checked: bool):
        if hasattr(self, "_manual_scoring_auto_calc_action"):
            self._manual_scoring_auto_calc_action.blockSignals(True)
            self._manual_scoring_auto_calc_action.setChecked(checked)
            self._manual_scoring_auto_calc_action.blockSignals(False)

    def _ensure_perfect_planner_view(self):
        if self._perfect_planner_view is not None:
            return
        self._perfect_planner_view = PerfectCatPlannerView(self)
        self._perfect_planner_view.hide()
        self._content_vb.addWidget(self._perfect_planner_view, 1)
        self._perfect_planner_view.cat_locator.set_navigate_to_cat_callback(self._navigate_to_cat)
        self._perfect_planner_view.offspring_tracker.set_navigate_to_cat_callback(self._navigate_to_cat)
        if self._mutation_planner_view is not None:
            self._perfect_planner_view.set_mutation_planner_view(self._mutation_planner_view)
        self._push_cats_to_view_if_loaded("perfect_planner", self._perfect_planner_view)

    def _ensure_safe_breeding_view(self):
        if self._safe_breeding_view is not None:
            return
        self._safe_breeding_view = SafeBreedingView(self)
        self._safe_breeding_view.set_navigate_to_pair_callback(self._navigate_to_cat_pair)
        self._safe_breeding_view.hide()
        self._content_vb.addWidget(self._safe_breeding_view, 1)
        self._push_cats_to_view_if_loaded("safe_breeding", self._safe_breeding_view)

    def _ensure_breeding_partners_view(self):
        if self._breeding_partners_view is not None:
            return
        self._breeding_partners_view = BreedingPartnersView(self)
        self._breeding_partners_view.set_navigate_to_cat_callback(self._navigate_to_cat_by_name)
        self._breeding_partners_view.hide()
        self._content_vb.addWidget(self._breeding_partners_view, 1)
        self._push_cats_to_view_if_loaded("breeding_partners", self._breeding_partners_view)

    def _ensure_tree_view(self):
        if self._tree_view is not None:
            return
        self._tree_view = FamilyTreeBrowserView(self)
        self._tree_view.hide()
        self._content_vb.addWidget(self._tree_view, 1)
        self._push_cats_to_view_if_loaded("tree", self._tree_view)

    def _ensure_furniture_view(self):
        if self._furniture_view is not None:
            return
        self._furniture_view = FurnitureView(self)
        self._furniture_view.hide()
        self._content_vb.addWidget(self._furniture_view, 1)
        # FurnitureView uses set_context(), not set_cats() — pushed in _on_save_loaded

    def _ensure_calibration_view(self):
        if self._calibration_view is not None:
            return
        self._calibration_view = CalibrationView(self)
        self._calibration_view.calibrationChanged.connect(self._on_calibration_changed)
        self._calibration_view.hide()
        self._content_vb.addWidget(self._calibration_view, 1)
        # CalibrationView uses set_context(), not set_cats() — pushed in _on_save_loaded

    def _ensure_breed_priority_view(self):
        if self._breed_priority_view is not None:
            return
        ratings_path = os.path.join(APPDATA_CONFIG_DIR, "breed_priority.json")
        self._breed_priority_view = BreedPriorityView(
            ratings_path,
            list(STAT_NAMES),
            ROOM_DISPLAY,
            _mutation_display_name,
            _ability_tip,
        )
        self._breed_priority_view.hide()
        self._content_vb.addWidget(self._breed_priority_view, 1)
        self._push_cats_to_view_if_loaded("breed_priority", self._breed_priority_view)

    def _build_all_views(self):
        """Build all secondary views eagerly during init."""
        self._ensure_room_optimizer_view()
        self._ensure_mutation_planner_view()
        self._ensure_manual_scoring_view()
        self._ensure_breed_priority_view()
        self._ensure_perfect_planner_view()
        self._ensure_safe_breeding_view()
        self._ensure_breeding_partners_view()
        self._ensure_tree_view()
        self._ensure_furniture_view()
        self._ensure_calibration_view()

    # ── View switching ─────────────────────────────────────────────────

    def _show_table_view(self):
        if hasattr(self, "_tree_view") and self._tree_view is not None:
            self._tree_view.hide()
        if hasattr(self, "_safe_breeding_view") and self._safe_breeding_view is not None:
            self._safe_breeding_view.hide()
        if hasattr(self, "_breeding_partners_view") and self._breeding_partners_view is not None:
            self._breeding_partners_view.hide()
        if hasattr(self, "_room_optimizer_view") and self._room_optimizer_view is not None:
            self._room_optimizer_view.hide()
        if hasattr(self, "_perfect_planner_view") and self._perfect_planner_view is not None:
            self._perfect_planner_view.hide()
        if hasattr(self, "_calibration_view") and self._calibration_view is not None:
            self._calibration_view.hide()
        if hasattr(self, "_mutation_planner_view") and self._mutation_planner_view is not None:
            self._mutation_planner_view.hide()
        if hasattr(self, "_furniture_view") and self._furniture_view is not None:
            self._furniture_view.hide()
        if hasattr(self, "_manual_scoring_view") and self._manual_scoring_view is not None:
            self._manual_scoring_view.hide()
        if hasattr(self, "_breed_priority_view") and self._breed_priority_view is not None:
            self._breed_priority_view.hide()
        if hasattr(self, "_header"):
            self._header.show()
        if hasattr(self, "_table_view_container"):
            self._table_view_container.show()
        if hasattr(self, "_btn_tree_view"):
            self._btn_tree_view.setChecked(False)
        if hasattr(self, "_btn_safe_breeding_view"):
            self._btn_safe_breeding_view.setChecked(False)
        if hasattr(self, "_btn_breeding_partners_view"):
            self._btn_breeding_partners_view.setChecked(False)
        if hasattr(self, "_btn_room_optimizer"):
            self._btn_room_optimizer.setChecked(False)
        if hasattr(self, "_btn_perfect_planner"):
            self._btn_perfect_planner.setChecked(False)
        if hasattr(self, "_btn_calibration"):
            self._btn_calibration.setChecked(False)
        if hasattr(self, "_btn_mutation_planner"):
            self._btn_mutation_planner.setChecked(False)
        if hasattr(self, "_btn_furniture_view"):
            self._btn_furniture_view.setChecked(False)
        if hasattr(self, "_btn_manual_scoring"):
            self._btn_manual_scoring.setChecked(False)
        if hasattr(self, "_btn_breed_priority"):
            self._btn_breed_priority.setChecked(False)

    def _show_fight_club_view(self):
        if hasattr(self, "_btn_fight_club"):
            self._filter("__fight_club__", self._btn_fight_club)

    def _show_tree_view(self):
        self._ensure_tree_view()
        if self._active_btn is not None:
            self._active_btn.setChecked(False)
        self._active_btn = None
        if hasattr(self, "_header"):
            self._header.hide()
        if hasattr(self, "_table_view_container"):
            self._table_view_container.hide()
        if hasattr(self, "_safe_breeding_view") and self._safe_breeding_view is not None:
            self._safe_breeding_view.hide()
        if hasattr(self, "_breeding_partners_view") and self._breeding_partners_view is not None:
            self._breeding_partners_view.hide()
        if hasattr(self, "_room_optimizer_view") and self._room_optimizer_view is not None:
            self._room_optimizer_view.hide()
        if hasattr(self, "_perfect_planner_view") and self._perfect_planner_view is not None:
            self._perfect_planner_view.hide()
        if hasattr(self, "_calibration_view") and self._calibration_view is not None:
            self._calibration_view.hide()
        if hasattr(self, "_mutation_planner_view") and self._mutation_planner_view is not None:
            self._mutation_planner_view.hide()
        if hasattr(self, "_furniture_view") and self._furniture_view is not None:
            self._furniture_view.hide()
        if hasattr(self, "_manual_scoring_view") and self._manual_scoring_view is not None:
            self._manual_scoring_view.hide()
        if hasattr(self, "_breed_priority_view") and self._breed_priority_view is not None:
            self._breed_priority_view.hide()
        if self._tree_view is not None:
            self._set_view_cats_if_needed("tree", self._tree_view, self._cats)
            self._tree_view.show()
        if hasattr(self, "_btn_tree_view"):
            self._btn_tree_view.setChecked(True)
        if hasattr(self, "_btn_safe_breeding_view"):
            self._btn_safe_breeding_view.setChecked(False)
        if hasattr(self, "_btn_breeding_partners_view"):
            self._btn_breeding_partners_view.setChecked(False)
        if hasattr(self, "_btn_room_optimizer"):
            self._btn_room_optimizer.setChecked(False)
        if hasattr(self, "_btn_perfect_planner"):
            self._btn_perfect_planner.setChecked(False)
        if hasattr(self, "_btn_calibration"):
            self._btn_calibration.setChecked(False)
        if hasattr(self, "_btn_mutation_planner"):
            self._btn_mutation_planner.setChecked(False)
        if hasattr(self, "_btn_furniture_view"):
            self._btn_furniture_view.setChecked(False)
        if hasattr(self, "_btn_manual_scoring"):
            self._btn_manual_scoring.setChecked(False)
        if hasattr(self, "_btn_breed_priority"):
            self._btn_breed_priority.setChecked(False)

    def _show_safe_breeding_view(self):
        self._ensure_safe_breeding_view()
        if self._active_btn is not None:
            self._active_btn.setChecked(False)
        self._active_btn = None
        if hasattr(self, "_header"):
            self._header.hide()
        if hasattr(self, "_table_view_container"):
            self._table_view_container.hide()
        if hasattr(self, "_tree_view") and self._tree_view is not None:
            self._tree_view.hide()
        if hasattr(self, "_breeding_partners_view") and self._breeding_partners_view is not None:
            self._breeding_partners_view.hide()
        if hasattr(self, "_room_optimizer_view") and self._room_optimizer_view is not None:
            self._room_optimizer_view.hide()
        if hasattr(self, "_perfect_planner_view") and self._perfect_planner_view is not None:
            self._perfect_planner_view.hide()
        if hasattr(self, "_calibration_view") and self._calibration_view is not None:
            self._calibration_view.hide()
        if hasattr(self, "_mutation_planner_view") and self._mutation_planner_view is not None:
            self._mutation_planner_view.hide()
        if hasattr(self, "_furniture_view") and self._furniture_view is not None:
            self._furniture_view.hide()
        if hasattr(self, "_manual_scoring_view") and self._manual_scoring_view is not None:
            self._manual_scoring_view.hide()
        if hasattr(self, "_breed_priority_view") and self._breed_priority_view is not None:
            self._breed_priority_view.hide()
        if self._safe_breeding_view is not None:
            self._safe_breeding_view.set_quality_mode(self._safe_breeding_quality_mode, refresh=False)
            self._set_view_cats_if_needed("safe_breeding", self._safe_breeding_view, self._cats)
            self._safe_breeding_view.show()
        if hasattr(self, "_btn_tree_view"):
            self._btn_tree_view.setChecked(False)
        if hasattr(self, "_btn_safe_breeding_view"):
            self._btn_safe_breeding_view.setChecked(True)
        if hasattr(self, "_btn_breeding_partners_view"):
            self._btn_breeding_partners_view.setChecked(False)
        if hasattr(self, "_btn_room_optimizer"):
            self._btn_room_optimizer.setChecked(False)
        if hasattr(self, "_btn_perfect_planner"):
            self._btn_perfect_planner.setChecked(False)
        if hasattr(self, "_btn_calibration"):
            self._btn_calibration.setChecked(False)
        if hasattr(self, "_btn_mutation_planner"):
            self._btn_mutation_planner.setChecked(False)
        if hasattr(self, "_btn_furniture_view"):
            self._btn_furniture_view.setChecked(False)
        if hasattr(self, "_btn_manual_scoring"):
            self._btn_manual_scoring.setChecked(False)
        if hasattr(self, "_btn_breed_priority"):
            self._btn_breed_priority.setChecked(False)

    def _show_breeding_partners_view(self):
        self._ensure_breeding_partners_view()
        if self._active_btn is not None:
            self._active_btn.setChecked(False)
        self._active_btn = None
        if hasattr(self, "_header"):
            self._header.hide()
        if hasattr(self, "_table_view_container"):
            self._table_view_container.hide()
        if hasattr(self, "_tree_view") and self._tree_view is not None:
            self._tree_view.hide()
        if hasattr(self, "_safe_breeding_view") and self._safe_breeding_view is not None:
            self._safe_breeding_view.hide()
        if hasattr(self, "_room_optimizer_view") and self._room_optimizer_view is not None:
            self._room_optimizer_view.hide()
        if hasattr(self, "_calibration_view") and self._calibration_view is not None:
            self._calibration_view.hide()
        if hasattr(self, "_mutation_planner_view") and self._mutation_planner_view is not None:
            self._mutation_planner_view.hide()
        if hasattr(self, "_perfect_planner_view") and self._perfect_planner_view is not None:
            self._perfect_planner_view.hide()
        if hasattr(self, "_furniture_view") and self._furniture_view is not None:
            self._furniture_view.hide()
        if hasattr(self, "_manual_scoring_view") and self._manual_scoring_view is not None:
            self._manual_scoring_view.hide()
        if hasattr(self, "_breed_priority_view") and self._breed_priority_view is not None:
            self._breed_priority_view.hide()
        if self._breeding_partners_view is not None:
            self._set_view_cats_if_needed("breeding_partners", self._breeding_partners_view, self._cats)
            self._breeding_partners_view.show()
        if hasattr(self, "_btn_tree_view"):
            self._btn_tree_view.setChecked(False)
        if hasattr(self, "_btn_safe_breeding_view"):
            self._btn_safe_breeding_view.setChecked(False)
        if hasattr(self, "_btn_breeding_partners_view"):
            self._btn_breeding_partners_view.setChecked(True)
        if hasattr(self, "_btn_room_optimizer"):
            self._btn_room_optimizer.setChecked(False)
        if hasattr(self, "_btn_perfect_planner"):
            self._btn_perfect_planner.setChecked(False)
        if hasattr(self, "_btn_calibration"):
            self._btn_calibration.setChecked(False)
        if hasattr(self, "_btn_mutation_planner"):
            self._btn_mutation_planner.setChecked(False)
        if hasattr(self, "_btn_furniture_view"):
            self._btn_furniture_view.setChecked(False)
        if hasattr(self, "_btn_manual_scoring"):
            self._btn_manual_scoring.setChecked(False)
        if hasattr(self, "_btn_breed_priority"):
            self._btn_breed_priority.setChecked(False)

    def _show_room_optimizer_view(self):
        self._ensure_room_optimizer_view()
        if self._active_btn is not None:
            self._active_btn.setChecked(False)
        self._active_btn = None
        if hasattr(self, "_header"):
            self._header.hide()
        if hasattr(self, "_table_view_container"):
            self._table_view_container.hide()
        if hasattr(self, "_tree_view") and self._tree_view is not None:
            self._tree_view.hide()
        if hasattr(self, "_safe_breeding_view") and self._safe_breeding_view is not None:
            self._safe_breeding_view.hide()
        if hasattr(self, "_breeding_partners_view") and self._breeding_partners_view is not None:
            self._breeding_partners_view.hide()
        if hasattr(self, "_calibration_view") and self._calibration_view is not None:
            self._calibration_view.hide()
        if hasattr(self, "_perfect_planner_view") and self._perfect_planner_view is not None:
            self._perfect_planner_view.hide()
        if hasattr(self, "_mutation_planner_view") and self._mutation_planner_view is not None:
            self._mutation_planner_view.hide()
        if hasattr(self, "_furniture_view") and self._furniture_view is not None:
            self._furniture_view.hide()
        if hasattr(self, "_manual_scoring_view") and self._manual_scoring_view is not None:
            self._manual_scoring_view.hide()
        if hasattr(self, "_breed_priority_view") and self._breed_priority_view is not None:
            self._breed_priority_view.hide()
        if self._room_optimizer_view is not None:
            self._set_view_cats_if_needed("room_optimizer", self._room_optimizer_view, self._cats)
            self._room_optimizer_view.show()
        if hasattr(self, "_btn_tree_view"):
            self._btn_tree_view.setChecked(False)
        if hasattr(self, "_btn_safe_breeding_view"):
            self._btn_safe_breeding_view.setChecked(False)
        if hasattr(self, "_btn_breeding_partners_view"):
            self._btn_breeding_partners_view.setChecked(False)
        if hasattr(self, "_btn_room_optimizer"):
            self._btn_room_optimizer.setChecked(True)
        if hasattr(self, "_btn_perfect_planner"):
            self._btn_perfect_planner.setChecked(False)
        if hasattr(self, "_btn_calibration"):
            self._btn_calibration.setChecked(False)
        if hasattr(self, "_btn_mutation_planner"):
            self._btn_mutation_planner.setChecked(False)
        if hasattr(self, "_btn_furniture_view"):
            self._btn_furniture_view.setChecked(False)
        if hasattr(self, "_btn_manual_scoring"):
            self._btn_manual_scoring.setChecked(False)
        if hasattr(self, "_btn_breed_priority"):
            self._btn_breed_priority.setChecked(False)

    def _show_perfect_planner_view(self):
        self._ensure_perfect_planner_view()
        if self._active_btn is not None:
            self._active_btn.setChecked(False)
        self._active_btn = None
        if hasattr(self, "_header"):
            self._header.hide()
        if hasattr(self, "_table_view_container"):
            self._table_view_container.hide()
        if hasattr(self, "_tree_view") and self._tree_view is not None:
            self._tree_view.hide()
        if hasattr(self, "_safe_breeding_view") and self._safe_breeding_view is not None:
            self._safe_breeding_view.hide()
        if hasattr(self, "_breeding_partners_view") and self._breeding_partners_view is not None:
            self._breeding_partners_view.hide()
        if hasattr(self, "_room_optimizer_view") and self._room_optimizer_view is not None:
            self._room_optimizer_view.hide()
        if hasattr(self, "_calibration_view") and self._calibration_view is not None:
            self._calibration_view.hide()
        if hasattr(self, "_mutation_planner_view") and self._mutation_planner_view is not None:
            self._mutation_planner_view.hide()
        if hasattr(self, "_furniture_view") and self._furniture_view is not None:
            self._furniture_view.hide()
        if hasattr(self, "_manual_scoring_view") and self._manual_scoring_view is not None:
            self._manual_scoring_view.hide()
        if hasattr(self, "_breed_priority_view") and self._breed_priority_view is not None:
            self._breed_priority_view.hide()
        if self._perfect_planner_view is not None:
            self._perfect_planner_view.show()
            if self._view_generation.get("perfect_planner") != self._cats_generation:
                self._perfect_planner_view.set_loading_state(True)
                cats = list(self._cats)
                gen = self._cats_generation
                def _deferred_set(cats=cats, gen=gen):
                    self._perfect_planner_view.set_cats(cats)
                    self._view_generation["perfect_planner"] = gen
                QTimer.singleShot(0, _deferred_set)
        if hasattr(self, "_btn_tree_view"):
            self._btn_tree_view.setChecked(False)
        if hasattr(self, "_btn_safe_breeding_view"):
            self._btn_safe_breeding_view.setChecked(False)
        if hasattr(self, "_btn_breeding_partners_view"):
            self._btn_breeding_partners_view.setChecked(False)
        if hasattr(self, "_btn_room_optimizer"):
            self._btn_room_optimizer.setChecked(False)
        if hasattr(self, "_btn_perfect_planner"):
            self._btn_perfect_planner.setChecked(True)
        if hasattr(self, "_btn_calibration"):
            self._btn_calibration.setChecked(False)
        if hasattr(self, "_btn_mutation_planner"):
            self._btn_mutation_planner.setChecked(False)
        if hasattr(self, "_btn_furniture_view"):
            self._btn_furniture_view.setChecked(False)
        if hasattr(self, "_btn_manual_scoring"):
            self._btn_manual_scoring.setChecked(False)
        if hasattr(self, "_btn_breed_priority"):
            self._btn_breed_priority.setChecked(False)

    def _show_calibration_view(self):
        self._ensure_calibration_view()
        if self._active_btn is not None:
            self._active_btn.setChecked(False)
        self._active_btn = None
        if hasattr(self, "_header"):
            self._header.hide()
        if hasattr(self, "_table_view_container"):
            self._table_view_container.hide()
        if hasattr(self, "_tree_view") and self._tree_view is not None:
            self._tree_view.hide()
        if hasattr(self, "_safe_breeding_view") and self._safe_breeding_view is not None:
            self._safe_breeding_view.hide()
        if hasattr(self, "_breeding_partners_view") and self._breeding_partners_view is not None:
            self._breeding_partners_view.hide()
        if hasattr(self, "_room_optimizer_view") and self._room_optimizer_view is not None:
            self._room_optimizer_view.hide()
        if hasattr(self, "_perfect_planner_view") and self._perfect_planner_view is not None:
            self._perfect_planner_view.hide()
        if hasattr(self, "_furniture_view") and self._furniture_view is not None:
            self._furniture_view.hide()
        if hasattr(self, "_manual_scoring_view") and self._manual_scoring_view is not None:
            self._manual_scoring_view.hide()
        if hasattr(self, "_breed_priority_view") and self._breed_priority_view is not None:
            self._breed_priority_view.hide()
        if self._calibration_view is not None:
            if self._current_save and self._view_generation.get("calibration") != self._cats_generation:
                self._calibration_view.set_context(self._current_save, self._cats)
                self._view_generation["calibration"] = self._cats_generation
            self._calibration_view.show()
        if hasattr(self, "_btn_tree_view"):
            self._btn_tree_view.setChecked(False)
        if hasattr(self, "_btn_safe_breeding_view"):
            self._btn_safe_breeding_view.setChecked(False)
        if hasattr(self, "_btn_breeding_partners_view"):
            self._btn_breeding_partners_view.setChecked(False)
        if hasattr(self, "_btn_room_optimizer"):
            self._btn_room_optimizer.setChecked(False)
        if hasattr(self, "_btn_perfect_planner"):
            self._btn_perfect_planner.setChecked(False)
        if hasattr(self, "_btn_calibration"):
            self._btn_calibration.setChecked(True)
        if hasattr(self, "_btn_mutation_planner"):
            self._btn_mutation_planner.setChecked(False)
        if hasattr(self, "_btn_furniture_view"):
            self._btn_furniture_view.setChecked(False)
        if hasattr(self, "_btn_manual_scoring"):
            self._btn_manual_scoring.setChecked(False)
        if hasattr(self, "_btn_breed_priority"):
            self._btn_breed_priority.setChecked(False)
        if hasattr(self, "_mutation_planner_view") and self._mutation_planner_view is not None:
            self._mutation_planner_view.hide()

    def _show_mutation_planner_view(self):
        self._ensure_mutation_planner_view()
        if self._active_btn is not None:
            self._active_btn.setChecked(False)
        self._active_btn = None
        if hasattr(self, "_header"):
            self._header.hide()
        if hasattr(self, "_table_view_container"):
            self._table_view_container.hide()
        if hasattr(self, "_tree_view") and self._tree_view is not None:
            self._tree_view.hide()
        if hasattr(self, "_safe_breeding_view") and self._safe_breeding_view is not None:
            self._safe_breeding_view.hide()
        if hasattr(self, "_breeding_partners_view") and self._breeding_partners_view is not None:
            self._breeding_partners_view.hide()
        if hasattr(self, "_room_optimizer_view") and self._room_optimizer_view is not None:
            self._room_optimizer_view.hide()
        if hasattr(self, "_perfect_planner_view") and self._perfect_planner_view is not None:
            self._perfect_planner_view.hide()
        if hasattr(self, "_calibration_view") and self._calibration_view is not None:
            self._calibration_view.hide()
        if hasattr(self, "_furniture_view") and self._furniture_view is not None:
            self._furniture_view.hide()
        if hasattr(self, "_manual_scoring_view") and self._manual_scoring_view is not None:
            self._manual_scoring_view.hide()
        if hasattr(self, "_breed_priority_view") and self._breed_priority_view is not None:
            self._breed_priority_view.hide()
        if self._mutation_planner_view is not None:
            self._set_view_cats_if_needed("mutation_planner", self._mutation_planner_view, self._cats)
            self._mutation_planner_view.show()
        if hasattr(self, "_btn_tree_view"):
            self._btn_tree_view.setChecked(False)
        if hasattr(self, "_btn_safe_breeding_view"):
            self._btn_safe_breeding_view.setChecked(False)
        if hasattr(self, "_btn_breeding_partners_view"):
            self._btn_breeding_partners_view.setChecked(False)
        if hasattr(self, "_btn_room_optimizer"):
            self._btn_room_optimizer.setChecked(False)
        if hasattr(self, "_btn_perfect_planner"):
            self._btn_perfect_planner.setChecked(False)
        if hasattr(self, "_btn_calibration"):
            self._btn_calibration.setChecked(False)
        if hasattr(self, "_btn_mutation_planner"):
            self._btn_mutation_planner.setChecked(True)
        if hasattr(self, "_btn_furniture_view"):
            self._btn_furniture_view.setChecked(False)
        if hasattr(self, "_btn_manual_scoring"):
            self._btn_manual_scoring.setChecked(False)
        if hasattr(self, "_btn_breed_priority"):
            self._btn_breed_priority.setChecked(False)

    def _show_furniture_view(self):
        self._ensure_furniture_view()
        if self._active_btn is not None:
            self._active_btn.setChecked(False)
        self._active_btn = None
        if hasattr(self, "_header"):
            self._header.hide()
        if hasattr(self, "_table_view_container"):
            self._table_view_container.hide()
        if hasattr(self, "_tree_view") and self._tree_view is not None:
            self._tree_view.hide()
        if hasattr(self, "_safe_breeding_view") and self._safe_breeding_view is not None:
            self._safe_breeding_view.hide()
        if hasattr(self, "_breeding_partners_view") and self._breeding_partners_view is not None:
            self._breeding_partners_view.hide()
        if hasattr(self, "_room_optimizer_view") and self._room_optimizer_view is not None:
            self._room_optimizer_view.hide()
        if hasattr(self, "_perfect_planner_view") and self._perfect_planner_view is not None:
            self._perfect_planner_view.hide()
        if hasattr(self, "_calibration_view") and self._calibration_view is not None:
            self._calibration_view.hide()
        if hasattr(self, "_mutation_planner_view") and self._mutation_planner_view is not None:
            self._mutation_planner_view.hide()
        if hasattr(self, "_manual_scoring_view") and self._manual_scoring_view is not None:
            self._manual_scoring_view.hide()
        if hasattr(self, "_breed_priority_view") and self._breed_priority_view is not None:
            self._breed_priority_view.hide()
        if self._furniture_view is not None:
            if self._current_save and self._view_generation.get("furniture") != self._cats_generation:
                self._furniture_view.set_context(self._cats, self._furniture, self._furniture_data, available_rooms=self._available_house_rooms)
                self._view_generation["furniture"] = self._cats_generation
            self._furniture_view.show()
        if hasattr(self, "_btn_tree_view"):
            self._btn_tree_view.setChecked(False)
        if hasattr(self, "_btn_safe_breeding_view"):
            self._btn_safe_breeding_view.setChecked(False)
        if hasattr(self, "_btn_breeding_partners_view"):
            self._btn_breeding_partners_view.setChecked(False)
        if hasattr(self, "_btn_room_optimizer"):
            self._btn_room_optimizer.setChecked(False)
        if hasattr(self, "_btn_perfect_planner"):
            self._btn_perfect_planner.setChecked(False)
        if hasattr(self, "_btn_calibration"):
            self._btn_calibration.setChecked(False)
        if hasattr(self, "_btn_mutation_planner"):
            self._btn_mutation_planner.setChecked(False)
        if hasattr(self, "_btn_furniture_view"):
            self._btn_furniture_view.setChecked(True)
        if hasattr(self, "_btn_manual_scoring"):
            self._btn_manual_scoring.setChecked(False)
        if hasattr(self, "_btn_breed_priority"):
            self._btn_breed_priority.setChecked(False)

    # ---- Navigation history (mouse back / forward buttons) -------------

    def _current_view_kind(self) -> str:
        """Return which major view is currently visible."""
        checks = [
            ("tree", getattr(self, "_tree_view", None)),
            ("safe_breeding", getattr(self, "_safe_breeding_view", None)),
            ("breeding_partners", getattr(self, "_breeding_partners_view", None)),
            ("room_optimizer", getattr(self, "_room_optimizer_view", None)),
            ("perfect_planner", getattr(self, "_perfect_planner_view", None)),
            ("calibration", getattr(self, "_calibration_view", None)),
            ("mutation_planner", getattr(self, "_mutation_planner_view", None)),
            ("furniture", getattr(self, "_furniture_view", None)),
            ("manual_scoring", getattr(self, "_manual_scoring_view", None)),
            ("breed_priority", getattr(self, "_breed_priority_view", None)),
        ]
        for kind, widget in checks:
            if widget is not None and widget.isVisible():
                return kind
        return "table"

    def _capture_nav_state(self) -> dict:
        """Snapshot the current view so it can be restored by a back click."""
        view = self._current_view_kind()
        state: dict = {"view": view}
        if view == "table":
            state["room_key"] = getattr(self._proxy_model, "_room", None)
            selected = []
            selection_model = self._table.selectionModel() if hasattr(self, "_table") else None
            if selection_model is not None:
                for idx in selection_model.selectedRows():
                    src_idx = self._proxy_model.mapToSource(idx)
                    if not src_idx.isValid():
                        continue
                    cat = self._source_model.cat_at(src_idx.row())
                    if cat is not None:
                        selected.append(cat.db_key)
            state["selected"] = selected
        return state

    def _push_nav_history(self) -> None:
        """Called BEFORE a navigation action. Snapshots current state onto the
        back stack (unless suppressed or it would duplicate the top) and
        clears the forward stack so new history supersedes the redo trail.
        """
        if self._nav_suppress:
            return
        # Bail out while the UI is still being constructed — the table,
        # proxy model, and sidebar buttons may not exist yet.
        if not hasattr(self, "_proxy_model") or not hasattr(self, "_table"):
            return
        state = self._capture_nav_state()
        if self._nav_back_stack and self._nav_back_stack[-1] == state:
            return
        self._nav_back_stack.append(state)
        # Cap unbounded growth from long sessions.
        if len(self._nav_back_stack) > 100:
            self._nav_back_stack.pop(0)
        self._nav_forward_stack.clear()

    def _apply_nav_state(self, state: dict) -> None:
        """Restore a snapshot produced by _capture_nav_state. Suppresses any
        further history pushes triggered by the restore calls themselves.
        """
        self._nav_suppress = True
        try:
            view = state.get("view", "table")
            if view == "table":
                room_key = state.get("room_key")
                btn = self._room_btns.get(room_key)
                if btn is not None:
                    self._filter(room_key, btn)
                else:
                    # Unknown room (e.g. a room that disappeared) — fall back
                    # to the Alive filter so the user still lands somewhere.
                    self._filter(None, self._btn_all)
                selected = state.get("selected") or []
                if selected:
                    self._select_cats_by_db_keys(selected)
            elif view == "tree":
                self._show_tree_view()
            elif view == "safe_breeding":
                self._show_safe_breeding_view()
            elif view == "breeding_partners":
                self._show_breeding_partners_view()
            elif view == "room_optimizer":
                self._show_room_optimizer_view()
            elif view == "perfect_planner":
                self._show_perfect_planner_view()
            elif view == "calibration":
                self._show_calibration_view()
            elif view == "mutation_planner":
                self._show_mutation_planner_view()
            elif view == "furniture":
                self._show_furniture_view()
            elif view == "manual_scoring":
                self._show_manual_scoring_view()
            elif view == "breed_priority":
                self._show_breed_priority_view()
        finally:
            self._nav_suppress = False

    def _navigate_back(self) -> None:
        if not self._nav_back_stack:
            return
        current = self._capture_nav_state()
        target = self._nav_back_stack.pop()
        self._nav_forward_stack.append(current)
        self._apply_nav_state(target)

    def _navigate_forward(self) -> None:
        if not self._nav_forward_stack:
            return
        current = self._capture_nav_state()
        target = self._nav_forward_stack.pop()
        self._nav_back_stack.append(current)
        self._apply_nav_state(target)

    def eventFilter(self, obj, event):
        # Mouse XButton1 (back) / XButton2 (forward) come through here via
        # an app-level filter installed in _build_ui. We swallow them so no
        # other widget tries to interpret them.
        if event.type() == QEvent.MouseButtonPress:
            btn = event.button()
            if btn == Qt.BackButton:
                self._navigate_back()
                return True
            if btn == Qt.ForwardButton:
                self._navigate_forward()
                return True
        return super().eventFilter(obj, event)

    def _navigate_to_cat(self, db_key: int):
        """Switch to Alive Cats view and select the given cat by db_key."""
        self._push_nav_history()
        self._nav_suppress = True
        try:
            self._filter(None, self._btn_all)
            if self._select_cats_by_db_keys([db_key]):
                return
            # Not found in Alive filter — try All Cats
            self._filter("__all__", self._btn_everyone)
            self._select_cats_by_db_keys([db_key])
        finally:
            self._nav_suppress = False

    def _find_visible_cat_row(self, db_key: int) -> Optional[int]:
        for row in range(self._proxy_model.rowCount()):
            src_idx = self._proxy_model.mapToSource(self._proxy_model.index(row, 0))
            cat = self._source_model.cat_at(src_idx.row())
            if cat is not None and cat.db_key == db_key:
                return row
        return None

    def _select_cats_by_db_keys(self, db_keys: list[int]) -> int:
        selection_model = self._table.selectionModel()
        if selection_model is None:
            return 0

        indexes = []
        for db_key in db_keys:
            row = self._find_visible_cat_row(db_key)
            if row is None:
                continue
            indexes.append(self._proxy_model.index(row, 0))

        if not indexes:
            return 0

        selection_model.blockSignals(True)
        try:
            selection = QItemSelection()
            for idx in indexes:
                selection.select(idx, idx)
            selection_model.select(
                selection,
                QItemSelectionModel.ClearAndSelect | QItemSelectionModel.Rows,
            )
            self._table.scrollTo(indexes[0])
            selection_model.setCurrentIndex(indexes[0], QItemSelectionModel.NoUpdate)
        finally:
            selection_model.blockSignals(False)

        self._on_selection()
        return len(selection_model.selectedRows())

    def _cats_by_db_keys(self, db_keys: list[int]) -> list[Cat]:
        lookup = getattr(self, "_cat_lookup", None) or {cat.db_key: cat for cat in self._cats}
        cats: list[Cat] = []
        seen: set[int] = set()
        for db_key in db_keys:
            if db_key in seen:
                continue
            seen.add(db_key)
            cat = lookup.get(db_key)
            if cat is not None:
                cats.append(cat)
        return cats

    def _navigate_to_cat_pair(self, db_key_a: int, db_key_b: int):
        """Switch to Alive Cats view and select both cats in the pair."""
        self._push_nav_history()
        self._pair_detail_override = True
        self._nav_suppress = True
        try:
            self._clear_pair_navigation_filters()
            self._filter(None, self._btn_all)
            pair_cats = self._cats_by_db_keys([db_key_a, db_key_b])
            if self._select_cats_by_db_keys([db_key_a, db_key_b]) < 2:
                # Fall back to All Cats if one of the cats is hidden by the Alive filter.
                self._clear_pair_navigation_filters()
                self._filter("__all__", self._btn_everyone)
                self._select_cats_by_db_keys([db_key_a, db_key_b])
            if len(pair_cats) == 2:
                self._source_model.set_focus_cat(None)
                self._detail.show_cats(pair_cats)
        finally:
            self._pair_detail_override = False
            self._nav_suppress = False

    def _clear_pair_navigation_filters(self):
        """Clear filters that can hide one cat from a pair jump."""
        if hasattr(self, "_search") and self._search.text():
            self._search.clear()
            self._proxy_model.set_name_filter("")

        if getattr(self._proxy_model, "tag_filter", set()):
            self._clear_tag_filter()

        if hasattr(self, "_pin_toggle") and self._pin_toggle.isChecked():
            self._pin_toggle.blockSignals(True)
            try:
                self._pin_toggle.setChecked(False)
            finally:
                self._pin_toggle.blockSignals(False)
            self._proxy_model.set_pinned_only(False)
            self._update_count()

    def _navigate_to_cat_by_name(self, cat_name_formatted: str):
        """Navigate to a cat by its formatted name (e.g. 'Fluffy (Female)')."""
        cat_name = cat_name_formatted.split(" (")[0] if " (" in cat_name_formatted else cat_name_formatted
        cat_name = cat_name.replace(" \u2665", "")
        for cat in self._cats:
            if cat.name == cat_name:
                self._navigate_to_cat(cat.db_key)
                return

    def _update_header(self, room_key):
        if room_key == "__all__":
            self._header_lbl.setText(_tr("header.filter.all_cats"))
            self._mode_badge_lbl.setVisible(False)
        elif room_key is None:
            self._header_lbl.setText(_tr("header.filter.alive"))
            total_stats_mode = hasattr(self, "_source_model") and self._source_model is not None and self._source_model.show_total_stats()
            self._mode_badge_lbl.setText(_tr("header.badge.total_stats", default="TOTAL STATS"))
            self._mode_badge_lbl.setToolTip(
                _tr(
                    "header.badge.total_stats.tooltip",
                    default="Roster is showing total stats instead of base stats.",
                )
            )
            self._mode_badge_lbl.setVisible(total_stats_mode)
        elif room_key == "__exceptional__":
            self._header_lbl.setText(_tr("header.filter.exceptional"))
            self._mode_badge_lbl.setVisible(False)
        elif room_key == "__donation__":
            self._header_lbl.setText(_tr("header.filter.donation"))
            self._mode_badge_lbl.setVisible(False)
        elif room_key == "__gone__":
            self._header_lbl.setText(_tr("header.filter.gone"))
            self._mode_badge_lbl.setVisible(False)
        elif room_key == "__fight_club__":
            self._header_lbl.setText(_tr("header.filter.fight_club", default="Fight Club"))
            self._mode_badge_lbl.setText(_tr("header.badge.full_stats", default="FULL STATS"))
            self._mode_badge_lbl.setToolTip(
                _tr(
                    "header.badge.full_stats.tooltip",
                    default="Fight Club shows total stats and the adventure attributes for eligible cats.",
                )
            )
            self._mode_badge_lbl.setVisible(True)
        elif room_key == "__adventure__":
            self._header_lbl.setText(_tr("header.filter.adventure"))
            self._mode_badge_lbl.setVisible(False)
        else:
            self._header_lbl.setText(ROOM_DISPLAY.get(room_key, room_key))
            self._mode_badge_lbl.setVisible(False)

    def _apply_fight_club_layout(self, enabled: bool, force: bool = False):
        enabled = bool(enabled)
        if enabled == self._fight_club_layout_active and not force:
            return
        if not hasattr(self, "_table") or self._table.model() is None or not hasattr(self, "_source_model"):
            self._fight_club_layout_active = enabled
            return

        self._fight_club_layout_active = enabled
        col_count = self._table.model().columnCount()
        if enabled:
            if not self._fight_club_hidden_state or not force:
                self._fight_club_hidden_state = {
                    col: self._table.isColumnHidden(col)
                    for col in range(col_count)
                }
                self._fight_club_prev_total_stats = self._source_model.show_total_stats()
            self._source_model.set_show_total_stats(True)
            if hasattr(self, "_total_stats_action"):
                self._total_stats_action.blockSignals(True)
                try:
                    self._total_stats_action.setChecked(True)
                    self._total_stats_action.setEnabled(False)
                finally:
                    self._total_stats_action.blockSignals(False)

            for col in (COL_GEN, COL_BL, COL_MB, COL_LIB, COL_INBRD, COL_SEXUALITY,
                        COL_GEN_DEPTH, COL_SRC, COL_REL):
                if col < col_count:
                    self._table.setColumnHidden(col, True)
            for col in (COL_TAGS, COL_NAME, COL_ROOM, COL_STAT, COL_SUM, COL_AGG, COL_ADV,
                        COL_AGE, COL_PIN, COL_ABIL, COL_MUTS):
                if col < col_count:
                    self._table.setColumnHidden(col, False)
            if hasattr(self, "_fight_club_abilities_filter"):
                self._fight_club_abilities_filter.setVisible(True)
            if hasattr(self, "_fight_club_mutations_filter"):
                self._fight_club_mutations_filter.setVisible(True)
            self._pin_roster_special_columns()
        else:
            self._source_model.set_show_total_stats(self._fight_club_prev_total_stats)
            if hasattr(self, "_total_stats_action"):
                self._total_stats_action.blockSignals(True)
                try:
                    self._total_stats_action.setEnabled(True)
                    self._total_stats_action.setChecked(self._fight_club_prev_total_stats)
                finally:
                    self._total_stats_action.blockSignals(False)
            hidden_state = self._fight_club_hidden_state or {}
            for col in range(col_count):
                self._table.setColumnHidden(col, hidden_state.get(col, False))
            self._fight_club_hidden_state = {}
            if hasattr(self, "_fight_club_abilities_filter"):
                self._fight_club_abilities_filter.setVisible(False)
            if hasattr(self, "_fight_club_mutations_filter"):
                self._fight_club_mutations_filter.setVisible(False)
            self._restore_roster_table_defaults()

    def _current_room_key(self):
        if self._active_btn is None:
            return None
        for key, btn in self._room_btns.items():
            if btn is self._active_btn:
                return key
        return None

    def _update_count(self):
        visible = self._proxy_model.rowCount()
        total   = self._source_model.rowCount()
        room_key = self._current_room_key()
        if room_key in ("__exceptional__", "__donation__"):
            summary = _current_threshold_summary(self._cats)
            if room_key == "__exceptional__":
                self._count_lbl.setText(
                    _tr(
                        "header.count_exceptional",
                        visible=visible,
                        total=total,
                        threshold=summary["exceptional"],
                    )
                )
            else:
                self._count_lbl.setText(
                    _tr(
                        "header.count_donation",
                        visible=visible,
                        total=total,
                        threshold=summary["donation"],
                    )
                )
        elif room_key == "__fight_club__":
            self._count_lbl.setText(
                _tr(
                    "header.count_fight_club",
                    visible=visible,
                    total=total,
                    default="  {visible} / {total} adventure-ready cats",
                )
            )
        else:
            self._count_lbl.setText(_tr("header.count", visible=visible, total=total))

        placed = sum(1 for c in self._cats if c.status == "In House")
        adv    = sum(1 for c in self._cats if c.status == "Adventure")
        gone   = sum(1 for c in self._cats if c.status == "Gone")
        self._summary_lbl.setText(_tr("header.summary", placed=placed, adv=adv, gone=gone))

    def _on_pin_toggle(self, checked: bool):
        self._proxy_model.set_pinned_only(checked)
        self._update_count()

    def _show_tags_menu(self):
        """Show dropdown menu to apply/remove tags on selected cats."""
        selected_cats = self._get_selected_cats()
        menu = QMenu(self)
        menu.setStyleSheet(
            "QMenu { background:#1a1a32; color:#ddd; border:1px solid #2a2a4a; padding:4px; }"
            "QMenu::item { padding:4px 16px; }"
            "QMenu::item:selected { background:#252545; }"
            "QMenu::separator { height:1px; background:#2a2a4a; margin:4px 8px; }"
        )

        if not _TAG_DEFS:
            no_tags = menu.addAction("No tags defined — open Manage Tags")
            no_tags.triggered.connect(self._open_tag_manager)
        else:
            header = menu.addAction("Apply Tags")
            header.setEnabled(False)
            menu.addSeparator()

            if not selected_cats:
                hint = menu.addAction("Select cats first, then apply tags")
                hint.setEnabled(False)
                menu.addSeparator()

            for td in _TAG_DEFS:
                tid = td["id"]
                label = td["name"] if td["name"] else ""
                # Show check if ALL selected cats have this tag
                all_have = bool(selected_cats) and all(tid in _cat_tags(c) for c in selected_cats)
                action = menu.addAction(f"  \u25CF  {label}")
                action.setCheckable(True)
                action.setChecked(all_have)
                # Color the dot via rich icon
                pix = QPixmap(12, 12)
                pix.fill(Qt.transparent)
                p = QPainter(pix)
                p.setRenderHint(QPainter.Antialiasing)
                p.setBrush(QBrush(QColor(td["color"])))
                p.setPen(Qt.NoPen)
                p.drawEllipse(1, 1, 10, 10)
                p.end()
                action.setIcon(QIcon(pix))
                action.triggered.connect(
                    lambda checked, tag_id=tid: self._apply_tag_to_selection(tag_id, checked)
                )

            menu.addSeparator()
            clear_action = menu.addAction("Clear all tags from selection")
            clear_action.setEnabled(bool(selected_cats))
            clear_action.triggered.connect(self._clear_tags_from_selection)

            # ── Filter section ──
            menu.addSeparator()
            filter_label = menu.addAction("Show only:")
            filter_label.setEnabled(False)

            current_filter = self._proxy_model.tag_filter
            show_all = menu.addAction("All cats")
            show_all.setCheckable(True)
            show_all.setChecked(not current_filter)
            show_all.triggered.connect(self._clear_tag_filter)

            for td in _TAG_DEFS:
                tid = td["id"]
                label = td["name"] if td["name"] else "\u25CF"
                is_active = tid in current_filter
                pix = QPixmap(12, 12)
                pix.fill(Qt.transparent)
                p = QPainter(pix)
                p.setRenderHint(QPainter.Antialiasing)
                p.setBrush(QBrush(QColor(td["color"])))
                p.setPen(Qt.NoPen)
                p.drawEllipse(1, 1, 10, 10)
                p.end()
                check_mark = "\u2713 " if is_active else "  "
                fa = menu.addAction(QIcon(pix), f"{check_mark}{label}")
                fa.setCheckable(True)
                fa.setChecked(is_active)
                fa.triggered.connect(
                    lambda checked, tag_id=tid: self._toggle_tag_filter(tag_id, checked)
                )

        menu.addSeparator()
        manage = menu.addAction("Manage Tags\u2026")
        manage.triggered.connect(self._open_tag_manager)

        menu.exec(self._tags_btn.mapToGlobal(
            self._tags_btn.rect().bottomLeft()))

    def _get_selected_cats(self) -> list:
        """Get currently selected cats from the main table."""
        rows = set()
        for idx in self._table.selectionModel().selectedRows():
            src = self._proxy_model.mapToSource(idx)
            rows.add(src.row())
        return [c for r in rows if (c := self._source_model.cat_at(r)) is not None]

    def _apply_tag_to_selection(self, tag_id: str, add: bool):
        """Add or remove a tag from all selected cats."""
        cats = self._get_selected_cats()
        if not cats:
            return
        _TAG_ICON_CACHE.clear()
        _TAG_PIX_CACHE.clear()
        for c in cats:
            current = list(getattr(c, 'tags', None) or [])
            if add and tag_id not in current:
                current.append(tag_id)
            elif not add and tag_id in current:
                current.remove(tag_id)
            c.tags = current
        # Refresh the tag column for affected rows
        for row in range(self._source_model.rowCount()):
            cat = self._source_model.cat_at(row)
            if cat in cats:
                idx = self._source_model.index(row, COL_TAGS)
                self._source_model.dataChanged.emit(
                    idx,
                    idx,
                    [Qt.DisplayRole, Qt.DecorationRole, Qt.ToolTipRole, Qt.UserRole],
                )
        if self._current_save:
            _save_tags(self._current_save, self._cats)
        if self._detail and self._detail.current_cats:
            self._detail.show_cats(self._detail.current_cats)

    def _clear_tags_from_selection(self):
        """Remove all tags from selected cats."""
        cats = self._get_selected_cats()
        if not cats:
            return
        _TAG_ICON_CACHE.clear()
        _TAG_PIX_CACHE.clear()
        for c in cats:
            c.tags = []
        for row in range(self._source_model.rowCount()):
            cat = self._source_model.cat_at(row)
            if cat in cats:
                idx = self._source_model.index(row, COL_TAGS)
                self._source_model.dataChanged.emit(
                    idx,
                    idx,
                    [Qt.DisplayRole, Qt.DecorationRole, Qt.ToolTipRole, Qt.UserRole],
                )
        if self._current_save:
            _save_tags(self._current_save, self._cats)
        if self._detail and self._detail.current_cats:
            self._detail.show_cats(self._detail.current_cats)

    def _tag_filtered_cats(self) -> list:
        """Return cats filtered by the active tag filter, or all cats if no filter."""
        f = self._proxy_model.tag_filter
        if not f:
            return self._cats
        return [c for c in self._cats if set(_cat_tags(c)) & f]

    def _toggle_tag_filter(self, tag_id: str, checked: bool):
        """Toggle a single tag in the filter set."""
        f = set(self._proxy_model.tag_filter)
        if checked:
            f.add(tag_id)
        else:
            f.discard(tag_id)
        self._proxy_model.set_tag_filter(f)
        self._update_count()
        self._refresh_views_for_tag_filter()
        # Visual indicator on the Tags button when filtering
        if f:
            self._tags_btn.setStyleSheet(
                "QPushButton { background:#2a3a2a; color:#8c8; border:1px solid #4a6a4a;"
                " border-radius:4px; padding:3px 10px; font-size:11px; font-weight:bold; }"
                "QPushButton:hover { background:#3a5a3a; color:#afa; }"
                "QPushButton::menu-indicator { image:none; }")
        else:
            self._tags_btn.setStyleSheet(
                "QPushButton { background:#1a1a32; color:#aaa; border:1px solid #2a2a4a;"
                " border-radius:4px; padding:3px 10px; font-size:11px; font-weight:bold; }"
                "QPushButton:hover { background:#252545; color:#ddd; }"
                "QPushButton::menu-indicator { image:none; }")

    def _refresh_views_for_tag_filter(self):
        """Push tag-filtered cat list to secondary views."""
        self._bump_cats_generation()
        filtered = self._tag_filtered_cats()
        if self._room_optimizer_view is not None and self._room_optimizer_view.isVisible():
            self._room_optimizer_view.set_cats(filtered)
            self._view_generation["room_optimizer"] = self._cats_generation
        if self._safe_breeding_view is not None and self._safe_breeding_view.isVisible():
            self._safe_breeding_view.set_cats(filtered)
            self._view_generation["safe_breeding"] = self._cats_generation
        if self._breeding_partners_view is not None and self._breeding_partners_view.isVisible():
            self._breeding_partners_view.set_cats(filtered)
            self._view_generation["breeding_partners"] = self._cats_generation
        if self._perfect_planner_view is not None and self._perfect_planner_view.isVisible():
            self._perfect_planner_view.set_cats(filtered)
            self._view_generation["perfect_planner"] = self._cats_generation
        if self._breed_priority_view is not None and self._breed_priority_view.isVisible():
            self._breed_priority_view.set_cats(filtered)
            self._view_generation["breed_priority"] = self._cats_generation

    def _clear_tag_filter(self):
        """Remove all tag filters."""
        self._proxy_model.set_tag_filter(set())
        self._update_count()
        self._refresh_views_for_tag_filter()
        self._tags_btn.setStyleSheet(
            "QPushButton { background:#1a1a32; color:#aaa; border:1px solid #2a2a4a;"
            " border-radius:4px; padding:3px 10px; font-size:11px; font-weight:bold; }"
            "QPushButton:hover { background:#252545; color:#ddd; }"
            "QPushButton::menu-indicator { image:none; }")

    def _open_tag_manager(self):
        dlg = TagManagerDialog(self)
        dlg.exec()
        _TAG_ICON_CACHE.clear()
        _TAG_PIX_CACHE.clear()
        if hasattr(self, "_source_model") and self._source_model is not None and self._source_model.rowCount() > 0:
            top_left = self._source_model.index(0, COL_TAGS)
            bottom_right = self._source_model.index(max(0, self._source_model.rowCount() - 1), COL_TAGS)
            self._source_model.dataChanged.emit(
                top_left,
                bottom_right,
                [Qt.DisplayRole, Qt.DecorationRole, Qt.ToolTipRole, Qt.UserRole],
            )
        if self._cats:
            self._bump_cats_generation()
            if self._tree_view is not None and self._tree_view.isVisible():
                self._set_view_cats_if_needed("tree", self._tree_view, self._cats)
            if self._safe_breeding_view is not None and self._safe_breeding_view.isVisible():
                self._set_view_cats_if_needed("safe_breeding", self._safe_breeding_view, self._cats)
            if self._breeding_partners_view is not None and self._breeding_partners_view.isVisible():
                self._set_view_cats_if_needed("breeding_partners", self._breeding_partners_view, self._cats)
            if self._room_optimizer_view is not None and self._room_optimizer_view.isVisible():
                self._set_view_cats_if_needed("room_optimizer", self._room_optimizer_view, self._cats)
            if self._perfect_planner_view is not None and self._perfect_planner_view.isVisible():
                self._set_view_cats_if_needed("perfect_planner", self._perfect_planner_view, self._cats)
            if self._calibration_view is not None and self._calibration_view.isVisible():
                self._calibration_view.set_context(self._current_save, self._cats)
                self._view_generation["calibration"] = self._cats_generation
        # Repaint table without invalidating selection
        self._table.viewport().update()
        if self._detail and self._detail.current_cats:
            self._detail.show_cats(self._detail.current_cats)
        if self._current_save:
            _save_tags(self._current_save, self._cats)

    def _on_blacklist_changed(self):
        if self._current_save:
            _save_blacklist(self._current_save, self._cats)
            _save_must_breed(self._current_save, self._cats)
            _save_pinned(self._current_save, self._cats)
            _save_tags(self._current_save, self._cats)
            _save_not_adventured(self._current_save, self._cats)
        self._refresh_bulk_view_buttons()
        self._bump_cats_generation()
        if self._safe_breeding_view is not None and self._safe_breeding_view.isVisible():
            self._set_view_cats_if_needed("safe_breeding", self._safe_breeding_view, self._cats)
        if self._breeding_partners_view is not None and self._breeding_partners_view.isVisible():
            self._set_view_cats_if_needed("breeding_partners", self._breeding_partners_view, self._cats)
        if self._room_optimizer_view is not None and self._room_optimizer_view.isVisible():
            self._set_view_cats_if_needed("room_optimizer", self._room_optimizer_view, self._cats)
        if self._perfect_planner_view is not None and self._perfect_planner_view.isVisible():
            self._set_view_cats_if_needed("perfect_planner", self._perfect_planner_view, self._cats)

    def _on_calibration_changed(self):
        if not self._current_save:
            return
        cal_explicit, cal_token, cal_rows = _apply_calibration(self._current_save, self._cats)
        self._source_model.load(self._cats)
        self._refresh_filter_button_counts()
        self._bump_cats_generation()
        if self._safe_breeding_view is not None and self._safe_breeding_view.isVisible():
            self._set_view_cats_if_needed("safe_breeding", self._safe_breeding_view, self._cats)
        if self._breeding_partners_view is not None and self._breeding_partners_view.isVisible():
            self._set_view_cats_if_needed("breeding_partners", self._breeding_partners_view, self._cats)
        if self._room_optimizer_view is not None and self._room_optimizer_view.isVisible():
            self._set_view_cats_if_needed("room_optimizer", self._room_optimizer_view, self._cats)
        if self._perfect_planner_view is not None and self._perfect_planner_view.isVisible():
            self._set_view_cats_if_needed("perfect_planner", self._perfect_planner_view, self._cats)
        if self._calibration_view is not None and self._calibration_view.isVisible():
            self._calibration_view.set_context(self._current_save, self._cats)
            self._view_generation["calibration"] = self._cats_generation
        self._update_count()
        self.statusBar().showMessage(
            _tr("status.calibration_applied", default="Calibration applied ({explicit} explicit, {token} token from {rows} rows)", explicit=cal_explicit, token=cal_token, rows=cal_rows)
        )

    # ── Breeding cache ──────────────────────────────────────────────────

    @staticmethod
    def _cache_cat_fingerprint(cat: 'Cat') -> tuple:
        """Tuple of every field that affects cache computation (not room/display)."""
        return _breeding_cache_fingerprint(cat)

    def _only_display_changed(self, new_cats: list['Cat']) -> bool:
        """Return True if self._cats and new_cats differ only in display fields (e.g. room)."""
        if not self._cats:
            return False
        old_fps = {c.db_key: self._cache_cat_fingerprint(c) for c in self._cats}
        new_fps = {c.db_key: self._cache_cat_fingerprint(c) for c in new_cats}
        return old_fps == new_fps

    def _start_breeding_cache(self, cats: list[Cat], force_full: bool = False):
        """Kick off background computation of the breeding cache."""
        # Fast path: skip rebuild when only display fields (e.g. room) changed
        if (not force_full
                and self._breeding_cache is not None
                and self._breeding_cache.ready
                and self._only_display_changed(cats)):
            # Refresh cat object references so views see updated rooms
            self._breeding_cache.refresh_cat_index(cats)
            # Keep _prev_parent_keys current for the next reload's incremental check
            self._prev_parent_keys = {
                c.db_key: (
                    c.parent_a.db_key if c.parent_a is not None else None,
                    c.parent_b.db_key if c.parent_b is not None else None,
                )
                for c in cats
            }
            return

        # Retire any in-progress cache worker so it cleans up properly.
        # The stale worker's phase1_ready / finished_cache slots drop
        # the result by identity check (`is self._cache_worker`).
        self._retire_worker(self._cache_worker)
        self._cache_worker = None

        # Snapshot parent keys before clearing old cache (for incremental update)
        prev_cache = self._breeding_cache if not force_full else None
        prev_parent_keys = dict(self._prev_parent_keys) if hasattr(self, "_prev_parent_keys") and not force_full else {}

        # Record current parent keys for next reload
        self._prev_parent_keys = {
            c.db_key: (
                c.parent_a.db_key if c.parent_a is not None else None,
                c.parent_b.db_key if c.parent_b is not None else None,
            )
            for c in cats
        }

        self._breeding_cache = None
        self._cache_progress.setValue(0)
        self._cache_progress.show()

        # Try loading pairwise data from disk (skip if force_full)
        existing = None
        save_path = self._current_save or ""
        save_signature = _breeding_save_signature(cats)
        pedigree_coi_memos = getattr(self, "_pedigree_coi_memos", {})
        if not force_full and save_path:
            existing = BreedingCache.load_from_disk(save_path, save_signature)
            if existing is not None:
                self._cache_progress.setFormat(_tr("loading.cache.loading_cached"))
            elif prev_cache is not None:
                self._cache_progress.setFormat(_tr("loading.cache.updating"))
            else:
                self._cache_progress.setFormat(_tr("loading.cache.computing"))
        else:
            self._cache_progress.setFormat(_tr("loading.cache.computing"))

        worker = BreedingCacheWorker(
            cats, save_path=save_path, existing_pairwise=existing,
            prev_cache=prev_cache, prev_parent_keys=prev_parent_keys,
            save_signature=save_signature,
            pedigree_coi_memos=pedigree_coi_memos,
            parent=self,
        )
        worker.progress.connect(
            lambda cur, tot, w=worker: self._on_cache_progress(cur, tot, w)
        )
        worker.phase1_ready.connect(
            lambda cache, w=worker: self._on_phase1_ready(cache, source_worker=w)
        )
        worker.finished_cache.connect(
            lambda cache, w=worker: self._on_cache_ready(cache, source_worker=w)
        )
        worker.finished.connect(lambda w=worker: self._cache_progress.hide() if w is self._cache_worker else None)
        self._cache_worker = worker
        worker.start()

    def _on_cache_progress(self, current: int, total: int,
                           source_worker: Optional[BreedingCacheWorker] = None):
        if source_worker is not None and source_worker is not self._cache_worker:
            return  # stale worker — ignore
        self._cache_progress.setMaximum(total)
        self._cache_progress.setValue(current)

    def _clear_breeding_cache(self):
        """Delete the on-disk breeding cache for the current save file."""
        if not self._current_save:
            self.statusBar().showMessage(_tr("status.no_save_loaded_clear"))
            return
        cp = _breeding_cache_path(self._current_save)
        if os.path.exists(cp):
            try:
                os.remove(cp)
                self.statusBar().showMessage(_tr("status.cache_cleared"))
            except OSError as e:
                self.statusBar().showMessage(_tr("status.cache_delete_failed", default="Could not delete cache: {error}", error=e))
        else:
            self.statusBar().showMessage(_tr("status.cache_missing"))

    def _on_phase1_ready(self, cache: BreedingCache, source_worker: Optional[BreedingCacheWorker] = None):
        """Ancestry computed — push to table and Mating Pair Search so they're usable immediately."""
        # Drop results from superseded workers (a newer load_save has
        # started).  Without this check, a stale cache would overwrite
        # the fresh one currently being computed.
        if source_worker is not None and source_worker is not self._cache_worker:
            return
        self._breeding_cache = cache
        self._source_model.set_breeding_cache(cache)
        if self._safe_breeding_view is not None:
            self._safe_breeding_view.set_cache(cache)
        if self._perfect_planner_view is not None:
            self._perfect_planner_view.set_cache(cache)
        if self._room_optimizer_view is not None:
            self._room_optimizer_view.set_cache(cache)
        self._cache_progress.setFormat(_tr("loading.cache.pair_risks"))

    def _on_cache_ready(self, cache: BreedingCache, source_worker: Optional[BreedingCacheWorker] = None):
        if source_worker is not None and source_worker is not self._cache_worker:
            return  # superseded — drop stale result
        self._breeding_cache = cache
        self._cache_worker = None
        self._cache_progress.hide()
        # Push completed cache (now includes pairwise risk) to all views
        self._source_model.set_breeding_cache(cache)
        if self._safe_breeding_view is not None:
            self._safe_breeding_view.set_cache(cache)
        if self._room_optimizer_view is not None:
            self._room_optimizer_view.set_cache(cache)
        if self._perfect_planner_view is not None:
            self._perfect_planner_view.set_cache(cache)
        self.statusBar().showMessage(
            self.statusBar().currentMessage() + _tr("status.cache_ready_suffix", default="  |  Breeding cache ready")
        )

    def _on_save_load_failed(self, msg: str, is_transient: bool = True,
                              source_worker: Optional[SaveLoadWorker] = None):
        """Handle a SaveLoadWorker that raised during parsing.

        Only schedule a self-heal reload for transient I/O errors — a
        permanently broken save (corrupt file, parser bug, KeyError, etc.)
        must not spin the retry timer forever.  The next `fileChanged`
        event will re-trigger a load naturally if the game fixes the
        condition by writing a fresh save.
        """
        if source_worker is not None and source_worker is not self._save_load_worker:
            return  # superseded — a newer load is already running
        self._save_load_worker = None
        self._loading_overlay.hide()
        if is_transient and self._save_load_retries < self._SAVE_LOAD_RETRY_CAP:
            self._save_load_retries += 1
            self.statusBar().showMessage(
                _tr("status.save_load_failed",
                    default="Save load failed ({error}) — retrying in 500 ms.",
                    error=msg)
            )
            QTimer.singleShot(500, self._reload)
        else:
            # Permanent failure or retry budget exhausted.  Leave the
            # error visible and let a fresh fileChanged event (or a
            # manual reload) reset the counter on success.
            self.statusBar().showMessage(
                _tr("status.save_load_failed_permanent",
                    default="Save load failed ({error}). Open a different save or fix the file.",
                    error=msg)
            )

    # ── Worker lifecycle ─────────────────────────────────────────────────

    @staticmethod
    def _retire_worker(worker: Optional[QThread]) -> None:
        """Gracefully retire a superseded QThread worker.

        Requests cancellation so the worker can exit its loop early,
        then schedules deleteLater on its finished signal so the C++
        QThread is cleaned up once it actually stops — without blocking
        the main thread.  This prevents zombie QThread accumulation that
        was causing the ~1-minute crash cycle when the game rewrites its
        save while MBM is open.
        """
        if worker is None:
            return
        # Guard against double-retirement: if we already wired up
        # deleteLater, don't connect it again.
        if getattr(worker, '_retired', False):
            return
        worker._retired = True
        worker.requestInterruption()
        # deleteLater must run after the thread's event loop exits.
        # Connecting to `finished` is safe even if the thread already
        # finished — Qt queues the call and it becomes a no-op.
        try:
            worker.finished.connect(worker.deleteLater)
        except RuntimeError:
            pass  # C++ object already destroyed

    # ── Loading ────────────────────────────────────────────────────────────

    def load_save(self, path: str, force_full_breeding_cache: bool = False):
        previous_save = self._current_save
        fresh_save = True
        if previous_save:
            fresh_save = os.path.normcase(os.path.abspath(previous_save)) != os.path.normcase(os.path.abspath(path))
        if fresh_save:
            self._breeding_cache = None
            self._prev_parent_keys = {}
        self._current_save = path
        _set_last_save(path)
        if self._room_optimizer_view is not None:
            self._room_optimizer_view.set_save_path(path, refresh_existing=False)
        if self._perfect_planner_view is not None:
            self._perfect_planner_view.set_save_path(path, refresh_existing=False)
        if self._mutation_planner_view is not None:
            self._mutation_planner_view.set_save_path(path, refresh_existing=False, notify=False)
            if self._room_optimizer_view is not None:
                self._room_optimizer_view.on_planner_traits_changed()
            if self._perfect_planner_view is not None:
                self._perfect_planner_view.sync_mutation_traits()
                self._perfect_planner_view.sync_mutation_import_button_state()
        if self._watcher.files():
            self._watcher.removePaths(self._watcher.files())
        self._watcher.addPath(path)

        # Retire in-progress workers so they clean up properly instead
        # of accumulating as zombie QThreads.  requestInterruption lets
        # the worker exit early at its next check point, and finished
        # triggers deleteLater.  Identity checks in finished_load /
        # failed / phase1_ready / finished_cache slots discard any
        # stale results that slip through.
        self._retire_worker(self._save_load_worker)
        self._save_load_worker = None
        self._retire_worker(self._cache_worker)
        self._cache_worker = None

        # Show overlay while parsing (background thread — main thread stays responsive for repaint)
        name = os.path.basename(path)
        self._loading_label.setText(_tr("loading.save_named", name=name))
        overlay = self._loading_overlay
        parent = overlay.parentWidget()
        if parent:
            overlay.setGeometry(0, 0, parent.width(), parent.height())
        overlay.raise_()
        overlay.show()

        worker = SaveLoadWorker(path, parent=self)
        worker.finished_load.connect(
            lambda result, w=worker, force=force_full_breeding_cache:
                self._on_save_loaded(result, force, source_worker=w)
        )
        worker.failed.connect(
            lambda msg, transient, w=worker:
                self._on_save_load_failed(msg, transient, source_worker=w)
        )
        self._save_load_worker = worker
        worker.start()

    def _on_save_loaded(self, result: dict, force_full_breeding_cache: bool = False,
                        source_worker: Optional[SaveLoadWorker] = None):
        # Drop results from superseded workers — a newer load_save has
        # already started and points `_save_load_worker` at a different
        # instance.  Processing this result would overwrite fresh state
        # with stale data (or worse, if the stale worker managed to
        # complete during a crash-repro reload storm).
        if source_worker is not None and source_worker is not self._save_load_worker:
            return
        self._save_load_worker = None
        self._save_load_retries = 0  # success resets the self-heal counter
        # Dismiss overlay immediately — UI work below is fast (model.load is O(n), no ancestry)
        self._loading_overlay.hide()
        self._save_view_disabled = True
        try:
            cats = result["cats"]
            errors = result["errors"]
            unlocked_house_rooms = result.get("unlocked_house_rooms", [])
            accessible_cats = result.get("accessible_cats", set())
            self._accessible_cat_keys = set(accessible_cats)
            self._proxy_model.set_accessible_cats(accessible_cats)
            furniture = result.get("furniture", [])
            furniture_by_room = result.get("furniture_by_room", {})
            applied_overrides = result["applied_overrides"]
            override_rows = result["override_rows"]
            cal_explicit = result["cal_explicit"]
            cal_token = result["cal_token"]
            cal_rows = result["cal_rows"]
            self._pedigree_coi_memos = dict(result.get("pedigree_coi_memos", {}))

            self._cats = cats
            self._bump_cats_generation()
            self._furniture = furniture
            self._furniture_by_room = furniture_by_room
            self._furniture_data = dict(_FURNITURE_DATA)
            self._available_house_rooms = [room for room in ROOM_KEYS if room in set(unlocked_house_rooms)] or list(ROOM_KEYS)
            self._room_summaries = {
                summary.room: summary
                for summary in build_furniture_room_summaries(
                    self._furniture_by_room,
                    self._furniture_data,
                    self._cats,
                    room_order=self._available_house_rooms,
                )
                if summary.room in self._available_house_rooms or not summary.room
            }
            self._source_model.set_breeding_cache(None)
            if self._safe_breeding_view is not None:
                self._safe_breeding_view.set_cache(None)
            if self._breeding_partners_view is not None:
                self._breeding_partners_view.set_cache(None)
            if self._room_optimizer_view is not None:
                self._room_optimizer_view.set_cache(None)
            if self._perfect_planner_view is not None:
                self._perfect_planner_view.set_cache(None)
            self._refresh_threshold_runtime(cats)
            self._source_model.load(cats, accessible_cats=accessible_cats)
            self._rebuild_room_buttons(cats)
            self._refresh_filter_button_counts()
            self._filter(None, self._btn_all)
            if self._room_optimizer_view is not None:
                self._room_optimizer_view.set_available_rooms(self._available_house_rooms)
                self._room_optimizer_view.set_room_summaries(self._room_summaries)
            if self._furniture_view is not None:
                self._furniture_view.set_context(self._cats, self._furniture, self._furniture_data, available_rooms=self._available_house_rooms)
                self._view_generation["furniture"] = self._cats_generation
            # Cats are pushed to views on-demand when they become visible
            # (each _show_*_view calls _set_view_cats_if_needed).
            # _restore_current_view() in the finally block shows the active
            # view, which triggers the push for just that one view.

            # Initialize shared trait ratings
            if self._current_save:
                scoring_path = _scoring_path(self._current_save)
                self._trait_ratings = TraitRatings(scoring_path)
                if self._manual_scoring_view is not None:
                    self._manual_scoring_view.set_trait_ratings(self._trait_ratings)

            if self._calibration_view is not None:
                self._calibration_view.set_context(self._current_save, cats)
                self._view_generation["calibration"] = self._cats_generation
            self._sync_donation_planner_traits()
            name = os.path.basename(self._current_save)
            self._save_lbl.setText(name)
            self.setWindowTitle(_tr("app.title_with_save", name=name))

            msg = _tr("status.save_loaded", default="Loaded {count} cats from {name}", count=len(cats), name=name)
            if errors:
                msg += _tr("status.save_loaded.parse_errors_suffix", default="  ({count} parse errors)", count=len(errors))
            if applied_overrides:
                msg += _tr("status.save_loaded.gender_overrides_suffix", default="  ({applied}/{rows} gender overrides)", applied=applied_overrides, rows=override_rows)
            if cal_rows:
                msg += _tr("status.save_loaded.calibration_suffix", default="  (calibration: {explicit} explicit, {token} token)", explicit=cal_explicit, token=cal_token)
            self.statusBar().showMessage(msg)

            # Start background breeding cache computation
            self._start_breeding_cache(cats, force_full=force_full_breeding_cache)

            # Update default save menu items
            self._update_default_save_menu()
        except Exception as e:
            import traceback
            print(traceback.format_exc())
            self.statusBar().showMessage(_tr("status.save_load_failed", default="Error loading save: {error}", error=e))
        finally:
            self._save_view_disabled = False
            self._restore_current_view()

    def _update_default_save_menu(self):
        """Update the enabled state of default save menu items."""
        has_save = self._current_save is not None
        default_save = _saved_default_save()
        is_current_default = has_save and default_save == self._current_save

        self._set_default_save_action.setEnabled(has_save and not is_current_default)
        self._clear_default_save_action.setEnabled(has_save and is_current_default)

    def _set_current_as_default(self):
        """Set the current save file as the default."""
        if self._current_save:
            _set_default_save(self._current_save)
            name = os.path.basename(self._current_save)
            self.statusBar().showMessage(_tr("status.default_save_set", default="Default save set to: {name}", name=name))
            self._update_default_save_menu()

    def _clear_default_save(self):
        """Clear the default save setting."""
        _set_default_save(None)
        self.statusBar().showMessage(_tr("status.default_save_cleared", default="Default save cleared"))
        self._update_default_save_menu()

    def _flush_persistent_view_state(self):
        """Persist planner-style view state before the app shuts down."""
        if self._room_optimizer_view is not None:
            self._room_optimizer_view.save_session_state()
            _save_room_priority_config(self._room_optimizer_view.get_room_config(), self._room_optimizer_view.save_path)
        if self._perfect_planner_view is not None:
            self._perfect_planner_view.save_session_state()
        if self._mutation_planner_view is not None:
            self._mutation_planner_view.save_session_state()
        if self._furniture_view is not None:
            self._furniture_view.save_session_state()
        if self._manual_scoring_view is not None:
            self._manual_scoring_view.save_session_state()
        _bp_view = getattr(self, "_breed_priority_view", None)
        if _bp_view is not None:
            try:
                _bp_view.save_session_state()
            except Exception:
                pass
        if self._trait_ratings is not None:
            self._trait_ratings.save()

    def closeEvent(self, event):
        try:
            _save_window_geometry(self.saveGeometry().toBase64().data().decode("ascii"))
        except Exception:
            pass
        self._flush_persistent_view_state()
        # Stop background workers so they don't fire signals into a
        # half-destroyed widget tree during shutdown.
        self._retire_worker(self._save_load_worker)
        self._save_load_worker = None
        self._retire_worker(self._cache_worker)
        self._cache_worker = None
        self._retire_worker(self._quick_refresh_worker)
        self._quick_refresh_worker = None
        super().closeEvent(event)

    def showEvent(self, event):
        super().showEvent(event)
        if not self._startup_dialogs_shown:
            QTimer.singleShot(0, self._maybe_show_startup_dialogs)

    def _reset_ui_settings_to_defaults(self):
        """Reset pane sizes and planner inputs without touching save-file data."""
        confirm = QMessageBox.question(
            self,
            _tr("menu.settings.reset_ui_defaults.title"),
            _tr("menu.settings.reset_ui_defaults.body"),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return

        for view in (
            self._room_optimizer_view,
            self._perfect_planner_view,
            self._furniture_view,
            self._mutation_planner_view,
        ):
            if view is not None and hasattr(view, "reset_to_defaults"):
                view.reset_to_defaults()

        _set_room_optimizer_auto_recalc(False)
        _save_optimizer_search_settings(_OPTIMIZER_SEARCH_DEFAULTS)
        if hasattr(self, "_room_optimizer_auto_recalc_action"):
            self._room_optimizer_auto_recalc_action.blockSignals(True)
            self._room_optimizer_auto_recalc_action.setChecked(False)
            self._room_optimizer_auto_recalc_action.blockSignals(False)
        if self._room_optimizer_view is not None and hasattr(self._room_optimizer_view, "set_auto_recalculate"):
            self._room_optimizer_view.set_auto_recalculate(False)

        _set_manual_scoring_auto_calc(True)
        if hasattr(self, "_manual_scoring_auto_calc_action"):
            self._manual_scoring_auto_calc_action.blockSignals(True)
            self._manual_scoring_auto_calc_action.setChecked(True)
            self._manual_scoring_auto_calc_action.blockSignals(False)
        msv = getattr(self, "_manual_scoring_view", None)
        if msv is not None and hasattr(msv, "set_auto_recalculate"):
            msv.set_auto_recalculate(True)

        self._apply_accessibility_preset("Default")

        _set_total_stats_display(False)
        _set_stat_icon_mode(False)
        if hasattr(self, "_source_model"):
            self._source_model.set_show_total_stats(False)
            self._source_model.set_show_stat_icons(False)
        if hasattr(self, "_total_stats_action"):
            self._total_stats_action.blockSignals(True)
            try:
                self._total_stats_action.setChecked(False)
            finally:
                self._total_stats_action.blockSignals(False)
        if hasattr(self, "_stat_icons_action"):
            self._stat_icons_action.blockSignals(True)
            try:
                self._stat_icons_action.setChecked(False)
            finally:
                self._stat_icons_action.blockSignals(False)
        if getattr(self, "_fight_club_layout_active", False):
            if self._current_room_key() == "__fight_club__":
                self._fight_club_prev_total_stats = False
                self._apply_fight_club_layout(True, force=True)
            else:
                self._apply_fight_club_layout(False, force=True)

        if hasattr(self, "_detail_splitter") and self._detail_splitter is not None:
            total = max(20, self._detail_splitter.height())
            detail_h = min(240, max(10, total - 10))
            self._detail_splitter.setSizes([max(10, total - detail_h), detail_h])
            _save_splitter_state(self._detail_splitter)

        if hasattr(self, "_sidebar_splitter") and self._sidebar_splitter is not None:
            total = max(20, self._sidebar_splitter.width())
            sidebar_w = min(self._base_sidebar_width, max(10, total - 10))
            self._sidebar_splitter.setSizes([sidebar_w, max(10, total - sidebar_w)])
            _save_splitter_state(self._sidebar_splitter)

        self.statusBar().showMessage(
            _tr("status.ui_settings_reset", default="UI settings reset to defaults")
        )

    def _toggle_room_optimizer_auto_recalc(self, checked: bool):
        _set_room_optimizer_auto_recalc(bool(checked))
        if self._room_optimizer_view is not None and hasattr(self._room_optimizer_view, "set_auto_recalculate"):
            self._room_optimizer_view.set_auto_recalculate(bool(checked))

    def _toggle_manual_scoring_auto_calc(self, checked: bool):
        _set_manual_scoring_auto_calc(bool(checked))
        if self._manual_scoring_view is not None and hasattr(self._manual_scoring_view, "set_auto_recalculate"):
            self._manual_scoring_view.set_auto_recalculate(bool(checked))

    def _toggle_lineage(self, checked: bool):
        self._show_lineage = checked
        for col in (COL_GEN_DEPTH, COL_SRC):
            self._table.setColumnHidden(col, not checked)
        self._source_model.set_show_lineage(checked)
        self._detail.set_show_lineage(checked)
        self._on_selection()   # refresh detail panel with updated flag

    def _open_file(self):
        saves   = find_save_files()
        start   = os.path.dirname(saves[0]) if saves else os.path.expanduser("~")
        path, _ = QFileDialog.getOpenFileName(
            self,
            _tr("dialog.open_save.title"),
            start,
            _tr("dialog.open_save.filter"),
        )
        if path:
            self.load_save(path)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "_loading_overlay") and self._loading_overlay.isVisible():
            parent = self._loading_overlay.parentWidget()
            if parent:
                self._loading_overlay.setGeometry(0, 0, parent.width(), parent.height())

    def _export_cats(self):
        if not self._cats:
            QMessageBox.information(self, _tr("export.title", default="Export"), _tr("export.no_save", default="No save loaded."))
            return

        base = os.path.splitext(self._current_save)[0] if self._current_save else "cats"
        path, _ = QFileDialog.getSaveFileName(
            self, _tr("export.dialog_title", default="Export Cats"),
            base,
            "CSV (*.csv);;Excel (*.xlsx)"
        )
        if not path:
            return

        base_stat_headers  = ["Base " + s for s in STAT_NAMES]
        actual_stat_headers = ["Actual " + s for s in STAT_NAMES]
        headers = (
            ["Name", "Status", "Room", "Age", "Gender", "Sexuality", "Generation"]
            + base_stat_headers + ["Base Sum"]
            + actual_stat_headers + ["Actual Sum"]
            + ["Abilities", "Mutations", "Aggression", "Libido", "Inbreeding",
               "Pinned", "Blacklisted", "Must Breed", "Parent A", "Parent B"]
        )

        def _trait(val, field):
            if val is None:
                return ""
            return _trait_label_from_value(field, val)

        rows = []
        for cat in self._cats:
            base_vals   = [cat.base_stats.get(s, 0) for s in STAT_NAMES]
            actual_vals = [cat.total_stats.get(s, 0) for s in STAT_NAMES]
            row = (
                [
                    cat.name,
                    cat.status or "",
                    cat.room_display,
                    str(cat.age) if cat.age is not None else "",
                    cat.gender or "",
                    cat.sexuality or "",
                    str(cat.generation),
                ]
                + [str(v) for v in base_vals] + [str(sum(base_vals))]
                + [str(v) for v in actual_vals] + [str(sum(actual_vals))]
                + [
                    "; ".join(cat.abilities or []),
                    "; ".join(cat.mutations or []),
                    _trait(cat.aggression, "aggression"),
                    _trait(cat.libido, "libido"),
                    _trait(cat.inbredness, "inbredness"),
                    "Yes" if getattr(cat, "is_pinned", False) else "No",
                    "Yes" if getattr(cat, "is_blacklisted", False) else "No",
                    "Yes" if getattr(cat, "must_breed", False) else "No",
                    cat.parent_a.name if cat.parent_a else "",
                    cat.parent_b.name if cat.parent_b else "",
                ]
            )
            rows.append(row)

        ext = os.path.splitext(path)[1].lower()

        if ext == ".xlsx":
            try:
                import openpyxl
                from openpyxl.styles import Font
            except ImportError:
                QMessageBox.critical(self, _tr("export.title", default="Export"), "openpyxl is not installed. Install it with: pip install openpyxl")
                return
            wb = openpyxl.Workbook()
            ws = wb.active
            ws.title = "Cats"
            ws.append(headers)
            for cell in ws[1]:
                cell.font = Font(bold=True)
            for row in rows:
                ws.append(row)
            wb.save(path)
        else:
            if not path.lower().endswith(".csv"):
                path += ".csv"
            import csv
            with open(path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(headers)
                writer.writerows(rows)

        QMessageBox.information(self, _tr("export.title", default="Export"), f"Exported {len(rows)} cats to:\n{path}")

    def _reload(self):
        if self._current_save:
            self.load_save(self._current_save)

    def _on_file_changed_raw(self, path: str):
        """Queue a debounced refresh so bursts of fileChanged events
        (the game writes the save in rapid succession) collapse into a
        single quick-refresh instead of spawning racing workers."""
        if path != self._current_save:
            return
        # Qt's QFileSystemWatcher stops watching a file after it's deleted
        # or replaced — Mewgenics writes saves atomically by writing to a
        # temp file then renaming over the original, which fires exactly
        # one fileChanged event before the watcher drops the subscription.
        # Re-add the path so subsequent in-game days still trigger refreshes.
        if path not in self._watcher.files():
            # Defer slightly: Qt sometimes fires fileChanged before the new
            # inode is fully materialised on NTFS, so addPath() fails silently.
            QTimer.singleShot(100, lambda p=path: self._rewatch_save(p))
        self._pending_changed_path = path
        # Restart the timer on every event so the debounce window
        # resets after the latest burst write.
        self._file_change_timer.start()

    def _on_file_changed_debounced(self):
        path = self._pending_changed_path
        self._pending_changed_path = None
        if path is None or path != self._current_save:
            return
        # If cats are already loaded and no full reload is running, try the fast path.
        if self._cats and self._save_load_worker is None:
            self._start_quick_room_refresh()
        else:
            self._reload()

    def _rewatch_save(self, path: str):
        """Re-subscribe the file watcher to *path* after the game rewrites it."""
        if path != self._current_save:
            return
        if not os.path.isfile(path):
            # File not materialised yet — try again shortly.
            QTimer.singleShot(100, lambda p=path: self._rewatch_save(p))
            return
        if path in self._watcher.files():
            return
        self._watcher.addPath(path)

    def _start_quick_room_refresh(self):
        # Retire the previous worker so it doesn't accumulate as a
        # zombie QThread.  We also bump the generation token so the old
        # worker's queued room_patch signal is recognised as stale by
        # `_on_room_patch` and silently dropped.
        self._retire_worker(self._quick_refresh_worker)
        self._quick_refresh_worker = None
        self._quick_refresh_generation += 1
        gen = self._quick_refresh_generation
        expected = {c.db_key for c in self._cats}
        w = QuickRoomRefreshWorker(self._current_save, expected, generation=gen, parent=self)
        w.room_patch.connect(self._on_room_patch)
        w.needs_full_reload.connect(self._on_quick_refresh_needs_full_reload)
        self._quick_refresh_worker = w
        w.start()

    def _on_quick_refresh_needs_full_reload(self, generation: int):
        """Quick refresh fell back — do a full reload, but only if this
        signal came from the current worker (stale ones are ignored)."""
        if generation != self._quick_refresh_generation:
            return
        self._reload()

    def _on_room_patch(self, generation: int, patch: dict):
        # Drop signals from superseded workers.  Without this guard, a
        # stale worker's room_patch can fire after the next refresh has
        # already mutated `self._cats` or `_rebuild_room_buttons()` has
        # deleteLater()'d its widgets — either path risks a crash.
        if generation != self._quick_refresh_generation:
            return
        self._quick_refresh_worker = None
        # Wrap the whole body in try/except: unlike `_on_save_loaded`,
        # this slot used to run bare, so any Qt-object-lifetime or view
        # bookkeeping error propagated to the event loop and aborted the
        # app.  On failure, log to the status bar and fall back to a
        # full reload — the user-visible symptom is a brief flicker
        # instead of a crash.
        try:
            # Safe to mutate cat.room / cat.status here even if a
            # BreedingCacheWorker is running — the worker snapshots the
            # alive list at construction time and never reads cat.status
            # from the live objects.
            changed = self._source_model.apply_room_patch(patch)
            # Refresh the cache's cat-by-key index so it sees updated
            # rooms/statuses without a full rebuild.
            if changed and self._breeding_cache is not None:
                self._breeding_cache.refresh_cat_index(self._cats)
            self._rebuild_room_buttons(self._cats)
            self._refresh_filter_button_counts()
            self._bump_cats_generation()
            if self._furniture_view is not None:
                self._furniture_view.set_context(self._cats, self._furniture, self._furniture_data, available_rooms=self._available_house_rooms)
                self._view_generation["furniture"] = self._cats_generation
            if self._tree_view is not None and self._tree_view.isVisible():
                self._set_view_cats_if_needed("tree", self._tree_view, self._cats)
            if self._safe_breeding_view is not None and self._safe_breeding_view.isVisible():
                self._set_view_cats_if_needed("safe_breeding", self._safe_breeding_view, self._cats)
            if self._breeding_partners_view is not None and self._breeding_partners_view.isVisible():
                self._set_view_cats_if_needed("breeding_partners", self._breeding_partners_view, self._cats)
            if self._room_optimizer_view is not None and self._room_optimizer_view.isVisible():
                self._set_view_cats_if_needed("room_optimizer", self._room_optimizer_view, self._cats)
            if self._perfect_planner_view is not None and self._perfect_planner_view.isVisible():
                self._set_view_cats_if_needed("perfect_planner", self._perfect_planner_view, self._cats)
            if self._calibration_view is not None and self._calibration_view.isVisible():
                self._calibration_view.set_context(self._current_save, self._cats)
                self._view_generation["calibration"] = self._cats_generation
            self.statusBar().showMessage(_tr("status.rooms_refreshed", default="Room locations updated."))
        except Exception as exc:  # noqa: BLE001 — last line of defence before the event loop
            self.statusBar().showMessage(
                _tr("status.quick_refresh_failed",
                    default="Quick refresh failed ({error}) — reloading save.",
                    error=repr(exc))
            )
            # Full reload regenerates all widgets from scratch, clearing
            # any dangling references that caused the failure.
            QTimer.singleShot(0, self._reload)

    def _open_tree_browser(self):
        self._push_nav_history()
        _save_current_view("tree")
        self._show_tree_view()
        rows = list({
            self._proxy_model.mapToSource(idx).row()
            for idx in self._table.selectionModel().selectedRows()
        })
        cats = [c for r in rows[:1] if (c := self._source_model.cat_at(r)) is not None]
        if cats and self._tree_view is not None:
            self._tree_view.select_cat(cats[0])

    def _open_tree_for_cat(self, cat: Cat):
        if cat is None:
            return
        self._navigate_to_cat(cat.db_key)
        self._open_tree_browser()

    def _open_safe_breeding_view(self, quality: Optional[bool] = None):
        self._push_nav_history()
        if quality is not None:
            self._safe_breeding_quality_mode = bool(quality)
        _save_current_view("safe_breeding")
        self._show_safe_breeding_view()
        rows = list({
            self._proxy_model.mapToSource(idx).row()
            for idx in self._table.selectionModel().selectedRows()
        })
        cats = [c for r in rows[:1] if (c := self._source_model.cat_at(r)) is not None]
        if cats and self._safe_breeding_view is not None:
            self._safe_breeding_view.select_cat(cats[0])

    def _open_safe_breeding_for_cat(self, cat: Cat, quality: Optional[bool] = None):
        if cat is None:
            return
        self._navigate_to_cat(cat.db_key)
        self._open_safe_breeding_view(quality=quality)

    def _open_breeding_partners_view(self):
        self._push_nav_history()
        _save_current_view("breeding_partners")
        self._show_breeding_partners_view()

    def _open_perfect_planner_for_cat(self, cat: Cat):
        if cat is None:
            return
        self._navigate_to_cat(cat.db_key)
        self._open_perfect_planner_view()

    def _open_room_optimizer(self):
        self._push_nav_history()
        _save_current_view("room_optimizer")
        self._show_room_optimizer_view()

    def _open_perfect_planner_view(self):
        self._push_nav_history()
        _save_current_view("perfect_planner")
        self._show_perfect_planner_view()

    def _open_calibration_view(self):
        self._push_nav_history()
        _save_current_view("calibration")
        self._show_calibration_view()

    def _open_mutation_planner_view(self):
        self._push_nav_history()
        _save_current_view("mutation_planner")
        self._show_mutation_planner_view()

    def _open_furniture_view(self):
        self._push_nav_history()
        _save_current_view("furniture")
        self._show_furniture_view()

    def _open_manual_scoring_view(self):
        self._push_nav_history()
        _save_current_view("manual_scoring")
        self._show_manual_scoring_view()

    def _show_manual_scoring_view(self):
        self._ensure_manual_scoring_view()
        if self._active_btn is not None:
            self._active_btn.setChecked(False)
        self._active_btn = None
        if hasattr(self, "_header"):
            self._header.hide()
        if hasattr(self, "_table_view_container"):
            self._table_view_container.hide()
        if hasattr(self, "_tree_view") and self._tree_view is not None:
            self._tree_view.hide()
        if hasattr(self, "_safe_breeding_view") and self._safe_breeding_view is not None:
            self._safe_breeding_view.hide()
        if hasattr(self, "_breeding_partners_view") and self._breeding_partners_view is not None:
            self._breeding_partners_view.hide()
        if hasattr(self, "_room_optimizer_view") and self._room_optimizer_view is not None:
            self._room_optimizer_view.hide()
        if hasattr(self, "_perfect_planner_view") and self._perfect_planner_view is not None:
            self._perfect_planner_view.hide()
        if hasattr(self, "_calibration_view") and self._calibration_view is not None:
            self._calibration_view.hide()
        if hasattr(self, "_mutation_planner_view") and self._mutation_planner_view is not None:
            self._mutation_planner_view.hide()
        if hasattr(self, "_furniture_view") and self._furniture_view is not None:
            self._furniture_view.hide()
        if hasattr(self, "_breed_priority_view") and self._breed_priority_view is not None:
            self._breed_priority_view.hide()
        if self._manual_scoring_view is not None:
            self._set_view_cats_if_needed("manual_scoring", self._manual_scoring_view, self._cats)
            self._manual_scoring_view.show()
        if hasattr(self, "_btn_tree_view"):
            self._btn_tree_view.setChecked(False)
        if hasattr(self, "_btn_safe_breeding_view"):
            self._btn_safe_breeding_view.setChecked(False)
        if hasattr(self, "_btn_breeding_partners_view"):
            self._btn_breeding_partners_view.setChecked(False)
        if hasattr(self, "_btn_room_optimizer"):
            self._btn_room_optimizer.setChecked(False)
        if hasattr(self, "_btn_perfect_planner"):
            self._btn_perfect_planner.setChecked(False)
        if hasattr(self, "_btn_calibration"):
            self._btn_calibration.setChecked(False)
        if hasattr(self, "_btn_mutation_planner"):
            self._btn_mutation_planner.setChecked(False)
        if hasattr(self, "_btn_furniture_view"):
            self._btn_furniture_view.setChecked(False)
        if hasattr(self, "_btn_manual_scoring"):
            self._btn_manual_scoring.setChecked(True)
        if hasattr(self, "_btn_breed_priority"):
            self._btn_breed_priority.setChecked(False)

    def _open_breed_priority_view(self):
        self._push_nav_history()
        _save_current_view("breed_priority")
        self._show_breed_priority_view()

    def _show_breed_priority_view(self):
        self._ensure_breed_priority_view()
        if self._active_btn is not None:
            self._active_btn.setChecked(False)
        self._active_btn = None
        if hasattr(self, "_header"):
            self._header.hide()
        if hasattr(self, "_table_view_container"):
            self._table_view_container.hide()
        for view_attr in ("_tree_view", "_safe_breeding_view", "_breeding_partners_view",
                          "_room_optimizer_view", "_perfect_planner_view", "_calibration_view",
                          "_mutation_planner_view", "_furniture_view", "_manual_scoring_view"):
            v = getattr(self, view_attr, None)
            if v is not None:
                v.hide()
        if self._breed_priority_view is not None:
            self._set_view_cats_if_needed("breed_priority", self._breed_priority_view, self._cats)
            self._breed_priority_view.show()
        for btn_attr in ("_btn_tree_view", "_btn_safe_breeding_view", "_btn_breeding_partners_view",
                         "_btn_room_optimizer", "_btn_perfect_planner", "_btn_calibration",
                         "_btn_mutation_planner", "_btn_furniture_view", "_btn_manual_scoring"):
            btn = getattr(self, btn_attr, None)
            if btn is not None:
                btn.setChecked(False)
        if hasattr(self, "_btn_breed_priority"):
            self._btn_breed_priority.setChecked(True)

    def _restore_current_view(self):
        """Restore the last-used view after a save is loaded."""
        view = _load_current_view()
        _restore_map = {
            "tree":               self._show_tree_view,
            "fight_club":         (lambda: self._filter(None, self._btn_all) if hasattr(self, "_btn_all") else self._show_table_view()),
            "safe_breeding":      self._show_safe_breeding_view,
            "breeding_partners":  self._show_breeding_partners_view,
            "room_optimizer":     self._show_room_optimizer_view,
            "perfect_planner":    self._show_perfect_planner_view,
            "calibration":        self._show_calibration_view,
            "mutation_planner":   self._show_mutation_planner_view,
            "furniture":          self._show_furniture_view,
            "manual_scoring":     self._show_manual_scoring_view,
            "breed_priority":     self._show_breed_priority_view,
        }
        fn = _restore_map.get(view)
        if fn:
            fn()

    def _toggle_single_cat_pin(self, cat: Cat):
        if cat is None:
            return
        cat.is_pinned = not cat.is_pinned
        self._emit_bulk_toggle_refresh()
        self.statusBar().showMessage(_tr("bulk.status.toggled_pin", default="Toggled pin for 1 selected cat", count=1))

    def _toggle_single_cat_must_breed(self, cat: Cat):
        if cat is None:
            return
        cat.must_breed = not cat.must_breed
        if cat.must_breed:
            cat.is_blacklisted = False
        self._emit_bulk_toggle_refresh()
        self.statusBar().showMessage(_tr("bulk.status.toggled_must_breed", default="Toggled must breed for 1 selected cat", count=1))

    def _toggle_single_cat_blacklist(self, cat: Cat):
        if cat is None:
            return
        cat.is_blacklisted = not cat.is_blacklisted
        if cat.is_blacklisted:
            cat.must_breed = False
        self._emit_bulk_toggle_refresh()
        self.statusBar().showMessage(_tr("bulk.status.toggled_breeding_block", default="Toggled breeding block for 1 selected cat", count=1))

    # ── UI zoom ───────────────────────────────────────────────────────────

    def _scaled(self, value: int) -> int:
        return max(1, round(value * (self._zoom_percent / 100.0)))

    def _update_zoom_info_action(self):
        if hasattr(self, "_zoom_info_action"):
            self._zoom_info_action.setText(_tr("menu.settings.zoom_info", percent=self._zoom_percent))

    def _set_zoom(self, percent: int):
        clamped = max(_ZOOM_MIN, min(_ZOOM_MAX, int(percent)))
        if clamped == self._zoom_percent:
            return
        self._zoom_percent = clamped
        _set_zoom_percent(clamped)
        self._apply_zoom()
        self._update_zoom_info_action()
        self.statusBar().showMessage(_tr("status.zoom_changed", default="UI zoom set to {percent}%", percent=self._zoom_percent))

    def _change_zoom(self, direction: int):
        self._set_zoom(self._zoom_percent + (direction * _ZOOM_STEP))

    def _reset_zoom(self):
        self._set_zoom(100)

    def _change_font_size(self, direction: int):
        self._set_font_size_offset(self._font_size_offset + direction)

    def _set_font_size_offset(self, offset: int):
        clamped = max(-6, min(12, offset))
        if clamped == self._font_size_offset:
            return
        self._font_size_offset = clamped
        _set_font_size_offset_config(clamped)
        self._apply_zoom()
        self._update_font_size_info_action()
        label = _font_size_offset_label(clamped)
        self.statusBar().showMessage(_tr("status.font_size_offset", default="Font size offset: {label}", label=label))

    def _update_font_size_info_action(self):
        if hasattr(self, "_font_size_info_action"):
            off = self._font_size_offset
            label = _font_size_offset_label(off)
            self._font_size_info_action.setText(_tr("menu.settings.font_size_info", label=label))

    def _apply_zoom(self):
        app = QApplication.instance()
        font = QFont(self._base_font)
        base_pt = self._base_font.pointSizeF()
        if base_pt > 0:
            zoomed_pt = base_pt * (self._zoom_percent / 100.0) + self._font_size_offset
            font.setPointSizeF(max(_ACCESSIBILITY_MIN_FONT_PT, zoomed_pt))
        elif self._base_font.pixelSize() > 0:
            font.setPixelSize(max(_ACCESSIBILITY_MIN_FONT_PX, self._scaled(self._base_font.pixelSize()) + self._font_size_offset))
        app.setFont(font)

        if hasattr(self, "_sidebar"):
            self._sidebar.setFixedWidth(self._scaled(self._base_sidebar_width))
        if hasattr(self, "_header"):
            self._header.setFixedHeight(self._scaled(self._base_header_height))
        if hasattr(self, "_search"):
            self._search.setFixedWidth(self._scaled(self._base_search_width))
        if hasattr(self, "_table"):
            for col, width in self._base_col_widths.items():
                self._table.setColumnWidth(col, self._scaled(width))
            # Row height depends on visual vs compact mode. Re-apply the
            # mode so widths/height stay correct after a zoom change.
            if getattr(self, "_source_model", None) is not None and self._source_model.visual_mode():
                self._apply_roster_visual_mode(True)
            else:
                self._table.verticalHeader().setDefaultSectionSize(self._scaled(24))

        # Scale all hardcoded stylesheet font-size values across the whole window.
        # 1pt ≈ 1.33px; round to nearest integer pixel.
        offset_px = round(self._font_size_offset * 1.333)
        _apply_font_offset_to_tree(self, offset_px)



def _ensure_gpak_path_interactive(parent: Optional[QWidget] = None):
    if _GPAK_PATH:
        return

    start_dir = _gpak_search_start_dir()
    chosen_dir = QFileDialog.getExistingDirectory(
        parent,
        "Select Mewgenics Install Folder",
        start_dir,
    )
    if not chosen_dir:
        return

    gpak_path = os.path.join(chosen_dir, "resources.gpak")
    if os.path.exists(gpak_path):
        _set_gpak_path(gpak_path)
        return

    QMessageBox.warning(
        parent,
        "resources.gpak not found",
        "The selected folder does not contain resources.gpak. "
        "Choose the Mewgenics install directory that contains that file.",
    )
