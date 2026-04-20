"""Breed Priority — color theme and chip color pairs.

All semantic colors, chip (bg, fg) pairs, and heatmap colors.
Derived colors are computed via ColorUtils so the entire theme
changes consistently when a base color is adjusted.
"""

from PySide6.QtGui import QColor

from .color_utils import ColorUtils


# ── Trait rating colors ───────────────────────────────────────────────────────

CLR_TOP_PRIORITY = "#40d0c0"
CLR_DESIRABLE = "#6aaa6a"
CLR_NEUTRAL = "#b0a040"
CLR_UNDECIDED = "#888899"
CLR_UNDESIRABLE = "#aa6a6a"
RATING_ITEM_COLORS = [CLR_TOP_PRIORITY, CLR_DESIRABLE, CLR_NEUTRAL, CLR_UNDECIDED, CLR_UNDESIRABLE]

# ── Base anchors ──────────────────────────────────────────────────────────────

_THEME_DARK = "#0c0c20"
_CLR_RED = "#cc3333"
_CLR_YELLOW = "#b0a040"

# ── Dynamic stat column color anchors ─────────────────────────────────────────
# Used when "Use Current Stats" or "Add Mutation Stats" is active.
# Values are colored by their position in each column's min–max range.

_CLR_STAT_DYNAMIC_LOW = "#7a5028"   # warm brown — column minimum
_CLR_STAT_DYNAMIC_HIGH = "#44cc66"  # bright teal-green — column maximum

# ── UI state colors ───────────────────────────────────────────────────────────

CLR_STATE_SELECTED_BG = "#0a1e18"
CLR_STATE_SELECTED_FG = "#aaddcc"
CLR_STATE_SELECTED_BORDER = "#1ec8a0"

CLR_STATE_SUBDUED_FG = "#ccccdd"
CLR_STATE_SUBDUED_BG = "#1a1a26"
CLR_STATE_SUBDUED_HOVER_FG = "#ddddf0"
CLR_STATE_SUBDUED_HOVER_BG = "#222230"
CLR_LABEL_SUBDUED = "#ccccdd"

# ── Text roles (defined + derived) ───────────────────────────────────────────

CLR_TEXT_CONTENT_PRIMARY = "#dddddd"
CLR_TEXT_CONTENT_SECONDARY = ColorUtils.blend(CLR_TEXT_CONTENT_PRIMARY, _THEME_DARK, 0.24)
CLR_TEXT_LABEL_UI = ColorUtils.blend(CLR_TEXT_CONTENT_PRIMARY, _THEME_DARK, 0.40)
CLR_TEXT_LABEL_GROUP = ColorUtils.blend(CLR_TEXT_CONTENT_PRIMARY, _THEME_DARK, 0.65)
CLR_TEXT_LABEL_SUBGROUP = CLR_TEXT_LABEL_GROUP
CLR_TEXT_LABEL_COUNT = ColorUtils.blend(CLR_TEXT_CONTENT_PRIMARY, _THEME_DARK, 0.72)
CLR_TEXT_CONTENT_UNSCORED = CLR_TEXT_LABEL_GROUP
CLR_TEXT_CONTENT_MUTED = ColorUtils.blend(CLR_TEXT_CONTENT_PRIMARY, _THEME_DARK, 0.80)

# ── Surface roles (defined + derived) ────────────────────────────────────────

CLR_SURFACE_APP_MAIN = "#07071a"
CLR_SURFACE_APP_ALT = ColorUtils.blend(CLR_SURFACE_APP_MAIN, "#1c1c34", 0.45)
CLR_SURFACE_PANEL = ColorUtils.blend(CLR_SURFACE_APP_MAIN, "#1f1f38", 0.55)
CLR_SURFACE_HEADER = ColorUtils.blend(CLR_SURFACE_APP_MAIN, "#2a4478", 0.48)
CLR_SURFACE_HEADER_BORDER = ColorUtils.blend(CLR_SURFACE_HEADER, _THEME_DARK, 0.35)
CLR_SURFACE_SCORE_AREA = ColorUtils.blend(CLR_SURFACE_APP_MAIN, _THEME_DARK, 0.50)
CLR_SURFACE_DEEP = ColorUtils.blend(CLR_SURFACE_SCORE_AREA, _THEME_DARK, 0.30)

# ── Neutral surfaces (defined + derived) ─────────────────────────────────────

CLR_SURFACE_NEUTRAL_OVERLAY = "rgba(0, 0, 0, 140)"
CLR_SURFACE_NEUTRAL = ColorUtils.blend(CLR_SURFACE_APP_MAIN, "#2a2a36", 0.52)
CLR_SURFACE_SEPARATOR = ColorUtils.blend(CLR_SURFACE_HEADER_BORDER, "#3a3a66", 0.45)
CLR_CHIP_NEUTRAL_TEXT = "#9a9ab0"

# ── Interaction accent colors ────────────────────────────────────────────────

CLR_INTERACTIVE = CLR_STATE_SELECTED_BORDER
CLR_INTERACTIVE_BG = ColorUtils.blend(_THEME_DARK, CLR_INTERACTIVE, 0.22)
CLR_INTERACTIVE_BDR = ColorUtils.blend(_THEME_DARK, CLR_INTERACTIVE, 0.34)
CLR_INTERACTIVE_HOV = ColorUtils.blend(CLR_INTERACTIVE, "#8ff8e0", 0.30)
CLR_HIGHLIGHT = "#eee"

# ── Value colors ──────────────────────────────────────────────────────────────

CLR_VALUE_POS = CLR_INTERACTIVE
CLR_VALUE_POS_BG = CLR_INTERACTIVE_BG
CLR_VALUE_NEG = "#e04040"
CLR_VALUE_NEG_BG = ColorUtils.blend(_THEME_DARK, CLR_VALUE_NEG, 0.15)
CLR_VALUE_NEUTRAL = "#888888"
_CLR_AGE_OLD = _CLR_RED

