"""Breed-priority scoring — weights, tiers, and main scoring function.

Pure Python — no Qt dependencies. Canonical home for the scoring engine
shared by Simple Scoring (views/manual_scoring.py) and Detailed Scoring
(src/breed_priority/). `breed_priority/scoring.py` is a thin shim that
re-exports from this module while preserving its own UI-layer constants.
"""

from __future__ import annotations

from save_parser import risk_percent, can_breed

from mewgenics.scoring.cat_stats import get_cat_stats

# ── Trait thresholds ─────────────────────────────────────────────────────────

TRAIT_LOW_THRESHOLD = 0.3
TRAIT_HIGH_THRESHOLD = 0.7
GENETIC_SAFE_RISK_FLOOR = 2.0

# ── Default weights ──────────────────────────────────────────────────────────

BREED_PRIORITY_WEIGHTS: dict[str, float] = {
    "stat_7":           5.0,
    "stat_7_threshold": 7.0,
    "stat_7_count":     2.0,
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
    "gene_risk_threshold":    2.0,
    "gene_risk_penalty_scale": 10.0,
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

# ── Weight tooltips ──────────────────────────────────────────────────────────

WEIGHT_TOOLTIPS: dict[str, str] = {
    "stat_sum":              "Points for total stat sum percentile among scope cats.\nTop 10% = full weight, top 25% = weight-1, top 50% = weight-2.",
    "age_penalty":           "Penalty applied to cats older than the age threshold.\nScales with how far over the threshold they are.",
    "age_threshold":         "Age at which the age penalty starts applying.\nCats at or below this age get no penalty.",
    "stat_7":                "Points for each stat at 7. Rarer stats in scope score higher.\nA sole owner of a 7-stat gets double points.",
    "stat_7_threshold":      "How many scope cats can share a 7 before the bonus starts diminishing.\nBelow this count = full points; above = divided proportionally.",
    "stat_7_count":          "Bonus per stat that the cat has at 7.\nRewards cats with multiple maxed stats.",
    "seven_sub":             "Penalty when a cat's set of 7-stats is strictly dominated\nby another scope cat (the other has all the same 7s and more).",
    "seven_sub_threshold":   "How many dominating cats it takes to reach the full 7-Sub penalty.\nPartial penalty below this count.",
    "cha_low":               "Penalty for low CHA. CHA=4 gets 1x weight, CHA 3 or below gets 2x.\nCHA affects breeding compatibility chance.",
    "gay_pref":              "Score adjustment for gay cats.\nPositive = prefer, negative = penalize, 0 = ignore.",
    "bi_pref":               "Score adjustment for bi cats.\nPositive = prefer, negative = penalize, 0 = ignore.",
    "high_libido":           "Bonus for cats with high libido (>= 0.7).\nHigh libido increases breeding success chance.",
    "low_libido":            "Penalty for cats with low libido (< 0.3).\nLow libido decreases breeding success chance.",
    "unknown_gender":        "Bonus for cats with unknown gender (?).\nThese cats can breed with any gender.",
    "no_children":           "Penalty scaled by average genetic risk with breedable scope partners.\nHigher risk = more penalty. Uses the threshold and scale below.",
    "zero_risk_bonus":       "Bonus for cats whose average genetic risk is at or below the threshold.\nRewards genetically safe breeders.",
    "gene_risk_threshold":   "Genetic risk percentage below which a cat is considered 'safe'.\nRisk above this triggers the penalty; at or below triggers the bonus.",
    "gene_risk_penalty_scale": "How aggressively genetic risk above the threshold is penalized.\nHigher = steeper penalty curve.",
    "high_aggression":       "Penalty for cats with high aggression (>= 0.7).\nHigh aggression cats may fight instead of breed.",
    "low_aggression":        "Bonus for cats with low aggression (< 0.3).\nLow aggression cats are calmer breeders.",
    "rivalry":               "Penalty for each hate relationship with a cat in scope.\nApplies to both hating and being hated.",
    "rivalry_room":          "Penalty for each hate relationship with a cat in the same room.\nMore targeted than the scope-wide rivalry penalty.",
    "love_interest":         "Bonus if this cat has a lover in scope.\nLover pairs get a breeding compatibility multiplier.",
    "love_interest_room":    "Bonus if this cat has a lover in the same room.\nRoommates who love each other breed more often.",
    "trait_top_priority":    "Points for each ability/mutation you rated 'Top Priority'.\nDivided by how many scope cats share the trait; sole owners get 2x.",
    "trait_desirable":       "Points for each ability/mutation you rated 'Desirable'.\nDivided by scope count; sole owners get 2x.",
    "trait_undesirable":     "Flat penalty for each ability/mutation you rated 'Undesirable'.\nNot divided — every holder gets the same penalty.",
}

# ── Weight editor UI rows ────────────────────────────────────────────────────

WEIGHT_UI_ROWS: list[tuple[str | None, str | tuple | None]] = [
    ("stat_sum",         "Stat Sum"),
    (None, None),
    ("age_penalty",      "Age penalty"),
    ("age_threshold",    "  \u2514 threshold"),
    (None, None),
    ("stat_7",           "7rare"),
    ("stat_7_threshold", "  \u2514 threshold"),
    ("stat_7_count",     "7-count"),
    (None, None),
    ("seven_sub",          "7-Sub score"),
    ("seven_sub_threshold","  \u2514 threshold"),
    (None, None),
    ("cha_low",            "CHA \u2264 4 penalty"),
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
    ("gene_risk_threshold",     "  \u2514 threshold (%)"),
    ("gene_risk_penalty_scale", "  \u2514 penalty scale"),
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

# ── Score table columns ──────────────────────────────────────────────────────

SCORE_COLUMNS: list[tuple[str, list[str]]] = [
    ("Sum",   ["stat_sum"]),
    ("Age",   ["age_penalty"]),
    ("7rare", ["stat_7"]),
    ("7cnt",  ["stat_7_count"]),
    ("7sub",  ["seven_sub"]),
    ("CHA",   ["cha_low"]),
    ("Sex",   ["gay_pref", "bi_pref"]),
    ("Lib",   ["high_libido", "low_libido"]),
    ("Gender", ["unknown_gender"]),
    ("Gene",  ["no_children", "zero_risk_bonus"]),
    ("Aggro", ["low_aggression", "high_aggression"]),
    ("\U0001f4a5\U0001f52d",    ["rivalry"]),
    ("\U0001f4a5\U0001f3e0",    ["rivalry_room"]),
    ("\U0001f497\U0001f52d",    ["love_interest"]),
    ("\U0001f497\U0001f3e0",    ["love_interest_room"]),
    ("Trait", ["trait_top_priority", "trait_desirable", "trait_undesirable"]),
]

# ── Tier classification ──────────────────────────────────────────────────────

BREED_PRIORITY_TIERS: list[tuple[float | None, str, str]] = [
    (10,   "Keep",     "#f0c060"),
    ( 4,   "Good",     "#1ec8a0"),
    ( 0,   "Neutral",  "#777777"),
    (-5,   "Consider", "#e08030"),
    (None, "Cull",     "#e04040"),
]

# ── Trait rating values ──────────────────────────────────────────────────────

TRAIT_RATING_OPTIONS: list[tuple[str, int | None]] = [
    ("Top Priority", 2),
    ("Desirable", 1),
    ("Neutral", 0),
    ("Undecided", None),
    ("Undesirable", -1),
]


# ── Helpers ──────────────────────────────────────────────────────────────────

class ScoreResult:
    __slots__ = ("total", "tier", "tier_color", "breakdown", "subtotals",
                 "scope_gene_risk")

    def __init__(self, total: float, tier: str, tier_color: str,
                 breakdown: list[tuple[str, float]],
                 subtotals: dict[str, float] | None = None,
                 scope_gene_risk: float | None = None):
        self.total = total
        self.tier = tier
        self.tier_color = tier_color
        self.breakdown = breakdown
        self.subtotals = subtotals or {}
        self.scope_gene_risk = scope_gene_risk


def priority_tier(score: float) -> tuple[str, str]:
    for threshold, label, color in BREED_PRIORITY_TIERS:
        if threshold is None or score >= threshold:
            return label, color
    return "Cull", "#e04040"


def is_basic_trait(name: str) -> bool:
    return name.lower().startswith("basic")


def ability_base(name: str) -> str:
    if len(name) > 1 and name[-1] == "2":
        return name[:-1]
    return name


def is_upgraded(name: str) -> bool:
    """Return True if the ability name indicates a tier-2 upgrade (trailing '2')."""
    return len(name) > 1 and name[-1] == "2"


# ── Main scoring function ────────────────────────────────────────────────────

def precompute_scope_data(
    scope_cats: list,
    stat_names: list[str],
    use_current_stats: bool = False,
    add_mutation_stats: bool = False,
) -> dict:
    """Pre-compute shared scope data once for all scoring calls.

    Returns a dict with keys: scope_stats, scope_set, scope_base_traits,
    stat_7_counts, trait_counts.
    """
    scope_stats = {
        id(c): get_cat_stats(c, use_current_stats, add_mutation_stats)
        for c in scope_cats
    }
    scope_set = {id(c) for c in scope_cats}

    # Per-stat count of scope cats with that stat at 7
    stat_7_counts: dict[str, int] = {}
    for sn in stat_names:
        stat_7_counts[sn] = sum(
            1 for cid, st in scope_stats.items() if st.get(sn) == 7
        )

    # Trait sets per scope cat + aggregate counts
    scope_base_traits: dict[int, set[str]] = {
        id(c): (
            {ability_base(a) for a in
             list(c.abilities) + list(c.passive_abilities) + list(getattr(c, 'disorders', []))}
            | set(c.mutations)
            | set(getattr(c, 'defects', []))
        )
        for c in scope_cats
    }
    trait_counts: dict[str, int] = {}
    for traits in scope_base_traits.values():
        for t in traits:
            trait_counts[t] = trait_counts.get(t, 0) + 1

    return {
        "scope_stats": scope_stats,
        "scope_set": scope_set,
        "scope_base_traits": scope_base_traits,
        "stat_7_counts": stat_7_counts,
        "trait_counts": trait_counts,
    }


def compute_breed_priority_score(
    cat,
    scope_cats: list,
    ma_ratings: dict[str, int | None],
    stat_names: list[str],
    weights: dict[str, float] | None = None,
    mutation_display_name=None,
    scope_stat_sums: list[float] | None = None,
    hated_by: list | None = None,
    gene_risk_lookup=None,
    gene_risk_cache: dict | None = None,
    use_current_stats: bool = False,
    add_mutation_stats: bool = False,
    can_breed_fn=None,
    _precomputed: dict | None = None,
    should_cancel=None,
    stat_sum_mode: str = "percentile",
) -> ScoreResult:
    """Compute breed priority score for an individual cat.

    Args:
        cat: The cat to score.
        scope_cats: All cats in the selected scope.
        ma_ratings: {trait_key: rating} where 2=Top, 1=Desirable, 0=Neutral,
            None=Undecided, -1=Undesirable.
        stat_names: Ordered stat keys, e.g. ["STR", "DEX", ...].
        weights: Override weight dict. Defaults to BREED_PRIORITY_WEIGHTS.
        mutation_display_name: Callable(str) -> str for display labels.
        scope_stat_sums: Pre-sorted list of stat sums for percentile calc.
        hated_by: Cats in scope that hate this cat (reverse lookup).
        gene_risk_lookup: Callable(cat, cat) -> float. Defaults to risk_percent.
        gene_risk_cache: Mutable dict for caching pair risk values.
        use_current_stats: Use total_stats instead of base_stats.
        add_mutation_stats: Add mutation stat bonuses on top.
        can_breed_fn: Callable(cat, cat) -> (bool, list). Defaults to can_breed.
        _precomputed: Output of precompute_scope_data() for O(1) lookups.
        stat_sum_mode: "percentile" (default, tiered 90/75/50 cutoffs) or
            "rank" (linear rank among unique scope sums, used by Detailed Scoring).
    """
    _w = weights if weights is not None else BREED_PRIORITY_WEIGHTS
    _display = mutation_display_name or (lambda n: n)
    _can_breed = can_breed_fn or can_breed
    _cat_stats = get_cat_stats(cat, use_current_stats, add_mutation_stats)

    # Use pre-computed data when available; fall back to per-call computation.
    if _precomputed is not None:
        _scope_stats = _precomputed["scope_stats"]
        scope_set = _precomputed["scope_set"]
        _scope_base_traits = _precomputed["scope_base_traits"]
        _stat_7_counts = _precomputed["stat_7_counts"]
        _trait_counts = _precomputed["trait_counts"]
    else:
        _scope_stats = {id(c): get_cat_stats(c, use_current_stats, add_mutation_stats)
                        for c in scope_cats}
        scope_set = {id(c) for c in scope_cats}
        _scope_base_traits = {
            id(c): (
                {ability_base(a) for a in
                 list(c.abilities) + list(c.passive_abilities) + list(getattr(c, 'disorders', []))}
                | set(c.mutations)
                | set(getattr(c, 'defects', []))
            )
            for c in scope_cats
        }
        _stat_7_counts = {
            sn: sum(1 for cid, st in _scope_stats.items() if st.get(sn) == 7)
            for sn in stat_names
        }
        _trait_counts = {}
        for traits in _scope_base_traits.values():
            for t in traits:
                _trait_counts[t] = _trait_counts.get(t, 0) + 1

    breakdown: list[tuple[str, float]] = []
    subtotals: dict[str, float] = {}
    for key in BREED_PRIORITY_WEIGHTS:
        subtotals[key] = 0.0
    _cat_in_scope = id(cat) in scope_set

    # ── Unknown gender bonus ──────────────────────────────────────────────
    if cat.gender == "?":
        breakdown.append(("Unknown gender (?)", _w["unknown_gender"]))
        subtotals["unknown_gender"] = _w["unknown_gender"]

    # ── CHA penalty ───────────────────────────────────────────────────────
    w_cha = _w.get("cha_low", 0.0)
    if w_cha != 0.0:
        _cha = _cat_stats.get("CHA")
        if _cha == 4:
            breakdown.append(("CHA = 4", round(w_cha, 3)))
            subtotals["cha_low"] = round(w_cha, 3)
        elif _cha is not None and _cha <= 3:
            _cha_pts = round(w_cha * 2, 3)
            breakdown.append((f"CHA = {_cha} (2\u00d7)", _cha_pts))
            subtotals["cha_low"] = _cha_pts

    # ── Aggression / libido / sexuality ───────────────────────────────────
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

    # ── Stat 7 rarity ─────────────────────────────────────────────────────
    _TARGET_N = int(round(_w.get("stat_7_threshold", 7.0)))
    _STAT7_BASE = _w["stat_7"]
    for stat_name in stat_names:
        if _cat_stats.get(stat_name) == 7:
            n_scope = _stat_7_counts.get(stat_name, 0)
            n = n_scope if _cat_in_scope else n_scope + 1
            if n == 1:
                w = _w["stat_7"] * 2
                label = f"7 in {stat_name} (sole \u2605\u2605)"
            elif n <= _TARGET_N:
                w = _w["stat_7"]
                label = f"7 in {stat_name} ({n} in scope)"
            else:
                w = round(_STAT7_BASE * _TARGET_N / n, 3)
                label = f"7 in {stat_name} ({n} in scope, \u00f7{n / _TARGET_N:.1f})"
            breakdown.append((label, float(w)))
            subtotals["stat_7"] += float(w)

    # ── Stat-count bonus ──────────────────────────────────────────────────
    # Flat bonus per stat at or above `stat_count_threshold` (default 7 = count
    # of max stats). Threshold is configurable; kept as the `stat_count_threshold`
    # weight key for compatibility with Detailed Scoring's weight editor.
    _w_7ct = _w.get("stat_7_count", 0.0)
    if _w_7ct != 0.0:
        _stat_cnt_thr = int(round(_w.get("stat_count_threshold", 7.0)))
        _n_above_thr = sum(1 for sn in stat_names if _cat_stats.get(sn, 0) >= _stat_cnt_thr)
        if _n_above_thr > 0:
            _7ct_pts = round(_w_7ct * _n_above_thr, 3)
            _plural = "s" if _n_above_thr != 1 else ""
            _label = (f"{_n_above_thr} stat{_plural} at 7"
                      if _stat_cnt_thr == 7
                      else f"{_n_above_thr} stat{_plural} at ≥{_stat_cnt_thr}")
            breakdown.append((_label, _7ct_pts))
            subtotals["stat_7_count"] = _7ct_pts

    # ── Trait scoring ─────────────────────────────────────────────────────
    _w_top = _w.get("trait_top_priority", 0.0)
    _w_des = _w.get("trait_desirable", 0.0)
    _w_und = _w.get("trait_undesirable", 0.0)

    def _score_trait(label: str, rating: int | None, n: int):
        if rating is None or rating == 0:
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
            tag = f"Top Priority (\u00f7{n})"
        elif rating == 1:
            pts = round(_w_des / n, 3)
            tag = f"Desirable (\u00f7{n})"
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

    all_ability_bases = list({
        ability_base(m) for m in
        list(cat.abilities) + list(cat.passive_abilities) + list(getattr(cat, 'disorders', []))
        if not is_basic_trait(m)
    })
    for ma in all_ability_bases:
        rating = ma_ratings.get(ma)
        n_scope = _trait_counts.get(ma, 0)
        n = max(1, n_scope if _cat_in_scope else n_scope + 1)
        _score_trait(_display(ma), rating, n)

    for ma in cat.mutations:
        if is_basic_trait(ma):
            continue
        rating = ma_ratings.get(ma)
        n_scope = _trait_counts.get(ma, 0)
        n = max(1, n_scope if _cat_in_scope else n_scope + 1)
        _score_trait(ma, rating, n)

    for ma in getattr(cat, 'defects', []):
        if is_basic_trait(ma):
            continue
        rating = ma_ratings.get(ma)
        n_scope = _trait_counts.get(ma, 0)
        n = max(1, n_scope if _cat_in_scope else n_scope + 1)
        _score_trait(ma, rating, n)

    # ── High aggression / low libido ──────────────────────────────────────
    if cat.aggression is not None and cat.aggression >= TRAIT_HIGH_THRESHOLD:
        breakdown.append(("High aggression", _w["high_aggression"]))
        subtotals["high_aggression"] = _w["high_aggression"]

    if cat.libido is not None and cat.libido < TRAIT_LOW_THRESHOLD:
        breakdown.append(("Low libido", _w["low_libido"]))
        subtotals["low_libido"] = _w["low_libido"]

    # ── Genetic safety ────────────────────────────────────────────────────
    risk_fn = gene_risk_lookup if callable(gene_risk_lookup) else risk_percent
    _risk_vals: list[float] = []
    for partner in scope_cats:
        if callable(should_cancel) and should_cancel():
            return None
        if partner is cat:
            continue
        if not _can_breed(cat, partner)[0]:
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

    gene_risk: float | None = (sum(_risk_vals) / len(_risk_vals)) if _risk_vals else None
    if gene_risk is not None:
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
                breakdown.append((f"Genetic safety (R{int(_gene_risk_display)} \u2264 {_gene_threshold:.0f})", safe_pts))
                subtotals["zero_risk_bonus"] = safe_pts

    # ── Stat sum scoring ──────────────────────────────────────────────────
    # Two modes:
    #   "percentile" — tiered (top 10%/25%/50% → w / w-1 / w-2, else 0)
    #   "rank"       — linear scaling over unique scope sums (0..w). Prevents
    #                   outliers from compressing the rest of the gradient.
    w_sum = _w.get("stat_sum", 0.0)
    if w_sum != 0 and scope_stat_sums:
        cat_sum = sum(_cat_stats.values())
        if stat_sum_mode == "rank":
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
                breakdown.append(
                    (f"Stat sum {cat_sum} (rank {_sum_rank_idx + 1}/{_n_unique})", pts)
                )
                subtotals["stat_sum"] = pts
        else:  # "percentile" (default)
            n_sums = len(scope_stat_sums)
            rank = sum(1 for v in scope_stat_sums if v <= cat_sum)
            pct = rank / n_sums * 100
            if pct >= 90:
                pts = w_sum
            elif pct >= 75:
                pts = max(0.0, w_sum - 1)
            elif pct >= 50:
                pts = max(0.0, w_sum - 2)
            else:
                pts = 0.0
            if pts:
                breakdown.append((f"Stat sum {cat_sum} ({pct:.0f}th percentile)", pts))
                subtotals["stat_sum"] = pts

    # ── Age penalty ───────────────────────────────────────────────────────
    w_age = _w.get("age_penalty", 0.0)
    if w_age != 0.0:
        age = getattr(cat, 'age', None)
        if age is not None:
            _age_thr = int(round(_w.get("age_threshold", 10.0)))
            if age > _age_thr:
                _over = age - _age_thr
                _mult = 1 + (_over - 1) // 3
                pts = round(_mult * w_age, 2)
                breakdown.append((f"Age {age} (+{_over} over threshold, {_mult}\u00d7)", pts))
                subtotals["age_penalty"] = pts

    # ── Love interest ─────────────────────────────────────────────────────
    w_love = _w.get("love_interest", 0.0)
    if w_love != 0.0:
        for lover in getattr(cat, 'lovers', []):
            if id(lover) in scope_set:
                pts = round(w_love, 2)
                breakdown.append((f"Loves {lover.name} (in scope)", pts))
                subtotals["love_interest"] = pts
                break

    # ── Rivalry ────────────────────────────────────────────────────────────
    w_rival = _w.get("rivalry", 0.0)
    if w_rival != 0.0:
        _rival_total = 0.0
        for hater in getattr(cat, 'haters', []):
            if id(hater) in scope_set:
                pts = round(w_rival, 2)
                breakdown.append((f"Hates {hater.name} (in scope)", pts))
                _rival_total += pts
        for hater in (hated_by or []):
            if id(hater) in scope_set and hater not in getattr(cat, 'haters', []):
                pts = round(w_rival, 2)
                breakdown.append((f"Hated by {hater.name} (in scope)", pts))
                _rival_total += pts
        if _rival_total:
            subtotals["rivalry"] = _rival_total

    # ── Love interest (room) ──────────────────────────────────────────────
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

    # ── Rivalry (room) ────────────────────────────────────────────────────
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
