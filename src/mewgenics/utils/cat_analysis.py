"""Cat metrics, exceptional/donation checks, and breakpoint analysis."""
from typing import Optional

from save_parser import Cat, STAT_NAMES


def _cat_uid(cat: Cat) -> str:
    return str(getattr(cat, "unique_id", "") or "").strip().lower()


def _cat_base_sum(cat: "Cat") -> int:
    return int(sum(cat.base_stats.values()))


def _is_exceptional_breeder(cat: "Cat") -> bool:
    from mewgenics.utils.thresholds import (
        EXCEPTIONAL_SUM_THRESHOLD, SCORE_SOURCE,
        DETAILED_EXCEPTIONAL_THRESHOLD, _get_detailed_score,
    )
    if SCORE_SOURCE == "detailed":
        score = _get_detailed_score(cat)
        if score is not None:
            return score >= DETAILED_EXCEPTIONAL_THRESHOLD
        # Fall back to base sum when the Detailed Scoring cache isn't populated
        # yet (e.g. view never opened this session).
    return _cat_base_sum(cat) >= EXCEPTIONAL_SUM_THRESHOLD


def _has_eternal_youth(cat: "Cat") -> bool:
    return any(d.lower() == "eternalyouth" for d in (getattr(cat, "disorders", None) or []))


def _donation_candidate_base_reason(cat: "Cat") -> Optional[str]:
    from mewgenics.utils.thresholds import (
        EXCEPTIONAL_SUM_THRESHOLD, DONATION_SUM_THRESHOLD, DONATION_MAX_TOP_STAT,
        DONATION_MISSING_PLANNER_TRAITS, _donation_planner_traits,
        SCORE_SOURCE, DETAILED_DONATION_THRESHOLD, _get_detailed_score,
    )
    from mewgenics.utils.abilities import _cat_has_trait
    if _has_eternal_youth(cat):
        return None

    use_detailed = SCORE_SOURCE == "detailed"
    detailed_score = _get_detailed_score(cat) if use_detailed else None
    # If the user picked Detailed but the cache is empty, silently fall back
    # to base-sum so the sidebar never flags every cat at once.
    if use_detailed and detailed_score is None:
        use_detailed = False

    planner_trait_reason: Optional[str] = None
    planner_mode = False
    if DONATION_MISSING_PLANNER_TRAITS:
        planner_traits = [
            t for t in _donation_planner_traits()
            if t.get("category") in {"mutation", "ability"}
        ]
        if planner_traits:
            planner_mode = True
            if any(_cat_has_trait(cat, t["category"], t["key"]) for t in planner_traits):
                return None
            missing = ", ".join(str(t.get("display") or t.get("key") or "?") for t in planner_traits[:4])
            planner_trait_reason = f"missing selected planner traits{f' ({missing})' if missing else ''}"

    total = _cat_base_sum(cat)
    top_stat = max(cat.base_stats.values()) if cat.base_stats else 0

    def _floor_reasons() -> list[str]:
        if use_detailed:
            if detailed_score is not None and detailed_score <= DETAILED_DONATION_THRESHOLD:
                return [f"detailed score {detailed_score:+.1f} <= {DETAILED_DONATION_THRESHOLD:+.1f}"]
            return []
        reasons: list[str] = []
        if total <= DONATION_SUM_THRESHOLD:
            reasons.append(f"base sum {total} <= {DONATION_SUM_THRESHOLD}")
        if top_stat <= DONATION_MAX_TOP_STAT:
            reasons.append(f"top base stat {top_stat} <= {DONATION_MAX_TOP_STAT}")
        return reasons

    if planner_mode:
        if planner_trait_reason is None:
            return None
        floor = _floor_reasons()
        if not floor:
            return None
        reasons: list[str] = [planner_trait_reason, *floor]
        aggression = cat.aggression
        if aggression is not None and aggression >= 0.66:
            reasons.append("high aggression")
        return ", ".join(reasons)

    if _is_exceptional_breeder(cat):
        return None

    reasons = _floor_reasons()
    aggression = cat.aggression
    if aggression is not None and aggression >= 0.66:
        reasons.append("high aggression")
    if not reasons:
        return None
    # In base-sum mode, require both sum AND top-stat to be under the floor —
    # a single low stat alone isn't enough.  Detailed mode already has a
    # single-axis floor so we don't apply that gate.
    if not use_detailed and total > DONATION_SUM_THRESHOLD and top_stat > DONATION_MAX_TOP_STAT:
        return None
    return ", ".join(reasons)


