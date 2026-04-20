"""Breed Priority view — main BreedPriorityView widget.

Standalone module — no imports from mewgenics_manager to avoid circular deps.
Game-specific helpers (STAT_NAMES, ROOM_DISPLAY, mutation_display_name,
ability_tip) are injected via BreedPriorityView.__init__() arguments.
"""

import html as _html
import os
import json
import tempfile
from typing import Optional, Callable

from save_parser import risk_percent, can_breed

from .filters import FilterState, FilterDialog, cat_passes_filter
from .stat_text_formatter import StatTextFormatter
from .color_utils import ColorUtils
from .chip_colors import ChipColors
from .deck_pull_button import create_pull_deck_save_button

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QSplitter,
    QSizePolicy, QFrame, QScrollArea,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QListWidget, QListWidgetItem, QButtonGroup,
    QCheckBox, QComboBox, QLineEdit, QPushButton, QGridLayout, QTabWidget,
)
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QBrush

# ── Re-exports for external consumers ────────────────────────────────────────
from .styles import SPLITTER_V_STYLE, SPLITTER_H_STYLE  # noqa: F401
from .scoring import compute_breed_priority_score         # noqa: F401

# ── Internal imports ──────────────────────────────────────────────────────────
from .collapsible_splitter import LEFT_PANEL_W, CollapseSplitter
from .scoring import (
    BREED_PRIORITY_WEIGHTS, WEIGHT_UI_ROWS, SCORE_COLUMNS,
    SCORE_HEADER_7_COUNT,
    TRAIT_LOW_THRESHOLD, TRAIT_HIGH_THRESHOLD, GENETIC_SAFE_RISK_FLOOR,
    TRAIT_RATING_VALUES,
)
from .theme import (
    CLR_TOP_PRIORITY, CLR_DESIRABLE, CLR_NEUTRAL, CLR_UNDECIDED,
    CLR_UNDESIRABLE, CLR_HIGHLIGHT, RATING_ITEM_COLORS,
    CLR_LABEL_SUBDUED,
    CLR_GENDER_MALE, CLR_GENDER_FEMALE, CLR_GENDER_UNKNOWN,
    _CHIP_GENDER_MALE, _CHIP_GENDER_FEMALE, _CHIP_GENDER_UNKNOWN,
    CLR_INTERACTIVE, CLR_INTERACTIVE_BG, CLR_INTERACTIVE_BDR,
    CLR_VALUE_POS, CLR_VALUE_NEG, CLR_VALUE_NEUTRAL,
    _CLR_AGE_OLD, _SEX_EMOJI_GAY, _SEX_EMOJI_BI,
    _CHIP_TOP_PRIORITY, _CHIP_DESIRABLE, _CHIP_UNDESIRABLE,
    _CHIP_DIM, _CHIP_NEUTRAL_STABLE, _CHIP_NEUTRAL_FAINT,
    _CHIP_LOVE, _CHIP_HATE, _CHIP_LOVE_OUTLINE, _CHIP_HATE_OUTLINE, _CHIP_AGE_WARN,
    CLR_TEXT_PRIMARY, CLR_TEXT_SECONDARY, CLR_TEXT_UI_LABEL,
    CLR_TEXT_GROUP, CLR_TEXT_SUBLABEL, CLR_TEXT_COUNT, CLR_TEXT_GRAYEDOUT,
    CLR_TEXT_MUTED,
    CLR_BG_MAIN, CLR_BG_ALT, CLR_BG_SCORE_AREA, CLR_BG_PANEL,
    CLR_BG_HEADER, CLR_BG_HEADER_BDR, CLR_BG_DEEP,
    CLR_SURFACE_SEPARATOR, _NEUTRAL_SURFACE,
)
from .styles import (
    SEGMENTED_CONTROL_BUTTON_STYLE, GROUP_LABEL_TEXT_STYLE,
    ACTION_BUTTON_PRIMARY_EMPHASIS_STYLE, ACTION_BUTTON_PRIMARY_STYLE,
    ACTION_BUTTON_SECONDARY_STYLE, ACTION_BUTTON_SECONDARY_LARGE_STYLE, TOGGLE_BUTTON_INACTIVE_STYLE,
    PRIORITY_TABLE_STYLE, PRIORITY_COMBO_STYLE,
    TRAIT_TAB_ABILITIES_STYLE, TRAIT_TAB_MUTATIONS_STYLE,
    checkbox_style,
)
from .columns import (
    COL_NAME, COL_LOC, COL_INJ, _STAT_COL_NAMES, _COL_STAT_START,
    _NUM_STAT_COLS, _SCORE_COLS, _COL_SCORE_START, COL_SCORE,
    COL_CW_SECTION_START,
    _ALL_HEADERS, _SEP_COLS, _SEP_WIDTH, _COL_MIN_WIDTH, _SEP_MIN_WIDTH,
    _CHIP_ROLE, _SCORE_SECONDARY_ROLE, _HEATMAP_ROLE,
    _ROOM_STYLE, INJURY_STAT_NAMES, _EMOJI_SCOPE, _EMOJI_ROOM,
    _SINGLE_VALUE_CENTER_SCORE_COLS, _MULTI_VALUE_LEFT_SCORE_COLS,
)
from .complex_weights import (
    ComplexWeight, compute_cw_matches, build_cat_trait_set, ComplexWeightsDialog,
)
from .scoring import (
    ScoreResult, ability_base, is_basic_trait,
)
from .tooltips import build_cat_tooltip, build_child_tooltip
from .column_values import raw_col_value
from .weight_popup import show_weights_popup
from .stats_overview import show_stats_overview, get_cat_stats
from .recompute_helpers import (
    build_relationship_maps, compute_seven_sets,
    compute_all_scores, compute_heatmap_norms,
)
from .profiles import (
    build_profile_bar as _build_profile_bar_impl,
    update_profile_bar as _update_profile_bar_impl,
    handle_profile_load as _handle_profile_load_impl,
    handle_profile_save as _handle_profile_save_impl,
    handle_profile_delete as _handle_profile_delete_impl,
)
from .delegates import (
    _BothModeDelegate, _CWDelegate,
    _FastTooltipFilter, _HateRowOverlay, _HeaderTooltipFilter,
    _IntParamSpin, _ListTooltipFilter, _NumericSortItem,
    _RatingCombo, _SeparatorDelegate,
    _SortHighlightHeader, _TraitChipDelegate, _TraitNameDelegate,
    _WeightSpin,
)

_NUM_PROFILES = 5


def _cat_injuries(cat, stat_names: list) -> list:
    """Return list of (injury_name, stat_key, delta) for stats with a negative total-vs-base delta."""
    injuries = []
    total = getattr(cat, 'total_stats', None)
    base  = getattr(cat, 'base_stats', None)
    if total is None or base is None:
        return injuries
    for sn in stat_names:
        b = base.get(sn, 0)
        t = total.get(sn, b)
        delta = t - b
        if delta < 0:
            name = INJURY_STAT_NAMES.get(sn, sn)
            injuries.append((name, sn, delta))
    return injuries


# ── Main view ─────────────────────────────────────────────────────────────────

