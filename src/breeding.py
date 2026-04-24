"""Shared breeding compatibility and scoring helpers."""

from __future__ import annotations

import math
import logging
from dataclasses import dataclass
from typing import Optional, Sequence

from save_parser import (
    Cat,
    STAT_NAMES,
    can_breed,
    risk_percent,
    shared_ancestor_counts,
    _stimulation_inheritance_weight,
)

logger = logging.getLogger("mewgenics.breeding")


@dataclass(slots=True)
class PairProjection:
    """Expected offspring stat projection for a breeding pair."""

    expected_stats: dict[str, float]
    stat_ranges: dict[str, tuple[int, int]]
    locked_stats: tuple[str, ...]
    reachable_stats: tuple[str, ...]
    missing_stats: tuple[str, ...]
    sum_range: tuple[int, int]
    avg_expected: float
    seven_plus_total: float
    distance_total: float

    def __getitem__(self, key: str):
        return getattr(self, key)

    def get(self, key: str, default=None):
        return getattr(self, key, default)


@dataclass(slots=True)
class PairFactors:
    """Complete score breakdown for a breeding pair."""

    cat_a: Cat
    cat_b: Cat
    compatible: bool
    reason: str
    risk: float
    projection: PairProjection
    complementarity_bonus: float
    variance_penalty: float
    personality_bonus: float
    trait_bonus: float
    must_breed_bonus: float
    lover_bonus: float
    quality: float
    stat_priority_bonus: float = 0.0
    game_compat: float = 0.0


def pair_key(a: Cat, b: Cat) -> tuple[int, int]:
    """Normalized pair key — smaller db_key first."""
    ak, bk = a.db_key, b.db_key
    return (ak, bk) if ak < bk else (bk, ak)


def planner_pair_bias(a: Cat, b: Cat) -> float:
    """
    Heuristic bias for planner suggestions.

    Prefer sexuality-compatible pairs, with a soft bias toward opposite-sex
    pairs or a cat with unknown/ditto-like gender.
    """
    if planner_pair_allows_breeding(a, b):
        return 10.0
    return -30.0


def planner_pair_allows_breeding(a: Cat, b: Cat) -> bool:
    """Hard planner rule that follows the game's breeding compatibility."""
    return can_breed(a, b)[0]


def _sexuality_coeff(cat: Cat) -> float:
    """Return the cat's 0..1 sexuality coefficient (0=straight, 1=gay).

    Prefer the raw float parsed from the save; fall back to the discretized
    label if it's missing (older saves or override paths).
    """
    raw = getattr(cat, "sexuality_raw", None)
    if isinstance(raw, (int, float)):
        return max(0.0, min(1.0, float(raw)))
    label = (getattr(cat, "sexuality", None) or "straight").lower()
    if label == "gay":
        return 1.0
    if label == "bi":
        return 0.5
    return 0.05


def estimate_breeding_compatibility(initiator: Cat, partner: Cat) -> float:
    """Estimate the game's breeding compatibility score for a one-way pairing.

    Implements the wiki formula:
      compat = 0.15 * initiator_CHA * partner_libido * lover_mult * sexuality_mult

    where sexuality_mult uses the partner's sexuality_coeff with
    cos(0.5π·coeff) for opposite-sex pairs and sin(0.5π·coeff) for same-sex
    pairs.  Unknown-gender pairs take sexuality_mult = 1.

    Returns the raw compatibility (roll probability per the wiki, before the
    sqrt(1+0.1·comfort) comfort multiplier).  Values below ~0.05 correspond
    to pairs the game rejects outright.
    """
    if initiator is partner:
        return 0.0
    total_stats = getattr(initiator, "total_stats", None)
    if total_stats is None:
        # Likely a test stub or otherwise unpopulated Cat — we can't compute
        # compatibility, so signal "unknown" by returning a high value rather
        # than silently filtering the pair out.
        return 1.0
    cha = float(total_stats.get("CHA", 0) or 0)
    libido_raw = getattr(partner, "libido", None)
    libido = float(libido_raw) if libido_raw is not None else 1.0
    if cha <= 0 or libido <= 0:
        return 0.0

    # Lover multiplier: partner-anchored with a default coeff of 0.25.
    lover_coeff = 0.25
    partner_lovers = getattr(partner, "lovers", []) or []
    if partner_lovers:
        if any(lv is initiator for lv in partner_lovers):
            lover_mult = 1.0 + lover_coeff
        else:
            lover_mult = 1.0 - lover_coeff
    else:
        lover_mult = 1.0

    ga = (initiator.gender or "?").strip().lower()
    gb = (partner.gender or "?").strip().lower()
    if ga == "?" or gb == "?":
        sex_mult = 1.0
    else:
        coeff = _sexuality_coeff(partner)
        if ga == gb:
            sex_mult = math.sin(0.5 * math.pi * coeff)
        else:
            sex_mult = math.cos(0.5 * math.pi * coeff)

    return 0.15 * cha * libido * lover_mult * sex_mult


