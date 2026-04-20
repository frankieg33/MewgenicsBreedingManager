"""Breed Priority — recompute helper functions.

Pure computation helpers extracted from BreedPriorityView.recompute().
No Qt dependencies — these functions operate on Cat objects and dicts.
"""

from .columns import _STAT_COL_NAMES
from .scoring import SCORE_COLUMNS, compute_breed_priority_score
from .stats_overview import get_cat_stats


def build_relationship_maps(cats):
    """Build reverse hated-by and loved-by maps from all in-house cats.

    Args:
        cats: Full cat list (all cats, not just alive/filtered).

    Returns:
        (hated_by_map, loved_by_map) — both are dict[int, list] keyed by id(target_cat).
    """
    all_in_house = [c for c in cats if c.status == "In House"]
    hated_by_map: dict[int, list] = {}
    for c in all_in_house:
        for h in getattr(c, 'haters', []):
            hated_by_map.setdefault(id(h), []).append(c)
    loved_by_map: dict[int, list] = {}
    for c in all_in_house:
        for lv in getattr(c, 'lovers', []):
            loved_by_map.setdefault(id(lv), []).append(c)
    return hated_by_map, loved_by_map


def compute_seven_sets(alive, scope_set, use_current_stats: bool = False,
                       add_mutation_stats: bool = False):
    """Pre-compute 7-stat sets for subset dominance detection.

    Args:
        alive: List of cats to compute for.
        scope_set: Set of id(cat) for cats in scope.
        use_current_stats: If True, use total_stats instead of base_stats.
        add_mutation_stats: If True, add parsed mutation stat bonuses.

    Returns:
        (seven_sets, scope_7_sets) — both are dict[int, frozenset].
    """
    seven_sets: dict[int, frozenset] = {
        id(c): frozenset(sn for sn in _STAT_COL_NAMES if get_cat_stats(c, use_current_stats, add_mutation_stats).get(sn) == 7)
        for c in alive
    }
    scope_7_sets: dict[int, frozenset] = {
        cid: s for cid, s in seven_sets.items()
        if cid in scope_set
    }
    return seven_sets, scope_7_sets


def compute_all_scores(
    alive, scope_cats, scope_set,
    seven_sets, scope_7_sets, hated_by_map,
    ma_ratings, stat_names, weights, display_name_fn,
    gene_risk_lookup=None,
    use_current_stats: bool = False,
    add_mutation_stats: bool = False,
):
    """Run Pass 1: compute ScoreResults + 7-sub contributions for all cats.

    Returns:
        (results, cat_sub_counts, all_scores_sorted,
         all_scope_gene_risks, all_scope_children, max_7_count,
         scope_stat_sums, pair_risk_cache)
    """
    scope_stat_sums = sorted(sum(get_cat_stats(c, use_current_stats, add_mutation_stats).values()) for c in scope_cats)
    pair_risk_cache: dict[tuple[int, int], float] = {}

    results: dict[int, object] = {}
    cat_sub_counts: dict[int, int] = {}
    for cat in alive:
        results[id(cat)] = compute_breed_priority_score(
            cat, scope_cats, ma_ratings,
            stat_names=stat_names,
            weights=weights,
            mutation_display_name=display_name_fn,
            scope_stat_sums=scope_stat_sums,
            hated_by=hated_by_map.get(id(cat), []),
            gene_risk_lookup=gene_risk_lookup,
            gene_risk_cache=pair_risk_cache,
            use_current_stats=use_current_stats,
            add_mutation_stats=add_mutation_stats,
        )
        my_sevens = seven_sets.get(id(cat), frozenset())
        sub_cnt = sum(
            1 for oc, os in scope_7_sets.items()
            if oc != id(cat) and my_sevens < os
        ) if my_sevens else 0
        cat_sub_counts[id(cat)] = sub_cnt
        sub_w   = weights.get("seven_sub", 0.0)
        sub_thr = max(1, int(round(weights.get("seven_sub_threshold", 1.0))))
        sub_pts = sub_w * min(sub_cnt / sub_thr, 1.0) if sub_cnt > 0 else 0.0
        results[id(cat)].subtotals["seven_sub"] = sub_pts
        results[id(cat)].total += sub_pts
        if sub_pts != 0:
            results[id(cat)].breakdown.append(("7sub", sub_pts))

    all_scores_sorted = sorted(results[id(c)].total for c in alive)

    all_scope_rel_counts = sorted(
        results[id(c)].scope_gene_risk
        for c in scope_cats
        if id(c) in results and results[id(c)].scope_gene_risk is not None
    )

    all_scope_children = sorted(
        sum(1 for ch in c.children if id(ch) in scope_set)
        for c in scope_cats
    )

    _stat_cnt_thr = int(round(weights.get("stat_count_threshold", 7.0)))
    max_7_count = max(
        (sum(1 for v in get_cat_stats(c, use_current_stats, add_mutation_stats).values() if v >= _stat_cnt_thr) for c in alive),
        default=0,
    )

    return (results, cat_sub_counts, all_scores_sorted,
            all_scope_rel_counts, all_scope_children, max_7_count,
            scope_stat_sums, pair_risk_cache)


def compute_heatmap_norms(results, alive, is_heat, heat_algo):
    """Pre-compute heatmap normalisation data.

    Args:
        results: Dict of id(cat) -> ScoreResult.
        alive: List of visible cats.
        is_heat: Whether heatmap is enabled.
        heat_algo: "column" or "row".

    Returns:
        (col_max_abs, row_max_abs, score_max_abs)
    """
    col_max_abs: dict[int, float] = {}
    row_max_abs: dict[int, float] = {}
    score_max_abs: float = 1.0
    if not is_heat:
        return col_max_abs, row_max_abs, score_max_abs

    for ci, (_, keys) in enumerate(SCORE_COLUMNS):
        mx = max((abs(sum(results[id(c)].subtotals.get(k, 0.0) for k in keys))
                  for c in alive), default=0.0)
        col_max_abs[ci] = mx if mx > 0 else 1.0
    smx = max((abs(results[id(c)].total) for c in alive), default=0.0)
    score_max_abs = smx if smx > 0 else 1.0
    if heat_algo == "row":
        for c in alive:
            r = results[id(c)]
            mx = max((abs(sum(r.subtotals.get(k, 0.0) for k in keys))
                      for _, keys in SCORE_COLUMNS), default=0.0)
            row_max_abs[id(c)] = mx if mx > 0 else 1.0

    return col_max_abs, row_max_abs, score_max_abs
