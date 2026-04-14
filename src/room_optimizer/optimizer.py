"""Room optimization logic for Mewgenics breeding."""

from __future__ import annotations

import math
from functools import lru_cache
from typing import Iterable

from breeding import PairFactors, is_hater_conflict, is_mutual_lover_pair, score_pair as score_pair_factors
from save_parser import Cat, FurnitureRoomSummary, ROOM_DISPLAY, STAT_NAMES
from mewgenics.utils.planner_state import _normalize_mutation_mode_profiles

from .parallel import run_parallel_sa
from .types import (
    OptimizationParams,
    OptimizationResult,
    OptimizationStats,
    RoomAssignment,
    RoomConfig,
    RoomType,
    ScoredPair,
)

# Maximum cats in a room before falling back to greedy pair selection.
# The bitmask DP is O(2^N * N); at N=24 this is ~400M ops (~5-10 s).
# Beyond this threshold a fast greedy approach is used instead.
_MAX_DP_CATS = 24


def _cat_stats_sum(cat: Cat) -> int:
    return sum(getattr(cat, "stat_base", []) or cat.base_stats.values())


def _has_eternal_youth(cat: Cat) -> bool:
    return any(d.lower() == "eternalyouth" for d in (getattr(cat, "disorders", None) or []))


def _is_kitten(cat: Cat, threshold: int) -> bool:
    """Return True if this cat counts as a kitten (too young to breed).

    Eternal-youth cats are excluded — they're handled separately by the
    caller so they still land in the best breeding room.
    """
    if threshold <= 0:
        return False
    age = getattr(cat, "age", None)
    if age is None:
        return False
    try:
        return int(age) < threshold
    except (TypeError, ValueError):
        return False


def _trait_loss_penalty(
    cat: Cat,
    room: RoomConfig,
    desired_traits: list[dict] | None,
) -> float:
    """Penalty for placing a cat with desired mutations/disorders into a room
    whose furniture can strip those traits away.

    Evolution rooms can override desired mutations; Health rooms can cure
    desired disorders. Penalty scales with the trait weight and the room's
    Evolution/Health total.

    Mutation planner keys are stored as ``"<name>|<mutation_id>"`` when a
    mutation chip ID is available (see `mutation_planner._build_trait_catalog`).
    `cat.mutations` holds only the display name, so we match by the portion
    before the pipe on both sides to catch real desired mutations.
    """
    if not desired_traits:
        return 0.0
    evolution = max(0.0, float(getattr(room, "evolution", 0.0) or 0.0))
    health = max(0.0, float(getattr(room, "health", 0.0) or 0.0))
    if evolution <= 0.0 and health <= 0.0:
        return 0.0

    def _name_only(raw: str) -> str:
        return str(raw or "").split("|", 1)[0].strip().lower()

    cat_mutations = {_name_only(m) for m in (getattr(cat, "mutations", None) or [])}
    cat_disorders = {_name_only(d) for d in (getattr(cat, "disorders", None) or [])}
    penalty = 0.0
    for trait in desired_traits:
        if not isinstance(trait, dict):
            continue
        try:
            weight = float(trait.get("weight", 0) or 0)
        except (TypeError, ValueError):
            weight = 0.0
        if weight <= 0:
            continue
        category = str(trait.get("category") or "").strip().lower()
        raw_key = str(trait.get("key") or "").strip()
        if not category or not raw_key:
            continue
        name_key = _name_only(raw_key)
        if not name_key:
            continue
        if category == "mutation" and evolution > 0.0 and name_key in cat_mutations:
            penalty += weight * (evolution / 100.0)
        elif category == "disorder" and health > 0.0 and name_key in cat_disorders:
            penalty += weight * (health / 100.0)
    return penalty


def _filter_cats(cats: list[Cat], excluded_keys: set[int], min_stats: int) -> list[Cat]:
    return [
        c
        for c in cats
        if c.status == "In House" and c.db_key not in excluded_keys and _cat_stats_sum(c) >= min_stats
    ]


def _build_lover_hater_maps(cats: Iterable[Cat]) -> tuple[dict[int, set[int]], dict[int, set[int]], set[int]]:
    hater_key_map = {cat.db_key: {o.db_key for o in getattr(cat, "haters", [])} for cat in cats}
    lover_key_map = {cat.db_key: {o.db_key for o in getattr(cat, "lovers", [])} for cat in cats}
    has_mutual_lover = {
        cat.db_key
        for cat in cats
        if any(cat.db_key in lover_key_map.get(o.db_key, set()) for o in getattr(cat, "lovers", []))
    }
    return hater_key_map, lover_key_map, has_mutual_lover


def _coerce_room_capacity(value, *, room_type: RoomType) -> int | None:
    if value in (None, ""):
        return 6 if room_type.uses_profile else None
    try:
        capacity = int(value)
    except (TypeError, ValueError):
        return 6 if room_type.uses_profile else None
    if capacity <= 0:
        return None
    return capacity


def _room_base_stim(entry: dict, room_key: str, room_stats: dict[str, FurnitureRoomSummary] | None) -> float:
    for key in ("base_stim", "stimulation", "stim"):
        if key not in entry:
            continue
        value = entry.get(key)
        if value in (None, ""):
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue

    summary = (room_stats or {}).get(room_key)
    if summary is not None:
        return max(0.0, float(summary.raw_effects.get("Stimulation", 0.0) or 0.0))
    return 50.0


