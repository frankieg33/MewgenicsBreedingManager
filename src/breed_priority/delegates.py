"""Breed Priority — custom delegates, overlays, and UI helper widgets.

Standalone module — no imports from mewgenics_manager to avoid circular deps.
"""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame,
    QTableWidget, QTableWidgetItem, QHeaderView,
    QListWidget,
    QComboBox, QLineEdit, QPushButton, QDialog, QGridLayout,
    QStyledItemDelegate, QApplication, QStyle,
)
from PySide6.QtCore import Qt, Signal, QTimer, QObject, QEvent, QRect, QSize
from PySide6.QtGui import QColor, QBrush, QPainter, QPen, QFont, QFontMetrics
from PySide6.QtWidgets import QToolTip

from .columns import (
    COL_NAME, _SEP_COLS, _SEP_WIDTH,
    _CHIP_ROLE, _SCORE_SECONDARY_ROLE, _HEATMAP_ROLE,
    _TRAIT_NAME_ROLE, _TRAIT_SUMMARY_ROLE,
    _LOVE_SCORE_COLS, _HATE_SCORE_COLS,
)
from .theme import (
    _CHIP_H, _CHIP_PAD_X, _CHIP_GAP, _CHIP_RADIUS,
    _CHIP_DESIRABLE, _CHIP_UNDESIRABLE, _CHIP_DIM,
    _SEP_BAND_BG,
    _HEAT_POS, _HEAT_NEG,
    CLR_DESIRABLE, CLR_UNDESIRABLE,
    CLR_VALUE_POS, CLR_VALUE_NEG, CLR_VALUE_NEUTRAL,
    CLR_TEXT_CONTENT_PRIMARY, CLR_TEXT_LABEL_UI, CLR_TEXT_CONTENT_SECONDARY, CLR_TEXT_CONTENT_UNSCORED,
    CLR_SURFACE_APP_MAIN, CLR_SURFACE_APP_ALT,
    CLR_SURFACE_HEADER, CLR_SURFACE_HEADER_BORDER, CLR_SURFACE_SCORE_AREA,
    CLR_SURFACE_SEPARATOR, CLR_SURFACE_NEUTRAL, CLR_SURFACE_NEUTRAL_OVERLAY,
    RATING_ITEM_COLORS,
    _CHIP_OVERFLOW_LOVE, _CHIP_OVERFLOW_HATE,
    _CHIP_LOVE_OUTLINE, _CHIP_HATE_OUTLINE,
)
from .scoring import (
    TRAIT_RATING_LABELS, TRAIT_RATING_VALUES, RATING_SHORT_LABELS,
)
from .styles import _PRIORITY_COMBO_STYLE


# ── Chip layout ──────────────────────────────────────────────────────────────

def _fit_chips(chips: list, available_width: int, fm: QFontMetrics) -> tuple:
    """Return (visible_chips, hidden_count) given available pixel width.

    Reserves room for a '+N' indicator pill when chips would overflow.
    """
    IND_W = fm.horizontalAdvance("+99") + 2 * _CHIP_PAD_X
    x = 4
    for i, chip in enumerate(chips):
        name, bg, fg = chip[0], chip[1], chip[2]
        chip_w = fm.horizontalAdvance(name) + 2 * _CHIP_PAD_X
        hidden = len(chips) - i
        extra = (_CHIP_GAP + IND_W) if hidden > 1 else 0
        if x + chip_w + extra > available_width - 2:
            return chips[:i], hidden
        x += chip_w + _CHIP_GAP
    return chips, 0


def _to_qcolor(value) -> QColor:
    """Normalize QColor-compatible values to QColor."""
    if isinstance(value, QColor):
        return QColor(value)
    return QColor(value or CLR_SURFACE_NEUTRAL)


def _avg_qcolor(values: list[QColor], fallback: QColor) -> QColor:
    """Average a list of colors channel-by-channel."""
    if not values:
        return QColor(fallback)
    n = len(values)
    r = sum(c.red() for c in values) // n
    g = sum(c.green() for c in values) // n
    b = sum(c.blue() for c in values) // n
    a = sum(c.alpha() for c in values) // n
    return QColor(r, g, b, a)


def _readable_text_for_bg(bg: QColor, preferred: QColor) -> QColor:
    """Keep averaged text color when readable, otherwise use a safe high-contrast tone."""
    def _luma(c: QColor) -> float:
        return 0.2126 * c.redF() + 0.7152 * c.greenF() + 0.0722 * c.blueF()

    if abs(_luma(preferred) - _luma(bg)) >= 0.34:
        return preferred
    return QColor("#f0f0f0" if _luma(bg) < 0.45 else "#181820")


def _overflow_chip_style(index, hidden_chips: list) -> tuple[QColor, QColor]:
    """Derive overflow chip style from hidden chips, with semantic overrides."""
    if index.column() in _LOVE_SCORE_COLS:
        return QColor(_CHIP_OVERFLOW_LOVE[0]), QColor(_CHIP_OVERFLOW_LOVE[1])
    if index.column() in _HATE_SCORE_COLS:
        return QColor(_CHIP_OVERFLOW_HATE[0]), QColor(_CHIP_OVERFLOW_HATE[1])

    bg_colors = [_to_qcolor(chip[1]) for chip in hidden_chips]
    fg_colors = [_to_qcolor(chip[2]) for chip in hidden_chips]
    avg_bg = _avg_qcolor(bg_colors, QColor(CLR_SURFACE_NEUTRAL))
    avg_fg = _avg_qcolor(fg_colors, QColor(CLR_TEXT_LABEL_UI))
    return avg_bg, _readable_text_for_bg(avg_bg, avg_fg)


# ── Heatmap painting ─────────────────────────────────────────────────────────

