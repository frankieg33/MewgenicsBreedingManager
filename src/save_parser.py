"""
Save parser and core data model for Mewgenics Breeding Manager.

Extracted from mewgenics_manager.py to enable independent testing and
separation of parsing/genetics logic from the Qt UI.
"""

from __future__ import annotations

import struct
import sqlite3
import lz4.block
import zlib
import re
import os
import math
import csv
import io
import html
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional
from collections import deque

from visual_mutation_catalog import load_visual_mutation_names, VISUAL_MUTATION_NAMES

logger = logging.getLogger("mewgenics.parser")

# ── Helpers ───────────────────────────────────────────────────────────────────

_JUNK_STRINGS = frozenset({"none", "null", "", "defaultmove", "default_move"})
_IDENT_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')

STAT_NAMES = ["STR", "DEX", "CON", "INT", "SPD", "CHA", "LCK"]

ROOM_DISPLAY = {
    "Floor1_Large":   "1st FL L",
    "Floor1_Small":   "1st FL R",
    "Floor2_Small":   "2nd FL L",
    "Floor2_Large":   "2nd FL R",
    "Attic":          "Attic",
}

ROOM_KEYS = tuple(ROOM_DISPLAY.keys())

FURNITURE_ROOM_STAT_KEYS = ("Appeal", "Comfort", "Stimulation", "Health", "Evolution")
FURNITURE_ROOM_STAT_LABELS = {
    "Appeal": "Appeal",
    "Comfort": "Comfort",
    "Stimulation": "Stimulation",
    "Health": "Health",
    "Evolution": "Mutation",
}

EXCEPTIONAL_SUM_THRESHOLD = 40
DONATION_SUM_THRESHOLD = 34
DONATION_MAX_TOP_STAT = 6

# Sexuality thresholds for the raw [0.0, 1.0] float stored at personality_anchor+40.
# The float encodes same-sex attraction: ~0.0 = straight, ~0.5 = bisexual, ~1.0 = gay.
# These cutoffs are a best guess derived from one save file (2026-03-21) and may need
# adjustment if edge cases surface in other saves.
# Observed distribution: straight cluster 0.00-0.10, bi spread 0.13-0.90, gay cluster 0.90-1.00.
_SEXUALITY_BI_THRESHOLD  = 0.1   # raw value >= this → at least bi (below = straight)
_SEXUALITY_GAY_THRESHOLD = 0.9   # raw value >= this → gay


def _valid_str(s) -> bool:
    """Reject None, empty, and game filler strings like 'none' or 'defaultmove'."""
    return bool(s) and s.strip().lower() not in _JUNK_STRINGS


def _normalize_gender(raw_gender: Optional[str]) -> str:
    """
    Normalize save-data gender variants to app-level values:
      - maleX   -> "male"
      - femaleX -> "female"
      - spidercat (ditto-like) -> "?"
    """
    g = (raw_gender or "").strip().lower()
    if g.startswith("male"):
        return "male"
    if g.startswith("female"):
        return "female"
    if g == "spidercat":
        return "?"
    return "?"


def _pair_key_u64(a: int, b: int) -> tuple[int, int]:
    """Return a stable order for symmetric pedigree pair keys."""
    return (a, b) if a <= b else (b, a)


@dataclass(slots=True)
class SaveData:
    """Parsed save output with tuple-style compatibility."""

    cats: list["Cat"]
    errors: list[tuple[int, str]]
    unlocked_house_rooms: list[str]
    furniture: list["FurnitureItem"] = field(default_factory=list)
    furniture_data: dict[str, "FurnitureDefinition"] = field(default_factory=dict)
    pedigree_map: dict[int, tuple[Optional[int], Optional[int]]] = field(default_factory=dict)
    pedigree_coi_memos: dict[tuple[int, int], float] = field(default_factory=dict)
    accessible_cats: set[int] = field(default_factory=set)

    def as_tuple(self) -> tuple[list["Cat"], list[tuple[int, str]], list[str]]:
        return self.cats, self.errors, self.unlocked_house_rooms

    def __iter__(self):
        yield from self.as_tuple()

    def __len__(self) -> int:
        return 3

    def __getitem__(self, index: int):
        return self.as_tuple()[index]

    def pedigree_coi_for(self, parent_a: int, parent_b: int) -> Optional[float]:
        """Look up a cached COI value for a parent pair, if present."""
        return self.pedigree_coi_memos.get(_pair_key_u64(int(parent_a), int(parent_b)))

    @property
    def furniture_by_room(self) -> dict[str, list["FurnitureItem"]]:
        grouped: dict[str, list[FurnitureItem]] = {}
        for item in self.furniture:
            grouped.setdefault(item.room, []).append(item)
        return grouped

    @property
    def placed_furniture(self) -> list["FurnitureItem"]:
        return [item for item in self.furniture if item.room]

    @property
    def unplaced_furniture(self) -> list["FurnitureItem"]:
        return [item for item in self.furniture if not item.room]


@dataclass(slots=True)
class FurnitureItem:
    """Parsed furniture record from the save's furniture table."""

    key: int
    version: int
    item_name: str
    room: str
    header_fields: tuple[int, int, int, int]
    placement_fields: tuple[int, ...]
    trailing_bytes: bytes = field(default=b"", repr=False, compare=False)

    @property
    def room_display(self) -> str:
        return ROOM_DISPLAY.get(self.room, self.room or "Unplaced")

    @property
    def is_placed(self) -> bool:
        return bool(self.room)

    @property
    def room_name_len(self) -> int:
        return int(self.header_fields[2]) if len(self.header_fields) >= 3 else 0


@dataclass(slots=True)
class FurnitureDefinition:
    """Parsed furniture metadata and stat effects from the game pack."""

    item_name: str
    display_name: str
    description: str
    effects: dict[str, float] = field(default_factory=dict)
    properties: dict[str, object] = field(default_factory=dict)

    @property
    def stat_effects(self) -> dict[str, float]:
        return {k: v for k, v in self.effects.items() if k in FURNITURE_ROOM_STAT_KEYS}


@dataclass(slots=True)
class FurnitureRoomSummary:
    """Computed room furniture summary used by the furniture view."""

    room: str
    cat_count: int
    furniture_count: int
    items: tuple["FurnitureItem", ...]
    raw_effects: dict[str, float]
    effective_effects: dict[str, float]
    all_effects: dict[str, float]
    crowd_penalty: int = 0
    dead_body_penalty: int = 0

    @property
    def room_display(self) -> str:
        return ROOM_DISPLAY.get(self.room, self.room or "Unplaced")


# ── Visual mutation table ─────────────────────────────────────────────────────

_VISUAL_MUTATION_FIELDS = [
    ("fur", 0, "fur", "texture", "fur", "Fur"),
    ("body", 3, "body", "body", "body", "Body"),
    ("head", 8, "head", "head", "head", "Head"),
    ("tail", 13, "tail", "tail", "tail", "Tail"),
    ("leg_L", 18, "legs", "legs", "legs", "Left Leg"),
    ("leg_R", 23, "legs", "legs", "legs", "Right Leg"),
    ("arm_L", 28, "arms", "legs", "legs", "Left Arm"),
    ("arm_R", 33, "arms", "legs", "legs", "Right Arm"),
    ("eye_L", 38, "eyes", "eyes", "eyes", "Left Eye"),
    ("eye_R", 43, "eyes", "eyes", "eyes", "Right Eye"),
    ("eyebrow_L", 48, "eyebrows", "eyebrows", "eyebrows", "Left Eyebrow"),
    ("eyebrow_R", 53, "eyebrows", "eyebrows", "eyebrows", "Right Eyebrow"),
    ("ear_L", 58, "ears", "ears", "ears", "Left Ear"),
    ("ear_R", 63, "ears", "ears", "ears", "Right Ear"),
    ("mouth", 68, "mouth", "mouth", "mouth", "Mouth"),
]

_VISUAL_MUTATION_VARIANT_FIELDS = [
    ("fur_variant",      4,  "fur",      "texture_variant", "fur",      "Fur Variant"),
    ("body_variant",     9,  "body",     "body_variant",    "body",     "Body Variant"),
    ("head_variant",    14,  "head",     "head_variant",    "head",     "Head Variant"),
    ("tail_variant",    19,  "tail",     "tail_variant",    "tail",     "Tail Variant"),
    ("leg_L_variant",   24,  "legs",     "legs_variant",    "legs",     "Left Leg Variant"),
    ("leg_R_variant",   29,  "legs",     "legs_variant",    "legs",     "Right Leg Variant"),
    ("arm_L_variant",   34,  "arms",     "legs_variant",    "legs",     "Left Arm Variant"),
    ("arm_R_variant",   39,  "arms",     "legs_variant",    "legs",     "Right Arm Variant"),
    ("eye_L_variant",   44,  "eyes",     "eyes_variant",    "eyes",     "Left Eye Variant"),
    ("eye_R_variant",   49,  "eyes",     "eyes_variant",    "eyes",     "Right Eye Variant"),
    ("eyebrow_L_variant", 54, "eyebrows", "eyebrows_variant", "eyebrows", "Left Eyebrow Variant"),
    ("eyebrow_R_variant", 59, "eyebrows", "eyebrows_variant", "eyebrows", "Right Eyebrow Variant"),
    ("ear_L_variant",   64,  "ears",     "ears_variant",    "ears",     "Left Ear Variant"),
    ("ear_R_variant",   69,  "ears",     "ears_variant",    "ears",     "Right Ear Variant"),
]

_VISUAL_MUTATION_PART_LABELS = {
    "fur": "Fur",
    "body": "Body",
    "head": "Head",
    "tail": "Tail",
    "legs": "Leg",
    "arms": "Arm",
    "eyes": "Eye",
    "eyebrows": "Eyebrow",
    "ears": "Ear",
    "mouth": "Mouth",
}

_STAT_LABELS = {
    "str": "STR",
    "con": "CON",
    "int": "INT",
    "dex": "DEX",
    "spd": "SPD",
    "lck": "LCK",
    "cha": "CHA",
    "shield": "Shield",
    "divine_shield": "Holy Shield",
}


_CLASS_STRING_TAIL_OFFSET = 115  # class string ends this many bytes before blob end


@dataclass(slots=True)
class GameData:
    """Resource-backed lookup tables used by parser helpers."""

    visual_mutation_data: dict[str, dict[int, tuple[str, str, str, bool]]] = field(default_factory=dict)
    furniture_data: dict[str, "FurnitureDefinition"] = field(default_factory=dict)
    game_tag_data: list[dict[str, str]] = field(default_factory=list)
    swf_symbol_data: dict[str, list[str]] = field(default_factory=dict)
    class_stat_mods: dict[str, dict[str, int]] = field(default_factory=dict)

    @classmethod
    def from_gpak(cls, gpak_path: str | None) -> "GameData":
        if not gpak_path:
            return cls()
        try:
            with open(gpak_path, "rb") as f:
                count = struct.unpack("<I", f.read(4))[0]
                entries = []
                for _ in range(count):
                    name_len = struct.unpack("<H", f.read(2))[0]
                    name = f.read(name_len).decode("utf-8", errors="replace")
                    size = struct.unpack("<I", f.read(4))[0]
                    entries.append((name, size))
                dir_end = f.tell()

                file_offsets: dict[str, tuple[int, int]] = {}
                offset = dir_end
                for name, size in entries:
                    file_offsets[name] = (offset, size)
                    offset += size

                game_strings = _load_gpak_text_strings(f, file_offsets)
                mutation_strings = _load_gpak_csv_strings(
                    f,
                    file_offsets,
                    "data/text/mutations.csv",
                    key_column="KEY",
                    value_column="en",
                )
                furniture_strings = _load_gpak_csv_strings(
                    f,
                    file_offsets,
                    "data/text/furniture.csv",
                    key_column="KEY",
                    value_column="en",
                )
                result: dict[str, dict[int, tuple[str, str, str, bool]]] = {}
                furniture_data: dict[str, FurnitureDefinition] = {}
                game_tag_data: list[dict[str, str]] = []
                swf_symbol_data: dict[str, list[str]] = {}
                for fname, (foff, fsz) in file_offsets.items():
                    lowered = fname.lower()
                    if fname.startswith("data/mutations/") and fname.endswith(".gon"):
                        category = fname.split("/")[-1].replace(".gon", "")
                        f.seek(foff)
                        content = f.read(fsz).decode("utf-8", errors="replace")
                        result[category] = _parse_mutation_gon(content, game_strings, category, mutation_strings)
                    elif fname == "data/furniture_effects.gon":
                        f.seek(foff)
                        content = f.read(fsz).decode("utf-8", errors="replace")
                        furniture_data = _parse_furniture_gon(content, furniture_strings)
                    elif _looks_like_game_tag_resource(lowered):
                        f.seek(foff)
                        content = f.read(fsz).decode("utf-8", errors="replace")
                        game_tag_data.extend(_parse_game_tag_resource(fname, content))
                    elif lowered.endswith(".swf") and any(token in lowered for token in ("icon", "portrait")):
                        f.seek(foff)
                        raw = f.read(fsz)
                        symbol_names = _parse_swf_symbol_names(raw)
                        if symbol_names:
                            swf_symbol_data[fname] = symbol_names
                class_stat_mods = _load_class_stat_mods(f, file_offsets)
                return cls(result, furniture_data, game_tag_data, swf_symbol_data, class_stat_mods)
        except Exception:
            return cls()