def _room_effect(
    room_key: str,
    room_stats: dict[str, FurnitureRoomSummary] | None,
    effect_name: str,
) -> float:
    summary = (room_stats or {}).get(room_key)
    if summary is None:
        return 0.0
    try:
        return max(0.0, float(summary.raw_effects.get(effect_name, 0.0) or 0.0))
    except (TypeError, ValueError):
        return 0.0


def best_breeding_room_stimulation(room_configs: list[RoomConfig], fallback: float = 50.0) -> float:
    """Return the strongest breeding-room stimulation available for generic pair scoring."""
    breeding_stims = [room.base_stim for room in room_configs if room.room_type.uses_profile]
    if breeding_stims:
        return max(0.0, max(float(stim) for stim in breeding_stims))

    if room_configs:
        return max(0.0, max(float(room.base_stim) for room in room_configs))

    return max(0.0, float(fallback))


def build_room_configs(
    room_config_entries: list[dict] | None,
    available_rooms: list[str] | None = None,
    room_stats: dict[str, FurnitureRoomSummary] | None = None,
) -> list[RoomConfig]:
    """Convert the UI's room config dicts into explicit room model objects."""
    allowed_rooms = {room for room in (available_rooms or []) if room in ROOM_DISPLAY}
    entries = [
        e
        for e in (room_config_entries or [])
        if isinstance(e, dict)
        and e.get("room") in ROOM_DISPLAY
        and (not allowed_rooms or e.get("room") in allowed_rooms)
    ]

    if entries:
        out: list[RoomConfig] = []
        for entry in entries:
            key = entry["room"]
            slot_type = str(entry.get("type", "best_pairs") or "best_pairs").strip().lower()
            if slot_type == "breeding":
                slot_type = "best_pairs"
            room_type = RoomType(slot_type) if slot_type in RoomType._value2member_map_ else RoomType.FALLBACK
            out.append(
                RoomConfig(
                    key=key,
                    room_type=room_type,
                    max_cats=_coerce_room_capacity(entry.get("max_cats", entry.get("capacity")), room_type=room_type),
                    base_stim=_room_base_stim(entry, key, room_stats),
                    evolution=_room_effect(key, room_stats, "Evolution"),
                    health=_room_effect(key, room_stats, "Health"),
                )
            )
        return out

    ordered = [room for room in (available_rooms or []) if room in ROOM_DISPLAY]
    if not ordered:
        ordered = list(ROOM_DISPLAY.keys())

    out: list[RoomConfig] = []
    for idx, room in enumerate(ordered):
        room_type = RoomType.BEST_PAIRS if len(ordered) == 1 or idx < len(ordered) - 1 else RoomType.FALLBACK
        out.append(
            RoomConfig(
                key=room,
                room_type=room_type,
                max_cats=_coerce_room_capacity(None, room_type=room_type),
                base_stim=_room_base_stim({}, room, room_stats),
                evolution=_room_effect(room, room_stats, "Evolution"),
                health=_room_effect(room, room_stats, "Health"),
            )
        )
    return out


def _normalized_mode_profiles(params: OptimizationParams) -> dict[str, dict]:
    profiles = _normalize_mutation_mode_profiles(getattr(params, "mode_profiles", {}) or {}, legacy_traits=getattr(params, "planner_traits", []))
    return profiles


def _profile_for_room(params: OptimizationParams, room: RoomConfig) -> dict:
    if not room.uses_profile:
        return {"traits": [], "stat_priority": []}
    profiles = _normalized_mode_profiles(params)
    return dict(profiles.get(room.mode_key, {"traits": [], "stat_priority": []}))


def score_pair(
    cat_a: Cat,
    cat_b: Cat,
    params: OptimizationParams,
    *,
    cache=None,
) -> ScoredPair | None:
    """Score a pair, returning None if it should not be considered."""
    factors = score_pair_factors(
        cat_a,
        cat_b,
        hater_key_map=getattr(cache, "hater_key_map", {}) if cache is not None else {},
        lover_key_map=getattr(cache, "lover_key_map", {}) if cache is not None else {},
        avoid_lovers=params.avoid_lovers,
        cache=cache,
        stimulation=params.stimulation,
        minimize_variance=params.minimize_variance,
        prefer_low_aggression=params.prefer_low_aggression,
        prefer_high_libido=params.prefer_high_libido,
        planner_traits=params.planner_traits,
    )
    if not factors.compatible or factors.risk > params.max_risk:
        return None
    return ScoredPair(cat_a=cat_a, cat_b=cat_b, risk=factors.risk, quality=factors.quality)


def _generate_pairs(cats: list[Cat]) -> list[tuple[Cat, Cat]]:
    return [(cats[i], cats[j]) for i in range(len(cats)) for j in range(i + 1, len(cats))]


def _filter_lover_exclusivity(
    pairs: list[tuple[Cat, Cat]],
    room_cats: list[Cat],
    lover_key_map: dict[int, set[int]],
) -> list[tuple[Cat, Cat]]:
    room_cat_ids = {c.db_key for c in room_cats}
    mutual_lover_targets = {
        c.db_key: {
            other_id
            for other_id in lover_key_map.get(c.db_key, set())
            if other_id in room_cat_ids and c.db_key in lover_key_map.get(other_id, set())
        }
        for c in room_cats
    }

    filtered = []
    for a, b in pairs:
        a_targets = mutual_lover_targets.get(a.db_key, set())
        b_targets = mutual_lover_targets.get(b.db_key, set())

        if a_targets and b.db_key not in a_targets:
            continue
        if b_targets and a.db_key not in b_targets:
            continue
        filtered.append((a, b))
    return filtered


