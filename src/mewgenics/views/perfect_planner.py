"""Perfect Cat Planner views extracted from mewgenics_manager.py."""
from __future__ import annotations

import html
from typing import TYPE_CHECKING, Optional, Sequence

from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDoubleSpinBox, QFrame,
    QGridLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMenu,
    QPushButton, QScrollArea, QSizePolicy, QSpinBox, QSplitter,
    QProgressBar, QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget,
)
from PySide6.QtCore import Qt, QByteArray, QSize, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QIcon

from save_parser import (
    Cat, STAT_NAMES, kinship_coi, get_parents,
    shared_ancestor_counts,
)
from breeding import (
    pair_projection, is_mutual_lover_pair,
    planner_inbreeding_penalty, planner_pair_allows_breeding,
    planner_pair_bias, score_pair as score_pair_factors,
    tracked_offspring, pair_breeding_compatibility,
)
from room_optimizer import (
    best_breeding_room_stimulation, build_room_configs,
)
from room_optimizer.parallel import run_parallel_p7p_sa

from mewgenics.constants import (
    STAT_COLORS, PAIR_COLORS,
    _room_key_from_display,
)
from mewgenics.utils.localization import ROOM_DISPLAY, _tr
from mewgenics.utils.paths import _planner_state_path
from mewgenics.utils.config import (
    _saved_optimizer_flag, _set_optimizer_flag,
)
from mewgenics.utils.tags import (
    _make_tag_icon, _cat_tags,
)
from mewgenics.utils.planner_state import (
    _load_planner_state_value, _save_planner_state_value,
    _active_cat_fingerprint,
    _default_perfect_planner_foundation_pairs,
    _load_perfect_planner_foundation_pairs, _save_perfect_planner_foundation_pairs,
    _load_perfect_planner_selected_offspring, _save_perfect_planner_selected_offspring,
    _planner_import_traits_summary, _planner_import_traits_tooltip,
)
from mewgenics.utils.optimizer_settings import (
    _saved_optimizer_search_temperature, _saved_optimizer_search_neighbors,
)
from mewgenics.utils.calibration import (
    _trait_label_from_value, _trait_level_color,
)
from mewgenics.utils.cat_analysis import (
    _cat_uid, _cat_base_sum, _pair_breakpoint_analysis,
)
from mewgenics.utils.abilities import (
    _cat_has_trait, _planner_trait_display_name,
)
from mewgenics.utils.styling import (
    _enforce_min_font_in_widget_tree, _blend_qcolor,
)
from mewgenics.utils.cat_persistence import _load_blacklist

if TYPE_CHECKING:
    from mewgenics.models.breeding_cache import BreedingCache


# ---------------------------------------------------------------------------
# Helpers also used by these views (defined in mewgenics_manager.py).
# Duplicated here to avoid circular imports.
# ---------------------------------------------------------------------------

def _planner_trait_color(ratio: float) -> QColor:
    """Return a tint color for mutation-planner trait coverage."""
    ratio = max(-1.0, min(1.0, float(ratio)))
    neutral = QColor(29, 29, 44)
    positive_low = QColor(214, 163, 69)
    positive_high = QColor(82, 185, 146)
    negative = QColor(177, 84, 94)
    if ratio > 0:
        warm = _blend_qcolor(positive_low, positive_high, min(ratio, 1.0))
        return _blend_qcolor(neutral, warm, 0.28 + 0.58 * min(ratio, 1.0))
    if ratio < 0:
        return _blend_qcolor(neutral, negative, 0.36 + 0.54 * min(abs(ratio), 1.0))
    return neutral


def _planner_trait_tooltip(summary: dict, *, label: str = "Mutation planner") -> str:
    if not summary:
        return ""

    score = float(summary.get("score", 0.0))
    matches = list(summary.get("matches", []) or [])
    penalties = list(summary.get("penalties", []) or [])
    parts = [f"{label}: {score:+.1f}"]
    if matches:
        parts.append("Matches: " + ", ".join(matches[:4]) + ("..." if len(matches) > 4 else ""))
    if penalties:
        parts.append("Penalties: " + ", ".join(penalties[:4]) + ("..." if len(penalties) > 4 else ""))
    return "\n".join(parts)


def _planner_trait_summary_for_cat(cat: Cat, traits: Sequence[dict]) -> dict:
    positive_score = 0.0
    negative_score = 0.0
    max_score = 0.0
    matches: list[str] = []
    penalties: list[str] = []

    for trait in traits:
        category = str(trait.get("category", "")).strip()
        key = str(trait.get("key", "")).strip().lower()
        if not category or not key:
            continue

        weight = float(trait.get("weight", 0) or 0)
        if weight == 0:
            continue

        max_score += abs(weight)
        if not _cat_has_trait(cat, category, key):
            continue

        display = _planner_trait_display_name(str(trait.get("display") or key))
        if weight > 0:
            matches.append(display)
            positive_score += weight
        else:
            penalties.append(display)
            negative_score += abs(weight)

    net_score = positive_score - negative_score
    ratio = net_score / max(1.0, max_score)
    return {
        "score": net_score,
        "ratio": ratio,
        "positive": positive_score,
        "negative": negative_score,
        "matches": matches,
        "penalties": penalties,
        "max": max_score,
    }


def _planner_trait_summary_for_pair(cat_a: Cat, cat_b: Cat, traits: Sequence[dict]) -> dict:
    score = 0.0
    max_score = 0.0
    matches: list[str] = []
    penalties: list[str] = []

    for trait in traits:
        category = str(trait.get("category", "")).strip()
        key = str(trait.get("key", "")).strip().lower()
        if not category or not key:
            continue

        weight = float(trait.get("weight", 0) or 0)
        if weight == 0:
            continue

        scale = weight / 10.0
        max_score += abs(scale) * 7.5

        a_has = _cat_has_trait(cat_a, category, key)
        b_has = _cat_has_trait(cat_b, category, key)
        if not (a_has or b_has):
            continue

        display = _planner_trait_display_name(str(trait.get("display") or key))
        if weight > 0:
            matches.append(display)
        else:
            penalties.append(display)

        score += scale * 5.0
        if a_has and b_has:
            score += scale * 2.5

    ratio = score / max(1.0, max_score)
    return {
        "score": score,
        "ratio": ratio,
        "matches": matches,
        "penalties": penalties,
        "max": max_score,
    }


# ── Classes ────────────────────────────────────────────────────────────────


class PerfectPlannerDetailPanel(QWidget):
    def __init__(self):
        super().__init__()
        self.setStyleSheet("background:#0a0a18; border-top:1px solid #1e1e38;")
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 6, 10, 8)
        root.setSpacing(6)

        self._summary = QLabel(_tr("perfect_planner.detail.summary.select_stage"))
        self._summary.setStyleSheet("color:#aaa; font-size:11px;")
        self._summary.setWordWrap(True)
        root.addWidget(self._summary)

        self._context = QLabel("")
        self._context.setStyleSheet("color:#7d8bb0; font-size:10px; font-style:italic;")
        self._context.setWordWrap(True)
        self._context.hide()
        root.addWidget(self._context)

        self._actions_table = QTableWidget(0, 3)
        self._actions_table.setHorizontalHeaderLabels([
            _tr("perfect_planner.detail.table.target", default="Target"),
            _tr("perfect_planner.table.coverage", default="7s"),
            _tr("perfect_planner.table.risk", default="Risk%"),
        ])
        self._actions_table.verticalHeader().setVisible(False)
        self._actions_table.setSelectionMode(QAbstractItemView.NoSelection)
        self._actions_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._actions_table.setFocusPolicy(Qt.NoFocus)
        self._actions_table.setWordWrap(True)
        self._actions_table.setAlternatingRowColors(True)
        self._actions_table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        hh = self._actions_table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Interactive)
        hh.setSectionResizeMode(1, QHeaderView.Interactive)
        hh.setSectionResizeMode(2, QHeaderView.Interactive)
        self._actions_table.setColumnWidth(0, 450)
        self._actions_table.setColumnWidth(1, 52)
        self._actions_table.setColumnWidth(2, 52)
        self._actions_table.verticalHeader().setDefaultSectionSize(24)
        self._actions_table.setStyleSheet("""
            QTableWidget {
                background:#0d0d1c; alternate-background-color:#131326;
                color:#ddd; border:1px solid #26264a; font-size:10px;
            }
            QTableWidget::item { padding:2px 4px; }
            QHeaderView::section {
                background:#16213e; color:#888; padding:5px 4px;
                border:none; border-bottom:1px solid #1e1e38;
                border-right:1px solid #16213e; font-size:10px; font-weight:bold;
            }
        """)
        root.addWidget(self._actions_table, 1)

        self._excluded_table = QTableWidget(0, 12)
        self._excluded_table.setHorizontalHeaderLabels([
            _tr("perfect_planner.detail.excluded.cat"), "STR", "DEX", "CON", "INT", "SPD", "CHA", "LCK",
            _tr("perfect_planner.detail.excluded.sum"),
            _tr("perfect_planner.detail.excluded.agg"),
            _tr("perfect_planner.detail.excluded.lib"),
            _tr("perfect_planner.detail.excluded.inbred"),
        ])
        self._excluded_table.verticalHeader().setVisible(False)
        self._excluded_table.setSelectionMode(QAbstractItemView.NoSelection)
        self._excluded_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._excluded_table.setFocusPolicy(Qt.NoFocus)
        self._excluded_table.setAlternatingRowColors(True)
        self._excluded_table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
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
        self._excluded_table.verticalHeader().setDefaultSectionSize(22)
        self._excluded_table.setStyleSheet("""
            QTableWidget {
                background:#0d0d1c; alternate-background-color:#131326;
                color:#ddd; border:1px solid #26264a; font-size:10px;
            }
            QTableWidget::item { padding:2px 3px; }
            QHeaderView::section {
                background:#16213e; color:#888; padding:5px 4px;
                border:none; border-bottom:1px solid #1e1e38;
                border-right:1px solid #16213e; font-size:10px; font-weight:bold;
            }
        """)
        root.addWidget(self._excluded_table, 1)

    def retranslate_ui(self):
        self._actions_table.setHorizontalHeaderLabels([
            _tr("perfect_planner.detail.table.target", default="Target"),
            _tr("perfect_planner.table.coverage", default="7s"),
            _tr("perfect_planner.table.risk", default="Risk%"),
        ])
        self._excluded_table.setHorizontalHeaderLabels([
            _tr("perfect_planner.detail.excluded.cat"), "STR", "DEX", "CON", "INT", "SPD", "CHA", "LCK",
            _tr("perfect_planner.detail.excluded.sum"),
            _tr("perfect_planner.detail.excluded.agg"),
            _tr("perfect_planner.detail.excluded.lib"),
            _tr("perfect_planner.detail.excluded.inbred"),
        ])

    @staticmethod
    def _build_target_grid(action: dict) -> QWidget:
        container = QWidget()
        grid = QGridLayout(container)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(2)
        grid.setVerticalSpacing(1)

        target_grid = action.get("target_grid") or {}
        parents = target_grid.get("parents", [])
        offspring = target_grid.get("offspring", {})
        mutation_summary = action.get("mutation_summary") or {}
        parent_summaries = []
        if isinstance(mutation_summary, dict):
            parent_summaries = list(mutation_summary.get("parents", []) or [])
        pair_summary = mutation_summary.get("pair") if isinstance(mutation_summary, dict) else None

        def _style_trait_label(lbl: QLabel, summary: Optional[dict], *, alpha: int, label: str, base_style: str):
            if not summary:
                lbl.setStyleSheet(base_style)
                return
            ratio = float(summary.get("ratio", 0.0))
            if abs(ratio) <= 1e-6:
                lbl.setStyleSheet(base_style)
                return
            color = _planner_trait_color(ratio)
            color.setAlpha(alpha)
            border = QColor(color).lighter(135)
            border.setAlpha(min(255, alpha + 50))
            lbl.setStyleSheet(
                base_style
                + f"background-color: rgba({color.red()},{color.green()},{color.blue()},{color.alpha()});"
                + f" border:1px solid rgba({border.red()},{border.green()},{border.blue()},{border.alpha()});"
                + " border-radius:3px; padding:1px 4px; color:#fff;"
            )
            tooltip = _planner_trait_tooltip(summary, label=label)
            if tooltip:
                lbl.setToolTip(tooltip)

        name_col_width = 76
        for row_idx, header in enumerate(["", *STAT_NAMES, "Sum"]):
            if row_idx == 0:
                continue
            hdr = QLabel(header)
            hdr.setAlignment(Qt.AlignCenter)
            hdr.setStyleSheet("color:#6f7fa0; font-size:8px; font-weight:bold;")
            grid.addWidget(hdr, 0, row_idx)

        def _parent_row(row: int, parent: dict):
            name = QLabel(parent.get("name", ""))
            name.setWordWrap(True)
            name.setMinimumWidth(name_col_width)
            _style_trait_label(
                name,
                parent_summaries[row - 1] if row - 1 < len(parent_summaries) else None,
                alpha=150,
                label=parent.get("name", "Parent"),
                base_style="color:#ddd; font-size:9px; font-weight:bold;",
            )
            if not name.toolTip():
                name.setToolTip(parent.get("name", ""))
            grid.addWidget(name, row, 0)
            for col, stat in enumerate(STAT_NAMES, 1):
                value = int(parent.get("stats", {}).get(stat, 0))
                c = STAT_COLORS.get(value, QColor(100, 100, 115))
                lbl = QLabel(str(value))
                lbl.setAlignment(Qt.AlignCenter)
                lbl.setStyleSheet(
                    f"background:rgb({c.red()},{c.green()},{c.blue()});"
                    "color:#fff; font-size:9px; font-weight:bold;"
                    "border-radius:2px; padding:1px 4px;"
                )
                grid.addWidget(lbl, row, col)
            sum_lbl = QLabel(str(int(parent.get("sum", 0))))
            sum_lbl.setAlignment(Qt.AlignCenter)
            sum_lbl.setStyleSheet("color:#9aa6ba; font-size:9px; font-weight:bold;")
            grid.addWidget(sum_lbl, row, len(STAT_NAMES) + 1)

        def _offspring_row(row: int, info: dict):
            name = QLabel(_tr("perfect_planner.detail.offspring"))
            _style_trait_label(
                name,
                pair_summary,
                alpha=120,
                label=_tr("perfect_planner.detail.offspring"),
                base_style="color:#777; font-size:8px; font-style:italic;",
            )
            if not name.toolTip():
                name.setToolTip(_tr("perfect_planner.detail.offspring"))
            grid.addWidget(name, row, 0)
            sum_lo, sum_hi = info.get("sum_range", (0, 0))
            for col, stat in enumerate(STAT_NAMES, 1):
                stat_info = info.get("stats", {}).get(stat, {})
                lo = int(stat_info.get("lo", 0))
                hi = int(stat_info.get("hi", 0))
                expected = float(stat_info.get("expected", hi))
                hi_color = STAT_COLORS.get(hi, QColor(100, 100, 115))
                if lo == hi:
                    text = f"{lo}"
                else:
                    text = f"{lo}-{hi}\n{expected:.1f}"
                lbl = QLabel(text)
                lbl.setAlignment(Qt.AlignCenter)
                lbl.setToolTip(_tr("perfect_planner.detail.tooltip.stat", stat=stat, lo=lo, hi=hi, expected=f"{expected:.1f}"))
                lbl.setStyleSheet(
                    f"background:rgba({hi_color.red()},{hi_color.green()},{hi_color.blue()},110);"
                    f"color:rgb({hi_color.red()},{hi_color.green()},{hi_color.blue()});"
                    "font-size:8px; font-weight:bold; border-radius:2px; padding:1px 3px;"
                )
                grid.addWidget(lbl, row, col)
            if sum_lo == sum_hi:
                sum_text = str(sum_lo)
            else:
                sum_text = f"{sum_lo}-{sum_hi}"
            sum_lbl = QLabel(sum_text)
            sum_lbl.setAlignment(Qt.AlignCenter)
            sum_lbl.setStyleSheet("color:#777; font-size:9px; font-weight:bold;")
            grid.addWidget(sum_lbl, row, len(STAT_NAMES) + 1)

        if len(parents) >= 1:
            _parent_row(1, parents[0])
        if len(parents) >= 2:
            _parent_row(2, parents[1])
        _offspring_row(3, offspring)
        container.setFixedHeight(84)
        return container

    def show_stage(self, data: Optional[dict], context_note: Optional[str] = None):
        if not data:
            self._summary.setText(_tr("perfect_planner.detail.summary.select_stage"))
            self._summary.setToolTip("")
            self._context.setText("")
            self._context.hide()
            self._actions_table.setRowCount(0)
            self._actions_table.show()
            self._excluded_table.hide()
            return

        if data.get("stage") == _tr("perfect_planner.stage.excluded"):
            rows = data.get("excluded_cat_rows", [])
            self._summary.setText(_tr("perfect_planner.detail.summary.excluded", count=len(rows)))
            self._summary.setToolTip(_tr("perfect_planner.detail.summary.excluded_tooltip"))
            self._context.setText(context_note or "")
            self._context.setVisible(bool(context_note))
            self._actions_table.hide()
            self._excluded_table.show()
            self._excluded_table.setRowCount(len(rows))
            for row_idx, cat_row in enumerate(rows):
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

        self._actions_table.show()
        self._excluded_table.hide()

        stage_label = data.get("stage", "")
        self._summary.setText(stage_label)
        self._summary.setToolTip("")
        self._context.setText(context_note or "")
        self._context.setVisible(bool(context_note))

        actions = data.get("actions", [])
        self._actions_table.setRowCount(len(actions))
        for row, action in enumerate(actions):
            coverage_value = action.get("coverage_value")
            if coverage_value is None:
                coverage_value = 0.0
            coverage_item = QTableWidgetItem(f"{float(coverage_value):.1f}/7")
            coverage_item.setTextAlignment(Qt.AlignCenter)
            if float(coverage_value) >= 6.0:
                coverage_item.setForeground(QBrush(QColor(98, 194, 135)))
            elif float(coverage_value) >= 4.5:
                coverage_item.setForeground(QBrush(QColor(216, 181, 106)))
            else:
                coverage_item.setForeground(QBrush(QColor(190, 145, 40)))

            risk_value = action.get("risk")
            risk_item = QTableWidgetItem("—" if risk_value is None else f"{float(risk_value):.0f}%")
            risk_item.setTextAlignment(Qt.AlignCenter)
            if risk_value is not None:
                risk = float(risk_value)
                if risk >= 50:
                    risk_item.setForeground(QBrush(QColor(217, 119, 119)))
                elif risk >= 20:
                    risk_item.setForeground(QBrush(QColor(216, 181, 106)))
                else:
                    risk_item.setForeground(QBrush(QColor(98, 194, 135)))

            if action.get("target_grid"):
                self._actions_table.setCellWidget(row, 0, self._build_target_grid(action))
            else:
                target_item = QTableWidgetItem(action.get("target", ""))
                self._actions_table.setItem(row, 0, target_item)
            self._actions_table.setItem(row, 1, coverage_item)
            self._actions_table.setItem(row, 2, risk_item)

        self._actions_table.resizeRowsToContents()


