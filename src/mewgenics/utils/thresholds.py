"""Breeding threshold preferences and adaptive thresholds."""
from save_parser import (
    Cat,
    EXCEPTIONAL_SUM_THRESHOLD as _BASE_EXCEPTIONAL,
    DONATION_SUM_THRESHOLD as _BASE_DONATION,
    DONATION_MAX_TOP_STAT as _BASE_TOP_STAT,
)
from mewgenics.utils.config import (
    _load_app_config, _save_app_config, _coerce_int, _coerce_float, _coerce_bool,
)
from mewgenics.utils.cat_analysis import _cat_base_sum


# ── Mutable threshold globals (updated by _apply_threshold_preferences) ──────
EXCEPTIONAL_SUM_THRESHOLD = int(_BASE_EXCEPTIONAL)
DONATION_SUM_THRESHOLD = int(_BASE_DONATION)
DONATION_MAX_TOP_STAT = int(_BASE_TOP_STAT)
DONATION_MISSING_PLANNER_TRAITS = False

# ── Score source ──────────────────────────────────────────────────────────────
# "base_sum" compares raw base-stat sums against the int thresholds above.
# "detailed" compares the Detailed Scoring total against the float thresholds
# below, using the latest cache populated by the Detailed Scoring view.
SCORE_SOURCE: str = "base_sum"
DETAILED_EXCEPTIONAL_THRESHOLD: float = 20.0
DETAILED_DONATION_THRESHOLD: float = -5.0

_DETAILED_SCORES: dict[int, float] = {}

_DONATION_PLANNER_TRAITS: tuple[dict, ...] = ()

_THRESHOLD_CONFIG_KEY = "threshold_preferences"
_THRESHOLD_DEFAULTS = {
    "exceptional_sum_threshold": int(_BASE_EXCEPTIONAL),
    "donation_sum_threshold": int(_BASE_DONATION),
    "donation_max_top_stat": int(_BASE_TOP_STAT),
    "donation_missing_planner_traits": False,
    "adaptive_enabled": False,
    "adaptive_reference_avg_sum": 28.0,
    "adaptive_curve_strength": 0.2,
    "score_source": "base_sum",
    "detailed_exceptional_threshold": 20.0,
    "detailed_donation_threshold": -5.0,
}

_THRESHOLD_PREFERENCES = dict(_THRESHOLD_DEFAULTS)


def _normalize_exceptional_and_donation(data: dict) -> dict:
    exceptional = int(data.get("exceptional_sum_threshold", _THRESHOLD_DEFAULTS["exceptional_sum_threshold"]))
    donation = int(data.get("donation_sum_threshold", _THRESHOLD_DEFAULTS["donation_sum_threshold"]))
    if exceptional < donation:
        exceptional = donation
    data["exceptional_sum_threshold"] = exceptional
    data["donation_sum_threshold"] = donation
    return data


def _normalize_threshold_preferences(data: dict | None) -> dict:
    data = data if isinstance(data, dict) else {}
    normalized = {
        "exceptional_sum_threshold": _coerce_int(
            data.get("exceptional_sum_threshold"),
            _THRESHOLD_DEFAULTS["exceptional_sum_threshold"],
            min_value=0,
        ),
        "donation_sum_threshold": _coerce_int(
            data.get("donation_sum_threshold"),
            _THRESHOLD_DEFAULTS["donation_sum_threshold"],
            min_value=0,
        ),
        "donation_max_top_stat": _coerce_int(
            data.get("donation_max_top_stat"),
            _THRESHOLD_DEFAULTS["donation_max_top_stat"],
            min_value=0,
        ),
        "donation_missing_planner_traits": _coerce_bool(
            data.get("donation_missing_planner_traits"),
            _THRESHOLD_DEFAULTS["donation_missing_planner_traits"],
        ),
        "adaptive_enabled": _coerce_bool(
            data.get("adaptive_enabled"),
            _THRESHOLD_DEFAULTS["adaptive_enabled"],
        ),
        "adaptive_reference_avg_sum": _coerce_float(
            data.get("adaptive_reference_avg_sum"),
            _THRESHOLD_DEFAULTS["adaptive_reference_avg_sum"],
            min_value=0.0,
        ),
        "adaptive_curve_strength": _coerce_float(
            data.get("adaptive_curve_strength"),
            _THRESHOLD_DEFAULTS["adaptive_curve_strength"],
            min_value=0.0,
        ),
        "score_source": (
            "detailed"
            if str(data.get("score_source", _THRESHOLD_DEFAULTS["score_source"])).strip().lower() == "detailed"
            else "base_sum"
        ),
        "detailed_exceptional_threshold": _coerce_float(
            data.get("detailed_exceptional_threshold"),
            _THRESHOLD_DEFAULTS["detailed_exceptional_threshold"],
        ),
        "detailed_donation_threshold": _coerce_float(
            data.get("detailed_donation_threshold"),
            _THRESHOLD_DEFAULTS["detailed_donation_threshold"],
        ),
    }
    if normalized["detailed_exceptional_threshold"] < normalized["detailed_donation_threshold"]:
        normalized["detailed_exceptional_threshold"] = normalized["detailed_donation_threshold"]
    return _normalize_exceptional_and_donation(normalized)


