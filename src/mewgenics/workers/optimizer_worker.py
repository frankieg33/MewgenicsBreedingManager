"""RoomOptimizerWorker: runs room assignment solver off the main thread."""
from PySide6.QtCore import QThread, Signal

from save_parser import (
    Cat, can_breed, risk_percent, ROOM_KEYS,
)
from breeding import pair_projection, is_mutual_lover_pair
from room_optimizer import (
    best_breeding_room_stimulation, OptimizationParams, RoomType, build_room_configs,
    optimize_room_distribution,
)
from mewgenics.utils.localization import ROOM_DISPLAY
from mewgenics.constants import _room_key_from_display
from mewgenics.utils.planner_state import _mutation_class_label, _normalize_mutation_mode_profiles
from mewgenics.utils.tags import _cat_tags
from mewgenics.utils.cat_analysis import _cat_base_sum
from mewgenics.utils.calibration import _trait_label_from_value
from mewgenics.utils.abilities import _trait_inheritance_probabilities


class RoomOptimizerWorker(QThread):
    """Runs _calculate_optimal_distribution off the main thread."""
    finished = Signal(object)   # emits result dict

    def __init__(self, cats, excluded_keys, cache, params, parent=None):
        super().__init__(parent)
        self._cats = cats
        self._excluded_keys = excluded_keys
        self._cache = cache
        self._params = params  # dict of UI settings

    def run(self):
        # All computation happens here; no Qt widgets are touched.
        p = self._params
        cache = self._cache
        excluded_keys = set(self._excluded_keys or set())

        alive_cats = [c for c in self._cats if c.status != "Gone" and c.db_key not in excluded_keys]
        excluded_cats = [c for c in self._cats if c.status != "Gone" and c.db_key in excluded_keys]

        min_stats = int(p.get("min_stats", 0) or 0)
        max_risk = float(p.get("max_risk", 10.0) or 0.0)
        minimize_variance = bool(p.get("minimize_variance", True))
        avoid_lovers = bool(p.get("avoid_lovers", True))
        prefer_low_aggression = bool(p.get("prefer_low_aggression", True))
        prefer_high_libido = bool(p.get("prefer_high_libido", True))
        maximize_throughput = bool(p.get("maximize_throughput", False))
        ignore_stat_priority = bool(p.get("ignore_stat_priority", False))
        send_kittens_to_fallback = bool(p.get("send_kittens_to_fallback", False))
        # Use a two-step default instead of `... or 2`: an explicit numeric 0
        # must pass through so callers can disable kitten routing via
        # threshold (optimizer logic treats `<= 0` as disabled). Only fall
        # back to 2 when the key is missing or the value can't be coerced.
        _raw_kitten_threshold = p.get("kitten_age_threshold", 2)
        try:
            kitten_age_threshold = int(_raw_kitten_threshold) if _raw_kitten_threshold is not None else 2
        except (TypeError, ValueError):
            kitten_age_threshold = 2
        avoid_trait_loss = bool(p.get("avoid_trait_loss", False))
        sa_temperature = float(p.get("sa_temperature", 8.0) or 8.0)
        sa_neighbors = int(p.get("sa_neighbors", 120) or 120)
        mode_family = bool(p.get("mode_family", False))
        use_sa = bool(p.get("use_sa", False))
        planner_traits = list(p.get("planner_traits", []))
        mode_profiles = _normalize_mutation_mode_profiles(p.get("mode_profiles", {}), legacy_traits=planner_traits)
        available_rooms = [room for room in p.get("available_rooms", []) if room in ROOM_DISPLAY]
        room_stats = p.get("room_stats", {})
        if not isinstance(room_stats, dict):
            room_stats = {}
        room_configs = build_room_configs(
            p.get("room_config", []),
            available_rooms=available_rooms,
            room_stats=room_stats,
        )
        stimulation = best_breeding_room_stimulation(room_configs)

        if min_stats > 0:
            alive_cats = [c for c in alive_cats if _cat_base_sum(c) >= min_stats]

        if len(alive_cats) < 2:
            self.finished.emit({"error": "Not enough cats to optimize"})
            return

        params = OptimizationParams(
            min_stats=min_stats,
            max_risk=max_risk,
            stimulation=stimulation,
            maximize_throughput=maximize_throughput,
            minimize_variance=minimize_variance,
            avoid_lovers=avoid_lovers,
            prefer_low_aggression=prefer_low_aggression,
            prefer_high_libido=prefer_high_libido,
            mode_family=mode_family,
            use_sa=use_sa,
            sa_temperature=max(0.1, sa_temperature),
            sa_neighbors_per_temp=max(1, sa_neighbors),
            planner_traits=planner_traits,
            mode_profiles=mode_profiles,
            ignore_stat_priority=ignore_stat_priority,
            send_kittens_to_fallback=send_kittens_to_fallback,
            kitten_age_threshold=kitten_age_threshold,
            avoid_trait_loss=avoid_trait_loss,
        )

        optimized = optimize_room_distribution(
            alive_cats,
            room_configs,
            params,
            cache=cache,
            excluded_keys=excluded_keys,
            cancel_check=self.isInterruptionRequested,
        )

        if self.isInterruptionRequested():
            self.finished.emit({"cancelled": True})
            return

        hater_key_map = {cat.db_key: {o.db_key for o in getattr(cat, "haters", [])} for cat in alive_cats}
        lover_key_map = {cat.db_key: {o.db_key for o in getattr(cat, "lovers", [])} for cat in alive_cats}
        has_mutual_lover = {
            cat.db_key
            for cat in alive_cats
            if any(cat.db_key in lover_key_map.get(o.db_key, set()) for o in getattr(cat, "lovers", []))
        }

        locator_data: list[dict] = []
        room_rows: list[dict] = []
        # Shared kinship memo so risk_percent calls across pairs reuse
        # intermediate kinship results instead of recomputing from scratch.
        kinship_memo: dict[tuple[int, int], float] = {}
        for room_idx, assignment in enumerate(optimized.rooms):
            room = assignment.room
            assigned_room_label = room.display_name
            cat_names = [f"{c.name} ({c.gender_display})" for c in assignment.cats]

            for cat in assignment.cats:
                current = cat.room_display or cat.status or "?"
                current_room_key = cat.room if cat.room in ROOM_DISPLAY else _room_key_from_display(cat.room_display)
                needs_move = cat.status != "In House" or cat.room_display != assigned_room_label
                locator_data.append({
                    "name": cat.name,
                    "gender_display": cat.gender_display,
                    "db_key": cat.db_key,
                    "has_lover": bool(getattr(cat, "lovers", None)),
                    "tags": list(_cat_tags(cat)),
                    "age": cat.age if cat.age is not None else cat.db_key,
                    "current_room": current,
                    "current_room_key": current_room_key,
                    "assigned_room": assigned_room_label,
                    "assigned_room_key": room.key,
                    "room_order": room_idx,
                    "needs_move": needs_move,
                })

            room_pairs = []
            cats_in_room = assignment.cats
            # Only enumerate breeding pairs for rooms that use profiles
            # (Best Pairs / Melee / Ranged / Magic).  Fallback rooms hold
            # overflow cats and don't need pair-level data for display.
            if room.room_type.uses_profile:
                for ri, a in enumerate(cats_in_room):
                    for b in cats_in_room[ri + 1:]:
                        ok, _ = can_breed(a, b)
                        if not ok:
                            continue
                        projection = pair_projection(a, b, room.base_stim)
                        trait_probs = _trait_inheritance_probabilities(a, b, room.base_stim)
                        mutations = [
                            (display, prob)
                            for display, category, prob, _ in trait_probs
                            if category == "mutation"
                        ]
                        if cache is not None and getattr(cache, 'ready', False):
                            pair_risk = cache.get_risk(a, b)
                        else:
                            pair_risk = risk_percent(a, b, kinship_memo)
                        room_pairs.append({
                            "cat_a": f"{a.name} ({a.gender_display})",
                            "cat_b": f"{b.name} ({b.gender_display})",
                            "cat_a_db_key": a.db_key,
                            "cat_b_db_key": b.db_key,
                            "is_lovers": is_mutual_lover_pair(a, b, lover_key_map),
                            "cat_a_has_lover": a.db_key in has_mutual_lover,
                            "cat_b_has_lover": b.db_key in has_mutual_lover,
                            "risk": pair_risk,
                            "avg_stats": (_cat_base_sum(a) + _cat_base_sum(b)) / 2,
                            "stat_ranges": projection.stat_ranges,
                            "sum_range": projection.sum_range,
                            "mutations": mutations,
                        })

            room_pairs.sort(key=lambda p: (-p["avg_stats"], p["risk"]))
            best_pairs_count = len(assignment.pairs)
            avg_stats = sum(p["avg_stats"] for p in room_pairs) / len(room_pairs) if room_pairs else 0.0
            avg_risk = sum(p["risk"] for p in room_pairs) / len(room_pairs) if room_pairs else 0.0
            room_rows.append({
                "room": room.key,
                "room_label": assigned_room_label,
                "room_mode": room.mode_key,
                "room_mode_label": _mutation_class_label(room.mode_key),
                "capacity": room.max_cats,
                "base_stim": room.base_stim,
                "cat_names": cat_names,
                "cat_keys": [c.db_key for c in assignment.cats],
                "pairs": room_pairs,
                "best_pairs_count": best_pairs_count,
                "avg_stats": avg_stats,
                "avg_risk": avg_risk,
                "is_fallback": room.room_type == RoomType.FALLBACK,
            })

        excluded_rows = [
            {
                "name": f"{c.name} ({c.gender_display})",
                "db_key": c.db_key,
                "tags": list(_cat_tags(c)),
                "stats": dict(c.base_stats),
                "sum": _cat_base_sum(c),
                "traits": {
                    "aggression": _trait_label_from_value("aggression", c.aggression) or "unknown",
                    "libido": _trait_label_from_value("libido", c.libido) or "unknown",
                    "inbredness": _trait_label_from_value("inbredness", c.inbredness) or "unknown",
                },
            }
            for c in excluded_cats
        ]

        self.finished.emit({
            "room_rows": room_rows,
            "locator_data": locator_data,
            "excluded_rows": excluded_rows,
            "excluded_cats": excluded_cats,
            "min_stats": min_stats,
            "max_risk": max_risk,
            "mode_family": mode_family,
            "minimize_variance": minimize_variance,
            "avoid_lovers": avoid_lovers,
            "prefer_low_aggression": prefer_low_aggression,
            "prefer_high_libido": prefer_high_libido,
            "maximize_throughput": maximize_throughput,
            "sa_temperature": sa_temperature,
            "sa_neighbors": sa_neighbors,
            "use_sa": use_sa,
            "send_kittens_to_fallback": send_kittens_to_fallback,
            "avoid_trait_loss": avoid_trait_loss,
        })
        return