def pair_breeding_compatibility(a: Cat, b: Cat) -> float:
    """Symmetric compatibility estimate for a pair.

    Returns the *worst-direction* compat so the planner filter matches
    "this pair reliably produces kittens regardless of who initiates".
    The game picks a random initiator each day and same-sex pairs see
    wildly asymmetric sex_mult values when the two cats have different
    sexuality coefficients — using max would let those pairs slip past
    a floor even though half of initiations would fail.
    """
    return min(
        estimate_breeding_compatibility(a, b),
        estimate_breeding_compatibility(b, a),
    )


def planner_inbreeding_penalty(a: Cat, b: Cat) -> float:
    """
    Conservative penalty for pairs that share ancestry.

    The perfect planner should strongly prefer unrelated pairs so repeated use
    does not quietly drift into low-grade inbreeding.
    """
    shared_total, shared_recent = shared_ancestor_counts(a, b, recent_depth=3, max_depth=8)
    return shared_total * 6.0 + shared_recent * 4.0


def is_hater_conflict(a: Cat, b: Cat, hater_key_map: dict[int, set[int]]) -> bool:
    """Check if either cat hates the other."""
    return b.db_key in hater_key_map.get(a.db_key, set()) or a.db_key in hater_key_map.get(b.db_key, set())


def is_mutual_lover_pair(a: Cat, b: Cat, lover_key_map: dict[int, set[int]]) -> bool:
    """Check if both cats are mutual lovers."""
    return b.db_key in lover_key_map.get(a.db_key, set()) and a.db_key in lover_key_map.get(b.db_key, set())


def is_lover_conflict(
    a: Cat,
    b: Cat,
    lover_key_map: dict[int, set[int]],
    avoid_lovers: bool,
) -> bool:
    """Lover relationships are a soft signal and never hard-block a pair.

    Lover exclusivity is enforced at the room assignment level by the
    optimizer's ``_filter_lover_exclusivity()`` rather than at pair evaluation
    time.  This function intentionally returns False for all inputs; the
    ``avoid_lovers`` parameter is retained for API compatibility with
    ``evaluate_pair()``.
    """
    return False


def trait_or_default(v: Optional[float], default: float = 0.5) -> float:
    """Clamp a trait value to [0, 1], using default if None."""
    return default if v is None else max(0.0, min(1.0, float(v)))


def personality_score(cats: list[Cat], prefer_low_aggression: bool, prefer_high_libido: bool) -> float:
    """Score personality traits for a group of cats."""
    score = 0.0
    n = len(cats)
    if not n:
        return 0.0
    if prefer_low_aggression:
        score += sum(1.0 - trait_or_default(c.aggression) for c in cats) / n
    if prefer_high_libido:
        score += sum(trait_or_default(c.libido) for c in cats) / n
    return score


# ---------------------------------------------------------------------------
# Game compatibility formula
# ---------------------------------------------------------------------------