def _filter_hater_conflicts(
    pairs: list[tuple[Cat, Cat]],
    room_cats: list[Cat],
    hater_key_map: dict[int, set[int]],
) -> list[tuple[Cat, Cat]]:
    filtered = []
    for a, b in pairs:
        a_hates_b = b.db_key in hater_key_map.get(a.db_key, set())
        b_hates_a = a.db_key in hater_key_map.get(b.db_key, set())
        if a_hates_b or b_hates_a:
            continue
        filtered.append((a, b))
    return filtered


def _can_fit_single(room: RoomConfig, current_count: int, cat: Cat | None = None) -> bool:
    if room.max_cats is None:
        return True
    if cat is not None and _has_eternal_youth(cat):
        return True
    return (current_count + 1) <= room.max_cats


def _best_breeding_room(room_configs: list[RoomConfig]) -> RoomConfig | None:
    breeding_rooms = [r for r in room_configs if r.room_type.uses_profile]
    if not breeding_rooms:
        return None
    return max(breeding_rooms, key=lambda r: r.base_stim)


def _throughput_density_bonus(valid_pairs: int, total_possible_pairs: float, enabled: bool) -> float:
    """Return an exponential bonus for denser, higher-throughput pairing layouts."""
    if not enabled or valid_pairs <= 0 or total_possible_pairs <= 0:
        return 0.0
    density = max(0.0, min(1.0, valid_pairs / total_possible_pairs))
    return math.expm1((density**1.5) * valid_pairs)


def _matching_result_key(result: tuple[int, float, float, tuple[tuple[int, int], ...]]) -> tuple[int, float, float]:
    count, quality, risk, _ = result
    return (count, quality, -risk)


def _select_room_pairs(
    cats_in_room: list[Cat],
    room: RoomConfig,
    room_stim: float,
    *,
    params: OptimizationParams,
    hater_key_map: dict[int, set[int]],
    lover_key_map: dict[int, set[int]],
    score_pair_cached,
    mode_family: bool = False,
    family_group_ids: dict[int, tuple[int, ...] | None] | None = None,
) -> list[ScoredPair] | None:
    """Return the best non-overlapping set of breeding pairs for a room."""
    if len(cats_in_room) < 2:
        return []

    family_group_ids = family_group_ids or {}

    if mode_family:
        for a, b in _generate_pairs(cats_in_room):
            group_a = family_group_ids.get(a.db_key)
            if group_a is not None and group_a == family_group_ids.get(b.db_key):
                return None
            factors = score_pair_cached(a, b, room, room_stim)
            if not factors.compatible or factors.risk > params.max_risk:
                return None

    room_cat_ids = {cat.db_key for cat in cats_in_room}
    mutual_lover_targets: dict[int, set[int]] = {}
    if params.avoid_lovers:
        for cat in cats_in_room:
            mutuals = {
                other_id
                for other_id in lover_key_map.get(cat.db_key, set())
                if other_id in room_cat_ids and cat.db_key in lover_key_map.get(other_id, set())
            }
            if mutuals:
                mutual_lover_targets[cat.db_key] = mutuals

    candidate_pairs: dict[tuple[int, int], ScoredPair] = {}
    for i, cat_a in enumerate(cats_in_room):
        for j in range(i + 1, len(cats_in_room)):
            cat_b = cats_in_room[j]
            if cat_b.db_key in hater_key_map.get(cat_a.db_key, set()):
                continue
            if cat_a.db_key in hater_key_map.get(cat_b.db_key, set()):
                continue

            lover_targets_a = mutual_lover_targets.get(cat_a.db_key)
            if lover_targets_a and cat_b.db_key not in lover_targets_a:
                continue
            lover_targets_b = mutual_lover_targets.get(cat_b.db_key)
            if lover_targets_b and cat_a.db_key not in lover_targets_b:
                continue

            factors = score_pair_cached(cat_a, cat_b, room, room_stim)
            if not factors.compatible or factors.risk > params.max_risk:
                continue

            candidate_pairs[(i, j)] = ScoredPair(
                cat_a=cat_a,
                cat_b=cat_b,
                risk=factors.risk,
                quality=factors.quality,
            )

    if len(cats_in_room) <= _MAX_DP_CATS:
        # Exact bitmask DP — optimal but exponential in room size.
        @lru_cache(maxsize=None)
        def _best_matching(mask: int) -> tuple[int, float, float, tuple[tuple[int, int], ...]]:
            if mask.bit_count() < 2:
                return (0, 0.0, 0.0, ())

            first_bit = mask & -mask
            first_idx = first_bit.bit_length() - 1
            best = _best_matching(mask ^ (1 << first_idx))

            for second_idx in range(first_idx + 1, len(cats_in_room)):
                if not (mask & (1 << second_idx)):
                    continue
                pair = candidate_pairs.get((first_idx, second_idx))
                if pair is None:
                    continue

                remainder = _best_matching(mask ^ (1 << first_idx) ^ (1 << second_idx))
                candidate = (
                    remainder[0] + 1,
                    remainder[1] + pair.quality,
                    remainder[2] + pair.risk,
                    ((first_idx, second_idx),) + remainder[3],
                )
                if _matching_result_key(candidate) > _matching_result_key(best):
                    best = candidate

            return best

        _, _, _, pair_indexes = _best_matching((1 << len(cats_in_room)) - 1)
        selected_pairs = [candidate_pairs[indexes] for indexes in pair_indexes]
    else:
        # Greedy fallback for large rooms — O(P log P) instead of O(2^N).
        sorted_candidates = sorted(
            candidate_pairs.items(),
            key=lambda item: (-item[1].quality, item[1].risk),
        )
        used_indices: set[int] = set()
        selected_pairs = []
        for (i, j), sp in sorted_candidates:
            if i not in used_indices and j not in used_indices:
                selected_pairs.append(sp)
                used_indices.add(i)
                used_indices.add(j)
    selected_pairs.sort(
        key=lambda pair: (
            -pair.quality,
            pair.risk,
            min(pair.cat_a.db_key, pair.cat_b.db_key),
            max(pair.cat_a.db_key, pair.cat_b.db_key),
        )
    )
    return selected_pairs


