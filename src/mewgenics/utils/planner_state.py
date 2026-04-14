"""Planner blob persistence, mutation class profiles, foundation pairs, and offspring selection."""
import json
import logging
import os
import tempfile
from typing import Optional

from save_parser import Cat, STAT_NAMES

from mewgenics.utils.paths import _planner_state_path
from mewgenics.utils.config import _load_app_config, _save_app_config
from mewgenics.utils.cat_analysis import _cat_uid


logger = logging.getLogger("mewgenics.planner_state")

_PLANNER_STATE_GLOBAL_MIRROR_KEYS = {"room_optimizer_state"}

MUTATION_CLASS_MODES = ("best_pairs", "melee", "ranged", "magic")
ROOM_OPTIMIZER_MODES = MUTATION_CLASS_MODES + ("fallback",)
MUTATION_CLASS_LABELS = {
    "best_pairs": "Best Pairs",
    "melee": "Melee",
    "ranged": "Ranged",
    "magic": "Magic",
    "fallback": "Fallback",
}
DEFAULT_MUTATION_CLASS_STAT_PRIORITY = {
    "best_pairs": list(STAT_NAMES),
    "melee": ["STR", "CON", "SPD", "DEX", "LCK", "CHA", "INT"],
    "ranged": ["DEX", "SPD", "LCK", "INT", "CON", "STR", "CHA"],
    "magic": ["INT", "CHA", "DEX", "SPD", "LCK", "CON", "STR"],
}


class _PlannerStateReadError(Exception):
    """Raised when a planner-state file exists but cannot be read or parsed.

    Distinguished from a missing file so callers can refuse to clobber
    other planners' data with an empty blob on transient I/O failures.
    """


def _load_planner_state_blob(save_path: Optional[str]) -> dict:
    """Return the full per-save planner-state dict.

    Returns ``{}`` if the file does not exist yet (fresh save).
    Raises ``_PlannerStateReadError`` if the file exists but cannot
    be parsed — the caller must NOT proceed to overwrite in that case,
    or it will silently destroy the other planner's data.
    """
    if not save_path:
        return {}
    path = _planner_state_path(save_path)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as exc:
        logger.warning("planner_state.json unreadable at %s: %s", path, exc)
        raise _PlannerStateReadError(str(exc)) from exc
    return data if isinstance(data, dict) else {}


def _save_planner_state_blob(save_path: Optional[str], blob: dict):
    """Atomically write the planner-state blob.

    Writes to a sibling temp file and ``os.replace()``s into place so
    an interrupted or failing write never leaves a truncated file on
    disk. A truncated file would parse as empty on next read, causing
    the next save to silently drop every key it doesn't write.
    """
    if not save_path:
        return
    if not isinstance(blob, dict):
        blob = {}
    path = _planner_state_path(save_path)
    parent = os.path.dirname(path) or "."
    tmp_fd = None
    tmp_path = None
    try:
        tmp_fd, tmp_path = tempfile.mkstemp(
            prefix=".planner_state.", suffix=".tmp", dir=parent
        )
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            tmp_fd = None  # now owned by f
            json.dump(blob, f, indent=2, sort_keys=True)
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                pass
        os.replace(tmp_path, path)
        tmp_path = None
    except OSError as exc:
        logger.warning("failed to write planner_state.json at %s: %s", path, exc)
    finally:
        if tmp_fd is not None:
            try:
                os.close(tmp_fd)
            except OSError:
                pass
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass


def _load_planner_state_value(key: str, default=None, save_path: Optional[str] = None):
    if save_path:
        if key in _PLANNER_STATE_GLOBAL_MIRROR_KEYS:
            try:
                data = _load_app_config()
                value = data.get(key)
                if value not in (None, {}, []):
                    try:
                        blob = _load_planner_state_blob(save_path)
                    except _PlannerStateReadError:
                        return value
                    if blob.get(key) != value:
                        blob[key] = value
                        _save_planner_state_blob(save_path, blob)
                    return value
            except Exception:
                pass
        try:
            blob = _load_planner_state_blob(save_path)
        except _PlannerStateReadError:
            return default
        if key in blob:
            return blob[key]
        try:
            data = _load_app_config()
            if key in data:
                value = data.get(key)
                blob[key] = value
                _save_planner_state_blob(save_path, blob)
                return value
        except Exception:
            return default
        return default
    try:
        data = _load_app_config()
        return data.get(key, default)
    except Exception:
        return default


