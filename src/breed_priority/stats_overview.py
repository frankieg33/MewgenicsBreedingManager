"""Breed Priority — Current Stats Overview popup.

Standalone QDialog showing per-cat effective (or base) stats with effects breakdown.
Opens as a non-blocking window from the Breed Priority top bar.

Only alive cats are shown (status != "Gone").
"""

import re

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QTableWidget, QTableWidgetItem,
    QHeaderView, QAbstractItemView, QPushButton, QCheckBox, QWidget,
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor

from save_parser import STAT_NAMES as _PARSER_STAT_NAMES

from .styles import (
    ACTION_BUTTON_SECONDARY_LARGE_STYLE, checkbox_style, PRIORITY_TABLE_STYLE,
)
from .theme import (
    CLR_BG_SCORE_AREA, CLR_TEXT_PRIMARY, CLR_TEXT_MUTED,
    CLR_BG_HEADER, CLR_VALUE_NEG, CLR_VALUE_POS, CLR_DESIRABLE, CLR_NEUTRAL,
    CLR_SURFACE_SEPARATOR,
)


_STAT_MAX_BASE = 7   # highest achievable base value in-game

# Stat value → foreground color
_STAT_COLOR = {
    7: CLR_DESIRABLE,
    6: CLR_NEUTRAL,
}


# Matches "+2 STR" or "-1 DEX" in mutation detail strings.
_MUT_STAT_RE = re.compile(r'([+-]?\d+)\s+(STR|CON|INT|DEX|SPD|LCK|CHA)')


def get_mutation_stat_bonuses(cat) -> dict:
    """Return {stat_name: total_delta} summed across all visual mutation entries.

    Parses the 'detail' field on each entry (e.g. "+2 STR, -1 DEX").
    Each unique mutation_id is counted only once — the game applies a mutation's
    stat bonus once regardless of how many body-part slots share the same ID
    (e.g. the same eyebrow mutation on left and right eyebrow counts once).
    Entries with no parseable stat effects contribute nothing.
    """
    bonuses: dict[str, int] = {}
    seen_ids: set[int] = set()
    for entry in getattr(cat, 'visual_mutation_entries', []) or []:
        mutation_id = entry.get('mutation_id')
        if mutation_id in seen_ids:
            continue
        seen_ids.add(mutation_id)
        detail = entry.get('detail', '') or ''
        for match in _MUT_STAT_RE.finditer(detail):
            delta = int(match.group(1))
            stat  = match.group(2)
            bonuses[stat] = bonuses.get(stat, 0) + delta
    return bonuses


def get_class_stat_bonuses(cat) -> dict[str, int]:
    """Return {stat_name: delta} from the cat's class stat modifiers, or empty dict."""
    return getattr(cat, 'class_stat_mods', None) or {}


def get_cat_stats(cat, use_current: bool, add_mutation_stats: bool = False) -> dict:
    """Return the stat dict to use for scoring/display.

    use_current=True  → total_stats (base + all modifiers/injuries + class bonuses)
    use_current=False → base_stats (genetic base values only)

    add_mutation_stats=True adds parsed mutation stat bonuses on top of
    whichever source is selected.  Falls back to base_stats if total_stats
    is unavailable.

    Class stat modifiers are always included when use_current=True, since the
    game applies them to all stats displayed on the house management screen.
    """
    if use_current:
        source = getattr(cat, 'total_stats', None) or getattr(cat, 'base_stats', {}) or {}
    else:
        source = getattr(cat, 'base_stats', {}) or {}

    # Collect all bonus sources that apply
    all_bonuses: dict[str, int] = {}

    if use_current:
        for stat, delta in get_class_stat_bonuses(cat).items():
            all_bonuses[stat] = all_bonuses.get(stat, 0) + delta

    if add_mutation_stats:
        for stat, delta in get_mutation_stat_bonuses(cat).items():
            all_bonuses[stat] = all_bonuses.get(stat, 0) + delta

    if not all_bonuses:
        return source
    result = dict(source)
    for stat, delta in all_bonuses.items():
        if stat in result:
            result[stat] = result[stat] + delta
    return result