def _paint_heatmap_bar(painter, rect, heat: float):
    """Draw a heatmap background bar into *rect*.  *heat* is signed normalised intensity."""
    intensity = abs(heat)
    if intensity < 0.001:
        return
    base = _HEAT_POS if heat > 0 else _HEAT_NEG
    wash = QColor(base.red(), base.green(), base.blue(), 18)
    painter.fillRect(rect, wash)
    alpha = int(30 + 130 * min(1.0, intensity))
    bar_color = QColor(base.red(), base.green(), base.blue(), alpha)
    bar_w = max(2, int(rect.width() * min(1.0, intensity)))
    painter.fillRect(QRect(rect.x(), rect.y(), bar_w, rect.height()), bar_color)
    edge_color = QColor(base.red(), base.green(), base.blue(), 200)
    painter.fillRect(QRect(rect.x(), rect.y(), 1, rect.height()), edge_color)


class _ChipOverflowPopup(QFrame):
    """Frameless popup that shows all chips in a multi-row layout.
    Uses Qt.Popup so it auto-dismisses when focus is lost.
    """
    _PAD   = 8
    _ROW_H = _CHIP_H + 6
    _MAX_W = 340

    def __init__(self, chips: list, global_pos):
        super().__init__(None, Qt.Popup | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self._chips = chips
        self._rows  = self._layout()
        n = len(self._rows)
        h = self._PAD * 2 + n * self._ROW_H + max(0, n - 1) * 2
        self.setFixedSize(self._MAX_W, h)
        # Position below click, clamped to screen
        screen = QApplication.primaryScreen().availableGeometry()
        px = min(global_pos.x(), screen.right()  - self._MAX_W)
        py = min(global_pos.y() + 6, screen.bottom() - h)
        self.move(px, py)

    def _layout(self):
        fm = QFontMetrics(QApplication.font())
        rows, row, x = [], [], self._PAD
        for chip in self._chips:
            w = fm.horizontalAdvance(chip[0]) + 2 * _CHIP_PAD_X
            if row and x + w > self._MAX_W - self._PAD:
                rows.append(row)
                row, x = [], self._PAD
            row.append(chip)
            x += w + _CHIP_GAP
        if row:
            rows.append(row)
        return rows

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(CLR_SURFACE_APP_ALT))
        painter.drawRoundedRect(self.rect(), 6, 6)
        painter.setPen(QColor("#334466"))
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1), 6, 6)

        fm = QFontMetrics(painter.font())
        y = self._PAD
        for row in self._rows:
            x = self._PAD
            chip_y = y + (self._ROW_H - _CHIP_H) // 2
            for name, bg, fg in row:
                w = fm.horizontalAdvance(name) + 2 * _CHIP_PAD_X
                r = QRect(x, chip_y, w, _CHIP_H)
                painter.setPen(Qt.NoPen)
                painter.setBrush(QColor(bg))
                painter.drawRoundedRect(r, _CHIP_RADIUS, _CHIP_RADIUS)
                painter.setPen(QColor(fg))
                painter.drawText(r, Qt.AlignCenter, name)
                x += w + _CHIP_GAP
            y += self._ROW_H + 2
        painter.end()


