"""
Save parser and core data model for Mewgenics Breeding Manager.

Extracted from mewgenics_manager.py to enable independent testing and
separation of parsing/genetics logic from the Qt UI.
"""

import struct
import sqlite3
import lz4.block
import re
import os
import math
import logging
from pathlib import Path
from typing import Optional
from collections import deque

try:
    from visual_mutation_catalog import load_visual_mutation_names
except ModuleNotFoundError:
    try:
        from tools.visual_mutation_catalog import load_visual_mutation_names
    except ModuleNotFoundError:
        # Support running from src/ directory
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
        from tools.visual_mutation_catalog import load_visual_mutation_names

logger = logging.getLogger("mewgenics.parser")

# ── Helpers ───────────────────────────────────────────────────────────────────

_JUNK_STRINGS = frozenset({"none", "null", "", "defaultmove", "default_move"})
_IDENT_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')

STAT_NAMES = ["STR", "DEX", "CON", "INT", "SPD", "CHA", "LCK"]

ROOM_DISPLAY = {
    "Floor1_Large":   "Ground Floor Left",
    "Floor1_Small":   "Ground Floor Right",
    "Floor2_Large":   "Second Floor Right",
    "Floor2_Small":   "Second Floor Left",
    "Attic":          "Attic",
}

ROOM_KEYS = tuple(ROOM_DISPLAY.keys())

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

# Populated at runtime via set_visual_mut_data() from the main module.
_VISUAL_MUT_DATA: dict[str, dict[int, tuple[str, str]]] = {}


def set_visual_mut_data(data: dict[str, dict[int, tuple[str, str]]]):
    """Update the visual mutation lookup data (called after gpak loading)."""
    global _VISUAL_MUT_DATA
    _VISUAL_MUT_DATA = data


