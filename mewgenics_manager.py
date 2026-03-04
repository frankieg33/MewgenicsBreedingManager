#!/usr/bin/env python3
"""
Mewgenics Breeding Manager
External viewer for cat stats, room locations, and breeding pairs.
Parsing logic based on pzx521521/mewgenics-save-editor.

Requirements: pip install PySide6 lz4
"""

import sys
import re
import struct
import sqlite3
import lz4.block
import os
from pathlib import Path
from typing import Optional

_IDENT_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QTableView, QPushButton, QLabel, QFileDialog, QHeaderView,
    QAbstractItemView, QSplitter, QFrame, QDialog, QGridLayout, QSizePolicy,
    QLineEdit, QListWidget, QListWidgetItem, QScrollArea, QToolButton,
    QTableWidget, QTableWidgetItem, QStyledItemDelegate, QStyle, QStyleOptionViewItem,
)
from PySide6.QtCore import (
    Qt, QAbstractTableModel, QModelIndex, QSortFilterProxyModel,
    QFileSystemWatcher, QItemSelectionModel, QSize,
)
from PySide6.QtGui import QColor, QBrush, QAction, QPalette, QFont, QKeySequence, QFontMetrics

# ── Helpers ───────────────────────────────────────────────────────────────────

_JUNK_STRINGS = frozenset({"none", "null", "", "defaultmove", "default_move"})
_ACCESSIBILITY_MIN_FONT_PX = 12
_ACCESSIBILITY_MIN_FONT_PT = 10.0
_FONT_SIZE_RE = re.compile(r"(font-size\s*:\s*)(\d+)(px)")

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

def _with_min_font_px(stylesheet: str, min_px: int = _ACCESSIBILITY_MIN_FONT_PX) -> str:
    """Clamp stylesheet font-size declarations to an accessible minimum."""
    if not stylesheet or "font-size" not in stylesheet:
        return stylesheet
    return _FONT_SIZE_RE.sub(
        lambda m: f"{m.group(1)}{max(min_px, int(m.group(2)))}{m.group(3)}",
        stylesheet,
    )

def _enforce_min_font_in_widget_tree(root: Optional[QWidget], min_px: int = _ACCESSIBILITY_MIN_FONT_PX):
    """Apply minimum stylesheet font size to a widget and all descendants."""
    if root is None:
        return
    widgets = [root] + root.findChildren(QWidget)
    for widget in widgets:
        style = widget.styleSheet()
        if style and "font-size" in style:
            adjusted = _with_min_font_px(style, min_px=min_px)
            if adjusted != style:
                widget.setStyleSheet(adjusted)

# ── Constants ─────────────────────────────────────────────────────────────────

STAT_NAMES = ["STR", "DEX", "CON", "INT", "SPD", "CHA", "LCK"]

APPDATA_SAVE_DIR = os.path.join(
    os.environ.get("APPDATA", ""),
    "Glaiel Games", "Mewgenics",
)

STAT_COLORS = {
    1: QColor(170, 40,  40),
    2: QColor(195, 85,  40),
    3: QColor(190, 145, 40),
    4: QColor(100, 100, 115),
    5: QColor(80,  160, 70),
    6: QColor(50,  195, 80),
    7: QColor(30,  215, 100),
}

ROOM_DISPLAY = {
    "Floor1_Large":   "Ground Floor Left",
    "Floor1_Small":   "Ground Floor Right",
    "Floor2_Large":   "Second Floor",
    "Floor2_Small":   "Second Floor Right",
    "Attic":          "Attic",
    "Attic_Large":    "Attic",
    "Basement":       "Basement",
    "Basement_Large": "Basement",
}

# Full status → abbreviated display in table cell
STATUS_ABBREV = {
    "In House":  "House",
    "Adventure": "Away",
    "Gone":      "Gone",
}
STATUS_COLOR = {
    "In House":  QColor(50,  170, 110),
    "Adventure": QColor(70,  120, 200),
    "Gone":      QColor(80,   80,  90),
}


# ── Ability / mutation tooltip lookup ────────────────────────────────────────
# Keys: display name lowercased with all non-alphanumeric chars removed.
# Sources: mewgenics.wiki.gg/wiki/Mutations and /wiki/Abilities

_ABILITY_LOOKUP: dict[str, str] = {
    # Birth defects
    "twoedarm":           "-2 Strength",
    "twotoedarm":         "-2 Strength",
    "bentarm":            "-2 Speed",
    "conjoinedbody":      "+2 Constitution, -3 Speed",
    "lumpybody":          "Start each battle with 1 Bruise",
    "malnourishedbody":   "-1 Constitution",
    "turnersyndrome":     "-2 Intelligence",
    "birdbeakears":       "Start each battle with Confusion 2",
    "floppyears":         "Start each battle with Immobile 1",
    "inwardeyes":         "Start each battle with Confusion 2",
    "redeyes":            "Gain 5% miss chance every turn",
    "blind":              "Start every battle with Blind 1",
    "bushyeyebrow":       "-1 Luck",
    "noeyebrows":         "-2 Charisma",
    "sloth":              "-3 Charisma, Brace 1",
    "conjoinedtwin":      "+2 Intelligence, -3 Charisma",
    "bentleg":            "Trample — units moved through take damage",
    "duckleg":            "-2 Speed, water does not slow movement",
    "twoedleg":           "-2 Strength",
    "twotoedleg":         "-2 Strength",
    "nomouth":            "Can't use consumables, eat, or musical abilities",
    "cleftlip":           "-2 Charisma",
    "lumpytail":          "+1 Constitution, start with 1 Immobile",
    "notail":             "-1 Dexterity",
    "tailsack":           "-1 Speed, -1 Constitution",
    # Collarless passives
    "180":                "When you use your basic attack, turn around and use it again.",
    "amped":              "Gain +1 Speed at the end of your turn.",
    "amplify":            "+1 Magic Damage.",
    "animalhandler":      "Start each battle with a random vermin familiar.",
    "bareminimum":        "Your stats can't go below 5.",
    "charming":           "25% chance to inflict Charm on units that damage you.",
    "daunt":              "Small enemies won't attack you.",
    "dealer":             "You can use consumables on other units.",
    "deathboon":          "When downed, all allies gain All Stats Up.",
    "deathsdoor":         "While at 1 HP, spells cost 1 mana but can only be cast once per turn.",
    "deathproof":         "While downed, 25% chance to revive with 1 HP at end of each round.",
    "dirtyclaws":         "Attacks on Poisoned/Bleeding enemies inflict +1 Poison/Bleed.",
    "etank":              "Start each battle with +20 unfilled max health.",
    "fastfootsies":       "Immune to negative tile effects.",
    "firstimpression":    "Start each battle with +1 Bonus Attack.",
    "furious":            "Gain +1 Damage per critical hit. +5% critical hit chance.",
    "gassy":              "When you take damage, knock back all adjacent units.",
    "hotblooded":         "Burn you inflict is increased by 1.",
    "infested":           "50% chance to spawn a flea familiar when you end your turn.",
    "latebloomer":        "On your 5th turn, gain All Stats Up 3.",
    "leader":             "Adjacent allies have +1 Damage and +1 Range.",
    "longshot":           "+1 Range.",
    "luckdrain":          "Steal luck from enemies you damage.",
    "lucky":              "+4 Luck.",
    "mange":              "Inflict Poison 1 on units that contact you.",
    "mania":              "10% chance to restore all mana at the start of your turn.",
    "metaldetector":      "5% chance to spawn a coin when you move over a tile.",
    "mightofthemeek":     "Damage of 2 or less is always critical.",
    "minime":             "Start each battle with a tiny duplicate cat at half your stats.",
    "naturalhealing":     "+1 Health Regeneration.",
    "overconfident":      "While at full HP, spells cost 2 less but you take double damage.",
    "patience":           "If you end your turn without actions, gain an extra turn at end of round.",
    "protection":         "Gain +1 Holy Shield.",
    "pulp":               "When you kill a unit, it becomes meat.",
    "rockin":             "Spawn 4 small rocks at the start of each battle.",
    "santasangre":        "When downed, allies heal 12 HP. Excess healing becomes Shield.",
    "scavenger":          "If trinket slot is empty, equip a small food item at battle start.",
    "selfassured":        "Gain a random stat up whenever you down a unit.",
    "serialkiller":       "After 3 kills, gain +6 Speed and backstabs have 100% crit.",
    "skillshare":         "Your other passive is shared with all party cats at battle start.",
    "slugger":            "+1 Damage.",
    "study":              "Gain +1 Intelligence whenever you hit a new unit type.",
    "unrestricted":       "Once-per-battle abilities can be cast once per turn instead.",
    "unscarred":          "While at full HP, 100% critical hit chance.",
    "wiggly":             "+25% Dodge Chance.",
    "worms":              "50% chance to spawn a maggot familiar when you end your turn.",
    "zenkaiboost":        "End battle at 1 HP → +1 random stat permanently, next battle starts with All Stats Up 3.",
    # Fighter passives
    "avenger":            "When an allied cat is downed, gain All Stats Up 2 and heal 8.",
    "boned":              "When you kill a unit without a weapon, gain a Bone Club.",
    "dualwield":          "When you use your weapon, automatically use it again for free.",
    "fervor":             "When you down a unit, heal 5 HP.",
    "frenzy":             "When you down a unit, gain +2 Strength.",
    "hamsterstyle":       "+1 INT, -1 STR, +1 CON, +1 Health Regen, start with 2 Bonus Moves.",
    "hulkup":             "When you take damage, gain +2 Speed.",
    "math":               "Spells cost 3 mana but can only be cast once per turn.",
    "merciless":          "10+ damage in a single hit: +2 Shield and refresh movement action.",
    "overpowered":        "Excess damage causes enemies to explode, dealing overflow to nearby units.",
    "patellarreflex":     "When damaged, counter-attack for 1 damage + Bruise.",
    "punchface":          "Basic attacks hitting the front of a unit are always critical.",
    "ratstyle":           "+2 Speed, +10% Dodge Chance.",
    "scars":              "Start with +1 Brace.",
    "skullcrack":         "Your basic attack inflicts Bruise.",
    "smash":              "Weapons deal triple damage but always break when used.",
    "thickskull":         "All injuries are Concussions. +3 Shield per concussion (max 30).",
    "turtlestyle":        "+4 Armor, +2 Vitality, -1 Speed.",
    "underdog":           "+2 STR and +1 Brace for each adjacent enemy.",
    "vengeful":           "Basic attack is always critical against enemies that have damaged you.",
    "weaponmaster":       "Weapon/item abilities deal +2 Damage and +25% critical chance.",
    # Tank passives
    "bouncer":            "When an ally takes damage, move toward the source and attack if possible.",
    "chainknockback":     "Basic attack gains +1 Knockback; knocked-back units knock back others.",
    "hardhead":           "You block attacks from the front.",
    "hardy":              "Heal to full HP at the start of each battle.",
    "heavyhanded":        "+2 Knockback Damage.",
    "homerun":            "Increases all Knockback by 10.",
    "mountainform":       "Knockback immunity. Tiles walked over become dirt and may spawn rocks.",
    "petrocks":           "Each rock you spawn becomes a Pet Rock. One Pet Rock spawns per combat.",
    "plow":               "When you knock back a unit, leave a rock where it was.",
    "prioritytarget":     "Enemies attack you instead of allies if they can.",
    "protective":         "Your allies have Brace 1.",
    "scabs":              "Gain +2 Shield when you take damage from an ability.",
    "slackoff":           "If you end your turn with unused movement, gain 8 HP.",
    "slowandsteady":      "At speed 0 or below, attack an extra time per turn. -2 SPD, +1 Range/turn.",
    "stoic":              "If you end your turn with unused movement, gain +2 Bonus Moves.",
    "thorns":             "Start with Thorns 2. Gain +1 Thorns when you take damage.",
    "thunderthighs":      "Trample. Contact effects from abilities/items apply when trampling.",
    "toadstyle":          "Movement action is a jump; landing on a unit deals damage and displaces it.",
    "wrestlemaniac":      "Basic attack becomes Suplex when adjacent to enemies. Gain Toss ability.",
    # Psychic passives
    "antigravity":        "Flying Movement. +1 SPD when using Gravity ability. Gravity costs -1 mana.",
    "beckon":             "Your basic attack has +4 Knockback.",
    "blink":              "33% chance to teleport to a random tile when targeted.",
    "eldritchvisage":     "Start of your turn: inflict Magic Weakness 1 on all enemies in line of sight.",
    "enlightened":        "While at full mana, the first spell you cast each turn is free.",
    "fullpower":          "While at full mana, basic attack deals triple damage and has +3 Knockback.",
    "glow":               "Your basic attack inflicts Blind.",
    "omniscience":        "All line-of-sight restrictions ignored. Hidden enemies are always highlighted.",
    "overflow":           "While at full mana, gain +2 Brace and Flying Movement. Mana is uncapped.",
    "psionicrepel":       "Units that attack or contact you get knocked back 10 tiles.",
    "psysmack":           "Knockback damage you and allies deal is doubled.",
    "soulshatter":        "When you kill a unit, deal 1 damage to all enemies.",
    "truesight":          "You and your allies can't miss enemies within your line of sight.",
    "wither":             "Gravity abilities inflict a random negative status on enemies.",
    # Necromancer passives
    "bedbugs":            "Start battles with 2 beefy leech familiars.",
    "cambionconception":  "When downed, spawn a demon kitten familiar.",
    "eternalhealth":      "Suffer only Jinxed when downed; heal to full when your party wins.",
    "infected":           "When you down a unit, reanimate it with 50% HP.",
    "lastgrasp":          "When downed, each enemy takes 6 damage and each ally heals 6 HP.",
    "leechmother":        "Your basic attack spawns a leech familiar.",
    "onewithnothing":     "If you end your turn with 0 mana, Mana Regeneration is doubled.",
    "parasitic":          "When you gain health, spawn a leech familiar.",
    "relentlessdead":     "At end of each round, spawn a Zombie kitten familiar onto a random tile.",
    "sacrificiallamb":    "When downed, allies gain All Stats Up and take an extra turn.",
    "soulbond":           "Your basic attack inflicts Soul Link.",
    "spreadsorrow":       "When you inflict a debuff, also inflict it on another random enemy.",
    "superstition":       "Basic attack inflicts -1 Luck. Units that damage you also lose 1 Luck.",
    "torpor":             "While downed, basic attack is Haunt. Your body gains +6 corpse HP.",
    "undeath":            "When downed, reanimate each ally to 33% HP. (Once per battle.)",
    "vampirism":          "Your basic attack has Lifesteal.",
    # Thief passives
    "afterimage":         "When you move, spawn a shadow that mimics your basic action.",
    "agile":              "+2 Movement Range. Move a 2nd time if not using full range.",
    "backstabber":        "Your backstabs are always critical.",
    "bountyhunter":       "During your turn, one random enemy has a Bounty.",
    "burgle":             "Your basic attack gains you 1 coin when it deals damage.",
    "cripple":            "Your critical hits inflict Immobilize and Weakness 2.",
    "critical":           "Critical hits deal +100% more damage. Gain +1 Luck per critical hit.",
    "doublethrow":        "Your basic attack hits twice for half damage.",
    "firststrike":        "Gain an extra turn at the start of battle.",
    "goldenclaws":        "+1 Damage for each coin you collect.",
    "more":               "When you kill a unit, refresh your movement action.",
    "penetrate":          "Basic attack passes through units and ignores shield. +1 Range.",
    "pinpoint":           "Your critical hits inflict Marked.",
    "poisontips":         "Your basic attack inflicts Poison 1.",
    "razorclaws":         "Your basic attack inflicts Bleed 1.",
    "shank":              "When behind an enemy, basic attack hits 2 times using Strength.",
    "shiv":               "Basic attack: +2 damage, +25% crit, inflicts Bleed 1 in melee range.",
    "stealthed":          "Start each battle with Stealth.",
    "sweetspot":          "+1 Range. Basic attack deals more damage the farther away you are.",
    "weakspot":           "Basic attack ignores shield and inflicts Weakness 1.",
    # Hunter passives
    "animalcontrol":      "Your basic attack causes units to immediately attack an enemy in range.",
    "broodmother":        "Familiars and Charmed units gain +2 Damage and +5 HP.",
    "bullseye":           "Your ranged attacks never miss. +25% critical hit chance.",
    "fleabag":            "Spawn Flea familiars equal to kills this battle when your turn ends.",
    "gravityfalls":       "+1 damage per tile beyond range 3.",
    "hazardous":          "Tile damage and effects are doubled.",
    "huntersboon":        "When you kill an enemy, gain 5 mana.",
    "luckswing":          "+50% critical hit chance but +25% miss chance.",
    "rubberarrows":       "Your projectiles bounce to another enemy within 3 tiles.",
    "sniper":             "Critical hits deal +100% damage and have 25% chance to inflict Stun.",
    "splitshot":          "Basic attack shoots multiple projectiles in a 5-tile cross (half damage each).",
    "survivalist":        "4 healing consumables and a water bottle added. +2 food stored after each battle.",
    "taintedmother":      "Familiars and Charmed units gain +4 Speed and inflict Poison and Bleed.",
    "vampirism":          "Your basic attack has Lifesteal.",
    # Cleric passives
    "angelic":            "When you heal an ally, they also gain mana.",
    "blessed":            "Gain +1 to 2 random stats at the start of each turn.",
    "devoted":            "Healing you provide is doubled.",
    "holyaura":           "Allies adjacent to you gain +1 Brace.",
    "inspiration":        "When you heal an ally, they gain +1 Damage.",
    "martyrdom":          "When you take damage, all allies heal 1 HP.",
    "pacifist":           "Your basic attack heals instead of dealing damage.",
    "radiant":            "Your healing abilities also deal damage to nearby enemies.",
    "sanctuary":          "Allies in your line of sight are immune to debuffs.",
    "smite":              "Holy damage you deal is doubled.",
    # Mage passives
    "arcanemastery":      "Your spells cost 1 less mana.",
    "blastzone":          "Your AOE spells affect a larger area.",
    "crystalclear":       "While at full mana, your spells deal +2 damage.",
    "focused":            "+2 Intelligence. Your spells deal +1 damage.",
    "magicshield":        "Gain +1 Shield when you cast a spell.",
    "manaburn":           "Your spells inflict Mana Drain.",
    "overload":           "When you run out of mana, deal damage equal to mana spent to all nearby enemies.",
    "sorcerersoul":       "Access to Sorcerer class abilities when leveling up.",
    "spellweaver":        "Casting the same spell twice in a row doubles its damage.",
    "unstable":           "Your spells have 20% chance to be empowered for double damage.",
    # Monk passives
    "acrobatics":         "+2 Movement Range. You can move through enemies.",
    "concentration":      "If you don't move during your turn, your next attack is always critical.",
    "counterattack":      "When damaged in melee, automatically counter-attack.",
    "discipline":         "+2 to all stats at the start of each battle.",
    "flowstate":          "After using an ability, gain +1 Speed for the rest of your turn.",
    "harmonize":          "Your abilities heal allies they pass through.",
    "innerpeace":         "+1 Health Regeneration and +1 Mana Regeneration per turn.",
    "ironbody":           "+4 Constitution. You are immune to Stun and Immobilize.",
    "reflexes":           "+10% Dodge Chance. Dodging an attack gives you +1 Speed.",
    "zenmaster":          "While at full HP, all your abilities cost 0 mana.",
    # Druid passives
    "barkaspect":         "Gain +1 Brace when you take damage.",
    "earthbound":         "Immunity to knockback. Gain +2 Constitution.",
    "floral":             "Spawn flowers that heal adjacent allies each turn.",
    "growth":             "Gain +1 to a random stat at the end of each battle.",
    "naturecall":         "Spawn a random nature familiar at the start of each battle.",
    "photosynthesis":     "Regenerate 1 HP and 1 mana each turn when standing on grass/dirt.",
    "pollinate":          "Your familiars spread healing pollen to adjacent allies.",
    "primalrage":         "When you take damage, gain +1 Strength and +1 Speed (stacks).",
    "regrowth":           "When downed, revive with 25% HP once per battle.",
    "thornedbody":        "Units that attack you in melee take 2 damage.",
    # Jester passives
    "allofthem":          "Gain a copy of the last ability used by any unit this battle.",
    "alsorandom":         "At the start of your turn, gain a random status effect.",
    "chaosmagic":         "Your abilities have random additional effects.",
    "clumsy":             "50% chance to hit adjacent allies when attacking.",
    "copycat":            "Your basic attack copies the last ability used by an ally.",
    "gambler":            "At battle start, randomly gain or lose 1-3 of each stat.",
    "jackofalltrades":    "Gain one random ability from each class at the start of each battle.",
    "jinx":               "Units adjacent to you have -2 Luck.",
    "pandemonium":        "At the start of each round, swap positions with a random unit.",
    "pratfall":           "When you miss, all allies gain +1 Damage for the next attack.",
    # Soul passives
    "butcherssoul":       "Access to Butcher class abilities when leveling up.",
    "clericsoul":         "Access to Cleric class abilities when leveling up.",
    "druidsoul":          "Access to Druid class abilities when leveling up.",
    "fighterssoul":       "Access to Fighter class abilities when leveling up.",
    "hunterssoul":        "Access to Hunter class abilities when leveling up.",
    "jesterssoul":        "Access to Jester class abilities when leveling up.",
    "magessoul":          "Access to Mage class abilities when leveling up.",
    "monkssoul":          "Access to Monk class abilities when leveling up.",
    "necromancerssoul":   "Access to Necromancer class abilities when leveling up.",
    "psychicssoul":       "Access to Psychic class abilities when leveling up.",
    "tankssoul":          "Access to Tank class abilities when leveling up.",
    "thiefsoul":          "Access to Thief class abilities when leveling up.",
    "tinkerersoul":       "Access to Tinkerer class abilities when leveling up.",
    "voidsoul":           "Only upgraded Collarless abilities offered on level up. Collarless spells cost 1 less mana.",
}