class _HoverTooltipPopup(QFrame):
    """Custom hover tooltip with fixed width for reliable sizing."""

    _W = 540
    _PAD = 8

    def __init__(self):
        super().__init__(None, Qt.ToolTip | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setStyleSheet(
            f"QFrame {{ background:{CLR_SURFACE_APP_ALT}; border:1px solid #2c3e5a; border-radius:4px; }}"
            f"QLabel {{ background:transparent; color:{CLR_TEXT_CONTENT_SECONDARY}; }}"
        )
        vb = QVBoxLayout(self)
        vb.setContentsMargins(self._PAD, self._PAD, self._PAD, self._PAD)
        vb.setSpacing(0)
        self._label = QLabel("")
        self._label.setTextFormat(Qt.RichText)
        self._label.setWordWrap(True)
        self._label.setFixedWidth(self._W - (self._PAD * 2))
        self._label.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        vb.addWidget(self._label)

    def show_html(self, html: str, global_pos):
        self._label.setText(html or "")
        self._label.adjustSize()
        h = self._label.sizeHint().height() + (self._PAD * 2)
        self.setFixedSize(self._W, max(40, h))
        screen = QApplication.primaryScreen().availableGeometry()
        px = min(global_pos.x() + 12, screen.right() - self.width())
        py = min(global_pos.y() + 18, screen.bottom() - self.height())
        self.move(px, py)
        self.show()
        self.raise_()


class _TraitNameDelegate(QStyledItemDelegate):
    """Paints trait name in normal color and stat summary in a dimmer color."""

    def paint(self, painter, option, index):
        self.initStyleOption(option, index)
        style = option.widget.style() if option.widget else QApplication.style()
        style.drawPrimitive(QStyle.PE_PanelItemViewItem, option, painter, option.widget)

        name = index.data(_TRAIT_NAME_ROLE) or index.data(Qt.DisplayRole) or ""
        summary = index.data(_TRAIT_SUMMARY_ROLE) or ""

        painter.save()
        r = option.rect.adjusted(4, 0, -4, 0)
        fg = index.data(Qt.ForegroundRole)
        name_color = fg.color() if fg and hasattr(fg, "color") else QColor(CLR_TEXT_CONTENT_PRIMARY)
        summary_color = QColor(CLR_TEXT_LABEL_UI)

        fm = QFontMetrics(painter.font())
        name_w = fm.horizontalAdvance(name)

        # Draw name
        painter.setPen(name_color)
        painter.drawText(r, Qt.AlignLeft | Qt.AlignVCenter, name)

        # Draw summary in dimmer color after the name
        if summary:
            summary_r = r.adjusted(name_w + fm.horizontalAdvance("  "), 0, 0, 0)
            painter.setPen(summary_color)
            # Elide if it doesn't fit
            elided = fm.elidedText(summary, Qt.ElideRight, summary_r.width())
            painter.drawText(summary_r, Qt.AlignLeft | Qt.AlignVCenter, elided)

        painter.restore()


class _TraitChipDelegate(QStyledItemDelegate):
    """Renders trait name chips with individual per-trait colored pill backgrounds.
    Shows a '+N' overflow indicator when the column is too narrow, and opens
    a floating popup with all chips on click.
    """

    def paint(self, painter, option, index):
        chips = index.data(_CHIP_ROLE)
        if not chips:
            super().paint(painter, option, index)

            return

        self.initStyleOption(option, index)
        style = option.widget.style() if option.widget else QApplication.style()
        style.drawPrimitive(QStyle.PE_PanelItemViewItem, option, painter, option.widget)

        # Heatmap bar (drawn behind chips)
        _heat = index.data(_HEATMAP_ROLE)
        if _heat is not None:
            _paint_heatmap_bar(painter, option.rect, _heat)

        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)

        fm         = QFontMetrics(painter.font())
        chip_top   = option.rect.y() + (option.rect.height() - _CHIP_H) // 2
        x          = option.rect.x() + 4
        avail      = option.rect.width()
        visible, hidden_count = _fit_chips(chips, avail, fm)

        _base_font = painter.font()
        _big_font = QFont(_base_font)
        if _big_font.pointSizeF() > 0:
            _big_font.setPointSizeF(_big_font.pointSizeF() + 3)
        else:
            _big_font.setPixelSize(_big_font.pixelSize() + 3)
        _big_fm = QFontMetrics(_big_font)

        for chip in visible:
            name, bg_color, text_color = chip[0], chip[1], chip[2]
            outline_color = chip[3] if len(chip) > 3 else None
            _is_emoji = name and ord(name[0]) > 0x2600
            _cfm = _big_fm if _is_emoji else fm
            chip_w  = _cfm.horizontalAdvance(name) + 2 * _CHIP_PAD_X
            chip_rect = QRect(x, chip_top, chip_w, _CHIP_H)
            painter.setBrush(QColor(bg_color))
            if outline_color:
                painter.setPen(QPen(QColor(outline_color), 1))
            else:
                painter.setPen(Qt.NoPen)
            painter.drawRoundedRect(chip_rect, _CHIP_RADIUS, _CHIP_RADIUS)
            if _is_emoji:
                painter.setFont(_big_font)
            painter.setPen(QColor(text_color))
            painter.drawText(chip_rect, Qt.AlignCenter, name)
            if _is_emoji:
                painter.setFont(_base_font)
            x += chip_w + _CHIP_GAP

        if hidden_count:
            ind_text = f"+{hidden_count}"
            ind_w    = fm.horizontalAdvance(ind_text) + 2 * _CHIP_PAD_X
            ind_rect = QRect(x, chip_top, ind_w, _CHIP_H)
            _hidden_chips = chips[len(visible):]
            _ov_bg, _ov_fg = _overflow_chip_style(index, _hidden_chips)
            painter.setBrush(_ov_bg)
            if index.column() in _LOVE_SCORE_COLS:
                painter.setPen(QPen(QColor(_CHIP_LOVE_OUTLINE), 1))
            elif index.column() in _HATE_SCORE_COLS:
                painter.setPen(QPen(QColor(_CHIP_HATE_OUTLINE), 1))
            else:
                painter.setPen(Qt.NoPen)
            painter.drawRoundedRect(ind_rect, _CHIP_RADIUS, _CHIP_RADIUS)
            painter.setPen(_ov_fg)
            painter.drawText(ind_rect, Qt.AlignCenter, ind_text)

        _score_sub = index.data(_SCORE_SECONDARY_ROLE)
        if _score_sub:
            _sf = QFont(painter.font())
            _sf.setPointSizeF(max(6.0, _sf.pointSizeF() * 0.72))
            painter.setFont(_sf)
            painter.setPen(QColor(CLR_VALUE_POS if _score_sub.startswith("+") else CLR_VALUE_NEG if _score_sub.startswith("-") else CLR_VALUE_NEUTRAL))
            _sub_rect = QRect(option.rect.x(), option.rect.bottom() - QFontMetrics(_sf).height() - 1,
                              option.rect.width(), QFontMetrics(_sf).height() + 2)
            painter.drawText(_sub_rect, Qt.AlignCenter, _score_sub)

        painter.restore()

    def sizeHint(self, option, index):
        sh = super().sizeHint(option, index)
        return QSize(sh.width(), max(sh.height(), _CHIP_H + 8))


