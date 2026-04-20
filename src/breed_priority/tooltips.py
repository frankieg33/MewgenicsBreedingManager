"""Breed Priority — HTML tooltip builders.

Standalone functions; no reference to BreedPriorityView instance state.
All required context is passed in explicitly.
"""

from .columns import _STAT_COL_NAMES
from .scoring import ScoreResult, ability_base, is_basic_trait, is_upgraded
from .theme import (
    CLR_HIGHLIGHT, CLR_TEXT_UI_LABEL,
    CLR_TOP_PRIORITY, CLR_DESIRABLE, CLR_UNDESIRABLE,
    CLR_NEUTRAL, CLR_UNDECIDED,
    CLR_VALUE_POS, CLR_VALUE_NEG, CLR_VALUE_NEUTRAL,
    _SEX_EMOJI_GAY, _SEX_EMOJI_BI,
)


def build_child_tooltip(cat, display_name_fn) -> str:
    """Build a rich HTML tooltip with full cat info for the children panel.

    Args:
        cat: Cat object.
        display_name_fn: Callable(trait_key) -> str  (e.g. BreedPriorityView._display_name)
    """
    html_parts = [
        f'<html><body style="font-family:monospace;font-size:11px;margin:0;padding:0;">',
        f'<b style="color:{CLR_HIGHLIGHT};font-size:12px">{cat.name}</b>'
        f' <span style="color:#88aacc;font-size:11px">{cat.gender_display}</span>'
        f' <span style="color:{CLR_TEXT_UI_LABEL};font-size:10px">age {getattr(cat, "age", "?")}</span>',
    ]
    # Stats row
    stats_str = "  ".join(
        f'{sn}:<b style="color:#ddddee">{cat.base_stats.get(sn, "?")}</b>'
        for sn in _STAT_COL_NAMES
    )
    html_parts.append(
        f'<br><span style="color:#888;font-size:10px">{stats_str}</span>'
    )
    # Trait sections
    passive_tiers = getattr(cat, 'passive_tiers', {})
    active_abs  = [
        display_name_fn(ability_base(a)) + ("+" if is_upgraded(a) else "")
        for a in cat.abilities if not is_basic_trait(a)
    ]
    passive_abs = [
        display_name_fn(ability_base(a)) + ("+" if passive_tiers.get(a, 1) > 1 else "")
        for a in cat.passive_abilities if not is_basic_trait(a)
    ]
    disorders   = [display_name_fn(ability_base(d))
                   for d in getattr(cat, 'disorders', []) if not is_basic_trait(d)]
    mutations   = [m for m in cat.mutations if not is_basic_trait(m)]
    defects     = [d for d in getattr(cat, 'defects', []) if not is_basic_trait(d)]

    for title, items, color in (
        ("ACTIVE ABILITIES",  active_abs,  CLR_DESIRABLE),
        ("PASSIVE ABILITIES", passive_abs, "#88aacc"),
        ("DISORDERS",         disorders,   CLR_UNDESIRABLE),
        ("MUTATIONS",         mutations,   "#cc88ff"),
        ("BIRTH DEFECTS",     defects,     "#cc4444"),
    ):
        if items:
            rows = "".join(
                f'<tr><td style="color:{color};padding:0 8px 0 0">{it}</td></tr>'
                for it in items
            )
            html_parts.append(
                f'<br><span style="color:{CLR_TEXT_UI_LABEL};font-size:10px">{title}</span>'
                f'<table cellspacing="0" cellpadding="1">{rows}</table>'
            )
    html_parts.append('</body></html>')
    return "".join(html_parts)


