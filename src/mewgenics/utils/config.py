"""Application configuration persistence and coercion helpers."""
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Optional

from PySide6.QtWidgets import QWidget, QSplitter
from PySide6.QtCore import QByteArray

from mewgenics.constants import _ZOOM_MIN, _ZOOM_MAX
from mewgenics.utils.paths import (
    APP_CONFIG_PATH, APPDATA_CONFIG_DIR, APPDATA_SAVE_DIR,
    _app_dir, _bundle_dir, _steam_library_paths,
)

logger = logging.getLogger("mewgenics.config")


def _atomic_write_json(path: str, data: dict):
    parent = os.path.dirname(path) or "."
    tmp_fd = None
    tmp_path = None
    try:
        os.makedirs(parent, exist_ok=True)
        tmp_fd, tmp_path = tempfile.mkstemp(
            prefix=".settings.", suffix=".tmp", dir=parent
        )
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            tmp_fd = None
            json.dump(data, f, indent=2, sort_keys=True)
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                pass
        os.replace(tmp_path, path)
        tmp_path = None
    except OSError:
        logger.warning("failed to write app config at %s", path, exc_info=True)
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


def _load_app_config() -> dict:
    if not os.path.exists(APP_CONFIG_PATH):
        return {}
    try:
        with open(APP_CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_app_config(data: dict):
    try:
        _atomic_write_json(APP_CONFIG_PATH, data if isinstance(data, dict) else {})
    except Exception:
        logger.warning("failed to persist app config at %s", APP_CONFIG_PATH, exc_info=True)


# ── Coercion helpers ─────────────────────────────────────────────────────────

def _coerce_int(value, default: int, min_value: int | None = None, max_value: int | None = None) -> int:
    try:
        result = int(float(value))
    except (TypeError, ValueError):
        result = default
    if min_value is not None:
        result = max(min_value, result)
    if max_value is not None:
        result = min(max_value, result)
    return result


def _coerce_float(value, default: float, min_value: float | None = None, max_value: float | None = None) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        result = default
    if min_value is not None:
        result = max(min_value, result)
    if max_value is not None:
        result = min(max_value, result)
    return result


def _coerce_bool(value, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes", "on"}:
            return True
        if lowered in {"false", "0", "no", "off"}:
            return False
    if value is None:
        return default
    return bool(value)


# ── Save / gpak path helpers ────────────────────────────────────────────────

def _saved_gpak_path() -> str:
    data = _load_app_config()
    value = data.get("gpak_path", "")
    return value.strip() if isinstance(value, str) else ""


def _gpak_search_start_dir() -> str:
    """Return the best starting directory for locating resources.gpak."""
    saved_path = _saved_gpak_path()
    if saved_path:
        saved_dir = os.path.dirname(saved_path)
        if saved_dir and os.path.isdir(saved_dir):
            return saved_dir

    save_root = _save_root_dir()
    if save_root and os.path.isdir(save_root):
        return save_root

    candidates = [
        os.path.join(
            os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
            "Steam", "steamapps", "common", "Mewgenics",
        ),
        os.path.join(
            os.environ.get("ProgramFiles", r"C:\Program Files"),
            "Steam", "steamapps", "common", "Mewgenics",
        ),
        r"D:\Games\Mewgenics",
        os.getcwd(),
        _app_dir(),
        _bundle_dir(),
    ]
    for path in candidates:
        if path and os.path.isdir(path):
            return path
    return str(Path.home())


def _saved_save_dir() -> str:
    data = _load_app_config()
    value = data.get("save_dir", "")
    return value.strip() if isinstance(value, str) else ""


def _save_root_dir() -> str:
    return _saved_save_dir() or APPDATA_SAVE_DIR


def _saved_default_save() -> Optional[str]:
    """Get the default save file path, if one is configured."""
    data = _load_app_config()
    value = data.get("default_save", "")
    if isinstance(value, str):
        value = value.strip()
        if value and os.path.exists(value):
            return value
    return None


def _set_default_save(path: Optional[str]):
    """Set or clear the default save file path."""
    data = _load_app_config()
    if path:
        data["default_save"] = path
    else:
        data.pop("default_save", None)
    _save_app_config(data)


def _saved_last_save() -> Optional[str]:
    """Return the most recently loaded save path, or None if unavailable."""
    data = _load_app_config()
    value = data.get("last_save", "")
    if isinstance(value, str):
        value = value.strip()
        if value and os.path.isfile(value):
            return value
    return None


def _set_last_save(path: str):
    """Persist the most recently loaded save path."""
    data = _load_app_config()
    data["last_save"] = path
    _save_app_config(data)


def _set_save_dir(path: str):
    cleaned = path.strip()
    if not cleaned:
        return
    data = _load_app_config()
    data["save_dir"] = cleaned
    _save_app_config(data)


def _saved_zoom_percent(default: int = 100) -> int:
    data = _load_app_config()
    return _coerce_int(data.get("zoom_percent"), default, min_value=_ZOOM_MIN, max_value=_ZOOM_MAX)


def _set_zoom_percent(percent: int):
    data = _load_app_config()
    data["zoom_percent"] = _coerce_int(percent, 100, min_value=_ZOOM_MIN, max_value=_ZOOM_MAX)
    _save_app_config(data)


def _saved_font_size_offset(default: int = 0) -> int:
    data = _load_app_config()
    return _coerce_int(data.get("font_size_offset"), default, min_value=-6, max_value=12)


def _set_font_size_offset_config(offset: int):
    data = _load_app_config()
    data["font_size_offset"] = _coerce_int(offset, 0, min_value=-6, max_value=12)
    _save_app_config(data)


def _saved_last_seen_version() -> str:
    data = _load_app_config()
    value = data.get("last_seen_version", "")
    return value.strip() if isinstance(value, str) else ""


def _set_last_seen_version(version: str):
    data = _load_app_config()
    cleaned = version.strip() if isinstance(version, str) else ""
    if cleaned:
        data["last_seen_version"] = cleaned
    else:
        data.pop("last_seen_version", None)
    _save_app_config(data)


def _saved_show_getting_started_prompt(default: bool = True) -> bool:
    data = _load_app_config()
    return _coerce_bool(data.get("show_getting_started_prompt"), default)


def _set_show_getting_started_prompt(enabled: bool):
    data = _load_app_config()
    data["show_getting_started_prompt"] = bool(enabled)
    _save_app_config(data)


def _saved_accessibility_preset(default: str = "Default") -> str:
    data = _load_app_config()
    value = data.get("accessibility_preset", default)
    return value.strip() if isinstance(value, str) and value.strip() else default


def _set_accessibility_preset(name: str):
    data = _load_app_config()
    cleaned = name.strip() if isinstance(name, str) else ""
    if cleaned:
        data["accessibility_preset"] = cleaned
    else:
        data.pop("accessibility_preset", None)
    _save_app_config(data)


def _saved_total_stats_display(default: bool = False) -> bool:
    data = _load_app_config()
    return _coerce_bool(data.get("show_total_stats"), default)


def _set_total_stats_display(enabled: bool):
    data = _load_app_config()
    data["show_total_stats"] = bool(enabled)
    _save_app_config(data)


def _saved_stat_icon_mode(default: bool = False) -> bool:
    data = _load_app_config()
    return _coerce_bool(data.get("show_stat_icons"), default)


def _set_stat_icon_mode(enabled: bool):
    data = _load_app_config()
    data["show_stat_icons"] = bool(enabled)
    _save_app_config(data)


def _saved_roster_visual_mode(default: bool = False) -> bool:
    data = _load_app_config()
    return _coerce_bool(data.get("roster_visual_mode"), default)


def _set_roster_visual_mode(enabled: bool):
    data = _load_app_config()
    data["roster_visual_mode"] = bool(enabled)
    _save_app_config(data)


# ── Tag color palette persistence ──────────────────────────────────────────

def _normalize_tag_color_hex(value, default: str = "") -> str:
    text = str(value or "").strip()
    if not text:
        return default
    if text.lower().startswith("0x"):
        text = text[2:]
    if text.startswith("#"):
        text = text[1:]
    if len(text) == 3:
        text = "".join(ch * 2 for ch in text)
    if len(text) != 6:
        return default
    try:
        int(text, 16)
    except ValueError:
        return default
    return f"#{text.lower()}"


def _normalize_tag_color_history(colors, limit: int = 12) -> list[str]:
    max_items = _coerce_int(limit, 12, min_value=1, max_value=64)

    if not isinstance(colors, list):
        return []

    history: list[str] = []
    seen: set[str] = set()
    for value in colors:
        color = _normalize_tag_color_hex(value)
        if not color or color in seen:
            continue
        seen.add(color)
        history.append(color)
        if len(history) >= max_items:
            break
    return history


def _saved_tag_color_history(default: Optional[list[str]] = None, limit: int = 12) -> list[str]:
    data = _load_app_config()
    history = _normalize_tag_color_history(data.get("tag_color_history", []), limit=limit)
    if history:
        return history
    if default:
        return _normalize_tag_color_history(list(default), limit=limit)
    return []


def _set_tag_color_history(colors: list[str], limit: int = 12):
    data = _load_app_config()
    history = _normalize_tag_color_history(colors, limit=limit)
    if history:
        data["tag_color_history"] = history
    else:
        data.pop("tag_color_history", None)
    _save_app_config(data)


def _remember_tag_color_history(color: str, limit: int = 12) -> list[str]:
    history = _saved_tag_color_history(limit=limit)
    normalized = _normalize_tag_color_hex(color)
    if not normalized:
        return history
    history = [normalized] + [c for c in history if c != normalized]
    history = history[:_coerce_int(limit, 12, min_value=1, max_value=64)]
    _set_tag_color_history(history, limit=limit)
    return history


def _save_current_view(name: str):
    """Persist the current view name to settings.json."""
    data = _load_app_config()
    data["current_view"] = name
    _save_app_config(data)


def _load_current_view() -> str:
    """Return the last saved view name, defaulting to 'table'."""
    return _load_app_config().get("current_view", "table")


def _candidate_gpak_paths() -> list[str]:
    candidates: list[str] = []

    env_path = os.environ.get("MEWGENICS_GPAK_PATH", "").strip()
    if env_path:
        candidates.append(env_path)

    direct_paths = [
        os.path.join(
            os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
            "Steam", "steamapps", "common", "Mewgenics", "resources.gpak",
        ),
        os.path.join(
            os.environ.get("ProgramFiles", r"C:\Program Files"),
            "Steam", "steamapps", "common", "Mewgenics", "resources.gpak",
        ),
        r"D:\Games\Mewgenics\resources.gpak",
        os.path.join(os.getcwd(), "resources.gpak"),
        os.path.join(_app_dir(), "resources.gpak"),
        os.path.join(_bundle_dir(), "resources.gpak"),
        "/mnt/c/Program Files (x86)/Steam/steamapps/common/Mewgenics/resources.gpak",
        "/mnt/c/Program Files/Steam/steamapps/common/Mewgenics/resources.gpak",
    ]
    candidates.extend(direct_paths)

    for library in _steam_library_paths():
        candidates.append(os.path.join(library, "steamapps", "common", "Mewgenics", "resources.gpak"))

    save_root_gpak = os.path.join(_save_root_dir(), "resources.gpak")
    if save_root_gpak:
        candidates.append(save_root_gpak)

    saved_path = _saved_gpak_path()
    if saved_path:
        candidates.append(saved_path)

    ordered: list[str] = []
    seen: set[str] = set()
    for path in candidates:
        norm = os.path.normcase(os.path.normpath(path))
        if norm in seen:
            continue
        seen.add(norm)
        ordered.append(path)
    return ordered


def find_save_files() -> list[str]:
    saves = []
    base  = Path(_save_root_dir())
    if not base.is_dir():
        return saves
    for profile in base.iterdir():
        saves_dir = profile / "saves"
        if saves_dir.is_dir():
            saves.extend(str(p) for p in saves_dir.glob("*.sav"))
    saves.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return saves


# ── Optimizer flags ──────────────────────────────────────────────────────────

def _saved_optimizer_flag(name: str, default: bool = False) -> bool:
    data = _load_app_config()
    value = data.get("optimizer_flags", {}).get(name, default)
    return bool(value)


def _set_optimizer_flag(name: str, value: bool):
    data = _load_app_config()
    flags = data.get("optimizer_flags")
    if not isinstance(flags, dict):
        flags = {}
    flags[name] = bool(value)
    data["optimizer_flags"] = flags
    _save_app_config(data)


def _saved_room_optimizer_auto_recalc(default: bool = False) -> bool:
    return _saved_optimizer_flag("room_optimizer_auto_recalc", default)


def _set_room_optimizer_auto_recalc(enabled: bool):
    _set_optimizer_flag("room_optimizer_auto_recalc", enabled)


def _saved_manual_scoring_auto_calc(default: bool = True) -> bool:
    return _saved_optimizer_flag("manual_scoring_auto_calc", default)


def _set_manual_scoring_auto_calc(enabled: bool):
    _set_optimizer_flag("manual_scoring_auto_calc", enabled)


# ── UI state persistence ─────────────────────────────────────────────────────

def _load_ui_state(key: str) -> dict:
    data = _load_app_config()
    state = data.get(key, {})
    return state if isinstance(state, dict) else {}


def _save_ui_state(key: str, state: dict):
    try:
        data = _load_app_config()
        data[key] = state if isinstance(state, dict) else {}
        _save_app_config(data)
    except Exception:
        pass


# ── Splitter state persistence ───────────────────────────────────────────────

def _load_splitter_states() -> dict[str, str]:
    data = _load_app_config()
    state = data.get("splitter_states", {})
    return state if isinstance(state, dict) else {}


def _save_splitter_states(states: dict[str, str]):
    try:
        data = _load_app_config()
        data["splitter_states"] = states if isinstance(states, dict) else {}
        _save_app_config(data)
    except Exception:
        pass


def _restore_splitter_state(splitter: QSplitter):
    key = splitter.objectName().strip()
    if not key:
        return
    encoded = _load_splitter_states().get(key)
    if not encoded:
        return
    try:
        splitter.restoreState(QByteArray.fromBase64(encoded.encode("ascii")))
    except Exception:
        pass


def _save_splitter_state(splitter: QSplitter):
    key = splitter.objectName().strip()
    if not key:
        return
    try:
        states = _load_splitter_states()
        states[key] = splitter.saveState().toBase64().data().decode("ascii")
        _save_splitter_states(states)
    except Exception:
        pass


def _bind_splitter_persistence(root: Optional[QWidget]):
    if root is None:
        return
    for splitter in root.findChildren(QSplitter):
        key = splitter.objectName().strip()
        if not key or splitter.property("_splitter_persist_bound"):
            continue
        splitter.setProperty("_splitter_persist_bound", True)
        _restore_splitter_state(splitter)
        splitter.splitterMoved.connect(lambda *_ , s=splitter: _save_splitter_state(s))



# ── Window geometry persistence ──────────────────────────────────────────────

def _save_window_geometry(geometry_b64: str):
    """Persist the main window geometry as a base64-encoded string."""
    try:
        data = _load_app_config()
        data["window_geometry"] = geometry_b64
        _save_app_config(data)
    except Exception:
        pass


def _load_window_geometry() -> Optional[str]:
    """Return the saved window geometry base64 string, or None if absent."""
    return _load_app_config().get("window_geometry") or None