# ── Gender colors ─────────────────────────────────────────────────────────────

CLR_GENDER_MALE = "#2aaa99"
CLR_GENDER_FEMALE = "#bb88dd"
CLR_GENDER_UNKNOWN = "#ccaa44"

_CHIP_GENDER_MALE = (ColorUtils.derive_chip_bg(CLR_GENDER_MALE, CLR_SURFACE_SCORE_AREA), CLR_GENDER_MALE)
_CHIP_GENDER_FEMALE = (ColorUtils.derive_chip_bg(CLR_GENDER_FEMALE, CLR_SURFACE_SCORE_AREA), CLR_GENDER_FEMALE)
_CHIP_GENDER_UNKNOWN = (ColorUtils.derive_chip_bg(CLR_GENDER_UNKNOWN, CLR_SURFACE_SCORE_AREA), CLR_GENDER_UNKNOWN)

# ── Sexuality display ─────────────────────────────────────────────────────────

_SEX_EMOJI_GAY = "🏳️‍🌈"
_SEX_EMOJI_BI = "BI"

# ── Chip appearance constants ─────────────────────────────────────────────────

_CHIP_H = 15
_CHIP_PAD_X = 5
_CHIP_GAP = 4
_CHIP_RADIUS = 5

# ── Chip color pairs (bg, fg) ─────────────────────────────────────────────────

_CHIP_TOP_PRIORITY = ("#004040", "#60e8d8")
_CHIP_DESIRABLE = ("#1d4a1d", "#a0e8a0")
_CHIP_UNDESIRABLE = (CLR_SURFACE_NEUTRAL_OVERLAY, "#e8a0a0")
_CHIP_NEUTRAL = ("#3a3a10", "#d8d870")
_CHIP_UNDECIDED = ("#252535", CLR_VALUE_NEUTRAL)
_CHIP_LOVE = ("#581838", "#ff88cc")   # bright pink/magenta — combined love (scope + room)
_CHIP_HATE = ("#4a2000", "#ff8822")   # bright orange — combined hate (scope + room)
_CHIP_LOVE_OUTLINE = ColorUtils.with_saturation("#581838", 1.0, min_value=0.75)
_CHIP_HATE_OUTLINE = ColorUtils.with_saturation("#4a2000", 1.0, min_value=0.75)
_CHIP_AGGRO_HI = ("#3a1a1a", "#cc6666")
_CHIP_AGGRO_LO = ("#1a2a3a", "#6699cc")
_CHIP_AGE_WARN = ("#3a2010", "#cc8833")
# Low-rarity dim: warm brown — visible against dark navy, distinct from green/red
_CHIP_RARITY_DIM = ("#2e1a06", "#a07840")
_CHIP_DIM = (CLR_SURFACE_NEUTRAL, CLR_TEXT_CONTENT_UNSCORED)
_CHIP_NEUTRAL_STABLE = (CLR_SURFACE_NEUTRAL, CLR_CHIP_NEUTRAL_TEXT)
_CHIP_NEUTRAL_FAINT = ("#13131a", "#7d7d96")
_CHIP_OVERFLOW_LOVE = ("#60284c", "#f08de4")
_CHIP_OVERFLOW_HATE = ("#502810", "#f2a45a")

# ── Table separators / heatmap visuals ───────────────────────────────────────

_SEP_BAND_BG = QColor(CLR_SURFACE_HEADER)
_HEAT_POS = QColor(*ColorUtils.parse_hex(CLR_DESIRABLE), 55)
_HEAT_NEG = QColor(*ColorUtils.parse_hex(CLR_UNDESIRABLE), 55)

# ── Backward-compatible aliases ───────────────────────────────────────────────

_SEL_BG = CLR_STATE_SELECTED_BG
_SEL_FG = CLR_STATE_SELECTED_FG
_SEL_BORDER = CLR_STATE_SELECTED_BORDER
_DIM_FG = CLR_STATE_SUBDUED_FG
_DIM_BG = CLR_STATE_SUBDUED_BG
_DIM_HOVER_FG = CLR_STATE_SUBDUED_HOVER_FG
_DIM_HOVER_BG = CLR_STATE_SUBDUED_HOVER_BG
_DIM_LABEL_FG = CLR_LABEL_SUBDUED

CLR_TEXT_PRIMARY = CLR_TEXT_CONTENT_PRIMARY
CLR_TEXT_SECONDARY = CLR_TEXT_CONTENT_SECONDARY
CLR_TEXT_UI_LABEL = CLR_TEXT_LABEL_UI
CLR_TEXT_GROUP = CLR_TEXT_LABEL_GROUP
CLR_TEXT_SUBLABEL = CLR_TEXT_LABEL_SUBGROUP
CLR_TEXT_COUNT = CLR_TEXT_LABEL_COUNT
CLR_TEXT_GRAYEDOUT = CLR_TEXT_CONTENT_UNSCORED
CLR_TEXT_MUTED = CLR_TEXT_CONTENT_MUTED

CLR_BG_MAIN = CLR_SURFACE_APP_MAIN
CLR_BG_ALT = CLR_SURFACE_APP_ALT
CLR_BG_SCORE_AREA = CLR_SURFACE_SCORE_AREA
CLR_BG_PANEL = CLR_SURFACE_PANEL
CLR_BG_HEADER = CLR_SURFACE_HEADER
CLR_BG_HEADER_BDR = CLR_SURFACE_HEADER_BORDER
CLR_BG_DEEP = CLR_SURFACE_DEEP
_NEUTRAL_SURFACE = CLR_SURFACE_NEUTRAL_OVERLAY