def _sexuality_mult(cat: Cat, same_sex: bool) -> float:
    """Compute the sexuality multiplier for one cat in a pairing.

    Uses the raw sexuality coefficient (0=straight, 0.5=bi, 1=gay):
      - Opposite-sex: cos(0.5 * pi * coeff)
      - Same-sex:     sin(0.5 * pi * coeff)
    """
    coeff = getattr(cat, "sexuality_raw", None)
    if coeff is None:
        coeff = {"straight": 0.05, "bi": 0.5, "gay": 0.95}.get(
            getattr(cat, "sexuality", "straight"), 0.05
        )
    coeff = max(0.0, min(1.0, float(coeff)))
    half_pi = 0.5 * math.pi
    return math.sin(half_pi * coeff) if same_sex else math.cos(half_pi * coeff)


def game_compatibility(a: Cat, b: Cat, comfort: float = 0.0) -> float:
    """Estimate the game's compatibility score for a pair.

    Formula: 0.15 * father_charisma * mother_libido * lover_mult * sexuality_mult
    The game picks father/mother roles at pairing time; we compute for both
    role assignments and return the higher one (the game shuffles randomly,
    but both orderings are attempted across breeding rounds).

    Returns a value in [0, ~inf).  The game rejects pairs with compat < 0.05.
    """
    ga = (getattr(a, "gender", "?") or "?").strip().lower()
    gb = (getattr(b, "gender", "?") or "?").strip().lower()

    # ? gender cats: sexuality multiplier is 1.0 (no effect)
    if ga == "?" or gb == "?":
        same_sex = False  # neutral, sexuality_mult = 1.0 for ? cats
        sex_mult_a = 1.0 if ga == "?" else _sexuality_mult(a, same_sex)
        sex_mult_b = 1.0 if gb == "?" else _sexuality_mult(b, same_sex)
    else:
        same_sex = ga == gb
        sex_mult_a = _sexuality_mult(a, same_sex)
        sex_mult_b = _sexuality_mult(b, same_sex)

    # Lover multiplier: default lover_coeff = 0.25
    # If no lover: lover_mult = 1
    # If partner IS lover: lover_mult = 1 + lover_coeff
    # If partner is NOT lover: lover_mult = 1 - lover_coeff
    # We don't have lover_coeff in the save, so approximate:
    #   mutual lovers → 1.25, has lover but not this partner → 0.75, no lover → 1.0
    def _lover_mult(mother: Cat, father: Cat) -> float:
        lovers = getattr(mother, "lovers", [])
        if not lovers:
            return 1.0
        if father in lovers:
            return 1.25  # default lover_coeff
        return 0.75  # penalised

    def _compat(father: Cat, mother: Cat) -> float:
        cha = father.total_stats.get("CHA", 1)
        lib = trait_or_default(mother.libido)
        lm = _lover_mult(mother, father)
        sm = sex_mult_a if father is a else sex_mult_b
        sm_other = sex_mult_b if father is a else sex_mult_a
        # The game multiplies sexuality_mult from the mother's perspective
        return 0.15 * cha * lib * lm * sm_other

    c1 = _compat(a, b)  # a as father, b as mother
    c2 = _compat(b, a)  # b as father, a as mother
    return max(c1, c2)


def breeding_success_chance(compat: float, comfort: float = 0.0) -> float:
    """Probability that a breeding attempt succeeds (both rolls pass).

    Each roll passes with probability: compat * sqrt(1 + 0.1 * comfort)
    Both rolls: compat^2 * (1 + 0.1 * comfort)
    Auto-fail when comfort < -10.
    """
    if comfort < -10:
        return 0.0
    per_roll = compat * math.sqrt(max(0.0, 1.0 + 0.1 * comfort))
    per_roll = max(0.0, min(1.0, per_roll))
    return per_roll * per_roll


# ---------------------------------------------------------------------------
# Ability / passive / disorder inheritance chances
# ---------------------------------------------------------------------------