def _save_planner_state_value(key: str, value, save_path: Optional[str] = None, *, mirror_global: bool = False):
    try:
        if save_path:
            try:
                blob = _load_planner_state_blob(save_path)
            except _PlannerStateReadError:
                # Refuse to overwrite. Reading the existing file failed,
                # so we cannot safely merge — writing would clobber the
                # other planner's keys. Drop this save; the next
                # successful save will retry from fresh in-memory state.
                logger.error(
                    "refusing to save %r — existing planner_state.json could not be "
                    "read and writing would destroy the other planner's data",
                    key,
                )
                return
            blob[key] = value
            _save_planner_state_blob(save_path, blob)
            if mirror_global or key in _PLANNER_STATE_GLOBAL_MIRROR_KEYS:
                data = _load_app_config()
                data[key] = value
                _save_app_config(data)
            return
        data = _load_app_config()
        data[key] = value
        _save_app_config(data)
    except Exception:
        pass


def _mutation_class_label(mode: str) -> str:
    return MUTATION_CLASS_LABELS.get(str(mode or "").strip().lower(), str(mode or "").strip() or "Unknown")


def _normalize_mutation_traits(traits) -> list[dict]:
    normalized: list[dict] = []
    if not isinstance(traits, list):
        return normalized
    for trait in traits:
        if not isinstance(trait, dict):
            continue
        category = str(trait.get("category") or "").strip()
        key = str(trait.get("key") or "").strip().lower()
        display = str(trait.get("display") or "").strip() or key
        if not category or not key:
            continue
        try:
            weight = int(trait.get("weight", 5))
        except (TypeError, ValueError):
            weight = 5
        normalized.append({
            "category": category,
            "key": key,
            "display": display,
            "weight": max(-10, min(10, weight)),
        })
    return normalized


def _normalize_stat_priority(order, *, fallback_mode: str | None = None) -> list[str]:
    valid = [str(stat).strip().upper() for stat in STAT_NAMES]
    chosen: list[str] = []
    for stat in order or []:
        key = str(stat or "").strip().upper()
        if key in valid and key not in chosen:
            chosen.append(key)
    if fallback_mode in DEFAULT_MUTATION_CLASS_STAT_PRIORITY:
        for stat in DEFAULT_MUTATION_CLASS_STAT_PRIORITY[fallback_mode]:
            if stat not in chosen:
                chosen.append(stat)
    else:
        for stat in valid:
            if stat not in chosen:
                chosen.append(stat)
    return chosen


def _default_mutation_mode_profiles() -> dict[str, dict]:
    return {
        mode: {
            "traits": [],
            "stat_priority": list(DEFAULT_MUTATION_CLASS_STAT_PRIORITY[mode]),
        }
        for mode in MUTATION_CLASS_MODES
    }


def _normalize_mutation_mode_profiles(data=None, *, legacy_traits=None) -> dict[str, dict]:
    defaults = _default_mutation_mode_profiles()
    source = data if isinstance(data, dict) else {}
    normalized: dict[str, dict] = {}
    for mode in MUTATION_CLASS_MODES:
        raw_profile = source.get(mode, {})
        raw_profile = raw_profile if isinstance(raw_profile, dict) else {}
        normalized[mode] = {
            "traits": _normalize_mutation_traits(raw_profile.get("traits", [])),
            "stat_priority": _normalize_stat_priority(raw_profile.get("stat_priority", []), fallback_mode=mode),
        }
    if legacy_traits and not normalized["best_pairs"]["traits"]:
        normalized["best_pairs"]["traits"] = _normalize_mutation_traits(legacy_traits)
    for mode in MUTATION_CLASS_MODES:
        if not normalized[mode]["stat_priority"]:
            normalized[mode]["stat_priority"] = list(defaults[mode]["stat_priority"])
    return normalized


# ── Foundation pairs ─────────────────────────────────────────────────────────

def _default_perfect_planner_foundation_pairs(count: int = 4) -> list[dict]:
    count = max(1, min(12, int(count or 4)))
    return [
        {"cat_a_uid": "", "cat_b_uid": "", "using": False}
        for _ in range(count)
    ]


