"""Collapsible left-panel splitter for the Breed Priority view.

Click-only splitter handle that collapses/expands the left pane with an
arrow indicator tab. No drag-to-resize.
"""

from PySide6.QtWidgets import QSplitter, QSplitterHandle
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QBrush, QPainter, QFont


# Must fit: room-row fixed labels (3×32 + 44 = 140px) + longest room-name checkbox
# (~94px) + row spacing + room-checks left margin (6px) + panel margins (16px) ≈ 268px.
# 280 adds headroom for font variation.
LEFT_PANEL_W = 280   # expanded width of the left scope/weights panel


class CollapseHandle(QSplitterHandle):
    """Vertical splitter handle that collapses/expands the left pane on click.

    Draws a centred tab indicator (◀ / ▶) instead of offering drag-to-resize.
    """

    _TAB_H   = 44
    _BG      = QColor("#131326")
    _TAB_BG  = QColor("#22224a")
    _TAB_BDR = QColor("#3a3a70")
    _ARROW   = QColor("#8888cc")
    _ARROW_H = QColor("#aaaaee")

    def __init__(self, orientation, parent):
        super().__init__(orientation, parent)
        self.setCursor(Qt.ArrowCursor)
        self._hovered = False

    def sizeHint(self):
        sh = super().sizeHint()
        sh.setWidth(14)
        return sh

    def enterEvent(self, event):
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovered = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        r = self.rect()

        # Background stripe
        painter.fillRect(r, self._BG)

        # Subtle centre line
        cx = r.width() // 2
        painter.setPen(QColor("#1e1e3a"))
        painter.drawLine(cx, 0, cx, r.height())

        # Tab pill centred vertically
        tab_w   = r.width() - 4
        tab_h   = self._TAB_H
        tab_x   = (r.width() - tab_w) // 2
        tab_y   = (r.height() - tab_h) // 2

        tab_color = self._TAB_BDR if self._hovered else self._TAB_BG
        painter.setBrush(QBrush(tab_color))
        painter.setPen(self._TAB_BDR)
        painter.drawRoundedRect(tab_x, tab_y, tab_w, tab_h, 4, 4)

        # Arrow (◀ collapsed → ▶ expand, ◀ expanded → collapse)
        collapsed = self.splitter().sizes()[0] == 0
        arrow = "▶" if collapsed else "◀"
        arrow_color = self._ARROW_H if self._hovered else self._ARROW
        painter.setPen(arrow_color)
        font = QFont()
        font.setPointSize(7)
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(tab_x, tab_y, tab_w, tab_h, Qt.AlignCenter, arrow)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            s = self.splitter()
            sizes = s.sizes()
            if sizes[0] == 0:
                s.setSizes([LEFT_PANEL_W, max(0, sizes[1])])
            else:
                s.setSizes([0, sizes[0] + sizes[1]])
            self.update()
            event.accept()
        else:
            super().mousePressEvent(event)

    # Swallow drag events so the handle is click-only
    def mouseMoveEvent(self, event):   event.ignore()
    def mouseReleaseEvent(self, event): event.ignore()


class CollapseSplitter(QSplitter):
    """QSplitter that installs CollapseHandle for all handles."""
    def createHandle(self):
        return CollapseHandle(self.orientation(), self)