# Populated at runtime via set_visual_mut_data() from the main module.
_VISUAL_MUT_DATA: dict[str, dict[int, tuple[str, str, str, bool]]] = {}

# Mutation names that map to multiple body parts in the catalog — these must
# always be disambiguated with a slot label even when a cat only has one.
_name_to_parts: dict[str, set[str]] = {}
for (_cat_part, _mid), _mname in VISUAL_MUTATION_NAMES.items():
    _name_to_parts.setdefault(_mname, set()).add(_cat_part)
_GLOBALLY_AMBIGUOUS_MUTATION_NAMES: frozenset[str] = frozenset(
    n for n, parts in _name_to_parts.items() if len(parts) > 1
)
del _name_to_parts, _cat_part, _mid, _mname


# Class stat modifiers: {class_name: {STAT_NAME: delta}}
# Populated at runtime via set_class_stat_mods() from the main module.
_CLASS_STAT_MODS: dict[str, dict[str, int]] = {}

# Stat abbreviation mapping for class gon files.
_GON_STAT_KEY_TO_NAME = {
    "str": "STR", "con": "CON", "int": "INT",
    "dex": "DEX", "spd": "SPD", "lck": "LCK", "cha": "CHA",
}


def set_class_stat_mods(data: dict[str, dict[str, int]]):
    """Update the class stat modifier lookup data (called after gpak loading)."""
    _CLASS_STAT_MODS.clear()
    _CLASS_STAT_MODS.update(data)


def get_class_stat_mods(class_name: str) -> dict[str, int]:
    """Return {STAT_NAME: delta} for a class, or empty dict if unknown."""
    return _CLASS_STAT_MODS.get(class_name, {})


def _parse_class_stat_mods_gon(content: str) -> dict[str, dict[str, int]]:
    """Parse a class GON file and extract stat_mods for each class."""
    result: dict[str, dict[str, int]] = {}
    for class_name, block in _iter_gon_blocks(content):
        stat_mods_match = re.search(r"stat_mods\s*\{", block)
        if not stat_mods_match:
            continue
        brace_start = stat_mods_match.end() - 1
        depth = 0
        pos = brace_start
        while pos < len(block):
            if block[pos] == "{":
                depth += 1
            elif block[pos] == "}":
                depth -= 1
                if depth == 0:
                    break
            pos += 1
        sub_block = block[brace_start + 1:pos]
        mods: dict[str, int] = {}
        for line in sub_block.splitlines():
            line = line.split("//")[0].strip()
            parts = line.split()
            if len(parts) >= 2 and parts[0] in _GON_STAT_KEY_TO_NAME:
                try:
                    mods[_GON_STAT_KEY_TO_NAME[parts[0]]] = int(parts[1])
                except ValueError:
                    continue
        if mods:
            result[class_name] = mods
    return result


def _load_class_stat_mods(file_obj, file_offsets: dict[str, tuple[int, int]]) -> dict[str, dict[str, int]]:
    """Load class stat modifiers from class GON files in the gpak."""
    merged: dict[str, dict[str, int]] = {}
    for fname in ("data/classes/classes.gon", "data/classes/advanced_classes.gon"):
        if fname not in file_offsets:
            continue
        foff, fsz = file_offsets[fname]
        file_obj.seek(foff)
        content = file_obj.read(fsz).decode("utf-8", errors="replace")
        merged.update(_parse_class_stat_mods_gon(content))
    return merged


def set_visual_mut_data(data: dict[str, dict[int, tuple[str, str, str, bool]]]):
    """Update the visual mutation lookup data (called after gpak loading)."""
    global _VISUAL_MUT_DATA, _GLOBALLY_AMBIGUOUS_MUTATION_NAMES
    _VISUAL_MUT_DATA = data
    # Extend ambiguous set with GPAK names that appear across categories
    gpak_name_cats: dict[str, set[str]] = {}
    for category, muts in data.items():
        for mid, (name, _desc, *_rest) in muts.items():
            name = str(name).strip()
            if name:
                gpak_name_cats.setdefault(name, set()).add(category)
    gpak_ambiguous = frozenset(n for n, cats in gpak_name_cats.items() if len(cats) > 1)
    _GLOBALLY_AMBIGUOUS_MUTATION_NAMES = _GLOBALLY_AMBIGUOUS_MUTATION_NAMES | gpak_ambiguous


def _load_gpak_text_strings(file_obj, file_offsets: dict[str, tuple[int, int]]) -> dict[str, str]:
    """Read every CSV text table from a resources.gpak file into one dict.

    Game CSVs are multi-column ``KEY,en,pl,ru,zh_CN`` files — the previous
    implementation used ``line.split(',', 1)`` which stored every localized
    column as one concatenated value, so mutation descriptions rendered as
    ``"Blue Fur,Fur Azul,Синий Мех,藍毛"`` with every language glued
    together. That then confused downstream consumers that tried to recover
    the English text by splitting on commas, which cropped any English
    description that legitimately contained a comma (e.g. "+2 STR, -1 DEX").

    The fix: parse each CSV with :class:`csv.DictReader`, read only the
    ``en`` column (falling back to the first non-KEY, non-notes column when
    ``en`` is missing). Legacy single-value files that have no header row
    fall back to the old split-on-first-comma behaviour so 2-column key/value
    tables still work.
    """
    game_strings: dict[str, str] = {}
    for fname, (offset, size) in file_offsets.items():
        if not fname.endswith(".csv"):
            continue
        file_obj.seek(offset)
        text = file_obj.read(size).decode("utf-8-sig", errors="replace")

        # Try structured CSV parsing first.
        parsed_any = False
        try:
            reader = csv.DictReader(io.StringIO(text))
            fieldnames = reader.fieldnames or []
            if fieldnames and "KEY" in fieldnames:
                value_columns = [c for c in fieldnames if c not in ("KEY", "notes")]
                preferred = "en" if "en" in value_columns else (value_columns[0] if value_columns else None)
                for row in reader:
                    key = (row.get("KEY") or "").strip()
                    if not key:
                        continue
                    value = ""
                    if preferred:
                        value = (row.get(preferred) or "").strip()
                    if not value:
                        for column in value_columns:
                            candidate = (row.get(column) or "").strip()
                            if candidate:
                                value = candidate
                                break
                    if value:
                        game_strings[key] = _extract_primary_language_text(html.unescape(value))
                        parsed_any = True
        except Exception:
            parsed_any = False

        if parsed_any:
            continue

        # Legacy fallback — headerless 2-column "key,value" file.
        for line in text.splitlines():
            parts = line.split(",", 1)
            if len(parts) != 2:
                continue
            key, value = parts[0].strip(), parts[1].strip()
            if key and value:
                game_strings[key] = _extract_primary_language_text(html.unescape(value))
    return game_strings


def _load_gpak_csv_strings(
    file_obj,
    file_offsets: dict[str, tuple[int, int]],
    target_name: str,
    key_column: str = "KEY",
    value_column: str = "en",
) -> dict[str, str]:
    """Read one CSV file from the game pack and return a key->column mapping."""
    entry = file_offsets.get(target_name)
    if entry is None:
        return {}

    offset, size = entry
    file_obj.seek(offset)
    text = file_obj.read(size).decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        return {}

    values: dict[str, str] = {}
    for row in reader:
        key = (row.get(key_column) or "").strip()
        if not key:
            continue
        value = (row.get(value_column) or "").strip()
        if not value:
            # Fall back to the first non-key, non-notes column with a value.
            # (The previous implementation had mis-indented break/if that
            # abandoned the outer row iteration after the first fallback hit.)
            for column in reader.fieldnames:
                if column in {key_column, "notes"}:
                    continue
                candidate = (row.get(column) or "").strip()
                if candidate:
                    value = candidate
                    break
        if value:
            values[key] = _extract_primary_language_text(html.unescape(value))
    return values


def _swf_rect_size(data: bytes, offset: int) -> int:
    """Return the size in bytes of the SWF RECT header at offset."""
    if offset >= len(data):
        return 0
    nbits = data[offset] >> 3
    bit_length = 5 + (4 * nbits)
    return (bit_length + 7) // 8


def _parse_swf_symbol_names(raw: bytes) -> list[str]:
    """Extract SymbolClass names from a SWF file."""
    if len(raw) < 12 or raw[:3] not in {b"FWS", b"CWS"}:
        return []

    data = raw
    if raw[:3] == b"CWS":
        try:
            data = raw[:8] + zlib.decompress(raw[8:])
        except Exception:
            return []

    try:
        pos = 8 + _swf_rect_size(data, 8) + 4
    except Exception:
        return []
    if pos >= len(data):
        return []

    names: list[str] = []
    seen: set[str] = set()
    while pos + 2 <= len(data):
        record = struct.unpack_from("<H", data, pos)[0]
        pos += 2
        code = record >> 6
        length = record & 0x3F
        if length == 0x3F:
            if pos + 4 > len(data):
                break
            length = struct.unpack_from("<I", data, pos)[0]
            pos += 4
        if pos + length > len(data):
            break

        payload = data[pos:pos + length]
        pos += length

        if code == 76 and len(payload) >= 2:  # SymbolClass
            count = struct.unpack_from("<H", payload, 0)[0]
            off = 2
            for _ in range(count):
                if off + 2 > len(payload):
                    break
                off += 2  # symbol ID
                end = payload.find(b"\x00", off)
                if end == -1:
                    break
                name = payload[off:end].decode("utf-8", errors="replace").strip()
                off = end + 1
                if not name or name in seen:
                    continue
                seen.add(name)
                names.append(name)
        elif code == 0:
            break

    return names


def _resolve_game_string(value: str, game_strings: dict[str, str]) -> str:
    """Resolve chained game-string references of the form [KEY]."""
    current = value.strip()
    seen: set[str] = set()
    while current.startswith("[") and current.endswith("]"):
        key = current[1:-1].strip()
        if not key or key in seen:
            break
        seen.add(key)
        next_value = game_strings.get(key)
        if next_value is None:
            break
        current = next_value.strip()
    return _extract_primary_language_text(current)


def _extract_primary_language_text(value: str) -> str:
    """Return the primary-language segment from packed localized strings."""
    text = str(value or "").replace("\u00a0", " ").strip()
    if not text:
        return ""

    # Common packed format where languages are concatenated with triple bars.
    if "|||" in text:
        first = text.split("|||", 1)[0].strip()
        return first or text

    lang_token = r"(?:en(?:[-_](?:us|gb))?|english|ru|russian|pl|polish|zh(?:[-_]cn)?|chinese|ja|japanese|ko|korean)"
    token_prefix = re.compile(rf"^\s*(?:\[{lang_token}\]|{lang_token})\s*[:=\-]\s*", flags=re.IGNORECASE)
    token_anywhere = re.compile(rf"(?:^|\s*[|/;]\s*)(?:\[{lang_token}\]|{lang_token})\s*[:=\-]\s*", flags=re.IGNORECASE)

    if token_anywhere.search(text):
        chunks = [chunk.strip() for chunk in re.split(r"\s*[|/;]\s*", text) if chunk.strip()]
        parsed: list[tuple[str, str]] = []
        for chunk in chunks:
            match = re.match(
                rf"^\s*(?:\[(?P<btag>{lang_token})\]|(?P<tag>{lang_token}))\s*[:=\-]\s*(?P<body>.+)$",
                chunk,
                flags=re.IGNORECASE,
            )
            if not match:
                continue
            tag = (match.group("btag") or match.group("tag") or "").strip().lower()
            body = (match.group("body") or "").strip()
            if body:
                parsed.append((tag, body))
        if parsed:
            for tag, body in parsed:
                if tag.startswith("en") or tag == "english":
                    return body
            return parsed[0][1]

    if token_prefix.match(text):
        cleaned = token_prefix.sub("", text, count=1).strip()
        return cleaned or text

    return text


