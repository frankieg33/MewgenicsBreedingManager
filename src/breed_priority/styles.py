"""Breed Priority — Qt stylesheet strings.

All pre-built stylesheet strings for buttons, tables, combos, and splitters.
Imports theme colors so styles update consistently with theme changes.
"""

from .theme import (
    CLR_TEXT_CONTENT_PRIMARY, CLR_TEXT_CONTENT_SECONDARY, CLR_TEXT_LABEL_UI, CLR_TEXT_LABEL_GROUP,
    CLR_SURFACE_APP_MAIN, CLR_SURFACE_APP_ALT, CLR_SURFACE_HEADER, CLR_SURFACE_HEADER_BORDER,
    CLR_SURFACE_SEPARATOR, CLR_SURFACE_NEUTRAL,
    CLR_INTERACTIVE, CLR_INTERACTIVE_BG, CLR_INTERACTIVE_BDR, CLR_INTERACTIVE_HOV,
    CLR_STATE_SELECTED_FG, CLR_STATE_SELECTED_BG, CLR_STATE_SELECTED_BORDER,
    CLR_STATE_SUBDUED_FG, CLR_STATE_SUBDUED_BG, CLR_STATE_SUBDUED_HOVER_FG, CLR_STATE_SUBDUED_HOVER_BG,
    CLR_DESIRABLE, CLR_UNDESIRABLE,
    ColorUtils,
)


# ── Splitter handle styles ────────────────────────────────────────────────────

SPLITTER_V_STYLE = (
    "QSplitter::handle:vertical {"
    " min-height:6px;"
    " background: qlineargradient(x1:0,y1:0,x2:0,y2:1,"
    " stop:0 #131326, stop:0.25 #1a1a36, stop:0.45 #2e2e58,"
    " stop:0.5 #3c3c70, stop:0.55 #2e2e58, stop:0.75 #1a1a36, stop:1 #131326);"
    " }"
    "QSplitter::handle:vertical:hover {"
    " background: qlineargradient(x1:0,y1:0,x2:0,y2:1,"
    " stop:0 #16162e, stop:0.25 #22224a, stop:0.45 #44449a,"
    " stop:0.5 #5555c0, stop:0.55 #44449a, stop:0.75 #22224a, stop:1 #16162e);"
    " }"
)
SPLITTER_H_STYLE = (
    "QSplitter::handle:horizontal {"
    " min-width:6px;"
    " background: qlineargradient(x1:0,y1:0,x2:1,y2:0,"
    " stop:0 #131326, stop:0.25 #1a1a36, stop:0.45 #2e2e58,"
    " stop:0.5 #3c3c70, stop:0.55 #2e2e58, stop:0.75 #1a1a36, stop:1 #131326);"
    " }"
    "QSplitter::handle:horizontal:hover {"
    " background: qlineargradient(x1:0,y1:0,x2:1,y2:0,"
    " stop:0 #16162e, stop:0.25 #22224a, stop:0.45 #44449a,"
    " stop:0.5 #5555c0, stop:0.55 #44449a, stop:0.75 #22224a, stop:1 #16162e);"
    " }"
)

# ── Toggle / segmented controls ───────────────────────────────────────────────

SEGMENTED_CONTROL_BUTTON_STYLE = """
    QPushButton {{
        color: {fg_dim}; background: {bg_dim}; border: 1px solid #333;
        padding: 1px 7px; font-size: 10px; border-radius: 0px;
    }}
    QPushButton:checked {{
        color: {sel_fg}; background: {sel_bg}; border-color: {sel_border};
    }}
    QPushButton:hover:!checked {{ color: {hover_fg}; background: {hover_bg}; }}
""".format(fg_dim=CLR_STATE_SUBDUED_FG, bg_dim=CLR_STATE_SUBDUED_BG,
           sel_fg=CLR_STATE_SELECTED_FG, sel_bg=CLR_STATE_SELECTED_BG, sel_border=CLR_STATE_SELECTED_BORDER,
           hover_fg=CLR_STATE_SUBDUED_HOVER_FG, hover_bg=CLR_STATE_SUBDUED_HOVER_BG)

# ── Label styles ──────────────────────────────────────────────────────────────

GROUP_LABEL_TEXT_STYLE = (
    f"color:{CLR_TEXT_LABEL_GROUP}; font-size:10px; font-weight:bold; letter-spacing:1px;"
)

# ── Action button styles ──────────────────────────────────────────────────────