def _load_threshold_preferences() -> dict:
    data = _load_app_config()
    prefs = _normalize_threshold_preferences(data.get(_THRESHOLD_CONFIG_KEY))
    return prefs


def _save_threshold_preferences(prefs: dict) -> bool:
    normalized = _normalize_threshold_preferences(prefs)
    data = _load_app_config()
    data[_THRESHOLD_CONFIG_KEY] = normalized
    _save_app_config(data)
    return True


def _set_donation_planner_traits(traits: list[dict] | None):
    global _DONATION_PLANNER_TRAITS
    normalized: list[dict] = []
    for trait in traits or []:
        if not isinstance(trait, dict):
            continue
        category = str(trait.get("category") or "").strip()
        key = str(trait.get("key") or "").strip().lower()
        if not category or not key:
            continue
        try:
            weight = int(trait.get("weight", 5) or 5)
        except (TypeError, ValueError):
            weight = 5
        normalized.append({
            "category": category,
            "key": key,
            "display": str(trait.get("display") or trait.get("name") or key).strip() or key,
            "weight": weight,
        })
    _DONATION_PLANNER_TRAITS = tuple(normalized)


def _donation_planner_traits() -> tuple[dict, ...]:
    return _DONATION_PLANNER_TRAITS


def _effective_thresholds_for_cats(
    prefs: dict | None = None,
    cats: list[Cat] | None = None,
) -> tuple[int, int, int, float]:
    prefs = _normalize_threshold_preferences(prefs or _THRESHOLD_PREFERENCES)
    alive = [cat for cat in (cats or []) if getattr(cat, "status", None) != "Gone"]
    avg_sum = sum(_cat_base_sum(cat) for cat in alive) / len(alive) if alive else 0.0
    exceptional = prefs["exceptional_sum_threshold"]
    donation = prefs["donation_sum_threshold"]
    if prefs["adaptive_enabled"] and alive:
        delta = avg_sum - prefs["adaptive_reference_avg_sum"]
        shift = int(round(delta * prefs["adaptive_curve_strength"] * 0.25))
        exceptional = max(0, exceptional + shift)
        donation = max(0, donation + shift)
    return exceptional, donation, prefs["donation_max_top_stat"], avg_sum


def _apply_threshold_preferences(prefs: dict | None = None, cats: list[Cat] | None = None):
    global _THRESHOLD_PREFERENCES, EXCEPTIONAL_SUM_THRESHOLD, DONATION_SUM_THRESHOLD, DONATION_MAX_TOP_STAT, DONATION_MISSING_PLANNER_TRAITS
    global SCORE_SOURCE, DETAILED_EXCEPTIONAL_THRESHOLD, DETAILED_DONATION_THRESHOLD
    normalized = _normalize_threshold_preferences(prefs or _load_threshold_preferences())
    _THRESHOLD_PREFERENCES = normalized
    DONATION_MISSING_PLANNER_TRAITS = bool(normalized.get("donation_missing_planner_traits"))
    EXCEPTIONAL_SUM_THRESHOLD, DONATION_SUM_THRESHOLD, DONATION_MAX_TOP_STAT, _ = _effective_thresholds_for_cats(normalized, cats)
    SCORE_SOURCE = str(normalized.get("score_source", "base_sum"))
    DETAILED_EXCEPTIONAL_THRESHOLD = float(normalized.get("detailed_exceptional_threshold", 20.0))
    DETAILED_DONATION_THRESHOLD = float(normalized.get("detailed_donation_threshold", -5.0))


def _set_detailed_scores(scores: dict[int, float] | None):
    """Install the latest Detailed Scoring totals keyed by id(cat).

    Called from the Detailed Scoring view after each recompute finishes.
    """
    global _DETAILED_SCORES
    _DETAILED_SCORES = dict(scores or {})


def _get_detailed_score(cat: Cat) -> float | None:
    """Return the cached Detailed Scoring total for a cat, or None if unavailable."""
    return _DETAILED_SCORES.get(id(cat))


def _detailed_scores_ready() -> bool:
    return bool(_DETAILED_SCORES)


def _current_threshold_summary(cats: list[Cat] | None = None) -> dict:
    exceptional, donation, top_stat, avg_sum = _effective_thresholds_for_cats(_THRESHOLD_PREFERENCES, cats)
    return {
        "exceptional": exceptional,
        "donation": donation,
        "top_stat": top_stat,
        "avg_sum": avg_sum,
        "adaptive_enabled": bool(_THRESHOLD_PREFERENCES.get("adaptive_enabled")),
        "adaptive_reference_avg_sum": float(_THRESHOLD_PREFERENCES.get("adaptive_reference_avg_sum", 0.0)),
        "adaptive_curve_strength": float(_THRESHOLD_PREFERENCES.get("adaptive_curve_strength", 0.0)),
        "donation_missing_planner_traits": bool(_THRESHOLD_PREFERENCES.get("donation_missing_planner_traits")),
        "base_exceptional": int(_THRESHOLD_PREFERENCES.get("exceptional_sum_threshold", _THRESHOLD_DEFAULTS["exceptional_sum_threshold"])),
        "base_donation": int(_THRESHOLD_PREFERENCES.get("donation_sum_threshold", _THRESHOLD_DEFAULTS["donation_sum_threshold"])),
    }