def build_cat_tooltip(
    cat,
    result: ScoreResult,
    scope_cats: list,
    *,
    weights: dict,
    ma_ratings: dict,
    display_name_fn,
    room_display: dict,
    hated_by_map: dict,
    loved_by_map: dict,
    cat_injuries_fn,
    top_gene_risks: list | None = None,
    cw_items: list | None = None,
    cw_delta_total: float = 0.0,
) -> str:
    """Build a rich HTML tooltip showing traits, relationships and score breakdown.

    Args:
        cat: Cat object.
        result: ScoreResult for this cat.
        scope_cats: List of cats in the current scope.
        weights: Current scoring weight dict.
        ma_ratings: Trait rating dict (trait_key -> int).
        display_name_fn: Callable(trait_key) -> str.
        room_display: Dict mapping room id -> display label.
        hated_by_map: Dict mapping id(cat) -> list of cats that hate it.
        loved_by_map: Dict mapping id(cat) -> list of cats that love it.
        cat_injuries_fn: Callable(cat) -> list of (name, stat_key, delta).
    """
    def row(color: str, label: str, score: str) -> str:
        return (
            f'<tr>'
            f'<td style="color:{color};padding:0 8px 0 0">{label}</td>'
            f'<td style="color:{color};text-align:right">{score}</td>'
            f'</tr>'
        )

    _scope_base = {
        id(c): (
            {ability_base(a) for a in list(c.abilities) + list(c.passive_abilities) + list(getattr(c, 'disorders', []))
             if not is_basic_trait(a)}
            | set(c.mutations)
            | set(getattr(c, 'defects', []))
        )
        for c in scope_cats
    }
    _w_top = float(weights.get("trait_top_priority", 0.0))
    _w_des = float(weights.get("trait_desirable", 0.0))
    _w_und = float(weights.get("trait_undesirable", 0.0))

    passive_base = {
        ability_base(p) for p in cat.passive_abilities if not is_basic_trait(p)
    }
    disorder_base = {
        ability_base(d) for d in getattr(cat, 'disorders', []) if not is_basic_trait(d)
    }
    seen: set = set()
    active_traits = [
        t for t in (
            ability_base(a) for a in cat.abilities
            if not is_basic_trait(a) and ability_base(a) not in passive_base
            and ability_base(a) not in disorder_base
        )
        if not (t in seen or seen.add(t))
    ]
    passive_traits = sorted(passive_base)
    disorder_traits = sorted(disorder_base)
    mutation_traits = [t for t in cat.mutations if not is_basic_trait(t)]
    defect_traits = [t for t in getattr(cat, 'defects', []) if not is_basic_trait(t)]

    _upgraded_active = {ability_base(a) for a in cat.abilities if is_upgraded(a)}
    _passive_tiers = getattr(cat, 'passive_tiers', {})
    _upgraded_passive = {
        ability_base(p) for p in cat.passive_abilities
        if _passive_tiers.get(p, 1) > 1
    }

    def _trait_rows_for(traits: list, upgraded_set: set = frozenset()) -> list:
        rows = []
        for trait in traits:
            suffix = "+" if trait in upgraded_set else ""
            display = display_name_fn(trait) + suffix
            rating = ma_ratings.get(trait)
            sharing = [c for c in scope_cats
                       if c is not cat and trait in _scope_base[id(c)]]
            n = len(sharing) + 1  # +1 for the cat itself
            cats_str = f" ({n} cats)"
            if rating in (None, 0):
                color = CLR_UNDECIDED if rating is None else CLR_NEUTRAL
                label = f"{display}  ?" if rating is None else display
                rows.append(row(color, label, "+0.00"))
            elif n == 1:
                if rating == 2:
                    pts = 2 * _w_top
                    clr, star = CLR_TOP_PRIORITY, "★★★"
                elif rating == 1:
                    pts = 2 * _w_des
                    clr, star = CLR_DESIRABLE, "★★"
                else:
                    pts = _w_und
                    clr, star = CLR_UNDESIRABLE, "★"
                rows.append(row(clr, f"{display}  {star}", f"{pts:+.2f}"))
            elif rating == 2:
                pts = round(_w_top / n, 3)
                rows.append(row(CLR_TOP_PRIORITY, display, f"{pts:+.2f}{cats_str}"))
            elif rating == 1:
                pts = round(_w_des / n, 3)
                rows.append(row(CLR_DESIRABLE, display, f"{pts:+.2f}{cats_str}"))
            elif rating == -1:
                rows.append(row(CLR_UNDESIRABLE, display, f"{_w_und:+.2f}{cats_str}"))
            else:
                rows.append(row(CLR_NEUTRAL, display, f"+0.00{cats_str}"))
            if sharing:
                names = [c.name for c in sharing[:5]]
                extra = len(sharing) - 5
                names_text = ", ".join(names)
                if extra > 0:
                    names_text += f", +{extra} more"
                rows.append(row(CLR_HIGHLIGHT, f"&nbsp;&nbsp;↳ {names_text}", ""))
        return rows

    active_rows   = _trait_rows_for(active_traits,  _upgraded_active)
    passive_rows  = _trait_rows_for(passive_traits, _upgraded_passive)
    disorder_rows = _trait_rows_for(disorder_traits)
    mutation_rows = _trait_rows_for(mutation_traits)
    defect_rows   = _trait_rows_for(defect_traits)

    # Build injury rows
    _injuries = cat_injuries_fn(cat)
    injury_rows = []
    for _iname, _isn, _idelta in _injuries:
        injury_rows.append(row("#cc4444", _isn, f"{_idelta:+d}"))

    scope_set = {id(c) for c in scope_cats}
    children_in_scope = [c for c in cat.children if id(c) in scope_set]
    other_rows = []
    for desc, pts in result.breakdown:
        if desc.startswith(("Sole owner", "Top Priority (÷", "Desirable (÷", "Undesirable:")):
            continue
        color = CLR_VALUE_POS if pts > 0 else CLR_VALUE_NEG
        other_rows.append(row(color, desc, f"{pts:+.2f}"))
        if "children in scope" in desc and children_in_scope:
            for child in children_in_scope:
                room = room_display.get(child.room, child.room or "?")
                other_rows.append(row(CLR_HIGHLIGHT, f"&nbsp;&nbsp;↳ {child.name}  ({room})", ""))

    total_color = CLR_VALUE_POS if result.total > 0 else CLR_VALUE_NEG if result.total < 0 else CLR_VALUE_NEUTRAL
    _sex = getattr(cat, 'sexuality', 'straight') or 'straight'
    _sex_glyph = (
        f' <span style="font-size:14px">{_SEX_EMOJI_GAY}</span>' if _sex == 'gay' else
        f' <span style="font-size:14px">{_SEX_EMOJI_BI}</span>'  if _sex == 'bi'  else
        ''
    )
    html_parts = [
        f'<html><body style="font-family:monospace;font-size:11px;margin:0;padding:0;">',
        f'<b style="color:{CLR_HIGHLIGHT};font-size:12px">{cat.name}</b>'
        f'{_sex_glyph}'
        f' <span style="color:#88aacc;font-size:11px">{cat.gender_display}</span>'
        f' <span style="color:{CLR_TEXT_UI_LABEL};font-size:10px">age {getattr(cat, "age", "?")}</span>',
    ]
    if injury_rows:
        html_parts.append('<br><span style="color:#cc4444;font-size:10px">INJURIES</span>')
        html_parts.append('<table cellspacing="0" cellpadding="1">' + "".join(injury_rows) + '</table>')
    _TT_SECTION = f'<br><span style="color:{CLR_TEXT_UI_LABEL};font-size:10px">'
    if active_rows:
        html_parts.append(f'{_TT_SECTION}ACTIVE ABILITIES</span>')
        html_parts.append('<table cellspacing="0" cellpadding="1">' + "".join(active_rows) + '</table>')
    if passive_rows:
        html_parts.append(f'{_TT_SECTION}PASSIVE ABILITIES</span>')
        html_parts.append('<table cellspacing="0" cellpadding="1">' + "".join(passive_rows) + '</table>')
    if disorder_rows:
        html_parts.append(f'{_TT_SECTION}DISORDERS</span>')
        html_parts.append('<table cellspacing="0" cellpadding="1">' + "".join(disorder_rows) + '</table>')
    if mutation_rows:
        html_parts.append(f'{_TT_SECTION}MUTATIONS</span>')
        html_parts.append('<table cellspacing="0" cellpadding="1">' + "".join(mutation_rows) + '</table>')
    if defect_rows:
        html_parts.append(f'{_TT_SECTION}BIRTH DEFECTS</span>')
        html_parts.append('<table cellspacing="0" cellpadding="1">' + "".join(defect_rows) + '</table>')
    if other_rows:
        html_parts.append(f'{_TT_SECTION}OTHER</span>')
        html_parts.append('<table cellspacing="0" cellpadding="1">' + "".join(other_rows) + '</table>')

    # ── Relationships (hate / love) with room/scope context ──
    _cat_room = getattr(cat, 'room', None)
    _rel_rows = []
    scope_set_ids = {id(c) for c in scope_cats}

    def _rel_context(other):
        """Return (context_str, color) for a relationship target."""
        _or = getattr(other, 'room', None)
        _same_room = _cat_room and _or == _cat_room
        _in_scope = id(other) in scope_set_ids
        if _same_room and _in_scope:
            return "room + scope", "#ddaa44"
        elif _same_room:
            return "room", "#dd8844"
        elif _in_scope:
            return "scope", "#88aacc"
        else:
            return "out of scope", "#666666"

    # Cats this cat hates
    for h in getattr(cat, 'haters', []):
        ctx, clr = _rel_context(h)
        _rel_rows.append(row(clr, f"Hates {h.name}", f"({ctx})"))
    # Cats that hate this cat (reverse)
    for h in hated_by_map.get(id(cat), []):
        if h not in getattr(cat, 'haters', []):
            ctx, clr = _rel_context(h)
            _rel_rows.append(row(clr, f"Hated by {h.name}", f"({ctx})"))
    # Cats this cat loves
    for lv in getattr(cat, 'lovers', []):
        ctx, clr = _rel_context(lv)
        _rel_rows.append(row(clr, f"Loves {lv.name}", f"({ctx})"))
    # Cats that love this cat (reverse)
    for lv in loved_by_map.get(id(cat), []):
        if lv not in getattr(cat, 'lovers', []):
            ctx, clr = _rel_context(lv)
            _rel_rows.append(row(clr, f"Loved by {lv.name}", f"({ctx})"))
    if _rel_rows:
        html_parts.append(f'{_TT_SECTION}RELATIONSHIPS</span>')
        html_parts.append('<table cellspacing="0" cellpadding="1">' + "".join(_rel_rows) + '</table>')

    if top_gene_risks:
        _risk_rows = []
        for partner_name, risk_pct in top_gene_risks[:3]:
            clr = CLR_VALUE_NEG if risk_pct >= 20.0 else CLR_NEUTRAL if risk_pct >= 10.0 else CLR_DESIRABLE
            _risk_rows.append(row(clr, partner_name, f"{risk_pct:.1f}%"))
        html_parts.append(f'{_TT_SECTION}TOP RISKY PARTNERS (SCOPE)</span>')
        html_parts.append('<table cellspacing="0" cellpadding="1">' + "".join(_risk_rows) + '</table>')

    if cw_items:
        _cw_rows = []
        for cw_name, cw_delta in cw_items:
            _cw_clr = CLR_VALUE_POS if cw_delta > 0 else CLR_VALUE_NEG if cw_delta < 0 else CLR_VALUE_NEUTRAL
            _cw_rows.append(row(_cw_clr, f"⭐ {cw_name}", f"{cw_delta:+.2f}"))
        html_parts.append(f'{_TT_SECTION}COMPLEX WEIGHTS</span>')
        html_parts.append('<table cellspacing="0" cellpadding="1">' + "".join(_cw_rows) + '</table>')

    _adjusted_total = result.total + cw_delta_total
    _adjusted_color = CLR_VALUE_POS if _adjusted_total > 0 else CLR_VALUE_NEG if _adjusted_total < 0 else CLR_VALUE_NEUTRAL
    html_parts.append(
        f'<br><b style="color:{_adjusted_color}">Total: {_adjusted_total:+.2f}</b>'
    )
    html_parts.append('</body></html>')
    return "".join(html_parts)