def _read_visual_mutation_entries(table: list[int]) -> list[dict[str, object]]:
    fallback_names = load_visual_mutation_names()
    entries: list[dict[str, object]] = []
    for slot_key, table_index, group_key, gpak_category, fallback_part, slot_label in _VISUAL_MUTATION_FIELDS:
        mutation_id = table[table_index] if table_index < len(table) else 0
        if mutation_id in (0, 0xFFFF_FFFF):
            continue

        display_name = ""
        detail = ""
        gpak_info = _VISUAL_MUT_DATA.get(gpak_category, {}).get(mutation_id)
        if gpak_info:
            raw_name, stat_desc = gpak_info
            if re.match(r'^Mutation \d+$', raw_name):
                display_name = f"{_VISUAL_MUTATION_PART_LABELS.get(group_key, slot_label)} Mutation"
            else:
                display_name = raw_name
            detail = stat_desc
        else:
            fallback_name = fallback_names.get((fallback_part, mutation_id))
            if fallback_name is None:
                if mutation_id < 300:
                    continue
                if mutation_id == 0xFFFF_FFFE:
                    fallback_name = f"No {_VISUAL_MUTATION_PART_LABELS.get(group_key, slot_label)}"
                else:
                    fallback_name = f"{_VISUAL_MUTATION_PART_LABELS.get(group_key, slot_label)} {mutation_id}"
            display_name = fallback_name

        is_defect = (700 <= mutation_id <= 706) or mutation_id == 0xFFFF_FFFE

        display_name = str(display_name).strip() or f"{slot_label} {mutation_id}"
        entries.append({
            "slot_key": slot_key,
            "slot_label": slot_label,
            "group_key": group_key,
            "part_label": _VISUAL_MUTATION_PART_LABELS.get(group_key, slot_label),
            "mutation_id": mutation_id,
            "name": display_name,
            "detail": str(detail).strip(),
            "is_defect": is_defect,
        })
    return entries


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
        is_defect = bool(items[0].get("is_defect", False))
        title_label = part_label if len(slot_labels) > 1 else str(items[0]["slot_label"])
        kind = "Birth Defect" if is_defect else "Mutation"
        id_str = "-2" if mutation_id == 0xFFFF_FFFE else str(mutation_id)
        tooltip = f"{title_label} {kind} (ID {id_str})\n{name}"
        if detail:
            tooltip = f"{tooltip}\n{detail}"
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
        if text_counts[text] > 1:
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
    stim = max(0.0, min(100.0, float(stimulation)))
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
    passive_abilities: list[str]

    def __init__(self, blob: bytes, cat_key: int, house_info: dict, adventure_keys: set, current_day: Optional[int] = None):
        uncomp_size = struct.unpack('<I', blob[:4])[0]
        raw = lz4.block.decompress(blob[4:], uncompressed_size=uncomp_size)
        r   = BinaryReader(raw)
        self._raw = raw   # kept for parent-UID blob scan in parse_save

        self.db_key = cat_key

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
        self.body_parts = {"texture": T[0], "bodyShape": T[3], "headShape": T[8]}
        self.visual_mutation_slots = {
            slot_key: T[table_index]
            for slot_key, table_index, *_ in _VISUAL_MUTATION_FIELDS
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

        self.base_stats  = {n: self.stat_base[i] for i, n in enumerate(STAT_NAMES)}
        self.total_stats = {n: self.stat_base[i] + self.stat_mod[i] + self.stat_sec[i]
                            for i, n in enumerate(STAT_NAMES)}

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
        self.inbredness = _sexuality_raw   # kept for COI override detection; overwritten in parse_save
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

        # ── Ability run — anchored on "DefaultMove" ─────────────────────────
        curr = r.pos
        run_start = -1
        for i in range(curr, min(curr + 600, len(raw) - 19)):
            lo = struct.unpack_from('<I', raw, i)[0]
            hi = struct.unpack_from('<I', raw, i + 4)[0]
            if hi != 0 or not (1 <= lo <= 96):
                continue
            try:
                cand = raw[i + 8: i + 8 + lo].decode('ascii')
                if cand == 'DefaultMove':
                    run_start = i
                    break
            except Exception:
                logger.debug("Cat %s: ability marker scan failed at byte %d", cat_key, i, exc_info=True)
                continue

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

            try:
                r.u32()   # passive1 tier — discard
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
                    r.u32()
                except Exception:
                    logger.debug("Cat %s: tail slot %d tier read failed", cat_key, tail_idx, exc_info=True)
                    break

            self.passive_abilities = passives
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
                for offset_from_end in [103, 102, 104, 101, 105, 100, 106, 107, 108, 109, 110]:
                    pos = len(raw) - offset_from_end
                    if pos + 4 > len(raw) or pos < 0:
                        continue
                    creation_day = struct.unpack_from('<I', raw, pos)[0]
                    if 0 <= creation_day <= current_day:
                        age = current_day - creation_day
                        if 0 <= age <= 100:
                            self.age = age
                            break
            except Exception:
                logger.debug("Cat %s: age extraction failed", cat_key, exc_info=True)

        self.parsed_age = self.age

        # Derive sexuality string from the raw float at personality_anchor+40.
        if _sexuality_raw is None or _sexuality_raw < _SEXUALITY_BI_THRESHOLD:
            self.sexuality: str = "straight"
        elif _sexuality_raw >= _SEXUALITY_GAY_THRESHOLD:
            self.sexuality = "gay"
        else:
            self.sexuality = "bi"

    # ── Display helpers ────────────────────────────────────────────────────

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
    def short_name(self) -> str:
        """First word of name for compact displays."""
        return self.name.split()[0] if self.name else "?"


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


def _kinship(a: Optional['Cat'], b: Optional['Cat'],
             memo: dict[tuple[int, int], float]) -> float:
    """
    Memoised kinship coefficient between two cats.
    """
    if a is None or b is None:
        return 0.0
    ia, ib = id(a), id(b)
    key = (ia, ib) if ia <= ib else (ib, ia)
    cached = memo.get(key)
    if cached is not None:
        return 0.0 if cached is _KINSHIP_CYCLE else cached
    memo[key] = _KINSHIP_CYCLE  # mark in-progress to detect cycles
    if a is b:
        result = (1.0 + _kinship(a.parent_a, a.parent_b, memo)) / 2.0
    else:
        if a.generation > b.generation:
            result = (_kinship(a.parent_a, b, memo) + _kinship(a.parent_b, b, memo)) / 2.0
        else:
            result = (_kinship(a, b.parent_a, memo) + _kinship(a, b.parent_b, memo)) / 2.0
    memo[key] = result
    return result


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


def find_common_ancestors(a: Cat, b: Cat) -> list[Cat]:
    """Return cats that appear in both ancestry trees."""
    return list(get_all_ancestors(a) & get_all_ancestors(b))


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
    """Return (ok, reason). reason is non-empty only when ok is False."""
    if a is b:
        return False, "Cannot pair a cat with itself"
    ga = (a.gender or "?").strip().lower()
    gb = (b.gender or "?").strip().lower()

    # Sexuality check
    sa = (getattr(a, "sexuality", None) or "straight").lower()
    sb = (getattr(b, "sexuality", None) or "straight").lower()

    # "bi" sexuality can breed with anyone
    if sa == "bi" or sb == "bi":
        if ga != "?" and gb != "?":
            return True, ""

    if ga == "?" or gb == "?":
        return True, ""

    if ga != "?" and gb != "?":
        same_gender = ga == gb
        if sa == "gay" and not same_gender:
            return False, f"{a.name} is gay — needs same-gender partner"
        if sb == "gay" and not same_gender:
            return False, f"{b.name} is gay — needs same-gender partner"
        if sa == "straight" and same_gender:
            return False, f"{a.name} is straight — needs opposite-gender partner"
        if sb == "straight" and same_gender:
            return False, f"{b.name} is straight — needs opposite-gender partner"
    if sa == "gay" or sb == "gay":
        return True, ""
    if ga != gb and {ga, gb} == {"male", "female"}:
        return True, ""
    if ga == "female" and gb == "female":
        return False, "Both cats are female — cannot produce offspring"
    if ga == "male" and gb == "male":
        return False, "Both cats are male — cannot produce offspring"
    return False, "Cats have incompatible genders — cannot produce offspring"


def _is_hater_pair(a: 'Cat', b: 'Cat') -> bool:
    return b in getattr(a, 'haters', []) or a in getattr(b, 'haters', [])


# ── Save-file helpers ─────────────────────────────────────────────────────────

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


def _get_unlocked_house_rooms(conn) -> list[str]:
    row = conn.execute("SELECT data FROM files WHERE key = 'house_unlocks'").fetchone()
    if not row or not row[0]:
        return []

    tokens = {
        m.group(0).decode("ascii", errors="ignore")
        for m in re.finditer(rb"[A-Za-z][A-Za-z0-9_]+", row[0])
    }
    unlocked = set()

    if tokens & {"Default", "House3", "MediumHouse", "LargeHouse"}:
        unlocked.add("Floor1_Large")
    if tokens & {"House3", "MediumHouse_SmallRoom", "LargeHouse"}:
        unlocked.add("Floor1_Small")
    if "SmallHouse_Attic" in tokens:
        unlocked.add("Attic")
    if tokens & {"MediumHouse", "LargeHouse_Floor2Large"}:
        unlocked.add("Floor2_Large")
    if "LargeHouse_Floor2Small" in tokens:
        unlocked.add("Floor2_Small")

    return [room for room in ROOM_KEYS if room in unlocked]


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


def _parse_pedigree(conn) -> dict:
    """
    Parse the pedigree blob from the files table.
    Each 32-byte entry: u64 cat_key, u64 parent_a_key, u64 parent_b_key, u64 extra.
    """
    try:
        row = conn.execute("SELECT data FROM files WHERE key='pedigree'").fetchone()
        if not row:
            return {}
        data = row[0]
    except Exception:
        logger.warning("Failed to read pedigree blob", exc_info=True)
        return {}

    NULL = 0xFFFF_FFFF_FFFF_FFFF
    MAX_KEY = 1_000_000
    ped_map: dict = {}

    for pos in range(8, len(data) - 31, 32):
        cat_k, pa_k, pb_k, extra = struct.unpack_from('<QQQQ', data, pos)
        if cat_k == 0 or cat_k == NULL or cat_k > MAX_KEY:
            continue
        pa = int(pa_k) if pa_k != NULL and 0 < pa_k <= MAX_KEY else None
        pb = int(pb_k) if pb_k != NULL and 0 < pb_k <= MAX_KEY else None
        cat_key = int(cat_k)

        existing = ped_map.get(cat_key)
        if existing is None:
            ped_map[cat_key] = (pa, pb)
        elif existing[0] is None or existing[1] is None:
            if pa is not None and pb is not None:
                ped_map[cat_key] = (pa, pb)

    return ped_map


def parse_save(path: str) -> tuple[list, list, list[str]]:
    conn  = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    house = _get_house_info(conn)
    unlocked_house_rooms = _get_unlocked_house_rooms(conn)
    adv   = _get_adventure_keys(conn)
    rows  = conn.execute("SELECT key, data FROM cats").fetchall()
    ped_map = _parse_pedigree(conn)
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
        pa: Optional[Cat] = None
        pb: Optional[Cat] = None
        if cat.db_key in ped_map:
            pa_k, pb_k = ped_map[cat.db_key]
            pa = key_to_cat.get(pa_k)
            pb = key_to_cat.get(pb_k)
            if pa is cat: pa = None
            if pb is cat: pb = None
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

    # Build children bottom-up
    for cat in cats:
        cat.children = []
    for cat in cats:
        for parent in (cat.parent_a, cat.parent_b):
            if parent is not None and cat not in parent.children:
                parent.children.append(cat)

    # Compute generation depth (iterative; handles cycles)
    for c in cats:
        c.generation = 0 if (c.parent_a is None and c.parent_b is None) else -1

    for _ in range(len(cats) + 1):
        changed = False
        for c in cats:
            pa_g = c.parent_a.generation if c.parent_a is not None else -1
            pb_g = c.parent_b.generation if c.parent_b is not None else -1

            if pa_g >= 0 or pb_g >= 0:
                g = max(pa_g, pb_g) + 1
                if c.generation != g:
                    c.generation = g
                    changed = True

        if not changed:
            break

    for c in cats:
        if c.generation < 0:
            c.generation = 0

    return cats, errors, unlocked_house_rooms


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