def _ability_tip(name: str) -> str:
    """Return a tooltip description for an ability/mutation name, or '' if unknown."""
    key = re.sub(r'[^a-z0-9]', '', name.lower())
    return _ABILITY_LOOKUP.get(key, "")


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


# ── Visual mutation scanner ───────────────────────────────────────────────────

# 14 body-part slots (0-indexed); slot_id ≥ 300 in the blob = active mutation
VISUAL_MUT_NAMES = [
    "Body", "Head", "Tail", "Eye", "Ear",
    "Leg", "Paw", "Belly", "Back", "Fur",
    "Wing", "Horn", "Fang", "Mark",
]

def _find_mutation_table(raw: bytes) -> int:
    """
    Locate the 296-byte visual-mutation table by scanning for its header:
      entry-0: f32 scale [0.05, 20.0], u32 coat_id [1, 20000], u32 small ≤ 500,
               u32 sentinel (0 or ≤ 5000)
      entries 1-14: 20 bytes each, second u32 (coat_id or 0) validated for ≥10 slots.
    Returns base offset, or -1 if not found.
    """
    size = 16 + 14 * 20   # 296 bytes
    limit = len(raw) - size
    for base in range(limit):
        scale = struct.unpack_from('<f', raw, base)[0]
        if not (0.05 <= scale <= 20.0):
            continue
        coat = struct.unpack_from('<I', raw, base + 4)[0]
        if coat == 0 or coat > 20_000:
            continue
        t1 = struct.unpack_from('<I', raw, base + 8)[0]
        if t1 > 500:
            continue
        t2 = struct.unpack_from('<I', raw, base + 12)[0]
        if t2 != 0xFFFF_FFFF and t2 > 5_000:
            continue
        matches = sum(
            1 for i in range(14)
            if struct.unpack_from('<I', raw, base + 16 + i * 20 + 4)[0] in (coat, 0)
        )
        if matches >= 10:
            return base
    return -1


def _read_visual_mutations(raw: bytes) -> list:
    """Return list of active visual-mutation names (e.g. 'Eye Mutation')."""
    base = _find_mutation_table(raw)
    if base == -1:
        return []
    result = []
    for i in range(14):
        slot_id = struct.unpack_from('<I', raw, base + 16 + i * 20)[0]
        if slot_id >= 300:
            name = VISUAL_MUT_NAMES[i] if i < len(VISUAL_MUT_NAMES) else f"Mutation{i+1}"
            result.append(f"{name} Mutation")
    return result


# ── Cat ───────────────────────────────────────────────────────────────────────

class Cat:
    # parent_a / parent_b are resolved after the full save is loaded
    parent_a: Optional['Cat'] = None
    parent_b: Optional['Cat'] = None
    generation: int = 0   # generation depth: 0=stray, 1=child of strays, etc.

    def __init__(self, blob: bytes, cat_key: int, house_info: dict, adventure_keys: set):
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
        _name_end = r.pos   # used below for reliable sex u16 read at _name_end+8

        r.str()  # unknown string between name and parent refs

        # Possible parent UIDs — fixed-position attempt.
        # parse_save will run a blob scan as a fallback if these don't resolve.
        self._parent_uid_a = r.u64()
        self._parent_uid_b = r.u64()

        self.collar = r.str() or ""
        r.u32()

        r.skip(64)
        T = [r.u32() for _ in range(72)]
        self.body_parts = {"texture": T[0], "bodyShape": T[3], "headShape": T[8]}
        self.visual_mutation_ids = [T[i] for i in range(14, 29) if i < 72 and T[i] != 0]

        r.skip(12)
        raw_gender = r.str()
        # Authoritative sex enum near the name block:
        #   0 = male, 1 = female, 2 = undefined/both (ditto-like)
        sex_code = raw[_name_end + 8] if (_name_end + 9) <= len(raw) else None
        self.gender = {0: "male", 1: "female", 2: "?"}.get(
            sex_code,
            _normalize_gender(raw_gender),
        )

        # Try to read sexual orientation (may be stored after gender)
        saved_pos = r.pos
        try:
            potential_orientation = r.str()
            if potential_orientation and potential_orientation.lower() in ['straight', 'gay', 'lesbian', 'bi', 'bisexual']:
                self.sexual_orientation = potential_orientation.lower()
            else:
                r.seek(saved_pos)
        except Exception:
            r.seek(saved_pos)

        r.f64()

        self.stat_base = [r.u32() for _ in range(7)]
        self.stat_mod  = [r.i32() for _ in range(7)]
        self.stat_sec  = [r.i32() for _ in range(7)]

        self.base_stats  = {n: self.stat_base[i] for i, n in enumerate(STAT_NAMES)}
        self.total_stats = {n: self.stat_base[i] + self.stat_mod[i] + self.stat_sec[i]
                            for i, n in enumerate(STAT_NAMES)}

        # Personality stats (aggression, libido, inbredness).
        # Exact offsets not yet documented; filled in by parse_save if found.
        self.aggression  = None   # None = unknown
        self.libido      = None
        self.inbredness  = None
        self.sexual_orientation = None  # None = unknown

        # Relationship scaffolds — resolved by parse_save after all cats loaded.
        self._lover_uids: list[int] = []
        self._hater_uids: list[int] = []
        self.lovers:   list['Cat'] = []
        self.haters:   list['Cat'] = []
        self.children: list['Cat'] = []   # direct offspring; assigned by parse_save

        # ── Ability run — anchored on "DefaultMove" ─────────────────────────
        # The ability block is a u64-length-prefixed ASCII identifier run.
        # Structure (from open-source editor research):
        #   items[0]  = "DefaultMove"  (active slot 1 default)
        #   items[1-5] = active abilities 2-6
        #   items[6-9] = padding / unknown slots
        #   items[10]  = Passive1 mutation  (e.g. "Sturdy", "Longshot")
        #   After run:  u32 tier, then 3 × [u64 id][u32 tier] tail entries
        #               = Passive2, Disorder1, Disorder2
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
                continue

        if run_start != -1:
            r.seek(run_start)
            # Read the full run until a non-identifier is encountered
            run_items: list[str] = []
            for _ in range(32):
                saved = r.pos
                item = r.str()
                if item is None or not _IDENT_RE.match(item):
                    r.seek(saved)
                    break
                run_items.append(item)

            # Active abilities: items[1-5] (skip DefaultMove at [0])
            self.abilities = [x for x in run_items[1:6] if _valid_str(x)]

            # Passive mutations: item[10] then 3 tail tier-entries
            passives: list[str] = []
            if len(run_items) > 10 and _valid_str(run_items[10]):
                passives.append(run_items[10])

            try:
                r.u32()   # passive1 tier — discard
            except Exception:
                pass

            for _ in range(3):   # Passive2, Disorder1, Disorder2
                saved = r.pos
                item = r.str()
                if item is None or not _IDENT_RE.match(item) or not _valid_str(item):
                    r.seek(saved)
                    break
                passives.append(item)
                try:
                    r.u32()   # tier — discard
                except Exception:
                    pass

            self.mutations = passives
            self.equipment = []   # equipment parsing requires separate byte-marker logic

        else:
            # Fallback: old heuristic scan for any uppercase-starting ASCII string
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

            self.mutations = []
            first = r.str()
            if _valid_str(first):
                self.mutations.append(first)
            for _ in range(13):
                if r.remaining() < 12:
                    break
                flag = r.u32()
                if flag == 0:
                    break
                p = r.str()
                if _valid_str(p):
                    self.mutations.append(p)

        # Visual mutations from the 296-byte fixed mutation table (prepend so they
        # show first; passive trait strings from the ability run follow)
        vis = _read_visual_mutations(raw)
        if vis:
            self.mutations = vis + self.mutations

        # Legacy token fallback is already handled above when sex_code is unavailable.

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

def get_all_ancestors(cat: Optional[Cat], depth: int = 6, _seen: set = None) -> set:
    """Return all ancestor Cat objects up to `depth` generations."""
    if cat is None or depth == 0:
        return set()
    if _seen is None:
        _seen = set()
    ancestors: set[Cat] = set()
    for parent in (cat.parent_a, cat.parent_b):
        if parent is not None and id(parent) not in _seen:
            _seen.add(id(parent))
            ancestors.add(parent)
            ancestors |= get_all_ancestors(parent, depth - 1, _seen)
    return ancestors


def _ancestor_depths(cat: Optional[Cat], max_depth: int = 8) -> dict[Cat, int]:
    """
    Return a map of ancestor -> generational distance.
    Includes `cat` itself at depth 0, then parents at depth 1, etc.
    """
    if cat is None:
        return {}
    depths: dict[Cat, int] = {cat: 0}
    frontier: list[tuple[Cat, int]] = [(cat, 0)]
    while frontier:
        cur, d = frontier.pop(0)
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


def raw_coi(a: Optional['Cat'], b: Optional['Cat'], max_steps: int = 12) -> float:
    """
    Raw Coefficient of Inbreeding between two cats:
      sum(0.5 ** (n + 1)) over all valid paths through common ancestors,
    where n = total edge count from A up to ancestor and down to B.
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
                # Valid full path cannot pass through the same cat twice
                # (except the common ancestor itself).
                overlap = (set_a & {id(x) for x in path_b}) - {id(anc)}
                if overlap:
                    continue
                sb = len(path_b) - 1
                coi += 0.5 ** (sa + sb + 1)
    return coi


def risk_percent(a: Optional['Cat'], b: Optional['Cat']) -> float:
    """
    Normalize raw CoI to UI risk scale:
      0.25 CoI => 100% risk, clamped to [0, 100].
    """
    return max(0.0, min(100.0, (raw_coi(a, b) / 0.25) * 100.0))


def find_common_ancestors(a: Cat, b: Cat) -> list[Cat]:
    """Return cats that appear in both ancestry trees."""
    return list(get_all_ancestors(a) & get_all_ancestors(b))


def shared_ancestor_counts(a: Cat, b: Cat, recent_depth: int = 3, max_depth: int = 8) -> tuple[int, int]:
    """
    Return (total_shared, recent_shared) common ancestor counts.
    recent_shared counts ancestors where both cats are within `recent_depth`
    generations of that ancestor (used for inbreeding-risk checks).
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
    # Spidercat/unknown cats ('?') are allowed to pair with any gender.
    if ga == "?" or gb == "?":
        return True, ""
    if ga != gb and {ga, gb} == {"male", "female"}:
        # Check sexual orientation compatibility
        oa = (a.sexual_orientation or "").strip().lower()
        ob = (b.sexual_orientation or "").strip().lower()

        # Check if orientations are compatible
        if oa and ob:
            # Both must be compatible with opposite sex
            if ga == "male" and oa in ["gay"] and gb == "female":
                return False, "Male cat is gay — incompatible with females"
            if ga == "female" and oa in ["lesbian"] and gb == "male":
                return False, "Female cat is lesbian — incompatible with males"
            if gb == "male" and ob in ["gay"] and ga == "female":
                return False, "Male cat is gay — incompatible with females"
            if gb == "female" and ob in ["lesbian"] and ga == "male":
                return False, "Female cat is lesbian — incompatible with males"

        return True, ""
    # Same known sex
    if ga == "female" and gb == "female":
        return False, "Both cats are female — cannot produce offspring"
    if ga == "male" and gb == "male":
        return False, "Both cats are male — cannot produce offspring"
    return False, "Cats have incompatible genders — cannot produce offspring"