def _parse_mutation_gon(
    content: str,
    game_strings: dict[str, str],
    category: str,
    mutation_strings: dict[str, str] | None = None,
) -> dict[int, tuple[str, str, str]]:
    """Parse a mutation GON file into {slot_id: (display_name, stat_desc, gon_stats)}.

    ``gon_stats`` is always extracted from the GON block header (e.g.
    ``+1 CON, -1 SPD``) and may differ from ``stat_desc`` which comes
    from the localized CSV strings.  Stat bonus computation should
    prefer ``gon_stats`` so that mutations whose CSV description uses
    non-stat phrasing (e.g. "Start with +1 Bonus Attack") still
    contribute the correct stat delta.
    """
    result: dict[int, tuple[str, str, str]] = {}
    mutation_strings = mutation_strings or {}
    csv_prefix = f"MUTATION_{category.upper()}_"

    def _extract_block(start_pos: int) -> tuple[str, int]:
        depth, end = 1, start_pos
        while end < len(content) and depth > 0:
            if content[end] == "{":
                depth += 1
            elif content[end] == "}":
                depth -= 1
            end += 1
        return content[start_pos:end - 1], end

    def _gon_stat_string(block: str) -> str:
        """Always extract stat deltas from the GON block header."""
        header = block.split("{")[0]
        stats: list[str] = []
        for key, label in _STAT_LABELS.items():
            stat_match = re.search(rf"(?<!\w){re.escape(key)}\s+(-?\d+)", header)
            if stat_match:
                value = int(stat_match.group(1))
                stats.append(f"{'+' if value > 0 else ''}{value} {label}")
        return ", ".join(stats)

    def _block_to_entry(slot_id: int, block: str):
        name_match = re.search(r"//\s*(.+)", block)
        raw_name = name_match.group(1).strip().title() if name_match else f"Mutation {slot_id}"
        raw_name = re.sub(r"\s*\(.*", "", raw_name).strip() or raw_name
        gon_stats = _gon_stat_string(block)
        is_birth_defect = bool(re.search(r"\btag\s+birth_defect\b", block))
        csv_key = f"{csv_prefix}{slot_id}_DESC"
        if csv_key in mutation_strings:
            stat_desc = _resolve_game_string(mutation_strings[csv_key], game_strings).strip().rstrip(".")
        elif csv_key in game_strings:
            stat_desc = _resolve_game_string(game_strings[csv_key], game_strings).strip().rstrip(".")
        else:
            stat_desc = gon_stats
        result[slot_id] = (raw_name, stat_desc, gon_stats, is_birth_defect)

    idx = 0
    while idx < len(content):
        # Match any numeric ID (including low IDs like 2); skip non-defect low
        # IDs below — but include them when they carry `tag birth_defect`.
        match = re.search(r"(?<!\w)(\d+)\s*\{", content[idx:])
        if not match:
            break
        slot_id = int(match.group(1))
        block, idx = _extract_block(idx + match.end())
        if slot_id < 300 and not re.search(r"\btag\s+birth_defect\b", block):
            continue
        _block_to_entry(slot_id, block)

    m2_match = re.search(r"(?<!\w)-2\s*\{", content)
    if m2_match:
        block, _ = _extract_block(m2_match.end())
        csv_key_m2 = f"{csv_prefix}M2_DESC"
        if csv_key_m2 in mutation_strings:
            name_match = re.search(r"//\s*(.+)", block)
            raw_name = name_match.group(1).strip().title() if name_match else "Missing Part"
            raw_name = re.sub(r"\s*\(.*", "", raw_name).strip() or raw_name
            stat_desc = _resolve_game_string(mutation_strings[csv_key_m2], game_strings).strip().rstrip(".")
            result[0xFFFFFFFE] = (raw_name, stat_desc, _gon_stat_string(block), True)
        elif csv_key_m2 in game_strings:
            name_match = re.search(r"//\s*(.+)", block)
            raw_name = name_match.group(1).strip().title() if name_match else "Missing Part"
            raw_name = re.sub(r"\s*\(.*", "", raw_name).strip() or raw_name
            stat_desc = _resolve_game_string(game_strings[csv_key_m2], game_strings).strip().rstrip(".")
            result[0xFFFFFFFE] = (raw_name, stat_desc, _gon_stat_string(block), True)
        else:
            _block_to_entry(0xFFFFFFFE, block)

    return result


def _iter_gon_blocks(content: str):
    """Yield (block_name, block_body) for top-level brace blocks in a GON file."""
    idx = 0
    pattern = re.compile(r"(?m)^([A-Za-z0-9_]+)\s*\{")
    while idx < len(content):
        match = pattern.search(content, idx)
        if not match:
            return
        name = match.group(1)
        brace_start = content.find("{", match.start())
        if brace_start < 0:
            return
        depth = 0
        pos = brace_start
        while pos < len(content):
            ch = content[pos]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    yield name, content[brace_start + 1:pos]
                    idx = pos + 1
                    break
            pos += 1
        else:
            return


def _coerce_furniture_value(value: str) -> object:
    text = value.strip()
    if text.lower() in {"true", "false"}:
        return text.lower() == "true"
    try:
        if "." in text:
            return float(text)
        return int(text)
    except Exception:
        return text


def _parse_furniture_gon(content: str, furniture_strings: dict[str, str]) -> dict[str, FurnitureDefinition]:
    """Parse furniture definitions from the game's furniture_effects.gon file."""
    definitions: dict[str, FurnitureDefinition] = {}

    for item_name, block in _iter_gon_blocks(content):
        properties: dict[str, object] = {}
        effects: dict[str, float] = {}
        name_key = ""
        desc_key = ""

        for raw_line in block.splitlines():
            line = raw_line.split("//", 1)[0].strip()
            if not line:
                continue
            parts = line.split()
            if len(parts) < 2:
                continue
            key = parts[0]
            value = " ".join(parts[1:]).strip()
            coerced = _coerce_furniture_value(value)
            properties[key] = coerced
            if key == "name":
                name_key = value
            elif key == "desc":
                desc_key = value
            elif isinstance(coerced, (int, float)):
                effects[key] = float(coerced)

        display_name = html.unescape(furniture_strings.get(name_key, "")).strip()
        if not display_name:
            display_name = item_name.replace("_", " ").strip().title()
        description = html.unescape(furniture_strings.get(desc_key, "")).strip()
        definitions[item_name] = FurnitureDefinition(
            item_name=item_name,
            display_name=display_name,
            description=description,
            effects=effects,
            properties=properties,
        )

    return definitions


def _looks_like_game_tag_resource(filename: str) -> bool:
    lowered = filename.lower()
    return any(token in lowered for token in ("tag", "badge", "name_tag", "name-tag"))


def _coerce_game_tag_definition_value(value: object) -> str:
    return str(value or "").strip()


def _parse_game_tag_csv(content: str, source_name: str) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    reader = csv.DictReader(io.StringIO(content))
    if not reader.fieldnames:
        return records

    field_names = [str(field or "").strip() for field in reader.fieldnames]
    interesting = any(
        any(token in field.lower() for token in ("tag", "name", "icon", "color", "image", "badge", "tooltip", "desc"))
        for field in field_names
    )
    if not interesting:
        return records

    for row in reader:
        record: dict[str, str] = {"source": source_name}
        for key, value in row.items():
            k = str(key or "").strip().lower()
            v = _coerce_game_tag_definition_value(value)
            if not v:
                continue
            if k in {"id", "key", "tag", "tag_id", "name_tag"} and "id" not in record:
                record["id"] = v
                continue
            if k in {"name", "title", "label", "display_name", "text"} and "name" not in record:
                record["name"] = v
                continue
            if k in {"color", "colour", "hex", "tint"} and "color" not in record:
                record["color"] = v
                continue
            if k in {"image", "image_path", "icon", "texture", "sprite"} and "image_path" not in record:
                record["image_path"] = v
                continue
            if k in {"tooltip", "description", "desc", "details"} and "tooltip" not in record:
                record["tooltip"] = v
                continue
        if "id" not in record and "name" in record:
            record["id"] = record["name"]
        if "id" in record or "name" in record or "image_path" in record:
            records.append(record)
    return records


def _parse_game_tag_gon(content: str, source_name: str) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []

    for block_name, block in _iter_gon_blocks(content):
        block_key = block_name.strip()
        lowered = block_key.lower()
        if not any(token in lowered for token in ("tag", "badge", "name_tag", "name-tag")):
            lines = block.lower()
            if not any(token in lines for token in (" tag ", "color ", "icon ", "image ", "badge ")):
                continue

        record: dict[str, str] = {"source": source_name}
        for raw_line in block.splitlines():
            line = raw_line.split("//", 1)[0].strip()
            if not line:
                continue
            parts = line.split(None, 1)
            if len(parts) != 2:
                continue
            key = parts[0].strip().lower()
            value = parts[1].strip().strip('"')
            if not value:
                continue
            if key in {"id", "key", "tag", "tag_id", "name_tag"} and "id" not in record:
                record["id"] = value
            elif key in {"name", "title", "label", "display_name", "text"} and "name" not in record:
                record["name"] = value
            elif key in {"color", "colour", "hex", "tint"} and "color" not in record:
                record["color"] = value
            elif key in {"image", "image_path", "icon", "texture", "sprite"} and "image_path" not in record:
                record["image_path"] = value
            elif key in {"tooltip", "description", "desc", "details"} and "tooltip" not in record:
                record["tooltip"] = value
        if "id" not in record and block_key:
            record["id"] = block_key
        if "id" in record or "name" in record or "image_path" in record:
            records.append(record)

    return records


def _parse_game_tag_resource(source_name: str, content: str) -> list[dict[str, str]]:
    lower = source_name.lower()
    if lower.endswith(".csv"):
        return _parse_game_tag_csv(content, source_name)
    if lower.endswith(".gon"):
        return _parse_game_tag_gon(content, source_name)
    return []


def _format_furniture_effect_value(value: float) -> str:
    number = float(value)
    if number.is_integer():
        number = int(number)
    return f"{number:+g}"


def summarize_furniture_room(
    items: list[FurnitureItem],
    definitions: dict[str, FurnitureDefinition] | None = None,
    room: str | None = None,
    cat_count: int = 0,
    dead_bodies: int = 0,
) -> FurnitureRoomSummary:
    """Summarize the furniture effects for a single room."""
    raw_effects = {key: 0.0 for key in FURNITURE_ROOM_STAT_KEYS}
    all_effects: dict[str, float] = {}
    for item in items:
        definition = definitions.get(item.item_name) if definitions else None
        effects = definition.effects if definition is not None else {}
        for key, value in effects.items():
            all_effects[key] = all_effects.get(key, 0.0) + float(value)
            if key in raw_effects:
                raw_effects[key] += float(value)

    crowd_penalty = max(0, int(cat_count) - 4)
    effective_effects = dict(raw_effects)
    effective_effects["Comfort"] -= crowd_penalty
    if dead_bodies:
        effective_effects["Health"] -= dead_bodies

    return FurnitureRoomSummary(
        room=room if room is not None else (items[0].room if items else ""),
        cat_count=int(cat_count),
        furniture_count=len(items),
        items=tuple(items),
        raw_effects=raw_effects,
        effective_effects=effective_effects,
        all_effects=all_effects,
        crowd_penalty=crowd_penalty,
        dead_body_penalty=int(dead_bodies),
    )


def build_furniture_room_summaries(
    furniture_by_room: dict[str, list[FurnitureItem]],
    definitions: dict[str, FurnitureDefinition] | None = None,
    cats: list[Cat] | None = None,
    room_order: Iterable[str] | None = None,
) -> list[FurnitureRoomSummary]:
    """Build consistent room summaries for UI panels and downstream consumers."""
    cat_counts: dict[str, int] = {}
    for cat in cats or []:
        if cat.status == "In House" and cat.room:
            cat_counts[cat.room] = cat_counts.get(cat.room, 0) + 1

    ordered_rooms: list[str] = []
    seen: set[str] = set()

    if room_order is not None:
        for room in room_order:
            if room and room not in seen:
                ordered_rooms.append(room)
                seen.add(room)
    else:
        for room in ROOM_KEYS:
            if room not in seen:
                ordered_rooms.append(room)
                seen.add(room)

    for room in furniture_by_room:
        if room and room not in seen:
            ordered_rooms.append(room)
            seen.add(room)

    if "" in furniture_by_room and "" not in seen:
        ordered_rooms.append("")

    summaries: list[FurnitureRoomSummary] = []
    for room in ordered_rooms:
        items = furniture_by_room.get(room, [])
        summaries.append(
            summarize_furniture_room(
                items,
                definitions,
                room=room,
                cat_count=cat_counts.get(room, 0),
            )
        )
    return summaries


