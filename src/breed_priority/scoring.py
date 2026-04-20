"""Breed Priority — scoring config for the Detailed Scoring view.

This module is a thin shim over ``mewgenics.scoring.engine``: the scoring
function, ScoreResult, tier logic, and trait helpers all live in the canonical
engine. What remains here is UI-layer configuration specific to Detailed
Scoring's table + weight-editor (weight defaults, column layout, rating labels).

Simple Scoring (``mewgenics.views.manual_scoring``) uses its own point-based
scorer and does not depend on this module.
"""

from __future__ import annotations

from mewgenics.scoring.engine import (  # noqa: F401
    ScoreResult,
    priority_tier,
    ability_base,
    is_basic_trait,
    is_upgraded,
    TRAIT_LOW_THRESHOLD,
    TRAIT_HIGH_THRESHOLD,
    GENETIC_SAFE_RISK_FLOOR,
    compute_breed_priority_score as _engine_compute_breed_priority_score,
)


# ── Scoring weights ───────────────────────────────────────────────────────────
# Detailed Scoring exposes an explicit ``stat_count_threshold`` knob so users
# can count stats at ≥N rather than exactly 7. The shared engine honors this
# key when present.

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
    ("\U0001f4a5",     ["rivalry", "rivalry_room"]),
    ("\U0001f497",     ["love_interest", "love_interest_room"]),
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

# Trait rating options — UI uses verbose labels explaining the per-rating math.
TRAIT_RATING_OPTIONS = [
    ("Top Priority - sole owner +2x, shared +1x÷n", 2),
    ("Desirable - sole owner +4, shared +2÷n",     1),
    ("Neutral - reviewed, not scored",                   0),
    ("Undecided - not yet reviewed",                     None),
    ("Undesirable - scored −2",                    -1),
]
TRAIT_RATING_LABELS = [label for label, _ in TRAIT_RATING_OPTIONS]
TRAIT_RATING_VALUES = [val   for _, val  in TRAIT_RATING_OPTIONS]
RATING_SHORT_LABELS = ["Top Priority", "Desirable", "Neutral", "Undecided", "Undesirable"]


def compute_breed_priority_score(cat, scope_cats: list, ma_ratings: dict,
                         stat_names: list, weights: dict = None,
                         mutation_display_name=None,
                         scope_stat_sums: list = None,
                         hated_by: list = None,
                         gene_risk_lookup=None,
                         gene_risk_cache: dict | None = None,
                         use_current_stats: bool = False,
                         add_mutation_stats: bool = False) -> ScoreResult:
    """Compute Detailed Scoring's breed-priority score.

    Thin wrapper over ``mewgenics.scoring.engine.compute_breed_priority_score``
    that pins the Detailed-Scoring-specific behavior:
      * default weights = ``BREED_PRIORITY_WEIGHTS`` above (with stat_count_threshold)
      * stat_sum uses rank-based scaling (linear over unique scope sums)
    """
    return _engine_compute_breed_priority_score(
        cat, scope_cats, ma_ratings,
        stat_names=stat_names,
        weights=weights if weights is not None else BREED_PRIORITY_WEIGHTS,
        mutation_display_name=mutation_display_name,
        scope_stat_sums=scope_stat_sums,
        hated_by=hated_by,
        gene_risk_lookup=gene_risk_lookup,
        gene_risk_cache=gene_risk_cache,
        use_current_stats=use_current_stats,
        add_mutation_stats=add_mutation_stats,
        stat_sum_mode="rank",
    )