def _run_sa_refinement(
    *,
    room_assignments: dict[str, list[Cat]],
    room_configs: list[RoomConfig],
    cats_by_id: dict[int, Cat],
    filtered_cats: list[Cat],
    params: OptimizationParams,
    mode_family: bool,
    family_group_ids: dict[int, tuple[int, ...] | None],
    hater_key_map: dict[int, set[int]],
    lover_key_map: dict[int, set[int]],
    best_ey_room: RoomConfig | None,
    original_state: dict[int, str],
    score_pair_cached,
    cancel_check=None,
) -> dict[str, list[Cat]]:
    """Refine room assignments with simulated annealing."""
    _cancelled = cancel_check or (lambda: False)
    all_cat_ids = list(cats_by_id.keys())
    for i in range(len(all_cat_ids)):
        if i % 20 == 0 and _cancelled():
            return room_assignments
        for j in range(i + 1, len(all_cat_ids)):
            a = cats_by_id[all_cat_ids[i]]
            b = cats_by_id[all_cat_ids[j]]
            default_room = best_ey_room or _best_breeding_room(room_configs) or room_configs[0]
            score_pair_cached(a, b, default_room, params.stimulation)
            for room in room_configs:
                score_pair_cached(a, b, room, room.base_stim)

    pair_factor_cache = getattr(score_pair_cached, "_pair_factor_cache", {})
    sa_pair_scores: dict[tuple[int, int, str], tuple[bool, float, float]] = {}
    for (ak, bk, _stim, room_mode), factors in pair_factor_cache.items():
        pk = (ak, bk, room_mode) if ak < bk else (bk, ak, room_mode)
        sa_pair_scores[pk] = (factors.compatible, factors.risk, factors.quality)

    sa_state: dict[int, str] = {}
    for room_key, cats_list in room_assignments.items():
        for cat in cats_list:
            sa_state[cat.db_key] = room_key

    sa_fixed = frozenset(c.db_key for c in filtered_cats if _has_eternal_youth(c))
    sa_haters = {k: frozenset(v) for k, v in hater_key_map.items()}
    sa_lovers = {k: frozenset(v) for k, v in lover_key_map.items()}
    sa_family = {k: v for k, v in family_group_ids.items()} if family_group_ids else {}

    if _cancelled():
        return room_assignments

    best_state = run_parallel_sa(
        initial_state=sa_state,
        original_state={cid: original_state.get(cid, "") for cid in sa_state},
        pair_scores=sa_pair_scores,
        breeding_room_keys=[r.key for r in room_configs if r.room_type.uses_profile],
        all_room_keys=[r.key for r in room_configs],
        room_max_cats={r.key: r.max_cats for r in room_configs},
        room_stim={r.key: r.base_stim for r in room_configs},
        room_modes={r.key: r.mode_key for r in room_configs},
        fixed_ids=sa_fixed,
        hater_key_map=sa_haters,
        lover_key_map=sa_lovers,
        avoid_lovers=params.avoid_lovers,
        max_risk=params.max_risk,
        maximize_throughput=params.maximize_throughput,
        move_penalty_weight=params.move_penalty_weight,
        mode_family=params.mode_family,
        family_group_ids=sa_family,
        sa_temperature=params.sa_temperature,
        sa_cooling_rate=params.sa_cooling_rate,
        sa_neighbors_per_temp=params.sa_neighbors_per_temp,
        n_chains=params.sa_chains,
        cancel_check=cancel_check,
    )

    refined_assignments = {room.key: [] for room in room_configs}
    for cid, room_key in best_state.items():
        if cid in cats_by_id and room_key in refined_assignments:
            refined_assignments[room_key].append(cats_by_id[cid])
    return refined_assignments