def ability_inheritance_chances(stimulation: float) -> dict[str, float]:
    """Return inheritance probabilities based on stimulation.

    Keys: first_active, second_active, passive
    Values: probability [0, 1]
    """
    stim = float(stimulation)
    return {
        "first_active": min(1.0, max(0.0, 0.20 + 0.025 * stim)),
        "second_active": min(1.0, max(0.0, 0.02 + 0.005 * stim)),
        "passive": min(1.0, max(0.0, 0.05 + 0.01 * stim)),
    }


def disorder_inheritance_chances(a: Cat, b: Cat) -> dict[str, object]:
    """Return disorder inheritance info for a pair.

    Each parent independently has a 15% chance of passing one random disorder.
    Returns dict with parent disorders and combined probabilities.
    """
    disorders_a = getattr(a, "disorders", None) or []
    disorders_b = getattr(b, "disorders", None) or []
    # Chance of inheriting at least one disorder from parents
    chance_from_a = 0.15 if disorders_a else 0.0
    chance_from_b = 0.15 if disorders_b else 0.0
    chance_none = (1.0 - chance_from_a) * (1.0 - chance_from_b)
    chance_any = 1.0 - chance_none
    return {
        "disorders_a": disorders_a,
        "disorders_b": disorders_b,
        "chance_from_a": chance_from_a,
        "chance_from_b": chance_from_b,
        "chance_any": chance_any,
    }


def is_direct_family_pair(a: Cat, b: Cat, parent_key_map: dict[int, set[int]]) -> bool:
    """Check if two cats are parent-child or siblings."""
    parents_a = parent_key_map.get(a.db_key, set())
    parents_b = parent_key_map.get(b.db_key, set())
    if a.db_key in parents_b or b.db_key in parents_a:
        return True
    return bool(parents_a & parents_b)


def tracked_offspring(a: Cat, b: Cat) -> list[Cat]:
    """
    Return the direct offspring already tracked in the save for a breeding pair.

    The result is deduplicated and keeps the order from the first parent that
    lists the child, which makes the tracker stable across refreshes.
    """
    a_children = list(getattr(a, "children", []) or [])
    b_children = list(getattr(b, "children", []) or [])
    if not a_children or not b_children:
        return []

    a_keys = {child.db_key for child in a_children}
    b_keys = {child.db_key for child in b_children}
    ordered: list[Cat] = []
    seen: set[int] = set()

    for child in a_children:
        if child.db_key in b_keys and child.db_key not in seen:
            ordered.append(child)
            seen.add(child.db_key)

    for child in b_children:
        if child.db_key in a_keys and child.db_key not in seen:
            ordered.append(child)
            seen.add(child.db_key)

    return ordered


def _cat_has_trait(cat: Cat, category: str, trait_key: str) -> bool:
    if category == "mutation":
        return any(m.lower() == trait_key for m in getattr(cat, "mutations", []) or [])
    if category == "defect":
        return any(d.lower() == trait_key for d in getattr(cat, "defects", []) or [])
    if category == "passive":
        return any(p.lower() == trait_key for p in getattr(cat, "passive_abilities", []) or [])
    if category == "disorder":
        return any(d.lower() == trait_key for d in getattr(cat, "disorders", []) or [])
    if category == "ability":
        return any(a.lower() == trait_key for a in getattr(cat, "abilities", []) or [])
    return False


def _stat_priority_bonus(projection: PairProjection, stat_priority: Optional[Sequence[str]]) -> float:
    ordered: list[str] = []
    for stat in stat_priority or ():
        key = str(stat or "").strip().upper()
        if key in STAT_NAMES and key not in ordered:
            ordered.append(key)
    if not ordered:
        return 0.0

    weights = [1.75, 1.45, 1.2, 1.0, 0.85, 0.7, 0.55]
    total_weight = 0.0
    weighted_total = 0.0
    for index, stat in enumerate(ordered):
        weight = weights[index] if index < len(weights) else max(0.35, weights[-1] - 0.05 * (index - len(weights) + 1))
        weighted_total += projection.expected_stats.get(stat, 0.0) * weight
        total_weight += weight
    if total_weight <= 0:
        return 0.0

    weighted_expected = weighted_total / total_weight
    return (weighted_expected - projection.avg_expected) * 4.0