class BreedPriorityView(QWidget):
    """Shows breed priority (keep vs cull) scores for all alive cats.

    Args:
        ratings_path: Path to the JSON file used to persist ratings/weights.
        stat_names: Ordered list of stat keys (e.g. ["STR","DEX",...]).
        room_display: Dict mapping room keys to display strings.
        mutation_display_name: Callable(str) -> str; converts trait IDs to labels.
        ability_tip: Callable(str) -> str; returns tooltip text for a trait.
    """

    def __init__(self, ratings_path: str, stat_names: list, room_display: dict,
                 mutation_display_name, ability_tip):
        super().__init__()
        self._cats: list = []
        self._ratings_path = ratings_path
        self._stat_names = stat_names
        self._room_display = room_display
        self._display_name = mutation_display_name
        self._ability_tip  = ability_tip
        self._ma_ratings: dict = {}
        self._room_checks: dict = {}
        self._saved_scope: dict = {}
        self._weights: dict = dict(BREED_PRIORITY_WEIGHTS)
        self._weight_spins: dict = {}
        self._populating = False
        self._all_abilities: list = []
        self._all_active_abilities: list = []
        self._all_passive_abilities: list = []
        self._all_disorders: list = []
        self._all_mutations: list = []
        self._all_good_mutations: list = []
        self._all_defects: list = []
        self._selected_cat = None
        self._hated_by_map: dict[int, list] = {}
        self._loved_by_map: dict[int, list] = {}
        self._scope_pair_risks: dict[tuple[int, int], float] = {}
        self._hide_kittens = False
        self._hide_out_of_scope = False
        self._use_current_stats = False
        self._add_mutation_stats = False
        self._filters_enabled = True
        self._display_mode = "score"   # "score" | "values" | "both"
        self._heatmap_on = False       # separate toggle for heatmap overlay
        self._heat_algo = "column"     # "column" | "row"
        self._show_stats = False
        self._sort_col: int = COL_SCORE
        self._sort_order = Qt.DescendingOrder
        self._filters = FilterState()
        self._col_widths: dict[str, dict[int, int]] = {}  # {mode_name: {col_idx: width}}
        self._bottom_pane_sizes: list[int] = []          # ABILITIES|MUTATIONS|CHILDREN|RISKS widths
        self._trait_col_widths: dict[int, int] = {}      # {col_idx: width} shared by both trait tables
        self._active_profile: int = 1   # currently selected profile slot
        self._loaded_profile: int = 1   # which profile's data is in memory
        self._profiles: dict = {}       # {int: dict} explicitly saved profile blobs
        self._profile_snapshot: dict = {} # serialized state when last profile was loaded
        self._profile_name_text: str = ""  # display name for the currently-loaded profile
        self._profile_traits_only: bool = False  # only save/load trait desirability ratings
        self._deck_save_puller = None
        self._complex_weights: list = []   # List[ComplexWeight]
        self._cw_dialog: ComplexWeightsDialog | None = None
        self._load_ratings()
        self._build_ui()
        self.setStyleSheet(
            f"QToolTip {{ background:{CLR_BG_PANEL}; color:{CLR_TEXT_SECONDARY};"
            f" border:1px solid {CLR_BG_HEADER_BDR}; }}"
        )
        self._col_save_timer = QTimer(self)
        self._col_save_timer.setSingleShot(True)
        self._col_save_timer.setInterval(600)
        self._col_save_timer.timeout.connect(self._save_ratings)

    # ── Persistence ───────────────────────────────────────────────────────────

    def _load_ratings(self):
        if not os.path.exists(self._ratings_path):
            return
        try:
            with open(self._ratings_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return

        # ── Profiles: load first so a failure below can never wipe them ──
        try:
            self._active_profile = int(data.get("active_profile", 1))
            self._loaded_profile = int(data.get("loaded_profile", 1))
            self._profiles = {
                int(k): v for k, v in data.get("profiles", {}).items()
            }
            self._profile_snapshot = self._profiles.get(self._loaded_profile, {})
            self._profile_name_text = self._profile_snapshot.get("name", "")
            self._profile_traits_only = bool(data.get("profile_traits_only", False))
        except Exception:
            pass

        # ── Trait ratings ──
        try:
            for section in ("abilities", "mutations"):
                for trait, val in data.get(section, {}).items():
                    if val in (-1, 0, 1, 2):
                        self._ma_ratings[trait] = val
        except Exception:
            pass

        # ── Complex Weights — migrate global → per-profile, then load from profile ──
        try:
            _global_cws = data.get("complex_weights", [])
            if _global_cws and isinstance(_global_cws, list):
                # One-time migration: seed any profile slot that has no CW key yet.
                for _slot_data in self._profiles.values():
                    if "complex_weights" not in _slot_data:
                        _slot_data["complex_weights"] = list(_global_cws)
            _profile_cws = self._profile_snapshot.get("complex_weights", [])
            if isinstance(_profile_cws, list):
                self._complex_weights = [ComplexWeight.from_dict(d) for d in _profile_cws]
        except Exception:
            pass

        # ── Scope, weights, display settings ──
        try:
            self._saved_scope = data.get("scope", {})
            for key in BREED_PRIORITY_WEIGHTS:
                if key in data.get("weights", {}):
                    self._weights[key] = float(data["weights"][key])
            _old_trait_w = data.get("weights", {}).get("unique_ma_max")
            if _old_trait_w is not None and not any(
                k in data.get("weights", {})
                for k in ("trait_top_priority", "trait_desirable", "trait_undesirable")
            ):
                _old_trait_w = float(_old_trait_w)
                self._weights["trait_top_priority"] = _old_trait_w
                self._weights["trait_desirable"] = _old_trait_w
                self._weights["trait_undesirable"] = -_old_trait_w
            self._hide_kittens = bool(data.get("hide_kittens", False))
            self._hide_out_of_scope = bool(data.get("hide_out_of_scope", False))
            self._use_current_stats = bool(data.get("use_current_stats", False))
            self._add_mutation_stats = bool(data.get("add_mutation_stats", False))
            _sv = data.get("display_mode", "values" if data.get("show_values", False) else "score")
            # Migrate old "heatmap" display mode → toggle
            if _sv == "heatmap":
                self._display_mode = "score"
                self._heatmap_on = True
            else:
                self._display_mode = _sv if _sv in ("score", "values", "both") else "score"
                self._heatmap_on = bool(data.get("heatmap_on", False))
            self._heat_algo = data.get("heat_algo", "column")
            if self._heat_algo not in ("column", "row"):
                self._heat_algo = "column"
            self._show_stats = bool(data.get("show_stats", False))
            _saved_sort = int(data.get("sort_col", COL_SCORE))
            # Allow sort on CW columns; reset to Score if separator, layout changed, or out of range.
            _col_layout_changed = data.get("col_count", 0) != len(_ALL_HEADERS)
            _enabled_cw_count = sum(1 for cw in self._complex_weights if cw.enabled)
            _max_valid_col = COL_CW_SECTION_START + _enabled_cw_count - 1
            if _saved_sort in _SEP_COLS or _saved_sort > _max_valid_col or _col_layout_changed:
                _saved_sort = COL_SCORE
            self._sort_col = _saved_sort
            self._sort_order = (
                Qt.DescendingOrder if data.get("sort_desc", True)
                else Qt.AscendingOrder
            )
        except Exception:
            pass

        # ── Filters ──
        try:
            if "filters" in data:
                self._filters = FilterState.from_dict(data["filters"])
            self._filters_enabled = data.get("filters_enabled", True)
        except Exception:
            pass

        # ── Column widths ──
        try:
            _raw_cw = data.get("col_widths", {})
            _saved_col_count = data.get("col_count", 0)
            _cur_col_count = len(_ALL_HEADERS)
            if _saved_col_count != _cur_col_count:
                # Column layout changed — discard stale saved widths
                _raw_cw = {}
            if _raw_cw and all(isinstance(v, dict) for v in _raw_cw.values()):
                # New per-mode format: {"score": {"0": 120, ...}, ...}
                self._col_widths = {
                    mode: {int(k): int(v) for k, v in widths.items()}
                    for mode, widths in _raw_cw.items()
                    if mode in ("score", "values", "both")
                }
            elif _raw_cw:
                # Old flat format: {"0": 120, ...} → copy to all modes
                _flat = {int(k): int(v) for k, v in _raw_cw.items()}
                self._col_widths = {m: dict(_flat) for m in ("score", "values", "both")}
        except Exception:
            pass

        # ── Bottom pane sizes ──
        try:
            _bps = data.get("bottom_pane_sizes", [])
            if isinstance(_bps, list) and len(_bps) == 4 and all(isinstance(x, int) for x in _bps):
                self._bottom_pane_sizes = _bps
        except Exception:
            pass

        # ── Trait table column widths ──
        try:
            _tcw = data.get("trait_col_widths", {})
            if isinstance(_tcw, dict):
                self._trait_col_widths = {int(k): int(v) for k, v in _tcw.items()}
        except Exception:
            pass

    def _profiles_safe(self) -> dict:
        """Return self._profiles, but if it's empty and the on-disk file already
        has profiles, return those instead.

        The delete-profile handler prevents deleting the last profile, so
        self._profiles should never legitimately be empty once profiles have
        been saved.  An empty dict here indicates a bug (e.g. an early save
        call before _load_ratings ran properly).  Reading back from disk is a
        cheap safety net that prevents silently wiping saved profiles.
        """
        if self._profiles:
            return self._profiles
        try:
            if os.path.exists(self._ratings_path):
                with open(self._ratings_path, "r", encoding="utf-8") as _f:
                    _on_disk = json.load(_f).get("profiles", {})
                    if _on_disk:
                        return {int(k): v for k, v in _on_disk.items()}
        except Exception:
            pass
        return {}

    def _save_ratings(self):
        ability_set = {
            ability_base(a)
            for c in self._cats
            for a in list(c.abilities) + list(c.passive_abilities) + list(getattr(c, 'disorders', []))
            if not is_basic_trait(a)
        }
        mutation_set = {m for c in self._cats for m in list(c.mutations) + list(getattr(c, 'defects', []))}
        data = {
            "abilities": {k: v for k, v in self._ma_ratings.items() if k in ability_set},
            "mutations": {k: v for k, v in self._ma_ratings.items() if k in mutation_set},
            "scope": self._saved_scope,
            "weights": self._weights,
            "hide_kittens": self._hide_kittens,
            "hide_out_of_scope": self._hide_out_of_scope,
            "use_current_stats": self._use_current_stats,
            "add_mutation_stats": self._add_mutation_stats,
            "display_mode": self._display_mode,
            "heatmap_on": self._heatmap_on,
            "heat_algo": self._heat_algo,
            "show_stats": self._show_stats,
            "sort_col": self._sort_col,
            "sort_desc": self._sort_order == Qt.DescendingOrder,
            "filters": self._filters.to_dict(),
            "filters_enabled": self._filters_enabled,
            "col_widths": {
                mode: {str(k): v for k, v in widths.items()}
                for mode, widths in self._col_widths.items()
            },
            "col_count": len(_ALL_HEADERS),
            "bottom_pane_sizes": (
                list(self._bottom_hs.sizes()) if hasattr(self, "_bottom_hs") else []
            ),
            "trait_col_widths": {str(k): v for k, v in self._trait_col_widths.items()},
            # Profile slots (separate from working state)
            "active_profile": self._active_profile,
            "loaded_profile": self._loaded_profile,
            "profiles": {str(k): v for k, v in self._profiles_safe().items()},
            "profile_traits_only": self._profile_traits_only,
        }
        try:
            _dir = os.path.dirname(self._ratings_path) or "."
            os.makedirs(_dir, exist_ok=True)
            _fd, _tmp = tempfile.mkstemp(dir=_dir, suffix=".tmp")
            try:
                with os.fdopen(_fd, "w", encoding="utf-8") as _f:
                    json.dump(data, _f, indent=2)
                os.replace(_tmp, self._ratings_path)
            except Exception:
                try:
                    os.unlink(_tmp)
                except Exception:
                    pass
        except Exception:
            pass
        self._update_profile_bar()

    def save_session_state(self) -> None:
        """Flush any pending state to disk immediately (called on app close)."""
        if hasattr(self, "_col_save_timer"):
            self._col_save_timer.stop()
        self._save_ratings()

    # ── Profile management ─────────────────────────────────────────────────────

    def _serialize_current(self) -> dict:
        """Snapshot of all current settings in profile-blob format."""
        return {
            "name": self._profile_name_text,
            "ma_ratings": dict(self._ma_ratings),
            "scope": self._saved_scope,
            "weights": dict(self._weights),
            "hide_kittens": self._hide_kittens,
            "hide_out_of_scope": self._hide_out_of_scope,
            "use_current_stats": self._use_current_stats,
            "add_mutation_stats": self._add_mutation_stats,
            "display_mode": self._display_mode,
            "heatmap_on": self._heatmap_on,
            "heat_algo": self._heat_algo,
            "show_stats": self._show_stats,
            "sort_col": self._sort_col,
            "sort_desc": self._sort_order == Qt.DescendingOrder,
            "filters": self._filters.to_dict(),
            "filters_enabled": self._filters_enabled,
            "complex_weights": [cw.to_dict() for cw in self._complex_weights],
        }

    def _is_dirty(self) -> bool:
        """True if current settings differ from the last-loaded profile snapshot."""
        if not self._profile_snapshot:
            return False
        if self._profile_traits_only:
            return self._ma_ratings != self._profile_snapshot.get("ma_ratings", {})
        return self._serialize_current() != self._profile_snapshot

    def _apply_profile_data(self, data: dict):
        """Apply a profile blob to all instance vars and refresh every UI widget."""
        # Weights
        new_w = data.get("weights", {})
        _old_trait_w = new_w.get("unique_ma_max")
        if _old_trait_w is not None and not any(
            k in new_w for k in ("trait_top_priority", "trait_desirable", "trait_undesirable")
        ):
            _old_trait_w = float(_old_trait_w)
            new_w = dict(new_w)
            new_w["trait_top_priority"] = _old_trait_w
            new_w["trait_desirable"] = _old_trait_w
            new_w["trait_undesirable"] = -_old_trait_w
        for key in BREED_PRIORITY_WEIGHTS:
            self._weights[key] = float(new_w.get(key, BREED_PRIORITY_WEIGHTS[key]))
        if self._weight_spins:
            self._populating = True
            for key, spin in self._weight_spins.items():
                spin.blockSignals(True)
                spin.setValue(self._weights.get(key, BREED_PRIORITY_WEIGHTS[key]))
                spin.blockSignals(False)
            self._populating = False

        # Trait ratings
        self._ma_ratings = {k: v for k, v in data.get("ma_ratings", {}).items()
                            if v in (-1, 0, 1, 2)}

        # Scope
        self._saved_scope = data.get("scope", {})

        # Options
        self._hide_kittens        = bool(data.get("hide_kittens", False))
        self._hide_out_of_scope   = bool(data.get("hide_out_of_scope", False))
        self._use_current_stats   = bool(data.get("use_current_stats", False))
        self._add_mutation_stats  = bool(data.get("add_mutation_stats", False))
        _sv = data.get("display_mode", "values" if data.get("show_values", False) else "score")
        if _sv == "heatmap":
            self._display_mode = "score"
            self._heatmap_on = True
        else:
            self._display_mode = _sv if _sv in ("score", "values", "both") else "score"
            self._heatmap_on = bool(data.get("heatmap_on", False))
        _ha = data.get("heat_algo", "column")
        self._heat_algo         = _ha if _ha in ("column", "row") else "column"
        self._show_stats        = bool(data.get("show_stats", False))
        # Complex Weights
        _raw_cws = data.get("complex_weights", [])
        self._complex_weights = []
        if isinstance(_raw_cws, list):
            for _cw_d in _raw_cws:
                try:
                    self._complex_weights.append(ComplexWeight.from_dict(_cw_d))
                except Exception:
                    pass
        if self._cw_dialog is not None:
            self._cw_dialog._cws = self._complex_weights
            self._cw_dialog._rebuild()

        _saved_sort = int(data.get("sort_col", COL_SCORE))
        _cw_enabled_count = sum(1 for cw in self._complex_weights if cw.enabled)
        _max_valid_cw_col = (COL_CW_SECTION_START + _cw_enabled_count - 1
                             if _cw_enabled_count else COL_SCORE)
        if _saved_sort in _SEP_COLS or _saved_sort > _max_valid_cw_col:
            _saved_sort = COL_SCORE
        self._sort_col          = _saved_sort
        self._sort_order        = (Qt.DescendingOrder if data.get("sort_desc", True)
                                   else Qt.AscendingOrder)
        if "filters" in data:
            self._filters = FilterState.from_dict(data["filters"])
        self._filters_enabled = data.get("filters_enabled", True)

        # Profile name
        self._profile_name_text = data.get("name", "")
        if hasattr(self, "_profile_name_edit"):
            self._profile_name_edit.blockSignals(True)
            self._profile_name_edit.setText(self._profile_name_text)
            self._profile_name_edit.blockSignals(False)

        # Stop here if UI hasn't been built yet
        if not hasattr(self, "_chk_hide_kittens"):
            return

        # Option checkboxes
        for chk, val in [
            (self._chk_hide_kittens,        self._hide_kittens),
            (self._chk_hide_out_of_scope,   self._hide_out_of_scope),
            (self._chk_use_current_stats,   self._use_current_stats),
            (self._chk_add_mutation_stats,  self._add_mutation_stats),
            (self._chk_show_stats,          self._show_stats),
        ]:
            chk.blockSignals(True)
            chk.setChecked(val)
            chk.blockSignals(False)

        # Segmented display-mode control
        if hasattr(self, "_btn_mode_score"):
            _mode_map = {"score": self._btn_mode_score,
                         "values": self._btn_mode_values,
                         "both": self._btn_mode_both}
            for _b in _mode_map.values():
                _b.blockSignals(True)
            _mode_map.get(self._display_mode, self._btn_mode_score).setChecked(True)
            for _b in _mode_map.values():
                _b.blockSignals(False)
            # Heatmap toggle
            self._btn_heatmap_toggle.blockSignals(True)
            self._btn_heatmap_toggle.setChecked(self._heatmap_on)
            self._btn_heatmap_toggle.blockSignals(False)
            self._update_heat_options_enabled()
            # Sync heat algo buttons
            _ha_map = {"column": self._btn_heat_col, "row": self._btn_heat_row}
            for _hb in _ha_map.values():
                _hb.blockSignals(True)
            _ha_map.get(self._heat_algo, self._btn_heat_col).setChecked(True)
            for _hb in _ha_map.values():
                _hb.blockSignals(False)
            self._apply_mode_col_widths()
        self._apply_stat_column_visibility()

        # Scope UI
        all_cats_on  = self._saved_scope.get("all_cats", True)
        saved_rooms  = self._saved_scope.get("rooms", {})
        self._chk_all_cats.blockSignals(True)
        self._chk_all_cats.setChecked(all_cats_on)
        self._chk_all_cats.blockSignals(False)
        for room, chk in self._room_checks.items():
            chk.blockSignals(True)
            chk.setChecked(all_cats_on or saved_rooms.get(room, False))
            chk.blockSignals(False)

        # Sort indicator
        _hdr = self._score_table.horizontalHeader()
        _hdr.blockSignals(True)
        _hdr.setSortIndicator(self._sort_col, self._sort_order)
        _hdr._sort_col = self._sort_col
        _hdr.blockSignals(False)

        # Trait tables + recompute
        if self._cats:
            for defect in getattr(self, "_defect_names", set()):
                if defect not in self._ma_ratings:
                    self._ma_ratings[defect] = -1
            self._selected_cat = None
            self._populate_trait_table(self._active_abilities_table, self._all_active_abilities)
            self._populate_trait_table(self._passive_abilities_table, self._all_passive_abilities)
            self._populate_trait_table(self._disorders_table, self._all_disorders)
            self._populate_trait_table(self._good_mutations_table, self._all_good_mutations)
            self._populate_trait_table(self._defects_table, self._all_defects)
        self.recompute()
        self._update_filter_btn()

    def _update_profile_bar(self):
        """Refresh profile button styles, name widgets, and status indicators."""
        if not hasattr(self, "_profile_widget_refs"):
            return
        _update_profile_bar_impl(
            self._profile_widget_refs,
            self._active_profile,
            self._loaded_profile,
            self._profiles,
            self._is_dirty(),
        )

    def _build_profile_bar(self) -> QWidget:
        """Build the centered profile selector bar above the score table."""
        bar, refs = _build_profile_bar_impl(
            parent=self,
            profile_name_text=self._profile_name_text,
            profile_traits_only=self._profile_traits_only,
            on_name_changed=self._on_profile_name_changed,
            on_traits_only_changed=self._on_traits_only_changed,
            on_btn_clicked=self._on_profile_btn_clicked,
            on_load=self._on_profile_load,
            on_save=self._on_profile_save,
            on_delete=self._on_profile_delete,
        )
        self._profile_widget_refs = refs
        self._profile_name_edit = refs["name_edit"]
        self._profile_sel_arrow_lbl = refs["sel_arrow_lbl"]
        self._profile_sel_name_lbl = refs["sel_name_lbl"]
        self._profile_btns = refs["profile_btns"]
        self._profile_load_btn = refs["load_btn"]
        self._profile_save_btn = refs["save_btn"]
        self._profile_delete_btn = refs["delete_btn"]
        self._profile_loaded_lbl = refs["loaded_lbl"]
        self._profile_dirty_lbl = refs["dirty_lbl"]
        self._chk_traits_only = refs["chk_traits_only"]
        self._update_profile_bar()
        return bar

    def _on_profile_name_changed(self, text: str):
        """User edited the profile name — update state and mark dirty."""
        self._profile_name_text = text
        self._save_ratings()

    def _on_traits_only_changed(self, state: int):
        self._profile_traits_only = bool(state)
        self._save_ratings()

    def _on_profile_btn_clicked(self, n: int):
        self._active_profile = n
        self._update_profile_bar()

    def _on_profile_load(self):
        profile_data = _handle_profile_load_impl(
            self, self._active_profile, self._profiles,
            self._profile_traits_only, self._is_dirty(),
        )
        if profile_data is None:
            return
        n = self._active_profile
        if self._profile_traits_only:
            self._ma_ratings = {k: v for k, v in profile_data.get("ma_ratings", {}).items()
                                 if v in (-1, 0, 1, 2)}
            self._profile_name_text = profile_data.get("name", "")
            if hasattr(self, "_profile_name_edit"):
                self._profile_name_edit.blockSignals(True)
                self._profile_name_edit.setText(self._profile_name_text)
                self._profile_name_edit.blockSignals(False)
            if self._cats:
                for defect in getattr(self, "_defect_names", set()):
                    if defect not in self._ma_ratings:
                        self._ma_ratings[defect] = -1
                self._selected_cat = None
                self._populate_trait_table(self._active_abilities_table, self._all_active_abilities)
                self._populate_trait_table(self._passive_abilities_table, self._all_passive_abilities)
                self._populate_trait_table(self._disorders_table, self._all_disorders)
                self._populate_trait_table(self._good_mutations_table, self._all_good_mutations)
                self._populate_trait_table(self._defects_table, self._all_defects)
            self.recompute()
        else:
            self._apply_profile_data(profile_data)
        self._loaded_profile = n
        self._active_profile = n
        self._profile_snapshot = dict(profile_data)
        self._save_ratings()

    def _on_profile_save(self):
        snapshot = _handle_profile_save_impl(
            self, self._active_profile, self._profiles,
            self._profile_traits_only, self._ma_ratings,
            self._serialize_current,
        )
        if snapshot is None:
            return
        n = self._active_profile
        self._profiles[n] = snapshot
        self._loaded_profile = n
        self._active_profile = n
        self._profile_snapshot = snapshot
        self._save_ratings()

    def _on_profile_delete(self):
        slot = _handle_profile_delete_impl(self, self._active_profile, self._profiles)
        if slot is None:
            return
        del self._profiles[slot]
        next_n = min(self._profiles.keys())
        self._apply_profile_data(self._profiles[next_n])
        self._loaded_profile = next_n
        self._active_profile = next_n
        self._profile_snapshot = dict(self._profiles[next_n])
        self._save_ratings()

    # ── UI build ──────────────────────────────────────────────────────────────

    def _make_trait_table(self) -> QTableWidget:
        t = QTableWidget()
        t.setColumnCount(2)
        t.setHorizontalHeaderLabels(["Trait", "Rating"])
        t.setEditTriggers(QAbstractItemView.NoEditTriggers)
        t.setSelectionMode(QAbstractItemView.NoSelection)
        t.verticalHeader().setVisible(False)
        t.setShowGrid(False)
        t.setAlternatingRowColors(True)
        t.setStyleSheet(PRIORITY_TABLE_STYLE)
        hh = t.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Interactive)
        hh.setSectionResizeMode(1, QHeaderView.Fixed)
        t.setColumnWidth(0, 180)
        t.setColumnWidth(1, 115)
        _FastTooltipFilter(t)   # fast tooltip on the trait name column
        t.setItemDelegateForColumn(0, _TraitNameDelegate(t))
        return t

    @staticmethod
    def _make_banner(icon: str, text: str, color: str, bg: str, border: str) -> QWidget:
        """Two-column banner: fixed-width centered icon label + text label.

        Using a real layout instead of padded spaces ensures the icon and text
        are independently aligned regardless of glyph width differences.
        """
        w = QWidget()
        w.setStyleSheet(
            f"QWidget {{ background:{bg}; border-bottom:1px solid {border}; }}"
            f"QLabel  {{ background:transparent; border:none; }}"
        )
        hb = QHBoxLayout(w)
        hb.setContentsMargins(14, 0, 14, 0)
        hb.setSpacing(10)

        icon_lbl = QLabel(icon)
        icon_lbl.setFixedWidth(20)
        icon_lbl.setAlignment(Qt.AlignCenter)
        icon_lbl.setStyleSheet(f"color:{color}; font-size:14px;")

        text_lbl = QLabel(text)
        text_lbl.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)
        text_lbl.setStyleSheet(f"color:{color}; font-size:12px; font-weight:bold;")

        hb.addWidget(icon_lbl)
        hb.addWidget(text_lbl)
        hb.addStretch()
        w.setFixedHeight(38)
        return w

    # ── UI builder helpers ──────────────────────────────────────────────────

    def _build_top_bar(self) -> QWidget:
        """Build the header bar with display mode, heatmap, and show-stats controls."""
        top_bar = QWidget()
        top_bar.setStyleSheet(f"background:{CLR_BG_HEADER}; border-bottom:1px solid {CLR_BG_HEADER_BDR};")
        top_bar.setFixedHeight(46)
        hb = QHBoxLayout(top_bar)
        hb.setContentsMargins(14, 0, 14, 0)
        hb.setSpacing(12)
        title_lbl = QLabel("Breed Priority")
        title_lbl.setStyleSheet(f"color:{CLR_TEXT_PRIMARY}; font-size:16px; font-weight:bold;")
        hb.addWidget(title_lbl)

        self._btn_stats_overview = QPushButton("Current Stats…")
        self._btn_stats_overview.setStyleSheet(ACTION_BUTTON_SECONDARY_STYLE)
        self._btn_stats_overview.setFixedHeight(22)
        self._btn_stats_overview.setToolTip(
            "Open a window showing all cats' current stats (base + injuries),\n"
            "with a toggle to include or exclude injury modifiers."
        )
        self._btn_stats_overview.clicked.connect(self._open_stats_overview)
        hb.addWidget(self._btn_stats_overview)

        self._btn_pull_deck_save = create_pull_deck_save_button(
            style=ACTION_BUTTON_SECONDARY_STYLE,
            parent=self,
        )
        hb.addWidget(self._btn_pull_deck_save)

        self._btn_complex_weights = QPushButton("Complex Weights…")
        self._btn_complex_weights.setStyleSheet(ACTION_BUTTON_SECONDARY_STYLE)
        self._btn_complex_weights.setFixedHeight(22)
        self._btn_complex_weights.setToolTip(
            "Define custom scoring rules that add or subtract points\n"
            "when a cat meets a set of conditions. Each enabled rule\n"
            "creates its own column in the scoring table."
        )
        self._btn_complex_weights.clicked.connect(self._open_complex_weights)
        hb.addWidget(self._btn_complex_weights)
        hb.addStretch()

        _chk_style = checkbox_style(
            font_size=11,
            emphasize_checked=True,
            text_color=CLR_TEXT_UI_LABEL,
        )
        # Segmented Score / Values / Both control
        self._btn_mode_score   = QPushButton("Score")
        self._btn_mode_values  = QPushButton("Values")
        self._btn_mode_both    = QPushButton("Both")
        for _b in (self._btn_mode_score, self._btn_mode_values, self._btn_mode_both):
            _b.setCheckable(True)
            _b.setStyleSheet(SEGMENTED_CONTROL_BUTTON_STYLE)
            _b.setFixedHeight(20)
        _mode_init = {"score": self._btn_mode_score, "values": self._btn_mode_values,
                      "both": self._btn_mode_both}
        _mode_init.get(self._display_mode, self._btn_mode_score).setChecked(True)
        self._display_mode_group = QButtonGroup(self)
        self._display_mode_group.setExclusive(True)
        self._display_mode_group.addButton(self._btn_mode_score,   0)
        self._display_mode_group.addButton(self._btn_mode_values,  1)
        self._display_mode_group.addButton(self._btn_mode_both,    2)
        self._display_mode_group.idToggled.connect(self._on_display_mode_changed)
        _seg_w = QWidget()
        _seg_l = QHBoxLayout(_seg_w)
        _seg_l.setSpacing(0)
        _seg_l.setContentsMargins(0, 0, 0, 0)
        for _b in (self._btn_mode_score, self._btn_mode_values, self._btn_mode_both):
            _seg_l.addWidget(_b)
        hb.addWidget(_seg_w)

        # Separate heatmap toggle button
        self._btn_heatmap_toggle = QPushButton("Heatmap")
        self._btn_heatmap_toggle.setCheckable(True)
        self._btn_heatmap_toggle.setChecked(self._heatmap_on)
        self._btn_heatmap_toggle.setStyleSheet(SEGMENTED_CONTROL_BUTTON_STYLE)
        self._btn_heatmap_toggle.setFixedHeight(20)
        self._btn_heatmap_toggle.toggled.connect(self._on_heatmap_toggled)
        hb.addWidget(self._btn_heatmap_toggle)

        # Heatmap algorithm selector (Column vs Row normalisation)
        self._btn_heat_col = QPushButton("Column")
        self._btn_heat_col.setToolTip(
            "Compare cats against each other within each column.\n"
            "The cat with the best score in a column gets the brightest bar.\n"
            "Good for finding which cats stand out in each category."
        )
        self._btn_heat_row = QPushButton("Row")
        self._btn_heat_row.setToolTip(
            "Compare columns against each other for each cat.\n"
            "The column with the highest score for a cat gets the brightest bar.\n"
            "Good for seeing each cat's strongest and weakest traits at a glance."
        )
        for _hb in (self._btn_heat_col, self._btn_heat_row):
            _hb.setCheckable(True)
            _hb.setStyleSheet(SEGMENTED_CONTROL_BUTTON_STYLE)
            _hb.setFixedHeight(20)
        (self._btn_heat_col if self._heat_algo == "column" else self._btn_heat_row).setChecked(True)
        self._heat_algo_group = QButtonGroup(self)
        self._heat_algo_group.setExclusive(True)
        self._heat_algo_group.addButton(self._btn_heat_col, 0)
        self._heat_algo_group.addButton(self._btn_heat_row, 1)
        self._heat_algo_group.idToggled.connect(self._on_heat_algo_changed)
        self._heat_algo_w = QWidget()
        _hal = QHBoxLayout(self._heat_algo_w)
        _hal.setSpacing(0)
        _hal.setContentsMargins(0, 0, 0, 0)
        self._ha_lbl = QLabel("Heat:")
        self._ha_lbl.setStyleSheet(f"color:{CLR_LABEL_SUBDUED}; font-size:10px;")
        _hal.addWidget(self._ha_lbl)
        _hal.addWidget(self._btn_heat_col)
        _hal.addWidget(self._btn_heat_row)
        self._update_heat_options_enabled()
        hb.addWidget(self._heat_algo_w)

        self._chk_show_stats = QCheckBox("Show Stats")
        self._chk_show_stats.setStyleSheet(_chk_style)
        self._chk_show_stats.setToolTip(
            "Show individual STR/DEX/CON/INT/SPD/CHA/LCK stat columns."
        )
        self._chk_show_stats.setChecked(self._show_stats)
        self._chk_show_stats.stateChanged.connect(self._on_show_stats_changed)
        hb.addWidget(self._chk_show_stats)
        return top_bar

    def _build_scope_panel(self, layout):
        """Build the scope + options + filters section into the given layout."""
        scope_lbl = QLabel("COMPARISON SCOPE")
        scope_lbl.setStyleSheet(GROUP_LABEL_TEXT_STYLE)
        layout.addWidget(scope_lbl)

        _ac_row = QWidget()
        _ac_row.setStyleSheet("background:transparent;")
        _ac_h = QHBoxLayout(_ac_row)
        _ac_h.setContentsMargins(0, 0, 0, 0)
        _ac_h.setSpacing(3)
        self._chk_all_cats = QCheckBox("All Cats")
        self._chk_all_cats.setStyleSheet(f"color:{CLR_TEXT_SECONDARY}; font-size:11px;")
        self._chk_all_cats.setChecked(True)
        self._chk_all_cats.stateChanged.connect(self._on_all_cats_changed)
        _ac_h.addWidget(self._chk_all_cats)
        _scope_vsep = QFrame()
        _scope_vsep.setFrameShape(QFrame.VLine)
        _scope_vsep.setStyleSheet(f"color:{CLR_SURFACE_SEPARATOR};")
        _scope_vsep.setFixedWidth(1)
        _ac_h.addWidget(_scope_vsep)
        for _leg_txt, _leg_clr in (("M", CLR_GENDER_MALE), ("F", CLR_GENDER_FEMALE), ("?", CLR_GENDER_UNKNOWN)):
            _leg = QLabel(_leg_txt)
            _leg.setFixedWidth(32)
            _leg.setStyleSheet(f"color:{_leg_clr}; font-size:12px; font-weight:bold;")
            _leg.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            _ac_h.addWidget(_leg)
        _gen_leg = QLabel("Risk")
        _gen_leg.setFixedWidth(44)
        _gen_leg.setStyleSheet(f"color:{CLR_TEXT_PRIMARY}; font-size:12px; font-weight:bold;")
        _gen_leg.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        _ac_h.addWidget(_gen_leg)
        layout.addWidget(_ac_row)

        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet(f"color:{CLR_SURFACE_SEPARATOR}; margin:2px 0;")
        layout.addWidget(sep)

        self._room_checks_widget = QWidget()
        self._room_checks_vb = QVBoxLayout(self._room_checks_widget)
        self._room_checks_vb.setContentsMargins(6, 0, 0, 0)
        self._room_checks_vb.setSpacing(2)
        layout.addWidget(self._room_checks_widget)

        _small_btn_style = ACTION_BUTTON_SECONDARY_STYLE

        sep2 = QFrame()
        sep2.setFrameShape(QFrame.HLine)
        sep2.setStyleSheet(f"color:{CLR_SURFACE_SEPARATOR}; margin:6px 0 2px 0;")
        layout.addWidget(sep2)

        opts_lbl = QLabel("OPTIONS")
        opts_lbl.setStyleSheet(GROUP_LABEL_TEXT_STYLE)
        layout.addWidget(opts_lbl)

        self._chk_hide_kittens = QCheckBox("Hide Kittens")
        self._chk_hide_kittens.setStyleSheet(checkbox_style(font_size=11, emphasize_checked=True))
        self._chk_hide_kittens.setToolTip(
            "Exclude kittens (age 1) from the list and from scoring comparisons."
        )
        self._chk_hide_kittens.setChecked(self._hide_kittens)
        self._chk_hide_kittens.stateChanged.connect(self._on_hide_kittens_changed)
        layout.addWidget(self._chk_hide_kittens)

        self._chk_hide_out_of_scope = QCheckBox("Hide Out-of-Scope")
        self._chk_hide_out_of_scope.setStyleSheet(checkbox_style(font_size=11, emphasize_checked=True))
        self._chk_hide_out_of_scope.setToolTip(
            "Only show cats that are within the current comparison scope."
        )
        self._chk_hide_out_of_scope.setChecked(self._hide_out_of_scope)
        self._chk_hide_out_of_scope.stateChanged.connect(self._on_hide_out_of_scope_changed)
        layout.addWidget(self._chk_hide_out_of_scope)

        self._chk_use_current_stats = QCheckBox("Use Current Stats")
        self._chk_use_current_stats.setStyleSheet(checkbox_style(font_size=11, emphasize_checked=True))
        self._chk_use_current_stats.setToolTip(
            "Score and display using current stats (base + modifiers/injuries)\n"
            "instead of base genetic stats only."
        )
        self._chk_use_current_stats.setChecked(self._use_current_stats)
        self._chk_use_current_stats.stateChanged.connect(self._on_use_current_stats_changed)
        layout.addWidget(self._chk_use_current_stats)

        self._chk_add_mutation_stats = QCheckBox("Add Mutation Stats")
        self._chk_add_mutation_stats.setStyleSheet(checkbox_style(font_size=11, emphasize_checked=True))
        self._chk_add_mutation_stats.setToolTip(
            "Add each mutation's stat bonuses (e.g. STR+2, DEX-1) on top of\n"
            "the selected stat source when scoring and displaying."
        )
        self._chk_add_mutation_stats.setChecked(self._add_mutation_stats)
        self._chk_add_mutation_stats.stateChanged.connect(self._on_add_mutation_stats_changed)
        layout.addWidget(self._chk_add_mutation_stats)

        sep_f = QFrame()
        sep_f.setFrameShape(QFrame.HLine)
        sep_f.setStyleSheet(f"color:{CLR_SURFACE_SEPARATOR}; margin:4px 0 2px 0;")
        layout.addWidget(sep_f)

        _filter_row = QHBoxLayout()
        _filter_row.setContentsMargins(0, 0, 0, 0)
        _filter_row.setSpacing(4)
        self._filter_btn = QPushButton("Filters…")
        self._filter_btn.setStyleSheet(_small_btn_style)
        self._filter_btn.setToolTip("Open filter settings to hide cats that don't match criteria.")
        self._filter_btn.clicked.connect(self._open_filters)
        _filter_row.addWidget(self._filter_btn)

        self._filter_toggle = QPushButton("On")
        self._filter_toggle.setCheckable(True)
        self._filter_toggle.setChecked(bool(self._filters_enabled))
        self._filter_toggle.setFixedWidth(36)
        self._filter_toggle.setToolTip("Toggle filters on/off without clearing them.")
        self._filter_toggle.clicked.connect(self._on_filter_toggle)
        _filter_row.addWidget(self._filter_toggle)

        layout.addLayout(_filter_row)
        self._update_filter_btn()

    def _build_weights_panel(self, layout):
        """Build the weights grid + reset/info buttons into the given layout."""
        sep3 = QFrame()
        sep3.setFrameShape(QFrame.HLine)
        sep3.setStyleSheet(f"color:{CLR_SURFACE_SEPARATOR}; margin:6px 0 2px 0;")
        layout.addWidget(sep3)

        weights_lbl = QLabel("WEIGHTS")
        weights_lbl.setStyleSheet(GROUP_LABEL_TEXT_STYLE)
        layout.addWidget(weights_lbl)

        weights_widget = QWidget()
        weights_widget.setStyleSheet(f"background:{CLR_BG_PANEL};")
        wg = QGridLayout(weights_widget)
        wg.setContentsMargins(0, 0, 0, 0)
        wg.setHorizontalSpacing(4)
        wg.setVerticalSpacing(3)
        _step_hdr_w = QWidget()
        _step_hdr_w.setStyleSheet("background:transparent;")
        _step_hdr_v = QVBoxLayout(_step_hdr_w)
        _step_hdr_v.setContentsMargins(0, 0, 0, 0)
        _step_hdr_v.setSpacing(1)
        _step_hdr_top = QWidget()
        _step_hdr_top.setStyleSheet("background:transparent;")
        _step_hdr_h = QHBoxLayout(_step_hdr_top)
        _step_hdr_h.setContentsMargins(0, 0, 0, 0)
        _step_hdr_h.setSpacing(2)
        _h1 = QLabel("1")
        _h1.setFixedWidth(18)
        _h1.setAlignment(Qt.AlignCenter)
        _h1.setStyleSheet(f"color:{CLR_TEXT_PRIMARY}; font-size:10px; font-weight:bold;")
        _mid = QLabel("")
        _mid.setFixedWidth(40)
        _h5 = QLabel("5")
        _h5.setFixedWidth(18)
        _h5.setAlignment(Qt.AlignCenter)
        _h5.setStyleSheet(f"color:{CLR_TEXT_PRIMARY}; font-size:10px; font-weight:bold;")
        _step_hdr_h.addWidget(_h1)
        _step_hdr_h.addWidget(_mid)
        _step_hdr_h.addWidget(_h5)
        _step_line = QFrame()
        _step_line.setFrameShape(QFrame.HLine)
        _step_line.setStyleSheet(f"color:{CLR_SURFACE_SEPARATOR}; margin:0;")
        _step_hdr_v.addWidget(_step_hdr_top)
        _step_hdr_v.addWidget(_step_line)
        wg.addWidget(_step_hdr_w, 0, 1)
        r = 1   # grid row index (separators consume a row too)
        for key, label in WEIGHT_UI_ROWS:
            if key is None:
                # Thin separator line spanning both columns
                _sep = QFrame()
                _sep.setFrameShape(QFrame.HLine)
                _sep.setStyleSheet(f"color:{CLR_SURFACE_SEPARATOR}; margin:1px 0;")
                wg.addWidget(_sep, r, 0, 1, 2)
                r += 1
                continue
            if isinstance(label, tuple):
                # Paired option: group name left, sub-label right (e.g. "Aggro | High")
                group_text, sub_text = label
                lbl = QWidget()
                lbl.setStyleSheet("background:transparent;")
                _lh = QHBoxLayout(lbl)
                _lh.setContentsMargins(0, 0, 0, 0)
                _lh.setSpacing(2)
                _grp = QLabel(group_text)
                _grp.setStyleSheet(f"color:{CLR_TEXT_GROUP}; font-size:10px;")
                _lh.addWidget(_grp)
                _lh.addStretch()
                _sub = QLabel(sub_text)
                _sub.setStyleSheet(f"color:{CLR_TEXT_UI_LABEL}; font-size:10px;")
                _lh.addWidget(_sub)
            else:
                is_subitem = label.startswith("  └")
                lbl = QLabel(label)
                lbl.setStyleSheet(
                    f"color:{CLR_TEXT_SUBLABEL}; font-size:10px;" if is_subitem else f"color:{CLR_TEXT_UI_LABEL}; font-size:10px;"
                )
            _INT_PARAM_RANGES = {
                "stat_7_threshold":      (1, 20),
                "age_threshold":         (1, 30),
                "seven_sub_threshold":   (1, 20),
                "gene_risk_threshold":   (0, 50),
                "gene_risk_penalty_scale": (1, 100),
            }
            if key in _INT_PARAM_RANGES:
                _mn, _mx = _INT_PARAM_RANGES[key]
                spin = _IntParamSpin(int(round(self._weights[key])), min_val=_mn, max_val=_mx)
            else:
                spin = _WeightSpin(self._weights[key])
            spin.valueChanged.connect(lambda val, k=key: self._on_weight_changed(k, val))
            wg.addWidget(lbl,  r, 0)
            wg.addWidget(spin, r, 1)
            self._weight_spins[key] = spin
            r += 1

        _small_btn_style = ACTION_BUTTON_SECONDARY_STYLE
        reset_btn = QPushButton("Reset")
        reset_btn.setStyleSheet(_small_btn_style)
        reset_btn.setToolTip("Reset all weights to defaults")
        reset_btn.clicked.connect(self._reset_weights)

        info_btn = QPushButton("?")
        info_btn.setFixedWidth(22)
        info_btn.setStyleSheet(_small_btn_style)
        info_btn.setToolTip("Show scoring weights reference")
        info_btn.clicked.connect(self._show_weights_popup)

        btn_row = r
        wg.addWidget(reset_btn, btn_row, 0)
        wg.addWidget(info_btn,  btn_row, 1)
        layout.addWidget(weights_widget)

    def _build_score_table_section(self) -> QWidget:
        """Build the score table with profile bar, banners, and delegates."""
        self._score_table = QTableWidget()
        self._score_table.setColumnCount(len(_ALL_HEADERS))
        shh = _SortHighlightHeader(self._score_table)
        shh.setSectionsClickable(True)
        self._score_table.setHorizontalHeader(shh)
        self._score_table.setHorizontalHeaderLabels(_ALL_HEADERS)
        # Column header tooltips
        _HEADER_TIPS_TEXT = {
            "Name":    "Cat name",
            "Age":     "Age in days",
            "Loc":     "Current room",
            "Inj":     "Active injuries",
            "STR":     "Strength",
            "DEX":     "Dexterity",
            "CON":     "Constitution",
            "INT":     "Intelligence",
            "SPD":     "Speed",
            "CHA":     "Charisma",
            "LCK":     "Luck",
            "Sum":     "Stat sum score. Percentile vs scope: full weight if top 10%, −1 per quartile drop, 0 below median.",
            "7rare":  "Rare 7s. Per stat at 7: full weight up to threshold owners; scaled down beyond; 2× if sole owner.",
            SCORE_HEADER_7_COUNT: "Stat-Count — flat weight × number of stats at or above the configured threshold.",
            "Trait":   "Trait score. Top Priority and Desirable use separate weights; Undesirable uses its own penalty weight.",
            "Aggro":   "Aggression — flat weight if High or Low.",
            "Gender": "Gender — M/F shown; ? (unknown) gets a flat score weight.",
            "Lib":  "Libido — flat weight if High or Low.",
            "Sex":  "Sexuality — flat Gay or Bi weight (straight = no score).",
            "Gene":    "Genetic Safety — average in-scope inbreeding risk; penalties start above 2% baseline.",
            "Age":     "Age penalty. No penalty at/below threshold. Each 3 years over = +1× multiplier (1 over=1×, 4 over=2×, 7 over=3×…).",
            "💗": "Love — 🔭 chip = love interest in scope (flat weight); 🐱 chip = love interest in same room. Both directions.",
            "💥": "Hate — 🔭 chip = rival in scope (per rival, both directions); 🐱 chip = rival in same room. Both directions.",
            "Score":   "Total weighted score — sum of all column scores.",
            "7sub":   "7-Subset: cats in scope whose stat-7 set strictly contains this cat's (▲N = dominated by N cats). Score = (count above threshold) × weight.",
        }
        _col_tips = {ci: _HEADER_TIPS_TEXT[hdr]
                     for ci, hdr in enumerate(_ALL_HEADERS) if hdr in _HEADER_TIPS_TEXT}
        _HeaderTooltipFilter(shh, _col_tips)
        self._score_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._score_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._score_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self._score_table.verticalHeader().setVisible(False)
        self._score_table.setShowGrid(False)
        self._score_table.setAlternatingRowColors(True)
        self._score_table.setSortingEnabled(True)
        self._score_table.setStyleSheet(PRIORITY_TABLE_STYLE)
        shh.setSectionResizeMode(QHeaderView.Interactive)
        shh.setMinimumSectionSize(_COL_MIN_WIDTH)
        self._score_table.setColumnWidth(COL_NAME, 120)
        self._score_table.setColumnWidth(COL_LOC, 112)
        self._score_table.setColumnWidth(COL_INJ, 100)
        for ci in range(_COL_STAT_START, _COL_STAT_START + _NUM_STAT_COLS):
            self._score_table.setColumnWidth(ci, 36)
        for ci in range(_COL_SCORE_START, _COL_SCORE_START + len(_SCORE_COLS)):
            self._score_table.setColumnWidth(ci, 52)
        _sex_col = _COL_SCORE_START + _SCORE_COLS.index("Sex")
        self._score_table.setColumnWidth(_sex_col, 72)
        _age_col = _COL_SCORE_START + _SCORE_COLS.index("Age")
        self._score_table.setColumnWidth(_age_col, 46)
        _loves_col = _COL_SCORE_START + _SCORE_COLS.index("💗")
        self._score_table.setColumnWidth(_loves_col, 52)
        _hates_col = _COL_SCORE_START + _SCORE_COLS.index("💥")
        self._score_table.setColumnWidth(_hates_col, 52)
        _7sub_col = _COL_SCORE_START + _SCORE_COLS.index("7sub")
        self._score_table.setColumnWidth(_7sub_col, 52)
        self._score_table.setColumnWidth(COL_SCORE, 55)
        # Separator columns
        _sep_delegate = _SeparatorDelegate(self._score_table)
        for _sep_ci in _SEP_COLS:
            self._score_table.setColumnWidth(_sep_ci, _SEP_WIDTH)
            self._score_table.setItemDelegateForColumn(_sep_ci, _sep_delegate)
        # Chip delegates
        _chip_delegate = _TraitChipDelegate(self._score_table)
        for _stat_ci in range(_COL_STAT_START, _COL_STAT_START + _NUM_STAT_COLS):
            self._score_table.setItemDelegateForColumn(_stat_ci, _chip_delegate)
        _trait_col   = _COL_SCORE_START + _SCORE_COLS.index("Trait")
        _rare7_col   = _COL_SCORE_START + _SCORE_COLS.index("7rare")
        self._score_table.setItemDelegateForColumn(_trait_col,    _chip_delegate)
        self._score_table.setItemDelegateForColumn(_rare7_col,    _chip_delegate)
        for _ehdr in ("Sex", "💗", "💥",
                       "Lib", "Age", "Gene", "Gender", "Sum", SCORE_HEADER_7_COUNT):
            _ecol = _COL_SCORE_START + _SCORE_COLS.index(_ehdr)
            self._score_table.setItemDelegateForColumn(_ecol, _chip_delegate)
        # Default delegate for "both" mode
        self._both_delegate = _BothModeDelegate(self._score_table)
        self._score_table.setItemDelegate(self._both_delegate)
        # Apply saved column widths
        _mode_widths = self._col_widths.get(self._display_mode, {})
        for ci, w in _mode_widths.items():
            self._score_table.setColumnWidth(ci, w)
        self._apply_stat_column_visibility()
        shh.sortIndicatorChanged.connect(self._on_sort_indicator_changed)
        shh.sectionResized.connect(self._on_col_resized)

        # Wrap table + profile bar + banners in a container
        score_container = QWidget()
        score_container.setStyleSheet(f"background:{CLR_BG_SCORE_AREA};")
        sc_vb = QVBoxLayout(score_container)
        sc_vb.setContentsMargins(0, 0, 0, 0)
        sc_vb.setSpacing(0)
        sc_vb.addWidget(self._build_profile_bar())

        self._filters_active_lbl = self._make_banner(
            icon="⬤", text="Filters Active",
            color=CLR_INTERACTIVE, bg=CLR_INTERACTIVE_BG, border=CLR_INTERACTIVE_BDR,
        )
        self._filters_active_lbl.setVisible(False)
        sc_vb.addWidget(self._filters_active_lbl)

        self._no_scope_banner = self._make_banner(
            icon="⚠", text="No comparison scope selected - scores are unavailable",
            color="#e0a020", bg="#201400", border="#604000",
        )
        self._no_scope_banner.setVisible(False)
        sc_vb.addWidget(self._no_scope_banner)
        sc_vb.addWidget(self._score_table)

        self._score_table.itemSelectionChanged.connect(self._on_cat_selected)
        _FastTooltipFilter(self._score_table)
        self._hate_overlay = _HateRowOverlay(self._score_table)
        self._update_sort_label()
        self._rebuild_cw_columns()
        return score_container

    def _make_abilities_tab_widget(self) -> QTabWidget:
        """Three-tab widget: Active | Passive | Disorder, each backed by a trait table."""
        tw = QTabWidget()
        tw.setStyleSheet(TRAIT_TAB_ABILITIES_STYLE)
        self._active_abilities_table = self._make_trait_table()
        self._passive_abilities_table = self._make_trait_table()
        self._disorders_table = self._make_trait_table()
        tw.addTab(self._active_abilities_table, "Active")
        tw.addTab(self._passive_abilities_table, "Passive")
        tw.addTab(self._disorders_table, "Disorder")
        return tw

    def _make_mutations_tab_widget(self) -> QTabWidget:
        """Two-tab widget: Mutations (green) | Defects (red), each backed by a trait table."""
        tw = QTabWidget()
        tw.setStyleSheet(TRAIT_TAB_MUTATIONS_STYLE)
        self._good_mutations_table = self._make_trait_table()
        self._defects_table = self._make_trait_table()
        tw.addTab(self._good_mutations_table, "Mutations")
        tw.addTab(self._defects_table, "Defects")
        return tw

    def _on_trait_col_resized(self, logical_idx: int, _old: int, new_size: int):
        if new_size > 0:
            self._trait_col_widths[logical_idx] = new_size
            self._col_save_timer.start()

    def _build_trait_section(self) -> QWidget:
        """Four equal panes: ABILITIES | MUTATIONS | CHILDREN | TOP BREEDING RISKS."""
        self._bottom_hs = QSplitter(Qt.Horizontal)
        self._bottom_hs.setHandleWidth(6)
        self._bottom_hs.setStyleSheet(SPLITTER_H_STYLE)
        self._bottom_hs.addWidget(self._make_abilities_tab_widget())
        self._bottom_hs.addWidget(self._make_mutations_tab_widget())
        self._bottom_hs.addWidget(self._make_children_panel())
        self._bottom_hs.addWidget(self._make_risk_panel())
        self._bottom_hs.setSizes(self._bottom_pane_sizes or [210, 210, 220, 220])
        self._bottom_hs.splitterMoved.connect(lambda *_: self._col_save_timer.start())

        _all_trait_tables = (
            self._active_abilities_table, self._passive_abilities_table, self._disorders_table,
            self._good_mutations_table, self._defects_table,
        )
        for tbl in _all_trait_tables:
            if self._trait_col_widths:
                for col_idx, width in self._trait_col_widths.items():
                    tbl.setColumnWidth(col_idx, width)
            tbl.horizontalHeader().sectionResized.connect(self._on_trait_col_resized)

        return self._bottom_hs

    def _build_ui(self):
        vb = QVBoxLayout(self)
        vb.setContentsMargins(0, 0, 0, 0)
        vb.setSpacing(0)

        vb.addWidget(self._build_top_bar())

        hs = CollapseSplitter(Qt.Horizontal)
        hs.setHandleWidth(14)
        vb.addWidget(hs)

        # Left: scope + weights panel (scrollable for short displays)
        left = QWidget()
        left.setObjectName("breed_priority_left_panel")
        left.setStyleSheet(f"QWidget#breed_priority_left_panel {{ background:{CLR_BG_PANEL}; }}")
        lv = QVBoxLayout(left)
        lv.setContentsMargins(8, 12, 8, 8)
        lv.setSpacing(4)
        self._build_scope_panel(lv)
        self._build_weights_panel(lv)
        lv.addStretch()
        left_scroll = QScrollArea()
        left_scroll.setMinimumWidth(LEFT_PANEL_W)
        left_scroll.setWidget(left)
        left_scroll.setWidgetResizable(True)
        left_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        left_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        left_scroll.setStyleSheet(
            "QScrollArea { border: none; background: transparent; }"
            "QWidget#qt_scrollarea_viewport { background: transparent; }"
            "QScrollBar:vertical { width: 5px; background: #0d0d1a; }"
            "QScrollBar::handle:vertical { background: #2a2a4a; border-radius: 2px; }"
        )
        hs.addWidget(left_scroll)

        # Right: score table (top) + trait editor (bottom)
        vs = QSplitter(Qt.Vertical)
        vs.setHandleWidth(6)
        vs.setStyleSheet(SPLITTER_V_STYLE)
        hs.addWidget(vs)
        hs.setCollapsible(0, True)
        hs.setCollapsible(1, False)
        hs.setStretchFactor(0, 0)
        hs.setStretchFactor(1, 1)
        hs.setSizes([LEFT_PANEL_W, 10000])

        vs.addWidget(self._build_score_table_section())
        vs.addWidget(self._build_trait_section())
        vs.setSizes([500, 220])
        vs.setStretchFactor(0, 1)
        vs.setStretchFactor(1, 0)

        # Final pass: sync banner/button state now that all widgets exist
        self._update_filter_btn()

    # ── Cat selection ─────────────────────────────────────────────────────────

    def _on_cat_selected(self):
        row = self._score_table.currentRow()
        if row < 0:
            self._selected_cat = None
        else:
            name_item = self._score_table.item(row, 0)
            cat_name = name_item.text() if name_item else None
            alive = [c for c in self._cats if c.status == "In House"]
            self._selected_cat = next((c for c in alive if c.name == cat_name), None)
        # Update hate-row overlay: highlight rivals in both directions
        hate_ids = set()
        if self._selected_cat:
            # Cats that the selected cat hates
            hate_ids |= {id(c) for c in getattr(self._selected_cat, 'haters', [])}
            # Cats that hate the selected cat (reverse)
            hate_ids |= {id(c) for c in self._hated_by_map.get(id(self._selected_cat), [])}
        self._hate_overlay.set_hate_ids(hate_ids)
        self._refresh_trait_table_order()
        self._refresh_children_panel()
        self._refresh_risk_panel()

    def _refresh_trait_table_order(self):
        cat = self._selected_cat
        if cat is None:
            self._populate_trait_table(self._active_abilities_table, self._all_active_abilities)
            self._populate_trait_table(self._passive_abilities_table, self._all_passive_abilities)
            self._populate_trait_table(self._disorders_table, self._all_disorders)
            self._populate_trait_table(self._good_mutations_table, self._all_good_mutations)
            self._populate_trait_table(self._defects_table, self._all_defects)
            return

        cat_active = {ability_base(a) for a in cat.abilities if not is_basic_trait(a)}
        cat_passive = {ability_base(a) for a in cat.passive_abilities if not is_basic_trait(a)}
        cat_disorders = {ability_base(a) for a in getattr(cat, 'disorders', []) if not is_basic_trait(a)}
        cat_good_mut = set(cat.mutations)
        cat_defects = set(getattr(cat, 'defects', []))

        def _ordered(full_list: list, cat_set: set) -> list:
            return [t for t in full_list if t in cat_set] + [t for t in full_list if t not in cat_set]

        self._populate_trait_table(
            self._active_abilities_table, _ordered(self._all_active_abilities, cat_active),
            highlight=cat_active,
        )
        self._populate_trait_table(
            self._passive_abilities_table, _ordered(self._all_passive_abilities, cat_passive),
            highlight=cat_passive,
        )
        self._populate_trait_table(
            self._disorders_table, _ordered(self._all_disorders, cat_disorders),
            highlight=cat_disorders,
        )
        self._populate_trait_table(
            self._good_mutations_table, _ordered(self._all_good_mutations, cat_good_mut),
            highlight=cat_good_mut,
        )
        self._populate_trait_table(
            self._defects_table, _ordered(self._all_defects, cat_defects),
            highlight=cat_defects,
        )

    # ── Children panel ────────────────────────────────────────────────────────

    def _make_children_panel(self) -> QWidget:
        self._children_filter = "all"   # "all" | "scope" | "room"
        w = QWidget()
        w.setStyleSheet(f"background:{CLR_BG_MAIN};")
        vb = QVBoxLayout(w)
        vb.setContentsMargins(8, 6, 8, 6)
        vb.setSpacing(4)

        # Header row: label + count
        hdr = QHBoxLayout()
        self._children_hdr_lbl = QLabel("CHILDREN")
        self._children_hdr_lbl.setStyleSheet(GROUP_LABEL_TEXT_STYLE)
        self._children_hdr_lbl.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        hdr.addWidget(self._children_hdr_lbl)
        hdr.addStretch()
        self._children_count_lbl = QLabel("")
        self._children_count_lbl.setStyleSheet(f"color:{CLR_TEXT_COUNT}; font-size:10px;")
        self._children_count_lbl.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        hdr.addWidget(self._children_count_lbl)
        vb.addLayout(hdr)

        # Filter toggle: All | In Scope | Same Room
        _seg_base = (
            f"QPushButton {{ background:{CLR_BG_ALT}; color:{CLR_TEXT_COUNT}; border:1px solid {CLR_SURFACE_SEPARATOR};"
            " padding:2px 7px; font-size:10px; }"
            f"QPushButton:hover {{ background:{CLR_BG_PANEL}; color:{CLR_TEXT_SECONDARY}; }}"
            "QPushButton:checked { background:#1e2050; color:#99aaff;"
            " border-color:#3a3a88; }"
        )
        _seg_l = _seg_base + (
            "QPushButton { border-top-left-radius:3px; border-bottom-left-radius:3px;"
            " border-right:none; }"
        )
        _seg_m = _seg_base + "QPushButton { border-radius:0; border-right:none; }"
        _seg_r = _seg_base + (
            "QPushButton { border-top-right-radius:3px;"
            " border-bottom-right-radius:3px; }"
        )
        btn_all   = QPushButton("All")
        btn_scope = QPushButton("In Scope")
        btn_room  = QPushButton("Same Room")
        btn_all.setStyleSheet(_seg_l)
        btn_scope.setStyleSheet(_seg_m)
        btn_room.setStyleSheet(_seg_r)
        for btn in (btn_all, btn_scope, btn_room):
            btn.setCheckable(True)
            btn.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        btn_all.setChecked(True)
        grp = QButtonGroup(w)
        grp.setExclusive(True)
        grp.addButton(btn_all,   0)
        grp.addButton(btn_scope, 1)
        grp.addButton(btn_room,  2)
        _fmap = {0: "all", 1: "scope", 2: "room"}

        def _on_toggle(bid: int, checked: bool):
            if checked:
                self._children_filter = _fmap[bid]
                self._refresh_children_panel()

        grp.idToggled.connect(_on_toggle)

        toggle_row = QHBoxLayout()
        toggle_row.setSpacing(0)
        toggle_row.setContentsMargins(0, 0, 0, 0)
        toggle_row.addWidget(btn_all)
        toggle_row.addWidget(btn_scope)
        toggle_row.addWidget(btn_room)
        toggle_row.addStretch()
        vb.addLayout(toggle_row)

        self._children_list = QListWidget()
        self._children_list.setStyleSheet(
            f"QListWidget {{ background:{CLR_BG_DEEP}; border:1px solid {CLR_BG_HEADER_BDR};"
            f" color:{CLR_TEXT_SECONDARY}; font-size:11px; outline:none; }}"
            "QListWidget::item { padding:2px 6px; }"
            f"QListWidget::item:hover {{ background:{CLR_BG_ALT}; }}"
            f"QListWidget::item:selected {{ background:{CLR_SURFACE_SEPARATOR}; }}"
        )
        self._children_list.setSelectionMode(QAbstractItemView.NoSelection)
        _ListTooltipFilter(self._children_list)
        vb.addWidget(self._children_list, stretch=1)
        return w

    def _refresh_children_panel(self):
        """Populate the children list for the currently selected cat."""
        self._children_list.clear()
        cat = self._selected_cat
        if not cat:
            self._children_hdr_lbl.setText("CHILDREN")
            self._children_count_lbl.setText("")
            return
        _possessive = cat.name + ("'" if cat.name.endswith("s") else "'s")
        self._children_hdr_lbl.setText(f"{_possessive} CHILDREN".upper())
        all_children = sorted(getattr(cat, 'children', []), key=lambda c: c.name)
        filt = getattr(self, '_children_filter', 'all')
        if filt == "scope":
            scope_ids = {id(c) for c in self._get_scope_cats()}
            children = [c for c in all_children if id(c) in scope_ids]
        elif filt == "room":
            children = [c for c in all_children if c.room == cat.room]
        else:
            children = all_children
        total = len(all_children)
        shown = len(children)
        if total == 0:
            self._children_count_lbl.setText("")
        elif filt == "all":
            self._children_count_lbl.setText(f"({total})")
        else:
            self._children_count_lbl.setText(f"({shown}/{total})")
        for child in children:
            room = self._room_display.get(child.room, child.room or "?")
            item = QListWidgetItem(f"{child.name}  ({room})")
            item.setToolTip(self._build_child_tooltip(child))
            self._children_list.addItem(item)

    def _build_child_tooltip(self, cat) -> str:
        """Build a rich HTML tooltip with full cat info for the children panel."""
        return build_child_tooltip(cat, self._display_name)

    # ── Top breeding-risk panel ───────────────────────────────────────────────

    def _make_risk_panel(self) -> QWidget:
        w = QWidget()
        w.setStyleSheet(f"background:{CLR_BG_MAIN};")
        vb = QVBoxLayout(w)
        vb.setContentsMargins(8, 6, 8, 6)
        vb.setSpacing(4)

        hdr = QHBoxLayout()
        self._risk_hdr_lbl = QLabel("TOP BREEDING RISKS")
        self._risk_hdr_lbl.setStyleSheet(GROUP_LABEL_TEXT_STYLE)
        self._risk_hdr_lbl.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        hdr.addWidget(self._risk_hdr_lbl)
        hdr.addStretch()
        self._risk_count_lbl = QLabel("")
        self._risk_count_lbl.setStyleSheet(f"color:{CLR_TEXT_COUNT}; font-size:10px;")
        self._risk_count_lbl.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        hdr.addWidget(self._risk_count_lbl)
        vb.addLayout(hdr)

        self._risk_list = QListWidget()
        self._risk_list.setStyleSheet(
            f"QListWidget {{ background:{CLR_BG_DEEP}; border:1px solid {CLR_BG_HEADER_BDR};"
            f" color:{CLR_TEXT_SECONDARY}; font-size:11px; outline:none; }}"
            "QListWidget::item { padding:2px 6px; }"
            f"QListWidget::item:hover {{ background:{CLR_BG_ALT}; }}"
            f"QListWidget::item:selected {{ background:{CLR_SURFACE_SEPARATOR}; }}"
        )
        self._risk_list.setSelectionMode(QAbstractItemView.NoSelection)
        _ListTooltipFilter(self._risk_list)
        vb.addWidget(self._risk_list, stretch=1)
        return w

    def _refresh_risk_panel(self):
        self._risk_list.clear()
        cat = self._selected_cat
        if not cat:
            self._risk_hdr_lbl.setText("TOP BREEDING RISKS")
            self._risk_count_lbl.setText("")
            return

        _possessive = cat.name + ("'" if cat.name.endswith("s") else "'s")
        self._risk_hdr_lbl.setText(f"{_possessive} TOP BREEDING RISKS".upper())

        scope_cats = self._get_scope_cats()
        risks = []
        for other in scope_cats:
            if other is cat:
                continue
            if not can_breed(cat, other)[0]:
                continue
            risks.append((other, self._scope_pair_risk(cat, other)))

        risks.sort(key=lambda x: x[1], reverse=True)
        top_risks = risks[:20]
        self._risk_count_lbl.setText(f"({len(top_risks)}/{len(risks)})" if risks else "")

        for other, risk_pct in top_risks:
            room = self._room_display.get(other.room, other.room or "?")
            item = QListWidgetItem(f"{other.name}  {risk_pct:.1f}%  ({room})")
            item.setToolTip(f"{cat.name} x {other.name}: {risk_pct:.1f}% risk")
            self._risk_list.addItem(item)

    # ── Scope helpers ─────────────────────────────────────────────────────────

    def _is_kitten(self, cat) -> bool:
        age = getattr(cat, 'age', None)
        return age is not None and age <= 1

    def _get_scope_cats(self) -> list:
        alive = [c for c in self._cats if c.status == "In House"]
        if self._hide_kittens:
            alive = [c for c in alive if not self._is_kitten(c)]
        if self._chk_all_cats.isChecked():
            return alive
        selected = {r for r, chk in self._room_checks.items() if chk.isChecked()}
        if not selected:
            return []   # empty scope - no rooms selected
        return [c for c in alive if c.room in selected]

    def _on_weight_changed(self, key: str, val: float):
        self._weights[key] = val
        self._save_ratings()
        self.recompute()

    def _reset_weights(self):
        for key, val in BREED_PRIORITY_WEIGHTS.items():
            self._weights[key] = val
            if key in self._weight_spins:
                self._weight_spins[key].blockSignals(True)
                self._weight_spins[key].setValue(val)
                self._weight_spins[key].blockSignals(False)
        self._save_ratings()
        self.recompute()

    def _on_all_cats_changed(self, *_):
        """Checking All Cats → check every room; unchecking → uncheck every room."""
        checked = self._chk_all_cats.isChecked()
        for chk in self._room_checks.values():
            chk.blockSignals(True)
            chk.setChecked(checked)
            chk.blockSignals(False)
        self._scope_commit()

    def _on_room_changed(self, *_):
        """Any individual room toggle → uncheck All Cats, then recompute."""
        self._chk_all_cats.blockSignals(True)
        self._chk_all_cats.setChecked(False)
        self._chk_all_cats.blockSignals(False)
        self._scope_commit()

    def _scope_commit(self):
        self._saved_scope = {
            "all_cats": self._chk_all_cats.isChecked(),
            "rooms": {r: chk.isChecked() for r, chk in self._room_checks.items()},
        }
        self._save_ratings()
        self.recompute()

    def _on_hide_kittens_changed(self, *_):
        self._hide_kittens = self._chk_hide_kittens.isChecked()
        self._save_ratings()
        self.recompute()

    def _on_hide_out_of_scope_changed(self, *_):
        self._hide_out_of_scope = self._chk_hide_out_of_scope.isChecked()
        self._save_ratings()
        self.recompute()

    def _on_use_current_stats_changed(self, *_):
        self._use_current_stats = self._chk_use_current_stats.isChecked()
        self._save_ratings()
        self.recompute()

    def _on_add_mutation_stats_changed(self, *_):
        self._add_mutation_stats = self._chk_add_mutation_stats.isChecked()
        self._save_ratings()
        self.recompute()

    def _on_display_mode_changed(self, btn_id: int, checked: bool):
        if not checked:
            return
        _old_mode = self._display_mode
        self._display_mode = ("score", "values", "both")[btn_id]
        # Snapshot current column widths for the old mode before switching
        self._snapshot_col_widths(_old_mode)
        # Apply saved column widths for the new mode
        self._apply_mode_col_widths()
        self._save_ratings()
        self.recompute()

    def _snapshot_col_widths(self, mode: str):
        """Capture static column widths into the per-mode dict.

        CW columns (>= COL_CW_SECTION_START) are excluded — they reset to
        their default width each time _rebuild_cw_columns() runs.
        """
        widths = {}
        for ci in range(COL_CW_SECTION_START):  # static columns only
            if ci in _SEP_COLS:
                continue
            w = self._score_table.columnWidth(ci)
            if w > 0:
                widths[ci] = w
        self._col_widths[mode] = widths

    def _apply_mode_col_widths(self):
        """Apply saved column widths for the current display mode, or keep defaults."""
        _mode_w = self._col_widths.get(self._display_mode, {})
        if not _mode_w:
            return
        shh = self._score_table.horizontalHeader()
        shh.blockSignals(True)
        for ci in range(self._score_table.columnCount()):
            if ci in _SEP_COLS:
                continue  # separator columns have fixed width
            if ci in _mode_w:
                self._score_table.setColumnWidth(ci, _mode_w[ci])
        shh.blockSignals(False)

    def _on_heatmap_toggled(self, checked: bool):
        self._heatmap_on = checked
        self._update_heat_options_enabled()
        self._save_ratings()
        self.recompute()

    def _update_heat_options_enabled(self):
        """Enable/disable heat algo buttons based on heatmap toggle state."""
        _on = self._heatmap_on
        _disabled_style = """
            QPushButton {{
                color: {fg}; background: {bg}; border: 1px solid {border};
                padding: 1px 7px; font-size: 10px; border-radius: 0px;
            }}
            QPushButton:checked {{
                color: {fg_checked}; background: {bg_checked}; border-color: {border_checked};
            }}
        """.format(
            fg=CLR_TEXT_COUNT,
            bg=CLR_BG_SCORE_AREA,
            border=CLR_SURFACE_SEPARATOR,
            fg_checked=CLR_TEXT_UI_LABEL,
            bg_checked=CLR_BG_ALT,
            border_checked=CLR_BG_HEADER_BDR,
        )
        for _hb in (self._btn_heat_col, self._btn_heat_row):
            _hb.setEnabled(_on)
            _hb.setStyleSheet(SEGMENTED_CONTROL_BUTTON_STYLE if _on else _disabled_style)
        self._ha_lbl.setStyleSheet(
            f"color:{CLR_LABEL_SUBDUED}; font-size:10px;" if _on
            else f"color:{CLR_TEXT_COUNT}; font-size:10px;")

    def _on_heat_algo_changed(self, btn_id: int, checked: bool):
        if not checked:
            return
        self._heat_algo = ("column", "row")[btn_id]
        self._save_ratings()
        self.recompute()

    def _on_show_stats_changed(self, *_):
        self._show_stats = self._chk_show_stats.isChecked()
        self._save_ratings()
        self._apply_stat_column_visibility()

    def _apply_stat_column_visibility(self):
        _STAT_DEFAULT_W = 36
        for ci in range(_COL_STAT_START, _COL_STAT_START + _NUM_STAT_COLS):
            if self._show_stats:
                self._score_table.showColumn(ci)
                # showColumn() may restore Qt's internal pre-hide width which
                # can be wrong; explicitly apply the saved or default width.
                _mode_w = self._col_widths.get(self._display_mode, {})
                self._score_table.setColumnWidth(
                    ci, _mode_w.get(ci, _STAT_DEFAULT_W)
                )
            else:
                self._score_table.hideColumn(ci)

    @staticmethod
    def _score_col_alignment(col_idx: int):
        if col_idx in _MULTI_VALUE_LEFT_SCORE_COLS:
            return Qt.AlignLeft | Qt.AlignVCenter
        if col_idx in _SINGLE_VALUE_CENTER_SCORE_COLS:
            return Qt.AlignCenter
        return Qt.AlignCenter

    def _on_col_resized(self, logical_idx: int, _old: int, new_size: int):
        if new_size == 0:
            return  # hideColumn() fires sectionResized(0) - don't save that
        if logical_idx in _SEP_COLS:
            if new_size < _SEP_MIN_WIDTH:
                self._score_table.setColumnWidth(logical_idx, _SEP_MIN_WIDTH)
            return
        mode = self._display_mode
        if mode not in self._col_widths:
            self._col_widths[mode] = {}
        self._col_widths[mode][logical_idx] = new_size
        self._col_save_timer.start()  # debounced - saves 600ms after last drag

    def _on_sort_indicator_changed(self, col_idx: int, order):
        if col_idx in _SEP_COLS:
            return  # don't sort on separator columns
        self._sort_col = col_idx
        self._sort_order = order
        self._update_sort_label()
        self._save_ratings()

    def _update_sort_label(self):
        """Drive the header highlight - the label is gone, the column speaks for itself."""
        hh = self._score_table.horizontalHeader()
        if isinstance(hh, _SortHighlightHeader):
            hh.set_sort(self._sort_col, self._sort_order)

    _FILTER_BTN_ACTIVE = ACTION_BUTTON_PRIMARY_EMPHASIS_STYLE
    _FILTER_BTN_INACTIVE = ACTION_BUTTON_SECONDARY_STYLE
    _FILTER_TOGGLE_ON = ACTION_BUTTON_PRIMARY_STYLE
    _FILTER_TOGGLE_OFF = TOGGLE_BUTTON_INACTIVE_STYLE

    def _update_filter_btn(self):
        active = self._filters.is_any_active()
        effectively_on = active and self._filters_enabled
        self._filter_btn.setText("Filters ●" if active else "Filters…")
        self._filter_btn.setStyleSheet(
            self._FILTER_BTN_ACTIVE if active else self._FILTER_BTN_INACTIVE
        )
        # Toggle button: only visible when filters are configured
        if hasattr(self, '_filter_toggle'):
            self._filter_toggle.setVisible(active)
            self._filter_toggle.blockSignals(True)
            self._filter_toggle.setChecked(self._filters_enabled)
            self._filter_toggle.blockSignals(False)
            self._filter_toggle.setText("On" if self._filters_enabled else "Off")
            self._filter_toggle.setStyleSheet(
                self._FILTER_TOGGLE_ON if self._filters_enabled else self._FILTER_TOGGLE_OFF
            )
        if hasattr(self, '_filters_active_lbl'):
            self._filters_active_lbl.setVisible(effectively_on)

    def _on_filter_toggle(self):
        self._filters_enabled = self._filter_toggle.isChecked()
        self._save_ratings()
        self._update_filter_btn()
        self.recompute()

    def _open_filters(self):
        _avail_rooms = sorted({
            self._room_display.get(c.room, c.room or "")
            for c in self._cats
            if c.room
        })
        dlg = FilterDialog(self, self._filters, _avail_rooms)
        if dlg.exec():
            new_state = dlg.applied_state()
            if new_state is not None:
                self._filters = new_state
                self._save_ratings()
                self._update_filter_btn()
                self.recompute()

    # ── Data ─────────────────────────────────────────────────────────────────

    def configure_deck_save_pull(
        self,
        current_save_provider: Callable[[], Optional[str]],
        on_reload_requested: Callable[[], None],
        on_status_message: Callable[[str], None],
    ):
        """Wire the temporary Deck save pull button and controller for this view."""
        from mewgenics.utils.deck_save_pull import create_temp_deck_save_puller

        puller = create_temp_deck_save_puller(
            parent=self,
            current_save_provider=current_save_provider,
        )
        puller.started.connect(lambda: self._btn_pull_deck_save.set_busy(True))
        puller.finished.connect(lambda: self._btn_pull_deck_save.set_busy(False))
        puller.message.connect(on_status_message)
        puller.reloadRequested.connect(on_reload_requested)
        self._btn_pull_deck_save.set_callback(puller.pull_and_reload)
        self._deck_save_puller = puller

    def set_cats(self, cats: list):
        self._cats = cats
        alive = [c for c in cats if c.status == "In House"]
        _risk_memo: dict = {}

        saved_rooms = self._saved_scope.get("rooms", {})
        self._chk_all_cats.blockSignals(True)
        self._chk_all_cats.setChecked(self._saved_scope.get("all_cats", True))
        self._chk_all_cats.blockSignals(False)
        while self._room_checks_vb.count():
            item = self._room_checks_vb.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._room_checks.clear()

        _ROOM_ORDER = {
            "Attic": 0,
            "Floor2_Large": 1, "Floor2_Small": 2,
            "Floor1_Large": 3, "Floor1_Small": 4,
        }
        rooms = sorted(
            {c.room for c in alive if c.room},
            key=lambda r: _ROOM_ORDER.get(r, 99),
        )
        _all_cats_on = self._chk_all_cats.isChecked()
        # Gender colors matching the table's Gender column chips
        _GC_M = CLR_GENDER_MALE
        _GC_F = CLR_GENDER_FEMALE
        _GC_U = CLR_GENDER_UNKNOWN
        for room in rooms:
            room_cats = [c for c in alive if c.room == room]
            # For risk calculation, exclude kittens when the option is on.
            risk_cats = [c for c in room_cats if not self._is_kitten(c)] \
                if self._hide_kittens else room_cats
            _n = len(room_cats)
            _nm = sum(1 for c in room_cats if getattr(c, 'gender_display', '?') in ('M', 'Male'))
            _nf = sum(1 for c in room_cats if getattr(c, 'gender_display', '?') in ('F', 'Female'))
            _nu = _n - _nm - _nf
            _room_avg_r = 0.0
            if len(risk_cats) > 1:
                _per_cat = []
                for _cat in risk_cats:
                    _vals = [
                        float(risk_percent(_cat, _other, _risk_memo))
                        for _other in risk_cats
                        if _other is not _cat and can_breed(_cat, _other)[0]
                    ]
                    if _vals:
                        _per_cat.append(sum(_vals) / len(_vals))
                if _per_cat:
                    _room_avg_r = sum(_per_cat) / len(_per_cat)

            row_w = QWidget()
            row_w.setStyleSheet("background:transparent;")
            row_h = QHBoxLayout(row_w)
            row_h.setContentsMargins(0, 0, 0, 0)
            row_h.setSpacing(3)

            chk = QCheckBox(self._room_display.get(room, room))
            chk.setStyleSheet(f"color:{CLR_TEXT_UI_LABEL}; font-size:11px;")
            # If All Cats is on, all room boxes start checked; otherwise restore saved state
            chk.setChecked(_all_cats_on or saved_rooms.get(room, False))
            chk.stateChanged.connect(self._on_room_changed)
            row_h.addWidget(chk)
            _row_vsep = QFrame()
            _row_vsep.setFrameShape(QFrame.VLine)
            _row_vsep.setStyleSheet(f"color:{CLR_SURFACE_SEPARATOR};")
            _row_vsep.setFixedWidth(1)
            row_h.addWidget(_row_vsep)

            if _n > 0:
                for _cnt, _gc in ((_nm, _GC_M), (_nf, _GC_F), (_nu, _GC_U)):
                    _pct = round(_cnt / _n * 100)
                    _glbl = QLabel(f"{_pct}%")
                    _glbl.setFixedWidth(32)   # fixed width keeps columns vertically aligned
                    _glbl.setStyleSheet(
                        f"color:{CLR_TEXT_COUNT if _pct == 0 else _gc}; font-size:12px;"
                    )
                    _glbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
                    row_h.addWidget(_glbl)
                _r_lbl = QLabel(f"R{_room_avg_r:.1f}")
                _r_lbl.setFixedWidth(44)
                if _room_avg_r <= 2.0:
                    _r_color = CLR_DESIRABLE
                elif _room_avg_r <= 4.0:
                    _r_color = "#b0a040"
                elif _room_avg_r <= 8.0:
                    _r_color = "#e08030"
                else:
                    _r_color = CLR_UNDESIRABLE
                _r_lbl.setStyleSheet(f"color:{_r_color}; font-size:12px;")
                _r_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
                row_h.addWidget(_r_lbl)

            self._room_checks_vb.addWidget(row_w)
            self._room_checks[room] = chk

        self._all_active_abilities = sorted({
            ability_base(a)
            for c in alive for a in c.abilities
            if not is_basic_trait(a)
        })
        self._all_passive_abilities = sorted({
            ability_base(a)
            for c in alive for a in c.passive_abilities
            if not is_basic_trait(a)
        })
        self._all_disorders = sorted({
            ability_base(a)
            for c in alive for a in getattr(c, 'disorders', [])
            if not is_basic_trait(a)
        })
        # Full union kept for scoring and Complex Weights
        self._all_abilities = sorted(
            set(self._all_active_abilities) | set(self._all_passive_abilities) | set(self._all_disorders)
        )
        self._all_good_mutations = sorted({
            m for c in alive for m in c.mutations
            if not is_basic_trait(m)
        })
        self._all_defects = sorted({
            m for c in alive for m in getattr(c, 'defects', [])
            if not is_basic_trait(m)
        })
        # Full union kept for scoring and Complex Weights
        self._all_mutations = sorted(set(self._all_good_mutations) | set(self._all_defects))
        # Track which mutation names are birth defects (for auto-defaulting to undesirable)
        self._defect_names: set[str] = {
            d for c in alive for d in getattr(c, 'defects', [])
        }
        self._mutation_tips: dict[str, str] = {}
        for c in alive:
            for text, tip in getattr(c, "mutation_chip_items", []):
                if tip and text not in self._mutation_tips:
                    self._mutation_tips[text] = tip
            for text, tip in getattr(c, "defect_chip_items", []):
                if tip and text not in self._mutation_tips:
                    self._mutation_tips[text] = tip
        # Auto-default defects to undesirable
        for defect in self._defect_names:
            if defect not in self._ma_ratings:
                self._ma_ratings[defect] = -1
        self._selected_cat = None
        self._populate_trait_table(self._active_abilities_table, self._all_active_abilities)
        self._populate_trait_table(self._passive_abilities_table, self._all_passive_abilities)
        self._populate_trait_table(self._disorders_table, self._all_disorders)
        self._populate_trait_table(self._good_mutations_table, self._all_good_mutations)
        self._populate_trait_table(self._defects_table, self._all_defects)
        if self._cw_dialog is not None and self._cw_dialog.isVisible():
            self._cw_dialog.refresh_traits(self._all_abilities + self._all_mutations)
        self.recompute()

    def _populate_trait_table(self, table: QTableWidget, traits: list,
                              highlight: set | None = None):
        visible = [t for t in traits if not is_basic_trait(t)]
        self._populating = True
        table.setSortingEnabled(False)
        table.setRowCount(len(visible))
        _HL_BG    = QColor(CLR_BG_HEADER)
        _UNSET_BG = QColor(CLR_BG_SCORE_AREA)
        _RATED_BG = QBrush()

        for row, trait in enumerate(visible):
            raw_display = self._display_name(trait)
            # Emojify stat abbreviations embedded in mutation display names
            # (e.g. "Body Mutation +2 STR, -1 INT" → "Body Mutation +2💪, -1💡")
            display = StatTextFormatter.emojify(raw_display)
            # Build inline summary from mutation tip or ability tip
            mut_tip = self._mutation_tips.get(trait, "")
            # Birth defects share a display name (e.g. "Ear Birth Defect") but different
            # mutation IDs have different gameplay effects.  The cached tip may come from
            # a different cat's defect variant, so when a cat is selected prefer that
            # cat's own defect_chip_items tip for the matching defect name.
            if mut_tip and trait in self._defect_names and self._selected_cat is not None:
                for _defect_text, _defect_tip in getattr(self._selected_cat, 'defect_chip_items', []):
                    if _defect_text == trait and _defect_tip:
                        mut_tip = _defect_tip
                        break
            abl_tip = self._ability_tip(trait) if not mut_tip else ""
            if mut_tip:
                summary = StatTextFormatter.mutation_summary(mut_tip)
            elif abl_tip:
                summary = StatTextFormatter.ability_summary(abl_tip)
            else:
                summary = ""
            # Avoid duplicating stats already present in the display name
            display_text = f"{display}  {summary}" if summary and summary not in display else display
            name_item = QTableWidgetItem(display_text)
            name_item.setData(Qt.UserRole, trait)
            name_item.setData(Qt.UserRole + 10, display)    # trait name only (emojified)
            name_item.setData(Qt.UserRole + 11, summary)    # stat summary only
            name_item.setFlags(Qt.ItemIsEnabled)
            current = self._ma_ratings.get(trait)
            if highlight and trait in highlight:
                name_item.setBackground(_HL_BG)
            elif current is None:
                name_item.setBackground(_UNSET_BG)
            tip = mut_tip or abl_tip
            if tip:
                esc_display = _html.escape(display)
                esc_tip = _html.escape(tip).replace("\n", "<br>")
                name_item.setToolTip(f"<b>{esc_display}</b><br><br>{esc_tip}")
            table.setItem(row, 0, name_item)

            combo = _RatingCombo()
            for ci, clr in enumerate(RATING_ITEM_COLORS):
                combo.model().item(ci).setForeground(QColor(clr))
            # Tooltip is on the name item (col 0) and shown via _FastTooltipFilter
            init_idx = {v: i for i, v in enumerate(TRAIT_RATING_VALUES)}.get(current, TRAIT_RATING_VALUES.index(None))
            combo.setCurrentIndex(init_idx)

            def _apply_combo_color(idx: int, cb: QComboBox, ni: QTableWidgetItem,
                                   is_highlighted: bool):
                clr = RATING_ITEM_COLORS[idx] if 0 <= idx < len(RATING_ITEM_COLORS) else "#ccc"
                cb.setStyleSheet(
                    PRIORITY_COMBO_STYLE + f"QComboBox {{ color:{clr}; }}"
                )
                if is_highlighted:
                    pass
                elif idx == 2:
                    ni.setBackground(_UNSET_BG)
                else:
                    ni.setBackground(_RATED_BG)

            is_hl = bool(highlight and trait in highlight)
            _apply_combo_color(init_idx, combo, name_item, is_hl)
            combo.currentIndexChanged.connect(
                lambda idx, t=trait, cb=combo, ni=name_item, hl=is_hl: [
                    self._on_rating_changed(t, idx),
                    _apply_combo_color(idx, cb, ni, hl),
                ]
            )
            table.setCellWidget(row, 1, combo)
            table.setRowHeight(row, 24)
        self._populating = False

    def _on_rating_changed(self, trait: str, combo_idx: int):
        if self._populating:
            return
        val = TRAIT_RATING_VALUES[combo_idx]
        if val is None:
            self._ma_ratings.pop(trait, None)
        else:
            self._ma_ratings[trait] = val
        self._save_ratings()
        self.recompute()

    # ── Score computation ─────────────────────────────────────────────────────

    def _scope_pair_risk(self, a, b) -> float:
        _k = (id(a), id(b)) if id(a) < id(b) else (id(b), id(a))
        _cached = self._scope_pair_risks.get(_k)
        if _cached is not None:
            return float(_cached)
        return float(risk_percent(a, b))

    def _build_cat_tooltip(self, cat, result: ScoreResult, scope_cats: list,
                           *, cw_items: list | None = None,
                           cw_delta_total: float = 0.0) -> str:
        _top_gene_risks = []
        _risk_floor = 2.0
        for _other in scope_cats:
            if _other is cat:
                continue
            if not can_breed(cat, _other)[0]:
                continue
            _risk = self._scope_pair_risk(cat, _other)
            if _risk <= _risk_floor:
                continue
            _top_gene_risks.append((_other.name, _risk))
        _top_gene_risks.sort(key=lambda x: x[1], reverse=True)
        return build_cat_tooltip(
            cat, result, scope_cats,
            weights=self._weights,
            ma_ratings=self._ma_ratings,
            display_name_fn=self._display_name,
            room_display=self._room_display,
            hated_by_map=self._hated_by_map,
            loved_by_map=self._loved_by_map,
            cat_injuries_fn=lambda c: _cat_injuries(c, self._stat_names),
            top_gene_risks=_top_gene_risks[:3],
            cw_items=cw_items or [],
            cw_delta_total=cw_delta_total,
        )

    def _raw_col_value(self, cat, col_idx: int,
                       scope_gene_risk: float,
                       all_scope_gene_risks: list) -> tuple:
        """Return (text, sort_val, color) for a column in value mode."""
        return raw_col_value(
            cat, col_idx, scope_gene_risk, all_scope_gene_risks,
            weights=self._weights,
            room_display=self._room_display,
        )

    def recompute(self, *_):
        if self._populating:
            return
        _restore_name = self._selected_cat.name if self._selected_cat else None

        scope_cats = self._get_scope_cats()
        _no_scope = len(scope_cats) == 0
        self._no_scope_banner.setVisible(_no_scope)

        alive = [c for c in self._cats if c.status == "In House"]
        if self._hide_kittens:
            alive = [c for c in alive if not self._is_kitten(c)]
        if self._hide_out_of_scope and not _no_scope:
            scope_set = {id(c) for c in scope_cats}
            alive = [c for c in alive if id(c) in scope_set]

        scope_set = {id(c) for c in scope_cats}

        # Pre-compute relationship maps, 7-sets, and scores
        _seven_sets, _scope_7_sets = compute_seven_sets(alive, scope_set,
                                                         use_current_stats=self._use_current_stats,
                                                         add_mutation_stats=self._add_mutation_stats)

        _hated_by_map, _loved_by_map = build_relationship_maps(self._cats)
        self._hated_by_map = _hated_by_map
        self._loved_by_map = _loved_by_map

        _gene_risk_memo: dict = {}
        (results, _cat_sub_counts, _all_scores_sorted,
         _all_scope_gene_risks, _all_scope_children, _max_7_count,
         _scope_stat_sums, _pair_risk_cache) = compute_all_scores(
            alive, scope_cats, scope_set,
            _seven_sets, _scope_7_sets, _hated_by_map,
            self._ma_ratings, self._stat_names, self._weights, self._display_name,
            gene_risk_lookup=lambda a, b, _m=_gene_risk_memo: risk_percent(a, b, _m),
            use_current_stats=self._use_current_stats,
            add_mutation_stats=self._add_mutation_stats,
        )
        self._scope_pair_risks = _pair_risk_cache
        _max_scope_gene_risk = max(_all_scope_gene_risks, default=0.0)

        def _children_in_scope(cat):
            return sum(1 for ch in cat.children if id(ch) in scope_set)

        # Capture current visible row order for stable re-sort
        _cat_id_map = {id(c): c for c in alive}
        _prev_order: dict[int, int] = {}
        for _r in range(self._score_table.rowCount()):
            _ni = self._score_table.item(_r, COL_NAME)
            if _ni is not None:
                _cid = _ni.data(Qt.UserRole + 1)
                if _cid in _cat_id_map:
                    _prev_order[_cid] = _r
        if _prev_order:
            alive.sort(key=lambda c: _prev_order.get(id(c), 999999))

        # Heatmap normalisation
        _is_heat = self._heatmap_on
        _heat_row = _is_heat and self._heat_algo == "row"
        _col_max_abs, _row_max_abs, _score_max_abs = compute_heatmap_norms(
            results, alive, _is_heat, self._heat_algo,
        )

        self._score_table.setSortingEnabled(False)
        self._score_table.setRowCount(len(alive))

        # Stat column coloring mode: rank-based per column when either stat
        # modifier toggle is active; legacy fixed-value scheme otherwise.
        # Ranks are over unique values so outliers only anchor the bright end
        # without compressing the colors of all other values.
        _stat_dynamic_mode = self._use_current_stats or self._add_mutation_stats
        if _stat_dynamic_mode:
            _stat_col_ranks: dict[str, dict[int, float]] = {}
            for _sn in _STAT_COL_NAMES:
                _col_vals = [
                    get_cat_stats(c, self._use_current_stats, self._add_mutation_stats).get(_sn, 0)
                    for c in alive
                ]
                _stat_col_ranks[_sn] = ChipColors.stat_col_ranks(_col_vals) if _col_vals else {}

        for row, cat in enumerate(alive):
            result = results[id(cat)]
            scope_gene_risk = result.scope_gene_risk
            ch_in_scope = _children_in_scope(cat)
            _sub_count = _cat_sub_counts.get(id(cat), 0)
            _has_sevens = bool(_seven_sets.get(id(cat), frozenset()))

            # ── Name ──
            name_item = QTableWidgetItem(cat.name)
            name_item.setData(Qt.UserRole + 1, id(cat))  # used to restore row order on recompute
            name_item.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            name_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            self._score_table.setItem(row, COL_NAME, name_item)

            # ── Location ──
            loc_text = self._room_display.get(cat.room, cat.room or "")
            _loc_color = _ROOM_STYLE.get(loc_text)
            loc_item = QTableWidgetItem(loc_text)
            loc_item.setForeground(QColor(_loc_color or CLR_VALUE_NEUTRAL))
            loc_item.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            loc_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            if id(cat) in scope_set:
                _lf = loc_item.font()
                _lf.setBold(True)
                loc_item.setFont(_lf)
            self._score_table.setItem(row, COL_LOC, loc_item)

            # ── Injuries ──
            _injuries = _cat_injuries(cat, self._stat_names)
            if _injuries:
                _inj_parts = []
                for _iname, _isn, _idelta in _injuries:
                    _inj_parts.append(f"{_isn} {_idelta:+d}")
                inj_item = QTableWidgetItem(", ".join(_inj_parts))
                inj_item.setForeground(QColor("#cc4444"))
                inj_item.setData(Qt.UserRole, float(len(_injuries)))
            else:
                inj_item = QTableWidgetItem("-")
                inj_item.setForeground(QColor(CLR_TEXT_MUTED))
                inj_item.setData(Qt.UserRole, 0.0)
            inj_item.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            inj_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            self._score_table.setItem(row, COL_INJ, inj_item)

            # ── Stat columns ──
            _cat_stats = get_cat_stats(cat, self._use_current_stats, self._add_mutation_stats)
            for si, stat in enumerate(_STAT_COL_NAMES):
                val = _cat_stats.get(stat, 0)
                stat_item = _NumericSortItem(str(val))
                stat_item.setData(Qt.UserRole, float(val))
                stat_item.setTextAlignment(Qt.AlignCenter)
                stat_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                if _stat_dynamic_mode:
                    # Rank-based coloring: each value's color reflects its rank
                    # among unique values in this column, not its absolute distance
                    # from the column min/max.
                    _t = _stat_col_ranks[stat].get(val, 0.0)
                    _sb, _val_fg = ChipColors.stat_ranked(_t)
                else:
                    # Legacy fixed-value scheme: 7=teal, 6=gold, 5=tan, rest=grey.
                    _STAT_FIXED_CLR = {7: "#44cc66", 6: "#bba844", 5: "#998855"}
                    _val_fg = _STAT_FIXED_CLR.get(val, CLR_VALUE_NEUTRAL)
                    _sb = _CHIP_NEUTRAL_STABLE[0] if val == 7 else _CHIP_NEUTRAL_FAINT[0]
                stat_item.setForeground(QColor(_val_fg))
                stat_item.setData(_CHIP_ROLE, [(str(val), _sb, _val_fg)])
                stat_item.setText("")
                self._score_table.setItem(row, _COL_STAT_START + si, stat_item)

            # ── Separator columns ──
            for _sep_ci in _SEP_COLS:
                _sep_item = QTableWidgetItem("")
                _sep_item.setFlags(Qt.ItemIsEnabled)  # not selectable, not editable
                _sep_item.setData(Qt.UserRole, 0.0)
                self._score_table.setItem(row, _sep_ci, _sep_item)

            # ── Score/value columns ──
            # sort_val is ALWAYS the score regardless of display mode so that
            # switching modes never changes the sort order.
            _cw = self._weights
            for ci, (hdr, keys) in enumerate(SCORE_COLUMNS):
                col_idx = _COL_SCORE_START + ci
                # Compute score (sort value) for this column - always used.
                score_val = sum(result.subtotals.get(k, 0.0) for k in keys)

                # Helper: score → display color
                def _score_color(v, pos=CLR_VALUE_POS, neg=CLR_VALUE_NEG):
                    return pos if v > 0 else neg if v < 0 else CLR_TEXT_COUNT

                # ── Love / Hate: combined scope + room chips ──
                if hdr in ("💗", "💥"):
                    _is_love = hdr == "💗"
                    _rel_list = getattr(cat, 'lovers' if _is_love else 'haters', [])
                    _reverse_map = _loved_by_map if _is_love else _hated_by_map
                    _rb = _reverse_map.get(id(cat), [])
                    _own_set = {id(h) for h in _rel_list}
                    _cat_room = getattr(cat, 'room', None)

                    # Scope: in-scope matches (forward + reverse)
                    _scope_fwd = [c for c in _rel_list if id(c) in scope_set]
                    _scope_rev = [c for c in _rb
                                  if id(c) not in _own_set and id(c) in scope_set]
                    _all_scope = _scope_fwd + _scope_rev

                    # Room: same-room matches (forward + reverse)
                    _room_fwd = [c for c in _rel_list
                                 if _cat_room and getattr(c, 'room', None) == _cat_room]
                    _room_rev = [c for c in _rb
                                 if id(c) not in _own_set
                                 and _cat_room and getattr(c, 'room', None) == _cat_room]
                    _all_room = _room_fwd + _room_rev

                    _all_rels = _all_scope + _all_room
                    _do_vals = self._display_mode in ("values", "both")
                    _rel_item = _NumericSortItem("")
                    _rel_item.setData(Qt.UserRole, score_val)
                    _rel_item.setTextAlignment(self._score_col_alignment(col_idx))
                    _rel_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                    if _do_vals:
                        _cbg, _cfg = _CHIP_LOVE if _is_love else _CHIP_HATE
                        _coutline = _CHIP_LOVE_OUTLINE if _is_love else _CHIP_HATE_OUTLINE
                        _rel_chips = (
                            [(_EMOJI_SCOPE, _cbg, _cfg, _coutline) for _ in _all_scope]
                            + [(_EMOJI_ROOM,  _cbg, _cfg, _coutline) for _ in _all_room]
                        )
                        if _rel_chips:
                            _rel_item.setData(_CHIP_ROLE, _rel_chips)
                        else:
                            _rel_item.setForeground(QColor(CLR_TEXT_COUNT))
                    else:
                        _color = _score_color(score_val)
                        _rel_item.setText(f"{score_val:+.1f}" if score_val != 0 else "")
                        _rel_item.setForeground(QColor(_color))
                    if self._display_mode == "both" and _all_rels and score_val != 0:
                        _rel_item.setData(_SCORE_SECONDARY_ROLE, f"{score_val:+.1f}")
                    # (hate/love details are shown in the main cat tooltip)
                    if _is_heat and score_val != 0:
                        _norm = _row_max_abs.get(id(cat), 1.0) if _heat_row else _col_max_abs.get(ci, 1.0)
                        _rel_item.setData(_HEATMAP_ROLE, score_val / _norm)
                    self._score_table.setItem(row, col_idx, _rel_item)
                    continue

                # ── Sexual column in value mode: show flag chip ──
                if hdr == "Sex" and self._display_mode in ("values", "both"):
                    _sex = getattr(cat, 'sexuality', 'straight') or 'straight'
                    _sx_item = _NumericSortItem("")
                    _sx_item.setData(Qt.UserRole, score_val)
                    _sx_item.setTextAlignment(self._score_col_alignment(col_idx))
                    _sx_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                    if _sex != 'straight':
                        _gay_w = _cw.get("gay_pref", 0.0)
                        _bi_w  = _cw.get("bi_pref",  0.0)
                        _gay_clr, _bi_clr = ChipColors.paired_weights(_gay_w, _bi_w)
                        _sx_ind_clr = _gay_clr if _sex == 'gay' else _bi_clr
                        _sx_bg, _sx_fg = ChipColors.sex_indicator(_sx_ind_clr)
                        _sx_emoji = _SEX_EMOJI_GAY if _sex == 'gay' else _SEX_EMOJI_BI
                        # "BI" label: teal text; slightly darker bg when grey
                        if _sex == 'bi':
                            _sx_fg = "#4ecdc4"
                            if _sx_bg == CLR_TEXT_GRAYEDOUT:
                                _sx_bg = "#383838"
                        _sx_item.setData(_CHIP_ROLE, [(_sx_emoji, _sx_bg, _sx_fg)])
                    if self._display_mode == "both" and score_val != 0:
                        _sx_item.setData(_SCORE_SECONDARY_ROLE, f"{score_val:+.1f}")
                    if _is_heat and score_val != 0:
                        _norm = _row_max_abs.get(id(cat), 1.0) if _heat_row else _col_max_abs.get(ci, 1.0)
                        _sx_item.setData(_HEATMAP_ROLE, score_val / _norm)
                    self._score_table.setItem(row, col_idx, _sx_item)
                    continue

                _chips = []   # populated for Trait column in value mode
                _score_for_sub = score_val   # preserved for "both" secondary text
                if self._display_mode in ("values", "both"):
                    # ── Value / Both display mode ──
                    if hdr == "Sum":
                        s = sum(_cat_stats.values())
                        score_val = float(s)   # sort by raw sum, not score
                        _unique_scope_sums = sorted(set(_scope_stat_sums) | {s})
                        _n_sum_unique = len(_unique_scope_sums)
                        if _n_sum_unique <= 1:
                            _sum_t = 1.0
                        else:
                            _sum_t = _unique_scope_sums.index(s) / (_n_sum_unique - 1)
                        color = ColorUtils.lerp("#cc3333", CLR_DESIRABLE, _sum_t)
                        text = str(s)
                        # Rank-driven text color; chip bg is score-aware.
                        _chip_bg = (
                            ColorUtils.derive_chip_bg(color, CLR_BG_SCORE_AREA)
                            if _score_for_sub != 0 else _CHIP_NEUTRAL_STABLE[0]
                        )
                        _chip_fg = color if _score_for_sub != 0 else _CHIP_NEUTRAL_STABLE[1]
                        _chips = [(text, _chip_bg, _chip_fg)]
                    elif hdr == "7rare":
                        # Chips: one per stat at 7, colored by rarity vs threshold
                        _cat_in_scope = id(cat) in scope_set
                        _thr = _cw.get("stat_7_threshold", 7.0)
                        for _sn in _STAT_COL_NAMES:
                            if _cat_stats.get(_sn) == 7:
                                _n_sc = sum(1 for _sc in scope_cats if get_cat_stats(_sc, self._use_current_stats, self._add_mutation_stats).get(_sn) == 7)
                                _n = _n_sc if _cat_in_scope else _n_sc + 1
                                _bg, _fg = ChipColors.rarity(_n, _thr)
                                _chips.append((_sn, _bg, _fg))
                        text = ""   # rendered by delegate
                        color = _score_color(score_val)
                    elif hdr == SCORE_HEADER_7_COUNT:
                        _stat_cnt_thr = int(round(_cw.get("stat_count_threshold", 7.0)))
                        count_7 = sum(1 for v in _cat_stats.values() if v >= _stat_cnt_thr)
                        w_7 = _cw.get(keys[0], 0.0)
                        color = ChipColors.sevens(count_7, _max_7_count, w_7 >= 0)
                        text = f"{count_7}x{_stat_cnt_thr}+"
                        _chip_bg = (
                            ColorUtils.derive_chip_bg(color, CLR_BG_SCORE_AREA)
                            if _score_for_sub != 0 else _CHIP_NEUTRAL_STABLE[0]
                        )
                        _chip_fg = color if _score_for_sub != 0 else _CHIP_NEUTRAL_STABLE[1]
                        _chips = [(text, _chip_bg, _chip_fg)]
                    elif hdr == "Trait":
                        # Value mode: individual colored chips per rated trait
                        # Hide entirely when the trait weight is 0 (no scoring context)
                        _chips = []
                        if any(
                            _cw.get(_k, 0.0) != 0.0
                            for _k in ("trait_top_priority", "trait_desirable", "trait_undesirable")
                        ):
                            for _desc, _pts in result.breakdown:
                                if _desc.startswith(("Sole owner", "Top Priority (÷", "Desirable (÷", "Undesirable:")):
                                    _tname = _desc.split(": ", 1)[1]
                                    if _desc.startswith(("Sole owner (top priority)", "Top Priority (÷")):
                                        _bg, _fg = _CHIP_TOP_PRIORITY
                                    elif _pts > 0:
                                        _bg, _fg = _CHIP_DESIRABLE
                                    else:
                                        _bg, _fg = _CHIP_UNDESIRABLE
                                    _chips.append((_tname, _bg, _fg))
                        text = ""   # rendered by delegate
                        color = _score_color(score_val)
                    elif hdr == "Aggro":
                        _high_ag_w = _cw.get("high_aggression", 0.0)
                        _low_ag_w  = _cw.get("low_aggression",  0.0)
                        _high_ag_clr, _low_ag_clr = ChipColors.paired_weights(_high_ag_w, _low_ag_w)
                        a = cat.aggression
                        if a is None:
                            text, color = "?", "#666"
                        elif a >= TRAIT_HIGH_THRESHOLD:
                            text, color = "▲Hi", _high_ag_clr
                        elif a < TRAIT_LOW_THRESHOLD:
                            text, color = "▼Lo", _low_ag_clr
                        else:
                            text, color = "—", CLR_TEXT_GRAYEDOUT
                    elif hdr == "Gene":
                        if scope_gene_risk is None:
                            _chips = [("—", CLR_BG_SCORE_AREA, CLR_TEXT_GRAYEDOUT)]
                            text, color = "—", CLR_TEXT_GRAYEDOUT
                            score_val = 0.0
                        elif scope_gene_risk <= _cw.get("gene_risk_threshold", GENETIC_SAFE_RISK_FLOOR):
                            _safe_score = float(result.subtotals.get("zero_risk_bonus", 0.0))
                            _score_for_sub = _safe_score
                            score_val = _safe_score
                            _cbg, _cfg = ChipColors.from_score(_safe_score) if _safe_score != 0 else _CHIP_DESIRABLE
                            _chips = [("🛡", _cbg, _cfg)]
                            text, color = "", _cfg
                        else:
                            _risk_txt = f"R{int(round(scope_gene_risk))}"
                            _gene_clr = ChipColors.sevens(scope_gene_risk, _max_scope_gene_risk, False)
                            _cbg = ColorUtils.derive_chip_bg(_gene_clr, CLR_BG_SCORE_AREA)
                            _cfg = _gene_clr
                            _chips = [(_risk_txt, _cbg, _cfg)]
                            text, color = "", _cfg
                    elif hdr == "Gender":
                        gd = getattr(cat, 'gender_display', '?')
                        if gd in ('M', 'Male'):
                            _chips = [("M", *_CHIP_GENDER_MALE)]
                            text, color = "", CLR_GENDER_MALE
                        elif gd in ('F', 'Female'):
                            _chips = [("F", *_CHIP_GENDER_FEMALE)]
                            text, color = "", CLR_GENDER_FEMALE
                        else:
                            _cbg, _cfg = ChipColors.from_score(score_val) if score_val != 0 else _CHIP_GENDER_UNKNOWN
                            _chips = [("?", _cbg, _cfg)]
                            text, color = "", CLR_GENDER_UNKNOWN
                    elif hdr == "Lib":
                        lb = cat.libido
                        if lb is not None and lb >= TRAIT_HIGH_THRESHOLD:
                            _cbg, _cfg = ChipColors.from_score(score_val) if score_val != 0 else _CHIP_DIM
                            _chips = [("❤️", _cbg, _cfg)]
                            text, color = "", _cfg
                        elif lb is not None and lb < TRAIT_LOW_THRESHOLD:
                            _cbg, _cfg = ChipColors.from_score(score_val) if score_val != 0 else _CHIP_DIM
                            _chips = [("💙", _cbg, _cfg)]
                            text, color = "", _cfg
                        else:
                            text, color = "", CLR_TEXT_GRAYEDOUT
                    elif hdr == "Age":
                        age = getattr(cat, 'age', None)
                        if age is None:
                            text, color = "-", "#666"
                        else:
                            _age_thr = int(round(_cw.get("age_threshold", 10.0)))
                            _over = age - _age_thr
                            if _over > 0:
                                _cbg, _cfg = _CHIP_AGE_WARN
                                _chips = [(f"⏳{age}", _cbg, _cfg)]
                                text, color = "", _cfg
                            else:
                                _chips = [(str(age), *_CHIP_NEUTRAL_STABLE)]
                                text, color = "", CLR_VALUE_NEUTRAL
                        score_val = float(age) if age is not None else 0.0
                    elif hdr == "7sub":
                        if _sub_count > 0:
                            text, color = f"▲{_sub_count}", "#cc8844"
                        elif _has_sevens:
                            # Distinguish unique 7-sets from cats with no 7s.
                            text, color = "0", CLR_DESIRABLE
                        else:
                            text, color = "", "#333333"
                    else:
                        text = f"{score_val:+.1f}" if score_val != 0 else ""
                        color = CLR_VALUE_NEUTRAL
                    sub_item = _NumericSortItem(text)
                    sub_item.setData(Qt.UserRole, score_val)
                    if _chips:
                        sub_item.setData(_CHIP_ROLE, _chips)
                    sub_item.setTextAlignment(self._score_col_alignment(col_idx))
                    sub_item.setForeground(QColor(color))
                    if self._display_mode == "both" and _score_for_sub != 0:
                        sub_item.setData(_SCORE_SECONDARY_ROLE, f"{_score_for_sub:+.1f}")
                    if _is_heat and _score_for_sub != 0:
                        _norm = _row_max_abs.get(id(cat), 1.0) if _heat_row else _col_max_abs.get(ci, 1.0)
                        sub_item.setData(_HEATMAP_ROLE, _score_for_sub / _norm)
                else:
                    # ── Score display mode: always show numeric score ──
                    if hdr == SCORE_HEADER_7_COUNT:
                        _stat_cnt_thr = int(round(_cw.get("stat_count_threshold", 7.0)))
                        count_7 = sum(1 for v in _cat_stats.values() if v >= _stat_cnt_thr)
                        w_7 = _cw.get(keys[0], 0.0)
                        color = ChipColors.sevens(count_7, _max_7_count, w_7 >= 0)
                    elif hdr == "Aggro":
                        _hi, _lo = ChipColors.paired_weights(
                            _cw.get("high_aggression", 0.0), _cw.get("low_aggression", 0.0))
                        a = cat.aggression
                        if a is None:       color = "#666"
                        elif a >= TRAIT_HIGH_THRESHOLD: color = _hi
                        elif a < TRAIT_LOW_THRESHOLD:   color = _lo
                        else:               color = CLR_VALUE_NEUTRAL
                    elif hdr == "Lib":
                        _hi, _lo = ChipColors.paired_weights(
                            _cw.get("high_libido", 0.0), _cw.get("low_libido", 0.0))
                        lb = cat.libido
                        if lb is None:      color = "#666"
                        elif lb >= TRAIT_HIGH_THRESHOLD: color = _hi
                        elif lb < TRAIT_LOW_THRESHOLD:   color = _lo
                        else:               color = CLR_VALUE_NEUTRAL
                    elif hdr == "Sex":
                        _gay_clr, _bi_clr = ChipColors.paired_weights(
                            _cw.get("gay_pref", 0.0), _cw.get("bi_pref", 0.0))
                        _sex = getattr(cat, 'sexuality', 'straight') or 'straight'
                        if _sex == 'gay':   color = _gay_clr
                        elif _sex == 'bi':  color = _bi_clr
                        else:               color = CLR_TEXT_COUNT
                    else:
                        color = _score_color(score_val)
                    text = f"{score_val:+.1f}" if score_val != 0 else ""
                    sub_item = _NumericSortItem(text)
                    sub_item.setData(Qt.UserRole, score_val)
                    sub_item.setTextAlignment(self._score_col_alignment(col_idx))
                    sub_item.setForeground(QColor(color))
                    if _is_heat and score_val != 0:
                        _norm = _row_max_abs.get(id(cat), 1.0) if _heat_row else _col_max_abs.get(ci, 1.0)
                        sub_item.setData(_HEATMAP_ROLE, score_val / _norm)
                sub_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                self._score_table.setItem(row, col_idx, sub_item)

            # ── Complex Weight evaluation (must precede total score) ──────────
            _enabled_cws = [cw for cw in self._complex_weights if cw.enabled]
            _cw_matches: list = []
            _cw_delta_total = 0.0
            if _enabled_cws:
                _cat_traits = build_cat_trait_set(cat)
                _cw_matches = compute_cw_matches(
                    _enabled_cws, cat,
                    cat_stats=_cat_stats,
                    cat_traits=_cat_traits,
                    scope_gene_risk=scope_gene_risk,
                    total_score=result.total,  # pre-CW score avoids circularity
                )
                _cw_delta_total = sum(d for matched, d in _cw_matches if matched)
            _adjusted_total = result.total + _cw_delta_total

            # ── Total score ──
            score_item = _NumericSortItem(f"{_adjusted_total:+.1f}")
            score_item.setData(Qt.UserRole, _adjusted_total)
            score_item.setTextAlignment(Qt.AlignCenter)
            _sc_total = len(_all_scores_sorted)
            if _sc_total > 0:
                _sc_rank = sum(1 for v in _all_scores_sorted if v <= _adjusted_total)
                _sc_pct = _sc_rank / _sc_total * 100
                if _sc_pct >= 75:
                    _sc_color = CLR_DESIRABLE
                elif _sc_pct >= 50:
                    _sc_color = "#b0a040"
                elif _sc_pct >= 25:
                    _sc_color = "#e08030"
                else:
                    _sc_color = "#cc3333"
            else:
                _sc_color = CLR_VALUE_NEUTRAL
            score_item.setForeground(QColor(_sc_color))
            score_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            if self._heatmap_on and _adjusted_total != 0:
                score_item.setData(_HEATMAP_ROLE, _adjusted_total / _score_max_abs)
            self._score_table.setItem(row, COL_SCORE, score_item)

            # ── Complex Weight columns ────────────────────────────────────────
            for _cwi, (matched, delta) in enumerate(_cw_matches):
                cw_col = COL_CW_SECTION_START + _cwi
                cw_item = _NumericSortItem("")
                cw_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                cw_item.setData(Qt.UserRole, delta if matched else 0.0)
                cw_item.setTextAlignment(Qt.AlignCenter)
                if matched:
                    if self._display_mode == "score":
                        _sign = "+" if delta >= 0 else ""
                        cw_item.setText(f"{_sign}{delta:.1f}")
                        _c = CLR_VALUE_POS if delta > 0 else CLR_VALUE_NEG if delta < 0 else CLR_VALUE_NEUTRAL
                        cw_item.setForeground(QColor(_c))
                    elif self._display_mode == "values":
                        cw_item.setData(_CHIP_ROLE, [("⭐", "#163030", "#44ddcc")])
                    else:  # "both"
                        cw_item.setData(_CHIP_ROLE, [("⭐", "#163030", "#44ddcc")])
                        if delta != 0:
                            _sign = "+" if delta >= 0 else ""
                            cw_item.setData(_SCORE_SECONDARY_ROLE, f"{_sign}{delta:.1f}")
                self._score_table.setItem(row, cw_col, cw_item)

            # ── No-scope override: replace all score columns with N/A ──
            if _no_scope:
                for _ci in range(len(SCORE_COLUMNS)):
                    _it = _NumericSortItem("N/A")
                    _it.setData(Qt.UserRole, -999.0)
                    _it.setTextAlignment(Qt.AlignCenter)
                    _it.setForeground(QColor(CLR_TEXT_COUNT))
                    _it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                    self._score_table.setItem(row, _COL_SCORE_START + _ci, _it)
                _sc_it = _NumericSortItem("N/A")
                _sc_it.setData(Qt.UserRole, -999.0)
                _sc_it.setTextAlignment(Qt.AlignCenter)
                _sc_it.setForeground(QColor(CLR_TEXT_COUNT))
                _sc_it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                self._score_table.setItem(row, COL_SCORE, _sc_it)

            # ── Tooltip ──
            _cw_tooltip_items = [
                (_enabled_cws[i].name, delta)
                for i, (matched, delta) in enumerate(_cw_matches)
                if matched
            ]
            tooltip = self._build_cat_tooltip(cat, result, scope_cats,
                                              cw_items=_cw_tooltip_items,
                                              cw_delta_total=_cw_delta_total)
            for col in range(self._score_table.columnCount()):
                item = self._score_table.item(row, col)
                if item:
                    item.setToolTip(tooltip)
            self._score_table.setRowHeight(row, 36 if self._display_mode == "both" else 22)

        self._finalize_recompute(alive, results, _children_in_scope, _restore_name)

    def _finalize_recompute(self, alive, results, children_in_scope_fn, restore_name):
        """Sort, filter, restore selection, and sync overlays after table population."""
        self._score_table.setSortingEnabled(True)
        shh = self._score_table.horizontalHeader()
        shh.blockSignals(True)
        self._score_table.sortItems(self._sort_col, self._sort_order)
        shh.blockSignals(False)

        # Apply row filters
        if self._filters_enabled and self._filters.is_any_active():
            _alive_by_name = {c.name: c for c in alive}
            _passes = {
                id(cat): cat_passes_filter(
                    cat, results[id(cat)], children_in_scope_fn(cat),
                    self._filters, TRAIT_LOW_THRESHOLD, TRAIT_HIGH_THRESHOLD,
                    self._room_display,
                )
                for cat in alive
            }
            for _r in range(self._score_table.rowCount()):
                _ni = self._score_table.item(_r, COL_NAME)
                if _ni:
                    _cat = _alive_by_name.get(_ni.text())
                    self._score_table.setRowHidden(_r, not (_cat and _passes.get(id(_cat), True)))
        else:
            for _r in range(self._score_table.rowCount()):
                self._score_table.setRowHidden(_r, False)

        if restore_name:
            for r in range(self._score_table.rowCount()):
                item = self._score_table.item(r, 0)
                if item and item.text() == restore_name:
                    self._score_table.blockSignals(True)
                    self._score_table.selectRow(r)
                    self._score_table.blockSignals(False)
                    break

        self._hate_overlay.update()
        self._refresh_children_panel()
        self._refresh_risk_panel()

    # ── Complex Weights ───────────────────────────────────────────────────────

    def _open_complex_weights(self):
        if self._cw_dialog is None or not self._cw_dialog.isVisible():
            self._cw_dialog = ComplexWeightsDialog(
                self,
                self._complex_weights,
                all_traits=self._all_abilities + self._all_mutations,
                stat_names=self._stat_names,
            )
            self._cw_dialog.cw_changed.connect(self._on_cw_changed)
            self._cw_dialog.show()
        else:
            self._cw_dialog.raise_()
            self._cw_dialog.activateWindow()

    def _on_cw_changed(self):
        self._save_ratings()
        self._rebuild_cw_columns()
        self.recompute()

    def _rebuild_cw_columns(self):
        """Sync table column count, headers, and delegates for enabled CWs."""
        enabled_cws = [cw for cw in self._complex_weights if cw.enabled]
        total_cols = COL_CW_SECTION_START + len(enabled_cws)

        shh = self._score_table.horizontalHeader()
        shh.blockSignals(True)
        self._score_table.setColumnCount(total_cols)
        _cw_delegate = _CWDelegate(self._score_table)
        for i, cw in enumerate(enabled_cws):
            cw_col = COL_CW_SECTION_START + i
            item = QTableWidgetItem(cw.name[:10])
            self._score_table.setHorizontalHeaderItem(cw_col, item)
            self._score_table.setColumnWidth(cw_col, 58)
            self._score_table.setItemDelegateForColumn(cw_col, _cw_delegate)
        shh.blockSignals(False)

    # ── Weights popup ─────────────────────────────────────────────────────────

    def _show_weights_popup(self):
        show_weights_popup(self, self._weights)

    def _open_stats_overview(self):
        """Open (or raise) the current-stats overview window."""
        dlg = getattr(self, '_stats_overview_dlg', None)
        if dlg is None or not dlg.isVisible():
            self._stats_overview_dlg = show_stats_overview(
                self, self._cats, self._stat_names,
                room_display=self._room_display,
            )
        else:
            self._stats_overview_dlg.refresh(self._cats)
            self._stats_overview_dlg.raise_()