_COL_NAME = 0
_COL_LOC  = 1
# Stat columns: 2 .. 2+len(stat_names)-1
# Sum = 2 + len(stat_names)
# Effects = 3 + len(stat_names)


def _stat_idx(sn: str) -> int:
    """Return save-parser list index for a stat name, or -1 if unknown."""
    try:
        return _PARSER_STAT_NAMES.index(sn)
    except ValueError:
        return -1


def _effects_for(cat, stat_names: list) -> list:
    """Return [(stat_key, total_delta, mod_part, sec_part, class_part), ...] for all non-zero deltas.

    total_delta = total_stats + class_mods - base_stats (positive = buff, negative = debuff/injury).
    mod_part / sec_part / class_part are the underlying components when available.
    """
    base  = getattr(cat, 'base_stats',  None)
    total = getattr(cat, 'total_stats', None)
    if base is None or total is None:
        return []

    stat_mod = getattr(cat, 'stat_mod', None) or []
    stat_sec = getattr(cat, 'stat_sec', None) or []
    class_mods = get_class_stat_bonuses(cat)

    result = []
    for sn in stat_names:
        b = base.get(sn, 0)
        t = total.get(sn, b)
        cls_mod = class_mods.get(sn, 0)
        delta = (t - b) + cls_mod
        if delta == 0:
            continue
        idx = _stat_idx(sn)
        mod = stat_mod[idx] if (idx >= 0 and idx < len(stat_mod)) else 0
        sec = stat_sec[idx] if (idx >= 0 and idx < len(stat_sec)) else 0
        result.append((sn, delta, mod, sec, cls_mod))
    return result


def _stat_cell_tooltip(sn: str, base_val: int, total_val: int, cat) -> str:
    """Build a per-stat tooltip showing the base + mod + sec + class breakdown."""
    stat_mod = getattr(cat, 'stat_mod', None) or []
    stat_sec = getattr(cat, 'stat_sec', None) or []
    class_mods = get_class_stat_bonuses(cat)
    idx = _stat_idx(sn)
    mod = stat_mod[idx] if (idx >= 0 and idx < len(stat_mod)) else 0
    sec = stat_sec[idx] if (idx >= 0 and idx < len(stat_sec)) else 0
    cls = class_mods.get(sn, 0)

    if mod == 0 and sec == 0 and cls == 0:
        return f"{sn}: {total_val} (base)"

    parts = [f"base {base_val}"]
    if mod != 0:
        parts.append(f"mod {mod:+d}")
    if sec != 0:
        parts.append(f"sec {sec:+d}")
    if cls != 0:
        cat_class = getattr(cat, 'cat_class', '')
        cls_label = f"{cat_class} class" if cat_class else "class"
        parts.append(f"{cls_label} {cls:+d}")
    return f"{sn}: {total_val}  ({', '.join(parts)})"