def pair_projection(cat_a: Cat, cat_b: Cat, stimulation: float = 50.0) -> PairProjection:
    """Predict offspring stat ranges and expected values for a pair."""
    better_stat_chance = _stimulation_inheritance_weight(stimulation)
    expected_stats: dict[str, float] = {}
    stat_ranges: dict[str, tuple[int, int]] = {}
    locked_stats: list[str] = []
    reachable_stats: list[str] = []
    missing_stats: list[str] = []
    seven_plus_total = 0.0
    distance_total = 0.0

    for stat in STAT_NAMES:
        stat_a = cat_a.base_stats[stat]
        stat_b = cat_b.base_stats[stat]
        lo = min(stat_a, stat_b)
        hi = max(stat_a, stat_b)
        stat_ranges[stat] = (lo, hi)
        expected = hi * better_stat_chance + lo * (1.0 - better_stat_chance)
        expected_stats[stat] = expected
        distance_total += abs(expected - 7.0)
        if lo >= 7:
            locked_stats.append(stat)
            reachable_stats.append(stat)
            seven_plus_total += 1.0
        elif hi >= 7:
            reachable_stats.append(stat)
            seven_plus_total += better_stat_chance
        else:
            missing_stats.append(stat)

    return PairProjection(
        expected_stats=expected_stats,
        stat_ranges=stat_ranges,
        locked_stats=tuple(locked_stats),
        reachable_stats=tuple(reachable_stats),
        missing_stats=tuple(missing_stats),
        sum_range=(sum(lo for lo, _ in stat_ranges.values()), sum(hi for _, hi in stat_ranges.values())),
        avg_expected=sum(expected_stats.values()) / len(STAT_NAMES),
        seven_plus_total=seven_plus_total,
        distance_total=distance_total,
    )


def evaluate_pair(
    a: Cat,
    b: Cat,
    *,
    hater_key_map: dict[int, set[int]],
    lover_key_map: dict[int, set[int]],
    avoid_lovers: bool,
    cache=None,
    parent_key_map: Optional[dict[int, set[int]]] = None,
    pair_eval_cache: Optional[dict] = None,
    compat_threshold: float = 0.05,
) -> tuple[bool, str, float, float]:
    """
    Unified pair evaluation. Returns (can_breed, reason, risk_pct, game_compat).

    game_compat is the game's compatibility score; pairs below compat_threshold
    are rejected early (before the expensive COI calculation).
    Pass parent_key_map to enable direct-family checking.
    """
    if pair_eval_cache is not None:
        key = pair_key(a, b)
        cached = pair_eval_cache.get(key)
        if cached is not None:
            return cached

    ok, reason = can_breed(a, b)

    # Cheap compatibility check — reject before expensive COI/risk calc
    compat = game_compatibility(a, b) if ok else 0.0
    if ok and compat < compat_threshold:
        ok, reason = False, f"Very low compatibility ({compat:.3f})"

    if ok and parent_key_map is not None and is_direct_family_pair(a, b, parent_key_map):
        ok, reason = False, "Direct family pair"

    if ok and is_hater_conflict(a, b, hater_key_map):
        ok, reason = False, "These cats hate each other"

    if ok and is_lover_conflict(a, b, lover_key_map, avoid_lovers):
        ok, reason = False, "Lover relationship blocks this pair"

    if ok:
        if cache is not None and getattr(cache, "ready", False):
            get_risk = getattr(cache, "get_risk", None)
            if callable(get_risk):
                risk = get_risk(a, b)
            else:
                risk = risk_percent(a, b)
        else:
            risk = risk_percent(a, b)
    else:
        risk = 0.0

    result = (ok, reason, risk, compat)
    if pair_eval_cache is not None:
        pair_eval_cache[pair_key(a, b)] = result
    return result