def optimize_room_distribution(
    cats: list[Cat],
    room_configs: list[RoomConfig],
    params: OptimizationParams,
    *,
    cache=None,
    excluded_keys: set[int] | None = None,
    cancel_check=None,
) -> OptimizationResult:
    """Optimize room assignments using greedy placement plus optional SA refinement.

    *cancel_check* is an optional callable returning True when the caller
    wants to abort early.  The function checks it periodically and returns
    the best result so far.
    """
    excluded_keys = excluded_keys or set()
    _cancelled = cancel_check or (lambda: False)
    filtered_cats = _filter_cats(cats, excluded_keys, params.min_stats)

    if not filtered_cats:
        return OptimizationResult(
            rooms=[],
            excluded_cats=[],
            stats=OptimizationStats(
                total_cats=0,
                assigned_cats=0,
                total_pairs=0,
                breeding_rooms_used=0,
                general_rooms_used=0,
                avg_pair_quality=0.0,
                avg_risk_percent=0.0,
            ),
        )

    hater_key_map, lover_key_map, has_mutual_lover = _build_lover_hater_maps(filtered_cats)
    room_lookup = {room.key: room for room in room_configs}
    room_order = [room.key for room in room_configs]
    room_assignments: dict[str, list[Cat]] = {room.key: [] for room in room_configs}
    room_effective_counts: dict[str, int] = {room.key: 0 for room in room_configs}
    assigned_cats: set[int] = set()
    ey_cats = [c for c in filtered_cats if _has_eternal_youth(c)]
    non_ey_cats = [c for c in filtered_cats if c.db_key not in {c2.db_key for c2 in ey_cats}]

    best_ey_room = _best_breeding_room(room_configs)
    if best_ey_room is not None and ey_cats:
        room_assignments[best_ey_room.key].extend(ey_cats)
        assigned_cats.update(c.db_key for c in ey_cats)

    kitten_cats: list[Cat] = []
    if params.send_kittens_to_fallback and params.kitten_age_threshold > 0:
        kitten_cats = [c for c in non_ey_cats if _is_kitten(c, params.kitten_age_threshold)]
        if kitten_cats:
            kitten_ids = {c.db_key for c in kitten_cats}
            non_ey_cats = [c for c in non_ey_cats if c.db_key not in kitten_ids]
            fallback_rooms_for_kittens = (
                [room.key for room in room_configs if not room.room_type.uses_profile]
                or (room_order[-1:] if room_order else [])
            )
            if fallback_rooms_for_kittens:
                for i, cat in enumerate(kitten_cats):
                    target = fallback_rooms_for_kittens[i % len(fallback_rooms_for_kittens)]
                    room_assignments[target].append(cat)
                    assigned_cats.add(cat.db_key)

    cats_by_id = {c.db_key: c for c in filtered_cats}
    original_state = {c.db_key: (c.room or "") for c in filtered_cats}
    pair_factor_cache: dict[tuple[int, int, float, str], PairFactors] = {}

    def _pair_factor_key(a: Cat, b: Cat, room: RoomConfig, stimulation: float) -> tuple[int, int, float, str]:
        return (min(a.db_key, b.db_key), max(a.db_key, b.db_key), float(stimulation), room.mode_key)

    def _score_pair_cached(a: Cat, b: Cat, room: RoomConfig, stimulation: float) -> PairFactors:
        key = _pair_factor_key(a, b, room, stimulation)
        if key not in pair_factor_cache:
            profile = _profile_for_room(params, room)
            pair_factor_cache[key] = score_pair_factors(
                a,
                b,
                hater_key_map=hater_key_map,
                lover_key_map=lover_key_map,
                avoid_lovers=params.avoid_lovers,
                cache=cache,
                stimulation=stimulation,
                minimize_variance=params.minimize_variance,
                prefer_low_aggression=params.prefer_low_aggression,
                prefer_high_libido=params.prefer_high_libido,
                planner_traits=profile.get("traits", params.planner_traits),
                stat_priority=[] if params.ignore_stat_priority else profile.get("stat_priority", []),
            )
        _score_pair_cached._pair_factor_cache = pair_factor_cache
        return pair_factor_cache[key]

    def _family_group_id(cat: Cat) -> tuple[int, ...] | None:
        ancestors: list[int] = []
        for p in (cat.parent_a, cat.parent_b):
            if p:
                ancestors.append(p.db_key)
                for gp in (p.parent_a, p.parent_b):
                    if gp:
                        ancestors.append(gp.db_key)
        return tuple(sorted(ancestors)) if ancestors else None

    family_group_ids: dict[int, tuple[int, ...] | None] = {
        c.db_key: _family_group_id(c) for c in filtered_cats
    } if params.mode_family else {}

    def _room_conflict(a: Cat, b: Cat, room: RoomConfig) -> bool:
        factors = _score_pair_cached(a, b, room, room.base_stim if room.base_stim is not None else params.stimulation)
        return (not factors.compatible) or factors.risk > params.max_risk

    def _room_pair_metrics(room: RoomConfig, cats_in_room: list[Cat], room_stim: float) -> tuple[float, int] | None:
        """Return the room score and simultaneous-pair count for this room."""
        selected_pairs = _select_room_pairs(
            cats_in_room,
            room,
            room_stim,
            params=params,
            hater_key_map=hater_key_map,
            lover_key_map=lover_key_map,
            score_pair_cached=_score_pair_cached,
            mode_family=params.mode_family,
            family_group_ids=family_group_ids,
        )
        if selected_pairs is None:
            return None
        return sum(pair.quality for pair in selected_pairs), len(selected_pairs)

    def _trait_loss_penalty_for(room: RoomConfig, cats_in_room: list[Cat]) -> float:
        if not params.avoid_trait_loss:
            return 0.0
        profile = _profile_for_room(params, room)
        desired = profile.get("traits") if room.uses_profile else None
        if not desired:
            return 0.0
        return sum(_trait_loss_penalty(cat, room, desired) for cat in cats_in_room)

    def _room_pair_score(room: RoomConfig, cats_in_room: list[Cat], room_stim: float) -> float | tuple[int, float]:
        metrics = _room_pair_metrics(room, cats_in_room, room_stim)
        if metrics is None:
            return (0, 0.0) if params.maximize_throughput else 0.0
        sum_quality, valid_pairs = metrics
        if valid_pairs <= 0:
            return (0, 0.0) if params.maximize_throughput else 0.0

        total_possible_pairs = (len(cats_in_room) * (len(cats_in_room) - 1)) / 2.0
        base_score = sum_quality / total_possible_pairs
        trait_penalty = _trait_loss_penalty_for(room, cats_in_room)
        if params.maximize_throughput:
            # Throughput mode should prefer the room that produces more valid
            # breeding pairs, even if its average pair quality is a little lower.
            return (
                valid_pairs,
                base_score + _throughput_density_bonus(
                    valid_pairs,
                    total_possible_pairs,
                    True,
                ) - trait_penalty,
            )
        return base_score + _throughput_density_bonus(
            valid_pairs,
            total_possible_pairs,
            False,
        ) - trait_penalty

    if params.mode_family:
        room_caps = {room.key: room.max_cats for room in room_configs}
        family_assignments: dict[str, dict[str, list[Cat]]] = {
            room.key: {"males": [], "females": [], "unknown": []} for room in room_configs
        }

        def _room_cats(room_key: str) -> list[Cat]:
            rd = family_assignments[room_key]
            return rd["males"] + rd["females"] + rd["unknown"]

        def _room_effective_count(room_key: str) -> int:
            return sum(0 if _has_eternal_youth(cat) else 1 for cat in _room_cats(room_key))

        def _room_has_space(room_key: str) -> bool:
            max_cats = room_caps.get(room_key)
            if max_cats is None:
                return True
            return _room_effective_count(room_key) < max_cats

        def _preferred_rooms(cat: Cat) -> list[str]:
            if not params.avoid_lovers:
                return list(room_order)
            lover_rooms = [r for r in room_order if any(is_mutual_lover_pair(cat, ec, lover_key_map) for ec in _room_cats(r))]
            return lover_rooms + [r for r in room_order if r not in lover_rooms]

        for cat in ey_cats:
            room_key = best_ey_room.key if best_ey_room is not None else (room_order[0] if room_order else None)
            if room_key is None:
                continue
            family_assignments[room_key]["unknown"].append(cat)

        if kitten_cats:
            # Kittens were pre-assigned to fallback rooms above; mirror those
            # placements into family_assignments so the rebind on line ~784
            # doesn't drop them.
            fallback_rooms_for_kittens = (
                [room.key for room in room_configs if not room.room_type.uses_profile]
                or (room_order[-1:] if room_order else [])
            )
            for cat in kitten_cats:
                gender = (getattr(cat, "gender", "") or "").lower()
                bucket = "males" if gender == "male" else "females" if gender == "female" else "unknown"
                # Find which fallback room this kitten was assigned to.
                target = next(
                    (r for r in fallback_rooms_for_kittens if cat in room_assignments.get(r, [])),
                    fallback_rooms_for_kittens[0] if fallback_rooms_for_kittens else None,
                )
                if target is None:
                    continue
                family_assignments[target][bucket].append(cat)

        for gender_list, gender_key in (
            ([c for c in non_ey_cats if (c.gender or "").lower() == "male"], "males"),
            ([c for c in non_ey_cats if (c.gender or "").lower() == "female"], "females"),
            ([c for c in non_ey_cats if (c.gender or "") == "?"], "unknown"),
        ):
            family_groups: dict[tuple[int, ...] | None, list[Cat]] = {}
            no_family: list[Cat] = []
            for cat in gender_list:
                fid = family_group_ids.get(cat.db_key)
                (family_groups.setdefault(fid, []) if fid else no_family).append(cat)

            for fid, fcats in family_groups.items():
                for cat in fcats:
                    placed = False
                    for room_key in _preferred_rooms(cat):
                        rc = _room_cats(room_key)
                        if not _room_has_space(room_key):
                            continue
                        room = room_lookup[room_key]
                        if any(family_group_ids.get(ec.db_key) == fid or _room_conflict(cat, ec, room) for ec in rc):
                            continue
                        family_assignments[room_key][gender_key].append(cat)
                        placed = True
                        break
                    if not placed:
                        best_room = min(
                            (r for r in _preferred_rooms(cat) if _room_has_space(r)),
                            key=lambda r: sum(
                                _score_pair_cached(cat, ec, room_lookup[r], room_lookup[r].base_stim).risk
                                for ec in _room_cats(r)
                                if not is_hater_conflict(cat, ec, hater_key_map)
                            ),
                            default=min(room_order, key=lambda r: _room_effective_count(r)),
                        )
                        family_assignments[best_room][gender_key].append(cat)

            for cat in no_family:
                placed = False
                for room_key in _preferred_rooms(cat):
                    rc = _room_cats(room_key)
                    room = room_lookup[room_key]
                    if _room_has_space(room_key) and not any(_room_conflict(cat, ec, room) for ec in rc):
                        family_assignments[room_key][gender_key].append(cat)
                        placed = True
                        break
                if not placed:
                    best_room = min(
                        (r for r in _preferred_rooms(cat) if _room_has_space(r)),
                        key=lambda r: sum(
                            _score_pair_cached(cat, ec, room_lookup[r], room_lookup[r].base_stim).risk
                            for ec in _room_cats(r)
                            if not is_hater_conflict(cat, ec, hater_key_map)
                        ),
                        default=min(room_order, key=lambda r: _room_effective_count(r)),
                    )
                    family_assignments[best_room][gender_key].append(cat)

        room_assignments = {room_key: _room_cats(room_key) for room_key in room_order}

    else:
        candidate_pairs = _generate_pairs([c for c in non_ey_cats if c.db_key not in assigned_cats])
        candidate_pairs = [p for p in candidate_pairs if p[0].db_key not in assigned_cats and p[1].db_key not in assigned_cats]

        lover_locked: set[int] = has_mutual_lover if params.avoid_lovers else set()
        pairs_with_scores: list[dict] = []
        for cat_a, cat_b in candidate_pairs:
            if params.avoid_lovers and (cat_a.db_key in lover_locked or cat_b.db_key in lover_locked):
                if not is_mutual_lover_pair(cat_a, cat_b, lover_key_map):
                    continue
            factors = _score_pair_cached(cat_a, cat_b, best_ey_room or _best_breeding_room(room_configs) or room_configs[0], params.stimulation)
            if not factors.compatible or factors.risk > params.max_risk:
                continue
            pairs_with_scores.append(
                {
                    "cat_a": cat_a,
                    "cat_b": cat_b,
                    "risk": factors.risk,
                    "avg_stats": sum(cat_a.base_stats[s] + cat_b.base_stats[s] for s in STAT_NAMES) / (2 * len(STAT_NAMES)),
                    "quality": factors.quality,
                    "must_breed_bonus": factors.must_breed_bonus,
                    "lover_bonus": factors.lover_bonus,
                }
            )

        pairs_with_scores.sort(
            key=lambda p: (p["must_breed_bonus"], p["lover_bonus"], p["quality"]),
            reverse=True,
        )

        for pair_idx, pair in enumerate(pairs_with_scores):
            if pair_idx % 50 == 0 and _cancelled():
                break
            a, b = pair["cat_a"], pair["cat_b"]
            if a.db_key in assigned_cats or b.db_key in assigned_cats:
                continue
            placed = False
            if params.maximize_throughput:
                best_room_key: str | None = None
                best_room_score: tuple[int, float] | None = None
                for room in room_configs:
                    if not room.room_type.uses_profile:
                        continue
                    rc = room_assignments[room.key]
                    effective_count = room_effective_counts[room.key]
                    if room.max_cats is not None and effective_count >= room.max_cats:
                        continue
                    if not _can_fit_single(room, effective_count, a):
                        continue
                    next_count = effective_count + (0 if _has_eternal_youth(a) else 1)
                    if not _can_fit_single(room, next_count, b):
                        continue
                    candidate_metrics = _room_pair_metrics(room, rc + [a, b], room.base_stim)
                    if candidate_metrics is None or candidate_metrics[1] <= 0:
                        continue
                    candidate_score = _room_pair_score(room, rc + [a, b], room.base_stim)
                    if best_room_score is None or candidate_score > best_room_score:
                        best_room_score = candidate_score
                        best_room_key = room.key
                if best_room_key is not None:
                    room_assignments[best_room_key].extend([a, b])
                    if not _has_eternal_youth(a):
                        room_effective_counts[best_room_key] += 1
                    if not _has_eternal_youth(b):
                        room_effective_counts[best_room_key] += 1
                    assigned_cats.update([a.db_key, b.db_key])
                    placed = True
            else:
                # When avoid_trait_loss is on, try rooms whose furniture is
                # least likely to strip this pair's desired traits first.
                if params.avoid_trait_loss:
                    iter_rooms = sorted(
                        room_configs,
                        key=lambda r: (
                            _trait_loss_penalty_for(r, [a, b]) if r.room_type.uses_profile else 0.0,
                        ),
                    )
                else:
                    iter_rooms = list(room_configs)
                for room in iter_rooms:
                    if not room.room_type.uses_profile:
                        continue
                    rc = room_assignments[room.key]
                    effective_count = room_effective_counts[room.key]
                    if room.max_cats is not None and effective_count >= room.max_cats:
                        continue
                    if _can_fit_single(room, effective_count, a) and _can_fit_single(room, effective_count + (0 if _has_eternal_youth(a) else 1), b):
                        candidate_metrics = _room_pair_metrics(room, rc + [a, b], room.base_stim)
                        if candidate_metrics is None or candidate_metrics[1] <= 0:
                            continue
                        rc.extend([a, b])
                        if not _has_eternal_youth(a):
                            room_effective_counts[room.key] += 1
                        if not _has_eternal_youth(b):
                            room_effective_counts[room.key] += 1
                        assigned_cats.update([a.db_key, b.db_key])
                        placed = True
                        break
            if not placed:
                for cat in [a, b]:
                    if cat.db_key in assigned_cats:
                        continue
                    preferred = sorted(
                        [room.key for room in room_configs if room.room_type.uses_profile],
                        key=lambda r: (
                            not params.avoid_lovers
                            or not any(is_mutual_lover_pair(cat, ec, lover_key_map) for ec in room_assignments[r]),
                            len(room_assignments[r]),
                        ),
                    )
                    for room_key in preferred:
                        room = room_lookup[room_key]
                        effective_count = room_effective_counts[room_key]
                        if room.max_cats is not None and effective_count >= room.max_cats:
                            continue
                        candidate_metrics = _room_pair_metrics(room, room_assignments[room_key] + [cat], room.base_stim)
                        if candidate_metrics is None:
                            continue
                        if _can_fit_single(room, effective_count, cat):
                            if params.maximize_throughput:
                                candidate_rooms = []
                                for candidate_key in preferred:
                                    candidate_room = room_lookup[candidate_key]
                                    candidate_count = room_effective_counts[candidate_key]
                                    if candidate_room.max_cats is not None and candidate_count >= candidate_room.max_cats:
                                        continue
                                    if not _can_fit_single(candidate_room, candidate_count, cat):
                                        continue
                                    current_metrics = _room_pair_metrics(
                                        candidate_room,
                                        room_assignments[candidate_key],
                                        candidate_room.base_stim,
                                    )
                                    if current_metrics is None:
                                        continue
                                    candidate_metrics = _room_pair_metrics(candidate_room, room_assignments[candidate_key] + [cat], candidate_room.base_stim)
                                    if candidate_metrics is None:
                                        continue
                                    if candidate_metrics[1] <= current_metrics[1]:
                                        continue
                                    candidate_score = _room_pair_score(
                                        candidate_room,
                                        room_assignments[candidate_key] + [cat],
                                        candidate_room.base_stim,
                                    )
                                    candidate_rooms.append((candidate_score, candidate_key))
                                if candidate_rooms:
                                    _, best_room_key = max(candidate_rooms, key=lambda item: item[0])
                                    room_assignments[best_room_key].append(cat)
                                    if not _has_eternal_youth(cat):
                                        room_effective_counts[best_room_key] += 1
                                    assigned_cats.add(cat.db_key)
                                    break
                            else:
                                room_assignments[room_key].append(cat)
                                if not _has_eternal_youth(cat):
                                    room_effective_counts[room_key] += 1
                                assigned_cats.add(cat.db_key)
                                break

        unassigned = [c for c in non_ey_cats if c.db_key not in assigned_cats]
        fallback_rooms = [room.key for room in room_configs if not room.room_type.uses_profile] or (room_order[-1:] if room_order else [])
        for i, cat in enumerate(unassigned):
            if not fallback_rooms:
                break
            room_assignments[fallback_rooms[i % len(fallback_rooms)]].append(cat)
            assigned_cats.add(cat.db_key)

    if params.use_sa and not _cancelled():
        room_assignments = _run_sa_refinement(
            room_assignments=room_assignments,
            room_configs=room_configs,
            cats_by_id=cats_by_id,
            filtered_cats=filtered_cats,
            params=params,
            mode_family=params.mode_family,
            family_group_ids=family_group_ids,
            hater_key_map=hater_key_map,
            lover_key_map=lover_key_map,
            best_ey_room=best_ey_room,
            original_state=original_state,
            score_pair_cached=_score_pair_cached,
            cancel_check=cancel_check,
        )

    room_results: list[RoomAssignment] = []
    breeding_rooms_used = 0
    general_rooms_used = 0
    total_pair_quality = 0.0
    total_risk = 0.0
    total_pairs = 0

    for room in room_configs:
        cats_in_room = room_assignments[room.key]
        pairs: list[ScoredPair] = []
        if room.room_type.uses_profile and len(cats_in_room) >= 2:
            selected_pairs = _select_room_pairs(
                cats_in_room,
                room,
                room.base_stim,
                params=params,
                hater_key_map=hater_key_map,
                lover_key_map=lover_key_map,
                score_pair_cached=_score_pair_cached,
                mode_family=params.mode_family,
                family_group_ids=family_group_ids,
            )
            if selected_pairs is not None:
                pairs = selected_pairs

        ey_in_room = [c for c in cats_in_room if _has_eternal_youth(c)]
        room_results.append(
            RoomAssignment(
                room=room,
                cats=cats_in_room,
                pairs=pairs,
                eternal_youth_cats=ey_in_room,
            )
        )

        if cats_in_room:
            if room.room_type.uses_profile:
                breeding_rooms_used += 1
            elif room.room_type != RoomType.NONE:
                general_rooms_used += 1

        total_pairs += len(pairs)
        for p in pairs:
            total_pair_quality += p.quality
            total_risk += p.risk

    avg_quality = total_pair_quality / total_pairs if total_pairs > 0 else 0.0
    avg_risk = total_risk / total_pairs if total_pairs > 0 else 0.0

    excluded = [c for c in filtered_cats if c.db_key not in assigned_cats]
    stats = OptimizationStats(
        total_cats=len(filtered_cats),
        assigned_cats=len(filtered_cats) - len(excluded),
        total_pairs=total_pairs,
        breeding_rooms_used=breeding_rooms_used,
        general_rooms_used=general_rooms_used,
        avg_pair_quality=avg_quality,
        avg_risk_percent=avg_risk,
    )

    return OptimizationResult(rooms=room_results, excluded_cats=excluded, stats=stats)