def _donation_candidate_reason(cat: "Cat") -> Optional[str]:
    base_reason = _donation_candidate_base_reason(cat)
    if base_reason is None:
        return None
    if cat.must_breed:
        return f"{base_reason} (currently marked Must Breed)"
    return base_reason


def _is_donation_candidate(cat: "Cat") -> bool:
    return _donation_candidate_base_reason(cat) is not None


def _relations_summary(cat: "Cat") -> str:
    parts: list[str] = []
    if cat.lovers:
        parts.append("L: " + ", ".join(other.name for other in cat.lovers))
    if cat.haters:
        parts.append("H: " + ", ".join(other.name for other in cat.haters))
    return " | ".join(parts)


def _pair_breakpoint_analysis(a: "Cat", b: "Cat", stimulation: float = 50.0) -> dict:
    better_stat_chance = (1.0 + 0.01 * stimulation) / (2.0 + 0.01 * stimulation)
    stat_rows: list[dict] = []
    locks: list[str] = []
    can_hit: list[str] = []
    near_hit: list[str] = []
    stalled: list[str] = []
    upgrade_now: list[str] = []

    for stat in STAT_NAMES:
        va = int(a.base_stats[stat])
        vb = int(b.base_stats[stat])
        lo = min(va, vb)
        hi = max(va, vb)
        expected = hi * better_stat_chance + lo * (1.0 - better_stat_chance)
        if lo >= 7:
            status = "locked"
            locks.append(stat)
        elif hi >= 7:
            status = "can hit 7"
            can_hit.append(stat)
        elif hi == 6:
            status = "one step off"
            near_hit.append(stat)
        else:
            status = "stalled"
            stalled.append(stat)
        if hi > lo:
            upgrade_now.append(stat)
        stat_rows.append({
            "stat": stat,
            "lo": lo,
            "hi": hi,
            "expected": expected,
            "status": status,
        })

    if locks:
        headline = f"Locks {', '.join(locks)}"
    elif can_hit:
        headline = f"Can hit 7 in {', '.join(can_hit)}"
    elif near_hit:
        headline = f"One step off in {', '.join(near_hit)}"
    else:
        headline = "No immediate 7 breakpoints"

    hints: list[str] = []
    if locks:
        hints.append(f"This pair already guarantees 7s in {', '.join(locks)}.")
    if can_hit:
        hints.append(f"High-roll path to 7 exists in {', '.join(can_hit)}.")
    if near_hit:
        hints.append(
            f"Next breakpoint is close in {', '.join(near_hit)}: bring in another 7 or keep the strongest kitten."
        )
    if stalled:
        hints.append(
            f"These stats are still below the next breakpoint: {', '.join(stalled)}."
        )
    if len(upgrade_now) >= 4:
        hints.append("Good progression pair: multiple stats can improve immediately.")
    elif len(upgrade_now) <= 1:
        hints.append("Weak progression pair: very few stats can improve from the better parent.")

    sum_lo = sum(row["lo"] for row in stat_rows)
    sum_hi = sum(row["hi"] for row in stat_rows)
    avg_expected = sum(row["expected"] for row in stat_rows) / len(STAT_NAMES)

    return {
        "headline": headline,
        "hints": hints,
        "locks": locks,
        "can_hit": can_hit,
        "near_hit": near_hit,
        "stalled": stalled,
        "rows": stat_rows,
        "sum_range": (sum_lo, sum_hi),
        "avg_expected": avg_expected,
        "better_stat_chance": better_stat_chance,
    }