def score_pair(
    a: Cat,
    b: Cat,
    *,
    hater_key_map: Optional[dict[int, set[int]]] = None,
    lover_key_map: Optional[dict[int, set[int]]] = None,
    avoid_lovers: bool = False,
    parent_key_map: Optional[dict[int, set[int]]] = None,
    pair_eval_cache: Optional[dict] = None,
    cache=None,
    stimulation: float = 50.0,
    minimize_variance: bool = True,
    prefer_low_aggression: bool = True,
    prefer_high_libido: bool = True,
    planner_traits: Optional[Sequence[dict]] = None,
    stat_priority: Optional[Sequence[str]] = None,
    must_breed_bonus: float = 1000.0,
    lover_bonus: float = 500.0,
) -> PairFactors:
    """Return a complete score breakdown for a pair."""
    hater_key_map = hater_key_map or {}
    lover_key_map = lover_key_map or {}
    planner_traits = planner_traits or ()

    compatible, reason, risk, compat = evaluate_pair(
        a,
        b,
        hater_key_map=hater_key_map,
        lover_key_map=lover_key_map,
        avoid_lovers=avoid_lovers,
        cache=cache,
        parent_key_map=parent_key_map,
        pair_eval_cache=pair_eval_cache,
    )

    projection = pair_projection(a, b, stimulation=stimulation)
    complementarity_bonus = sum(0.5 for stat in STAT_NAMES if max(a.base_stats[stat], b.base_stats[stat]) >= 8)
    variance_penalty = sum(
        abs(a.base_stats[stat] - b.base_stats[stat]) * 2.0
        for stat in STAT_NAMES
        if minimize_variance and abs(a.base_stats[stat] - b.base_stats[stat]) > 2
    )
    personality_bonus = personality_score([a, b], prefer_low_aggression, prefer_high_libido) * 2.5

    trait_bonus = 0.0
    for t in planner_traits:
        category = str(t.get("category", ""))
        key = str(t.get("key", ""))
        wf = float(t.get("weight", 0)) / 10.0
        if not key:
            continue
        a_has = _cat_has_trait(a, category, key)
        b_has = _cat_has_trait(b, category, key)
        if a_has or b_has:
            trait_bonus += wf * 5.0
            if a_has and b_has:
                trait_bonus += wf * 2.5

    stat_priority_bonus = _stat_priority_bonus(projection, stat_priority)

    quality = 0.0
    must_breed_total = 0.0
    lover_total = 0.0
    if compatible:
        quality = (projection.avg_expected + complementarity_bonus) * (1.0 - risk / 200.0)
        quality -= variance_penalty
        quality += personality_bonus + trait_bonus + stat_priority_bonus
        # Scale by compatibility — pairs less likely to breed in-game score lower
        if compat > 0:
            compat_factor = min(1.0, compat / 0.40)  # full credit at compat >= 0.40
            quality *= (0.5 + 0.5 * compat_factor)   # floor at 50% for low-compat pairs
        if getattr(a, "must_breed", False) or getattr(b, "must_breed", False):
            quality += must_breed_bonus
            must_breed_total = must_breed_bonus
        if is_mutual_lover_pair(a, b, lover_key_map):
            quality += lover_bonus
            lover_total = lover_bonus

    return PairFactors(
        cat_a=a,
        cat_b=b,
        compatible=compatible,
        reason=reason,
        risk=risk,
        projection=projection,
        complementarity_bonus=complementarity_bonus,
        variance_penalty=variance_penalty,
        personality_bonus=personality_bonus,
        trait_bonus=trait_bonus,
        stat_priority_bonus=stat_priority_bonus,
        must_breed_bonus=must_breed_total,
        lover_bonus=lover_total,
        quality=quality,
        game_compat=compat,
    )