# ── Compatibility check ───────────────────────────────────────────────────────

def _compatibility(focus: 'Cat', other: 'Cat') -> str:
    """
    Returns one of: 'self' | 'incompatible' | 'risky' | 'ok'
    Used to dim rows in the table when a single cat is selected.
    """
    if focus is other:
        return 'self'
    ok, _ = can_breed(focus, other)
    if not ok:
        return 'incompatible'
    # Hate relationship
    if other in getattr(focus, 'haters', []) or focus in getattr(other, 'haters', []):
        return 'incompatible'
    # Direct parent/offspring
    if focus in get_parents(other) or other in get_parents(focus):
        return 'incompatible'
    # Shared ancestors → inbreeding risk
    if find_common_ancestors(focus, other):
        return 'risky'
    return 'ok'


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
        pass
    return keys


def _parse_pedigree(conn) -> dict:
    """
    Parse the pedigree blob from the files table.
    Each 32-byte entry: u64 cat_key, u64 parent_a_key, u64 parent_b_key, u64 extra.
    0xFFFFFFFFFFFFFFFF means null/unknown for parent fields.

    Returns ped_map: db_key -> (parent_a_db_key | None, parent_b_db_key | None).

    NOTE: children are NOT derived from this map because the pedigree blob
    appears to store more than just direct parent-child pairs (possibly full
    lineage chains), which causes circular references when used for children.
    Children are instead computed bottom-up from resolved parent fields.
    """
    try:
        row = conn.execute("SELECT data FROM files WHERE key='pedigree'").fetchone()
        if not row:
            return {}
        data = row[0]
    except Exception:
        return {}

    NULL = 0xFFFF_FFFF_FFFF_FFFF
    MAX_KEY = 1_000_000   # anything larger is a legacy UID or garbage
    ped_map: dict = {}

    # Entries start at offset 8 (after a single u64 header), stride 32
    for pos in range(8, len(data) - 31, 32):
        cat_k, pa_k, pb_k, _ = struct.unpack_from('<QQQQ', data, pos)
        if cat_k == 0 or cat_k == NULL or cat_k > MAX_KEY:
            continue
        pa = int(pa_k) if pa_k != NULL and 0 < pa_k <= MAX_KEY else None
        pb = int(pb_k) if pb_k != NULL and 0 < pb_k <= MAX_KEY else None
        # First occurrence of each cat_k is the parent record; later occurrences
        # are other relationship types (breeding history, etc.) — skip them.
        if int(cat_k) not in ped_map:
            ped_map[int(cat_k)] = (pa, pb)

    return ped_map


def parse_save(path: str) -> tuple[list, list]:
    conn  = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    house = _get_house_info(conn)
    adv   = _get_adventure_keys(conn)
    rows  = conn.execute("SELECT key, data FROM cats").fetchall()
    ped_map = _parse_pedigree(conn)
    conn.close()

    cats, errors = [], []
    for key, blob in rows:
        try:
            cats.append(Cat(blob, key, house, adv))
        except Exception as e:
            errors.append((key, str(e)))

    key_to_cat: dict = {c.db_key: c for c in cats}

    for cat in cats:
        # Pedigree db_key lookup — only assigns a parent if that cat is still
        # present in the save.  If the real parents are gone (dead/sold), we
        # leave parent_a/parent_b as None rather than falling back to an
        # unreliable blob-UID scan that picks up wrong living cats.
        pa: Optional[Cat] = None
        pb: Optional[Cat] = None
        if cat.db_key in ped_map:
            pa_k, pb_k = ped_map[cat.db_key]
            pa = key_to_cat.get(pa_k)
            pb = key_to_cat.get(pb_k)
            # Sanity: a cat cannot be its own parent
            if pa is cat: pa = None
            if pb is cat: pb = None
        cat.parent_a = pa
        cat.parent_b = pb

    # Build children bottom-up from the now-resolved parent fields.
    # This avoids the circular-reference problem in the pedigree blob.
    for cat in cats:
        cat.children = []
    for cat in cats:
        for parent in (cat.parent_a, cat.parent_b):
            if parent is not None and cat not in parent.children:
                parent.children.append(cat)

    # Compute generation depth (0=stray, N=max parent gen + 1).
    # Uses memoisation; safe because circular parent refs are already blocked.
    _gen_cache: dict = {}
    def _get_gen(c: Cat) -> int:
        cid = id(c)
        if cid in _gen_cache:
            return _gen_cache[cid]
        if c.parent_a is None and c.parent_b is None:
            _gen_cache[cid] = 0
            return 0
        pa_g = _get_gen(c.parent_a) if c.parent_a else -1
        pb_g = _get_gen(c.parent_b) if c.parent_b else -1
        g = max(pa_g, pb_g) + 1
        _gen_cache[cid] = g
        return g
    for cat in cats:
        cat.generation = _get_gen(cat)

    return cats, errors


def find_save_files() -> list[str]:
    saves = []
    base  = Path(APPDATA_SAVE_DIR)
    if not base.is_dir():
        return saves
    for profile in base.iterdir():
        saves_dir = profile / "saves"
        if saves_dir.is_dir():
            saves.extend(str(p) for p in saves_dir.glob("*.sav"))
    saves.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return saves


# ── Qt table model ────────────────────────────────────────────────────────────

COLUMNS   = ["Name", "♀/♂", "Orient", "Room", "Status"] + STAT_NAMES + ["Sum", "Abilities", "Mutations", "Risk%", "Gen", "Source", "Inbr"]
COL_NAME  = 0
COL_GEN   = 1
COL_ORIENT = 2
COL_ROOM  = 3
COL_STAT  = 4
STAT_COLS = list(range(5, 12))   # STR … LCK  (indices 5–11)
COL_SUM   = 12
COL_ABIL  = 13
COL_MUTS  = 14
COL_REL   = 15
COL_AGE   = 16   # generation depth
COL_SRC   = 17
COL_INB   = 18

# Fixed pixel widths for narrow columns
_W_STATUS = 62
_W_STAT   = 34
_W_GEN    = 28
_W_REL    = 68
_ZOOM_MIN = 70
_ZOOM_MAX = 200
_ZOOM_STEP = 10