def _load_perfect_planner_foundation_pairs(save_path: Optional[str] = None) -> list[dict]:
    try:
        cfg = _load_planner_state_value("perfect_planner_foundation_pairs", [], save_path=save_path)
        if isinstance(cfg, list):
            out: list[dict] = []
            for slot_data in cfg[:12]:
                slot = slot_data if isinstance(slot_data, dict) else {}
                out.append({
                    "cat_a_uid": str(slot.get("cat_a_uid") or "").strip().lower(),
                    "cat_b_uid": str(slot.get("cat_b_uid") or "").strip().lower(),
                    "using": bool(slot.get("using", False)),
                })
            if out:
                return out
    except Exception:
        pass
    return _default_perfect_planner_foundation_pairs()


def _save_perfect_planner_foundation_pairs(config: list[dict], save_path: Optional[str] = None):
    try:
        normalized = []
        for slot in (config or [])[:12]:
            if not isinstance(slot, dict):
                continue
            normalized.append({
                "cat_a_uid": str(slot.get("cat_a_uid") or "").strip().lower(),
                "cat_b_uid": str(slot.get("cat_b_uid") or "").strip().lower(),
                "using": bool(slot.get("using", False)),
            })
        if not normalized:
            normalized = _default_perfect_planner_foundation_pairs()
        _save_planner_state_value("perfect_planner_foundation_pairs", normalized, save_path=save_path)
    except Exception:
        pass


# ── Offspring selection ──────────────────────────────────────────────────────

def _default_perfect_planner_selected_offspring() -> dict[str, str]:
    return {}


def _load_perfect_planner_selected_offspring(save_path: Optional[str] = None) -> dict[str, str]:
    try:
        cfg = _load_planner_state_value("perfect_planner_selected_offspring", {}, save_path=save_path)
        if isinstance(cfg, dict):
            normalized: dict[str, str] = {}
            for pair_key, child_uid in cfg.items():
                pair_key = str(pair_key or "").strip().lower()
                child_uid = str(child_uid or "").strip().lower()
                if pair_key and child_uid:
                    normalized[pair_key] = child_uid
            return normalized
    except Exception:
        pass
    return _default_perfect_planner_selected_offspring()


def _save_perfect_planner_selected_offspring(config: dict[str, str], save_path: Optional[str] = None):
    try:
        normalized: dict[str, str] = {}
        for pair_key, child_uid in (config or {}).items():
            pair_key = str(pair_key or "").strip().lower()
            child_uid = str(child_uid or "").strip().lower()
            if pair_key and child_uid:
                normalized[pair_key] = child_uid
        _save_planner_state_value("perfect_planner_selected_offspring", normalized, save_path=save_path)
    except Exception:
        pass


def _planner_pair_uid_key(cat_a: Cat, cat_b: Cat) -> str:
    a = _cat_uid(cat_a)
    b = _cat_uid(cat_b)
    if not a or not b:
        return ""
    left, right = sorted((a, b))
    return f"{left}|{right}"


def _planner_import_trait_display(trait: dict) -> str:
    display = str(trait.get("display", trait.get("name", "?"))).strip() or "?"
    return display.split("] ", 1)[-1]


def _planner_import_traits_summary(traits: "list[dict]", limit: int = 4) -> str:
    valid_traits = [trait for trait in traits if isinstance(trait, dict)]
    names: list[str] = []
    for trait in valid_traits[:limit]:
        display = _planner_import_trait_display(trait)
        weight = trait.get("weight", "?")
        names.append(f"{display}({weight})")
    summary = ", ".join(names)
    if len(valid_traits) > limit:
        summary += f" +{len(valid_traits) - limit} more"
    return summary


def _planner_import_traits_tooltip(traits: "list[dict]", *, empty_text: str) -> str:
    valid_traits = [trait for trait in traits if isinstance(trait, dict)]
    if not valid_traits:
        return empty_text
    lines = [f"Imported traits ({len(valid_traits)}):"]
    for trait in valid_traits:
        display = _planner_import_trait_display(trait)
        weight = trait.get("weight", "?")
        lines.append(f"- {display} ({weight})")
    return "\n".join(lines)