ACTION_BUTTON_PRIMARY_EMPHASIS_STYLE = (
    f"QPushButton {{ background:{CLR_INTERACTIVE_BG}; color:{CLR_INTERACTIVE};"
    f" border:1px solid {CLR_INTERACTIVE};"
    " border-radius:4px; padding:3px 4px; font-size:10px; font-weight:bold; }"
    f"QPushButton:hover {{ background:{CLR_INTERACTIVE_BDR}; color:{CLR_INTERACTIVE_HOV}; }}"
)
ACTION_BUTTON_PRIMARY_STYLE = (
    f"QPushButton {{ background:{CLR_INTERACTIVE_BG}; color:{CLR_INTERACTIVE};"
    f" border:1px solid {CLR_INTERACTIVE_BDR};"
    " border-radius:4px; padding:3px 4px; font-size:10px; font-weight:bold; }"
    f"QPushButton:hover {{ background:{CLR_INTERACTIVE_BDR}; color:{CLR_INTERACTIVE_HOV}; }}"
)
ACTION_BUTTON_PRIMARY_COMPACT_STYLE = (
    f"QPushButton {{ background:{CLR_INTERACTIVE_BG}; color:{CLR_INTERACTIVE};"
    f" border:1px solid {CLR_INTERACTIVE_BDR};"
    " border-radius:2px; padding:0 2px; font-size:9px; font-weight:bold; }"
    f"QPushButton:hover {{ background:{CLR_INTERACTIVE_BDR}; }}"
)
ACTION_BUTTON_PRIMARY_LARGE_STYLE = (
    f"QPushButton {{ color:#fff; background:{CLR_INTERACTIVE_BG};"
    f" border:1px solid {CLR_INTERACTIVE};"
    " border-radius:4px; padding:4px 14px; font-size:11px; }"
    f"QPushButton:hover {{ background:{CLR_INTERACTIVE_BDR}; }}"
)
ACTION_BUTTON_SECONDARY_STYLE = (
    "QPushButton { background:#1a1a32; color:#aaa; border:1px solid #2a2a4a;"
    " border-radius:4px; padding:3px 4px; font-size:10px; }"
    f"QPushButton:hover {{ background:{CLR_SURFACE_SEPARATOR}; color:{CLR_TEXT_CONTENT_PRIMARY}; }}"
)
ACTION_BUTTON_SECONDARY_LARGE_STYLE = (
    f"QPushButton {{ color:{CLR_TEXT_CONTENT_SECONDARY}; background:#1a1a32; border:1px solid #2a2a4a;"
    " border-radius:4px; padding:4px 14px; font-size:11px; }"
    f"QPushButton:hover {{ background:{CLR_SURFACE_SEPARATOR}; color:{CLR_TEXT_CONTENT_PRIMARY}; }}"
)
TOGGLE_BUTTON_INACTIVE_STYLE = (
    "QPushButton { background:#1a1a2e; color:#555566; border:1px solid #2a2a44;"
    " border-radius:4px; padding:3px 4px; font-size:10px; }"
    f"QPushButton:hover {{ background:#222238; color:{ColorUtils.blend('#555566', CLR_INTERACTIVE, 0.35)}; }}"
)
TOGGLE_BUTTON_INACTIVE_COMPACT_STYLE = (
    "QPushButton { background:#1a1a2e; color:#555566; border:1px solid #2a2a44;"
    " border-radius:2px; padding:0 2px; font-size:9px; }"
    f"QPushButton:hover {{ background:#222238; color:{ColorUtils.blend('#555566', CLR_INTERACTIVE, 0.35)}; }}"
)

# ── Checkbox styles ────────────────────────────────────────────────────────────

def checkbox_style(
    font_size: int = 11,
    emphasize_checked: bool = False,
    text_color: str = CLR_TEXT_CONTENT_SECONDARY,
) -> str:
    """Return a dark-theme checkbox style with clear checked/hover/disabled states."""
    checked_bg = ColorUtils.blend(CLR_SURFACE_APP_MAIN, CLR_INTERACTIVE, 0.22)
    checked_bdr = ColorUtils.blend(CLR_SURFACE_APP_MAIN, CLR_INTERACTIVE, 0.42)
    checked_fg = CLR_INTERACTIVE_HOV
    hover_fg = ColorUtils.blend(text_color, CLR_INTERACTIVE_HOV, 0.30)
    disabled_fg = ColorUtils.blend(text_color, CLR_SURFACE_APP_MAIN, 0.52)
    checked_block = ""
    if emphasize_checked:
        checked_block = (
            f"QCheckBox:checked {{ color:{checked_fg}; background:{checked_bg};"
            f" border:1px solid {checked_bdr}; border-radius:4px; padding:1px 4px 1px 2px; }}"
        )
    return (
        f"QCheckBox {{ color:{text_color}; font-size:{int(font_size)}px; spacing:6px; border:1px solid transparent;"
        " border-radius:4px; padding:1px 3px 1px 2px; }"
        f"QCheckBox:hover {{ color:{hover_fg}; }}"
        + checked_block +
        f"QCheckBox:disabled {{ color:{disabled_fg}; }}"
    )

# ── Data-view controls ────────────────────────────────────────────────────────

