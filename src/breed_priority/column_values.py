"""Breed Priority — column value computation for value / both display modes.

``raw_col_value`` is a pure function: it takes a cat and context data and
returns a (text, sort_val, color) tuple for rendering a single score-table cell.
"""

from .chip_colors import ChipColors
from .color_utils import ColorUtils
from .columns import _ALL_HEADERS, _STAT_COL_NAMES, _ROOM_STYLE
from .scoring import SCORE_HEADER_7_COUNT, TRAIT_HIGH_THRESHOLD, TRAIT_LOW_THRESHOLD, GENETIC_SAFE_RISK_FLOOR
from .theme import (
    CLR_DESIRABLE, CLR_GENDER_FEMALE, CLR_GENDER_MALE, CLR_GENDER_UNKNOWN,
    CLR_TEXT_COUNT, CLR_TEXT_GRAYEDOUT, CLR_UNDESIRABLE,
    CLR_VALUE_NEUTRAL,
    _CLR_AGE_OLD,
    _SEX_EMOJI_BI, _SEX_EMOJI_GAY,
)


def raw_col_value(
    cat,
    col_idx: int,
    scope_gene_risk: float | None,
    all_scope_gene_risks: list,
    *,
    weights: dict,
    room_display: dict,
) -> tuple:
    """Return (text, sort_val, color) for a column in value mode.

    Args:
        cat: Cat object.
        col_idx: Absolute column index into _ALL_HEADERS.
        scope_gene_risk: Average in-scope pair risk (%) for this cat.
        all_scope_gene_risks: List of scope risk values for all cats
            (used for percentile ranking in the Gene column).
        weights: Current scoring weight dict.
        room_display: Dict mapping room id -> display label.
    """
    hdr = _ALL_HEADERS[col_idx]

    if hdr == "Age":
        age = getattr(cat, 'age', None)
        if age is None:
            return ("-", -1.0, "#666")
        _age_thr = int(round(weights.get("age_threshold", 10.0)))
        _over = age - _age_thr
        t = 0.0 if _over <= 0 else min(1.0, _over / 20.0)
        color = ColorUtils.lerp(CLR_VALUE_NEUTRAL, _CLR_AGE_OLD, t)
        text = f"⏳{age}" if _over > 0 else str(age)
        return (text, float(age), color)

    if hdr == "Loc":
        loc = room_display.get(cat.room, cat.room or "")
        _clr = _ROOM_STYLE.get(loc)
        return (loc, 0, _clr or CLR_VALUE_NEUTRAL)

    if hdr in _STAT_COL_NAMES:
        val = cat.base_stats.get(hdr, 0)
        # 7=green, 6=medium yellow, 5=greyer yellow, 4=grey
        _STAT_VAL_COLORS = {7: "#44cc66", 6: "#bba844", 5: "#998855", 4: CLR_VALUE_NEUTRAL}
        color = _STAT_VAL_COLORS.get(val, CLR_VALUE_NEUTRAL)
        return (str(val), float(val), color)

    if hdr == "Sum":
        s = sum(cat.base_stats.values())
        return (str(s), float(s), "#aaaaaa")

    if hdr == SCORE_HEADER_7_COUNT:
        _stat_cnt_thr = int(round(weights.get("stat_count_threshold", 7.0)))
        count_above_thr = sum(1 for v in cat.base_stats.values() if v >= _stat_cnt_thr)
        if count_above_thr == 0:
            color = CLR_TEXT_GRAYEDOUT
        else:
            # grey→gold
            t = min(1.0, count_above_thr / 7.0)
            color = ColorUtils.lerp(CLR_VALUE_NEUTRAL, "#ffcc00", t)
        return (str(count_above_thr) if count_above_thr else "", float(count_above_thr), color)

    if hdr == "Trait":
        return ("", 0.0, CLR_VALUE_NEUTRAL)

    if hdr == "Aggro":
        a = cat.aggression
        if a is None:
            return ("?", 0.0, "#666")
        _high_ag_w = weights.get("high_aggression", 0.0)
        _low_ag_w  = weights.get("low_aggression",  0.0)
        _high_clr, _low_clr = ChipColors.paired_weights(_high_ag_w, _low_ag_w)
        if a >= TRAIT_HIGH_THRESHOLD:
            return ("▲Hi", a, _high_clr)
        elif a < TRAIT_LOW_THRESHOLD:
            return ("▼Lo", a, _low_clr)
        else:
            return ("—",   a, CLR_TEXT_GRAYEDOUT)

    if hdr == "Gender":
        gd = getattr(cat, 'gender_display', '?')
        if gd in ('M', 'Male'):
            return ("M", 0.0, CLR_GENDER_MALE)
        elif gd in ('F', 'Female'):
            return ("F", 0.0, CLR_GENDER_FEMALE)
        return ("?", 1.0, CLR_GENDER_UNKNOWN)

    if hdr == "Lib":
        lb = cat.libido
        if lb is None:
            return ("?", 0.0, "#666")
        _high_lb_w = weights.get("high_libido", 0.0)
        _low_lb_w  = weights.get("low_libido",  0.0)
        _high_clr, _low_clr = ChipColors.paired_weights(_high_lb_w, _low_lb_w)
        if lb >= TRAIT_HIGH_THRESHOLD:
            return ("❤️", lb, _high_clr)
        elif lb < TRAIT_LOW_THRESHOLD:
            return ("💙", lb, _low_clr)
        else:
            return ("—", lb, CLR_TEXT_GRAYEDOUT)

    if hdr == "Sex":
        sex = getattr(cat, 'sexuality', 'straight') or 'straight'
        if sex == 'straight':
            return ("", 0.0, CLR_TEXT_COUNT)
        gay_w = weights.get("gay_pref", 0.0)
        bi_w  = weights.get("bi_pref",  0.0)
        gay_clr, bi_clr = ChipColors.paired_weights(gay_w, bi_w)
        if sex == 'gay':
            return (f"{_SEX_EMOJI_GAY}", gay_w, gay_clr)
        else:  # bi
            return (f"{_SEX_EMOJI_BI}", bi_w, bi_clr)

    if hdr == "Gene":
        if scope_gene_risk is None:
            return ("—", -1.0, CLR_VALUE_NEUTRAL)
        n = float(scope_gene_risk)
        total = len(all_scope_gene_risks)
        if total > 0:
            rank = sum(1 for v in all_scope_gene_risks if v <= n)
            pct = rank / total * 100
            # lower risk = better (greener)
            if n == 0:
                color = CLR_DESIRABLE
            elif pct >= 75:
                color = CLR_UNDESIRABLE
            elif pct >= 50:
                color = "#e08030"
            else:
                color = "#b0a040"
        else:
            color = CLR_VALUE_NEUTRAL
        _gene_thr = float(weights.get("gene_risk_threshold", GENETIC_SAFE_RISK_FLOOR))
        text = "🛡" if n <= _gene_thr else f"R{int(round(n))}"
        return (text, float(n), color)

    return ("", 0.0, CLR_VALUE_NEUTRAL)
