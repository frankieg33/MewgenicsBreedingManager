"""CatTableModel, TagStripDelegate, and sort helper items."""
from typing import Optional

from PySide6.QtWidgets import (
    QApplication, QStyledItemDelegate, QStyle, QStyleOptionViewItem,
    QTableWidgetItem,
)
from PySide6.QtCore import (
    Qt, QAbstractTableModel, QModelIndex, Signal,
)
from PySide6.QtGui import (
    QColor, QBrush, QPalette, QPainter, QIcon, QPixmap, QFont,
)

from save_parser import (
    Cat, STAT_NAMES,
    can_breed, risk_percent,
    get_all_ancestors, get_parents, find_common_ancestors,
    _is_hater_pair, _kinship,
)
from mewgenics.constants import (
    STAT_COLORS, STATUS_COLOR,
    COL_NAME, COL_TAGS, COL_AGE, COL_GEN, COL_ROOM, COL_STAT, COL_ADV, COL_BL, COL_MB, COL_PIN,
    STAT_COLS, COL_SUM, COL_AGG, COL_LIB, COL_INBRD, COL_SEXUALITY,
    COL_RELNS, COL_REL, COL_ABIL, COL_MUTS, COL_GEN_DEPTH, COL_SRC,
)
from mewgenics.utils.localization import ROOM_DISPLAY, STATUS_ABBREV, COLUMNS, _tr
from mewgenics.utils.tags import (
    _cat_tag_pixmap, _cat_tag_summary, _cat_tag_tooltip,
)
from mewgenics.utils.thresholds import EXCEPTIONAL_SUM_THRESHOLD
from mewgenics.utils.cat_analysis import (
    _cat_base_sum, _is_exceptional_breeder,
    _donation_candidate_reason, _is_donation_candidate, _relations_summary,
)
from mewgenics.utils.calibration import _trait_label_from_value, _trait_level_color
from mewgenics.utils.abilities import (
    _mutation_display_name, _strip_tier, _abilities_tooltip, _mutations_tooltip,
)


_STAT_ICON_CACHE: dict[tuple[str, int], QIcon] = {}
_STAT_SVG_PIXMAP_CACHE: dict[tuple[str, int], QPixmap] = {}
_STAT_SVG_NAMES = {
    "STR": "Stat_Strength.svg",
    "DEX": "Stat_Dexterity.svg",
    "CON": "Stat_Constitution.svg",
    "INT": "Stat_Intelligence.svg",
    "SPD": "Stat_Speed.svg",
    "CHA": "Stat_Charisma.svg",
    "LCK": "Stat_Luck.svg",
}


def _stat_svg_pixmap(stat_name: str, size: int = 16) -> QPixmap | None:
    """Load and cache an SVG stat icon as a QPixmap at the given size."""
    key = (stat_name, int(size))
    cached = _STAT_SVG_PIXMAP_CACHE.get(key)
    if cached is not None:
        return cached if not cached.isNull() else None
    svg_name = _STAT_SVG_NAMES.get(stat_name)
    if svg_name is None:
        _STAT_SVG_PIXMAP_CACHE[key] = QPixmap()
        return None
    from pathlib import Path
    from mewgenics.utils.paths import _bundle_dir, _app_dir
    candidates = [
        Path(__file__).resolve().parents[3] / "images" / svg_name,
        Path(_bundle_dir()) / "images" / svg_name,
        Path(_app_dir()) / "images" / svg_name,
    ]
    for path in candidates:
        if path.exists():
            from PySide6.QtSvg import QSvgRenderer
            renderer = QSvgRenderer(str(path))
            if renderer.isValid():
                pix = QPixmap(size, size)
                pix.fill(Qt.transparent)
                painter = QPainter(pix)
                renderer.render(painter)
                painter.end()
                _STAT_SVG_PIXMAP_CACHE[key] = pix
                return pix
    _STAT_SVG_PIXMAP_CACHE[key] = QPixmap()
    return None


