"""Breed Priority — scoring config, helpers, and main scoring function.

Standalone module — no imports from mewgenics_manager to avoid circular deps.
"""

from save_parser import risk_percent, can_breed

from .stats_overview import get_cat_stats

# ── Personality trait thresholds ─────────────────────────────────────────────

TRAIT_LOW_THRESHOLD  = 0.3   # < this  → "low"
TRAIT_HIGH_THRESHOLD = 0.7   # >= this → "high"
GENETIC_SAFE_RISK_FLOOR = 2.0  # baseline/non-blood-risk floor (%)

# ── Scoring weights ───────────────────────────────────────────────────────────

BREED_PRIORITY_WEIGHTS = {
    "stat_7":           5.0,
    "stat_7_threshold": 7.0,   # cats with 7 in a stat before score scales down
    "stat_7_count":          2.0,   # flat bonus per stat at or above stat_count_threshold (additive)
    "stat_count_threshold":  7.0,   # minimum stat value counted by stat_7_count
    "trait_top_priority": 2.0,
    "trait_desirable":   2.0,
    "trait_undesirable": -2.0,
    "low_aggression":  1.0,
    "unknown_gender":  1.0,
    "high_libido":     0.5,
    "high_aggression": -1.0,
    "low_libido":      -0.5,
    "gay_pref":        0.0,
    "bi_pref":         0.0,
    "no_children":           -2.0,
    "zero_risk_bonus":        2.0,
    "gene_risk_threshold":    2.0,   # risk% threshold; below = bonus, above = scaling penalty
    "gene_risk_penalty_scale": 10.0, # higher = faster penalty growth (rate per 1% above threshold)
    "stat_sum":        4.0,
    "age_penalty":    -2.0,
    "age_threshold":  10.0,
    "love_interest":      1.0,
    "rivalry":           -2.0,
    "love_interest_room": 0.0,
    "rivalry_room":       0.0,
    "seven_sub":           0.0,
    "seven_sub_threshold": 1.0,
    "cha_low":             0.0,
}

# Weight editor UI rows
WEIGHT_UI_ROWS = [
    ("stat_sum",         "Stat Sum"),
    (None, None),
    ("age_penalty",      "Age penalty"),
    ("age_threshold",    "  └ threshold"),
    (None, None),
    ("stat_7",           "7rare"),
    ("stat_7_threshold", "  └ threshold"),
    ("stat_7_count",          "Stat-Count"),
    ("stat_count_threshold",  "  └ threshold"),
    (None, None),
    ("seven_sub",          "7-Sub score"),
    ("seven_sub_threshold","  └ threshold"),
    (None, None),
    ("cha_low",            "CHA ≤ 4 penalty"),
    (None, None),
    ("gay_pref",         ("Sex", "Gay")),
    ("bi_pref",          ("",       "Bi")),
    (None, None),
    ("high_libido",      ("Lib", "High")),
    ("low_libido",       ("",       "Low")),
    (None, None),
    ("unknown_gender",   "Unknown gender"),
    (None, None),
    ("no_children",             "Genetic Safety Risk"),
    ("zero_risk_bonus",         "Genetic Safety Bonus"),
    ("gene_risk_threshold",     "  └ threshold (%)"),
    ("gene_risk_penalty_scale", "  └ penalty scale"),
    (None, None),
    ("high_aggression",  ("Aggro", "High")),
    ("low_aggression",   ("",      "Low")),
    (None, None),
    ("rivalry",            ("Hate", "In Scope")),
    ("rivalry_room",       ("",     "In Room")),
    (None, None),
    ("love_interest",      ("Love", "In Scope")),
    ("love_interest_room", ("",     "In Room")),
    (None, None),
    ("trait_top_priority", ("Trait", "Top Priority")),
    ("trait_desirable",    ("",      "Desirable")),
    ("trait_undesirable",  ("",      "Undesirable")),
]

# Score table columns
SCORE_HEADER_7_COUNT = "St-Cnt"

SCORE_COLUMNS = [
    ("Sum",   ["stat_sum"]),
    ("Age",   ["age_penalty"]),
    ("7rare", ["stat_7"]),
    (SCORE_HEADER_7_COUNT,  ["stat_7_count"]),
    ("7sub",  ["seven_sub"]),
    ("CHA",   ["cha_low"]),
    ("Sex",   ["gay_pref", "bi_pref"]),
    ("Lib",   ["high_libido", "low_libido"]),
    ("Gender", ["unknown_gender"]),
    ("Gene",  ["no_children", "zero_risk_bonus"]),
    ("Aggro", ["low_aggression", "high_aggression"]),
    ("💥",     ["rivalry", "rivalry_room"]),
    ("💗",     ["love_interest", "love_interest_room"]),
    ("Trait", ["trait_top_priority", "trait_desirable", "trait_undesirable"]),
]