PRIORITY_TABLE_STYLE = f"""
    QTableWidget {{
        background:{CLR_SURFACE_APP_MAIN}; alternate-background-color:{CLR_SURFACE_APP_ALT};
        color:{CLR_TEXT_CONTENT_PRIMARY}; border:none; font-size:12px;
    }}
    QTableWidget::item {{
        padding:3px 4px;
        border-right:1px solid {CLR_SURFACE_HEADER};
    }}
    QTableWidget::item:selected {{ background:#1e3060; color:#fff; }}
    QHeaderView::section {{
        background:{CLR_SURFACE_HEADER}; color:{CLR_TEXT_LABEL_UI}; padding:5px 4px;
        border:none; border-bottom:1px solid {CLR_SURFACE_HEADER_BORDER};
        border-right:1px solid {CLR_SURFACE_HEADER};
        font-size:11px; font-weight:bold;
    }}
    QScrollBar:vertical {{ background:{CLR_SURFACE_APP_MAIN}; width:10px; }}
    QScrollBar::handle:vertical {{
        background:{CLR_SURFACE_SEPARATOR}; border-radius:5px; min-height:20px;
    }}
"""

PRIORITY_COMBO_STYLE = (
    f"QComboBox {{ background:{CLR_SURFACE_APP_ALT}; color:{CLR_TEXT_CONTENT_SECONDARY}; border:1px solid {CLR_SURFACE_SEPARATOR};"
    " padding:1px 4px; font-size:11px; }"
    "QComboBox:hover { border-color:#3a3a7a; }"
    "QComboBox::drop-down { border:none; }"
    # No `color` here — item colors come from setForeground() on each model item
    f"QComboBox QAbstractItemView {{ background:{CLR_SURFACE_APP_ALT};"
    f" selection-background-color:#1e3060; border:1px solid {CLR_SURFACE_SEPARATOR}; }}"
)

# ── Trait tab widget styles ───────────────────────────────────────────────────

_TRAIT_TAB_BASE = (
    f"QTabWidget::pane {{ border: none; background: {CLR_SURFACE_APP_MAIN}; }}"
    f"QTabBar::tab {{"
    f" background: {CLR_SURFACE_APP_ALT}; color: #888;"
    f" padding: 3px 10px; font-size: 10px; font-weight: bold; letter-spacing: 0.5px;"
    f" border: 1px solid {CLR_SURFACE_SEPARATOR}; border-bottom: none; margin-right: 2px;"
    f"}}"
    f"QTabBar::tab:selected {{ background: {CLR_SURFACE_APP_MAIN}; color: #ccc; }}"
    f"QTabBar::tab:hover:!selected {{ background: {CLR_SURFACE_NEUTRAL}; }}"
)

TRAIT_TAB_ABILITIES_STYLE = _TRAIT_TAB_BASE

_CLR_MUTATIONS_TAB_SEL = ColorUtils.blend(CLR_DESIRABLE, "#ffffff", 0.35)
_CLR_DEFECTS_TAB_SEL = ColorUtils.blend(CLR_UNDESIRABLE, "#ffffff", 0.35)

TRAIT_TAB_MUTATIONS_STYLE = (
    _TRAIT_TAB_BASE
    + f"QTabBar::tab:first {{ color: {CLR_DESIRABLE}; }}"
    + f"QTabBar::tab:selected:first {{ color: {_CLR_MUTATIONS_TAB_SEL}; }}"
    + f"QTabBar::tab:last {{ color: {CLR_UNDESIRABLE}; }}"
    + f"QTabBar::tab:selected:last {{ color: {_CLR_DEFECTS_TAB_SEL}; }}"
)

# ── Backward-compatible aliases ───────────────────────────────────────────────

_SEG_BTN_STYLE = SEGMENTED_CONTROL_BUTTON_STYLE
_GROUP_LABEL_STYLE = GROUP_LABEL_TEXT_STYLE
_INTERACTIVE_BTN_ACTIVE = ACTION_BUTTON_PRIMARY_EMPHASIS_STYLE
_INTERACTIVE_BTN_ON = ACTION_BUTTON_PRIMARY_STYLE
_INTERACTIVE_BTN_ON_SM = ACTION_BUTTON_PRIMARY_COMPACT_STYLE
_INTERACTIVE_BTN_LG = ACTION_BUTTON_PRIMARY_LARGE_STYLE
_DIM_BTN = ACTION_BUTTON_SECONDARY_STYLE
_DIM_BTN_LG = ACTION_BUTTON_SECONDARY_LARGE_STYLE
_TOGGLE_OFF_BTN = TOGGLE_BUTTON_INACTIVE_STYLE
_TOGGLE_OFF_BTN_SM = TOGGLE_BUTTON_INACTIVE_COMPACT_STYLE
_PRIORITY_TABLE_STYLE = PRIORITY_TABLE_STYLE
_PRIORITY_COMBO_STYLE = PRIORITY_COMBO_STYLE