def _read_visual_mutation_entries(table: list[int]) -> list[dict[str, object]]:
    fallback_names = load_visual_mutation_names()
    verbose_logs = os.environ.get("MEWGENICS_VERBOSE_PARSER", "").strip().lower() in {"1", "true", "yes", "on"}
    entries: list[dict[str, object]] = []
    for slot_key, table_index, group_key, gpak_category, fallback_part, slot_label in _VISUAL_MUTATION_FIELDS:
        mutation_id = table[table_index] if table_index < len(table) else 0
        if mutation_id in (0, 0xFFFF_FFFF):
            continue

        part_label = _VISUAL_MUTATION_PART_LABELS.get(group_key, slot_label)
        # Sentinel "missing part" is always a defect. Range heuristic
        # (700–706) is only used as a fallback when the GPAK data for
        # this mutation does not carry an explicit `tag birth_defect`
        # marker — GPAK is authoritative otherwise. See issue #93: some
        # 700-range mutations are normal variants, not defects.
        is_sentinel_missing = (mutation_id == 0xFFFF_FFFE)
        is_defect = is_sentinel_missing
        display_name = ""
        detail = ""
        gon_stats = ""
        source = "generic"
        defect_source = "sentinel" if is_sentinel_missing else "none"
        catalog_name = fallback_names.get((fallback_part, mutation_id))
        gpak_info = _VISUAL_MUT_DATA.get(gpak_category, {}).get(mutation_id)
        # Skip base/default part IDs — real mutations have gpak_info or a
        # catalog entry, or the special "missing part" sentinel. Low IDs
        # (< 300) without a lookup are the cat's base sprite selection,
        # not a mutation, and must not appear in the mutation chip list.
        if gpak_info is None and catalog_name is None and mutation_id != 0xFFFF_FFFE:
            continue
        if gpak_info:
            # Tolerate 2-, 3-, or 4-tuple GPAK info for back-compat with tests.
            # Full tuple is (raw_name, stat_desc, gon_stats, is_birth_defect).
            _gi = tuple(gpak_info)
            raw_name = _gi[0] if len(_gi) >= 1 else ""
            stat_desc = _gi[1] if len(_gi) >= 2 else ""
            gon_stats = _gi[2] if len(_gi) >= 3 else ""
            if len(_gi) >= 4:
                # GPAK is authoritative: trust its birth_defect tag.
                if bool(_gi[3]):
                    is_defect = True
                    defect_source = "gpak"
            elif not is_sentinel_missing and 700 <= mutation_id <= 706:
                # Legacy 3-tuple GPAK entry — fall back to the range heuristic.
                is_defect = True
                defect_source = "range_fallback_gpak3tuple"
            raw_name = str(raw_name).strip()
            detail = str(stat_desc).strip()
            if raw_name and not re.match(r'^Mutation \d+$', raw_name):
                display_name = raw_name
                source = f"gpak:{gpak_category}"
            elif catalog_name:
                display_name = str(catalog_name).strip()
                source = f"catalog:{fallback_part}"
            else:
                base = f"{part_label} Mutation"
                display_name = f"{base} {stat_desc}" if stat_desc else base
                source = f"generic:{gpak_category}"
        elif not is_sentinel_missing and 700 <= mutation_id <= 706:
            # No GPAK data at all — use the range heuristic as a best guess.
            is_defect = True
            defect_source = "range_fallback_no_gpak"
            if catalog_name:
                display_name = str(catalog_name).strip()
                source = f"catalog:{fallback_part}"
        elif catalog_name:
            display_name = str(catalog_name).strip()
            source = f"catalog:{fallback_part}"
        else:
            if mutation_id == 0xFFFF_FFFE:
                display_name = f"No {part_label}"
            else:
                display_name = f"{part_label} {mutation_id}"

        if is_defect:
            # Defects are shown in-game as part-level defect labels.
            display_name = f"{part_label} Birth Defect"

        display_name = str(display_name).strip() or f"{slot_label} {mutation_id}"
        if logger.isEnabledFor(logging.DEBUG) and (verbose_logs or not _is_synthetic_visual_mutation_name(display_name, part_label, slot_label, mutation_id)):
            logger.debug(
                "visual mutation slot=%s table_index=%s mutation_id=%s gpak_category=%s source=%s defect=%s defect_source=%s name=%s detail=%s",
                slot_key,
                table_index,
                mutation_id,
                gpak_category,
                source,
                is_defect,
                defect_source,
                display_name,
                detail,
            )
        if is_defect:
            # Always log defect detections at INFO so uploaded user logs
            # show exactly which mutation was flagged and why. Cheap —
            # most cats have 0–2 defects.
            logger.info(
                "defect detected slot=%s mutation_id=%s gpak_category=%s defect_source=%s name=%s",
                slot_key, mutation_id, gpak_category, defect_source, display_name,
            )
        entries.append({
            "slot_key": slot_key,
            "slot_label": slot_label,
            "group_key": group_key,
            "part_label": part_label,
            "mutation_id": mutation_id,
            "name": display_name,
            "detail": str(detail).strip(),
            "gon_stats": str(gon_stats).strip(),
            "is_defect": is_defect,
        })
    return entries


