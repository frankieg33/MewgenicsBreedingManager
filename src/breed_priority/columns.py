"""Breed Priority — column layout, data roles, and lookup tables.

Defines the score table column structure, custom Qt data roles,
room style mapping, and injury display names.
No Qt widget dependencies — only QtCore for UserRole.
"""

from PySide6.QtCore import Qt

from .scoring import SCORE_COLUMNS, SCORE_HEADER_7_COUNT


# ── Column indices ────────────────────────────────────────────────────────────

_SEP_HEADER      = "│"
COL_NAME          = 0
COL_LOC           = 1
COL_INJ           = 2
_STAT_COL_NAMES   = ["STR", "DEX", "CON", "INT", "SPD", "CHA", "LCK"]
_COL_STAT_START   = 3
_NUM_STAT_COLS    = len(_STAT_COL_NAMES)
_SCORE_COLS       = [h for h, _ in SCORE_COLUMNS]
COL_SEP1          = _COL_STAT_START + _NUM_STAT_COLS
_COL_SCORE_START  = COL_SEP1 + 1
COL_SCORE         = _COL_SCORE_START + len(SCORE_COLUMNS)
_ALL_HEADERS      = (
    ["Name", "Loc", "Inj"]
    + _STAT_COL_NAMES
    + [_SEP_HEADER]
    + _SCORE_COLS
    + ["Score"]
)
_SEP_COLS         = frozenset({COL_SEP1})
_SEP_WIDTH        = 8
_COL_MIN_WIDTH    = 20   # ~2 text chars or 1 emoji at table font size
_SEP_MIN_WIDTH    = _COL_MIN_WIDTH

# ── Dynamic CW section (appended after static columns) ───────────────────────
# Complex Weight columns start immediately after COL_SCORE.
# This is a computed constant so it auto-updates if the static layout grows.
COL_CW_SECTION_START = len(_ALL_HEADERS)   # = COL_SCORE + 1


def _score_col_idx(header: str) -> int:
    """Return absolute score-table column index for a SCORE_COLUMNS header."""
    return _COL_SCORE_START + _SCORE_COLS.index(header)


# ── Semantic score-table column roles ────────────────────────────────────────

# Value cells that should stay chip-backed when no heatmap bar is present.
_NEUTRAL_VALUE_CHIP_SCORE_HEADERS = frozenset({"Sum", "Age", SCORE_HEADER_7_COUNT})
_NEUTRAL_VALUE_CHIP_COLS = frozenset(_score_col_idx(h) for h in _NEUTRAL_VALUE_CHIP_SCORE_HEADERS)

# Score-table relationship columns with semantic overflow colors.
_LOVE_SCORE_HEADERS = frozenset({"💗"})
_HATE_SCORE_HEADERS = frozenset({"💥"})
_LOVE_SCORE_COLS = frozenset(_score_col_idx(h) for h in _LOVE_SCORE_HEADERS)
_HATE_SCORE_COLS = frozenset(_score_col_idx(h) for h in _HATE_SCORE_HEADERS)
_RELATIONSHIP_SCORE_COLS = frozenset(_LOVE_SCORE_COLS | _HATE_SCORE_COLS)

# Alignment roles by content behavior.
_SINGLE_VALUE_CENTER_SCORE_HEADERS = frozenset({
    "Sum", "Age", SCORE_HEADER_7_COUNT, "7sub", "Sex", "Lib", "Gene", "Aggro", "Gender",
})
_SINGLE_VALUE_CENTER_SCORE_COLS = frozenset(_score_col_idx(h) for h in _SINGLE_VALUE_CENTER_SCORE_HEADERS)

_MULTI_VALUE_LEFT_SCORE_HEADERS = frozenset({
    "7rare", "Trait", "💗", "💥",
})
_MULTI_VALUE_LEFT_SCORE_COLS = frozenset(_score_col_idx(h) for h in _MULTI_VALUE_LEFT_SCORE_HEADERS)


# ── Custom Qt data roles ──────────────────────────────────────────────────────

_CHIP_ROLE             = Qt.UserRole + 2
_SCORE_SECONDARY_ROLE  = Qt.UserRole + 3
_HEATMAP_ROLE          = Qt.UserRole + 4
_TRAIT_NAME_ROLE       = Qt.UserRole + 10
_TRAIT_SUMMARY_ROLE    = Qt.UserRole + 11


# ── Room display name → text color ───────────────────────────────────────────

_ROOM_STYLE = {
    "1F Left":  "#55bbdd",
    "1F Right": "#ddbb55",
    "2F Left":  "#bb77ee",
    "2F Right": "#66cc77",
    "Attic":    "#ee7788",
}


# ── Injury display ────────────────────────────────────────────────────────────

INJURY_STAT_NAMES = {
    "INT": "Concussion",
    "LCK": "Jinxed",
    "CHA": "Disfigured",
}
_INJ_SHORT = {
    "Concussion": "Conc",
    "Jinxed":     "Jinx",
    "Disfigured": "Disfig",
}


# ── Relationship chip emoji constants ────────────────────────────────────────

# Chips in combined love/hate columns distinguish scope vs room by emoji.
_EMOJI_SCOPE = "🔭"
_EMOJI_ROOM  = "🐱"