class PerfectPlannerGuidePanel(QWidget):
    """Read-only guide for how the Perfect 7 planner is meant to be used."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(
            "QWidget { background:#0a0a18; }"
            "QLabel { color:#bbb; }"
            "QTextBrowser { background:#0d0d1c; color:#ddd; border:1px solid #26264a; "
            "border-radius:6px; padding:10px; font-size:12px; }"
        )
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        self._title = QLabel(_tr("perfect_planner.guide.title", default="Planner Guide"))
        self._title.setStyleSheet("color:#ddd; font-size:18px; font-weight:bold;")
        root.addWidget(self._title)

        self._subtitle = QLabel(_tr(
            "perfect_planner.guide.subtitle",
            default="A built-in README for the perfect-line workflow.",
        ))
        self._subtitle.setStyleSheet("color:#8d8da8; font-size:11px;")
        self._subtitle.setWordWrap(True)
        root.addWidget(self._subtitle)

        from PySide6.QtWidgets import QTextBrowser
        self._browser = QTextBrowser()
        self._browser.setOpenExternalLinks(False)
        self._browser.setFocusPolicy(Qt.NoFocus)
        self._browser.setFrameShape(QFrame.NoFrame)
        self._browser.setStyleSheet(
            "QTextBrowser { background:#0d0d1c; color:#ddd; border:1px solid #26264a; "
            "border-radius:6px; padding:10px; }"
            "QTextBrowser h2 { color:#f0f0ff; margin-top: 6px; margin-bottom: 6px; }"
            "QTextBrowser h3 { color:#c9d6ff; margin-top: 12px; margin-bottom: 4px; }"
            "QTextBrowser ul, QTextBrowser ol { margin-left: 18px; }"
            "QTextBrowser li { margin-bottom: 4px; }"
            "QTextBrowser p { margin-top: 4px; margin-bottom: 8px; }"
            "QTextBrowser .muted { color:#8d8da8; }"
        )
        root.addWidget(self._browser, 1)

        self.retranslate_ui()
        _enforce_min_font_in_widget_tree(self)

    def retranslate_ui(self):
        self._title.setText(_tr("perfect_planner.guide.title", default="Planner Guide"))
        self._subtitle.setText(_tr(
            "perfect_planner.guide.subtitle",
            default="A built-in README for the perfect-line workflow.",
        ))
        self._browser.setHtml(self._build_html())

    @staticmethod
    def _esc(text: str) -> str:
        return html.escape(text or "")

    def _build_html(self) -> str:
        stage1_details = self._esc(_tr("perfect_planner.stage1.details"))
        stage1_note1 = self._esc(_tr("perfect_planner.stage1.note1"))
        stage1_note2 = self._esc(_tr("perfect_planner.stage1.note2"))
        stage2_details = self._esc(_tr("perfect_planner.stage2.details"))
        stage3_details = self._esc(_tr("perfect_planner.stage3.details"))
        stage4_details = self._esc(_tr("perfect_planner.stage4.details"))
        description = self._esc(_tr("perfect_planner.description"))
        guide_note = self._esc(
            "Foundation pair edits and offspring selections refresh the plan automatically."
        )

        return f"""
        <html>
          <body style="font-family:Segoe UI, Arial, sans-serif; line-height:1.45;">
            <h2>{self._esc(_tr("perfect_planner.guide.title", default="Planner Guide"))}</h2>
            <p>{description}</p>

            <h3>Where to look</h3>
            <ul>
              <li><strong>Stage Details</strong> uses the wider layout now: parent pair, projected stat spread, coverage, and risk only.</li>
              <li><strong>Planner Guide</strong> holds the longer explanations that used to repeat in the lower-left pane.</li>
              <li><strong>Foundation Pairs</strong> is the one-time setup area for the starting lines you want to use.</li>
              <li><strong>Offspring Tracker</strong> is where you pick a keeper child for each pair and keep that choice over time.</li>
              <li><strong>Cat Locator</strong> keeps the room-moving side of the plan visible, including offspring.</li>
            </ul>

            <h3>How to use it</h3>
            <ol>
              <li>Pick your starting pairs in the Foundation Pairs tab.</li>
              <li>Set how many starting pairs you want with <strong>Start pairs</strong> and click <strong>Build Perfect 7 Plan</strong>.</li>
              <li>Use the stage table above to jump between the four planning stages.</li>
              <li>Read the focused stage notes on the left when you need the active action list without all the duplicate text.</li>
              <li>Use the Offspring Tracker to pick one keeper offspring per pair; the choice is saved and the plan refreshes.</li>
              <li>Use the Cat Locator to see where parents, offspring, and rotation candidates should live.</li>
            </ol>

            <h3>Stage map</h3>
            <ul>
              <li><strong>{self._esc(_tr("perfect_planner.stage1.title"))}</strong>: {stage1_details}</li>
              <li><strong>{self._esc(_tr("perfect_planner.stage2.title"))}</strong>: {stage2_details}</li>
              <li><strong>{self._esc(_tr("perfect_planner.stage3.title"))}</strong>: {stage3_details}</li>
              <li><strong>{self._esc(_tr("perfect_planner.stage4.title"))}</strong>: {stage4_details}</li>
            </ul>

            <h3>Working rules</h3>
            <ul>
              <li>{stage1_note1}</li>
              <li>{stage1_note2}</li>
              <li>{self._esc(_tr("perfect_planner.stage2.note1"))}</li>
              <li>{self._esc(_tr("perfect_planner.stage3.note1"))}</li>
              <li>{self._esc(_tr("perfect_planner.stage4.note1"))}</li>
              <li><span style="color:#8d8da8;">{guide_note}</span></li>
            </ul>
          </body>
        </html>
        """


class PerfectPlannerOffspringTracker(QWidget):
    """Track the actual and projected offspring for Perfect 7 planner pairs."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(
            "QWidget { background:#0a0a18; }"
            "QLabel { color:#bbb; }"
            "QTableWidget { background:#101023; color:#ddd; border:1px solid #26264a; }"
            "QHeaderView::section { background:#151532; color:#7d8bb0; border:none; padding:4px; font-weight:bold; }"
        )
        self._rows: list[dict] = []
        self._render_rows: list[dict] = []
        self._selected_offspring_by_pair: dict[tuple[int, int], int] = {}
        self._save_path: Optional[str] = None
        self._selected_child_uid_by_pair_key: dict[str, str] = _load_perfect_planner_selected_offspring(self._save_path)
        self._navigate_to_cat_callback = None
        self._select_offspring_callback = None

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        header = QHBoxLayout()
        self._title = QLabel(_tr("perfect_planner.offspring_tracker.title", default="Offspring Tracker"))
        self._title.setStyleSheet("color:#ddd; font-size:18px; font-weight:bold;")
        self._summary = QLabel(_tr(
            "perfect_planner.offspring_tracker.summary_empty",
            default="Build a plan to track offspring outcomes.",
        ))
        self._summary.setStyleSheet("color:#666; font-size:11px;")
        header.addWidget(self._title)
        header.addStretch()
        header.addWidget(self._summary)
        root.addLayout(header)

        self._desc = QLabel(_tr(
            "perfect_planner.offspring_tracker.description",
            default="Track each planned pair, any kittens already in the save, and the projected stat / inbreeding outcome.",
        ))
        self._desc.setWordWrap(True)
        self._desc.setStyleSheet("color:#8d8da8; font-size:11px;")
        root.addWidget(self._desc)

        self._table = QTableWidget(0, 16)
        self._table.setIconSize(QSize(60, 20))
        self._table.setHorizontalHeaderLabels([
            _tr("perfect_planner.offspring_tracker.table.parent_a", default="Parent A"),
            _tr("perfect_planner.offspring_tracker.table.parent_b", default="Parent B"),
            _tr("perfect_planner.offspring_tracker.table.offspring", default="Offspring"),
            "Sel",
            "Age",
            "STR",
            "DEX",
            "CON",
            "INT",
            "SPD",
            "CHA",
            "LCK",
            "Agg",
            "Lib",
            "Inbred",
            "Notes",
        ])
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QAbstractItemView.SelectItems)
        self._table.setSelectionMode(QAbstractItemView.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.setWordWrap(True)
        self._table.setSortingEnabled(False)
        hh = self._table.horizontalHeader()
        hh.setDefaultAlignment(Qt.AlignCenter)
        hh.setStretchLastSection(False)
        hh.setSectionResizeMode(0, QHeaderView.Interactive)
        hh.setSectionResizeMode(1, QHeaderView.Interactive)
        hh.setSectionResizeMode(2, QHeaderView.Interactive)
        hh.setSectionResizeMode(3, QHeaderView.Fixed)
        hh.setSectionResizeMode(4, QHeaderView.Fixed)
        for col in range(5, 12):
            hh.setSectionResizeMode(col, QHeaderView.Fixed)
        for col in range(12, 15):
            hh.setSectionResizeMode(col, QHeaderView.Fixed)
        hh.setSectionResizeMode(15, QHeaderView.Stretch)
        self._table.setColumnWidth(0, 145)
        self._table.setColumnWidth(1, 145)
        self._table.setColumnWidth(2, 145)
        self._table.setColumnWidth(3, 24)
        self._table.setColumnWidth(4, 44)
        for col in range(5, 12):
            self._table.setColumnWidth(col, 44)
        self._table.setColumnWidth(12, 52)
        self._table.setColumnWidth(13, 52)
        self._table.setColumnWidth(14, 60)
        self._table.setColumnWidth(15, 100)
        self._table.setStyleSheet("""
            QTableWidget {
                background:#101023;
                color:#ddd;
                border:1px solid #26264a;
                font-size:9px;
            }
            QTableWidget::item { padding:1px 2px; }
            QHeaderView::section {
                background:#151532;
                color:#7d8bb0;
                border:none;
                border-bottom:1px solid #26264a;
                padding:2px 2px;
                font-weight:bold;
                font-size:8px;
            }
        """)
        self._table.cellClicked.connect(self._on_cell_clicked)
        root.addWidget(self._table, 1)

        self.retranslate_ui()
        _enforce_min_font_in_widget_tree(self)
        self._table.setSortingEnabled(False)
        self._table.horizontalHeader().setSortIndicatorShown(False)

    def set_navigate_to_cat_callback(self, callback):
        self._navigate_to_cat_callback = callback

    def retranslate_ui(self):
        self._title.setText(_tr("perfect_planner.offspring_tracker.title", default="Offspring Tracker"))
        self._desc.setText(_tr(
            "perfect_planner.offspring_tracker.description",
            default="Track each planned pair, any kittens already in the save, and the projected stat / inbreeding outcome.",
        ))
        self._table.setHorizontalHeaderLabels([
            _tr("perfect_planner.offspring_tracker.table.parent_a", default="Parent A"),
            _tr("perfect_planner.offspring_tracker.table.parent_b", default="Parent B"),
            _tr("perfect_planner.offspring_tracker.table.offspring", default="Offspring"),
            "Sel",
            "Age",
            "STR",
            "DEX",
            "CON",
            "INT",
            "SPD",
            "CHA",
            "LCK",
            "Agg",
            "Lib",
            "Inbred",
            "Notes",
        ])
        if self._rows:
            self.set_rows(self._rows)
        else:
            self._summary.setText(_tr(
                "perfect_planner.offspring_tracker.summary_empty",
                default="Build a plan to track offspring outcomes.",
            ))

    @staticmethod
    def _parent_caption(cat: Cat) -> str:
        room = cat.room_display or cat.status or "?"
        heart = " \u2665" if getattr(cat, "lovers", None) else ""
        return f"{cat.name}{heart}\n{cat.gender_display} \u00b7 {room}"

    @staticmethod
    def _parent_tooltip(cat: Cat) -> str:
        room = cat.room_display or cat.status or "?"
        return (
            f"Room: {room}\n"
            f"Generation: {getattr(cat, 'generation', 0)}\n"
            f"Base sum: {sum(cat.base_stats.values())}"
        )

    @staticmethod
    def _offspring_caption(children: list[Cat]) -> str:
        if not children:
            return "No tracked offspring yet"

        lines = [f"Tracked offspring ({len(children)})"]
        for child in children[:3]:
            lines.append(f"{child.name} ({child.gender_display})")
        if len(children) > 3:
            lines.append(f"+{len(children) - 3} more")
        return "\n".join(lines)

    @staticmethod
    def _offspring_tooltip(children: list[Cat]) -> str:
        if not children:
            return "No tracked offspring are recorded for this pair yet."
        return "\n".join(
            f"{child.name} ({child.gender_display}) - {child.room_display or child.status or '?'}"
            for child in children
        )

    @staticmethod
    def _pair_key_for_cats(cat_a: Cat, cat_b: Cat) -> tuple[int, int]:
        a_key, b_key = cat_a.db_key, cat_b.db_key
        return (a_key, b_key) if a_key < b_key else (b_key, a_key)

    @staticmethod
    def _pair_uid_key(cat_a: Cat, cat_b: Cat) -> str:
        a_uid = _cat_uid(cat_a)
        b_uid = _cat_uid(cat_b)
        if not a_uid or not b_uid:
            return ""
        left, right = sorted((a_uid, b_uid))
        return f"{left}|{right}"

    def _set_selected_child(self, cat_a: Cat, cat_b: Cat, child: Optional[Cat]) -> bool:
        pair_key = self._pair_key_for_cats(cat_a, cat_b)
        pair_uid_key = self._pair_uid_key(cat_a, cat_b)
        current = self._selected_offspring_by_pair.get(pair_key)
        child_uid = _cat_uid(child) if child is not None else ""

        if child is None:
            self._selected_offspring_by_pair.pop(pair_key, None)
            if pair_uid_key:
                self._selected_child_uid_by_pair_key.pop(pair_uid_key, None)
            _save_perfect_planner_selected_offspring(self._selected_child_uid_by_pair_key, self._save_path)
            return False

        if current == child.db_key:
            self._selected_offspring_by_pair.pop(pair_key, None)
            if pair_uid_key:
                self._selected_child_uid_by_pair_key.pop(pair_uid_key, None)
            _save_perfect_planner_selected_offspring(self._selected_child_uid_by_pair_key, self._save_path)
            return False

        self._selected_offspring_by_pair[pair_key] = child.db_key
        if pair_uid_key and child_uid:
            self._selected_child_uid_by_pair_key[pair_uid_key] = child_uid
            _save_perfect_planner_selected_offspring(self._selected_child_uid_by_pair_key, self._save_path)
        return True

    @staticmethod
    def _compact_stat_lines(values: dict[str, int] | dict[str, float], *, expected: bool = False) -> list[str]:
        def _fmt(stat: str) -> str:
            prefix = stat[:3].title()
            val = values.get(stat, 0)
            return f"{prefix} {val:.1f}" if expected else f"{prefix} {int(val)}"

        return [
            " | ".join(_fmt(stat) for stat in STAT_NAMES[:4]),
            " | ".join(_fmt(stat) for stat in STAT_NAMES[4:]),
        ]

    @staticmethod
    def _born_stats_caption(cat: Cat) -> str:
        return "\n".join(["Actual"] + PerfectPlannerOffspringTracker._compact_stat_lines(cat.base_stats))

    @staticmethod
    def _expected_stats_caption(projection: dict) -> str:
        stat_ranges = projection.get("stat_ranges", {})

        def _fmt(stat: str) -> str:
            lo, hi = stat_ranges.get(stat, (0, 0))
            prefix = stat[:3].title()
            return f"{prefix} {lo}" if lo == hi else f"{prefix} {lo}-{hi}"

        return "\n".join([
            "Expected",
            " | ".join(_fmt(stat) for stat in STAT_NAMES[:4]),
            " | ".join(_fmt(stat) for stat in STAT_NAMES[4:]),
        ])

    @staticmethod
    def _born_attributes_caption(cat: Cat) -> str:
        inbred = _trait_label_from_value("inbredness", getattr(cat, "inbredness", 0.0)) or "unknown"
        aggression = _trait_label_from_value("aggression", getattr(cat, "aggression", 0.0)) or "unknown"
        libido = _trait_label_from_value("libido", getattr(cat, "libido", 0.0)) or "unknown"
        return f"Inbred {inbred} | Agg {aggression} | Lib {libido}"

    @staticmethod
    def _expected_attributes_caption(cat_a: Cat, cat_b: Cat, coi: float, risk: float, shared_total: int, shared_recent: int) -> str:
        inbred = _trait_label_from_value("inbredness", coi) or "unknown"
        aggression = _trait_label_from_value("aggression", (getattr(cat_a, "aggression", 0.0) + getattr(cat_b, "aggression", 0.0)) / 2.0) or "unknown"
        libido = _trait_label_from_value("libido", (getattr(cat_a, "libido", 0.0) + getattr(cat_b, "libido", 0.0)) / 2.0) or "unknown"
        return f"Inbred {inbred} | Agg {aggression} | Lib {libido}"

    @staticmethod
    def _metric_item(label: str, detail: str, bg: QColor, tooltip: str) -> QTableWidgetItem:
        item = QTableWidgetItem(label)
        item.setTextAlignment(Qt.AlignCenter)
        item.setBackground(QBrush(bg))
        item.setForeground(QBrush(QColor(255, 255, 255)))
        item.setToolTip(tooltip)
        return item

    def _build_attributes_widget(
        self,
        aggression_value: float,
        libido_value: float,
        inbred_value: float,
    ) -> QWidget:
        wrapper = QFrame()
        wrapper.setStyleSheet("QFrame { background: transparent; border: none; }")
        layout = QVBoxLayout(wrapper)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(4)
        grid.setVerticalSpacing(2)

        values = [
            ("aggression", aggression_value),
            ("libido", libido_value),
            ("inbredness", inbred_value),
        ]
        for col, (field, value) in enumerate(values):
            header = QLabel(field.title())
            header.setAlignment(Qt.AlignCenter)
            header.setStyleSheet("color:#9ca6c7; font-size:8px; font-weight:bold;")
            grid.addWidget(header, 0, col)

            label = _trait_label_from_value(field, value) or "unknown"
            item = QLabel(label)
            item.setAlignment(Qt.AlignCenter)
            item.setStyleSheet(
                f"background:{_trait_level_color(label).name()}; color:#fff; "
                "font-size:9px; font-weight:bold; border-radius:3px; padding:1px 4px;"
            )
            item.setToolTip(f"{field.title()}: {value:.3f} ({label})")
            grid.addWidget(item, 1, col)

        layout.addLayout(grid)
        wrapper.setToolTip(
            f"Aggression: {aggression_value:.3f} ({_trait_label_from_value('aggression', aggression_value) or 'unknown'})\n"
            f"Libido: {libido_value:.3f} ({_trait_label_from_value('libido', libido_value) or 'unknown'})\n"
            f"Inbredness: {inbred_value:.3f} ({_trait_label_from_value('inbredness', inbred_value) or 'unknown'})"
        )
        return wrapper

    @staticmethod
    def _stats_caption(projection: dict) -> str:
        stat_ranges = projection.get("stat_ranges", {})
        first_line: list[str] = []
        second_line: list[str] = []
        for stat in STAT_NAMES[:4]:
            lo, hi = stat_ranges.get(stat, (0, 0))
            first_line.append(f"{stat} {lo}" if lo == hi else f"{stat} {lo}-{hi}")
        for stat in STAT_NAMES[4:]:
            lo, hi = stat_ranges.get(stat, (0, 0))
            second_line.append(f"{stat} {lo}" if lo == hi else f"{stat} {lo}-{hi}")

        sum_lo, sum_hi = projection.get("sum_range", (0, 0))
        avg_expected = float(projection.get("avg_expected", 0.0))
        seven_plus = float(projection.get("seven_plus_total", 0.0))
        return "\n".join([
            "Stats",
            " | ".join(first_line),
            " | ".join(second_line),
            f"Sum {sum_lo}-{sum_hi} | Avg {avg_expected:.1f} | 7+ {seven_plus:.1f}/7",
        ])

    @staticmethod
    def _stats_tooltip(projection: dict) -> str:
        stat_ranges = projection.get("stat_ranges", {})
        expected_stats = projection.get("expected_stats", {})
        lines = ["Projected stat ranges:"]
        for stat in STAT_NAMES:
            lo, hi = stat_ranges.get(stat, (0, 0))
            expected = float(expected_stats.get(stat, hi))
            lines.append(f"  {stat}: {lo}-{hi} (expected {expected:.1f})")
        locked = ", ".join(projection.get("locked_stats", ())) or "none"
        reachable = ", ".join(projection.get("reachable_stats", ())) or "none"
        missing = ", ".join(projection.get("missing_stats", ())) or "none"
        sum_lo, sum_hi = projection.get("sum_range", (0, 0))
        lines.extend([
            f"Sum range: {sum_lo}-{sum_hi}",
            f"Locked stats: {locked}",
            f"Reachable stats: {reachable}",
            f"Missing stats: {missing}",
        ])
        return "\n".join(lines)

    @staticmethod
    def _notes_caption(projection: dict, coi: float, risk: float, shared_total: int, shared_recent: int) -> str:
        locked = ", ".join(projection.get("locked_stats", ())) or "none"
        reachable = ", ".join(projection.get("reachable_stats", ())) or "none"
        missing = ", ".join(projection.get("missing_stats", ())) or "none"
        label = _trait_label_from_value("inbredness", coi) or "unknown"
        return (
            f"Lck {locked} | Rch {reachable} | Miss {missing} | "
            f"Inbred {label} | R {risk:.1f}% | Sh {shared_total}/{shared_recent}"
        )

    @staticmethod
    def _inbredness_caption(coi: float, risk: float, shared_total: int, shared_recent: int) -> str:
        label = _trait_label_from_value("inbredness", coi) or "unknown"
        return "\n".join([
            "Inbredness",
            f"{label} | COI {coi * 100:.1f}% | Risk {risk:.1f}%",
            f"Shared {shared_total} total / {shared_recent} recent",
        ])

    @staticmethod
    def _inbredness_tooltip(coi: float, risk: float, shared_total: int, shared_recent: int) -> str:
        label = _trait_label_from_value("inbredness", coi) or "unknown"
        return (
            f"Inbredness label: {label}\n"
            f"Coefficient of inbreeding: {coi:.3f}\n"
            f"Birth defect risk: {risk:.1f}%\n"
            f"Shared ancestors: {shared_total} total, {shared_recent} recent"
        )

    @staticmethod
    def _stat_tint(color: QColor, strength: float = 0.26, lift: int = 16) -> QColor:
        return QColor(
            min(255, int(color.red() * strength) + lift),
            min(255, int(color.green() * strength) + lift),
            min(255, int(color.blue() * strength) + lift),
        )

    def _build_stats_widget(
        self,
        *,
        projection: dict,
        actual_stats: dict[str, int] | None = None,
        trait_values: dict[str, float] | None = None,
        detail_text: str = "",
    ) -> QWidget:
        table = QTableWidget(2, len(STAT_NAMES) + 3)
        table.setObjectName("offspringMetricsTable")
        table.setHorizontalHeaderLabels([s.upper() for s in STAT_NAMES] + ["AGG", "LIB", "INBRED"])
        table.setVerticalHeaderLabels(["Value", "Details"])
        table.verticalHeader().setVisible(False)
        table.setSelectionMode(QAbstractItemView.NoSelection)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setFocusPolicy(Qt.NoFocus)
        table.setAlternatingRowColors(False)
        table.setShowGrid(True)
        table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        table.setStyleSheet("""
            QTableWidget {
                background:#101023;
                color:#ddd;
                border:1px solid #26264a;
                font-size:9px;
            }
            QTableWidget::item { padding:1px 2px; }
            QHeaderView::section {
                background:#1a1a36;
                color:#9ca6c7;
                border:none;
                border-bottom:1px solid #26264a;
                padding:1px 2px;
                font-weight:bold;
                font-size:8px;
            }
        """)
        hh = table.horizontalHeader()
        hh.setDefaultAlignment(Qt.AlignCenter)
        for col in range(len(STAT_NAMES) + 3):
            hh.setSectionResizeMode(col, QHeaderView.Stretch)
        table.verticalHeader().setDefaultSectionSize(18)
        table.horizontalHeader().setFixedHeight(16)

        stat_ranges = projection.get("stat_ranges", {})
        expected_stats = projection.get("expected_stats", {})
        stat_map = actual_stats or {}
        trait_map = trait_values or {}

        def _metric_item(text: str, bg: QColor, tooltip: str) -> QTableWidgetItem:
            item = QTableWidgetItem(text)
            item.setTextAlignment(Qt.AlignCenter)
            item.setBackground(QBrush(bg))
            item.setForeground(QBrush(QColor(255, 255, 255)))
            item.setToolTip(tooltip)
            return item

        for col, stat in enumerate(STAT_NAMES):
            if actual_stats is not None:
                value = int(stat_map.get(stat, 0))
                detail = "actual"
                base = STAT_COLORS.get(value, QColor(100, 100, 115))
                bg = self._stat_tint(base, strength=0.28, lift=18)
                tip = f"{stat}: {value}"
                text = str(value)
            else:
                lo, hi = stat_ranges.get(stat, (0, 0))
                detail = "projected"
                base = STAT_COLORS.get(max(lo, hi), QColor(100, 100, 115))
                bg = self._stat_tint(base, strength=0.22, lift=18)
                expected = float(expected_stats.get(stat, hi))
                text = f"{lo}" if lo == hi else f"{lo}-{hi}"
                tip = f"{stat}: {lo}-{hi} (expected {expected:.1f})"
            table.setItem(0, col, _metric_item(text, bg, tip))
            table.setItem(1, col, _metric_item(detail, QColor(22, 22, 43), tip))

        for offset, field in enumerate(("aggression", "libido", "inbredness"), start=len(STAT_NAMES)):
            value = float(trait_map.get(field, 0.0))
            text = _trait_label_from_value(field, value) or "unknown"
            detail = "actual" if actual_stats is not None else "projected"
            tip = f"{field.title()}: {value:.3f} ({text})"
            bg = _trait_level_color(text)
            table.setItem(0, offset, _metric_item(text, bg, tip))
            table.setItem(1, offset, _metric_item(detail, QColor(22, 22, 43), tip))

        if detail_text:
            table.setToolTip(detail_text)
        table.setFixedHeight(table.horizontalHeader().height() + sum(table.rowHeight(i) for i in range(table.rowCount())) + 6)
        wrapper = QFrame()
        wrapper.setStyleSheet("QFrame { background: transparent; border: none; }")
        layout = QVBoxLayout(wrapper)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(table)
        return wrapper

    def set_rows(self, rows: list[dict]):
        restore_row = self._table.currentRow()
        restore_column = self._table.currentColumn()
        self._rows = list(rows)
        self._selected_offspring_by_pair = {}
        tracked_offspring = sum(len(row.get("known_offspring", [])) for row in self._rows)
        using_count = sum(1 for row in self._rows if row.get("source") == "using")
        suggested_count = len(self._rows) - using_count
        self._table.clearSpans()
        self._table.setSortingEnabled(False)
        self._table.horizontalHeader().setSortIndicatorShown(False)
        try:

            if not self._rows:
                self._render_rows = []
                self._table.setRowCount(0)
                self._summary.setText(_tr(
                    "perfect_planner.offspring_tracker.summary_empty",
                    default="Build a plan to track offspring outcomes.",
                ))
                return

            self._summary.setText(_tr(
                "perfect_planner.offspring_tracker.summary",
                default="{pairs} pairs tracked | {offspring} known offspring already in the save",
                pairs=len(self._rows),
                offspring=tracked_offspring,
            ) + f" | {using_count} using, {suggested_count} suggested")

            render_rows: list[dict] = []
            for pair_row in self._rows:
                known_offspring = list(pair_row.get("known_offspring", []))
                if known_offspring:
                    for child_idx, child in enumerate(known_offspring, 1):
                        render_rows.append({
                            "pair": pair_row,
                            "child": child,
                            "child_index": child_idx,
                            "is_expected": False,
                        })
                else:
                    render_rows.append({
                        "pair": pair_row,
                        "child": None,
                        "child_index": 1,
                        "is_expected": True,
                    })

            self._render_rows = render_rows
            self._table.setRowCount(len(render_rows))
            row_idx = 0
            for pair_row in self._rows:
                cat_a = pair_row["cat_a"]
                cat_b = pair_row["cat_b"]
                projection = pair_row["projection"]
                known_offspring = list(pair_row.get("known_offspring", []))
                risk = float(pair_row.get("risk", 0.0))
                coi = float(pair_row.get("coi", 0.0))
                shared_total, shared_recent = pair_row.get("shared", (0, 0))
                pair_key = self._pair_key_for_cats(cat_a, cat_b)
                pair_uid_key = self._pair_uid_key(cat_a, cat_b)
                selected_child_uid = self._selected_child_uid_by_pair_key.get(pair_uid_key, "")
                selected_child_db = None
                if pair_uid_key:
                    for child in known_offspring:
                        if _cat_uid(child) and _cat_uid(child) == selected_child_uid:
                            selected_child_db = child.db_key
                            self._selected_offspring_by_pair[pair_key] = child.db_key
                            break
                    if selected_child_db is None:
                        self._selected_offspring_by_pair.pop(pair_key, None)

                span = len(known_offspring) if known_offspring else 1
                parent_a_item = QTableWidgetItem(self._parent_caption(cat_a))
                parent_a_item.setData(Qt.UserRole, cat_a.db_key)
                parent_a_item.setToolTip(self._parent_tooltip(cat_a))
                parent_a_item.setForeground(QBrush(QColor(100, 149, 237)))
                icon_a = _make_tag_icon(_cat_tags(cat_a), dot_size=14, spacing=4)
                if not icon_a.isNull():
                    parent_a_item.setIcon(icon_a)

                parent_b_item = QTableWidgetItem(self._parent_caption(cat_b))
                parent_b_item.setData(Qt.UserRole, cat_b.db_key)
                parent_b_item.setToolTip(self._parent_tooltip(cat_b))
                parent_b_item.setForeground(QBrush(QColor(100, 149, 237)))
                icon_b = _make_tag_icon(_cat_tags(cat_b), dot_size=14, spacing=4)
                if not icon_b.isNull():
                    parent_b_item.setIcon(icon_b)

                if span > 1:
                    self._table.setSpan(row_idx, 0, span, 1)
                    self._table.setSpan(row_idx, 1, span, 1)

                self._table.setItem(row_idx, 0, parent_a_item)
                self._table.setItem(row_idx, 1, parent_b_item)

                for child_offset in range(span):
                    current_row = row_idx + child_offset
                    child = known_offspring[child_offset] if known_offspring else None
                    render_row = self._render_rows[current_row]

                    if child is not None:
                        heart = " \u2665" if getattr(child, "lovers", None) else ""
                        selected = selected_child_db == child.db_key
                        offspring_text = f"{child.name}{heart}"
                        age_text = str(child.age) if getattr(child, "age", None) is not None else "\u2014"
                        offspring_color = QColor(98, 194, 135) if selected else QColor(100, 149, 237)
                    else:
                        offspring_text = "Not yet"
                        age_text = "\u2014"
                        offspring_color = QColor(150, 150, 165)

                    offspring_item = QTableWidgetItem(offspring_text)
                    offspring_item.setToolTip(
                        self._offspring_tooltip(known_offspring) if child is None else f"{child.name} ({child.gender_display})"
                    )
                    offspring_item.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
                    offspring_item.setForeground(QBrush(offspring_color))
                    offspring_item.setData(Qt.UserRole, child.db_key if child is not None else None)
                    if child is not None:
                        f = offspring_item.font()
                        f.setUnderline(True)
                        offspring_item.setFont(f)
                        offspring_item.setForeground(QBrush(offspring_color))
                        lover_note = ""
                        if getattr(child, "lovers", None):
                            lover_note = "\nIn love with: " + ", ".join(other.name for other in child.lovers)
                        selected_note = "\nSelected for next breeding." if selected_child_db == child.db_key else ""
                        offspring_item.setToolTip(f"{child.name} ({child.gender_display}){lover_note}{selected_note}\nClick to open in the main cat view.")

                    self._table.setItem(current_row, 2, offspring_item)
                    sel_item = QTableWidgetItem("\u2611" if child is not None and selected_child_db == child.db_key else "\u2610")
                    sel_item.setTextAlignment(Qt.AlignCenter)
                    sel_item.setForeground(QBrush(QColor(98, 194, 135) if child is not None and selected_child_db == child.db_key else QColor(155, 168, 196)))
                    sel_item.setToolTip("Selected offspring for next breeding" if child is not None and selected_child_db == child.db_key else "Click to select this offspring")
                    self._table.setItem(current_row, 3, sel_item)
                    age_item = QTableWidgetItem(age_text)
                    age_item.setTextAlignment(Qt.AlignCenter)
                    age_item.setForeground(QBrush(QColor(98, 194, 135) if child is not None else QColor(155, 168, 196)))
                    age_item.setToolTip("Actual age" if child is not None else "Projected")
                    self._table.setItem(current_row, 4, age_item)
                    if child is not None:
                        stat_values = child.base_stats
                        trait_values = {
                            "aggression": float(getattr(child, "aggression", 0.0) or 0.0),
                            "libido": float(getattr(child, "libido", 0.0) or 0.0),
                            "inbredness": float(getattr(child, "inbredness", 0.0) or 0.0),
                        }
                    else:
                        stat_values = None
                        trait_values = {
                            "aggression": (getattr(cat_a, "aggression", 0.0) + getattr(cat_b, "aggression", 0.0)) / 2.0,
                            "libido": (getattr(cat_a, "libido", 0.0) + getattr(cat_b, "libido", 0.0)) / 2.0,
                            "inbredness": coi,
                        }

                    for stat_idx, stat in enumerate(STAT_NAMES, start=5):
                        if stat_values is not None:
                            val = int(stat_values.get(stat, 0))
                            label = str(val)
                            base = STAT_COLORS.get(val, QColor(100, 100, 115))
                            bg = self._stat_tint(base, strength=0.28, lift=18)
                            tip = f"Actual {stat}: {val}"
                        else:
                            lo, hi = projection["stat_ranges"].get(stat, (0, 0))
                            label = f"{lo}" if lo == hi else f"{lo}-{hi}"
                            base = STAT_COLORS.get(max(lo, hi), QColor(100, 100, 115))
                            bg = self._stat_tint(base, strength=0.22, lift=18)
                            tip = f"Projected {stat}: {lo}-{hi} (expected {float(projection.get('expected_stats', {}).get(stat, 0.0)):.1f})"
                        self._table.setItem(current_row, stat_idx, self._metric_item(label, "", bg, tip))

                    for trait_idx, field in enumerate(("aggression", "libido", "inbredness"), start=12):
                        value = float(trait_values[field])
                        label = _trait_label_from_value(field, value) or "unknown"
                        bg = _trait_level_color(label)
                        tip = f"{field.title()}: {value:.3f} ({label})"
                        self._table.setItem(current_row, trait_idx, self._metric_item(label, "", bg, tip))

                    note_text = "Projected" if child is None else ""
                    note_item = QTableWidgetItem(note_text)
                    note_item.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
                    note_item.setForeground(QBrush(QColor(216, 181, 106) if child is None else QColor(155, 168, 196)))
                    note_item.setToolTip("Projected offspring" if child is None else "")
                    self._table.setItem(current_row, 15, note_item)

                    self._table.setRowHeight(current_row, max(self._table.rowHeight(current_row), 38))

                row_idx += span
        finally:
            if 0 <= restore_row < self._table.rowCount() and 0 <= restore_column < self._table.columnCount():
                self._table.setCurrentCell(restore_row, restore_column)

    def set_save_path(self, save_path: Optional[str], *, refresh_existing: bool = True):
        self._save_path = save_path
        self._selected_child_uid_by_pair_key = _load_perfect_planner_selected_offspring(self._save_path)
        if refresh_existing and self._rows:
            self.set_rows(self._rows)

    def reset_to_defaults(self):
        self._selected_offspring_by_pair = {}
        self._selected_child_uid_by_pair_key = {}
        _save_perfect_planner_selected_offspring(self._selected_child_uid_by_pair_key, self._save_path)
        if self._rows:
            self.set_rows(self._rows)
        else:
            self._table.clearSelection()

    def clear(self):
        self._rows = []
        self._render_rows = []
        self._table.clearSpans()
        self._table.setRowCount(0)
        self._summary.setText(_tr(
            "perfect_planner.offspring_tracker.summary_empty",
            default="Build a plan to track offspring outcomes.",
        ))

    def _on_cell_clicked(self, row: int, column: int):
        if column == 2 and 0 <= row < len(self._render_rows):
            render_row = self._render_rows[row]
            child = render_row.get("child")
            if child is not None:
                pair_row = render_row.get("pair", {})
                cat_a = pair_row.get("cat_a")
                cat_b = pair_row.get("cat_b")
                if hasattr(cat_a, "db_key") and hasattr(cat_b, "db_key"):
                    self._set_selected_child(cat_a, cat_b, child)
                    self.set_rows(self._rows)
                if self._navigate_to_cat_callback is not None:
                    self._navigate_to_cat_callback(int(child.db_key))
                if self._select_offspring_callback is not None:
                    self._select_offspring_callback(render_row)
                return
            if self._select_offspring_callback is not None:
                self._select_offspring_callback(render_row)
            return
        if column == 3 and 0 <= row < len(self._render_rows):
            render_row = self._render_rows[row]
            child = render_row.get("child")
            if child is None:
                return
            pair_row = render_row.get("pair", {})
            cat_a = pair_row.get("cat_a")
            cat_b = pair_row.get("cat_b")
            if hasattr(cat_a, "db_key") and hasattr(cat_b, "db_key"):
                self._set_selected_child(cat_a, cat_b, child)
                self.set_rows(self._rows)
            if self._select_offspring_callback is not None:
                self._select_offspring_callback(render_row)
            return
        if column not in (0, 1):
            return
        if self._navigate_to_cat_callback is None:
            return
        item = self._table.item(row, column)
        if item is None:
            return
        db_key = item.data(Qt.UserRole)
        if db_key is not None:
            self._navigate_to_cat_callback(int(db_key))


class PerfectPlannerFoundationPairsPanel(QWidget):
    """Persistent editor for the four foundation breeding pairs."""

    configChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(
            "QWidget { background:#0a0a18; }"
            "QLabel { color:#bbb; }"
            "QComboBox { background:#1a1a32; color:#ddd; border:1px solid #2a2a4a; "
            "border-radius:4px; padding:2px 6px; }"
            "QComboBox QAbstractItemView { background:#101023; color:#ddd; "
            "selection-background-color:#252545; }"
            "QPushButton { background:#1a1a32; color:#aaa; border:1px solid #2a2a4a; "
            "border-radius:4px; padding:4px 8px; font-size:11px; }"
            "QPushButton:hover { background:#252545; color:#ddd; }"
        )
        self._cats: list[Cat] = []
        self._cat_by_uid: dict[str, Cat] = {}
        self._slots: list[dict] = []
        self._save_path: Optional[str] = None
        self._stored_config = _load_perfect_planner_foundation_pairs(self._save_path)
        self._slot_count = max(4, min(12, len(self._stored_config) or 4))

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(6)

        header = QHBoxLayout()
        self._title = QLabel(_tr("perfect_planner.foundation.title", default="Foundation Pairs"))
        self._title.setStyleSheet("color:#ddd; font-size:13px; font-weight:bold;")
        self._summary = QLabel("")
        self._summary.setStyleSheet("color:#666; font-size:11px;")
        header.addWidget(self._title)
        header.addStretch()
        header.addWidget(self._summary)
        root.addLayout(header)

        self._desc = QLabel(_tr(
            "perfect_planner.foundation.description",
            default="Pick the starting pairs you plan to use, then mark each one as suggested or actively used. The selections are saved alongside the current save file.",
        ))
        self._desc.setWordWrap(True)
        self._desc.setStyleSheet("color:#8d8da8; font-size:11px;")
        root.addWidget(self._desc)

        self._rows_widget = QWidget()
        self._rows_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
        self._rows_layout = QVBoxLayout(self._rows_widget)
        self._rows_layout.setContentsMargins(0, 0, 0, 0)
        self._rows_layout.setSpacing(6)
        root.addWidget(self._rows_widget)
        root.addStretch(1)

        self._apply_slot_count(self._slot_count, emit=False)
        self.set_config(self._stored_config)
        self._update_summary()
        _enforce_min_font_in_widget_tree(self)

    @staticmethod
    def _slot_color(slot_index: int) -> QColor:
        color = QColor(PAIR_COLORS[slot_index % len(PAIR_COLORS)])
        return color if color.isValid() else QColor(90, 90, 110)

    @staticmethod
    def _cat_label(cat: Cat) -> str:
        room = cat.room_display or cat.status or "?"
        return f"{cat.name} ({cat.gender_display}) \u00b7 {room}"

    def _refresh_combo(self, combo: QComboBox, selected_uid: str):
        combo.blockSignals(True)
        combo.clear()
        combo.addItem("None", "")
        for cat in sorted(self._cats, key=lambda c: ((c.name or "").lower(), _cat_uid(c))):
            uid = _cat_uid(cat)
            if not uid:
                continue
            combo.addItem(self._cat_label(cat), uid)
            combo.setItemData(combo.count() - 1, self._cat_tooltip(cat), Qt.ToolTipRole)
        idx = combo.findData(selected_uid)
        combo.setCurrentIndex(idx if idx >= 0 else 0)
        combo.blockSignals(False)

    @staticmethod
    def _cat_tooltip(cat: Cat) -> str:
        room = cat.room_display or cat.status or "?"
        return (
            f"{cat.name}\n"
            f"Room: {room}\n"
            f"Base sum: {sum(cat.base_stats.values())}"
        )

    def _slot_values(self, slot: dict) -> tuple[str, str, bool]:
        a_uid = str(slot["combo_a"].currentData() or "").strip().lower()
        b_uid = str(slot["combo_b"].currentData() or "").strip().lower()
        using = bool(slot["use_btn"].isChecked())
        return a_uid, b_uid, using

    def _update_slot_style(self, slot: dict):
        slot_index = slot["slot_index"]
        color = self._slot_color(slot_index)
        a_uid, b_uid, using = self._slot_values(slot)
        selected = bool(a_uid and b_uid)
        accent = color.lighter(125 if using else 102)
        bg = color.darker(220 if using else 260)
        state_text = _tr("perfect_planner.foundation.using", default="Using these") if using else _tr("perfect_planner.foundation.suggested", default="Suggested")
        if selected:
            slot["state_lbl"].setText(state_text)
            slot["state_lbl"].setStyleSheet(
                f"color:#fff; background:rgba({accent.red()},{accent.green()},{accent.blue()},160);"
                " border:1px solid rgba(255,255,255,40); border-radius:4px; padding:2px 6px;"
                " font-size:10px; font-weight:bold;"
            )
        else:
            slot["state_lbl"].setText(_tr("perfect_planner.foundation.empty", default="Empty"))
            slot["state_lbl"].setStyleSheet(
                "color:#888; background:#15152e; border:1px solid #242447; "
                "border-radius:4px; padding:2px 6px; font-size:10px;"
            )
        if not selected and slot["use_btn"].isChecked():
            slot["use_btn"].blockSignals(True)
            slot["use_btn"].setChecked(False)
            slot["use_btn"].blockSignals(False)
        slot["use_btn"].setEnabled(selected)
        slot["use_btn"].setText(state_text)
        slot["use_btn"].setStyleSheet(
            "QPushButton { "
            f"background:rgba({bg.red()},{bg.green()},{bg.blue()},180); color:#f2f2f7; "
            f"border:1px solid rgba({accent.red()},{accent.green()},{accent.blue()},180);"
            " border-radius:4px; padding:4px 8px; font-size:11px; font-weight:bold; }"
            "QPushButton:hover { background:#252545; color:#fff; }"
            "QPushButton:checked { background:#2a5a3a; color:#f0fff0; border-color:#4a8a5a; }"
        )
        slot["widget"].setStyleSheet(
            "QFrame { "
            f"background:rgba({max(16, accent.red()//5)},{max(16, accent.green()//5)},{max(16, accent.blue()//5)},120);"
            " border:1px solid #242447; border-radius:6px; }"
        )
        slot["idx_lbl"].setStyleSheet(
            "QLabel { "
            f"color:#fff; background:rgba({accent.red()},{accent.green()},{accent.blue()},190);"
            " border:1px solid rgba(255,255,255,30); border-radius:4px; padding:2px 4px;"
            " font-size:10px; font-weight:bold; }"
        )
        slot["swatch"].setStyleSheet(
            f"background:{accent.name()}; border-radius:3px;"
        )

    def _clear_slot_widgets(self):
        for slot in self._slots:
            self._rows_layout.removeWidget(slot["widget"])
            slot["widget"].deleteLater()
        self._slots = []

    def _apply_slot_count(self, count: int, emit: bool = True):
        count = max(1, min(12, int(count or 1)))
        if count == self._slot_count and len(self._slots) == count:
            return
        current = self._stored_config[:]
        self._clear_slot_widgets()
        self._slot_count = count
        if len(current) < count:
            current.extend([
                {"cat_a_uid": "", "cat_b_uid": "", "using": False}
                for _ in range(count - len(current))
            ])
        self._stored_config = current
        for slot_index in range(count):
            self._add_slot(slot_index, emit=False)
        self._update_summary()
        if emit:
            self.configChanged.emit()

    def set_slot_count(self, count: int):
        self._apply_slot_count(count, emit=False)
        for slot in self._slots:
            self._refresh_slot(slot)
        self._update_summary()

    def _save(self):
        self._sync_visible_to_stored()
        _save_perfect_planner_foundation_pairs(self._stored_config, self._save_path)
        self._update_summary()
        self.configChanged.emit()

    def _sync_visible_to_stored(self):
        for slot in self._slots:
            idx = slot["slot_index"]
            if idx >= len(self._stored_config):
                self._stored_config.extend([
                    {"cat_a_uid": "", "cat_b_uid": "", "using": False}
                    for _ in range(idx + 1 - len(self._stored_config))
                ])
            self._stored_config[idx] = {
                "cat_a_uid": str(slot["combo_a"].currentData() or "").strip().lower(),
                "cat_b_uid": str(slot["combo_b"].currentData() or "").strip().lower(),
                "using": bool(slot["use_btn"].isChecked()),
            }

    def _add_slot(self, slot_index: int, emit: bool = True):
        row = QFrame()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(8, 6, 8, 6)
        row_layout.setSpacing(6)

        swatch = QLabel()
        swatch.setFixedWidth(6)
        swatch.setMinimumHeight(24)
        row_layout.addWidget(swatch)

        idx_lbl = QLabel(_tr("perfect_planner.foundation.slot", default="Pair {index}", index=slot_index + 1))
        idx_lbl.setFixedWidth(52)
        idx_lbl.setAlignment(Qt.AlignCenter)
        idx_lbl.setStyleSheet(
            "color:#fff; font-size:10px; font-weight:bold; border-radius:4px; padding:2px 4px;"
        )
        row_layout.addWidget(idx_lbl)

        combo_a = QComboBox()
        combo_a.setMinimumWidth(170)
        combo_a.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        row_layout.addWidget(combo_a, 1)

        combo_b = QComboBox()
        combo_b.setMinimumWidth(170)
        combo_b.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        row_layout.addWidget(combo_b, 1)

        swap_btn = QPushButton("\u2194")
        swap_btn.setFixedWidth(28)
        row_layout.addWidget(swap_btn)

        clear_btn = QPushButton(_tr("common.clear", default="Clear"))
        clear_btn.setFixedWidth(64)
        row_layout.addWidget(clear_btn)

        use_btn = QPushButton()
        use_btn.setCheckable(True)
        use_btn.setMinimumWidth(110)
        row_layout.addWidget(use_btn)

        state_lbl = QLabel("")
        state_lbl.setFixedWidth(84)
        state_lbl.setAlignment(Qt.AlignCenter)
        row_layout.addWidget(state_lbl)

        slot = {
            "slot_index": slot_index,
            "widget": row,
            "swatch": swatch,
            "idx_lbl": idx_lbl,
            "combo_a": combo_a,
            "combo_b": combo_b,
            "swap_btn": swap_btn,
            "clear_btn": clear_btn,
            "use_btn": use_btn,
            "state_lbl": state_lbl,
        }
        self._slots.append(slot)
        self._rows_layout.addWidget(row)

        def _emit_change():
            self._save()

        def _refresh():
            self._update_slot_style(slot)
            self._update_summary()

        combo_a.currentIndexChanged.connect(lambda _: (_refresh(), _emit_change()))
        combo_b.currentIndexChanged.connect(lambda _: (_refresh(), _emit_change()))
        use_btn.toggled.connect(lambda _: (_refresh(), _emit_change()))
        swap_btn.clicked.connect(lambda: self._swap_slot(slot))
        clear_btn.clicked.connect(lambda: self._clear_slot(slot))

        self._refresh_slot(slot)
        if emit:
            self.configChanged.emit()

    def _refresh_slot(self, slot: dict):
        config_slot = self._stored_config[slot["slot_index"]] if slot["slot_index"] < len(self._stored_config) else {}
        self._refresh_combo(slot["combo_a"], str(config_slot.get("cat_a_uid") or "").strip().lower())
        self._refresh_combo(slot["combo_b"], str(config_slot.get("cat_b_uid") or "").strip().lower())
        slot["use_btn"].blockSignals(True)
        slot["use_btn"].setChecked(bool(config_slot.get("using", False)))
        slot["use_btn"].blockSignals(False)
        self._update_slot_style(slot)

    def _swap_slot(self, slot: dict):
        a_uid = slot["combo_a"].currentData()
        b_uid = slot["combo_b"].currentData()
        slot["combo_a"].blockSignals(True)
        slot["combo_b"].blockSignals(True)
        slot["combo_a"].setCurrentIndex(slot["combo_a"].findData(b_uid))
        slot["combo_b"].setCurrentIndex(slot["combo_b"].findData(a_uid))
        slot["combo_a"].blockSignals(False)
        slot["combo_b"].blockSignals(False)
        self._update_slot_style(slot)
        self._save()

    def _clear_slot(self, slot: dict):
        slot["combo_a"].blockSignals(True)
        slot["combo_b"].blockSignals(True)
        slot["combo_a"].setCurrentIndex(0)
        slot["combo_b"].setCurrentIndex(0)
        slot["combo_a"].blockSignals(False)
        slot["combo_b"].blockSignals(False)
        slot["use_btn"].blockSignals(True)
        slot["use_btn"].setChecked(False)
        slot["use_btn"].blockSignals(False)
        self._update_slot_style(slot)
        self._save()

    def _update_summary(self):
        filled = 0
        using = 0
        for slot in self._slots:
            a_uid, b_uid, is_using = self._slot_values(slot)
            if a_uid and b_uid:
                filled += 1
                if is_using:
                    using += 1
        suggested = filled - using
        self._summary.setText(_tr(
            "perfect_planner.foundation.summary",
            default="{filled} saved | {using} using | {suggested} suggested",
            filled=filled,
            using=using,
            suggested=suggested,
        ))

    def set_cats(self, cats: list[Cat]):
        self._cats = [cat for cat in cats if cat.status != "Gone"]
        self._cat_by_uid = {_cat_uid(cat): cat for cat in self._cats if _cat_uid(cat)}
        for slot in self._slots:
            slot_index = slot["slot_index"]
            config_slot = self._stored_config[slot_index] if slot_index < len(self._stored_config) else {}
            a_uid = str(config_slot.get("cat_a_uid") or "").strip().lower()
            b_uid = str(config_slot.get("cat_b_uid") or "").strip().lower()
            self._refresh_combo(slot["combo_a"], a_uid)
            self._refresh_combo(slot["combo_b"], b_uid)
            slot["use_btn"].blockSignals(True)
            slot["use_btn"].setChecked(bool(config_slot.get("using", False)))
            slot["use_btn"].blockSignals(False)
            self._update_slot_style(slot)
        self._update_summary()

    def get_config(self) -> list[dict]:
        self._sync_visible_to_stored()
        return list(self._stored_config)

    def set_config(self, config: list[dict]):
        normalized = []
        for i, slot in enumerate(config or []):
            if not isinstance(slot, dict):
                slot = {}
            normalized.append({
                "cat_a_uid": str(slot.get("cat_a_uid") or "").strip().lower(),
                "cat_b_uid": str(slot.get("cat_b_uid") or "").strip().lower(),
                "using": bool(slot.get("using", False)),
            })
        if not normalized:
            normalized = _default_perfect_planner_foundation_pairs()
        self._stored_config = normalized
        self._apply_slot_count(max(self._slot_count or 0, len(self._stored_config), 4), emit=False)
        for slot in self._slots:
            self._refresh_slot(slot)
        self._update_summary()

    def set_save_path(self, save_path: Optional[str], *, refresh_existing: bool = True):
        self._save_path = save_path
        self._stored_config = _load_perfect_planner_foundation_pairs(self._save_path)
        self.set_config(self._stored_config)
        if refresh_existing and self._cats:
            self.set_cats(self._cats)

    def reset_to_defaults(self):
        self.set_config(_default_perfect_planner_foundation_pairs())
        self._save()
        if self._cats:
            self.set_cats(self._cats)

    def retranslate_ui(self):
        self._title.setText(_tr("perfect_planner.foundation.title", default="Foundation Pairs"))
        self._desc.setText(_tr(
            "perfect_planner.foundation.description",
            default="Pick the starting pairs you plan to use, then mark each one as suggested or actively used. The selections are saved alongside the current save file.",
        ))
        for slot in self._slots:
            slot_index = slot["slot_index"]
            slot["idx_lbl"].setText(_tr("perfect_planner.foundation.slot", default="Pair {index}", index=slot_index + 1))
            self._update_slot_style(slot)
        self._update_summary()


class PerfectCatPlannerView(QWidget):
    """Stage-based planner for building perfect 7-base-stat lines."""

    @staticmethod
    def _set_toggle_button_label(btn: QPushButton, label: str):
        state = _tr("common.on") if btn.isChecked() else _tr("common.off")
        btn.setText(_tr("bulk.label_template", label=label, state=state))

    @staticmethod
    def _bind_persistent_toggle(btn: QPushButton, label_key: str, key: str, *, default: Optional[str] = None):
        PerfectCatPlannerView._set_toggle_button_label(btn, _tr(label_key, default=default))
        btn.toggled.connect(lambda checked: _set_optimizer_flag(key, checked))
        btn.toggled.connect(lambda _: PerfectCatPlannerView._set_toggle_button_label(btn, _tr(label_key, default=default)))

    def __init__(self, parent=None):
        super().__init__(parent)
        # Deferred import to avoid circular dependency
        from mewgenics.views.room_optimizer import RoomOptimizerCatLocator
        self.setStyleSheet(
            "QWidget { background:#0a0a18; }"
            "QLabel { color:#bbb; }"
            "QTableWidget { background:#101023; color:#ddd; border:1px solid #26264a; }"
            "QHeaderView::section { background:#151532; color:#7d8bb0; border:none; padding:4px; font-weight:bold; }"
            "QPushButton { background:#1a1a32; color:#aaa; border:1px solid #2a2a4a; "
            "border-radius:4px; padding:6px 12px; font-size:11px; }"
            "QPushButton:hover { background:#252545; color:#ddd; }"
            "QSpinBox, QDoubleSpinBox { background:#0d0d1c; color:#ccc; border:1px solid #2a2a4a; "
            "border-radius:4px; padding:3px 6px; }"
        )
        self._cats: list[Cat] = []
        self._excluded_keys: set[int] = set()
        self._cache: Optional[BreedingCache] = None
        self._mutation_planner_view: Optional['MutationDisorderPlannerView'] = None
        self._mutation_planner_traits: list[dict] = []
        self._pending_stage_context: Optional[str] = None
        self._save_path: Optional[str] = None
        self._session_state: dict = _load_planner_state_value("perfect_planner_state", {})
        self._restoring_session_state = False
        self._import_mutation_btn: Optional[QPushButton] = None

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(12)

        header = QHBoxLayout()
        self._title = QLabel(_tr("perfect_planner.title"))
        self._title.setStyleSheet("color:#ddd; font-size:18px; font-weight:bold;")
        self._summary = QLabel("")
        self._summary.setStyleSheet("color:#666; font-size:11px;")
        header.addWidget(self._title)
        header.addStretch()
        header.addWidget(self._summary)
        self._loading_bar = QProgressBar()
        self._loading_bar.setRange(0, 0)
        self._loading_bar.setFixedWidth(110)
        self._loading_bar.setFixedHeight(10)
        self._loading_bar.setTextVisible(False)
        self._loading_bar.setStyleSheet(
            "QProgressBar { background:#151527; border:1px solid #2a2a4a; border-radius:4px; }"
            "QProgressBar::chunk { background:#4a86c5; border-radius:4px; }"
        )
        self._loading_bar.hide()
        header.addWidget(self._loading_bar)
        root.addLayout(header)

        self._desc = QLabel()
        self._desc.setWordWrap(True)
        self._desc.setStyleSheet("color:#8d8da8; font-size:11px;")
        root.addWidget(self._desc)

        controls_wrap = QScrollArea()
        controls_wrap.setWidgetResizable(True)
        controls_wrap.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        controls_wrap.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        controls_wrap.setFrameShape(QFrame.NoFrame)
        controls_wrap.setStyleSheet("QScrollArea { border:none; background:transparent; }")
        controls_box = QWidget()
        controls = QHBoxLayout(controls_box)
        controls.setSpacing(8)
        controls.setContentsMargins(0, 0, 0, 0)

        self._min_stats_label = QLabel(_tr("perfect_planner.min_stats"))
        self._min_stats_label.setStyleSheet("color:#888; font-size:11px;")
        controls.addWidget(self._min_stats_label)

        self._min_stats_input = QLineEdit()
        self._min_stats_input.setPlaceholderText(_tr("perfect_planner.placeholder.min_stats"))
        self._min_stats_input.setFixedWidth(60)
        self._min_stats_input.setStyleSheet(
            "QLineEdit { background:#0d0d1c; color:#ccc; border:1px solid #2a2a4a;"
            " border-radius:4px; padding:4px 8px; }"
        )
        self._min_stats_input.textChanged.connect(lambda _: self._save_session_state())
        controls.addWidget(self._min_stats_input)

        controls.addSpacing(12)

        self._max_risk_label = QLabel(_tr("perfect_planner.max_risk"))
        self._max_risk_label.setStyleSheet("color:#888; font-size:11px;")
        controls.addWidget(self._max_risk_label)

        self._max_risk_input = QLineEdit()
        self._max_risk_input.setPlaceholderText(_tr("perfect_planner.placeholder.max_risk"))
        self._max_risk_input.setFixedWidth(60)
        self._max_risk_input.setStyleSheet(
            "QLineEdit { background:#0d0d1c; color:#ccc; border:1px solid #2a2a4a;"
            " border-radius:4px; padding:4px 8px; }"
        )
        self._max_risk_input.textChanged.connect(lambda _: self._save_session_state())
        controls.addWidget(self._max_risk_input)

        controls.addSpacing(12)

        self._min_compat_label = QLabel(_tr(
            "perfect_planner.min_compat", default="Min breed %"
        ))
        self._min_compat_label.setStyleSheet("color:#888; font-size:11px;")
        controls.addWidget(self._min_compat_label)

        self._min_compat_input = QSpinBox()
        self._min_compat_input.setRange(0, 100)
        self._min_compat_input.setValue(15)
        self._min_compat_input.setSuffix("%")
        self._min_compat_input.setFixedWidth(70)
        self._min_compat_input.setToolTip(_tr(
            "perfect_planner.min_compat_tooltip",
            default=(
                "Minimum per-roll breeding compatibility to keep a pair "
                "(wiki formula: 0.15 × CHA × libido × lover × sexuality).  "
                "The game auto-rejects pairs below 5%.  Default 15% filters "
                "out weak pairs while keeping reasonable options."
            ),
        ))
        self._min_compat_input.valueChanged.connect(lambda _: self._save_session_state())
        controls.addWidget(self._min_compat_input)

        controls.addSpacing(12)

        self._starter_label = QLabel(_tr("perfect_planner.start_pairs"))
        self._starter_label.setStyleSheet("color:#888; font-size:11px;")
        controls.addWidget(self._starter_label)
        self._starter_pairs_input = QSpinBox()
        self._starter_pairs_input.setRange(1, 12)
        self._starter_pairs_input.setValue(4)
        self._starter_pairs_input.setFixedWidth(60)
        self._starter_pairs_input.setToolTip(_tr("perfect_planner.start_pairs_tooltip"))
        self._starter_pairs_input.valueChanged.connect(lambda _: self._save_session_state())
        controls.addWidget(self._starter_pairs_input)

        controls.addSpacing(12)

        self._stimulation_label = QLabel(_tr("perfect_planner.stimulation"))
        self._stimulation_label.setStyleSheet("color:#888; font-size:11px;")
        controls.addWidget(self._stimulation_label)
        self._stimulation_input = QSpinBox()
        self._stimulation_input.setRange(0, 200)
        self._stimulation_input.setValue(50)
        self._stimulation_input.setFixedWidth(70)
        self._stimulation_input.setToolTip(_tr("perfect_planner.stimulation_tooltip"))
        self._stimulation_input.valueChanged.connect(lambda _: self._save_session_state())
        controls.addWidget(self._stimulation_input)

        controls.addSpacing(12)

        self._plan_btn = QPushButton(_tr("perfect_planner.build_plan"))
        self._plan_btn.setStyleSheet(
            "QPushButton { background:#1f5f4a; color:#f2f7f3; border:1px solid #3f8f72; "
            "border-radius:4px; padding:6px 14px; font-size:11px; font-weight:bold; }"
            "QPushButton:hover { background:#26735a; }"
            "QPushButton:pressed { background:#184b3a; }"
        )
        self._plan_btn.clicked.connect(self._calculate_plan)
        controls.addWidget(self._plan_btn)

        controls.addSpacing(12)

        self._deep_optimize_btn = QPushButton()
        self._deep_optimize_btn.setCheckable(True)
        self._deep_optimize_btn.setChecked(_saved_optimizer_flag("perfect_planner_use_sa", False))
        self._deep_optimize_btn.setToolTip(_tr("perfect_planner.more_depth_tooltip", default="Use simulated annealing for a slower, deeper search."))
        self._deep_optimize_btn.setStyleSheet(
            "QPushButton { background:#2a2a5a; color:#bbbbee; border:1px solid #4a4a8a; "
            "border-radius:4px; padding:6px 12px; font-size:11px; font-weight:bold; }"
            "QPushButton:hover { background:#3a3a6a; color:#ddd; }"
            "QPushButton:checked { background:#4a4a7a; color:#f0f0ff; border-color:#6a6a9a; }"
            "QPushButton:pressed { background:#202048; }"
            "QPushButton:disabled { background:#1a1a32; color:#555; border-color:#2a2a4a; }"
        )
        self._bind_persistent_toggle(
            self._deep_optimize_btn,
            "perfect_planner.more_depth",
            "perfect_planner_use_sa",
            default="More Depth",
        )
        self._deep_optimize_btn.toggled.connect(lambda _: self._save_session_state())
        controls.addWidget(self._deep_optimize_btn)

        self._import_mutation_btn = QPushButton(_tr(
            "perfect_planner.import_mutation.button",
            default="Import Mutation Planner",
        ))
        self._import_mutation_btn.setMinimumWidth(182)
        self._import_mutation_btn.clicked.connect(self._import_mutation_traits)
        controls.addWidget(self._import_mutation_btn)
        self._sync_mutation_import_button_state()

        self._avoid_lovers_checkbox = QPushButton()
        self._avoid_lovers_checkbox.setCheckable(True)
        self._avoid_lovers_checkbox.setChecked(_saved_optimizer_flag("perfect_planner_avoid_lovers", False))
        self._avoid_lovers_checkbox.setStyleSheet(
            "QPushButton { background:#1a1a32; color:#aaa; border:1px solid #2a2a4a; "
            "border-radius:4px; padding:6px 12px; font-size:11px; }"
            "QPushButton:checked { background:#5a3a2a; color:#ddd; border:1px solid #8a5a4a; }"
            "QPushButton:hover { background:#252545; color:#ddd; }"
        )
        self._bind_persistent_toggle(self._avoid_lovers_checkbox, "perfect_planner.toggle.avoid_lovers", "perfect_planner_avoid_lovers")
        self._avoid_lovers_checkbox.toggled.connect(lambda _: self._save_session_state())
        controls.addWidget(self._avoid_lovers_checkbox)

        self._prefer_low_aggression_checkbox = QPushButton()
        self._prefer_low_aggression_checkbox.setCheckable(True)
        self._prefer_low_aggression_checkbox.setChecked(_saved_optimizer_flag("prefer_low_aggression", True))
        self._prefer_low_aggression_checkbox.setStyleSheet(
            "QPushButton { background:#1a1a32; color:#aaa; border:1px solid #2a2a4a; "
            "border-radius:4px; padding:6px 12px; font-size:11px; }"
            "QPushButton:checked { background:#4a2a2a; color:#ddd; border:1px solid #7a4a4a; }"
            "QPushButton:hover { background:#252545; color:#ddd; }"
        )
        self._bind_persistent_toggle(
            self._prefer_low_aggression_checkbox,
            "perfect_planner.toggle.prefer_low_aggression",
            "prefer_low_aggression",
        )
        self._prefer_low_aggression_checkbox.toggled.connect(lambda _: self._save_session_state())
        controls.addWidget(self._prefer_low_aggression_checkbox)

        self._prefer_high_libido_checkbox = QPushButton()
        self._prefer_high_libido_checkbox.setCheckable(True)
        self._prefer_high_libido_checkbox.setChecked(_saved_optimizer_flag("prefer_high_libido", True))
        self._prefer_high_libido_checkbox.setStyleSheet(
            "QPushButton { background:#1a1a32; color:#aaa; border:1px solid #2a2a4a; "
            "border-radius:4px; padding:6px 12px; font-size:11px; }"
            "QPushButton:checked { background:#2a4a36; color:#ddd; border:1px solid #4a7a5a; }"
            "QPushButton:hover { background:#252545; color:#ddd; }"
        )
        self._bind_persistent_toggle(
            self._prefer_high_libido_checkbox,
            "perfect_planner.toggle.prefer_high_libido",
            "prefer_high_libido",
        )
        self._prefer_high_libido_checkbox.toggled.connect(lambda _: self._save_session_state())
        controls.addWidget(self._prefer_high_libido_checkbox)

        controls.addStretch()

        controls_wrap.setWidget(controls_box)
        root.addWidget(controls_wrap)

        self._splitter = QSplitter(Qt.Vertical)
        self._splitter.setObjectName("perfect_planner_main_splitter")
        self._splitter.setStyleSheet("QSplitter::handle:vertical { background:#1e1e38; }")
        self._selected_stage_row = 0
        self._plan_refresh_timer = QTimer(self)
        self._plan_refresh_timer.setSingleShot(True)
        self._plan_refresh_timer.timeout.connect(self._calculate_plan)

        self._table = QTableWidget(0, 6)
        self._table.setIconSize(QSize(60, 20))
        self._table.setHorizontalHeaderLabels([
            _tr("perfect_planner.table.stage"),
            _tr("perfect_planner.table.goal"),
            _tr("perfect_planner.table.pairs"),
            _tr("perfect_planner.table.coverage"),
            _tr("perfect_planner.table.risk"),
            _tr("perfect_planner.table.details"),
        ])
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionMode(QAbstractItemView.SingleSelection)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        hh = self._table.horizontalHeader()
        hh.setStretchLastSection(False)
        hh.setSectionResizeMode(0, QHeaderView.Interactive)
        hh.setSectionResizeMode(1, QHeaderView.Interactive)
        hh.setSectionResizeMode(2, QHeaderView.Interactive)
        hh.setSectionResizeMode(3, QHeaderView.Interactive)
        hh.setSectionResizeMode(4, QHeaderView.Interactive)
        hh.setSectionResizeMode(5, QHeaderView.Interactive)
        self._table.setColumnWidth(0, 100)
        self._table.setColumnWidth(1, 260)
        self._table.setColumnWidth(2, 60)
        self._table.setColumnWidth(3, 60)
        self._table.setColumnWidth(4, 70)
        self._table.setColumnWidth(5, 400)
        self._table.itemSelectionChanged.connect(self._on_table_selection_changed)
        self._table.cellClicked.connect(self._on_stage_cell_clicked)
        self._splitter.addWidget(self._table)

        self._details_pane = PerfectPlannerDetailPanel()
        self._details_pane.setMinimumWidth(500)
        self._detail_actions_header = self._details_pane._actions_table.horizontalHeader()
        self._detail_actions_header.sectionResized.connect(lambda *_: self._save_session_state())
        self._detail_actions_header.sectionMoved.connect(lambda *_: self._save_session_state())
        self._detail_actions_header.sortIndicatorChanged.connect(lambda *_: self._save_session_state())
        self._bottom_splitter = QSplitter(Qt.Horizontal)
        self._bottom_splitter.setObjectName("perfect_planner_bottom_splitter")
        self._bottom_splitter.setStyleSheet("QSplitter::handle:horizontal { background:#1e1e38; }")
        self._bottom_splitter.setChildrenCollapsible(False)
        self._bottom_splitter.addWidget(self._details_pane)

        self._bottom_tabs = QTabWidget()
        self._bottom_tabs.setStyleSheet(
            "QTabWidget::pane { border:1px solid #1e1e38; background:#0a0a18; }"
            "QTabBar::tab { background:#14142a; color:#888; padding:6px 14px; border:1px solid #1e1e38;"
            " border-bottom:none; margin-right:2px; font-size:11px; }"
            "QTabBar::tab:selected { background:#1a1a36; color:#ddd; font-weight:bold; }"
            "QTabBar::tab:hover { background:#1e1e3a; color:#bbb; }"
        )

        self._guide_panel = PerfectPlannerGuidePanel()
        self._bottom_tabs.addTab(self._guide_panel, _tr("perfect_planner.tab.planner_guide", default="Planner Guide"))

        self._foundation_panel = PerfectPlannerFoundationPairsPanel()
        self._foundation_panel.configChanged.connect(self._request_plan_refresh)
        self._bottom_tabs.addTab(self._foundation_panel, _tr("perfect_planner.tab.foundation_pairs", default="Foundation Pairs"))

        self._offspring_tracker = PerfectPlannerOffspringTracker()
        self._offspring_tracker._select_offspring_callback = self._on_offspring_selected
        self._bottom_tabs.addTab(
            self._offspring_tracker,
            _tr("perfect_planner.tab.offspring_tracker", default="Offspring Tracker"),
        )
        self._cat_locator = RoomOptimizerCatLocator()
        self._bottom_tabs.addTab(self._cat_locator, _tr("perfect_planner.tab.cat_locator"))
        self._bottom_tabs.setCurrentIndex(0)

        self._bottom_splitter.addWidget(self._bottom_tabs)
        self._bottom_splitter.setStretchFactor(0, 3)
        self._bottom_splitter.setStretchFactor(1, 2)
        self._bottom_splitter.setSizes([760, 520])
        self._splitter.addWidget(self._bottom_splitter)
        self._splitter.setSizes([200, 520])
        self._splitter.splitterMoved.connect(lambda *_: self._save_session_state())
        self._bottom_splitter.splitterMoved.connect(lambda *_: self._save_session_state())
        root.addWidget(self._splitter, 1)

        self.retranslate_ui()
        PerfectCatPlannerView._restore_session_state(self)
        _enforce_min_font_in_widget_tree(self)

    def set_loading_state(self, loading: bool):
        self._loading_bar.setVisible(bool(loading))

    def retranslate_ui(self):
        self._title.setText(_tr("perfect_planner.title"))
        self._desc.setText(_tr("perfect_planner.description"))
        self._min_stats_label.setText(_tr("perfect_planner.min_stats"))
        self._min_stats_input.setPlaceholderText(_tr("perfect_planner.placeholder.min_stats"))
        self._max_risk_label.setText(_tr("perfect_planner.max_risk"))
        self._max_risk_input.setPlaceholderText(_tr("perfect_planner.placeholder.max_risk"))
        self._starter_label.setText(_tr("perfect_planner.start_pairs"))
        self._starter_pairs_input.setToolTip(_tr("perfect_planner.start_pairs_tooltip"))
        self._stimulation_label.setText(_tr("perfect_planner.stimulation"))
        self._stimulation_input.setToolTip(_tr("perfect_planner.stimulation_tooltip"))
        self._plan_btn.setText(_tr("perfect_planner.build_plan"))
        self._import_mutation_btn.setText(_tr(
            "perfect_planner.import_mutation.button",
            default="Import Mutation Planner",
        ))
        self._set_toggle_button_label(self._deep_optimize_btn, _tr("perfect_planner.more_depth", default="More Depth"))
        self._deep_optimize_btn.setToolTip(_tr("perfect_planner.more_depth_tooltip", default="Use simulated annealing for a slower, deeper search."))
        self._sync_mutation_import_button_state()
        self._table.setHorizontalHeaderLabels([
            _tr("perfect_planner.table.stage"),
            _tr("perfect_planner.table.goal"),
            _tr("perfect_planner.table.pairs"),
            _tr("perfect_planner.table.coverage"),
            _tr("perfect_planner.table.risk"),
            _tr("perfect_planner.table.details"),
        ])
        self._bottom_tabs.setTabText(0, _tr("perfect_planner.tab.planner_guide", default="Planner Guide"))
        self._bottom_tabs.setTabText(1, _tr("perfect_planner.tab.foundation_pairs", default="Foundation Pairs"))
        self._bottom_tabs.setTabText(2, _tr("perfect_planner.tab.offspring_tracker", default="Offspring Tracker"))
        self._bottom_tabs.setTabText(3, _tr("perfect_planner.tab.cat_locator"))
        self._guide_panel.retranslate_ui()
        self._foundation_panel.retranslate_ui()
        self._set_toggle_button_label(self._avoid_lovers_checkbox, _tr("perfect_planner.toggle.avoid_lovers"))
        self._set_toggle_button_label(self._prefer_low_aggression_checkbox, _tr("perfect_planner.toggle.prefer_low_aggression"))
        self._set_toggle_button_label(self._prefer_high_libido_checkbox, _tr("perfect_planner.toggle.prefer_high_libido"))
        self._details_pane.retranslate_ui()
        self._details_pane.show_stage(None)
        self._cat_locator.retranslate_ui()
        self._offspring_tracker.retranslate_ui()

    def _request_plan_refresh(self):
        if not self._cats:
            return
        self._plan_refresh_timer.start(80)

    def _stage_data_for_row(self, row: int) -> Optional[dict]:
        if not (0 <= row < self._table.rowCount()):
            return None
        stage_item = self._table.item(row, 0)
        if stage_item is None:
            return None
        data = stage_item.data(Qt.UserRole)
        return data if isinstance(data, dict) else None

    def _show_stage_row(self, row: int, context_note: Optional[str] = None):
        data = self._stage_data_for_row(row)
        if isinstance(data, dict):
            self._details_pane.show_stage(data, context_note=context_note)
        else:
            self._details_pane.show_stage(None)

    def _on_table_selection_changed(self):
        selected_ranges = self._table.selectedRanges()
        if not selected_ranges:
            self._details_pane.show_stage(None)
            self._pending_stage_context = None
            return
        row = selected_ranges[0].topRow()
        self._selected_stage_row = row
        self._show_stage_row(row, context_note=self._pending_stage_context)
        self._pending_stage_context = None

    def _on_stage_cell_clicked(self, row: int, column: int):
        if not (0 <= row < self._table.rowCount()):
            return
        self._selected_stage_row = row
        self._table.selectRow(row)
        self._show_stage_row(row, context_note=self._pending_stage_context)
        self._pending_stage_context = None

    def _on_offspring_selected(self, row: dict):
        if not row:
            return
        if self._table.rowCount() <= 0:
            return
        pair_row = row.get("pair", row)
        children = pair_row.get("known_offspring", [])
        if children:
            offspring_names = ", ".join(child.name for child in children[:3])
            if len(children) > 3:
                offspring_names += f" +{len(children) - 3} more"
        else:
            offspring_names = "No tracked offspring"
        selected_child = row.get("child")
        selected_child_text = f" | Selected: {selected_child.name}" if selected_child is not None else ""
        context_note = (
            f"Selected offspring pair: {pair_row['cat_a'].name} x {pair_row['cat_b'].name}"
            f"{selected_child_text} | "
            f"Offspring: {offspring_names}"
        )
        self._pending_stage_context = context_note
        self._show_stage_row(self._selected_stage_row, context_note=context_note)
        self._request_plan_refresh()

    @property
    def cat_locator(self):
        return self._cat_locator

    @property
    def offspring_tracker(self):
        return self._offspring_tracker

    def sync_mutation_traits(self):
        self._sync_mutation_traits()

    def sync_mutation_import_button_state(self):
        self._sync_mutation_import_button_state()

    def save_session_state(self, **kwargs):
        self._save_session_state(**kwargs)

    def set_cats(self, cats: list[Cat], excluded_keys: set[int] = None):
        self.set_loading_state(True)
        try:
            self._cats = cats
            blacklisted_keys = {c.db_key for c in cats if c.is_blacklisted}
            self._excluded_keys = (excluded_keys or set()) | blacklisted_keys
            alive_count = len([c for c in cats if c.status != "Gone"])
            excluded_count = len([c for c in cats if c.status != "Gone" and c.db_key in self._excluded_keys])
            if excluded_count > 0:
                self._summary.setText(_tr("perfect_planner.summary.with_excluded", alive=alive_count, excluded=excluded_count))
            else:
                self._summary.setText(_tr("perfect_planner.summary.no_excluded", alive=alive_count))
            self._sync_mutation_traits()
            self._foundation_panel.set_cats([c for c in cats if c.status != "Gone" and c.db_key not in self._excluded_keys])
            if self._session_state.get("has_run") and len([c for c in cats if c.status != "Gone" and c.db_key not in self._excluded_keys]) >= 2:
                cached = self._load_cached_results()
                if cached is not None:
                    self._restore_from_cached_results(cached)
                else:
                    self._calculate_plan()
        finally:
            self.set_loading_state(False)

    def set_cache(self, cache: Optional[BreedingCache]):
        self._cache = cache

    def sync_from_room_config(self, room_config: list[dict], available_rooms: list[str] | None = None):
        room_configs = build_room_configs(room_config, available_rooms=available_rooms)
        if not room_configs:
            return

        stim = best_breeding_room_stimulation(room_configs, fallback=float(self._stimulation_input.value() or 50))
        stim_value = max(0, min(200, int(round(float(stim)))))

        self._stimulation_input.blockSignals(True)
        try:
            self._stimulation_input.setValue(stim_value)
        finally:
            self._stimulation_input.blockSignals(False)
        self._stimulation_input.setToolTip(
            f"{_tr('perfect_planner.stimulation_tooltip')} Current room default: {stim_value}"
        )

        self._save_session_state()

    def set_mutation_planner_view(self, planner: Optional['MutationDisorderPlannerView']):
        if self._mutation_planner_view is not None and hasattr(self._mutation_planner_view, "traitsChanged"):
            try:
                self._mutation_planner_view.traitsChanged.disconnect(self._on_mutation_traits_changed)
            except (TypeError, RuntimeError):
                pass
        self._mutation_planner_view = planner
        if self._mutation_planner_view is not None and hasattr(self._mutation_planner_view, "traitsChanged"):
            try:
                self._mutation_planner_view.traitsChanged.connect(self._on_mutation_traits_changed)
            except (TypeError, RuntimeError):
                pass
        self._sync_mutation_traits()
        self._sync_mutation_import_button_state()
        if self.isVisible() and self._cats:
            self._request_plan_refresh()

    def set_save_path(self, save_path: Optional[str], *, refresh_existing: bool = True):
        self._save_path = save_path
        self._foundation_panel.set_save_path(save_path, refresh_existing=refresh_existing)
        self._offspring_tracker.set_save_path(save_path, refresh_existing=refresh_existing)
        if refresh_existing and self._cats:
            self.set_cats(self._cats, self._excluded_keys)
            return
        self._restore_session_state()
        self._sync_mutation_traits()
        self._sync_mutation_import_button_state()

    def _sync_mutation_traits(self) -> bool:
        if self._mutation_planner_view is None:
            traits = []
        elif hasattr(self._mutation_planner_view, "get_traits_for_mode"):
            try:
                traits = self._mutation_planner_view.get_traits_for_mode("best_pairs")
            except Exception:
                traits = self._mutation_planner_view.get_selected_traits()
        else:
            traits = self._mutation_planner_view.get_selected_traits()
        normalized = [dict(t) for t in traits]
        if normalized == self._mutation_planner_traits:
            return False
        self._mutation_planner_traits = normalized
        return True

    def _mutation_import_button_label(self) -> str:
        if not self._mutation_planner_traits:
            return _tr("perfect_planner.import_mutation.none", default="No Best Pairs Mutations Imported")
        summary = _planner_import_traits_summary(self._mutation_planner_traits)
        return _tr("perfect_planner.import_mutation.imported", summary=summary, default=f"Best Pairs: {summary}")

    def _mutation_import_button_tooltip(self) -> str:
        tooltip = _planner_import_traits_tooltip(
            self._mutation_planner_traits,
            empty_text=_tr(
                "perfect_planner.import_mutation.tooltip_empty",
                default="Select Best Pairs traits in the mutation planner first.",
            ),
        )
        if self._mutation_planner_traits:
            return _tr(
                "perfect_planner.import_mutation.tooltip_prefix",
                default="Best Pairs mutations:\n{tooltip}",
            ).format(tooltip=tooltip)
        return tooltip

    def _on_mutation_traits_changed(self):
        changed = self._sync_mutation_traits()
        self._sync_mutation_import_button_state()
        if changed and self.isVisible():
            self._request_plan_refresh()

    def _sync_mutation_import_button_state(self):
        if self._import_mutation_btn is None:
            return
        active = bool(self._mutation_planner_traits)
        self._import_mutation_btn.setText(self._mutation_import_button_label())
        # Deferred import to avoid circular dependency
        from mewgenics.views.room_optimizer import RoomOptimizerView
        RoomOptimizerView._style_import_planner_button(self._import_mutation_btn, active=active)
        self._import_mutation_btn.setEnabled(True)
        self._import_mutation_btn.setToolTip(self._mutation_import_button_tooltip())

    def _import_mutation_traits(self):
        if not self._sync_mutation_traits():
            # Even if nothing changed, the user explicitly requested a refresh.
            pass
        if not self._mutation_planner_traits:
            return
        self._sync_mutation_import_button_state()
        self._request_plan_refresh()

    def _session_state_payload(self, *, has_run: Optional[bool] = None) -> dict:
        state = dict(self._session_state) if isinstance(self._session_state, dict) else {}
        actions_table_header_state = ""
        try:
            actions_table_header_state = self._details_pane._actions_table.horizontalHeader().saveState().toBase64().data().decode("ascii")
        except Exception:
            actions_table_header_state = ""
        state.update({
            "min_stats": self._min_stats_input.text().strip(),
            "max_risk": self._max_risk_input.text().strip(),
            "min_compat_pct": int(self._min_compat_input.value()),
            "starter_pairs": int(self._starter_pairs_input.value()),
            "stimulation": int(self._stimulation_input.value()),
            "use_sa": bool(self._deep_optimize_btn.isChecked()),
            "avoid_lovers": bool(self._avoid_lovers_checkbox.isChecked()),
            "prefer_low_aggression": bool(self._prefer_low_aggression_checkbox.isChecked()),
            "prefer_high_libido": bool(self._prefer_high_libido_checkbox.isChecked()),
            "splitter_sizes": list(self._splitter.sizes()) if hasattr(self, "_splitter") else [],
            "bottom_splitter_sizes": list(self._bottom_splitter.sizes()) if hasattr(self, "_bottom_splitter") else [],
            "actions_table_header_state": actions_table_header_state,
        })
        if has_run is not None:
            state["has_run"] = bool(has_run)
        else:
            state["has_run"] = bool(state.get("has_run", False))
        return state

    def _save_session_state(self, *, has_run: Optional[bool] = None):
        if getattr(self, "_restoring_session_state", False):
            return
        self._session_state = self._session_state_payload(has_run=has_run)
        _save_planner_state_value("perfect_planner_state", self._session_state, self._save_path)

    # ── Result caching ──────────────────────────────────────────────────

    @staticmethod
    def _cats_fingerprint(cats: list[Cat]) -> str:
        return _active_cat_fingerprint(cats)

    def _save_cached_results(self, stage_rows: list[dict], tracker_rows: list[dict],
                              locator_data: list[dict], summary_text: str):
        if not self._save_path:
            return
        # Build serializable stage data (strip Cat objects from actions)
        serializable_stages = []
        for stage in stage_rows:
            s = {
                "stage": stage.get("stage", ""),
                "goal": stage.get("goal", ""),
                "pairs": stage.get("pairs", 0),
                "coverage": stage.get("coverage", 0.0),
                "risk": stage.get("risk", 0.0),
                "mutation_ratio": stage.get("mutation_ratio", 0.0),
                "details": stage.get("details", ""),
                "summary": stage.get("summary", ""),
                "notes": stage.get("notes", []),
            }
            # Save lightweight action summaries (no Cat objects)
            actions = []
            for action in stage.get("actions", []):
                a = {
                    "action": action.get("action", ""),
                    "target": action.get("target", ""),
                    "risk": action.get("risk"),
                    "why": action.get("why", ""),
                    "children": action.get("children", ""),
                    "rotate": action.get("rotate", ""),
                    "coverage_value": action.get("coverage_value", 0.0),
                    "target_grid": action.get("target_grid"),
                    "detail_projection": action.get("detail_projection"),
                    "mutation_summary": action.get("mutation_summary"),
                }
                # Replace Cat objects in parents with identifiers
                parents = action.get("parents", [])
                a["parent_keys"] = [
                    {"db_key": p.db_key, "name": p.name, "gender_display": p.gender_display}
                    if hasattr(p, "db_key") else p
                    for p in parents
                ]
                actions.append(a)
            s["actions"] = actions
            serializable_stages.append(s)

        # Serialize tracker rows (replace Cat objects with db_keys)
        serializable_tracker = []
        for row in tracker_rows:
            r = {
                "pair_index": row.get("pair_index"),
                "cat_a_key": row["cat_a"].db_key if hasattr(row.get("cat_a"), "db_key") else None,
                "cat_b_key": row["cat_b"].db_key if hasattr(row.get("cat_b"), "db_key") else None,
                "cat_a_name": f"{row['cat_a'].name} ({row['cat_a'].gender_display})" if hasattr(row.get("cat_a"), "name") else "",
                "cat_b_name": f"{row['cat_b'].name} ({row['cat_b'].gender_display})" if hasattr(row.get("cat_b"), "name") else "",
                "projection": row.get("projection"),
                "risk": row.get("risk", 0.0),
                "coi": row.get("coi", 0.0),
                "shared": row.get("shared", (0, 0)),
                "source": row.get("source", "suggested"),
                "slot_index": row.get("slot_index"),
                "offspring_keys": [c.db_key for c in row.get("known_offspring", []) if hasattr(c, "db_key")],
            }
            serializable_tracker.append(r)

        payload = {
            "fingerprint": self._cats_fingerprint(self._cats),
            "stage_rows": serializable_stages,
            "tracker_rows": serializable_tracker,
            "locator_data": locator_data,
            "summary_text": summary_text,
        }
        _save_planner_state_value("perfect_planner_results", payload, self._save_path)

    def _load_cached_results(self) -> Optional[dict]:
        if not self._save_path or not self._cats:
            return None
        payload = _load_planner_state_value("perfect_planner_results", None, self._save_path)
        if not isinstance(payload, dict):
            return None
        if payload.get("fingerprint") != self._cats_fingerprint(self._cats):
            return None
        if not payload.get("stage_rows"):
            return None
        return payload

    def _restore_from_cached_results(self, cached: dict):
        """Repopulate tables from a cached result snapshot."""
        stage_rows = cached.get("stage_rows", [])
        tracker_rows = cached.get("tracker_rows", [])
        locator_data = cached.get("locator_data", [])
        summary_text = cached.get("summary_text", "")

        # Repopulate stage table
        self._table.setRowCount(0)
        self._details_pane.show_stage(None)

        cat_lookup = {c.db_key: c for c in self._cats}

        for row_idx, stage in enumerate(stage_rows):
            self._table.insertRow(row_idx)

            # Restore Cat objects in actions where possible
            for action in stage.get("actions", []):
                parent_keys = action.pop("parent_keys", [])
                parents = []
                for pk in parent_keys:
                    if isinstance(pk, dict) and "db_key" in pk:
                        cat = cat_lookup.get(pk["db_key"])
                        if cat is not None:
                            parents.append(cat)
                action["parents"] = parents

            stage_item = QTableWidgetItem(stage["stage"])
            stage_item.setData(Qt.UserRole, stage)
            stage_item.setTextAlignment(Qt.AlignCenter)

            goal_item = QTableWidgetItem(stage["goal"])
            pair_item = QTableWidgetItem(str(stage["pairs"]))
            pair_item.setTextAlignment(Qt.AlignCenter)

            coverage_value = float(stage["coverage"])
            coverage_item = QTableWidgetItem(f"{coverage_value:.1f}/7")
            coverage_item.setTextAlignment(Qt.AlignCenter)
            if coverage_value >= 6.0:
                coverage_item.setForeground(QBrush(QColor(98, 194, 135)))
            elif coverage_value >= 4.5:
                coverage_item.setForeground(QBrush(QColor(216, 181, 106)))
            else:
                coverage_item.setForeground(QBrush(QColor(190, 145, 40)))

            risk_value = float(stage["risk"])
            risk_item = QTableWidgetItem(f"{risk_value:.0f}%")
            risk_item.setTextAlignment(Qt.AlignCenter)
            if risk_value >= 20:
                risk_item.setForeground(QBrush(QColor(217, 119, 119)))
            elif risk_value > 0:
                risk_item.setForeground(QBrush(QColor(216, 181, 106)))
            else:
                risk_item.setForeground(QBrush(QColor(98, 194, 135)))

            details_item = QTableWidgetItem(stage["details"])
            mutation_ratio = float(stage.get("mutation_ratio", 0.0))
            if abs(mutation_ratio) > 1e-6:
                mutation_color = _planner_trait_color(mutation_ratio)
                mutation_color.setAlpha(85)
                stage_item.setBackground(QBrush(mutation_color))

            self._table.setItem(row_idx, 0, stage_item)
            self._table.setItem(row_idx, 1, goal_item)
            self._table.setItem(row_idx, 2, pair_item)
            self._table.setItem(row_idx, 3, coverage_item)
            self._table.setItem(row_idx, 4, risk_item)
            self._table.setItem(row_idx, 5, details_item)

        # Restore tracker rows (re-resolve Cat objects)
        restored_tracker: list[dict] = []
        for row in tracker_rows:
            cat_a = cat_lookup.get(row.get("cat_a_key"))
            cat_b = cat_lookup.get(row.get("cat_b_key"))
            if cat_a is None or cat_b is None:
                continue
            offspring = [cat_lookup[k] for k in row.get("offspring_keys", []) if k in cat_lookup]
            restored_tracker.append({
                "pair_index": row.get("pair_index", 0),
                "cat_a": cat_a,
                "cat_b": cat_b,
                "known_offspring": offspring,
                "projection": row.get("projection", {}),
                "risk": row.get("risk", 0.0),
                "coi": row.get("coi", 0.0),
                "shared": tuple(row.get("shared", (0, 0))),
                "source": row.get("source", "suggested"),
                "slot_index": row.get("slot_index"),
            })
        self._offspring_tracker.set_rows(restored_tracker)

        # Restore cat locator
        if locator_data:
            self._cat_locator.show_assignments(locator_data)

        # Restore summary
        if summary_text:
            self._summary.setText(summary_text)

        # Select first stage row
        if stage_rows:
            self._selected_stage_row = 0
            self._table.selectRow(0)
            self._show_stage_row(0)

    def _restore_session_state(self):
        state = _load_planner_state_value("perfect_planner_state", {}, self._save_path)
        if not isinstance(state, dict):
            state = {}
        self._session_state = state
        self._restoring_session_state = True
        try:
            self._min_stats_input.setText(str(state.get("min_stats", "") or ""))
            self._max_risk_input.setText(str(state.get("max_risk", "") or ""))
            self._min_compat_input.setValue(int(state.get("min_compat_pct", 15) or 0))
            self._starter_pairs_input.setValue(int(state.get("starter_pairs", 4) or 4))
            self._stimulation_input.setValue(int(state.get("stimulation", 50) or 50))
            self._deep_optimize_btn.setChecked(bool(state.get("use_sa", False)))
            self._avoid_lovers_checkbox.setChecked(bool(state.get("avoid_lovers", False)))
            self._prefer_low_aggression_checkbox.setChecked(bool(state.get("prefer_low_aggression", True)))
            self._prefer_high_libido_checkbox.setChecked(bool(state.get("prefer_high_libido", True)))
            splitter_sizes = state.get("splitter_sizes", [])
            if isinstance(splitter_sizes, list) and len(splitter_sizes) == 2:
                self._splitter.setSizes([
                    max(10, int(splitter_sizes[0] or 0)),
                    max(10, int(splitter_sizes[1] or 0)),
                ])
            bottom_splitter_sizes = state.get("bottom_splitter_sizes", [])
            if isinstance(bottom_splitter_sizes, list) and len(bottom_splitter_sizes) == 2:
                self._bottom_splitter.setSizes([
                    max(500, int(bottom_splitter_sizes[0] or 0)),
                    max(10, int(bottom_splitter_sizes[1] or 0)),
                ])
            actions_table_header_state = state.get("actions_table_header_state", "")
            if isinstance(actions_table_header_state, str) and actions_table_header_state:
                try:
                    self._details_pane._actions_table.horizontalHeader().restoreState(
                        QByteArray.fromBase64(actions_table_header_state.encode("ascii"))
                    )
                except Exception:
                    pass
        finally:
            self._restoring_session_state = False

    def reset_to_defaults(self):
        """Restore the perfect planner to its default layout and input state.

        Preserves foundation pairs and offspring selections so the user does
        not lose curated data.
        """
        self._session_state = {}
        self._restoring_session_state = True
        try:
            self._min_stats_input.setText("")
            self._max_risk_input.setText("")
            self._min_compat_input.setValue(15)
            self._starter_pairs_input.setValue(4)
            self._stimulation_input.setValue(50)
            self._deep_optimize_btn.setChecked(False)
            self._avoid_lovers_checkbox.setChecked(False)
            self._prefer_low_aggression_checkbox.setChecked(True)
            self._prefer_high_libido_checkbox.setChecked(True)
            self._splitter.setSizes([200, 520])
            self._bottom_splitter.setSizes([760, 520])
        finally:
            self._restoring_session_state = False
        self.retranslate_ui()
        self._save_session_state(has_run=False)

    def _run_sa_refinement(
        self,
        evaluated_pairs: list[dict],
        selected_pairs: list[dict],
        starter_pairs: int,
        sa_temperature: float,
        sa_neighbors: int,
    ) -> list[dict]:
        """Refine greedy pair picks using parallel simulated annealing."""
        if len(selected_pairs) < 2:
            return sorted(selected_pairs, key=lambda pair: pair["score"], reverse=True)

        pair_by_id = {pair["pair_index"]: pair for pair in evaluated_pairs}
        if len(pair_by_id) < 2:
            return sorted(selected_pairs, key=lambda pair: pair["score"], reverse=True)

        # Serialize pair data for subprocess workers (no Cat objects)
        serial_pairs = [
            {
                "pair_index": p["pair_index"],
                "cat_a_key": p["cat_a"].db_key,
                "cat_b_key": p["cat_b"].db_key,
                "score": p["score"],
            }
            for p in evaluated_pairs
        ]
        initial_ids = sorted(p["pair_index"] for p in selected_pairs)

        best_ids = run_parallel_p7p_sa(
            pair_data=serial_pairs,
            initial_ids=initial_ids,
            starter_pairs=starter_pairs,
            sa_temperature=sa_temperature,
            sa_neighbors=sa_neighbors,
        )

        refined = [pair_by_id[pid] for pid in best_ids if pid in pair_by_id]
        refined.sort(key=lambda pair: pair["score"], reverse=True)
        return refined

    def _calculate_plan(self):
        self._save_session_state(has_run=True)
        excluded_keys = getattr(self, "_excluded_keys", set())
        alive_cats = [c for c in self._cats if c.status != "Gone" and c.db_key not in excluded_keys]
        excluded_cats = [c for c in self._cats if c.status != "Gone" and c.db_key in excluded_keys]

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

        min_compat = max(0.0, min(1.0, float(self._min_compat_input.value()) / 100.0))

        starter_pairs = int(self._starter_pairs_input.value())
        stimulation = float(self._stimulation_input.value())
        sa_temperature = _saved_optimizer_search_temperature()
        sa_neighbors = _saved_optimizer_search_neighbors()
        use_sa = self._deep_optimize_btn.isChecked()
        avoid_lovers = self._avoid_lovers_checkbox.isChecked()
        prefer_low_aggression = self._prefer_low_aggression_checkbox.isChecked()
        prefer_high_libido = self._prefer_high_libido_checkbox.isChecked()
        self._sync_mutation_traits()
        planner_traits = list(self._mutation_planner_traits)

        def _mutation_payload(cat_a: Cat, cat_b: Cat) -> dict:
            if not planner_traits:
                return {}
            return {
                "pair": _planner_trait_summary_for_pair(cat_a, cat_b, planner_traits),
                "parents": [
                    _planner_trait_summary_for_cat(cat_a, planner_traits),
                    _planner_trait_summary_for_cat(cat_b, planner_traits),
                ],
            }

        def _stage_mutation_ratio(actions: list[dict]) -> float:
            ratios: list[float] = []
            for action in actions:
                summary = action.get("mutation_summary") or {}
                pair_summary = summary.get("pair") if isinstance(summary, dict) else None
                if isinstance(pair_summary, dict):
                    ratios.append(float(pair_summary.get("ratio", 0.0)))
            return sum(ratios) / len(ratios) if ratios else 0.0

        if min_stats > 0:
            alive_cats = [c for c in alive_cats if sum(c.base_stats.values()) >= min_stats]

        if len(alive_cats) < 2:
            self._table.setRowCount(0)
            self._details_pane.show_stage(None)
            self._cat_locator.clear()
            self._offspring_tracker.clear()
            self._summary.setText(_tr("perfect_planner.status.not_enough_cats"))
            return

        stat_sum = {cat.db_key: sum(cat.base_stats.values()) for cat in alive_cats}
        cache = self._cache
        parent_key_map = {
            cat.db_key: {parent.db_key for parent in get_parents(cat)}
            for cat in alive_cats
        }
        hater_key_map = {
            cat.db_key: {other.db_key for other in getattr(cat, "haters", [])}
            for cat in alive_cats
        }
        lover_key_map = {
            cat.db_key: {other.db_key for other in getattr(cat, "lovers", [])}
            for cat in alive_cats
        }
        has_mutual_lover = {
            cat.db_key
            for cat in alive_cats
            if any(cat.db_key in lover_key_map.get(o.db_key, set()) for o in getattr(cat, "lovers", []))
        }
        lover_locked: set[int] = has_mutual_lover if avoid_lovers else set()
        pair_eval_cache: dict[tuple[int, int], tuple[bool, str, float]] = {}
        pair_factor_cache: dict[tuple[int, int, float], object] = {}

        def _pair_factor_key(cat_a: Cat, cat_b: Cat, stimulation_value: float) -> tuple[int, int, float]:
            a_key, b_key = cat_a.db_key, cat_b.db_key
            return (a_key, b_key, float(stimulation_value)) if a_key < b_key else (b_key, a_key, float(stimulation_value))

        def _score_pair_cached(cat_a: Cat, cat_b: Cat, stimulation_value: float):
            key = _pair_factor_key(cat_a, cat_b, stimulation_value)
            cached = pair_factor_cache.get(key)
            if cached is None:
                cached = score_pair_factors(
                    cat_a,
                    cat_b,
                    hater_key_map=hater_key_map,
                    lover_key_map=lover_key_map,
                    avoid_lovers=avoid_lovers,
                    parent_key_map=parent_key_map,
                    pair_eval_cache=pair_eval_cache,
                    cache=cache,
                    stimulation=stimulation_value,
                    minimize_variance=False,
                    prefer_low_aggression=prefer_low_aggression,
                    prefer_high_libido=prefer_high_libido,
                    planner_traits=planner_traits,
                )
                pair_factor_cache[key] = cached
            return cached

        candidate_pairs = [(cat_a, cat_b) for i, cat_a in enumerate(alive_cats) for cat_b in alive_cats[i + 1:]]

        evaluated_pairs = []
        for pair_index, (cat_a, cat_b) in enumerate(candidate_pairs):
            if not planner_pair_allows_breeding(cat_a, cat_b):
                continue
            if min_compat > 0.0 and pair_breeding_compatibility(cat_a, cat_b) < min_compat:
                continue
            if avoid_lovers and (cat_a.db_key in lover_locked or cat_b.db_key in lover_locked):
                if not is_mutual_lover_pair(cat_a, cat_b, lover_key_map):
                    continue
            factors = _score_pair_cached(cat_a, cat_b, stimulation)
            if not factors.compatible or factors.risk > max_risk:
                continue

            projection = factors.projection
            founder_bonus = sum(1.0 for cat in (cat_a, cat_b) if not get_parents(cat)) * 2.0
            must_breed_bonus = 50.0 if cat_a.must_breed or cat_b.must_breed else 0.0
            personality = factors.personality_bonus * 3.0
            planner_bias = planner_pair_bias(cat_a, cat_b)
            ancestry_penalty = planner_inbreeding_penalty(cat_a, cat_b)
            progress_score = (
                projection["seven_plus_total"] * 16.0
                + len(projection["locked_stats"]) * 12.0
                + len(projection["reachable_stats"]) * 6.0
                - len(projection["missing_stats"]) * 7.0
                - projection["distance_total"] * 2.5
                - factors.risk * 1.2
                + founder_bonus
                + personality
                + must_breed_bonus
                + planner_bias
                - ancestry_penalty
                + factors.trait_bonus
                + factors.lover_bonus
            )

            evaluated_pairs.append({
                "pair_index": pair_index,
                "cat_a": cat_a,
                "cat_b": cat_b,
                "risk": factors.risk,
                "projection": projection,
                "score": progress_score,
                "personality": personality,
            })

        evaluated_pairs.sort(
            key=lambda pair: (
                pair["projection"]["seven_plus_total"],
                len(pair["projection"]["locked_stats"]),
                pair["score"],
                stat_sum[pair["cat_a"].db_key] + stat_sum[pair["cat_b"].db_key],
            ),
            reverse=True,
        )

        if hasattr(self, "_foundation_panel"):
            self._foundation_panel.set_slot_count(starter_pairs)
            foundation_slots = self._foundation_panel.get_config()[:starter_pairs]
        else:
            foundation_slots = _load_perfect_planner_foundation_pairs()[:starter_pairs]
        pair_lookup = {_cat_uid(cat): cat for cat in alive_cats if _cat_uid(cat)}
        selected_pairs_by_slot: list[Optional[dict]] = [None] * starter_pairs
        used_keys: set[int] = set()
        plan_notes: list[str] = []
        foundation_input_count = sum(
            1
            for slot in foundation_slots
            if str(slot.get("cat_a_uid") or "").strip() and str(slot.get("cat_b_uid") or "").strip()
        )
        manual_using_count = 0
        extra_foundation_ignored = False

        for slot_index, slot in enumerate(foundation_slots, 1):
            if slot_index > starter_pairs:
                extra_foundation_ignored = True
                break
            if not slot.get("using"):
                continue
            a_uid = str(slot.get("cat_a_uid") or "").strip().lower()
            b_uid = str(slot.get("cat_b_uid") or "").strip().lower()
            if not a_uid and not b_uid:
                continue
            if not a_uid or not b_uid:
                plan_notes.append(f"Foundation pair {slot_index} is missing one cat and was skipped.")
                continue

            cat_a = pair_lookup.get(a_uid)
            cat_b = pair_lookup.get(b_uid)
            if cat_a is None or cat_b is None:
                plan_notes.append(f"Foundation pair {slot_index} references a cat that is no longer available.")
                continue
            if cat_a.db_key == cat_b.db_key:
                plan_notes.append(f"Foundation pair {slot_index} uses the same cat twice and was skipped.")
                continue
            if cat_a.db_key in used_keys or cat_b.db_key in used_keys:
                plan_notes.append(f"Foundation pair {slot_index} reuses a cat from another pair and was skipped.")
                continue
            if not planner_pair_allows_breeding(cat_a, cat_b):
                plan_notes.append(f"Foundation pair {slot_index} is not a valid breeding pair.")
                continue

            factors = _score_pair_cached(cat_a, cat_b, stimulation)
            if not factors.compatible or factors.risk > max_risk:
                plan_notes.append(f"Foundation pair {slot_index} exceeded the current risk limit.")
                continue

            source = "using"
            manual_using_count += 1

            selected_pairs_by_slot[slot_index - 1] = {
                "pair_index": len(evaluated_pairs) + slot_index,
                "cat_a": cat_a,
                "cat_b": cat_b,
                "risk": factors.risk,
                "projection": factors.projection,
                "score": 999999.0,
                "personality": factors.personality_bonus * 3.0,
                "source": source,
                "slot_index": slot_index,
                "manual": True,
            }
            used_keys.add(cat_a.db_key)
            used_keys.add(cat_b.db_key)

        if extra_foundation_ignored:
            plan_notes.append("Extra foundation pairs beyond Start pairs were ignored.")

        target_pairs = starter_pairs
        for pair in evaluated_pairs:
            if all(slot is not None for slot in selected_pairs_by_slot):
                break
            cat_a = pair["cat_a"]
            cat_b = pair["cat_b"]
            if cat_a.db_key in used_keys or cat_b.db_key in used_keys:
                continue
            for slot_idx, slot in enumerate(selected_pairs_by_slot):
                if slot is None:
                    selected_pairs_by_slot[slot_idx] = {
                        **pair,
                        "source": "suggested",
                        "slot_index": slot_idx + 1,
                        "manual": False,
                    }
                    break
            used_keys.add(cat_a.db_key)
            used_keys.add(cat_b.db_key)

        selected_pairs = [pair for pair in selected_pairs_by_slot if pair is not None]

        if use_sa and len(selected_pairs) >= 2 and foundation_input_count == 0:
            selected_meta = {
                pair["pair_index"]: {
                    "source": pair.get("source", "suggested"),
                    "slot_index": pair.get("slot_index"),
                    "manual": pair.get("manual", False),
                }
                for pair in selected_pairs
            }
            selected_pairs = self._run_sa_refinement(
                evaluated_pairs,
                selected_pairs,
                starter_pairs,
                sa_temperature,
                sa_neighbors,
            )
            for pair in selected_pairs:
                pair.update(selected_meta.get(pair["pair_index"], {}))

        if not selected_pairs:
            self._table.setRowCount(0)
            self._details_pane.show_stage(None)
            self._cat_locator.clear()
            self._offspring_tracker.clear()
            self._summary.setText(_tr("perfect_planner.status.no_pairs_found"))
            return

        header = self._table.horizontalHeader()
        table_sorting_was_enabled = self._table.isSortingEnabled()
        had_sort_indicator = header.isSortIndicatorShown()
        sort_column = header.sortIndicatorSection()
        sort_order = header.sortIndicatorOrder()
        if table_sorting_was_enabled:
            self._table.setSortingEnabled(False)

        tracker_rows: list[dict] = []
        for idx, pair in enumerate(selected_pairs, 1):
            cat_a = pair["cat_a"]
            cat_b = pair["cat_b"]
            tracker_rows.append({
                "pair_index": idx,
                "cat_a": cat_a,
                "cat_b": cat_b,
                "known_offspring": tracked_offspring(cat_a, cat_b),
                "projection": pair["projection"],
                "risk": pair["risk"],
                "coi": kinship_coi(cat_a, cat_b),
                "shared": shared_ancestor_counts(cat_a, cat_b, recent_depth=3, max_depth=8),
                "source": pair.get("source", "suggested"),
                "slot_index": pair.get("slot_index"),
            })
        self._offspring_tracker.set_rows(tracker_rows)
        self._summary.setText(
            f"{len(selected_pairs)} pairs planned | {manual_using_count} using | {len(selected_pairs) - manual_using_count} suggested"
        )

        def _fmt_stats(stats: list[str]) -> str:
            return ", ".join(stats) if stats else "none"

        def _pair_name(pair: dict) -> str:
            return f"{pair['cat_a'].name} ({pair['cat_a'].gender_display}) x {pair['cat_b'].name} ({pair['cat_b'].gender_display})"

        def _stage1_target_grid(pair: dict) -> dict:
            projection = pair["projection"]
            return {
                "parents": [
                    {
                        "name": f"{pair['cat_a'].name}\n{pair['cat_a'].gender_display}",
                        "stats": pair["cat_a"].base_stats,
                        "sum": sum(pair["cat_a"].base_stats.values()),
                    },
                    {
                        "name": f"{pair['cat_b'].name}\n{pair['cat_b'].gender_display}",
                        "stats": pair["cat_b"].base_stats,
                        "sum": sum(pair["cat_b"].base_stats.values()),
                    },
                ],
                "offspring": {
                    "stats": {
                        stat: {
                            "lo": projection["stat_ranges"][stat][0],
                            "hi": projection["stat_ranges"][stat][1],
                            "expected": projection["expected_stats"][stat],
                        }
                        for stat in STAT_NAMES
                    },
                    "sum_range": projection["sum_range"],
                },
            }

        def _planner_pair_grid(cat_a: Cat, cat_b: Cat, projection: dict) -> dict:
            return {
                "parents": [
                    {
                        "name": f"{cat_a.name}\n{cat_a.gender_display}",
                        "stats": cat_a.base_stats,
                        "sum": sum(cat_a.base_stats.values()),
                    },
                    {
                        "name": f"{cat_b.name}\n{cat_b.gender_display}",
                        "stats": cat_b.base_stats,
                        "sum": sum(cat_b.base_stats.values()),
                    },
                ],
                "offspring": {
                    "stats": {
                        stat: {
                            "lo": projection["stat_ranges"][stat][0],
                            "hi": projection["stat_ranges"][stat][1],
                            "expected": projection["expected_stats"][stat],
                        }
                        for stat in STAT_NAMES
                    },
                    "sum_range": projection["sum_range"],
                },
            }

        def _rotation_candidate(pair: dict) -> Optional[dict]:
            missing_stats = pair["projection"]["missing_stats"]
            if not missing_stats:
                return None
            best = None
            pair_cats = {pair["cat_a"].db_key, pair["cat_b"].db_key}
            for parent in (pair["cat_a"], pair["cat_b"]):
                for candidate in alive_cats:
                    if candidate.db_key in pair_cats:
                        continue
                    if not planner_pair_allows_breeding(parent, candidate):
                        continue
                    if min_compat > 0.0 and pair_breeding_compatibility(parent, candidate) < min_compat:
                        continue
                    factors = _score_pair_cached(parent, candidate, stimulation)
                    if not factors.compatible or factors.risk > max_risk:
                        continue
                    bring_stats = [stat for stat in missing_stats if candidate.base_stats[stat] >= 7]
                    if not bring_stats:
                        continue
                    planner_bias = planner_pair_bias(parent, candidate)
                    ancestry_penalty = planner_inbreeding_penalty(parent, candidate)
                    score = (
                        len(bring_stats) * 15.0
                        + sum(candidate.base_stats[stat] for stat in bring_stats)
                        - factors.risk
                        + factors.personality_bonus * 3.0
                        + (4.0 if not get_parents(candidate) else 0.0)
                        + planner_bias
                        - ancestry_penalty
                        + factors.trait_bonus
                    )
                    record = {
                        "parent": parent,
                        "candidate": candidate,
                        "risk": factors.risk,
                        "bring_stats": bring_stats,
                        "score": score,
                    }
                    if best is None or record["score"] > best["score"]:
                        best = record
            return best

        stage_rows: list[dict] = []

        stage1_actions = []
        for idx, pair in enumerate(selected_pairs, 1):
            projection = pair["projection"]
            bp = _pair_breakpoint_analysis(pair["cat_a"], pair["cat_b"], stimulation)
            mode = _tr("perfect_planner.foundation.using", default="Using these") if pair.get("source") == "using" else _tr("perfect_planner.foundation.suggested", default="Suggested")
            stage1_actions.append({
                "action": _tr("perfect_planner.action.pair", index=idx),
                "target": f"{mode}: {_pair_name(pair)}",
                "parents": [pair["cat_a"], pair["cat_b"]],
                "mutation_summary": _mutation_payload(pair["cat_a"], pair["cat_b"]),
                "detail_projection": projection,
                "coverage_value": float(projection["seven_plus_total"]),
                "target_grid": _stage1_target_grid(pair),
                "risk": pair["risk"],
                "why": (
                    _tr(
                        "perfect_planner.stage1.why",
                        coverage=f"{projection['seven_plus_total']:.1f}",
                        stim=int(stimulation),
                        headline=bp["headline"],
                        hints=" ".join(bp["hints"][:2]),
                    )
                ),
                "children": (
                    _tr("perfect_planner.stage1.children")
                ),
                "rotate": (
                    _tr("perfect_planner.stage1.rotate")
                ),
            })

        stage_rows.append({
            "stage": _tr("perfect_planner.stage1.title"),
            "goal": (
                f"{len(selected_pairs)} pairs"
                f" | {manual_using_count} using"
                f" | {len(selected_pairs) - manual_using_count} suggested"
            ),
            "pairs": len(selected_pairs),
            "coverage": sum(pair["projection"]["seven_plus_total"] for pair in selected_pairs) / len(selected_pairs) if selected_pairs else 0.0,
            "risk": max((pair["risk"] for pair in selected_pairs), default=0.0),
            "mutation_ratio": _stage_mutation_ratio(stage1_actions),
            "details": _tr("perfect_planner.stage1.details"),
            "summary": _tr("perfect_planner.stage1.summary", count=len(selected_pairs)),
            "notes": [
                _tr("perfect_planner.stage1.note1"),
                _tr("perfect_planner.stage1.note2"),
                *plan_notes[:3],
            ],
            "actions": stage1_actions,
        })

        stage2_actions = []
        for idx, pair in enumerate(selected_pairs, 1):
            projection = pair["projection"]
            stage2_actions.append({
                "action": _tr("perfect_planner.stage2.action", index=idx),
                "target": _tr("perfect_planner.stage2.target", stats=_fmt_stats(projection["locked_stats"])),
                "parents": [pair["cat_a"], pair["cat_b"]],
                "mutation_summary": _mutation_payload(pair["cat_a"], pair["cat_b"]),
                "detail_projection": projection,
                "coverage_value": float(projection["seven_plus_total"]),
                "target_grid": _planner_pair_grid(pair["cat_a"], pair["cat_b"], projection),
                "risk": None,
                "why": _tr("perfect_planner.stage2.why"),
                "children": _tr(
                    "perfect_planner.stage2.children",
                    index=idx,
                    a=pair["cat_a"].name,
                    b=pair["cat_b"].name,
                ),
                "rotate": _tr("perfect_planner.stage2.rotate"),
            })

        stage_rows.append({
            "stage": _tr("perfect_planner.stage2.title"),
            "goal": _tr("perfect_planner.stage2.goal"),
            "pairs": len(stage2_actions),
            "coverage": sum(len(pair["projection"]["locked_stats"]) for pair in selected_pairs) / len(selected_pairs) if selected_pairs else 0.0,
            "risk": 0.0,
            "mutation_ratio": _stage_mutation_ratio(stage2_actions),
            "details": _tr("perfect_planner.stage2.details"),
            "summary": _tr("perfect_planner.stage2.summary"),
            "notes": [
                _tr("perfect_planner.stage2.note1"),
                _tr("perfect_planner.stage2.note2"),
            ],
            "actions": stage2_actions,
        })

        stage3_actions = []
        stage3_import_counts: list[float] = []
        for idx, pair in enumerate(selected_pairs, 1):
            rotation = _rotation_candidate(pair)
            missing = pair["projection"]["missing_stats"]
            if rotation is None:
                stage3_import_counts.append(0.0)
                stage3_actions.append({
                    "action": _tr("perfect_planner.stage3.action_later", index=idx),
                    "target": _tr("perfect_planner.stage3.target_missing", stats=_fmt_stats(missing)),
                    "parents": [pair["cat_a"], pair["cat_b"]],
                    "mutation_summary": _mutation_payload(pair["cat_a"], pair["cat_b"]),
                    "detail_projection": pair["projection"],
                    "coverage_value": float(pair["projection"]["seven_plus_total"]),
                    "risk": None,
                    "why": _tr("perfect_planner.stage3.why_none"),
                    "children": _tr("perfect_planner.stage3.children_none"),
                    "rotate": _tr("perfect_planner.stage3.rotate_none", stats=_fmt_stats(missing)),
                })
            else:
                source_note = (
                    _tr("perfect_planner.stage3.source.founder")
                    if not get_parents(rotation["candidate"])
                    else _tr("perfect_planner.stage3.source.existing")
                )
                rotated_projection = pair_projection(rotation["parent"], rotation["candidate"], stimulation=stimulation)
                rotated_bp = _pair_breakpoint_analysis(rotation["parent"], rotation["candidate"], stimulation)
                stage3_import_counts.append(float(len(rotation["bring_stats"])))
                stage3_actions.append({
                    "action": _tr("perfect_planner.stage3.action_rotation", index=idx),
                    "target": (
                        f"{rotation['parent'].name} ({rotation['parent'].gender_display}) x "
                        f"{rotation['candidate'].name} ({rotation['candidate'].gender_display})"
                    ),
                    "parents": [rotation["parent"], rotation["candidate"]],
                    "mutation_summary": _mutation_payload(rotation["parent"], rotation["candidate"]),
                    "detail_projection": rotated_projection,
                    "coverage_value": float(rotated_projection["seven_plus_total"]),
                    "target_grid": _planner_pair_grid(
                        rotation["parent"],
                        rotation["candidate"],
                        rotated_projection,
                    ),
                    "risk": rotation["risk"],
                    "why": (
                        _tr(
                            "perfect_planner.stage3.why_rotation",
                            source=source_note,
                            index=idx,
                            missing=_fmt_stats(missing),
                            coverage=f"{rotated_projection['seven_plus_total']:.1f}",
                            stim=int(stimulation),
                            headline=rotated_bp["headline"],
                            hints=" ".join(rotated_bp["hints"][:2]),
                        )
                    ),
                    "children": _tr("perfect_planner.stage3.children_rotation"),
                    "rotate": _tr("perfect_planner.stage3.rotate_rotation"),
                })

        stage_rows.append({
            "stage": _tr("perfect_planner.stage3.title"),
            "goal": _tr("perfect_planner.stage3.goal"),
            "pairs": len(stage3_actions),
            "coverage": sum(stage3_import_counts) / max(1, len(stage3_import_counts)),
            "risk": max(
                [float(action["risk"]) for action in stage3_actions if action["risk"] is not None] or [0.0]
            ),
            "mutation_ratio": _stage_mutation_ratio(stage3_actions),
            "details": _tr("perfect_planner.stage3.details"),
            "summary": _tr("perfect_planner.stage3.summary"),
            "notes": [
                _tr("perfect_planner.stage3.note1"),
                _tr("perfect_planner.stage3.note2"),
            ],
            "actions": stage3_actions,
        })

        stage4_actions = []
        for idx, pair in enumerate(selected_pairs, 1):
            missing = pair["projection"]["missing_stats"]
            if missing:
                stage4_actions.append({
                    "action": _tr("perfect_planner.stage4.action_finish", index=idx),
                    "target": _tr("perfect_planner.stage4.target_finish", stats=_fmt_stats(missing)),
                    "parents": [pair["cat_a"], pair["cat_b"]],
                    "mutation_summary": _mutation_payload(pair["cat_a"], pair["cat_b"]),
                    "detail_projection": pair["projection"],
                    "coverage_value": float(pair["projection"]["seven_plus_total"]),
                    "risk": pair["risk"],
                    "why": _tr("perfect_planner.stage4.why_finish"),
                    "children": _tr("perfect_planner.stage4.children_finish"),
                    "rotate": _tr("perfect_planner.stage4.rotate_finish"),
                })
            else:
                stage4_actions.append({
                    "action": _tr("perfect_planner.stage4.action_maintain", index=idx),
                    "target": _tr("perfect_planner.stage4.target_maintain"),
                    "parents": [pair["cat_a"], pair["cat_b"]],
                    "mutation_summary": _mutation_payload(pair["cat_a"], pair["cat_b"]),
                    "detail_projection": pair["projection"],
                    "coverage_value": float(pair["projection"]["seven_plus_total"]),
                    "risk": pair["risk"],
                    "why": _tr("perfect_planner.stage4.why_maintain"),
                    "children": _tr("perfect_planner.stage4.children_maintain"),
                    "rotate": _tr("perfect_planner.stage4.rotate_maintain"),
                })

        stage_rows.append({
            "stage": _tr("perfect_planner.stage4.title"),
            "goal": _tr("perfect_planner.stage4.goal"),
            "pairs": len(stage4_actions),
            "coverage": sum(len(pair["projection"]["reachable_stats"]) for pair in selected_pairs) / len(selected_pairs) if selected_pairs else 0.0,
            "risk": max((pair["risk"] for pair in selected_pairs), default=0.0),
            "mutation_ratio": _stage_mutation_ratio(stage4_actions),
            "details": _tr("perfect_planner.stage4.details"),
            "summary": _tr("perfect_planner.stage4.summary"),
            "notes": [
                _tr("perfect_planner.stage4.note1"),
                _tr("perfect_planner.stage4.note2"),
            ],
            "actions": stage4_actions,
        })

        self._table.setRowCount(0)
        self._details_pane.show_stage(None)

        for row_idx, stage in enumerate(stage_rows):
            self._table.insertRow(row_idx)
            stage_item = QTableWidgetItem(stage["stage"])
            stage_item.setData(Qt.UserRole, stage)
            stage_item.setTextAlignment(Qt.AlignCenter)

            goal_item = QTableWidgetItem(stage["goal"])
            pair_item = QTableWidgetItem(str(stage["pairs"]))
            pair_item.setTextAlignment(Qt.AlignCenter)

            coverage_value = float(stage["coverage"])
            coverage_item = QTableWidgetItem(f"{coverage_value:.1f}/7")
            coverage_item.setTextAlignment(Qt.AlignCenter)
            if coverage_value >= 6.0:
                coverage_item.setForeground(QBrush(QColor(98, 194, 135)))
            elif coverage_value >= 4.5:
                coverage_item.setForeground(QBrush(QColor(216, 181, 106)))
            else:
                coverage_item.setForeground(QBrush(QColor(190, 145, 40)))

            risk_value = float(stage["risk"])
            risk_item = QTableWidgetItem(f"{risk_value:.0f}%")
            risk_item.setTextAlignment(Qt.AlignCenter)
            if risk_value >= 20:
                risk_item.setForeground(QBrush(QColor(217, 119, 119)))
            elif risk_value > 0:
                risk_item.setForeground(QBrush(QColor(216, 181, 106)))
            else:
                risk_item.setForeground(QBrush(QColor(98, 194, 135)))

            details_item = QTableWidgetItem(stage["details"])
            mutation_ratio = float(stage.get("mutation_ratio", 0.0))
            if abs(mutation_ratio) > 1e-6:
                mutation_color = _planner_trait_color(mutation_ratio)
                mutation_color.setAlpha(85)
                stage_item.setBackground(QBrush(mutation_color))

            self._table.setItem(row_idx, 0, stage_item)
            self._table.setItem(row_idx, 1, goal_item)
            self._table.setItem(row_idx, 2, pair_item)
            self._table.setItem(row_idx, 3, coverage_item)
            self._table.setItem(row_idx, 4, risk_item)
            self._table.setItem(row_idx, 5, details_item)

        if excluded_cats:
            row_idx = self._table.rowCount()
            self._table.insertRow(row_idx)
            stage_item = QTableWidgetItem(_tr("perfect_planner.stage.excluded"))
            stage_item.setTextAlignment(Qt.AlignCenter)
            stage_item.setForeground(QBrush(QColor(170, 120, 120)))
            stage_item.setData(Qt.UserRole, {
                "stage": _tr("perfect_planner.stage.excluded"),
                "excluded_cat_rows": [
                    {
                        "name": f"{cat.name} ({cat.gender_display})",
                        "tags": list(_cat_tags(cat)),
                        "stats": dict(cat.base_stats),
                        "sum": _cat_base_sum(cat),
                        "traits": {
                            "aggression": _trait_label_from_value("aggression", cat.aggression) or "unknown",
                            "libido": _trait_label_from_value("libido", cat.libido) or "unknown",
                            "inbredness": _trait_label_from_value("inbredness", cat.inbredness) or "unknown",
                        },
                    }
                    for cat in excluded_cats
                ],
            })
            details_item = QTableWidgetItem(_tr("perfect_planner.excluded.details"))
            dash_pair = QTableWidgetItem("\u2014"); dash_pair.setTextAlignment(Qt.AlignCenter)
            dash_cov = QTableWidgetItem("\u2014"); dash_cov.setTextAlignment(Qt.AlignCenter)
            dash_risk = QTableWidgetItem("\u2014"); dash_risk.setTextAlignment(Qt.AlignCenter)
            self._table.setItem(row_idx, 0, stage_item)
            self._table.setItem(row_idx, 1, QTableWidgetItem(_tr("perfect_planner.excluded.count", count=len(excluded_cats))))
            self._table.setItem(row_idx, 2, dash_pair)
            self._table.setItem(row_idx, 3, dash_cov)
            self._table.setItem(row_idx, 4, dash_risk)
            self._table.setItem(row_idx, 5, details_item)

        # Build cat locator data from all cats involved in the plan
        locator_cats: dict[int, dict] = {}  # keyed by db_key to deduplicate
        room_order_counter = 0
        tracker_rows_by_pair_index = {row["pair_index"]: row for row in tracker_rows}
        for idx, pair in enumerate(selected_pairs):
            pair_label = f"Pair {idx + 1}"
            cat_a, cat_b = pair["cat_a"], pair["cat_b"]
            row_info = tracker_rows_by_pair_index.get(idx + 1, {})
            room_a = cat_a.room_display or cat_a.status or "?"
            room_b = cat_b.room_display or cat_b.status or "?"
            # Pair needs to move if the two cats aren't already in the same room together
            pair_needs_move = (cat_a.status != "In House" or cat_b.status != "In House"
                               or room_a != room_b)
            base_order = float(room_order_counter)
            for cat in (cat_a, cat_b):
                if cat.db_key not in locator_cats:
                    current = cat.room_display or cat.status or "?"
                    current_room_key = cat.room if cat.room in ROOM_DISPLAY else _room_key_from_display(cat.room_display)
                    locator_cats[cat.db_key] = {
                        "name": cat.name,
                        "gender_display": cat.gender_display,
                        "db_key": cat.db_key, "tags": list(_cat_tags(cat)),
                        "has_lover": bool(getattr(cat, "lovers", None)),
                        "age": cat.age if cat.age is not None else cat.db_key,
                        "current_room": current,
                        "current_room_key": current_room_key,
                        "assigned_room": pair_label,
                        "room_order": base_order,
                        "needs_move": pair_needs_move,
                    }
            for child_idx, child in enumerate(row_info.get("known_offspring", []), 1):
                if child.db_key in locator_cats:
                    continue
                current = child.room_display or child.status or "?"
                current_room_key = child.room if child.room in ROOM_DISPLAY else _room_key_from_display(child.room_display)
                locator_cats[child.db_key] = {
                    "name": child.name,
                    "gender_display": child.gender_display,
                    "db_key": child.db_key,
                    "has_lover": bool(getattr(child, "lovers", None)),
                    "tags": list(_cat_tags(child)),
                    "age": child.age if child.age is not None else child.db_key,
                    "current_room": current,
                    "current_room_key": current_room_key,
                    "assigned_room": f"{pair_label} offspring",
                    "room_order": base_order + 0.2 + (child_idx * 0.01),
                    "needs_move": child.status != "In House",
                }

            rotation = _rotation_candidate(pair)
            if rotation is not None:
                cat = rotation["candidate"]
                if cat.db_key not in locator_cats:
                    current = cat.room_display or cat.status or "?"
                    current_room_key = cat.room if cat.room in ROOM_DISPLAY else _room_key_from_display(cat.room_display)
                    locator_cats[cat.db_key] = {
                        "name": cat.name,
                        "gender_display": cat.gender_display,
                        "db_key": cat.db_key, "tags": list(_cat_tags(cat)),
                        "has_lover": bool(getattr(cat, "lovers", None)),
                        "age": cat.age if cat.age is not None else cat.db_key,
                        "current_room": current,
                        "current_room_key": current_room_key,
                        "assigned_room": f"Rotation {idx + 1}",
                        "room_order": base_order + 0.4,
                        "needs_move": cat.status != "In House",
                    }
            room_order_counter += 1
        self._cat_locator.show_assignments(list(locator_cats.values()))

        if excluded_cats:
            self._summary.setText(
                _tr(
                    "perfect_planner.status.planned_with_excluded",
                    pairs=len(selected_pairs),
                    alive=len(alive_cats),
                    excluded=len(excluded_cats),
                ) + f" \u00b7 {'SA' if use_sa else 'greedy'}"
            )
        else:
            self._summary.setText(
                _tr("perfect_planner.status.planned", pairs=len(selected_pairs), alive=len(alive_cats))
                + f" \u00b7 {'SA' if use_sa else 'greedy'}"
            )

        if stage_rows:
            self._selected_stage_row = min(max(int(getattr(self, "_selected_stage_row", 0) or 0), 0), len(stage_rows) - 1)
            self._table.selectRow(self._selected_stage_row)
            self._show_stage_row(self._selected_stage_row, context_note=self._pending_stage_context)
        else:
            self._selected_stage_row = 0

        if table_sorting_was_enabled:
            self._table.setSortingEnabled(True)
            if had_sort_indicator and sort_column >= 0:
                self._table.sortItems(sort_column, sort_order)
            else:
                self._table.sortItems(0, Qt.AscendingOrder)

        # Cache results for cross-session restore
        self._save_cached_results(
            stage_rows, tracker_rows,
            list(locator_cats.values()),
            self._summary.text(),
        )
