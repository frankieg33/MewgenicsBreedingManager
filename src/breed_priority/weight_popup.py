"""Breed Priority — scoring weights popup dialog."""

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QTableWidget, QTableWidgetItem,
    QHeaderView, QAbstractItemView, QPushButton,
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor

from .scoring import TRAIT_HIGH_THRESHOLD, TRAIT_LOW_THRESHOLD, GENETIC_SAFE_RISK_FLOOR
from .styles import _DIM_BTN_LG, _PRIORITY_TABLE_STYLE
from .theme import (
    CLR_BG_SCORE_AREA, CLR_TEXT_PRIMARY,
    CLR_VALUE_POS, CLR_VALUE_NEG,
)


def show_weights_popup(parent, weights: dict) -> None:
    """Open a modal dialog displaying the current scoring weight breakdown.

    Args:
        parent: Parent QWidget for the dialog.
        weights: Current scoring weight dict.
    """
    dlg = QDialog(parent)
    dlg.setWindowTitle("Scoring Weights")
    dlg.setModal(True)
    dlg.setStyleSheet(f"background:{CLR_BG_SCORE_AREA}; color:{CLR_TEXT_PRIMARY};")
    dlg.resize(440, 380)

    vb = QVBoxLayout(dlg)
    vb.setContentsMargins(16, 16, 16, 16)
    vb.setSpacing(8)

    title = QLabel("Breed Priority - Scoring Weights")
    title.setStyleSheet(f"color:{CLR_TEXT_PRIMARY}; font-size:13px; font-weight:bold;")
    vb.addWidget(title)

    table = QTableWidget()
    table.setColumnCount(2)
    table.setHorizontalHeaderLabels(["Attribute", "Weight"])
    table.setEditTriggers(QAbstractItemView.NoEditTriggers)
    table.setSelectionMode(QAbstractItemView.NoSelection)
    table.verticalHeader().setVisible(False)
    table.setShowGrid(False)
    table.setAlternatingRowColors(True)
    table.setStyleSheet(_PRIORITY_TABLE_STYLE)
    hh = table.horizontalHeader()
    hh.setSectionResizeMode(0, QHeaderView.Stretch)
    hh.setSectionResizeMode(1, QHeaderView.Fixed)
    table.setColumnWidth(1, 90)

    w = weights
    _thr = int(round(w.get("stat_7_threshold", 7.0)))
    _stat_cnt_thr = int(round(w.get("stat_count_threshold", 7.0)))
    rows_data = [
        ("── 7-rare: bonus per stat where few scope cats share that 7 ──", ""),
        (f"  7 in a stat (≤{_thr} cats in scope have it)",  f"+{w['stat_7']:.0f}"),
        (f"  7 in a stat ({_thr+1} cats in scope)",         f"+{max(0.1, round(w['stat_7']*_thr/(_thr+1),1)):.1f}"),
        (f"  7 in a stat ({_thr+3} cats in scope)",         f"+{max(0.1, round(w['stat_7']*_thr/(_thr+3),1)):.1f}"),
        (f"  7 in a stat ({_thr+6} cats in scope)",         f"+{max(0.1, round(w['stat_7']*_thr/(_thr+6),1)):.1f}"),
        (f"  7 in a stat (sole owner, none in scope)",      f"+{w['stat_7']*2:.0f} (★★ bonus)"),
        (f"── Stat-cnt: bonus per stat at or above threshold (≥{_stat_cnt_thr}) ──", ""),
        (f"  Threshold",             f"≥{_stat_cnt_thr}"),
        (f"  1 stat ≥{_stat_cnt_thr}",  f"+{w['stat_7_count']*1:.2f}"),
        (f"  3 stats ≥{_stat_cnt_thr}", f"+{w['stat_7_count']*3:.2f}"),
        (f"  5 stats ≥{_stat_cnt_thr}", f"+{w['stat_7_count']*5:.2f}"),
        (f"  7 stats ≥{_stat_cnt_thr}", f"+{w['stat_7_count']*7:.2f} (max)"),
        ("Trait - top priority sole owner",               f"{2*w['trait_top_priority']:+.1f}"),
        ("Trait - top priority, shared with N cats",      f"{w['trait_top_priority']:+.1f} ÷ N"),
        ("Trait - desirable sole owner",                  f"{2*w['trait_desirable']:+.1f}"),
        ("Trait - desirable, shared with N cats",         f"{w['trait_desirable']:+.1f} ÷ N"),
        ("Trait - neutral or undecided",                   "+0.00"),
        ("Trait - undesirable",                           f"{w['trait_undesirable']:+.1f}"),
        ("── CHA penalty: applied when CHA is below 5 ──", ""),
        ("  CHA = 4",  f"{w['cha_low']:+.1f}"),
        ("  CHA = 3",  f"{w['cha_low']*2:+.1f} (2×)"),
        (f"Low aggression (<{TRAIT_LOW_THRESHOLD*100:.0f}%)",   f"+{w['low_aggression']:.1f}"),
        ("Unknown gender (?)",                                    f"+{w['unknown_gender']:.1f}"),
        (f"High libido (≥{TRAIT_HIGH_THRESHOLD*100:.0f}%)",      f"+{w['high_libido']:.1f}"),
        (f"High aggression (≥{TRAIT_HIGH_THRESHOLD*100:.0f}%)",  f"{w['high_aggression']:.1f}"),
        (f"Low libido (<{TRAIT_LOW_THRESHOLD*100:.0f}%)",        f"{w['low_libido']:.1f}"),
        ("── Genetic safety: configurable threshold and penalty scale ──", ""),
        (f"  Bonus (avg risk ≤ threshold)",  f"+{w['zero_risk_bonus']:.1f}"),
        (f"  Penalty (avg risk > threshold)", f"{w['no_children']:.1f} × (risk%−thr) × scale÷100"),
        (f"  Threshold",  f"{int(round(w.get('gene_risk_threshold', GENETIC_SAFE_RISK_FLOOR)))}%"),
        (f"  Penalty scale (higher = faster)", f"{int(round(w.get('gene_risk_penalty_scale', 10.0)))}"),
        ("Love interest in scope",                         f"+{w['love_interest']:.1f}"),
        ("Rival in scope",                                 f"{w['rivalry']:.1f}"),
        ("── age penalty: multiplies per 3 years above threshold ──", ""),
        (f"  Age ≤ {int(round(w.get('age_threshold',10)))} (at or below threshold)",  "+0"),
        (f"  Age {int(round(w.get('age_threshold',10)))+1} (+1 over, 1×)",  f"{w['age_penalty']:.1f}"),
        (f"  Age {int(round(w.get('age_threshold',10)))+4} (+4 over, 2×)",  f"{2*w['age_penalty']:.1f}"),
        (f"  Age {int(round(w.get('age_threshold',10)))+7} (+7 over, 3×)",  f"{3*w['age_penalty']:.1f}"),
    ]
    table.setRowCount(len(rows_data))
    for r, (attr, wt) in enumerate(rows_data):
        is_header = wt == ""
        a_item = QTableWidgetItem(attr)
        a_item.setFlags(Qt.ItemIsEnabled)
        if is_header:
            a_item.setForeground(QColor("#7070c0"))
            f = a_item.font()
            f.setItalic(True)
            a_item.setFont(f)
        w_item = QTableWidgetItem(wt)
        w_item.setFlags(Qt.ItemIsEnabled)
        w_item.setTextAlignment(Qt.AlignCenter)
        if wt.startswith("+"):
            w_item.setForeground(QColor(CLR_VALUE_POS))
        elif wt.startswith("-"):
            w_item.setForeground(QColor(CLR_VALUE_NEG))
        table.setItem(r, 0, a_item)
        table.setItem(r, 1, w_item)
        table.setRowHeight(r, 22 if is_header else 24)
    vb.addWidget(table)

    close_btn = QPushButton("Close")
    close_btn.setStyleSheet(_DIM_BTN_LG)
    close_btn.clicked.connect(dlg.accept)
    vb.addWidget(close_btn, alignment=Qt.AlignRight)
    dlg.exec()
