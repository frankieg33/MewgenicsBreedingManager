"""Pure evaluation logic for ComplexWeight conditions. No Qt dependencies."""

from __future__ import annotations

from ..scoring import TRAIT_LOW_THRESHOLD, TRAIT_HIGH_THRESHOLD, ability_base
from .model import (
    ComplexWeight, Condition,
    FIELD_GENDER, FIELD_LIBIDO, FIELD_AGGRESSION, FIELD_SEXUALITY,
    FIELD_STAT_SUM, FIELD_AGE, FIELD_GENE_RISK, FIELD_GENE_UNIQUE,
    FIELD_SCORE, FIELD_TRAIT, FIELD_STAT_PREFIX,
    OP_EQ, OP_NEQ, OP_GT, OP_LT, OP_GTE, OP_LTE,
    TRAIT_MODE_ANY, TRAIT_MODE_ALL, TRAIT_MODE_NONE,
    LOGIC_AND, LOGIC_OR,
)


_NUMERIC_FNS = {
    OP_EQ:  lambda a, b: a == b,
    OP_NEQ: lambda a, b: a != b,
    OP_GT:  lambda a, b: a > b,
    OP_LT:  lambda a, b: a < b,
    OP_GTE: lambda a, b: a >= b,
    OP_LTE: lambda a, b: a <= b,
}


def build_cat_trait_set(cat) -> frozenset:
    """Return frozenset of all trait keys for a cat (ability bases + mutations)."""
    return frozenset(
        {ability_base(a)
         for a in (list(cat.abilities)
                   + list(cat.passive_abilities)
                   + list(getattr(cat, 'disorders', [])))}
        | set(cat.mutations)
        | set(getattr(cat, 'defects', []))
    )


def _normalize_gender_value(gender_value: object) -> str:
    """Return compact gender token used by complex-weight conditions."""
    normalized_gender = str(gender_value or "?").strip().lower()
    if normalized_gender in {"m", "male"}:
        return "m"
    if normalized_gender in {"f", "female"}:
        return "f"
    return "?"


def _eval_condition(
    cond: Condition,
    cat,
    cat_stats: dict,
    cat_traits: frozenset,
    scope_gene_risk: float | None,
    total_score: float,
) -> bool:
    f   = cond.field
    op  = cond.operator
    val = cond.value

    # ── Categorical ───────────────────────────────────────────────────────────
    if f == FIELD_GENDER:
        actual_gender = _normalize_gender_value(getattr(cat, "gender", "?"))
        expected_gender = _normalize_gender_value(val)
        return (actual_gender == expected_gender) if op == OP_EQ else (actual_gender != expected_gender)

    if f == FIELD_LIBIDO:
        lb = cat.libido
        if lb is None:
            actual = "normal"
        elif lb >= TRAIT_HIGH_THRESHOLD:
            actual = "high"
        elif lb < TRAIT_LOW_THRESHOLD:
            actual = "low"
        else:
            actual = "normal"
        return (actual == val) if op == OP_EQ else (actual != val)

    if f == FIELD_AGGRESSION:
        ag = cat.aggression
        if ag is None:
            actual = "normal"
        elif ag >= TRAIT_HIGH_THRESHOLD:
            actual = "high"
        elif ag < TRAIT_LOW_THRESHOLD:
            actual = "low"
        else:
            actual = "normal"
        return (actual == val) if op == OP_EQ else (actual != val)

    if f == FIELD_SEXUALITY:
        actual = (getattr(cat, 'sexuality', None) or 'straight').lower()
        return (actual == val) if op == OP_EQ else (actual != val)

    # ── Boolean ───────────────────────────────────────────────────────────────
    if f == FIELD_GENE_UNIQUE:
        is_unique = (scope_gene_risk is not None and scope_gene_risk == 0.0)
        return is_unique if val else not is_unique

    # ── Trait multi-select ────────────────────────────────────────────────────
    if f == FIELD_TRAIT:
        check = frozenset(val) if isinstance(val, (list, tuple)) else frozenset()
        if op == TRAIT_MODE_ANY:
            return bool(cat_traits & check)
        if op == TRAIT_MODE_ALL:
            return check.issubset(cat_traits)
        if op == TRAIT_MODE_NONE:
            return not bool(cat_traits & check)
        return False

    # ── Numeric ───────────────────────────────────────────────────────────────
    fn = _NUMERIC_FNS.get(op)
    if fn is None:
        return False

    if f == FIELD_STAT_SUM:
        return fn(sum(cat_stats.values()), float(val))

    if f == FIELD_AGE:
        age = getattr(cat, 'age', None)
        return age is not None and fn(float(age), float(val))

    if f == FIELD_GENE_RISK:
        return scope_gene_risk is not None and fn(scope_gene_risk, float(val))

    if f == FIELD_SCORE:
        return fn(total_score, float(val))

    if f.startswith(FIELD_STAT_PREFIX):
        sn = f[len(FIELD_STAT_PREFIX):]
        return fn(float(cat_stats.get(sn, 0)), float(val))

    return False


def evaluate_cw(
    cw: ComplexWeight,
    cat,
    cat_stats: dict,
    cat_traits: frozenset,
    scope_gene_risk: float | None,
    total_score: float,
) -> bool:
    """Return True if cat satisfies the complex weight's conditions.

    FIELD_SCORE evaluates against *total_score* (the pre-CW base score),
    so CW deltas do not feed back recursively into other CW conditions.
    An empty conditions list never matches.
    """
    if not cw.conditions:
        return False
    results = [
        _eval_condition(c, cat, cat_stats, cat_traits, scope_gene_risk, total_score)
        for c in cw.conditions
    ]
    return all(results) if cw.logic == LOGIC_AND else any(results)


def compute_cw_matches(
    enabled_cws: list,
    cat,
    cat_stats: dict,
    cat_traits: frozenset,
    scope_gene_risk: float | None,
    total_score: float,
) -> list:
    """Return list of (matched: bool, delta: float) for each enabled CW."""
    return [
        (
            evaluate_cw(cw, cat, cat_stats, cat_traits, scope_gene_risk, total_score),
            cw.delta,
        )
        for cw in enabled_cws
    ]