# Regex used to extract per-stat deltas from a mutation/defect detail string
# like "+1 LCK", "-1 SPD, +2 LCK" or "+2 CON, -1 DEX". Only the 7 core stats
# are recognized — things like "+1 Range" or "+1 Charge" are deliberately
# ignored because they don't feed into the Total Stats sum.
_MUTATION_STAT_ALIASES: dict[str, str] = {
    "STR": "STR", "STRENGTH": "STR",
    "DEX": "DEX", "DEXTERITY": "DEX",
    "CON": "CON", "CONSTITUTION": "CON",
    "INT": "INT", "INTELLIGENCE": "INT",
    "SPD": "SPD", "SPEED": "SPD",
    "CHA": "CHA", "CHARISMA": "CHA",
    "LCK": "LCK", "LUCK": "LCK",
}
_MUTATION_STAT_RE = re.compile(
    r"(?P<sign>[+-])\s*(?P<value>\d+)\s*"
    r"(?P<label>" + "|".join(sorted(_MUTATION_STAT_ALIASES, key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
)


def _parse_mutation_stat_delta(detail: str) -> dict[str, int]:
    """Parse stat deltas from a mutation/defect detail string.

    Returns {stat: delta} with stat ∈ STAT_NAMES. Non-stat effects such as
    "+1 Range", "+1 Reach" or free-form text are ignored. Examples::

        '+1 LCK'            -> {'LCK': 1}
        '-1 SPD, +2 LCK'    -> {'SPD': -1, 'LCK': 2}
        '+1 Range'          -> {}
    """
    if not detail:
        return {}
    deltas: dict[str, int] = {}
    for match in _MUTATION_STAT_RE.finditer(str(detail)):
        key = _MUTATION_STAT_ALIASES.get(match.group("label").upper())
        if not key:
            continue
        try:
            amount = int(match.group("sign") + match.group("value"))
        except Exception:
            continue
        deltas[key] = deltas.get(key, 0) + amount
    return deltas


def _mutation_stat_bonus_from_entries(entries: list[dict[str, object]]) -> dict[str, int]:
    """Sum mutation stat deltas across all unique visual mutations.

    Paired body parts (e.g. ``arm_L`` + ``arm_R``) that share the same
    mutation id represent a single mutation affecting both slots and are
    therefore counted once, matching how the UI merges them into one chip.

    Stat deltas are preferentially extracted from ``gon_stats`` (raw GON
    block data like ``+1 CON``) rather than ``detail`` (the localized
    CSV description), because some mutations use non-stat phrasing in
    their description (e.g. "Start with +1 Bonus Attack") while the
    GON block still contains the actual stat field (e.g. ``con 1``).
    """
    bonus: dict[str, int] = {name: 0 for name in STAT_NAMES}
    seen: set[tuple[str, int]] = set()
    for entry in entries or []:
        key = (str(entry.get("group_key") or ""), int(entry.get("mutation_id") or 0))
        if key in seen:
            continue
        seen.add(key)
        # Prefer gon_stats (raw GON block data) over detail (CSV description)
        gon = str(entry.get("gon_stats") or "")
        deltas = _parse_mutation_stat_delta(gon) if gon else {}
        if not deltas:
            deltas = _parse_mutation_stat_delta(str(entry.get("detail") or ""))
        for stat, delta in deltas.items():
            bonus[stat] = bonus.get(stat, 0) + delta
    return bonus


def _is_synthetic_visual_mutation_name(display_name: str, part_label: str, slot_label: str, mutation_id: int) -> bool:
    """Return True when the resolved name is still just a generic fallback."""
    normalized = str(display_name or "").strip().casefold()
    if not normalized:
        return True

    part = str(part_label or "").strip()
    slot = str(slot_label or "").strip()
    candidates = {
        f"{part} {mutation_id}".casefold(),
        f"{slot} {mutation_id}".casefold(),
        f"{part} mutation".casefold(),
        f"{slot} mutation".casefold(),
        f"mutation {mutation_id}".casefold(),
        f"no {part}".casefold(),
        f"no {slot}".casefold(),
    }
    return normalized in candidates


def _visual_mutation_chip_items(entries: list[dict[str, object]]) -> list[tuple[str, str, bool]]:
    """Return [(display_text, tooltip, is_defect), ...] from visual mutation entries."""
    grouped: dict[tuple[str, int], list[dict[str, object]]] = {}
    order: list[tuple[str, int]] = []
    for entry in entries:
        key = (str(entry["group_key"]), int(entry["mutation_id"]))
        if key not in grouped:
            grouped[key] = []
            order.append(key)
        grouped[key].append(entry)

    groups: list[dict[str, object]] = []
    for key in order:
        items = grouped[key]
        slot_labels = [str(item["slot_label"]) for item in items]
        name = str(items[0]["name"])
        mutation_id = int(items[0]["mutation_id"])
        part_label = str(items[0]["part_label"])
        detail = str(items[0]["detail"]).strip()
        gon_stats = str(items[0].get("gon_stats") or "").strip()
        is_defect = bool(items[0].get("is_defect", False))
        title_label = part_label if len(slot_labels) > 1 else str(items[0]["slot_label"])
        kind = "Birth Defect" if is_defect else "Mutation"
        id_str = "-2" if mutation_id == 0xFFFF_FFFE else str(mutation_id)
        tooltip = f"{title_label} {kind} (ID {id_str})\n{name}"
        if detail and detail not in name:
            tooltip = f"{tooltip}\n{detail}"
        # Show raw stat bonus from GON data if different from the display text
        if gon_stats and gon_stats != detail:
            tooltip = f"{tooltip}\n{gon_stats}"
        if len(slot_labels) > 1:
            tooltip = f"{tooltip}\nAffects: {', '.join(slot_labels)}"
        groups.append({
            "text": name,
            "tooltip": tooltip,
            "slot_labels": slot_labels,
            "is_defect": is_defect,
        })

    text_counts: dict[str, int] = {}
    for group in groups:
        text = str(group["text"])
        text_counts[text] = text_counts.get(text, 0) + 1

    chip_items: list[tuple[str, str, bool]] = []
    for group in groups:
        text = str(group["text"])
        if text_counts[text] > 1 or text in _GLOBALLY_AMBIGUOUS_MUTATION_NAMES:
            text = f"{text} ({' / '.join(group['slot_labels'])})"
        chip_items.append((text, str(group["tooltip"]), bool(group["is_defect"])))
    return chip_items


def _appearance_group_names(cat: 'Cat', group_key: str) -> list[str]:
    entries = getattr(cat, "visual_mutation_entries", []) or []
    names: list[str] = []
    seen: set[str] = set()
    for entry in entries:
        if str(entry.get("group_key")) != group_key:
            continue
        name = str(entry.get("name", "")).strip()
        if name and name not in seen:
            seen.add(name)
            names.append(name)
    if names:
        return names
    if group_key in {"fur", "body", "head"}:
        return [f"Base {_VISUAL_MUTATION_PART_LABELS.get(group_key, group_key).title()}"]
    return []


def _appearance_preview_text(a_names: list[str], b_names: list[str]) -> str:
    if not a_names and not b_names:
        return "No distinct appearance data"
    a_text = " / ".join(a_names) if a_names else "Base"
    b_text = " / ".join(b_names) if b_names else "Base"
    if set(a_names) == set(b_names):
        return f"Likely {a_text}"
    return f"Probabilistic: {a_text} or {b_text}"


def _stimulation_inheritance_weight(stimulation: float) -> float:
    stim = float(stimulation)
    return (1.0 + 0.01 * stim) / (2.0 + 0.01 * stim)


def _inheritance_candidates(
    a_items: list[str],
    b_items: list[str],
    stimulation: float,
    display_fn=None,
) -> tuple[list[tuple[str, str]], float, float]:
    share_a = _stimulation_inheritance_weight(stimulation)
    share_b = 1.0 - share_a
    odds: dict[str, float] = {}
    tips: dict[str, list[str]] = {}

    def _add(items: list[str], share: float, source_name: str):
        if not items:
            return
        per_item = share / len(items)
        for raw in items:
            key = str(raw)
            odds[key] = odds.get(key, 0.0) + per_item
            tips.setdefault(key, []).append(f"{source_name}: {per_item * 100:.0f}%")

    _add(a_items, share_a, "Parent A")
    _add(b_items, share_b, "Parent B")

    ordered = sorted(odds.items(), key=lambda kv: (-kv[1], (display_fn(kv[0]) if display_fn else kv[0]).lower()))
    chips: list[tuple[str, str]] = []
    for key, prob in ordered:
        label = display_fn(key) if display_fn else key
        chips.append((f"{label} {prob * 100:.0f}%", "\n".join(tips.get(key, []))))
    return chips, share_a, share_b


# ── Binary reader ─────────────────────────────────────────────────────────────

class BinaryReader:
    def __init__(self, data, pos=0):
        self.data = data
        self.pos  = pos

    def u32(self):
        v = struct.unpack_from('<I', self.data, self.pos)[0]
        self.pos += 4
        return v

    def i32(self):
        v = struct.unpack_from('<i', self.data, self.pos)[0]
        self.pos += 4
        return v

    def u8(self):
        v = struct.unpack_from('<B', self.data, self.pos)[0]
        self.pos += 1
        return v

    def u64(self):
        lo, hi = struct.unpack_from('<II', self.data, self.pos)
        self.pos += 8
        return lo + hi * 4_294_967_296

    def f64(self):
        v = struct.unpack_from('<d', self.data, self.pos)[0]
        self.pos += 8
        return v

    def str(self):
        start = self.pos
        try:
            length = self.u64()
            if length < 0 or length > 10_000:
                self.pos = start
                return None
            s = self.data[self.pos:self.pos + int(length)].decode('utf-8', errors='ignore')
            self.pos += int(length)
            return s
        except Exception:
            logger.debug("BinaryReader.str() failed at pos %d", start, exc_info=True)
            self.pos = start
            return None

    def utf16str(self):
        char_count = self.u64()
        byte_len   = int(char_count * 2)
        s = self.data[self.pos:self.pos + byte_len].decode('utf-16le', errors='ignore')
        self.pos += byte_len
        return s

    def skip(self, n):
        self.pos += n

    def seek(self, n):
        self.pos = n

    def remaining(self):
        return len(self.data) - self.pos

    def find(self, needle: bytes | str, start: Optional[int] = None, end: Optional[int] = None) -> int:
        if isinstance(needle, str):
            needle = needle.encode("ascii", errors="ignore")
        start = self.pos if start is None else start
        end = len(self.data) if end is None else end
        return self.data.find(needle, start, end)


# ── Parent UID scanner ────────────────────────────────────────────────────────

def _scan_blob_for_parent_uids(raw: bytes, uid_set: frozenset, self_uid: int) -> tuple[int, int]:
    """
    Scan the decompressed blob byte-by-byte looking for two consecutive u64
    values (4-byte aligned) that are in uid_set and are not self_uid.
    Parent UIDs appear early in the blob so we only scan the first 1 KB.
    Returns (parent_a_uid, parent_b_uid), each 0 if not found.
    """
    if not uid_set:
        return 0, 0
    limit = min(1024, len(raw) - 16)
    i = 12  # skip breed_id(4) + own uid(8)
    while i <= limit - 16:
        lo1, hi1 = struct.unpack_from('<II', raw, i)
        v1 = lo1 + hi1 * 4_294_967_296
        if v1 in uid_set and v1 != self_uid:
            lo2, hi2 = struct.unpack_from('<II', raw, i + 8)
            v2 = lo2 + hi2 * 4_294_967_296
            if v2 in uid_set and v2 != self_uid:
                return v1, v2          # both parents found
            if v2 == 0:
                return v1, 0           # one parent (other unknown)
        i += 4  # u64-aligned steps
    return 0, 0


def _resolve_parent_uids(
    cat: "Cat",
    ped_map: dict[int, tuple[Optional[int], Optional[int]]],
) -> tuple[Optional[int], Optional[int]]:
    """Resolve parent IDs from pedigree data only.

    The pedigree blob is the authoritative source. If it does not name a
    parent, leave that parent unknown rather than guessing from raw blob bytes.
    """
    pa_k, pb_k = ped_map.get(cat.db_key, (None, None))
    if pa_k == cat.db_key:
        pa_k = None
    if pb_k == cat.db_key:
        pb_k = None
    return pa_k, pb_k


def _break_pedigree_cycles(cats: list["Cat"]) -> int:
    """Break invalid parent loops so ancestry helpers stay simple downstream.

    Uses iterative DFS with white/gray/black coloring for O(V+E) cycle
    detection instead of the previous O(V²) approach.
    """
    broken = 0

    def _mark_repair(cat: "Cat"):
        cat.pedigree_was_repaired = True
        cat.pedigree_cycle_breaks = getattr(cat, "pedigree_cycle_breaks", 0) + 1

    # Quick pass: fix self-parent loops
    for cat in cats:
        for attr in ("parent_a", "parent_b"):
            if getattr(cat, attr) is cat:
                logger.warning("Breaking self-parent loop for cat %s", cat.db_key)
                _mark_repair(cat)
                setattr(cat, attr, None)
                broken += 1

    # Iterative DFS cycle detection: WHITE=0, GRAY=1, BLACK=2
    WHITE, GRAY, BLACK = 0, 1, 2
    color: dict[int, int] = {cat.db_key: WHITE for cat in cats}
    cat_by_key: dict[int, "Cat"] = {cat.db_key: cat for cat in cats}
    attrs = ("parent_a", "parent_b")

    for start_cat in cats:
        if color[start_cat.db_key] != WHITE:
            continue
        stack: list[tuple[int, int]] = [(start_cat.db_key, 0)]
        while stack:
            ck, pi = stack[-1]
            if pi == 0:
                color[ck] = GRAY
            if pi < 2:
                stack[-1] = (ck, pi + 1)
                c = cat_by_key[ck]
                parent = getattr(c, attrs[pi])
                if parent is not None and parent.db_key in color:
                    pk = parent.db_key
                    if color[pk] == GRAY:
                        logger.warning(
                            "Breaking pedigree cycle: cat %s -> parent %s via %s",
                            ck, pk, attrs[pi],
                        )
                        _mark_repair(c)
                        setattr(c, attrs[pi], None)
                        broken += 1
                    elif color[pk] == WHITE:
                        stack.append((pk, 0))
            else:
                color[ck] = BLACK
                stack.pop()

    return broken


def _choose_age_from_creation_days(current_day: int, creation_days: list[int], eternal_youth: bool = False) -> Optional[int]:
    """
    Pick the most plausible age from candidate creation_day values.

    Some cat blobs include a zero-padded slot immediately before the real
    creation_day field. Preferring the largest valid non-zero creation day keeps
    those cats from being misread as day-0 imports.
    """
    valid_days = sorted(
        {day for day in creation_days if 0 <= day <= current_day},
        reverse=True,
    )
    if not valid_days:
        return None

    for creation_day in valid_days:
        age = current_day - creation_day
        if age <= 100 or eternal_youth:
            return age
    return None


def _read_db_key_candidates(raw: bytes, self_key: int, offsets: tuple[int, ...], base_offset: int = 0) -> list[int]:
    keys: list[int] = []
    for off in offsets:
        pos = base_offset + off
        if pos < 0 or pos + 4 > len(raw):
            continue
        try:
            value = struct.unpack_from('<I', raw, pos)[0]
        except Exception:
            logger.debug("_read_db_key_candidates: unpack failed at pos %d", pos, exc_info=True)
            continue
        if value in (0, 0xFFFF_FFFF) or value == self_key:
            continue
        if value not in keys:
            keys.append(value)
    return keys


# ── Cat ───────────────────────────────────────────────────────────────────────

class Cat:
    # parent_a / parent_b are resolved after the full save is loaded
    parent_a: Optional['Cat'] = None
    parent_b: Optional['Cat'] = None
    generation: int = 0   # generation depth: 0=stray, 1=child of strays, etc.
    is_blacklisted: bool = False  # exclude from breeding calculations
    must_breed: bool = False  # prioritize in breeding optimization
    is_pinned: bool = False  # user-pinned for tracking
    tags: list[str] = None  # user-assigned tag IDs for organization
    passive_abilities: list[str]

    def __init__(self, blob: bytes, cat_key: int, house_info: dict, adventure_keys: set, current_day: Optional[int] = None):
        uncomp_size = struct.unpack('<I', blob[:4])[0]
        raw = lz4.block.decompress(blob[4:], uncompressed_size=uncomp_size)
        r   = BinaryReader(raw)
        self._raw = raw   # kept for parent-UID blob scan in parse_save

        self.db_key = cat_key
        self.tags = []

        # Location / status
        if cat_key in adventure_keys:
            self.status = "Adventure"
            self.room   = "Adventure"
        elif cat_key in house_info:
            self.status = "In House"
            self.room   = house_info[cat_key]
        else:
            self.status = "Gone"
            self.room   = ""

        # Blob fields
        self.breed_id = r.u32()
        self._uid_int = r.u64()            # cat's own unique id (seed)
        self.unique_id = hex(self._uid_int)
        self.name = r.utf16str()

        # Optional post-name tag string (empty for most cats). Some fields below
        # are anchored to the byte immediately after this string.
        self.name_tag = r.str() or ""
        personality_anchor = r.pos

        # Possible parent UIDs — fixed-position attempt.
        # parse_save will run a blob scan as a fallback if these don't resolve.
        self._parent_uid_a = r.u64()
        self._parent_uid_b = r.u64()

        self.collar = r.str() or ""
        r.u32()

        r.skip(64)
        T = [r.u32() for _ in range(72)]
        self.base_palette_index = T[1] if len(T) > 1 else 0
        self.class_palette_index = T[2] if len(T) > 2 and T[2] < 256 else None
        self.body_parts = {
            "texture": T[0],
            "color_palette_index": self.base_palette_index,
            "class_palette_index": self.class_palette_index,
            "bodyShape": T[3],
            "headShape": T[8],
        }
        self.visual_mutation_slots = {
            slot_key: T[table_index]
            for slot_key, table_index, *_ in _VISUAL_MUTATION_FIELDS
            if table_index < len(T)
        }
        self.visual_mutation_variant_slots = {
            slot_key: T[table_index]
            for slot_key, table_index, *_ in _VISUAL_MUTATION_VARIANT_FIELDS
            if table_index < len(T)
        }
        visual_entries = _read_visual_mutation_entries(T)
        visual_items = _visual_mutation_chip_items(visual_entries)
        self.visual_mutation_entries = visual_entries
        self.visual_mutation_ids = [int(entry["mutation_id"]) for entry in visual_entries
                                    if not entry.get("is_defect")]
        # Separate normal mutations from birth defects
        visual_display_names = [text for text, _, is_def in visual_items if not is_def]
        defect_display_names = [text for text, _, is_def in visual_items if is_def]

        self.gender_token_fields = tuple(r.u32() for _ in range(3))
        raw_gender = r.str()
        self.gender_token = (raw_gender or "").strip().lower()
        # Authoritative sex enum near the name block:
        #   0 = male, 1 = female, 2 = undefined/both (ditto-like)
        sex_code = raw[personality_anchor] if personality_anchor < len(raw) else None
        gender_from_code = {0: "male", 1: "female", 2: "?"}.get(sex_code)
        if gender_from_code:
            self.gender = gender_from_code
            self.gender_source = "sex_code"
        else:
            self.gender = _normalize_gender(raw_gender)
            self.gender_source = "token_fallback"
        r.f64()

        self.stat_base = [r.u32() for _ in range(7)]
        self.stat_mod  = [r.i32() for _ in range(7)]
        self.stat_sec  = [r.i32() for _ in range(7)]

        # Visual mutations grant stat bonuses described in their detail
        # string (e.g. "+2 CON, -1 DEX"). The game applies these on top of
        # stat_base/stat_mod/stat_sec at the character-sheet level — the
        # save file does not roll them into any of those three fields — so
        # we parse the deltas from visual_mutation_entries and fold them
        # into total_stats here. base_stats is deliberately left alone: it
        # represents the cat's unmodified birth stats used for breeding
        # math, which mutations do not change.
        self.mutation_stat_bonus = _mutation_stat_bonus_from_entries(visual_entries)

        self.base_stats  = {n: self.stat_base[i] for i, n in enumerate(STAT_NAMES)}
        self.total_stats = {
            n: self.stat_base[i] + self.stat_mod[i] + self.stat_sec[i] + self.mutation_stat_bonus.get(n, 0)
            for i, n in enumerate(STAT_NAMES)
        }
        self.parsed_stats = dict(self.base_stats)

        # Personality stats (age, aggression, libido, inbredness).
        self.age         = None
        self.aggression  = None   # None = unknown
        self.libido      = None
        self.inbredness  = None
        def _read_personality(offset: int) -> Optional[float]:
            i = personality_anchor + offset
            if i + 8 > len(raw):
                return None
            try:
                v = struct.unpack_from('<d', raw, i)[0]
            except Exception:
                logger.debug("Cat %s: personality read failed at offset %d", cat_key, offset, exc_info=True)
                return None
            if not math.isfinite(v) or not (0.0 <= v <= 1.0):
                return None
            return float(v)

        self.libido = _read_personality(32)
        # Offset +40 stores the cat's sexuality as a [0.0, 1.0] float:
        # ~0.0 = straight, ~0.5 = bisexual, ~1.0 = gay.
        # This field was previously (incorrectly) labeled inbredness in the parser;
        # true inbredness is derived from ancestry (COI) and applied in parse_save.
        _sexuality_raw = _read_personality(40)
        self.sexuality_raw = _sexuality_raw    # raw [0,1] float: 0=straight, 0.5=bi, 1=gay
        self.inbredness = _sexuality_raw       # overwritten with true COI in parse_save; kept for override detection
        self.aggression = _read_personality(64)

        # Parsed baseline values (before any manual calibration overrides).
        self.parsed_gender = self.gender
        self.parsed_aggression = self.aggression
        self.parsed_libido = self.libido
        self.parsed_inbredness = self.inbredness

        # Relationship slots
        self._lover_uids = _read_db_key_candidates(raw, self.db_key, (48,), base_offset=personality_anchor)
        self._hater_uids = _read_db_key_candidates(raw, self.db_key, (72,), base_offset=personality_anchor)
        self.lovers:   list['Cat'] = []
        self.haters:   list['Cat'] = []
        self.children: list['Cat'] = []   # direct offspring; assigned by parse_save
        self.pedigree_was_repaired = False
        self.pedigree_cycle_breaks = 0

        # ── Ability run — anchored on "DefaultMove" ─────────────────────────
        curr = r.pos
        run_start = -1
        marker = r.find("DefaultMove", start=curr, end=min(curr + 600, len(raw)))
        if marker != -1 and marker >= 8:
            run_start = marker - 8
            try:
                lo = struct.unpack_from('<I', raw, run_start)[0]
                hi = struct.unpack_from('<I', raw, run_start + 4)[0]
                if hi != 0 or not (1 <= lo <= 96):
                    run_start = -1
            except Exception:
                logger.debug("Cat %s: ability marker scan failed at byte %d", cat_key, run_start, exc_info=True)
                run_start = -1

        if run_start != -1:
            r.seek(run_start)
            run_items: list[str] = []
            for _ in range(32):
                saved = r.pos
                item = r.str()
                if item is None or not _IDENT_RE.match(item):
                    r.seek(saved)
                    break
                run_items.append(item)

            self.abilities = [x for x in run_items[1:6] if _valid_str(x)]

            passives: list[str] = []
            for ri in run_items[10:]:
                if _valid_str(ri):
                    passives.append(ri)

            passive_tiers: dict[str, int] = {}
            try:
                passive1_tier = r.u32()
                if passives:
                    passive_tiers[passives[0]] = passive1_tier
            except Exception:
                logger.debug("Cat %s: passive1 tier read failed", cat_key, exc_info=True)

            disorders: list[str] = []
            for tail_idx in range(3):
                try:
                    item = r.str()
                except Exception:
                    logger.debug("Cat %s: tail slot %d read failed", cat_key, tail_idx, exc_info=True)
                    break
                if item is not None and _IDENT_RE.match(item) and _valid_str(item):
                    if tail_idx == 0:
                        if item not in passives:
                            passives.append(item)
                    else:
                        disorders.append(item)
                try:
                    slot_tier = r.u32()
                    if tail_idx == 0 and item is not None and _IDENT_RE.match(item) and _valid_str(item):
                        passive_tiers[item] = slot_tier
                except Exception:
                    logger.debug("Cat %s: tail slot %d tier read failed", cat_key, tail_idx, exc_info=True)
                    break

            self.passive_abilities = passives
            self.passive_tiers = passive_tiers
            self.disorders = disorders
            self.equipment = []

        else:
            # Fallback: old heuristic scan for any uppercase-starting ASCII string
            logger.debug("Cat %s: DefaultMove marker not found, using heuristic fallback", cat_key)
            found = -1
            for i in range(curr, min(curr + 500, len(raw) - 9)):
                length = struct.unpack_from('<I', raw, i)[0]
                if (0 < length < 64
                        and struct.unpack_from('<I', raw, i + 4)[0] == 0
                        and 65 <= raw[i + 8] <= 90):
                    found = i
                    break
            if found != -1:
                r.seek(found)

            self.abilities = [a for a in [r.str() for _ in range(6)] if _valid_str(a)]
            self.equipment = [s for s in [r.str() for _ in range(4)] if _valid_str(s)]

            self.passive_abilities = []
            self.passive_tiers = {}
            self.disorders = []
            first = r.str()
            if _valid_str(first):
                self.passive_abilities.append(first)
            for _ in range(13):
                if r.remaining() < 12:
                    break
                flag = r.u32()
                if flag == 0:
                    break
                p = r.str()
                if _valid_str(p):
                    self.passive_abilities.append(p)

        self.mutations = visual_display_names
        self.mutation_chip_items = [(text, tip) for text, tip, is_def in visual_items if not is_def]
        self.defects = defect_display_names
        self.defect_chip_items = [(text, tip) for text, tip, is_def in visual_items if is_def]

        # Extract age from creation_day stored near the end of the blob
        if current_day is not None:
            try:
                eternal_youth = any(d.lower() == "eternalyouth" for d in (getattr(self, "disorders", None) or []))
                creation_day_candidates: list[int] = []
                for offset_from_end in [103, 102, 104, 101, 105, 100, 106, 107, 108, 109, 110]:
                    pos = len(raw) - offset_from_end
                    if pos + 4 > len(raw) or pos < 0:
                        continue
                    creation_day = struct.unpack_from('<I', raw, pos)[0]
                    if 0 <= creation_day <= current_day:
                        creation_day_candidates.append(creation_day)
                self.age = _choose_age_from_creation_days(current_day, creation_day_candidates, eternal_youth)
            except Exception:
                logger.debug("Cat %s: age extraction failed", cat_key, exc_info=True)

        self.parsed_age = self.age

        # Extract class name from a fixed offset before blob end.
        # The class string ends exactly 115 bytes before the blob end
        # (stored as u32 length + u32 zero-pad + UTF-8 class name).
        self.cat_class: str = ""
        self.class_stat_mods: dict[str, int] = {}
        try:
            class_str_end = len(raw) - _CLASS_STRING_TAIL_OFFSET
            for class_len in range(3, 30):
                prefix_pos = class_str_end - class_len - 8
                if prefix_pos < 0:
                    break
                length = struct.unpack_from('<I', raw, prefix_pos)[0]
                zero = struct.unpack_from('<I', raw, prefix_pos + 4)[0]
                if length == class_len and zero == 0:
                    class_name = raw[prefix_pos + 8:prefix_pos + 8 + class_len].decode('utf-8', errors='replace')
                    if class_name != "Colorless":
                        self.cat_class = class_name
                        self.class_stat_mods = _CLASS_STAT_MODS.get(class_name, {})
                    break
        except Exception:
            logger.debug("Cat %s: class extraction failed", cat_key, exc_info=True)

        # Death day: an i64 at offset +8 from creation_day (len(raw) - 95).
        # Value of -1 means alive; a non-negative value is the day the cat died.
        self.death_day: Optional[int] = None
        try:
            dd_pos = len(raw) - 95
            if dd_pos >= 0 and dd_pos + 8 <= len(raw):
                dd = struct.unpack_from('<q', raw, dd_pos)[0]
                max_day = current_day if current_day is not None else 100000
                if 0 <= dd <= max_day:
                    self.death_day = dd
        except Exception:
            logger.debug("Cat %s: death_day extraction failed", cat_key, exc_info=True)

        # Dead cats that still appear in house_info are functionally gone.
        if self.death_day is not None and self.status == "In House":
            self.status = "Gone"

        # Derive sexuality string from the raw float at personality_anchor+40.
        if _sexuality_raw is None or _sexuality_raw < _SEXUALITY_BI_THRESHOLD:
            self.sexuality: str = "straight"
        elif _sexuality_raw >= _SEXUALITY_GAY_THRESHOLD:
            self.sexuality = "gay"
        else:
            self.sexuality = "bi"
        self.parsed_sexuality = self.sexuality

    # ── Display helpers ────────────────────────────────────────────────────

    @property
    def is_dead(self) -> bool:
        return self.death_day is not None

    @property
    def room_display(self) -> str:
        if not self.room or self.room == "Adventure":
            return self.room or ""
        return ROOM_DISPLAY.get(self.room, self.room)

    @property
    def gender_display(self) -> str:
        g = (self.gender or "").strip().lower()
        if g.startswith("male"):   return "M"
        if g.startswith("female"): return "F"
        return "?"

    @property
    def can_move(self) -> bool:
        return self.status == "In House"

    @property
    def has_adventured(self) -> bool:
        """Return True if this cat has likely been on at least one adventure.

        Checks for positive ``stat_mod`` values (per-stat int32 deltas written
        by adventure leveling) AND 4+ abilities (cats gain abilities through
        adventure leveling; freshly hatched cats have 2-3).

        Positive ``stat_mod`` alone is ambiguous — nightly cat fights also
        write positive values.  Requiring 4+ abilities eliminates nearly all
        fight-club false positives: across ~13K cats in test saves, only 6
        cats with 4+ abilities had zero stat gains.  See GitHub issue #81.

        If ``not_adventured_override`` is set, this returns False regardless.
        """
        if getattr(self, "not_adventured_override", False):
            return False
        stat_mod = getattr(self, "stat_mod", None) or []
        has_stat_gains = any(int(x) > 0 for x in stat_mod)
        if not has_stat_gains:
            return False
        return len(getattr(self, "abilities", None) or []) >= 4

    @property
    def short_name(self) -> str:
        """First word of name for compact displays."""
        return self.name.split()[0] if self.name else "?"

    def get_effective_texture(self, slot: str) -> Optional[int]:
        """Get effective texture ID for a body part slot, using variant as fallback.

        Variant textures represent the base/default limb when no mutation is present.
        If the primary texture is 0 or unset, falls back to the variant slot.
        """
        primary = self.visual_mutation_slots.get(slot)
        if primary and primary != 0:
            return primary
        variant_slot = f"{slot}_variant"
        variant = getattr(self, 'visual_mutation_variant_slots', {}).get(variant_slot)
        if variant and variant != 0:
            return variant
        return primary


# ── Ancestry helpers ──────────────────────────────────────────────────────────

def get_all_ancestors(cat: Optional[Cat], depth: int = 6) -> set:
    """Return all ancestor Cat objects up to `depth` generations."""
    if cat is None or depth <= 0:
        return set()
    ancestors: set[Cat] = set()
    seen: set[int] = {id(cat)}
    stack: list[tuple[Cat, int]] = [(cat, 0)]
    while stack:
        node, dist = stack.pop()
        if dist >= depth:
            continue
        for parent in (node.parent_a, node.parent_b):
            if parent is None:
                continue
            pid = id(parent)
            if pid in seen:
                continue
            seen.add(pid)
            ancestors.add(parent)
            stack.append((parent, dist + 1))
    return ancestors


def _ancestor_depths(cat: Optional[Cat], max_depth: int = 8) -> dict[Cat, int]:
    """
    Return a map of ancestor -> generational distance (minimum).
    Includes `cat` itself at depth 0, then parents at depth 1, etc.
    """
    if cat is None:
        return {}
    depths: dict[Cat, int] = {cat: 0}
    frontier: deque = deque([(cat, 0)])
    while frontier:
        cur, d = frontier.popleft()
        if d >= max_depth:
            continue
        for parent in (cur.parent_a, cur.parent_b):
            if parent is None:
                continue
            nd = d + 1
            prev = depths.get(parent)
            if prev is None or nd < prev:
                depths[parent] = nd
                frontier.append((parent, nd))
    return depths


def _ancestor_paths(start: Optional['Cat'], max_steps: int = 12) -> dict['Cat', list[tuple['Cat', ...]]]:
    """
    For each reachable ancestor, return all unique upward paths from `start`
    to that ancestor (inclusive). Paths never repeat the same cat.
    """
    if start is None:
        return {}
    paths: dict[Cat, list[tuple[Cat, ...]]] = {}
    stack: list[tuple[Cat, tuple[Cat, ...], frozenset[int]]] = [(start, (start,), frozenset({id(start)}))]
    while stack:
        node, path, seen = stack.pop()
        paths.setdefault(node, []).append(path)
        steps = len(path) - 1
        if steps >= max_steps:
            continue
        for parent in (node.parent_a, node.parent_b):
            if parent is None:
                continue
            pid = id(parent)
            if pid in seen:
                continue
            stack.append((parent, path + (parent,), seen | frozenset({pid})))
    return paths


def _build_ancestor_paths_batch(
    cats: list['Cat'],
    max_steps: int = 12,
) -> dict[int, dict['Cat', list[tuple['Cat', ...]]]]:
    """
    Compute ancestor paths for all cats using a shared memo keyed by id(cat).
    """
    ordered = sorted(cats, key=lambda c: c.generation)
    memo: dict[int, dict['Cat', list[tuple['Cat', ...]]]] = {}
    result: dict[int, dict['Cat', list[tuple['Cat', ...]]]] = {}

    for cat in ordered:
        paths: dict['Cat', list[tuple['Cat', ...]]] = {cat: [(cat,)]}

        for parent in (cat.parent_a, cat.parent_b):
            if parent is None:
                continue
            parent_paths = memo.get(id(parent))
            if parent_paths is None:
                parent_paths = _ancestor_paths(parent, max_steps)
                memo[id(parent)] = parent_paths

            for anc, path_list in parent_paths.items():
                for path in path_list:
                    if len(path) >= max_steps:
                        continue
                    new_path = (cat,) + path
                    paths.setdefault(anc, []).append(new_path)

        memo[id(cat)] = paths
        result[cat.db_key] = paths

    return result


def raw_coi(a: Optional['Cat'], b: Optional['Cat'], max_steps: int = 12) -> float:
    """
    Raw Coefficient of Inbreeding between two cats.
    """
    if a is None or b is None:
        return 0.0
    pa = _ancestor_paths(a, max_steps=max_steps)
    pb = _ancestor_paths(b, max_steps=max_steps)
    common = set(pa.keys()) & set(pb.keys())
    if not common:
        return 0.0
    coi = 0.0
    for anc in common:
        for path_a in pa[anc]:
            set_a = {id(x) for x in path_a}
            sa = len(path_a) - 1
            for path_b in pb[anc]:
                overlap = (set_a & {id(x) for x in path_b}) - {id(anc)}
                if overlap:
                    continue
                sb = len(path_b) - 1
                coi += 0.5 ** (sa + sb + 1)
    return coi


def _raw_coi_from_paths(
    pa: dict['Cat', list[tuple['Cat', ...]]],
    pb: dict['Cat', list[tuple['Cat', ...]]],
) -> float:
    common = set(pa.keys()) & set(pb.keys())
    if not common:
        return 0.0
    coi = 0.0
    for anc in common:
        for path_a in pa[anc]:
            set_a = {id(x) for x in path_a}
            sa = len(path_a) - 1
            for path_b in pb[anc]:
                overlap = (set_a & {id(x) for x in path_b}) - {id(anc)}
                if overlap:
                    continue
                sb = len(path_b) - 1
                coi += 0.5 ** (sa + sb + 1)
    return coi


_MIN_CONTRIB = 1e-10  # prune ancestors with contribution < 2^-33 (depth > 33)

def _ancestor_contributions(cat: Optional['Cat'], max_depth: int = 14) -> dict['Cat', float]:
    """
    For each reachable ancestor, return the sum of (0.5 ** depth) over every
    path from *cat* to that ancestor.
    """
    if cat is None:
        return {}
    contribs: dict['Cat', float] = {}
    stack: list[tuple['Cat', int, float]] = [(cat, 0, 1.0)]
    while stack:
        node, depth, prob = stack.pop()
        contribs[node] = contribs.get(node, 0.0) + prob
        if depth >= max_depth:
            continue
        half_prob = prob * 0.5
        if half_prob < _MIN_CONTRIB:
            continue
        for parent in (node.parent_a, node.parent_b):
            if parent is not None:
                stack.append((parent, depth + 1, half_prob))
    return contribs


def _build_ancestor_contribs_batch(
    cats: list['Cat'],
    max_depth: int = 14,
) -> dict[int, dict['Cat', float]]:
    """
    Batch-compute ancestor contribution dicts for all cats using a shared memo.
    """
    ordered = sorted(cats, key=lambda c: c.generation)
    memo: dict[int, dict['Cat', float]] = {}
    result: dict[int, dict['Cat', float]] = {}

    for cat in ordered:
        contribs: dict['Cat', float] = {cat: 1.0}

        for parent in (cat.parent_a, cat.parent_b):
            if parent is None:
                continue
            pc = memo.get(id(parent))
            if pc is None:
                pc = _ancestor_contributions(parent, max_depth)
                memo[id(parent)] = pc
            for anc, prob in pc.items():
                new_prob = prob * 0.5
                if new_prob < _MIN_CONTRIB:
                    continue
                contribs[anc] = contribs.get(anc, 0.0) + new_prob

        memo[id(cat)] = contribs
        result[cat.db_key] = {k: v for k, v in contribs.items() if k is not cat}

    return result


def _coi_from_contribs(
    ca: dict['Cat', float],
    cb: dict['Cat', float],
) -> float:
    """
    Compute raw COI from two ancestor-contribution dicts.
    """
    if not ca or not cb:
        return 0.0
    coi = 0.0
    if len(ca) > len(cb):
        ca, cb = cb, ca
    for anc, prob_a in ca.items():
        prob_b = cb.get(anc)
        if prob_b is not None:
            coi += prob_a * prob_b
    return coi * 0.5


_KINSHIP_CYCLE = object()  # sentinel for cycle detection


def _kinship(start_a: Optional['Cat'], start_b: Optional['Cat'],
             memo: dict[tuple[int, int], float]) -> float:
    """
    Memoised kinship coefficient between two cats.
    Converted to iterative stack-based evaluation to avoid RecursionError.
    """
    if start_a is None or start_b is None:
        return 0.0

    stack = [(start_a, start_b, False)]

    while stack:
        a, b, processed = stack.pop()
        
        ia, ib = id(a), id(b)
        key = (ia, ib) if ia <= ib else (ib, ia)

        if processed:
            # Both sides evaluated. Compute local value
            if a is b:
                ka = 0.0
                if a.parent_a and a.parent_b:
                    pak = (id(a.parent_a), id(a.parent_b)) if id(a.parent_a) <= id(a.parent_b) else (id(a.parent_b), id(a.parent_a))
                    v = memo.get(pak)
                    if v is not None and v is not _KINSHIP_CYCLE: ka = v
                memo[key] = (1.0 + ka) / 2.0
            elif a.generation > b.generation:
                k1, k2 = 0.0, 0.0
                if a.parent_a:
                    k1k = (id(a.parent_a), id(b)) if id(a.parent_a) <= id(b) else (id(b), id(a.parent_a))
                    v = memo.get(k1k)
                    if v is not None and v is not _KINSHIP_CYCLE: k1 = v
                if a.parent_b:
                    k2k = (id(a.parent_b), id(b)) if id(a.parent_b) <= id(b) else (id(b), id(a.parent_b))
                    v = memo.get(k2k)
                    if v is not None and v is not _KINSHIP_CYCLE: k2 = v
                memo[key] = (k1 + k2) / 2.0
            else:
                k1, k2 = 0.0, 0.0
                if b.parent_a:
                    k1k = (id(a), id(b.parent_a)) if id(a) <= id(b.parent_a) else (id(b.parent_a), id(a))
                    v = memo.get(k1k)
                    if v is not None and v is not _KINSHIP_CYCLE: k1 = v
                if b.parent_b:
                    k2k = (id(a), id(b.parent_b)) if id(a) <= id(b.parent_b) else (id(b.parent_b), id(a))
                    v = memo.get(k2k)
                    if v is not None and v is not _KINSHIP_CYCLE: k2 = v
                memo[key] = (k1 + k2) / 2.0
            continue

        cached = memo.get(key)
        if cached is not None:
            continue

        memo[key] = _KINSHIP_CYCLE
        stack.append((a, b, True))

        if a is b:
            if a.parent_a and a.parent_b:
                stack.append((a.parent_a, a.parent_b, False))
        elif a.generation > b.generation:
            if a.parent_a:
                stack.append((a.parent_a, b, False))
            if a.parent_b:
                stack.append((a.parent_b, b, False))
        else:
            if b.parent_a:
                stack.append((a, b.parent_a, False))
            if b.parent_b:
                stack.append((a, b.parent_b, False))

    ska, skb = id(start_a), id(start_b)
    skey = (ska, skb) if ska <= skb else (skb, ska)
    v = memo.get(skey)
    return 0.0 if v is None or v is _KINSHIP_CYCLE else v


def kinship_coi(a: Optional['Cat'], b: Optional['Cat'],
                memo: Optional[dict] = None) -> float:
    """
    COI of a hypothetical offspring of a x b, using memoised kinship.
    """
    if a is None or b is None:
        return 0.0
    if memo is None:
        memo = {}
    return _kinship(a, b, memo)


def _malady_breakdown(coi: float) -> tuple[float, float, float]:
    """
    Return (disorder_chance, part_defect_chance, combined_chance) from game logic.
    """
    disorder = 0.02 + 0.4 * min(max(coi - 0.20, 0.0), 1.0)
    defect = min(1.5 * coi, 1.0) if coi > 0.05 else 0.0
    combined = 1.0 - (1.0 - disorder) * (1.0 - defect)
    return disorder, defect, combined


def _combined_malady_chance(coi: float) -> float:
    """
    Probability that AT LEAST ONE birth defect occurs.
    """
    return _malady_breakdown(coi)[2]


def risk_percent(a: Optional['Cat'], b: Optional['Cat'],
                 memo: Optional[dict] = None) -> float:
    """Combined birth-defect probability as a percentage, clamped to [0, 100]."""
    coi = kinship_coi(a, b, memo)
    return max(0.0, min(100.0, _combined_malady_chance(coi) * 100.0))


def find_common_ancestors(a: Cat, b: Cat, depth: int = 6) -> list[Cat]:
    """Return cats that appear in both ancestry trees."""
    return list(get_all_ancestors(a, depth=depth) & get_all_ancestors(b, depth=depth))


def shared_ancestor_counts(a: Cat, b: Cat, recent_depth: int = 3, max_depth: int = 8) -> tuple[int, int]:
    """
    Return (total_shared, recent_shared) common ancestor counts.
    """
    da = _ancestor_depths(a, max_depth=max_depth)
    db = _ancestor_depths(b, max_depth=max_depth)
    common = set(da.keys()) & set(db.keys())
    if not common:
        return 0, 0
    recent_shared = sum(1 for anc in common if da[anc] <= recent_depth and db[anc] <= recent_depth)
    return len(common), recent_shared


def get_parents(cat: Cat) -> list[Cat]:
    return [p for p in (cat.parent_a, cat.parent_b) if p is not None]


def get_grandparents(cat: Cat) -> list[Cat]:
    gp = []
    for p in get_parents(cat):
        gp.extend(get_parents(p))
    return gp


def can_breed(a: Cat, b: Cat) -> tuple[bool, str]:
    """Return (ok, reason). reason is non-empty only when ok is False.

    Game rule (per wiki): compatibility = 0.15 * charisma * libido * lover_mult * sexuality_mult
    where sexuality_mult = cos(0.5*pi*sexuality_coeff) for opposite-sex pairs
    and sin(0.5*pi*sexuality_coeff) for same-sex pairs.  A breeding attempt
    succeeds when compatibility > 0.05.

    So a "straight" cat (coeff ~0) has sin ~0 → same-sex compatibility ~0 →
    hard block.  A bi/gay cat has non-trivial sin and *can* produce offspring
    with a same-sex partner.  Symmetric rule for gay+gay opposite-sex.
    """
    if a is b:
        return False, "Cannot pair a cat with itself"
    ga = (a.gender or "?").strip().lower()
    gb = (b.gender or "?").strip().lower()

    if ga == "?" or gb == "?":
        return True, ""

    same_gender = ga == gb
    if same_gender:
        sa = (getattr(a, "sexuality", None) or "straight").lower()
        sb = (getattr(b, "sexuality", None) or "straight").lower()
        if sa == "straight" and sb == "straight":
            return False, f"Both cats are straight — very low same-sex compatibility"
        if sa == "straight":
            return True, f"{a.name} is straight — very low same-sex compatibility"
        if sb == "straight":
            return True, f"{b.name} is straight — very low same-sex compatibility"
        return True, ""

    # Opposite-sex pair
    sa = (getattr(a, "sexuality", None) or "straight").lower()
    sb = (getattr(b, "sexuality", None) or "straight").lower()
    if sa == "gay" and sb == "gay":
        return False, f"Both cats are gay — very low opposite-sex compatibility"
    if sa == "gay":
        return True, f"{a.name} is gay — very low opposite-sex compatibility"
    if sb == "gay":
        return True, f"{b.name} is gay — very low opposite-sex compatibility"
    return True, ""


def _is_hater_pair(a: 'Cat', b: 'Cat') -> bool:
    return b in getattr(a, 'haters', []) or a in getattr(b, 'haters', [])


# ── Save-file helpers ─────────────────────────────────────────────────────────

def _parse_furniture_entry(blob: bytes, key: int) -> FurnitureItem:
    """Parse a single row from the furniture table."""
    if len(blob) < 12 + 16:
        raise ValueError("Furniture blob too short")

    version = struct.unpack_from("<I", blob, 0)[0]
    item_name_len = struct.unpack_from("<Q", blob, 4)[0]
    item_name_start = 12
    item_name_end = item_name_start + int(item_name_len)
    if item_name_end > len(blob):
        raise ValueError("Furniture item name overruns blob")

    item_name = blob[item_name_start:item_name_end].decode("utf-8", errors="ignore")

    header_start = item_name_end
    header_end = header_start + 16
    if header_end > len(blob):
        raise ValueError("Furniture room header overruns blob")

    header_fields = struct.unpack_from("<4I", blob, header_start)
    room_name_len = int(header_fields[2])
    room_start = header_end
    room_end = room_start + room_name_len
    if room_end > len(blob):
        raise ValueError("Furniture room name overruns blob")

    room = blob[room_start:room_end].decode("utf-8", errors="ignore")

    tail_start = room_end
    placement_fields: list[int] = []
    while tail_start + 4 <= len(blob):
        placement_fields.append(struct.unpack_from("<i", blob, tail_start)[0])
        tail_start += 4

    return FurnitureItem(
        key=int(key),
        version=version,
        item_name=item_name,
        room=room,
        header_fields=header_fields,
        placement_fields=tuple(placement_fields),
        trailing_bytes=blob[tail_start:],
    )


def _get_furniture_items(conn) -> list[FurnitureItem]:
    try:
        rows = conn.execute("SELECT key, data FROM furniture ORDER BY key").fetchall()
    except Exception:
        return []

    items: list[FurnitureItem] = []
    for key, blob in rows:
        try:
            items.append(_parse_furniture_entry(blob, int(key)))
        except Exception:
            logger.warning("Failed to parse furniture key=%s", key, exc_info=True)
    return items

def _get_house_info(conn) -> dict:
    row = conn.execute("SELECT data FROM files WHERE key = 'house_state'").fetchone()
    if not row or len(row[0]) < 8:
        return {}
    data  = row[0]
    count = struct.unpack_from('<I', data, 4)[0]
    pos   = 8
    result = {}
    for _ in range(count):
        if pos + 8 > len(data):
            break
        cat_key  = struct.unpack_from('<I', data, pos)[0]
        pos += 8
        room_len = struct.unpack_from('<I', data, pos)[0]
        pos += 8
        room_name = ""
        if room_len > 0:
            room_name = data[pos:pos + room_len].decode('ascii', errors='ignore')
            pos += room_len
        pos += 24
        result[cat_key] = room_name
    return result


def _get_unlocked_house_rooms(conn, house: dict | None = None, furniture: list[FurnitureItem] | None = None) -> list[str]:
    """Return the house rooms that are unlocked in this save.

    Merges two sources: rooms observed in ``house_state``/furniture (concrete
    evidence a room is in use) and the ``house_unlocks`` blob (which records
    unlock flags).  The union ensures newly unlocked but still-empty rooms
    (no cats or furniture placed yet) are not missed.
    """
    rooms: set[str] = set()

    if isinstance(house, dict):
        rooms.update(room for room in house.values() if room in ROOM_KEYS)

    if furniture:
        rooms.update(
            item.room for item in furniture
            if getattr(item, "room", None) in ROOM_KEYS
        )

    row = conn.execute("SELECT data FROM files WHERE key = 'house_unlocks'").fetchone()
    if row and row[0]:
        tokens = {
            m.group(0).decode("ascii", errors="ignore")
            for m in re.finditer(rb"[A-Za-z][A-Za-z0-9_]+", row[0])
        }
        if tokens & {"Default", "House3", "MediumHouse", "LargeHouse"}:
            rooms.add("Floor1_Large")
        if tokens & {"House3", "MediumHouse_SmallRoom", "LargeHouse"}:
            rooms.add("Floor1_Small")
        if "SmallHouse_Attic" in tokens:
            rooms.add("Attic")
        if tokens & {"LargeHouse", "LargeHouse_Floor2Large"}:
            rooms.add("Floor2_Large")
        if "LargeHouse_Floor2Small" in tokens:
            rooms.add("Floor2_Small")

    return [room for room in ROOM_KEYS if room in rooms]


def _get_adventure_keys(conn) -> set:
    keys = set()
    try:
        row = conn.execute("SELECT data FROM files WHERE key = 'adventure_state'").fetchone()
        if not row or len(row[0]) < 8:
            return keys
        data  = row[0]
        count = struct.unpack_from('<I', data, 4)[0]
        pos   = 8
        for _ in range(count):
            if pos + 8 > len(data):
                break
            val = struct.unpack_from('<Q', data, pos)[0]
            pos += 8
            cat_key = (val >> 32) & 0xFFFF_FFFF
            if cat_key:
                keys.add(cat_key)
    except Exception:
            logger.warning("Failed to parse adventure_state blob", exc_info=True)
    return keys


def _read_parallel_hash_table(
    buffer: bytes,
    offset: int,
    unpack_string: str,
    unpack_size: int,
) -> tuple[list[tuple], int]:
    """
    Read one parallel-hashmap table from a serialized blob.

    This mirrors the structure reverse-engineered in the analysis tools:
    24-byte header, control bytes, compacted data table, then growth_left.
    """
    if offset + 24 > len(buffer):
        return [], len(buffer)

    first_qword = struct.unpack_from("<Q", buffer, offset)[0]
    if first_qword < 0xFFFFFFFFFFFFFFF5:
        # Older layout without the version field.
        size, capacity = struct.unpack_from("<QQ", buffer, offset)
        table_start = offset + 16
    else:
        _, size, capacity = struct.unpack_from("<QQQ", buffer, offset)
        table_start = offset + 24

    _ = size  # kept for parity with the reverse-engineering notes
    hash_table_size = capacity + 1 + 16
    if table_start + hash_table_size > len(buffer):
        return [], len(buffer)

    hash_table = struct.unpack_from(f"<{capacity}B", buffer, table_start)
    data_start = table_start + hash_table_size
    rows: list[tuple] = []
    for i in range(capacity):
        if hash_table[i] <= 0x7F:
            row_start = data_start + i * unpack_size
            if row_start + unpack_size > len(buffer):
                break
            rows.append(struct.unpack_from(unpack_string, buffer, row_start))

    next_offset = data_start + capacity * unpack_size + 8
    return rows, next_offset


def _parse_pedigree_tables(
    conn,
) -> tuple[
    dict[int, tuple[Optional[int], Optional[int]]],
    dict[tuple[int, int], float],
    set[int],
]:
    """
    Parse the pedigree blob from the files table.

    The blob is a concatenation of parallel-hashmap tables:
    - pedigree rows: child -> parents + cached COI
    - COI memo rows: parent pair -> cached COI
    - accessible cat keys
    """
    try:
        row = conn.execute("SELECT data FROM files WHERE key='pedigree'").fetchone()
        if not row:
            return {}, {}, set()
        data = row[0]
    except Exception:
        logger.warning("Failed to read pedigree blob", exc_info=True)
        return {}, {}, set()

    MAX_KEY = 1_000_000
    ped_map: dict[int, tuple[Optional[int], Optional[int]]] = {}
    coi_memos: dict[tuple[int, int], float] = {}
    accessible_cats: set[int] = set()

    rows, offset = _read_parallel_hash_table(data, 0, "<qqqd", 32)
    for cat_k, pa_k, pb_k, _coi in rows:
        cat_key = int(cat_k)
        if cat_key <= 0 or cat_key > MAX_KEY:
            continue

        pa = int(pa_k) if 0 < int(pa_k) <= MAX_KEY else None
        pb = int(pb_k) if 0 < int(pb_k) <= MAX_KEY else None
        existing = ped_map.get(cat_key)
        if existing is None:
            ped_map[cat_key] = (pa, pb)
            continue

        merged = (
            pa if pa is not None else existing[0],
            pb if pb is not None else existing[1],
        )
        if merged != existing:
            ped_map[cat_key] = merged

    memo_rows, offset = _read_parallel_hash_table(data, offset, "<qqd", 24)
    for pa_k, pb_k, coi in memo_rows:
        pa = int(pa_k)
        pb = int(pb_k)
        if not (0 < pa <= MAX_KEY and 0 < pb <= MAX_KEY):
            continue
        if not math.isfinite(float(coi)):
            continue
        key = _pair_key_u64(pa, pb)
        coi_memos[key] = float(coi)

    access_rows, _ = _read_parallel_hash_table(data, offset, "<q", 8)
    for (cat_k,) in access_rows:
        cat_key = int(cat_k)
        if 0 < cat_key <= MAX_KEY:
            accessible_cats.add(cat_key)

    return ped_map, coi_memos, accessible_cats


def _parse_pedigree(conn) -> dict:
    """Return the child -> parent pedigree map only."""
    return _parse_pedigree_tables(conn)[0]


def parse_save(path: str) -> SaveData:
    conn  = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    house = _get_house_info(conn)
    adv   = _get_adventure_keys(conn)
    furniture = _get_furniture_items(conn)
    unlocked_house_rooms = _get_unlocked_house_rooms(conn, house=house, furniture=furniture)
    rows  = conn.execute("SELECT key, data FROM cats").fetchall()
    ped_map, pedigree_coi_memos, accessible_cats = _parse_pedigree_tables(conn)
    current_day_row = conn.execute("SELECT data FROM properties WHERE key='current_day'").fetchone()
    current_day = current_day_row[0] if current_day_row else None
    conn.close()

    cats, errors = [], []
    for key, blob in rows:
        try:
            cats.append(Cat(blob, key, house, adv, current_day))
        except Exception as e:
            logger.warning("Failed to parse cat key=%s: %s", key, e, exc_info=True)
            errors.append((key, str(e)))

    key_to_cat: dict = {c.db_key: c for c in cats}

    for cat in cats:
        pa = pb = None
        pa_k, pb_k = _resolve_parent_uids(cat, ped_map)
        if pa_k is not None:
            pa = key_to_cat.get(pa_k)
        if pb_k is not None:
            pb = key_to_cat.get(pb_k)
        if pa is cat:
            pa = None
        if pb is cat:
            pb = None
        cat.parent_a = pa
        cat.parent_b = pb

        cat.lovers = []
        for key in getattr(cat, "_lover_uids", []):
            other = key_to_cat.get(key)
            if other is not None and other is not cat and other not in cat.lovers:
                cat.lovers.append(other)

        cat.haters = []
        for key in getattr(cat, "_hater_uids", []):
            other = key_to_cat.get(key)
            if other is not None and other is not cat and other not in cat.haters:
                cat.haters.append(other)

    _break_pedigree_cycles(cats)

    # Build children bottom-up
    for cat in cats:
        cat.children = []
    for cat in cats:
        for parent in (cat.parent_a, cat.parent_b):
            if parent is not None and cat not in parent.children:
                parent.children.append(cat)

    # Compute generation depth (O(V) using explicit stack DFS)
    for c in cats:
        c.generation = -1

    def _calc_gen(start_cat):
        if start_cat.generation >= 0:
            return
        
        stack = [(start_cat, False)]
        while stack:
            c, children_processed = stack.pop()
            
            if children_processed:
                pa_g = c.parent_a.generation if c.parent_a is not None else -1
                pb_g = c.parent_b.generation if c.parent_b is not None else -1
                
                # If both parents are missing/broken in the graph, it's a stray (gen 0).
                if c.parent_a is None and c.parent_b is None:
                    c.generation = 0
                else:
                    g = max(pa_g, pb_g) + 1
                    c.generation = g if (pa_g >= 0 or pb_g >= 0) else 0
                continue
                
            stack.append((c, True))
            for p in (c.parent_a, c.parent_b):
                if p is not None and p.generation < 0:
                    stack.append((p, False))

    for c in cats:
        _calc_gen(c)

    return SaveData(
        cats=cats,
        errors=errors,
        unlocked_house_rooms=unlocked_house_rooms,
        furniture=furniture,
        pedigree_map=ped_map,
        pedigree_coi_memos=pedigree_coi_memos,
        accessible_cats=accessible_cats,
    )


def find_save_files(root_dir: str) -> list[str]:
    saves = []
    base  = Path(root_dir)
    if not base.is_dir():
        return saves
    for profile in base.iterdir():
        saves_dir = profile / "saves"
        if saves_dir.is_dir():
            saves.extend(str(p) for p in saves_dir.glob("*.sav"))
    saves.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return saves