class _SexChipDelegate(QStyledItemDelegate):
    """Renders the Sexual column as a single pill chip with a ~30 % larger font."""

    def paint(self, painter, option, index):
        chips = index.data(_CHIP_ROLE)
        self.initStyleOption(option, index)
        style = option.widget.style() if option.widget else QApplication.style()
        style.drawPrimitive(QStyle.PE_PanelItemViewItem, option, painter, option.widget)

        # Heatmap bar (drawn behind chip/text)
        _heat = index.data(_HEATMAP_ROLE)
        if _heat is not None:
            _paint_heatmap_bar(painter, option.rect, _heat)

        if not chips:
            # Score mode: draw plain centred text with the item's foreground colour
            text = index.data(Qt.DisplayRole) or ""
            if text:
                fg = index.data(Qt.ForegroundRole)
                painter.save()
                if fg:
                    painter.setPen(fg.color() if hasattr(fg, "color") else QColor(str(fg)))
                painter.drawText(option.rect, Qt.AlignCenter, text)
                painter.restore()
            return

        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)

        font = painter.font()
        if font.pointSizeF() > 0:
            font.setPointSizeF(font.pointSizeF() * 1.3)
        else:
            # Font uses pixel size — scale that instead
            px = font.pixelSize()
            if px > 0:
                font.setPixelSize(int(px * 1.3))
        painter.setFont(font)
        fm = QFontMetrics(font)

        chip_h   = fm.height() + 4
        chip_top = option.rect.y() + (option.rect.height() - chip_h) // 2
        name, bg_color, text_color = chips[0]
        chip_w   = fm.horizontalAdvance(name) + 2 * _CHIP_PAD_X
        x        = option.rect.x() + max(4, (option.rect.width() - chip_w) // 2)
        chip_rect = QRect(x, chip_top, chip_w, chip_h)

        painter.setBrush(QColor(bg_color))
        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(chip_rect, _CHIP_RADIUS, _CHIP_RADIUS)
        painter.setPen(QColor(text_color))
        painter.drawText(chip_rect, Qt.AlignCenter, name)

        _score_sub = index.data(_SCORE_SECONDARY_ROLE)
        if _score_sub:
            _sf = QFont(painter.font())
            _sf.setPointSizeF(max(6.0, _sf.pointSizeF() * 0.72))
            painter.setFont(_sf)
            painter.setPen(QColor(CLR_VALUE_POS if _score_sub.startswith("+") else CLR_VALUE_NEG if _score_sub.startswith("-") else CLR_VALUE_NEUTRAL))
            _sub_rect = QRect(option.rect.x(), option.rect.bottom() - QFontMetrics(_sf).height() - 1,
                              option.rect.width(), QFontMetrics(_sf).height() + 2)
            painter.drawText(_sub_rect, Qt.AlignCenter, _score_sub)

        painter.restore()

    def sizeHint(self, option, index):
        sh = super().sizeHint(option, index)
        return QSize(sh.width(), max(sh.height(), _CHIP_H + 8))


# ── Both-mode delegate ────────────────────────────────────────────────────────

class _BothModeDelegate(QStyledItemDelegate):
    """For 'Both' display mode: renders value text (top) + score subscript (bottom, dim, smaller).
    Also draws heatmap bars when heatmap data is present."""

    def paint(self, painter, option, index):
        score_sub = index.data(_SCORE_SECONDARY_ROLE)
        _heat = index.data(_HEATMAP_ROLE)
        if not score_sub and _heat is None:
            super().paint(painter, option, index)

            return
        if not score_sub and _heat is not None:
            # Heatmap only (no "both" subscript) — draw bar + standard text
            self.initStyleOption(option, index)
            style = option.widget.style() if option.widget else QApplication.style()
            style.drawPrimitive(QStyle.PE_PanelItemViewItem, option, painter, option.widget)
            painter.save()
            _paint_heatmap_bar(painter, option.rect, _heat)
            fg = index.data(Qt.ForegroundRole)
            text = index.data(Qt.DisplayRole) or ""
            if text and abs(_heat) > 0.001:
                fm = QFontMetrics(painter.font())
                tw = fm.horizontalAdvance(text) + 2 * _CHIP_PAD_X
                pill_h = _CHIP_H
                pill_x = option.rect.x() + (option.rect.width() - tw) // 2
                pill_y = option.rect.y() + (option.rect.height() - pill_h) // 2
                pill_rect = QRect(pill_x, pill_y, tw, pill_h)
                painter.setBrush(QColor(CLR_SURFACE_NEUTRAL_OVERLAY))
                painter.setPen(Qt.NoPen)
                painter.setRenderHint(QPainter.Antialiasing)
                painter.drawRoundedRect(pill_rect, _CHIP_RADIUS, _CHIP_RADIUS)
                if fg:
                    painter.setPen(fg.color() if hasattr(fg, "color") else QColor(str(fg)))
                painter.drawText(pill_rect, Qt.AlignCenter, text)
            elif text:
                if fg:
                    painter.setPen(fg.color() if hasattr(fg, "color") else QColor(str(fg)))
                painter.drawText(option.rect, Qt.AlignCenter, text)
            painter.restore()

            return

        self.initStyleOption(option, index)
        style = option.widget.style() if option.widget else QApplication.style()
        style.drawPrimitive(QStyle.PE_PanelItemViewItem, option, painter, option.widget)

        painter.save()
        # Heatmap bar behind "both" content
        if _heat is not None:
            _paint_heatmap_bar(painter, option.rect, _heat)
        r = option.rect
        # Primary text – upper ~60% of cell
        val_rect = QRect(r.x(), r.y(), r.width(), int(r.height() * 0.62))
        fg = index.data(Qt.ForegroundRole)
        if fg:
            painter.setPen(fg.color() if hasattr(fg, "color") else QColor(str(fg)))
        text = index.data(Qt.DisplayRole) or ""
        painter.drawText(val_rect, Qt.AlignCenter, text)
        # Score sub – lower ~38% of cell, smaller dim font
        _sf = QFont(painter.font())
        _sf.setPointSizeF(max(6.0, _sf.pointSizeF() * 0.72))
        painter.setFont(_sf)
        painter.setPen(QColor(CLR_VALUE_POS if score_sub.startswith("+") else CLR_VALUE_NEG if score_sub.startswith("-") else CLR_VALUE_NEUTRAL))
        sub_rect = QRect(r.x(), r.y() + int(r.height() * 0.60), r.width(), r.height() - int(r.height() * 0.60))
        painter.drawText(sub_rect, Qt.AlignCenter, score_sub)
        painter.restore()


# Separator columns are now dedicated thin columns (see _SEP_COLS) rather than painted lines

class _SeparatorDelegate(QStyledItemDelegate):
    """Paints a subtle divider band for separator columns."""

    def paint(self, painter, option, index):
        painter.fillRect(option.rect, _SEP_BAND_BG)

    def sizeHint(self, option, index):
        return QSize(_SEP_WIDTH, 22)


class _HeatmapDelegate(QStyledItemDelegate):
    """'Heatmap' display mode: shows value text with a colored background bar
    whose intensity reflects the score magnitude.  Green = positive, red = negative.
    The bar fills the cell width proportionally to the normalised intensity stored
    in ``_HEATMAP_ROLE`` (0.0–1.0).  Falls through to ``_BothModeDelegate`` /
    default for chip columns.
    """

    def paint(self, painter, option, index):
        heat = index.data(_HEATMAP_ROLE)
        if heat is None:
            super().paint(painter, option, index)

            return

        self.initStyleOption(option, index)
        style = option.widget.style() if option.widget else QApplication.style()
        style.drawPrimitive(QStyle.PE_PanelItemViewItem, option, painter, option.widget)

        painter.save()
        r = option.rect
        _paint_heatmap_bar(painter, r, heat)

        # Draw value text inside a dark pill so it's readable over the bar
        painter.setRenderHint(QPainter.Antialiasing)
        fg = index.data(Qt.ForegroundRole)
        text = index.data(Qt.DisplayRole) or ""
        if text and abs(heat) > 0.001:
            fm = QFontMetrics(painter.font())
            tw = fm.horizontalAdvance(text) + 2 * _CHIP_PAD_X
            pill_h = _CHIP_H
            pill_x = r.x() + (r.width() - tw) // 2
            pill_y = r.y() + (r.height() - pill_h) // 2
            pill_rect = QRect(pill_x, pill_y, tw, pill_h)
            painter.setBrush(QColor(CLR_SURFACE_NEUTRAL_OVERLAY))
            painter.setPen(Qt.NoPen)
            painter.drawRoundedRect(pill_rect, _CHIP_RADIUS, _CHIP_RADIUS)
            if fg:
                painter.setPen(fg.color() if hasattr(fg, "color") else QColor(str(fg)))
            painter.drawText(pill_rect, Qt.AlignCenter, text)
        elif text:
            if fg:
                painter.setPen(fg.color() if hasattr(fg, "color") else QColor(str(fg)))
            painter.drawText(r, Qt.AlignCenter, text)

        painter.restore()


# ── Complex Weight column delegate ───────────────────────────────────────────

_CW_CHIP_BG = "#163030"
_CW_CHIP_FG = "#44ddcc"
_CW_CHIP_STAR = "⭐"


class _CWDelegate(QStyledItemDelegate):
    """Renders a Complex Weight column cell.

    Score mode:  coloured score text (or blank if cat didn't match).
    Values mode: star chip centred in cell when matched, blank when not.
    Both mode:   star chip (top) + score subscript (bottom).

    Data roles expected on the item:
      Qt.DisplayRole          — score string for score mode (e.g. "+8.0")
      Qt.ForegroundRole       — colour for score text
      _CHIP_ROLE              — [(_CW_CHIP_STAR, bg, fg)] when matched in values/both
      _SCORE_SECONDARY_ROLE   — subscript string in both mode (e.g. "+8.0")
    """

    def paint(self, painter, option, index):
        chips = index.data(_CHIP_ROLE)
        sub   = index.data(_SCORE_SECONDARY_ROLE)
        text  = index.data(Qt.DisplayRole) or ""

        self.initStyleOption(option, index)
        style = option.widget.style() if option.widget else QApplication.style()
        style.drawPrimitive(QStyle.PE_PanelItemViewItem, option, painter, option.widget)

        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        r = option.rect

        if chips:
            name, bg, fg = chips[0]
            fm = QFontMetrics(painter.font())
            chip_w = fm.horizontalAdvance(name) + 2 * _CHIP_PAD_X
            chip_h = _CHIP_H
            chip_x = r.x() + (r.width()  - chip_w) // 2
            chip_y = r.y() + (r.height() - chip_h) // 2
            chip_rect = QRect(chip_x, chip_y, chip_w, chip_h)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(bg))
            painter.drawRoundedRect(chip_rect, _CHIP_RADIUS, _CHIP_RADIUS)
            painter.setPen(QColor(fg))
            painter.drawText(chip_rect, Qt.AlignCenter, name)

        elif text:
            fg = index.data(Qt.ForegroundRole)
            if fg:
                painter.setPen(fg.color() if hasattr(fg, "color") else QColor(str(fg)))
            painter.drawText(r, Qt.AlignCenter, text)

        if sub:
            sf = QFont(painter.font())
            sf.setPointSizeF(max(6.0, sf.pointSizeF() * 0.72))
            painter.setFont(sf)
            sfm = QFontMetrics(sf)
            sub_h = sfm.height() + 2
            sub_rect = QRect(r.x(), r.bottom() - sub_h, r.width(), sub_h)
            pos = sub.startswith("+")
            neg = sub.startswith("-")
            painter.setPen(QColor(
                CLR_VALUE_POS if pos else CLR_VALUE_NEG if neg else CLR_VALUE_NEUTRAL
            ))
            painter.drawText(sub_rect, Qt.AlignCenter, sub)

        painter.restore()

    def sizeHint(self, option, index):
        sh = super().sizeHint(option, index)
        return QSize(sh.width(), max(sh.height(), _CHIP_H + 8))


