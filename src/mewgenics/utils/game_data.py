"""GPAK loading and game data reload."""
import os

from save_parser import GameData, FurnitureDefinition, set_visual_mut_data, set_class_stat_mods

from mewgenics.utils.config import _load_app_config, _save_app_config, _candidate_gpak_paths
from mewgenics.utils.abilities import _load_ability_descriptions, _ABILITY_DESC
from mewgenics.utils.ability_icons import _reload_ability_icon_cache
from mewgenics.utils.tags import _load_game_tag_definitions


# ── Mutable module state ─────────────────────────────────────────────────────

_GPAK_SEARCH_PATHS: list[str] = []
_GPAK_PATH: str | None = None
_VISUAL_MUT_DATA: dict = {}
_FURNITURE_DATA: dict[str, FurnitureDefinition] = {}
_GAME_TAG_DEFS: list[dict] = []
_SWF_SYMBOL_DATA: dict[str, list[str]] = {}


def _reload_game_data():
    global _GPAK_SEARCH_PATHS, _GPAK_PATH, _VISUAL_MUT_DATA, _FURNITURE_DATA, _GAME_TAG_DEFS, _SWF_SYMBOL_DATA
    _GPAK_SEARCH_PATHS = _candidate_gpak_paths()
    _GPAK_PATH = next((p for p in _GPAK_SEARCH_PATHS if os.path.exists(p)), None)
    _ABILITY_DESC.clear()
    _ABILITY_DESC.update(_load_ability_descriptions(_GPAK_PATH))
    game_data = GameData.from_gpak(_GPAK_PATH)
    _VISUAL_MUT_DATA = game_data.visual_mutation_data
    _FURNITURE_DATA = game_data.furniture_data
    _GAME_TAG_DEFS = list(game_data.game_tag_data)
    _SWF_SYMBOL_DATA = {key: list(value) for key, value in game_data.swf_symbol_data.items()}
    set_visual_mut_data(_VISUAL_MUT_DATA)
    set_class_stat_mods(game_data.class_stat_mods)
    _load_game_tag_definitions(_GAME_TAG_DEFS)
    _reload_ability_icon_cache(_GPAK_PATH)


def _set_gpak_path(path: str):
    cleaned = path.strip()
    if not cleaned:
        return
    data = _load_app_config()
    data["gpak_path"] = cleaned
    _save_app_config(data)
    _reload_game_data()


def get_gpak_path() -> str | None:
    return _GPAK_PATH


def get_visual_mut_data() -> dict:
    return _VISUAL_MUT_DATA


def get_furniture_data() -> dict[str, FurnitureDefinition]:
    return _FURNITURE_DATA


def get_game_tag_data() -> list[dict]:
    return list(_GAME_TAG_DEFS)


def get_swf_symbol_data() -> dict[str, list[str]]:
    return {key: list(value) for key, value in _SWF_SYMBOL_DATA.items()}