class StatsOverviewDialog(QDialog):
    """Non-blocking popup: alive cats × current stats with effects breakdown."""

    def __init__(self, cats: list, stat_names: list | None = None,
                 room_display: dict | None = None, parent=None):
        super().__init__(parent)
        self._all_cats   = cats
        self._stat_names = stat_names or list(_PARSER_STAT_NAMES)
        self._room_disp  = room_display or {}
        self._include_injuries = True

        n = len(self._stat_names)
        self._col_sum    = 2 + n
        self._col_fx     = 3 + n
        self._num_cols   = 4 + n   # Name, Loc, n stats, Sum, Effects

        self.setWindowTitle("Current Stats Overview")
        self.setWindowFlags(self.windowFlags() | Qt.Window)
        self.setStyleSheet(f"background:{CLR_BG_SCORE_AREA}; color:{CLR_TEXT_PRIMARY};")
        self.resize(960, 580)

        vb = QVBoxLayout(self)
        vb.setContentsMargins(12, 12, 12, 12)
        vb.setSpacing(8)

        # ── Header bar ──────────────────────────────────────────────────────────
        hdr = QWidget()
        hdr.setStyleSheet(
            f"background:{CLR_BG_HEADER}; border-radius:4px;"
            f" border-bottom:1px solid {CLR_SURFACE_SEPARATOR};"
        )
        hdr_l = QHBoxLayout(hdr)
        hdr_l.setContentsMargins(10, 6, 10, 6)
        hdr_l.setSpacing(10)

        title = QLabel("Current Stats Overview")
        title.setStyleSheet(
            f"color:{CLR_TEXT_PRIMARY}; font-size:14px; font-weight:bold;"
        )
        hdr_l.addWidget(title)
        hdr_l.addStretch()

        self._chk_injuries = QCheckBox("Include injuries / effects")
        self._chk_injuries.setChecked(True)
        self._chk_injuries.setToolTip(
            "Checked: stats show effective values (base + all modifiers).\n"
            "Unchecked: stats show base values only — modifiers excluded from sum."
        )
        self._chk_injuries.setStyleSheet(
            checkbox_style(font_size=11, emphasize_checked=True)
        )
        self._chk_injuries.stateChanged.connect(self._on_toggle)
        hdr_l.addWidget(self._chk_injuries)

        vb.addWidget(hdr)

        # ── Table ───────────────────────────────────────────────────────────────
        headers = (
            ["Name", "Loc"]
            + list(self._stat_names)
            + ["Sum", "Effects"]
        )
        self._table = QTableWidget()
        self._table.setColumnCount(self._num_cols)
        self._table.setHorizontalHeaderLabels(headers)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SingleSelection)
        self._table.setAlternatingRowColors(True)
        self._table.setSortingEnabled(False)   # enabled only after populate
        self._table.setStyleSheet(PRIORITY_TABLE_STYLE)
        self._table.verticalHeader().setVisible(False)

        hh = self._table.horizontalHeader()
        hh.setSectionResizeMode(_COL_NAME, QHeaderView.Stretch)
        hh.setSectionResizeMode(_COL_LOC,  QHeaderView.Fixed)
        self._table.setColumnWidth(_COL_LOC, 68)
        for c in range(2, 2 + len(self._stat_names)):
            hh.setSectionResizeMode(c, QHeaderView.Fixed)
            self._table.setColumnWidth(c, 38)
        hh.setSectionResizeMode(self._col_sum, QHeaderView.Fixed)
        self._table.setColumnWidth(self._col_sum, 44)
        hh.setSectionResizeMode(self._col_fx, QHeaderView.Interactive)
        self._table.setColumnWidth(self._col_fx, 220)

        vb.addWidget(self._table)

        # ── Footer ──────────────────────────────────────────────────────────────
        self._note = QLabel("")
        self._note.setStyleSheet(f"color:{CLR_TEXT_MUTED}; font-size:10px;")
        vb.addWidget(self._note)

        close_btn = QPushButton("Close")
        close_btn.setStyleSheet(ACTION_BUTTON_SECONDARY_LARGE_STYLE)
        close_btn.clicked.connect(self.accept)
        vb.addWidget(close_btn, alignment=Qt.AlignRight)

        self._populate()

    # ── Internal ────────────────────────────────────────────────────────────────

    def _on_toggle(self):
        self._include_injuries = self._chk_injuries.isChecked()
        self._populate()

    def _populate(self):
        # Only alive cats (status != "Gone")
        cats = [c for c in self._all_cats if getattr(c, 'status', 'Gone') != 'Gone']

        self.setUpdatesEnabled(False)
        try:
            self._table.setSortingEnabled(False)
            self._table.setRowCount(0)
            self._table.setRowCount(len(cats))

            fx_count = 0   # cats with any non-zero effects

            for row, cat in enumerate(cats):
                base  = getattr(cat, 'base_stats',  {}) or {}
                stats = get_cat_stats(cat, self._include_injuries)
                effects = _effects_for(cat, self._stat_names)
                if effects:
                    fx_count += 1

                # ── Name ────────────────────────────────────────────────────
                name_item = QTableWidgetItem(getattr(cat, 'name', '?'))
                name_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                self._table.setItem(row, _COL_NAME, name_item)

                # ── Location ─────────────────────────────────────────────────
                raw_room  = getattr(cat, 'room', '') or ''
                cat_status = getattr(cat, 'status', '')
                if cat_status == 'Adventure':
                    loc_text = 'Adv.'
                else:
                    loc_text = self._room_disp.get(raw_room, raw_room or '—')
                loc_item = QTableWidgetItem(loc_text)
                loc_item.setTextAlignment(Qt.AlignCenter)
                loc_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                self._table.setItem(row, _COL_LOC, loc_item)

                # ── Stat columns ─────────────────────────────────────────────
                cat_sum = 0
                for ci, sn in enumerate(self._stat_names):
                    val = stats.get(sn, 0)
                    cat_sum += val

                    item = QTableWidgetItem()
                    item.setData(Qt.DisplayRole, val)
                    item.setTextAlignment(Qt.AlignCenter)
                    item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)

                    # Tooltip: show base / mod / sec breakdown
                    b_val = base.get(sn, 0)
                    item.setToolTip(_stat_cell_tooltip(sn, b_val, val, cat))

                    # Foreground: teal above base-max, green at 7, yellow at 6, muted below 5
                    if val > _STAT_MAX_BASE:
                        item.setForeground(QColor(CLR_VALUE_POS))
                    else:
                        fg = _STAT_COLOR.get(val)
                        if fg:
                            item.setForeground(QColor(fg))
                        elif val < 5:
                            item.setForeground(QColor(CLR_TEXT_MUTED))

                    # Dark-red background when an effect is depressing this stat
                    if self._include_injuries and val < b_val:
                        item.setBackground(QColor("#2a0505"))

                    self._table.setItem(row, 2 + ci, item)

                # ── Sum ─────────────────────────────────────────────────────
                sum_item = QTableWidgetItem()
                sum_item.setData(Qt.DisplayRole, cat_sum)
                sum_item.setTextAlignment(Qt.AlignCenter)
                sum_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                self._table.setItem(row, self._col_sum, sum_item)

                # ── Effects column ───────────────────────────────────────────
                # Shows all non-zero stat deltas (buffs green, debuffs red).
                # Always reflects reality regardless of the include/exclude toggle.
                if effects:
                    parts = []
                    for sn, delta, *_ in effects:
                        parts.append(f"{sn} {delta:+d}")
                    fx_text = ",  ".join(parts)
                    fx_item = QTableWidgetItem(fx_text)
                    # Color by majority direction
                    has_neg = any(d < 0 for _, d, *_ in effects)
                    has_pos = any(d > 0 for _, d, *_ in effects)
                    if has_neg and not has_pos:
                        fx_item.setForeground(QColor(CLR_VALUE_NEG))
                    elif has_pos and not has_neg:
                        fx_item.setForeground(QColor(CLR_VALUE_POS))
                    # Mixed: leave default color
                    # Tooltip shows full mod/sec/class breakdown
                    tip_lines = []
                    for sn, delta, *_ in effects:
                        b_val = base.get(sn, 0)
                        tip_lines.append(_stat_cell_tooltip(sn, b_val, b_val + delta, cat))
                    fx_item.setToolTip("\n".join(tip_lines))
                else:
                    fx_item = QTableWidgetItem("—")
                    fx_item.setForeground(QColor(CLR_TEXT_MUTED))
                fx_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                self._table.setItem(row, self._col_fx, fx_item)

            self._table.setSortingEnabled(True)
            self._table.sortByColumn(self._col_sum, Qt.DescendingOrder)

            mode = "effective" if self._include_injuries else "base (modifiers excluded from sum)"
            self._note.setText(
                f"{len(cats)} alive cats  ·  {fx_count} with stat effects  ·  showing {mode} stats"
            )
        finally:
            self.setUpdatesEnabled(True)

    def refresh(self, cats: list):
        """Update cat list and repopulate (call when save reloads)."""
        self._all_cats = cats
        self._populate()


def show_stats_overview(parent, cats: list, stat_names: list | None = None,
                        room_display: dict | None = None) -> StatsOverviewDialog:
    """Open the current-stats overview as a non-blocking window.

    Returns the dialog so the caller can hold a reference and call refresh()
    when new save data arrives.
    """
    dlg = StatsOverviewDialog(cats, stat_names, room_display=room_display, parent=parent)
    dlg.show()
    dlg.raise_()
    return dlg