# ── Hate-row overlay ──────────────────────────────────────────────────────────

class _HateRowOverlay(QWidget):
    """Transparent overlay on the score-table viewport that draws a red outline
    around any row whose cat is hated by the currently selected cat.

    Sits above all items (transparent to mouse), redraws on scroll/resize.
    """

    _PEN_COLOR = "#bb2222"
    _PEN_WIDTH = 2

    def __init__(self, table):
        super().__init__(table.viewport())
        self._table = table
        self._hate_cat_ids: set[int] = set()
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_NoSystemBackground)
        self.setAttribute(Qt.WA_TranslucentBackground)
        # Geometry is synced lazily in paintEvent; also connect scroll so we repaint
        table.verticalScrollBar().valueChanged.connect(self._sync_and_update)
        table.horizontalScrollBar().valueChanged.connect(self._sync_and_update)
        table.viewport().installEventFilter(self)

    def _sync_and_update(self):
        """Keep overlay covering the full viewport, then repaint."""
        vp = self._table.viewport()
        r = vp.rect()
        if self.geometry() != r:
            self.setGeometry(r)
        self.raise_()
        self.update()

    def set_hate_ids(self, cat_ids: set[int]):
        self._hate_cat_ids = cat_ids
        self._sync_and_update()

    def eventFilter(self, obj, event):
        if obj is self._table.viewport() and event.type() in (QEvent.Resize, QEvent.Show):
            self._sync_and_update()
        return False

    def paintEvent(self, _event):
        # Sync geometry at paint time so it's always correct
        vp_rect = self._table.viewport().rect()
        if self.geometry() != vp_rect:
            self.setGeometry(vp_rect)
            self.raise_()
        if not self._hate_cat_ids:
            return
        table = self._table
        painter = QPainter(self)
        pen = QPen(QColor(self._PEN_COLOR))
        pen.setWidth(self._PEN_WIDTH)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        p = self._PEN_WIDTH // 2
        vw = self.width()
        for r in range(table.rowCount()):
            if table.isRowHidden(r):
                continue
            name_item = table.item(r, COL_NAME)
            if name_item is None:
                continue
            if name_item.data(Qt.UserRole + 1) not in self._hate_cat_ids:
                continue
            # visualRect gives the item's rectangle in viewport coordinates — reliable
            # regardless of sort order, scroll position, or header height.
            vis = table.visualRect(table.indexFromItem(name_item))
            if not vis.isValid() or vis.bottom() < 0 or vis.top() > self.height():
                continue
            painter.drawRect(QRect(p, vis.y() + p, vw - self._PEN_WIDTH,
                                   vis.height() - self._PEN_WIDTH))
        painter.end()