def _make_stat_header_icon(stat_name: str, size: int = 16) -> QIcon:
    key = (stat_name, int(size))
    cached = _STAT_ICON_CACHE.get(key)
    if cached is not None:
        return cached
    pix = _stat_svg_pixmap(stat_name, size)
    if pix is not None:
        icon = QIcon(pix)
        _STAT_ICON_CACHE[key] = icon
        return icon
    # Fallback: colored rounded rectangle with stat initial
    pix = QPixmap(size, size)
    pix.fill(Qt.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.Antialiasing)
    _STAT_ICON_COLORS = {
        "STR": QColor(212, 82, 82),
        "DEX": QColor(92, 170, 220),
        "CON": QColor(102, 190, 104),
        "INT": QColor(155, 124, 220),
        "SPD": QColor(214, 164, 72),
        "CHA": QColor(214, 110, 176),
        "LCK": QColor(90, 205, 176),
    }
    color = _STAT_ICON_COLORS.get(stat_name, QColor(100, 100, 115))
    painter.setBrush(QBrush(color))
    painter.setPen(QColor(color.darker(150)))
    painter.drawRoundedRect(1, 1, size - 2, size - 2, 3, 3)
    font = QFont()
    font.setBold(True)
    font.setPointSize(max(6, size // 3))
    painter.setFont(font)
    painter.setPen(QColor(255, 255, 255))
    painter.drawText(pix.rect(), Qt.AlignCenter, stat_name[:1])
    painter.end()
    icon = QIcon(pix)
    _STAT_ICON_CACHE[key] = icon
    return icon


# ── Compatibility check ───────────────────────────────────────────────────────

def _compatibility(focus: 'Cat', other: 'Cat') -> str:
    """
    Returns one of: 'self' | 'incompatible' | 'risky' | 'ok'
    Used to dim rows in the table when a single cat is selected.
    """
    if focus is other:
        return 'self'
    ok, _ = can_breed(focus, other)
    if not ok:
        return 'incompatible'
    # Hate relationship
    if _is_hater_pair(focus, other):
        return 'incompatible'
    # Direct parent/offspring
    if focus in get_parents(other) or other in get_parents(focus):
        return 'incompatible'
    # Shared ancestors → inbreeding risk
    if find_common_ancestors(focus, other):
        return 'risky'
    return 'ok'


# ── Source summary ────────────────────────────────────────────────────────────

def _source_summary(cat: Cat) -> tuple[str, str]:
    """Return the source/lineage label and tooltip for a cat."""
    repaired = bool(getattr(cat, "pedigree_was_repaired", False))
    repair_suffix = ""
    if repaired:
        repair_suffix = f" ({_tr('cat_detail.pedigree_repaired', default='pedigree repaired')})"

    pa = getattr(cat, "parent_a", None)
    pb = getattr(cat, "parent_b", None)

    if pa is None and pb is None:
        display = _tr("cat_detail.stray", default="Stray") + repair_suffix
    else:
        def _pname(p):
            name = getattr(p, "name", "?")
            if getattr(p, "status", "") == "Gone":
                return _tr("cat_detail.gone_suffix", name=name)
            return name

        display = " × ".join(_pname(p) for p in (pa, pb) if p is not None)
        display += repair_suffix

    tooltip = display
    if repaired:
        tooltip = (
            f"{display}\n"
            + _tr(
                "cat_detail.pedigree_repaired_note",
                default="One or more parent links were broken while loading this save to prevent a pedigree cycle.",
            )
        )
    return display, tooltip


# ── Cat sprite helper (visual roster mode) ───────────────────────────────────

try:
    import swf_cat_renderer as _swf_cat_renderer
    _SWF_CAT_RENDERER_AVAILABLE = True
except Exception:
    _swf_cat_renderer = None
    _SWF_CAT_RENDERER_AVAILABLE = False


_CAT_SPRITE_PIXMAP_CACHE: dict[tuple[int, int], QPixmap] = {}


def _cat_sprite_pixmap(cat: Cat, size: int) -> Optional[QPixmap]:
    """Return a cached QPixmap of the cat's face on a white rounded-rect
    background, or None.  Uses the face-crop renderer when available."""
    if not _SWF_CAT_RENDERER_AVAILABLE or cat is None:
        return None
    size = int(max(16, size))
    db_key = getattr(cat, "db_key", None)
    if db_key is None or db_key == 0:
        # Use uid as a stable, non-reusable key instead of id() which can
        # be recycled after GC.
        db_key = getattr(cat, "uid", None) or id(cat)
    dpr = getattr(QApplication.instance(), "devicePixelRatio", lambda: 1.0)()
    key = (int(db_key), size, dpr)
    cached = _CAT_SPRITE_PIXMAP_CACHE.get(key)
    if cached is not None:
        return cached

    # Prefer face-only crop; fall back to full thumbnail.
    phys = int(size * dpr)
    png = None
    render_face = getattr(_swf_cat_renderer, "render_cat_face_thumbnail", None)
    if render_face is not None:
        try:
            png = render_face(cat, size=phys)
        except Exception:
            pass
    if not png:
        try:
            png = _swf_cat_renderer.render_cat_thumbnail(cat, size=phys)
        except Exception:
            pass
    if not png:
        return None
    raw = QPixmap()
    if not raw.loadFromData(png, "PNG"):
        return None
    # Composite onto a white rounded-rect background.  Pad inward so the
    # face fills most of the area.
    pad = max(2, phys // 16)
    inner = phys - 2 * pad
    pix = QPixmap(phys, phys)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QBrush(QColor(255, 255, 255)))
    p.setPen(QColor(200, 200, 210))
    p.drawRoundedRect(0, 0, phys - 1, phys - 1, 4, 4)
    scaled = raw.scaled(inner, inner, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    p.drawPixmap(
        pad + (inner - scaled.width()) // 2,
        pad + (inner - scaled.height()) // 2,
        scaled,
    )
    p.end()
    pix.setDevicePixelRatio(dpr)
    _CAT_SPRITE_PIXMAP_CACHE[key] = pix
    return pix


def clear_cat_sprite_cache():
    _CAT_SPRITE_PIXMAP_CACHE.clear()


# ── Mutation body-part sprite icons ──────────────────────────────────────────

_MUTATION_PART_PIXMAP_CACHE: dict[tuple, QPixmap] = {}


def _mutation_part_pixmap(slot_key: str, part_id: int, size: int) -> Optional[QPixmap]:
    """Render a cat's mutated body-part sprite at *size* px and return as QPixmap.

    The renderer returns a large canvas (570×580) with the part centred.
    We auto-trim transparent padding, tint to a neutral light colour so the
    raw green/pink SWF colours become a clean silhouette, then scale to fit.
    """
    if not _SWF_CAT_RENDERER_AVAILABLE:
        return None
    dpr = getattr(QApplication.instance(), "devicePixelRatio", lambda: 1.0)()
    size = int(max(16, size))
    cache_key = (slot_key, int(part_id), size, dpr)
    cached = _MUTATION_PART_PIXMAP_CACHE.get(cache_key)
    if cached is not None:
        return cached
    render_part = getattr(_swf_cat_renderer, "render_cat_part", None)
    if render_part is None:
        return None
    try:
        png = render_part(slot_key, int(part_id))
    except Exception:
        return None
    if not png:
        return None

    from PIL import Image
    import numpy as np
    import io as _io
    try:
        img = Image.open(_io.BytesIO(png)).convert("RGBA")
        bbox = img.getbbox()
        if bbox:
            img = img.crop(bbox)

        # Tint to a neutral light colour so raw green/pink sprites become
        # a clean silhouette icon.  Preserves alpha and luminance variation.
        arr = np.array(img, dtype=np.float32)
        a = arr[:, :, 3]
        lum = (arr[:, :, 0] + arr[:, :, 1] + arr[:, :, 2]) / 3.0
        # Map luminance to 180–240 range for a light, readable silhouette.
        bright = 180.0 + (lum / 255.0) * 60.0
        arr[:, :, 0] = bright
        arr[:, :, 1] = bright
        arr[:, :, 2] = np.minimum(bright + 10, 255)  # slight cool tint
        arr[:, :, 3] = a
        img = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGBA")

        # Add a small margin so the part doesn't touch the edges.
        margin = max(2, int(max(img.width, img.height) * 0.05))
        padded = Image.new("RGBA",
                           (img.width + 2 * margin, img.height + 2 * margin),
                           (0, 0, 0, 0))
        padded.alpha_composite(img, (margin, margin))
        buf = _io.BytesIO()
        padded.save(buf, format="PNG")
        trimmed_png = buf.getvalue()
    except Exception:
        trimmed_png = png

    raw = QPixmap()
    if not raw.loadFromData(trimmed_png, "PNG"):
        return None
    phys = int(size * dpr)
    scaled = raw.scaled(phys, phys, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    scaled.setDevicePixelRatio(dpr)
    _MUTATION_PART_PIXMAP_CACHE[cache_key] = scaled
    return scaled


def clear_mutation_part_cache():
    _MUTATION_PART_PIXMAP_CACHE.clear()
    _BRIGHTEN_CACHE.clear()


_BRIGHTEN_CACHE: dict[int, QPixmap] = {}


def _brighten_pixmap(pix: QPixmap) -> QPixmap:
    """Return a brightened copy of *pix* so dark SWF icons are visible on
    the near-black icon background.  Uses QPainter composition for speed.

    A single Screen pass lifts dark pixels while preserving gradient hue
    information.  Two passes were too aggressive and washed icons to
    near-white, defeating the gradient rendering (#90).
    """
    key = pix.cacheKey()
    cached = _BRIGHTEN_CACHE.get(key)
    if cached is not None:
        return cached
    result = QPixmap(pix.size())
    result.setDevicePixelRatio(pix.devicePixelRatio())
    result.fill(Qt.transparent)
    p = QPainter(result)
    p.drawPixmap(0, 0, pix)
    p.setCompositionMode(QPainter.CompositionMode_Screen)
    p.drawPixmap(0, 0, pix)  # single screen pass — brightens darks, preserves hue
    p.end()
    _BRIGHTEN_CACHE[key] = result
    return result


# ── Delegate ──────────────────────────────────────────────────────────────────

class TagStripDelegate(QStyledItemDelegate):
    """Paints compact tag strips in the roster Tags column."""

    _PAD_LEFT = 4

    def paint(self, painter, option, index):
        pixmap = index.data(Qt.DecorationRole)
        if pixmap is None or (hasattr(pixmap, "isNull") and pixmap.isNull()):
            super().paint(painter, option, index)
            return

        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        style = opt.widget.style() if opt.widget else QApplication.style()
        opt.text = ""
        opt.icon = QIcon()
        style.drawControl(QStyle.CE_ItemViewItem, opt, painter, opt.widget)

        painter.save()
        r = option.rect
        if isinstance(pixmap, QIcon):
            pixmap = pixmap.pixmap(r.size())
        if hasattr(pixmap, "size") and pixmap.size().isValid():
            _dpr = pixmap.devicePixelRatio() or 1.0
            logical_h = int(pixmap.height() / _dpr)
            draw_y = r.center().y() - logical_h // 2
            draw_x = r.left() + self._PAD_LEFT
            painter.drawPixmap(draw_x, draw_y, pixmap)
        painter.restore()


# Backwards compatibility for any code still importing the old name.
NameTagDelegate = TagStripDelegate


class VisualIconDelegate(QStyledItemDelegate):
    """Paints ability/mutation icons horizontally when the model's visual
    mode is enabled. When visual mode is off, falls back to the default
    text rendering so compact mode is unaffected."""

    _ICON_PAD = 4
    _ICON_SPACING = 6

    def __init__(self, kind: str, parent=None):
        super().__init__(parent)
        # "abilities" or "mutations" — determines which data we pull.
        self._kind = kind

    def _visual_enabled(self, index) -> bool:
        model = index.model()
        source = getattr(model, "sourceModel", None)
        if callable(source):
            src_model = source()
            if src_model is not None and hasattr(src_model, "visual_mode"):
                return src_model.visual_mode()
        if hasattr(model, "visual_mode"):
            return model.visual_mode()
        return False

    def _cat_for_index(self, index) -> Optional[Cat]:
        model = index.model()
        src_index = index
        source = getattr(model, "mapToSource", None)
        if callable(source):
            src_index = source(index)
        src_model = getattr(model, "sourceModel", None)
        if callable(src_model):
            src_model = src_model()
        else:
            src_model = model
        if src_model is None or not src_index.isValid():
            return None
        cat_at = getattr(src_model, "cat_at", None)
        if cat_at is None:
            return None
        return cat_at(src_index.row())

    def _icon_items(self, cat: Cat) -> list[tuple[str, str, bool]]:
        """Return (icon_key, label, is_defect) tuples for the icons to paint.

        For abilities: icon_key is the ability/passive name for SWF lookup.
        For mutations: icon_key is ``"part:<slot_key>:<part_id>"`` so the
        paint method can render the actual body-part sprite."""
        if cat is None:
            return []
        items: list[tuple[str, str, bool]] = []
        if self._kind == "abilities":
            for name in getattr(cat, "abilities", []) or []:
                items.append((str(name), str(name), False))
            for name in getattr(cat, "passive_abilities", []) or []:
                items.append((str(name), _mutation_display_name(name), False))
            for name in getattr(cat, "disorders", []) or []:
                items.append((str(name), _mutation_display_name(name), True))
        elif self._kind == "mutations":
            # Use visual_mutation_entries so we can render the body-part
            # sprite as the icon (keyed by slot_key + mutation_id).
            entries = getattr(cat, "visual_mutation_entries", None) or []
            # Build a lookup: display_name -> first (slot_key, mutation_id)
            seen_names: set[str] = set()
            for entry in entries:
                name = str(entry.get("name", ""))
                if name in seen_names:
                    continue
                seen_names.add(name)
                slot_key = str(entry.get("slot_key", ""))
                mutation_id = int(entry.get("mutation_id", 0))
                is_defect = bool(entry.get("is_defect", False))
                label = _mutation_display_name(name)
                icon_key = f"part:{slot_key}:{mutation_id}"
                items.append((icon_key, label, is_defect))
            # Fallback: if visual_mutation_entries is missing, use flat lists.
            if not entries:
                for name in getattr(cat, "mutations", []) or []:
                    items.append((str(name), _mutation_display_name(name), False))
                for name in getattr(cat, "defects", []) or []:
                    items.append((str(name), str(name), True))
        return items

    def paint(self, painter, option, index):
        if not self._visual_enabled(index):
            super().paint(painter, option, index)
            return

        # Draw the normal cell chrome (background, selection) first.
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        style = opt.widget.style() if opt.widget else QApplication.style()
        opt.text = ""
        opt.icon = QIcon()
        style.drawControl(QStyle.CE_ItemViewItem, opt, painter, opt.widget)

        cat = self._cat_for_index(index)
        items = self._icon_items(cat)
        if not items:
            return

        from mewgenics.utils.ability_icons import (
            get_ability_icon_pixmap, get_passive_icon_pixmap,
        )

        r = option.rect
        row_h = r.height()
        # Reserve a label line under each icon (~12 px) and small padding.
        label_h = 12
        icon_size = max(16, row_h - label_h - 2 * self._ICON_PAD)
        y_top = r.top() + self._ICON_PAD
        x = r.left() + self._ICON_PAD

        painter.save()
        label_font = QFont(opt.font)
        label_font.setPixelSize(10)
        painter.setFont(label_font)
        fm = painter.fontMetrics()

        for icon_key, label, is_defect in items:
            if x + icon_size > r.right():
                break
            pix = None
            if icon_key.startswith("part:"):
                # Mutation body-part sprite.
                parts = icon_key.split(":", 2)
                if len(parts) == 3:
                    try:
                        pix = _mutation_part_pixmap(parts[1], int(parts[2]), icon_size)
                    except Exception:
                        pix = None
            elif self._kind == "abilities":
                try:
                    pix = get_ability_icon_pixmap(icon_key, icon_size)
                except Exception:
                    pix = None
                if pix is None or pix.isNull():
                    try:
                        pix = get_passive_icon_pixmap(icon_key, icon_size)
                    except Exception:
                        pix = None
            else:  # mutations / defects fallback (no part info)
                try:
                    pix = get_passive_icon_pixmap(icon_key, icon_size)
                except Exception:
                    pix = None

            # Dark rounded-rect background behind each icon.
            painter.setRenderHint(QPainter.Antialiasing)
            painter.setBrush(QBrush(QColor(18, 18, 30)))
            painter.setPen(QColor(40, 40, 60))
            painter.drawRoundedRect(int(x), int(y_top), int(icon_size), int(icon_size), 4, 4)

            if pix is not None and not pix.isNull():
                # Brighten ability SWF icons so they're visible on the dark bg.
                # Mutation part icons are already tinted light in their renderer.
                if self._kind == "abilities" and not icon_key.startswith("part:"):
                    pix = _brighten_pixmap(pix)
                painter.drawPixmap(x, y_top, icon_size, icon_size, pix)
            else:
                # Fallback: first letter of the label.
                painter.setPen(QColor(80, 80, 100))
                painter.drawText(
                    int(x), int(y_top),
                    int(icon_size), int(icon_size),
                    Qt.AlignCenter,
                    (label[:1] or "?").upper(),
                )

            # Label line
            label_color = QColor(224, 160, 160) if is_defect else QColor(204, 204, 220)
            painter.setPen(label_color)
            text = fm.elidedText(label, Qt.ElideRight, icon_size + self._ICON_SPACING)
            painter.drawText(
                int(x - 2),
                int(y_top + icon_size + 1),
                int(icon_size + 4),
                int(label_h),
                Qt.AlignHCenter | Qt.AlignTop,
                text,
            )

            x += icon_size + self._ICON_SPACING
        painter.restore()

    def sizeHint(self, option, index):
        # Row height is controlled by the table's vertical header in
        # visual mode, so just defer to the default here.
        return super().sizeHint(option, index)


# ── Table model ───────────────────────────────────────────────────────────────

class CatTableModel(QAbstractTableModel):
    blacklistChanged = Signal()

    def __init__(self):
        super().__init__()
        self._cats: list[Cat] = []
        self._focus_cat: Optional[Cat] = None
        self._show_lineage: bool = False
        self._relation_cache: dict[int, float] = {}
        self._compat_cache: dict[int, str] = {}
        self._inbred_score_cache: dict[int, int] = {}
        self._ancestor_ids_cache: dict[int, frozenset[int]] = {}
        self._parent_ids_cache: dict[int, frozenset[int]] = {}
        self._hater_ids_cache: dict[int, frozenset[int]] = {}
        self._breeding_cache = None  # Optional[BreedingCache]
        self._show_total_stats: bool = False
        self._show_stat_icons: bool = False
        self._visual_mode: bool = False
        self._visual_sprite_size: int = 48
        self._accessible_cat_keys: set[int] = set()

    def set_breeding_cache(self, cache):
        self._breeding_cache = cache
        self._relation_cache.clear()
        self._compat_cache.clear()
        # Fill deferred caches from breeding cache data
        if cache is not None and cache.ready:
            for cat in self._cats:
                depths = cache.ancestor_depths.get(cat.db_key, {})
                self._ancestor_ids_cache[id(cat)] = frozenset(
                    id(anc) for anc in depths if anc is not cat
                )
                if cat.parent_a is not None and cat.parent_b is not None:
                    da = cache.ancestor_depths.get(cat.parent_a.db_key, {})
                    db = cache.ancestor_depths.get(cat.parent_b.db_key, {})
                    self._inbred_score_cache[id(cat)] = len(set(da.keys()) & set(db.keys()))
                else:
                    self._inbred_score_cache[id(cat)] = 0
        if self._cats:
            self.dataChanged.emit(
                self.index(0, 0),
                self.index(len(self._cats) - 1, len(COLUMNS) - 1),
                [Qt.DisplayRole, Qt.UserRole, Qt.BackgroundRole, Qt.ForegroundRole],
            )

    def set_show_lineage(self, show: bool):
        self._show_lineage = show
        if self._cats:
            self.dataChanged.emit(
                self.index(0, 0),
                self.index(len(self._cats) - 1, len(COLUMNS) - 1),
                [Qt.BackgroundRole, Qt.ForegroundRole],
            )

    def set_show_total_stats(self, show: bool):
        if self._show_total_stats == bool(show):
            return
        self._show_total_stats = bool(show)
        if self._cats:
            self.dataChanged.emit(
                self.index(0, STAT_COLS[0]),
                self.index(len(self._cats) - 1, STAT_COLS[-1]),
                [Qt.DisplayRole, Qt.UserRole, Qt.BackgroundRole, Qt.ForegroundRole, Qt.ToolTipRole],
            )
            self.dataChanged.emit(
                self.index(0, COL_SUM),
                self.index(len(self._cats) - 1, COL_SUM),
                [Qt.DisplayRole, Qt.UserRole, Qt.ToolTipRole],
            )

    def show_total_stats(self) -> bool:
        return self._show_total_stats

    def set_show_stat_icons(self, show: bool):
        if self._show_stat_icons == bool(show):
            return
        self._show_stat_icons = bool(show)
        if self._cats:
            self.headerDataChanged.emit(Qt.Horizontal, STAT_COLS[0], STAT_COLS[-1])

    def set_visual_mode(self, enabled: bool, sprite_size: int = 48):
        """Toggle 'visual' roster mode. In visual mode the name column
        gets a cat sprite decoration and the abilities/mutations columns
        are painted with icons via their delegates."""
        enabled = bool(enabled)
        if self._visual_mode == enabled and int(sprite_size) == self._visual_sprite_size:
            return
        self._visual_mode = enabled
        self._visual_sprite_size = max(16, int(sprite_size))
        if self._cats:
            self.dataChanged.emit(
                self.index(0, 0),
                self.index(len(self._cats) - 1, len(COLUMNS) - 1),
                [Qt.DisplayRole, Qt.DecorationRole, Qt.SizeHintRole],
            )

    def visual_mode(self) -> bool:
        return self._visual_mode

    def visual_sprite_size(self) -> int:
        return self._visual_sprite_size

    def load(self, cats: list[Cat], accessible_cats: Optional[set[int]] = None):
        self.beginResetModel()
        self._cats = cats
        if accessible_cats is not None:
            self._accessible_cat_keys = set(accessible_cats)
        self._relation_cache.clear()
        self._compat_cache.clear()
        # Cheap caches — computed inline
        self._parent_ids_cache = {
            id(cat): frozenset(id(parent) for parent in get_parents(cat))
            for cat in cats
        }
        self._hater_ids_cache = {
            id(cat): frozenset(id(hater) for hater in getattr(cat, "haters", []))
            for cat in cats
        }
        # Ancestor + inbred caches — computed immediately so risky highlighting
        # and inbred scores are available right away (v1.7.0 behaviour).
        # The breeding cache will refine these later with deeper traversal.
        self._ancestor_ids_cache = {
            id(cat): frozenset(id(anc) for anc in get_all_ancestors(cat))
            for cat in cats
        }
        self._inbred_score_cache = {
            id(cat): len(find_common_ancestors(cat.parent_a, cat.parent_b))
            if cat.parent_a is not None and cat.parent_b is not None else 0
            for cat in cats
        }
        # Compute ancestry-based inbredness (COI) for cats with known parents.
        # The game's stored inbredness value is unreliable, so we derive it
        # from the actual family tree using the kinship coefficient.
        # Stored as raw COI (0.25 = full siblings, 0.50+ = multi-gen inbreeding).
        # For strays (no parents), scale the game's 0-1 value to approx COI range.
        kinship_memo: dict[tuple[int, int], float] = {}
        for cat in cats:
            # Preserve manual calibration overrides
            if cat.inbredness != cat.parsed_inbredness:
                continue
            if cat.parent_a is not None and cat.parent_b is not None:
                cat.inbredness = _kinship(cat.parent_a, cat.parent_b, kinship_memo)
            else:
                # Stray — no parents means no inbreeding; parsed values are noise.
                cat.inbredness = 0.0
        self.endResetModel()

    def apply_room_patch(self, patch: dict[int, tuple[str, str]]) -> bool:
        """Apply a quick room/status patch in place.

        Source row positions are stable — only cat.room / cat.status are
        mutated. Bracketing the change with the proper
        layoutAboutToBeChanged / layoutChanged pair lets the proxy
        update its sort/filter mapping while preserving view selection
        and scroll position. Emitting layoutChanged on its own (without
        the matching about-to signal) leaves persistent indexes
        dangling and crashes when the user clicks a sort header.
        """
        if not self._cats or not patch:
            return False

        changes: list[tuple[Cat, str, str]] = []
        for cat in self._cats:
            entry = patch.get(cat.db_key)
            if entry is None:
                continue
            room, status = entry
            if cat.room == room and cat.status == status:
                continue
            changes.append((cat, room, status))

        if not changes:
            return False

        self.layoutAboutToBeChanged.emit()
        old_indexes = self.persistentIndexList()
        try:
            self._relation_cache.clear()
            self._compat_cache.clear()
            for cat, room, status in changes:
                cat.room = room
                cat.status = status
        finally:
            # Source row positions are stable — persistent indexes map
            # to themselves. The explicit call keeps Qt's bookkeeping
            # consistent and silences "persistent index" warnings.
            self.changePersistentIndexList(old_indexes, list(old_indexes))
            self.layoutChanged.emit()
        return True

    def set_focus_cat(self, cat: Optional[Cat]):
        if cat is self._focus_cat:
            return
        self._focus_cat = cat
        self._relation_cache.clear()
        self._compat_cache.clear()
        if self._cats:
            self.dataChanged.emit(
                self.index(0, 0),
                self.index(len(self._cats) - 1, len(COLUMNS) - 1),
                [Qt.DisplayRole, Qt.UserRole, Qt.BackgroundRole, Qt.ForegroundRole],
            )

    def _relation_for(self, cat: Cat) -> float:
        if self._focus_cat is None:
            return 0.0
        if cat is self._focus_cat:
            return 100.0
        key = id(cat)
        cached = self._relation_cache.get(key)
        if cached is not None:
            return cached
        bc = self._breeding_cache
        if bc is not None and bc.ready:
            pct = bc.get_risk(self._focus_cat, cat)
        else:
            pct = risk_percent(self._focus_cat, cat)
        self._relation_cache[key] = pct
        return pct

    def _compat_for(self, cat: Cat) -> Optional[str]:
        if self._focus_cat is None or cat is self._focus_cat:
            return None
        focus = self._focus_cat
        key = id(cat)
        cached = self._compat_cache.get(key)
        if cached is not None:
            return cached

        ok, _ = can_breed(focus, cat)
        if not ok:
            compat = 'incompatible'
        else:
            focus_id = id(focus)
            cat_id = id(cat)
            focus_haters = self._hater_ids_cache.get(focus_id, frozenset())
            cat_haters = self._hater_ids_cache.get(cat_id, frozenset())
            focus_parents = self._parent_ids_cache.get(focus_id, frozenset())
            cat_parents = self._parent_ids_cache.get(cat_id, frozenset())
            focus_anc = self._ancestor_ids_cache.get(focus_id, frozenset())
            cat_anc = self._ancestor_ids_cache.get(cat_id, frozenset())

            if cat_id in focus_haters or focus_id in cat_haters:
                compat = 'incompatible'
            elif focus_id in cat_parents or cat_id in focus_parents:
                compat = 'incompatible'
            elif focus_anc & cat_anc:
                compat = 'risky'
            else:
                compat = 'ok'

        self._compat_cache[key] = compat
        return compat

    def _inbred_score_for(self, cat: Cat) -> int:
        return self._inbred_score_cache.get(id(cat), 0)

    def rowCount(self, parent=QModelIndex()):    return len(self._cats)
    def columnCount(self, parent=QModelIndex()): return len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return COLUMNS[section]
        if orientation == Qt.Horizontal and role == Qt.ToolTipRole and section == COL_TAGS:
            return _tr(
                "table.tooltip.tags",
                default="Game tag first, then custom tags, shown as icons.",
            )
        if orientation == Qt.Horizontal and role == Qt.ToolTipRole and section == COL_ADV:
            return _tr(
                "table.tooltip.adventure_ready",
                default="Cats that can go on the next adventure. Sort this column to bring them to the top.",
            )
        if orientation == Qt.Horizontal and role == Qt.DecorationRole and self._show_stat_icons and section in STAT_COLS:
            stat_name = STAT_NAMES[section - STAT_COLS[0]]
            return _make_stat_header_icon(stat_name)
        return None

    @staticmethod
    def _exceptional_tooltip(cat, prefix: str = "Exceptional breeder") -> str:
        from mewgenics.utils.thresholds import (
            SCORE_SOURCE, DETAILED_EXCEPTIONAL_THRESHOLD, _get_detailed_score,
        )
        if SCORE_SOURCE == "detailed":
            score = _get_detailed_score(cat)
            if score is not None:
                return f"{prefix}: detailed score {score:+.1f} >= {DETAILED_EXCEPTIONAL_THRESHOLD:+.1f}"
        return f"{prefix}: base stat sum {_cat_base_sum(cat)} >= {EXCEPTIONAL_SUM_THRESHOLD}"

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        cat = self._cats[index.row()]
        col = index.column()
        display_stats = cat.total_stats if self._show_total_stats else cat.base_stats
        is_exceptional = _is_exceptional_breeder(cat)
        donation_reason = _donation_candidate_reason(cat)
        is_donation = donation_reason is not None
        # Adv Ready: the cat must be alive, in the house (or currently on
        # an adventure), AND flagged as accessible by the game's own
        # pedigree table. "Gone" covers dead/aged-out cats — those must
        # never show ✓ even if a stale entry lingers in the hash table.
        # Retired cats (cats that have already gone on at least one
        # adventure — detected via non-zero stat_mod level-up bonuses)
        # also remain in the accessible hash but cannot be sent out
        # again, so they must be filtered out here.
        can_adventure = (
            cat.status != "Gone"
            and not cat.has_adventured
            and cat.db_key in self._accessible_cat_keys
        )

        def _badge_background() -> Optional[QColor]:
            if is_exceptional:
                return QColor(24, 78, 48)
            if is_donation:
                return QColor(82, 52, 22)
            return None

        if role == Qt.DisplayRole:
            if col == COL_NAME:
                if is_exceptional:
                    return f"[EXC] {cat.name}"
                if is_donation:
                    return f"[DON] {cat.name}"
                return cat.name
            if col == COL_TAGS:
                return _cat_tag_summary(cat)
            if col == COL_AGE:  return str(cat.age) if cat.age is not None else "—"
            if col == COL_GEN:  return cat.gender_display
            if col == COL_ROOM: return cat.room_display
            if col == COL_STAT:
                if cat.is_dead:
                    return "Dead"
                return STATUS_ABBREV.get(cat.status, cat.status)
            if col == COL_ADV:  return "✓" if can_adventure else "—"
            if col == COL_BL:   return "X" if cat.is_blacklisted else ""
            if col == COL_MB:   return "★" if cat.must_breed else ""
            if col == COL_PIN:  return "\u25C6" if cat.is_pinned else ""
            if col in STAT_COLS:
                stat_name = STAT_NAMES[col - STAT_COLS[0]]
                return str(display_stats[stat_name])
            if col == COL_SUM:
                return str(sum(display_stats.values()))
            if col == COL_MUTS:
                parts = [_mutation_display_name(m) for m in cat.mutations]
                if cat.defects:
                    parts += [f"⚠ {d}" for d in cat.defects]
                return ", ".join(parts)
            if col == COL_ABIL:
                _pt = getattr(cat, "passive_tiers", {})
                parts = []
                for ab in cat.abilities:
                    base, tier = _strip_tier(ab)
                    parts.append(f"{base}+" if tier > 1 else base)
                for p in cat.passive_abilities:
                    tier = _pt.get(p, 1)
                    name = _mutation_display_name(p)
                    parts.append(f"● {name}+" if tier > 1 else f"● {name}")
                if cat.disorders:
                    parts += [f"⚠ {_mutation_display_name(d)}" for d in cat.disorders]
                return ", ".join(parts)
            if col == COL_RELNS:
                return _relations_summary(cat) or "—"
            if col == COL_REL:
                if self._focus_cat is None:
                    return "—"
                return f"{int(round(self._relation_for(cat)))}%"
            if col == COL_GEN_DEPTH:
                return str(cat.generation)
            if col == COL_AGG:
                label = _trait_label_from_value("aggression", cat.aggression)
                return label if label else "—"
            if col == COL_LIB:
                label = _trait_label_from_value("libido", cat.libido)
                return label if label else "—"
            if col == COL_INBRD:
                label = _trait_label_from_value("inbredness", cat.inbredness)
                return label if label else "—"
            if col == COL_SEXUALITY:
                return getattr(cat, "sexuality", None) or ""
            if col == COL_SRC:
                return _source_summary(cat)[0]
        elif role == Qt.UserRole:
            if col == COL_NAME:
                return (cat.name or "").lower()
            if col == COL_TAGS:
                return _cat_tag_summary(cat).lower()
            if col in STAT_COLS:
                return display_stats[STAT_NAMES[col - STAT_COLS[0]]]
            if col == COL_SUM:
                return sum(display_stats.values())
            if col == COL_ADV:
                return 0 if can_adventure else 1
            if col == COL_REL:
                return self._relation_for(cat) if self._focus_cat is not None else -1.0
            if col == COL_AGE:
                return cat.age if cat.age is not None else -1
            if col == COL_GEN_DEPTH:
                return cat.generation
            if col == COL_AGG:
                return cat.aggression if cat.aggression is not None else -1.0
            if col == COL_LIB:
                return cat.libido if cat.libido is not None else -1.0
            if col == COL_INBRD:
                return cat.inbredness if cat.inbredness is not None else -1.0
            if col == COL_SEXUALITY:
                return getattr(cat, "sexuality", None) or ""
            if col == COL_SRC:
                return _source_summary(cat)[1]
            return self.data(index, Qt.DisplayRole)

        elif role == Qt.DecorationRole:
            if col == COL_TAGS:
                return _cat_tag_pixmap(cat, dot_size=16, spacing=4)
            if self._visual_mode and col == COL_NAME:
                pix = _cat_sprite_pixmap(cat, self._visual_sprite_size)
                if pix is not None and not pix.isNull():
                    return pix

        elif role == Qt.BackgroundRole:
            compat = self._compat_for(cat)
            # Suppress risky highlight when lineage features are off
            if compat == 'risky' and not self._show_lineage:
                compat = 'ok'
            if col in STAT_COLS:
                stat_name = STAT_NAMES[col - STAT_COLS[0]]
                base_c = STAT_COLORS.get(display_stats[stat_name], QColor(100, 100, 115))
                if compat == 'incompatible':
                    return QBrush(QColor(base_c.red() // 4, base_c.green() // 4, base_c.blue() // 4))
                if compat == 'risky':
                    return QBrush(QColor(base_c.red() // 2, base_c.green() // 2, base_c.blue() // 2))
                return QBrush(base_c)
            if col == COL_STAT:
                sc = STATUS_COLOR.get(cat.status, QColor(80, 80, 90))
                if compat == 'incompatible':
                    return QBrush(QColor(sc.red() // 4, sc.green() // 4, sc.blue() // 4))
                if compat == 'risky':
                    return QBrush(QColor(sc.red() // 2, sc.green() // 2, sc.blue() // 2))
                return QBrush(sc)
            if col == COL_ADV:
                if can_adventure:
                    return QBrush(QColor(36, 96, 64))
                return QBrush(QColor(48, 48, 58))
            if col in (COL_AGG, COL_LIB, COL_INBRD):
                if col == COL_AGG:
                    base = _trait_level_color(_trait_label_from_value("aggression", cat.aggression))
                elif col == COL_LIB:
                    base = _trait_level_color(_trait_label_from_value("libido", cat.libido))
                else:
                    base = _trait_level_color(_trait_label_from_value("inbredness", cat.inbredness))
                if compat == 'incompatible':
                    return QBrush(QColor(base.red() // 4, base.green() // 4, base.blue() // 4))
                if compat == 'risky':
                    return QBrush(QColor(base.red() // 2, base.green() // 2, base.blue() // 2))
                return QBrush(base)
            if col in (COL_NAME, COL_SUM, COL_TAGS):
                badge = _badge_background()
                if badge is not None:
                    if compat == 'incompatible':
                        badge = QColor(badge.red() // 4, badge.green() // 4, badge.blue() // 4)
                    elif compat == 'risky':
                        badge = QColor(badge.red() // 2, badge.green() // 2, badge.blue() // 2)
                    return QBrush(badge)
            if compat == 'incompatible':
                return QBrush(QColor(18, 12, 14))
            if compat == 'risky':
                return QBrush(QColor(22, 18, 10))

        elif role == Qt.ForegroundRole:
            compat = self._compat_for(cat)
            # Suppress risky highlight when lineage features are off
            if compat == 'risky' and not self._show_lineage:
                compat = 'ok'
            if compat == 'incompatible':
                return QBrush(QColor(65, 55, 60))
            if compat == 'risky':
                return QBrush(QColor(130, 110, 60))
            if col == COL_ADV:
                return QBrush(QColor(230, 255, 240)) if can_adventure else QBrush(QColor(150, 160, 170))
            if col in STAT_COLS or col == COL_STAT or col in (COL_AGG, COL_LIB, COL_INBRD, COL_NAME, COL_SUM, COL_TAGS):
                return QBrush(QColor(255, 255, 255))

        elif role == Qt.ToolTipRole:
            if col == COL_NAME:
                notes: list[str] = []
                if is_exceptional:
                    notes.append(self._exceptional_tooltip(cat))
                if donation_reason:
                    notes.append(f"Donation candidate: {donation_reason}")
                if notes:
                    return "\n".join(notes)
                return cat.name
            if col == COL_TAGS:
                return _cat_tag_tooltip(cat)
            if col in STAT_COLS:
                n = STAT_NAMES[col - STAT_COLS[0]]
                b = cat.base_stats[n]
                t = cat.total_stats[n]
                shown = display_stats[n]
                mode = "total" if self._show_total_stats else "base"
                extra = f"  (base: {b}, total: {t})" if t != b else f"  (base: {b})"
                return f"{n}  {mode}: {shown}{extra}"
            if col == COL_ROOM:
                return cat.room
            if col == COL_ADV:
                if can_adventure:
                    if cat.status == "Adventure":
                        return "Adventure-ready, currently away on adventure."
                    return "Eligible for the next adventure."
                if cat.status == "Adventure":
                    return "Currently on adventure."
                return "Not eligible for the next adventure."
            if col == COL_BL:
                return _tr("table.tooltip.excluded") if cat.is_blacklisted else _tr("table.tooltip.included")
            if col == COL_MB:
                return _tr("table.tooltip.must_breed") if cat.must_breed else _tr("table.tooltip.normal_priority")
            if col == COL_PIN:
                return _tr("table.tooltip.pinned") if cat.is_pinned else _tr("table.tooltip.not_pinned")
            if col == COL_MUTS and (cat.mutations or cat.defects):
                return _mutations_tooltip(cat)
            if col == COL_ABIL and (cat.abilities or cat.passive_abilities or cat.disorders):
                return _abilities_tooltip(cat)
            if col == COL_RELNS and (cat.lovers or cat.haters):
                lines: list[str] = []
                if cat.lovers:
                    lines.append("Lovers: " + ", ".join(other.name for other in cat.lovers))
                if cat.haters:
                    lines.append("Haters: " + ", ".join(other.name for other in cat.haters))
                return "\n".join(lines)
            if col == COL_AGG:
                if cat.aggression is None:
                    return "Aggression: unknown"
                return f"Aggression: {cat.aggression:.3f} ({_trait_label_from_value('aggression', cat.aggression)})"
            if col == COL_LIB:
                if cat.libido is None:
                    return "Libido: unknown"
                return f"Libido: {cat.libido:.3f} ({_trait_label_from_value('libido', cat.libido)})"
            if col == COL_INBRD:
                if cat.inbredness is None:
                    return "Inbredness: unknown"
                return f"Inbredness: {cat.inbredness:.3f} ({_trait_label_from_value('inbredness', cat.inbredness)})"
            if col == COL_SUM:
                notes: list[str] = [f"Base stat sum: {_cat_base_sum(cat)}"]
                if self._show_total_stats:
                    notes.append(f"Total stat sum: {sum(cat.total_stats.values())}")
                if is_exceptional:
                    notes.append(self._exceptional_tooltip(cat, prefix="Exceptional threshold"))
                if donation_reason:
                    notes.append(f"Donation signal: {donation_reason}")
                return "\n".join(notes)

        elif role == Qt.CheckStateRole:
            if col == COL_BL:
                return Qt.Checked if cat.is_blacklisted else Qt.Unchecked
            if col == COL_MB:
                return Qt.Checked if cat.must_breed else Qt.Unchecked
            if col == COL_PIN:
                return Qt.Checked if cat.is_pinned else Qt.Unchecked

        elif role == Qt.TextAlignmentRole:
            if col in STAT_COLS or col in (COL_GEN, COL_STAT, COL_ADV, COL_AGE, COL_BL, COL_MB, COL_PIN, COL_SUM, COL_REL, COL_GEN_DEPTH, COL_AGG, COL_LIB, COL_INBRD, COL_SEXUALITY, COL_TAGS):
                return Qt.AlignCenter

        return None

    def flags(self, index):
        if not index.isValid():
            return Qt.NoItemFlags
        base = Qt.ItemIsSelectable | Qt.ItemIsEnabled
        if index.column() in (COL_BL, COL_MB, COL_PIN):
            return base | Qt.ItemIsUserCheckable
        return base

    def setData(self, index, value, role=Qt.EditRole):
        if not index.isValid():
            return False
        col = index.column()
        if col not in (COL_BL, COL_MB, COL_PIN) or role != Qt.CheckStateRole:
            return False
        cat = self._cats[index.row()]
        new_state = (value == Qt.Checked)
        changed_indexes = [index]

        if col == COL_BL:
            if cat.is_blacklisted == new_state:
                return False
            cat.is_blacklisted = new_state
            if new_state and cat.must_breed:
                cat.must_breed = False
                changed_indexes.append(self.index(index.row(), COL_MB))
        elif col == COL_MB:
            if cat.must_breed == new_state:
                return False
            cat.must_breed = new_state
            if new_state and cat.is_blacklisted:
                cat.is_blacklisted = False
                changed_indexes.append(self.index(index.row(), COL_BL))
        elif col == COL_PIN:
            if cat.is_pinned == new_state:
                return False
            cat.is_pinned = new_state

        for changed_index in changed_indexes:
            self.dataChanged.emit(changed_index, changed_index, [Qt.DisplayRole, Qt.CheckStateRole, Qt.ToolTipRole])
        self.blacklistChanged.emit()
        return True

    def cat_at(self, row: int) -> Optional[Cat]:
        return self._cats[row] if 0 <= row < len(self._cats) else None


# ── Sort helper items ─────────────────────────────────────────────────────────

class _SortByUserRoleItem(QTableWidgetItem):
    """QTableWidgetItem that sorts by UserRole data instead of display text."""
    def __lt__(self, other):
        a = self.data(Qt.UserRole)
        b = other.data(Qt.UserRole) if isinstance(other, QTableWidgetItem) else None
        if a is not None and b is not None:
            try:
                return a < b
            except TypeError:
                pass
        return super().__lt__(other)


class _SortKeyItem(QTableWidgetItem):
    """QTableWidgetItem that sorts by an integer key stored in Qt.UserRole."""
    def __lt__(self, other: QTableWidgetItem) -> bool:
        a = self.data(Qt.UserRole)
        b = other.data(Qt.UserRole)
        if a is None and b is None:
            return self.text() < other.text()
        if a is None:
            return True
        if b is None:
            return False
        return a < b