# Scoring tiers: (threshold, label, color) — first match wins; None = catch-all
BREED_PRIORITY_TIERS = [
    (10,   "Keep",     "#f0c060"),
    ( 4,   "Good",     "#1ec8a0"),
    ( 0,   "Neutral",  "#777777"),
    (-5,   "Consider", "#e08030"),
    (None, "Cull",     "#e04040"),
]

# Trait rating options
TRAIT_RATING_OPTIONS = [
    ("Top Priority - sole owner +2x, shared +1x÷n", 2),
    ("Desirable - sole owner +4, shared +2÷n",     1),
    ("Neutral - reviewed, not scored",              0),
    ("Undecided - not yet reviewed",                None),
    ("Undesirable - scored −2",                    -1),
]
TRAIT_RATING_LABELS = [label for label, _ in TRAIT_RATING_OPTIONS]
TRAIT_RATING_VALUES = [val   for _, val  in TRAIT_RATING_OPTIONS]
RATING_SHORT_LABELS = ["Top Priority", "Desirable", "Neutral", "Undecided", "Undesirable"]


# ── Scoring helpers ───────────────────────────────────────────────────────────

class ScoreResult:
    __slots__ = ("total", "tier", "tier_color", "breakdown", "subtotals",
                 "scope_gene_risk")

    def __init__(self, total: float, tier: str, tier_color: str, breakdown: list,
                 subtotals: dict | None = None, scope_gene_risk: float | None = None):
        self.total = total
        self.tier = tier
        self.tier_color = tier_color
        self.breakdown = breakdown
        self.subtotals = subtotals or {}
        self.scope_gene_risk = scope_gene_risk


def priority_tier(score: float) -> tuple:
    for threshold, label, color in BREED_PRIORITY_TIERS:
        if threshold is None or score >= threshold:
            return label, color
    return "Cull", "#e04040"


def is_basic_trait(name: str) -> bool:
    """Return True for generic starter traits that should be ignored."""
    return name.lower().startswith("basic")


def ability_base(name: str) -> str:
    """Strip trailing '2' if present (e.g. 'Vurp2' → 'Vurp'). When Breeding, we only care about the base ability."""
    if len(name) > 1 and name[-1] == "2":
        return name[:-1]
    return name


def is_upgraded(name: str) -> bool:
    """Return True if the ability name indicates a tier-2 upgrade (trailing '2')."""
    return len(name) > 1 and name[-1] == "2"