# ── UI helpers ────────────────────────────────────────────────────────────────

class _NumericSortItem(QTableWidgetItem):
    """QTableWidgetItem that sorts numerically via Qt.UserRole."""

    def __lt__(self, other: "QTableWidgetItem") -> bool:
        try:
            return float(self.data(Qt.UserRole)) < float(other.data(Qt.UserRole))
        except (TypeError, ValueError):
            return super().__lt__(other)


_RATING_DESC_COLOR = QColor("#666677")
_RATING_ITEM_PADDING = 4


class _RatingItemDelegate(QStyledItemDelegate):
    """Renders each rating combo item with the label in its rating color and
    the description suffix (' - ...') in a dim grey so the two parts are
    visually distinct."""

    def paint(self, painter, option, index):
        self.initStyleOption(option, index)
        style = option.widget.style() if option.widget else QApplication.style()
        style.drawPrimitive(QStyle.PE_PanelItemViewItem, option, painter, option.widget)

        text = index.data(Qt.DisplayRole) or ""
        fg = index.data(Qt.ForegroundRole)
        label_color = fg.color() if fg and hasattr(fg, "color") else QColor(CLR_TEXT_CONTENT_SECONDARY)

        sep = " - "
        sep_pos = text.find(sep)
        has_desc = sep_pos != -1

        painter.save()
        fm = QFontMetrics(painter.font())
        r = option.rect.adjusted(_RATING_ITEM_PADDING, 0, -_RATING_ITEM_PADDING, 0)

        if has_desc:
            label_text = text[:sep_pos]
            desc_text = text[sep_pos:]           # includes " - "
            label_w = fm.horizontalAdvance(label_text)
            painter.setPen(label_color)
            painter.drawText(r, Qt.AlignLeft | Qt.AlignVCenter, label_text)
            desc_r = r.adjusted(label_w, 0, 0, 0)
            painter.setPen(_RATING_DESC_COLOR)
            elided = fm.elidedText(desc_text, Qt.ElideRight, desc_r.width())
            painter.drawText(desc_r, Qt.AlignLeft | Qt.AlignVCenter, elided)
        else:
            painter.setPen(label_color)
            painter.drawText(r, Qt.AlignLeft | Qt.AlignVCenter, text)

        painter.restore()

    def sizeHint(self, option, index):
        sh = super().sizeHint(option, index)
        return QSize(sh.width(), max(sh.height(), 20))


class _RatingCombo(QComboBox):
    """Rating combo that shows short labels collapsed, long labels in dropdown.

    Each item is colored by its rating via setForeground(); the popup uses
    _RatingItemDelegate to render the label in rating color and the description
    suffix in dim grey.
    """

    def __init__(self):
        super().__init__()
        self.wheelEvent = lambda e: e.ignore()
        self.addItems(RATING_SHORT_LABELS)
        self.view().setItemDelegate(_RatingItemDelegate(self.view()))

    def showPopup(self):
        for i, long in enumerate(TRAIT_RATING_LABELS):
            self.setItemText(i, long)
        super().showPopup()

    def hidePopup(self):
        super().hidePopup()
        for i, short in enumerate(RATING_SHORT_LABELS):
            self.setItemText(i, short)


class _SortHighlightHeader(QHeaderView):
    """Horizontal header that paints the sorted column with a visible highlight.

    All other sections are delegated to the normal QHeaderView paint path.
    The sorted section gets a brighter background, bolder label colour, and
    the sort-direction arrow drawn explicitly - no separate sort-label needed.
    """

    _NORMAL_BG   = QColor(CLR_SURFACE_HEADER)
    _SORTED_BG   = QColor("#1a3060")
    _NORMAL_FG   = QColor(CLR_TEXT_LABEL_UI)
    _SORTED_FG   = QColor("#ccd8f0")
    _BORDER_R    = QColor(CLR_SURFACE_HEADER)
    _BORDER_B    = QColor(CLR_SURFACE_HEADER_BORDER)

    def __init__(self, parent=None):
        super().__init__(Qt.Horizontal, parent)
        self._sort_col   = -1
        self._sort_order = Qt.DescendingOrder

    def set_sort(self, col: int, order):
        self._sort_col   = col
        self._sort_order = order
        self.viewport().update()

    def mousePressEvent(self, event):
        col = self.logicalIndexAt(event.pos())
        # Qt toggles sort direction when the indicator is already on the
        # clicked column.  By silently pre-seeding the indicator to Ascending
        # on a *new* column (signals blocked so no sort fires), Qt's normal
        # click handler will toggle it to Descending - one sort, correct order.
        # Skip pre-seeding for separator columns (sorting by them is meaningless).
        if col not in _SEP_COLS and col != self._sort_col and col >= 0:
            self.blockSignals(True)
            self.setSortIndicator(col, Qt.AscendingOrder)
            self.blockSignals(False)
        super().mousePressEvent(event)

    def paintSection(self, painter, rect, logical_idx):
        if logical_idx != self._sort_col:
            super().paintSection(painter, rect, logical_idx)
            return

        painter.save()
        painter.setClipRect(rect)

        # Highlighted background
        painter.fillRect(rect, self._SORTED_BG)

        # Right + bottom borders to match other sections
        painter.setPen(self._BORDER_R)
        painter.drawLine(rect.right(), rect.top(), rect.right(), rect.bottom())
        painter.setPen(self._BORDER_B)
        painter.drawLine(rect.left(), rect.bottom(), rect.right(), rect.bottom())

        # Label + arrow
        label = self.model().headerData(logical_idx, Qt.Horizontal, Qt.DisplayRole) or ""
        arrow = " ▼" if self._sort_order == Qt.DescendingOrder else " ▲"
        font = painter.font()
        font.setBold(True)
        font.setPointSize(font.pointSize() - 1)   # match the 11px style
        painter.setFont(font)
        painter.setPen(self._SORTED_FG)
        painter.drawText(
            rect.adjusted(4, 0, -4, 0),
            Qt.AlignCenter,
            str(label) + arrow,
        )

        painter.restore()