class CatTableModel(QAbstractTableModel):
    def __init__(self):
        super().__init__()
        self._cats: list[Cat] = []
        self._focus_cat: Optional[Cat] = None
        self._show_lineage: bool = False
        self._relation_cache: dict[int, float] = {}

    def set_show_lineage(self, show: bool):
        self._show_lineage = show
        if self._cats:
            self.dataChanged.emit(
                self.index(0, 0),
                self.index(len(self._cats) - 1, len(COLUMNS) - 1),
                [Qt.BackgroundRole, Qt.ForegroundRole],
            )

    def load(self, cats: list[Cat]):
        self.beginResetModel()
        self._cats = cats
        self._relation_cache.clear()
        self.endResetModel()

    def set_focus_cat(self, cat: Optional[Cat]):
        self._focus_cat = cat
        self._relation_cache.clear()
        if self._cats:
            self.dataChanged.emit(
                self.index(0, 0),
                self.index(len(self._cats) - 1, len(COLUMNS) - 1),
                [Qt.DisplayRole, Qt.UserRole, Qt.BackgroundRole, Qt.ForegroundRole],
            )

    def _relation_for(self, cat: Cat) -> float:
        if self._focus_cat is None:
            return 0.0
        if cat is self._focus_cat:
            return 100.0
        key = id(cat)
        cached = self._relation_cache.get(key)
        if cached is not None:
            return cached
        pct = risk_percent(self._focus_cat, cat)
        self._relation_cache[key] = pct
        return pct

    def rowCount(self, parent=QModelIndex()):    return len(self._cats)
    def columnCount(self, parent=QModelIndex()): return len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return COLUMNS[section]
        return None

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        cat = self._cats[index.row()]
        col = index.column()

        if role == Qt.DisplayRole:
            if col == COL_NAME: return cat.name
            if col == COL_GEN:  return cat.gender_display
            if col == COL_ORIENT:
                if cat.sexual_orientation:
                    abbrev = {"straight": "Het", "gay": "Gay", "lesbian": "Les", "bi": "Bi", "bisexual": "Bi"}
                    return abbrev.get(cat.sexual_orientation, cat.sexual_orientation[:3].capitalize())
                return "—"
            if col == COL_ROOM: return cat.room_display
            if col == COL_STAT: return STATUS_ABBREV.get(cat.status, cat.status)
            if col in STAT_COLS:
                return str(cat.base_stats[STAT_NAMES[col - 5]])
            if col == COL_SUM:
                return str(sum(cat.base_stats.values()))
            if col == COL_MUTS:
                return ", ".join(cat.mutations)
            if col == COL_ABIL:
                return ", ".join(cat.abilities)
            if col == COL_REL:
                if self._focus_cat is None:
                    return "—"
                return f"{int(round(self._relation_for(cat)))}%"
            if col == COL_AGE:
                return str(cat.generation)
            if col == COL_SRC:
                pa, pb = cat.parent_a, cat.parent_b
                if pa is None and pb is None:
                    return "Stray"
                def _pname(p):
                    return p.name if p.status != "Gone" else f"{p.name} (gone)"
                return " × ".join(_pname(p) for p in (pa, pb) if p is not None)
            if col == COL_INB:
                if cat.parent_a is None or cat.parent_b is None:
                    return "—"
                score = len(find_common_ancestors(cat.parent_a, cat.parent_b))
                return str(score) if score else "—"

        elif role == Qt.UserRole:
            if col in STAT_COLS:
                return cat.base_stats[STAT_NAMES[col - 5]]
            if col == COL_SUM:
                return sum(cat.base_stats.values())
            if col == COL_REL:
                return self._relation_for(cat) if self._focus_cat is not None else -1.0
            if col == COL_AGE:
                return cat.generation
            return self.data(index, Qt.DisplayRole)

        elif role == Qt.BackgroundRole:
            compat = (
                _compatibility(self._focus_cat, cat)
                if self._focus_cat is not None and cat is not self._focus_cat
                else None
            )
            # Suppress risky highlight when lineage features are off
            if compat == 'risky' and not self._show_lineage:
                compat = 'ok'
            if col in STAT_COLS:
                base_c = STAT_COLORS.get(cat.base_stats[STAT_NAMES[col - 5]], QColor(100, 100, 115))
                if compat == 'incompatible':
                    return QBrush(QColor(base_c.red() // 4, base_c.green() // 4, base_c.blue() // 4))
                if compat == 'risky':
                    return QBrush(QColor(base_c.red() // 2, base_c.green() // 2, base_c.blue() // 2))
                return QBrush(base_c)
            if col == COL_STAT:
                sc = STATUS_COLOR.get(cat.status, QColor(80, 80, 90))
                if compat == 'incompatible':
                    return QBrush(QColor(sc.red() // 4, sc.green() // 4, sc.blue() // 4))
                if compat == 'risky':
                    return QBrush(QColor(sc.red() // 2, sc.green() // 2, sc.blue() // 2))
                return QBrush(sc)
            if col == COL_INB:
                if cat.parent_a is not None and cat.parent_b is not None:
                    score = len(find_common_ancestors(cat.parent_a, cat.parent_b))
                    if score >= 3:
                        return QBrush(QColor(80, 20, 20))
                    if score >= 1:
                        return QBrush(QColor(70, 55, 10))
            if compat == 'incompatible':
                return QBrush(QColor(18, 12, 14))
            if compat == 'risky':
                return QBrush(QColor(22, 18, 10))

        elif role == Qt.ForegroundRole:
            compat = (
                _compatibility(self._focus_cat, cat)
                if self._focus_cat is not None and cat is not self._focus_cat
                else None
            )
            # Suppress risky highlight when lineage features are off
            if compat == 'risky' and not self._show_lineage:
                compat = 'ok'
            if compat == 'incompatible':
                return QBrush(QColor(65, 55, 60))
            if compat == 'risky':
                return QBrush(QColor(130, 110, 60))
            if col in STAT_COLS or col == COL_STAT:
                return QBrush(QColor(255, 255, 255))

        elif role == Qt.ToolTipRole:
            if col in STAT_COLS:
                n = STAT_NAMES[col - 5]
                b = cat.base_stats[n]
                t = cat.total_stats[n]
                extra = f"  (+{t - b})" if t != b else ""
                return f"{n}  base: {b}{extra}  |  total: {t}"
            if col == COL_ROOM:
                return cat.room
            if col == COL_MUTS and cat.mutations:
                return "\n".join(cat.mutations)
            if col == COL_ABIL and cat.abilities:
                return "\n".join(cat.abilities)

        elif role == Qt.TextAlignmentRole:
            if col in STAT_COLS or col in (COL_GEN, COL_STAT, COL_SUM, COL_REL, COL_AGE):
                return Qt.AlignCenter

        return None

    def cat_at(self, row: int) -> Optional[Cat]:
        return self._cats[row] if 0 <= row < len(self._cats) else None


class RoomFilterModel(QSortFilterProxyModel):
    def __init__(self):
        super().__init__()
        self._room = None
        self._name_filter = ""
        self.setSortRole(Qt.UserRole)

    def set_room(self, key):
        self._room = key
        self.invalidate()

    def set_name_filter(self, text: str):
        self._name_filter = text.strip().lower()
        self.invalidate()

    def filterAcceptsRow(self, source_row, source_parent):
        cat = self.sourceModel().cat_at(source_row)
        if cat is None:
            return False
        if self._name_filter and self._name_filter not in cat.name.lower():
            return False
        if self._room == "__all__":
            return True
        if self._room is None:
            return cat.status != "Gone"
        if self._room == "__gone__":
            return cat.status == "Gone"
        if self._room == "__adventure__":
            return cat.status == "Adventure"
        return cat.room == self._room


# ── Detail / breeding panel widgets ──────────────────────────────────────────

_CHIP_STYLE = ("QLabel { background:#252545; color:#ccc; border-radius:6px;"
               " padding:2px 7px; font-size:11px; }")
_SEC_STYLE  = "color:#555; font-size:10px; font-weight:bold; letter-spacing:1px;"
_NAME_STYLE = "color:#eee; font-size:13px; font-weight:bold;"
_META_STYLE = "color:#777; font-size:11px;"
_WARN_STYLE = "color:#e07050; font-size:11px; font-weight:bold;"
_SAFE_STYLE = "color:#50c080; font-size:11px;"
_ANCS_STYLE = "color:#aaa; font-size:11px;"
_PANEL_BG   = "background:#0a0a18; border-top:1px solid #1e1e38;"


def _chip(text: str, tooltip: str = "") -> QLabel:
    lbl = QLabel(text)
    lbl.setStyleSheet(_CHIP_STYLE)
    if tooltip:
        lbl.setToolTip(tooltip)
    return lbl

def _sec(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setStyleSheet(_SEC_STYLE)
    return lbl

def _vsep() -> QFrame:
    f = QFrame()
    f.setFrameShape(QFrame.VLine)
    f.setStyleSheet("color:#1e1e38;")
    return f


class ChipRow(QWidget):
    def __init__(self, items: list[str], tooltip_fn=None):
        super().__init__()
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(5)
        for item in items:
            tip = tooltip_fn(item) if tooltip_fn else ""
            row.addWidget(_chip(item, tip))
        row.addStretch()


class CatDetailPanel(QWidget):
    """
    Bottom panel driven by table selection.
    1 cat  → abilities / mutations / ancestry
    2 cats → breeding comparison with lineage safety check
    """

    def __init__(self):
        super().__init__()
        self.setStyleSheet(_PANEL_BG)
        self.setFixedHeight(0)
        self._show_lineage: bool = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 10, 14, 10)
        outer.setSpacing(0)
        self._content = QWidget()
        outer.addWidget(self._content)

    def set_show_lineage(self, show: bool):
        self._show_lineage = show

    def show_cats(self, cats: list[Cat]):
        old = self._content
        self._content = QWidget()
        self.layout().replaceWidget(old, self._content)
        old.deleteLater()

        if not cats:
            self.setFixedHeight(0)
            return

        min_h = 160 if len(cats) == 1 else 220
        self.setMinimumHeight(min_h)
        self.setMaximumHeight(16777215)   # remove the fixed-height lock

        if len(cats) == 1:
            self._build_single(cats[0])
        else:
            self._build_pair(cats[0], cats[1])
        _enforce_min_font_in_widget_tree(self)

    # ── Single cat ─────────────────────────────────────────────────────────

    def _build_single(self, cat: Cat):
        root = QHBoxLayout(self._content)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(20)

        # Identity
        id_col = QVBoxLayout()
        id_col.setSpacing(3)
        name_row = QHBoxLayout()
        nl = QLabel(cat.name); nl.setStyleSheet(_NAME_STYLE)
        gl = QLabel(cat.gender_display)
        gl.setStyleSheet("color:#7ac; font-size:12px; font-weight:bold;")
        name_row.addWidget(nl); name_row.addWidget(gl)
        if cat.sexual_orientation:
            orient_label = QLabel(f"({cat.sexual_orientation.capitalize()})")
            orient_label.setStyleSheet("color:#9a9; font-size:10px;")
            name_row.addWidget(orient_label)
        name_row.addStretch()
        id_col.addLayout(name_row)
        id_col.addWidget(QLabel(cat.room_display or "—", styleSheet=_META_STYLE))

        # Stats: show all 7; highlight any that are modified
        has_mods = any(cat.total_stats[n] != cat.base_stats[n] for n in STAT_NAMES)
        if has_mods:
            id_col.addSpacing(4)
            stat_row = QHBoxLayout(); stat_row.setSpacing(6)
            for n in STAT_NAMES:
                base = cat.base_stats[n]
                total = cat.total_stats[n]
                if total != base:
                    delta = total - base
                    sign  = "+" if delta > 0 else ""
                    text  = f"{n} {base}{sign}{delta}"
                    col_s = "#5a9" if delta > 0 else "#c55"
                else:
                    text  = f"{n} {base}"
                    col_s = "#555"
                lbl = QLabel(text)
                lbl.setStyleSheet(f"color:{col_s}; font-size:10px;")
                stat_row.addWidget(lbl)
            stat_row.addStretch()
            id_col.addLayout(stat_row)

        def _navigate(target: Cat):
            mw = self.window()
            # Use "All Cats" view so gone/adventure cats are always reachable
            mw._filter("__all__", mw._btn_everyone)
            for row in range(mw._source_model.rowCount()):
                if mw._source_model.cat_at(row) is target:
                    proxy_idx = mw._proxy_model.mapFromSource(
                        mw._source_model.index(row, 0))
                    if proxy_idx.isValid():
                        mw._table.selectionModel().setCurrentIndex(
                            proxy_idx,
                            QItemSelectionModel.SelectionFlag.ClearAndSelect |
                            QItemSelectionModel.SelectionFlag.Rows)
                        mw._table.scrollTo(proxy_idx)
                    break

        if self._show_lineage:
            tree_btn = QPushButton("Family Tree…")
            tree_btn.setStyleSheet(
                "QPushButton { color:#5a8aaa; background:transparent; border:1px solid #252545;"
                " padding:3px 8px; border-radius:4px; font-size:10px; }"
                "QPushButton:hover { background:#131328; }")
            tree_btn.clicked.connect(lambda: LineageDialog(cat, self, navigate_fn=_navigate).exec())
            id_col.addWidget(tree_btn)
        id_col.addStretch()
        root.addLayout(id_col)

        # Abilities
        if cat.abilities:
            root.addWidget(_vsep())
            ab = QVBoxLayout(); ab.setSpacing(4)
            ab.addWidget(_sec("ABILITIES"))
            ab.addWidget(ChipRow(cat.abilities, tooltip_fn=_ability_tip))
            ab.addStretch()
            root.addLayout(ab)

        # Mutations
        if cat.mutations:
            root.addWidget(_vsep())
            mu = QVBoxLayout(); mu.setSpacing(4)
            mu.addWidget(_sec("MUTATIONS"))
            mu.addWidget(ChipRow(cat.mutations, tooltip_fn=_ability_tip))
            mu.addStretch()
            root.addLayout(mu)

        # Equipment
        if cat.equipment:
            root.addWidget(_vsep())
            eq = QVBoxLayout(); eq.setSpacing(4)
            eq.addWidget(_sec("EQUIPMENT"))
            eq.addWidget(ChipRow(cat.equipment))
            eq.addStretch()
            root.addLayout(eq)

        # Ancestry
        parents = get_parents(cat)
        gparents = get_grandparents(cat)
        if parents:
            root.addWidget(_vsep())
            anc = QVBoxLayout(); anc.setSpacing(4)
            anc.addWidget(_sec("LINEAGE"))

            p_names = " × ".join(
                f"{p.name} ({p.gender_display})" for p in parents)
            pl = QLabel(p_names); pl.setStyleSheet(_ANCS_STYLE)
            anc.addWidget(pl)

            if gparents:
                gp_names = "  ·  ".join(gp.short_name for gp in gparents)
                gl2 = QLabel(gp_names)
                gl2.setStyleSheet("color:#555; font-size:10px;")
                anc.addWidget(gl2)

            anc.addStretch()
            root.addLayout(anc)

        # Lovers & haters
        if cat.lovers or cat.haters:
            root.addWidget(_vsep())
            rel = QVBoxLayout(); rel.setSpacing(4)
            if cat.lovers:
                rel.addWidget(_sec("LOVERS"))
                rel.addWidget(ChipRow([c.name for c in cat.lovers]))
            if cat.haters:
                rel.addWidget(_sec("HATERS"))
                hl = ChipRow([c.name for c in cat.haters])
                for i in range(hl.layout().count() - 1):  # tint hater chips red
                    w = hl.layout().itemAt(i).widget()
                    if w:
                        w.setStyleSheet(w.styleSheet().replace("background:#252545", "background:#452020"))
                rel.addWidget(hl)
            rel.addStretch()
            root.addLayout(rel)

        root.addStretch()

    # ── Breeding pair ──────────────────────────────────────────────────────

    def _build_pair(self, a: Cat, b: Cat):
        ok, reason = can_breed(a, b)

        root = QVBoxLayout(self._content)
        root.setContentsMargins(0, 4, 0, 0)
        root.setSpacing(10)

        # ── Header: parent names + room ────────────────────────────────────
        hdr = QHBoxLayout()
        hdr.setSpacing(6)

        for cat in (a, b):
            nl = QLabel(cat.name)
            nl.setStyleSheet(_NAME_STYLE)
            nl.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
            hdr.addWidget(nl)
            gl = QLabel(cat.gender_display)
            gl.setStyleSheet("color:#7ac; font-size:12px; font-weight:bold;")
            hdr.addWidget(gl)
            if cat.sexual_orientation:
                ol = QLabel(f"({cat.sexual_orientation[:3].capitalize()})")
                ol.setStyleSheet("color:#9a9; font-size:10px;")
                hdr.addWidget(ol)
            rl = QLabel(f"  {cat.room_display}" if cat.room_display else "")
            rl.setStyleSheet(_META_STYLE)
            hdr.addWidget(rl)
            if cat is not b:
                x = QLabel("×")
                x.setStyleSheet("color:#444; font-size:14px; padding:0 10px;")
                hdr.addWidget(x)

        hdr.addStretch()
        if not ok:
            hdr.addWidget(QLabel(f"⚠  {reason}", styleSheet=_WARN_STYLE))

        root.addLayout(hdr)

        if not ok:
            root.addStretch()
            return

        # ── Stats grid + abilities ─────────────────────────────────────────
        mid = QHBoxLayout()
        mid.setSpacing(20)

        # Grid rows: Cat A, Cat B, then Offspring last
        grid_rows = [
            (a, True),    # (cat, is_cat)
            (b, True),
            (None, False),  # offspring range
        ]

        grid_w = QWidget()
        grid   = QGridLayout(grid_w)
        grid.setHorizontalSpacing(5)
        grid.setVerticalSpacing(5)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setColumnMinimumWidth(0, 110)   # ensure label column has room for full names

        # Stat column headers
        for j, stat in enumerate(STAT_NAMES):
            h = QLabel(stat)
            h.setStyleSheet("color:#555; font-size:9px; font-weight:bold;")
            h.setAlignment(Qt.AlignCenter)
            grid.addWidget(h, 0, j + 1)
        sum_col = len(STAT_NAMES) + 1
        sh = QLabel("Sum")
        sh.setStyleSheet("color:#455; font-size:9px; font-weight:bold;")
        sh.setAlignment(Qt.AlignCenter)
        grid.addWidget(sh, 0, sum_col)

        for i, (cat, is_cat) in enumerate(grid_rows):
            row_num = i + 1

            # Label cell: name + gender chip for cat rows, plain text for offspring
            lbl_w  = QWidget()
            lbl_hb = QHBoxLayout(lbl_w)
            lbl_hb.setContentsMargins(0, 0, 6, 0)
            lbl_hb.setSpacing(5)

            if is_cat:
                name_lbl = QLabel(cat.name)
                name_lbl.setStyleSheet("color:#ddd; font-size:11px; font-weight:bold;")
                gen_lbl  = QLabel(cat.gender_display)
                gen_lbl.setFixedWidth(20)
                gen_lbl.setAlignment(Qt.AlignCenter)
                gen_lbl.setStyleSheet(
                    "color:#fff; background:#253555; border-radius:4px;"
                    " font-size:10px; font-weight:bold;")
                lbl_hb.addWidget(name_lbl)
                lbl_hb.addWidget(gen_lbl)
            else:
                off_lbl = QLabel("Offspring")
                off_lbl.setStyleSheet("color:#555; font-size:10px; font-style:italic;")
                lbl_hb.addWidget(off_lbl)

            lbl_hb.addStretch()
            grid.addWidget(lbl_w, row_num, 0)

            # Stat cells
            for j, stat in enumerate(STAT_NAMES):
                if is_cat:
                    val  = cat.base_stats[stat]
                    c    = STAT_COLORS.get(val, QColor(100, 100, 115))
                    cell = QLabel(str(val))
                    cell.setAlignment(Qt.AlignCenter)
                    cell.setStyleSheet(
                        f"background:rgb({c.red()},{c.green()},{c.blue()});"
                        f"color:#fff; font-size:11px; font-weight:bold;"
                        f"border-radius:2px; padding:2px 6px;")
                else:
                    va, vb = a.base_stats[stat], b.base_stats[stat]
                    lo, hi = min(va, vb), max(va, vb)
                    c      = STAT_COLORS.get(hi, QColor(100, 100, 115))
                    text   = f"{lo}–{hi}" if lo != hi else str(lo)
                    cell   = QLabel(text)
                    cell.setAlignment(Qt.AlignCenter)
                    cell.setStyleSheet(
                        f"color:rgb({c.red()},{c.green()},{c.blue()});"
                        f"font-size:11px; font-weight:bold;")
                grid.addWidget(cell, row_num, j + 1)

            # Sum cell
            if is_cat:
                sv = sum(cat.base_stats.values())
                sc = QLabel(str(sv))
                sc.setStyleSheet("color:#aaa; font-size:11px; font-weight:bold;")
            else:
                lo_s = sum(min(a.base_stats[st], b.base_stats[st]) for st in STAT_NAMES)
                hi_s = sum(max(a.base_stats[st], b.base_stats[st]) for st in STAT_NAMES)
                sc = QLabel(f"{lo_s}–{hi_s}" if lo_s != hi_s else str(lo_s))
                sc.setStyleSheet("color:#777; font-size:11px; font-weight:bold;")
            sc.setAlignment(Qt.AlignCenter)
            grid.addWidget(sc, row_num, sum_col)

        mid.addWidget(grid_w)
        mid.addWidget(_vsep())

        # Abilities column
        ab_col = QVBoxLayout()
        ab_col.setSpacing(6)
        ab_col.addWidget(_sec("ABILITIES"))
        for cat in (a, b):
            if cat.abilities:
                row = QHBoxLayout()
                row.setSpacing(5)
                row.addWidget(QLabel(f"{cat.name}:", styleSheet="color:#555; font-size:10px;"))
                for ab in cat.abilities:
                    row.addWidget(_chip(ab, _ability_tip(ab)))
                row.addStretch()
                ab_col.addLayout(row)
        ab_col.addStretch()
        mid.addLayout(ab_col)

        root.addLayout(mid)

        # ── Possible mutations + lineage ───────────────────────────────────
        bot = QHBoxLayout()
        bot.setSpacing(20)

        if a.mutations or b.mutations:
            mc = QVBoxLayout()
            mc.setSpacing(4)
            mc.addWidget(_sec("MUTATIONS"))
            for cat in (a, b):
                if cat.mutations:
                    mrow = QHBoxLayout()
                    mrow.setSpacing(5)
                    mrow.addWidget(QLabel(f"{cat.name}:", styleSheet="color:#555; font-size:10px;"))
                    for mut in cat.mutations:
                        mrow.addWidget(_chip(mut, _ability_tip(mut)))
                    mrow.addStretch()
                    mc.addLayout(mrow)
            mc.addStretch()
            bot.addLayout(mc)
            bot.addWidget(_vsep())

        if self._show_lineage:
            lc = QVBoxLayout()
            lc.setSpacing(3)
            lc.addWidget(_sec("LINEAGE"))
            common    = find_common_ancestors(a, b)
            is_direct = (a in get_parents(b) or b in get_parents(a))
            is_haters = (b in getattr(a, 'haters', []) or a in getattr(b, 'haters', []))

            if is_haters:
                lc.addWidget(QLabel("⚠  These cats hate each other", styleSheet=_WARN_STYLE))
            if is_direct:
                lc.addWidget(QLabel("⚠  Direct parent/offspring", styleSheet=_WARN_STYLE))
            elif common:
                lc.addWidget(QLabel(
                    f"⚠  {len(common)} shared ancestor{'s' if len(common) > 1 else ''}: "
                    + "  ·  ".join(c.short_name for c in common[:6]),
                    styleSheet=_WARN_STYLE))
            elif get_parents(a) or get_parents(b):
                lc.addWidget(QLabel("✓  No shared ancestors", styleSheet=_SAFE_STYLE))
            else:
                lc.addWidget(QLabel("—  Lineage unknown", styleSheet=_META_STYLE))

            lc.addStretch()
            bot.addLayout(lc)
        bot.addStretch()

        root.addLayout(bot)


# ── Lineage tree dialog ───────────────────────────────────────────────────────

class LineageDialog(QDialog):
    """
    Family tree dialog — generations from oldest (top) to newest (bottom).
    Layout:  Grandparents → Parents → Self → Children → Grandchildren
    """

    def __init__(self, cat: 'Cat', parent=None, navigate_fn=None):
        super().__init__(parent)
        self.setWindowTitle(f"Family Tree — {cat.name}")
        self.setMinimumSize(700, 400)
        self.setStyleSheet(
            "QDialog { background:#0a0a18; }"
            "QScrollArea { border:none; background:#0a0a18; }"
            "QPushButton { background:#1e1e38; color:#ccc; border:1px solid #2a2a4a;"
            " padding:5px 14px; border-radius:4px; font-size:11px; }"
            "QPushButton:hover { background:#252555; }"
        )

        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 16, 20, 14)
        outer.setSpacing(12)

        # ── Reusable box builder ─────────────────────────────────────────
        def cat_box(cat_obj, highlight=False, dim=False):
            if cat_obj is None:
                btn = QPushButton("Unknown")
                btn.setEnabled(False)
                btn.setStyleSheet(
                    "QPushButton { color:#252535; font-size:10px; padding:6px 10px;"
                    " background:#0d0d1c; border:1px solid #141424; border-radius:5px; }")
            else:
                line2 = cat_obj.gender_display
                if cat_obj.room_display:
                    line2 += f"  {cat_obj.room_display}"
                bg     = "#1a2840" if highlight else ("#0e0e1a" if dim else "#121222")
                border = "#3060a0" if highlight else ("#1a1a28" if dim else "#222238")
                col    = "#ddd"    if not dim    else "#333"
                can_nav = navigate_fn is not None and cat_obj is not cat
                hover  = "#1d3560" if can_nav else bg
                btn = QPushButton(f"{cat_obj.name}\n{line2}")
                btn.setStyleSheet(
                    f"QPushButton {{ color:{col}; font-size:10px; padding:6px 10px;"
                    f" background:{bg}; border:1px solid {border}; border-radius:5px;"
                    f" text-align:center; }}"
                    f"QPushButton:hover {{ background:{hover}; }}")
                if can_nav:
                    btn.setCursor(Qt.CursorShape.PointingHandCursor)
                    btn.clicked.connect(
                        lambda checked=False, c=cat_obj: (self.accept(), navigate_fn(c)))
            btn.setMinimumWidth(100)
            btn.setMaximumWidth(200)
            return btn

        # ── Generation label ─────────────────────────────────────────────
        def gen_row_label(text):
            lbl = QLabel(text)
            lbl.setStyleSheet(
                "color:#333; font-size:9px; font-weight:bold; letter-spacing:1px;"
                " min-width:90px;")
            lbl.setAlignment(Qt.AlignVCenter | Qt.AlignRight)
            return lbl

        def make_gen_row(label_text, cat_list, highlight_all=False, dim_all=False):
            row = QHBoxLayout()
            row.setSpacing(8)
            row.addWidget(gen_row_label(label_text))
            for c in cat_list:
                row.addWidget(cat_box(c, highlight=highlight_all,
                                      dim=(dim_all and c is not None)))
            row.addStretch()
            outer.addLayout(row)

        # ── Build generations ────────────────────────────────────────────
        pa, pb = cat.parent_a, cat.parent_b
        gp_a1 = pa.parent_a if pa else None
        gp_a2 = pa.parent_b if pa else None
        gp_b1 = pb.parent_a if pb else None
        gp_b2 = pb.parent_b if pb else None

        grandparents = [gp_a1, gp_a2, gp_b1, gp_b2]
        parents      = [pa, pb]

        children = list(cat.children)
        grandchildren: list = []
        for child in children:
            grandchildren.extend(child.children)

        make_gen_row("GRANDPARENTS", grandparents)
        make_gen_row("PARENTS",      parents)
        make_gen_row("",             [cat], highlight_all=True)
        if children:
            make_gen_row("CHILDREN", children[:8])
            if len(children) > 8:
                outer.addWidget(
                    QLabel(f"  … and {len(children)-8} more children",
                           styleSheet="color:#444; font-size:10px; padding-left:100px;"))
        if grandchildren:
            unique_gc = list({id(g): g for g in grandchildren}.values())
            make_gen_row("GRANDCHILDREN", unique_gc[:8])
            if len(unique_gc) > 8:
                outer.addWidget(
                    QLabel(f"  … and {len(unique_gc)-8} more grandchildren",
                           styleSheet="color:#444; font-size:10px; padding-left:100px;"))

        outer.addStretch()
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        outer.addWidget(close_btn, alignment=Qt.AlignRight)
        _enforce_min_font_in_widget_tree(self)


class FamilyTreeBrowserView(QWidget):
    """
    Dedicated tree-browsing view:
    left side = cat list, right side = visual family tree for selected cat.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(
            "QWidget { background:#0a0a18; }"
            "QLabel { color:#bbb; }"
            "QListWidget { background:#0d0d1c; color:#ddd; border:1px solid #1e1e38; }"
            "QLineEdit { background:#0d0d1c; color:#ccc; border:1px solid #2a2a4a;"
            " border-radius:4px; padding:4px 8px; }"
            "QScrollArea { border:none; background:#0a0a18; }"
        )
        self._cats: list[Cat] = []
        self._by_key: dict[int, Cat] = {}
        self._alive_only: bool = True

        root = QHBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(12)

        # Left pane: search + list
        left = QWidget()
        left.setFixedWidth(320)
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.setSpacing(8)
        lv.addWidget(QLabel("Cats", styleSheet="color:#666; font-size:10px; font-weight:bold;"))
        mode_row = QHBoxLayout()
        mode_row.setContentsMargins(0, 0, 0, 0)
        mode_row.setSpacing(6)
        self._all_btn = _sidebar_btn("All")
        self._alive_btn = _sidebar_btn("Alive")
        self._all_btn.setCheckable(True)
        self._alive_btn.setCheckable(True)
        self._alive_btn.setChecked(True)
        self._all_btn.clicked.connect(lambda: self._set_alive_only(False))
        self._alive_btn.clicked.connect(lambda: self._set_alive_only(True))
        mode_row.addWidget(self._all_btn)
        mode_row.addWidget(self._alive_btn)
        lv.addLayout(mode_row)
        self._search = QLineEdit()
        self._search.setPlaceholderText("Search cat name…")
        lv.addWidget(self._search)
        self._list = QListWidget()
        lv.addWidget(self._list, 1)
        root.addWidget(left)

        # Right pane: tree
        self._tree_scroll = QScrollArea()
        self._tree_scroll.setWidgetResizable(True)
        self._tree_content = QWidget()
        self._tree_scroll.setWidget(self._tree_content)
        root.addWidget(self._tree_scroll, 1)

        self._search.textChanged.connect(self._refresh_list)
        self._list.currentItemChanged.connect(self._on_current_item_changed)
        _enforce_min_font_in_widget_tree(self)

    def set_cats(self, cats: list[Cat]):
        selected_key = None
        cur = self._list.currentItem()
        if cur is not None:
            selected_key = int(cur.data(Qt.UserRole))
        self._cats = sorted(cats, key=lambda c: (c.name or "").lower())
        self._by_key = {c.db_key: c for c in self._cats}
        self._refresh_list()
        if selected_key is not None and selected_key in self._by_key:
            self.select_cat(self._by_key[selected_key])
        elif self._list.count():
            self._list.setCurrentRow(0)
        else:
            self._render_tree(None)

    def select_cat(self, cat: Optional[Cat]):
        if cat is None:
            return
        for i in range(self._list.count()):
            item = self._list.item(i)
            if int(item.data(Qt.UserRole)) == cat.db_key:
                self._list.setCurrentRow(i)
                self._list.scrollToItem(item)
                return

    def _open_cat_from_tree(self, cat: Optional[Cat]):
        if cat is None:
            return
        # If a gone cat is clicked while Alive filter is active, switch to All.
        if self._alive_only and cat.status == "Gone":
            self._set_alive_only(False)
        # Ensure search does not hide the clicked target.
        if self._search.text():
            self._search.clear()
        self.select_cat(cat)

    def _set_alive_only(self, enabled: bool):
        self._alive_only = enabled
        self._alive_btn.setChecked(enabled)
        self._all_btn.setChecked(not enabled)
        self._refresh_list()

    def _refresh_list(self):
        query = self._search.text().strip().lower()
        current_key = None
        cur = self._list.currentItem()
        if cur is not None:
            current_key = int(cur.data(Qt.UserRole))

        self._list.clear()
        for cat in self._cats:
            if self._alive_only and cat.status == "Gone":
                continue
            if query and query not in cat.name.lower():
                continue
            label = f"{cat.name}  ({cat.gender_display})"
            if cat.status != "In House":
                label += f"  [{STATUS_ABBREV.get(cat.status, cat.status)}]"
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, cat.db_key)
            self._list.addItem(item)

        if self._list.count() == 0:
            self._render_tree(None)
            return
        if current_key is not None:
            for i in range(self._list.count()):
                it = self._list.item(i)
                if int(it.data(Qt.UserRole)) == current_key:
                    self._list.setCurrentRow(i)
                    return
        self._list.setCurrentRow(0)

    def _on_current_item_changed(self, current, previous):
        if current is None:
            self._render_tree(None)
            return
        cat = self._by_key.get(int(current.data(Qt.UserRole)))
        self._render_tree(cat)

    def _render_tree(self, cat: Optional[Cat]):
        self._tree_content = QWidget()
        self._tree_scroll.setWidget(self._tree_content)

        root = QVBoxLayout(self._tree_content)
        root.setContentsMargins(8, 6, 8, 8)
        root.setSpacing(10)

        if cat is None:
            root.addWidget(QLabel("No cats match the current filter.", styleSheet="color:#666; font-size:12px;"))
            root.addStretch()
            return

        title = QLabel(f"Family Tree — {cat.name}")
        title.setStyleSheet("color:#ddd; font-size:16px; font-weight:bold;")
        root.addWidget(title)
        root.addWidget(QLabel("Click any box to jump to that cat.", styleSheet="color:#666; font-size:11px;"))

        def cat_box(c: Optional[Cat], highlight=False):
            if c is None:
                btn = QPushButton("Unknown")
                btn.setEnabled(False)
                btn.setStyleSheet(
                    "QPushButton { color:#303040; font-size:10px; padding:7px 10px;"
                    " background:#0e0e1c; border:1px solid #18182a; border-radius:6px; }")
                return btn
            line2 = c.gender_display
            if c.room_display:
                line2 += f"  {c.room_display}"
            if c.status == "Gone":
                line2 += "  (Gone)"
            bg = "#1d2f4a" if highlight else "#131326"
            border = "#3b5f95" if highlight else "#252545"
            btn = QPushButton(f"{c.name}\n{line2}")
            btn.setStyleSheet(
                f"QPushButton {{ color:#ddd; font-size:10px; padding:7px 10px;"
                f" background:{bg}; border:1px solid {border}; border-radius:6px; }}"
                "QPushButton:hover { background:#1a2a46; }")
            if c is not cat:
                btn.clicked.connect(lambda checked=False, target=c: self._open_cat_from_tree(target))
            else:
                btn.setEnabled(False)
            btn.setMinimumWidth(120)
            return btn

        def row_label(text: str) -> QLabel:
            lbl = QLabel(text)
            lbl.setStyleSheet("color:#444; font-weight:bold; letter-spacing:1px;")
            lbl.setFixedWidth(row_label_width)
            lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            return lbl

        def add_generation_row(label: str, cats_row: list[Optional[Cat]], highlight_self=False):
            row = QHBoxLayout()
            row.setSpacing(8)
            row.addWidget(row_label(label))
            for c in cats_row:
                row.addWidget(cat_box(c, highlight=highlight_self and c is cat))
            row.addStretch()
            root.addLayout(row)

        def add_arrow():
            a = QLabel("↓")
            a.setStyleSheet("color:#2f3f66; font-size:16px;")
            a.setAlignment(Qt.AlignCenter)
            root.addWidget(a)

        def _dedupe_keep_order(items: list[Cat]) -> list[Cat]:
            seen = set()
            out: list[Cat] = []
            for item in items:
                sid = id(item)
                if sid in seen:
                    continue
                seen.add(sid)
                out.append(item)
            return out

        def _ancestor_row_label(level: int) -> str:
            if level == 1:
                return "PARENTS"
            if level == 2:
                return "GRANDPARENTS"
            if level == 3:
                return "GREAT-GRANDPARENTS"
            return f"{level - 2}x GREAT-GRANDPARENTS"

        # Build all known ancestor levels (1=parents, 2=grandparents, ...).
        ancestor_levels: list[list[Cat]] = []
        frontier: list[Cat] = [cat]
        for _ in range(8):
            nxt: list[Cat] = []
            for node in frontier:
                if node.parent_a is not None:
                    nxt.append(node.parent_a)
                if node.parent_b is not None:
                    nxt.append(node.parent_b)
            nxt = _dedupe_keep_order(nxt)
            if not nxt:
                break
            ancestor_levels.append(nxt)
            frontier = nxt

        # Dynamic row-label gutter width: based on the longest visible label and
        # current font metrics, so it tracks zoom/font-size changes.
        label_texts = ["SELF", "CHILDREN", "GRANDCHILDREN"] + [
            _ancestor_row_label(i) for i in range(1, len(ancestor_levels) + 1)
        ]
        label_font = QFont(self.font())
        label_font.setBold(True)
        fm = QFontMetrics(label_font)
        max_text_px = max(fm.horizontalAdvance(t) for t in label_texts)
        # Row labels use letter-spacing:1px in stylesheet; account for that so
        # long prefixes like "10x " are fully measured.
        max_letter_spacing_px = max(max(len(t) - 1, 0) for t in label_texts)
        row_label_width = max(120, max_text_px + max_letter_spacing_px + 24)

        children = list(cat.children)
        grandchildren: list[Cat] = []
        for child in children:
            grandchildren.extend(child.children)
        grandchildren = list({id(c): c for c in grandchildren}.values())

        # Render oldest ancestors at top, then down to self.
        for idx in range(len(ancestor_levels), 0, -1):
            level_nodes = ancestor_levels[idx - 1]
            add_generation_row(_ancestor_row_label(idx), level_nodes[:12])
            if len(level_nodes) > 12:
                root.addWidget(QLabel(
                    f"… and {len(level_nodes)-12} more in {_ancestor_row_label(idx)}",
                    styleSheet="color:#555; font-size:10px;"))
            add_arrow()
        add_generation_row("SELF", [cat], highlight_self=True)

        if children:
            add_arrow()
            add_generation_row("CHILDREN", children[:10])
            if len(children) > 10:
                root.addWidget(QLabel(f"… and {len(children)-10} more children", styleSheet="color:#555; font-size:10px;"))
        if grandchildren:
            add_arrow()
            add_generation_row("GRANDCHILDREN", grandchildren[:10])
            if len(grandchildren) > 10:
                root.addWidget(QLabel(f"… and {len(grandchildren)-10} more grandchildren", styleSheet="color:#555; font-size:10px;"))
        if not any([ancestor_levels, children, grandchildren]):
            root.addWidget(QLabel("No known lineage data for this cat yet.", styleSheet="color:#666; font-size:12px;"))

        root.addStretch()
        _enforce_min_font_in_widget_tree(self._tree_content)


class SafeBreedingView(QWidget):
    """Dedicated view for ranking alive breeding candidates."""
    class _ColumnPaddingDelegate(QStyledItemDelegate):
        def __init__(self, extra_width: int, left_padding: int = 0, parent=None):
            super().__init__(parent)
            self._extra_width = extra_width
            self._left_padding = left_padding

        def sizeHint(self, option, index):
            s = super().sizeHint(option, index)
            return QSize(s.width() + self._extra_width, s.height())

        def paint(self, painter, option, index):
            if self._left_padding <= 0:
                return super().paint(painter, option, index)

            opt = QStyleOptionViewItem(option)
            self.initStyleOption(opt, index)
            style = opt.widget.style() if opt.widget is not None else QApplication.style()

            text = opt.text
            opt.text = ""
            style.drawControl(QStyle.CE_ItemViewItem, opt, painter, opt.widget)

            text_rect = style.subElementRect(QStyle.SE_ItemViewItemText, opt, opt.widget).adjusted(
                self._left_padding, 0, 0, 0
            )
            if opt.textElideMode != Qt.ElideNone:
                text = opt.fontMetrics.elidedText(text, opt.textElideMode, text_rect.width())

            painter.save()
            if opt.state & QStyle.State_Selected:
                painter.setPen(opt.palette.color(QPalette.HighlightedText))
            else:
                painter.setPen(opt.palette.color(QPalette.Text))
            painter.setFont(opt.font)
            painter.drawText(text_rect, Qt.AlignVCenter | Qt.AlignLeft, text)
            painter.restore()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(
            "QWidget { background:#0a0a18; }"
            "QLabel { color:#bbb; }"
            "QListWidget { background:#0d0d1c; color:#ddd; border:1px solid #1e1e38; }"
            "QLineEdit { background:#0d0d1c; color:#ccc; border:1px solid #2a2a4a;"
            " border-radius:4px; padding:4px 8px; }"
            "QTableWidget { background:#101023; color:#ddd; border:1px solid #26264a; }"
            "QHeaderView::section { background:#151532; color:#7d8bb0; border:none; padding:4px; font-weight:bold; }"
        )
        self._cats: list[Cat] = []
        self._alive: list[Cat] = []
        self._by_key: dict[int, Cat] = {}
        self._table_row_cat_keys: list[int] = []

        root = QHBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(12)

        left = QWidget()
        left.setFixedWidth(320)
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.setSpacing(8)
        lv.addWidget(QLabel("Alive cats", styleSheet="color:#666; font-size:10px; font-weight:bold;"))
        self._search = QLineEdit()
        self._search.setPlaceholderText("Search cat name…")
        lv.addWidget(self._search)
        self._list = QListWidget()
        lv.addWidget(self._list, 1)
        root.addWidget(left)

        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(8)
        self._title = QLabel("Safe Breeding")
        self._title.setStyleSheet("color:#ddd; font-size:16px; font-weight:bold;")
        self._summary = QLabel("")
        self._summary.setStyleSheet("color:#666; font-size:11px;")
        self._table = QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels(["Cat", "Risk%", "Shared Anc.", "Children will be"])
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionMode(QAbstractItemView.SingleSelection)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.setSortingEnabled(False)
        self._table.horizontalHeader().setStretchLastSection(False)
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Fixed)
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Fixed)
        self._table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self._table.setItemDelegateForColumn(0, SafeBreedingView._ColumnPaddingDelegate(24, 8, self._table))
        self._table.setColumnWidth(1, 80)
        self._table.setColumnWidth(2, 110)
        self._table.setItemDelegateForColumn(3, SafeBreedingView._ColumnPaddingDelegate(24, 0, self._table))

        rv.addWidget(self._title)
        rv.addWidget(self._summary)
        rv.addWidget(self._table, 1)
        root.addWidget(right, 1)

        self._search.textChanged.connect(self._refresh_list)
        self._list.currentItemChanged.connect(self._on_current_item_changed)
        self._table.cellClicked.connect(self._on_table_row_clicked)
        _enforce_min_font_in_widget_tree(self)

    def set_cats(self, cats: list[Cat]):
        selected_key = None
        cur = self._list.currentItem()
        if cur is not None:
            selected_key = int(cur.data(Qt.UserRole))
        self._cats = cats
        self._alive = sorted([c for c in cats if c.status != "Gone"], key=lambda c: (c.name or "").lower())
        self._by_key = {c.db_key: c for c in self._alive}
        self._refresh_list()
        if selected_key is not None and selected_key in self._by_key:
            self.select_cat(self._by_key[selected_key])
        elif self._list.count():
            self._list.setCurrentRow(0)
        else:
            self._render_for(None)

    def select_cat(self, cat: Optional[Cat]):
        if cat is None or cat.db_key not in self._by_key:
            return
        for i in range(self._list.count()):
            item = self._list.item(i)
            if int(item.data(Qt.UserRole)) == cat.db_key:
                self._list.setCurrentRow(i)
                self._list.scrollToItem(item)
                return

    def _refresh_list(self):
        query = self._search.text().strip().lower()
        current_key = None
        cur = self._list.currentItem()
        if cur is not None:
            current_key = int(cur.data(Qt.UserRole))

        self._list.clear()
        for cat in self._alive:
            if query and query not in cat.name.lower():
                continue
            item = QListWidgetItem(f"{cat.name}  ({cat.gender_display})")
            item.setData(Qt.UserRole, cat.db_key)
            self._list.addItem(item)
        if self._list.count() == 0:
            self._render_for(None)
            return
        if current_key is not None:
            for i in range(self._list.count()):
                item = self._list.item(i)
                if int(item.data(Qt.UserRole)) == current_key:
                    self._list.setCurrentRow(i)
                    return
        self._list.setCurrentRow(0)

    def _on_current_item_changed(self, current, previous):
        if current is None:
            self._render_for(None)
            return
        self._render_for(self._by_key.get(int(current.data(Qt.UserRole))))

    def _on_table_row_clicked(self, row: int, _column: int):
        if row < 0 or row >= len(self._table_row_cat_keys):
            return
        cat = self._by_key.get(self._table_row_cat_keys[row])
        if cat is not None:
            self.select_cat(cat)

    def _render_for(self, cat: Optional[Cat]):
        self._table.setRowCount(0)
        self._table_row_cat_keys = []
        if cat is None:
            self._title.setText("Safe Breeding")
            self._summary.setText("Select an alive cat.")
            return

        self._title.setText(f"Safe Breeding — {cat.name}")
        candidates: list[tuple[float, int, int, Cat]] = []
        for other in self._alive:
            if other is cat:
                continue
            ok, _ = can_breed(cat, other)
            if not ok:
                continue
            shared, recent_shared = shared_ancestor_counts(cat, other, recent_depth=3)
            rel = risk_percent(cat, other)
            closest_recent_gen = 0
            if recent_shared:
                da = _ancestor_depths(cat, max_depth=8)
                db = _ancestor_depths(other, max_depth=8)
                common = set(da.keys()) & set(db.keys())
                recent_levels = [
                    max(da[anc], db[anc])
                    for anc in common
                    if da[anc] <= 3 and db[anc] <= 3
                ]
                closest_recent_gen = min(recent_levels) if recent_levels else 3
            # Sort by Risk% first so safest pairs appear at top.
            candidates.append((rel, recent_shared * 1000 + shared, closest_recent_gen, other))
        candidates.sort(key=lambda t: (t[0], t[1], (t[3].name or "").lower()))

        self._summary.setText(
            f"{len(candidates)} possible alive candidates  |  "
            "Risk% = normalized CoI (0.25 => 100%)"
        )
        self._table.setRowCount(len(candidates))
        for row, (rel, packed_shared, closest_recent_gen, other) in enumerate(candidates):
            self._table_row_cat_keys.append(other.db_key)
            shared = packed_shared % 1000
            risk_pct = int(round(rel))
            if risk_pct >= 100:
                tag, col = "Highly Inbred", QColor(217, 119, 119)
            elif risk_pct >= 50:
                tag, col = "Moderately Inbred", QColor(216, 181, 106)
            elif risk_pct >= 20:
                tag, col = "Slightly Inbred", QColor(143, 201, 230)
            else:
                tag, col = "Not Inbred", QColor(98, 194, 135)

            name_item = QTableWidgetItem(f"{other.name} ({other.gender_display})")
            rel_item = QTableWidgetItem(f"{risk_pct}%")
            shared_item = QTableWidgetItem(str(shared))
            risk_item = QTableWidgetItem(tag)
            rel_item.setData(Qt.UserRole, risk_pct)
            shared_item.setData(Qt.UserRole, shared)
            for it in (rel_item, shared_item, risk_item):
                it.setTextAlignment(Qt.AlignCenter)
            risk_item.setForeground(QBrush(col))
            self._table.setItem(row, 0, name_item)
            self._table.setItem(row, 1, rel_item)
            self._table.setItem(row, 2, shared_item)
            self._table.setItem(row, 3, risk_item)


# ── Room Optimizer View ───────────────────────────────────────────────────────

class RoomOptimizerView(QWidget):
    """View for optimizing cat room distribution to maximize breeding outcomes."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(
            "QWidget { background:#0a0a18; }"
            "QLabel { color:#bbb; }"
            "QTableWidget { background:#101023; color:#ddd; border:1px solid #26264a; }"
            "QHeaderView::section { background:#151532; color:#7d8bb0; border:none; padding:4px; font-weight:bold; }"
            "QPushButton { background:#1a1a32; color:#aaa; border:1px solid #2a2a4a; "
            "border-radius:4px; padding:6px 12px; font-size:11px; }"
            "QPushButton:hover { background:#252545; color:#ddd; }"
        )
        self._cats: list[Cat] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(12)

        # Header
        header = QHBoxLayout()
        self._title = QLabel("Room Distribution Optimizer")
        self._title.setStyleSheet("color:#ddd; font-size:18px; font-weight:bold;")
        self._summary = QLabel("")
        self._summary.setStyleSheet("color:#666; font-size:11px;")
        header.addWidget(self._title)
        header.addStretch()
        header.addWidget(self._summary)
        root.addLayout(header)

        # Controls
        controls = QHBoxLayout()
        controls.setSpacing(8)

        self._min_stats_label = QLabel("Min total stats:")
        self._min_stats_label.setStyleSheet("color:#888; font-size:11px;")
        controls.addWidget(self._min_stats_label)

        self._min_stats_input = QLineEdit()
        self._min_stats_input.setPlaceholderText("0")
        self._min_stats_input.setFixedWidth(60)
        self._min_stats_input.setStyleSheet(
            "QLineEdit { background:#0d0d1c; color:#ccc; border:1px solid #2a2a4a;"
            " border-radius:4px; padding:4px 8px; }"
        )
        controls.addWidget(self._min_stats_input)

        controls.addSpacing(16)

        self._max_risk_label = QLabel("Max inbreeding risk %:")
        self._max_risk_label.setStyleSheet("color:#888; font-size:11px;")
        controls.addWidget(self._max_risk_label)

        self._max_risk_input = QLineEdit()
        self._max_risk_input.setPlaceholderText("20")
        self._max_risk_input.setFixedWidth(60)
        self._max_risk_input.setStyleSheet(
            "QLineEdit { background:#0d0d1c; color:#ccc; border:1px solid #2a2a4a;"
            " border-radius:4px; padding:4px 8px; }"
        )
        controls.addWidget(self._max_risk_input)

        self._optimize_btn = QPushButton("Calculate Optimal Distribution")
        self._optimize_btn.clicked.connect(self._calculate_optimal_distribution)
        controls.addWidget(self._optimize_btn)

        controls.addStretch()
        root.addLayout(controls)

        # Results table
        self._table = QTableWidget(0, 6)
        self._table.setHorizontalHeaderLabels([
            "Room", "Cats to Place", "Expected Pairs", "Avg Stats", "Risk%", "Details"
        ])
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionMode(QAbstractItemView.SingleSelection)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.setSortingEnabled(False)

        hh = self._table.horizontalHeader()
        hh.setStretchLastSection(True)
        hh.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.Stretch)
        hh.setSectionResizeMode(2, QHeaderView.Fixed)
        hh.setSectionResizeMode(3, QHeaderView.Fixed)
        hh.setSectionResizeMode(4, QHeaderView.Fixed)
        hh.setSectionResizeMode(5, QHeaderView.Stretch)
        self._table.setColumnWidth(2, 100)
        self._table.setColumnWidth(3, 90)
        self._table.setColumnWidth(4, 70)

        root.addWidget(self._table, 1)

        _enforce_min_font_in_widget_tree(self)

    def set_cats(self, cats: list[Cat]):
        self._cats = cats
        self._summary.setText(f"{len([c for c in cats if c.status != 'Gone'])} alive cats available")

    def _calculate_optimal_distribution(self):
        """Calculate and display optimal room distribution."""
        alive_cats = [c for c in self._cats if c.status != "Gone"]

        # Get minimum stats filter
        min_stats = 0
        try:
            if self._min_stats_input.text().strip():
                min_stats = int(self._min_stats_input.text().strip())
        except ValueError:
            pass

        # Get maximum risk filter
        max_risk = 100  # Default: allow all
        try:
            if self._max_risk_input.text().strip():
                max_risk = float(self._max_risk_input.text().strip())
        except ValueError:
            pass

        # Filter cats by minimum stats
        if min_stats > 0:
            alive_cats = [c for c in alive_cats if sum(c.total_stats.values()) >= min_stats]

        if len(alive_cats) < 2:
            self._table.setRowCount(0)
            self._summary.setText("Not enough cats to optimize")
            return

        # Separate males and females
        males = [c for c in alive_cats if c.gender == "male"]
        females = [c for c in alive_cats if c.gender == "female"]
        unknown = [c for c in alive_cats if c.gender == "?"]

        # Sort by total stats (best first)
        males.sort(key=lambda c: sum(c.total_stats.values()), reverse=True)
        females.sort(key=lambda c: sum(c.total_stats.values()), reverse=True)
        unknown.sort(key=lambda c: sum(c.total_stats.values()), reverse=True)

        # Priority rooms + fallback
        priority_rooms = ["Priority 1", "Priority 2", "Priority 3", "Priority 4"]
        fallback_room = "Fallback"
        all_rooms = priority_rooms + [fallback_room]
        room_assignments = {room: [] for room in all_rooms}

        # Strategy: Find best breeding pairs and distribute to priority rooms
        # Maximize expected offspring quality while respecting risk constraints

        # Find all viable pairs and score them
        all_cats = males + females + unknown
        pairs_with_scores = []

        for i, cat_a in enumerate(all_cats):
            for cat_b in all_cats[i+1:]:
                ok, _ = can_breed(cat_a, cat_b)
                if not ok:
                    continue

                risk = risk_percent(cat_a, cat_b)
                if risk > max_risk:
                    continue

                # Quality score: average stats with penalty for risk
                avg_stats = (sum(cat_a.total_stats.values()) + sum(cat_b.total_stats.values())) / 2
                quality = avg_stats * (1.0 - risk / 200.0)

                pairs_with_scores.append({
                    'cat_a': cat_a,
                    'cat_b': cat_b,
                    'risk': risk,
                    'avg_stats': avg_stats,
                    'quality': quality
                })

        # Sort pairs by quality (best first)
        pairs_with_scores.sort(key=lambda p: p['quality'], reverse=True)

        # Greedy assignment: place best pairs in priority rooms
        assigned_cats = set()
        max_cats_per_priority_room = 6

        for pair in pairs_with_scores:
            cat_a = pair['cat_a']
            cat_b = pair['cat_b']

            # Skip if either cat already assigned
            if cat_a.db_key in assigned_cats or cat_b.db_key in assigned_cats:
                continue

            # Try to place in a priority room
            placed = False
            for room in priority_rooms:
                room_cats = room_assignments[room]

                # Check if room has space
                if len(room_cats) >= max_cats_per_priority_room:
                    continue

                # Check compatibility with existing cats in room
                can_place_both = True
                for existing_cat in room_cats:
                    # Check cat_a compatibility
                    ok_a, _ = can_breed(cat_a, existing_cat)
                    if ok_a:
                        risk_a = risk_percent(cat_a, existing_cat)
                        if risk_a > max_risk:
                            can_place_both = False
                            break

                    # Check cat_b compatibility
                    ok_b, _ = can_breed(cat_b, existing_cat)
                    if ok_b:
                        risk_b = risk_percent(cat_b, existing_cat)
                        if risk_b > max_risk:
                            can_place_both = False
                            break

                if can_place_both and len(room_cats) + 2 <= max_cats_per_priority_room:
                    room_assignments[room].extend([cat_a, cat_b])
                    assigned_cats.add(cat_a.db_key)
                    assigned_cats.add(cat_b.db_key)
                    placed = True
                    break

            if not placed:
                # Try placing individually in priority rooms
                for cat in [cat_a, cat_b]:
                    if cat.db_key in assigned_cats:
                        continue

                    for room in priority_rooms:
                        room_cats = room_assignments[room]
                        if len(room_cats) >= max_cats_per_priority_room:
                            continue

                        # Check compatibility
                        compatible = True
                        for existing_cat in room_cats:
                            ok, _ = can_breed(cat, existing_cat)
                            if ok:
                                risk = risk_percent(cat, existing_cat)
                                if risk > max_risk:
                                    compatible = False
                                    break

                        if compatible:
                            room_assignments[room].append(cat)
                            assigned_cats.add(cat.db_key)
                            break

        # Put unassigned cats in fallback room
        for cat in all_cats:
            if cat.db_key not in assigned_cats:
                room_assignments[fallback_room].append(cat)

        # Display results
        self._table.setRowCount(0)
        row_idx = 0
        total_pairs = 0
        total_assigned = 0

        for room in all_rooms:
            cats_in_room = room_assignments[room]

            if not cats_in_room:
                continue

            total_assigned += len(cats_in_room)

            # Calculate expected pairs and outcomes for this room
            room_pairs = []
            for i, cat_a in enumerate(cats_in_room):
                for cat_b in cats_in_room[i+1:]:
                    ok, _ = can_breed(cat_a, cat_b)
                    if ok:
                        risk = risk_percent(cat_a, cat_b)
                        avg_stats = (sum(cat_a.total_stats.values()) + sum(cat_b.total_stats.values())) / 2
                        room_pairs.append({'cat_a': cat_a, 'cat_b': cat_b, 'risk': risk, 'avg_stats': avg_stats})

            if not room_pairs:
                continue

            total_pairs += len(room_pairs)

            self._table.insertRow(row_idx)

            # Room name with special styling for fallback
            room_item = QTableWidgetItem(room)
            room_item.setTextAlignment(Qt.AlignCenter)
            if room == fallback_room:
                room_item.setForeground(QBrush(QColor(150, 150, 150)))

            # Cats to place
            cat_names = [f"{c.name} ({c.gender_display})" for c in cats_in_room]
            cats_item = QTableWidgetItem(", ".join(cat_names))

            # Expected pairs
            pairs_item = QTableWidgetItem(str(len(room_pairs)))
            pairs_item.setTextAlignment(Qt.AlignCenter)

            # Average stats of expected offspring
            avg_room_stats = sum(p['avg_stats'] for p in room_pairs) / len(room_pairs)
            stats_item = QTableWidgetItem(f"{avg_room_stats:.1f}")
            stats_item.setTextAlignment(Qt.AlignCenter)

            # Color code by stats quality
            if avg_room_stats >= 200:
                stats_item.setForeground(QBrush(QColor(98, 194, 135)))
            elif avg_room_stats >= 150:
                stats_item.setForeground(QBrush(QColor(143, 201, 230)))
            else:
                stats_item.setForeground(QBrush(QColor(190, 145, 40)))

            # Average risk
            avg_risk = sum(p['risk'] for p in room_pairs) / len(room_pairs)
            risk_item = QTableWidgetItem(f"{avg_risk:.0f}%")
            risk_item.setTextAlignment(Qt.AlignCenter)

            # Color code by risk
            if avg_risk >= 50:
                risk_item.setForeground(QBrush(QColor(217, 119, 119)))
            elif avg_risk >= 20:
                risk_item.setForeground(QBrush(QColor(216, 181, 106)))
            else:
                risk_item.setForeground(QBrush(QColor(98, 194, 135)))

            # Details: list all possible pairs
            details_lines = []
            for p in room_pairs[:3]:  # Show top 3 pairs
                details_lines.append(
                    f"{p['cat_a'].name} × {p['cat_b'].name} "
                    f"(stats: {p['avg_stats']:.0f}, risk: {p['risk']:.0f}%)"
                )
            if len(room_pairs) > 3:
                details_lines.append(f"... and {len(room_pairs) - 3} more")
            details_item = QTableWidgetItem("; ".join(details_lines))

            self._table.setItem(row_idx, 0, room_item)
            self._table.setItem(row_idx, 1, cats_item)
            self._table.setItem(row_idx, 2, pairs_item)
            self._table.setItem(row_idx, 3, stats_item)
            self._table.setItem(row_idx, 4, risk_item)
            self._table.setItem(row_idx, 5, details_item)

            row_idx += 1

        # Calculate stats
        filter_info = []
        if min_stats > 0:
            filter_info.append(f"min stats: {min_stats}")
        if max_risk < 100:
            filter_info.append(f"max risk: {max_risk}%")

        filter_str = f"  |  Filters: {', '.join(filter_info)}" if filter_info else ""

        self._summary.setText(
            f"Optimized {total_assigned} cats into {row_idx} rooms  |  "
            f"{total_pairs} total breeding pairs{filter_str}"
        )


# ── Sidebar helpers ───────────────────────────────────────────────────────────

_SIDEBAR_BTN = """
QPushButton {
    color:#ccc; background:transparent; border:none;
    text-align:left; padding:6px 10px; border-radius:4px; font-size:12px;
}
QPushButton:hover   { background:#252545; }
QPushButton:checked { background:#353568; color:#fff; font-weight:bold; }
"""

def _sidebar_btn(label: str) -> QPushButton:
    btn = QPushButton(label)
    btn.setCheckable(True)
    btn.setStyleSheet(_SIDEBAR_BTN)
    return btn


# ── Main window ───────────────────────────────────────────────────────────────

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Mewgenics Breeding Manager")
        self.resize(1440, 900)

        self._current_save = None
        self._cats: list[Cat] = []
        self._room_btns: dict = {}
        self._active_btn = None
        self._show_lineage: bool = False
        self._tree_view: Optional[FamilyTreeBrowserView] = None
        self._safe_breeding_view: Optional[SafeBreedingView] = None
        self._room_optimizer_view: Optional[RoomOptimizerView] = None
        self._zoom_percent: int = 100
        self._base_font: QFont = QApplication.instance().font()
        self._base_sidebar_width = 190
        self._base_header_height = 46
        self._base_search_width = 180
        self._base_col_widths = {
            COL_NAME: 130,
            COL_GEN: _W_GEN,
            COL_STAT: _W_STATUS,
            COL_SUM: 38,
            COL_ABIL: 180,
            COL_MUTS: 155,
            COL_REL: _W_REL,
            COL_AGE: 34,
            COL_INB: 38,
            **{c: _W_STAT for c in STAT_COLS},
        }

        self._build_ui()
        self._build_menu()
        self._apply_zoom()

        self._watcher = QFileSystemWatcher(self)
        self._watcher.fileChanged.connect(self._on_file_changed)

        saves = find_save_files()
        if saves:
            self.load_save(saves[0])

    # ── Menu ──────────────────────────────────────────────────────────────

    def _build_menu(self):
        fm = self.menuBar().addMenu("File")

        oa = QAction("Open Save File…", self)
        oa.setShortcut("Ctrl+O")
        oa.triggered.connect(self._open_file)
        fm.addAction(oa)

        ra = QAction("Reload", self)
        ra.setShortcut("F5")
        ra.triggered.connect(self._reload)
        fm.addAction(ra)

        fm.addSeparator()
        for path in find_save_files():
            a = QAction(os.path.basename(path), self)
            a.triggered.connect(lambda _, p=path: self.load_save(p))
            fm.addAction(a)

        sm = self.menuBar().addMenu("Settings")
        self._lineage_action = QAction("Show Family Tree && Inbreeding", self)
        self._lineage_action.setCheckable(True)
        self._lineage_action.setChecked(False)
        self._lineage_action.triggered.connect(self._toggle_lineage)
        sm.addAction(self._lineage_action)

        sm.addSeparator()
        zoom_in = QAction("Zoom In", self)
        zoom_in_keys = QKeySequence.keyBindings(QKeySequence.StandardKey.ZoomIn)
        if not zoom_in_keys:
            zoom_in_keys = []
        for seq in (QKeySequence("Ctrl+="), QKeySequence("Ctrl++")):
            if seq not in zoom_in_keys:
                zoom_in_keys.append(seq)
        zoom_in.setShortcuts(zoom_in_keys)
        zoom_in.triggered.connect(lambda: self._change_zoom(+1))
        sm.addAction(zoom_in)

        zoom_out = QAction("Zoom Out", self)
        zoom_out_keys = QKeySequence.keyBindings(QKeySequence.StandardKey.ZoomOut)
        if not zoom_out_keys:
            zoom_out_keys = []
        if QKeySequence("Ctrl+-") not in zoom_out_keys:
            zoom_out_keys.append(QKeySequence("Ctrl+-"))
        zoom_out.setShortcuts(zoom_out_keys)
        zoom_out.triggered.connect(lambda: self._change_zoom(-1))
        sm.addAction(zoom_out)

        zoom_reset = QAction("Reset Zoom", self)
        zoom_reset.setShortcut("Ctrl+0")
        zoom_reset.triggered.connect(self._reset_zoom)
        sm.addAction(zoom_reset)

        self._zoom_info_action = QAction("", self)
        self._zoom_info_action.setEnabled(False)
        sm.addAction(self._zoom_info_action)
        self._update_zoom_info_action()

    # ── Layout ────────────────────────────────────────────────────────────

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        rl = QHBoxLayout(central)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(0)

        hs = QSplitter(Qt.Horizontal)
        rl.addWidget(hs)
        hs.addWidget(self._build_sidebar())
        hs.addWidget(self._build_content())
        hs.setStretchFactor(0, 0)
        hs.setStretchFactor(1, 1)
        hs.setSizes([190, 1250])
        _enforce_min_font_in_widget_tree(central)

    # ── Sidebar ────────────────────────────────────────────────────────────

    def _build_sidebar(self) -> QWidget:
        w  = QWidget()
        self._sidebar = w
        w.setFixedWidth(self._base_sidebar_width)
        w.setStyleSheet("background:#14142a;")
        vb = QVBoxLayout(w)
        vb.setContentsMargins(8, 14, 8, 12)
        vb.setSpacing(2)

        def sl(text):
            l = QLabel(text)
            l.setStyleSheet("color:#444; font-size:10px; font-weight:bold;"
                            " letter-spacing:1px; padding:8px 4px 4px 4px;")
            return l

        vb.addWidget(sl("VIEW"))
        self._btn_everyone = _sidebar_btn("All Cats")
        self._btn_everyone.clicked.connect(
            lambda: self._filter("__all__", self._btn_everyone))
        vb.addWidget(self._btn_everyone)
        self._room_btns["__all__"] = self._btn_everyone

        self._btn_all = _sidebar_btn("Alive")
        self._btn_all.setChecked(True)
        self._active_btn = self._btn_all
        self._btn_all.clicked.connect(lambda: self._filter(None, self._btn_all))
        vb.addWidget(self._btn_all)
        self._room_btns[None] = self._btn_all

        self._btn_safe_breeding_view = _sidebar_btn("Safe Breeding")
        self._btn_safe_breeding_view.clicked.connect(self._open_safe_breeding_view)
        vb.addWidget(self._btn_safe_breeding_view)
        self._btn_room_optimizer = _sidebar_btn("Room Optimizer")
        self._btn_room_optimizer.clicked.connect(self._open_room_optimizer)
        vb.addWidget(self._btn_room_optimizer)
        self._btn_tree_view = _sidebar_btn("Family Tree View")
        self._btn_tree_view.clicked.connect(self._open_tree_browser)
        vb.addWidget(self._btn_tree_view)

        vb.addWidget(_hsep())
        vb.addWidget(sl("ROOMS"))
        self._rooms_vb = QVBoxLayout(); self._rooms_vb.setSpacing(2)
        vb.addLayout(self._rooms_vb)
        vb.addWidget(_hsep())

        vb.addWidget(sl("OTHER"))
        self._btn_adventure = _sidebar_btn("On Adventure")
        self._btn_gone      = _sidebar_btn("Gone")
        self._btn_adventure.clicked.connect(
            lambda: self._filter("__adventure__", self._btn_adventure))
        self._btn_gone.clicked.connect(
            lambda: self._filter("__gone__", self._btn_gone))
        vb.addWidget(self._btn_adventure)
        vb.addWidget(self._btn_gone)
        self._room_btns["__adventure__"] = self._btn_adventure
        self._room_btns["__gone__"]      = self._btn_gone

        vb.addStretch()

        self._save_lbl = QLabel("No save loaded")
        self._save_lbl.setStyleSheet("color:#444; font-size:10px;")
        self._save_lbl.setWordWrap(True)
        vb.addWidget(self._save_lbl)

        rb = QPushButton("⟳  Reload  (F5)")
        rb.setStyleSheet("QPushButton { color:#888; background:#1a1a32;"
                         " border:1px solid #2a2a4a; padding:7px;"
                         " border-radius:4px; font-size:11px; }"
                         "QPushButton:hover { background:#222244; }")
        rb.clicked.connect(self._reload)
        vb.addWidget(rb)
        return w

    def _rebuild_room_buttons(self, cats: list[Cat]):
        while self._rooms_vb.count():
            item = self._rooms_vb.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        _ROOM_ORDER = {
            "Attic": 0, "Attic_Large": 0,
            "Floor2_Large": 1, "Floor2_Small": 2,
            "Floor1_Large": 3, "Floor1_Small": 4,
            "Basement": 5, "Basement_Large": 5,
        }
        rooms = sorted(
            {c.room for c in cats if c.status == "In House" and c.room},
            key=lambda r: _ROOM_ORDER.get(r, 99),
        )
        for room in rooms:
            count = sum(1 for c in cats if c.room == room)
            display = ROOM_DISPLAY.get(room, room)
            btn = _sidebar_btn(f"{display}  ({count})")
            btn.clicked.connect(lambda _, r=room, b=btn: self._filter(r, b))
            self._rooms_vb.addWidget(btn)
            self._room_btns[room] = btn

    # ── Content ────────────────────────────────────────────────────────────

    def _build_content(self) -> QWidget:
        w  = QWidget()
        vb = QVBoxLayout(w)
        vb.setContentsMargins(0, 0, 0, 0)
        vb.setSpacing(0)

        # Header
        hdr = QWidget()
        self._header = hdr
        hdr.setStyleSheet("background:#16213e; border-bottom:1px solid #1e1e38;")
        hdr.setFixedHeight(self._base_header_height)
        hb = QHBoxLayout(hdr); hb.setContentsMargins(14, 0, 14, 0)
        self._header_lbl = QLabel("All Cats")
        self._header_lbl.setStyleSheet("color:#eee; font-size:15px; font-weight:bold;")
        self._count_lbl = QLabel("")
        self._count_lbl.setStyleSheet("color:#555; font-size:12px; padding-left:8px;")
        self._summary_lbl = QLabel("")
        self._summary_lbl.setStyleSheet("color:#4a7a9a; font-size:11px;")
        self._search = QLineEdit()
        self._search.setPlaceholderText("Search…")
        self._search.setClearButtonEnabled(True)
        self._search.setFixedWidth(self._base_search_width)
        self._search.setStyleSheet(
            "QLineEdit { background:#0d0d1c; color:#ccc; border:1px solid #2a2a4a;"
            " border-radius:4px; padding:3px 8px; font-size:12px; }"
            "QLineEdit:focus { border-color:#3a3a7a; }")
        hb.addWidget(self._header_lbl)
        hb.addWidget(self._count_lbl)
        hb.addStretch()
        hb.addWidget(self._search)
        hb.addSpacing(12)
        hb.addWidget(self._summary_lbl)
        vb.addWidget(hdr)

        # Vertical splitter: table on top, detail panel on bottom (user-resizable)
        vs = QSplitter(Qt.Vertical)
        vs.setHandleWidth(4)
        vs.setStyleSheet("QSplitter::handle:vertical { background:#1e1e38; }")
        self._detail_splitter = vs
        self._table_view_container = vs
        vb.addWidget(vs)

        # Table
        self._source_model = CatTableModel()
        self._proxy_model  = RoomFilterModel()
        self._proxy_model.setSourceModel(self._source_model)
        self._proxy_model.modelReset.connect(self._update_count)
        self._proxy_model.rowsInserted.connect(self._update_count)
        self._proxy_model.rowsRemoved.connect(self._update_count)

        self._table = QTableView()
        self._table.setModel(self._proxy_model)
        self._table.setSortingEnabled(True)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setVisible(False)
        self._table.setShowGrid(False)
        self._table.setWordWrap(False)

        hh = self._table.horizontalHeader()
        hh.setStretchLastSection(False)  # we control stretch manually

        # Name: interactive so the user can resize it; not Stretch so it
        # doesn't eat the blank space that should sit at the right edge.
        hh.setSectionResizeMode(COL_NAME, QHeaderView.Interactive)
        self._table.setColumnWidth(COL_NAME, self._base_col_widths[COL_NAME])

        # Room: size to content so it adapts to room name length
        hh.setSectionResizeMode(COL_ROOM, QHeaderView.ResizeToContents)

        # Narrow fixed columns (gender, status, stats, sum)
        for col, width in [(COL_GEN, _W_GEN), (COL_STAT, _W_STATUS),
                           (COL_SUM, 38)] + [(c, _W_STAT) for c in STAT_COLS]:
            hh.setSectionResizeMode(col, QHeaderView.Fixed)
            self._table.setColumnWidth(col, width)

        # Abilities: interactive — user drags to taste
        hh.setSectionResizeMode(COL_ABIL, QHeaderView.Interactive)
        self._table.setColumnWidth(COL_ABIL, self._base_col_widths[COL_ABIL])

        # Mutations: interactive
        hh.setSectionResizeMode(COL_MUTS, QHeaderView.Interactive)
        self._table.setColumnWidth(COL_MUTS, self._base_col_widths[COL_MUTS])

        # Generation depth: fixed narrow, hidden by default (behind lineage toggle)
        hh.setSectionResizeMode(COL_REL, QHeaderView.Fixed)
        self._table.setColumnWidth(COL_REL, self._base_col_widths[COL_REL])

        # Generation depth: fixed narrow, hidden by default (behind lineage toggle)
        hh.setSectionResizeMode(COL_AGE, QHeaderView.Fixed)
        self._table.setColumnWidth(COL_AGE, self._base_col_widths[COL_AGE])
        self._table.setColumnHidden(COL_AGE, True)

        # Source: Stretch — absorbs blank space, hidden by default (behind lineage toggle)
        hh.setSectionResizeMode(COL_SRC, QHeaderView.Stretch)
        self._table.setColumnHidden(COL_SRC, True)

        # Inbreeding score: fixed narrow, hidden by default
        hh.setSectionResizeMode(COL_INB, QHeaderView.Fixed)
        self._table.setColumnWidth(COL_INB, self._base_col_widths[COL_INB])
        self._table.setColumnHidden(COL_INB, True)

        self._table.setStyleSheet("""
            QTableView {
                background:#0d0d1c; alternate-background-color:#131326;
                color:#ddd; border:none; font-size:12px;
                selection-background-color:#1e3060;
            }
            QTableView::item { padding:3px 4px; }
            QTableView::item:selected { color:#fff; }
            QHeaderView::section {
                background:#16213e; color:#888; padding:5px 4px;
                border:none; border-bottom:1px solid #1e1e38;
                border-right:1px solid #16213e;
                font-size:11px; font-weight:bold;
            }
            QScrollBar:vertical { background:#0d0d1c; width:10px; }
            QScrollBar::handle:vertical {
                background:#252545; border-radius:5px; min-height:20px;
            }
        """)

        self._table.selectionModel().selectionChanged.connect(self._on_selection)
        self._search.textChanged.connect(self._proxy_model.set_name_filter)
        self._search.textChanged.connect(self._update_count)
        vs.addWidget(self._table)

        # Detail panel
        self._detail = CatDetailPanel()
        vs.addWidget(self._detail)
        vs.setStretchFactor(0, 1)
        vs.setStretchFactor(1, 0)

        # Family tree view lives in the same main container and is swapped in/out
        # via left sidebar "VIEW" buttons.
        self._tree_view = FamilyTreeBrowserView(self)
        self._tree_view.hide()
        vb.addWidget(self._tree_view, 1)
        self._safe_breeding_view = SafeBreedingView(self)
        self._safe_breeding_view.hide()
        vb.addWidget(self._safe_breeding_view, 1)
        self._room_optimizer_view = RoomOptimizerView(self)
        self._room_optimizer_view.hide()
        vb.addWidget(self._room_optimizer_view, 1)

        return w

    # ── Selection → detail ────────────────────────────────────────────────

    def _on_selection(self):
        rows = list({
            self._proxy_model.mapToSource(idx).row()
            for idx in self._table.selectionModel().selectedRows()
        })
        cats = [c for r in rows[:2] if (c := self._source_model.cat_at(r)) is not None]
        was_collapsed = self._detail.maximumHeight() == 0
        self._detail.show_cats(cats)
        if cats and was_collapsed:
            total   = self._detail_splitter.height()
            panel_h = 200 if len(cats) == 1 else 300
            self._detail_splitter.setSizes([max(10, total - panel_h), panel_h])

        # Highlight compatibility: dim incompatible cats when 1 is selected
        focus = cats[0] if len(cats) == 1 else None
        self._source_model.set_focus_cat(focus)
        if self._tree_view is not None and self._tree_view.isVisible() and focus is not None:
            self._tree_view.select_cat(focus)
        if self._safe_breeding_view is not None and self._safe_breeding_view.isVisible() and focus is not None:
            self._safe_breeding_view.select_cat(focus)

    # ── Filtering ──────────────────────────────────────────────────────────

    def _filter(self, room_key, btn: QPushButton):
        self._show_table_view()
        if self._active_btn and self._active_btn is not btn:
            self._active_btn.setChecked(False)
        btn.setChecked(True)
        self._active_btn = btn
        self._proxy_model.set_room(room_key)
        self._update_header(room_key)
        self._update_count()
        self._detail.show_cats([])
        self._source_model.set_focus_cat(None)

    def _show_table_view(self):
        if hasattr(self, "_tree_view") and self._tree_view is not None:
            self._tree_view.hide()
        if hasattr(self, "_safe_breeding_view") and self._safe_breeding_view is not None:
            self._safe_breeding_view.hide()
        if hasattr(self, "_room_optimizer_view") and self._room_optimizer_view is not None:
            self._room_optimizer_view.hide()
        if hasattr(self, "_header"):
            self._header.show()
        if hasattr(self, "_table_view_container"):
            self._table_view_container.show()
        if hasattr(self, "_btn_tree_view"):
            self._btn_tree_view.setChecked(False)
        if hasattr(self, "_btn_safe_breeding_view"):
            self._btn_safe_breeding_view.setChecked(False)
        if hasattr(self, "_btn_room_optimizer"):
            self._btn_room_optimizer.setChecked(False)

    def _show_tree_view(self):
        if self._active_btn is not None:
            self._active_btn.setChecked(False)
        self._active_btn = None
        if hasattr(self, "_header"):
            self._header.hide()
        if hasattr(self, "_table_view_container"):
            self._table_view_container.hide()
        if hasattr(self, "_safe_breeding_view") and self._safe_breeding_view is not None:
            self._safe_breeding_view.hide()
        if hasattr(self, "_room_optimizer_view") and self._room_optimizer_view is not None:
            self._room_optimizer_view.hide()
        if self._tree_view is not None:
            self._tree_view.set_cats(self._cats)
            self._tree_view.show()
        if hasattr(self, "_btn_tree_view"):
            self._btn_tree_view.setChecked(True)
        if hasattr(self, "_btn_safe_breeding_view"):
            self._btn_safe_breeding_view.setChecked(False)
        if hasattr(self, "_btn_room_optimizer"):
            self._btn_room_optimizer.setChecked(False)

    def _show_safe_breeding_view(self):
        if self._active_btn is not None:
            self._active_btn.setChecked(False)
        self._active_btn = None
        if hasattr(self, "_header"):
            self._header.hide()
        if hasattr(self, "_table_view_container"):
            self._table_view_container.hide()
        if hasattr(self, "_tree_view") and self._tree_view is not None:
            self._tree_view.hide()
        if hasattr(self, "_room_optimizer_view") and self._room_optimizer_view is not None:
            self._room_optimizer_view.hide()
        if self._safe_breeding_view is not None:
            self._safe_breeding_view.set_cats(self._cats)
            self._safe_breeding_view.show()
        if hasattr(self, "_btn_tree_view"):
            self._btn_tree_view.setChecked(False)
        if hasattr(self, "_btn_safe_breeding_view"):
            self._btn_safe_breeding_view.setChecked(True)
        if hasattr(self, "_btn_room_optimizer"):
            self._btn_room_optimizer.setChecked(False)

    def _show_room_optimizer_view(self):
        if self._active_btn is not None:
            self._active_btn.setChecked(False)
        self._active_btn = None
        if hasattr(self, "_header"):
            self._header.hide()
        if hasattr(self, "_table_view_container"):
            self._table_view_container.hide()
        if hasattr(self, "_tree_view") and self._tree_view is not None:
            self._tree_view.hide()
        if hasattr(self, "_safe_breeding_view") and self._safe_breeding_view is not None:
            self._safe_breeding_view.hide()
        if self._room_optimizer_view is not None:
            self._room_optimizer_view.set_cats(self._cats)
            self._room_optimizer_view.show()
        if hasattr(self, "_btn_tree_view"):
            self._btn_tree_view.setChecked(False)
        if hasattr(self, "_btn_safe_breeding_view"):
            self._btn_safe_breeding_view.setChecked(False)
        if hasattr(self, "_btn_room_optimizer"):
            self._btn_room_optimizer.setChecked(True)

    def _update_header(self, room_key):
        if room_key == "__all__":
            self._header_lbl.setText("All Cats")
        elif room_key is None:
            self._header_lbl.setText("Alive")
        elif room_key == "__gone__":
            self._header_lbl.setText("Gone")
        elif room_key == "__adventure__":
            self._header_lbl.setText("On Adventure")
        else:
            self._header_lbl.setText(ROOM_DISPLAY.get(room_key, room_key))

    def _update_count(self):
        visible = self._proxy_model.rowCount()
        total   = self._source_model.rowCount()
        self._count_lbl.setText(f"  {visible} / {total} cats")

        placed = sum(1 for c in self._cats if c.status == "In House")
        adv    = sum(1 for c in self._cats if c.status == "Adventure")
        gone   = sum(1 for c in self._cats if c.status == "Gone")
        self._summary_lbl.setText(
            f"House: {placed}  |  Away: {adv}  |  Gone: {gone}")

    # ── Loading ────────────────────────────────────────────────────────────

    def load_save(self, path: str):
        self._current_save = path
        if self._watcher.files():
            self._watcher.removePaths(self._watcher.files())
        self._watcher.addPath(path)

        try:
            cats, errors = parse_save(path)
            self._cats = cats
            self._source_model.load(cats)
            self._rebuild_room_buttons(cats)
            # Update fixed sidebar button counts
            total = len(cats)
            alive = sum(1 for c in cats if c.status != "Gone")
            adv   = sum(1 for c in cats if c.status == "Adventure")
            gone  = sum(1 for c in cats if c.status == "Gone")
            self._btn_everyone.setText(f"All Cats  ({total})")
            self._btn_all.setText(f"Alive  ({alive})")
            self._btn_adventure.setText(f"On Adventure  ({adv})")
            self._btn_gone.setText(f"Gone  ({gone})")
            self._filter(None, self._btn_all)
            if self._tree_view is not None:
                self._tree_view.set_cats(cats)
            if self._safe_breeding_view is not None:
                self._safe_breeding_view.set_cats(cats)

            name = os.path.basename(path)
            self._save_lbl.setText(name)
            self.setWindowTitle(f"Mewgenics Breeding Manager — {name}")

            msg = f"Loaded {len(cats)} cats from {name}"
            if errors:
                msg += f"  ({len(errors)} parse errors)"
            self.statusBar().showMessage(msg)
        except Exception as e:
            self.statusBar().showMessage(f"Error loading save: {e}")

    def _toggle_lineage(self, checked: bool):
        self._show_lineage = checked
        for col in (COL_AGE, COL_SRC, COL_INB):
            self._table.setColumnHidden(col, not checked)
        self._source_model.set_show_lineage(checked)
        self._detail.set_show_lineage(checked)
        self._on_selection()   # refresh detail panel with updated flag

    def _open_file(self):
        saves   = find_save_files()
        start   = os.path.dirname(saves[0]) if saves else os.path.expanduser("~")
        path, _ = QFileDialog.getOpenFileName(
            self, "Open Mewgenics Save File", start,
            "Save Files (*.sav);;All Files (*)")
        if path:
            self.load_save(path)

    def _reload(self):
        if self._current_save:
            self.load_save(self._current_save)

    def _on_file_changed(self, path: str):
        if path == self._current_save:
            self._reload()

    def _open_tree_browser(self):
        self._show_tree_view()
        rows = list({
            self._proxy_model.mapToSource(idx).row()
            for idx in self._table.selectionModel().selectedRows()
        })
        cats = [c for r in rows[:1] if (c := self._source_model.cat_at(r)) is not None]
        if cats and self._tree_view is not None:
            self._tree_view.select_cat(cats[0])

    def _open_safe_breeding_view(self):
        self._show_safe_breeding_view()
        rows = list({
            self._proxy_model.mapToSource(idx).row()
            for idx in self._table.selectionModel().selectedRows()
        })
        cats = [c for r in rows[:1] if (c := self._source_model.cat_at(r)) is not None]
        if cats and self._safe_breeding_view is not None:
            self._safe_breeding_view.select_cat(cats[0])

    def _open_room_optimizer(self):
        self._show_room_optimizer_view()

    # ── UI zoom ───────────────────────────────────────────────────────────

    def _scaled(self, value: int) -> int:
        return max(1, round(value * (self._zoom_percent / 100.0)))

    def _update_zoom_info_action(self):
        if hasattr(self, "_zoom_info_action"):
            self._zoom_info_action.setText(f"Zoom: {self._zoom_percent}%")

    def _set_zoom(self, percent: int):
        clamped = max(_ZOOM_MIN, min(_ZOOM_MAX, int(percent)))
        if clamped == self._zoom_percent:
            return
        self._zoom_percent = clamped
        self._apply_zoom()
        self._update_zoom_info_action()
        self.statusBar().showMessage(f"UI zoom set to {self._zoom_percent}%")

    def _change_zoom(self, direction: int):
        self._set_zoom(self._zoom_percent + (direction * _ZOOM_STEP))

    def _reset_zoom(self):
        self._set_zoom(100)

    def _apply_zoom(self):
        app = QApplication.instance()
        font = QFont(self._base_font)
        base_pt = self._base_font.pointSizeF()
        if base_pt > 0:
            font.setPointSizeF(max(_ACCESSIBILITY_MIN_FONT_PT, base_pt * (self._zoom_percent / 100.0)))
        elif self._base_font.pixelSize() > 0:
            font.setPixelSize(max(_ACCESSIBILITY_MIN_FONT_PX, self._scaled(self._base_font.pixelSize())))
        app.setFont(font)

        if hasattr(self, "_sidebar"):
            self._sidebar.setFixedWidth(self._scaled(self._base_sidebar_width))
        if hasattr(self, "_header"):
            self._header.setFixedHeight(self._scaled(self._base_header_height))
        if hasattr(self, "_search"):
            self._search.setFixedWidth(self._scaled(self._base_search_width))
        if hasattr(self, "_table"):
            for col, width in self._base_col_widths.items():
                self._table.setColumnWidth(col, self._scaled(width))
            self._table.verticalHeader().setDefaultSectionSize(self._scaled(24))
        _enforce_min_font_in_widget_tree(self)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _hsep() -> QFrame:
    f = QFrame(); f.setFrameShape(QFrame.HLine)
    f.setStyleSheet("color:#1e1e38; margin:6px 0;")
    return f


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    pal = QPalette()
    pal.setColor(QPalette.Window,          QColor(13,  13,  28))
    pal.setColor(QPalette.WindowText,      QColor(220, 220, 230))
    pal.setColor(QPalette.Base,            QColor(18,  18,  36))
    pal.setColor(QPalette.AlternateBase,   QColor(20,  20,  40))
    pal.setColor(QPalette.Text,            QColor(220, 220, 230))
    pal.setColor(QPalette.Button,          QColor(22,  22,  46))
    pal.setColor(QPalette.ButtonText,      QColor(200, 200, 210))
    pal.setColor(QPalette.Highlight,       QColor(30,  48, 100))
    pal.setColor(QPalette.HighlightedText, QColor(255, 255, 255))
    pal.setColor(QPalette.ToolTipBase,     QColor(20,  20,  40))
    pal.setColor(QPalette.ToolTipText,     QColor(220, 220, 230))
    app.setPalette(pal)

    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