def compute_breed_priority_score(cat, scope_cats: list, ma_ratings: dict,
                         stat_names: list, weights: dict = None,
                         mutation_display_name=None,
                         scope_stat_sums: list = None,
                         hated_by: list = None,
                         gene_risk_lookup=None,
                         gene_risk_cache: dict | None = None,
                         use_current_stats: bool = False,
                         add_mutation_stats: bool = False) -> ScoreResult:
    """Compute breed priority score for a cat.

    stat_names: ordered list of stat keys (e.g. ["STR","DEX",...]).
    mutation_display_name: callable(str) -> str for display labels in breakdown.
    ma_ratings: {trait_key: int} where 1=Desirable, 0=Neutral, -1=Undesirable.
      Ability keys are base ability names; mutation keys are display strings.
    scope_stat_sums: sorted list of total base-stat sums for all scope cats,
      used to compute percentile rank for stat_sum scoring.
    hated_by: list of cats (in scope/room) that have *this* cat as their rival.
    """
    _w = weights if weights is not None else BREED_PRIORITY_WEIGHTS
    _display = mutation_display_name if mutation_display_name else (lambda n: n)
    _cat_stats = get_cat_stats(cat, use_current_stats, add_mutation_stats)
    _scope_stats = {id(c): get_cat_stats(c, use_current_stats, add_mutation_stats) for c in scope_cats}
    breakdown: list = []
    subtotals: dict = {
        "stat_7": 0.0, "stat_7_count": 0.0,
        "trait_top_priority": 0.0, "trait_desirable": 0.0, "trait_undesirable": 0.0,
        "low_aggression": 0.0, "high_aggression": 0.0,
        "unknown_gender": 0.0,
        "high_libido": 0.0, "low_libido": 0.0,
        "no_children": 0.0, "zero_risk_bonus": 0.0,
        "stat_sum": 0.0, "age_penalty": 0.0,
        "love_interest": 0.0, "rivalry": 0.0,
        "cha_low": 0.0,
    }
    scope_set = {id(c) for c in scope_cats}
    _cat_in_scope = id(cat) in scope_set

    # ── Positive attributes ───────────────────────────────────────────────────
    if cat.gender == "?":
        breakdown.append(("Unknown gender (?)", _w["unknown_gender"]))
        subtotals["unknown_gender"] = _w["unknown_gender"]

    # ── CHA penalty ───────────────────────────────────────────────────────────
    w_cha = _w.get("cha_low", 0.0)
    if w_cha != 0.0:
        _cha = _cat_stats.get("CHA")
        if _cha == 4:
            breakdown.append(("CHA = 4", round(w_cha, 3)))
            subtotals["cha_low"] = round(w_cha, 3)
        elif _cha == 3:
            _cha_pts = round(w_cha * 2, 3)
            breakdown.append(("CHA = 3 (2×)", _cha_pts))
            subtotals["cha_low"] = _cha_pts

    if cat.aggression is not None and cat.aggression < TRAIT_LOW_THRESHOLD:
        breakdown.append(("Low aggression", _w["low_aggression"]))
        subtotals["low_aggression"] = _w["low_aggression"]

    if cat.libido is not None and cat.libido >= TRAIT_HIGH_THRESHOLD:
        breakdown.append(("High libido", _w["high_libido"]))
        subtotals["high_libido"] = _w["high_libido"]

    _sex = getattr(cat, 'sexuality', 'straight') or 'straight'
    if _sex == 'gay' and _w.get("gay_pref", 0.0) != 0.0:
        breakdown.append(("Gay", _w["gay_pref"]))
        subtotals["gay_pref"] = _w["gay_pref"]
    elif _sex == 'bi' and _w.get("bi_pref", 0.0) != 0.0:
        breakdown.append(("Bi", _w["bi_pref"]))
        subtotals["bi_pref"] = _w["bi_pref"]

    _TARGET_N = int(round(_w.get("stat_7_threshold", 7.0)))  # cats with a 7 before score scales down
    _STAT7_BASE = _w["stat_7"]
    for stat_name in stat_names:
        if _cat_stats.get(stat_name) == 7:
            n_scope = sum(1 for c in scope_cats if _scope_stats[id(c)].get(stat_name) == 7)
            n = n_scope if _cat_in_scope else n_scope + 1
            # Sole owner of a 7 in this stat - extra bonus
            if n == 1:
                w = _w["stat_7"] * 2
                label = f"7 in {stat_name} (sole ★★)"
            # Full user weight up to target; beyond target, overflow portion
            # uses the default base weight so user increases favour the first 7
            elif n <= _TARGET_N:
                w = _w["stat_7"]
                label = f"7 in {stat_name} ({n} in scope)"
            else:
                w = round(_STAT7_BASE * _TARGET_N / n, 3)
                label = f"7 in {stat_name} ({n} in scope, ÷{n / _TARGET_N:.1f})"
            breakdown.append((label, float(w)))
            subtotals["stat_7"] += float(w)

    # ── Stat-count bonus: flat bonus per stat at or above the configured threshold ──
    _w_7ct = _w.get("stat_7_count", 0.0)
    if _w_7ct != 0.0:
        _stat_cnt_thr = int(round(_w.get("stat_count_threshold", 7.0)))
        _n_above_thr = sum(1 for sn in stat_names if _cat_stats.get(sn, 0) >= _stat_cnt_thr)
        if _n_above_thr > 0:
            _stat_cnt_pts = round(_w_7ct * _n_above_thr, 3)
            _s = "s" if _n_above_thr != 1 else ""
            breakdown.append((f"{_n_above_thr} stat{_s} at ≥{_stat_cnt_thr}", _stat_cnt_pts))
            subtotals["stat_7_count"] = _stat_cnt_pts

    # Combined trait set per scope cat (ability base names + mutation display strings)
    scope_base_traits = {
        id(c): (
            {ability_base(a) for a in list(c.abilities) + list(c.passive_abilities) + list(getattr(c, 'disorders', []))}
            | set(c.mutations)
            | set(getattr(c, 'defects', []))
        )
        for c in scope_cats
    }
    _w_top = _w.get("trait_top_priority", 0.0)
    _w_des = _w.get("trait_desirable", 0.0)
    _w_und = _w.get("trait_undesirable", 0.0)

    # Score abilities (active + passive), normalized to base names
    all_ability_bases = list({
        ability_base(m) for m in list(cat.abilities) + list(cat.passive_abilities) + list(getattr(cat, 'disorders', []))
        if not is_basic_trait(m)
    })
    def _score_trait(label: str, rating, n: int):
        if rating in (None, 0):
            return
        if n == 1:
            if rating == 2:
                pts = 2 * _w_top
                tag = "Sole owner (top priority)"
            elif rating == 1:
                pts = 2 * _w_des
                tag = "Sole owner (desirable)"
            else:
                pts = _w_und
                tag = "Sole owner (undesirable)"
        elif rating == 2:
            pts = round(_w_top / n, 3)
            tag = f"Top Priority (÷{n})"
        elif rating == 1:
            pts = round(_w_des / n, 3)
            tag = f"Desirable (÷{n})"
        elif rating == -1:
            pts = _w_und
            tag = "Undesirable"
        else:
            return
        breakdown.append((f"{tag}: {label}", pts))
        if rating == 2:
            subtotals["trait_top_priority"] += pts
        elif rating == 1:
            subtotals["trait_desirable"] += pts
        elif rating == -1:
            subtotals["trait_undesirable"] += pts

    for ma in all_ability_bases:
        rating = ma_ratings.get(ma)
        n_scope = sum(1 for c in scope_cats if ma in scope_base_traits[id(c)])
        n = max(1, n_scope if _cat_in_scope else n_scope + 1)
        _score_trait(_display(ma), rating, n)

    # Score visual mutations (keyed by display string from cat.mutations)
    for ma in cat.mutations:
        if is_basic_trait(ma):
            continue
        rating = ma_ratings.get(ma)
        n_scope = sum(1 for c in scope_cats if ma in scope_base_traits[id(c)])
        n = max(1, n_scope if _cat_in_scope else n_scope + 1)
        _score_trait(ma, rating, n)

    # Score birth defects (visual mutation IDs 700-706)
    for ma in getattr(cat, 'defects', []):
        if is_basic_trait(ma):
            continue
        rating = ma_ratings.get(ma)
        n_scope = sum(1 for c in scope_cats if ma in scope_base_traits[id(c)])
        n = max(1, n_scope if _cat_in_scope else n_scope + 1)
        _score_trait(ma, rating, n)

    # ── Negative attributes ───────────────────────────────────────────────────
    if cat.aggression is not None and cat.aggression >= TRAIT_HIGH_THRESHOLD:
        breakdown.append(("High aggression", _w["high_aggression"]))
        subtotals["high_aggression"] = _w["high_aggression"]

    if cat.libido is not None and cat.libido < TRAIT_LOW_THRESHOLD:
        breakdown.append(("Low libido", _w["low_libido"]))
        subtotals["low_libido"] = _w["low_libido"]

    # Genetic Safety: average in-scope pair risk.
    # Only include partners this cat can actually breed with — same-sex and
    # incompatible-sexuality pairs are excluded since they can't produce kittens.
    risk_fn = gene_risk_lookup if callable(gene_risk_lookup) else risk_percent
    _risk_vals = []
    for partner in scope_cats:
        if partner is cat:
            continue
        if not can_breed(cat, partner)[0]:
            continue
        if gene_risk_cache is not None:
            _rk = (id(cat), id(partner)) if id(cat) < id(partner) else (id(partner), id(cat))
            _rv = gene_risk_cache.get(_rk)
            if _rv is None:
                _rv = float(risk_fn(cat, partner))
                gene_risk_cache[_rk] = _rv
        else:
            _rv = float(risk_fn(cat, partner))
        _risk_vals.append(_rv)
    # None means no breedable partners in scope — no gene score in either direction.
    gene_risk: float | None = (sum(_risk_vals) / len(_risk_vals)) if _risk_vals else None
    if gene_risk is not None:
        # Keep scoring aligned with displayed Gene buckets (R0, R1, R2, ...).
        _gene_risk_display = float(int(round(gene_risk)))
        _gene_threshold = float(_w.get("gene_risk_threshold", GENETIC_SAFE_RISK_FLOOR))
        _gene_penalty_scale = float(_w.get("gene_risk_penalty_scale", 10.0))
        _effective_gene_risk = max(0.0, _gene_risk_display - _gene_threshold)
        gene_units = round(_effective_gene_risk * _gene_penalty_scale / 100.0, 3)
        if gene_units > 0:
            gene_pts = round(_w["no_children"] * gene_units, 3)
            breakdown.append((f"Genetic risk {gene_risk:.1f}% (R{int(_gene_risk_display)}, {_gene_threshold:.0f}% threshold)", gene_pts))
            subtotals["no_children"] = gene_pts
        elif _gene_risk_display <= _gene_threshold:
            safe_pts = float(_w.get("zero_risk_bonus", 0.0))
            if safe_pts != 0.0:
                breakdown.append((f"Genetic safety (R{int(_gene_risk_display)} ≤ {_gene_threshold:.0f})", safe_pts))
                subtotals["zero_risk_bonus"] = safe_pts

    # ── Stat sum rank-based scoring ───────────────────────────────────────────
    # Ranks by unique values so all cats at the same sum share a rank and a
    # single outlier above/below doesn't compress the rest of the gradient.
    w_sum = _w.get("stat_sum", 0.0)
    if w_sum != 0 and scope_stat_sums:
        cat_sum = sum(_cat_stats.values())
        _unique_sums = sorted(set(scope_stat_sums) | {cat_sum})
        _n_unique = len(_unique_sums)
        if _n_unique <= 1:
            _sum_t = 1.0
            _sum_rank_idx = 0
        else:
            _sum_rank_idx = _unique_sums.index(cat_sum)
            _sum_t = _sum_rank_idx / (_n_unique - 1)
        pts = round(w_sum * _sum_t, 3)
        if pts:
            breakdown.append((f"Stat sum {cat_sum} (rank {_sum_rank_idx + 1}/{_n_unique})", pts))
            subtotals["stat_sum"] = pts

    # ── Age penalty ───────────────────────────────────────────────────────────
    w_age = _w.get("age_penalty", 0.0)
    if w_age != 0.0:
        age = getattr(cat, 'age', None)
        if age is not None:
            _age_thr = int(round(_w.get("age_threshold", 10.0)))
            if age > _age_thr:
                _over = age - _age_thr
                _mult = 1 + (_over - 1) // 3
                pts = round(_mult * w_age, 2)
                breakdown.append((f"Age {age} (+{_over} over threshold, {_mult}×)", pts))
                subtotals["age_penalty"] = pts

    # ── Love interest bonus ────────────────────────────────────────────────────
    w_love = _w.get("love_interest", 0.0)
    if w_love != 0.0:
        for lover in getattr(cat, 'lovers', []):
            if id(lover) in scope_set:
                pts = round(w_love, 2)
                breakdown.append((f"Loves {lover.name} (in scope)", pts))
                subtotals["love_interest"] = pts
                break  # flat bonus - only once

    # ── Rivalry penalty ────────────────────────────────────────────────────────
    w_rival = _w.get("rivalry", 0.0)
    if w_rival != 0.0:
        _rival_total = 0.0
        # Cat's own rivals in scope
        for hater in getattr(cat, 'haters', []):
            if id(hater) in scope_set:
                pts = round(w_rival, 2)
                breakdown.append((f"Hates {hater.name} (in scope)", pts))
                _rival_total += pts
        # Cats in scope that hate this cat (reverse)
        for hater in (hated_by or []):
            if id(hater) in scope_set and hater not in getattr(cat, 'haters', []):
                pts = round(w_rival, 2)
                breakdown.append((f"Hated by {hater.name} (in scope)", pts))
                _rival_total += pts
        if _rival_total:
            subtotals["rivalry"] = _rival_total

    # ── Love interest (room) bonus ─────────────────────────────────────────────
    w_love_room = _w.get("love_interest_room", 0.0)
    if w_love_room != 0.0:
        _cat_room = getattr(cat, 'room', None)
        if _cat_room:
            for lover in getattr(cat, 'lovers', []):
                if getattr(lover, 'room', None) == _cat_room:
                    pts = round(w_love_room, 2)
                    breakdown.append((f"Loves {lover.name} (in room)", pts))
                    subtotals["love_interest_room"] = pts
                    break

    # ── Rivalry (room) penalty ─────────────────────────────────────────────────
    w_rival_room = _w.get("rivalry_room", 0.0)
    if w_rival_room != 0.0:
        _cat_room = getattr(cat, 'room', None)
        if _cat_room:
            _rr_total = 0.0
            for hater in getattr(cat, 'haters', []):
                if getattr(hater, 'room', None) == _cat_room:
                    pts = round(w_rival_room, 2)
                    breakdown.append((f"Hates {hater.name} (in room)", pts))
                    _rr_total += pts
            for hater in (hated_by or []):
                if hater not in getattr(cat, 'haters', []) and getattr(hater, 'room', None) == _cat_room:
                    pts = round(w_rival_room, 2)
                    breakdown.append((f"Hated by {hater.name} (in room)", pts))
                    _rr_total += pts
            if _rr_total:
                subtotals["rivalry_room"] = _rr_total

    total = sum(pts for _, pts in breakdown)
    tier, color = priority_tier(total)
    return ScoreResult(total=total, tier=tier, tier_color=color,
                       breakdown=breakdown, subtotals=subtotals,
                       scope_gene_risk=gene_risk)