class _HeaderTooltipFilter(QObject):
    """Event filter that shows per-column tooltips on QHeaderView hover."""

    def __init__(self, header, tips: dict):
        super().__init__(header)
        self._header = header
        self._tips = tips   # {col_idx: str}
        header.viewport().setMouseTracking(True)
        header.viewport().installEventFilter(self)

    def eventFilter(self, obj, event):
        t = event.type()
        if t in (QEvent.MouseMove, QEvent.Type.ToolTip):
            col = self._header.logicalIndexAt(event.pos())
            tip = self._tips.get(col, "")
            gpos = obj.mapToGlobal(event.pos())
            if tip:
                QToolTip.showText(gpos, tip, obj)
            else:
                QToolTip.hideText()
            return t == QEvent.Type.ToolTip   # suppress native ToolTip; pass MouseMove
        return False


class _FastTooltipFilter(QObject):
    """Event filter that shows QTableWidget item tooltips with a short custom delay,
    and handles chip-overflow popup on click.

    Intercepts MouseMove on the table viewport to start a short timer, then calls
    QToolTip.showText() directly - bypassing the platform's ~700ms default delay.
    Also suppresses the system QEvent.ToolTip so it can't re-trigger late.
    On MouseButtonRelease, checks whether the clicked cell has chip overflow and
    shows the _ChipOverflowPopup if so.
    """

    DELAY_MS = 60    # ~1/12th of the typical 700ms system tooltip delay

    def __init__(self, table: QTableWidget):
        super().__init__(table)
        self._table  = table
        self._tip    = ""
        self._gpos   = None
        self._timer  = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(self.DELAY_MS)
        self._timer.timeout.connect(self._show)
        self._chip_popup       = None   # keep reference to prevent GC
        self._hover_popup      = _HoverTooltipPopup()
        self._pending_popup    = None   # (chips, gpos) deferred until event loop clears
        table.viewport().setMouseTracking(True)
        table.viewport().installEventFilter(self)

    def eventFilter(self, obj, event):
        t = event.type()
        if t == QEvent.MouseMove:
            lpos  = event.position().toPoint()
            item  = self._table.itemAt(lpos)
            tip   = item.toolTip() if item else ""
            gpos  = self._table.viewport().mapToGlobal(lpos)
            self._gpos = gpos
            if tip != self._tip:
                self._tip = tip
                self._timer.stop()
                self._hover_popup.hide()
                if tip:
                    self._timer.start()
        elif t == QEvent.MouseButtonRelease:
            lpos  = event.position().toPoint()
            item  = self._table.itemAt(lpos)
            if item is not None:
                chips = item.data(_CHIP_ROLE)
                if chips:
                    fm = QFontMetrics(QApplication.font())
                    col  = self._table.column(item)
                    col_w = self._table.columnWidth(col)
                    _, hidden = _fit_chips(chips, col_w, fm)
                    if hidden:
                        gpos = self._table.viewport().mapToGlobal(lpos)
                        # Defer until event loop is clear so Qt.Popup grab
                        # doesn't fight with in-flight mouse events
                        self._pending_popup = (chips, gpos)
                        QTimer.singleShot(0, self._show_chip_popup)
        elif t == QEvent.Leave:
            self._timer.stop()
            self._hover_popup.hide()
            self._tip = ""
        elif t == QEvent.Type.ToolTip:
            # Suppress the platform-delayed tooltip; we handle it ourselves
            return True
        return False

    def _show(self):
        if self._tip and self._gpos:
            self._hover_popup.show_html(self._tip, self._gpos)

    def _show_chip_popup(self):
        if self._pending_popup:
            chips, gpos = self._pending_popup
            self._pending_popup = None
            self._chip_popup = _ChipOverflowPopup(chips, gpos)
            self._chip_popup.show()


class _ListTooltipFilter(QObject):
    """Fast tooltip filter for QListWidget — shows item tooltips with a short delay."""

    DELAY_MS = 60

    def __init__(self, lst: QListWidget):
        super().__init__(lst)
        self._list  = lst
        self._tip   = ""
        self._gpos  = None
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(self.DELAY_MS)
        self._timer.timeout.connect(self._show)
        self._hover_popup = _HoverTooltipPopup()
        lst.viewport().setMouseTracking(True)
        lst.viewport().installEventFilter(self)

    def eventFilter(self, obj, event):
        t = event.type()
        if t == QEvent.MouseMove:
            lpos = event.position().toPoint()
            item = self._list.itemAt(lpos)
            tip  = item.toolTip() if item else ""
            gpos = self._list.viewport().mapToGlobal(lpos)
            self._gpos = gpos
            if tip != self._tip:
                self._tip = tip
                self._timer.stop()
                self._hover_popup.hide()
                if tip:
                    self._timer.start()
        elif t == QEvent.Leave:
            self._timer.stop()
            self._hover_popup.hide()
            self._tip = ""
        elif t == QEvent.Type.ToolTip:
            return True
        return False

    def _show(self):
        if self._tip and self._gpos:
            self._hover_popup.show_html(self._tip, self._gpos)


class _WeightSpin(QWidget):
    """Compact value editor with visible ▲/▼ buttons."""
    valueChanged = Signal(float)

    _BTN_STYLE = (
        "QPushButton { color:#ccc; background:#3a3a60; border:1px solid #4a4a80;"
        " font-size:8px; padding:0; }"
        "QPushButton:hover { background:#5050a0; }"
        "QPushButton:pressed { background:#6060c0; }"
    )
    _LBL_BASE = (
        f"background:{CLR_SURFACE_APP_ALT};"
        f" border:1px solid {CLR_SURFACE_SEPARATOR}; border-right:none;"
    )

    def __init__(self, value: float, min_val=-50.0, max_val=50.0, step=0.5):
        super().__init__()
        self._value = float(value)
        self._min   = min_val
        self._max   = max_val
        self._step  = step

        hb = QHBoxLayout(self)
        hb.setContentsMargins(0, 0, 0, 0)
        hb.setSpacing(2)

        self._lbl = QLabel(self._fmt(self._value))
        self._lbl.setFixedWidth(36)
        self._lbl.setAlignment(Qt.AlignCenter)
        _f = self._lbl.font()
        _f.setPointSize(8)
        self._lbl.setFont(_f)
        self._update_color()

        btn_col_1 = QWidget()
        bv1 = QVBoxLayout(btn_col_1)
        bv1.setContentsMargins(0, 0, 0, 0)
        bv1.setSpacing(0)

        up = QPushButton("▲")
        up.setFixedSize(18, 11)
        up.setStyleSheet(self._BTN_STYLE)
        up.clicked.connect(self._inc)

        dn = QPushButton("▼")
        dn.setFixedSize(18, 11)
        dn.setStyleSheet(self._BTN_STYLE)
        dn.clicked.connect(self._dec)

        up5 = QPushButton("▲")
        up5.setFixedSize(18, 11)
        up5.setStyleSheet(self._BTN_STYLE)
        up5.clicked.connect(self._inc5)

        dn5 = QPushButton("▼")
        dn5.setFixedSize(18, 11)
        dn5.setStyleSheet(self._BTN_STYLE)
        dn5.clicked.connect(self._dec5)
        bv1.addWidget(up)
        bv1.addWidget(dn)
        btn_col_5 = QWidget()
        bv5 = QVBoxLayout(btn_col_5)
        bv5.setContentsMargins(0, 0, 0, 0)
        bv5.setSpacing(0)
        bv5.addWidget(up5)
        bv5.addWidget(dn5)
        hb.addWidget(btn_col_1)
        hb.addWidget(self._lbl)
        hb.addSpacing(2)
        hb.addWidget(btn_col_5)

    @staticmethod
    def _fmt(v: float) -> str:
        return f"{v:+.1f}"

    def _update_color(self):
        if self._value > 0:
            clr = CLR_DESIRABLE
        elif self._value < 0:
            clr = CLR_UNDESIRABLE
        else:
            clr = CLR_TEXT_CONTENT_UNSCORED
        self._lbl.setStyleSheet(f"color:{clr}; {self._LBL_BASE}")

    def _set(self, val: float):
        val = round(max(self._min, min(self._max, val)) / self._step) * self._step
        if val != self._value:
            self._value = val
            self._lbl.setText(self._fmt(val))
            self._update_color()
            if not self.signalsBlocked():
                self.valueChanged.emit(val)

    def _inc(self): self._set(self._value + self._step)
    def _dec(self): self._set(self._value - self._step)
    def _inc5(self): self._set(self._value + 5.0)
    def _dec5(self): self._set(self._value - 5.0)

    def value(self) -> float:
        return self._value

    def setValue(self, val: float):
        self._value = float(val)
        self._lbl.setText(self._fmt(self._value))
        self._update_color()


class _IntParamSpin(_WeightSpin):
    """Integer-only variant of _WeightSpin - shows plain integers, no +/- sign.

    Used for parameters like stat_7_threshold that are natural counts (1–20).
    """

    def _update_color(self):
        # Threshold / count parameters: always plain; no sign-based colouring
        self._lbl.setStyleSheet(f"color:{CLR_TEXT_CONTENT_SECONDARY}; {self._LBL_BASE}")

    def __init__(self, value: int, min_val=1, max_val=20, step=1):
        super().__init__(float(value), float(min_val), float(max_val), float(step))
        self._lbl.setText(self._fmt(self._value))

    @staticmethod
    def _fmt(v: float) -> str:
        return str(int(round(v)))

    def _set(self, val: float):
        val = float(max(self._min, min(self._max, round(val))))
        if val != self._value:
            self._value = val
            self._lbl.setText(self._fmt(val))
            if not self.signalsBlocked():
                self.valueChanged.emit(val)

    def setValue(self, val: float):
        self._value = float(round(val))
        self._lbl.setText(self._fmt(self._value))


class _ProfileNameEdit(QLineEdit):
    """QLineEdit that accepts focus via single-click without selecting all text."""
    def mousePressEvent(self, event):
        super().mousePressEvent(event)
        if not self.hasSelectedText():
            self.deselect()


class _ConfirmDialog(QDialog):
    """Simple dark-themed confirmation dialog with a message and Ok/Cancel buttons."""

    def __init__(self, title: str, message: str, ok_label: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setStyleSheet(
            f"QDialog {{ background:{CLR_SURFACE_APP_MAIN}; }}"
            f"QLabel  {{ color:{CLR_TEXT_CONTENT_SECONDARY}; font-size:12px; background:transparent; border:none; }}"
            "QPushButton { background:#14142e; color:#8899bb; border:1px solid #2a2a55;"
            "  border-radius:4px; padding:5px 18px; font-size:12px; }"
            "QPushButton:hover { background:#1c1c3a; color:#ccd; border-color:#4444aa; }"
            "QPushButton#ok { background:#0e2030; color:#88aadd; border-color:#2244aa; }"
            "QPushButton#ok:hover { background:#122840; color:#aaccff; border-color:#3366cc; }"
        )
        vb = QVBoxLayout(self)
        vb.setContentsMargins(24, 20, 24, 16)
        vb.setSpacing(16)
        msg_lbl = QLabel(message)
        msg_lbl.setWordWrap(True)
        msg_lbl.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        vb.addWidget(msg_lbl)
        btns = QHBoxLayout()
        btns.addStretch()
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        ok = QPushButton(ok_label)
        ok.setObjectName("ok")
        ok.setDefault(True)
        ok.clicked.connect(self.accept)
        btns.addWidget(cancel)
        btns.addSpacing(8)
        btns.addWidget(ok)
        vb.addLayout(btns)
        self.setMinimumWidth(340)
